"""V3 exact-pair record sealing, not an Owner issuer or deployment entry.

Proposed Root manual-attestation workflow. Independent authority must approve
the workflow and this tool's exact bytes before any privileged use. A hash or
UID cannot authenticate an Owner: an authorized human verifies the actual
decision in its independent authenticated channel, then attests on /dev/tty.
No key generation, approval synthesis, install, service, DB or Provider action.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
import stat
import subprocess
import sys
import tarfile
import time

ROOT = Path(__file__).absolute().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import schema015_successor_binding as binding
from scripts.runtime_policy_rollout import Rollout, Image, SafeParser

VERSION = "V3_ROOT_MANUAL_ATTESTATION_V1"
BASE = Path("/opt/enterprise-agent-workbench")
LOCK = Path("/run/lock/enterprise-agent-workbench-release.lock")
HOST = "iv-yekqs6ez9cnu262hhsg0"
EXACT = {
    "release_id": "20261012-7dcde57-p0-controlled-v3",
    "source_commit": "7dcde5736c9493b5862f465848afb585691b721f",
    "source_tree": "1d332373bd8cc31da074456e9e9bcaaa4aceb948",
    "parent_source_commit": "280ae327a9d4720c5cadfac38aba259c7ff161ae",
    "archive_sha256": "82b52d58300a0545b14c334f3e5be2d4878ade74c39ad5f7771330d1233f776e",
    "raw_manifest_sha256": "ad6c2d444950045e4143deb30b9c3bfe8caf01dca48be5074cd75e26d92380ce",
    "manifest_sha256": "71c5b8fdccd41d75cfb068b84cd86562fd8173351881c46542c6acfb6ed56bc8",
}
BINDING_SHA = "040ac73cf432184b3dca93121a9336ee2e723f91b275b633f6c43047e0a1a3e1"
CODE_FILES = (
    "scripts/v3_trusted_record_bootstrap.py", "scripts/runtime_policy_rollout.py",
    "scripts/schema015_successor_binding.py", "scripts/rollback_preflight.py",
    "scripts/verify_runtime_config.py", "scripts/release_manifest.py", "scripts/release_runtime_recovery.py",
    "app/settings.py", "deploy/rollback_compatibility.json",
)
ID = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
SUFFIX = {"policy": ".policy-approval.v1.json", "deployment": ".approval.v1.json",
          "evidence": ".evidence.v1.json"}
COMMON = {"schema_version", "scope", "issuer_uid", "authority", "principal_id", "audit_id",
          "decision", "issued_at", "expires_at", "binding_sha256", "candidate", "predecessor"}


class Blocked(ValueError):
    """Only fixed codes cross the CLI boundary; never echo decision contents."""


def need(ok, code):
    if not ok:
        raise Blocked(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def trusted_path(path):
    """Same strict Root-chain predicates as the frozen protected_json reader.

    Apply them also to directories/artifacts, not just 0600 JSON inputs. No
    importing historical Test-only tooling absent from the approved Source.
    """
    need(path.is_absolute() and path.resolve(strict=True) == path, "TRUSTED_PATH_IDENTITY")
    for item in (path, *path.parents):
        info = item.lstat()
        need(info.st_uid == 0 and not info.st_mode & 0o022
             and (stat.S_ISDIR(info.st_mode) or item == path and stat.S_ISREG(info.st_mode)
                  and info.st_nlink == 1), "TRUSTED_ROOT_PATH_REQUIRED")


def window(record, now):
    need(type(record.get("issued_at")) is int and type(record.get("expires_at")) is int
         and 0 < record["issued_at"] <= now < record["expires_at"]
         and record["expires_at"] - record["issued_at"] <= 86400, "DECISION_EXPIRED_OR_INVALID")


def policy_valid(record, now):
    need(isinstance(record, dict) and set(record) == COMMON | {
        "replacements", "accepted_risks", "test_environment_id", "production_environment_id"},
        "OWNER_DECISION_FIELDS")
    need(type(record["schema_version"]) is int and record["schema_version"] == 1
         and type(record["issuer_uid"]) is int and record["issuer_uid"] == 0
         and record["scope"] == "P0_EXACT_PAIR_POLICY_CHANGE"
         and record["authority"] == "PROJECT_OWNER_POLICY_AUTHORITY"
         and record["decision"] == "APPROVED"
         and all(isinstance(record[k], str) and ID.fullmatch(record[k]) for k in ("principal_id", "audit_id")),
         "INDEPENDENT_OWNER_DECISION_REQUIRED")
    window(record, now)
    need(record["candidate"] == EXACT and record["predecessor"] == binding.PREDECESSOR
         and record["binding_sha256"] == BINDING_SHA, "OWNER_EXACT_PAIR_REQUIRED")
    need(record["replacements"] == binding.P0_REPLACEMENTS
         and record["accepted_risks"] == binding.P0_RISKS, "OWNER_EXPLICIT_RISKS_REQUIRED")
    environments = (record["test_environment_id"], record["production_environment_id"])
    need(all(isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{1,127}", v)
             for v in environments) and environments[0] != environments[1], "ENVIRONMENT_ISOLATION_REQUIRED")


def packaged_index(blob_reader):
    """Exact existing build_release.py static-version transform, no free edits.

    Its only two substitutions are SHA256[:16] for the frozen placeholders.
    Do not execute an uploaded builder or pretend transformed HTML is a blob.
    """
    index, mode = blob_reader("enterprise_agent_poc/app/static/index.html")
    for asset, placeholder in (("workbench.js", b"__JS_SHA256_V1__"),
                               ("workbench.css", b"__CSS_SHA256_V1_")):
        data, _ = blob_reader("enterprise_agent_poc/app/static/" + asset)
        need(index.count(placeholder) == 1, "STATIC_BUILD_CONTRACT_DRIFT")
        version = sha(data)[:16].encode("ascii")
        index = index.replace(placeholder, version)
        need(index.count(b"/static/" + asset.encode() + b"?v=" + version) == 1, "STATIC_BUILD_REFERENCE_DRIFT")
    return index, mode


def inspect_artifact(archive, manifest_raw, *, blob_reader):
    """No candidate code executed. Compare every member to exact Git source bytes."""
    need(sha(manifest_raw) == EXACT["raw_manifest_sha256"], "RAW_MANIFEST_DRIFT")
    manifest = json.loads(manifest_raw, object_pairs_hook=binding._pairs)
    need(binding.digest(manifest) == EXACT["manifest_sha256"], "CANONICAL_MANIFEST_DRIFT")
    need(manifest["release_id"] == EXACT["release_id"] and manifest["source_commit"] == EXACT["source_commit"]
         and manifest["archive_sha256"] == EXACT["archive_sha256"], "ARTIFACT_IDENTITY_DRIFT")
    declaration = binding.validate(manifest["runtime_only_release"])
    need(binding.digest(declaration) == BINDING_SHA, "BINDING_DRIFT")
    need(archive.stat().st_size <= 8 * 1024 * 1024, "ARCHIVE_SIZE_BLOCKED")
    need(sha(archive.read_bytes()) == EXACT["archive_sha256"], "ARCHIVE_DRIFT")
    files, directories, modes, seen = {}, [], {}, set()
    with tarfile.open(archive, "r:gz") as bundle:
        need(bundle.pax_headers.get("comment") == EXACT["source_commit"], "ARCHIVE_SOURCE_DRIFT")
        for member in bundle.getmembers():
            path = PurePosixPath(member.name)
            need(not path.is_absolute() and ".." not in path.parts
                 and str(path) == member.name and path.parts[0] == "enterprise_agent_poc"
                 and member.name not in seen and (member.isfile() or member.isdir())
                 and not member.mode & 0o7002, "ARCHIVE_MEMBER_UNSAFE")
            seen.add(member.name)
            if member.isdir():
                # Frozen git archive uses tar.umask=0002: 0775/0664 headers.
                # Authorized inactive installation MUST strip group-write,
                # without stripping the Git executable bit (umask 0022).
                need(member.mode in {0o755, 0o775}, "ARCHIVE_DIRECTORY_MODE")
                directories.append(member.name)
                continue
            need(member.mode in {0o644, 0o755, 0o664, 0o775} and member.size <= 32 * 1024 * 1024,
                 "ARCHIVE_FILE_MODE_OR_SIZE")
            source, mode = blob_reader(member.name)
            if member.name == "enterprise_agent_poc/app/static/index.html":
                source, mode = packaged_index(blob_reader)
            data = bundle.extractfile(member).read()
            need(data == source and member.mode & ~0o022 == mode, "ARCHIVE_GIT_BYTES_OR_MODE_DRIFT")
            files[member.name] = sha(data)
            modes[member.name] = mode
        need(len(files) == manifest["selected_file_count"] == 145 and len(directories) == 26
             and set(files) == set(manifest["selected_files"]), "ARTIFACT_INVENTORY_DRIFT")
    pins = declaration["successor"]["tooling_pins"]
    need(all(files["enterprise_agent_poc/" + p] == h for p, h in pins.items()), "SEVEN_CODE_PIN_DRIFT")
    return manifest, {"files": files, "directories": directories, "permanent_files": 147,
                      "permanent_directories": 27, "code_pins": pins, "installed_modes": modes}


def git_reader(repo, source, tree, parent):
    def git(*args):
        return subprocess.check_output(["/usr/bin/git", "--no-replace-objects", "-c", "core.hooksPath=/dev/null",
                                        "-C", str(repo), *args], stderr=subprocess.DEVNULL, timeout=20)
    need(git("rev-parse", source + "^{commit}").decode().strip() == source
         and git("rev-parse", source + "^{tree}").decode().strip() == tree
         and git("show", "-s", "--format=%P", source).decode().strip() == parent, "GIT_SOURCE_TREE_PARENT_DRIFT")
    def blob(path):
        item = git("ls-tree", source, "--", path).decode().split()
        need(len(item) == 4 and item[0] in {"100644", "100755"} and item[1] == "blob" and item[3] == path,
             "GIT_FILE_IDENTITY")
        return git("cat-file", "blob", item[2]), 0o755 if item[0] == "100755" else 0o644
    return blob


def technical_valid(grant, *, expected_sha, raw, action, inputs, host, operator, producer, now):
    need(isinstance(expected_sha, str) and binding.HASH.fullmatch(expected_sha)
         and sha(raw) == expected_sha, "INDEPENDENT_TECHNICAL_PIN_REQUIRED")
    need(json.loads(raw, object_pairs_hook=binding._pairs) == grant, "TECHNICAL_DECISION_BYTES_DRIFT")
    need(isinstance(grant, dict) and set(grant) == {
        "schema_version", "workflow", "authority", "decision", "principal_id", "audit_id", "issued_at",
        "expires_at", "host", "operator", "action", "candidate", "binding_sha256", "producer",
        "input_sha256", "independent_decision_references", "production_switch_authorized",
        "business_database_write_authorized"}, "TECHNICAL_GRANT_FIELDS")
    need(type(grant["schema_version"]) is int and grant["schema_version"] == 1
         and grant["workflow"] == VERSION and grant["authority"] == "PRODUCTION_ROOT_MAINTENANCE_AUTHORITY"
         and grant["decision"] == "APPROVED" and grant["production_switch_authorized"] is False
         and grant["business_database_write_authorized"] is False, "INDEPENDENT_TECHNICAL_AUTHORITY_REQUIRED")
    need(host == HOST and grant["host"] == host and grant["operator"] == operator
         and isinstance(operator, str) and ID.fullmatch(operator)
         and all(isinstance(grant[k], str) and ID.fullmatch(grant[k]) for k in ("principal_id", "audit_id")),
         "HOST_OR_OPERATOR_DRIFT")
    need(grant["action"] == action and grant["candidate"] == EXACT and grant["binding_sha256"] == BINDING_SHA
         and grant["producer"] == producer and grant["input_sha256"] == inputs, "TECHNICAL_SCOPE_DRIFT")
    refs = grant["independent_decision_references"]
    need(isinstance(refs, dict) and set(refs) == set(inputs)
         and all(isinstance(v, str) and ID.fullmatch(v) for v in refs.values()), "AUTHENTICATED_CHANNEL_REFERENCES_REQUIRED")
    window(grant, now)


def tty_attest(grant_sha, inputs):
    """Attestation records human verification, not cryptographic Owner authentication."""
    need(os.geteuid() == 0, "ROOT_TTY_REQUIRED")
    with open("/dev/tty", "r+", encoding="utf-8") as tty:
        need(os.isatty(tty.fileno()), "ROOT_TTY_REQUIRED")
        challenge = "VERIFIED-INDEPENDENT-DECISIONS " + grant_sha + " " + binding.digest(inputs)
        tty.write("Verify actual identities, authority, expiry/revocation and report contents in the independent\n"
                  "authenticated channel. Root possession and these JSON labels are NOT authorization.\n"
                  "Paste the externally approved technical SHA and the exact acknowledgement:\n" + challenge + "\n")
        tty.flush()
        need(tty.readline().strip() == challenge, "INDEPENDENT_HUMAN_VERIFICATION_NOT_ATTESTED")
        return {"device": os.ttyname(tty.fileno()), "euid": os.geteuid(), "pid": os.getpid(),
                "acknowledgement_sha256": sha(challenge.encode())}


def lock_parent_valid(info, existing):
    # The existing /run/lock may be a system sticky directory. An ALREADY
    # Root-owned safe lock inode cannot be renamed/unlinked by an unprivileged
    # peer there. Do not create a new lock in a peer-writable directory.
    return (stat.S_ISDIR(info.st_mode) and info.st_uid == 0
            and (not info.st_mode & 0o022 or bool(info.st_mode & stat.S_ISVTX) and existing))


class Store:
    """Use existing Root reader and rollout atomic writer, under the SAME Release lock."""
    def __init__(self):
        need(os.geteuid() == 0 and socket.gethostname() == HOST, "EXACT_PRODUCTION_ROOT_REQUIRED")
        self.base = BASE
        self.writer = Rollout(BASE)
        self.qualifications = BASE / "shared/release-qualifications"
        self.audit = BASE / "shared/release-state/v3-record-bootstrap"

    def read(self, path):
        value, fingerprint = binding.protected_json(path)
        raw = self.writer._read(path).data
        need(sha(raw) == fingerprint, "PROTECTED_INPUT_CHANGED")
        return value, raw

    def create(self, path, raw):
        trusted_path(path.parent)
        self.writer._directories(path.parent)
        need(not path.exists() and not path.is_symlink(), "RECORD_ALREADY_EXISTS_OR_CONSUMED")
        self.writer._atomic(path, raw, Image(b"", 0, 0, 0o600, 0, 0))
        need(self.read(path)[1] == raw, "RECORD_READBACK_DRIFT")

    @contextmanager
    def lock(self):
        # The existing switch uses this inode. No override and never unlink it.
        need(LOCK.parent.resolve() == LOCK.parent and lock_parent_valid(LOCK.parent.lstat(), LOCK.exists()),
             "UNTRUSTED_RELEASE_LOCK_PARENT")
        for parent in LOCK.parent.parents:
            trusted_path(parent)
        flags = os.O_RDWR | os.O_NOFOLLOW
        if not LOCK.parent.stat().st_mode & 0o022:
            flags |= os.O_CREAT
        fd = os.open(LOCK, flags, 0o600)
        try:
            info = os.fstat(fd)
            need(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                 and not info.st_mode & 0o022, "UNTRUSTED_RELEASE_LOCK")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Blocked("GLOBAL_RELEASE_LOCK_BUSY") from None
            current = LOCK.lstat()
            need((current.st_dev, current.st_ino) == (info.st_dev, info.st_ino), "RELEASE_LOCK_INODE_DRIFT")
            yield
        finally:
            os.close(fd)

    def prepare_directories(self):
        # Only future independently authorized record sealing, never installation.
        for path in (self.qualifications, self.audit.parent, self.audit):
            trusted_path(path.parent)
            self.writer._directories(path.parent)
            if not path.exists():
                path.mkdir(mode=0o700)
            self.writer._directories(path)

    def path(self, kind):
        return self.qualifications / (EXACT["release_id"] + SUFFIX[kind])


def seal(store, action, grant, grant_raw, records, record_raw, *, grant_sha, producer, operator,
         host, now, manifest=None, schema_fingerprint=None, reports=None, attest=tty_attest, publish=True):
    """Consumes only the independent decision for sealing. Does not deploy/install."""
    need(action in {"seal-policy", "seal-release-records", "revoke-policy"}, "ACTION_SCOPE_REJECTED")
    names = {"policy"} if action != "seal-release-records" else {"deployment", "evidence"}
    need(set(records) == set(record_raw) == names, "RECORD_SET_REQUIRED")
    need(all(json.loads(raw, object_pairs_hook=binding._pairs) == records[name]
             for name, raw in record_raw.items()), "RECORD_BYTES_DRIFT")
    inputs = {name: sha(record_raw[name]) for name in names}
    if action == "seal-release-records":
        need(isinstance(reports, dict) and set(reports) == set(binding.P0_CHECKS), "ACTUAL_REPORT_BYTES_REQUIRED")
        checks = records["evidence"].get("checks", {})
        need(isinstance(checks, dict) and set(checks) == set(binding.P0_CHECKS), "ALL_18_REPORTS_REQUIRED")
        for name, raw in reports.items():
            need(isinstance(raw, bytes) and raw and checks[name].get("report_sha256") == sha(raw),
                 "ACTUAL_REPORT_HASH_DRIFT:" + name)
            inputs["report:" + name] = sha(raw)
    technical_valid(grant, expected_sha=grant_sha, raw=grant_raw, action=action, inputs=inputs,
                    host=host, operator=operator, producer=producer, now=now)
    need(all(grant["audit_id"] != record.get("audit_id")
             and grant["issued_at"] >= record.get("issued_at", 0)
             for name, record in records.items() if name != "evidence"), "TECHNICAL_AND_OWNER_DECISIONS_MUST_BE_SEPARATE")
    if action in {"seal-policy", "revoke-policy"}:
        policy_valid(records["policy"], now)
    else:
        need(manifest is not None, "VERIFIED_MANIFEST_REQUIRED")
        declaration = manifest["runtime_only_release"]
        binding._p0_qualification(store.qualifications, declaration, EXACT, records["deployment"],
                                 records["evidence"], inputs["evidence"], schema_fingerprint, now=now)
    if not publish:
        return {"status": "DRY_RUN_BYTES_VERIFIED_NOT_AUTHORITY", "writes": 0,
                "human_authority_verification": "NOT_PERFORMED", "action": action}
    with store.lock():
        store.prepare_directories()
        marker = store.audit / (grant["audit_id"] + ".json")
        need(not marker.exists(), "TECHNICAL_DECISION_ALREADY_CONSUMED")
        intent = store.audit / (EXACT["release_id"] + "." + action + ".intent.json")
        need(not intent.exists(), "SOURCE_OPERATION_ALREADY_CONSUMED")
        revoked = store.audit / (EXACT["release_id"] + ".revoked.json")
        need(not revoked.exists(), "SOURCE_AUTHORIZATION_REVOKED")
        decisions = {name: store.audit / ("decision-" + inputs[name] + ".json")
                     for name in names if name != "evidence" and action != "revoke-policy"}
        need(all(not path.exists() for path in decisions.values()), "INDEPENDENT_DECISION_ALREADY_CONSUMED")
        for name in names:
            if action == "revoke-policy":
                need(store.read(store.path(name))[1] == record_raw[name], "REVOKE_POLICY_DRIFT")
            else:
                need(not store.path(name).exists() and not store.path(name).is_symlink(), "RECORD_ALREADY_EXISTS_OR_CONSUMED")
        # Recheck the existing policy while holding the global lock. A concurrent
        # revocation/replacement cannot slip between verification and publication.
        if action == "seal-release-records":
            binding._p0_qualification(store.qualifications, declaration, EXACT, records["deployment"],
                                     records["evidence"], inputs["evidence"], schema_fingerprint, now=int(time.time()))
        tty_evidence = attest(grant_sha, inputs)
        # A person may take time at the TTY. Never seal expired inputs after it.
        window(grant, int(time.time()))
        if action in {"seal-policy", "revoke-policy"}:
            policy_valid(records["policy"], int(time.time()))
        else:
            binding._p0_qualification(store.qualifications, declaration, EXACT, records["deployment"],
                                     records["evidence"], inputs["evidence"], schema_fingerprint, now=int(time.time()))
        # Durable one-use audit BEFORE publication. Failed/partial publication is
        # retained and rejects retries; no automatic cleanup/re-authorisation.
        audit = {"workflow": VERSION, "action": action, "technical_audit_id": grant["audit_id"],
                 "technical_grant_sha256": grant_sha, "host": host, "operator": operator,
                 "candidate": EXACT, "producer": producer, "input_sha256": inputs,
                 "independent_decision_references": grant["independent_decision_references"],
                 "human_attestation": "INDEPENDENT_AUTHENTICATED_CHANNEL_VERIFIED_BY_AUTHORIZED_ROOT",
                 "tty_evidence": tty_evidence,
                 "observed_at": now, "production_switch_authorized": False,
                 "state": "CONSUMED_FOR_RECORD_SEAL_ONLY"}
        # Source-wide one-shot intent FIRST, so a crash before the technical
        # snapshot cannot be replayed under a different audit ID either.
        store.create(intent, (json.dumps(audit, sort_keys=True) + "\n").encode())
        store.create(store.audit / (grant["audit_id"] + ".technical-decision.json"), grant_raw)
        store.create(marker, (json.dumps(audit, sort_keys=True) + "\n").encode())
        for path in decisions.values():
            store.create(path, (json.dumps(audit, sort_keys=True) + "\n").encode())
        if action == "revoke-policy":
            # Preserve the immutable original under audit; absence of the policy
            # makes the frozen 7dc consumer reject, without teaching it a new seal.
            archive = store.audit / (EXACT["release_id"] + ".withdrawn-policy.json")
            need(not archive.exists() and store.path("policy").stat().st_dev == store.audit.stat().st_dev,
                 "REVOCATION_ARCHIVE_UNSAFE")
            os.rename(store.path("policy"), archive)
            for path in (store.audit, store.qualifications):
                directory = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            store.create(revoked, (json.dumps(audit, sort_keys=True) + "\n").encode())
        else:
            # Publish approval LAST: no partial evidence-only operation can
            # authorize release. Policy-only publication cannot deploy either.
            for name in sorted(names, key=lambda n: n == "deployment"):
                store.create(store.path(name), record_raw[name])
        return {"status": "RECORD_SEALED_NOT_INSTALL_OR_RELEASE_AUTHORITY", "action": action,
                "audit_id": grant["audit_id"], "input_sha256": inputs, "deployment_executed": False}


def main(argv=None):
    parser = SafeParser(description=__doc__)
    parser.add_argument("action", choices=("inspect-artifact", "seal-policy", "seal-release-records", "revoke-policy"))
    parser.add_argument("--git-repository", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--technical-grant", type=Path)
    parser.add_argument("--independently-approved-technical-sha256")
    parser.add_argument("--producer-source")
    parser.add_argument("--producer-tree")
    parser.add_argument("--operator")
    for name in SUFFIX:
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--reports", type=Path, help="Root-protected <check>.report bytes for all 18 checks")
    parser.add_argument("--seal", action="store_true", help="future independent Root authority and TTY required")
    args = parser.parse_args(argv)
    try:
        store = Store()
        # Root-protected uploaded artifact and repo, no arbitrary download/input.
        trusted_path(args.git_repository)
        for path in (args.archive, args.manifest):
            trusted_path(path)
            info = path.lstat()
            need(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                 and stat.S_IMODE(info.st_mode) in {0o400, 0o600, 0o644}, "UNTRUSTED_UPLOADED_ARTIFACT")
        manifest, inventory = inspect_artifact(args.archive, args.manifest.read_bytes(),
            blob_reader=git_reader(args.git_repository, EXACT["source_commit"], EXACT["source_tree"], EXACT["parent_source_commit"]))
        if args.action == "inspect-artifact":
            print(json.dumps({"status": "EXACT_ARTIFACT_VERIFIED_NOT_AUTHORITY", "candidate": EXACT,
                              "files": inventory["permanent_files"], "directories": inventory["permanent_directories"]}))
            return 0
        need(args.technical_grant is not None and args.operator is not None
             and isinstance(args.producer_source, str) and binding.COMMIT.fullmatch(args.producer_source)
             and isinstance(args.producer_tree, str) and binding.COMMIT.fullmatch(args.producer_tree),
             "INDEPENDENT_TECHNICAL_APPROVAL_REQUIRED")
        blob = git_reader(args.git_repository, args.producer_source, args.producer_tree, EXACT["source_commit"])
        pins = {}
        for relative in CODE_FILES:
            observed = ROOT / relative
            trusted_path(observed)
            info = observed.lstat()
            need(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                 and not info.st_mode & 0o022, "PRODUCER_TRUSTED_CODE_REQUIRED")
            original, _ = blob("enterprise_agent_poc/" + relative)
            need(observed.read_bytes() == original, "PRODUCER_CODE_DRIFT")
            pins[relative] = sha(original)
        producer = {"source_commit": args.producer_source, "source_tree": args.producer_tree, "code_pins": pins}
        grant, grant_raw = store.read(args.technical_grant)
        names = ("deployment", "evidence") if args.action == "seal-release-records" else ("policy",)
        records, raw = {}, {}
        for name in names:
            path = getattr(args, name)
            need(path is not None, "EXACT_RECORD_INPUT_REQUIRED")
            records[name], raw[name] = store.read(path)
        inputs = {name: sha(raw[name]) for name in names}
        reports = None
        if args.action == "seal-release-records":
            need(args.reports is not None, "ACTUAL_REPORT_BYTES_REQUIRED")
            trusted_path(args.reports)
            reports = {}
            for name in binding.P0_CHECKS:
                # Original bytes, not invented normalized PASS summaries.
                reports[name] = store.writer._read(args.reports / (name + ".report")).data
                inputs["report:" + name] = sha(reports[name])
        technical_valid(grant, expected_sha=args.independently_approved_technical_sha256, raw=grant_raw,
                        action=args.action, inputs=inputs, host=socket.gethostname(), operator=args.operator,
                        producer=producer, now=int(time.time()))
        # Manifest/schema helpers are internal arguments, never grant input.
        from scripts import rollback_preflight as gate
        lock014 = gate.epoch_contract(gate.read_json(ROOT / "deploy/rollback_compatibility.json"))["schema_migrations"]
        from scripts.release_runtime_recovery import MIGRATION
        fingerprint = binding.digest(lock014 + [{k: MIGRATION[k] for k in ("version", "filename", "canonical_sha256")}])
        print(json.dumps(seal(store, args.action, grant, grant_raw, records, raw,
            grant_sha=args.independently_approved_technical_sha256, producer=producer, operator=args.operator,
            host=socket.gethostname(), now=int(time.time()), manifest=manifest, schema_fingerprint=fingerprint,
            reports=reports, publish=args.seal)))
        return 0
    except Exception as exc:
        code = str(exc) if isinstance(exc, (Blocked, binding.BindingBlocked)) else "BOOTSTRAP_INPUT_OR_FILESYSTEM_BLOCKED"
        print(json.dumps({"status": "BLOCKED", "check": code}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
