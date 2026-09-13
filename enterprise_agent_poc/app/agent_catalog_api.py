"""Platform-admin-only Stage 1 API, isolated from existing execution APIs."""
from fastapi import APIRouter, Cookie, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.agent_productization import AgentProductization


class BindingList(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bindings: list[dict] = Field(max_length=30)


class PublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str = Field(default="production", pattern="^(production|local_test)$")


class InstanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_template_version_id: str
    overrides: dict = Field(default_factory=dict)


def catalog_router(catalog: AgentProductization, require_platform_admin) -> APIRouter:
    router = APIRouter(prefix="/api/v1/platform/agents", tags=["Agent Productization Stage 1"])

    def admin(workbench_session: str | None = Cookie(default=None)):
        principal = require_platform_admin(workbench_session)
        catalog.ensure_initialized()
        return principal

    @router.get("")
    def listing(principal=Depends(admin)):
        return catalog.list_templates()

    @router.get("/options")
    def options(principal=Depends(admin)):
        return catalog.options()

    @router.post("", status_code=201)
    def create(payload: dict, principal=Depends(admin)):
        return catalog.create_template(payload, principal.user_id)

    @router.get("/{template_id}")
    def detail(template_id: str, principal=Depends(admin)):
        return catalog.detail(template_id)

    @router.post("/{template_id}/versions", status_code=201)
    def revision(template_id: str, payload: dict, principal=Depends(admin)):
        return catalog.create_version(template_id, payload, principal.user_id)

    @router.patch("/{template_id}/versions/{version_id}")
    def edit(template_id: str, version_id: str, payload: dict, principal=Depends(admin)):
        return catalog.edit_version(template_id, version_id, payload, principal.user_id)

    @router.put("/{template_id}/versions/{version_id}/skills")
    def skills(template_id: str, version_id: str, payload: BindingList, principal=Depends(admin)):
        return catalog.bind_skills(template_id, version_id, payload.bindings, principal.user_id)

    @router.put("/{template_id}/versions/{version_id}/tools")
    def tools(template_id: str, version_id: str, payload: BindingList, principal=Depends(admin)):
        return catalog.bind_tools(template_id, version_id, payload.bindings, principal.user_id)

    @router.post("/{template_id}/versions/{version_id}/validation")
    def validation(template_id: str, version_id: str, principal=Depends(admin)):
        return catalog.validate(template_id, version_id, principal.user_id)

    @router.post("/{template_id}/versions/{version_id}/test")
    async def runtime_test(template_id: str, version_id: str, principal=Depends(admin)):
        if not catalog.runtime_tester:
            from app.agent_productization import AgentCatalogError
            raise AgentCatalogError("Runtime Test Pending: Execution Resolver unavailable",409)
        return await catalog.runtime_tester.run(template_id,version_id,principal.user_id)

    @router.post("/{template_id}/instances/{tenant_id}/enable")
    def enable(template_id: str, tenant_id: str, principal=Depends(admin)):
        return catalog.set_instance_status(template_id,tenant_id,"enabled")

    @router.post("/{template_id}/instances/{tenant_id}/disable")
    def disable(template_id: str, tenant_id: str, principal=Depends(admin)):
        return catalog.set_instance_status(template_id,tenant_id,"disabled")

    @router.post("/{template_id}/versions/{version_id}/publish")
    def publish(template_id: str, version_id: str, payload: PublishRequest, principal=Depends(admin)):
        return catalog.publish(template_id, version_id, principal.user_id, payload.mode)

    @router.post("/{template_id}/versions/{version_id}/deprecate")
    def deprecate(template_id: str, version_id: str, principal=Depends(admin)):
        return catalog.deprecate(template_id, version_id, principal.user_id)

    @router.put("/{template_id}/instances/{tenant_id}")
    def configure(template_id: str, tenant_id: str, payload: InstanceRequest, principal=Depends(admin)):
        return catalog.configure_instance(template_id, tenant_id, payload.agent_template_version_id, payload.overrides)

    return router
