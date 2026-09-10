import pytest

from scripts.reindex_knowledge_v1_4 import _safe_table_name


def test_reindex_backup_table_name_is_strictly_scoped():
    assert _safe_table_name("rag_index_backup_v1_20260910") == "rag_index_backup_v1_20260910"
    with pytest.raises(ValueError, match="invalid_backup_table_name"):
        _safe_table_name("knowledge_chunks; DROP TABLE tenants")
