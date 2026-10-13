"""V3 exact-pair record sealing, not an Owner issuer or deployment entry.

Proposed Root manual-attestation workflow. Independent authority must approve
the workflow and this tool's exact bytes before any privileged use. A hash or
UID cannot authenticate an Owner: an authorized human verifies the actual
decision in its independent authenticated channel, then attests on /dev/tty.
No key generation, approval synthesis, install, service, DB or Provider action.
"""
from __future__ import annotations

# Direct execution cannot establish trust in this file or its dependency graph.
# The independently verified stdlib entry validates every repository byte first.
# Keep this before *all* imports, including scripts package initialization.
if __name__ == "__main__":
    raise SystemExit("BLOCKED: VERIFIED_SOURCE_ONLY_ENTRY_REQUIRED")

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
# The constants above describe historical 7dc inspection fixtures only. The
# formal source-only entry installs a separately pinned NEW artifact identity.
STATEFUL_CONTEXT = False
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


def configure_candidate(exact, binding_sha):
    """Called only after independently pinned entry verifies all source bytes.

    No artifact/approval is produced here. The actual artifact is subsequently
    verified against Git and this independent identity by inspect_artifact.
    """
    global EXACT, BINDING_SHA, STATEFUL_CONTEXT, VERSION
    need(isinstance(exact, dict) and set(exact) == set(EXACT)
         and all(isinstance(exact[k], str) and binding.COMMIT.fullmatch(exact[k])
                 for k in ("source_commit", "source_tree", "parent_source_commit"))
         and exact["parent_source_commit"] == binding.STATE_BASE_SOURCE
         and exact["source_commit"] != binding.STATE_BASE_SOURCE
         and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{1,127}", exact["release_id"])
         and all(isinstance(exact[k], str) and binding.HASH.fullmatch(exact[k]) for k in (
             "archive_sha256", "raw_manifest_sha256", "manifest_sha256"))
         and isinstance(binding_sha, str) and binding.HASH.fullmatch(binding_sha),
         "STATEFUL_APPLICATION_IDENTITY_REQUIRED")
    EXACT, BINDING_SHA = dict(exact), binding_sha
    STATEFUL_CONTEXT = True
    VERSION = "V3_ROOT_MANUAL_ATTESTATION_STATE_V2"


def record_common():
    return COMMON | ({"state_contract"} if STATEFUL_CONTEXT else set())


class CommitUnknown(OSError):
    """An attempted protected write may have committed. Never means zero writes."""
    def __init__(self, action, phase):
        super().__init__("PROTECTED_COMMIT_UNKNOWN")
        self.action, self.phase = action, phase


def operation_path(store, action, suffix):
    return store.audit / (EXACT["release_id"] + "." + action + "." + suffix + ".json")


def operation_state(store, action):
    """Coordination evidence, NOT a replacement for the frozen consumer.

    An intent without an exact completion remains PENDING_OR_UNKNOWN, even if
    output bytes happen to be readable. Never infer durability from exists().
    Completed records undergo independent read/fsync/read reconciliation of
    unchanged bytes; this does not issue authority or repair a frozen reader.
    """
    intent = operation_path(store, action, "intent")
    complete = operation_path(store, action, "complete")
    if not intent.exists() and not intent.is_symlink():
        need(not complete.exists() and not complete.is_symlink(), "ORPHAN_COMPLETION_REJECTED")
        return None
    value, original = store.read(intent)
    need(value.get("action") == action and value.get("candidate") == EXACT,
         "OPERATION_INTENT_IDENTITY_DRIFT")
    if not complete.exists() and not complete.is_symlink():
        return {"state": "PENDING_OR_UNKNOWN", "intent": value, "intent_sha256": sha(original)}
    # Even the completion write itself can fail after replace. Visible bytes
    # alone are not recovery proof: independently re-read and flush both records
    # and their directory before resolving an uncertain terminal publication.
    try:
        terminal, terminal_raw = store.read(complete)
        need(terminal == {"state": "PREPARED" if "state_contract" in value else "VERIFIED_COMMITTED", "action": action,
                          "intent_sha256": sha(original)}, "OPERATION_COMPLETION_DRIFT")
        verified_existing(store, intent, original)
        verified_existing(store, complete, terminal_raw)
    except OSError as exc:
        raise CommitUnknown(action, "COMPLETION_RECONCILIATION_REQUIRED") from exc
    if "state_contract" in value:
        reconciled = operation_path(store, action, "reconciled")
        if not reconciled.exists():
            return {"state": "PENDING_OR_UNKNOWN", "intent": value, "intent_sha256": sha(original)}
        receipt, receipt_raw = store.read(reconciled)
        need(receipt.get("intent_sha256") == sha(original)
             and receipt.get("completion_sha256") == sha(terminal_raw)
             and receipt.get("candidate") == EXACT and receipt.get("binding_sha256") == BINDING_SHA
             and receipt.get("state") == "VERIFIED_COMMITTED"
             and receipt.get("contract_id") == binding.STATE_CONTRACT,
             "RECONCILIATION_IDENTITY_DRIFT")
        verified_existing(store, reconciled, receipt_raw)
    return {"state": "VERIFIED_COMMITTED", "intent": value, "intent_sha256": sha(original)}


