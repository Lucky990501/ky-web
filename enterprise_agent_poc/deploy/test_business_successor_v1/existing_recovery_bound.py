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
import re
import shutil
import stat
import tarfile
import time
from urllib.parse import unquote, urlsplit

ROOT = Path("/opt/enterprise-agent-workbench-test")
CONTROL = Path("/etc/enterprise-agent-test-business-894-successor-v2")
BACKUP = ROOT / "backups/business-894-successor-v2"
DATA_ROOTS = (ROOT / "data/application", ROOT / "data/object-storage")
PG_RESTORE_MODE = "PRESERVE_VERIFIED_VECTOR_0_8_6_BUSINESS_ROLE_V1"


def pg_contract_catalog(db):
    """Stable names/owners/ACL/definitions, not OIDs or secret values."""
    queries = {
        "database": "SELECT pg_get_userbyid(datdba) AS owner,datacl::text AS acl,pg_encoding_to_char(encoding) AS encoding,datcollate,datctype FROM pg_database WHERE datname=current_database()",
        "extensions": "SELECT e.extname,e.extversion,n.nspname AS schema,pg_get_userbyid(e.extowner) AS owner,e.extrelocatable,obj_description(e.oid,'pg_extension') AS comment FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace ORDER BY 1",
        "schemas": "SELECT nspname,pg_get_userbyid(nspowner) AS owner,nspacl::text AS acl FROM pg_namespace WHERE nspname !~ '^pg_' AND nspname<>'information_schema' ORDER BY 1",
        "relations": "SELECT n.nspname,c.relname,c.relkind,pg_get_userbyid(c.relowner) AS owner,c.relacl::text AS acl,c.relrowsecurity,c.relforcerowsecurity,c.reloptions FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' ORDER BY 1,2,3",
        "functions": "SELECT p.proname,pg_get_function_identity_arguments(p.oid) AS args,p.prokind,pg_get_userbyid(p.proowner) AS owner,p.proacl::text AS acl,p.prosecdef,p.proconfig,CASE WHEN p.prokind='a' THEN md5(pg_get_function_arguments(p.oid)||pg_get_function_result(p.oid)) ELSE md5(pg_get_functiondef(p.oid)) END AS definition_hash FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' ORDER BY 1,2",
        "types": "SELECT t.typname,t.typtype,pg_get_userbyid(t.typowner) AS owner,t.typacl::text AS acl FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname='public' ORDER BY 1",
        "columns": "SELECT c.relname,a.attname,a.attnum,format_type(a.atttypid,a.atttypmod) AS type,a.attnotnull,a.attidentity,a.attgenerated,md5(pg_get_expr(d.adbin,d.adrelid)) AS default_hash FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m') AND a.attnum>0 AND NOT a.attisdropped ORDER BY 1,3",
        "constraints": "SELECT c.relname,k.conname,k.contype,md5(pg_get_constraintdef(k.oid)) AS definition_hash FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' ORDER BY 1,2",
        "indexes": "SELECT c.relname,md5(pg_get_indexdef(c.oid)) AS definition_hash FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='i' ORDER BY 1",
        "default_acls": "SELECT pg_get_userbyid(defaclrole) AS owner,defaclnamespace::regnamespace::text AS schema,defaclobjtype,defaclacl::text AS acl FROM pg_default_acl ORDER BY 1,2,3",
        "business_role": "SELECT rolname,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,rolinherit,rolcanlogin,rolconfig FROM pg_roles WHERE rolname='enterprise_agent_test'",
        "business_memberships": "SELECT pg_get_userbyid(roleid) AS role,admin_option FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname='enterprise_agent_test') ORDER BY 1",
    }
    value = {name: [dict(row) for row in db.execute(sql).fetchall()] for name, sql in queries.items()}
    value["sequences"] = []
    for row in value["relations"]:
        if row["relkind"] == "S":
            quoted = '"' + row["relname"].replace('"', '""') + '"'
            state = dict(db.execute('SELECT last_value,is_called FROM public.' + quoted).fetchone())
            value["sequences"].append({"name": row["relname"], **state})
    return value


