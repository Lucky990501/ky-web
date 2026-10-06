"""Read-only exact015 Runtime release gate. No DDL, ledger or transition runner."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).absolute().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import rollback_preflight as gate
from scripts.release_manifest import validate_manifest_contract, validate_runtime_only_release


def declaration(root, manifest):
    validate_manifest_contract(manifest)
    value = manifest.get("runtime_only_release")
    validate_runtime_only_release(value)
    gate.require(value == gate.read_json(root / "deploy/schema_015_runtime_only.v1.json"),
                 "runtime_only_release_declared_identity")
    from scripts.release_runtime_recovery import contract
    recovery = contract(root)
    gate.require(value["recovery_contract"] == recovery["contract_id"], "runtime_only_recovery_identity")
    return value


def ledger_snapshot(root, database_url):
    """Read exact ledger + inventory, preserving old migrator behavior unchanged."""
    from scripts import migrate
    from psycopg import connect
    from psycopg.rows import dict_row
    items = migrate.migration_items()
    versions = [f"{i:03}" for i in range(1, 16)]
    gate.require([item["version"] for item in items] == versions, "runtime_only_migration_inventory")
    with connect(database_url, row_factory=dict_row,
                 options="-c default_transaction_read_only=on") as conn:
        gate.require(conn.execute("SHOW transaction_read_only").fetchone()["transaction_read_only"] == "on",
                     "runtime_only_readonly_required")
        rows = [dict(r) for r in conn.execute("SELECT version,name,checksum FROM schema_migrations ORDER BY version")]
    gate.require([r["version"] for r in rows] == versions, "runtime_only_requires_exact_schema015")
    for row, item in zip(rows, items):
        gate.require(row["name"] == item["name"] and migrate.compatibility_status(row["checksum"], item)
                     in {migrate.EXACT_MATCH, migrate.LEGACY_LINE_ENDING_COMPATIBLE},
                     "runtime_only_migration_checksum")
    return {"ledger_sha256": gate.digest(rows), "migration_action": "NONE",
            "migration_commands_executed": 0, "pending": 0, "unknown": 0, "checksum_drift": 0}


def preflight(base, root, manifest):
    declaration(root, manifest)
    from scripts.release_runtime_recovery import PREDECESSOR, verify_schema, predecessor
    current = (base / "release-current").resolve()
    # Before switch current must be exact predecessor; after switch only this
    # exact Candidate is legal. No arbitrary future/foreign runtime acceptance.
    prior, _ = predecessor(base, root)
    gate.require(current in {prior, root}, "runtime_only_exact_predecessor")
    from scripts.migrate import settings
    ledger = ledger_snapshot(root, settings.database_url)
    schema = verify_schema(base, root, PREDECESSOR["release_id"], PREDECESSOR["source_commit"])
    gate.require(schema["applied_versions"] == [f"{i:03}" for i in range(1, 16)]
                 and schema["pending"] == 0, "runtime_only_requires_exact_schema015")
    return {"status": "runtime_only_schema015_verified", "read_only": True,
            "release_mode": "RUNTIME_ONLY", "current_schema": "015", "target_schema": "015",
            "migration_action": "NONE", "migration_commands_executed": 0,
            "migration015_state": "ALREADY_APPLIED", "pending": 0, "unknown": 0,
            "checksum_drift": 0, "ledger_sha256": ledger["ledger_sha256"], "schema": schema, "exact_predecessor": PREDECESSOR["release_id"],
            "recovery_mode": "PREDECESSOR_ON_SCHEMA_015", "post_commit_runtime_rollback": "ENABLED"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("preflight", "verify"))
    parser.add_argument("--candidate-manifest", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        root = args.candidate_manifest.parent / "enterprise_agent_poc"
        gate.require(root == ROOT, "trusted_runtime_only_tooling_path")
        result = preflight(root.parent.parent.parent, root, gate.read_json(args.candidate_manifest))
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        reason = str(exc) if isinstance(exc, (gate.RollbackBlocked, ValueError)) else "runtime_only_input_or_io"
        print(json.dumps({"status": "BLOCKED", "check": reason}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
