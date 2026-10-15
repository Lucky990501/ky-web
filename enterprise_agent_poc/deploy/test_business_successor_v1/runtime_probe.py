"""UID1000-only application/library side of exact Test tooling.

Root controller verifies this file BEFORE demotion/launch. This process never
receives Root privileges and cannot create an approval, Witness or unit file.
"""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import types

HERE = Path(__file__).parent


def compiled(name, path):
    value = types.ModuleType(name); value.__file__ = str(path)
    exec(compile(path.read_bytes(), str(path), "exec"), value.__dict__)
    return value


def libraries(c, project):
    c.source_identity(project.parent, c.SOURCE, c.TREE) if project == c.RELEASE / "enterprise_agent_poc" else None
    sys.path.insert(0, str(project))
    from app.settings import Settings
    from app.store import POCStore
    from app.skill_registry import SkillRegistry
    settings = Settings.from_env()
    c.need(settings.data_dir.resolve() == c.ROOT / "data/application", "EXACT_TEST_REGISTRY_DATA_ROOT")
    store = POCStore(settings.database_url)
    factory = lambda scoped: SkillRegistry(scoped, settings.data_dir / "skill-registry", project / "skill_packages")
    return store, factory


def rows(connection):
    output = {}
    tables = connection.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall()
    for row in tables:
        name = row["tablename"]
        if not name.replace("_", "").isalnum(): raise RuntimeError("TABLE_NAME")
        output[name] = [r["row"] for r in connection.execute('SELECT to_jsonb(t) AS row FROM "' + name + '" t LIMIT 10001')]
        if len(output[name]) > 10000: raise RuntimeError("TABLE_RESOURCE_SCOPE")
        output[name].sort(key=c_digest)
    return output


def c_digest(row):
    return hashlib.sha256(json.dumps(row, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, default=str).encode()).hexdigest()


def snapshot(c):
    c.env_check(os.environ)
    import psycopg
    with psycopg.connect(os.environ["ENTERPRISE_POC_DATABASE_URL"],
                         options="-c default_transaction_read_only=on -c statement_timeout=10000") as db:
        db.execute("SET TRANSACTION READ ONLY")
        if db.execute("SELECT current_database(),current_user,inet_server_port(),current_setting('server_version')").fetchone() != (
                "enterprise_agent_test", "enterprise_agent_test", 55432, "16.6"):
            raise RuntimeError("EXACT_TEST_PG_VERSION_ROLE")
        if db.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone() != ("0.8.6",):
            raise RuntimeError("EXACT_TEST_VECTOR_EXTENSION")
    return c.old_guard().base.snapshot()


def original_guard(c, topology=False):
    old = c.old_guard()
    if not (c.OP / "registry-witness.v2.json").exists():
        return old.verify(topology=topology)
    baseline = c.read(c.OP / "prepare.json")
    witness = c.registry_witness()
    schema, data = snapshot(c)
    c.data_guard(schema, data, witness["active_baseline"])
    extension = compiled("exact_registry_extension", HERE / "registry_extension.py")
    projected = extension.project_original(data, witness, baseline)
    old.base.snapshot = lambda: (schema, projected)
    result = old.verify(topology=topology)
    return dict(result, compatibility="EXACT_VERSIONED_REGISTRY_AND_TASK_OVERLAY",
                historical_witness_covers_new_rows=False, actual_registry_version="1.0.1_PRESERVED")


def register(c):
    c.authority("registry")
    project = c.RELEASE / "enterprise_agent_poc"
    store, factory = libraries(c, project)
    extension = compiled("exact_registry_extension", HERE / "registry_extension.py")
    # The unchanged official initializer is imported from exact Application.
    grant_module = compiled("official_admin_initializer", project / "scripts/grant_platform_admin.py")
    path = project / "skill_packages/wechat-official-account-writing/1.0.1.zip"
    if not path.exists():
        manifest = c.read(project / "skill_packages/manifest.json")
        entry = next(x for x in manifest["skills"] if x["skill_slug"] == extension.SLUG and x["version"] == extension.VERSION)
        path = project / "skill_packages" / entry["artifact_path"]
    def current(connection):
        if connection is not None: return rows(connection)
        return snapshot(c)[1]
    return extension.register(store, factory, grant_module.grant_platform_admin_access,
        path.read_bytes(), c.USER, lambda: c.authority("registry"),
        before_rows=lambda: snapshot(c)[1], after_rows=current)


def redis_dump(c):
    from redis import Redis
    client = Redis.from_url(os.environ["REDIS_URL"])
    helper = compiled("exact_recovery", HERE / "existing_recovery_bound.py")
    try: return helper.redis_dump(c, client)
    finally: client.close()


def redis_restore(c, values, repair=False):
    from redis import Redis
    client = Redis.from_url(os.environ["REDIS_URL"])
    try:
        pipeline = client.pipeline(transaction=True)
        pipeline.flushdb()
        import time
        now = int(time.time() * 1000)
        for row in values:
            ttl = 0 if row["expires_at_ms"] is None else row["expires_at_ms"] - now
            if ttl > 0 or row["expires_at_ms"] is None:
                pipeline.restore(base64.b64decode(row["key"]), max(ttl, 0), base64.b64decode(row["dump"]), replace=False)
        c.authority("recover" if repair else "restore-rehearsal")
        pipeline.execute()
        return {"status": "RESTORED"}
    finally: client.close()


