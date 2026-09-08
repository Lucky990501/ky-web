from __future__ import annotations

from app.domain import RuntimeProfile


def test_runtime_profile_changes_with_tenant_and_skill_version():
    common = dict(agent_id="image-agent", model_provider_id="provider-a", model_id="model-a", reasoning_effort="medium")
    tenant_a = RuntimeProfile.build(tenant_id="tenant-a", skill_manifest={"poster-design": "1.0.0"}, **common)
    tenant_b = RuntimeProfile.build(tenant_id="tenant-b", skill_manifest={"poster-design": "1.0.0"}, **common)
    upgraded = RuntimeProfile.build(tenant_id="tenant-a", skill_manifest={"poster-design": "1.1.0"}, **common)
    assert len({tenant_a.id, tenant_b.id, upgraded.id}) == 3
