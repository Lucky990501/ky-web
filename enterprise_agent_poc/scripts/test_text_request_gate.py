"""Local Test operator only; no HTTP endpoint, credential input or Provider call."""
import argparse
import json
import time
from pathlib import Path

from app.settings import settings
from app.test_text_gate import RequestBudget, TestTextGateDenied, require
from app.store import POCStore


def main():
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=("initialize", "allow-task", "status", "revoke"))
    p.add_argument("--operation-id")
    p.add_argument("--tenant-id")
    p.add_argument("--task-id")
    p.add_argument("--valid-seconds", type=int, default=3600)
    args = p.parse_args()
    require(settings.environment == "test" and settings.test_text_gate_enabled
            and settings.test_text_gate_state and settings.test_text_gate_state.is_absolute(), "test_only_gate_required")
    path = settings.test_text_gate_state
    if args.action == "initialize":
        RequestBudget.initialize(path,settings.database_url,args.tenant_id,args.operation_id,time.time()+args.valid_seconds)
    budget = RequestBudget(path, settings.database_url)
    if args.action == "allow-task":
        with POCStore(settings.database_url).connection() as c:
            row = c.execute("SELECT * FROM tasks WHERE id=?", (args.task_id,)).fetchone()
        require(row is not None, "task_not_accepted")
        budget.allow(dict(row))
    elif args.action == "revoke":
        with budget.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("UPDATE budget SET revoked=1 WHERE id=1")
    with budget.connect() as c:
        b = dict(c.execute("SELECT * FROM budget WHERE id=1").fetchone())
        tasks = [dict(r) for r in c.execute("SELECT * FROM allowed_tasks ORDER BY task_id")]
    print(json.dumps({"operation_id":b["operation_id"], "tenant_id":b["tenant_id"], "max_requests":2,
                      "spent":b["spent"], "revoked":bool(b["revoked"]), "allowed_tasks":tasks}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print('{"status":"BLOCKED","reason":"test_gate_unavailable_or_denied"}')
        raise SystemExit(2)
