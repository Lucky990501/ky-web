"""Exact customer Test pilot operator. Does not issue technical authority."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import stat
import time

from app.settings import settings
from app.test_text_gate import RequestBudget, require, read_technical_scope



def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=('initialize','status','revoke'))
    parser.add_argument('--context',required=True)
    args=parser.parse_args()
    require(settings.environment=='test' and settings.test_text_gate_enabled
            and settings.test_text_gate_state is not None,'test_only_gate_required')
    context,scope_sha=read_technical_scope(args.context)
    customer=context['customer']; wechat=context['wechat']; owner=context['owner_business_approval']
    approved=datetime.fromisoformat(owner['approved_at_utc'].replace('Z','+00:00')).timestamp()
    owner_end=datetime.fromisoformat(owner['not_after_utc'].replace('Z','+00:00')).timestamp()
    require(owner_end<=approved+86400 and context['expires_at']<=owner_end,'owner_window_no_extension')
    budget=RequestBudget(settings.test_text_gate_state,settings.database_url)
    if args.action=='initialize':
        budget=RequestBudget.initialize(settings.test_text_gate_state,settings.database_url,
            customer['tenant_id'],context['operation_id'],context['expires_at'],
            member_id=customer['member_id'],operator_id=customer['operator_id'],
            wechat_agent_id=wechat['agent_id'],revision_id=wechat['revision_id'],
            fingerprint=wechat['fingerprint'],owner_approved_at=approved,
            owner_event_sha256=owner['original_event_sha256'],scope_sha256=scope_sha,context_path=args.context)
    with budget.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        identity=budget.scope(c)
        require(identity['scope_sha256']==scope_sha and identity['operation_id']==context['operation_id'],
                'pilot_current_scope_required')
        if args.action=='revoke':
            c.execute('UPDATE budget SET revoked=1 WHERE id=1')
        counts={kind:c.execute('SELECT count(*) FROM attempts WHERE kind=?',(kind,)).fetchone()[0]
                for kind in ('text','image')}
        totals=c.execute('SELECT COALESCE(sum(input_debit),0),COALESCE(sum(output_debit),0) FROM attempts').fetchone()
    print(json.dumps({'status':'REVOKED' if args.action=='revoke' else 'PILOT_BUDGET_VERIFIED',
        'operation_id':identity['operation_id'],'scope_sha256':scope_sha,'tenant_id':identity['tenant_id'],
        'limits':RequestBudget.LIMITS,'spent_http':counts,'debited_input_tokens':totals[0],
        'debited_output_tokens_including_reasoning':totals[1],'expires_at':identity['expires_at'],
        'provider_invoice_hard_cap':False}))


if __name__=='__main__':
    try: main()
    except Exception:
        print('{"status":"BLOCKED","reason":"pilot_context_or_budget_unavailable"}')
        raise SystemExit(2)
