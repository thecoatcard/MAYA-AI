"""Deterministic guards for generated deliverables (no model call).

* ``strip_invented_contacts``: a model invents footer and contact lines (a made-up e-mail or
  link). Any e-mail address or URL that is not in the text the user (or a source
  step) supplied is removed from a deck or a document, and a line left with only a label
  ("Contact:", "Email:") goes with it.
* ``title_only_slides``: content slides that have a title and nothing else.
"""

from __future__ import annotations

import re
from typing import Any

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"(?:https?://|www\.)[^\s)>\]\"']+", re.IGNORECASE)
_TEXT_KEYS = frozenset({"text", "title", "subtitle", "body", "detail", "quote", "by", "notes"})


def _allowed(allowed_text: str) -> set[str]:
    found = {m.group(0).lower().rstrip(".,;") for m in _EMAIL.finditer(allowed_text)}
    found |= {m.group(0).lower().rstrip(".,;") for m in _URL.finditer(allowed_text)}
    return found


def _clean(text: str, allowed: set[str], removed: list[str]) -> str:
    def repl(match: re.Match[str]) -> str:
        value = match.group(0)
        if value.lower().rstrip(".,;") in allowed:
            return value
        removed.append(value)
        return ""

    out = _URL.sub(repl, _EMAIL.sub(repl, text))
    if out != text:
        out = re.sub(r"\s{2,}", " ", out).strip(" -|,;:•")
    return out


def _walk(node: Any, allowed: set[str], removed: list[str]) -> Any:
    if isinstance(node, str):
        return _clean(node, allowed, removed)
    if isinstance(node, list):
        cleaned = [_walk(x, allowed, removed) for x in node]
        # An item that was only a (now empty) contact line goes away; untouched items stay.
        return [
            c
            for c, old in zip(cleaned, node, strict=True)
            if not (isinstance(old, str) and c != old and len(c.split()) <= 2)
        ]
    if isinstance(node, dict):
        return {
            k: (
                _walk(v, allowed, removed) if (k in _TEXT_KEYS or isinstance(v, list | dict)) else v
            )
            for k, v in node.items()
        }
    return node


def strip_invented_contacts(items: list[dict[str, Any]], allowed_text: str) -> list[str]:
    """Remove e-mails and URLs absent from ``allowed_text`` from slides or blocks, in place.

    Returns the removed values (for the healing trail). A line that only held a contact label
    (two words or fewer, such as "Email:") is dropped too.
    """
    allowed = _allowed(allowed_text)
    removed: list[str] = []
    for index, item in enumerate(items):
        before = len(removed)
        cleaned = _walk(item, allowed, removed)
        if len(removed) == before:
            continue
        items[index] = cleaned
    return removed


def title_only_slides(slides: list[dict[str, Any]]) -> list[int]:
    """1-based numbers of CONTENT slides that carry a title and nothing else.

    The first slide (cover) and a closing/section slide may be title-only by design.
    """
    out: list[int] = []
    for number, slide in enumerate(slides, start=1):
        if number == 1 or slide.get("layout") in ("cover", "title", "section", "closing", "image"):
            continue
        body = [
            v
            for k, v in slide.items()
            if k not in ("layout", "title", "subtitle", "notes") and v not in (None, "", [], {})
        ]
        if not body:
            out.append(number)
    return out
