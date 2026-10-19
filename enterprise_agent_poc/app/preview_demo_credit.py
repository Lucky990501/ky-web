"""Owner-operated, one-time Preview demo-credit grant; not an HTTP endpoint.

No Provider budget change, no blanket anonymous-user grant, no ledger reset.
"""
import os
from uuid import uuid5,NAMESPACE_URL

AMOUNT=1000
REASON='owner_preview_ux_acceptance_credit_v1'


def grant_once(products,tenant_id,user_id):
    if os.environ.get('CUSTOMER_PREVIEW_MODE')!='true' or not tenant_id.startswith('preview-'):
        raise PermissionError('Only the verified Preview acceptance tenant may receive demo credits')
    identifier=str(uuid5(NAMESPACE_URL,REASON+':'+tenant_id))
    with products._store.connection() as conn:
        if not products._store.is_postgres:conn.execute('BEGIN IMMEDIATE')
        actor=conn.execute('select tenant_id,role,account_status from users where id=?',(user_id,)).fetchone()
        if not actor or actor['tenant_id']!=tenant_id or actor['role']!='enterprise_admin' or actor['account_status']!='enabled':raise PermissionError()
        account=conn.execute('select balance from credit_accounts where tenant_id=?',(tenant_id,)).fetchone()
        if not account:raise LookupError('Missing Preview credit account')
        old=conn.execute('select amount from credit_transactions where id=? and tenant_id=?',(identifier,tenant_id)).fetchone()
        if old:return {'transaction_id':identifier,'added':0,'balance':account['balance'],'already_applied':True}
        # Grant only to close the actual insufficient-credit admission failure.
        if account['balance']>=20:return {'added':0,'balance':account['balance'],'already_sufficient':True}
        conn.execute('update credit_accounts set balance=balance+?,updated_at=CURRENT_TIMESTAMP where tenant_id=?',(AMOUNT,tenant_id))
        conn.execute('insert into credit_transactions(id,tenant_id,user_id,task_id,amount,reason) values(?,?,?,NULL,?,?)',(identifier,tenant_id,user_id,AMOUNT,REASON))
        return {'transaction_id':identifier,'added':AMOUNT,'balance':account['balance']+AMOUNT,'already_applied':False}
