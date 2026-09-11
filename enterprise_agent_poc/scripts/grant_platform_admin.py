from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.settings import settings
from app.skill_registry import SkillRegistry
from app.store import POCStore


def grant_platform_admin_access(store: POCStore, registry: SkillRegistry, email: str, execute: bool) -> tuple[int, dict]:
    normalized_email = email.strip().lower()
    with store.connection() as conn:
        row = conn.execute(
            "SELECT u.id,u.email,u.tenant_id,u.role,t.name AS tenant_name "
            "FROM users u JOIN tenants t ON t.id=u.tenant_id WHERE LOWER(u.email)=LOWER(?)",
            (normalized_email,),
        ).fetchone()
    if not row:
        return 2, {"status": "not_found", "email": normalized_email}

    user = dict(row)
    result = {
        "status": "dry_run",
        "user_id": user["id"],
        "email": user["email"],
        "tenant_id": user["tenant_id"],
        "tenant_name": user["tenant_name"],
        "current_role": user["role"],
    }
    if execute:
        # Deliberately do not initialize schemas here. Production migration 005
        # must already exist; otherwise the grant fails instead of mutating an
        # unprepared database through an administrative helper.
        registry.grant_platform_admin(user["id"])
        result["status"] = "granted"
    return 0, result


def main() -> int:
    parser = argparse.ArgumentParser(description="Grant Skill Registry platform-admin access to one existing user.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    store = POCStore(settings.database_url)
    registry = SkillRegistry(store, settings.data_dir / "skill-registry", ROOT / "skill_packages")
    exit_code, result = grant_platform_admin_access(store, registry, args.email, args.execute)
    print(json.dumps(result, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