def validate_pg_contract(c, value):
    c.need(value.get("mode") == PG_RESTORE_MODE and value.get("database") == "enterprise_agent_test"
           and value.get("version") == "16.6" and value.get("port") == 55432
           and value.get("business_executor") == "enterprise_agent_test"
           and value.get("maintenance_verifier") == "eaw_test_admin"
           and value.get("maintenance_readonly") is True and value.get("maintenance_catalog_match") is True,
           "EXACT_PG_RESTORE_IDENTITIES")
    state = value["catalog"]
    c.need(state["database"][0]["owner"] == "enterprise_agent_test"
           and state["database"][0]["acl"] is None, "RESTORE_DATABASE_OWNER_ACL")
    extensions = {row["extname"]: row for row in state["extensions"]}
    c.need(set(extensions) == {"vector", "plpgsql"}
           and (extensions["vector"]["extversion"], extensions["vector"]["schema"], extensions["vector"]["owner"]) ==
               ("0.8.6", "public", "eaw_test_admin")
           and (extensions["plpgsql"]["extversion"], extensions["plpgsql"]["schema"], extensions["plpgsql"]["owner"]) ==
               ("1.0", "pg_catalog", "eaw_test_admin"), "EXACT_PRESERVED_EXTENSION_CONTRACT")
    c.need(len(state["schemas"]) == 1 and state["schemas"][0]["nspname"] == "public"
           and state["schemas"][0]["owner"] == "pg_database_owner"
           and value.get("business_public_create") is True, "EXACT_PRESERVED_SCHEMA_CONTRACT")
    c.need(not state["default_acls"] and all(row["acl"] is None for kind in
           ("relations", "functions", "types") for row in state[kind]), "NONDEFAULT_OBJECT_ACL_NOT_IN_NO_ACL_ARCHIVE")
    role = state["business_role"][0]
    c.need(not any(role[key] for key in ("rolsuper", "rolcreatedb", "rolcreaterole", "rolreplication", "rolbypassrls")),
           "BUSINESS_ROLE_MUST_REMAIN_UNPRIVILEGED")
    c.need(all(row["owner"] == "enterprise_agent_test" for row in state["relations"]),
           "RESTORE_RELATION_OWNER")
    return value


def business_restore_toc(c, raw):
    """Only exact two vector entries are preserved; every other TOC is checked."""
    allowed = ("SEQUENCE OWNED BY", "SEQUENCE SET", "FK CONSTRAINT", "TABLE DATA",
               "FUNCTION", "TABLE", "SEQUENCE", "DEFAULT", "CONSTRAINT", "INDEX", "TRIGGER")
    lines, kept, skipped, seen = [], [], [], set()
    for line in raw.decode("utf-8").splitlines():
        if not line or line.startswith(";"): continue
        match = re.fullmatch(r"(\d+); (\d+) (\d+) (.+)", line)
        c.need(match is not None and match[1] not in seen, "RESTORE_TOC_FORMAT_OR_DUPLICATE")
        seen.add(match[1]); body = match[4].strip()
        if body in ("EXTENSION - vector", "COMMENT - EXTENSION vector"):
            skipped.append(body)
            continue
        kind = next((kind for kind in allowed if body.startswith(kind + " public ")), None)
        c.need(kind is not None and body.endswith(" enterprise_agent_test"),
               "RESTORE_TOC_PRIVILEGE_OR_SCHEMA_UNSUPPORTED:" + match[1])
        kept.append(match[1]); lines.append(line)
    c.need(sorted(skipped) == ["COMMENT - EXTENSION vector", "EXTENSION - vector"] and kept,
           "EXACT_VECTOR_TOC_PRESERVATION")
    selected = ("\n".join(lines) + "\n").encode()
    return selected, {"mode": PG_RESTORE_MODE, "input_toc_sha256": c.hashlib.sha256(raw).hexdigest(),
        "selected_toc_sha256": c.hashlib.sha256(selected).hexdigest(), "selected_count": len(kept),
        "preserved_entries": sorted(skipped), "business_executor": "enterprise_agent_test"}


