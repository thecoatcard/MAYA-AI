"""Turn a parsed message into safe text: the plain-text body (HTML stripped when that is all
there is), correctly decoded (UTF-8, Hindi and other charsets), capped, plus attachment names,
types and sizes. Attachments are never downloaded or opened."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from email.message import Message
from html import unescape
from html.parser import HTMLParser
from typing import Any, Final

from app.mail.imap import clean_header

_BLOCK_TAGS: Final = frozenset(
    "p div br li tr table ul ol h1 h2 h3 h4 h5 h6 blockquote pre hr section article".split()
)
_SKIP_TAGS: Final = frozenset({"script", "style", "head", "title"})
_CONTROL: Final = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    """Visible text of an HTML mail: scripts and styles dropped, block tags become line breaks."""
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
        text = "".join(parser.parts)
    except Exception:  # noqa: BLE001 - broken HTML: fall back to a crude tag strip
        text = unescape(re.sub(r"<[^>]*>", " ", html))
    return tidy(text)


def tidy(text: str) -> str:
    """Control characters out, runs of spaces and blank lines collapsed."""
    text = _CONTROL.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    lines = [" ".join(line.split()) for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _decode_part(part: Message[str, Any]) -> str:
    payload = part.get_payload(decode=True)
    if not isinstance(payload, bytes):
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


@dataclass(frozen=True, slots=True)
class AttachmentInfo:
    name: str
    content_type: str
    size: int


@dataclass(slots=True)
class MessageText:
    text: str
    truncated: bool
    attachments: list[AttachmentInfo] = field(default_factory=list)
    source: str = "plain"  # "plain", "html" or "none"


def extract(message: Message[str, Any], max_chars: int) -> MessageText:
    plain: list[str] = []
    html: list[str] = []
    attachments: list[AttachmentInfo] = []
    for part in message.walk():
        if part.is_multipart():
            continue
        content_type = part.get_content_type()
        filename = part.get_filename()
        if part.get_content_disposition() == "attachment" or filename:
            payload = part.get_payload(decode=True)
            attachments.append(
                AttachmentInfo(
                    name=clean_header(filename) or "(unnamed)",
                    content_type=content_type,
                    size=len(payload) if isinstance(payload, bytes) else 0,
                )
            )
        elif content_type == "text/plain":
            plain.append(_decode_part(part))
        elif content_type == "text/html":
            html.append(_decode_part(part))
    plain_text = tidy("\n\n".join(plain))
    if plain_text:
        text, source = plain_text, "plain"
    else:
        text = tidy("\n\n".join(html_to_text(h) for h in html))
        source = "html" if text else "none"
    truncated = len(text) > max_chars
    return MessageText(text[:max_chars], truncated, attachments, source)
