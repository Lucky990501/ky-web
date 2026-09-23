"""Ten isolated synthetic DeepSeek runs; emit metadata only, never plans or credentials."""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from verify_activity_plan_runtime_live import _load_key  # noqa: E402


SCENARIOS = (
    ("R1 ordinary", "发布技术验证：请为社区教师节设计完整活动方案，包含主题、时间、地点、对象、宣发、活动环节和邀约文案。时间地点未定，保留待确认；不要承诺企业权益。"),
    ("R2 time place", "请设计完整社区教师节活动方案，活动时间和场地都未知，必须列为待确认，不要在邀约中当作已确定。"),
    ("R3 staff", "请设计完整社区教师节活动方案。工作人员与讲解人员尚未确认，活动环节可提出建议，但不得承诺一定有人执行。"),
    ("R4 material", "请设计完整社区教师节活动方案。心愿卡和手作物料尚未采购，保留创意但将执行与邀约写成条件性提案。"),
    ("R5 fake benefit", "请设计完整社区教师节活动方案。有人提出现场赠送价值1999元课程，但这项企业权益未经核实；不得把它写成已确认福利。"),
    ("R6 verified fact", "请设计完整社区教师节活动方案。可使用企业资料中已核实的品牌名称和官方口号，但不要编造课程优惠或权益。"),
    ("R7 multiple pending", "请设计完整社区教师节活动方案。工作人员、心愿卡物料、活动场地和日期均待确认；每个依赖都要保持条件性。"),
    ("R8 creative", "请设计完整社区教师节活动方案，提出教师心愿卡、社区共读等创意环节；这些只是活动建议，不需要声称企业已经提供资源。"),
    ("R9 repeat", "发布技术验证：请为社区教师节设计完整活动方案，包含主题、时间、地点、对象、宣发、活动环节和邀约文案。时间地点未定，保留待确认；不要承诺企业权益。"),
    ("R10 repeat", "发布技术验证：请为社区教师节设计完整活动方案，包含主题、时间、地点、对象、宣发、活动环节和邀约文案。时间地点未定，保留待确认；不要承诺企业权益。"),
)


async def _collect_sse(response) -> dict:
    first = complete = previous = None
    longest_gap = 0.0
    terminal = {}
    async for chunk in response.body_iterator:
        now = time.monotonic()
        if first is None:
            first = now
        if previous is not None:
            longest_gap = max(longest_gap, now - previous)
        previous = now
        data = chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
        if "event: complete\ndata: " in data:
            terminal = json.loads(data.split("event: complete\ndata: ", 1)[1].split("\n\n", 1)[0])
            complete = now
    return {"first": first, "complete": complete, "longest_gap": longest_gap, "terminal": terminal}


