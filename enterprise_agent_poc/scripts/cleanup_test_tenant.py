"""Delete only the Phase A controlled tenant and verify all its resources."""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.settings import settings
from app.storage import storage_provider
from app.store import POCStore
from provision_test_tenant import TEST_TENANT_ID


def inventory(store: POCStore) -> dict:
    with store.connection() as conn:
        file_keys = [r["storage_key"] for r in conn.execute("SELECT storage_key FROM knowledge_files WHERE tenant_id=? AND storage_key IS NOT NULL", (TEST_TENANT_ID,)).fetchall()]
        generated_keys = [r["storage_key"] for r in conn.execute("SELECT storage_key FROM generations WHERE tenant_id=? AND storage_key IS NOT NULL", (TEST_TENANT_ID,)).fetchall()]
        avatars = [r["avatar_storage_key"] for r in conn.execute("SELECT avatar_storage_key FROM users WHERE tenant_id=? AND avatar_storage_key IS NOT NULL", (TEST_TENANT_ID,)).fetchall()]
        counts = {}
        for table, where in (("users", "tenant_id=?"), ("knowledge_files", "tenant_id=?"), ("knowledge_chunks", "tenant_id=?"), ("knowledge_bases", "tenant_id=?"), ("knowledge_documents", "tenant_id=?"), ("assets", "tenant_id=?"), ("generations", "tenant_id=?"), ("tasks", "tenant_id=?"), ("conversations", "tenant_id=?"), ("run_traces", "tenant_id=?"), ("tenant_agent_instances", "tenant_id=?"), ("credit_accounts", "tenant_id=?"), ("credit_transactions", "tenant_id=?"), ("enterprise_configs", "tenant_id=?"), ("tenants", "id=?")):
            counts[table] = conn.execute(f"SELECT COUNT(*) AS value FROM {table} WHERE {where}", (TEST_TENANT_ID,)).fetchone()["value"]
    provider = storage_provider(settings)
    prefix_keys = list(provider.list_keys(f"knowledge/{TEST_TENANT_ID}/"))
    runtime_root = settings.data_dir / "runtime" / TEST_TENANT_ID
    runtime_dirs = sum(1 for _ in runtime_root.rglob("*") if _.is_dir()) if runtime_root.exists() else 0
    return {"database_rows": counts, "storage_keys": sorted(set(file_keys + generated_keys + avatars + prefix_keys)), "runtime_dirs": runtime_dirs, "runtime_root": str(runtime_root)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    store = POCStore(settings.database_url)
    if args.dry_run or not args.execute:
        if store.is_postgres:
            before = inventory(store)
        else:
            before = {"database_rows": None, "storage_keys": None, "runtime_dirs": None, "runtime_root": str(settings.data_dir / "runtime" / TEST_TENANT_ID)}
        print(json.dumps({"status": "dry_run", "tenant_id": TEST_TENANT_ID, **before}, ensure_ascii=False))
        return 0
    if not store.is_postgres:
        raise RuntimeError("生产受控租户工具仅允许 PostgreSQL。")
    before = inventory(store)
    for key in before["storage_keys"]:
        storage_provider(settings).delete(key)
    with store.connection() as conn:
        # Dependants without tenant cascades are removed before the tenant.
        conn.execute("DELETE FROM task_results WHERE task_id IN (SELECT id FROM tasks WHERE tenant_id=?)", (TEST_TENANT_ID,))
        conn.execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE tenant_id=?)", (TEST_TENANT_ID,))
        conn.execute("DELETE FROM messages WHERE conversation_id IN (SELECT id FROM conversations WHERE tenant_id=?)", (TEST_TENANT_ID,))
        conn.execute("DELETE FROM conversation_owners WHERE conversation_id IN (SELECT id FROM conversations WHERE tenant_id=?)", (TEST_TENANT_ID,))
        conn.execute("DELETE FROM execution_events WHERE conversation_id IN (SELECT id FROM conversations WHERE tenant_id=?)", (TEST_TENANT_ID,))
        conn.execute("DELETE FROM asset_metadata WHERE asset_id IN (SELECT id FROM assets WHERE tenant_id=?)", (TEST_TENANT_ID,))
        # Delete children before parents even when a deployment has stricter
        # legacy foreign keys without ON DELETE CASCADE.
        for table in ("run_traces", "generations", "knowledge_chunks", "knowledge_documents", "knowledge_files", "knowledge_bases", "assets", "tasks", "conversations", "credit_transactions", "tenant_agent_instances", "credit_accounts", "enterprise_configs", "users"):
            conn.execute(f"DELETE FROM {table} WHERE tenant_id=?", (TEST_TENANT_ID,))
        conn.execute("DELETE FROM tenants WHERE id=?", (TEST_TENANT_ID,))
    runtime_root = settings.data_dir / "runtime" / TEST_TENANT_ID
    if runtime_root.exists():
        expected_parent = (settings.data_dir / "runtime").resolve()
        if expected_parent not in runtime_root.resolve().parents:
            raise RuntimeError("运行目录未处于受控 runtime 根目录，拒绝删除。")
        shutil.rmtree(runtime_root)
    after = inventory(store)
    remaining = sum(after["database_rows"].values()) + len(after["storage_keys"]) + after["runtime_dirs"]
    print(json.dumps({"status": "clean" if remaining == 0 else "verification_failed", "tenant_id": TEST_TENANT_ID, "active_temporary_credentials": 0 if remaining == 0 else None, **after}, ensure_ascii=False))
    return 0 if remaining == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
