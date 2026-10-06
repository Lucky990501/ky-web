"""Exact CURRENT015 validation; never broadens historical epoch/rollback gates."""
from __future__ import annotations

import hashlib
from pathlib import Path

from scripts import rollback_preflight as gate

CONTRACT_ID = 'current-release-schema-015-v1'
MIGRATION = {
    'version': '015', 'filename': '015_chat_image_attachments.sql',
    'git_blob': '96bba0ea64ade51670775cf6f69b806a2248342a',
    'canonical_sha256': '67c4c85007d7ac1a9dc3f0e979359d6b3f9ea84206740d11b6ce05c11563f1fc',
}


def current_schema(root: Path, historical_lock: list[dict]) -> dict:
    """Read a separate versioned lock, not an automatic 'latest SQL' allowance."""
    value = gate.read_json(root / 'deploy/current_schema_015.v1.json')
    expected = {
        'schema_version': 1, 'contract_id': CONTRACT_ID,
        'scope': 'CURRENT_RELEASE_VALIDATION', 'historical_epoch_schema': '014',
        'historical_schema_lock_sha256': '6209531538f525109aaef1e196403190b358de5695abf8f40969f5649f350137',
        'latest_schema': '015', 'pending': 0, 'data_contract': 'member_account_status_v1',
        'additional_migration': MIGRATION,
        'historical_epoch_semantics': 'UNCHANGED_EXACT_001_014',
    }
    gate.require(value == expected and type(value['schema_version']) is int
                 and type(value['pending']) is int, 'current_schema_015_contract_identity')
    gate.require([r['version'] for r in historical_lock] == [f'{i:03}' for i in range(1, 15)]
                 and gate.digest(historical_lock) == value['historical_schema_lock_sha256'],
                 'current_schema_historical_lock_identity')
    path = root / 'migrations/postgres' / MIGRATION['filename']
    gate.require(path.is_file() and not path.is_symlink(), 'current_schema_015_blob')
    content = path.read_bytes().replace(b'\r\n', b'\n')
    gate.require(hashlib.sha256(content).hexdigest() == MIGRATION['canonical_sha256']
                 and hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest()
                 == MIGRATION['git_blob'], 'current_schema_015_blob')
    lock = historical_lock + [{k: MIGRATION[k] for k in ('version', 'filename', 'canonical_sha256')}]
    # Exact inventory rejects missing/unknown migrations. Historical check_sources
    # is unchanged and still rejects this inventory against its own 014 lock.
    gate.check_sources(root, lock)
    return {**value, 'schema_migrations': lock}
