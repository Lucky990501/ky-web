"""Order-independent verification view of the existing Native Revision v1.

This is NOT a new persisted identity/hash: ZIP SHA, original declaration bytes,
Runtime lock and receipts remain their original contracts. Only the unordered
file inventory is canonicalized; metadata lists retain exact semantic order.
"""
from __future__ import annotations

import json
import re

from app.bundled_skills import BundledSkillError, safe_path

CANONICALIZATION_VERSION = 'native-revision-v1-order-independent'
METADATA_FIELDS = (
    'slug', 'name', 'version', 'registry_revision_field', 'source_type',
    'original_zip_sha256', 'source_root', 'artifact_sha256', 'entrypoint',
    'windows_helper', 'dependencies_sha256', 'actions', 'permissions',
    'upload_gate', 'forbidden_actions', 'proposed_binding',
)


class RevisionIdentityError(ValueError):
    pass


def canonical_path(value: str) -> str:
    """Relative POSIX separators; exact Unicode/case; no dot/path aliases.

    Like existing bundled path rules, Unicode code points are preserved, not
    NFC/NFKC/case/locale folded. UTF-8 must encode strictly. The caller deriving
    a filesystem path first uses relative_to(root), never absolute-path slicing.
    """
    try:
        if not isinstance(value, str):
            raise ValueError()
        name = safe_path(value.replace('\\', '/'))
        name.encode('utf-8', errors='strict')
        return name
    except (BundledSkillError, ValueError, UnicodeError):
        raise RevisionIdentityError('SKILL_REVISION_PATH_INVALID') from None


def canonical_files(entries: list[dict]) -> list[dict]:
    """Full exact inventory, not a set dropping duplicates or extra entries."""
    try:
        if not isinstance(entries, list) or not entries:
            raise ValueError()
        result, seen = [], set()
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {'path', 'sha256', 'git_mode'}:
                raise ValueError()
            name = canonical_path(entry['path'])
            if (name in seen or not isinstance(entry['sha256'], str)
                    or not re.fullmatch('[a-f0-9]{64}', entry['sha256'])
                    or entry['git_mode'] not in ('100644', '100755')):
                raise ValueError()
            seen.add(name)
            result.append(dict(path=name, sha256=entry['sha256'], git_mode=entry['git_mode']))
        return sorted(result, key=lambda entry: entry['path'].encode('utf-8'))
    except (KeyError, TypeError, ValueError):
        raise RevisionIdentityError('SKILL_REVISION_FILES_INVALID') from None


def _metadata(value):
    """Nested maps use UTF-8 bytewise keys; metadata lists are NOT sorted."""
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise RevisionIdentityError('SKILL_REVISION_METADATA_INVALID')
        return {key: _metadata(value[key]) for key in sorted(value, key=lambda key: key.encode('utf-8'))}
    if isinstance(value, list):
        return [_metadata(item) for item in value]
    if type(value) not in (str, int, float, bool, type(None)):
        raise RevisionIdentityError('SKILL_REVISION_METADATA_INVALID')
    return value


def canonical_revision(contract: dict) -> bytes:
    """Separate fixed-field seal metadata from canonically sorted file entries.

    These comparison bytes are not written over any historical manifest/seal or
    used as a replacement SHA in Registry/Runtime/qualification receipts.
    """
    try:
        if not isinstance(contract, dict) or set(contract) != set(METADATA_FIELDS) | {'files'}:
            raise ValueError()
        metadata = [[field, _metadata(contract[field])] for field in METADATA_FIELDS]
        files = canonical_files(contract['files'])
        return json.dumps(dict(metadata=metadata, files=files), ensure_ascii=False,
                          separators=(',', ':'), allow_nan=False).encode('utf-8', errors='strict')
    except (KeyError, TypeError, ValueError, UnicodeError):
        raise RevisionIdentityError('SKILL_REVISION_IDENTITY_INVALID') from None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RevisionIdentityError('SKILL_REVISION_DUPLICATE_KEY')
        result[key] = value
    return result


def parse_revision(content: bytes) -> dict:
    try:
        value = json.loads(content, object_pairs_hook=_unique_object)
        canonical_revision(value)  # schema, paths, duplicates, finite metadata
        return value  # Preserve legacy declaration order and API representation.
    except (ValueError, TypeError, UnicodeError):
        raise RevisionIdentityError('SKILL_REVISION_DECLARATION_INVALID') from None


def verify_revision_identity(declaration: dict, observed: dict) -> None:
    if canonical_revision(declaration) != canonical_revision(observed):
        raise RevisionIdentityError('SKILL_REVISION_IDENTITY_MISMATCH')
