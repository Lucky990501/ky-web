"""Read-only Release gate. Never call app.main, initialize, seed, or publish."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.skill_registry import SkillRegistry
from app.bundled_skills import sha256
from app.store import POCStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--candidate-release-manifest", type=Path)
    parser.add_argument("--predecessor-release-manifest", type=Path)
    args = parser.parse_args(argv)
    try:
        production = os.environ.get("APP_ENV", "development") == "production"
        if production and args.data_dir is None:
            print(json.dumps({"status": "BLOCKED", "check": "explicit_production_data_dir"}))
            return 2
        data_root = args.data_dir or Path(os.environ.get("ENTERPRISE_POC_DATA_DIR", str(ROOT / ".runtime-data")))
        if args.data_dir is not None:
            if not data_root.is_absolute() or not data_root.is_dir() or data_root.resolve().is_relative_to(ROOT.resolve()):
                print(json.dumps({"status": "BLOCKED", "check": "data_dir_input"}))
                return 2
        elif not data_root.is_absolute():
            data_root = ROOT / data_root
        database_url = os.environ.get("ENTERPRISE_POC_DATABASE_URL", f"sqlite:///{ROOT / '.runtime-data' / 'poc.db'}")
        registry = SkillRegistry(POCStore(database_url), data_root / "skill-registry", ROOT / "skill_packages")
        if bool(args.candidate_release_manifest) != bool(args.predecessor_release_manifest):
            raise ValueError("transition manifests must be paired")
        if args.candidate_release_manifest:
            from scripts.release_binding_transition import candidate_packages, declaration, load_json, preflight
            candidate = load_json(args.candidate_release_manifest)
            if "binding_transition" in candidate:
                predecessor = load_json(args.predecessor_release_manifest)
                transitions = declaration(candidate, predecessor, sha256(args.predecessor_release_manifest.read_bytes()))
                result = preflight(registry, transitions, candidate_packages(ROOT / "skill_packages"))
            else:
                result = registry.verify_bootstrap()
        else:
            result = registry.verify_bootstrap()
    except Exception:
        # DB driver errors may contain credentials/DSNs. Never print exceptions.
        print(json.dumps({"status": "BLOCKED", "check": "bundled_source_artifact_registry_cache_binding"}))
        return 2
    print(json.dumps({**result, "data_dir_resolved": True}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
