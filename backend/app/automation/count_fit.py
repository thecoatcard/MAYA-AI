"""Deterministic fitting of a deck to the slide count the user stated.

Count policy (docs/architecture/DOCUMENT_GENERATION.md): a count that differs from what the user
asked for NEVER rejects or discards the file. A deck is fitted to the stated number here, without a
model call:

* too many slides: adjacent plain bullet slides are merged (nothing is lost), then the least
  important content slides are dropped (their text moves into the speaker notes of the slide
  before). The cover (first slide), an agenda and the closing (last slide) are kept.
* too few slides: the fullest bullet slides are split in two, then clearly labelled
  "Appendix: Notes" slides are added just before the closing slide.

Pure functions on the legacy slide dicts (``layout``, ``title``, ``bullets``, ``notes`` ...); no
I/O. The caller measures the real file (the pptx engine can split an overfull slide itself) and
calls :func:`fit_slides` again with a corrected target.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.automation import model as doc_model

COVER_LAYOUTS = frozenset({"cover", "hero_cover", "title"})
SECTION_LAYOUTS = frozenset({"section", "section_break"})
_AGENDA = re.compile(r"\b(agenda|contents?|outline|overview|roadmap)\b", re.IGNORECASE)
_PLAIN_KEYS = frozenset({"layout", "title", "subtitle", "bullets", "notes", "transition"})
_SPLIT_MIN_BULLETS = 4


@dataclass
class SlideFit:
    """The fitted slides and what was done, in plain words."""

    slides: list[dict[str, Any]]
    actions: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.actions)


def _layout(slide: dict[str, Any]) -> str:
    return str(slide.get("layout") or "bullets").strip().lower()


def _title(slide: dict[str, Any]) -> str:
    return str(slide.get("title") or "").strip()


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _weight(slide: dict[str, Any]) -> int:
    """How much content a slide carries (characters of every text it holds)."""

    def size(value: Any) -> int:
        if isinstance(value, str):
            return len(value)
        if isinstance(value, dict):
            return sum(size(v) for k, v in value.items() if k != "layout")
        if isinstance(value, list | tuple):
            return sum(size(v) for v in value)
        return 0

    return size({k: v for k, v in slide.items() if k not in ("layout", "notes", "transition")})


def _is_agenda(slide: dict[str, Any]) -> bool:
    return bool(_AGENDA.search(_title(slide)))


def _plain_bullets(slide: dict[str, Any]) -> bool:
    """A slide that is only a title and bullets (it can be merged without losing a layout)."""
    if _layout(slide) != "bullets":
        return False
    return all(k in _PLAIN_KEYS or not v for k, v in slide.items())


def _as_text(slide: dict[str, Any]) -> str:
    parts = [_title(slide)]
    parts.extend(str(b) for b in slide.get("bullets") or [])
    return "; ".join(p for p in parts if p)


def _append_notes(slide: dict[str, Any], text: str) -> None:
    joined = (str(slide.get("notes") or "") + "\n" + text).strip()
    slide["notes"] = joined[: doc_model.MAX_NOTES_CHARS]


# --------------------------------------------------------------------------- too many
def _merge_once(work: list[dict[str, Any]], actions: list[str]) -> bool:
    last = len(work) - 1
    best: tuple[int, int] | None = None
    for i in range(1, last - 1):
        a, b = work[i], work[i + 1]
        if i + 1 >= last or not (_plain_bullets(a) and _plain_bullets(b)):
            continue
        if _is_agenda(a) or _is_agenda(b):
            continue
        total = len(a.get("bullets") or []) + len(b.get("bullets") or [])
        if total > doc_model.MAX_SLIDE_BULLETS:
            continue
        if best is None or total < best[0]:
            best = (total, i)
    if best is None:
        return False
    i = best[1]
    a, b = work[i], work[i + 1]
    merged = dict(a)
    merged["title"] = _clip(f"{_title(a)} & {_title(b)}", doc_model.MAX_SLIDE_TITLE_CHARS)
    merged["bullets"] = [*(a.get("bullets") or []), *(b.get("bullets") or [])]
    if b.get("notes"):
        _append_notes(merged, str(b["notes"]))
    work[i : i + 2] = [merged]
    actions.append(f"merged '{_title(a)}' and '{_title(b)}' into one slide")
    return True


def _drop_once(work: list[dict[str, Any]], actions: list[str], *, allow_agenda: bool) -> bool:
    last = len(work) - 1
    candidates = [i for i in range(1, last) if allow_agenda or not _is_agenda(work[i])]
    if not candidates:
        return False
    victim = min(
        candidates, key=lambda i: (_layout(work[i]) not in SECTION_LAYOUTS, _weight(work[i]), -i)
    )
    # Section dividers go first (they hold no content), then the thinnest content slide.
    removed = work.pop(victim)
    keeper = work[max(0, victim - 1)]
    text = _as_text(removed)
    if text:
        _append_notes(keeper, f"Removed to fit the requested slide count: {text}")
    actions.append(f"removed '{_title(removed) or 'a slide'}' (its text is in the speaker notes)")
    return True


def _shrink(work: list[dict[str, Any]], target: int, actions: list[str]) -> None:
    while len(work) > target:
        if _merge_once(work, actions):
            continue
        if _drop_once(work, actions, allow_agenda=False):
            continue
        if _drop_once(work, actions, allow_agenda=True):
            continue
        break
    if len(work) > target:
        # Only the cover and the closing are left to choose from: keep the cover first and the
        # closing last, and cut whatever is in between.
        keep = [work[0]] if target < 2 else [work[0], work[-1]]
        actions.append(f"kept only {len(keep)} slide(s) of {len(work)} to reach {target}")
        work[:] = keep[:target]


# --------------------------------------------------------------------------- too few
def _split_once(work: list[dict[str, Any]], actions: list[str]) -> bool:
    best: int | None = None
    for i, slide in enumerate(work):
        if i == 0 or _layout(slide) != "bullets":
            continue
        count = len(slide.get("bullets") or [])
        if count >= _SPLIT_MIN_BULLETS and (best is None or count > len(work[best]["bullets"])):
            best = i
    if best is None:
        return False
    slide = work[best]
    bullets = list(slide["bullets"])
    half = (len(bullets) + 1) // 2
    first = dict(slide)
    first["bullets"] = bullets[:half]
    second = {k: v for k, v in slide.items() if k not in ("notes", "bullets")}
    second["title"] = _clip(f"{_title(slide)} (cont.)", doc_model.MAX_SLIDE_TITLE_CHARS)
    second["bullets"] = bullets[half:]
    work[best : best + 1] = [first, second]
    actions.append(f"split '{_title(slide)}' into two slides")
    return True


def _pad(work: list[dict[str, Any]], count: int, actions: list[str]) -> None:
    """``count`` labelled Appendix slides just before the closing (last) slide."""
    topics = [_title(s) for s in work[1:-1] if _title(s)] or [_title(s) for s in work if _title(s)]
    covered = _clip(
        "Covered in this deck: " + "; ".join(topics[:6]), doc_model.MAX_SLIDE_BULLET_CHARS
    )
    insert_at = len(work) - 1 if len(work) >= 2 else len(work)
    for n in range(1, count + 1):
        title = "Appendix: Notes" if count == 1 else f"Appendix: Notes ({n})"
        bullets = (
            [
                "Added so the deck has the number of slides you asked for.",
                covered,
                "Use this space for your own notes or questions.",
            ]
            if n == 1
            else ["Space for your own notes or questions."]
        )
        work.insert(
            insert_at + n - 1,
            {
                "layout": "bullets",
                "title": title,
                "bullets": bullets,
                "notes": "Appendix slide added to reach the requested slide count.",
            },
        )
    actions.append(f"added {count} labelled 'Appendix: Notes' slide(s)")


def _grow(work: list[dict[str, Any]], target: int, actions: list[str]) -> None:
    while len(work) < target:
        if not _split_once(work, actions):
            break
    if len(work) < target:
        _pad(work, target - len(work), actions)


def fit_slides(slides: Sequence[dict[str, Any]], target: int) -> SlideFit:
    """``slides`` fitted to exactly ``target`` slides (``target`` is capped to the deck limit).

    Too many: merge, then drop the least important content slides; too few: split, then pad with
    labelled Appendix slides. The first slide (cover) and the last (closing) always stay.
    """
    want = max(1, min(int(target), doc_model.MAX_SLIDES))
    work = [dict(s) for s in slides]
    actions: list[str] = []
    if len(work) > want:
        _shrink(work, want, actions)
    elif len(work) < want:
        _grow(work, want, actions)
    return SlideFit(work, actions)


def slide_count_note(asked: int, got: int, *, adjusted: Sequence[str] = ()) -> str:
    """One plain sentence on the final slide count (the same wording in chat and task results)."""

    def slides(n: int) -> str:
        return f"{n} slide" if n == 1 else f"{n} slides"

    if got == asked:
        text = f"The deck has {slides(got)}, as you asked."
        if adjusted:
            text += " It was adjusted to fit: " + "; ".join(adjusted[:4]) + "."
        return text
    return f"You asked for {slides(asked)}; the deck has {slides(got)}."


__all__ = ["SlideFit", "fit_slides", "slide_count_note"]
