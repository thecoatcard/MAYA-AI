"""Deterministic per-slide repairs for decks, run before rendering and by the renderer.

Two kinds of repair, both reported as notes and never silent:

* item limits per layout (for example at most 6 bullets, 3 stat numbers, 16 code lines): an
  over-full slide is split into continuation slides titled "... (cont.)";
* text that does not fit even at the smallest font: the renderer calls :func:`halve` and renders
  the two halves as continuation slides.

Splitting never changes the words, only the order and the grouping of the same items. The first
part of a split keeps the speaker notes; continuation slides do not repeat them.
"""

from __future__ import annotations

from typing import Any

from app.automation.model import MAX_SLIDE_TITLE_CHARS, Deck, Slide

# Items per slide for each layout that carries a list. The schema allows a little more in a few
# places; anything over these limits is split into continuation slides.
LAYOUT_ITEM_LIMITS: dict[str, int] = {
    "bullets": 6,
    "closing": 6,
    "cards": 4,
    "stat_callout": 3,
    "timeline": 6,
    "code": 16,
    "table": 10,
    # Library layouts (the same data fields as the classic layout in the comment).
    "stat_grid": 4,  # metrics
    "icon_cards": 4,  # cards
    "process": 5,  # steps
    "image_text": 6,  # bullets
}
_BULLET_LAYOUTS = ("bullets", "closing", "image_text")
_CARD_LAYOUTS = ("cards", "icon_cards")
_STEP_LAYOUTS = ("timeline", "process")
_METRIC_LAYOUTS = ("stat_callout", "stat_grid")
CONT_SUFFIX = " (cont.)"


