"""A tiny, safe inline markup parser: ``**bold**``, ``*italic*``, `` `code` `` and links.

The parser never produces HTML. It produces :class:`Span` values; each renderer turns spans into
its own format and escapes text itself. Links are kept only for ``http``, ``https`` and
``mailto`` targets. ``javascript:``, ``data:`` and every other scheme degrade to plain text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

ALLOWED_LINK_SCHEMES = frozenset({"http", "https", "mailto"})
_MAX_DEPTH = 3

_CODE = re.compile(r"`([^`\n]+)`")
_LINK = re.compile(r"\[([^\]\n]{1,300})\]\(([^()\s]{1,2000})\)")
_BOLD_ITALIC = re.compile(r"\*\*\*(?=\S)(.+?)(?<=\S)\*\*\*", re.DOTALL)
_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", re.DOTALL)
_ITALIC = re.compile(r"(?<![*\w])\*(?=[^\s*])([^*\n]+?)(?<=[^\s*])\*(?![*\w])")


@dataclass(frozen=True)
class Span:
    text: str
    bold: bool = False
    italic: bool = False
    code: bool = False
    href: str | None = None


def safe_href(url: str) -> str | None:
    """Return ``url`` if it is an allowed link target, else ``None``."""
    candidate = url.strip()
    if not candidate or any(ord(ch) < 0x20 or ch.isspace() for ch in candidate):
        return None
    try:
        scheme = urlsplit(candidate).scheme.lower()
    except ValueError:
        return None
    if scheme not in ALLOWED_LINK_SCHEMES:
        return None
    return candidate


def _merge(spans: list[Span]) -> list[Span]:
    merged: list[Span] = []
    for span in spans:
        if not span.text:
            continue
        if merged:
            prev = merged[-1]
            same = (prev.bold, prev.italic, prev.code, prev.href) == (
                span.bold,
                span.italic,
                span.code,
                span.href,
            )
            if same:
                merged[-1] = Span(
                    prev.text + span.text, prev.bold, prev.italic, prev.code, prev.href
                )
                continue
        merged.append(span)
    return merged


def _parse(text: str, bold: bool, italic: bool, href: str | None, depth: int) -> list[Span]:
    spans: list[Span] = []
    plain: list[str] = []
    i = 0
    n = len(text)

    def flush() -> None:
        if plain:
            spans.append(Span("".join(plain), bold, italic, False, href))
            plain.clear()

    while i < n:
        ch = text[i]
        if ch == "`":
            m = _CODE.match(text, i)
            if m:
                flush()
                spans.append(Span(m.group(1), bold, italic, True, href))
                i = m.end()
                continue
        elif ch == "[" and href is None:
            m = _LINK.match(text, i)
            if m:
                target = safe_href(m.group(2))
                flush()
                if target is None:
                    spans.extend(_parse(m.group(1), bold, italic, None, depth + 1))
                else:
                    spans.extend(_parse(m.group(1), bold, italic, target, depth + 1))
                i = m.end()
                continue
        elif ch == "*" and depth < _MAX_DEPTH:
            m = _BOLD_ITALIC.match(text, i)
            if m:
                flush()
                spans.extend(_parse(m.group(1), True, True, href, depth + 1))
                i = m.end()
                continue
            m = _BOLD.match(text, i)
            if m:
                flush()
                spans.extend(_parse(m.group(1), True, italic, href, depth + 1))
                i = m.end()
                continue
            m = _ITALIC.match(text, i)
            if m:
                flush()
                spans.extend(_parse(m.group(1), bold, True, href, depth + 1))
                i = m.end()
                continue
        plain.append(ch)
        i += 1
    flush()
    return spans


def parse_inline(text: str) -> list[Span]:
    """Parse inline markup into spans. Unmatched markers stay as literal text."""
    return _merge(_parse(text, False, False, None, 0))


def plain_text(text: str) -> str:
    """The text with inline markup removed (link targets dropped)."""
    return "".join(span.text for span in parse_inline(text))


_XML_ILLEGAL = re.compile(
    "[" + "".join(chr(c) for c in (*range(0, 9), 11, 12, *range(14, 32), 0xFFFE, 0xFFFF)) + "]"
)


def xml_safe(text: str) -> str:
    """Remove characters that are not allowed in XML 1.0 (OOXML would reject the file)."""
    return _XML_ILLEGAL.sub("", text)
