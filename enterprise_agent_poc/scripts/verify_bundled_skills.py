"""Read-only Release gate. Never call app.main, initialize, seed, or publish."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.skill_registry import SkillRegistry
from app.store import POCStore


def main() -> int:
    database_url = os.environ.get("ENTERPRISE_POC_DATABASE_URL", f"sqlite:///{ROOT / '.runtime-data' / 'poc.db'}")
    data_root = Path(os.environ.get("ENTERPRISE_POC_DATA_DIR", str(ROOT / ".runtime-data")))
    if not data_root.is_absolute():
        data_root = ROOT / data_root
    registry = SkillRegistry(POCStore(database_url), data_root / "skill-registry", ROOT / "skill_packages")
    try:
        result = registry.verify_bootstrap()
    except Exception:
        # DB driver errors may contain credentials/DSNs. Never print exceptions.
        print(json.dumps({"status": "BLOCKED", "check": "bundled_source_artifact_registry_cache_binding"}))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
