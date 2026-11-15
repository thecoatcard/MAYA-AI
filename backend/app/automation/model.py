"""Structured, size-bounded document models the AI fills in and the renderers consume.

The AI never writes HTML or file markup. It produces a :class:`Document` (blocks) or a
:class:`Deck` (slides); the renderers in this package turn those into PDF, DOCX and PPTX with
fixed, escaped templates. Every limit below is enforced at validation time so an oversized or
malformed model output fails with a precise message instead of producing a broken file.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationError,
    field_validator,
    model_validator,
)

from app.automation.deck_transitions import TRANSITION_NAMES, canonical_transition
from app.automation.normalize import normalize_blocks, normalize_slides
from app.automation.themes import THEME_NAMES

# --------------------------------------------------------------------------- limits
# Long study material (about 60 to 100 pages, see ADR-0020): a page holds roughly 8 to 14 blocks
# (headings, paragraphs, lists, code, tables), so 1500 blocks covers 100 pages with headroom while
# a hostile model output stays bounded. The single-call document prompt still asks for far fewer.
MAX_BLOCKS = 1_500
MAX_TITLE_CHARS = 200
MAX_SHORT_CHARS = 200
MAX_PARAGRAPH_CHARS = 5_000
MAX_LIST_ITEMS = 50
MAX_LIST_ITEM_CHARS = 1_000
MAX_TABLE_ROWS = 200
MAX_TABLE_COLS = 12
MAX_CELL_CHARS = 500
MAX_CODE_CHARS = 8_000
MAX_METRICS = 6
MAX_IMAGE_REF_CHARS = 7_000_000  # a base64 data URI of roughly 5 MB of image bytes
# Characters across all blocks. A prose page is about 3 000 characters and a dense code or table
# page about 6 000, so 800 000 holds 100 dense pages; per-block caps above are unchanged and the
# whole model is a few MB at most, which is safe to keep in memory.
MAX_DOCUMENT_CHARS = 800_000

MAX_SLIDES = 60
MAX_SLIDE_BULLETS = 8
MAX_SLIDE_BULLET_CHARS = 160
MAX_SLIDE_TITLE_CHARS = 100
MAX_COLUMN_BULLETS = 6
MAX_COLUMN_BULLET_CHARS = 110
MAX_CARD_BODY_CHARS = 400
MAX_STEP_DETAIL_CHARS = 240
MAX_LABEL_CHARS = 100
MAX_ITEM_TITLE_CHARS = 80
MAX_SLIDE_CARDS = 4
MAX_SLIDE_METRICS = 4
MAX_SLIDE_STEPS = 6
MAX_SLIDE_TABLE_ROWS = 10
MAX_SLIDE_TABLE_COLS = 6
MAX_SLIDE_TEXT_CHARS = 600
MAX_NOTES_CHARS = 2_000

_DATA_URI = re.compile(r"^data:image/(?:png|jpeg|gif|webp);base64,[A-Za-z0-9+/]+={0,2}$")
_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp")
_CODE_LANGUAGE = re.compile(r"^[A-Za-z0-9_+#.-]{0,30}$")

BLOCK_TYPES: tuple[str, ...] = (
    "heading",
    "paragraph",
    "bullets",
    "numbered",
    "table",
    "callout",
    "metrics",
    "quote",
    "image",
    "code",
    "page_break",
)
# The closed slide-layout list the AI picks from (also the tool schema enum). Legacy names are
# accepted on input and mapped to these: "title" -> "cover", "metrics" -> "stat_callout".
SLIDE_LAYOUTS: tuple[str, ...] = (
    "cover",
    "section",
    "bullets",
    "two_column",
    "cards",
    "stat_callout",
    "timeline",
    "comparison",
    "table",
    "code",
    "quote",
    "closing",
    "image",
    # Added with the slide theme library; the 13 above never change.
    "hero_cover",
    "section_break",
    "stat_grid",
    "icon_cards",
    "process",
    "image_text",
    "chart",
)
LEGACY_SLIDE_LAYOUTS: dict[str, str] = {"title": "cover", "metrics": "stat_callout"}
_TITLE_REQUIRED = frozenset(
    {
        "cover",
        "section",
        "closing",
        "bullets",
        "two_column",
        "cards",
        "stat_callout",
        "timeline",
        "comparison",
        "table",
        "code",
        "hero_cover",
        "section_break",
        "stat_grid",
        "icon_cards",
        "process",
        "image_text",
        "chart",
    }
)
MAX_CODE_LINES = 16
MAX_CODE_LINE_CHARS = 110
MAX_SLIDE_CODE_CHARS = 1_800
MAX_SECTION_NUMBER_CHARS = 12
MAX_CARD_GLYPH_CHARS = 8
MAX_ICON_CHARS = 24
MAX_CHART_CATEGORIES = 12
MAX_CHART_SERIES = 4
CHART_TYPES: tuple[str, ...] = ("column", "bar", "line", "doughnut")


def _scalar_to_text(value: Any) -> Any:
    """Accept a number where text is expected (model output often gives ``42`` for ``"42"``)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return str(value)
    return value


