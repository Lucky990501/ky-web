"""Vendor-neutral object storage contract; LocalStorage is the development adapter."""
from __future__ import annotations
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterable
from uuid import uuid4
from app.settings import Settings


class StorageObjectNotFound(FileNotFoundError):
    """The requested object is absent without exposing provider details."""


class StorageUnavailable(RuntimeError):
    """The object provider could not complete a read request."""


class StorageProvider(ABC):
    @abstractmethod
    def put(self, key: str, content: bytes, mime_type: str) -> str: ...
    @abstractmethod
    def get(self, key: str) -> bytes: ...
    @abstractmethod
    def delete(self, key: str) -> None: ...
    @abstractmethod
    def signed_url(self, key: str) -> str: ...
    @abstractmethod
    def list_keys(self, prefix: str) -> Iterable[str]: ...

class LocalStorage(StorageProvider):
    def __init__(self, root: Path) -> None: self.root = root
    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root.resolve() not in path.parents: raise ValueError("非法存储键。")
        return path
    def put(self, key: str, content: bytes, mime_type: str) -> str:
        path=self._path(key); path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(content); return key
    def get(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError as exc:
            raise StorageObjectNotFound(key) from exc
        except ValueError as exc:
            raise StorageObjectNotFound("invalid storage key") from exc
        except OSError as exc:
            raise StorageUnavailable("本地对象存储暂时不可用。") from exc
    def delete(self, key: str) -> None: self._path(key).unlink(missing_ok=True)
    def signed_url(self, key: str) -> str: return f"/api/v1/storage/{key}?v={uuid4().hex}"
    def list_keys(self, prefix: str) -> Iterable[str]:
        root = self.root.resolve()
        if not root.exists():
            return ()
        return tuple(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file() and path.relative_to(root).as_posix().startswith(prefix))


class OSSStorage(StorageProvider):
    """Private OSS adapter. Browser access stays behind the authenticated API."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        required = (settings.oss_access_key_id, settings.oss_access_key_secret, settings.oss_endpoint, settings.oss_bucket_name)
        if not all(required):
            raise StorageUnavailable("生产对象存储配置不完整。")

    def _bucket(self):
        import oss2
        auth = oss2.Auth(self._settings.oss_access_key_id, self._settings.oss_access_key_secret)
        return oss2.Bucket(auth, self._settings.oss_endpoint, self._settings.oss_bucket_name)

    def put(self, key: str, content: bytes, mime_type: str) -> str:
        self._bucket().put_object(key, content, headers={"Content-Type": mime_type})
        return key

    def get(self, key: str) -> bytes:
        try:
            return self._bucket().get_object(key).read()
        except Exception as exc:
            if getattr(exc, "status", None) == 404 or getattr(exc, "code", None) in {"NoSuchKey", "NoSuchObject"}:
                raise StorageObjectNotFound(key) from exc
            raise StorageUnavailable("对象存储暂时不可用。") from exc

    def delete(self, key: str) -> None:
        self._bucket().delete_object(key)

    def signed_url(self, key: str) -> str:
        return self._bucket().sign_url("GET", key, self._settings.oss_signed_url_expire_seconds)

    def list_keys(self, prefix: str) -> Iterable[str]:
        import oss2

        return tuple(item.key for item in oss2.ObjectIterator(self._bucket(), prefix=prefix))


def storage_provider(settings: Settings) -> StorageProvider:
    if settings.object_storage_provider == "oss":
        return OSSStorage(settings)
    if settings.object_storage_provider != "local":
        raise StorageUnavailable("对象存储 Provider 不可用。")
    return LocalStorage(settings.object_storage_dir)
