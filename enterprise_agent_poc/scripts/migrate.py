"""Controlled PostgreSQL migration runner for production releases.

The runner records immutable checksums in ``schema_migrations``.  Existing
installations are reconciled by applying the idempotent baseline migrations
once; a changed historical file is always a hard failure.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.settings import settings
from app.store import POCStore

MIGRATIONS = ROOT / "migrations" / "postgres"
EXACT_MATCH = "EXACT_MATCH"
LEGACY_LINE_ENDING_COMPATIBLE = "LEGACY_LINE_ENDING_COMPATIBLE"
PENDING = "PENDING"
UNKNOWN = "UNKNOWN"
CHECKSUM_MISMATCH = "CHECKSUM_MISMATCH"


def canonical_lf_bytes(content: bytes) -> bytes:
    """Normalize line endings only; every other byte remains significant."""
    return content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def migration_checksums(path: Path) -> dict[str, str]:
    raw = path.read_bytes()
    canonical = canonical_lf_bytes(raw)
    legacy_crlf = canonical.replace(b"\n", b"\r\n")
    return {
        "current_raw_checksum": sha256(raw),
        "canonical_checksum": sha256(canonical),
        "legacy_crlf_checksum": sha256(legacy_crlf),
    }


def checksum(path: Path) -> str:
    """Return the canonical checksum recorded for newly applied migrations."""
    return migration_checksums(path)["canonical_checksum"]


def compatibility_status(stored_checksum: str | None, checksums: dict[str, str]) -> str:
    if stored_checksum is None:
        return PENDING
    if stored_checksum == checksums["current_raw_checksum"]:
        return EXACT_MATCH
    if stored_checksum == checksums["legacy_crlf_checksum"]:
        return LEGACY_LINE_ENDING_COMPATIBLE
    return CHECKSUM_MISMATCH


def migration_files(directory: Path | None = None) -> list[Path]:
    root = directory or MIGRATIONS
    files = sorted(root.glob("*.sql"))
    if not files:
        raise RuntimeError("未找到任何迁移文件；拒绝继续执行。")
    invalid = [path.name for path in files if not re.fullmatch(r"[0-9]{3}_.+\.sql", path.name)]
    if invalid:
        raise RuntimeError(f"迁移文件名不合法：{invalid}")
    versions = [int(path.name[:3]) for path in files]
    expected = list(range(1, len(files) + 1))
    if versions != expected:
        raise RuntimeError(f"迁移文件顺序异常：expected={expected}, actual={versions}")
    return files


def migration_items() -> list[dict]:
    return [
        {
            "version": path.name[:3],
            "name": path.name[4:],
            "migration": path.name,
            "path": path,
            **migration_checksums(path),
        }
        for path in migration_files()
    ]


def ensure_history(conn) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version TEXT PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL, "
        "applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
    )


def state(store: POCStore) -> tuple[list[dict], dict[str, dict]]:
    if not store.is_postgres:
        raise RuntimeError("迁移工具仅支持 PostgreSQL，拒绝对 SQLite 伪执行生产迁移。")
    with store.connection() as conn:
        ensure_history(conn)
        rows = conn.execute("SELECT version,name,checksum,applied_at FROM schema_migrations ORDER BY version").fetchall()
    return migration_items(), {row["version"]: dict(row) for row in rows}


def migration_records(files: list[dict], applied: dict[str, dict]) -> list[dict]:
    records = []
    for item in files:
        historical = applied.get(item["version"])
        stored_checksum = historical.get("checksum") if historical else None
        compatibility = compatibility_status(stored_checksum, item)
        if historical and historical.get("name") != item["name"]:
            compatibility = CHECKSUM_MISMATCH
        records.append(
            {
                "version": item["version"],
                "name": item["name"],
                "migration": item["migration"],
                "status": "pending" if compatibility == PENDING else ("checksum_mismatch" if compatibility == CHECKSUM_MISMATCH else "applied"),
                "compatibility_status": compatibility,
                "stored_checksum": stored_checksum,
                "current_raw_checksum": item["current_raw_checksum"],
                "canonical_checksum": item["canonical_checksum"],
                "legacy_crlf_checksum": item["legacy_crlf_checksum"],
                "applied_at": historical.get("applied_at") if historical else None,
            }
        )
    return records


def status(store: POCStore) -> int:
    files, applied = state(store)
    records = migration_records(files, applied)
    unknown = sorted(set(applied) - {item["version"] for item in files})
    unknown_records = [
        {
            "version": version,
            "migration": applied[version].get("name"),
            "stored_checksum": applied[version].get("checksum"),
            "compatibility_status": UNKNOWN,
        }
        for version in unknown
    ]
    mismatch_count = sum(record["compatibility_status"] == CHECKSUM_MISMATCH for record in records)
    pending_count = sum(record["compatibility_status"] == PENDING for record in records)
    print(
        json.dumps(
            {
                "database": "postgresql",
                "migrations": records,
                "unknown_history_versions": unknown,
                "unknown_migrations": unknown_records,
                "checksum_mismatch": mismatch_count,
                "pending": pending_count,
            },
            ensure_ascii=False,
            default=str,
        )
    )
    return 2 if mismatch_count or unknown else 0


def up(store: POCStore) -> int:
    files, applied = state(store)
    unknown = sorted(set(applied) - {item["version"] for item in files})
    if unknown:
        raise RuntimeError(f"数据库存在代码仓库未知的迁移版本：{unknown}")
    records = migration_records(files, applied)
    blocked = [record["version"] for record in records if record["compatibility_status"] == CHECKSUM_MISMATCH]
    if blocked:
        raise RuntimeError(f"迁移 {blocked} 的历史 checksum 不匹配；拒绝继续执行。")
    applied_now: list[str] = []
    for item in files:
        if item["version"] in applied:
            continue
        with store.connection() as conn:
            ensure_history(conn)
            conn.execute(item["path"].read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO schema_migrations(version,name,checksum) VALUES (?,?,?)",
                (item["version"], item["name"], item["canonical_checksum"]),
            )
        applied_now.append(item["version"])
    print(json.dumps({"status": "ok", "applied": applied_now}, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("status", "up"))
    args = parser.parse_args()
    store = POCStore(settings.database_url)
    return status(store) if args.command == "status" else up(store)


if __name__ == "__main__":
    raise SystemExit(main())
