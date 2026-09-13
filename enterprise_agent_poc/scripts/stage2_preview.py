"""Explicit isolated Stage 2 launcher. Credentials stay in process memory only."""
import argparse
import ast
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.stage2_isolation import bootstrap, assert_isolated, IsolationError


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("role",choices=("check","provision","api","mcp","worker"))
    parser.add_argument("--config",required=True)
    args = parser.parse_args()
    config,settings = bootstrap(args.config)
    if args.role == "check":
        return
    # Hard gate has already passed; a second check guards each initialization.
    def gate():
        assert_isolated(settings,config["root"],config["snapshot"])
    gate()
    if args.role in {"api","worker"}:
        secret_file = Path(config["credential_file"])
        if secret_file.stat().st_mode & 0o077:
            raise IsolationError("Test credential file permissions must be 600; BLOCK")
        value = None
        for line in secret_file.read_text().splitlines():
            if line.strip().startswith("STAGE2_DEEPSEEK_TEST_API_KEY="):
                raw = line.partition("=")[2].strip()
                try:
                    value = ast.literal_eval(raw) if raw.startswith(("'",'"')) else raw
                except Exception:
                    raise IsolationError("Test credential syntax invalid; BLOCK") from None
        if not isinstance(value,str) or not value.strip():
            raise IsolationError("Test credential missing; BLOCK")
        os.environ["STAGE2_DEEPSEEK_TEST_API_KEY"] = value.strip()
    if args.role == "mcp":
        from app.platform_mcp.server import create_mcp, store
        gate()
        store.initialize()
        create_mcp().run(transport="streamable-http")
    elif args.role == "worker":
        raise IsolationError("Preview uses the API's local TaskService worker; separate worker forbidden")
    else:
        from app.main import store, product_store, agent_catalog_control, skill_registry
        gate()
        agent_catalog_control.runtime_tester.isolation_guard = gate
        from app.main import task_service
        task_service.pre_execute_guard = gate
        if args.role == "provision":
            from app.auth import hash_password
            store.seed_demo_data()
            gate()
            product_store.initialize()
            tenant = os.environ["STAGE2_RUNTIME_TEST_TENANT_ID"]
            with store.connection() as conn:
                conn.execute("INSERT OR IGNORE INTO tenants(id,name,poc_api_key) VALUES (?, 'Stage 2 合成测试', 'stage2-nonfunctional-fixture')",(tenant,))
                conn.execute("INSERT OR IGNORE INTO enterprise_configs VALUES (?, '{\"brand_name\":\"Stage 2 合成品牌\"}')",(tenant,))
                conn.execute("INSERT OR IGNORE INTO credit_accounts(tenant_id,balance) VALUES (?,1000)",(tenant,))
            product_store.create_user(tenant,"runtime@stage2.test",hash_password("Stage2Local!2026"),"Runtime Test","enterprise_admin")
            gate()
            skill_registry.initialize()
            gate()
            agent_catalog_control.ensure_initialized()
            return
        gate()
        # Local worker and Runtime Test share these exact object instances / Settings.
        from contextlib import asynccontextmanager
        from app.main import app, lifespan
        @asynccontextmanager
        async def guarded_lifespan(app):
            gate()
            async with lifespan(app):
                yield
        app.router.lifespan_context = guarded_lifespan
        import uvicorn
        uvicorn.run(app,host="127.0.0.1",port=config["api_port"],log_level="warning")


if __name__ == "__main__":
    try:
        main()
    except IsolationError as exc:
        raise SystemExit(str(exc)) from None
