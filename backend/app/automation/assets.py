"""Workspace-relative image loading shared by the PDF, PPTX and DOCX renderers."""

from __future__ import annotations

import base64
import binascii
from pathlib import Path

from app.automation.html_render import ImageResolver

MAX_ASSET_BYTES = 5 * 1024 * 1024
MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def make_image_resolver(assets_root: Path | str | None) -> ImageResolver | None:
    """Resolve workspace-relative image paths to ``data:`` URIs, never leaving ``assets_root``."""
    if assets_root is None:
        return None
    root = Path(assets_root).resolve()

    def resolve(relative: str) -> str | None:
        mime = MIME_BY_SUFFIX.get(Path(relative).suffix.lower())
        if mime is None:
            return None
        try:
            candidate = (root / relative).resolve()
            candidate.relative_to(root)
            if not candidate.is_file() or candidate.stat().st_size > MAX_ASSET_BYTES:
                return None
            data = candidate.read_bytes()
        except (OSError, ValueError):
            return None
        return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"

    return resolve


def decode_data_uri(src: str) -> bytes | None:
    """Bytes of a base64 ``data:`` URI, or ``None`` if it is malformed."""
    try:
        return base64.b64decode(src.split(",", 1)[1], validate=True)
    except (IndexError, binascii.Error, ValueError):
        return None
