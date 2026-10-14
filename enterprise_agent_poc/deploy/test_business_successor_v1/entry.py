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
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import types
from urllib.parse import unquote, urlsplit

CONTRACT = "PRIMARY_TEST_D8A_894_BUSINESS_SUCCESSOR_V1"
HOST = "iv-yew0uhfksgbw80bm48mv"
ROOT = Path("/opt/enterprise-agent-workbench-test")
CONTROL = Path("/etc/enterprise-agent-test-business-894-successor-v1")
OP = ROOT / "release-evidence/business-894-successor-v1"
SOURCE = "894bdd2a031725a0d0df3b95d6dfc26d08f0e4ab"
TREE = "bb9317207372ad1a215eaeebb7a2b0151a35be4d"
GIT_PARENT = "d11c2fb5ddb6203eb5cee23832fafec47a2781f0"
OLD_SOURCE = "d8a7814f1ceb528cad8a2d3b4c7a5aa135f6ccf7"
OLD_TREE = "6021a1f5017b92c3e5b785db1dd6bfcbb190d7d5"
RID = "20261014-894bdd2-business-p0-test-v1"
RELEASE = ROOT / "releases" / RID
OLD_RELEASE = ROOT / "releases/20261008-d8a7814-wechat-personal-v1"
PYTHON = ROOT / "shared/runtime/python311/bin/python"
PG = ROOT / "shared/runtime/postgresql-16.6/bin"
REPO = ROOT / "shared/source/business-894-exact-v1"
LOCK = Path("/run/lock/enterprise-agent-test-successor-v1.lock")
FORMAL = Path("/etc/enterprise-agent-test-successor-chat-reference-v1/entry.py")
FORMAL_PIN = "5f91603551e38119a294de7ecdce457ef19d557e902c1af0cfc2726f49af6b9e"
DCA = Path("/etc/enterprise-agent-test-successor-wechat-dca-v1/entry.py")
OLD_GUARD = Path("/etc/enterprise-agent-test-wechat-persistent-config-v1/primary_guard.py")
OLD_GUARD_PIN = "5ba582e45b55e35aaeae83d5de995db00853ae83df45f0dab58d7d361c0c2c47"
KEY = ROOT / "shared/credentials/wechat-tenant-secret-d8a-v1.key"
SECRET = ROOT / "data/application/tenant-secrets/test/9a68955cf97b027ae79a199d77750e8ea218486f4e3505cfaf8dd1d259d32476.fernet"
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
        need(not stat.S_ISLNK(info.st_mode) and info.st_uid == info.st_gid == 0
             and not info.st_mode & 0o022, "ROOT_ANCESTOR_TRUST")
    info = path.lstat()
    need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "REGULAR_SINGLE_LINK")
    need(not readonly or not info.st_mode & 0o222, "READONLY_RECORD")


def pinned(path, expected):
    native(path)
    need(sha(path) == expected, "CODE_PIN_BEFORE_EXECUTION")


