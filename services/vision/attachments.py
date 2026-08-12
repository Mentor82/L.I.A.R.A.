"""Secure normalization of chat image attachments before orchestration."""

from __future__ import annotations

import base64
import hashlib
import io
from pathlib import Path

try:
    from PIL import Image, UnidentifiedImageError
except ImportError:
    Image = None  # type: ignore

    class UnidentifiedImageError(Exception):  # type: ignore
        pass

from services.contracts import ChatAttachment


_FORMAT_MEDIA = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp", "BMP": "image/bmp"}


def is_image_attachment(attachment: ChatAttachment) -> bool:
    return str(attachment.media_type or "").lower().startswith("image/")


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _read_image_bytes(attachment: ChatAttachment, sandbox_root: Path, max_bytes: int) -> bytes:
    if attachment.content_base64:
        try:
            raw = base64.b64decode(attachment.content_base64, validate=True)
        except Exception as exc:
            raise ValueError(f"Invalid base64 image attachment: {attachment.name or 'image'}") from exc
    else:
        stored = str((attachment.metadata or {}).get("stored_path_local") or "").strip()
        if not stored:
            if attachment.content_url:
                raise ValueError("Remote image URLs are not fetched at the API trust boundary")
            raise ValueError(f"Image payload missing: {attachment.name or 'image'}")
        path = Path(stored).resolve()
        root = sandbox_root.resolve()
        if not _is_within(path, root) or not path.is_file():
            raise ValueError("Image attachment path is outside the active sandbox or unavailable")
        raw = path.read_bytes()
    if not raw or len(raw) > max_bytes:
        raise ValueError(f"Image attachment must be between 1 and {max_bytes} bytes")
    return raw


def normalize_image_attachments(
    attachments: list[ChatAttachment],
    *,
    sandbox_root: Path,
    max_bytes: int = 5 * 1024 * 1024,
    max_pixels: int = 16_000_000,
) -> tuple[list[ChatAttachment], dict[int, bytes]]:
    """Validate image content, bind its hash/dimensions, and inline it transiently."""
    normalized: list[ChatAttachment] = []
    raw_images: dict[int, bytes] = {}
    image_count = 0
    for index, attachment in enumerate(attachments):
        if not is_image_attachment(attachment):
            normalized.append(attachment)
            continue
        image_count += 1
        if image_count > 4:
            raise ValueError("At most four images are supported per vision request")
        raw = _read_image_bytes(attachment, sandbox_root, max_bytes)
        try:
            with Image.open(io.BytesIO(raw)) as image:
                image.verify()
            with Image.open(io.BytesIO(raw)) as image:
                width, height = image.size
                media_type = _FORMAT_MEDIA.get(str(image.format or "").upper())
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise ValueError(f"Unsupported or invalid image: {attachment.name or 'image'}") from exc
        if not media_type:
            raise ValueError("Only JPEG, PNG, WebP and BMP images are supported")
        if width * height > max_pixels:
            raise ValueError(f"Image exceeds the {max_pixels} pixel safety limit")
        metadata = dict(attachment.metadata or {})
        metadata.update({
            "vision": {
                "sha256": hashlib.sha256(raw).hexdigest(),
                "width": width,
                "height": height,
                "media_type_detected": media_type,
                "normalized": True,
            }
        })
        normalized.append(attachment.model_copy(update={
            "media_type": media_type,
            "content_base64": base64.b64encode(raw).decode("ascii"),
            "size_bytes": len(raw),
            "metadata": metadata,
        }))
        raw_images[index] = raw
    return normalized, raw_images