Text = Annotated[str, BeforeValidator(_scalar_to_text)]


class _Strict(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", str_strip_whitespace=True)


def validate_image_ref(value: str) -> str:
    """An image is a base64 data URI (png/jpeg/gif/webp) or a workspace-relative path."""
    if not value:
        raise ValueError("image src must not be empty")
    lowered = value.lower()
    if lowered.startswith("data:"):
        if not _DATA_URI.match(value):
            raise ValueError("image data URI must be base64 png, jpeg, gif or webp")
        return value
    if "://" in value or lowered.startswith(("//", "http:", "https:", "file:", "javascript:")):
        raise ValueError("image src must be a data URI or a workspace-relative path, not a URL")
    if value.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", value):
        raise ValueError("image path must be workspace-relative, not absolute")
    if ".." in re.split(r"[\\/]", value) or "\x00" in value:
        raise ValueError("image path must not contain '..'")
    if not lowered.endswith(_IMAGE_EXTENSIONS):
        raise ValueError("image path must end in .png, .jpg, .jpeg, .gif or .webp")
    if len(value) > 500:
        raise ValueError("image path is too long")
    return value


class Metric(_Strict):
    value: Annotated[Text, Field(min_length=1, max_length=40)]
    label: Annotated[Text, Field(min_length=1, max_length=MAX_LABEL_CHARS)]


# --------------------------------------------------------------------------- document blocks
class HeadingBlock(_Strict):
    type: Literal["heading"] = "heading"
    level: Annotated[int, Field(ge=1, le=3)] = 1
    text: Annotated[str, Field(min_length=1, max_length=MAX_TITLE_CHARS)]


class ParagraphBlock(_Strict):
    type: Literal["paragraph"] = "paragraph"
    text: Annotated[str, Field(min_length=1, max_length=MAX_PARAGRAPH_CHARS)]


class BulletsBlock(_Strict):
    type: Literal["bullets"] = "bullets"
    items: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=MAX_LIST_ITEM_CHARS)]],
        Field(min_length=1, max_length=MAX_LIST_ITEMS),
    ]


class NumberedBlock(_Strict):
    type: Literal["numbered"] = "numbered"
    items: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=MAX_LIST_ITEM_CHARS)]],
        Field(min_length=1, max_length=MAX_LIST_ITEMS),
    ]


def _check_rectangular(headers: list[str], rows: list[list[str]]) -> None:
    width = len(headers)
    for index, row in enumerate(rows, start=1):
        if len(row) != width:
            raise ValueError(
                f"table row {index} has {len(row)} cells but there are {width} headers"
            )


class TableBlock(_Strict):
    type: Literal["table"] = "table"
    headers: Annotated[
        list[Annotated[Text, Field(max_length=MAX_CELL_CHARS)]],
        Field(min_length=1, max_length=MAX_TABLE_COLS),
    ]
    rows: Annotated[
        list[list[Annotated[Text, Field(max_length=MAX_CELL_CHARS)]]],
        Field(max_length=MAX_TABLE_ROWS),
    ] = []
    caption: Annotated[str, Field(max_length=MAX_SHORT_CHARS)] | None = None

    @model_validator(mode="after")
    def _rectangular(self) -> TableBlock:
        _check_rectangular(self.headers, self.rows)
        return self


class CalloutBlock(_Strict):
    type: Literal["callout"] = "callout"
    kind: Literal["info", "warning", "success"] = "info"
    text: Annotated[str, Field(min_length=1, max_length=MAX_PARAGRAPH_CHARS)]


class MetricsBlock(_Strict):
    type: Literal["metrics"] = "metrics"
    items: Annotated[list[Metric], Field(min_length=1, max_length=MAX_METRICS)]


