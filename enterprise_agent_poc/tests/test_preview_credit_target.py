"""Zero-cost isolated SQLite and actual ASGI new-session fixtures; no Provider."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.preview_credit_target import (
    EXISTING_TENANTS_OPERATION, NEW_VISITOR_OPERATION, top_up_preview_credits,
)
from app.store import POCStore
from app.product_store import ProductStore


@pytest.fixture
def credits(tmp_path, monkeypatch):
    monkeypatch.setenv("CUSTOMER_PREVIEW_MODE", "true")
    store = POCStore(tmp_path / "customer-ai-preview-data" / "preview.sqlite")
    store.initialize()
    products = ProductStore(store)
    products.initialize()
    with store.connection() as conn:
        for tenant, balance in [("preview-low", 2), ("preview-zero", 0),
                                ("preview-near", 99999), ("preview-exact", 100000),
                                ("preview-high", 100001), ("production-fixture", 3)]:
            conn.execute("INSERT INTO tenants VALUES (?,?,?)", (tenant, "FIXTURE", tenant))
            conn.execute("INSERT INTO credit_accounts(tenant_id,balance) VALUES (?,?)", (tenant, balance))
            conn.execute("INSERT INTO credit_transactions(id,tenant_id,amount,reason) VALUES (?,?,?,?)",
                         (tenant, tenant, -3, "historical_fixture_deduction"))
    return store, products


def test_exact_target_scope_audit_and_old_deductions_preserved(credits):
    store, _ = credits
    with store.connection() as conn:
        old = [tuple(r) for r in conn.execute("SELECT * FROM credit_transactions ORDER BY id")]
    rows = top_up_preview_credits(store, operation=EXISTING_TENANTS_OPERATION)
    assert len(rows) == 5 and sum(r["added"] > 0 for r in rows) == 3
    assert {r["tenant_id"]: r["added"] for r in rows} == {
        "preview-low": 99998, "preview-zero": 100000, "preview-near": 1,
        "preview-exact": 0, "preview-high": 0}
    with store.connection() as conn:
        assert [tuple(r) for r in conn.execute("SELECT * FROM credit_transactions WHERE reason='historical_fixture_deduction' ORDER BY id")] == old
        assert conn.execute("SELECT balance FROM credit_accounts WHERE tenant_id='production-fixture'").fetchone()[0] == 3
        for row in rows:
            receipt = conn.execute("SELECT amount,reason FROM credit_transactions WHERE id=?", (row["transaction_id"],)).fetchone()
            assert tuple(receipt) == (row["added"], EXISTING_TENANTS_OPERATION)


def test_repeat_does_not_refill_after_normal_deduction(credits):
    store, _ = credits
    top_up_preview_credits(store, operation=EXISTING_TENANTS_OPERATION)
    with store.connection() as conn:
        conn.execute("UPDATE credit_accounts SET balance=balance-8 WHERE tenant_id IN ('preview-low','preview-high')")
        count = conn.execute("SELECT count(*) FROM credit_transactions").fetchone()[0]
    again = top_up_preview_credits(store, operation=EXISTING_TENANTS_OPERATION)
    assert all(r["already_applied"] and r["added"] == 0 for r in again)
    assert {r["tenant_id"]: r["balance"] for r in again}["preview-low"] == 99992
    with store.connection() as conn:
        assert conn.execute("SELECT count(*) FROM credit_transactions").fetchone()[0] == count


@pytest.mark.parametrize("invalid", ["environment", "path", "operation", "tenant"])
def test_fail_closed_scope(credits, monkeypatch, invalid):
    store, _ = credits
    options = {"operation": EXISTING_TENANTS_OPERATION}
    if invalid == "environment":
        monkeypatch.delenv("CUSTOMER_PREVIEW_MODE")
    elif invalid == "path":
        store = POCStore(store.database_path.with_name("production.sqlite"))
    elif invalid == "operation":
        options["operation"] = "unknown"
    else:
        options["tenant_id"] = "production-fixture"
    with pytest.raises(PermissionError):
        top_up_preview_credits(store, **options)


def test_shared_create_user_default_stays_200(credits):
    store, products = credits
    with store.connection() as conn:
        conn.execute("INSERT INTO tenants VALUES ('preview-new','FIXTURE','new')")
    products.create_user("preview-new", "new@fixture.invalid", "FIXTURE", "FIXTURE", "enterprise_admin")
    with store.connection() as conn:
        assert conn.execute("SELECT balance FROM credit_accounts WHERE tenant_id='preview-new'").fetchone()[0] == 200
    result = top_up_preview_credits(store, operation=NEW_VISITOR_OPERATION, tenant_id="preview-new")
    assert len(result) == 1 and result[0]["added"] == 99800 and result[0]["balance"] == 100000


def test_actual_preview_new_session_and_four_agent_credit_admission(tmp_path):
    app_path = Path(__file__).resolve().parents[1]
    probe = r'''
import asyncio,sys
from pathlib import Path
import httpx
from scripts.customer_preview import create_preview
app=create_preview(Path(sys.argv[1])/'customer-ai-preview-data',local_http=True,
    credentials={'DEEPSEEK_API_KEY':'FIXTURE_ONLY_NOT_A_KEY'},pricing={})
from app import main
from app.preview_credit_target import NEW_VISITOR_OPERATION
async def run():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://127.0.0.1') as client:
        assert (await client.get('/preview/')).status_code==307
        actor=(await client.get('/preview/api/v1/me')).json()
        with main.store.connection() as conn:
            assert conn.execute('SELECT balance FROM credit_accounts WHERE tenant_id=?',(actor['tenant_id'],)).fetchone()[0]==100000
            assert conn.execute('SELECT amount FROM credit_transactions WHERE tenant_id=? AND reason=?',(actor['tenant_id'],NEW_VISITOR_OPERATION)).fetchone()[0]==99800
        for agent in ['copywriting-agent','campaign-agent','preview-wechat-agent','image-agent']:
            task=main.product_store.create_task(actor['tenant_id'],actor['user_id'],agent,'FIXTURE_ONLY no dispatch',None)
            assert task['id'] and task['status']=='queued'
        # No manager dispatch/Provider; create_task still enforces real credit admission.
        with main.store.connection() as conn:
            conn.execute('UPDATE credit_accounts SET balance=0 WHERE tenant_id=?',(actor['tenant_id'],))
        try:main.product_store.create_task(actor['tenant_id'],actor['user_id'],'campaign-agent','FIXTURE',None)
        except ValueError as exc:assert str(exc)=='insufficient_credit'
        else:raise AssertionError('Normal credit check disabled')
        await client.get('/preview/')
        with main.store.connection() as conn:
            assert conn.execute('SELECT balance FROM credit_accounts WHERE tenant_id=?',(actor['tenant_id'],)).fetchone()[0]==0
    print('FIXTURE_CREDIT_SESSION_ADMISSION_PASS real_provider_calls=0')
asyncio.run(run())
'''
    env = {**os.environ, "PYTHONPATH": str(app_path) + os.pathsep + os.environ.get("PYTHONPATH", ""),
           "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run([sys.executable, "-c", probe, str(tmp_path)], cwd=app_path,
                            env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=50)
    assert result.returncode == 0, result.stdout[-4000:]
    assert "FIXTURE_CREDIT_SESSION_ADMISSION_PASS" in result.stdout
