import uuid

from fastapi.testclient import TestClient

from app.main import app, product_store, store


def _login(client, account):
    response = client.post("/api/v1/auth/login", json={"account": account, "password": "ChangeMe!2026"})
    assert response.status_code == 200


def test_recent_tasks_is_tenant_scoped_filterable_and_safe_for_enterprise_admin():
    with TestClient(app) as client:
        member = product_store.user_by_email("member@tenant-a.test")
        task = product_store.create_task("tenant-a", member["id"], "copywriting-agent", "为秋季班写一段介绍", None)
        product_store.set_task(task["id"], "tenant-a", "completed", "completed", "任务完成", response="完成的文案")
        failed = product_store.create_task("tenant-a", member["id"], "campaign-agent", "安排开放日", None)
        product_store.set_task(failed["id"], "tenant-a", "failed", "failed", "内部异常", error_code="mcp_error")
        other = product_store.user_by_email("admin@tenant-b.test")
        hidden = product_store.create_task("tenant-b", other["id"], "copywriting-agent", "不应跨租户出现", None)
        product_store.set_task(hidden["id"], "tenant-b", "completed", "completed", "任务完成", response="tenant-b")
        with product_store._store.connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO credit_transactions(id,tenant_id,user_id,task_id,amount,reason) VALUES (?,?,?,?,?,?)",
                (f"p1:{task['id']}", "tenant-a", member["id"], task["id"], -3, "test"),
            )
        _login(client, "member@tenant-a.test")
        assert client.get("/api/v1/admin/recent-tasks").status_code == 403
        client.post("/api/v1/auth/logout")
        _login(client, "admin@tenant-a.test")
        response = client.get("/api/v1/admin/recent-tasks?days=7&status=all")
        assert response.status_code == 200
        payload = response.json()
        rows = {item["id"]: item for item in payload["tasks"]}
        assert task["id"] in rows and failed["id"] in rows and hidden["id"] not in rows
        assert rows[task["id"]]["member_name"] == "Tenant A 成员"
        assert rows[task["id"]]["credit_used"] == 3
        assert rows[failed["id"]]["error_code"] == "SERVICE_TEMPORARILY_UNAVAILABLE"
        assert rows[failed["id"]]["user_message"] == "服务暂时不可用，请稍后重试。"
        assert rows[failed["id"]]["diagnostic_id"]
        completed = client.get("/api/v1/admin/recent-tasks?days=7&status=completed&agent_id=copywriting-agent").json()
        assert any(item["id"] == task["id"] for item in completed["tasks"])
        assert all(item["status"] == "completed" and item["agent_id"] == "copywriting-agent" for item in completed["tasks"])


def test_conversation_references_only_render_accepted_tenant_knowledge_evidence():
    with TestClient(app) as client:
        user = product_store.user_by_email("member@tenant-a.test")
        conversation_id, task_id, file_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        store.save_conversation(conversation_id, "tenant-a", "copywriting-agent", "profile", "p1-thread", "P1 references")
        product_store.attach_conversation(conversation_id, user["id"], "P1 references")
        with product_store._store.connection() as conn:
            conn.execute(
                "INSERT INTO tasks(id,tenant_id,user_id,agent_id,conversation_id,input_text,status,stage,run_id) VALUES (?,?,?,?,?,?,?,?,?)",
                (task_id, "tenant-a", user["id"], "copywriting-agent", conversation_id, "写招生文案", "completed", "completed", run_id),
            )
            conn.execute("INSERT INTO knowledge_bases(id,tenant_id,name) VALUES (?,?,?)", (f"base-{file_id}", "tenant-a", "P1 test"))
            conn.execute(
                "INSERT INTO knowledge_files(id,tenant_id,knowledge_base_id,name,status,storage_key) VALUES (?,?,?,?,?,?)",
                (file_id, "tenant-a", f"base-{file_id}", "品牌规范.pdf", "ready", f"knowledge/tenant-a/{file_id}.pdf"),
            )
        store.create_run_trace(
            run_id,
            conversation_id,
            "tenant-a",
            "copywriting-agent",
            "p1-thread",
            {"knowledge_retrievals": [{"results": [{"file_id": file_id, "accepted": True}, {"file_id": "rejected", "accepted": False}]}], "asset_calls": [{"output_summary": "unstructured asset summary"}]},
        )
        product_store.add_message(conversation_id, "user", "写招生文案", message_id=f"task:{task_id}:user")
        product_store.add_message(conversation_id, "assistant", "这是已完成的文案", message_id=f"task:{task_id}:assistant")
        _login(client, "member@tenant-a.test")
        response = client.get(f"/api/v1/conversations/{conversation_id}")
        assert response.status_code == 200
        assistant = next(item for item in response.json()["messages"] if item["role"] == "assistant")
        assert assistant["references"] == {"knowledge": [{"id": file_id, "name": "品牌规范.pdf"}]}
        assert "assets" not in assistant["references"]
