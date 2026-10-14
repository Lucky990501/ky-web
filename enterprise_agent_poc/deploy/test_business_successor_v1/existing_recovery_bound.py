"""Exact Test backup/restore adapter: pg_dump/pg_restore, Redis DUMP/RESTORE,
GPG and the existing row collector. No old DCA or synthetic cleanup.

Run only through independently pinned entry.py in the existing resource slice.
Actual service-restoration rehearsal remains required external 06 evidence.
"""
import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import pwd
import shutil
import stat
import tarfile
import time
from urllib.parse import unquote, urlsplit

ROOT = Path("/opt/enterprise-agent-workbench-test")
CONTROL = Path("/etc/enterprise-agent-test-business-894-successor-v2")
BACKUP = ROOT / "backups/business-894-successor-v2"
DATA_ROOTS = (ROOT / "data/application", ROOT / "data/object-storage")


def load():
    # Root checks the two source SHA from the independently approved scope
    # before this script is invoked; additionally avoid cached import bytes.
    path = CONTROL / "entry.py"
    import types
    c = types.ModuleType("exact_test_successor")
    c.__file__ = str(path)
    exec(compile(path.read_bytes(), str(path), "exec"), c.__dict__)
    c.context()
    c.authority({"capture": "backup", "rehearse-exact-d8a": "restore-rehearsal",
                 "preserve-and-verify-d8a": "recover", "repair-failed-rehearsal": "recover"}[sys.argv[1]])
    c.env_check(os.environ)
    return c


def pg_env():
    url = urlsplit(os.environ["ENTERPRISE_POC_DATABASE_URL"])
    env = dict(os.environ, PGHOST="127.0.0.1", PGPORT="55432", PGUSER="enterprise_agent_test",
               PGPASSWORD=unquote(url.password or ""), PGDATABASE="enterprise_agent_test")
    env.pop("PGOPTIONS", None)
    return env


def crypt(c, raw, *, decrypt=False):
    return c.run(["/usr/bin/gpg", "--batch", "--no-symkey-cache", "--pinentry-mode", "loopback",
                  "--passphrase-file", c.KEY,
                  *(["--decrypt"] if decrypt else ["--symmetric", "--cipher-algo", "AES256"])],
                 input=raw, timeout=180)


def member(c, name, raw):
    path = BACKUP / (name + ".gpg")
    encrypted = crypt(c, raw)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    with os.fdopen(fd, "wb") as stream:
        stream.write(encrypted); stream.flush(); os.fsync(stream.fileno())
    c.fsync_dir(BACKUP)
    c.need(crypt(c, path.read_bytes(), decrypt=True) == raw, "ENCRYPTED_MEMBER_READBACK")
    return {"path": str(path), "sha256": c.sha(path), "size": path.stat().st_size}


def redis_dump(c, client):
    captured = int(time.time() * 1000)
    rows = []
    for key in sorted(client.scan_iter()):
        blob = client.dump(key)
        ttl = client.pttl(key)
        c.need(blob is not None and ttl != -2, "REDIS_CHANGED_DURING_BACKUP")
        rows.append({"key": base64.b64encode(key).decode(), "dump": base64.b64encode(blob).decode(),
                     "expires_at_ms": captured + ttl if ttl >= 0 else None})
    return rows


def file_archive(c):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for root in DATA_ROOTS:
            c.need(root.exists() and not root.is_symlink(), "EXACT_TEST_DATA_ROOT")
            for path in (root, *sorted(root.rglob("*"))):
                info = path.lstat()
                c.need((stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode))
                       and not stat.S_ISLNK(info.st_mode) and info.st_uid in (0, 1000)
                       and not info.st_mode & 0o022 and (not path.is_file() or info.st_nlink == 1),
                       "DATA_ARCHIVE_TRUST")
                archive.add(path, arcname=str(path.relative_to(ROOT / "data")), recursive=False)
    c.need(output.tell() <= 512 * 1024 * 1024, "RESOURCE_SCOPE_REVIEW_REQUIRED")
    return output.getvalue()


