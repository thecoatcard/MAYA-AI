"""A small, byte-exact ``multipart/form-data`` reader (no third-party parser is installed).

The upload endpoint reads the already size-bounded request body and splits it on the boundary.
Only what uploads need is supported: ``Content-Disposition: form-data`` parts with a ``name`` and
an optional ``filename`` (plain or RFC 5987 ``filename*``). Anything malformed raises
:class:`MultipartError` (the endpoint answers 400).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote

MAX_PARTS = 64
_BOUNDARY = re.compile(r'boundary=(?:"([^"]{1,200})"|([^;\s]{1,200}))', re.IGNORECASE)
_NAME = re.compile(r'(?<![\w*])name="((?:[^"\\]|\\.)*)"', re.IGNORECASE)
_FILENAME = re.compile(r'filename="((?:[^"\\]|\\.)*)"', re.IGNORECASE)
_FILENAME_STAR = re.compile(r"filename\*=(?:UTF-8|utf-8)''([^;\s]+)")


class MultipartError(ValueError):
    """The body is not a valid multipart/form-data message."""


@dataclass(frozen=True, slots=True)
class FormPart:
    name: str
    filename: str | None
    data: bytes


def _unescape(value: str) -> str:
    return value.replace('\\"', '"').replace("\\\\", "\\")


def parse_multipart(content_type: str, body: bytes) -> list[FormPart]:
    if not content_type.lower().startswith("multipart/form-data"):
        raise MultipartError("expected multipart/form-data")
    match = _BOUNDARY.search(content_type)
    if match is None:
        raise MultipartError("missing boundary")
    delimiter = b"--" + (match.group(1) or match.group(2)).encode("ascii", "ignore")
    chunks = body.split(delimiter)
    if len(chunks) < 3:
        raise MultipartError("no parts")
    parts: list[FormPart] = []
    for chunk in chunks[1:]:
        if chunk.startswith(b"--"):
            break  # closing delimiter
        if not chunk.startswith(b"\r\n"):
            raise MultipartError("malformed part")
        chunk = chunk[2:]
        split = chunk.find(b"\r\n\r\n")
        if split < 0:
            raise MultipartError("part without headers")
        header_text = chunk[:split].decode("utf-8", errors="replace")
        content = chunk[split + 4 :]
        if content.endswith(b"\r\n"):
            content = content[:-2]
        name: str | None = None
        filename: str | None = None
        for line in header_text.split("\r\n"):
            key, _, value = line.partition(":")
            if key.strip().lower() != "content-disposition":
                continue
            if "form-data" not in value.lower():
                raise MultipartError("not form-data")
            found = _NAME.search(value)
            name = _unescape(found.group(1)) if found else None
            star = _FILENAME_STAR.search(value)
            plain = _FILENAME.search(value)
            if star:
                filename = unquote(star.group(1), encoding="utf-8", errors="replace")
            elif plain:
                filename = _unescape(plain.group(1))
        if name is None:
            raise MultipartError("part without a name")
        parts.append(FormPart(name=name, filename=filename, data=content))
        if len(parts) > MAX_PARTS:
            raise MultipartError("too many parts")
    if not parts:
        raise MultipartError("no parts")
    return parts
