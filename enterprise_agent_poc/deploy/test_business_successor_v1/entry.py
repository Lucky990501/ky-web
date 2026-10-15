"""One exact PRIMARY Test d8a -> 894 transition; external tooling, NOT Application.

No issuer, paid-call permission, migration, DCA cleanup or Production action.
The operator supplies an actually approved, protected scope; this file never
creates approval. Formal Git-install prefix / atomic switch / units are reused
from their pinned installed Test entry, not the destructive old suffix.
"""
from __future__ import annotations

import ast
import base64
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import shlex
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import types
from urllib.parse import unquote, urlsplit

CONTRACT = "PRIMARY_TEST_D8A_894_BUSINESS_SUCCESSOR_V2"
HOST = "iv-yew0uhfksgbw80bm48mv"
ROOT = Path("/opt/enterprise-agent-workbench-test")
CONTROL = Path("/etc/enterprise-agent-test-business-894-successor-v2")
OP = CONTROL / "operation"
SOURCE = "894bdd2a031725a0d0df3b95d6dfc26d08f0e4ab"
TREE = "bb9317207372ad1a215eaeebb7a2b0151a35be4d"
GIT_PARENT = "d11c2fb5ddb6203eb5cee23832fafec47a2781f0"
OLD_SOURCE = "d8a7814f1ceb528cad8a2d3b4c7a5aa135f6ccf7"
OLD_TREE = "6021a1f5017b92c3e5b785db1dd6bfcbb190d7d5"
RID = "20261014-894bdd2-business-p0-test-v1"
RELEASE = ROOT / "releases" / RID
OLD_RELEASE = ROOT / "releases/20261008-d8a7814-wechat-personal-v1"
PYTHON = Path("/usr/bin/python3.10")
APPLICATION_PYTHON = ROOT / "shared/runtime/python311/bin/python"
RECOVERY_FILE = "existing_recovery_bound.py"
CODE_FILES = ("entry.py", RECOVERY_FILE, "runtime_probe.py", "registry_extension.py", "verified_entry.py")
BACKUP_ROOT = ROOT / "backups/business-894-successor-v2"
PG = ROOT / "shared/runtime/postgresql-16.6/bin"
REPO = ROOT / "shared/source/business-894-exact-v1"
LOCK = Path("/run/lock/enterprise-agent-test-successor-v1.lock")
FORMAL = Path("/etc/enterprise-agent-test-successor-chat-reference-v1/entry.py")
FORMAL_PIN = "5f91603551e38119a294de7ecdce457ef19d557e902c1af0cfc2726f49af6b9e"
DCA = Path("/etc/enterprise-agent-test-successor-wechat-dca-v1/entry.py")
OLD_GUARD = Path("/etc/enterprise-agent-test-wechat-persistent-config-v1/primary_guard.py")
OLD_GUARD_PIN = "5ba582e45b55e35aaeae83d5de995db00853ae83df45f0dab58d7d361c0c2c47"
KEY = ROOT / "shared/credentials/wechat-tenant-secret-d8a-seeding-approval-v1.key"
SECRET = ROOT / "data/application/tenant-secrets/test/9a68955cf97b027ae79a199d77750e8ea218486f4e3505cfaf8dd1d259d32476.fernet"
SECRET_KEY_ENV = "ENTERPRISE_POC_TENANT_SECRET_KEY_FILE"
METADATA_DIRECTORIES = (
    Path("/etc/enterprise-agent-test-wechat-persistent-config-v1"),
    Path("/etc/enterprise-agent-test-wechat-persistent-config-recovery-v1"),
    Path("/etc/enterprise-agent-test-tenant-seeding-v1"),
    Path("/etc/enterprise-agent-test-successor-wechat-d8a-seeding-approval-v1"))
SECRET_KEY_DECLARATION = METADATA_DIRECTORIES[-1] / "common.py"
CURRENT_SECRET_WITNESS = METADATA_DIRECTORIES[0] / "current-state-witness.v1.json"
SECRET_KEY_DROPINS = tuple(Path("/etc/systemd/system") / ("enterprise-agent-test-" + role + ".service.d") /
    "zz-wechat-personal-d8a-seeding-approval-v1.conf" for role in ("api", "mcp", "worker"))
TENANT = "internal-test-staging-v1"
USER = "integrated-test-user-v1"
ROLES = ("mcp", "api", "worker")
DROP_NAME = "zzzzz-business-894-successor-v1.conf"
DEP = "0ee0a997e95f650a5be699075ec214dd7d9c2d94a94d8c7a7becfd0397fd1008"
MUTABLE = frozenset({"tasks", "task_events", "conversations", "messages", "run_traces",
                     "generations", "credit_accounts", "credit_transactions", "execution_events",
                     "chat_image_attachments"})


class Blocked(RuntimeError):
    pass


def need(value, code):
    if not value:
        raise Blocked(code)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                      ensure_ascii=False, default=str).encode()).hexdigest()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_bytes())