class QuoteBlock(_Strict):
    type: Literal["quote"] = "quote"
    text: Annotated[str, Field(min_length=1, max_length=MAX_PARAGRAPH_CHARS)]
    by: Annotated[str, Field(max_length=MAX_SHORT_CHARS)] | None = None


class ImageBlock(_Strict):
    type: Literal["image"] = "image"
    src: Annotated[str, Field(max_length=MAX_IMAGE_REF_CHARS)]
    alt: Annotated[str, Field(min_length=1, max_length=MAX_SHORT_CHARS)]
    caption: Annotated[str, Field(max_length=MAX_SHORT_CHARS)] | None = None

    @field_validator("src")
    @classmethod
    def _check_src(cls, value: str) -> str:
        return validate_image_ref(value)


class CodeBlock(_Strict):
    type: Literal["code"] = "code"
    language: str = ""
    text: Annotated[str, Field(min_length=1, max_length=MAX_CODE_CHARS)]

    @field_validator("language")
    @classmethod
    def _check_language(cls, value: str) -> str:
        if not _CODE_LANGUAGE.match(value):
            raise ValueError("code language must be a short identifier such as 'python'")
        return value


class PageBreakBlock(_Strict):
    type: Literal["page_break"] = "page_break"


Block = Annotated[
    HeadingBlock
    | ParagraphBlock
    | BulletsBlock
    | NumberedBlock
    | TableBlock
    | CalloutBlock
    | MetricsBlock
    | QuoteBlock
    | ImageBlock
    | CodeBlock
    | PageBreakBlock,
    Field(discriminator="type"),
]


def _check_theme(value: str) -> str:
    if value not in THEME_NAMES:
        raise ValueError(f"unknown theme '{value}'; allowed themes: {', '.join(THEME_NAMES)}")
    return value


def _block_text_size(block: Any) -> int:
    total = 0
    for key in ("text", "caption", "by"):
        total += len(getattr(block, key, None) or "")
    for item in getattr(block, "items", None) or []:
        total += len(item) if isinstance(item, str) else len(item.value) + len(item.label)
    for header in getattr(block, "headers", None) or []:
        total += len(header)
    for row in getattr(block, "rows", None) or []:
        total += sum(len(cell) for cell in row)
    return total


class Document(_Strict):
    """A report-style document: title page data plus an ordered list of blocks."""

    theme: str = "executive"
    title: Annotated[str, Field(min_length=1, max_length=MAX_TITLE_CHARS)]
    subtitle: Annotated[str, Field(max_length=MAX_SHORT_CHARS)] | None = None
    author: Annotated[str, Field(max_length=MAX_SHORT_CHARS)] | None = None
    date: Annotated[str, Field(max_length=40)] | None = None
    # Study-material layout: a full cover page and a contents list (level 1 and 2 headings, with
    # real page numbers in the PDF when they could be measured). Off by default: reports keep
    # the compact title block.
    cover_page: bool = False
    toc: bool = False
    # Render option of the healing: "compact" prints denser (smaller gaps and margins) so a long
    # document with enough words but too many pages can fit its page band without losing content.
    density: Literal["normal", "compact"] = "normal"
    # Page fitting (``automation.page_fit``): -3 (densest) to 3 (loosest), 0 = the theme as it is.
    # Set by the engine when the user stated an exact page count; see ``design.fit_sizes``.
    fit_step: Annotated[int, Field(ge=-3, le=3)] = 0
    # The user's accent colour (``#RRGGBB``) replaces the theme's accent, links and headings tint.
    accent_color: Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{6}$")] | None = None
    # The model's per-request design (``automation.design.DesignSpec`` as a dict). It is
    # validated and contrast-repaired when the theme is built; ``theme`` is its fallback preset.
    design: dict[str, Any] | None = None
    blocks: Annotated[list[Block], Field(min_length=1, max_length=MAX_BLOCKS)]

    @field_validator("theme")
    @classmethod
    def _theme(cls, value: str) -> str:
        return _check_theme(value)

    @model_validator(mode="after")
    def _total_size(self) -> Document:
        total = sum(_block_text_size(b) for b in self.blocks)
        if total > MAX_DOCUMENT_CHARS:
            raise ValueError(
                f"document text is {total} characters; the limit is {MAX_DOCUMENT_CHARS}"
            )
        return self


# --------------------------------------------------------------------------- deck
def _check_transition(value: str) -> str:
    name = canonical_transition(value)
    if name is None:
        raise ValueError(f"unknown transition '{value}'; allowed: {', '.join(TRANSITION_NAMES)}")
    return name


