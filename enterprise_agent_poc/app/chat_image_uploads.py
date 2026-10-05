"""Validation for one user-supplied chat reference image per task."""

import io

from PIL import Image, UnidentifiedImageError

from app.platform_mcp.service import _validated_image_metadata


MAX_CHAT_IMAGE_BYTES = 10 * 1024 * 1024
MAX_CHAT_IMAGE_DIMENSION = 4096
ALLOWED_CHAT_IMAGE_MIME = frozenset({"image/jpeg", "image/png", "image/webp"})


class ChatImageUploadError(ValueError):
    pass


def validate_chat_image(content: bytes, declared_mime: str) -> tuple[str, str, int, int]:
    if declared_mime not in ALLOWED_CHAT_IMAGE_MIME:
        raise ChatImageUploadError("仅支持 JPEG、PNG 或 WebP 图片。")
    if not content or len(content) > MAX_CHAT_IMAGE_BYTES:
        raise ChatImageUploadError("图片不能为空，且不能超过 10MB。")
    try:
        _actual_format, extension, actual_mime = _validated_image_metadata(content)
        with Image.open(io.BytesIO(content)) as picture:
            width, height = picture.size
            if width < 1 or height < 1 or width > MAX_CHAT_IMAGE_DIMENSION or height > MAX_CHAT_IMAGE_DIMENSION:
                raise ChatImageUploadError("图片尺寸不能超过 4096 × 4096。")
    except ChatImageUploadError:
        raise
    except (RuntimeError, UnidentifiedImageError, OSError, ValueError) as exc:
        raise ChatImageUploadError("图片文件无效或已损坏，请重新上传。") from exc
    if actual_mime != declared_mime:
        raise ChatImageUploadError("图片实际格式与文件类型不一致，请重新选择图片。")
    return extension, actual_mime, width, height