def native(path, *, readonly=True):
    path = Path(path)
    for ancestor in (path, *path.parents):
        info = ancestor.lstat()
        need(not stat.S_ISLNK(info.st_mode) and info.st_uid == 0 and info.st_gid in (0, 1000),
             "ROOT_ANCESTOR_TRUST")
        if (path == LOCK == Path("/run/lock/enterprise-agent-test-successor-v1.lock")
                and ancestor == Path("/run/lock")):
            # This ONE existing global lock, not a general evidence/code path.
            # Sticky + Root ownership prevents an ordinary user unlinking or
            # replacing its Root-owned inode. Never chmod the system directory.
            need(stat.S_ISDIR(info.st_mode) and (info.st_uid, info.st_gid) == (0, 0)
                 and stat.S_IMODE(info.st_mode) == 0o1777, "EXACT_ROOT_STICKY_LOCK_PARENT")
        else:
            need(not info.st_mode & 0o022, "ROOT_ANCESTOR_TRUST")
    info = path.lstat()
    need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "REGULAR_SINGLE_LINK")
    if path == Path("/run/lock/enterprise-agent-test-successor-v1.lock"):
        need((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (0, 0, 0o644),
             "EXACT_ROOT_GLOBAL_LOCK_FILE")
    need(not readonly or not info.st_mode & 0o222, "READONLY_RECORD")


def pinned(path, expected):
    native(path)
    need(sha(path) == expected, "CODE_PIN_BEFORE_EXECUTION")


def run(argv, *, env=None, timeout=180, **kwargs):
    # Shared Test runtimes/tools are owned by UID1000. Never execute their
    # bytecode or ELF binaries with Root privileges. Root uses OS stdlib only.
    program = Path(str(argv[0]))
    if program.name == "git":
        argv = ["/usr/bin/git", "--no-optional-locks", "-c", "core.fsmonitor=false",
                "-c", "core.hooksPath=/dev/null", *argv[1:]]
        env = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "APP_ENV": "test",
               "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_OPTIONAL_LOCKS": "0"}
    if os.geteuid() == 0 and program.is_absolute() and program.is_relative_to(ROOT / "shared/runtime"):
        argv = ["/usr/sbin/runuser", "-u", "lucky", "--", *argv]
    result = subprocess.run([str(x) for x in argv], capture_output=True,
                            timeout=timeout, env=env, **kwargs)
    need(result.returncode == 0, "COMMAND_FAILED:" + Path(str(argv[0])).name)
    return result.stdout


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def record(path, value, mode=0o400):
    raw = (json.dumps(value, sort_keys=True, indent=2, default=str) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    fsync_dir(Path(path).parent)
    need(Path(path).read_bytes() == raw, "RECORD_READBACK")


def context(runtime=False):
    need(socket.gethostname() == HOST and os.geteuid() == (1000 if runtime else 0), "EXACT_TEST_ACTOR_HOST")
    need("enterprise-agent-test.slice" in Path("/proc/self/cgroup").read_text(), "TEST_RESOURCE_SLICE")
    need(sys.dont_write_bytecode, "NO_BYTECODE_REQUIRED")
    need(Path(sys.executable).resolve() == PYTHON.resolve(), "EXACT_TEST_PYTHON")
    space = os.statvfs(ROOT)
    need(space.f_bavail / space.f_blocks > .2, "TEST_DISK_RESERVE")


def trusted_python(value):
    real_python = PYTHON.resolve(strict=True)
    need(str(real_python) == value.get("python_real_path"), "PYTHON_ALIAS_BINDING")
    for parent in PYTHON.parents:
        info = parent.lstat()
        need(not stat.S_ISLNK(info.st_mode) and info.st_uid == info.st_gid == 0
             and not info.st_mode & 0o022, "PYTHON_LOGICAL_ANCESTOR")
    native(real_python, readonly=False)
    need(sha(real_python) == value.get("python_sha256"), "PYTHON_PIN")
    app = APPLICATION_PYTHON.resolve(strict=True)
    need(str(app) == value.get("application_python_real_path")
         and app.is_relative_to(ROOT / "shared/runtime")
         and sha(app) == value.get("application_python_sha256"), "APPLICATION_PYTHON_PIN")
    info = app.stat()
    need((info.st_uid, info.st_gid) == (1000, 1000) and not info.st_mode & 0o022,
         "APPLICATION_RUNTIME_UNPRIVILEGED_OWNER")


def authority(action, clock=time.time):
    """Fresh protected external decision, never a cached approved=True flag."""
    path = CONTROL / "scope.approved.v1.json"
    native(path)
    value = read(path)
    need(value.get("contract") == CONTRACT and value.get("host") == HOST
         and value.get("source") == SOURCE and value.get("tree") == TREE
         and value.get("git_parent") == GIT_PARENT
         and value.get("runtime_predecessor_source") == OLD_SOURCE
         and value.get("runtime_predecessor_tree") == OLD_TREE
         and value.get("environment") == "test" and value.get("production_authority") is False,
         "EXACT_SCOPE")
    need(value.get("operation_path") == str(OP) and value.get("lock_path") == str(LOCK),
         "EXACT_PROTECTED_OPERATION_AND_SHARED_LOCK_BINDING")
    need(value.get("operator") == "06_CODEX_AUTHORIZED_LUCKY_SUDO"
         and value.get("actions") == ["install", "registry", "backup", "restore-rehearsal", "activate", "recover"],
         "EXACT_EXECUTION_SCOPE")
    for field in ("owner_decision_reference", "technical_decision_reference"):
        need(isinstance(value.get(field), str) and len(value[field]) >= 8, "ACTUAL_DECISION_REFERENCE")
    instant = clock()
    need(type(value.get("issued_at")) is int and type(value.get("expires_at")) is int
         and value["issued_at"] <= instant < value["expires_at"]
         and 0 < value["expires_at"] - value["issued_at"] <= 14400,
         "AUTHORITY_EXPIRED_OR_NOT_ISSUED")
    need(not (CONTROL / "REVOKED").exists() and value.get("revoked") is False,
         "AUTHORITY_REVOKED")
    need(value.get("automatic_retry") is False and value.get("provider_budget") == 0
         and value.get("migration_action") == "NONE" and value.get("restore_exact_test_data") is True,
         "NO_RETRY_NO_PROVIDER_NO_MIGRATION")
    need(action in value["actions"], "ACTION_NOT_AUTHORIZED")
    pins = value.get("code_sha256", {})
    need(set(pins) == set(CODE_FILES), "FIRST_BYTE_CODE_PIN_SET")
    for name, pin in pins.items():
        pinned(CONTROL / name, pin)
    trusted_python(value)
    need(value.get("registry_scope") == {
        "slug": "wechat-official-account-writing", "version": "1.0.1",
        "sha256": "6b37d4769cad4a8e0301a2b8c01fd90ca7ab8e0229c3e8df1e46d8a9d525a03b",
        "registrar_user_id": USER, "admin": "EXISTING_FORMAL_INITIALIZER_TRANSACTION_ONLY",
        "bindings": "UNCHANGED", "provider_budget": 0, "recovery": "KEEP_REGISTERED_PACKAGE_AND_BUSINESS_DATA"},
        "EXACT_REGISTRY_ADMIN_SCOPE")
    need(value.get("restore_reserve_seconds") == 3600, "RESTORE_RESERVE")
    if action != "recover":
        need(value["expires_at"] - instant >= 3600, "NO_NEW_WORK_IN_RESTORE_RESERVE")
    return value


def runtime_call(command, *args, payload=None):
    """Only demoted processes may import application, psycopg/Redis/MCP libs."""
    if (CONTROL / "runtime-binding.v1.json").exists():
        doc = read(CONTROL / "runtime-binding.v1.json")
    else:
        native(CONTROL / "scope.approved.v1.json")
        doc = read(CONTROL / "scope.approved.v1.json")
    native(CONTROL / "runtime-binding.v1.json" if (CONTROL / "runtime-binding.v1.json").exists()
           else CONTROL / "scope.approved.v1.json")
    for name in CODE_FILES:
        pinned(CONTROL / name, doc["code_sha256"][name])
    app = APPLICATION_PYTHON.resolve(strict=True)
    need(str(app) == doc["application_python_real_path"] and sha(app) == doc["application_python_sha256"],
         "RUNTIME_PIN_BEFORE_DEMOTED_EXECUTION")
    argv = [APPLICATION_PYTHON, "-I", "-B", CONTROL / "runtime_probe.py", command, *map(str, args)]
    if os.geteuid() == 0: argv = ["/usr/sbin/runuser", "-u", "lucky", "--", *argv]
    raw = run(argv, input=None if payload is None else json.dumps(payload).encode(), timeout=180)
    return json.loads(raw)


@contextmanager
def locked():
    native(LOCK, readonly=False)
    before = LOCK.lstat()
    fd = os.open(LOCK, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        def same_inode():
            opened = os.fstat(fd)
            current = LOCK.lstat()
            fields = ("st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink")
            need(stat.S_ISREG(opened.st_mode) and opened.st_nlink == 1
                 and all(getattr(before, field) == getattr(opened, field) == getattr(current, field)
                         for field in fields), "GLOBAL_LOCK_OPENED_INODE_CHANGED")
        same_inode()
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        native(LOCK, readonly=False)
        same_inode()  # reject name/inode replacement during acquisition too
        yield
    finally:
        os.close(fd)


def extract_function(path, expected, name, c, *, prefix=False):
    """Reuse exact sealed functions; unreachable migration/cleanup suffix omitted."""
    pinned(path, expected)
    tree = ast.parse(path.read_bytes())
    fn = next(x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name == name)
    if prefix:
        end = next(i for i, node in enumerate(fn.body) if isinstance(node, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "migration" for t in node.targets))
        fn.body = fn.body[:end] + [ast.Return(value=ast.Tuple(elts=[
            ast.Name(id="files", ctx=ast.Load()), ast.Name(id="snapshot", ctx=ast.Load())], ctx=ast.Load()))]
        # Keep original installation body/guards. Add fresh authority at the
        # final directory-rename boundary, after potentially slow Git reads.
        for node in ast.walk(fn):
            if isinstance(node, ast.With):
                for index, statement in enumerate(list(node.body)):
                    if (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call)
                            and isinstance(statement.value.func, ast.Attribute)
                            and statement.value.func.attr == "rename"):
                        node.body.insert(index, ast.Expr(value=ast.Call(func=ast.Attribute(
                            value=ast.Name(id="c", ctx=ast.Load()), attr="before_install_mutation", ctx=ast.Load()),
                            args=[], keywords=[])))
    namespace = dict(c=c, Path=Path, tempfile=tempfile, tarfile=tarfile, json=json,
                     os=os, pwd=pwd, hashlib=hashlib, old_preflight=lambda env: old_state())
    exec(compile(ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])),
                 str(path) + "::exact-test-only-function", "exec"), namespace)
    return namespace[name]


