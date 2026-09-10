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
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.settings import settings
from app.store import POCStore

MIGRATIONS = ROOT / "migrations" / "postgres"


def migration_files() -> list[Path]:
    return sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql"))


def checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
    return ([{"version": p.name[:3], "name": p.name[4:], "checksum": checksum(p)} for p in migration_files()], {row["version"]: dict(row) for row in rows})


def status(store: POCStore) -> int:
    files, applied = state(store)
    records = []
    exit_code = 0
    for item in files:
        historical = applied.get(item["version"])
        mismatch = bool(historical and historical["checksum"] != item["checksum"])
        if mismatch:
            exit_code = 2
        records.append({"version": item["version"], "name": item["name"], "status": "checksum_mismatch" if mismatch else ("applied" if historical else "pending"), "applied_at": historical.get("applied_at") if historical else None})
    unknown = sorted(set(applied) - {item["version"] for item in files})
    if unknown:
        exit_code = 2
    print(json.dumps({"database": "postgresql", "migrations": records, "unknown_history_versions": unknown}, ensure_ascii=False))
    return exit_code


def up(store: POCStore) -> int:
    files, applied = state(store)
    for item in files:
        historical = applied.get(item["version"])
        if historical and historical["checksum"] != item["checksum"]:
            raise RuntimeError(f"迁移 {item['version']} 的历史 checksum 不匹配；拒绝继续执行。")
    applied_now: list[str] = []
    for item in files:
        if item["version"] in applied:
            continue
        path = next(p for p in migration_files() if p.name[:3] == item["version"])
        with store.connection() as conn:
            ensure_history(conn)
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations(version,name,checksum) VALUES (?,?,?)", (item["version"], item["name"], item["checksum"]))
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
