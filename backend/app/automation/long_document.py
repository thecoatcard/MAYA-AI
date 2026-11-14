"""Assembling a long document from chapters (pure, no I/O, no model).

The pipeline (ADR-0020) writes the chapters separately; this module turns their validated blocks
into one :class:`~app.automation.model.Document` (cover, contents, chapters each on a new page,
consistent heading levels, optional "Exercises" section) and carries the page arithmetic the
expand/trim healing needs. Page counts here are ESTIMATES used to choose what to expand or drop;
the real page count always comes from the rendered PDF.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from app.automation import model as doc_model
from app.automation.acceptance import CHAPTER_TAIL_PAGES, NOTES_WORDS_PER_PAGE

# Page cost per block at the study-notes theme (A4, 10.5 pt, 1.45 line height, 16 mm margins),
# MEASURED on the real browser print (see ``tests/unit/test_long_document_render.py``, which
# re-measures them and fails when the estimate drifts by more than 20%): pure prose fills about
# 726 words per page (``acceptance.THEME_WORDS_PER_PAGE`` notes: 310 for the mix). The per-block
# costs below (code, table, callout, list, heading) are the older, looser-theme figures, so the
# mixed-content scale absorbs the denser layout.
PROSE_WORDS_PER_PAGE = 726
# Re-calibrated 2026-11 on nine 5-page fake chapters printed with the denser notes theme: real
# Edge print 50 pages; the old 555 / 0.88 pair estimated 61.8 (+24%). 726 / 0.80 is within +6%,
# -1% and -8% of the real print on the three measured documents (50, 69 and 88 real pages).
MIXED_CONTENT_SCALE = 0.80
CODE_LINE_PAGES = 0.0225
CODE_BLOCK_PAGES = 0.065
CODE_WRAP_CHARS = 95
TABLE_ROW_PAGES = 0.036
TABLE_BLOCK_PAGES = 0.06
LIST_ITEM_PAGES = 0.006
PARAGRAPH_GAP_PAGES = 0.006
CALLOUT_PAGES = 0.056
HEADING_PAGES = {1: 0.14, 2: 0.065, 3: 0.045}
TOC_LINE_PAGES = 0.032

EXERCISE_HEADINGS = re.compile(
    r"\b(exercises?|practice|practise|review questions?|quiz|self[- ]check|assignments?)\b",
    re.IGNORECASE,
)
TAKEAWAY_TITLE = "Key takeaways"


def _words(text: str) -> int:
    return len(text.split())


def block_words(block: dict[str, Any]) -> int:
    """Words in a block dict (what a word counter would count, code included)."""
    kind = block.get("type")
    if kind in ("paragraph", "callout", "quote", "heading", "code"):
        return _words(str(block.get("text") or ""))
    if kind in ("bullets", "numbered"):
        return sum(_words(str(i)) for i in block.get("items") or [])
    if kind == "table":
        cells = [*(block.get("headers") or []), *(c for r in block.get("rows") or [] for c in r)]
        return sum(_words(str(c)) for c in cells)
    return 0


def block_pages(block: dict[str, Any]) -> float:
    """Estimated pages one block takes (a ROUGH estimate; the real count comes from the PDF)."""
    return _raw_block_pages(block) * MIXED_CONTENT_SCALE


def _raw_block_pages(block: dict[str, Any]) -> float:
    kind = block.get("type")
    prose = block_words(block) / PROSE_WORDS_PER_PAGE
    if kind == "paragraph":
        return prose + PARAGRAPH_GAP_PAGES
    if kind == "callout":
        return prose + CALLOUT_PAGES
    if kind == "quote":
        return prose * 1.3 + CALLOUT_PAGES
    if kind in ("bullets", "numbered"):
        return prose + len(block.get("items") or []) * LIST_ITEM_PAGES + PARAGRAPH_GAP_PAGES
    if kind == "table":
        rows = len(block.get("rows") or []) + 1
        return rows * TABLE_ROW_PAGES + TABLE_BLOCK_PAGES + prose * 0.5
    if kind == "code":
        lines = sum(
            max(1, math.ceil(len(line) / CODE_WRAP_CHARS))
            for line in str(block.get("text") or "").split(chr(10))
        )
        return lines * CODE_LINE_PAGES + CODE_BLOCK_PAGES
    if kind == "heading":
        return HEADING_PAGES.get(int(block.get("level") or 2), 0.05)
    return 0.0


def block_page_words(block: dict[str, Any]) -> float:
    """A block's cost in page-budget words (``block_pages`` at the notes words per page)."""
    return block_pages(block) * NOTES_WORDS_PER_PAGE