def formal(name, *, prefix=False):
    c = types.SimpleNamespace(R=ROOT, P=CONTROL, REPO=REPO, PY=APPLICATION_PYTHON, SOURCE=SOURCE,
        TREE=TREE, RID=RID, RELEASE=RELEASE, OLD_RELEASE=OLD_RELEASE, need=need,
        sha=sha, run=run, hashlib=hashlib, source_identity=lambda: source_identity(RELEASE),
        before_install_mutation=lambda: authority("install"))
    return extract_function(FORMAL, FORMAL_PIN, name, c, prefix=prefix)


def old_guard():
    need(os.geteuid() == 1000, "NO_PRIVILEGED_APPLICATION_IMPORT")
    pinned(OLD_GUARD, OLD_GUARD_PIN)
    for root in (OLD_GUARD.parent,
                 Path("/etc/enterprise-agent-test-successor-wechat-d8a-seeding-approval-v1"),
                 Path("/etc/enterprise-agent-test-successor-wechat-personal-d8a-v1"),
                 Path("/etc/enterprise-agent-test-successor-wechat-dca-v1")):
        need(not any(root.rglob("*.pyc")), "UNLISTED_CACHED_GUARD_BYTES")
    # Execute verified source, not importlib's cache-loading path. Historical
    # transitive modules retain their existing sealed Root trust contract.
    obj = types.ModuleType("existing_sealed_d8a_guard")
    obj.__file__ = str(OLD_GUARD)
    exec(compile(OLD_GUARD.read_bytes(), str(OLD_GUARD), "exec"), obj.__dict__)
    return obj


def env_check(env):
    db = urlsplit(env.get("ENTERPRISE_POC_DATABASE_URL", ""))
    rd = urlsplit(env.get("REDIS_URL", ""))
    need((db.hostname, db.port, db.username, db.path) ==
         ("127.0.0.1", 55432, "enterprise_agent_test", "/enterprise_agent_test"), "TEST_DATABASE_ONLY")
    need((rd.hostname, rd.port, rd.username, rd.path) in (
         ("127.0.0.1", 56379, "enterprise_agent_test", "/0"),
         ("127.0.0.1", 56379, "enterprise_agent_test", "")), "TEST_REDIS_ONLY")
    need(env.get("APP_ENV") == "test" and env.get("ENTERPRISE_POC_BOOTSTRAP_DEMO_DATA") == "false",
         "NO_STARTUP_RESEED")
    need(env.get("ENTERPRISE_POC_MODEL_PROVIDER_ID") == "disabled-test-provider"
         and env.get("ENTERPRISE_POC_MODEL_BASE_URL") == "http://127.0.0.1:9/disabled"
         and env.get("ENTERPRISE_POC_IMAGE_PROVIDER_ID") == "disabled-test-provider"
         and env.get("ENTERPRISE_POC_WECHAT_NETWORK_ALLOWED") == "false", "NO_PAID_EGRESS_AUTHORITY")


def source_identity(path, source=SOURCE, tree=TREE):
    args = ["/usr/bin/git", "--no-optional-locks", "-c", "safe.directory=" + str(path), "-C", path]
    actual = run(args + ["rev-parse", "HEAD", "HEAD^{tree}"]).decode().splitlines()
    need(actual == [source, tree], "SOURCE_TREE")
    need(not run(args + ["status", "--porcelain", "--untracked-files=no"]).strip(), "DIRTY_SOURCE")


def old_state(topology=True):
    env_check(os.environ)
    need((ROOT / "current").resolve() == OLD_RELEASE, "EXACT_D8A_RUNTIME_PREDECESSOR")
    source_identity(OLD_RELEASE, OLD_SOURCE, OLD_TREE)
    return runtime_call("old-state", int(topology))


def snapshot():
    """Reuse sealed readonly catalog/row collector, no seeding module invocation."""
    env_check(os.environ)
    return runtime_call("snapshot")


def row_pins(data):
    return {name: sorted(digest(row) for row in rows) for name, rows in data.items()}


