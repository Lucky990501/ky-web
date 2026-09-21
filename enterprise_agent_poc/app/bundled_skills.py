"""Approved bootstrap inputs, separate from immutable Registry package identity.

No normalization of Native ZIP bytes. Historical source and artifact identities
are independent, reviewed locks; historical artifacts are never rebuilt.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import zipfile


BUILDER_POLICY = "native-zip-stored-v1"
LEGACY_POLICY = "legacy-production-zip-v1"
MANIFEST_PATH = "enterprise_agent_poc/skill_packages/manifest.json"
HISTORICAL_SOURCE_COMMIT = "0f18a233111c95442dbbadee4df51841c51e019d"
# Approved independently of editable manifest, from production read-only evidence.
# Updating source + manifest cannot replace an already approved 1.0.0 identity.
HISTORICAL_IDENTITIES = {
    "campaign-planning": ("426f2ed147c30b83d21b6abd3d4591c0101cd3b98f9b02bc8603118ee52f2ea3", "37794f8842774bf08ee2478b07c6d248f229c96ced26dd75099337015feb91c0"),
    "event-copywriting": ("a5d230413c233b16995f70ec9452bb2ba018053635091c33167e6e44b1e822ff", "c4c04d4972156f72560d07bbc8dea6aaa48f3e65873035b8924d2e8f9edb01ad"),
    "marketing-copywriting": ("02ea7a868db96a99c025d6c2f7671869adbd988274ed66ce4d728f1fb79aa674", "706b0ca1b63e9a0f6c4e4651f851777c834f4f7e82e828087647e13990a3fc7e"),
    "poster-design": ("b70f470cd1b455f8170cf409109ef37f2524db4322518db1df57e05482eac14b", "82689e8361d60ddc1e09e4a16ac35fff5e84825b9bdc1c2a96490cd8c9e7a957"),
    "social-copywriting": ("4e62a73f7cd274c41be57050b09a80dba63f2626f27e085ab1ce8d17b5aa951a", "2fd4024753bd1535c2dd6ec983d1082e2f17a6d61c987cdd25f9b06ec4137f6a"),
}
# This is deliberately not a general unpublished-version escape hatch. The
# only permitted transition was audited before either Registry was published.
PRE_RELEASE_IDENTITY_CORRECTIONS = {
    ("event-campaign-plan", "1.0.0"): {
        "old_identity": {
            "source_identity": {"path": "event-campaign-plan/1.0.0", "files": [{"path": "SKILL.md", "sha256": "5b086da9ddb4b13c59f0d5e81444b75dade927a1a9d2a5cc0a47bcdaea419592", "git_mode": "100644"}]},
            "artifact_sha256": "d3a2f98b699a62d7cebea73695fa2e9d19fca5a7c6e09c6bdf47025e3184d06d",
            "artifact_path": "event-campaign-plan/1.0.0.zip",
            "builder_policy": "native-zip-stored-v1",
            "legacy_artifact": False,
        },
        "corrected_identity": {
            "source_identity": {"path": "event-campaign-plan/1.0.0", "files": [{"path": "SKILL.md", "sha256": "5b086da9ddb4b13c59f0d5e81444b75dade927a1a9d2a5cc0a47bcdaea419592", "git_mode": "100644"}]},
            "artifact_sha256": "19a79e7dab430136cf3ae4513b54b74cab98c8f5b50e766f018a4dd75024ad38",
            "artifact_path": "event-campaign-plan/1.0.0.zip",
            "builder_policy": "native-zip-stored-v1",
            "legacy_artifact": False,
        },
        "reason": "Restore the audited historical CRLF ZIP payload before any Production or Shared Registry publication.",
    },
}
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
VERSION = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?")
HASH = re.compile(r"[0-9a-f]{64}")
TEXT_SUFFIXES = {".md", ".py", ".sql", ".sh", ".js", ".ts", ".tsx", ".jsx",
                 ".json", ".yaml", ".yml", ".txt", ".toml", ".css", ".html",
                 ".ps1", ".ini", ".cfg", ".conf", ".xml", ".svg", ".csv"}


class BundledSkillError(ValueError):
    pass


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _entry_identity(entry: dict) -> dict:
    """The immutable portion of one manifest entry, excluding bindings."""
    return {name: entry[name] for name in ("source_identity", "artifact_sha256", "artifact_path",
                                            "builder_policy", "legacy_artifact")}


def pre_release_correction_state(entry: dict) -> str | None:
    """Return an audited state, never a best-effort identity match."""
    correction = PRE_RELEASE_IDENTITY_CORRECTIONS.get((entry.get("skill_slug"), entry.get("version")))
    if correction is None:
        return None
    identity = _entry_identity(entry)
    if identity == correction["old_identity"]:
        return "old"
    if identity == correction["corrected_identity"]:
        return "corrected"
    raise BundledSkillError("pre-release identity correction mismatch; BLOCK")


def is_corrected_pre_release_identity(entry: dict) -> bool:
    return pre_release_correction_state(entry) == "corrected"


def _is_pre_release_correction_transition(old: dict, current: dict) -> bool:
    """Allow only the one audited old-to-corrected transition in Git ancestry."""
    if (old["skill_slug"], old["version"]) != (current["skill_slug"], current["version"]):
        return False
    try:
        return pre_release_correction_state(old) == "old" and pre_release_correction_state(current) == "corrected"
    except BundledSkillError:
        return False


def forbidden_path(name: str) -> bool:
    parts = PurePosixPath(name).parts
    junk = {".ds_store", ".appledouble", ".lsoverride", "thumbs.db", "desktop.ini",
            ".runtime-data", ".venv", "venv", "node_modules", "__pycache__",
            ".pytest_cache", ".git", ".secrets-backups", ".deploy-secrets.local",
            "id_rsa", "id_ed25519", "authorized_keys"}
    return any(part.lower() in junk or
               (part.lower().startswith(".env") and not part.lower().endswith(".example"))
               for part in parts) or name.lower().endswith(
                   (".pem", ".key", ".p12", ".pfx", ".pyc", ".db", ".sqlite", ".sqlite3"))


def secret_content(content: bytes) -> bool:
    # Never include matched values in diagnostics.
    return bool(re.search(rb"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----|"
                          rb"\bAKIA[0-9A-Z]{16}\b|"
                          rb"\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}", content))


def safe_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise BundledSkillError("invalid bundled path")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or path.as_posix() != value or any(
        part in {".", ".."} or ":" in part or part.endswith((" ", ".")) or any(ord(char) < 32 for char in part) for part in path.parts
    ) or forbidden_path(value):
        raise BundledSkillError("unsafe or forbidden bundled path")
    return value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BundledSkillError("duplicate manifest JSON key")
        result[key] = value
    return result


def parse_manifest(content: bytes) -> list[dict]:
    try:
        manifest = json.loads(content, object_pairs_hook=_unique_object)
        if set(manifest) != {"schema_version", "historical_source_commit", "skills"} or type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1:
            raise BundledSkillError("unsupported bundled manifest")
        if manifest["historical_source_commit"] != HISTORICAL_SOURCE_COMMIT:
            raise BundledSkillError("historical source provenance changed")
        entries = manifest["skills"]
        if not isinstance(entries, list) or not entries:
            raise BundledSkillError("empty bundled manifest")
        identities, defaults, legacy = set(), set(), set()
        for entry in entries:
            if set(entry) != {"skill_slug", "version", "source_identity", "artifact_sha256",
                             "artifact_path", "bootstrap_default", "builder_policy", "legacy_artifact"}:
                raise BundledSkillError("invalid manifest entry fields")
            slug, version = entry["skill_slug"], entry["version"]
            if not isinstance(slug, str) or not SLUG.fullmatch(slug) or not isinstance(version, str) or not VERSION.fullmatch(version):
                raise BundledSkillError("invalid bundled slug/version")
            if (slug, version) in identities:
                raise BundledSkillError("duplicate bundled slug/version")
            identities.add((slug, version))
            safe_path(entry["artifact_path"])
            if entry["artifact_path"] != f"{slug}/{version}.zip" or not HASH.fullmatch(entry["artifact_sha256"]):
                raise BundledSkillError("invalid artifact identity")
            source = entry["source_identity"]
            if set(source) != {"path", "files"} or source["path"] != f"{slug}/{version}":
                raise BundledSkillError("invalid source identity")
            files, seen = source["files"], set()
            if not isinstance(files, list) or not files:
                raise BundledSkillError("empty source identity")
            for item in files:
                if set(item) != {"path", "sha256", "git_mode"}:
                    raise BundledSkillError("invalid source lock fields")
                safe_path(item["path"])
                if item["path"].casefold() in seen or item["git_mode"] not in {"100644", "100755"} or not HASH.fullmatch(item["sha256"]):
                    raise BundledSkillError("invalid or duplicate source lock")
                seen.add(item["path"].casefold())
            if "skill.md" not in seen:
                raise BundledSkillError("source lock lacks SKILL.md")
            if not isinstance(entry["bootstrap_default"], list):
                raise BundledSkillError("invalid bootstrap defaults")
            for agent in entry["bootstrap_default"]:
                if not isinstance(agent, str) or not SLUG.fullmatch(agent) or (agent, slug) in defaults:
                    raise BundledSkillError("duplicate or invalid bootstrap binding")
                defaults.add((agent, slug))
            if type(entry["legacy_artifact"]) is not bool:
                raise BundledSkillError("invalid legacy marker")
            # Known correction keys must always be one of the two audited
            # identities, even before release preflight reaches Git ancestry.
            pre_release_correction_state(entry)
            if entry["legacy_artifact"]:
                approved = HISTORICAL_IDENTITIES.get(slug)
                expected = [{"path": "SKILL.md", "sha256": approved[0], "git_mode": "100644"}] if approved else None
                if version != "1.0.0" or not approved or files != expected or entry["artifact_sha256"] != approved[1] or entry["builder_policy"] != LEGACY_POLICY:
                    raise BundledSkillError("approved historical identity changed")
                legacy.add(slug)
            elif entry["builder_policy"] != BUILDER_POLICY or (slug in HISTORICAL_IDENTITIES and version == "1.0.0"):
                raise BundledSkillError("historical artifact cannot be rebuilt or relabeled")
        if legacy != set(HISTORICAL_IDENTITIES):
            raise BundledSkillError("approved historical versions missing")
        return entries
    except (KeyError, TypeError, AttributeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BundledSkillError("invalid bundled manifest") from exc


def deterministic_zip(slug: str, version: str, files: dict[str, tuple[bytes, str]]) -> bytes:
    """Offline future-version builder v1: exact Git bytes, ZIP_STORED, no zlib."""
    return _native_deterministic_zip(slug, version, files)


def _native_deterministic_zip(slug: str, version: str, files: dict[str, tuple[bytes, str]], *, crlf_text_payload: bool = False) -> bytes:
    """Fixed-metadata native ZIP writer; CRLF is internal to the audited contract."""
    if not SLUG.fullmatch(slug) or not VERSION.fullmatch(version):
        raise BundledSkillError("invalid future artifact identity")
    if slug in HISTORICAL_IDENTITIES and version == "1.0.0":
        raise BundledSkillError("historical 1.0.0 must never be rebuilt")
    if crlf_text_payload and (slug, version) not in PRE_RELEASE_IDENTITY_CORRECTIONS:
        raise BundledSkillError("unapproved pre-release correction packaging")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED, allowZip64=False) as archive:
        for name in sorted(files, key=lambda value: value.encode("utf-8")):
            safe_path(name)
            content, mode = files[name]
            if mode not in {"100644", "100755"} or secret_content(content):
                raise BundledSkillError("invalid future source")
            if (PurePosixPath(name).suffix.lower() in TEXT_SUFFIXES or PurePosixPath(name).name in {"LICENSE", "Dockerfile"} or name.endswith(".env.example")) and b"\r" in content:
                raise BundledSkillError("future text source must be committed LF bytes")
            if crlf_text_payload and PurePosixPath(name).suffix.lower() in TEXT_SUFFIXES:
                content = content.replace(b"\n", b"\r\n")
            info = zipfile.ZipInfo(f"{slug}/{name}", date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.create_version = info.extract_version = 20
            info.external_attr = int(mode, 8) << 16
            info.internal_attr = info.flag_bits = 0
            info.extra = info.comment = b""
            info.compress_type = zipfile.ZIP_STORED
            archive.writestr(info, content)
    result = buffer.getvalue()
    from app.skill_registry import NativeSkillArchive
    NativeSkillArchive.inspect(result, slug)
    return result


def canonical_artifact(entry: dict, files: dict[str, tuple[bytes, str]]) -> bytes:
    """Return the sole approved deterministic artifact for a manifest identity."""
    if pre_release_correction_state(entry) == "corrected":
        artifact = _native_deterministic_zip(entry["skill_slug"], entry["version"], files, crlf_text_payload=True)
        if sha256(artifact) != entry["artifact_sha256"]:
            raise BundledSkillError("pre-release corrected artifact mismatch; BLOCK")
        return artifact
    return deterministic_zip(entry["skill_slug"], entry["version"], files)


def future_artifact_from_git(repo: Path, commit: str, slug: str, version: str) -> bytes:
    """Offline API: caller writes/reviews returned bytes; never read checkout."""
    if not SLUG.fullmatch(slug) or not VERSION.fullmatch(version):
        raise BundledSkillError("invalid future artifact identity")
    entries = parse_manifest(_git(repo, "show", f"{commit}:{MANIFEST_PATH}"))
    entry = next((item for item in entries if (item["skill_slug"], item["version"]) == (slug, version)), None)
    if entry is None:
        raise BundledSkillError("future committed manifest entry missing")
    prefix = f"enterprise_agent_poc/skill_packages/{slug}/{version}/"
    files = {}
    for record in _git(repo, "ls-tree", "-r", "-z", commit, "--", prefix).split(b"\0"):
        if not record:
            continue
        header, name = record.split(b"\t", 1)
        mode, kind, oid = header.decode().split()
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise BundledSkillError("invalid future Git source type/mode")
        files[name.decode().removeprefix(prefix)] = (_git(repo, "cat-file", "blob", oid), mode)
    if not files:
        raise BundledSkillError("future committed source missing")
    return canonical_artifact(entry, files)


def _validate_inputs(entries: list[dict], read, names: set[str], modes, *, rebuild_future: bool = False) -> None:
    from app.skill_registry import NativeSkillArchive
    expected = {"manifest.json"}
    for entry in entries:
        source = {}
        for item in entry["source_identity"]["files"]:
            name = f"{entry['source_identity']['path']}/{item['path']}"
            expected.add(name)
            content = read(name)
            if sha256(content) != item["sha256"] or modes(name) != item["git_mode"] or secret_content(content):
                raise BundledSkillError(f"source lock mismatch: {name}")
            source[item["path"]] = (content, item["git_mode"])
        name = entry["artifact_path"]
        expected.add(name)
        artifact = read(name)
        if sha256(artifact) != entry["artifact_sha256"]:
            raise BundledSkillError(f"artifact lock mismatch: {name}")
        NativeSkillArchive.inspect(artifact, entry["skill_slug"])
        with zipfile.ZipFile(io.BytesIO(artifact)) as archive:
            for info in archive.infolist():
                safe_path(info.filename.rstrip("/"))
                if not info.is_dir() and secret_content(archive.read(info)):
                    raise BundledSkillError("forbidden content in bundled artifact")
        if pre_release_correction_state(entry) == "corrected":
            source_skill = source["SKILL.md"][0]
            with zipfile.ZipFile(io.BytesIO(artifact)) as archive:
                payload_skill = archive.read(f"{entry['skill_slug']}/SKILL.md")
            if b"\r" in source_skill or b"\r\n" not in payload_skill or payload_skill.replace(b"\r\n", b"\n") != source_skill:
                raise BundledSkillError("pre-release historical newline contract mismatch; BLOCK")
        # No legacy rebuild, normalization, or runtime reinterpretation. The
        # exact pre-release contract is the sole audited non-LF payload case.
        if rebuild_future and not entry["legacy_artifact"] and canonical_artifact(entry, source) != artifact:
            raise BundledSkillError("future deterministic artifact mismatch")
    if names != expected:
        raise BundledSkillError("bundled file set mismatch or unapproved files")


def validate_bundle(root: Path) -> list[dict]:
    root = root.resolve()
    try:
        entries = parse_manifest((root / "manifest.json").read_bytes())
        paths = list(root.rglob("*"))
        if any(path.is_symlink() for path in paths):
            raise BundledSkillError("bundled symlink forbidden")
        names = {path.relative_to(root).as_posix() for path in paths if path.is_file()}
        def mode(name):
            path = root / name
            expected = next((item["git_mode"] for entry in entries for item in entry["source_identity"]["files"]
                             if f"{entry['source_identity']['path']}/{item['path']}" == name), None)
            # Windows checkout cannot reliably express Unix bits; committed Git
            # modes are always checked by release preflight on every platform.
            if os.name == "nt":
                return expected
            return "100755" if path.stat().st_mode & 0o111 == 0o111 else "100644" if not path.stat().st_mode & 0o111 else "INVALID"
        _validate_inputs(entries, lambda name: (root / name).read_bytes(), names, mode)
        return entries
    except OSError as exc:
        raise BundledSkillError("bundled manifest/source/artifact missing or unreadable") from exc


def _git(repo: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.DEVNULL)


def validate_git_bundle(repo: Path, commit: str) -> list[dict]:
    """Validate exact committed inputs, not checkout, and freeze prior versions."""
    try:
        if _git(repo, "rev-parse", "--is-shallow-repository").strip() == b"true":
            raise BundledSkillError("complete Git history required for immutable version review")
        prefix = "enterprise_agent_poc/skill_packages/"
        entries = parse_manifest(_git(repo, "show", f"{commit}:{MANIFEST_PATH}"))
        tree = {}
        for record in _git(repo, "ls-tree", "-r", "-z", commit, "--", prefix).split(b"\0"):
            if not record:
                continue
            header, name = record.split(b"\t", 1)
            mode, kind, oid = header.decode().split()
            if kind != "blob" or mode not in {"100644", "100755"}:
                raise BundledSkillError("invalid Git bundled file type/mode")
            tree[name.decode().removeprefix(prefix)] = (mode, oid)
        _validate_inputs(entries, lambda name: _git(repo, "cat-file", "blob", tree[name][1]),
                         set(tree), lambda name: tree[name][0], rebuild_future=True)
        current = {(e["skill_slug"], e["version"]): e for e in entries}
        # Existing source/artifact identities are frozen at their first committed
        # appearance on this revision's ancestry; defaults may change by review.
        revisions = _git(repo, "rev-list", commit, "--", MANIFEST_PATH).decode().splitlines()
        for revision in revisions:
            try:
                previous_content = _git(repo, "show", f"{revision}:{MANIFEST_PATH}")
            except subprocess.CalledProcessError:
                continue
            for old in parse_manifest(previous_content):
                key = old["skill_slug"], old["version"]
                if key not in current:
                    raise BundledSkillError("committed version identity changed; create a new version")
                old_identity = {k: v for k, v in old.items() if k != "bootstrap_default"}
                current_identity = {k: v for k, v in current[key].items() if k != "bootstrap_default"}
                if old_identity != current_identity and not _is_pre_release_correction_transition(old, current[key]):
                    raise BundledSkillError("committed version identity changed; create a new version")
        return entries
    except (subprocess.CalledProcessError, KeyError) as exc:
        raise BundledSkillError("committed bundled inputs missing") from exc