def capture(c):
    c.authority("backup"); c.stopped()
    c.need(not BACKUP.exists(), "BACKUP_EXISTS_NO_RETRY")
    BACKUP.mkdir(mode=0o700)
    schema, data = c.snapshot()
    witness = c.active_witness()
    c.need(c.row_pins(data) == witness["rows"] and c.digest(schema) == witness["schema_sha256"],
           "QUIESCENT_PREDECESSOR_CHANGED")
    redis = c.runtime_call("redis-dump")
    database = c.run([c.PG / "pg_dump", "--format=custom", "--no-owner", "--no-acl"], env=pg_env(), timeout=180)
    c.need(len(database) <= 512 * 1024 * 1024, "DATABASE_RESOURCE_LIMIT")
    members = {"database": member(c, "database.dump", database),
               "redis": member(c, "redis.json", json.dumps(redis).encode()),
               "files": member(c, "files.tar", file_archive(c))}
    value = {"scope": c.CONTRACT, "operation": str(c.OP), "source": c.OLD_SOURCE,
             "tree": c.OLD_TREE, "captured_at": int(time.time()), "backup_members": members,
             "schema_sha256": c.digest(schema), "row_pins": c.row_pins(data),
             "metadata_pins": c.metadata_pins(), "actual_restore": "NOT_EXECUTED"}
    c.record(c.OP / "fresh-backup.json", value)
    return {"status": "ENCRYPTED_BACKUP_CAPTURED_RESTORE_NOT_PROVEN", "members": members}


def decrypt_members(c, rehearsal=False):
    fresh = c.read(c.OP / "fresh-backup.json")
    if not rehearsal and (c.OP / "ACTIVATE_ATTEMPTED.json").exists():
        proof = c.read(CONTROL / "backup-restored.verified.v1.json")
        c.need(proof["backup_members"] == fresh["backup_members"], "EXACT_SELECTED_BACKUP")
    plain = {}
    for name, spec in fresh["backup_members"].items():
        path = Path(spec["path"])
        c.native(path)
        c.need(path.is_relative_to(BACKUP) and c.sha(path) == spec["sha256"], "BACKUP_MEMBER_PIN")
        plain[name] = crypt(c, path.read_bytes(), decrypt=True)
    return plain, fresh


def restore_files(c, raw, rehearsal=False):
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        members = archive.getmembers()
        for item in members:
            name = Path(item.name)
            c.need(not name.is_absolute() and ".." not in name.parts and name.parts
                   and name.parts[0] in ("application", "object-storage")
                   and (item.isfile() or item.isdir()) and item.uid in (0, 1000)
                   and item.gid in (0, 1000) and not item.mode & 0o022,
                   "RESTORE_DATA_MEMBER_SCOPE")
        saved = c.OP / ("data-before-rehearsal" if rehearsal else "data-before-recovery")
        c.need(not saved.exists(), "RESTORE_ALREADY_STARTED")
        saved.mkdir(mode=0o700)
        for root in DATA_ROOTS:
            c.need(not root.is_symlink(), "DATA_ROOT_SYMLINK")
            root.rename(saved / root.name)  # preserve all newly generated outputs
        archive.extractall(ROOT / "data", members=members, filter="data")
        for item in members:
            path = ROOT / "data" / item.name
            os.chown(path, item.uid, item.gid); path.chmod(item.mode)
        c.fsync_dir(ROOT / "data")


