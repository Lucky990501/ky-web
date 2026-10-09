"""Targeted real PG16.6 parity replay of the SHA-pinned 06 b34 probe.

Not a release/PRIMARY installer. Linux UID1000, separate user+net namespace,
synthetic marked PGDATA only. Uses formal runtime reservation APIs, real
transactions, actual child exits and PG stop/start; no Runtime Double/SQLite.
Authority/root-proof inputs are isolated emulation, NOT PRIMARY qualification.
--draft explicitly denotes adb plus the byte-pinned local tooling edits.
"""
from pathlib import Path
import argparse
import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

PROBE_SHA = '45b6b4762ce6796d552ee9b9feb4308fd044fcb433091eac1b5565020c138ebe'
BASE = 'adb99fbdfec50170de42cf118db6d5e588349770'
BASE_TREE = 'a49a0415b48603b1034bc8ddc52b1bc26239a6e6'
EDITS = ('scripts/exact_test_admin_lifecycle.py', 'scripts/wechat_runtime_test_lifecycle_guard.py',
    'scripts/verify_wechat_recovery_admission_postgres.py')
CASES = ('no_reservation', 'queued', 'failed_enqueue', 'normal_finish', 'ambiguous',
    'wrong_tenant', 'wrong_agent', 'wrong_revision', 'wrong_actor', 'wrong_scope',
    'wrong_source', 'no_proof', 'stale_proof', 'reservation_outside_lease',
    'start_outside_lease', 'expired_new_operation', 'crash_after_admission',
    'crash_after_delete', 'forged_admission', 'wrong_ticket', 'wrong_pre_test_ids',
    'failed_runtime', 'early_failure', 'profile_failure', 'early_failure_negatives', 'manager_startup')