def restore_database(c, database, fresh, action, prefix):
    expected = validate_pg_contract(c, fresh["pg_restore_contract"])
    actual = validate_pg_contract(c, c.runtime_call("pg-restore-contract"))
    c.need(actual == expected, "PG_CONTRACT_CHANGED_BEFORE_RESTORE")
    raw_toc = c.run([c.PG / "pg_restore", "--list"], input=database, env=pg_env())
    selected, plan = business_restore_toc(c, raw_toc)
    path = c.OP / (prefix + ".business-restore.list")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444)
    with os.fdopen(fd, "wb") as stream:
        os.fchmod(stream.fileno(), 0o444); stream.write(selected); stream.flush(); os.fsync(stream.fileno())
    c.fsync_dir(c.OP); c.native(path)
    c.need(c.sha(path) == plan["selected_toc_sha256"], "RESTORE_TOC_PIN")
    c.need(c.authority(action).get("pg_restore_mode") == PG_RESTORE_MODE, "EXPLICIT_PG_RESTORE_MODE_AUTHORITY")
    # Business role alone restores ordinary objects. No extension/schema DDL,
    # no admin write, role elevation or ACL suppression beyond the saved dump.
    c.run([c.PG / "pg_restore", "--clean", "--if-exists", "--single-transaction", "--exit-on-error",
           "--no-owner", "--no-acl", "--use-list=" + str(path), "--dbname=enterprise_agent_test"],
          input=database, env=pg_env(), timeout=180)
    restored = validate_pg_contract(c, c.runtime_call("pg-restore-contract"))
    c.need(restored == expected, "ACTUAL_RESTORED_PG_OWNER_ACL_EXTENSION_SCHEMA")
    plan.update(database_plain_sha256=c.hashlib.sha256(database).hexdigest(),
                encrypted_backup_sha256=fresh["backup_members"]["database"]["sha256"],
                actual_contract_sha256=c.digest(restored), database="enterprise_agent_test")
    c.record(c.OP / (prefix + ".pg-restore-verified.json"), plan)
    return plan


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
    # Keep profile/cache creation inside this approved private backup, never
    # implicit /root/.gnupg or an ordinary user's options/config as Root.
    explicit_unprivileged_home = os.environ.get("GNUPGHOME") if os.geteuid() != 0 else None
    if explicit_unprivileged_home:
        # Ordinary-user engineering clients retain their own explicit private
        # profile. Root ALWAYS ignores this environment setting.
        home = Path(explicit_unprivileged_home)
    else:
        for path in (BACKUP, *BACKUP.parents):
            info = path.lstat()
            c.need(stat.S_ISDIR(info.st_mode) and info.st_uid in (0, os.geteuid())
                   and not info.st_mode & 0o022, "GPG_BACKUP_ANCESTOR_TRUST")
        home = BACKUP / "gnupg"
    if not home.exists(): home.mkdir(mode=0o700)
    info = home.lstat()
    c.need(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
           and stat.S_IMODE(info.st_mode) == 0o700, "PRIVATE_BACKUP_GPG_HOME")
    return c.run(["/usr/bin/gpg", "--no-options", "--homedir", home,
                  "--batch", "--no-symkey-cache", "--pinentry-mode", "loopback",
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
    c.need(c.authority("backup").get("pg_restore_mode") == PG_RESTORE_MODE, "EXPLICIT_PG_RESTORE_MODE_AUTHORITY")
    pg_contract = validate_pg_contract(c, c.runtime_call("pg-restore-contract"))
    c.need(not BACKUP.exists(), "BACKUP_EXISTS_NO_RETRY")
    BACKUP.mkdir(mode=0o700)
    schema, data = c.snapshot()
    witness = c.active_witness()
    c.need(c.row_pins(data) == witness["rows"] and c.digest(schema) == witness["schema_sha256"],
           "QUIESCENT_PREDECESSOR_CHANGED")
    redis = c.runtime_call("redis-dump")
    database = c.run([c.PG / "pg_dump", "--format=custom", "--no-owner", "--no-acl"], env=pg_env(), timeout=180)
    business_restore_toc(c, c.run([c.PG / "pg_restore", "--list"], input=database, env=pg_env()))
    c.need(len(database) <= 512 * 1024 * 1024, "DATABASE_RESOURCE_LIMIT")
    members = {"database": member(c, "database.dump", database),
               "redis": member(c, "redis.json", json.dumps(redis).encode()),
               "files": member(c, "files.tar", file_archive(c))}
    value = {"scope": c.CONTRACT, "operation": str(c.OP), "source": c.OLD_SOURCE,
             "tree": c.OLD_TREE, "captured_at": int(time.time()), "backup_members": members,
             "schema_sha256": c.digest(schema), "row_pins": c.row_pins(data),
             "metadata_pins": c.metadata_pins(), "pg_restore_contract": pg_contract, "actual_restore": "NOT_EXECUTED"}
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
    restore_database(c, plain["database"], fresh, action, prefix)
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