def restore(c, rehearsal=False, repair=False):
    action = "restore-rehearsal" if rehearsal else "recover"
    prefix = "pre-rehearsal" if rehearsal else "pre-recovery"
    c.authority(action); c.stopped()
    if not rehearsal and not repair:
        # Normal exact d8a recovery is Source/config only. Schema is unchanged;
        # preserve newly registered1.0.1 and ALL accepted business test data.
        schema, data = c.snapshot()
        c.data_guard(schema, data, c.active_witness())
        c.registry_witness()
        c.record(c.OP / "DATA_PRESERVED.json", {"status": "BUSINESS_AND_REGISTRY_PRESERVED",
            "row_pins": c.row_pins(data), "schema_sha256": c.digest(schema),
            "registry_version": "1.0.1", "at": int(time.time())})
        return {"status": "BUSINESS_AND_REGISTRY_PRESERVED"}
    if repair:
        c.need((c.OP / "REHEARSAL_ATTEMPTED.json").exists()
               and not (c.OP / "ACTIVATE_ATTEMPTED.json").exists()
               and not (c.OP / "actual-d8a-restore-report.json").exists(), "REPAIR_ONLY_FAILED_PREACTIVATION_REHEARSAL")
    plain, fresh = decrypt_members(c, rehearsal=rehearsal)
    # Keep new test DB rows encrypted as audit, never silently erase them.
    member(c, prefix + ".database.dump", c.run([c.PG / "pg_dump", "--format=custom", "--no-owner", "--no-acl"],
                                                 env=pg_env(), timeout=180))
    c.authority(action)
    # PostgreSQL handles clean+restore in ONE transaction. The only DB target
    # is this server's dedicated enterprise_agent_test, never Production.
    c.run([c.PG / "pg_restore", "--clean", "--if-exists", "--single-transaction", "--exit-on-error",
           "--no-owner", "--no-acl", "--dbname=enterprise_agent_test"],
          input=plain["database"], env=pg_env(), timeout=180)
    member(c, prefix + ".redis.json", json.dumps(c.runtime_call("redis-dump")).encode())
    c.authority(action)
    c.runtime_call("redis-restore-repair" if repair else "redis-restore-rehearsal", payload=json.loads(plain["redis"]))
    actual = c.runtime_call("redis-dump")
    expected = json.loads(plain["redis"])
    c.need([(r["key"], r["dump"]) for r in actual if r["expires_at_ms"] is None]
           == [(r["key"], r["dump"]) for r in expected if r["expires_at_ms"] is None],
           "ACTUAL_RESTORED_REDIS")
    c.authority(action)
    restore_files(c, plain["files"], rehearsal=rehearsal)
    schema, data = c.snapshot()
    c.need(c.digest(schema) == fresh["schema_sha256"] and c.row_pins(data) == fresh["row_pins"], "ACTUAL_RESTORED_DATA")
    c.need(c.metadata_pins() == fresh["metadata_pins"], "ACTUAL_RESTORED_REFERENCES")
    c.record(c.OP / ("REHEARSAL_DATA_RESTORED.json" if rehearsal else "DATA_RESTORED.json"), {"status": "DATA_RESTORED_NOT_SERVICE_PASS",
              "backup_members": fresh["backup_members"], "source": c.OLD_SOURCE, "at": int(time.time())})
    return {"status": "DATA_RESTORED_NOT_SERVICE_PASS"}


import sys
if __name__ == "__main__":
    try:
        c = load()
        c.need(len(sys.argv) == 2 and sys.argv[1] in ("capture", "rehearse-exact-d8a", "preserve-and-verify-d8a", "repair-failed-rehearsal"), "EXACT_BACKUP_COMMAND")
        print(json.dumps(capture(c) if sys.argv[1] == "capture" else restore(c,
            sys.argv[1] == "rehearse-exact-d8a", sys.argv[1] == "repair-failed-rehearsal"), sort_keys=True))
    except Exception as error:
        print(json.dumps({"status": "BACKUP_OR_RESTORE_BLOCKED_PRESERVE_SCENE",
                          "category": str(error) if "c" in globals() and isinstance(error, c.Blocked) else type(error).__name__}))
        raise SystemExit(2)