def word_count(blocks: list[dict[str, Any]]) -> int:
    """Total words in the blocks (code included, as a reader or a word counter would count)."""
    return sum(block_words(b) for b in blocks)


def estimate_body_pages(blocks: list[dict[str, Any]]) -> float:
    """Estimated pages of these blocks alone (no front matter, no chapter-end waste)."""
    return sum(block_pages(b) for b in blocks)


def estimate_pages(
    chapters: list[ChapterContent],
    *,
    exercises: bool = False,
    cover: bool = True,
    page_breaks: bool = True,
) -> float:
    """Estimated page count of the assembled document: cover, contents, chapters, exercises.

    ``cover`` and ``page_breaks`` follow the form: notes have no cover page and flow on, so they
    lose the cover page and the half-empty last page of every chapter.
    """
    bodies = [chapter.render_blocks() for chapter in chapters]
    extra = exercise_blocks(chapters) if exercises else []
    entries = sum(
        1
        for blocks in (*bodies, extra)
        for b in blocks
        if b.get("type") == "heading" and int(b.get("level") or 9) <= 2
    )
    if cover:
        toc = float(max(1, math.ceil(0.06 + entries * TOC_LINE_PAGES)))
    else:
        # No cover (notes, report): the contents list flows into the content instead of taking
        # a page of its own (see html_render: nav.toc has no page break without a cover).
        toc = 0.06 + entries * TOC_LINE_PAGES
    tail = CHAPTER_TAIL_PAGES if page_breaks else 0.0
    total = (1.0 if cover else 0.0) + toc
    for blocks in bodies:
        total += estimate_body_pages(blocks) + tail
    if extra:
        total += estimate_body_pages(extra) + tail
    return total


# --------------------------------------------------------------------------- chapters
@dataclass
class ChapterContent:
    """One written chapter: validated block dicts plus its brief."""

    index: int
    title: str
    kind: str = "concept"
    target_pages: float = 5.0
    target_words: int = 2250
    blocks: list[dict[str, Any]] = field(default_factory=list)
    exercises: list[str] = field(default_factory=list)
    takeaways: list[str] = field(default_factory=list)
    dropped_sections: list[str] = field(default_factory=list)
    drop_takeaways: bool = False

    @property
    def words(self) -> int:
        return word_count(self.blocks)

    @property
    def heading(self) -> str:
        return f"Chapter {self.index}: {self.title}"[: doc_model.MAX_TITLE_CHARS]

    def render_blocks(self) -> list[dict[str, Any]]:
        """The chapter as blocks: its heading, its content, then the takeaways."""
        out: list[dict[str, Any]] = [{"type": "heading", "level": 1, "text": self.heading}]
        out.extend(normalize_heading_levels(self.blocks))
        if self.takeaways and not self.drop_takeaways:
            out.append({"type": "heading", "level": 2, "text": TAKEAWAY_TITLE})
            out.append({"type": "bullets", "items": [_clip(t) for t in self.takeaways[:12]]})
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "title": self.title,
            "kind": self.kind,
            "target_pages": self.target_pages,
            "target_words": self.target_words,
            "words": self.words,
            "blocks": self.blocks,
            "exercises": self.exercises,
            "takeaways": self.takeaways,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChapterContent:
        return cls(
            index=int(data.get("index") or 0),
            title=str(data.get("title") or "Untitled"),
            kind=str(data.get("kind") or "concept"),
            target_pages=float(data.get("target_pages") or 5.0),
            target_words=int(data.get("target_words") or 0),
            blocks=[b for b in data.get("blocks") or [] if isinstance(b, dict)],
            exercises=[str(e) for e in data.get("exercises") or []],
            takeaways=[str(t) for t in data.get("takeaways") or []],
        )