def pending_barrier(store, action, grant_sha):
    for pending in store.audit.glob(EXACT["release_id"] + ".revocation-*"):
        need(action == "revoke-policy" and pending.name == EXACT["release_id"] + ".revocation-" + grant_sha,
             "POLICY_REVOCATION_PENDING_OR_COMPLETE")
        store.writer._directories(pending)
    for prior in ("seal-policy", "seal-release-records", "revoke-policy"):
        state = operation_state(store, prior)
        if state is None:
            continue
        if prior == "revoke-policy":
            need(action == prior and state["intent"]["technical_grant_sha256"] == grant_sha,
                 "POLICY_REVOCATION_PENDING_OR_COMPLETE")
        if state["state"] != "VERIFIED_COMMITTED":
            need(prior == action == "revoke-policy"
                 and state["intent"]["technical_grant_sha256"] == grant_sha,
                 "SOURCE_OPERATION_ALREADY_CONSUMED_COMMIT_UNKNOWN")


def verified_existing(store, path, expected):
    """Independent protected read/fsync/read for exact resumption/reconciliation.

    A previous exception is not interpreted as absence. Foreign/mismatched
    bytes remain blocked; nothing is removed or overwritten to enable retry.
    """
    need(store.read(path)[1] == expected, "RECOVERY_RECORD_DRIFT")
    for item in (path, path.parent):
        flags = os.O_RDONLY | os.O_NOFOLLOW
        if item == path.parent:
            flags |= os.O_DIRECTORY
        fd = os.open(item, flags)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    need(store.read(path)[1] == expected, "RECOVERY_READBACK_DRIFT")


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
    need(isinstance(record, dict) and set(record) == record_common() | {
        "replacements", "accepted_risks", "test_environment_id", "production_environment_id"},
        "OWNER_DECISION_FIELDS")
    need(type(record["schema_version"]) is int and record["schema_version"] == (2 if STATEFUL_CONTEXT else 1)
         and (not STATEFUL_CONTEXT or record.get("state_contract") == binding.STATE_CONTRACT)
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
    need(not STATEFUL_CONTEXT or binding.is_stateful(manifest), "STATEFUL_ARTIFACT_REQUIRED")
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
        need(len(files) == manifest["selected_file_count"]
             and (STATEFUL_CONTEXT or len(files) == 145 and len(directories) == 26)
             and set(files) == set(manifest["selected_files"]), "ARTIFACT_INVENTORY_DRIFT")
    pins = declaration["successor"]["tooling_pins"]
    need(all(files["enterprise_agent_poc/" + p] == h for p, h in pins.items()), "SEVEN_CODE_PIN_DRIFT")
    return manifest, {"files": files, "directories": directories, "permanent_files": len(files) + 2,
                      "permanent_directories": len(directories) + 1, "code_pins": pins, "installed_modes": modes}


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


def approval_identity_separation(store, action, grant, grant_raw, records, record_raw, now):
    """Check typed decision identities; this is NOT human issuer authentication.

    The frozen Owner/Deployment scopes and the technical workflow are distinct
    record types. Their audit IDs name decisions, never cross-type references.
    Read protected policy bytes again under the release lock and after TTY.
    Existing immutable technical snapshots provide cross-operation replay checks;
    no new approval registry or authority is introduced.
    """
    if action == "seal-release-records":
        policy, policy_raw = store.read(store.path("policy"))
    else:
        policy, policy_raw = records["policy"], record_raw["policy"]
    policy_valid(policy, now)
    owner_id = policy["audit_id"]
    decisions = {"policy": policy}
    if "deployment" in records:
        decisions["deployment"] = records["deployment"]
    elif store.path("deployment").exists() or store.path("deployment").is_symlink():
        decisions["deployment"] = store.read(store.path("deployment"))[0]
    if "deployment" in decisions:
        deployment = decisions["deployment"]
        need(isinstance(deployment, dict) and set(deployment) == record_common() | {
            "policy_approval_sha256", "policy_audit_id", "evidence_sha256"}, "DEPLOYMENT_DECISION_TYPE_REQUIRED")
        need(type(deployment["schema_version"]) is int and deployment["schema_version"] == (2 if STATEFUL_CONTEXT else 1)
             and (not STATEFUL_CONTEXT or deployment.get("state_contract") == binding.STATE_CONTRACT)
             and type(deployment["issuer_uid"]) is int and deployment["issuer_uid"] == 0
             and deployment["scope"] == "P0_EXACT_PAIR_PRODUCTION_DEPLOYMENT"
             and deployment["authority"] == "PRODUCTION_RELEASE_AUTHORITY"
             and deployment["decision"] == "APPROVED"
             and all(isinstance(deployment[k], str) and ID.fullmatch(deployment[k])
                     for k in ("principal_id", "audit_id")), "DEPLOYMENT_DECISION_TYPE_REQUIRED")
        need(deployment["candidate"] == EXACT and deployment["predecessor"] == binding.PREDECESSOR
             and deployment["binding_sha256"] == BINDING_SHA
             and deployment["policy_audit_id"] == owner_id
             and deployment["policy_approval_sha256"] == sha(policy_raw)
             and type(deployment["issued_at"]) is int and type(deployment["expires_at"]) is int
             and policy["issued_at"] <= deployment["issued_at"] < deployment["expires_at"] <= policy["expires_at"],
             "DEPLOYMENT_OWNER_RELATION_DRIFT")
        # An expired deployment does not obstruct revoking its still-valid
        # parent policy; a newly submitted deployment must be currently valid.
        if "deployment" in records:
            window(deployment, now)
    decision_ids = [record["audit_id"] for record in decisions.values()]
    need(len(set(decision_ids + [grant["audit_id"]])) == len(decision_ids) + 1
         and all(grant["issued_at"] >= record["issued_at"] for record in decisions.values()),
         "TECHNICAL_AND_OWNER_DECISIONS_MUST_BE_SEPARATE")
    for path in sorted(store.audit.glob("*.technical-decision.json")):
        previous, previous_raw = store.read(path)
        need(isinstance(previous, dict) and previous.get("workflow") == VERSION
             and previous.get("authority") == "PRODUCTION_ROOT_MAINTENANCE_AUTHORITY"
             and previous.get("decision") == "APPROVED"
             and isinstance(previous.get("audit_id"), str) and ID.fullmatch(previous["audit_id"])
             and path.name == previous["audit_id"] + ".technical-decision.json"
             and previous.get("candidate") == EXACT and previous.get("binding_sha256") == BINDING_SHA,
             "TECHNICAL_HISTORY_IDENTITY_DRIFT")
        need(previous["audit_id"] not in decision_ids, "DECISION_TYPES_MUST_BE_SEPARATE")
        if previous["audit_id"] == grant["audit_id"]:
            # Resuming exact revocation is completion of that one decision, not
            # a second grant. The state machine must independently permit it.
            need(action == previous.get("action") == "revoke-policy" and previous_raw == grant_raw,
                 "TECHNICAL_DECISION_ALREADY_CONSUMED")


def tty_attest(grant_sha, inputs):
    """Attestation records human verification, not cryptographic Owner authentication."""
    need(os.geteuid() == 0, "ROOT_TTY_REQUIRED")
    with open("/dev/tty", "r", encoding="utf-8") as tty_in, \
            open("/dev/tty", "w", encoding="utf-8") as tty_out:
        need(os.isatty(tty_in.fileno()) and os.isatty(tty_out.fileno()), "ROOT_TTY_REQUIRED")
        challenge = "VERIFIED-INDEPENDENT-DECISIONS " + grant_sha + " " + binding.digest(inputs)
        tty_out.write("Verify actual identities, authority, expiry/revocation and report contents in the independent\n"
                      "authenticated channel. Root possession and these JSON labels are NOT authorization.\n"
                      "Paste the externally approved technical SHA and the exact acknowledgement:\n" + challenge + "\n")
        tty_out.flush()
        need(tty_in.readline().strip() == challenge, "INDEPENDENT_HUMAN_VERIFICATION_NOT_ATTESTED")
        return {"device": os.ttyname(tty_in.fileno()), "euid": os.geteuid(), "pid": os.getpid(),
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
        inherited = False
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
            if STATEFUL_CONTEXT:
                # State consumers prove ownership of the SAME open description
                # as the existing release shell, never an environment claim.
                if fd != 9:
                    try:
                        os.fstat(9)
                    except OSError:
                        pass
                    else:
                        raise Blocked("RELEASE_LOCK_DESCRIPTOR_IN_USE")
                    os.dup2(fd, 9, inheritable=False)
                    inherited = True
                binding.require_release_lock()
            yield
        finally:
            if inherited:
                os.close(9)
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
    need(not STATEFUL_CONTEXT or manifest is not None and binding.is_stateful(manifest),
         "STATEFUL_ARTIFACT_REQUIRED")
    need(not STATEFUL_CONTEXT or producer.get("source_commit") == EXACT["source_commit"]
         and producer.get("source_tree") == EXACT["source_tree"], "STATEFUL_PRODUCER_SOURCE_DRIFT")
    decision_validator = binding._p0_decisions if STATEFUL_CONTEXT else binding._p0_qualification
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
    if action in {"seal-policy", "revoke-policy"}:
        policy_valid(records["policy"], now)
    else:
        need(manifest is not None, "VERIFIED_MANIFEST_REQUIRED")
        pending_barrier(store, action, grant_sha)
        declaration = manifest["runtime_only_release"]
        decision_validator(store.qualifications, declaration, EXACT, records["deployment"],
                                 records["evidence"], inputs["evidence"], schema_fingerprint, now=now)
    approval_identity_separation(store, action, grant, grant_raw, records, record_raw, now)
    if not publish:
        return {"status": "DRY_RUN_BYTES_VERIFIED_NOT_AUTHORITY", "writes": 0,
                "human_authority_verification": "NOT_PERFORMED", "action": action}
    with store.lock():
        store.prepare_directories()
        pending_barrier(store, action, grant_sha)
        if STATEFUL_CONTEXT:
            binding.require_release_lock()
            if action == "seal-release-records":
                binding.state_qualification(store.qualifications, declaration, EXACT,
                    {"policy": sha(store.read(store.path("policy"))[1])}, actions=("seal-policy",))
        approval_identity_separation(store, action, grant, grant_raw, records, record_raw, int(time.time()))
        marker = store.audit / (grant["audit_id"] + ".json")
        resume = action == "revoke-policy"
        need(resume or not marker.exists(), "TECHNICAL_DECISION_ALREADY_CONSUMED")
        intent = store.audit / (EXACT["release_id"] + "." + action + ".intent.json")
        need(resume or not intent.exists(), "SOURCE_OPERATION_ALREADY_CONSUMED")
        revoked = store.audit / (EXACT["release_id"] + ".revoked.json")
        need(resume or not revoked.exists(), "SOURCE_AUTHORIZATION_REVOKED")
        pending_dir = store.audit / (EXACT["release_id"] + ".revocation-" + grant_sha)
        pending_policy = pending_dir / "policy.json"
        archive = store.audit / (EXACT["release_id"] + ".withdrawn-policy.json")
        decisions = {name: store.audit / ("decision-" + inputs[name] + ".json")
                     for name in names if name != "evidence" and action != "revoke-policy"}
        need(all(not path.exists() for path in decisions.values()), "INDEPENDENT_DECISION_ALREADY_CONSUMED")
        for name in names:
            if action == "revoke-policy":
                candidates = [p for p in (store.path(name), pending_policy, archive) if p.exists() or p.is_symlink()]
                need(len(candidates) == 1 and store.read(candidates[0])[1] == record_raw[name], "REVOKE_POLICY_DRIFT")
            else:
                need(not store.path(name).exists() and not store.path(name).is_symlink(), "RECORD_ALREADY_EXISTS_OR_CONSUMED")
        # Recheck the existing policy while holding the global lock. A concurrent
        # revocation/replacement cannot slip between verification and publication.
        if action == "seal-release-records":
            decision_validator(store.qualifications, declaration, EXACT, records["deployment"],
                                     records["evidence"], inputs["evidence"], schema_fingerprint, now=int(time.time()))
        tty_evidence = attest(grant_sha, inputs)
        # A person may take time at the TTY. Never seal expired inputs after it.
        window(grant, int(time.time()))
        if action in {"seal-policy", "revoke-policy"}:
            policy_valid(records["policy"], int(time.time()))
        else:
            decision_validator(store.qualifications, declaration, EXACT, records["deployment"],
                                     records["evidence"], inputs["evidence"], schema_fingerprint, now=int(time.time()))
        approval_identity_separation(store, action, grant, grant_raw, records, record_raw, int(time.time()))
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
        if STATEFUL_CONTEXT:
            audit["state_contract"] = binding.STATE_CONTRACT
        if resume and intent.exists():
            original, _ = store.read(intent)
            need(all(original.get(k) == audit[k] for k in (
                "workflow", "action", "technical_audit_id", "technical_grant_sha256", "host", "operator",
                "candidate", "producer", "input_sha256", "independent_decision_references")),
                "RECOVERY_INTENT_IDENTITY_DRIFT")
            audit = original
        audit_raw = (json.dumps(audit, sort_keys=True) + "\n").encode()
        phase = "BEFORE_FIRST_WRITE"

        def write(path, data):
            nonlocal phase
            phase = "ATTEMPTING:" + path.name
            if resume and (path.exists() or path.is_symlink()):
                verified_existing(store, path, data)
            else:
                store.create(path, data)
            phase = "VERIFIED:" + path.name

        def sync_dirs(*paths):
            for path in paths:
                fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)

        try:
            if resume:
                # FIRST withdraw the policy, retaining its exact bytes. The
                # pending directory binds the independent grant SHA. After any
                # successfully persisted revoke intent the frozen reader sees
                # no active policy, even if later audit publication fails.
                phase = "REVOKE_WITHDRAWAL_ATTEMPTED"
                if not pending_dir.exists():
                    pending_dir.mkdir(mode=0o700)
                store.writer._directories(pending_dir)
                sync_dirs(store.audit)
                if store.path("policy").exists():
                    need(not pending_policy.exists() and not archive.exists()
                         and store.path("policy").stat().st_dev == pending_dir.stat().st_dev,
                         "REVOCATION_ARCHIVE_UNSAFE")
                    os.rename(store.path("policy"), pending_policy)
                sync_dirs(store.qualifications, pending_dir)
                location = pending_policy if pending_policy.exists() else archive
                verified_existing(store, location, record_raw["policy"])
                phase = "REVOKE_WITHDRAWAL_VERIFIED"
            # Before approval publication, persist the one-shot audit. An error
            # is UNKNOWN, never a claim that no protected bytes were written.
            write(intent, audit_raw)
            write(store.audit / (grant["audit_id"] + ".technical-decision.json"), grant_raw)
            write(marker, audit_raw)
            for path in decisions.values():
                write(path, audit_raw)
            if resume:
                if pending_policy.exists():
                    phase = "REVOKE_ARCHIVE_MOVE_ATTEMPTED"
                    need(not archive.exists() and not archive.is_symlink(), "REVOCATION_ARCHIVE_UNSAFE")
                    os.rename(pending_policy, archive)
                sync_dirs(pending_dir, store.audit)
                verified_existing(store, archive, record_raw["policy"])
                write(revoked, audit_raw)
            else:
                # Approval is last among the authority records; completion is
                # separate. Frozen consumers still need an approved contract
                # successor to reject the publication-to-completion crash gap.
                for name in sorted(names, key=lambda n: n == "deployment"):
                    write(store.path(name), record_raw[name])
            write(operation_path(store, action, "complete"), (json.dumps({
                "state": "PREPARED" if STATEFUL_CONTEXT else "VERIFIED_COMMITTED", "action": action, "intent_sha256": sha(audit_raw)},
                sort_keys=True) + "\n").encode())
        except (OSError, ValueError) as exc:
            raise CommitUnknown(action, phase) from exc
        return {"status": "PREPARED_REQUIRES_DURABILITY_RECONCILIATION" if STATEFUL_CONTEXT else "RECORD_SEALED_NOT_INSTALL_OR_RELEASE_AUTHORITY", "action": action,
                "audit_id": grant["audit_id"], "input_sha256": inputs, "deployment_executed": False}


def reconcile(store, action, *, grant_sha, producer, operator, host, now, manifest,
              schema_fingerprint, attest=tty_attest, publish=False):
    """Finish durability of ONE existing operation, never reissue its decision.

    Only completion/reconciliation metadata may be added. Missing authority,
    outputs or original audit bytes cannot be reconstructed here. Original
    technical authority, expiry and exact bytes remain mandatory.
    """
    need(STATEFUL_CONTEXT and binding.is_stateful(manifest)
         and action in {"seal-policy", "seal-release-records"}, "RECONCILIATION_SCOPE_REQUIRED")
    need(producer.get("source_commit") == EXACT["source_commit"]
         and producer.get("source_tree") == EXACT["source_tree"], "STATEFUL_PRODUCER_SOURCE_DRIFT")
    with store.lock():
        binding.require_release_lock()
        need(not list(store.audit.glob(EXACT["release_id"] + ".revocation-*"))
             and not operation_path(store, "revoke-policy", "intent").exists()
             and not (store.audit / (EXACT["release_id"] + ".revoked.json")).exists(),
             "REVOKED_OR_REVOKING_CANNOT_RECONCILE")
        intent_path = operation_path(store, action, "intent")
        intent, intent_raw = store.read(intent_path)
        need(intent.get("state_contract") == binding.STATE_CONTRACT and intent.get("action") == action
             and intent.get("candidate") == EXACT and intent.get("producer") == producer
             and intent.get("technical_grant_sha256") == grant_sha
             and intent.get("host") == host and intent.get("operator") == operator
             and isinstance(intent.get("technical_audit_id"), str)
             and ID.fullmatch(intent["technical_audit_id"]), "RECONCILIATION_INTENT_DRIFT")
        grant_path = store.audit / (intent["technical_audit_id"] + ".technical-decision.json")
        grant, grant_raw = store.read(grant_path)
        technical_valid(grant, expected_sha=grant_sha, raw=grant_raw, action=action,
            inputs=intent["input_sha256"], host=host, operator=operator, producer=producer, now=now)
        names = ("policy",) if action == "seal-policy" else ("deployment", "evidence")
        records, record_raw = {}, {}
        for name in names:
            records[name], record_raw[name] = store.read(store.path(name))
            need(sha(record_raw[name]) == intent["input_sha256"].get(name), "RECONCILIATION_OUTPUT_DRIFT")
        # This is not another sealing decision; original consumed ID must not
        # be accepted by seal() again. Relation/type checks below use original
        # immutable decision bytes and existing consumer decision checks.
        if action == "seal-policy":
            policy_valid(records["policy"], now)
        else:
            binding._p0_decisions(store.qualifications, manifest["runtime_only_release"], EXACT,
                records["deployment"], records["evidence"], sha(record_raw["evidence"]), schema_fingerprint, now=now)
        need(grant["audit_id"] not in {record.get("audit_id") for record in records.values()}
             and (action != "seal-release-records" or grant["audit_id"] != store.read(store.path("policy"))[0]["audit_id"]),
             "TECHNICAL_AND_OWNER_DECISIONS_MUST_BE_SEPARATE")
        audit_paths = [intent_path, store.audit / (grant["audit_id"] + ".json")]
        audit_paths += [store.audit / ("decision-" + intent["input_sha256"][n] + ".json") for n in names if n != "evidence"]
        need(all(store.read(path)[1] == intent_raw for path in audit_paths), "RECONCILIATION_AUDIT_DRIFT")
        complete = operation_path(store, action, "complete")
        complete_raw = (json.dumps({"state": "PREPARED", "action": action,
                                   "intent_sha256": sha(intent_raw)}, sort_keys=True) + "\n").encode()
        if complete.exists() or complete.is_symlink():
            need(store.read(complete)[1] == complete_raw, "RECONCILIATION_COMPLETION_DRIFT")
        receipt = {"schema_version": 1, "contract_id": binding.STATE_CONTRACT,
                   "scope": "DURABILITY_RECONCILIATION", "state": "VERIFIED_COMMITTED",
                   "action": action, "candidate": EXACT, "binding_sha256": BINDING_SHA,
                   "intent_sha256": sha(intent_raw), "completion_sha256": sha(complete_raw),
                   "record_sha256": {name: sha(data) for name, data in record_raw.items()}}
        receipt_raw = (json.dumps(receipt, sort_keys=True) + "\n").encode()
        output = operation_path(store, action, "reconciled")
        if output.exists() or output.is_symlink():
            need(store.read(output)[1] == receipt_raw, "RECONCILIATION_RECORD_DRIFT")
        if not publish:
            return {"status": "RECONCILIATION_INSPECTED_NOT_AUTHORITY", "writes": 0}
        attest(grant_sha, intent["input_sha256"])
        # TTY latency invalidates the earlier time-window result. Recheck all
        # applicable decisions at one fresh instant, under the same lock and
        # before adding completion/reconciliation bytes. Refusal retains all
        # earlier evidence; it does not assert that earlier writes never occurred.
        post_attest_now = int(time.time())
        technical_valid(grant, expected_sha=grant_sha, raw=grant_raw, action=action,
            inputs=intent["input_sha256"], host=host, operator=operator, producer=producer, now=post_attest_now)
        binding.reject_revocation(store.qualifications, EXACT["release_id"])
        if action == "seal-policy":
            policy_valid(records["policy"], post_attest_now)
        else:
            binding._p0_decisions(store.qualifications, manifest["runtime_only_release"], EXACT,
                records["deployment"], records["evidence"], sha(record_raw["evidence"]),
                schema_fingerprint, now=post_attest_now)
        phase = "RECONCILIATION_READBACK"
        try:
            for path in audit_paths:
                verified_existing(store, path, intent_raw)
            verified_existing(store, grant_path, grant_raw)
            for name in names:
                verified_existing(store, store.path(name), record_raw[name])
            phase = "RECONCILIATION_COMPLETION"
            if not complete.exists():
                store.create(complete, complete_raw)
            verified_existing(store, complete, complete_raw)
            phase = "RECONCILIATION_RECEIPT"
            if not output.exists():
                store.create(output, receipt_raw)
            verified_existing(store, output, receipt_raw)
            binding.state_qualification(store.qualifications, manifest["runtime_only_release"], EXACT,
                receipt["record_sha256"], actions=(action,))
        except (OSError, ValueError) as exc:
            raise CommitUnknown(action, phase) from exc
        return {"status": "EXISTING_OPERATION_DURABILITY_RECONCILED_NOT_NEW_AUTHORITY", "action": action,
                "decision_reissued": False, "deployment_executed": False}


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
    parser.add_argument("--reconcile", action="store_true", help="verify existing operation; never rewrite authority records")
    args = parser.parse_args(argv)
    try:
        store = Store()
        need(not (args.seal and args.reconcile), "MUTUALLY_EXCLUSIVE_WRITE_MODES")
        need(STATEFUL_CONTEXT, "VERIFIED_STATEFUL_ENTRY_REQUIRED")
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
        need((args.reconcile or args.technical_grant is not None) and args.operator is not None
             and isinstance(args.producer_source, str) and binding.COMMIT.fullmatch(args.producer_source)
             and isinstance(args.producer_tree, str) and binding.COMMIT.fullmatch(args.producer_tree),
             "INDEPENDENT_TECHNICAL_APPROVAL_REQUIRED")
        blob = git_reader(args.git_repository, args.producer_source, args.producer_tree,
                          binding.STATE_BASE_SOURCE)
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
        if args.reconcile:
            from scripts import rollback_preflight as gate
            from scripts.release_runtime_recovery import MIGRATION
            lock014 = gate.epoch_contract(gate.read_json(ROOT / "deploy/rollback_compatibility.json"))["schema_migrations"]
            fingerprint = binding.digest(lock014 + [{k: MIGRATION[k] for k in ("version", "filename", "canonical_sha256")}])
            print(json.dumps(reconcile(store, args.action,
                grant_sha=args.independently_approved_technical_sha256, producer=producer,
                operator=args.operator, host=socket.gethostname(), now=int(time.time()), manifest=manifest,
                schema_fingerprint=fingerprint, publish=True)))
            return 0
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
    except CommitUnknown as exc:
        print(json.dumps({"status": "COMMIT_UNKNOWN", "check": str(exc), "action": exc.action,
                          "phase": exc.phase, "writes": "MAY_HAVE_COMMITTED",
                          "next_action": "STOP_INDEPENDENT_PROTECTED_RECONCILIATION_REQUIRED"}))
        return 3
    except Exception as exc:
        code = str(exc) if isinstance(exc, (Blocked, binding.BindingBlocked)) else "BOOTSTRAP_INPUT_OR_FILESYSTEM_BLOCKED"
        print(json.dumps({"status": "BLOCKED", "check": code}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
