from dataclasses import replace

import pytest

from app.product_store import ProductStore
from app.settings import settings
from app.storage import (
    LocalStorage,
    StorageObjectNotFound,
    StorageUnavailable,
    storage_provider,
)
from app.store import POCStore
from scripts.migrate import migration_files


def test_local_storage_distinguishes_missing_and_unavailable(tmp_path, monkeypatch):
    storage = LocalStorage(tmp_path)

    with pytest.raises(StorageObjectNotFound):
        storage.get("generated/missing.png")

    class DeniedPath:
        def read_bytes(self):
            raise PermissionError("denied")

    monkeypatch.setattr(storage, "_path", lambda _key: DeniedPath())
    with pytest.raises(StorageUnavailable):
        storage.get("generated/unreadable.png")


def test_storage_factory_maps_configuration_failures_to_safe_unavailable(tmp_path):
    incomplete_oss = replace(
        settings,
        object_storage_provider="oss",
        object_storage_dir=tmp_path,
        oss_access_key_id="",
        oss_access_key_secret="",
        oss_endpoint="",
        oss_bucket_name="",
    )
    unknown = replace(settings, object_storage_provider="unknown", object_storage_dir=tmp_path)

    with pytest.raises(StorageUnavailable):
        storage_provider(incomplete_oss)
    with pytest.raises(StorageUnavailable):
        storage_provider(unknown)


def test_history_storage_indexes_exist_in_sqlite_and_append_only_migration(tmp_path):
    store = POCStore(tmp_path / "history-indexes.db")
    ProductStore(store).initialize()

    expected = {
        "idx_tasks_history",
        "idx_generations_history",
        "idx_generations_storage",
        "idx_assets_tenant_url",
        "idx_conversation_owners_history",
    }
    with store.connection() as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()

    assert expected <= {row["name"] for row in rows}
    migration = migration_files()[-1]
    assert migration.name == "007_history_storage_indexes.sql"
    source = migration.read_text(encoding="utf-8")
    assert "SET LOCAL lock_timeout = '5s'" in source
    assert "SET LOCAL statement_timeout = '60s'" in source
