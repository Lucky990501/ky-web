from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from uuid import uuid4
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Cookie, FastAPI, File, Form, Header, UploadFile, HTTPException, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.auth import AuthenticationError, SessionIssuer, UserPrincipal, hash_password, verify_password
from app.product_service import TaskService
from app.product_store import ProductStore
from app.knowledge import KnowledgeProcessingService, KnowledgeRetrievalService, runtime_diagnostic
from app.storage import storage_provider
from app.runtime.codex_provider import CodexRuntimeManager, CodexRuntimeProvider
from app.security import RuntimeTokenIssuer
from app.service import AgentService
from app.settings import settings
from app.skills import SkillDeployment
from app.store import POCStore


class RunRequest(BaseModel):
    agent_id: str = Field(min_length=1)
    message: str = Field(min_length=1, max_length=4_000)
    conversation_id: str | None = None


class LoginRequest(BaseModel):
    account: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=256)


class AgentTaskRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4_000)
    conversation_id: str | None = None
class RenameRequest(BaseModel): title: str = Field(min_length=1,max_length=80)
class EnterpriseConfigRequest(BaseModel): payload: dict
class KnowledgeTextRequest(BaseModel): name: str = Field(min_length=1,max_length=180); content: str = Field(min_length=1,max_length=100_000)
class KnowledgeQueryRequest(BaseModel): query: str = Field(min_length=1, max_length=2_000); top_k: int = Field(default=5, ge=1, le=10)
class AssetRequest(BaseModel): name: str = Field(min_length=1,max_length=120); asset_type: str; url: str = Field(min_length=1,max_length=2_000); tags: list[str]=[]; description: str=""
class SaveGenerationRequest(BaseModel): name: str = Field(min_length=1,max_length=120)
class RuntimeTestRequest(BaseModel): mode: str = Field(pattern="^(ok|enterprise_config)$")
class ProfileUpdateRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)
    email: str = Field(min_length=3, max_length=254)
    avatar_data_url: str | None = Field(default=None, max_length=3_000_000)


store = POCStore(settings.database_url)
token_issuer = RuntimeTokenIssuer(settings.token_secret)
manager = CodexRuntimeManager(settings, SkillDeployment(Path(__file__).resolve().parents[1] / "skill_packages"), token_issuer)
runtime = CodexRuntimeProvider(manager)
agents = AgentService(store, runtime, settings)
product_store = ProductStore(store)
task_service = TaskService(product_store, agents)
knowledge_processing = KnowledgeProcessingService(product_store, settings)
knowledge_retrieval = KnowledgeRetrievalService(product_store, settings)
sessions = SessionIssuer(settings.token_secret)
STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    product_store.initialize()
    if settings.bootstrap_demo_data:
        store.seed_demo_data()
        product_store.initialize()
        # Development-only synthetic accounts. Production provisioning is external.
        product_store.create_user("tenant-a", "admin@tenant-a.test", hash_password("ChangeMe!2026"), "Tenant A 管理员", "enterprise_admin")
        product_store.create_user("tenant-a", "member@tenant-a.test", hash_password("ChangeMe!2026"), "Tenant A 成员", "member")
        product_store.create_user("tenant-b", "admin@tenant-b.test", hash_password("ChangeMe!2026"), "Tenant B 管理员", "enterprise_admin")
    if settings.task_queue == "local":
        for task in product_store.recoverable_tasks():
            asyncio.create_task(task_service.execute(task))
    yield
    await runtime.close()


