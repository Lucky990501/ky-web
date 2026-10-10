"""V2 default-deny execution authority, separate from historical V1 denial.

No issuer or sample executable grant is shipped. Installation is a separate
02/06 protected release decision, not a request parameter or environment flag.
"""
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import stat

from app.skill_python_runtime import git_identity
from app.wechat_prepare_reader import _read_regular, strict_json, digest
from app.wechat_draft_operations import DraftOperationError

CONTRACT='WECHAT_CREATE_DRAFT_ACTION_CAPABILITY_V2'
PATH=Path('/etc/enterprise-agent-integrated-test-v3.3/wechat-create-draft.v2.json')
PROJECT=Path(__file__).resolve().parents[1]
PINS=('app/wechat_draft_capability.py','app/wechat_draft_operations.py','app/wechat_draft_execution.py',
      'app/wechat_draft_api.py','app/wechat_prepare_reader.py','app/wechat_prepare_action.py',
      'app/skill_dispatch.py','app/skill_dispatch_config.py','app/product_service.py',
      'app/product_store.py','app/main.py','app/worker.py','app/task_queue.py','app/agent_execution.py','app/security.py',
      'app/tenant_secret_reference.py','scripts/wechat_draft_journal_child.py',
      'migrations/postgres/016_wechat_draft_operations.sql')
ENDPOINTS=['GET token','POST material/add_material','POST media/uploadimg','POST draft/add','POST draft/get']


def validate(value, binding, source, now):
    try:
        required={'contract','authority_id','environment','source_commit','source_tree','code_pins',
                  'tenant_id','user_id','agent_id','agent_revision_id','skill_revision_id','account_identity',
                  'secret_version','not_before','expires_at','revoked','network','max_requests'}
        if set(value)!=required or value['contract']!=CONTRACT:raise ValueError()
        if value['environment']!='test' or value['revoked'] is not False:raise ValueError()
        if not re.fullmatch('[a-zA-Z0-9_-]{1,128}',value['authority_id']):raise ValueError()
        if any(not re.fullmatch('[a-f0-9]{40}',value[k]) for k in ('source_commit','source_tree')):raise ValueError()
        if source!={k:value[k] for k in ('source_commit','source_tree')}:raise ValueError()
        if any(value[k]!=binding[k] for k in ('environment','tenant_id','user_id','agent_id',
                'agent_revision_id','skill_revision_id','account_identity','secret_version')):raise ValueError()
        if (type(value['not_before']) is not int or type(value['expires_at']) is not int or
            not value['not_before']<=now<value['expires_at'] or value['expires_at']-value['not_before']>86400):raise ValueError()
        if (value['network']!={'host':'api.weixin.qq.com','port':443,'endpoints':ENDPOINTS} or
            type(value['max_requests']) is not int or not 1<=value['max_requests']<=32):raise ValueError()
        if set(value['code_pins'])!=set(PINS) or any(not re.fullmatch('[a-f0-9]{64}',v) for v in value['code_pins'].values()):raise ValueError()
    except Exception:
        raise DraftOperationError('WECHAT_CAPABILITY_REQUIRED') from None


def load(binding):
    try:
        if os.name!='posix':raise ValueError()
        for path in (PATH,*PATH.parents):
            info=path.lstat()
            if stat.S_ISLNK(info.st_mode) or info.st_uid!=0 or info.st_gid!=0 or info.st_mode & 0o022:raise ValueError()
        if PATH.stat().st_mode & 0o222:raise ValueError()
        raw=_read_regular(PATH,64*1024);value=strict_json(raw)
        validate(value,binding,git_identity(PROJECT),int(datetime.now(timezone.utc).timestamp()))
        for name,pin in value['code_pins'].items():
            if digest(_read_regular(PROJECT/name,4*1024*1024))!=pin:raise ValueError()
        return value,digest(raw)
    except Exception:
        raise DraftOperationError('WECHAT_CAPABILITY_REQUIRED') from None
