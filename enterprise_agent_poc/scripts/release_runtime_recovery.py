"""Strict Migration 015 gates for the existing release_switch rollback entry.

All functions here are read-only except writing release-owned audit evidence.
Service activation remains exclusively in release_switch.sh. No down SQL,
Registry initialize, Provider task, Binding transition or arbitrary target.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).absolute().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.release_dual_source import application_root
ROOT = application_root(ROOT)
from scripts import rollback_preflight as gate

CONTRACT_ID = "migration-015-exact-predecessor-recovery-v1"
PREDECESSOR = {
    "release_id": "20261006-519c649-reference-image-oss-v1",
    "source_commit": "519c649bd61d3bf3b1a6708410b9b7102eccb7a2",
    "source_tree": "e867a4f9922f0f73e71e818e2993666d842559f2",
    "archive_sha256": "6d6ca0612dfc2d52e96c8775594ea90a5d697afd02c4fb229367e0c58db6ca11",
    "raw_manifest_sha256": "bd60c630c4fd7b9b2382d13d042ae6398c19e7da6de2cd5d1916f17a6cab3d4e",
    "manifest_sha256": "b159797b4596620f6de4df092ac6a1fc344d7921c51e331b7908d5ef87b2e271",
}
MIGRATION = {
    "version": "015", "filename": "015_chat_image_attachments.sql",
    "git_blob": "96bba0ea64ade51670775cf6f69b806a2248342a",
    "canonical_sha256": "67c4c85007d7ac1a9dc3f0e979359d6b3f9ea84206740d11b6ce05c11563f1fc",
}
STAGES = ("PRE_COMMIT", "MIGRATION_APPLIED", "RUNTIME_SWITCHED", "POST_COMMIT_HEALTH")


def contract(root):
    value = gate.read_json(root / "deploy/migration_015_recovery.v1.json")
    expected = {
        "schema_version": 1, "contract_id": CONTRACT_ID,
        "feature_source": "b25fc4381ce5fa2d9a35325155953a9f5cb0d2e4",
        "feature_tree": "0e7a0e589442141af70943113e858648a1ff46b8",
        "from_schema": "014", "target_schema": "015", "migration": MIGRATION,
        "exact_predecessor": PREDECESSOR,
        "recovery_mode": "PREDECESSOR_ON_SCHEMA_015",
        "forward_schema_predecessor_compatible": True,
        "data_contract": "member_account_status_v1",
        "compatibility_evidence": {
            "version": "schema015-production-predecessor519-readonly-v1-20261007",
            "report_sha256": "0d19e8ae96f88ee06f1ab8a31495bbf9a79fb838cc84c90c8c634999f8d61ba1",
            "scope": "EXACT_PRODUCTION_PREDECESSOR_NATIVE_RUNTIME_ON_SCHEMA_015_PROVIDER_ZERO",
        },
    }
    gate.require(value == expected and type(value["schema_version"]) is int
                 and value["forward_schema_predecessor_compatible"] is True,
                 "migration_015_recovery_contract_identity")
    content = (root / "migrations/postgres" / MIGRATION["filename"]).read_bytes().replace(b"\r\n", b"\n")
    gate.require(hashlib.sha256(content).hexdigest() == MIGRATION["canonical_sha256"]
                 and hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
                 == MIGRATION["git_blob"], "migration_015_blob_identity")
    return value


def declared(root, manifest):
    value = contract(root)
    if "runtime_only_release" in manifest:
        from scripts.schema_015_runtime_only import declaration
        declaration(root, manifest)
        return value
    plan = manifest.get("forward_migrations")
    gate.require(plan == gate.read_json(root / "deploy/forward_migrations_015.json")
                 and plan.get("recovery_contract") == CONTRACT_ID, "migration_015_plan_identity")
    gate.require(not ({"binding_transition", "agent_productization_transition", "skill_package_staging",
                       "deferred_skill"} & set(manifest)), "migration_015_code_only_scope")
    return value


def predecessor(base, root):
    exact = contract(root)["exact_predecessor"]
    source, manifest = gate.release_identity(base, exact["release_id"], exact["source_commit"], exact)
    raw = source.parent / (exact["release_id"] + ".manifest.json")
    gate.require(hashlib.sha256(raw.read_bytes()).hexdigest() == exact["raw_manifest_sha256"],
                 "predecessor_raw_manifest_identity")
    # Source -> full Git tree is correlated with the pinned native attestation.
    # A selected-file archive is NOT mislabeled as a complete Git tree hash.
    gate.require(exact["source_tree"] == PREDECESSOR["source_tree"], "predecessor_source_tree")
    return source, manifest


def verify_schema(base, root, target_id, target_commit, *, plan=False, database_url=None):
    own = gate.read_json(root.parent / (root.parent.name + ".manifest.json"))
    gate.require(own["release_id"] == root.parent.name, "trusted_recovery_release_id")
    installed, _ = gate.release_identity(base, own["release_id"], own["source_commit"])
    gate.require(installed == root, "trusted_recovery_release_path")
    recovery = declared(root, own)
    self_target = (target_id, target_commit) == (own["release_id"], own["source_commit"])
    prior_target = (target_id, target_commit) == (PREDECESSOR["release_id"], PREDECESSOR["source_commit"])
    gate.require(self_target or prior_target, "rollback_target_below_member_account_status_floor")
    declaration = gate.read_json(root / "deploy/rollback_compatibility.json")
    epoch = gate.epoch_contract(declaration)
    lock014 = epoch["schema_migrations"]
    gate.require([x["version"] for x in lock014] == [f"{i:03}" for i in range(1, 15)],
                 "migration_015_schema_baseline")
    lock015 = lock014 + [{k: MIGRATION[k] for k in ("version", "filename", "canonical_sha256")}]
    trusted = gate.check_sources(root, lock015)
    prior_root, _ = predecessor(base, root)
    # The exact new predecessor already carries all fifteen migration files.
    gate.check_sources(prior_root, lock015)
    floors = gate.data_contract_floors(declaration, lock015)
    if database_url is None:
        from scripts.migrate import settings
        database_url = settings.database_url
    rows, state, origin, active = gate.read_history(
        database_url, 16, plan=plan, target_id=target_id, target_commit=target_commit,
        contract=epoch, floors=floors, trusted_items=trusted)
    # Schema 014 is legal only for the exact pre-switch plan / pre-apply abort.
    gate.require(len(rows) == 15 or (len(rows) == 14 and (plan or prior_target)),
                 "migration_015_schema_state")
    if "runtime_only_release" in own:
        gate.require(len(rows) == 15, "runtime_only_requires_exact_schema015")
        from scripts.schema_015_runtime_only import ledger_snapshot
        ledger_snapshot(root, database_url)
    gate.require(recovery["data_contract"] in active, "migration_015_data_contract")
    return {"status": "rollback_plan_passed" if plan else "rollback_preflight_passed", "read_only": True,
            "target_release_id": target_id, "target_source_commit": target_commit,
            "applied_versions": [r["version"] for r in rows], "pending": 15 - len(rows),
            "target_known_versions": [f"{i:03}" for i in range(1, 16)],
            "epoch_schema_fingerprint": gate.digest(lock015), "compatibility_epoch": state["epoch"],
            "epoch_source": origin, "active_data_contract_floors": active,
            "old_runner_invoked": False, "recovery_mode": recovery["recovery_mode"]}


def approval(base, root, manifest):
    pins = {key: os.environ.get(env, "") for key, env in (
        ("source_commit", "RELEASE_EXPECTED_SOURCE_COMMIT"),
        ("archive_sha256", "RELEASE_EXPECTED_ARCHIVE_SHA256"),
        ("raw_manifest_sha256", "RELEASE_EXPECTED_RAW_MANIFEST_SHA256"),
        ("manifest_sha256", "RELEASE_EXPECTED_CANONICAL_MANIFEST_SHA256"))}
    gate.require(gate.COMMIT.fullmatch(pins["source_commit"])
                 and all(gate.HASH.fullmatch(v) for k, v in pins.items() if k != "source_commit"),
                 "known_current_approval_required")
    gate.require(manifest["source_commit"] == pins["source_commit"], "unknown_current_release")
    gate.require(manifest["release_id"] == root.parent.name, "known_current_release_path")
    installed, _ = gate.release_identity(base, manifest["release_id"], pins["source_commit"], pins)
    gate.require(installed == root, "known_current_release_path")
    gate.require(hashlib.sha256((root.parent / (manifest["release_id"] + ".manifest.json")).read_bytes()).hexdigest()
                 == pins["raw_manifest_sha256"], "known_current_raw_manifest")
    return pins


def receipt_path(base, rid):
    gate.require(gate.RELEASE.fullmatch(rid) and rid not in {".", ".."}, "release_receipt_path")
    return base / "shared/release-state" / (rid + ".json")


def receipt(base, root, manifest, snapshot):
    declared(root, manifest)
    schema = verify_schema(base, root, PREDECESSOR["release_id"], PREDECESSOR["source_commit"])
    gate.require(schema["applied_versions"] == [f"{i:03}" for i in range(1, 16)], "receipt_schema_015")
    from scripts import release_verify
    active = release_verify.binding_gate(base, root.parent / (manifest["release_id"] + ".manifest.json"),
        base / "releases" / PREDECESSOR["release_id"] / (PREDECESSOR["release_id"] + ".manifest.json"), snapshot, "state")
    value = {"schema_version": 1, "phase": "POST_COMMIT_HEALTH", "release_id": manifest["release_id"],
             "source_commit": manifest["source_commit"], "manifest_sha256": gate.digest(manifest),
             "recovery_contract_sha256": gate.digest(contract(root)), "predecessor": PREDECESSOR,
             "bindings": active, "schema": "015", "schema_rollback": False}
    if "runtime_only_release" in manifest:
        from scripts.schema_015_runtime_only import ledger_snapshot
        from scripts.migrate import settings
        value.update(release_mode="RUNTIME_ONLY", current_schema="015", target_schema="015",
                     migration_action="NONE", migration_commands_executed=0,
                     migration_ledger_sha256=ledger_snapshot(root, settings.database_url)["ledger_sha256"],
                     runtime_only_contract_sha256=gate.digest(manifest["runtime_only_release"]))
    path = receipt_path(base, manifest["release_id"])
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    gate.require(path.parent.resolve() == path.parent and not path.exists(), "commit_receipt_already_exists")
    with path.open("x", encoding="utf-8") as f:
        os.chmod(path, 0o600)
        json.dump(value, f, sort_keys=True)
    return {"status": "release_commit_receipt_written", "phase": value["phase"]}


def preflight(base, root, manifest):
    declared(root, manifest)
    approval(base, root, manifest)
    gate.require((base / "release-current").is_symlink()
                 and (base / "release-current").resolve() == root, "unknown_current_release")
    value = gate.read_json(receipt_path(base, manifest["release_id"]))
    gate.require(value.get("phase") == "POST_COMMIT_HEALTH" and value.get("release_id") == manifest["release_id"]
                 and value.get("source_commit") == manifest["source_commit"]
                 and value.get("manifest_sha256") == gate.digest(manifest)
                 and value.get("recovery_contract_sha256") == gate.digest(contract(root))
                 and value.get("predecessor") == PREDECESSOR, "release_commit_receipt_identity")
    if "runtime_only_release" in manifest:
        from scripts.schema_015_runtime_only import ledger_snapshot
        from scripts.migrate import settings
        gate.require(value.get("release_mode") == "RUNTIME_ONLY" and value.get("current_schema") == "015"
                     and value.get("target_schema") == "015" and value.get("migration_action") == "NONE"
                     and type(value.get("migration_commands_executed")) is int
                     and value["migration_commands_executed"] == 0
                     and value.get("migration_ledger_sha256") == ledger_snapshot(root, settings.database_url)["ledger_sha256"]
                     and value.get("runtime_only_contract_sha256") == gate.digest(manifest["runtime_only_release"]),
                     "runtime_only_commit_receipt_identity")
    schema = verify_schema(base, root, PREDECESSOR["release_id"], PREDECESSOR["source_commit"])
    gate.require(schema["applied_versions"] == [f"{i:03}" for i in range(1, 16)] and schema["pending"] == 0,
                 "post_commit_requires_schema_015")
    from scripts import release_verify
    active = release_verify.bindings(release_verify.registry_for(base))
    gate.require(active == value.get("bindings"), "UNKNOWN_BINDING_STATE")
    for role, state in release_verify.service_state(base).items():
        gate.require("error" not in state and type(state.get("pid")) is int
                     and state.get("active") in {"active", "inactive", "failed"},
                     "unknown_current_service_state_" + role)
        # A stopped/crashed service is recoverable. A live foreign runtime is
        # not: do not overwrite unknown service ownership under this contract.
        if state.get("pid", 0) > 0:
            gate.require(state.get("cwd") == str(root) and state.get("module_ok")
                         and state.get("exe") == str((base / "venv/bin/python").resolve()),
                         "unknown_current_service_" + role)
        else:
            gate.require(state["active"] in {"inactive", "failed"}, "unknown_current_service_state_" + role)
    return {"status": "post_commit_runtime_rollback_preflight_passed", "read_only": True,
            "exact_predecessor": PREDECESSOR["release_id"], "predecessor_source": PREDECESSOR["source_commit"],
            "predecessor_tree": PREDECESSOR["source_tree"], "schema": schema,
            "phase": "POST_COMMIT_HEALTH", "schema_rollback": False}


def dual_recovery(args,base,root,manifest):
    from scripts.release_dual_source import bound,profile
    from scripts.release_schema016 import inspect,target,RECOVERY
    from scripts import release_verify
    from app.store import POCStore
    from app.settings import settings
    b,p=bound(manifest)
    gate.require(base==p['base'],'schema016_recovery_base')
    state=inspect(POCStore(settings.database_url),root,b)
    if args.mode=='target': return target(state,manifest)
    if args.mode=='phase': return state
    if args.mode=='native':
        gate.require(args.role is not None and args.phase is not None and state['schema']=='016',
                     'schema016_native_role_schema')
        gate.require((base/'release-current').resolve()==root,'schema016_native_current_runtime')
        if args.phase=='post':
            current=release_verify.service_state(base)[args.role]
            gate.require(current.get('pid',0)>0 and current.get('active') in {'active','activating'}
                and current.get('cwd')==str(root) and current.get('module_ok') and
                current.get('exe')==str((base/'venv/bin/python').resolve()),'schema016_native_service_identity')
        return dict(status='PASS',native=state,role=args.role,phase=args.phase,
                    application_source=b['application']['source'],tooling_source=b['tooling']['source'])
    gate.require(args.role is None and args.phase is None and state['schema']=='016', 'schema016_recovery_phase')
    location=receipt_path(base,manifest['release_id'])
    active=release_verify.bindings(release_verify.registry_for(base))
    if args.mode=='receipt':
        gate.require(args.snapshot is not None and json.loads(args.snapshot.read_text())==active, 'schema016_commit_binding_snapshot')
        final=release_verify.verify_state(base,root.parent/(manifest['release_id']+'.manifest.json'),
            base/'releases'/b['exact_predecessor']['release_id']/(b['exact_predecessor']['release_id']+'.manifest.json'),args.snapshot)
        record=dict(schema_version=2,contract=RECOVERY,phase='POST_COMMIT_HEALTH',
            manifest_sha256=gate.digest(manifest),application=b['application']['source'],
            application_tree=b['application']['tree'],tooling=b['tooling']['source'],tooling_tree=b['tooling']['tree'],
            bindings=active,schema=state,final_state=final,exact_predecessor=b['exact_predecessor'])
        location.parent.mkdir(parents=True,exist_ok=True)
        gate.require(not location.exists() and not location.is_symlink(), 'schema016_commit_receipt_exists')
        with location.open('x') as out:
            os.chmod(location,0o600);json.dump(record,out,sort_keys=True,default=str);out.flush();os.fsync(out.fileno())
        return dict(status='schema016_release_commit_recorded',recovery_mode=RECOVERY)
    record=gate.read_json(location)
    gate.require(record.get('contract')==RECOVERY and record.get('phase')=='POST_COMMIT_HEALTH'
        and record.get('manifest_sha256')==gate.digest(manifest) and record.get('bindings')==active
        and record.get('application')==b['application']['source'] and record.get('application_tree')==b['application']['tree']
        and record.get('tooling')==b['tooling']['source'] and record.get('tooling_tree')==b['tooling']['tree']
        and record.get('exact_predecessor')==b['exact_predecessor'], 'schema016_commit_receipt_identity')
    for s in release_verify.service_state(base).values():
        gate.require(s.get('active') in {'active','inactive','failed'} and isinstance(s.get('pid'),int)
            and (s['pid']==0 or (s.get('cwd')==str(root) and s.get('module_ok'))), 'schema016_unknown_current_runtime')
    if args.mode=='capture':
        gate.require(args.snapshot.name=='state.json' and args.snapshot.parent.parent==base and
            args.snapshot.parent.name.startswith('.release-switch.') and not args.snapshot.exists(), 'schema016_recovery_snapshot')
        with args.snapshot.open('x') as out:
            os.chmod(args.snapshot,0o600);json.dump(active,out,sort_keys=True)
    return dict(status='schema016_forward_recovery_preflight_passed',recovery_mode=RECOVERY,
                schema=state,old_worker_allowed=False,schema_rollback=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("target", "preflight", "receipt", "capture", "native", "phase"))
    parser.add_argument("--candidate-manifest", required=True, type=Path)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument('--role',choices=('api','mcp','worker'))
    parser.add_argument('--phase',choices=('pre','post'))
    args = parser.parse_args(argv)
    try:
        manifest = gate.read_json(args.candidate_manifest)
        root = args.candidate_manifest.parent / "enterprise_agent_poc"
        base = root.parent.parent.parent
        gate.require(root == ROOT, "trusted_recovery_tooling_path")
        if 'dual_source_release' in manifest:
            result=dual_recovery(args,base,root,manifest)
            if args.mode=='target': print(result); return 0
            print(json.dumps(result,sort_keys=True)); return 0
        gate.require(args.mode not in {'native','phase'} and args.role is None and args.phase is None,
                     'schema016_versioned_entry_required')
        if args.mode == "target":
            declared(root, manifest)
            print(PREDECESSOR["release_id"])
            return 0
        if args.mode == "capture":
            preflight(base, root, manifest)
            gate.require(args.snapshot.name == "state.json" and args.snapshot.parent.parent == base
                         and args.snapshot.parent.name.startswith(".release-switch.")
                         and args.snapshot.parent.resolve() == args.snapshot.parent
                         and not args.snapshot.exists(), "recovery_snapshot_path")
            value = gate.read_json(receipt_path(base, manifest["release_id"]))
            # Guard against drift from the ORIGINAL commit boundary, not a
            # freshly accepted arbitrary Binding state during this recovery.
            with args.snapshot.open("x", encoding="utf-8") as f:
                os.chmod(args.snapshot, 0o600)
                json.dump(value["bindings"], f, sort_keys=True)
            result = {"status": "committed_binding_snapshot_captured"}
        else:
            result = preflight(base, root, manifest) if args.mode == "preflight" else receipt(base, root, manifest, args.snapshot)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        reason = str(exc) if isinstance(exc, gate.RollbackBlocked) else "runtime_recovery_input_or_io"
        print(json.dumps({"status": "BLOCKED", "check": reason}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
