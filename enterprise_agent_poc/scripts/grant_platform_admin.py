from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.product_store import ProductStore
from app.settings import settings
from app.skill_registry import SkillRegistry
from app.store import POCStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Grant Skill Registry platform-admin access to one existing user.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    store = POCStore(settings.database_url)
    product_store = ProductStore(store)
    product_store.initialize()
    registry = SkillRegistry(store, settings.data_dir / "skill-registry", ROOT / "skill_packages")
    registry.initialize()
    user = product_store.user_by_email(args.email.strip().lower())
    if not user:
        print(json.dumps({"status": "not_found", "email": args.email.strip().lower()}, ensure_ascii=False))
        return 2
    result = {"status": "ready" if args.execute else "dry_run", "user_id": user["id"], "email": user["email"], "tenant_id": user["tenant_id"]}
    if args.execute:
        registry.grant_platform_admin(user["id"])
        result["status"] = "granted"
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