async def _run() -> None:
    diagnose = sys.argv[1] if len(sys.argv) == 2 and sys.argv[1] in {"--diagnose-r1", "--diagnose-r4"} else None
    scenarios = (SCENARIOS[0],) if diagnose == "--diagnose-r1" else (SCENARIOS[3],) if diagnose == "--diagnose-r4" else SCENARIOS
    _load_key()
    isolated = Path(tempfile.mkdtemp(prefix="activity-plan-stability-"))
    server = None
    manager = None
    try:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        os.environ.update({
            "APP_ENV": "test",
            "ENTERPRISE_POC_DATA_DIR": str(isolated / "data"),
            "ENTERPRISE_POC_DATABASE_URL": f"sqlite:///{isolated / 'product.db'}",
            "ENTERPRISE_POC_OBJECT_STORAGE_DIR": str(isolated / "objects"),
            "ENTERPRISE_POC_MCP_URL": f"http://127.0.0.1:{port}/mcp",
            "ENTERPRISE_POC_MCP_PORT": str(port),
            "ENTERPRISE_POC_TASK_QUEUE": "local",
            "ENTERPRISE_POC_MODEL_PROVIDER_ID": "deepseek",
            "ENTERPRISE_POC_MODEL_ID": "deepseek-v4-pro",
            "ENTERPRISE_POC_REASONING_EFFORT": "high",
        })
        from app.activity_plan_runtime import is_full_activity_plan, validated_envelope
        from app.agent_catalog import CATALOG
        from app.auth import SessionIssuer, hash_password
        from app.product_service import TaskService
        from app.product_store import ProductStore
        from app.runtime.codex_provider import CodexRuntimeManager, CodexRuntimeProvider
        from app.security import RuntimeTokenIssuer
        from app.service import AgentService
        from app.settings import Settings
        from app.skills import SkillDeployment
        from app.store import POCStore

        if diagnose:
            from app.runtime import codex_provider
            guard = codex_provider.semantic_guard

            def synthetic_diagnostic_guard(plan, grounding=()):
                issues = guard(plan, grounding)
                print("SYNTHETIC_VIOLATIONS " + json.dumps([
                    {"type": issue.type, "field": issue.field,
                     "evidence_excerpt": issue.evidence[:120].replace("启明教育", "[synthetic brand]"),
                     "pending_excerpt": issue.pending_item[:80].replace("启明教育", "[synthetic brand]")}
                    for issue in issues
                ], ensure_ascii=False), flush=True)
                return issues

            codex_provider.semantic_guard = synthetic_diagnostic_guard

        if not Path(sys.modules["app"].__file__).resolve().is_relative_to(PROJECT):
            raise RuntimeError("wrong source tree")
        settings = Settings.from_env()
        store = POCStore(settings.database_url)
        store.seed_demo_data()
        if store.enterprise_config("tenant-a").get("data_classification") != "synthetic_gate2_test_data":
            raise RuntimeError("requires synthetic tenant data")
        product = ProductStore(store)
        product.initialize()
        product.create_user("tenant-a", "stability@tenant-a.test", hash_password("TemporaryLocal!2026"), "隔离联调成员", "member")
        member = product.user_by_email("stability@tenant-a.test")
        server = subprocess.Popen(
            [sys.executable, "-m", "app.platform_mcp.server"], cwd=PROJECT,
            env={**os.environ, "PYTHONPATH": str(PROJECT)}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        manager = CodexRuntimeManager(settings, SkillDeployment(PROJECT / "skill_packages"), RuntimeTokenIssuer(settings.token_secret))
        for _ in range(50):
            if server.poll() is not None:
                raise RuntimeError("isolated MCP server exited")
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.close()
                await writer.wait_closed()
                break
            except OSError:
                await asyncio.sleep(0.2)
        else:
            raise RuntimeError("isolated MCP server did not start")
        agents = AgentService(store, CodexRuntimeProvider(manager), settings,
                              lambda agent_id: CATALOG[agent_id].skill_manifest)
        service = TaskService(product, agents)
        from app import main
        main.product_store = product
        main.sessions = SessionIssuer(settings.token_secret)
        token = main.sessions.issue(main.UserPrincipal(
            member["id"], "tenant-a", "member", main.sessions.credential_version(member["password_hash"])
        ))
        client = TestClient(main.app)
        client.cookies.set("workbench_session", token)
        results = []
        try:
            for name, prompt in scenarios:
                if not is_full_activity_plan("campaign-agent", prompt):
                    raise RuntimeError(f"scenario not classified as full plan: {name}")
                task = product.create_task("tenant-a", member["id"], "campaign-agent", prompt, None)
                started = time.monotonic()
                stream = await main.stream_task_events(task["id"], after=0, workbench_session=token)
                sse_future = asyncio.create_task(_collect_sse(stream))
                await asyncio.wait_for(service.execute(task), timeout=240)
                sse = await asyncio.wait_for(sse_future, timeout=10)
                runtime_seconds = time.monotonic() - started
                saved = product.task(task["id"], "tenant-a", member["id"])
                trace = store.run_trace(saved["run_id"], "tenant-a") if saved.get("run_id") else None
                evidence = (trace or {}).get("payload", {})
                structured = saved.get("structured_result")
                grounding = tuple(str(call.get("output_summary") or "") for call in evidence.get("mcp_calls", [])
                                  if call.get("tool") in {"knowledge_search", "enterprise_config_get"}
                                  and call.get("status") == "completed")
                contract_valid = bool(structured and validated_envelope(structured, saved.get("final_response") or "", grounding) == structured)
                document_ok = False
                if contract_valid:
                    created = client.post("/api/v1/documents/activity-plan", json={"content": structured["data"]})
                    if created.status_code == 201:
                        downloaded = client.get(created.json()["download_url"])
                        document_ok = downloaded.status_code == 200 and downloaded.content.startswith(b"PK")
                attempts = evidence.get("structured_attempt_trace") or []
                record = {
                    "scenario": name, "conversation_completed": saved["status"] == "completed",
                    "structured_result": bool(structured), "contract_valid": contract_valid,
                    "document_post_ok": document_ok,
                    "status": evidence.get("structured_result_status"),
                    "model_calls": evidence.get("total_model_calls_for_structured_result"),
                    "initial_pass": evidence.get("structured_result_status") == "validated_initial",
                    "correction_invoked": any(step.get("stage") == "CORRECTION" for step in attempts),
                    "correction_success": evidence.get("structured_result_status") == "validated_after_retry",
                    "first_event_seconds": round(sse["first"] - started, 3) if sse["first"] else None,
                    "complete_seconds": round(sse["complete"] - started, 3) if sse["complete"] else None,
                    "longest_event_gap_seconds": round(sse["longest_gap"], 3),
                    "total_runtime_seconds": round(runtime_seconds, 3),
                    "sse_complete_match": sse["terminal"].get("structured_result") == structured
                    and sse["terminal"].get("assistant_message_id") == saved.get("assistant_message_id"),
                    "attempt_trace": attempts if diagnose or evidence.get("structured_result_status") not in
                    {"validated_initial", "validated_after_retry"} else None,
                }
                print(json.dumps(record, ensure_ascii=False), flush=True)
                results.append(record)
        finally:
            client.close()
        summary = {
            "runs": len(results), "initial_pass": sum(item["initial_pass"] for item in results),
            "correction_invoked": sum(item["correction_invoked"] for item in results),
            "correction_success": sum(item["correction_success"] for item in results),
            "final_valid": sum(item["structured_result"] and item["contract_valid"] for item in results),
            "final_semantic_guard_failed": sum(item["status"] == "semantic_guard_failed_after_retry" for item in results),
            "average_model_calls": round(sum(item["model_calls"] or 0 for item in results) / len(results), 3),
            "max_model_calls": max(item["model_calls"] or 0 for item in results),
            "document_eligible": sum(item["document_post_ok"] for item in results),
            "max_complete_seconds": max(item["complete_seconds"] or 0 for item in results),
            "max_event_gap_seconds": max(item["longest_event_gap_seconds"] for item in results),
            "max_runtime_seconds": max(item["total_runtime_seconds"] for item in results),
        }
        print("SUMMARY " + json.dumps(summary, ensure_ascii=False), flush=True)
        if diagnose:
            return
        if (summary["final_valid"] != 10 or summary["document_eligible"] != 10
                or summary["final_semantic_guard_failed"] or summary["max_model_calls"] > 2
                or not all(item["conversation_completed"] and item["sse_complete_match"] for item in results)):
            raise RuntimeError("real model stability gate failed")
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
        if not isolated.resolve().is_relative_to(temporary_root) or not isolated.name.startswith("activity-plan-stability-"):
            raise RuntimeError("unexpected isolated directory; refusing cleanup")
        import shutil
        shutil.rmtree(isolated)
        os.environ.pop("DEEPSEEK_API_KEY", None)


if __name__ == "__main__":
    asyncio.run(_run())
