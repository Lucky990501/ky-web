"""Release-owned read-only state checks, recovery evidence and bounded smoke.

No Registry initialization, package staging or account provisioning. Only smoke
creates tasks and a document, using an explicitly supplied, operator-approved
smoke account.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class GateFailed(ValueError):
    pass


def require(value, reason):
    if not value:
        raise GateFailed(reason)


def registry_for(base):
    from app.skill_registry import SkillRegistry
    from app.store import POCStore
    return SkillRegistry(POCStore(os.environ["ENTERPRISE_POC_DATABASE_URL"]),
                         base / "shared/runtime-data/skill-registry", ROOT / "skill_packages")


def bindings(registry):
    from scripts.release_binding_transition import registry_bindings, registry_manifest, TransitionBlocked
    with registry._read_connection() as conn:
        rows = conn.execute("SELECT b.agent_id,b.skill_id,v.skill_id AS version_skill_id,v.status,a.id AS agent_exists,s.id AS skill_exists FROM agent_skill_bindings b LEFT JOIN skill_versions v ON v.id=b.skill_version_id LEFT JOIN skills s ON s.id=b.skill_id LEFT JOIN agent_templates a ON a.id=b.agent_id").fetchall()
        require(all(r["agent_exists"] and r["skill_exists"] and r["skill_id"] == r["version_skill_id"]
                    and r["status"] == "published" for r in rows), "UNKNOWN_BINDING_STATE")
        result = {}
        for row in conn.execute("SELECT id FROM agent_templates ORDER BY id").fetchall():
            agent = row["id"]
            active = registry_bindings(conn, agent)
            try:
                manifest = registry_manifest(conn, agent)
            except TransitionBlocked as exc:
                raise GateFailed("UNKNOWN_BINDING_STATE") from exc
            require(manifest == active, "UNKNOWN_BINDING_STATE")
            result[agent] = active
    return result


def expected_bindings(candidate, predecessor, snapshot):
    from scripts.release_binding_transition import declaration, sha256
    before = json.loads(snapshot.read_text())
    after = json.loads(snapshot.read_text())
    manifest = json.loads(candidate.read_text())
    if "binding_transition" in manifest:
        for item in declaration(manifest, json.loads(predecessor.read_text()), sha256(predecessor.read_bytes())):
            require(before.get(item["agent_id"]) == item["from"], "snapshot_not_declared_from")
            after[item["agent_id"]] = item["to"]
    return before, after


def binding_gate(base, candidate, predecessor, snapshot, mode):
    before, after = expected_bindings(candidate, predecessor, snapshot)
    actual = bindings(registry_for(base))
    if mode == "rollback-guard":
        # Before activation, a staging failure may leave the proven FROM state.
        # After activation only exact TO permits automatic mutation. Never
        # overwrite unknown/mixed rows, including drift in unrelated agents.
        prior = json.loads(predecessor.read_text())["release_id"]
        pre_activation = (base / "release-current").resolve() == base / "releases" / prior / "enterprise_agent_poc"
        require(actual == after or (pre_activation and actual == before), "UNKNOWN_BINDING_STATE")
    else:
        require(actual == (before if mode == "restored" else after), "UNKNOWN_BINDING_STATE")
    return actual


def service_state(base):
    result = {}
    for role in ("api", "mcp", "worker"):
        try:
            output = subprocess.check_output(["systemctl", "show", f"enterprise-agent-{role}.service",
                                              "-p", "ActiveState", "-p", "MainPID"], text=True, timeout=10)
            values = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
            pid = int(values.get("MainPID", "0"))
            proc = Path("/proc") / str(pid)
            result[role] = {"active": values.get("ActiveState"), "pid": pid,
                            "cwd": str((proc / "cwd").resolve(strict=True)),
                            "exe": str((proc / "exe").resolve(strict=True)),
                            "module_ok": {"api": b"app.main:app", "mcp": b"app.platform_mcp.server", "worker": b"app.worker"}[role]
                            in (proc / "cmdline").read_bytes().split(b"\0")}
        except Exception:
            result[role] = {"error": "service_unreadable"}
    return result


async def mcp_health():
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    from app.settings import settings
    async with streamablehttp_client(settings.platform_mcp_url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await session.send_ping()
            result = await session.list_tools()
            require({"enterprise_config_get", "knowledge_search", "asset_search"} <= {t.name for t in result.tools}, "mcp_tools")


def health():
    import httpx
    from app.settings import settings
    from app.task_queue import RedisTaskQueue
    response = httpx.get("http://127.0.0.1:18090/api/health", timeout=10, trust_env=False)
    require(response.status_code == 200, "api_health_http")
    data = response.json()
    require(data.get("status") == "ok" and data.get("knowledge") == "ok"
            and data.get("environment") == "production", "api_health")
    require(settings.task_queue == "redis", "redis_worker_required")
    from redis import Redis
    queue = RedisTaskQueue.from_settings(settings)
    # Release checks must not wait indefinitely on a dead queue endpoint.
    queue._client = Redis.from_url(settings.redis_url, socket_timeout=5, socket_connect_timeout=5)
    try:
        require(queue.ping(), "redis_health")
    finally:
        queue._client.close()
    async def bounded():
        await asyncio.wait_for(mcp_health(), 20)
    asyncio.run(bounded())
    return {"api": "PASS", "mcp": "PASS", "redis": "PASS"}


def final_preflight(base, candidate, predecessor):
    """Read-only gate called by release_switch while it owns the global lock.

    Approval pins are deployment inputs, never inferred from the artifact being
    checked. No Registry initialization, staging, account login or snapshot here.
    """
    from scripts import rollback_preflight as gate, migrate
    from scripts import release_binding_transition as transition
    root = candidate.parent / "enterprise_agent_poc"
    require(root == ROOT and root.parent.parent == base / "releases", "candidate_controlled_path")
    manifest = gate.read_json(candidate)
    pins = {
        "source_commit": os.environ.get("RELEASE_EXPECTED_SOURCE_COMMIT", ""),
        "archive_sha256": os.environ.get("RELEASE_EXPECTED_ARCHIVE_SHA256", ""),
        "raw_manifest_sha256": os.environ.get("RELEASE_EXPECTED_RAW_MANIFEST_SHA256", ""),
        "canonical_manifest_sha256": os.environ.get("RELEASE_EXPECTED_CANONICAL_MANIFEST_SHA256", ""),
    }
    require(gate.COMMIT.fullmatch(pins["source_commit"])
            and all(gate.HASH.fullmatch(v) for k, v in pins.items() if k != "source_commit"), "approval_pins_required")
    require(manifest["source_commit"] == pins["source_commit"]
            and manifest["archive_sha256"] == pins["archive_sha256"]
            and hashlib.sha256(candidate.read_bytes()).hexdigest() == pins["raw_manifest_sha256"]
            and gate.digest(manifest) == pins["canonical_manifest_sha256"], "approved_candidate_identity")
    require(candidate == root.parent / (manifest["release_id"] + ".manifest.json"), "candidate_manifest_path")
    gate.release_identity(base, manifest["release_id"], pins["source_commit"])

    compatibility = gate.read_json(root / "deploy/rollback_compatibility.json")
    exact = compatibility["forward_predecessor_approval"]
    prior_root, prior = gate.release_identity(base, exact["release_id"], exact["source_commit"], exact)
    require(predecessor == prior_root.parent / (prior["release_id"] + ".manifest.json")
            and (base / "release-current").is_symlink()
            and (base / "release-current").resolve() == prior_root, "EXACT_PREDECESSOR_MISMATCH")
    services = service_state(base)
    for role, state in services.items():
        require(state.get("active") == "active" and state.get("pid", 0) > 0
                and state.get("cwd") == str(prior_root) and state.get("module_ok")
                and state.get("exe") == str((base / "venv/bin/python").resolve()), f"preflight_{role}_runtime")

    registry = registry_for(base)
    active = bindings(registry)  # Includes manifest consistency for every agent.
    packages = transition.candidate_packages(root / "skill_packages")
    transitions = []
    if "binding_transition" in manifest:
        transitions = transition.declaration(manifest, prior, hashlib.sha256(predecessor.read_bytes()).hexdigest())
        transition.preflight(registry, transitions, packages)
    else:
        registry.verify_bootstrap()
    required_to = {key for item in transitions for key in item["to"].items()}
    reusable, stageable = [], []
    with registry._read_connection() as conn:
        for (slug, version), entry in packages.items():
            rows = conn.execute(
                "SELECT s.slug,v.id,v.skill_id,v.version,v.status,v.checksum,"
                "p.storage_path,p.sha256 AS package_sha256,p.size_bytes "
                "FROM skill_versions v JOIN skills s ON s.id=v.skill_id "
                "LEFT JOIN skill_packages p ON p.skill_version_id=v.id "
                "WHERE s.slug=? AND v.version=?", (slug, version),
            ).fetchall()
            require(len(rows) <= 1, "candidate_package_duplicate")
            if rows:
                row = rows[0]
                require(row["status"] in ({"published"} if (slug, version) in required_to
                                          else {"published", "deprecated"}), "candidate_package_status")
                registry._verify_package(row, expected=entry["artifact_sha256"])
                reusable.append({"slug": slug, "version": version})
            else:
                # Only declared transitions use the existing staging consumer.
                require(bool(transitions), "legacy_package_missing")
                stageable.append({"slug": slug, "version": version})

    schema = gate.verify(base, root, manifest["release_id"], manifest["source_commit"])
    gate.verify(base, root, prior["release_id"], prior["source_commit"])
    require(schema["applied_versions"] == [item["version"] for item in migrate.migration_items()], "pending_migrations")
    require(schema["epoch_schema_fingerprint"] == exact["schema_fingerprint"], "preflight_schema_fingerprint")
    require(exact["data_contract"] in compatibility["supported_data_contracts"]
            and exact["data_contract"] in schema["active_data_contract_floors"], "preflight_data_contract")
    smoke_config(base)  # Existing credential checker; never output its values.
    return {"status": "final_preflight_passed", "read_only": True,
            "release_id": manifest["release_id"], "source_commit": manifest["source_commit"],
            **pins, "exact_predecessor": prior["release_id"], "services": services,
            "bindings": active, "registry_exact_reusable": reusable, "registry_stageable": stageable,
            "schema": schema, "pending": 0, "migration": "NONE",
            "data_contract": exact["data_contract"], "credentials": "READY"}


def verify_state(base, candidate, predecessor, snapshot, *, rollback=False):
    from scripts.rollback_preflight import verify
    active = binding_gate(base, candidate, predecessor, snapshot, "restored" if rollback else "state")
    target = json.loads((predecessor if rollback else candidate).read_text())
    root = base / "releases" / target["release_id"] / "enterprise_agent_poc"
    require((base / "release-current").resolve() == root, "release_current")
    services = service_state(base)
    for role, state in services.items():
        require(state.get("active") == "active" and state.get("pid", 0) > 0
                and state.get("cwd") == str(root) and state.get("module_ok")
                and state.get("exe") == str((base / "venv/bin/python").resolve()), f"{role}_runtime")
    schema = verify(base, candidate.parent / "enterprise_agent_poc", target["release_id"], target["source_commit"])
    # The trusted consumer validates schema fingerprints, floors and contracts.
    # Legacy approved subsets remain supported; require no pending trusted DDL.
    from scripts import migrate
    require(schema["applied_versions"] == [item["version"] for item in migrate.migration_items()], "pending_migrations")
    registry = registry_for(base)
    registry.verify_bootstrap()
    require(bool(active.get("campaign-agent")), "campaign_skills_missing")
    for slug, version in active["campaign-agent"].items():
        require((registry.published_root / slug / version / "SKILL.md").is_file(), "skill_resolution")
    return {"status": "rollback_state_verified" if rollback else "final_state_verified",
            "release_id": target["release_id"], "services": services,
            "campaign_binding": active["campaign-agent"], "schema": schema, "health": health()}


def smoke_config(base):
    path = base / "shared/release-smoke.json"
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= 8192, "smoke_credentials_permissions")
    value = json.loads(path.read_text())
    require(isinstance(value, dict) and set(value) == {"account", "password", "tenant_id", "user_id"}
            and all(isinstance(v, str) and v for v in value.values()), "smoke_credentials_fields")
    return value


def check_event(event, value, seen):
    require(event in {"progress", "activity", "delta", "complete"}, "sse_error_or_unknown_event")
    require(isinstance(value, dict), "sse_payload")
    if event == "delta":
        require(isinstance(value.get("sequence"), int) and value["sequence"] > 0
                and isinstance(value.get("text"), str) and bool(value["text"]), "sse_delta")
    if event == "activity":
        require(isinstance(value.get("sequence"), int) and value["sequence"] > 0
                and isinstance(value.get("stage"), str)
                and value.get("status") in {"started", "completed"}, "sse_activity")
    if event == "complete":
        require({"progress", "activity", "delta"} <= seen, "sse_missing_events")
        require(value.get("status") == "completed" and bool(value.get("final_response")), "sse_complete")
    seen.add(event)


async def technical_smoke(config, *, transport=None):
    import httpx
    task_id, completed = None, False
    async with httpx.AsyncClient(base_url="http://127.0.0.1:18090", trust_env=False,
                                 follow_redirects=False, timeout=20, transport=transport) as client:
        async def request(method, path, **kwargs):
            response = await client.request(method, path, **kwargs)
            require(response.status_code in {200, 202}, "smoke_http_status")
            return response.json()
        try:
            login = await request("POST", "/api/v1/auth/login", json={"account": config["account"], "password": config["password"]})
            require(login["user"]["tenant_id"] == config["tenant_id"] and login["user"]["id"] == config["user_id"], "smoke_account_scope")
            token = client.cookies.get("workbench_session")
            require(bool(token), "login_session_missing")
            # Forward the returned Secure cookie only to this fixed loopback origin.
            client.headers["Cookie"] = f"workbench_session={token}"
            me = await request("GET", "/api/v1/me")
            require(me["user_id"] == config["user_id"] and me["tenant_id"] == config["tenant_id"], "authenticated_api")
            agent = await request("GET", "/api/v1/agents/campaign-agent")
            require(agent["id"] == "campaign-agent" and agent.get("enabled"), "campaign_agent_load")
            task = await request("POST", "/api/v1/agents/campaign-agent/runs", json={
                "message": "发布技术连通性验证：请简短回复‘连接正常’。无需检索资料或生成活动方案。"})
            task_id = task["id"]
            require(isinstance(task_id, str) and task_id and "/" not in task_id, "smoke_task_id")
            seen, terminal = set(), None
            async with client.stream("GET", f"/api/v1/tasks/{task_id}/events") as response:
                require(response.status_code == 200 and "text/event-stream" in response.headers.get("content-type", ""), "sse_response")
                event, data = "", []
                async for line in response.aiter_lines():
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        data.append(line[5:].strip())
                    elif not line and data:
                        value = json.loads("\n".join(data))
                        check_event(event, value, seen)
                        if event == "complete":
                            terminal = value
                            break
                        event, data = "", []
            require(terminal is not None, "sse_incomplete")
            task = await request("GET", f"/api/v1/tasks/{task_id}")
            require(task["status"] == "completed" and bool(task.get("final_response")), "worker_completion")
            conversation_id = task.get("conversation_id")
            require(isinstance(conversation_id, str) and conversation_id and "/" not in conversation_id, "conversation_id")
            conversation = await request("GET", f"/api/v1/conversations/{conversation_id}")
            require(conversation.get("id") == conversation_id, "conversation_readback")
            cancelled = await request("POST", f"/api/v1/tasks/{task_id}/cancel")
            require(cancelled["status"] == "completed" and cancelled.get("final_response") == task["final_response"], "cancel_terminal_idempotency")
            document_export = await activity_plan_document_export_smoke(client, request)
            completed = True
            return {"status": "technical_smoke_passed", "events": sorted(seen), "task_id": task_id,
                    "conversation_id": conversation_id, "cancel": "terminal_idempotency_PASS",
                    "activity_plan_document_export": document_export}
        finally:
            if task_id and not completed:
                try:
                    await asyncio.wait_for(client.post(f"/api/v1/tasks/{task_id}/cancel"), 10)
                except Exception:
                    pass  # Preserve the blocking failure; never log response bodies.


async def activity_plan_document_export_smoke(client, request):
    """Exercise the real full-plan result through authenticated DOCX download."""
    from app.activity_plan_runtime import validated_envelope

    task_id, passed = None, False
    try:
        task = await request("POST", "/api/v1/agents/campaign-agent/runs", json={
            "message": "发布技术验证：请为社区教师节设计完整活动方案，包含主题、时间、地点、对象、宣发、活动环节和邀约文案。时间地点未定，保留待确认；不要承诺企业权益。"
        })
        task_id = task.get("id")
        require(isinstance(task_id, str) and task_id and "/" not in task_id, "document_smoke_task_id")
        seen, terminal = set(), None
        async with client.stream("GET", f"/api/v1/tasks/{task_id}/events") as response:
            require(response.status_code == 200 and "text/event-stream" in response.headers.get("content-type", ""), "document_smoke_sse_response")
            event, data = "", []
            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].strip())
                elif not line and data:
                    value = json.loads("\n".join(data))
                    check_event(event, value, seen)
                    if event == "complete":
                        terminal = value
                        break
                    event, data = "", []
        require(terminal is not None, "document_smoke_sse_incomplete")
        saved = await request("GET", f"/api/v1/tasks/{task_id}")
        structured = saved.get("structured_result")
        require(saved.get("status") == "completed" and isinstance(structured, dict)
                and structured.get("type") == "activity_plan" and structured.get("version") == "1"
                and terminal.get("structured_result") == structured
                and terminal.get("assistant_message_id") == saved.get("assistant_message_id")
                and bool(saved.get("assistant_message_id"))
                and validated_envelope(structured, saved.get("final_response") or "") == structured,
                "document_smoke_structured_result")
        created = await client.post("/api/v1/documents/activity-plan", json={"content": structured["data"]})
        require(created.status_code == 201, "document_smoke_create_status")
        document = created.json()
        document_id = document.get("document_id")
        require(isinstance(document_id, str) and document_id and "/" not in document_id
                and isinstance(document.get("filename"), str) and document["filename"].endswith(".docx")
                and document.get("download_url") == f"/api/v1/documents/activity-plan/{document_id}",
                "document_smoke_create_identity")
        downloaded = await client.get(document["download_url"])
        require(downloaded.status_code == 200
                and downloaded.headers.get("content-type", "").split(";", 1)[0]
                == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                and downloaded.content.startswith(b"PK"), "document_smoke_download")
        passed = True
        return "PASS"
    finally:
        if task_id and not passed:
            try:
                await asyncio.wait_for(client.post(f"/api/v1/tasks/{task_id}/cancel"), 10)
            except Exception:
                pass  # Preserve the blocking failure; never log response bodies.


def evidence(base, candidate, predecessor, snapshot):
    """Best-effort reads only. Missing evidence is explicit, never guessed."""
    result = {"candidate": candidate.parent.name, "predecessor": predecessor.parent.name,
              "release_current": str((base / "release-current").resolve()), "services": service_state(base)}
    def collect(name, reader):
        try:
            result[name] = reader()
        except Exception as exc:
            result[name] = {"error_type": type(exc).__name__}
    from scripts.rollback_preflight import digest
    collect("candidate_manifest_identity", lambda: {"raw": hashlib.sha256(candidate.read_bytes()).hexdigest(),
             "canonical": digest(json.loads(candidate.read_text()))})
    def registry_rows():
        with registry_for(base)._read_connection() as conn:
            return {"bindings": [dict(r) for r in conn.execute("SELECT agent_id,skill_id,skill_version_id FROM agent_skill_bindings").fetchall()],
                    "manifests": [dict(r) for r in conn.execute("SELECT id,skill_manifest FROM agent_templates").fetchall()],
                    "packages": [dict(r) for r in conn.execute("SELECT s.slug,v.version,v.status,v.checksum,p.sha256,p.size_bytes FROM skill_versions v JOIN skills s ON s.id=v.skill_id LEFT JOIN skill_packages p ON p.skill_version_id=v.id").fetchall()]}
    collect("registry", registry_rows)
    collect("health", health)
    # Logs contain only sanitized helper output; no auth bodies or environment.
    result["gate_logs"] = {p.name: p.read_text() for p in snapshot.parent.glob("*.log")}
    path = snapshot.parent / "recovery-evidence.json"
    path.write_text(json.dumps(result, sort_keys=True, default=str))
    path.chmod(0o600)
    return {"status": "recovery_evidence_captured", "path": str(path)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("preflight", "credentials", "capture", "state", "smoke", "rollback-guard", "evidence"))
    parser.add_argument("--candidate-manifest", type=Path)
    parser.add_argument("--predecessor-manifest", type=Path)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--rollback", action="store_true")
    args = parser.parse_args(argv)
    base = ROOT.parent.parent.parent
    try:
        if args.mode == "preflight":
            result = final_preflight(base, args.candidate_manifest, args.predecessor_manifest)
        elif args.mode == "credentials":
            smoke_config(base)
            result = {"status": "smoke_credentials_ready"}
        elif args.mode == "capture":
            args.snapshot.write_text(json.dumps(bindings(registry_for(base)), sort_keys=True))
            result = {"status": "release_state_captured"}
        elif args.mode == "rollback-guard":
            binding_gate(base, args.candidate_manifest, args.predecessor_manifest, args.snapshot, args.mode)
            result = {"status": "rollback_binding_state_proven"}
        elif args.mode == "state":
            result = verify_state(base, args.candidate_manifest, args.predecessor_manifest, args.snapshot, rollback=args.rollback)
        elif args.mode == "evidence":
            result = evidence(base, args.candidate_manifest, args.predecessor_manifest, args.snapshot)
        else:
            async def bounded():
                return await asyncio.wait_for(technical_smoke(smoke_config(base)), 180)
            result = asyncio.run(bounded())
        print(json.dumps(result))
        return 0
    except Exception as exc:
        reason = str(exc) if isinstance(exc, GateFailed) else type(exc).__name__
        print(json.dumps({"status": "BLOCKED", "check": "release_" + args.mode, "reason": reason}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
