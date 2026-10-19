"""Owner-approved virtual credits, exclusively for independent Preview SQLite.

Explicit batch operation or new visitor initialization, never a startup refill.
Provider budgets and normal credit deductions are intentionally untouched.
"""
import os
from uuid import NAMESPACE_URL, uuid5

PREVIEW_CREDIT_TARGET = 100000
EXISTING_TENANTS_OPERATION = "owner_preview_existing_credit_target_100000_v1"
NEW_VISITOR_OPERATION = "preview_new_visitor_credit_target_100000_v1"


def top_up_preview_credits(store, *, operation, tenant_id=None):
    path = store.database_path
    if (os.environ.get("CUSTOMER_PREVIEW_MODE") != "true" or store.is_postgres
            or path is None or path.name != "preview.sqlite"
            or path.parent.name != "customer-ai-preview-data"):
        raise PermissionError("Independent Preview SQLite required")
    if operation not in (EXISTING_TENANTS_OPERATION, NEW_VISITOR_OPERATION):
        raise PermissionError("Unknown Preview credit authorization")
    if (tenant_id is not None and not tenant_id.startswith("preview-")) or (
            operation == NEW_VISITOR_OPERATION and tenant_id is None):
        raise PermissionError("Preview tenant required")
    results = []
    with store.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        query = "SELECT id FROM tenants WHERE substr(id,1,8)='preview-'"
        tenants = conn.execute(query + (" AND id=?" if tenant_id else "") + " ORDER BY id",
                               (tenant_id,) if tenant_id else ()).fetchall()
        if tenant_id and not tenants:
            raise LookupError("Preview tenant not found")
        for tenant in tenants:
            current = tenant["id"]
            identifier = str(uuid5(NAMESPACE_URL, operation + ":" + current))
            conn.execute("INSERT OR IGNORE INTO credit_accounts(tenant_id,balance) VALUES (?,0)", (current,))
            before = conn.execute("SELECT balance FROM credit_accounts WHERE tenant_id=?", (current,)).fetchone()["balance"]
            prior = conn.execute("SELECT id FROM credit_transactions WHERE id=?", (identifier,)).fetchone()
            added = 0 if prior else max(0, PREVIEW_CREDIT_TARGET - before)
            if not prior:
                if added:
                    conn.execute("UPDATE credit_accounts SET balance=balance+?,updated_at=CURRENT_TIMESTAMP WHERE tenant_id=?", (added, current))
                # Zero-delta receipts also make already-sufficient accounts one-shot.
                conn.execute("INSERT INTO credit_transactions(id,tenant_id,user_id,task_id,amount,reason) VALUES (?,?,NULL,NULL,?,?)",
                             (identifier, current, added, operation))
            results.append({"tenant_id": current, "before": before, "added": added,
                            "balance": before + added, "transaction_id": identifier,
                            "already_applied": bool(prior)})
    return results