def run(argv, *, env=None, timeout=180, **kwargs):
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
    need(value.get("operator") == "06_CODEX_AUTHORIZED_LUCKY_SUDO"
         and value.get("actions") == ["install", "backup", "restore-rehearsal", "activate", "recover"],
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
    need(set(pins) == {"entry.py", "existing_recovery_bound.py"}, "FIRST_BYTE_CODE_PIN_SET")
    for name, pin in pins.items():
        pinned(CONTROL / name, pin)
    trusted_python(value)
    need(value.get("restore_reserve_seconds") == 3600, "RESTORE_RESERVE")
    if action != "recover":
        need(value["expires_at"] - instant >= 3600, "NO_NEW_WORK_IN_RESTORE_RESERVE")
    return value


@contextmanager
def locked():
    native(LOCK, readonly=False)
    fd = os.open(LOCK, os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
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
    c = types.SimpleNamespace(R=ROOT, P=CONTROL, REPO=REPO, PY=PYTHON, SOURCE=SOURCE,
        TREE=TREE, RID=RID, RELEASE=RELEASE, OLD_RELEASE=OLD_RELEASE, need=need,
        sha=sha, run=run, hashlib=hashlib, source_identity=lambda: source_identity(RELEASE),
        before_install_mutation=lambda: authority("install"))
    return extract_function(FORMAL, FORMAL_PIN, name, c, prefix=prefix)


def old_guard():
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
    return old_guard().verify(topology=topology)


def snapshot():
    """Reuse sealed readonly catalog/row collector, no seeding module invocation."""
    env_check(os.environ)
    import psycopg
    with psycopg.connect(os.environ["ENTERPRISE_POC_DATABASE_URL"],
                         options="-c default_transaction_read_only=on -c statement_timeout=10000") as db:
        db.execute("SET TRANSACTION READ ONLY")
        identity = db.execute("SELECT current_database(),current_user,inet_server_port(),current_setting('server_version')").fetchone()
        need(identity == ("enterprise_agent_test", "enterprise_agent_test", 55432, "16.6"), "EXACT_TEST_PG_VERSION_ROLE")
        need(db.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone() == ("0.8.6",),
             "EXACT_TEST_VECTOR_EXTENSION")
    return old_guard().base.snapshot()


def row_pins(data):
    return {name: sorted(digest(row) for row in rows) for name, rows in data.items()}


def metadata_pins():
    paths = [KEY, SECRET, ROOT / "shared/config/test-environment.env",
             ROOT / "shared/credentials/test-environment.secrets.env"]
    for directory in (Path("/etc/enterprise-agent-test-wechat-persistent-config-v1"),
                      Path("/etc/enterprise-agent-test-wechat-persistent-config-recovery-v1"),
                      Path("/etc/enterprise-agent-test-tenant-seeding-v1")):
        paths.extend(path for path in directory.rglob("*") if path.is_file())
    result = {}
    for path in sorted(set(paths)):
        info = path.lstat()
        need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid in (0, 1000)
             and not info.st_mode & 0o022, "TEST_REFERENCE_PATH")
        result[str(path)] = dict(sha256=sha(path), mode=stat.S_IMODE(info.st_mode),
                                uid=info.st_uid, gid=info.st_gid, size=info.st_size)
    return result


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
    from redis import Redis
    client = Redis.from_url(os.environ["REDIS_URL"])
    try:
        for key in client.scan_iter():
            if client.type(key) == b"list":
                need(client.llen(key) == 0, "NONEMPTY_REDIS_QUEUE")
    finally:
        client.close()


def sealed_runtime():
    native(CONTROL / "runtime-binding.v1.json")
    binding = read(CONTROL / "runtime-binding.v1.json")
    need((binding["source"], binding["tree"], binding["predecessor_source"]) ==
         (SOURCE, TREE, OLD_SOURCE), "WITNESS_SOURCE_BINDING")
    pinned(CONTROL / "entry.py", binding["entry_sha256"])
    need(sha(OP / "prepare.json") == binding["prepare_sha256"], "PREPARE_WITNESS_PIN")
    return binding, read(OP / "prepare.json")


def runtime_check(role=None):
    env_check(os.environ)
    binding, witness = sealed_runtime()
    need((ROOT / "current").resolve() == RELEASE, "CURRENT_SOURCE")
    source_identity(RELEASE)
    installed = {str(p.relative_to(RELEASE)): sha(p) for p in RELEASE.rglob("*") if p.is_file()}
    need(installed == witness["files"], "SOURCE_FILE_MAP")
    need(metadata_pins() == witness["metadata_pins"], "SECRET_SEEDING_OR_OLD_WITNESS_CHANGED")
    schema, data = snapshot()
    status = data_guard(schema, data, witness)
    return {"status": "PASS", "source": SOURCE, "tree": TREE, "role": role,
            "data_guard": status, "secret_backend": "OPAQUE_REFERENCE_PRESERVED_NOT_USED",
            "seeding": "DISABLED_EXISTING_RECORDS_ONLY", "provider_authority": False}


def prepare():
    authority("install")
    old_state()
    need(not OP.exists() and not RELEASE.exists(), "NO_RETRY_OR_OVERWRITE")
    source_identity(REPO)
    parent = run(["/usr/bin/git", "-C", REPO, "rev-parse", "HEAD^"]).decode().strip()
    need(parent == GIT_PARENT, "REAL_GIT_PARENT")
    old_files = old_guard().base.read(old_guard().BASE / "policy.v1.json")["files"]
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
    witness = {"source": SOURCE, "tree": TREE, "git_parent": GIT_PARENT,
        "predecessor_source": OLD_SOURCE, "predecessor_tree": OLD_TREE,
        "files": files, "schema_sha256": digest(schema), "rows": row_pins(data),
        "credit_accounts": data.get("credit_accounts", []), "metadata_pins": metadata_pins(),
        "seeding_action": "NONE", "secret_action": "NONE", "at": int(time.time())}
    record(OP / "prepare.json", witness, mode=0o440)
    os.chown(OP / "prepare.json", 0, 1000)
    record(CONTROL / "runtime-binding.v1.json", {"contract": CONTRACT, "source": SOURCE,
        "tree": TREE, "predecessor_source": OLD_SOURCE, "entry_sha256": sha(CONTROL / "entry.py"),
        "prepare_sha256": sha(OP / "prepare.json"), "provider_authority": False}, mode=0o444)
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
        need(path.is_relative_to(ROOT / "backups/business-894-successor-v1"), "BACKUP_PATH_SCOPE")
        native(path)
        need(sha(path) == member["sha256"], "BACKUP_BYTES_CHANGED:" + name)
    # Restore actuator is existing exact Test recovery code, independently
    # approved/pinned. No arbitrary argv/script supplied by a candidate.
    need(value.get("restore_entry") == str(CONTROL / "existing-recovery-bound.py"), "RESTORE_ENTRY_SCOPE")
    pinned(CONTROL / "existing-recovery-bound.py", value["restore_entry_sha256"])
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
            need((Path("/proc") / item["MainPID"] / "cwd").resolve() == RELEASE / "enterprise_agent_poc",
                 "SERVICE_SOURCE:" + role)
        services[role] = item
    import urllib.request
    with urllib.request.urlopen("http://127.0.0.1:18100/api/health", timeout=5) as response:
        value = json.load(response)
    need(value.get("status") == "ok" and value.get("environment") == "test", "API_HEALTH")
    import asyncio
    from datetime import timedelta
    async def mcp_ping():
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        async with streamablehttp_client("http://127.0.0.1:18101/mcp", timeout=timedelta(seconds=10)) as (r, w, _):
            async with ClientSession(r, w) as session:
                await session.initialize(); await session.send_ping()
    asyncio.run(mcp_ping())
    from redis import Redis
    client = Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    try:
        need(any(row["cmd"].lower() in ("brpoplpush", "rpoplpush") for row in client.client_list()),
             "WORKER_QUEUE_LOOP_NOT_PROVEN")
    finally:
        client.close()
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
    run([PYTHON, "-I", "-B", CONTROL / "existing-recovery-bound.py", "restore-exact-d8a"], timeout=1800)
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
    authority("recover")
    formal("switch_pointer")(OLD_RELEASE)
    fsync_dir(ROOT)
    run(["/usr/bin/systemctl", "daemon-reload"])
    formal("units")("reset-failed", *ROLES)
    for role in ROLES:
        formal("units")("start", role)
    actual = old_state(topology=True)
    schema, data = snapshot()
    need(row_pins(data) == read(OP / "prepare.json")["rows"], "RESTORED_EXACT_D8A_DATA")
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
    pinned(CONTROL / "existing-recovery-bound.py", grant.get("restore_entry_sha256"))
    need(value.get("restore_entry_sha256") == grant.get("restore_entry_sha256"), "RECOVERY_PIN")
    for member in value["backup_members"].values():
        path = Path(member["path"])
        native(path)
        need(path.is_relative_to(ROOT / "backups/business-894-successor-v1")
             and sha(path) == member["sha256"], "RECOVERY_BACKUP_PIN")
    return value


def drop_text(role):
    need(role in ROLES, "ROLE")
    app = RELEASE / "enterprise_agent_poc"
    return ("[Service]\nWorkingDirectory=" + str(app) + "\nEnvironment=PYTHONPATH=" + str(app)
            + "\nEnvironment=ENTERPRISE_POC_BOOTSTRAP_DEMO_DATA=false"
            + "\nEnvironment=ENTERPRISE_POC_WECHAT_NETWORK_ALLOWED=false"
            + "\nEnvironment=PYTHONDONTWRITEBYTECODE=1\nExecStartPre=\nExecStartPre=" + str(PYTHON)
            + " -I -B " + str(CONTROL / "entry.py") + " runtime-check " + role
            + "\nExecStartPost=\n")


def quiesce():
    authority("backup")
    need(not (OP / "QUIESCE_ATTEMPTED.json").exists(), "QUIESCE_ALREADY_ATTEMPTED")
    old_state()
    schema, data = snapshot()
    idle(schema, data)
    need(row_pins(data) == read(OP / "prepare.json")["rows"], "PREPARED_STATE_CHANGED")
    need(all(not path.exists() for path in drops().values()), "DROPIN_EXISTS")
    registry_preflight()  # missing package must reject BEFORE any service stop
    record(OP / "QUIESCE_ATTEMPTED.json", {"source": SOURCE, "at": int(time.time())})
    formal("units")("stop", "worker", "api", "mcp")
    stopped()
    return {"status": "QUIESCENT_D8A_FRESH_BACKUP_AND_REHEARSAL_REQUIRED"}


def registry_preflight():
    """Existing formal readonly Registry verifier; never auto import/publish or
    pretend package validation is Agent Runtime Test. Separate scope if absent.
    """
    env_check(os.environ)
    sealed_runtime()
    source_identity(RELEASE)
    code = ("import sys,json;sys.path.insert(0," + repr(str(RELEASE / "enterprise_agent_poc")) + ");"
            "from app.settings import settings;from app.store import POCStore;"
            "from app.skill_registry import SkillRegistry;"
            "result=SkillRegistry(POCStore(settings.database_url),"
            "settings.data_dir/'skill-registry'," + repr(str(RELEASE / "enterprise_agent_poc/skill_packages"))
            + ").verify_bootstrap();print(json.dumps({'status':result['status'],'mode':result['mode']}))")
    try:
        raw = run(["/usr/sbin/runuser", "-u", "lucky", "--", PYTHON, "-I", "-B", "-c", code])
        result = json.loads(raw)
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
    run([PYTHON, "-I", "-B", CONTROL / "existing_recovery_bound.py", "rehearse-exact-d8a"], timeout=1800)
    authority("restore-rehearsal")
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
    need(command in ("prepare", "registry-check", "quiesce", "backup", "rehearse", "activate", "recover",
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
            run([PYTHON, "-I", "-B", CONTROL / "existing_recovery_bound.py", "capture"], timeout=1800)
            return {"status": "ENCRYPTED_BACKUP_CAPTURED_RESTORE_NOT_PROVEN"}
        return {"prepare": prepare, "quiesce": quiesce, "rehearse": rehearse,
                "activate": activate, "recover": recover, "resume-unchanged": resume_unchanged}[command]()


if __name__ == "__main__":
    try:
        print(json.dumps(main(), sort_keys=True, default=str))
    except Exception as error:
        # Never echo DB URL, password, argv, SQL or captured stderr.
        print(json.dumps({"status": "BLOCKED_OR_UNKNOWN_QUERY_ORIGINAL_OPERATION",
                          "category": str(error) if isinstance(error, Blocked) else type(error).__name__}))
        raise SystemExit(2)
