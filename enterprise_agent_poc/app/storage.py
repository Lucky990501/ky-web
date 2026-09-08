"""Vendor-neutral object storage contract; LocalStorage is the development adapter."""
from __future__ import annotations
from abc import ABC, abstractmethod
from pathlib import Path
from uuid import uuid4
from app.settings import Settings

class StorageProvider(ABC):
    @abstractmethod
    def put(self, key: str, content: bytes, mime_type: str) -> str: ...
    @abstractmethod
    def get(self, key: str) -> bytes: ...
    @abstractmethod
    def delete(self, key: str) -> None: ...
    @abstractmethod
    def signed_url(self, key: str) -> str: ...

class LocalStorage(StorageProvider):
    def __init__(self, root: Path) -> None: self.root = root
    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root.resolve() not in path.parents: raise ValueError("非法存储键。")
        return path
    def put(self, key: str, content: bytes, mime_type: str) -> str:
        path=self._path(key); path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(content); return key
    def get(self, key: str) -> bytes: return self._path(key).read_bytes()
    def delete(self, key: str) -> None: self._path(key).unlink(missing_ok=True)
    def signed_url(self, key: str) -> str: return f"/api/v1/storage/{key}?v={uuid4().hex}"


class OSSStorage(StorageProvider):
    """Private OSS adapter. Browser access stays behind the authenticated API."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        required = (settings.oss_access_key_id, settings.oss_access_key_secret, settings.oss_endpoint, settings.oss_bucket_name)
        if not all(required):
            raise RuntimeError("生产 OSS 配置不完整。")

    def _bucket(self):
        import oss2
        auth = oss2.Auth(self._settings.oss_access_key_id, self._settings.oss_access_key_secret)
        return oss2.Bucket(auth, self._settings.oss_endpoint, self._settings.oss_bucket_name)

    def put(self, key: str, content: bytes, mime_type: str) -> str:
        self._bucket().put_object(key, content, headers={"Content-Type": mime_type})
        return key

    def get(self, key: str) -> bytes:
        return self._bucket().get_object(key).read()

    def delete(self, key: str) -> None:
        self._bucket().delete_object(key)

    def signed_url(self, key: str) -> str:
        return self._bucket().sign_url("GET", key, self._settings.oss_signed_url_expire_seconds)


def storage_provider(settings: Settings) -> StorageProvider:
    if settings.object_storage_provider == "oss":
        return OSSStorage(settings)
    if settings.object_storage_provider != "local":
        raise RuntimeError("不支持的对象存储 Provider。")
    return LocalStorage(settings.object_storage_dir)
