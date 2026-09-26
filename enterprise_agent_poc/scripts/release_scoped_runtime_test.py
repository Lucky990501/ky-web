"""One Release-owned Runtime Test bracketed by exact Runtime Policy restore.

This is an internal Release entry point, never a user API. It deliberately
reuses the application's normal queue, worker, provider and persistence path.
The caller must hold the global Release lock and keep rollback armed.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import fcntl
import json
import os
import time
from pathlib import Path

from scripts.release_manifest import validate_manifest_contract
from scripts.rollback_preflight import digest
from scripts.runtime_policy_rollout import Rollout


class ReleaseRuntimeTestBlocked(RuntimeError):
    pass


class ReleaseRuntimeManualRecovery(RuntimeError):
    pass


def require(condition: bool, code: str) -> None:
    if not condition:
        raise ReleaseRuntimeTestBlocked(code)


def require_inherited_release_lock() -> None:
    """A direct CLI invocation cannot substitute for the formal switch lock."""
    # Mirror the formal shell's test-only lock override; Production leaves it
    # unset and uses /run/lock/enterprise-agent-workbench-release.lock.
    lock = Path(os.environ.get("RELEASE_LOCK_FILE",
                               "/run/lock/enterprise-agent-workbench-release.lock"))
    try:
        held = os.fstat(9)
        actual = lock.stat()
        require((held.st_dev, held.st_ino) == (actual.st_dev, actual.st_ino),
                "GLOBAL_RELEASE_LOCK_REQUIRED")
        fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, ValueError):
        raise ReleaseRuntimeTestBlocked("GLOBAL_RELEASE_LOCK_REQUIRED") from None


def _read_result(tester, task_id: str, test_id: str, tenant: str, cost: int) -> dict | None:
    with tester.resolver.store.connection() as conn:
        test = conn.execute(
            "SELECT status,result_json,task_id FROM agent_template_tests WHERE id=? AND test_type='runtime'",
            (test_id,),
        ).fetchone()
        task = conn.execute(
            "SELECT status,tenant_id,user_id,conversation_id,run_id FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone()
        require(test and task and test["task_id"] == task_id and task["tenant_id"] == tenant,
                "RELEASE_RUNTIME_ARTIFACT_MISMATCH")
        if test["status"] not in {"passed", "failed", "invalidated"}:
            return None
        require(test["status"] == "passed" and task["status"] == "completed",
                "RELEASE_RUNTIME_TEST_FAILED")
        charge = conn.execute(
            "SELECT amount FROM credit_transactions WHERE task_id=? AND tenant_id=?",
            (task_id, tenant),
        ).fetchall()
        require(len(charge) == 1 and charge[0]["amount"] == -cost,
                "RELEASE_RUNTIME_CREDIT_MISMATCH")
        result = json.loads(test["result_json"] or "{}")
        require(result.get("status") == "passed" and result.get("final_response_persisted") is True
                and task["conversation_id"] and task["run_id"],
                "RELEASE_RUNTIME_RESULT_INCOMPLETE")
        return {"status": "passed", "task_id": task_id, "runtime_test_id": test_id,
                "conversation_id": task["conversation_id"], "run_id": task["run_id"],
                "credit_cost": cost}


async def execute(*, manifest: dict, release_operation_id: str, revision_id: str,
                  fingerprint: str, tester, rollout, timeout_seconds: int = 240) -> dict:
    """Fail closed on policy drift and restore exact bytes in every exit path."""
    validate_manifest_contract(manifest)
    declaration = manifest.get("agent_productization_transition")
    require(declaration is not None and type(timeout_seconds) is int and 1 <= timeout_seconds <= 600,
            "RELEASE_RUNTIME_DECLARATION_REQUIRED")
    scope = declaration["runtime_test"]
    tenant, slug, actor = scope["tenant"], scope["agent_slug"], scope["actor"]
    plan = rollout.plan(tenant, slug)
    current = plan["current_policy"]
    require(plan["status"] == "planned" and plan["policy_scope"] == "one_tenant_one_template"
            and current == {"enabled": False, "allowed_tenant_count": 0,
                            "allowed_slug_count": 0, "runtime_test_tenant_configured": False},
            "RELEASE_RUNTIME_POLICY_NOT_CLOSED")
    original_sha = plan["original_sha256"]
    applied = None
    evidence = None
    resolver = tester.resolver
    original_settings = resolver.settings
    original_tenant = resolver.test_tenant_id
    require(original_settings.agent_runtime_test_production_enabled is False
            and not original_settings.agent_runtime_test_allowed_tenant_ids
            and not original_settings.agent_runtime_test_allowed_template_slugs
            and not original_tenant, "RELEASE_RUNTIME_PROCESS_POLICY_NOT_CLOSED")
    try:
        applied = rollout.apply(tenant, slug, expected_config_sha256=original_sha)
        require(applied.get("status") == "verified" and applied.get("operation_id")
                and applied.get("post_sha256") == plan["predicted_sha256"],
                "RELEASE_RUNTIME_POLICY_APPLY_FAILED")
        active = rollout.status()
        require(active["config_sha256"] == applied["post_sha256"]
                and active["config_fingerprint"] == plan["predicted_fingerprint"]
                and active["policy_enabled"] is True
                and active["policy_tenant_count"] == active["policy_slug_count"] == 1
                and active["runtime_test_tenant_configured"] is True,
                "RELEASE_RUNTIME_POLICY_VERIFY_FAILED")
        # The CLI imported app.main before Rollout changed the shared file.
        # Services have reloaded, but this one-shot process still holds frozen
        # pre-rollout Settings. Mirror only the four verified policy values in
        # its existing resolver; never instantiate another Runtime or Provider.
        resolver.settings = replace(
            original_settings,
            agent_runtime_test_production_enabled=True,
            agent_runtime_test_allowed_tenant_ids=(tenant,),
            agent_runtime_test_allowed_template_slugs=(slug,),
            agent_runtime_test_tenant_id=tenant,
        )
        resolver.test_tenant_id = tenant
        queued = await tester.run_release(
            release_operation_id=release_operation_id,
            revision_id=revision_id, fingerprint=fingerprint,
            tenant_id=tenant, agent_slug=slug, actor_id=actor,
            release_identity=manifest["release_id"],
            source_identity=manifest["source_commit"],
            manifest_identity=digest(manifest), runtime_test=scope,
        )
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            evidence = _read_result(tester, queued["task_id"], queued["runtime_test_id"],
                                    tenant, scope["credit_cost"])
            if evidence is not None:
                break
            await asyncio.sleep(1)
        if evidence is None:
            # Never claim an unsettled worker-owned Task is safe to abort.
            # Stop it through the existing product cancellation path, then
            # require explicit recovery evidence after the policy is restored.
            try:
                cancelled = tester.product.cancel_task(queued["task_id"], tenant, actor)
                require(cancelled is not None, "RELEASE_RUNTIME_CANCEL_UNPROVEN")
            except Exception:
                raise ReleaseRuntimeManualRecovery("RELEASE_RUNTIME_CANCEL_UNPROVEN") from None
            raise ReleaseRuntimeManualRecovery("RELEASE_RUNTIME_TEST_UNSETTLED")
    finally:
        resolver.settings = original_settings
        resolver.test_tenant_id = original_tenant
        try:
            if applied is not None and applied.get("operation_id"):
                restored = rollout.restore(applied["operation_id"])
                require(restored["status"] in {"rolled_back", "already_restored"},
                        "RELEASE_RUNTIME_POLICY_RESTORE_FAILED")
            # apply() itself attempts rollback on failure; verify that too.
            state = rollout.status()
            require(state["config_sha256"] == original_sha
                    and state["policy_enabled"] is False
                    and state["policy_tenant_count"] == state["policy_slug_count"] == 0
                    and state["runtime_test_tenant_configured"] is False,
                    "RELEASE_RUNTIME_POLICY_RESTORE_UNPROVEN")
        except Exception:
            raise ReleaseRuntimeManualRecovery("RELEASE_RUNTIME_POLICY_RESTORE_UNPROVEN") from None
    return {**evidence, "release_operation_id": release_operation_id,
            "revision_id": revision_id, "fingerprint": fingerprint,
            "tenant_id": tenant, "actor_id": actor, "agent_slug": slug,
            "policy_restored": True, "policy_original_sha256": original_sha}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-manifest", type=Path, required=True)
    parser.add_argument("--release-operation-id", required=True)
    parser.add_argument("--revision-id", required=True)
    parser.add_argument("--fingerprint", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=240)
    args = parser.parse_args()
    try:
        require_inherited_release_lock()
        manifest = json.loads(args.candidate_manifest.read_text(encoding="utf-8"))
        from app.main import agent_catalog_control

        tester = agent_catalog_control.runtime_tester
        result = asyncio.run(execute(
            manifest=manifest, release_operation_id=args.release_operation_id,
            revision_id=args.revision_id, fingerprint=args.fingerprint,
            tester=tester, rollout=Rollout(), timeout_seconds=args.timeout_seconds,
        ))
    except ReleaseRuntimeManualRecovery:
        print(json.dumps({"status": "AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED"}))
        return 3
    except Exception as exc:
        # Do not print provider exception bodies, config values, or secrets.
        print(json.dumps({"status": "BLOCKED", "error_type": type(exc).__name__}))
        return 2
    print(json.dumps({"status": "passed", **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
