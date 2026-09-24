"""Tenant-owned transparent PNG validation and enterprise brand-logo storage."""

from __future__ import annotations

import io
import re
import struct
import warnings
from dataclasses import dataclass
from pathlib import PurePosixPath
from uuid import uuid4

from PIL import Image, UnidentifiedImageError

from app.storage import StorageProvider


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_LOGO_BYTES = 5 * 1024 * 1024
MAX_LOGO_DIMENSION = 4096
LOGO_DOWNLOAD_URL = "/api/v1/enterprise-config/brand-logo"


class BrandLogoError(ValueError):
    def __init__(self, code: str, status_code: int = 422) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class LogoInspection:
    width: int
    height: int


def validate_brand_logo_key(key: str, tenant_id: str) -> str:
    if not isinstance(key, str) or not key or len(key) > 512 or "\\" in key:
        raise BrandLogoError("LOGO_PERMISSION_DENIED", 403)
    path = PurePosixPath(key)
    parts = path.parts
    if (path.is_absolute() or len(parts) != 4 or tuple(parts[:3]) != ("brand-assets", tenant_id, "logos")
            or any(part in {"", ".", ".."} for part in parts)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}\.png", parts[3])):
        raise BrandLogoError("LOGO_PERMISSION_DENIED", 403)
    return path.as_posix()


def inspect_transparent_png(content: bytes, *, filename: str | None = None) -> LogoInspection:
    """Decode the entire image; a PNG header or RGBA mode alone is insufficient."""
    if not content:
        raise BrandLogoError("LOGO_FILE_REQUIRED", 400)
    if len(content) > MAX_LOGO_BYTES:
        raise BrandLogoError("LOGO_TOO_LARGE", 413)
    if filename is not None and PurePosixPath(filename.replace("\\", "/")).suffix.lower() != ".png":
        raise BrandLogoError("LOGO_FORMAT_NOT_SUPPORTED", 415)
    if not content.startswith(PNG_SIGNATURE):
        raise BrandLogoError("LOGO_FORMAT_NOT_SUPPORTED", 415)
    if len(content) < 33 or content[12:16] != b"IHDR" or struct.unpack(">I", content[8:12])[0] != 13:
        raise BrandLogoError("LOGO_INVALID_PNG")
    width, height = struct.unpack(">II", content[16:24])
    if not width or not height:
        raise BrandLogoError("LOGO_INVALID_PNG")
    if width > MAX_LOGO_DIMENSION or height > MAX_LOGO_DIMENSION:
        raise BrandLogoError("LOGO_DIMENSIONS_TOO_LARGE")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                if image.format != "PNG" or getattr(image, "n_frames", 1) != 1:
                    raise BrandLogoError("LOGO_INVALID_PNG")
                image.verify()
            with Image.open(io.BytesIO(content)) as image:
                if image.format != "PNG" or image.size != (width, height):
                    raise BrandLogoError("LOGO_INVALID_PNG")
                if "A" not in image.getbands():
                    raise BrandLogoError("LOGO_TRANSPARENCY_REQUIRED")
                image.load()
                low, high = image.getchannel("A").getextrema()
                if low == 255 or high == 0:
                    raise BrandLogoError("LOGO_TRANSPARENCY_REQUIRED")
    except BrandLogoError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise BrandLogoError("LOGO_DIMENSIONS_TOO_LARGE") from None
    except (OSError, ValueError, SyntaxError, UnidentifiedImageError):
        raise BrandLogoError("LOGO_INVALID_PNG") from None
    return LogoInspection(width, height)


def public_brand_logo(config: dict) -> dict:
    """Hide object keys while preserving the rest of the enterprise config."""
    result = dict(config)
    metadata = result.pop("brand_logo_metadata", None)
    result.pop("brand_logo", None)
    result.pop("brand_mark_logo", None)
    fields = ("asset_id", "filename", "content_type", "width", "height", "has_transparency")
    result["brand_logo"] = (
        {**{field: metadata.get(field) for field in fields}, "download_url": LOGO_DOWNLOAD_URL}
        if isinstance(metadata, dict) else None
    )
    return result


class BrandLogoService:
    def __init__(self, storage: StorageProvider, config_store) -> None:
        self._storage = storage
        self._config_store = config_store

    def upload(self, tenant_id: str, filename: str, content: bytes) -> dict:
        inspection = inspect_transparent_png(content, filename=filename)
        asset_id = uuid4().hex
        key = f"brand-assets/{tenant_id}/logos/{asset_id}.png"
        safe_name = PurePosixPath(filename.replace("\\", "/")).name
        safe_name = re.sub(r"[\x00-\x1f\x7f]", "", safe_name).strip()[:180] or f"{asset_id}.png"
        metadata = {
            "asset_id": asset_id,
            "filename": safe_name,
            "content_type": "image/png",
            "width": inspection.width,
            "height": inspection.height,
            "has_transparency": True,
        }
        try:
            stored_key = self._storage.put(key, content, "image/png")
            if stored_key != key:
                raise RuntimeError("Storage returned an unexpected object key")
        except Exception as exc:
            raise BrandLogoError("LOGO_STORAGE_FAILED", 503) from exc
        try:
            self._config_store.set_brand_logo(tenant_id, key, metadata)
        except Exception as exc:
            # The new object may remain inactive; the old config pointer stays.
            raise BrandLogoError("LOGO_CONFIG_UPDATE_FAILED", 503) from exc
        return {**metadata, "download_url": LOGO_DOWNLOAD_URL}

    def current_bytes(self, tenant_id: str, config: dict) -> bytes:
        key = config.get("brand_logo")
        if not key:
            raise BrandLogoError("LOGO_FILE_REQUIRED", 404)
        validate_brand_logo_key(key, tenant_id)
        try:
            content = self._storage.get(key)
        except Exception as exc:
            raise BrandLogoError("LOGO_STORAGE_FAILED", 503) from exc
        inspect_transparent_png(content)
        return content