def pg_restore_contract(c):
    """Existing Test maintainer verifies catalogs READ ONLY; never restores as admin."""
    import psycopg
    from psycopg.rows import dict_row
    helper = compiled("exact_pg_restore_contract", HERE / "existing_recovery_bound.py")
    c.env_check(os.environ)
    options = "-c default_transaction_read_only=on -c statement_timeout=10000"
    with psycopg.connect(os.environ["ENTERPRISE_POC_DATABASE_URL"], row_factory=dict_row, options=options) as db:
        db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        identity = db.execute("SELECT current_database() AS database,current_user AS role,inet_server_port() AS port,current_setting('server_version') AS version").fetchone()
        c.need((identity["database"], identity["role"], identity["port"], identity["version"]) ==
               ("enterprise_agent_test", "enterprise_agent_test", 55432, "16.6"), "EXACT_TEST_RESTORE_BUSINESS_IDENTITY")
        catalog = helper.pg_contract_catalog(db)
        can_create = db.execute("SELECT has_schema_privilege(current_user,'public','CREATE')").fetchone()
        can_create = next(iter(can_create.values()))
    key = c.ROOT / "shared/credentials/postgresql-admin-password"
    for path in (key, *key.parents):
        info = path.lstat()
        c.need(not stat.S_ISLNK(info.st_mode) and info.st_uid in (0, 1000)
               and not info.st_mode & 0o022, "EXISTING_TEST_MAINTENANCE_CREDENTIAL_TRUST")
    info = key.lstat()
    c.need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600,
           "EXISTING_TEST_MAINTENANCE_CREDENTIAL_PROTECTION")
    # Existing formal Test maintainer only; no Production credential or new role.
    with psycopg.connect(host="127.0.0.1", port=55432, dbname="enterprise_agent_test",
            user="eaw_test_admin", password=key.read_text().strip(), row_factory=dict_row, options=options) as db:
        db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        actor = db.execute("SELECT current_user AS role,current_database() AS database,inet_server_port() AS port").fetchone()
        c.need(tuple(actor.values()) == ("eaw_test_admin", "enterprise_agent_test", 55432), "EXACT_EXISTING_MAINTENANCE_VERIFIER")
        maintenance_catalog = helper.pg_contract_catalog(db)
    c.need(catalog == maintenance_catalog, "MAINTENANCE_READONLY_CATALOG_MISMATCH")
    return helper.validate_pg_contract(c, {"mode": helper.PG_RESTORE_MODE,
        "database": identity["database"], "version": identity["version"], "port": identity["port"],
        "business_executor": "enterprise_agent_test", "maintenance_verifier": "eaw_test_admin",
        "maintenance_readonly": True, "maintenance_catalog_match": True,
        "business_public_create": bool(can_create), "catalog": catalog})


def main():
    c = compiled("exact_test_entry", HERE / "entry.py")
    if os.geteuid() != 1000 or Path(sys.executable).resolve() != c.APPLICATION_PYTHON.resolve():
        raise RuntimeError("APPLICATION_RUNTIME_UID1000_ONLY")
    c.env_check(os.environ)
    command = sys.argv[1]
    if command == "snapshot": return snapshot(c)
    if command == "old-state": return original_guard(c, bool(int(sys.argv[2])))
    if command == "registry": return register(c)
    if command == "registry-check":
        store, factory = libraries(c, c.RELEASE / "enterprise_agent_poc")
        result = factory(store).verify_bootstrap()
        return {"status": result["status"], "mode": result["mode"]}
    if command == "redis-dump": return redis_dump(c)
    if command == "redis-restore-rehearsal": return redis_restore(c, json.load(sys.stdin))
    if command == "redis-restore-repair": return redis_restore(c, json.load(sys.stdin), repair=True)
    if command == "pg-restore-contract": return pg_restore_contract(c)
    if command == "idle":
        from redis import Redis
        client = Redis.from_url(os.environ["REDIS_URL"])
        try:
            if any(client.type(k) == b"list" and client.llen(k) for k in client.scan_iter()):
                raise RuntimeError("NONEMPTY_REDIS_QUEUE")
            return {"status": "IDLE"}
        finally: client.close()
    if command == "health":
        import asyncio
        from datetime import timedelta
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        async def ping():
            async with streamablehttp_client("http://127.0.0.1:18101/mcp", timeout=timedelta(seconds=10)) as (r,w,_):
                async with ClientSession(r,w) as session:
                    await session.initialize(); await session.send_ping()
        asyncio.run(ping())
        from redis import Redis
        client = Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
        try:
            if not any(r["cmd"].lower() in ("brpoplpush","rpoplpush") for r in client.client_list()):
                raise RuntimeError("WORKER_QUEUE_LOOP_NOT_PROVEN")
        finally: client.close()
        return {"mcp": "PING_PASS", "worker": "QUEUE_PASS"}
    raise RuntimeError("EXACT_RUNTIME_PROBE_COMMAND")


if __name__ == "__main__":
    try:
        print(json.dumps(main(), sort_keys=True, default=str))
    except Exception as error:
        print(json.dumps({"status": "RUNTIME_PROBE_BLOCKED", "category": type(error).__name__}))
        raise SystemExit(2)
