"""Provision the one controlled tenant used by Phase A isolation gates.

No secret is emitted.  Supply the test administrator password through the
named environment variable only when performing an actual provision.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agent_catalog import CATALOG
from app.auth import hash_password
from app.domain import RuntimeProfile
from app.settings import settings
from app.store import POCStore

TEST_TENANT_ID = "rag-isolation-test"
TEST_ADMIN_EMAIL = "admin@rag-isolation-test.invalid"


def profile_id() -> str:
    return RuntimeProfile.build(
        tenant_id=TEST_TENANT_ID,
        agent_id="copywriting-agent",
        model_provider_id=settings.model_provider_id,
        model_id=settings.model_id,
        reasoning_effort=settings.reasoning_effort,
        skill_manifest=CATALOG["copywriting-agent"].skill_manifest,
    ).id


def planned_resources() -> dict:
    return {
        "tenant_id": TEST_TENANT_ID,
        "user_id": f"phase-a-{uuid.uuid5(uuid.NAMESPACE_URL, TEST_TENANT_ID + ':admin')}",
        "runtime_profile_id": profile_id(),
        "created_resources": ["tenant", "enterprise_config", "credit_account", "test_admin", "tenant_agent_instances", "runtime_workspace"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="仅显示将创建的资源（默认安全模式）。")
    parser.add_argument("--execute", action="store_true", help="确认执行写入。")
    parser.add_argument("--password-env", default="PHASE_A_TEST_PASSWORD")
    args = parser.parse_args()
    if args.dry_run or not args.execute:
        print(json.dumps({"status": "dry_run", **planned_resources()}, ensure_ascii=False))
        return 0
    password = os.environ.get(args.password_env)
    if not password:
        raise RuntimeError(f"缺少临时测试密码环境变量 {args.password_env}；脚本不会读取或输出密码文件。")
    store = POCStore(settings.database_url)
    if not store.is_postgres:
        raise RuntimeError("生产受控租户工具仅允许 PostgreSQL。")
    resource = planned_resources()
    with store.connection() as conn:
        existing = conn.execute("SELECT 1 FROM tenants WHERE id=?", (TEST_TENANT_ID,)).fetchone()
        if existing:
            raise RuntimeError("受控测试租户已存在；请先使用 cleanup_test_tenant.py --dry-run 审核。")
        user_id = resource["user_id"]
        conn.execute("INSERT INTO tenants(id,name,poc_api_key) VALUES (?,?,?)", (TEST_TENANT_ID, "Phase A 隔离测试租户", None))
        conn.execute("INSERT INTO enterprise_configs(tenant_id,payload) VALUES (?,?)", (TEST_TENANT_ID, json.dumps({"data_classification": "phase_a_isolation_test", "tenant_label": "rag-isolation-test"}, ensure_ascii=False)))
        conn.execute("INSERT INTO credit_accounts(tenant_id,balance) VALUES (?,?)", (TEST_TENANT_ID, 1000))
        conn.execute("INSERT INTO users(id,tenant_id,email,password_hash,display_name,role) VALUES (?,?,?,?,?,?)", (user_id, TEST_TENANT_ID, TEST_ADMIN_EMAIL, hash_password(password), "Phase A 测试管理员", "enterprise_admin"))
        templates = {row["id"] for row in conn.execute("SELECT id FROM agent_templates").fetchall()}
        missing = set(CATALOG) - templates
        if missing:
            raise RuntimeError(f"缺少 Agent 模板 {sorted(missing)}；请先执行迁移/应用初始化。")
        for agent_id in CATALOG:
            conn.execute("INSERT INTO tenant_agent_instances(tenant_id,agent_id,status) VALUES (?,?,'enabled')", (TEST_TENANT_ID, agent_id))
    runtime_root = settings.data_dir / "runtime" / TEST_TENANT_ID / "copywriting-agent" / resource["runtime_profile_id"]
    runtime_root.mkdir(parents=True, exist_ok=False)
    (runtime_root / ".phase-a-control.json").write_text(json.dumps({"tenant_id": TEST_TENANT_ID, "profile_id": resource["runtime_profile_id"]}), encoding="utf-8")
    print(json.dumps({"status": "provisioned", **resource}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