def _chunks[T](items: list[T], size: int) -> list[list[T]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _continuation_title(title: str) -> str:
    return title[: MAX_SLIDE_TITLE_CHARS - len(CONT_SUFFIX)].rstrip() + CONT_SUFFIX


def _part(slide: Slide, index: int, update: dict[str, Any]) -> Slide:
    """One part of a split slide. Continuations get the "(cont.)" title and no notes."""
    changes: dict[str, Any] = dict(update)
    if index > 0:
        changes["title"] = _continuation_title(slide.title)
        changes["notes"] = None
    return slide.model_copy(update=changes)


def _code_parts(slide: Slide, size: int) -> list[Slide]:
    assert slide.code is not None
    parts: list[Slide] = []
    for index, lines in enumerate(_chunks(slide.code.text.split("\n"), size)):
        code = slide.code.model_copy(update={"text": "\n".join(lines)})
        parts.append(_part(slide, index, {"code": code}))
    return parts


def _column_parts(slide: Slide, left: list[str], right: list[str]) -> list[Slide]:
    assert slide.left is not None and slide.right is not None
    cut_l, cut_r = (len(left) + 1) // 2, (len(right) + 1) // 2
    first = _part(
        slide,
        0,
        {
            "left": slide.left.model_copy(update={"bullets": left[:cut_l]}),
            "right": slide.right.model_copy(update={"bullets": right[:cut_r]}),
        },
    )
    second = _part(
        slide,
        1,
        {
            "left": slide.left.model_copy(update={"bullets": left[cut_l:]}),
            "right": slide.right.model_copy(update={"bullets": right[cut_r:]}),
        },
    )
    return [first, second]


def _table_parts(slide: Slide, size: int) -> list[Slide]:
    assert slide.table is not None
    head = slide.table.headers
    return [
        _part(slide, index, {"table": {"headers": head, "rows": rows}})
        for index, rows in enumerate(_chunks(slide.table.rows, size))
    ]


def halve(slide: Slide) -> list[Slide] | None:
    """Two continuation slides with the items split in half, or ``None`` when the slide holds
    nothing that can be split (a single item, or a layout without a list)."""
    layout = slide.layout
    if layout in _BULLET_LAYOUTS and len(slide.bullets) > 1:
        cut = (len(slide.bullets) + 1) // 2
        return [
            _part(slide, 0, {"bullets": slide.bullets[:cut]}),
            _part(slide, 1, {"bullets": slide.bullets[cut:]}),
        ]
    if layout in _CARD_LAYOUTS and len(slide.cards) > 1:
        cut = (len(slide.cards) + 1) // 2
        return [
            _part(slide, 0, {"cards": slide.cards[:cut]}),
            _part(slide, 1, {"cards": slide.cards[cut:]}),
        ]
    if layout in _STEP_LAYOUTS and len(slide.steps) > 1:
        cut = (len(slide.steps) + 1) // 2
        return [
            _part(slide, 0, {"steps": slide.steps[:cut]}),
            _part(slide, 1, {"steps": slide.steps[cut:]}),
        ]
    if layout in _METRIC_LAYOUTS and len(slide.metrics) > 1:
        cut = (len(slide.metrics) + 1) // 2
        return [
            _part(slide, 0, {"metrics": slide.metrics[:cut]}),
            _part(slide, 1, {"metrics": slide.metrics[cut:]}),
        ]
    if layout == "table" and slide.table is not None and len(slide.table.rows) > 1:
        return _table_parts(slide, (len(slide.table.rows) + 1) // 2)
    if layout == "code" and slide.code is not None and "\n" in slide.code.text:
        return _code_parts(slide, (slide.code.text.count("\n") + 2) // 2)
    if layout in ("two_column", "comparison") and slide.left and slide.right:
        left, right = slide.left.bullets, slide.right.bullets
        if len(left) > 1 and len(right) > 1:
            return _column_parts(slide, left, right)
    return None


def _count(slide: Slide) -> int | None:
    """How many items the slide holds in its layout's list, or ``None`` for a layout without
    a limit."""
    layout = slide.layout
    if layout not in LAYOUT_ITEM_LIMITS:
        return None
    if layout in _BULLET_LAYOUTS:
        return len(slide.bullets)
    if layout in _CARD_LAYOUTS:
        return len(slide.cards)
    if layout in _METRIC_LAYOUTS:
        return len(slide.metrics)
    if layout in _STEP_LAYOUTS:
        return len(slide.steps)
    if layout == "code":
        return slide.code.text.count("\n") + 1 if slide.code else 0
    return len(slide.table.rows) if slide.table else 0


def _split_over_limit(slide: Slide) -> list[Slide]:
    count = _count(slide)
    limit = LAYOUT_ITEM_LIMITS.get(slide.layout)
    if count is None or limit is None or count <= limit:
        return [slide]
    layout = slide.layout
    if layout in _BULLET_LAYOUTS:
        return [
            _part(slide, i, {"bullets": chunk})
            for i, chunk in enumerate(_chunks(slide.bullets, limit))
        ]
    if layout in _CARD_LAYOUTS:
        return [_part(slide, i, {"cards": c}) for i, c in enumerate(_chunks(slide.cards, limit))]
    if layout in _METRIC_LAYOUTS:
        return [
            _part(slide, i, {"metrics": m}) for i, m in enumerate(_chunks(slide.metrics, limit))
        ]
    if layout in _STEP_LAYOUTS:
        return [_part(slide, i, {"steps": s}) for i, s in enumerate(_chunks(slide.steps, limit))]
    if layout == "code":
        return _code_parts(slide, limit)
    return _table_parts(slide, limit)


def check_deck(deck: Deck) -> tuple[Deck, list[str]]:
    """The deck with every over-full slide split, and a note for each split.

    Notes name the slide by its position in the deck as written (1-based). The caller renders the
    returned deck; text that still does not fit is split by the renderer and reported there.
    """
    notes: list[str] = []
    slides: list[Slide] = []
    for number, slide in enumerate(deck.slides, start=1):
        parts = _split_over_limit(slide)
        if len(parts) > 1:
            limit = LAYOUT_ITEM_LIMITS[slide.layout]
            unit = "lines" if slide.layout == "code" else "items"
            notes.append(
                f"slide {number} ({slide.layout}) held more than {limit} {unit}; "
                f"split into {len(parts)} slides"
            )
        slides.extend(parts)
    if not notes:
        return deck, notes
    return deck.model_copy(update={"slides": slides}), notes
