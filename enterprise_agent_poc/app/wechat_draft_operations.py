"""PG-only durable Action journal, using the existing store/transaction boundary.

The journal never grants execution. Callers must supply current capability and
ownership checks. No automatic UNKNOWN -> mutation recovery exists.
"""
from datetime import datetime, timedelta, timezone
import json
import re
import uuid

from app.wechat_prepare_reader import identity

TASK_MARKER = '[WORKBENCH_WECHAT_DRAFT_OPERATION_V2]'
TERMINAL = {'CONFIRMED', 'FAILED', 'REVOKED'}


class DraftOperationError(PermissionError):
    def __init__(self, code='WECHAT_ACTION_BLOCKED'):
        self.code = code
        super().__init__(code)


def wire(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


class DraftOperations:
    def __init__(self, store):
        self.store = store

    def require_schema(self):
        if not self.store.is_postgres:
            raise DraftOperationError('WECHAT_SCHEMA016_REQUIRED')
        with self.store.connection() as conn:
            row = conn.execute("SELECT to_regclass('public.wechat_draft_operations') AS name").fetchone()
        if not row['name']:
            raise DraftOperationError('WECHAT_SCHEMA016_REQUIRED')

    def by_task(self, task_id):
        self.require_schema()
        with self.store.connection() as conn:
            row = conn.execute('SELECT * FROM wechat_draft_operations WHERE action_task_id=?', (task_id,)).fetchone()
        return dict(row) if row else None

    def recoverable_task_ids(self):
        """Existing Worker startup repairs commit-before-enqueue gaps. No retry grant."""
        if not self.store.is_postgres:return []
        with self.store.connection() as conn:
            if not conn.execute("SELECT to_regclass('public.wechat_draft_operations') AS name").fetchone()['name']:
                return []  # Schema015 applications have no durable Actions.
            rows=conn.execute("SELECT action_task_id FROM wechat_draft_operations WHERE state IN ('QUEUED','UPLOADING','SUBMITTING','VERIFYING') ORDER BY created_at").fetchall()
        return [r['action_task_id'] for r in rows]

    def owned(self, principal, agent, message):
        self.require_schema()
        with self.store.connection() as conn:
            rows = conn.execute('''SELECT * FROM wechat_draft_operations WHERE tenant_id=? AND user_id=?
                AND agent_id=? AND source_message_id=? ORDER BY created_at DESC''',
                (principal.tenant_id, principal.user_id, agent, message)).fetchall()
        return [dict(row) for row in rows]

    def create(self, binding, authorize):
        """Owner lock serializes confirmation; Task/Run/context/op commit together.

        authorize(conn) rechecks current DB state in this transaction. Unique key
        excludes rotating secret/capability versions, preventing duplicate effects.
        """
        self.require_schema()
        key = identity({k: binding[k] for k in ('environment','tenant_id','user_id','agent_id',
            'source_message_id','article_version','manifest_sha256','account_identity')})
        with self.store.connection() as conn:
            owner = conn.execute('SELECT id FROM users WHERE id=? AND tenant_id=? AND account_status=\'enabled\' FOR UPDATE',
                                 (binding['user_id'], binding['tenant_id'])).fetchone()
            if not owner:
                raise DraftOperationError()
            prior = conn.execute('SELECT * FROM wechat_draft_operations WHERE environment=? AND tenant_id=? AND user_id=? AND confirmation_key=?',
                (binding['environment'], binding['tenant_id'], binding['user_id'], key)).fetchone()
            if prior:
                return dict(prior), False
            authorize(conn)
            op, task, run = (str(uuid.uuid4()) for _ in range(3))
            conn.execute('''INSERT INTO tasks(id,tenant_id,user_id,agent_id,conversation_id,input_text,status,stage,run_id)
                VALUES (?,?,?,?,?,?,'queued','queued',?)''', (task,binding['tenant_id'],binding['user_id'],binding['agent_id'],
                binding['conversation_id'],TASK_MARKER,run))
            conn.execute('INSERT INTO task_agent_contexts(task_id,context_id) VALUES (?,?)', (task,binding['context_id']))
            trace = dict(execution_kind='wechat_draft_action',wechat_operation_id=op,
                         execution_context_id=binding['context_id'],provider_calls=0)
            conn.execute('''INSERT INTO run_traces(run_id,conversation_id,tenant_id,agent_id,status,payload)
                VALUES (?,?,?,?,'queued',?)''', (run,binding['conversation_id'],binding['tenant_id'],binding['agent_id'],wire(trace)))
            fields = ('environment','tenant_id','user_id','source_task_id','source_run_id','source_message_id',
                      'context_id','agent_id','agent_revision_id','skill_revision_id','article_version','account_identity',
                      'secret_version','capability_identity')
            values = {k: binding[k] for k in fields}
            values.update(id=op, confirmation_key=key, action_task_id=task, action_run_id=run,
                prepare_manifest_sha256=binding['manifest_sha256'],binding_json=wire(binding))
            conn.execute(f"INSERT INTO wechat_draft_operations({','.join(values)}) VALUES ({','.join('?' for _ in values)})", tuple(values.values()))
            return dict(conn.execute('SELECT * FROM wechat_draft_operations WHERE id=?', (op,)).fetchone()), True

    @staticmethod
    def _update(conn, old, **changes):
        for key in ('intent_json','upload_receipts_json','verification_json'):
            if key in changes:
                changes[key] = wire(changes[key])
        changes['revision'] = old['revision']+1
        result = conn.execute(f"UPDATE wechat_draft_operations SET {','.join(k+'=?' for k in changes)} WHERE id=? AND revision=?",
                              (*changes.values(),old['id'],old['revision']))
        if result.rowcount != 1:
            raise DraftOperationError('WECHAT_OPERATION_CAS')
        return dict(conn.execute('SELECT * FROM wechat_draft_operations WHERE id=?',(old['id'],)).fetchone())

    @staticmethod
    def _lock(conn, op):
        row = conn.execute('SELECT * FROM wechat_draft_operations WHERE id=? FOR UPDATE', (op,)).fetchone()
        if not row:
            raise DraftOperationError()
        return dict(row)

    def claim(self, op):
        with self.store.connection() as conn:
            row = self._lock(conn,op)
            now = datetime.now(timezone.utc)
            if row['state'] in TERMINAL or row['state']=='UNKNOWN':
                return None
            if row['lease_id'] and row['lease_expires_at'] > now:
                return None
            # Expired ownership after ANY external intent is never re-executed.
            if row['intent_json'] and not (row['state']=='VERIFYING' and row['draft_media_id'] and not row['lease_id']):
                self._update(conn,row,state='UNKNOWN',lease_id=None,lease_expires_at=None)
                return None
            return self._update(conn,row,state='VERIFYING' if row['draft_media_id'] else 'UPLOADING',lease_id=str(uuid.uuid4()),
                                lease_expires_at=now+timedelta(seconds=600))

    def _leased(self, conn, op, lease):
        row = self._lock(conn,op)
        if row['lease_id'] != lease or row['lease_expires_at'] <= datetime.now(timezone.utc):
            raise DraftOperationError('WECHAT_WORKER_LEASE_INVALID')
        return row

    def intent(self, op, lease, endpoint, request_sha256, authorize, *, artifact_key=None):
        if endpoint not in ('material/add_material','media/uploadimg','draft/add') or not re.fullmatch('[a-f0-9]{64}',request_sha256):
            raise DraftOperationError()
        with self.store.connection() as conn:
            row = self._leased(conn,op,lease)
            authorize()
            if row['state'] not in ('UPLOADING','SUBMITTING') or any(x['id'] not in row['upload_receipts_json'] for x in row['intent_json']):
                raise DraftOperationError('WECHAT_UNRESOLVED_INTENT')
            if any(x['endpoint']=='draft/add' for x in row['intent_json']):
                raise DraftOperationError('WECHAT_DRAFT_RESUBMISSION_BLOCKED')
            if artifact_key is None:artifact_key=endpoint+':'+request_sha256
            if not isinstance(artifact_key,str) or len(artifact_key)>100 or any(x.get('artifact_key')==artifact_key for x in row['intent_json']):
                raise DraftOperationError('WECHAT_DUPLICATE_EFFECT')
            intent = dict(id=str(uuid.uuid4()),endpoint=endpoint,request_sha256=request_sha256,artifact_key=artifact_key)
            self._update(conn,row,state='SUBMITTING' if endpoint=='draft/add' else 'UPLOADING',
                         intent_json=[*row['intent_json'],intent])
        return intent['id']  # commit precedes the network permit

    def acknowledge(self, op, lease, intent_id, result):
        with self.store.connection() as conn:
            row = self._leased(conn,op,lease)
            matches = [x for x in row['intent_json'] if x['id']==intent_id]
            if len(matches)!=1 or intent_id in row['upload_receipts_json']:
                raise DraftOperationError()
            endpoint = matches[0]['endpoint']
            if endpoint=='media/uploadimg':
                from urllib.parse import urlparse
                value=result.get('url'); parts=urlparse(value or '')
                if parts.scheme not in ('http','https') or parts.hostname not in ('mmbiz.qpic.cn','mmbiz.qlogo.cn') or len(value)>2048:
                    raise DraftOperationError('WECHAT_INVALID_UPLOAD_RECEIPT')
                safe={'url':value}
            else:
                value=result.get('media_id')
                if not isinstance(value,str) or not re.fullmatch('[a-zA-Z0-9_-]{1,256}',value):
                    raise DraftOperationError('WECHAT_INVALID_UPLOAD_RECEIPT')
                safe={'media_id':value}
            changes=dict(upload_receipts_json={**row['upload_receipts_json'],intent_id:safe})
            if endpoint=='draft/add':
                changes.update(draft_media_id=value,state='VERIFYING')
            return self._update(conn,row,**changes)

    def unknown(self, op, lease):
        with self.store.connection() as conn:
            row=self._lock(conn,op)
            if row['state'] in TERMINAL or row['lease_id']!=lease:
                return row
            return self._update(conn,row,state='UNKNOWN' if row['intent_json'] else 'FAILED',lease_id=None,lease_expires_at=None)

    def confirm(self, op, lease, readback_sha256):
        if not re.fullmatch('[a-f0-9]{64}',readback_sha256):
            raise DraftOperationError()
        with self.store.connection() as conn:
            row=self._leased(conn,op,lease)
            if row['state']!='VERIFYING' or not row['draft_media_id']:
                raise DraftOperationError()
            evidence=dict(matched=True,media_id=row['draft_media_id'],account_identity=row['account_identity'],
                          manifest_sha256=row['prepare_manifest_sha256'],readback_sha256=readback_sha256)
            return self._update(conn,row,state='CONFIRMED',verification_json=evidence,lease_id=None,lease_expires_at=None)

    def request_readback(self, op, authorize):
        with self.store.connection() as conn:
            row=self._lock(conn,op)
            if row['state']!='UNKNOWN' or not row['draft_media_id'] or row['lease_id']:
                raise DraftOperationError('WECHAT_MANUAL_REVIEW_REQUIRED')
            authorize(row)
            row=self._update(conn,row,state='VERIFYING')
            conn.execute("UPDATE tasks SET status='queued',stage='wechat_readback' WHERE id=?",(row['action_task_id'],))
            return row

    @staticmethod
    def public(row):
        return dict(operation_id=row['id'],task_id=row['action_task_id'],state=row['state'],
                    agent_id=row['agent_id'],message_id=row['source_message_id'],article_version=row['article_version'],
                    confirmed=row['state']=='CONFIRMED',media_id=row['draft_media_id'] if row['state']=='CONFIRMED' else None,
                    manual_review_required=row['state']=='UNKNOWN',automatic_resubmit=False)
