"""Exact016 adapter of existing manifest/migration/native/recovery gates.

No grant, publish, effect replay, down migration or business-data cleanup.
Catalog pins are independently approved for the real target DB, not borrowed
from a Test fixture. Every Action row is read and checked, including UNKNOWN.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urlparse

from scripts.release_dual_source import APP_SOURCE, APP_TREE, VERSION, require, digest

RECOVERY = 'SCHEMA016_FORWARD_COMPATIBLE_RUNTIME_V1'
MIGRATION = dict(version='016', filename='016_wechat_draft_operations.sql',
    git_blob='80bee5380e0922146b171f88df62a15179075718',
    canonical_sha256='6d36dd3bcb9c0fbaed638a6fa29853a587a6146e61e511b6cc105de87b1c353a')
PREDECESSOR = dict(release_id='20261007-562f201-reference-dependency-retry-v1',
    source_commit='562f201e362f0b10ae4fe3a8dae9a8c4d9ef91d3',
    source_tree='e5205965e57fce6ce2b65dd747a110d00f7f1b1a',
    archive_sha256='17f31cb139ef9a619e455e5999b407fc7cfd98b45c3fd3757912b16a2da39941',
    raw_manifest_sha256='eb6d382b3bc0b720d54349293d4f2afe73f372c8fd3838beb3eb86d557a9811c',
    manifest_sha256='f99d02e36e33e2d0df20127019d019c52f93b5fdb26ebd0105c306c2ae93968a')
TABLES = sorted(('schema_migrations tenants enterprise_configs knowledge_documents assets users '
    'credit_accounts credit_transactions conversations conversation_owners messages tasks task_events '
    'task_results generations knowledge_bases knowledge_files asset_metadata execution_events run_traces '
    'gate2_checks agent_templates tenant_agent_instances knowledge_chunks skills skill_versions skill_packages '
    'agent_skill_bindings platform_admins embedding_index_profiles knowledge_chunk_embeddings '
    'agent_template_versions agent_template_version_skills tool_capabilities agent_template_version_tools '
    'agent_template_tests agent_execution_contexts conversation_agent_contexts task_agent_contexts '
    'platform_compatibility_state agent_release_operations agent_release_artifacts agent_release_events '
    'chat_image_attachments wechat_draft_operations').split())
PLAN = dict(schema_version=3, from_schema='015', target_schema='016', migrations=[MIGRATION],
    exact_predecessor=PREDECESSOR, target_table_set=TABLES, data_contract='member_account_status_v1',
    rollback_strategy='forward_only_compatible_runtime', recovery_contract=RECOVERY,
    destructive_down_migration=False, action_execution_contract='WECHAT_CREATE_DRAFT_ACTION_CAPABILITY_V2_TEST_ONLY')


def declaration(tooling_source, tooling_tree):
    require(all(isinstance(x,str) and re.fullmatch('[a-f0-9]{40}',x)
                for x in (tooling_source,tooling_tree)), 'dual_source_manifest_git')
    return dict(contract=VERSION, application_source=APP_SOURCE, application_tree=APP_TREE,
        tooling_source=tooling_source, tooling_tree=tooling_tree, recovery_contract=RECOVERY)


def ledger(conn, root):
    from scripts import migrate
    rows = [dict(r) for r in conn.execute('SELECT version,name,checksum FROM schema_migrations ORDER BY version')]
    require(len(rows) in {15,16} and [r['version'] for r in rows]==[f'{n:03}' for n in range(1,len(rows)+1)],
            'schema016_unknown_or_pending_history')
    paths=sorted((root/'migrations/postgres').glob('*.sql'))
    require(len(paths)==16 and [p.name[:3] for p in paths]==[f'{n:03}' for n in range(1,17)], 'schema016_source_inventory')
    raw=paths[-1].read_bytes().replace(b'\r\n',b'\n').replace(b'\r',b'\n')
    require(hashlib.sha256(raw).hexdigest()==MIGRATION['canonical_sha256'] and
        hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==MIGRATION['git_blob'], 'schema016_migration_identity')
    for row,path in zip(rows,paths):
        require(row['name']==path.name[4:] and migrate.compatibility_status(row['checksum'],
            migrate.migration_checksums(path)) in {migrate.EXACT_MATCH,migrate.LEGACY_LINE_ENDING_COMPATIBLE},
            'schema016_history_checksum')
    return rows


CATALOG = {
    'tables': "SELECT c.relname,c.relkind,c.relrowsecurity,c.relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f') ORDER BY 1",
    'columns': "SELECT c.relname,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,pg_get_expr(d.adbin,d.adrelid),a.attidentity,a.attgenerated FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f') AND a.attnum>0 AND NOT a.attisdropped ORDER BY 1,2",
    'constraints': "SELECT t.relname,c.conname,c.contype,pg_get_constraintdef(c.oid,true),c.convalidated,c.condeferrable,c.condeferred FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname='public' ORDER BY 1,2",
    'indexes': "SELECT t.relname,ic.relname,pg_get_indexdef(i.indexrelid),i.indisvalid,i.indisready FROM pg_index i JOIN pg_class t ON t.oid=i.indrelid JOIN pg_class ic ON ic.oid=i.indexrelid JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname='public' ORDER BY 1,2",
    'triggers': "SELECT c.relname,t.tgname,pg_get_triggerdef(t.oid,true),t.tgenabled FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND NOT t.tgisinternal ORDER BY 1,2",
    'functions': "SELECT p.proname,pg_get_function_identity_arguments(p.oid),pg_get_functiondef(p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.prokind='f' AND NOT EXISTS(SELECT 1 FROM pg_depend d WHERE d.classid='pg_proc'::regclass AND d.objid=p.oid AND d.deptype='e') ORDER BY 1,2",
    'extensions': 'SELECT extname,extversion FROM pg_extension ORDER BY 1',
    'sequences': "SELECT schemaname,sequencename,data_type,start_value,min_value,max_value,increment_by,cycle,cache_size FROM pg_sequences WHERE schemaname='public' ORDER BY 2",
    'ledger': 'SELECT version,name,checksum FROM schema_migrations ORDER BY version',
}


def catalog(conn):
    return {k:[dict(r) for r in conn.execute(q)] for k,q in CATALOG.items()}


def database_identity(conn):
    return dict(conn.execute("SELECT current_database() AS name,current_user AS role,"
        "current_setting('server_version_num')::int AS server_version,"
        "(SELECT system_identifier::text FROM pg_control_system()) AS system_identifier").fetchone())


def actions(conn, *, environment):
    rows=[dict(r) for r in conn.execute('SELECT * FROM wechat_draft_operations ORDER BY id')]
    require(len(rows)<=10000, 'schema016_action_retention_review_required')
    def one(table,key,value):
        require(table in TABLES and re.fullmatch('[a-z_]+',key), 'schema016_relation_identifier')
        data=conn.execute(f'SELECT * FROM {table} WHERE {key}=%s',(value,)).fetchall()
        require(len(data)==1, 'schema016_action_relation:'+table)
        return dict(data[0])
    def obj(value):
        return json.loads(value) if isinstance(value,str) else value
    for op in rows:
        require(op['environment']==environment and op['state'] in {
            'QUEUED','UPLOADING','SUBMITTING','VERIFYING','UNKNOWN','CONFIRMED','FAILED','REVOKED'}, 'schema016_action_state')
        require(type(op['revision']) is int and op['revision']>=1 and
            (op['lease_id'] is None)==(op['lease_expires_at'] is None), 'schema016_action_cas_lease')
        require(op['updated_at']>=op['created_at'] and op['secret_version']>0, 'schema016_action_chronology')
        binding=op['binding_json']; require(isinstance(binding,dict), 'schema016_action_binding')
        fields=('environment','tenant_id','user_id','source_task_id','source_run_id','source_message_id',
            'context_id','agent_id','agent_revision_id','skill_revision_id','article_version','account_identity',
            'secret_version','capability_identity')
        require(all(binding.get(k)==op[k] for k in fields) and binding.get('manifest_sha256')==op['prepare_manifest_sha256'],
                'schema016_action_immutable_confirmation')
        key={k:binding[k] for k in ('environment','tenant_id','user_id','agent_id','source_message_id',
            'article_version','manifest_sha256','account_identity')}
        require(digest(key)==op['confirmation_key'] and all(re.fullmatch('[a-f0-9]{64}',op[k]) for k in
            ('article_version','prepare_manifest_sha256','account_identity','capability_identity')), 'schema016_action_identity')
        user=one('users','id',op['user_id']); task=one('tasks','id',op['action_task_id'])
        source=one('tasks','id',op['source_task_id']); run=one('run_traces','run_id',op['action_run_id'])
        sr=one('run_traces','run_id',op['source_run_id']); ctx=one('agent_execution_contexts','id',op['context_id'])
        message=one('messages','id',op['source_message_id']); result=one('task_results','task_id',op['source_task_id'])
        cv=one('conversations','id',source['conversation_id']); owner=one('conversation_owners','conversation_id',cv['id'])
        version=one('agent_template_versions','id',op['agent_revision_id']); skill=one('skill_versions','id',op['skill_revision_id'])
        require(user['tenant_id']==cv['tenant_id']==op['tenant_id'] and owner['user_id']==op['user_id'], 'schema016_action_owner')
        require(all((t['tenant_id'],t['user_id'],t['agent_id'],t['conversation_id'])==
            (op['tenant_id'],op['user_id'],op['agent_id'],cv['id']) for t in (task,source)), 'schema016_action_task_scope')
        require(task['input_text']=='[WORKBENCH_WECHAT_DRAFT_OPERATION_V2]' and task['run_id']==run['run_id']
            and source['run_id']==sr['run_id'] and source['status']==sr['status']=='completed', 'schema016_action_source_task')
        require(all((r['tenant_id'],r['agent_id'],r['conversation_id'])==(op['tenant_id'],op['agent_id'],cv['id'])
            for r in (run,sr)), 'schema016_action_run_scope')
        require((ctx['tenant_id'],ctx['agent_id'],ctx['agent_template_version_id'])==
            (op['tenant_id'],op['agent_id'],op['agent_revision_id']) and version['agent_template_id']==op['agent_id'],
            'schema016_action_revision')
        require(message['role']=='assistant' and message['conversation_id']==cv['id']
            and obj(result['result_json']).get('assistant_message_id')==message['id'], 'schema016_action_article')
        for t in (task,source):
            require(one('task_agent_contexts','task_id',t['id'])['context_id']==ctx['id'], 'schema016_action_context')
        payload=obj(run['payload'])
        require(payload.get('wechat_operation_id')==op['id'] and payload.get('execution_context_id')==ctx['id']
            and payload.get('execution_kind')=='wechat_draft_action' and payload.get('provider_calls')==0,
            'schema016_action_run_identity')
        refs=conn.execute('SELECT skill_id FROM agent_template_version_skills WHERE agent_template_version_id=%s AND skill_version_id=%s',
            (version['id'],skill['id'])).fetchall()
        require(len(refs)==1 and refs[0]['skill_id']==skill['skill_id'], 'schema016_action_skill_identity')
        intents=op['intent_json']; receipts=op['upload_receipts_json']
        require(isinstance(intents,list) and isinstance(receipts,dict), 'schema016_action_journal')
        ids=set(); artifacts=set(); draft_intents=0
        for intent in intents:
            require(set(intent)=={'id','endpoint','request_sha256','artifact_key'} and intent['id'] not in ids
                and intent['artifact_key'] not in artifacts and intent['endpoint'] in {'material/add_material','media/uploadimg','draft/add'}
                and re.fullmatch('[a-f0-9]{64}',intent['request_sha256']), 'schema016_action_intent')
            ids.add(intent['id']); artifacts.add(intent['artifact_key']); draft_intents+=intent['endpoint']=='draft/add'
            if intent['id'] in receipts:
                receipt=receipts[intent['id']]
                if intent['endpoint']=='media/uploadimg':
                    parsed=urlparse(receipt.get('url',''))
                    require(set(receipt)=={'url'} and parsed.scheme in {'http','https'}
                        and parsed.hostname in {'mmbiz.qpic.cn','mmbiz.qlogo.cn'}, 'schema016_action_receipt')
                else:
                    require(set(receipt)=={'media_id'} and re.fullmatch('[a-zA-Z0-9_-]{1,256}',receipt['media_id']), 'schema016_action_receipt')
        require(set(receipts)<=ids and draft_intents<=1, 'schema016_action_duplicate_effect')
        if op['draft_media_id'] is not None:
            require(any(i['endpoint']=='draft/add' and receipts.get(i['id'],{}).get('media_id')==op['draft_media_id']
                        for i in intents), 'schema016_action_known_media_receipt')
        if op['state']=='CONFIRMED':
            proof=op['verification_json']
            require(proof.get('matched') is True and proof.get('media_id')==op['draft_media_id']
                and proof.get('account_identity')==op['account_identity'] and proof.get('manifest_sha256')==op['prepare_manifest_sha256']
                and re.fullmatch('[a-f0-9]{64}',proof.get('readback_sha256','')), 'schema016_action_confirmed_readback')
        require(task['status'] in {'queued','running','failed','completed'} and
            (task['status']!='completed' or op['state']=='CONFIRMED'), 'schema016_action_terminal_state')
    return dict(operations=len(rows), unknown=sum(r['state']=='UNKNOWN' for r in rows),
        persisted_state_sha256=digest(rows), execution_authority_granted=False, effect_replay=False)


def inspect(store, root, binding, *, required_schema=None):
    require(store.is_postgres, 'schema016_postgres_required')
    require(hashlib.sha256(store.database_url.encode()).hexdigest()==binding['runtime_inputs']['database_identity']['dsn_sha256'],
            'schema016_database_dsn')
    with store.connection() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        require(database_identity(conn)=={k:v for k,v in binding['runtime_inputs']['database_identity'].items() if k!='dsn_sha256'},
                'schema016_exact_database')
        rows=ledger(conn,root); schema=f'{len(rows):03}'
        require(required_schema in (None,schema), 'schema016_wrong_schema')
        value=catalog(conn)
        expected=set(TABLES)-({'wechat_draft_operations'} if schema=='015' else set())
        require({r['relname'] for r in value['tables']}==expected and digest(value)==binding['schema_catalogs'][schema],
                'schema016_native_full_catalog')
        from scripts.compatibility_epoch import read_state
        state=read_state(conn,postgres=True)
        require(state is not None and state['epoch']=='productized_v1' and state['epoch_rank']==2, 'schema016_data_contract_epoch')
        journal=actions(conn,environment=binding['environment']) if schema=='016' else None
    return dict(status='PASS', schema=schema, pending=16-len(rows), read_only=True,
        data_contract=PLAN['data_contract'], action_journal=journal, schema_rollback=False,
        recovery_mode='EXACT_PREDECESSOR_ON_015' if schema=='015' else RECOVERY)


def target(state, candidate):
    # Even an empty016 table forbids rollback to a015 Worker. UNKNOWN remains
    # data, not a reason to requeue uploads or restore an old database.
    require(state['schema'] in {'015','016'}, 'schema016_unknown_recovery_state')
    return PREDECESSOR['release_id'] if state['schema']=='015' else candidate['release_id']


def held_lock():
    import fcntl
    import stat
    from scripts.release_dual_source import load, PRODUCTION
    _,p=load()
    name='enterprise-agent-workbench-release.lock' if p['base']==PRODUCTION else 'enterprise-agent-schema016-isolated-release.lock'
    path=Path('/run/lock')/name
    require(os.geteuid()==0, 'schema016_root_release_operator')
    fd=9; info=os.fstat(fd); actual=path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid==info.st_gid==0 and not info.st_mode & 0o022
        and (info.st_dev,info.st_ino)==(actual.st_dev,actual.st_ino), 'schema016_global_lock_identity')
    probe=os.open(path,os.O_RDWR|os.O_NOFOLLOW)
    try:
        try: fcntl.flock(probe,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: pass
        else: require(False,'schema016_global_lock_not_held')
        try: fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: require(False,'schema016_global_lock_not_owned')
    finally: os.close(probe)


def quiesced(base):
    from scripts.release_verify import service_state
    states=service_state(base)
    require(set(states)=={'api','mcp','worker'} and all(s.get('pid')==0 and
        s.get('active') in {'inactive','failed'} for s in states.values()), 'schema016_old_process_not_quiesced')