def _clip(text: str, limit: int = doc_model.MAX_LIST_ITEM_CHARS) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def normalize_heading_levels(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Inside a chapter the chapter heading is level 1: a level-1 heading becomes 2 and a level
    never jumps by more than one (so the outline stays consistent across all chapters)."""
    out: list[dict[str, Any]] = []
    last = 1
    for block in blocks:
        if block.get("type") == "heading":
            level = max(2, int(block.get("level") or 2))
            level = min(level, last + 1)
            last = level
            out.append({**block, "level": level})
        else:
            out.append(block)
    return out


def exercise_blocks(chapters: list[ChapterContent]) -> list[dict[str, Any]]:
    """The closing "Exercises" section (empty when no chapter wrote exercises)."""
    sections: list[dict[str, Any]] = []
    for chapter in chapters:
        if chapter.exercises:
            sections.append({"type": "heading", "level": 2, "text": chapter.heading})
            sections.append({"type": "numbered", "items": [_clip(e) for e in chapter.exercises]})
    if not sections:
        return []
    return [{"type": "heading", "level": 1, "text": "Exercises"}, *sections]


# --------------------------------------------------------------------------- outline
def normalize_outline(
    chapters: list[dict[str, Any]], slots: int, body_pages: float
) -> list[dict[str, Any]]:
    """Fit the model's chapter list to the planned number of chapter steps and the page budget.

    More chapters than steps: the two adjacent chapters with the fewest pages are merged until it
    fits (their subtopics are kept). Fewer: the list stays short (the spare steps write nothing).
    The page numbers are then scaled so they add up to ``body_pages``, whatever the model said.
    """
    items: list[dict[str, Any]] = [
        {
            "title": str(c.get("title") or "").strip() or f"Chapter {i + 1}",
            "subtopics": [str(s).strip() for s in c.get("subtopics") or [] if str(s).strip()],
            "kind": str(c.get("kind") or "concept"),
            "target_pages": max(1.0, float(c.get("target_pages") or 1)),
            "goal": str(c.get("goal") or "").strip(),
            "needs_code": bool(c.get("needs_code")),
            "needs_table": bool(c.get("needs_table")),
            "story_beat": str(c.get("story_beat") or "").strip()[:400],
        }
        for i, c in enumerate(chapters)
    ]
    while len(items) > max(1, slots):
        cheapest = min(
            range(len(items) - 1),
            key=lambda i: items[i]["target_pages"] + items[i + 1]["target_pages"],
        )
        first, second = items[cheapest], items.pop(cheapest + 1)
        first["title"] = f"{first['title']} and {second['title']}"[: doc_model.MAX_TITLE_CHARS - 20]
        first["subtopics"] = [*first["subtopics"], *second["subtopics"]][:14]
        first["goal"] = first["goal"] or second["goal"]
        first["needs_code"] = first["needs_code"] or second["needs_code"]
        first["needs_table"] = first["needs_table"] or second["needs_table"]
        first["story_beat"] = first["story_beat"] or second["story_beat"]
        first["target_pages"] += second["target_pages"]
    total = sum(i["target_pages"] for i in items) or 1.0
    scale = body_pages / total
    for number, item in enumerate(items, start=1):
        item["index"] = number
        item["target_pages"] = round(max(1.0, item["target_pages"] * scale), 1)
        item["target_words"] = round(item["target_pages"] * NOTES_WORDS_PER_PAGE)
    return items


# --------------------------------------------------------------------------- assembly
@dataclass
class Assembly:
    """The chapters that exist, what is missing, and what trimming removed."""

    title: str
    subtitle: str
    chapters: list[ChapterContent]
    missing: list[tuple[int, str]] = field(default_factory=list)
    with_exercises: bool = False
    drop_exercises: bool = False
    trimmed: list[str] = field(default_factory=list)
    cover_page: bool = True
    page_breaks: bool = True  # a page break before each chapter (book form); notes flow on

    def blocks(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for position, chapter in enumerate(sorted(self.chapters, key=lambda c: c.index)):
            if position and self.page_breaks:
                out.append({"type": "page_break"})
            out.extend(chapter.render_blocks())
        if self.with_exercises and not self.drop_exercises:
            extra = exercise_blocks(self.chapters)
            if extra:
                if self.page_breaks:
                    out.append({"type": "page_break"})
                out.extend(extra)
        return out

    def estimated_pages(self) -> float:
        return estimate_pages(
            self.chapters,
            exercises=self.with_exercises and not self.drop_exercises,
            cover=self.cover_page,
            page_breaks=self.page_breaks,
        )

    def total_words(self) -> int:
        return word_count(self.blocks())

    def document_dict(
        self,
        *,
        theme: str,
        author: str,
        date: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """The document dict; ``options`` (``form.render_options``) set cover, contents, density."""
        opts = options or {}
        return {
            "theme": theme,
            "title": self.title[: doc_model.MAX_TITLE_CHARS],
            "subtitle": self.subtitle[: doc_model.MAX_SHORT_CHARS] or None,
            "author": author or None,
            "date": date or None,
            "cover_page": bool(opts.get("cover_page", True)),
            "toc": bool(opts.get("toc", True)),
            "density": opts.get("density", "normal"),
            "accent_color": opts.get("accent_color"),
            "blocks": self.blocks(),
        }


def _sections(blocks: list[dict[str, Any]]) -> list[tuple[int, int, str]]:
    """``(start, end, heading)`` of each level-2 section of a chapter's blocks (end exclusive)."""
    starts = [i for i, b in enumerate(blocks) if b.get("type") == "heading" and b.get("level") == 2]
    out: list[tuple[int, int, str]] = []
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(blocks)
        out.append((start, end, str(blocks[start].get("text") or "")))
    return out


def trim_assembly(assembly: Assembly, target_pages: int) -> list[str]:
    """Drop the least important material until the estimate is at or under ``target_pages``.

    Order: the closing Exercises section; chapters' key takeaways; sections inside a chapter
    that are exercises or quizzes; then the LAST whole level-2 section of the longest chapter
    (never a partial section, never a chapter's only section). Returns what was dropped, for
    the proof; the caller re-renders and re-checks, so this only aims at ``target_pages``.
    """
    dropped: list[str] = []

    def over() -> bool:
        return assembly.estimated_pages() > target_pages

    if over() and assembly.with_exercises and not assembly.drop_exercises:
        if exercise_blocks(assembly.chapters):
            assembly.drop_exercises = True
            dropped.append("the closing Exercises section")
    for chapter in sorted(assembly.chapters, key=lambda c: -c.index):
        if over() and chapter.takeaways and not chapter.drop_takeaways:
            chapter.drop_takeaways = True
            dropped.append(f"key takeaways of chapter {chapter.index}")
    for chapter in sorted(assembly.chapters, key=lambda c: -c.index):
        while over():
            found = [s for s in _sections(chapter.blocks) if EXERCISE_HEADINGS.search(s[2])]
            if not found:
                break
            start, end, heading = found[-1]
            del chapter.blocks[start:end]
            chapter.dropped_sections.append(heading)
            dropped.append(f"section '{heading}' of chapter {chapter.index}")
    guard = 0
    while over() and guard < 200:
        guard += 1
        candidates = [c for c in assembly.chapters if len(_sections(c.blocks)) > 1]
        if not candidates:
            break
        chapter = max(candidates, key=lambda c: estimate_body_pages(c.blocks))
        start, end, heading = _sections(chapter.blocks)[-1]
        del chapter.blocks[start:end]
        chapter.dropped_sections.append(heading)
        dropped.append(f"section '{heading}' of chapter {chapter.index}")
    assembly.trimmed.extend(dropped)
    return dropped


def thinnest_chapters(chapters: list[ChapterContent], count: int) -> list[ChapterContent]:
    """The ``count`` chapters furthest below their word target (thinnest first)."""

    def ratio(chapter: ChapterContent) -> float:
        return chapter.words / chapter.target_words if chapter.target_words else 1.0

    return sorted(chapters, key=ratio)[: max(0, count)]


def chapters_to_expand(
    chapters: list[ChapterContent], shortfall_pages: float, max_chapters: int = 6
) -> int:
    """How many chapters to regenerate to cover ``shortfall_pages``: each can grow by about 70%
    of its target, at least one and at most ``max_chapters``."""
    if not chapters:
        return 0
    average = sum(c.target_pages for c in chapters) / len(chapters)
    per_chapter = max(1.0, average * 0.7)
    return max(1, min(max_chapters, len(chapters), math.ceil(shortfall_pages / per_chapter)))


__all__ = [
    "Assembly",
    "ChapterContent",
    "chapters_to_expand",
    "estimate_body_pages",
    "estimate_pages",
    "exercise_blocks",
    "normalize_heading_levels",
    "normalize_outline",
    "thinnest_chapters",
    "trim_assembly",
    "word_count",
]
