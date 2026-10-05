"""Release-owned, exact 012 -> 013 -> 014 forward migration transaction.

The caller must hold the global Release lock. Both PostgreSQL DDL files and
their history rows commit together; this helper never runs a down migration.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.store import POCStore
from scripts import migrate
from scripts.release_manifest import validate_manifest_contract

DECLARATION_PATH = ROOT / "deploy/forward_migrations_013_014.json"
WECHAT_SLUG = "wechat-official-account-writing"


class MigrationTransitionBlocked(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise MigrationTransitionBlocked(reason)


def declaration(manifest: dict) -> dict:
    validate_manifest_contract(manifest)
    declared = manifest.get("forward_migrations")
    if declared and declared.get("schema_version") == 2:
        from scripts.release_runtime_recovery import declared as recovery_declared
        recovery_declared(ROOT, manifest)
        return declared
    require(declared is not None and declared == json.loads(DECLARATION_PATH.read_text(encoding="utf-8")),
            "forward_migration_declaration_mismatch")
    return declared


def _history(conn):
    rows = [dict(row) for row in conn.execute(
        "SELECT version,name,checksum FROM schema_migrations ORDER BY version"
    ).fetchall()]
    require(len({row["version"] for row in rows}) == len(rows), "migration_history_duplicate")
    return rows


def _fingerprints(conn):
    return {row["id"]: row["configuration_fingerprint"] for row in conn.execute(
        "SELECT id,configuration_fingerprint FROM agent_template_versions ORDER BY id"
    ).fetchall()}


def _wechat_absent(conn):
    require(not conn.execute("SELECT 1 FROM agent_templates WHERE slug=%s", (WECHAT_SLUG,)).fetchone(),
            "phase_a_wechat_agent_must_remain_absent")


def verify_history(conn, declared: dict, *, applied_count: int) -> dict:
    is015 = declared.get("schema_version") == 2
    if is015:
        from scripts.release_manifest import validate_forward_migrations
        from scripts.release_runtime_recovery import contract
        validate_forward_migrations(declared)
        contract(ROOT)
    first = 14 if is015 else 12
    total = 15 if is015 else 14
    require(applied_count in ({0, 1} if is015 else {0, 1, 2}), "migration_phase_invalid")
    items = migrate.migration_items()
    require([item["version"] for item in items] == [f"{v:03d}" for v in range(1, total + 1)],
            "unexpected_candidate_migration_set")
    for pin, item in zip(declared["migrations"], items[first:]):
        require(pin == {"version": item["version"], "filename": item["migration"],
                        "canonical_sha256": item["canonical_checksum"]}, "declared_migration_identity")
    history = _history(conn)
    expected = items[:first + applied_count]
    require([row["version"] for row in history] == [item["version"] for item in expected],
            "unexpected_pending_or_unknown_migration")
    for row, item in zip(history, expected):
        require(row["name"] == item["name"] and migrate.compatibility_status(
            row["checksum"], item) in {migrate.EXACT_MATCH, migrate.LEGACY_LINE_ENDING_COMPATIBLE},
            "migration_history_identity")
    if not is015:
        _wechat_absent(conn)
    return {"schema": f"{first + applied_count:03d}",
            "applied_versions": [row["version"] for row in history],
            "pending_versions": [item["version"] for item in items[first + applied_count:]],
            "fingerprints": _fingerprints(conn)}


def read_only_plan(store: POCStore, declared: dict) -> dict:
    require(store.is_postgres, "postgres_required")
    from psycopg import connect
    from psycopg.rows import dict_row
    with connect(store.database_url, row_factory=dict_row,
                 options="-c default_transaction_read_only=on") as conn:
        require(conn.execute("SHOW transaction_read_only").fetchone()["transaction_read_only"] == "on",
                "read_only_required")
        return verify_history(conn, declared, applied_count=0)


def apply_declared(store: POCStore, declared: dict) -> dict:
    before = read_only_plan(store, declared)
    items = migrate.migration_items()[14 if declared.get("schema_version") == 2 else 12:]
    with store.connection() as conn:
        # The existing OS release lock serializes releases; this transaction
        # also excludes a separate migration runner until both DDL files commit.
        conn.execute("LOCK TABLE schema_migrations IN EXCLUSIVE MODE")
        require(verify_history(conn, declared, applied_count=0)["fingerprints"] == before["fingerprints"],
                "historical_revision_fingerprint_changed")
        for count, item in enumerate(items, start=1):
            conn.execute(item["path"].read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations(version,name,checksum) VALUES (%s,%s,%s)",
                         (item["version"], item["name"], item["canonical_checksum"]))
            state = verify_history(conn, declared, applied_count=count)
            require(state["fingerprints"] == before["fingerprints"],
                    "historical_revision_fingerprint_changed")
    # Commit has completed. A failed post-commit verification leaves Schema 014
    # as a forward-safe state for the predecessor, not a destructive rollback.
    after = read_only_verify(store, declared)
    require(after["fingerprints"] == before["fingerprints"],
            "historical_revision_fingerprint_changed")
    return {"status": "declared_forward_migrations_applied", "schema": declared["target_schema"],
            "applied": [item["version"] for item in items], "schema_rollback": False,
            "historical_revision_fingerprints_unchanged": True}


def read_only_verify(store: POCStore, declared: dict) -> dict:
    require(store.is_postgres, "postgres_required")
    from psycopg import connect
    from psycopg.rows import dict_row
    with connect(store.database_url, row_factory=dict_row,
                 options="-c default_transaction_read_only=on") as conn:
        require(conn.execute("SHOW transaction_read_only").fetchone()["transaction_read_only"] == "on",
                "read_only_required")
        return verify_history(conn, declared, applied_count=len(declared["migrations"]))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("preflight", "apply", "verify"))
    parser.add_argument("--candidate-manifest", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        declared = declaration(json.loads(args.candidate_manifest.read_text(encoding="utf-8")))
        store = POCStore(migrate.settings.database_url)
        if args.mode == "preflight":
            state = read_only_plan(store, declared)
            result = {k: v for k, v in state.items() if k != "fingerprints"}
        elif args.mode == "apply":
            result = apply_declared(store, declared)
        else:
            state = read_only_verify(store, declared)
            result = {k: v for k, v in state.items() if k != "fingerprints"}
        print(json.dumps(result, sort_keys=True))
        return 0
    except MigrationTransitionBlocked as exc:
        print(json.dumps({"status": "BLOCKED", "check": str(exc)}))
        return 2
    except Exception:
        # Driver exceptions may contain a DSN. Never print them in a release log.
        print(json.dumps({"status": "BLOCKED", "check": "declared_forward_migration_transaction"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