class Card(_Strict):
    title: Annotated[Text, Field(min_length=1, max_length=MAX_ITEM_TITLE_CHARS)]
    body: Annotated[Text, Field(max_length=MAX_CARD_BODY_CHARS)] = ""
    # An optional short glyph or number drawn in the card (for example "01", "⚡", "3x").
    glyph: Annotated[Text, Field(max_length=MAX_CARD_GLYPH_CHARS)] = ""
    # An icon name from ``deck_icons.ICON_NAMES`` (themed decks draw it as a native shape); an
    # unknown name falls back to a numbered badge.
    icon: Annotated[Text, Field(max_length=MAX_ICON_CHARS)] = ""


class SlideCode(_Strict):
    language: str = ""
    text: Annotated[str, Field(min_length=1, max_length=MAX_SLIDE_CODE_CHARS)]

    @field_validator("language")
    @classmethod
    def _check_language(cls, value: str) -> str:
        if not _CODE_LANGUAGE.match(value):
            raise ValueError("code language must be a short identifier such as 'python'")
        return value


class Step(_Strict):
    title: Annotated[Text, Field(min_length=1, max_length=MAX_ITEM_TITLE_CHARS)]
    detail: Annotated[Text, Field(max_length=MAX_STEP_DETAIL_CHARS)] = ""
    icon: Annotated[Text, Field(max_length=MAX_ICON_CHARS)] = ""


class Column(_Strict):
    heading: Annotated[str, Field(max_length=MAX_ITEM_TITLE_CHARS)] = ""
    bullets: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=MAX_COLUMN_BULLET_CHARS)]],
        Field(min_length=1, max_length=MAX_COLUMN_BULLETS),
    ]


class SlideImage(_Strict):
    # An empty src is a placeholder frame (two_column with no picture yet).
    src: Annotated[str, Field(max_length=MAX_IMAGE_REF_CHARS)] = ""
    alt: Annotated[str, Field(min_length=1, max_length=MAX_SHORT_CHARS)]
    caption: Annotated[str, Field(max_length=MAX_SHORT_CHARS)] | None = None

    @field_validator("src")
    @classmethod
    def _check_src(cls, value: str) -> str:
        return validate_image_ref(value) if value else value


class SlideTable(_Strict):
    headers: Annotated[
        list[Annotated[Text, Field(max_length=MAX_CELL_CHARS)]],
        Field(min_length=1, max_length=MAX_SLIDE_TABLE_COLS),
    ]
    rows: Annotated[
        list[list[Annotated[Text, Field(max_length=MAX_CELL_CHARS)]]],
        Field(min_length=1, max_length=MAX_SLIDE_TABLE_ROWS),
    ]

    @model_validator(mode="after")
    def _rectangular(self) -> SlideTable:
        _check_rectangular(self.headers, self.rows)
        return self


class SlideMetric(Metric):
    """A slide metric: ``progress`` (0 to 100) adds a progress bar to the stat tile."""

    progress: Annotated[float, Field(ge=0, le=100)] | None = None


class ChartSeries(_Strict):
    name: Annotated[Text, Field(max_length=60)] = ""
    values: Annotated[list[float], Field(min_length=1, max_length=MAX_CHART_CATEGORIES)]


class SlideChart(_Strict):
    """A native chart: one category list and one to four series of numbers (a doughnut has one)."""

    type: Literal["column", "bar", "line", "doughnut"] = "column"
    categories: Annotated[
        list[Annotated[Text, Field(min_length=1, max_length=40)]],
        Field(min_length=1, max_length=MAX_CHART_CATEGORIES),
    ]
    series: Annotated[list[ChartSeries], Field(min_length=1, max_length=MAX_CHART_SERIES)]

    @model_validator(mode="after")
    def _shape(self) -> SlideChart:
        for series in self.series:
            if len(series.values) != len(self.categories):
                raise ValueError(
                    f"chart series '{series.name}' has {len(series.values)} values for "
                    f"{len(self.categories)} categories"
                )
        if self.type == "doughnut" and len(self.series) != 1:
            raise ValueError("a doughnut chart takes exactly one series")
        return self


