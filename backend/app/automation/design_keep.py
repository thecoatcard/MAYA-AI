"""Keep the design of a file when only its content is edited.

The chat tool loop edits a file by calling ``create_document`` again with the whole spec, so a
model that re-plans the look (or leaves ``design`` out, which falls back to the default preset)
silently restyles the file. This module carries the previous document's design into the new spec:

* a content edit keeps the previous ``theme`` and ``design`` (and the cover and table of contents
  choices); whatever look the model sent for this call is discarded;
* an explicit restyle request ("make it dark", "change the font") changes only the fields the new
  design names; every other field keeps its previous value (merge, not replace).

Pure functions on plain dicts; nothing here renders or validates a file.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any

# Words that say the user wants a different LOOK. A request that merely changes content ("add a
# slide about costs", "fix the typo in section 2") never matches, so the design stays.
_LOOK_NOUN = (
    r"(?:theme|themes|colou?rs?|palette|fonts?|typeface|typography|styles?|styling|look|"
    r"design|layout|template|background|dark\s*mode|branding|rang|colour\s*scheme)"
)
_CHANGE_VERB = (
    r"(?:change|changing|switch|switching|apply|try|swap|replace|different|another|"
    r"restyle|redesign|re-?style|re-?design|badal|badlo|badalna|karo|kar\s+do|make)"
)
_RESTYLE = re.compile(
    rf"(?i)\b(?:re-?style|re-?design|re-?theme|rebrand)\b"
    rf"|\b{_CHANGE_VERB}\b[^.!?\n]{{0,40}}\b{_LOOK_NOUN}\b"
    rf"|\b{_LOOK_NOUN}\b[^.!?\n]{{0,30}}\b(?:change|badal|badlo|different|new|update)\b"
    r"|\bmake\s+it\s+(?:look\s+)?(?:more\s+|a\s+bit\s+|much\s+)?"
    r"(?:professional|modern|colou?rful|minimal|elegant|attractive|beautiful|stylish|dark|"
    r"darker|light|lighter|bold|playful|formal|clean|fancy)\b"
    r"|\b(?:dark|light)\s+(?:mode|theme|background)\b"
    r"|\b(?:midnight|executive|minimal|notes)\s+theme\b"
    r"|\bdark\s+(?:karo|kar\s+do|banao|bana\s+do)\b"
)


def requests_restyle(text: str) -> bool:
    """True when the user explicitly asks to change the look of the file."""
    return bool(_RESTYLE.search(text or ""))


def _empty(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    return value is None or value in ("", [], {}) or (isinstance(value, int | float) and value == 0)


def _norm_title(title: str) -> str:
    return " ".join((title or "").lower().split())


def snapshot(body: Mapping[str, Any]) -> dict[str, Any]:
    """The design facts of a spec to keep in the working state (small, JSON-safe)."""
    keep: dict[str, Any] = {}
    for key in ("theme", "design", "cover_page", "toc"):
        value = body.get(key)
        if value not in (None, "", {}):
            keep[key] = copy.deepcopy(value)
    return keep


def previous_snapshot(previous: Mapping[str, Any] | None) -> dict[str, Any]:
    """The design facts of the previous document: the stored snapshot, else read from its spec."""
    if not isinstance(previous, Mapping):
        return {}
    stored = previous.get("design_state")
    if isinstance(stored, Mapping) and stored:
        return dict(stored)
    spec = previous.get("spec")
    return snapshot(spec) if isinstance(spec, Mapping) else {}


def is_same_document(
    previous: Mapping[str, Any] | None, *, fmt: str, title: str, edits_file_id: str | None
) -> bool:
    """True when this call changes the previous document (same format, and the model named its
    file id or kept its title)."""
    if not isinstance(previous, Mapping) or previous.get("format") != fmt:
        return False
    if edits_file_id and edits_file_id == previous.get("file_id"):
        return True
    return _norm_title(str(previous.get("title") or "")) == _norm_title(title) and bool(title)


def merge_design(old: Mapping[str, Any] | None, new: Mapping[str, Any] | None) -> dict[str, Any]:
    """``new`` over ``old``, field by field. The palette merges by colour; a different ``base``
    preset with no palette of its own drops the old palette (the preset's colours were asked
    for, a kept custom palette would hide them)."""
    old_d = copy.deepcopy(dict(old or {}))
    new_d = {k: v for k, v in dict(new or {}).items() if not _empty(v)}
    if not new_d:
        return old_d
    merged = old_d
    new_palette = new_d.pop("palette", None) or {}
    new_base = new_d.get("base")
    if new_base and new_base != old_d.get("base") and not new_palette:
        merged.pop("palette", None)
    palette = dict(merged.get("palette") or {})
    palette.update({k: v for k, v in dict(new_palette).items() if v})
    merged.update(new_d)
    if palette:
        merged["palette"] = palette
    return merged


def carry_design(
    body: Mapping[str, Any],
    previous: Mapping[str, Any] | None,
    *,
    fmt: str,
    title: str,
    edits_file_id: str | None,
    request: str,
) -> tuple[dict[str, Any], str | None]:
    """``body`` with the previous document's design applied, and a note (or ``None``).

    Does nothing for a first document, or for a document that is not the previous one. A content
    edit takes the previous theme and design whole; an explicit restyle request merges the new
    design over the previous one.
    """
    out = dict(body)
    if not is_same_document(previous, fmt=fmt, title=title, edits_file_id=edits_file_id):
        return out, None
    kept = previous_snapshot(previous)
    if not kept:
        return out, None
    restyle = requests_restyle(request)
    new_design = out.get("design") if isinstance(out.get("design"), Mapping) else None
    if not restyle:
        for key in ("theme", "design", "cover_page", "toc"):
            if key in kept:
                out[key] = copy.deepcopy(kept[key])
            else:
                out.pop(key, None)
        note = None
        if new_design or (body.get("theme") and body.get("theme") != kept.get("theme")):
            note = "the existing design was kept (no restyle was asked for)"
        return out, note
    theme_changed = bool(out.get("theme")) and out.get("theme") != kept.get("theme")
    design = merge_design(kept.get("design"), new_design)
    if theme_changed and not (new_design or {}).get("base"):
        # A new preset was asked for through 'theme': it replaces the old palette and base.
        design = merge_design({**design, "palette": {}, "base": ""}, {"base": out["theme"]})
    if design:
        out["design"] = design
    for key in ("cover_page", "toc"):
        if key not in out and key in kept:
            out[key] = kept[key]
    if "theme" not in out and kept.get("theme"):
        out["theme"] = kept["theme"]
    return out, "only the requested design fields were changed"
