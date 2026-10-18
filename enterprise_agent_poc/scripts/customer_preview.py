"""Run the existing application as a separate, path-isolated customer Preview.

No Production DB, Release pointer, user session or administrator secret is used.
"""
from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal
import hashlib
from http.cookies import SimpleCookie
import json
import os
from pathlib import Path
import secrets
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if os.environ.get("PREVIEW_EXTRA_SITE_PACKAGES"):
    sys.path.append(os.environ["PREVIEW_EXTRA_SITE_PACKAGES"])
BASE = "/preview"
COOKIE = "workbench_preview_session"


def load_provider_values(path):
    if isinstance(path, dict): return path
    allowed = {"DEEPSEEK_API_KEY", "GATEWAY_API_TOKEN", "N1N_IMAGE_API_KEY", "EMBEDDING_API_KEY"}
    values = {}
    if path:
        for raw in Path(path).read_text(encoding="utf-8-sig").splitlines():
            key, separator, value = raw.strip().removeprefix("export ").partition("=")
            if separator and key in allowed:
                values[key] = value.strip().strip('"').strip("'")
    return values


def drop_privileges(user, data_root):
    if not user: return
    import pwd
    import ctypes
    account = pwd.getpwnam(user)
    os.initgroups(user, account.pw_gid)
    os.setgid(account.pw_gid)
    os.setuid(account.pw_uid)
    os.environ["HOME"] = str(Path(data_root).resolve())
    os.umask(0o077)
    # The SDK child receives only the loopback gate token, not the supplier
    # secret retained in its parent Python process memory.
    ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)


def prepare(root, api_port, mcp_port, keys=None, pricing=None):
    root = Path(root).resolve()
    if root.name != "customer-ai-preview-data" or root.is_symlink():
        raise ValueError("Use a distinct customer-ai-preview-data directory")
    marker = root / "customer-preview.marker"
    if root.exists() and any(root.iterdir()) and not marker.is_file():
        raise ValueError("Existing unmarked data is never reused")
    root.mkdir(parents=True, exist_ok=True)
    marker.touch(exist_ok=True)
    token_file = root / "session-secret"
    if not token_file.exists():
        token_file.write_text(secrets.token_urlsafe(48), encoding="ascii")
        token_file.chmod(0o600)
    os.environ.update({
        "CUSTOMER_PREVIEW_MODE": "true", "APP_ENV": "development",
        "ENTERPRISE_POC_BOOTSTRAP_DEMO_DATA": "false",
        "ENTERPRISE_POC_DATA_DIR": str(root),
        "ENTERPRISE_POC_DATABASE_URL": "sqlite:///" + str(root / "preview.sqlite"),
        "ENTERPRISE_POC_OBJECT_STORAGE_DIR": str(root / "files"),
        "ENTERPRISE_POC_OBJECT_STORAGE_PROVIDER": "local",
        "ENTERPRISE_POC_TOKEN_SECRET": token_file.read_text(encoding="ascii"),
        "ENTERPRISE_POC_TASK_QUEUE": "local", "REDIS_URL": "",
        "ENTERPRISE_POC_TASK_QUEUE_NAMESPACE": "customer-ai-preview",
        "ENTERPRISE_POC_MCP_URL": f"http://127.0.0.1:{mcp_port}/mcp",
        "ENTERPRISE_POC_MCP_HOST": "127.0.0.1", "ENTERPRISE_POC_MCP_PORT": str(mcp_port),
        "ENTERPRISE_POC_MODEL_BASE_URL": f"http://127.0.0.1:{api_port}/_preview_model",
        "ENTERPRISE_POC_MODEL_PROVIDER_ID": "deepseek", "ENTERPRISE_POC_MODEL_WIRE_API": "responses",
        "ENTERPRISE_POC_CODEX_API_KEY_ENV": "PREVIEW_TEXT_GATE_TOKEN",
        "PREVIEW_TEXT_GATE_TOKEN": secrets.token_urlsafe(32),
        "ENTERPRISE_POC_IMAGE_API_KEY_ENV": "PREVIEW_IMAGE_KEY",
        "EMBEDDING_PROVIDER": "local-hash", "EMBEDDING_MODEL": "local-hash-v1",
        "EMBEDDING_DIMENSION": "128", "KNOWLEDGE_ALLOW_FALLBACK": "false",
        "KNOWLEDGE_MAX_UPLOAD_BYTES": str(2 * 1024 * 1024),
        "ENTERPRISE_POC_TEST_TEXT_GATE_ENABLED": "false",
    })
    os.environ.pop("STAGE2_TWO_PROBE_AUTHORITY", None)
    if (pricing or {}).get('embedding_pricing_verified'):
        os.environ.update(EMBEDDING_PROVIDER='openai-compatible', EMBEDDING_MODEL='text-embedding-3-small',
                          EMBEDDING_DIMENSION='1536', EMBEDDING_BASE_URL='https://llm-api.net/v1',
                          EMBEDDING_API_KEY=(keys or {}).get('EMBEDDING_API_KEY',''))
    return root