class Slide(_Strict):
    """One slide. Only the fields of its ``layout`` are used; the rest are ignored."""

    layout: Literal[
        "cover",
        "section",
        "bullets",
        "two_column",
        "cards",
        "stat_callout",
        "timeline",
        "comparison",
        "table",
        "code",
        "quote",
        "closing",
        "image",
        "hero_cover",
        "section_break",
        "stat_grid",
        "icon_cards",
        "process",
        "image_text",
        "chart",
    ] = "bullets"
    # Big numeral or short label on a section slide ("02", "Phase 2").
    number: Annotated[Text, Field(max_length=MAX_SECTION_NUMBER_CHARS)] = ""
    title: Annotated[Text, Field(max_length=MAX_SLIDE_TITLE_CHARS)] = ""
    subtitle: Annotated[Text, Field(max_length=MAX_SHORT_CHARS)] = ""
    bullets: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=MAX_SLIDE_BULLET_CHARS)]],
        Field(max_length=MAX_SLIDE_BULLETS),
    ] = []
    left: Column | None = None
    right: Column | None = None
    cards: Annotated[list[Card], Field(max_length=MAX_SLIDE_CARDS)] = []
    metrics: Annotated[list[SlideMetric], Field(max_length=MAX_SLIDE_METRICS)] = []
    quote: Annotated[str, Field(max_length=MAX_SLIDE_TEXT_CHARS)] = ""
    by: Annotated[str, Field(max_length=MAX_SHORT_CHARS)] | None = None
    table: SlideTable | None = None
    code: SlideCode | None = None
    image: SlideImage | None = None
    steps: Annotated[list[Step], Field(max_length=MAX_SLIDE_STEPS)] = []
    chart: SlideChart | None = None
    # Transition into this slide: "" (the deck's), none, fade, push, wipe or cover.
    transition: str = ""
    notes: Annotated[str, Field(max_length=MAX_NOTES_CHARS)] | None = None

    @field_validator("transition")
    @classmethod
    def _transition(cls, value: str) -> str:
        return _check_transition(value)

    @field_validator("layout", mode="before")
    @classmethod
    def _legacy_layout(cls, value: Any) -> Any:
        if isinstance(value, str) and value.strip().lower() in LEGACY_SLIDE_LAYOUTS:
            return LEGACY_SLIDE_LAYOUTS[value.strip().lower()]
        return value

    @model_validator(mode="after")
    def _layout_requirements(self) -> Slide:
        layout = self.layout
        problems: list[str] = []
        if layout in _TITLE_REQUIRED and not self.title:
            problems.append(f"layout '{layout}' needs a title")
        if layout == "bullets" and not self.bullets:
            problems.append("layout 'bullets' needs at least one bullet")
        if layout == "two_column" and (
            self.left is None or (self.right is None and not self.image)
        ):
            problems.append(
                "layout 'two_column' needs a 'left' column and a 'right' column or image"
            )
        if layout == "comparison" and (self.left is None or self.right is None):
            problems.append("layout 'comparison' needs both 'left' and 'right' columns")
        if layout == "cards" and not self.cards:
            problems.append("layout 'cards' needs at least one card")
        if layout in ("stat_callout", "stat_grid") and not self.metrics:
            problems.append(f"layout '{layout}' needs at least one metric")
        if layout == "icon_cards" and not self.cards:
            problems.append("layout 'icon_cards' needs at least one card")
        if layout == "chart" and self.chart is None:
            problems.append("layout 'chart' needs a 'chart'")
        if layout == "image_text" and self.image is None:
            problems.append("layout 'image_text' needs an 'image'")
        if layout == "code" and self.code is None:
            problems.append("layout 'code' needs a 'code' block")
        if layout == "quote" and not self.quote:
            problems.append("layout 'quote' needs 'quote' text")
        if layout == "table" and self.table is None:
            problems.append("layout 'table' needs a 'table'")
        if layout == "image" and self.image is None:
            problems.append("layout 'image' needs an 'image'")
        if layout in ("timeline", "process") and len(self.steps) < 2:
            problems.append(f"layout '{layout}' needs at least 2 steps")
        if problems:
            raise ValueError("; ".join(problems))
        return self


class Deck(_Strict):
    theme: str = "executive"
    design: dict[str, Any] | None = None
    title: Annotated[str, Field(min_length=1, max_length=MAX_TITLE_CHARS)]
    slides: Annotated[list[Slide], Field(min_length=1, max_length=MAX_SLIDES)]
    # Deck-level transition: "" (the theme's default: fade for glass themes, none otherwise), none,
    # fade, push, wipe or cover. A slide's own ``transition`` wins.
    transition: str = ""
    # Repairs made while the AI's slide list was reshaped (not part of the schema).
    _normalize_notes: list[str] = PrivateAttr(default_factory=list)

    @property
    def normalize_notes(self) -> list[str]:
        """Repairs made when this deck was built from a dict, for the renderer to report."""
        return list(self._normalize_notes)

    @field_validator("theme")
    @classmethod
    def _theme(cls, value: str) -> str:
        return _check_theme(value)

    @field_validator("transition")
    @classmethod
    def _transition(cls, value: str) -> str:
        return _check_transition(value)


