"""Isolated, opt-in product-chain quality sample for the ungrounded WeChat draft.

Only synthetic local data is used. Credentials stay in the process environment;
articles and metadata are written solely to the explicitly supplied local path.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))


async def run() -> None:
    if os.environ.get("WECHAT_SIMPLIFIED_LIVE") != "1":
        raise RuntimeError("Explicit isolated live evaluation opt-in required")
    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("CREDENTIAL_RUNTIME_PROCESS=MISSING", flush=True)
        raise RuntimeError("Provider credential unavailable")
    print("CREDENTIAL_RUNTIME_PROCESS=SET", flush=True)
    output = Path(os.environ["WECHAT_SIMPLIFIED_LIVE_OUTPUT"])
    cases = json.loads((PROJECT / "evals/wechat_official_account_simplified_v1.json").read_text(encoding="utf-8"))["cases"]
    selected = {case_id.strip() for case_id in os.environ.get("WECHAT_SIMPLIFIED_CASE_IDS", "").split(",") if case_id.strip()}
    if selected:
        cases = [case for case in cases if case["id"] in selected]
        if {case["id"] for case in cases} != selected:
            raise ValueError("Unknown isolated evaluation case ID")
    isolated = Path(tempfile.mkdtemp(prefix="wechat-simplified-live-"))
    (isolated / "wechat-isolated.marker").write_text("synthetic-only", encoding="utf-8")
    server = manager = None
    try:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        os.environ.update({
            "APP_ENV": "test", "ENTERPRISE_POC_DATA_DIR": str(isolated / "data"),
            "ENTERPRISE_POC_DATABASE_URL": f"sqlite:///{isolated / 'product.db'}",
            "ENTERPRISE_POC_OBJECT_STORAGE_DIR": str(isolated / "objects"),
            "ENTERPRISE_POC_MCP_URL": f"http://127.0.0.1:{port}/mcp",
            "ENTERPRISE_POC_MCP_PORT": str(port), "ENTERPRISE_POC_TASK_QUEUE": "local",
            "ENTERPRISE_POC_MODEL_PROVIDER_ID": "deepseek",
            "ENTERPRISE_POC_MODEL_ID": "deepseek-v4-pro",
            "ENTERPRISE_POC_REASONING_EFFORT": "high",
        })
        from app.agent_execution import ExecutionResolver, profile
        from app.agent_productization import AgentProductization
        from app.product_service import TaskService
        from app.product_store import ProductStore
        from app.runtime.codex_provider import CodexRuntimeManager, CodexRuntimeProvider
        from app.security import RuntimeTokenIssuer
        from app.service import AgentService
        from app.settings import Settings
        from app.skill_registry import SkillRegistry
        from app.skills import SkillDeployment
        from app.store import POCStore
        from scripts.create_wechat_official_account_agent_v1 import create_draft

        settings = Settings.from_env()
        store = POCStore(settings.database_url)
        store.seed_demo_data()
        if store.enterprise_config("tenant-a").get("data_classification") != "synthetic_gate2_test_data":
            raise RuntimeError("Synthetic test tenant required")
        product = ProductStore(store)
        product.initialize()
        product.create_user("tenant-a", "wechat-live@tenant-a.test", "unused", "WeChat Test", "enterprise_admin")
        actor = product.user_by_email("wechat-live@tenant-a.test")["id"]
        registry = SkillRegistry(store, isolated / "registry", PROJECT / "skill_packages")
        registry.initialize()
        registry.grant_platform_admin(actor)
        control = AgentProductization(store)
        control.initialize()
        with store.connection() as conn:
            conn.execute(
                "UPDATE platform_compatibility_state SET epoch='productized_v1',epoch_rank=2,"
                "advanced_at=CURRENT_TIMESTAMP,advanced_by_release_id='isolated-wechat',"
                "advanced_by_source_commit=?,advance_origin='controlled_advance' "
                "WHERE scope='agent_data_contract'", ("f" * 40,),
            )
        resolver = ExecutionResolver(store, registry, control, settings, "tenant-a")
        resolver.initialize_local()
        control.execution_resolver = resolver
        product.execution_resolver = resolver
        draft = create_draft(control, actor)
        revision = draft["versions"][0]
        with store.connection() as conn:
            conn.execute(
                "INSERT INTO tenant_agent_instances(tenant_id,agent_id,status,instance_id,agent_template_version_id,overrides_json) "
                "VALUES ('tenant-a',?,'configured',?,?,'{}')",
                (draft["id"], str(uuid4()), revision["id"]),
            )
        server = subprocess.Popen(
            [sys.executable, "-m", "app.platform_mcp.server"], cwd=PROJECT,
            env={**os.environ, "PYTHONPATH": str(PROJECT) + os.pathsep + os.environ.get("PYTHONPATH", "")},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for _ in range(50):
            if server.poll() is not None:
                raise RuntimeError("Isolated MCP server exited")
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.close()
                await writer.wait_closed()
                break
            except OSError:
                await asyncio.sleep(0.2)
        else:
            raise RuntimeError("Isolated MCP server did not start")
        manager = CodexRuntimeManager(settings, SkillDeployment(PROJECT / "skill_packages"), RuntimeTokenIssuer(settings.token_secret))
        agents = AgentService(store, CodexRuntimeProvider(manager), settings)
        service = TaskService(product, agents)
        results = []
        comparison = None
        for case in cases:
            task = product.create_task(
                "tenant-a", actor, draft["id"], case["prompt"], None,
                _test_revision=revision["id"], _test_id=str(uuid4()),
            )
            with store.connection() as conn:
                snapshot = resolver.task_context(conn, task)
            policy = json.loads(snapshot["tool_policy_snapshot"])["grounding"]
            if policy != {"enabled": False} or profile(snapshot).grounding_policy != policy:
                raise RuntimeError("Grounding must be disabled in the actual Execution Context")
            start = time.monotonic()
            await asyncio.wait_for(service.execute(task), timeout=240)
            elapsed = round(time.monotonic() - start, 2)
            saved = product.task_for_worker(task["id"])
            trace = store.run_trace(saved["run_id"], "tenant-a") if saved.get("run_id") else None
            payload = trace["payload"] if trace else {}
            final = payload.get("final_result") or ""
            events = product.task_events_since(task["id"], "tenant-a", actor)
            deltas = [json.loads(event["message"])["text"] for event in events if event["stage"] == "delta"]
            result = {
                "id": case["id"], "prompt": case["prompt"], "check": case["check"],
                "status": saved["status"], "duration_seconds": elapsed,
                "grounding_policy": policy, "revision_id_matches": snapshot["agent_template_version_id"] == revision["id"],
                "fingerprint_matches": snapshot["configuration_fingerprint"] == revision["configuration_fingerprint"],
                "skill_manifest": json.loads(snapshot["skill_manifest_snapshot"]),
                "skill_discovered": any(e.get("event") == "skill_discovered" and e.get("skill") == "wechat-official-account-writing" for e in payload.get("lifecycle_events", [])),
                "delta_count": len(deltas), "delta_reconstruction_exact": "".join(deltas) == final,
                "final_response": final, "error_code": saved.get("error_code"),
            }
            results.append(result)
            output.write_text(json.dumps({"source": str(PROJECT), "model": "deepseek-v4-pro/high", "results": results, "comparison": comparison}, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"CASE={case['id']} STATUS={saved['status']} DURATION_SECONDS={elapsed} DELTAS={len(deltas)}", flush=True)
        same_theme = next((case["prompt"] for case in cases if case["id"] == "V7"), None)
        if same_theme:
            copy_task = product.create_task("tenant-a", actor, "copywriting-agent", same_theme, None)
            start = time.monotonic()
            await asyncio.wait_for(service.execute(copy_task), timeout=240)
            copy_elapsed = round(time.monotonic() - start, 2)
            copy_saved = product.task_for_worker(copy_task["id"])
            copy_trace = store.run_trace(copy_saved["run_id"], "tenant-a") if copy_saved.get("run_id") else None
            comparison = {
                "prompt": same_theme,
                "wechat_agent": "wechat-official-account-writing",
                "copywriting_agent": "copywriting-agent",
                "status": copy_saved["status"],
                "duration_seconds": copy_elapsed,
                "final_response": (copy_trace or {}).get("payload", {}).get("final_result") or "",
                "error_code": copy_saved.get("error_code"),
            }
            output.write_text(json.dumps({"source": str(PROJECT), "model": "deepseek-v4-pro/high", "results": results, "comparison": comparison}, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"CASE=V7_COPY STATUS={copy_saved['status']} DURATION_SECONDS={copy_elapsed}", flush=True)
    finally:
        if manager is not None:
            await manager.close()
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
        temporary_root = Path(tempfile.gettempdir()).resolve()
        if (not isolated.resolve().is_relative_to(temporary_root)
                or not isolated.name.startswith("wechat-simplified-live-")
                or (isolated / "wechat-isolated.marker").read_text(encoding="utf-8") != "synthetic-only"):
            raise RuntimeError("Unexpected isolated directory; refusing cleanup")
        shutil.rmtree(isolated)


if __name__ == "__main__":
    asyncio.run(run())