def metered_clients(root, pricing):
    import httpx
    from app.preview_budget import PreviewBudget, PreviewImageTransport, PreviewSyncTransport
    original_async, original_sync = httpx.AsyncClient.__init__, httpx.Client.__init__
    def async_init(self, *args, **kwargs):
        kwargs.setdefault('transport', PreviewImageTransport(PreviewBudget(root), pricing))
        kwargs.setdefault('trust_env', False)
        original_async(self, *args, **kwargs)
    def sync_init(self, *args, **kwargs):
        kwargs.setdefault('transport', PreviewSyncTransport(PreviewBudget(root), pricing))
        kwargs.setdefault('trust_env', False)
        original_sync(self, *args, **kwargs)
    httpx.AsyncClient.__init__, httpx.Client.__init__ = async_init, sync_init


def add_preview_agent():
    from app.agent_catalog import CATALOG, AgentDefinition
    # An explicit Preview-only Legacy catalog entry, not a fake published
    # Revision or Runtime Test. Existing Skill bytes are reused unchanged.
    CATALOG["preview-wechat-agent"] = AgentDefinition(
        "preview-wechat-agent", "公众号编写智能体", "wechat-official-account-writing",
        "从选题、结构到成稿，生成可预览及复制的公众号文章。", "newspaper",
        {"wechat-official-account-writing": "1.0.1"}, 3, False,
        (ROOT / "skill_packages/wechat-official-account-writing/1.0.1/SKILL.md").read_text(encoding="utf-8"),
    )


