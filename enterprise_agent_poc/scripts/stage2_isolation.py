"""Fail-closed local Stage 2 harness boundary; never used by production startup."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

REQUIRED = ("APP_ENV", "ENTERPRISE_POC_DATABASE_URL", "ENTERPRISE_POC_DATA_DIR",
            "ENTERPRISE_POC_OBJECT_STORAGE_DIR", "ENTERPRISE_POC_OBJECT_STORAGE_PROVIDER",
            "ENTERPRISE_POC_TASK_QUEUE", "ENTERPRISE_POC_MCP_URL")
FORBIDDEN = Path(__file__).resolve().parents[1] / ".runtime-data"


class IsolationError(RuntimeError):
    pass


def assert_isolated(settings, root, expected=None):
    """Validate actual parsed Settings BEFORE any DB / storage initialization."""
    root = Path(root)
    if not root.is_absolute() or root.is_symlink() or not root.name.startswith("ky-web-stage2-"):
        raise IsolationError("Unmarked isolated root; BLOCK")
    root = root.resolve()
    if root.parent != Path(tempfile.gettempdir()).resolve() and root.parent != Path("/private/tmp"):
        raise IsolationError("Root outside explicit temporary directory; BLOCK")
    if root.stat().st_uid != os.getuid() or (root / "stage2-isolated.marker").read_text().strip() != "ky-web-stage2-isolated-v1":
        raise IsolationError("Isolation marker / owner mismatch; BLOCK")
    if any(not os.environ.get(k) for k in REQUIRED):
        raise IsolationError("Explicit isolation variables missing; BLOCK")
    if settings.environment not in {"test", "development"} or os.environ["APP_ENV"] != settings.environment:
        raise IsolationError("Non-test environment; BLOCK")
    paths = (settings.database_path, settings.data_dir, settings.object_storage_dir)
    if not settings.database_url.startswith("sqlite:///") or settings.database_path is None:
        raise IsolationError("Preview does not accept a network / production DSN; BLOCK")
    for path in paths:
        if not path.is_absolute() or not path.resolve().is_relative_to(root) or path.resolve().is_relative_to(FORBIDDEN.resolve()):
            raise IsolationError("Database / data / object storage outside isolated root; BLOCK")
        if any(p.is_symlink() for p in [path, *path.parents] if p.is_relative_to(root)):
            raise IsolationError("Isolated path contains symlink; BLOCK")
    if settings.object_storage_provider != "local" or settings.task_queue != "local" or settings.redis_url:
        raise IsolationError("External storage / queue forbidden; BLOCK")
    snapshot = {"environment": settings.environment,"database_url":settings.database_url,
                "data_dir":str(settings.data_dir),"object_storage_dir":str(settings.object_storage_dir),
                "mcp_url":settings.platform_mcp_url,"test_tenant":os.environ.get("STAGE2_RUNTIME_TEST_TENANT_ID")}
    if expected is not None and snapshot != expected:
        raise IsolationError("API / MCP / Worker configuration mismatch; BLOCK")
    return {"database_is_isolated":True,"data_dir_is_isolated":True,
            "object_storage_is_isolated":True,"environment":settings.environment}, snapshot


def bootstrap(config_path):
    """Same manifest and parsed Settings for API, MCP, Worker and Runtime Test."""
    config = json.loads(Path(config_path).read_text())
    # The manifest contains local public configuration, never a credential value.
    for key,value in config["environment"].items():
        if not isinstance(value,str) or key not in {*REQUIRED,"ENTERPRISE_POC_TOKEN_SECRET", "ENTERPRISE_POC_MCP_HOST", "ENTERPRISE_POC_MCP_PORT",
                "ENTERPRISE_POC_MODEL_PROVIDER_ID","ENTERPRISE_POC_MODEL_ID","ENTERPRISE_POC_REASONING_EFFORT",
                "ENTERPRISE_POC_MODEL_BASE_URL","ENTERPRISE_POC_MODEL_WIRE_API","ENTERPRISE_POC_CODEX_API_KEY_ENV",
                "ENTERPRISE_POC_BOOTSTRAP_DEMO_DATA","STAGE2_RUNTIME_TEST_TENANT_ID", "REDIS_URL",
                "ENTERPRISE_POC_TASK_QUEUE_NAMESPACE", "ENTERPRISE_POC_AGENT_RUNTIME_TEST_TENANT_ID"}:
            raise IsolationError("Unexpected harness configuration field; BLOCK")
        if key in os.environ and os.environ[key] != value:
            raise IsolationError("Conflicting inherited process configuration; BLOCK")
    os.environ.update(config["environment"])
    # Never allow settings' workspace secret auto-loader to inspect .env.
    os.environ.update(DEEPSEEK_API_KEY="stage2-unused-placeholder",GATEWAY_API_TOKEN="stage2-image-disabled",
                      EMBEDDING_PROVIDER="local-hash",EMBEDDING_MODEL="local-hash-v1",EMBEDDING_DIMENSION="128")
    for key in ("OSS_ACCESS_KEY_ID","OSS_ACCESS_KEY_SECRET","EMBEDDING_API_KEY","EMBEDDING_BASE_URL"):
        os.environ.pop(key,None)
    if config.get('mode') != 'redis-postgres':os.environ.pop('REDIS_URL',None)
    from app.settings import Settings
    settings = Settings.from_env()
    checker=assert_worker_isolated if config.get('mode')=='redis-postgres' else assert_isolated
    checks,_ = checker(settings,config["root"],config["snapshot"])
    print(json.dumps(checks,sort_keys=True),flush=True)
    return config,settings


def assert_worker_isolated(settings,root,expected=None):
    """Separate explicit PG/Redis harness; the original SQLite gate is unchanged."""
    from urllib.parse import urlparse,parse_qs
    root=Path(root)
    if not root.is_absolute() or root.is_symlink() or root.parent != Path('/private/tmp') or not root.name.startswith('ky-web-stage2-readiness.'):
        raise IsolationError('Invalid Worker isolation root; BLOCK')
    if root.stat().st_uid!=os.getuid() or (root/'stage2-isolated.marker').read_text().strip()!='ky-web-stage2-isolated-v1':
        raise IsolationError('Worker isolation marker / owner mismatch; BLOCK')
    if any(not os.environ.get(k) for k in REQUIRED) or settings.environment not in {'test','development'} or os.environ['APP_ENV']!=settings.environment:
        raise IsolationError('Explicit non-production Worker configuration required; BLOCK')
    for path in (settings.data_dir,settings.object_storage_dir):
        if not path.is_absolute() or not path.resolve().is_relative_to(root) or path.resolve().is_relative_to(FORBIDDEN.resolve()) or any(p.is_symlink() for p in [path,*path.parents] if p.is_relative_to(root)):
            raise IsolationError('Worker storage path escaped root; BLOCK')
    db=urlparse(settings.database_url);query=parse_qs(db.query)
    redis=urlparse(settings.redis_url or '')
    if (db.scheme!='postgresql' or db.hostname or not db.path.startswith('/stage25_') or query.get('host')!=[str(root/'pg/socket')]
        or query.get('port')!=['54330'] or query.get('user')!=['stage1_fixture']
        or redis.scheme!='unix' or redis.netloc or redis.path!=str(root/'redis.sock') or redis.query!='db=0'
        or settings.task_queue!='redis' or settings.task_queue_namespace!=root.name or settings.object_storage_provider!='local'):
        raise IsolationError('Private PG / Redis queue configuration required; BLOCK')
    for socket in (root/'pg/socket',root/'redis.sock'):
        if socket.is_symlink() or socket.stat().st_uid!=os.getuid() or socket.stat().st_mode & 0o077:
            raise IsolationError('Private socket permissions / owner mismatch; BLOCK')
    snapshot={'environment':settings.environment,'database_url':settings.database_url,'data_dir':str(settings.data_dir),
        'object_storage_dir':str(settings.object_storage_dir),'mcp_url':settings.platform_mcp_url,
        'test_tenant':settings.agent_runtime_test_tenant_id,'redis_url':settings.redis_url,'namespace':settings.task_queue_namespace}
    if expected is not None and expected!=snapshot:raise IsolationError('API / Worker / MCP configuration mismatch; BLOCK')
    # Read-only attestation before ANY initialize, over the validated private socket.
    import psycopg
    with psycopg.connect(settings.database_url,options='-c default_transaction_read_only=on') as conn:
        assert conn.execute('SHOW listen_addresses').fetchone()[0]==''
        assert Path(conn.execute('SHOW data_directory').fetchone()[0]).resolve()==root/'pg/cluster'
    return {'database_is_isolated':True,'data_dir_is_isolated':True,'object_storage_is_isolated':True,'environment':settings.environment},snapshot