app = FastAPI(title="Enterprise AI Agent Runtime POC", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def current_user(workbench_session: str | None = Cookie(default=None)) -> UserPrincipal:
    if not workbench_session:
        raise HTTPException(401, "请先登录。")
    try:
        return sessions.verify(workbench_session)
    except AuthenticationError as exc:
        raise HTTPException(401, "登录已失效，请重新登录。") from exc

def require_admin(workbench_session: str | None) -> UserPrincipal:
    principal=current_user(workbench_session)
    if principal.role != "enterprise_admin": raise HTTPException(403,"仅企业管理员可操作。")
    return principal


def profile_response(user: dict) -> dict:
    return {
        "user_id": user["id"],
        "tenant_id": user["tenant_id"],
        "tenant_name": user["tenant_name"],
        "role": user["role"],
        "display_name": user["display_name"],
        "email": user["email"],
        "avatar_url": f"/api/v1/me/avatar?v={uuid4().hex}" if user.get("avatar_storage_key") else None,
    }


def decode_avatar(data_url: str) -> tuple[bytes, str, str]:
    try:
        header, encoded = data_url.split(",", 1)
        mime_type = header.removeprefix("data:").removesuffix(";base64")
        extensions = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
        if mime_type not in extensions or not header.endswith(";base64"):
            raise ValueError
        content = base64.b64decode(encoded, validate=True)
        if not content or len(content) > 2 * 1024 * 1024:
            raise ValueError
        return content, mime_type, extensions[mime_type]
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, "头像仅支持不超过 2MB 的 PNG、JPG 或 WebP 图片。") from exc

def _key_fingerprint() -> str | None:
    key=os.environ.get(settings.codex_api_key_env)
    return hashlib.sha256(key.encode()).hexdigest()[:12] if key else None


def admin_user(principal: UserPrincipal = Cookie(default=None)):  # pragma: no cover - route helper replaced below
    return principal


def tenant_from_key(poc_api_key: str | None) -> str:
    if not poc_api_key:
        raise HTTPException(401, "缺少 POC API Key。")
    tenant_id = store.tenant_for_api_key(poc_api_key)
    if not tenant_id:
        raise HTTPException(401, "POC API Key 无效。")
    return tenant_id


@app.get("/api/v1/poc/health")
async def health() -> dict:
    return {"status": "ok", "runtime": "openai-codex==0.147.0", "scope": "runtime-poc"}


@app.get("/api/health")
async def production_health() -> dict:
    """Stable unauthenticated health endpoint for the production proxy."""
    diagnostic = runtime_diagnostic(product_store, settings)
    return {"status": "degraded" if diagnostic["strict"] and diagnostic["status"] != "ok" else "ok", "runtime": "openai-codex==0.147.0", "environment": settings.environment, "knowledge": diagnostic["status"]}


@app.get("/", include_in_schema=False)
@app.get("/login", include_in_schema=False)
@app.get("/workspace", include_in_schema=False)
@app.get("/agents/image", include_in_schema=False)
@app.get("/conversations", include_in_schema=False)
@app.get("/generations", include_in_schema=False)
@app.get("/enterprise-config", include_in_schema=False)
@app.get("/knowledge", include_in_schema=False)
@app.get("/assets", include_in_schema=False)
async def product_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/v1/auth/login")
async def login(payload: LoginRequest, response: Response) -> dict:
    user = product_store.user_by_email(payload.account)
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(401, "账号或密码不正确。")
    token = sessions.issue(UserPrincipal(user["id"], user["tenant_id"], user["role"]))
    response.set_cookie("workbench_session", token, httponly=True, samesite="lax", secure=settings.secure_cookies, max_age=12 * 60 * 60, path="/")
    return {"user": {"id": user["id"], "display_name": user["display_name"], "tenant_id": user["tenant_id"], "role": user["role"]}}


@app.post("/api/v1/auth/logout")
async def logout(response: Response) -> dict:
    response.delete_cookie("workbench_session", path="/")
    return {"status": "ok"}


@app.get("/api/v1/me")
async def me(workbench_session: str | None = Cookie(default=None)) -> dict:
    principal = current_user(workbench_session)
    user = product_store.user_by_id(principal.user_id, principal.tenant_id)
    if not user:
        raise HTTPException(401, "当前用户不存在。")
    return profile_response(user)


