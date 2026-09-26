"""Release-only policy bracket: exact plan, one task, finally restoration."""
import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import release_scoped_runtime_test as scoped
from test_runtime_policy_rollout import fixture as isolated_policy_fixture


ROOT = Path(__file__).resolve().parents[1]


class PolicyFixture:
    def __init__(self):
        self.calls = []
        self.enabled = False

    def plan(self, tenant, slug):
        self.calls.append(("plan", tenant, slug))
        return {"status": "planned", "policy_scope": "one_tenant_one_template",
                "original_sha256": "a" * 64, "predicted_sha256": "c" * 64,
                "predicted_fingerprint": "d" * 64,
                "current_policy": {"enabled": False, "allowed_tenant_count": 0,
                                   "allowed_slug_count": 0, "runtime_test_tenant_configured": False}}

    def apply(self, tenant, slug, *, expected_config_sha256):
        self.calls.append(("apply", tenant, slug, expected_config_sha256))
        assert expected_config_sha256 == "a" * 64
        self.enabled = True
        return {"status": "verified", "operation_id": "b" * 32, "post_sha256": "c" * 64}

    def status(self):
        self.calls.append(("status", self.enabled))
        return {"config_sha256": ("c" if self.enabled else "a") * 64,
                "config_fingerprint": "d" * 64 if self.enabled else "e" * 64,
                "policy_enabled": self.enabled,
                "policy_tenant_count": int(self.enabled),
                "policy_slug_count": int(self.enabled),
                "runtime_test_tenant_configured": self.enabled}

    def restore(self, operation_id):
        self.calls.append(("restore", operation_id))
        assert operation_id == "b" * 32
        self.enabled = False
        return {"status": "rolled_back"}


@dataclass(frozen=True)
class RuntimeSettingsFixture:
    agent_runtime_test_production_enabled: bool = False
    agent_runtime_test_allowed_tenant_ids: tuple[str, ...] = ()
    agent_runtime_test_allowed_template_slugs: tuple[str, ...] = ()
    agent_runtime_test_tenant_id: str | None = None


class RuntimeTesterFixture:
    def __init__(self, error=None):
        self.error = error
        self.calls = []
        self.cancellations = []
        self.resolver = SimpleNamespace(settings=RuntimeSettingsFixture(), test_tenant_id=None)
        self.product = SimpleNamespace(cancel_task=lambda *args: (self.cancellations.append(args) or {"status": "cancelled"}))

    async def run_release(self, **kwargs):
        self.calls.append(kwargs)
        assert self.resolver.settings.agent_runtime_test_production_enabled is True
        assert self.resolver.settings.agent_runtime_test_allowed_tenant_ids == (kwargs["tenant_id"],)
        assert self.resolver.settings.agent_runtime_test_allowed_template_slugs == (kwargs["agent_slug"],)
        assert self.resolver.test_tenant_id == kwargs["tenant_id"]
        if self.error:
            raise self.error
        return {"task_id": "task-1", "runtime_test_id": "test-1", "status": "queued"}


def manifest():
    return {"release_id": "phase-b-fixture", "source_commit": "1" * 40,
            "archive_sha256": "2" * 64, "selected_files": ["enterprise_agent_poc/pyproject.toml"],
            "selected_file_count": 1, "build_platform": "linux-fixture",
            "agent_productization_transition": json.loads(
                (ROOT / "deploy/phase_b_agent_productization.json").read_text(encoding="utf-8"))}


def test_rt7_rt8_rt12_exact_scope_apply_one_task_and_restore(monkeypatch):
    policy, tester = PolicyFixture(), RuntimeTesterFixture()
    monkeypatch.setattr(scoped, "_read_result", lambda *args: {
        "status": "passed", "task_id": "task-1", "runtime_test_id": "test-1",
        "conversation_id": "conversation-1", "run_id": "run-1", "credit_cost": 5,
    })
    result = asyncio.run(scoped.execute(
        manifest=manifest(), release_operation_id="operation-1",
        revision_id="revision-1", fingerprint="f" * 64,
        tester=tester, rollout=policy,
    ))
    assert result["policy_restored"] is True and policy.enabled is False
    assert result["credit_cost"] == 5
    assert policy.calls[0] == ("plan", "zhiy-e-intelligence", "wechat-official-account-writing")
    assert len(tester.calls) == 1
    assert tester.calls[0]["release_operation_id"] == "operation-1"
    assert tester.calls[0]["revision_id"] == "revision-1"
    assert tester.calls[0]["fingerprint"] == "f" * 64
    assert tester.resolver.settings == RuntimeSettingsFixture()
    assert tester.resolver.test_tenant_id is None
    assert policy.calls[-2:] == [("restore", "b" * 32), ("status", False)]