# --------------------------------------------------------------------------- helpers
def format_validation_error(error: ValidationError, limit: int = 5) -> str:
    """A short, precise, human-readable summary of a pydantic validation error."""
    problems = error.errors()
    lines: list[str] = []
    for item in problems[:limit]:
        location = ".".join(str(part) for part in item["loc"])
        message = str(item["msg"]).removeprefix("Value error, ")
        lines.append(f"{location or 'document'}: {message}")
    if len(problems) > limit:
        lines.append(f"... and {len(problems) - limit} more problem(s)")
    return "; ".join(lines)


def _prune_none(value: Any) -> Any:
    """Drop ``None`` values so an optional field the model left ``null`` validates as absent."""
    if isinstance(value, dict):
        return {k: _prune_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_prune_none(v) for v in value]
    return value


def document_from_dict(data: dict[str, Any]) -> Document:
    """Validate a plain dict (for example model output) into a :class:`Document`.

    Blocks are reshaped first (aliases, a missing ``type`` inferred from the fields, see
    :mod:`app.automation.normalize`); the strict validation that follows is unchanged.
    """
    cleaned = _prune_none(data)
    if isinstance(cleaned, dict) and "blocks" in cleaned:
        blocks, _notes = normalize_blocks(cleaned["blocks"])
        cleaned = {**cleaned, "blocks": blocks}
    return Document.model_validate(cleaned)


def deck_from_dict(data: dict[str, Any]) -> Deck:
    """Validate a plain dict into a :class:`Deck` (slides are reshaped first)."""
    cleaned = _prune_none(data)
    notes: list[str] = []
    if isinstance(cleaned, dict) and "slides" in cleaned:
        slides, notes = normalize_slides(cleaned["slides"])
        cleaned = {**cleaned, "slides": slides}
    deck = Deck.model_validate(cleaned)
    deck._normalize_notes = notes
    return deck


def blocks_from_legacy_sections(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map legacy ``sections`` dicts (heading1/heading2/paragraph/table/page_break/...) to
    block dicts. An unknown section type is an error, never silently dropped."""
    blocks: list[dict[str, Any]] = []
    for index, item in enumerate(sections, start=1):
        kind = str(item.get("type", "paragraph"))
        text = str(item.get("text", "")).strip()
        if kind in ("heading1", "heading2", "heading3"):
            if text:
                blocks.append({"type": "heading", "level": int(kind[-1]), "text": text})
        elif kind == "paragraph":
            if text:
                blocks.append({"type": "paragraph", "text": text})
        elif kind in ("bullet", "bullets"):
            raw = item.get("items") or ([text] if text else [])
            items = [str(i) for i in raw if str(i).strip()]
            if items:
                blocks.append({"type": "bullets", "items": items})
        elif kind == "callout":
            if text:
                blocks.append({"type": "callout", "kind": "info", "text": text})
        elif kind == "page_break":
            blocks.append({"type": "page_break"})
        elif kind == "table":
            headers = [str(h) for h in item.get("headers") or []]
            rows = [[str(c) for c in row] for row in item.get("rows") or []]
            if not headers and rows:
                headers = [""] * len(rows[0])
            width = len(headers)
            rows = [row + [""] * (width - len(row)) for row in rows]
            if headers:
                blocks.append({"type": "table", "headers": headers, "rows": rows})
        else:
            raise ValueError(f"sections[{index}]: unsupported legacy section type '{kind}'")
    return blocks


def document_from_legacy(
    title: str,
    sections: list[dict[str, Any]],
    subtitle: str | None = None,
    theme: str = "executive",
    design: dict[str, Any] | None = None,
) -> Document:
    """Build a :class:`Document` from the legacy ``create_pdf``/``create_document`` inputs."""
    blocks = blocks_from_legacy_sections(sections)
    if not blocks:
        # Legacy callers could render an empty body; keep a single blank paragraph.
        blocks = [{"type": "paragraph", "text": "(no content)"}]
    return Document.model_validate(
        {
            "theme": theme,
            "design": design or None,
            "title": title or "Document",
            "subtitle": subtitle or None,
            "blocks": blocks,
        }
    )