@app.put("/api/v1/me")
async def update_me(payload: ProfileUpdateRequest, workbench_session: str | None = Cookie(default=None)) -> dict:
    principal = current_user(workbench_session)
    display_name, email = payload.display_name.strip(), payload.email.strip().lower()
    if not display_name or "@" not in email:
        raise HTTPException(422, "请输入有效的姓名和邮箱。")
    existing = product_store.user_by_email(email)
    if existing and existing["id"] != principal.user_id:
        raise HTTPException(409, "该邮箱已被其他账号使用。")
    current = product_store.user_by_id(principal.user_id, principal.tenant_id)
    if not current:
        raise HTTPException(401, "当前用户不存在。")
    avatar_key = avatar_mime = None
    if payload.avatar_data_url:
        content, avatar_mime, extension = decode_avatar(payload.avatar_data_url)
        avatar_key = f"profiles/{principal.tenant_id}/{principal.user_id}/avatar-{uuid4().hex}.{extension}"
        storage_provider(settings).put(avatar_key, content, avatar_mime)
    user = product_store.update_user_profile(principal.user_id, principal.tenant_id, display_name, email, avatar_key, avatar_mime)
    if not user:
        raise HTTPException(404, "用户不存在。")
    if avatar_key and current.get("avatar_storage_key") and current["avatar_storage_key"] != avatar_key:
        try:
            storage_provider(settings).delete(current["avatar_storage_key"])
        except FileNotFoundError:
            pass
    return profile_response(user)


@app.get("/api/v1/me/avatar")
async def my_avatar(workbench_session: str | None = Cookie(default=None)):
    principal = current_user(workbench_session)
    user = product_store.user_by_id(principal.user_id, principal.tenant_id)
    if not user or not user.get("avatar_storage_key"):
        raise HTTPException(404, "头像不存在。")
    try:
        return Response(storage_provider(settings).get(user["avatar_storage_key"]), media_type=user.get("avatar_mime_type") or "image/png")
    except FileNotFoundError as exc:
        raise HTTPException(404, "头像文件不存在。") from exc


@app.get("/api/v1/workspace")
async def workspace(workbench_session: str | None = Cookie(default=None)) -> dict:
    principal = current_user(workbench_session)
    return product_store.workspace(principal.tenant_id, principal.user_id)


@app.get("/api/v1/agents")
async def list_agents(workbench_session: str | None = Cookie(default=None)) -> list[dict]:
    """Return only templates enabled for the signed-in tenant."""
    principal = current_user(workbench_session)
    return product_store.agents(principal.tenant_id)


@app.post("/api/v1/agents/{agent_id}/runs", status_code=202)
async def create_agent_task(agent_id: str, payload: AgentTaskRequest, workbench_session: str | None = Cookie(default=None)) -> dict:
    principal = current_user(workbench_session)
    if not product_store.agent_enabled(principal.tenant_id, agent_id):
        raise HTTPException(404, "该智能体尚未为当前企业启用。")
    try:
        task = product_store.create_task(principal.tenant_id, principal.user_id, agent_id, payload.message, payload.conversation_id)
    except ValueError as exc:
        if str(exc) == "insufficient_credit":
            raise HTTPException(402, "积分不足，无法提交任务。") from exc
        raise
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    if settings.task_queue == "redis":
        from app.task_queue import RedisTaskQueue
        RedisTaskQueue.from_settings(settings).enqueue(task["id"])
    else:
        asyncio.create_task(task_service.execute(task))
    return task


@app.get("/api/v1/tasks/{task_id}")
async def get_task(task_id: str, workbench_session: str | None = Cookie(default=None)) -> dict:
    principal = current_user(workbench_session)
    task = product_store.task(task_id, principal.tenant_id, principal.user_id)
    if not task:
        raise HTTPException(404, "任务不存在。")
    return task


@app.get("/api/v1/tasks/{task_id}/events")
async def stream_task_events(task_id: str, after: int = 0, workbench_session: str | None = Cookie(default=None)):
    """SSE of persisted, user-visible task phases. No reasoning is streamed."""
    principal = current_user(workbench_session)
    async def events():
        last_id = after
        for _ in range(180):
            for item in product_store.task_events_since(task_id, principal.tenant_id, principal.user_id, last_id):
                last_id = item["id"]
                yield f"event: progress\ndata: {json.dumps(item, ensure_ascii=False)}\n\n"
            task = product_store.task(task_id, principal.tenant_id, principal.user_id)
            if not task:
                yield "event: error\ndata: {\"message\":\"任务不存在。\"}\n\n"
                return
            if task["status"] in {"completed", "failed", "cancelled"}:
                yield f"event: complete\ndata: {json.dumps({'status': task['status'], 'message': task.get('user_message'), 'final_response': task.get('final_response'), 'conversation_id': task.get('conversation_id')}, ensure_ascii=False)}\n\n"
                return
            await asyncio.sleep(0.7)
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/v1/conversations")
async def list_conversations(workbench_session: str | None = Cookie(default=None)) -> list[dict]:
    principal = current_user(workbench_session)
    return product_store.conversations(principal.tenant_id, principal.user_id)