def test_rt13_restore_after_runtime_failure():
    policy, tester = PolicyFixture(), RuntimeTesterFixture(RuntimeError("provider failed"))
    with pytest.raises(RuntimeError, match="provider failed"):
        asyncio.run(scoped.execute(
            manifest=manifest(), release_operation_id="operation-1",
            revision_id="revision-1", fingerprint="f" * 64,
            tester=tester, rollout=policy,
        ))
    assert policy.enabled is False
    assert policy.calls[-2:] == [("restore", "b" * 32), ("status", False)]


def test_policy_not_closed_blocks_before_apply():
    policy = PolicyFixture()
    original = policy.plan
    def unsafe(tenant, slug):
        result = original(tenant, slug)
        result["current_policy"]["enabled"] = True
        return result
    policy.plan = unsafe
    with pytest.raises(scoped.ReleaseRuntimeTestBlocked, match="POLICY_NOT_CLOSED"):
        asyncio.run(scoped.execute(
            manifest=manifest(), release_operation_id="operation-1",
            revision_id="revision-1", fingerprint="f" * 64,
            tester=RuntimeTesterFixture(), rollout=policy,
        ))
    assert all(call[0] != "apply" for call in policy.calls)


def test_timeout_cancels_owned_task_restores_policy_and_requires_recovery(monkeypatch):
    policy, tester = PolicyFixture(), RuntimeTesterFixture()
    ticks = iter((0, 1))
    monkeypatch.setattr(scoped, "time", SimpleNamespace(monotonic=lambda: next(ticks, 1)))
    with pytest.raises(scoped.ReleaseRuntimeManualRecovery, match="UNSETTLED"):
        asyncio.run(scoped.execute(
            manifest=manifest(), release_operation_id="operation-1",
            revision_id="revision-1", fingerprint="f" * 64,
            tester=tester, rollout=policy, timeout_seconds=1,
        ))
    assert tester.cancellations == [
        ("task-1", "zhiy-e-intelligence", "ba2afd04-0cfd-45fe-9771-ef8e741796ba"),
    ]
    assert policy.enabled is False


def test_failed_restore_is_manual_recovery(monkeypatch):
    policy, tester = PolicyFixture(), RuntimeTesterFixture()
    monkeypatch.setattr(scoped, "_read_result", lambda *args: {
        "status": "passed", "task_id": "task-1", "runtime_test_id": "test-1",
        "conversation_id": "conversation-1", "run_id": "run-1", "credit_cost": 5,
    })
    policy.restore = lambda operation_id: (_ for _ in ()).throw(RuntimeError("restore failed"))
    with pytest.raises(scoped.ReleaseRuntimeManualRecovery, match="RESTORE_UNPROVEN"):
        asyncio.run(scoped.execute(
            manifest=manifest(), release_operation_id="operation-1",
            revision_id="revision-1", fingerprint="f" * 64,
            tester=tester, rollout=policy,
        ))


def test_isolated_real_rollout_apply_and_exact_restore(isolated_policy_fixture, monkeypatch):
    rollout, _ = isolated_policy_fixture
    original = rollout.env.read_bytes()
    monkeypatch.setattr(scoped, "_read_result", lambda *args: {
        "status": "passed", "task_id": "task-1", "runtime_test_id": "test-1",
        "conversation_id": "conversation-1", "run_id": "run-1", "credit_cost": 5,
    })
    result = asyncio.run(scoped.execute(
        manifest=manifest(), release_operation_id="operation-1",
        revision_id="revision-1", fingerprint="f" * 64,
        tester=RuntimeTesterFixture(), rollout=rollout,
    ))
    assert result["policy_restored"] is True
    assert rollout.env.read_bytes() == original
    assert rollout.status()["policy_enabled"] is False


def test_isolated_real_rollout_restores_after_runtime_failure(isolated_policy_fixture):
    rollout, _ = isolated_policy_fixture
    original = rollout.env.read_bytes()
    with pytest.raises(RuntimeError, match="provider failed"):
        asyncio.run(scoped.execute(
            manifest=manifest(), release_operation_id="operation-1",
            revision_id="revision-1", fingerprint="f" * 64,
            tester=RuntimeTesterFixture(RuntimeError("provider failed")), rollout=rollout,
        ))
    assert rollout.env.read_bytes() == original
    assert rollout.status()["policy_enabled"] is False