def create_preview(root, api_port=18920, mcp_port=18921, credentials=None, pricing=None, local_http=False):
    keys, prices = load_provider_values(credentials), pricing or {}
    root = prepare(root, api_port, mcp_port, keys, prices)
    metered_clients(root, prices)
    add_preview_agent()
    from app import main
    os.environ.pop('EMBEDDING_API_KEY', None)
    from app.auth import UserPrincipal
    from app.agent_catalog import CATALOG
    from app.preview_budget import PreviewBudget, plaintext_history
    from app.test_text_gate import TestTextGate
    from starlette.requests import Request
    from starlette.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
    import httpx

    budget = PreviewBudget(root)
    identity_path=ROOT.parent/'preview-source.json'
    identity=json.loads(identity_path.read_text()) if identity_path.is_file() else {'source':'LOCAL_CANDIDATE','base_source':'8eb2fa7061de17fe355d64c03fa5451b547dae85'}
    submission_lock = asyncio.Lock()
    main.product_store.initialize()
    main.skill_registry.initialize()
    main.agent_catalog_control.initialize()
    main.skill_registry.bind_agent("preview-wechat-agent", "wechat-official-account-writing", "1.0.1", None, allow_new_binding=True)

    def new_session():
        tenant = "preview-" + uuid4().hex
        with main.store.connection() as conn:
            if conn.execute("SELECT count(*) FROM tenants").fetchone()[0] >= 30:
                raise PermissionError("临时体验访客数量已到上限，请联系Owner。")
            conn.execute("INSERT INTO tenants VALUES(?,?,?)", (tenant, "AI体验企业", secrets.token_urlsafe(32)))
            conn.execute("INSERT INTO enterprise_configs VALUES(?,?)", (tenant, json.dumps({"brand_name": "AI体验企业", "brand_logo": None, "description": "独立体验资料，请在企业设置中填写测试信息。"}, ensure_ascii=False)))
            for agent in CATALOG:
                conn.execute("INSERT INTO tenant_agent_instances(tenant_id,agent_id,status) VALUES(?,?,'enabled')", (tenant, agent))
        email = uuid4().hex + "@preview.invalid"
        main.product_store.create_user(tenant, email, main.hash_password(secrets.token_urlsafe(32)), "体验用户", "enterprise_admin")
        user = main.product_store.user_by_email(email)
        return main.sessions.issue(UserPrincipal(user["id"], tenant, user["role"], main.sessions.credential_version(user["password_hash"])), lifetime_seconds=72*3600)

    async def text_request(scope, receive, send):
        request = Request(scope, receive)
        if not secrets.compare_digest(request.headers.get("authorization", ""), "Bearer " + os.environ["PREVIEW_TEXT_GATE_TOKEN"]):
            return await JSONResponse({"error": {"message": "Preview model authorization required"}}, 403)(scope, receive, send)
        try:
            if not keys.get("DEEPSEEK_API_KEY") or not prices.get("text_pricing_verified"):
                raise PermissionError("Preview文本供应商配置或费率尚未核实。")
            body, dropped = plaintext_history(await request.json())
            body, namespaces = TestTextGate.provider_tools(body)
            with main.store.connection() as conn:
                running_task = conn.execute("SELECT id,agent_id FROM tasks WHERE status='running' ORDER BY created_at DESC LIMIT 1").fetchone()
            # An anonymous Preview receives only the existing tenant-scoped
            # business MCP tools, never shell/file/browser/server-side tools.
            body['tools'] = [tool for tool in body.get('tools', [])
                             if tool.get('type') == 'function'
                             and tool.get('name', '').startswith('mcp__platform__')
                             and (tool.get('name') != 'mcp__platform__image_generation'
                                  or running_task and running_task['agent_id']=='image-agent')]
            if not body['tools']:
                raise PermissionError('未取得合法业务工具定义；未调用模型。')
            body['instructions'] = (body.get('instructions') or '') + '\nPreview执行要求：读取上下文中真实工具返回；同一轮已取得企业配置或充分的同一事实检索结果后不要重复查询。信息足够时直接完成用户要求。工具返回报错时如实说明，不伪造结果，不改用其他执行通道。'
            wire = json.dumps(body, ensure_ascii=False).encode()
            if len(wire) > 65536:
                raise PermissionError("本轮模型上下文超过体验费用边界，请缩短会话。")
            if body.get("model") != main.settings.model_id:
                raise PermissionError("模型身份不匹配。")
            output_cap = 8192 if running_task and running_task['agent_id']=='campaign-agent' else 4096
            body["max_output_tokens"] = min(int(body.get("max_output_tokens") or output_cap), output_cap)
            bounds = TestTextGate.request_bound(body)
            if prices.get('text_dispatch_paused'):
                raise PermissionError('Preview付费请求已暂停；未调用供应商。')
            cost = (Decimal(bounds['input_tokens_upper']) * Decimal(str(prices["text_input_cny_per_million"]))
                    + Decimal(body["max_output_tokens"]) * Decimal(str(prices["text_output_cny_per_million"]))) / Decimal(1_000_000)
            budget.reserve("text", cost)
            with main.store.connection() as conn:
                task = conn.execute("SELECT id FROM tasks WHERE status='running' ORDER BY created_at DESC LIMIT 1").fetchone()
            budget.observe({"event": "text_dispatch", "task_id": task["id"] if task else None,
                            "input_types": [item.get("type", "message") for item in body.get("input", []) if isinstance(item, dict)],
                            "tool_names": [tool['name'] for tool in body['tools']],
                            "opaque_reasoning_items_omitted": dropped})
        except (PermissionError, ValueError, KeyError) as exc:
            budget.observe({'event':'text_denied','reason':getattr(exc,'branch_id',type(exc).__name__),
                            'structure':getattr(exc,'safe_structure',None)})
            return await JSONResponse({"error": {"message": str(exc)}}, 429)(scope, receive, send)
        client = httpx.AsyncClient(timeout=180, trust_env=False, transport=httpx.AsyncHTTPTransport(retries=0))
        upstream = await client.send(client.build_request("POST", prices["text_base_url"].rstrip("/") + "/responses",
            headers={"Authorization": "Bearer " + keys["DEEPSEEK_API_KEY"]}, json=body), stream=True)
        if upstream.status_code >= 400:
            await upstream.aclose(); await client.aclose()
            return await JSONResponse({"error": {"message": "Preview供应商请求未成功；不会自动重试。"}}, upstream.status_code)(scope, receive, send)
        async def chunks():
            try:
                if upstream.headers.get('content-type','').startswith('text/event-stream'):
                    pending = b''
                    async for chunk in upstream.aiter_bytes():
                        pending += chunk
                        pending = pending.replace(b'\r\n', b'\n')
                        while b'\n\n' in pending:
                            block, pending = pending.split(b'\n\n', 1)
                            yield TestTextGate.sdk_tools('text/event-stream', block + b'\n\n', namespaces)
                    if pending: yield TestTextGate.sdk_tools('text/event-stream', pending, namespaces)
                else:
                    yield TestTextGate.sdk_tools(upstream.headers.get('content-type',''), await upstream.aread(), namespaces)
            finally:
                await upstream.aclose(); await client.aclose()
        return await StreamingResponse(chunks(), media_type=upstream.headers.get("content-type", "application/json"))(scope, receive, send)

    class Preview:
        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                return await main.app(scope, receive, send)
            path = scope["path"]
            # This route is private to the backend Codex client, never advertised
            # in the browser and requires a distinct transient bearer token.
            if path == "/_preview_model/responses" and scope["method"] == "POST":
                return await text_request(scope, receive, send)
            if path == BASE: return await Response(status_code=307, headers={"location": BASE + "/"})(scope, receive, send)
            # Nginx must preserve /preview/; unprefixed APIs fail closed.
            if not path.startswith(BASE + "/"):
                return await JSONResponse({"user_message": "Preview路径不匹配"}, 404)(scope, receive, send)
            scope = dict(scope, path=path[len(BASE):], raw_path=scope.get("raw_path", b"")[len(BASE):])
            path = scope["path"]
            if path in {"/api/health", "/api/v1/preview-status"}:
                return await JSONResponse({"status": "preview", **identity, "budget": budget.summary(), "knowledge": main.settings.embedding_provider, "production_db_used": False})(scope, receive, send)
            denied = ("/api/v1/platform", "/api/admin", "/api/v1/auth", "/api/v1/members", "/api/v1/profile", "/mcp", "/docs", "/openapi")
            if path.startswith(denied):
                return await JSONResponse({"user_message": "体验版不开放平台管理或账号接口"}, 403)(scope, receive, send)
            if path.startswith("/api/") and not path.startswith(("/api/v1/me", "/api/v1/workspace", "/api/v1/agents", "/api/v1/tasks", "/api/v1/conversations", "/api/v1/generations", "/api/v1/enterprise-config", "/api/v1/knowledge", "/api/v1/assets", "/api/v1/storage", "/api/v1/chat-images", "/api/v1/documents")):
                return await JSONResponse({"user_message": "体验版未开放此接口"}, 403)(scope, receive, send)
            if path.startswith("/api/v1/agents/") and path.endswith("/runs") and scope["method"] == "POST":
                if not keys.get("DEEPSEEK_API_KEY") or not prices.get("text_pricing_verified"):
                    return await JSONResponse({"user_message": "Preview模型凭据或费率尚未就绪，未发起付费任务。"}, 503)(scope, receive, send)
                quota=budget.summary()['text']
                if prices.get('text_dispatch_paused') or quota['requests']>=quota['max_requests'] or Decimal(quota['reserved_ceiling_cny'])>=Decimal(quota['max_cny']):
                    return await JSONResponse({'user_message':'本次体验模型调用额度已用完或暂停，请Owner补充授权；未创建新任务。'},429)(scope,receive,send)
            if path.startswith("/static/"):
                return await main.app(scope, receive, send)
            headers = [(k, v) for k, v in scope["headers"] if k.lower() != b"cookie"]
            cookie = SimpleCookie()
            try: cookie.load(dict(scope["headers"]).get(b"cookie", b"").decode())
            except Exception: pass
            token = cookie[COOKIE].value if COOKIE in cookie else ""
            try:
                principal = main.sessions.verify(token)
                if not principal.tenant_id.startswith("preview-"): raise PermissionError()
                if not main.product_store.user_by_id(principal.user_id, principal.tenant_id): raise PermissionError()
            except Exception:
                try: token = new_session()
                except PermissionError as exc: return await JSONResponse({"user_message": str(exc)}, 429)(scope, receive, send)
            headers.append((b"cookie", ("workbench_session=" + token).encode()))
            scope["headers"] = headers
            async def with_cookie(message):
                if message["type"] == "http.response.start":
                    message.setdefault("headers", []).append((b"set-cookie", (COOKIE + "=" + token + "; Path=/preview/; HttpOnly; SameSite=Lax; Max-Age=259200" + ("" if local_http else "; Secure")).encode()))
                    message["headers"].append((b"cache-control", b"no-store"))
                await send(message)
            if not path.startswith("/api/"):
                if path == "/":
                    return await Response(status_code=307, headers={"location": BASE + "/agents/copywriting"})(scope, receive, with_cookie)
                allowed = {"/knowledge", "/enterprise-config", "/conversations", "/generations", "/workspace"}
                if path not in allowed and not path.startswith("/agents/"):
                    return await Response(status_code=404)(scope, receive, with_cookie)
                html = (main.STATIC_DIR / "index.html").read_text(encoding="utf-8")
                for name, marker in (("workbench.js", "__JS_SHA256_V1__"), ("workbench.css", "__CSS_SHA256_V1_")):
                    html = html.replace(marker, hashlib.sha256((main.STATIC_DIR / name).read_bytes()).hexdigest()[:16])
                html = html.replace('src="/static/', 'src="/preview/static/').replace('href="/static/', 'href="/preview/static/')
                html = html.replace("<head>", '<head><script src="/preview/static/customer-preview.js"></script><link rel="stylesheet" href="/preview/static/customer-preview.css">')
                return await HTMLResponse(html)(scope, receive, with_cookie)
            if path.startswith('/api/v1/agents/') and path.endswith('/runs') and scope['method']=='POST':
                async with submission_lock:
                    with main.store.connection() as conn:
                        busy = conn.execute("SELECT count(*) FROM tasks WHERE status IN ('queued','running')").fetchone()[0]
                    if busy: return await JSONResponse({"user_message": "体验服务当前正在生成，请稍后再提交。"}, 429)(scope, receive, with_cookie)
                    return await main.app(scope, receive, with_cookie)
            return await main.app(scope, receive, with_cookie)
    return Preview()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--port", type=int, default=18920)
    parser.add_argument("--mcp-port", type=int, default=18921)
    parser.add_argument("--credentials-env")
    parser.add_argument("--pricing-json")
    parser.add_argument("--local-http", action="store_true")
    parser.add_argument("--mcp", action="store_true")
    parser.add_argument("--run-as")
    args = parser.parse_args()
    pricing = json.loads(Path(args.pricing_json).read_text(encoding="utf-8")) if args.pricing_json else {}
    keys = load_provider_values(args.credentials_env)
    drop_privileges(args.run_as, args.data_dir)
    if args.mcp:
        root = prepare(args.data_dir, args.port, args.mcp_port, keys, pricing)
        os.environ["PREVIEW_IMAGE_KEY"] = keys.get("N1N_IMAGE_API_KEY") or keys.get("GATEWAY_API_TOKEN", "")
        metered_clients(root, pricing)
        from app.platform_mcp.server import create_mcp
        os.environ.pop('EMBEDDING_API_KEY', None)
        create_mcp().run(transport="streamable-http")
    else:
        import uvicorn
        uvicorn.run(create_preview(args.data_dir, args.port, args.mcp_port, keys, pricing, args.local_http), host="127.0.0.1", port=args.port, access_log=False)