@app.get("/api/v1/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, workbench_session: str | None = Cookie(default=None)) -> dict:
    principal = current_user(workbench_session)
    detail = product_store.conversation_detail(principal.tenant_id, principal.user_id, conversation_id)
    if not detail:
        raise HTTPException(404, "会话不存在。")
    return detail


@app.get("/api/v1/generations")
async def list_generations(workbench_session: str | None = Cookie(default=None)) -> list[dict]:
    principal = current_user(workbench_session)
    return product_store.generations(principal.tenant_id, principal.user_id)

@app.delete("/api/v1/generations/{generation_id}")
async def delete_generation(generation_id: str, workbench_session: str | None = Cookie(default=None)) -> dict:
    principal=current_user(workbench_session); item=product_store.delete_generation(principal.tenant_id,principal.user_id,generation_id)
    if not item: raise HTTPException(404,"生成记录不存在。")
    return {"status":"deleted"}

@app.post("/api/v1/generations/{generation_id}/save-to-assets")
async def save_to_assets(generation_id: str,payload: SaveGenerationRequest,workbench_session: str | None = Cookie(default=None)) -> dict:
    principal=current_user(workbench_session); item=product_store.save_generation_as_asset(principal.tenant_id,principal.user_id,generation_id,payload.name)
    if not item: raise HTTPException(404,"生成记录不存在。")
    return item

@app.patch("/api/v1/conversations/{conversation_id}")
async def rename_conversation(conversation_id: str,payload: RenameRequest,workbench_session: str | None = Cookie(default=None)) -> dict:
    principal=current_user(workbench_session)
    if not product_store.rename_conversation(principal.tenant_id,principal.user_id,conversation_id,payload.title): raise HTTPException(404,"会话不存在。")
    return {"status":"ok"}

@app.delete("/api/v1/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str,workbench_session: str | None = Cookie(default=None)) -> dict:
    principal=current_user(workbench_session)
    if not product_store.delete_conversation(principal.tenant_id,principal.user_id,conversation_id): raise HTTPException(404,"会话不存在。")
    return {"status":"deleted"}

@app.get("/api/v1/enterprise-config")
async def get_enterprise_config(workbench_session: str | None = Cookie(default=None)) -> dict:
    principal=require_admin(workbench_session); return store.enterprise_config(principal.tenant_id)
@app.put("/api/v1/enterprise-config")
async def put_enterprise_config(payload: EnterpriseConfigRequest,workbench_session: str | None = Cookie(default=None)) -> dict:
    principal=require_admin(workbench_session); return product_store.update_enterprise_config(principal.tenant_id,payload.payload)
@app.get("/api/v1/knowledge/files")
async def list_knowledge_files(workbench_session: str | None = Cookie(default=None)) -> list[dict]:
    principal=require_admin(workbench_session); return product_store.knowledge_files(principal.tenant_id)
@app.get("/api/v1/knowledge/diagnostics")
async def knowledge_diagnostics(workbench_session: str | None = Cookie(default=None)) -> dict:
    require_admin(workbench_session)
    return runtime_diagnostic(product_store, settings)
@app.post("/api/v1/knowledge/files", status_code=202)
async def upload_knowledge_file(file: UploadFile = File(...), knowledge_base_id: str | None = Form(default=None), workbench_session: str | None = Cookie(default=None)) -> dict:
    principal = require_admin(workbench_session)
    filename = Path(file.filename or "").name
    suffix = Path(filename).suffix.lower()
    if suffix not in {".pdf", ".docx", ".txt", ".md"}:
        raise HTTPException(415, "仅支持 PDF、DOCX、TXT、MD 文件。")
    content = await file.read(settings.knowledge_max_upload_bytes + 1)
    if not content:
        raise HTTPException(422, "不能上传空文件。")
    if len(content) > settings.knowledge_max_upload_bytes:
        raise HTTPException(413, "文件超过允许大小。")
    file_id = str(uuid4())
    storage_key = f"knowledge/{principal.tenant_id}/{file_id}{suffix}"
    storage_provider(settings).put(storage_key, content, file.content_type or "application/octet-stream")
    try:
        record = product_store.create_knowledge_file(principal.tenant_id, principal.user_id, filename, file.content_type or "application/octet-stream", len(content), storage_key, knowledge_base_id)
        product_store.set_knowledge_file_status(principal.tenant_id, record["file_id"], "queued")
        if settings.task_queue == "redis":
            from app.task_queue import RedisTaskQueue
            RedisTaskQueue.from_settings(settings).enqueue_knowledge(record["file_id"], principal.tenant_id)
        else:
            asyncio.create_task(knowledge_processing.process(principal.tenant_id, record["file_id"]))
        return {**record, "status": "queued"}
    except Exception:
        storage_provider(settings).delete(storage_key)
        raise
@app.get("/api/v1/knowledge/files/{file_id}")
async def get_knowledge_file(file_id: str, workbench_session: str | None = Cookie(default=None)) -> dict:
    principal = require_admin(workbench_session); item = product_store.knowledge_file(principal.tenant_id, file_id)
    if not item: raise HTTPException(404, "知识文件不存在。")
    item["chunks"] = product_store.knowledge_chunks(principal.tenant_id, file_id) if item["status"] == "ready" else []
    return item
@app.post("/api/v1/knowledge/files/{file_id}/retry", status_code=202)
async def retry_knowledge_file(file_id: str, workbench_session: str | None = Cookie(default=None)) -> dict:
    principal = require_admin(workbench_session); item = product_store.knowledge_file(principal.tenant_id, file_id)
    if not item: raise HTTPException(404, "知识文件不存在。")
    product_store.set_knowledge_file_status(principal.tenant_id, file_id, "queued", error_message=None)
    if settings.task_queue == "redis":
        from app.task_queue import RedisTaskQueue
        RedisTaskQueue.from_settings(settings).enqueue_knowledge(file_id, principal.tenant_id)
    else: asyncio.create_task(knowledge_processing.process(principal.tenant_id, file_id))
    return {"file_id":file_id,"status":"queued"}
@app.post("/api/v1/knowledge/retrieval-test")
async def knowledge_retrieval_test(payload: KnowledgeQueryRequest, workbench_session: str | None = Cookie(default=None)) -> dict:
    principal = require_admin(workbench_session)
    return {"query": payload.query, "results": knowledge_retrieval.search(principal.tenant_id, payload.query, payload.top_k)}
@app.post("/api/v1/knowledge/text")
async def create_knowledge_text(payload: KnowledgeTextRequest,workbench_session: str | None = Cookie(default=None)) -> dict:
    principal=require_admin(workbench_session); return product_store.add_knowledge_text(principal.tenant_id,payload.name,payload.content)
@app.delete("/api/v1/knowledge/files/{file_id}")
async def delete_knowledge_file(file_id: str,workbench_session: str | None = Cookie(default=None)) -> dict:
    principal=require_admin(workbench_session)
    item=product_store.knowledge_file(principal.tenant_id,file_id)
    if not item or not product_store.delete_knowledge_file(principal.tenant_id,file_id): raise HTTPException(404,"文件不存在。")
    if item.get("storage_key"):
        storage_provider(settings).delete(item["storage_key"])
    return {"status":"deleted"}
@app.get("/api/v1/assets")
async def list_assets(workbench_session: str | None = Cookie(default=None)) -> list[dict]:
    principal=require_admin(workbench_session); return product_store.assets(principal.tenant_id)
@app.post("/api/v1/assets")
async def create_asset(payload: AssetRequest,workbench_session: str | None = Cookie(default=None)) -> dict:
    principal=require_admin(workbench_session)
    try: return product_store.add_asset(principal.tenant_id,payload.name,payload.asset_type,payload.url,payload.tags,payload.description)
    except ValueError as exc: raise HTTPException(422,"不支持的素材类型。") from exc
@app.delete("/api/v1/assets/{asset_id}")
async def delete_asset(asset_id: str,workbench_session: str | None = Cookie(default=None)) -> dict:
    principal=require_admin(workbench_session)
    if not product_store.delete_asset(principal.tenant_id,asset_id): raise HTTPException(404,"素材不存在。")
    return {"status":"deleted"}


@app.get("/api/v1/storage/{storage_key:path}")
async def get_storage(storage_key: str, workbench_session: str | None = Cookie(default=None)):
    principal = current_user(workbench_session)
    if not product_store.can_read_storage(principal.tenant_id, principal.user_id, storage_key):
        raise HTTPException(404, "图片不存在。")
    try:
        return Response(storage_provider(settings).get(storage_key), media_type="image/png")
    except FileNotFoundError as exc:
        raise HTTPException(404, "图片文件不存在。") from exc


@app.get("/api/v1/poc/runtime-baseline")
async def runtime_baseline() -> dict:
    """Safe-to-display configuration; secrets are deliberately excluded."""
    return {
        "runtime_version": "openai-codex==0.147.0",
        "model_provider_id": settings.model_provider_id,
        "model_base_url": settings.model_base_url,
        "model_wire_api": settings.model_wire_api,
        "model_id": settings.model_id,
        "reasoning_effort": settings.reasoning_effort,
        "skill": {"name": "poster-design", "version": "1.0.0"},
        "image_provider_id": settings.image_provider_id,
        "image_model_id": settings.image_model_id,
        "api_key_configured": bool(os.environ.get(settings.codex_api_key_env)),
    }

@app.get("/api/admin/runtime/diagnostics")
async def runtime_diagnostics(workbench_session: str | None = Cookie(default=None)) -> dict:
    require_admin(workbench_session)
    last=store.latest_run_trace()
    latest_success=None
    if last and last["status"] == "completed": latest_success={"run_id":last["run_id"],"completed_at":last["completed_at"]}
    return {"runtime_version":"openai-codex==0.147.0","provider":settings.model_provider_id,"model":settings.model_id,"base_url":settings.model_base_url,"wire_api":settings.model_wire_api,"api_key_present":bool(os.environ.get(settings.codex_api_key_env)),"api_key_fingerprint":_key_fingerprint(),"codex_process_profiles":len(manager._instances),"platform_mcp_url":settings.platform_mcp_url,"platform_mcp_configured":bool(settings.platform_mcp_url),"deepseek_auth_status":"not_probed","deepseek_responses_status":"not_probed","last_successful_turn":latest_success,"last_error":(last["payload"].get("error") if last and last["status"] == "failed" else None)}

@app.post("/api/admin/runtime/test")
async def runtime_test(payload: RuntimeTestRequest, workbench_session: str | None = Cookie(default=None)) -> dict:
    principal=require_admin(workbench_session)
    prompt="只回复 OK" if payload.mode == "ok" else "调用 enterprise_config_get 并只返回企业名称"
    try:
        result=await agents.run(principal.tenant_id,"image-agent",prompt)
    except RuntimeError as exc:
        raise HTTPException(502,"Runtime 最小测试失败；请查看 diagnostics 的 last_error。") from exc
    return {"status":"completed","run_id":result.run_id,"conversation_id":result.conversation_id,"codex_thread_id":result.thread_id,"response":result.text}


@app.post("/api/v1/poc/runs")
async def run_agent(request: RunRequest, x_poc_api_key: str | None = Header(default=None)) -> dict:
    tenant_id = tenant_from_key(x_poc_api_key)
    try:
        result = await agents.run(tenant_id, request.agent_id, request.message, request.conversation_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {
        "run_id": result.run_id,
        "conversation_id": result.conversation_id,
        "runtime_thread_id": result.thread_id,
        "status": "completed",
        "reply": result.text,
    }
