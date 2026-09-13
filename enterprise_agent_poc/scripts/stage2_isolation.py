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
                "ENTERPRISE_POC_BOOTSTRAP_DEMO_DATA","STAGE2_RUNTIME_TEST_TENANT_ID"}:
            raise IsolationError("Unexpected harness configuration field; BLOCK")
        if key in os.environ and os.environ[key] != value:
            raise IsolationError("Conflicting inherited process configuration; BLOCK")
    os.environ.update(config["environment"])
    # Never allow settings' workspace secret auto-loader to inspect .env.
    os.environ.update(DEEPSEEK_API_KEY="stage2-unused-placeholder",GATEWAY_API_TOKEN="stage2-image-disabled",
                      EMBEDDING_PROVIDER="local-hash",EMBEDDING_MODEL="local-hash-v1",EMBEDDING_DIMENSION="128")
    for key in ("REDIS_URL","OSS_ACCESS_KEY_ID","OSS_ACCESS_KEY_SECRET","EMBEDDING_API_KEY","EMBEDDING_BASE_URL"):
        os.environ.pop(key,None)
    from app.settings import Settings
    settings = Settings.from_env()
    checks,_ = assert_isolated(settings,config["root"],config["snapshot"])
    print(json.dumps(checks,sort_keys=True),flush=True)
    return config,settings
