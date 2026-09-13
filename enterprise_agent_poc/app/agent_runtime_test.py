"""Non-production, server-controlled Runtime Test using the unified TaskService."""
import json
from uuid import uuid4

from app.agent_productization import AgentCatalogError, canonical
from app.runtime.codex_provider import CodexRuntimeProvider


class AgentRuntimeTest:
    def __init__(self, resolver, product, tasks):
        self.resolver, self.product, self.tasks = resolver, product, tasks
        self.isolation_guard = None

    async def run(self, template_id, version_id, actor):
        r = self.resolver
        if not self.isolation_guard:
            raise AgentCatalogError("Runtime Test isolation hard gate not configured",409)
        self.isolation_guard()
        if r.settings.environment == "production" or not r.test_tenant_id:
            raise AgentCatalogError("Explicit isolated Runtime Test tenant required",409)
        if not isinstance(self.tasks._agents._runtime, CodexRuntimeProvider):
            raise AgentCatalogError("Runtime Test requires real CodexRuntimeProvider",409)
        with r.store.connection() as conn:
            if not r.store.is_postgres:
                conn.execute("BEGIN IMMEDIATE")
            r.catalog._productized(r.catalog._template(conn, template_id, lock=True))
            version = r.catalog._version(conn, template_id, version_id, draft=True)
            errors = r.catalog._validation_errors(conn, version)
            if errors:
                raise AgentCatalogError("; ".join(errors),409)
            user = conn.execute("SELECT id FROM users WHERE tenant_id=? ORDER BY id LIMIT 1", (r.test_tenant_id,)).fetchone()
            if not user:
                raise AgentCatalogError("Controlled test tenant/user must be provisioned separately",409)
            conn.execute("INSERT INTO tenant_agent_instances(tenant_id,agent_id,status,instance_id,agent_template_version_id,overrides_json) VALUES (?,?,'configured',?,?, '{}') ON CONFLICT(tenant_id,agent_id) DO UPDATE SET status='configured',agent_template_version_id=excluded.agent_template_version_id,overrides_json='{}'", (r.test_tenant_id,template_id,str(uuid4()),version_id))
            fingerprint = version["configuration_fingerprint"]
        test_id = str(uuid4())
        task = self.product.create_task(r.test_tenant_id,user["id"],template_id,
            "执行一个最小验证：读取绑定 Skill 的 SKILL.md，按 Persona 用一句中文介绍能力。调用所有 required MCP 工具；optional 工具若存在，请调用 enterprise_config_get 获取合成品牌名称。只给最终结果，不生成图片，不访问外部资源。",
            None,_test_revision=version_id,_test_id=test_id)
        await self.tasks.execute(task)
        saved = self.product.task_for_worker(task["id"])
        trace = r.store.run_trace(saved["run_id"],r.test_tenant_id) if saved.get("run_id") else None
        with r.store.connection() as conn:
            context = r.task_context(conn,task)
        manifest = json.loads(context["skill_manifest_snapshot"])
        events = trace["payload"].get("lifecycle_events",[]) if trace else []
        discovered = {e.get("skill") for e in events if e.get("event") == "skill_discovered"}
        skill_passed = set(manifest) <= discovered
        passed = saved["status"] == "completed" and trace and trace["status"] == "completed" and trace["payload"].get("final_response_persisted") is True and skill_passed
        result = {"version_id":version_id,"configuration_fingerprint":fingerprint,"task_id":task["id"],"status":"passed" if passed else "failed", "execution_chain":"codex-runtime-persisted" if passed else "codex-runtime-failed", "run_id":saved.get("run_id"),"error_code":saved.get("error_code")}
        result.update(skill_discovery_passed=skill_passed,instance_id=context["instance_id"],context_id=context["id"],runtime_profile_id=context["runtime_profile_id"],conversation_id=saved.get("conversation_id"),skill_manifest=manifest,tool_policy=json.loads(context["tool_policy_snapshot"])["bindings"])
        with r.store.connection() as conn:
            current = r.catalog._version(conn,template_id,version_id)
            status = result["status"] if current["configuration_fingerprint"] == fingerprint else "invalidated"
            result["status"] = status
            conn.execute("UPDATE agent_template_tests SET status=?,result_json=? WHERE id=?", (status,canonical(result),test_id))
        return result