def metadata_inventory():
    """Bounded complete preflight: collect ALL missing/unsafe references.
    Only paths/stat/hash are returned, never config/credential contents.
    Nothing is optional merely because a legacy directory is missing.
    """
    paths = {KEY, SECRET, SECRET_KEY_DECLARATION, CURRENT_SECRET_WITNESS, *SECRET_KEY_DROPINS,
             ROOT / "shared/config/test-environment.env", ROOT / "shared/credentials/test-environment.secrets.env"}
    paths.update(METADATA_DIRECTORIES[0] / name for name in (
        "approval.v1.json", "policy.v1.json", "terminal-task-witness.v1.json",
        "primary_guard.py", "wechat_persistent_config_guard.py", "scripts/receipt_row_canonicalization.py"))
    result, issues = {}, []
    def issue(path, code): issues.append({"path": str(path), "code": code})
    def ancestry(path):
        for parent in path.parents:
            info = parent.lstat()
            need(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
                 and info.st_uid in (0, 1000) and not info.st_mode & 0o022,
                 "TEST_REFERENCE_ANCESTOR")
    for directory in METADATA_DIRECTORIES:
        try:
            info = directory.lstat()
            need(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
                 and info.st_uid == 0 and not info.st_mode & 0o022, "METADATA_DIRECTORY_TRUST")
            ancestry(directory)
            paths.update(path for path in directory.rglob("*") if path.is_file() or path.is_symlink())
        except FileNotFoundError: issue(directory, "REQUIRED_METADATA_DIRECTORY_MISSING")
        except (OSError, Blocked): issue(directory, "REQUIRED_METADATA_DIRECTORY_UNTRUSTED")
    for path in sorted(paths):
        try:
            info = path.lstat()
            need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid in (0, 1000)
                 and not info.st_mode & 0o022, "TEST_REFERENCE_PATH")
            ancestry(path)
            if path in (KEY, SECRET):
                need((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (1000, 1000, 0o600),
                     "CURRENT_SECRET_FILE_PROTECTION")
            if path in SECRET_KEY_DROPINS or path == SECRET_KEY_DECLARATION:
                native(path, readonly=False)
            result[str(path)] = dict(sha256=sha(path), mode=stat.S_IMODE(info.st_mode),
                                    uid=info.st_uid, gid=info.st_gid, size=info.st_size)
        except FileNotFoundError: issue(path, "REQUIRED_METADATA_PATH_MISSING")
        except (OSError, Blocked): issue(path, "REQUIRED_METADATA_PATH_UNTRUSTED")
    if str(SECRET_KEY_DECLARATION) in result:
        try:
            tree = ast.parse(SECRET_KEY_DECLARATION.read_bytes())
            values = [n.value for n in tree.body if isinstance(n, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == "KEY_FILE" for t in n.targets)]
            expected = ast.parse("R / " + repr(str(KEY.relative_to(ROOT))), mode="eval").body
            need(len(values) == 1 and ast.dump(values[0]) == ast.dump(expected), "D8A_KEY_FILE_CONTRACT")
        except (SyntaxError, OSError, Blocked): issue(SECRET_KEY_DECLARATION, "D8A_KEY_FILE_CONTRACT_MISMATCH")
    for path in SECRET_KEY_DROPINS:
        if str(path) not in result: continue
        try:
            refs = [token.split("=", 1)[1] for line in path.read_text().splitlines()
                    if line.strip().startswith("Environment=")
                    for token in shlex.split(line.strip().split("=", 1)[1])
                    if token.startswith(SECRET_KEY_ENV + "=")]
            need(refs == [str(KEY)], "CURRENT_SERVICE_SECRET_REFERENCE")
        except (ValueError, OSError, Blocked): issue(path, "CURRENT_SERVICE_SECRET_REFERENCE_MISMATCH")
    if str(CURRENT_SECRET_WITNESS) in result and str(SECRET) in result:
        try:
            native(CURRENT_SECRET_WITNESS)
            expected = read(CURRENT_SECRET_WITNESS)["encrypted_secret_record"]
            actual = result[str(SECRET)]
            need(expected["path"] == str(SECRET) and expected["sha256"] == actual["sha256"]
                 and expected["size"] == actual["size"], "CURRENT_ENCRYPTED_SECRET_WITNESS")
        except (KeyError, ValueError, OSError, Blocked):
            issue(SECRET, "CURRENT_ENCRYPTED_SECRET_WITNESS_MISMATCH")
    if SECRET_KEY_ENV in os.environ and os.environ[SECRET_KEY_ENV] != str(KEY):
        issue(KEY, "CURRENT_PROCESS_SECRET_REFERENCE_MISMATCH")
    return {"status": "PASS" if not issues else "BLOCKED", "files": result,
            "issues": issues, "key_file": str(KEY), "ciphertext": str(SECRET)}


def metadata_pins():
    report = metadata_inventory()
    need(report["status"] == "PASS", "REQUIRED_METADATA_REFERENCES_BLOCKED:" + json.dumps(report["issues"], sort_keys=True))
    return report["files"]


def data_guard(schema, data, witness):
    need(digest(schema) == witness["schema_sha256"] and set(data) == set(witness["rows"]), "SCHEMA_OR_TABLE_DRIFT")
    pins = row_pins(data)
    for table, expected in witness["rows"].items():
        if table not in MUTABLE:
            need(pins[table] == expected, "PROTECTED_DATA_DRIFT:" + table)
            continue
        if table == "credit_accounts":
            baseline = witness["credit_accounts"]
            need(len(data[table]) == len(baseline), "ACCOUNT_CARDINALITY")
            for row in data[table]:
                original = next((x for x in baseline if x["tenant_id"] == row["tenant_id"]), None)
                need(original is not None, "ACCOUNT_SCOPE")
                if row["tenant_id"] != TENANT:
                    need(digest(row) == digest(original), "OTHER_TENANT_CREDIT")
                else:
                    need(row.get("balance", 0) >= 0, "NEGATIVE_CREDIT")
                    need({k: v for k, v in row.items() if k not in ("balance", "updated_at")}
                         == {k: v for k, v in original.items() if k not in ("balance", "updated_at")},
                         "CREDIT_ACCOUNT_IDENTITY_OR_LIMIT")
            continue
        need(all(pin in pins[table] for pin in expected), "HISTORICAL_ROW_MUTATION:" + table)
        # New rows only, never exempt baseline or unrelated tenant/user rows.
        for row in data[table]:
            if digest(row) in expected:
                continue
            if "tenant_id" in row:
                need(row["tenant_id"] == TENANT, "OTHER_TENANT_WRITE")
            if "user_id" in row:
                need(row["user_id"] == USER, "OTHER_USER_WRITE")
            if table == "tasks":
                need(row.get("agent_id") in ("image-agent", "campaign-agent", "copywriting-agent"), "TASK_AGENT_SCOPE")
    tasks = {r["id"]: r for r in data.get("tasks", [])}
    conversations = {r["id"]: r for r in data.get("conversations", [])}
    for table in ("task_events", "credit_transactions", "chat_image_attachments"):
        for row in data.get(table, []):
            if digest(row) in witness["rows"][table]:
                continue
            task = tasks.get(row.get("task_id"))
            need(task and task.get("tenant_id") == TENANT and task.get("user_id") == USER, "TASK_CHILD_SCOPE")
    for table in ("messages", "generations", "run_traces", "execution_events"):
        for row in data.get(table, []):
            if digest(row) in witness["rows"][table]:
                continue
            cid = row.get("conversation_id")
            if cid:
                need(cid in conversations and conversations[cid]["tenant_id"] == TENANT, "CONVERSATION_CHILD_SCOPE")
            elif table != "execution_events":
                need(row.get("tenant_id") == TENANT, "NULL_CONVERSATION_SCOPE")
            else:
                payload = row.get("payload", {})
                payload = json.loads(payload) if isinstance(payload, str) else payload
                need(payload.get("tenant_id") == TENANT and payload.get("actor_id", USER) == USER
                     and not row.get("event_type", "").startswith(("tenant_secret.", "wechat.account.")),
                     "NO_SECRET_OR_UNSCOPED_AUDIT_MUTATION")
    return "EXACT_BASELINE_PRESERVED_BOUNDED_TEST_ROWS_ONLY"


def idle(schema, data):
    need(not any(row.get("status") in ("queued", "running", "processing", "cancelling")
                 for table in ("tasks", "knowledge_files", "knowledge_rebuild_jobs")
                 for row in data.get(table, [])), "ACTIVE_TASK_OR_PARSING_JOB")
    need(runtime_call("idle")["status"] == "IDLE", "NONEMPTY_REDIS_QUEUE")


def sealed_runtime():
    native(CONTROL / "runtime-binding.v1.json")
    binding = read(CONTROL / "runtime-binding.v1.json")
    need((binding["source"], binding["tree"], binding["predecessor_source"]) ==
         (SOURCE, TREE, OLD_SOURCE), "WITNESS_SOURCE_BINDING")
    pinned(CONTROL / "entry.py", binding["entry_sha256"])
    need(sha(OP / "prepare.json") == binding["prepare_sha256"], "PREPARE_WITNESS_PIN")
    return binding, read(OP / "prepare.json")


def registry_witness():
    path = OP / "registry-witness.v2.json"
    native(path)
    doc = read(path)
    need(doc.get("contract") == CONTRACT and doc.get("source") == SOURCE
         and doc.get("tree") == TREE and doc.get("prepare_sha256") == sha(OP / "prepare.json")
         and doc.get("temporary_admin_removed") is True, "REGISTRY_WITNESS_SCOPE")
    need(registry_file_pins() == doc["registry_files"], "REGISTRY_FILE_DRIFT")
    return doc


def registry_file_pins():
    root = ROOT / "data/application/skill-registry"
    need(root.is_dir() and not root.is_symlink(), "REGISTRY_ROOT_TRUST")
    result = {}
    for path in root.rglob("*"):
        info = path.lstat()
        need(not stat.S_ISLNK(info.st_mode) and (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
             and info.st_uid in (0, 1000) and not info.st_mode & 0o022, "REGISTRY_PATH_TRUST")
        if path.is_file():
            need(info.st_nlink == 1, "REGISTRY_SINGLE_LINK")
            result[str(path)] = {"sha256": sha(path), "mode": stat.S_IMODE(info.st_mode),
                                 "uid": info.st_uid, "gid": info.st_gid}
    return result


def active_witness():
    if (OP / "registry-witness.v2.json").exists():
        return registry_witness()["active_baseline"]
    return read(OP / "prepare.json")


def registry_register():
    grant = authority("registry")
    old_state(topology=False)
    stopped()
    need(not (OP / "REGISTRY_ATTEMPTED.json").exists(), "REGISTRY_NO_RETRY_QUERY_ONLY")
    baseline = read(OP / "prepare.json")
    schema, before = snapshot()
    need(row_pins(before) == baseline["rows"], "REGISTRY_ORIGINAL_STATE_DRIFT")
    need(registry_file_pins() == baseline["registry_files"], "REGISTRY_ORIGINAL_FILES_DRIFT")
    record(OP / "REGISTRY_ATTEMPTED.json", {"source": SOURCE, "at": int(time.time()),
                                            "scope_sha256": sha(CONTROL / "scope.approved.v1.json")})
    proof = runtime_call("registry")
    authority("registry")
    schema, after = snapshot()
    # Pure stdlib validator, already independently pinned; no app import as Root.
    module = types.ModuleType("root_registry_delta_validator")
    pinned(CONTROL / "registry_extension.py", grant["code_sha256"]["registry_extension.py"])
    exec(compile((CONTROL / "registry_extension.py").read_bytes(), "registry-delta", "exec"), module.__dict__)
    module.validate_delta(before, after, proof)
    need(digest(schema) == baseline["schema_sha256"], "REGISTRY_NO_SCHEMA_CHANGE")
    return write_registry_witness(grant, baseline, schema, after, proof, module)


def write_registry_witness(grant, baseline, schema, after, proof, module):
    registry_files = registry_file_pins()
    module.validate_files(baseline["registry_files"], registry_files, proof,
                          ROOT / "data/application/skill-registry",
                          (RELEASE / "enterprise_agent_poc/skill_packages/wechat-official-account-writing/1.0.1.zip").read_bytes())
    value = dict(baseline, rows=row_pins(after), credit_accounts=after.get("credit_accounts", []))
    witness = {"contract": CONTRACT, "source": SOURCE, "tree": TREE,
        "prepare_sha256": sha(OP / "prepare.json"), "registration": proof, "registry_files": registry_files,
        "active_baseline": value, "temporary_admin_removed": True, "at": int(time.time())}
    authority("registry")
    record(OP / "registry-witness.v2.json", witness, mode=0o440)
    os.chown(OP / "registry-witness.v2.json", 0, 1000)
    return {"status": "EXACT_REGISTRY_ADDITION_WITNESSED_NOT_AGENT_RUNTIME_PUBLISHED",
            "temporary_platform_admins": 0, "bindings": "UNCHANGED"}


def registry_reconcile():
    """Only read back the SAME attempted transaction; never re-import/grant.
    A partial SQL rollback or conflicting filesystem remains blocked/preserved.
    """
    grant = authority("registry")
    stopped()
    source_identity(OLD_RELEASE, OLD_SOURCE, OLD_TREE)
    need((ROOT / "current").resolve() == OLD_RELEASE, "REGISTRY_RECONCILE_BEFORE_ACTIVATION")
    attempt = OP / "REGISTRY_ATTEMPTED.json"
    native(attempt)
    need(read(attempt)["scope_sha256"] == sha(CONTROL / "scope.approved.v1.json"), "REGISTRY_ORIGINAL_SCOPE")
    if (OP / "registry-witness.v2.json").exists():
        return {"status": "EXISTING_REGISTRY_WITNESS_VERIFIED", "witness": registry_witness()["at"]}
    schema, after = snapshot()
    baseline = read(OP / "prepare.json")
    module = types.ModuleType("root_registry_reconcile")
    pinned(CONTROL / "registry_extension.py", grant["code_sha256"]["registry_extension.py"])
    exec(compile((CONTROL / "registry_extension.py").read_bytes(), "registry-reconcile", "exec"), module.__dict__)
    skill = next(r for r in after["skills"] if r["slug"] == module.SLUG)
    versions = [r for r in after["skill_versions"] if r["skill_id"] == skill["id"] and r["version"] == module.VERSION]
    need(len(versions) == 1 and versions[0]["created_by"] == USER, "REGISTRY_COMMIT_NOT_VERIFIABLE_NO_RETRY")
    packages = [r for r in after["skill_packages"] if r["skill_version_id"] == versions[0]["id"]]
    need(len(packages) == 1, "REGISTRY_PACKAGE_COMMIT_NOT_VERIFIABLE")
    proof = {"version": versions[0], "package": packages[0], "registrar_user_id": USER,
             "admin_initialization": "PINNED_ORIGINAL_TRANSACTION_COMMITTED_READBACK",
             "temporary_admin_removed": True, "bindings_changed": False,
             "runtime_test": "NOT_EXECUTED", "provider_calls": 0, "reconciled": True}
    before = {t: [r for r in rows if not (t == "skill_versions" and r == versions[0]
              or t == "skill_packages" and r == packages[0])] for t, rows in after.items()}
    need(row_pins(before) == baseline["rows"] and digest(schema) == baseline["schema_sha256"], "REGISTRY_RECONCILE_ORIGINAL_STATE")
    module.validate_delta(before, after, proof)
    authority("registry")
    return write_registry_witness(grant, baseline, schema, after, proof, module)


def runtime_check(role=None):
    env_check(os.environ)
    binding, witness = sealed_runtime()
    current = (ROOT / "current").resolve()
    need(current in (RELEASE, OLD_RELEASE), "CURRENT_SOURCE")
    if current == OLD_RELEASE:
        source_identity(OLD_RELEASE, OLD_SOURCE, OLD_TREE)
        result = old_state(topology=False)
        return dict(result, role=role, source=OLD_SOURCE, tree=OLD_TREE, registry_preserved=True)
    source_identity(RELEASE)
    installed = {str(p.relative_to(RELEASE)): sha(p) for p in RELEASE.rglob("*") if p.is_file()}
    need(installed == witness["files"], "SOURCE_FILE_MAP")
    need(metadata_pins() == witness["metadata_pins"], "SECRET_SEEDING_OR_OLD_WITNESS_CHANGED")
    schema, data = snapshot()
    status = data_guard(schema, data, active_witness())
    return {"status": "PASS", "source": SOURCE, "tree": TREE, "role": role,
            "data_guard": status, "secret_backend": "OPAQUE_REFERENCE_PRESERVED_NOT_USED",
            "seeding": "DISABLED_EXISTING_RECORDS_ONLY", "provider_authority": False}


def prepare():
    authority("install")
    old_state()
    metadata = metadata_pins()  # read-only closure BEFORE any new staging/attempt
    need(not OP.exists() and not RELEASE.exists(), "NO_RETRY_OR_OVERWRITE")
    source_identity(REPO)
    parent = run(["/usr/bin/git", "-C", REPO, "rev-parse", "HEAD^"]).decode().strip()
    need(parent == GIT_PARENT, "REAL_GIT_PARENT")
    old_policy = Path("/etc/enterprise-agent-test-successor-wechat-d8a-seeding-approval-v1/policy.v1.json")
    pinned(old_policy, "f0cafe7bc1e85407da355febf1a855eb87c97036b5c0e094a250881381a9a028")
    old_files = read(old_policy)["files"]
    git = ["/usr/bin/git", "-C", REPO]
    for name, expected in old_files.items():
        if name.startswith("enterprise_agent_poc/migrations/"):
            need(hashlib.sha256(run(git + ["show", SOURCE + ":" + name])).hexdigest() == expected,
                 "NO_MIGRATION_BYTE_CHANGE")
    schema, data = snapshot()
    idle(schema, data)
    authority("install")
    OP.mkdir(mode=0o750)
    os.chown(OP, 0, 1000)
    record(OP / "ATTEMPTED.json", {"action": "prepare", "source": SOURCE, "at": int(time.time())})
    files, dep = formal("prepare", prefix=True)(dict(os.environ))
    need(dep["sha256"] == DEP, "DEPENDENCY_SNAPSHOT")
    # Same sealed metadata-binding body. No application bytes are changed.
    grant = authority("install")
    dca_pin = grant.get("dca_entry_sha256")
    pinned(DCA, dca_pin)
    c = types.SimpleNamespace(R=ROOT, RID=RID, REPO=REPO, RELEASE=RELEASE,
        SOURCE=SOURCE, TREE=TREE, need=need, run=run, source_identity=lambda: source_identity(RELEASE), sha=sha)
    extract_function(DCA, dca_pin, "add_git_binding", c)()
    need(not any(RELEASE.rglob("*.pyc")), "UNLISTED_SOURCE_CACHE")
    files[".git"] = sha(RELEASE / ".git")
    need(metadata_pins() == metadata, "METADATA_CHANGED_DURING_PREPARE")
    witness = {"source": SOURCE, "tree": TREE, "git_parent": GIT_PARENT,
        "predecessor_source": OLD_SOURCE, "predecessor_tree": OLD_TREE,
        "files": files, "schema_sha256": digest(schema), "rows": row_pins(data),
        "credit_accounts": data.get("credit_accounts", []), "metadata_pins": metadata,
        "registry_files": registry_file_pins(),
        "seeding_action": "NONE", "secret_action": "NONE", "at": int(time.time())}
    record(OP / "prepare.json", witness, mode=0o440)
    os.chown(OP / "prepare.json", 0, 1000)
    record(CONTROL / "runtime-binding.v1.json", {"contract": CONTRACT, "source": SOURCE,
        "tree": TREE, "predecessor_source": OLD_SOURCE, "entry_sha256": sha(CONTROL / "entry.py"),
        "prepare_sha256": sha(OP / "prepare.json"), "provider_authority": False,
        "code_sha256": grant["code_sha256"],
        "python_real_path": grant["python_real_path"], "python_sha256": grant["python_sha256"],
        "system_python_manifest_sha256": grant["system_python_manifest_sha256"],
        "application_python_real_path": grant["application_python_real_path"],
        "application_python_sha256": grant["application_python_sha256"]}, mode=0o444)
    return {"status": "PREPARED_NOT_TEST_PASS", "source": SOURCE, "tree": TREE}


def drops():
    return {role: Path("/etc/systemd/system") /
            ("enterprise-agent-test-" + role + ".service.d") / DROP_NAME for role in ROLES}


def stopped():
    for role in ROLES:
        fields = run(["/usr/bin/systemctl", "show", "enterprise-agent-test-" + role + ".service",
                      "-p", "MainPID", "-p", "ActiveState"]).decode()
        value = dict(line.split("=", 1) for line in fields.splitlines())
        need(value["MainPID"] == "0" and value["ActiveState"] in ("inactive", "failed"), "APP_NOT_STOPPED")


def backup_gate():
    """Use existing encrypted backup/rehearsal workflow; exact fresh set, not old Test-only proof."""
    path = CONTROL / "backup-restored.verified.v1.json"
    native(path)
    value = read(path)
    grant = authority("activate")
    need(value.get("scope") == CONTRACT and value.get("operation") == str(OP)
         and value.get("source") == OLD_SOURCE and value.get("tree") == OLD_TREE
         and value.get("restore_source") == OLD_SOURCE
         and value.get("captured_at", 0) >= grant["issued_at"], "FRESH_EXACT_BACKUP")
    need(value.get("quiescent_apps") is True and value.get("schema") == "001-015"
         and value.get("pg_version") == "16.6" and value.get("redis_version") == "7.0.15",
         "BACKUP_DATA_CONTRACT")
    for fact in ("actual_pg_restore", "actual_redis_restore", "files_restored", "hashes_match",
                 "api_mcp_worker_restore_verified", "original_data_preserved"):
        need(value.get(fact) is True, "ACTUAL_RESTORE_PROOF_REQUIRED:" + fact)
    report = Path(value["restore_report_path"])
    native(report)
    need(sha(report) == value["restore_report_sha256"], "RESTORE_PROOF_PIN")
    proof = read(report)
    need(proof.get("result") == "PASS" and proof.get("operation") == str(OP)
         and proof.get("backup_members") == value.get("backup_members"), "BACKUP_PROOF_EXACT_SET")
    for name, member in value["backup_members"].items():
        path = Path(member["path"])
        need(path.is_relative_to(BACKUP_ROOT), "BACKUP_PATH_SCOPE")
        native(path)
        need(sha(path) == member["sha256"], "BACKUP_BYTES_CHANGED:" + name)
    # Restore actuator is existing exact Test recovery code, independently
    # approved/pinned. No arbitrary argv/script supplied by a candidate.
    need(value.get("restore_entry") == str(CONTROL / RECOVERY_FILE), "RESTORE_ENTRY_SCOPE")
    pinned(CONTROL / RECOVERY_FILE, value["restore_entry_sha256"])
    need(value.get("restore_entry_sha256") == grant.get("restore_entry_sha256"), "ROOT_RESTORE_CODE_PIN")
    return value


def health():
    result = runtime_check()
    services = {}
    for role in (*ROLES, "postgresql", "redis"):
        fields = run(["/usr/bin/systemctl", "show", "enterprise-agent-test-" + role + ".service",
                      "-p", "MainPID", "-p", "ActiveState", "-p", "WorkingDirectory"]).decode()
        item = dict(line.split("=", 1) for line in fields.splitlines())
        need(item["ActiveState"] == "active" and int(item["MainPID"]) > 0, "SERVICE_HEALTH:" + role)
        if role in ROLES:
            need((Path("/proc") / item["MainPID"] / "cwd").resolve() == (ROOT / "current").resolve() / "enterprise_agent_poc",
                 "SERVICE_SOURCE:" + role)
        services[role] = item
    import urllib.request
    with urllib.request.urlopen("http://127.0.0.1:18100/api/health", timeout=5) as response:
        value = json.load(response)
    need(value.get("status") == "ok" and value.get("environment") == "test", "API_HEALTH")
    runtime_call("health")
    return dict(result, services=services, knowledge=value.get("knowledge"),
                knowledge_attribution="PRE_EXISTING_DEGRADED_NOT_KNOWLEDGE_FEATURE_PASS")


def recover():
    # This can run with applications stopped. Never invoke d8a entry.recover,
    # its synthetic cleanup/DCA pointer, or historical guard requiring live CWD.
    authority("recover")
    need(not (OP / "RECOVER_ATTEMPTED.json").exists(), "RECOVERY_ALREADY_ATTEMPTED_QUERY_ONLY")
    value = backup_gate_for_recovery()
    formal("units")("stop", "worker", "api", "mcp")
    stopped()
    authority("recover")  # Fresh immediately before real restore mutation.
    record(OP / "RECOVER_ATTEMPTED.json", {"at": int(time.time()), "source": OLD_SOURCE})
    repair = (OP / "REHEARSAL_ATTEMPTED.json").exists() and not (OP / "actual-d8a-restore-report.json").exists()
    need(not repair or not (OP / "ACTIVATE_ATTEMPTED.json").exists(), "NO_DATA_ROLLBACK_AFTER_ACTIVATION")
    run([PYTHON, "-I", "-S", "-B", CONTROL / RECOVERY_FILE,
         "repair-failed-rehearsal" if repair else "preserve-and-verify-d8a"], timeout=1800)
    need(metadata_pins() == read(OP / "prepare.json")["metadata_pins"], "RESTORED_SECRET_SEEDING")
    authority("recover")
    for path in drops().values():
        if path.exists():
            # Preserve only this operation's generated drop-ins, never remove
            # historical files or logs. Recovery move is exclusive.
            archive = OP / (path.parent.name + ".restored-dropin")
            need(not archive.exists() and path.read_text() == drop_text(path.parent.name.split("test-")[1].split(".")[0]),
                 "RECOVERY_DROPIN_CONFLICT")
            path.rename(archive)
    for role, path in recovery_drops().items():
        authority("recover")
        raw = drop_text(role, recovery=True).encode()
        if path.exists():
            native(path)
            need(path.read_bytes() == raw, "RECOVERY_DROPIN_EXISTS_DIFFERENT_BYTES")
            continue
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        fsync_dir(path.parent)
    authority("recover")
    formal("switch_pointer")(OLD_RELEASE)
    fsync_dir(ROOT)
    run(["/usr/bin/systemctl", "daemon-reload"])
    formal("units")("reset-failed", *ROLES)
    for role in ROLES:
        formal("units")("start", role)
    actual = old_state(topology=True)
    schema, data = snapshot()
    data_guard(schema, data, active_witness())
    record(OP / "RECOVERED.json", {"status": "EXACT_D8A_RESTORED", "result": actual,
                                  "source": OLD_SOURCE, "at": int(time.time())})
    return {"status": "EXACT_D8A_RESTORED", "source": OLD_SOURCE}


def resume_unchanged():
    """Early backup/preflight failure: restart original services only if no
    target/drop-in/data restore happened and the old guard accepts exact data.
    """
    authority("recover")
    need(not any((OP / name).exists() for name in (
        "ACTIVATE_ATTEMPTED.json", "REHEARSAL_ATTEMPTED.json", "RECOVER_ATTEMPTED.json",
        "RESUME_UNCHANGED_ATTEMPTED.json")), "EARLY_RESTART_NOT_APPLICABLE")
    need(all(not path.exists() for path in drops().values()), "EARLY_RESTART_DROPIN")
    old_state(topology=False)
    stopped()
    authority("recover")
    record(OP / "RESUME_UNCHANGED_ATTEMPTED.json", {"at": int(time.time()), "source": OLD_SOURCE})
    formal("units")("reset-failed", *ROLES)
    for role in ROLES:
        authority("recover")
        formal("units")("start", role)
    result = old_state(topology=True)
    record(OP / "D8A_UNCHANGED_RESUMED.json", result)
    return {"status": "D8A_UNCHANGED_RESUMED", "source": OLD_SOURCE}


def backup_gate_for_recovery():
    # Do not require a still-open *activation* window to recover. Independently
    # verify the same protected backup proof under fresh recovery authority.
    authority("recover")
    # Before any 894 switch, a failed rehearsal may use the selected fresh,
    # decrypt-readback-verified d8a snapshot to repair d8a. It may NOT advance
    # to 894 without actual rehearsal PASS. After switch, full proof mandatory.
    if not (OP / "ACTIVATE_ATTEMPTED.json").exists():
        native(OP / "fresh-backup.json")
        fresh = read(OP / "fresh-backup.json")
        need(fresh.get("scope") == CONTRACT and fresh.get("operation") == str(OP)
             and fresh.get("source") == OLD_SOURCE and fresh.get("tree") == OLD_TREE,
             "PRE_TARGET_EXACT_RECOVERY")
        value = {"scope": CONTRACT, "operation": str(OP), "restore_source": OLD_SOURCE,
                 "backup_members": fresh["backup_members"],
                 "restore_entry_sha256": authority("recover")["restore_entry_sha256"]}
    else:
        value = read(CONTROL / "backup-restored.verified.v1.json")
        native(CONTROL / "backup-restored.verified.v1.json")
    need(value.get("scope") == CONTRACT and value.get("operation") == str(OP)
         and value.get("restore_source") == OLD_SOURCE, "RECOVERY_SCOPE")
    if (OP / "ACTIVATE_ATTEMPTED.json").exists():
        proof_path = Path(value["restore_report_path"])
        native(proof_path)
        proof = read(proof_path)
        need(sha(proof_path) == value["restore_report_sha256"] and proof.get("result") == "PASS"
             and proof.get("backup_members") == value.get("backup_members")
             and proof.get("operation") == str(OP), "RECOVERY_RESTORE_PROOF")
    grant = authority("recover")
    pinned(CONTROL / RECOVERY_FILE, grant.get("restore_entry_sha256"))
    need(value.get("restore_entry_sha256") == grant.get("restore_entry_sha256"), "RECOVERY_PIN")
    for member in value["backup_members"].values():
        path = Path(member["path"])
        native(path)
        need(path.is_relative_to(BACKUP_ROOT)
             and sha(path) == member["sha256"], "RECOVERY_BACKUP_PIN")
    return value


def recovery_drops():
    return {role: Path("/etc/systemd/system") / ("enterprise-agent-test-" + role + ".service.d") /
            "zzzzzz-business-894-compatible-d8a-v2.conf" for role in ROLES}


def drop_text(role, recovery=False):
    need(role in ROLES, "ROLE")
    app = (OLD_RELEASE if recovery else RELEASE) / "enterprise_agent_poc"
    return ("[Service]\nWorkingDirectory=" + str(app) + "\nEnvironment=PYTHONPATH=" + str(app)
            + "\nEnvironment=ENTERPRISE_POC_BOOTSTRAP_DEMO_DATA=false"
            + "\nEnvironment=ENTERPRISE_POC_WECHAT_NETWORK_ALLOWED=false"
            + "\nEnvironment=PYTHONDONTWRITEBYTECODE=1\nExecStartPre=\nExecStartPre=" + str(PYTHON)
            + " -I -S -B " + str(CONTROL / "verified_entry.py") + " runtime-check " + role
            + "\nExecStartPost=\n")


def install_compatible_d8a_dropins():
    for role, path in recovery_drops().items():
        raw = drop_text(role, recovery=True).encode()
        if path.exists():
            need(path.read_bytes() == raw, "EXACT_RECOVERY_DROPIN_DRIFT")
            continue
        authority("restore-rehearsal")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        fsync_dir(path.parent)
    run(["/usr/bin/systemctl", "daemon-reload"])


def quiesce_registry():
    authority("registry")
    need(not (OP / "QUIESCE_ATTEMPTED.json").exists(), "QUIESCE_ALREADY_ATTEMPTED")
    old_state()
    schema, data = snapshot()
    idle(schema, data)
    need(row_pins(data) == read(OP / "prepare.json")["rows"], "ORIGINAL_PRESTATE_CHANGED")
    authority("registry")
    record(OP / "QUIESCE_ATTEMPTED.json", {"source": OLD_SOURCE, "at": int(time.time()), "phase": "BEFORE_REGISTRY"})
    formal("units")("stop", "worker", "api", "mcp")
    stopped()
    return {"status": "D8A_QUIESCENT_REGISTRY_TRANSACTION_REQUIRED"}


def quiesce():
    authority("backup")
    need(not (OP / "QUIESCE_ATTEMPTED.json").exists(), "QUIESCE_ALREADY_ATTEMPTED")
    old_state()
    schema, data = snapshot()
    idle(schema, data)
    need(row_pins(data) == active_witness()["rows"], "PREPARED_STATE_CHANGED")
    need(all(not path.exists() for path in drops().values()), "DROPIN_EXISTS")
    registry_preflight()  # missing package must reject BEFORE any service stop
    record(OP / "QUIESCE_ATTEMPTED.json", {"source": SOURCE, "at": int(time.time())})
    formal("units")("stop", "worker", "api", "mcp")
    stopped()
    return {"status": "QUIESCENT_D8A_FRESH_BACKUP_AND_REHEARSAL_REQUIRED"}


def registry_preflight():
    """Existing formal readonly verify_bootstrap runs in the demoted probe;
    never auto import/publish or
    pretend package validation is Agent Runtime Test. Separate scope if absent.
    """
    env_check(os.environ)
    sealed_runtime()
    source_identity(RELEASE)
    try:
        result = runtime_call("registry-check")
        need(result.get("status") == "ok" and result.get("mode") == "reuse", "REGISTRY_REUSE_REQUIRED")
    except Exception:
        raise Blocked("EXACT_894_REGISTRY_READONLY_PREFLIGHT_FAILED_NO_AUTO_BOOTSTRAP") from None
    return result


def rehearse():
    """Restore the selected fresh backup to stopped d8a FIRST, then prove its
    actual three-service restart. No second runtime/server and no target switch.
    """
    grant = authority("restore-rehearsal")
    stopped()
    need((ROOT / "current").resolve() == OLD_RELEASE, "REHEARSAL_EXACT_D8A")
    need(not (OP / "REHEARSAL_ATTEMPTED.json").exists(), "REHEARSAL_NO_RETRY")
    pinned(CONTROL / "existing_recovery_bound.py", grant["restore_entry_sha256"])
    record(OP / "REHEARSAL_ATTEMPTED.json", {"source": OLD_SOURCE, "at": int(time.time())})
    run([PYTHON, "-I", "-S", "-B", CONTROL / RECOVERY_FILE, "rehearse-exact-d8a"], timeout=1800)
    authority("restore-rehearsal")
    install_compatible_d8a_dropins()
    formal("units")("reset-failed", *ROLES)
    for role in ROLES:
        formal("units")("start", role)
    live = old_state(topology=True)
    schema, data = snapshot()
    fresh = read(OP / "fresh-backup.json")
    need(row_pins(data) == fresh["row_pins"] and digest(schema) == fresh["schema_sha256"],
         "REHEARSAL_ACTUAL_DATA_DRIFT")
    need(metadata_pins() == fresh["metadata_pins"], "REHEARSAL_ACTUAL_CONFIG_DRIFT")
    report = {"result": "PASS", "operation": str(OP), "backup_members": fresh["backup_members"],
              "services": live, "source": OLD_SOURCE, "tree": OLD_TREE,
              "provider_calls": 0, "at": int(time.time())}
    record(OP / "actual-d8a-restore-report.json", report)
    # Evidence producer, NOT an approval issuer. True facts follow actual
    # restore/row/secret/old service readiness checks, never candidate labels.
    record(CONTROL / "backup-restored.verified.v1.json", {
        "scope": CONTRACT, "operation": str(OP), "source": OLD_SOURCE, "tree": OLD_TREE,
        "restore_source": OLD_SOURCE, "captured_at": fresh["captured_at"], "quiescent_apps": True,
        "schema": "001-015", "pg_version": "16.6", "redis_version": "7.0.15",
        "actual_pg_restore": True, "actual_redis_restore": True, "files_restored": True,
        "hashes_match": True, "api_mcp_worker_restore_verified": True, "original_data_preserved": True,
        "restore_report_path": str(OP / "actual-d8a-restore-report.json"),
        "restore_report_sha256": sha(OP / "actual-d8a-restore-report.json"),
        "backup_members": fresh["backup_members"],
        "restore_entry": str(CONTROL / "existing_recovery_bound.py"),
        "restore_entry_sha256": grant["restore_entry_sha256"]})
    authority("restore-rehearsal")
    formal("units")("stop", "worker", "api", "mcp")
    stopped()
    return {"status": "ACTUAL_D8A_DATA_AND_THREE_SERVICE_RESTORE_VERIFIED", "source": OLD_SOURCE}


def activate():
    authority("activate")
    need(not (OP / "ACTIVATE_ATTEMPTED.json").exists(), "NO_RETRY_QUERY_OR_RECOVER_ONLY")
    need((OP / "QUIESCE_ATTEMPTED.json").is_file(), "QUIESCE_REQUIRED")
    old_state(topology=False)
    stopped()
    # Operator's existing workflow produces fresh encrypted backup and actual
    # rehearsal now; never reuse the old Oct09 archive as current state proof.
    backup_gate()
    authority("activate")
    record(OP / "ACTIVATE_ATTEMPTED.json", {"source": SOURCE, "at": int(time.time())})
    try:
        # The trial's compatible d8a drop-ins are preserved as audit, not
        # left lexicographically after the active 894 drop-in.
        for role, path in recovery_drops().items():
            authority("activate")
            need(path.is_file() and path.read_text() == drop_text(role, recovery=True), "D8A_TRIAL_DROPIN_REQUIRED")
            destination = OP / (role + ".trial-d8a-dropin.conf")
            need(not destination.exists(), "TRIAL_DROPIN_AUDIT_COLLISION")
            path.rename(destination)
        for role, path in drops().items():
            authority("activate")
            raw = drop_text(role).encode()
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444)
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            fsync_dir(path.parent)
        authority("activate")
        formal("switch_pointer")(RELEASE)
        fsync_dir(ROOT)
        run(["/usr/bin/systemctl", "daemon-reload"])
        for role in ROLES:
            formal("units")("start", role)
        result = health()
        record(OP / "COMMITTED.json", {"status": "COMMITTED", "source": SOURCE,
                "tree": TREE, "health": result, "at": int(time.time())})
        need(read(OP / "COMMITTED.json")["status"] == "COMMITTED", "COMMIT_READBACK_UNKNOWN")
        return result
    except Exception:
        # Query original operation after interruption; no automatic second
        # switch/attempt. Explicit recovery uses the same authority and lock.
        record(OP / "FAILED_QUERY_OR_EXACT_RECOVER.json", {"source": SOURCE, "at": int(time.time())})
        raise


def main():
    need(len(sys.argv) in (2, 3), "COMMAND")
    command = sys.argv[1]
    context(runtime=command == "runtime-check")
    if command == "runtime-check":
        need(len(sys.argv) == 3, "ROLE_REQUIRED")
        return runtime_check(sys.argv[2])
    need(command in ("prepare", "registry", "registry-reconcile", "registry-check", "quiesce-registry", "quiesce", "backup", "rehearse", "activate", "recover",
                     "resume-unchanged", "health", "query"), "COMMAND")
    with locked():
        if command == "query":
            return {"source": SOURCE, "records": [p.name for p in OP.glob("*.json")],
                    "current": str((ROOT / "current").resolve()), "retry_permitted": False}
        if command == "health":
            return health()
        if command == "registry-check":
            return registry_preflight()
        if command == "backup":
            grant = authority("backup")
            pinned(CONTROL / "existing_recovery_bound.py", grant["restore_entry_sha256"])
            run([PYTHON, "-I", "-S", "-B", CONTROL / RECOVERY_FILE, "capture"], timeout=1800)
            return {"status": "ENCRYPTED_BACKUP_CAPTURED_RESTORE_NOT_PROVEN"}
        return {"prepare": prepare, "registry": registry_register, "registry-reconcile": registry_reconcile,
                "quiesce-registry": quiesce_registry,
                "quiesce": quiesce, "rehearse": rehearse,
                "activate": activate, "recover": recover, "resume-unchanged": resume_unchanged}[command]()


if __name__ == "__main__":
    try:
        print(json.dumps(main(), sort_keys=True, default=str))
    except Exception as error:
        # Never echo DB URL, password, argv, SQL or captured stderr.
        print(json.dumps({"status": "BLOCKED_OR_UNKNOWN_QUERY_ORIGINAL_OPERATION",
                          "category": str(error) if isinstance(error, Blocked) else type(error).__name__}))
        raise SystemExit(2)