BODY = '''        def rows_now(): return snapshot(store)
        anchors={name:copy.deepcopy(next(r for r in baseline[name] if
            r.get('id')==(agent if name=='agent_templates' else revision)
            or name=='tenant_agent_instances' and r['agent_id']==agent))
            for name in ('agent_templates','agent_template_versions','tenant_agent_instances')}
        pins={name:[guard.digest(r) for r in rows] for name,rows in baseline.items()}
        def predecessor(view):
            # Synthetic predecessor emulator, NOT the PRIMARY Root wrapper.
            # No permissive/no-op callback: all protected original rows stay
            # pinned, and only independently joined runtime tasks may appear.
            dynamic={'tasks','task_events','run_traces','conversations','conversation_owners',
                'task_results','messages','credit_transactions'}
            for table in set(baseline)-dynamic:
                assert sorted(guard.digest(r) for r in view[table])==sorted(pins[table]),table
            allowed={r['task_id'] for r in test_rows(rows_now())}
            assert all(r['id'] in allowed and (r['tenant_id'],r['user_id'],r['agent_id'])
                ==('tenant-a',actor,agent) for r in view['tasks'])
            return {'status':'PASS','evidence':'STRICT_SYNTHETIC_PREDECESSOR_NOT_PRIMARY_ROOT'}
        def check_now():
            data = rows_now()
            quality = guard.validate_records(data, runtime_scope, environment='test', source=guard.SOURCE, tree=guard.TREE)
            ledger = native.validate_ledger(data, scope)
            assert {r['id']:r['task_id'] for r in test_rows(data)} == ledger[1]
            assert not data['platform_admins'] and ledger[3] == 'revoked'
            assert not any(quality.values())  # Never manufacture model PASS.
            result=native.validate_snapshot(data,scope=scope,runtime_scope=runtime_scope,
                anchors=anchors,parent_pins=pins,ordinary_principals=[actor],predecessor_validate=predecessor)
            assert result['status']=='PASS' and result['passed_runtime_tests']==0
            return ledger
        def proof_now():
            with store.connection() as connection: h = admin._history(connection, scope)
            return dict(contract=admin_code.CONTRACT, run_id=scope['run_id'],
                application_source=CANDIDATE, application_tree=TREE,
                last_audit_sha256=admin_code.event_hash(h[-1][0]), tickets=sorted(admin_code.outstanding(h)),
                quiesced_process_ids=sorted({p['process_id'] for _,p in h if p['action']=='operation_started' and p['ticket'] in admin_code.outstanding(h)}),
                observed_at=datetime.now(timezone.utc).isoformat(),
                independent_approval='EXACT_API_PROCESS_QUIESCENCE_ATTESTED_BY_06')
        def recover(proof):
            with patch('app.test_tenant_seeding.native_json', lambda _path: (proof, 'isolated-proof-input')):
                return admin.revoke(**call, dead_operation_proof=admin_code.load_dead_operation_proof)
        def await_dead(child):
            waited, code = os.waitpid(child, 0)
            assert waited == child and code == 0
            try: os.kill(child, 0)
            except ProcessLookupError: return
            raise AssertionError('CHILD_NOT_QUIESCED')
        negative = {'ambiguous':'RESERVATION_COUNT','wrong_tenant':'TASK_OWNERSHIP',
            'wrong_agent':'TASK_OWNERSHIP','wrong_revision':'REVISION_SCOPE','wrong_actor':'TASK_OWNERSHIP',
            'wrong_scope':'CALLER_SCOPE_MISMATCH','wrong_source':'AUDIT_CHAIN_REJECTED',
            'no_proof':'RECOVERY_PROOF_REQUIRED','stale_proof':'RECOVERY_PROOF_REJECTED',
            'reservation_outside_lease':'RESERVATION_OUTSIDE_LEASE','start_outside_lease':'START_OUTSIDE_LEASE',
            'wrong_ticket':'RECOVERY_PROOF_REJECTED','wrong_pre_test_ids':'RECOVERY_PRE_TEST_IDS'}
        if args.case in {'normal_finish','early_failure','profile_failure','early_failure_negatives','manager_startup'}:
            response = client.post(endpoint,json={'configuration_fingerprint':runtime_scope['fingerprint']})
            assert response.status_code == 202
            created = response.json()
            if args.case=='manager_startup':
                from app.agent_execution import profile as context_profile
                context=next(r for r in rows_now()['agent_execution_contexts'])
                profile=context_profile(context)
                # Isolated local SDK startup ONLY. No turn/model request, no
                # real credential, no egress interface in this namespace.
                os.environ[settings.codex_api_key_env]='synthetic-invalid-no-provider-authority'
                async def startup_only():
                    try:
                        await asyncio.wait_for(manager.get(profile),30)
                        assert {'runtime_process_created','app_server_ready'} <= {e['event'] for e in manager.startup_events(profile)}
                    finally: await manager.close()
                asyncio.run(startup_only())
                os.environ.pop(settings.codex_api_key_env)
            if args.case in {'early_failure','profile_failure','early_failure_negatives'}:
                asyncio.run(tasks.execute(product.task_for_worker(created['task_id'])))
            assert admin.revoke(**call)['active_test_platform_admin'] == 0
        elif args.case == 'expired_new_operation':
            future = datetime.now(timezone.utc)+timedelta(hours=2)
            class FutureClock(datetime):
                @classmethod
                def now(cls,tz=None): return future
            with patch.object(admin_code,'datetime',FutureClock):
                try: admin.begin_operation(UserPrincipal(actor,'tenant-a','member'),'POST',endpoint,
                    {'configuration_fingerprint':runtime_scope['fingerprint']},scope['run_id'])
                except admin_code.Blocked as error: assert str(error)=='EXACT_ADMIN_LEASE_EXPIRED'
                else: raise AssertionError('EXPIRED_NEW_EXECUTION_ACCEPTED')
                assert admin.revoke(**call)['active_test_platform_admin']==0
        else:
            child = os.fork()
            if child == 0:
                try:
                    admin.begin_operation(UserPrincipal(actor,'tenant-a','member'),'POST',endpoint,
                        {'configuration_fingerprint':runtime_scope['fingerprint']},scope['run_id'])
                    if args.case != 'no_reservation':
                        if args.case == 'failed_enqueue':
                            def fail_queue(_task): raise RuntimeError('synthetic enqueue interruption')
                            tester.enqueue=fail_queue
                        try: created=asyncio.run(tester.run(agent,revision,actor,runtime_scope['fingerprint']))
                        except AgentCatalogError:
                            assert args.case=='failed_enqueue'
                        if args.case=='ambiguous':
                            asyncio.run(tester.run(agent,revision,actor,runtime_scope['fingerprint']))
                        if args.case=='failed_runtime':
                            asyncio.run(tasks.execute(product.task_for_worker(created['task_id'])))
                    os._exit(0)
                except BaseException: os._exit(2)
            await_dead(child)
            if args.case in {'wrong_tenant','wrong_agent','wrong_actor','wrong_revision','reservation_outside_lease','start_outside_lease'}:
                # Negative tampering in this disposable DB only. Positive test
                # records are ALWAYS created by AgentRuntimeTest.run().
                with store.connection() as connection:
                    row = test_rows(rows_now())[0]
                    if args.case=='wrong_tenant': connection.execute('UPDATE tasks SET tenant_id=? WHERE id=?',('tenant-b',row['task_id']))
                    elif args.case=='wrong_agent': connection.execute('UPDATE tasks SET agent_id=? WHERE id=?',('copywriting-agent',row['task_id']))
                    elif args.case=='wrong_actor':
                        other=product.create_user('tenant-a','negative-actor@example.invalid','synthetic-unused','Negative actor','member')
                        other_id=product.user_by_email('negative-actor@example.invalid')['id']
                        connection.execute('UPDATE tasks SET user_id=? WHERE id=?',(other_id,row['task_id']))
                    elif args.case=='wrong_revision':
                        second=catalog.create_version(agent,{'persona':'Negative different revision'},actor)
                        different=next(v['id'] for v in second['versions'] if v['id']!=revision)
                        connection.execute('UPDATE agent_template_tests SET agent_template_version_id=? WHERE id=?',(different,row['id']))
                    elif args.case=='reservation_outside_lease':
                        connection.execute('UPDATE agent_template_tests SET created_at=? WHERE id=?',(scope['expires_at'],row['id']))
                    else:
                        h=admin._history(connection,scope)
                        connection.execute('UPDATE execution_events SET created_at=? WHERE id=?',
                            ((datetime.fromisoformat(scope['issued_at'])-timedelta(seconds=1)).isoformat(),h[-1][0]['id']))
            if args.case=='wrong_pre_test_ids':
                with store.connection() as connection:
                    h=admin._history(connection,scope); row,payload=h[-1]
                    payload['pre_test_ids']=[str(uuid4())]
                    connection.execute('UPDATE execution_events SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
            proof=proof_now()
            before=rows_now()
            if args.case in negative:
                changed_call=dict(call)
                if args.case=='wrong_scope': changed_call['run_id']=str(uuid4())
                if args.case=='wrong_source': admin.authority_loader=lambda: dict(scope,application_source='e'*40,tooling_source='e'*40)
                if args.case=='stale_proof': proof['observed_at']=(datetime.now(timezone.utc)-timedelta(seconds=90)).isoformat()
                if args.case=='wrong_ticket': proof['tickets']=[str(uuid4())]
                try:
                    if args.case=='no_proof': admin.revoke(**changed_call)
                    else:
                        with patch('app.test_tenant_seeding.native_json',lambda _path:(proof,'isolated-proof-input')):
                            admin.revoke(**changed_call,dead_operation_proof=admin_code.load_dead_operation_proof)
                except admin_code.Blocked as error: assert negative[args.case] in str(error),str(error)
                else: raise AssertionError('UNPROVEN_RECOVERY_ACCEPTED')
                assert hashes(rows_now())==hashes(before)  # Rejection is atomic; owned admin stays for controlled cleanup.
                results=dict(expected_reject=negative[args.case],db_unchanged=True)
                print(json.dumps(dict(case=args.case,result='PASS',postgres='16.6',primary_changes=0,
                    production_changes=0,provider_calls=0,wechat_calls=0,image_calls=0,results=results)))
                return 0
            if args.case in {'crash_after_admission','crash_after_delete'}:
                recovery_child=os.fork()
                if recovery_child==0:
                    audit=admin._audit
                    def die(conn,scope_,action,history,**kw):
                        if args.case=='crash_after_delete' and action=='revoked': os._exit(0)
                        result=audit(conn,scope_,action,history,**kw)
                        if args.case=='crash_after_admission' and action=='operation_abandoned': os._exit(0)
                        return result
                    admin._audit=die
                    recover(proof)
                    os._exit(3)
                await_dead(recovery_child)
                assert hashes(rows_now())==hashes(before)
                proof=proof_now()
            assert recover(proof)['active_test_platform_admin']==0
            after=rows_now()
            assert after['tasks']==before['tasks'] and after['agent_template_tests']==before['agent_template_tests']
            if args.case=='forged_admission':
                corrupt=copy.deepcopy(after)
                event=next(r for r in corrupt['execution_events'] if r['event_type']==admin_code.EVENT
                    and json.loads(r['payload'])['action']=='operation_abandoned')
                payload=json.loads(event['payload']); payload['recovered_admission']['task_id']=str(uuid4())
                event['payload']=json.dumps(payload)
                # Negative copied fixture only: recompute a syntactically valid
                # chain to prove exact association is independently checked.
                following=next(r for r in corrupt['execution_events'] if r['event_type']==admin_code.EVENT and r['id']>event['id'])
                payload=json.loads(following['payload']); payload['previous_sha256']=admin_code.event_hash(event)
                following['payload']=json.dumps(payload)
                try: native.validate_ledger(corrupt,scope)
                except guard.Blocked as error: assert str(error)=='NATIVE_RECOVERED_ADMISSION_ASSOCIATION'
                else: raise AssertionError('FORGED_ADMISSION_ACCEPTED')
        if args.case in {'early_failure','profile_failure','early_failure_negatives'}:
            data=rows_now(); row=next(r for r in data['tasks'] if r['id']==created['task_id'])
            trace=next(r for r in data['run_traces'] if r['run_id']==row['run_id'])
            assert row['status']==trace['status']=='failed'
            assert not any(r['id']==row['conversation_id'] for r in data['conversations'])
            payload=guard.obj(trace['payload'])
            startup=[e['stage'] for e in payload['lifecycle_events'] if e['event']=='runtime_start_failed']
            assert startup==(['runtime_profile'] if args.case=='profile_failure' else ['provider_initialization']),startup
            assert not any(e['event']=='runtime_process_created' for e in payload['lifecycle_events'])
            quality_error=None
            check_now()
            results=dict(task_status=row['status'],run_status=trace['status'],conversation_exists=False,
                guard_error=quality_error,runtime_test_id=created['runtime_test_id'],task_id=row['id'],run_id=row['run_id'],
                startup_failure_stage=startup,provider_process_created=False)
            if args.case=='early_failure_negatives':
                rejected=[]
                def reject(label,change,expected):
                    altered=copy.deepcopy(data)
                    change(altered)
                    try: guard.validate_records(altered,runtime_scope,environment='test',source=guard.SOURCE,tree=guard.TREE)
                    except guard.Blocked as error: assert expected in str(error),(label,str(error))
                    else: raise AssertionError('UNSAFE_FAILURE_ACCEPTED:'+label)
                    rejected.append(label)
                def task_change(d,**kw): d['tasks'][0].update(kw)
                def payload_change(d,**kw):
                    p=guard.obj(d['run_traces'][0]['payload']);p.update(kw);d['run_traces'][0]['payload']=json.dumps(p)
                reject('normal_running_missing_conversation',lambda d:task_change(d,status='running'),'PRE_THREAD_FAILURE_STATE')
                reject('completed_missing_conversation',lambda d:task_change(d,status='completed'),'PRE_THREAD_FAILURE_STATE')
                reject('forged_failure_code',lambda d:task_change(d,error_code='runtime_error'),'PRE_THREAD_FAILURE_STATE')
                reject('wrong_tenant',lambda d:task_change(d,tenant_id='tenant-b'),'TASK_OWNERSHIP')
                reject('wrong_actor',lambda d:task_change(d,user_id='other'),'TASK_OWNERSHIP')
                reject('wrong_revision',lambda d:d['agent_template_tests'][-1].update(agent_template_version_id='other'),'REVISION_SCOPE')
                reject('wrong_trace_context',lambda d:payload_change(d,execution_context_id='other'),'PRE_THREAD_FAILURE_IDENTITY')
                reject('missing_startup_evidence',lambda d:payload_change(d,lifecycle_events=[]),'PRE_THREAD_STARTUP_EVIDENCE')
                reject('post_thread_failure',lambda d:payload_change(d,lifecycle_events=[{'event':'thread_started'}]),'PRE_THREAD_STARTUP_EVIDENCE')
                reject('fake_runtime_pass',lambda d:payload_change(d,runtime_completed=True),'PRE_THREAD_NO_EXECUTION')
                reject('real_tool_side_effect',lambda d:payload_change(d,tool_calls=[{'name':'x'}]),'PRE_THREAD_NO_EXECUTION')
                reject('missing_terminal_timestamp',lambda d:task_change(d,completed_at=None),'PRE_THREAD_CHRONOLOGY')
                reject('missing_transition_evidence',lambda d:d.update(task_events=[]),'PRE_THREAD_TASK_TRANSITIONS')
                reject('persisted_result',lambda d:d['task_results'].append({'task_id':row['id']}),'PRE_THREAD_NO_PERSISTED_SIDE_EFFECTS')
                reject('persisted_image',lambda d:d['generations'].append({'task_id':row['id']}),'PRE_THREAD_NO_PERSISTED_SIDE_EFFECTS')
                reject('conflicting_owner',lambda d:d['conversation_owners'].append({'conversation_id':row['conversation_id'],'user_id':'other'}),'PRE_THREAD_NO_PERSISTED_SIDE_EFFECTS')
                def cross_conversation(d):
                    d['conversations'].append({'id':row['conversation_id'],'tenant_id':'tenant-b','agent_id':agent,'runtime_profile_id':context_id_profile[1]})
                    d['conversation_agent_contexts'].append({'conversation_id':row['conversation_id'],'context_id':context_id_profile[0]})
                context_id_profile=(data['agent_execution_contexts'][0]['id'],data['agent_execution_contexts'][0]['runtime_profile_id'])
                reject('cross_tenant_conversation',cross_conversation,'CONVERSATION_OWNERSHIP')
                corrupt=copy.deepcopy(data)
                corrupt['execution_events']=[r for r in corrupt['execution_events'] if r['event_type']!=admin_code.EVENT]
                try: native.validate_snapshot(corrupt,scope=scope,runtime_scope=runtime_scope,
                    anchors=anchors,parent_pins=pins,ordinary_principals=[actor],predecessor_validate=predecessor)
                except guard.Blocked: rejected.append('unauthorized_failure_no_admission')
                else: raise AssertionError('UNAUTHORIZED_FAILURE_ACCEPTED')
                for label,publish_data in [('failed_cannot_publish',data),('no_evidence_cannot_publish',dict(data,agent_template_tests=[r for r in data['agent_template_tests'] if r['test_type']!='runtime'],task_agent_contexts=[]))]:
                    try: guard.publication_eligibility(publish_data,dict(runtime_scope,publication_allowed=True),resolver=resolver,connection=None)
                    except guard.Blocked as error: assert str(error)=='RUNTIME_REAL_PASS_REQUIRED_FOR_PUBLISH'
                    else: raise AssertionError('FALSE_PUBLISH_ELIGIBILITY')
                    rejected.append(label)
                try: f.wechat_skill.execute('CREATE_DRAFT',root,root/'unused.json',upload_requested=True)
                except f.wechat_skill.WechatSkillError as error: assert str(error)=='WECHAT_SECRET_AND_EGRESS_CONTRACT_REQUIRED'
                else: raise AssertionError('CREATE_DRAFT_ENABLED')
                rejected.append('create_draft_still_disabled')
                results['negative_checks']=rejected
                assert len(rejected)==21
        else:
            quality_error=None
            ledger=check_now()
            evidence_before=hashes(rows_now())
            assert admin.revoke(**call)['active_test_platform_admin']==0
            assert hashes(rows_now())==evidence_before
            assert client.get('/api/v1/platform/agents/'+agent).status_code==403
            results=dict(admissions=ledger[1],admin_state=ledger[3],admins=0,repeat_recover_idempotent=True,
                post_revoke_permission='REJECT',passed_runtime_tests=0)
            if args.case=='manager_startup':
                results['real_manager_startup']='APP_SERVER_READY_SKILL_DISCOVERED'
                results['model_turns']=0
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--host-net', required=True)
    parser.add_argument('--case', choices=CASES, required=True)
    parser.add_argument('--draft', action='store_true')
    args = parser.parse_args()
    assert os.name == 'posix' and os.geteuid() == 1000 and sys.dont_write_bytecode
    assert os.environ.get('PYTHONDONTWRITEBYTECODE') == '1'
    assert os.readlink('/proc/self/ns/net') != args.host_net
    subprocess.run(['/usr/sbin/ip','link','set','lo','up'],check=True)
    assert hashlib.sha256(args.probe.read_bytes()).hexdigest() == PROBE_SHA
    git = ['git','-C',str(args.candidate)]
    identity = subprocess.check_output(git+['rev-parse','HEAD','HEAD^{tree}'],text=True).splitlines()
    changed = subprocess.check_output(git+['diff','--name-only','HEAD'],text=True).splitlines()
    if args.draft:
        assert identity == [BASE,BASE_TREE]
        assert set(changed) <= {'enterprise_agent_poc/'+name for name in EDITS}
    else:
        assert not changed and subprocess.check_output(git+['rev-parse','HEAD^'],text=True).strip() == BASE
    source_root=Path(tempfile.mkdtemp(prefix='wechat-recovery-source-'))
    assert source_root.parent == Path('/tmp')
    git_env={k:v for k,v in os.environ.items() if k not in {'GIT_DIR','GIT_WORK_TREE'}}
    candidate=source_root/'candidate'
    subprocess.run(['git','clone','--shared','--no-checkout',os.environ['GIT_COMMON_SOURCE'],str(candidate)],
        env=git_env,check=True,stdout=subprocess.DEVNULL)
    subprocess.run(['git','-C',str(candidate),'checkout','--detach',identity[0]],env=git_env,check=True,stdout=subprocess.DEVNULL)
    if args.draft:
        for relative in EDITS: shutil.copyfile(args.candidate/'enterprise_agent_poc'/relative,candidate/'enterprise_agent_poc'/relative)
    pins={name:hashlib.sha256((candidate/'enterprise_agent_poc'/name).read_bytes()).hexdigest() for name in EDITS}
    os.environ.pop('GIT_DIR',None);os.environ.pop('GIT_WORK_TREE',None)
    root=Path(tempfile.mkdtemp(prefix='wechat-recovery-pg-'))
    try:
        text=args.probe.read_text()
        text=text.replace('    git_identity(candidate)\n','')
        text=text.replace('    assert candidate == Path("/opt/enterprise-agent-workbench-test/shared/source-qualifications/wechat-b34-original-status-v1")\n','')
        text=text.replace('    assert args.isolation_root.resolve() == Path("/opt/enterprise-agent-workbench-test/tmp/b34-recovery-isolated-v1")\n','')
        text=text.replace('choices=("normal", "interrupted")','choices='+repr(CASES))
        text=text.replace('CANDIDATE = "b34e62cfb1a8567db89f823e9191fad360fb4088"','CANDIDATE = "'+identity[0]+'"')
        text=text.replace('TREE = "dcecf7e53023e054707e0e7142325025b129a206"','TREE = "'+identity[1]+'"')
        text=text.replace('"credit_transactions", "execution_events", "enterprise_configs",',
            '"credit_transactions", "execution_events", "enterprise_configs", "task_events", "generations",')
        old='        runtime = CodexRuntimeProvider(None)  # No manager: controlled local startup failure, never Provider or Runtime Double.'
        new='''        from app.runtime.codex_provider import CodexRuntimeManager
        from app.security import RuntimeTokenIssuer
        from app.skills import SkillDeployment
        settings.codex_api_key_env='WECHAT_RECOVERY_ISOLATED_ABSENT_KEY'
        assert settings.codex_api_key_env not in os.environ
        settings.model_base_url='http://127.0.0.1:9/disabled'
        settings.model_wire_api='responses'
        settings.platform_mcp_url='http://127.0.0.1:9/disabled'
        manager=CodexRuntimeManager(settings,SkillDeployment(registry.published_root),RuntimeTokenIssuer('synthetic-isolated-runtime-key'))
        runtime=CodexRuntimeProvider(manager)
        if args.case=='profile_failure':
            # Real filesystem precondition failure before any process/credential.
            settings.data_dir.mkdir(parents=True,exist_ok=True)
            (settings.data_dir/'runtime').write_text('synthetic non-directory')'''
        assert old in text
        text=text.replace(old,new)
        text=text.replace('        assert admin.prepare(**call)["status"] == "REVOKE_PROVEN_BEFORE_GRANT"',
            '        baseline=snapshot(store)\n        assert admin.prepare(**call)["status"] == "REVOKE_PROVEN_BEFORE_GRANT"')
        begin=text.index('        if args.case == "normal":\n')
        end=text.index('        before_restart = snapshot(store)\n',begin)
        text=text[:begin]+BODY+text[end:]
        begin=text.index('        if args.case == "normal":\n',text.index('        before_restart = snapshot(store)\n'))
        end=text.index('        print(json.dumps(',begin)
        text=text[:begin]+'''        assert restart_record_error==quality_error
        check_now()
        results['guard_before_and_after_pg_restart']='PASS'
'''+text[end:]
        namespace={'__name__':'__main__'}
        sys.argv=[str(args.probe),'--candidate',str(candidate),'--case',args.case,
            '--isolation-root',str(root),'--host-net',args.host_net]
        try: exec(compile(ast.parse(text),str(args.probe),'exec'),namespace)
        except SystemExit as outcome:
            if outcome.code not in (0,None): raise
        print(json.dumps(dict(case=args.case,status='PROBE_ASSERTIONS_PASS',source=identity[0],tree=identity[1],
            draft=args.draft,code_sha256=pins,root_proof='ISOLATED_INPUT_EMULATION_NOT_PRIMARY',
            native_full_entry='NOT_PRIMARY_REVERIFIED',runtime_double=False,sqlite=False)))
    finally:
        if root.exists() and not any(root.iterdir()): root.rmdir()
        assert source_root.resolve().parent==Path('/tmp') and source_root.name.startswith('wechat-recovery-source-')
        shutil.rmtree(source_root)


if __name__=='__main__': main()
