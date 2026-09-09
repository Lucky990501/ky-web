from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_workspace_secrets() -> None:
    """Load only POC secret names into memory; never log or persist their values."""
    required = {"DEEPSEEK_API_KEY", "GATEWAY_API_TOKEN"}
    missing = {name for name in required if not os.environ.get(name)}
    if not missing:
        return
    for env_path in (PROJECT_ROOT / ".env", PROJECT_ROOT.parent / ".env"):
        if not env_path.is_file():
            continue
        for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            match = re.match(r"^(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=\s*(.+?)\s*$", line)
            if not match:
                continue
            key, value = match.group(1), match.group(2).strip().strip('"').strip("'")
            if key in missing and value:
                os.environ[key] = value
                missing.remove(key)
                if not missing:
                    return


_load_workspace_secrets()


def _path_from_env(name: str, default: str) -> Path:
    raw = Path(os.environ.get(name, default))
    return raw if raw.is_absolute() else PROJECT_ROOT / raw


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    database_url: str
    database_path: Path | None
    platform_mcp_url: str
    token_secret: str
    model_provider_id: str
    model_id: str
    reasoning_effort: str
    codex_api_key_env: str
    model_base_url: str
    model_wire_api: str
    image_provider_id: str
    image_model_id: str
    image_api_key_env: str
    object_storage_dir: Path
    object_storage_provider: str
    oss_access_key_id: str
    oss_access_key_secret: str
    oss_endpoint: str
    oss_bucket_name: str
    oss_prefix: str
    oss_signed_url_expire_seconds: int
    redis_url: str | None
    task_queue: str
    environment: str
    secure_cookies: bool
    bootstrap_demo_data: bool
    knowledge_chunk_size: int
    knowledge_chunk_overlap: int
    knowledge_min_score: float
    embedding_provider: str
    embedding_model: str
    embedding_dimension: int
    knowledge_max_upload_bytes: int

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = _path_from_env("ENTERPRISE_POC_DATA_DIR", ".runtime-data")
        database_url = os.environ.get("ENTERPRISE_POC_DATABASE_URL", "sqlite:///./.runtime-data/poc.db")
        if not (database_url.startswith("sqlite:///") or database_url.startswith(("postgresql://", "postgres://"))):
            raise ValueError("DATABASE_URL 仅支持 sqlite:/// 或 postgresql://。");
        database_path: Path | None = None
        if database_url.startswith("sqlite:///"):
            database_raw_path = Path(database_url.removeprefix("sqlite:///"))
            database_path = database_raw_path if database_raw_path.is_absolute() else PROJECT_ROOT / database_raw_path
        return cls(
            data_dir=data_dir,
            database_url=database_url,
            database_path=database_path,
            platform_mcp_url=os.environ.get("ENTERPRISE_POC_MCP_URL", "http://127.0.0.1:8091/mcp"),
            token_secret=os.environ.get("ENTERPRISE_POC_TOKEN_SECRET", "local-development-only-change-me"),
            model_provider_id=os.environ.get("ENTERPRISE_POC_MODEL_PROVIDER_ID", "deepseek"),
            model_id=os.environ.get("ENTERPRISE_POC_MODEL_ID", "deepseek-v4-pro"),
            reasoning_effort=os.environ.get("ENTERPRISE_POC_REASONING_EFFORT", "high"),
            codex_api_key_env=os.environ.get("ENTERPRISE_POC_CODEX_API_KEY_ENV", "DEEPSEEK_API_KEY"),
            model_base_url=os.environ.get("ENTERPRISE_POC_MODEL_BASE_URL", "https://api.deepseek.com/"),
            model_wire_api=os.environ.get("ENTERPRISE_POC_MODEL_WIRE_API", "responses"),
            image_provider_id=os.environ.get("ENTERPRISE_POC_IMAGE_PROVIDER_ID", "image-gateway"),
            image_model_id=os.environ.get("ENTERPRISE_POC_IMAGE_MODEL_ID", "gateway-managed-gpt-image-2"),
            image_api_key_env=os.environ.get("ENTERPRISE_POC_IMAGE_API_KEY_ENV", "GATEWAY_API_TOKEN"),
            object_storage_dir=_path_from_env("ENTERPRISE_POC_OBJECT_STORAGE_DIR", ".runtime-data/object-storage"),
            object_storage_provider=os.environ.get("ENTERPRISE_POC_OBJECT_STORAGE_PROVIDER", "local").lower(),
            oss_access_key_id=os.environ.get("OSS_ACCESS_KEY_ID", ""),
            oss_access_key_secret=os.environ.get("OSS_ACCESS_KEY_SECRET", ""),
            oss_endpoint=os.environ.get("OSS_ENDPOINT", ""),
            oss_bucket_name=os.environ.get("OSS_BUCKET_NAME", ""),
            oss_prefix=os.environ.get("OSS_WORKBENCH_PREFIX", "enterprise-agent-workbench").strip("/"),
            oss_signed_url_expire_seconds=int(os.environ.get("OSS_SIGNED_URL_EXPIRE_SECONDS", "900")),
            redis_url=os.environ.get("REDIS_URL") or None,
            task_queue=os.environ.get("ENTERPRISE_POC_TASK_QUEUE", "local").lower(),
            environment=os.environ.get("APP_ENV", "development").lower(),
            secure_cookies=os.environ.get("ENTERPRISE_POC_SECURE_COOKIES", "false").lower() == "true",
            bootstrap_demo_data=os.environ.get("ENTERPRISE_POC_BOOTSTRAP_DEMO_DATA", "true").lower() == "true",
            knowledge_chunk_size=int(os.environ.get("KNOWLEDGE_CHUNK_SIZE", "800")),
            knowledge_chunk_overlap=int(os.environ.get("KNOWLEDGE_CHUNK_OVERLAP", "120")),
            knowledge_min_score=float(os.environ.get("KNOWLEDGE_MIN_SCORE", "0.16")),
            embedding_provider=os.environ.get("EMBEDDING_PROVIDER", "local-hash"),
            embedding_model=os.environ.get("EMBEDDING_MODEL", "local-hash-v1"),
            embedding_dimension=int(os.environ.get("EMBEDDING_DIMENSION", "128")),
            knowledge_max_upload_bytes=int(os.environ.get("KNOWLEDGE_MAX_UPLOAD_BYTES", str(100 * 1024 * 1024))),
        )


settings = Settings.from_env()
