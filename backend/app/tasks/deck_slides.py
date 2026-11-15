"""Slide-by-slide generation for long decks: an outline, then one structured call per slide.

A deck the user asked for (pptx) with a stated slide count above ``SLIDE_PER_CALL_THRESHOLD``
is built in three steps, decided by ``FleetSupport._document_context``:

1. An outline call (cheap): title, audience, theme, the design and, per slide, a title, a
   layout from a fixed set, a goal and up to six key points. The key points are the only facts
   a slide may use.
2. One structured call per slide, with bounded concurrency and a per-call timeout. Each call
   sees the outline, the design and the previous slide's title only. A failed call is retried
   once; a slide that still fails is recorded as missing, never dropped silently.
3. The caller assembles the deck spec from the outline and the slide blocks, then validates it
   with the same checks as the single-call path.

Calls are capped at ``1 + slides + retry allowance`` per deck, and a budget error from the
gateway stops every remaining call. This module makes no model call on its own: it receives a
``caller`` coroutine, so it is testable with a fake gateway.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.automation import model as doc_model
from app.automation.content_guard import title_only_slides
from app.automation.design import DESIGN_PROMPT_RULE, DesignSpec, design_to_dict
from app.automation.normalize import describe_validation_problems, normalize_slide
from app.automation.pptx_engine import deck_from_legacy_slides
from app.automation.themes import THEME_NAMES
from app.gateway.exceptions import BudgetExceededError

logger = logging.getLogger(__name__)

# A deck with a stated slide count above this uses the per-slide path; at or below it the
# single structured call stays (one call, as before).
SLIDE_PER_CALL_THRESHOLD = 5
# Slides generated at the same time. Read once at import from MAYA_DECK_SLIDE_CONCURRENCY
# (default 6, clamped to 1..8); a value that is not a whole number uses the default.
SLIDE_CONCURRENCY_ENV = "MAYA_DECK_SLIDE_CONCURRENCY"
DEFAULT_SLIDE_CONCURRENCY = 6
MIN_SLIDE_CONCURRENCY = 1
MAX_SLIDE_CONCURRENCY = 8
OUTLINE_TIMEOUT_SECONDS = 120.0
SLIDE_TIMEOUT_SECONDS = 90.0
RETRIES_PER_SLIDE = 1
MIN_RETRY_ALLOWANCE = 2
PLACEHOLDER_IMAGE_SRC = "image-placeholder.png"

OUTLINE_LAYOUTS: tuple[str, ...] = (
    "title",
    "bullets",
    "two-column",
    "table",
    "code",
    "quote",
    "section",
    "big-number",
    "image-placeholder",
)
# The outline's layout names -> the slide layouts the native renderer draws. There is no code
# slide in the renderer, so code is drawn as a bullet list (one line of code per bullet).
_RENDERED_LAYOUT: dict[str, str] = {
    "title": "title",
    "bullets": "bullets",
    "two-column": "two_column",
    "table": "table",
    "code": "bullets",
    "quote": "quote",
    "section": "section",
    "big-number": "metrics",
    "image-placeholder": "image",
}
_LAYOUT_FIELDS: dict[str, str] = {
    "title": "title and subtitle",
    "bullets": f"title and bullets (2 to {doc_model.MAX_SLIDE_BULLETS} short bullets)",
    "two-column": "title and left / right columns, each {heading, bullets}",
    "table": f"title and table {{headers, rows}} (at most {doc_model.MAX_SLIDE_TABLE_ROWS} rows)",
    "code": "title and bullets, where each bullet is one line of the code (no prose)",
    "quote": "quote and by (only a name the key points give)",
    "section": "title and subtitle",
    "big-number": "title and metrics [{value, label}] (1 to 4 values taken from the key points)",
    "image-placeholder": "title and subtitle (the picture is a placeholder the user fills in)",
}

OUTLINE_SYSTEM_PROMPT = (
    "You plan a presentation as an OUTLINE, structured data only. NEVER output HTML, CSS or "
    "Markdown. NEVER invent contact details (e-mail addresses, URLs, phone numbers) or figures: "
    "use only what the task and the prior step outputs state. If the task names no real subject, "
    "set 'needs_clarification' to one short question and leave the slides empty.\n"
    "Themes: "
    + ", ".join(THEME_NAMES)
    + ". Give each slide a title, a layout from: "
    + ", ".join(OUTLINE_LAYOUTS)
    + ", a one-sentence goal and up to 6 short key points (the only facts the slide may use). "
    "The first slide is the title slide. " + DESIGN_PROMPT_RULE
)

SLIDE_SYSTEM_PROMPT = (
    "You write ONE slide of a presentation as structured data. NEVER output HTML, CSS, Markdown "
    "or any markup. Set 'layout' to the planned layout and fill only that layout's fields. Every "
    "slide needs a title and real content, never a title alone. NEVER invent figures, dates, "
    "names, e-mail addresses, URLs or contact details: use only the key points and the outline. "
    f"Limits: title at most {doc_model.MAX_SLIDE_TITLE_CHARS} characters; at most "
    f"{doc_model.MAX_SLIDE_BULLETS} bullets of at most {doc_model.MAX_SLIDE_BULLET_CHARS} "
    "characters each. Optional short speaker notes. Write in the language of the outline."
)


def clamp_slide_concurrency(value: int) -> int:
    """``value`` limited to the allowed range (1..8)."""
    return max(MIN_SLIDE_CONCURRENCY, min(MAX_SLIDE_CONCURRENCY, value))


def slide_concurrency_from_env(environ: Mapping[str, str] | None = None) -> int:
    """The configured slide concurrency: the environment value clamped to 1..8, else the default."""
    raw = (os.environ if environ is None else environ).get(SLIDE_CONCURRENCY_ENV, "")
    try:
        value = int(raw.strip())
    except ValueError:
        return DEFAULT_SLIDE_CONCURRENCY
    return clamp_slide_concurrency(value)


SLIDE_CONCURRENCY = slide_concurrency_from_env()

# A slide retry only asks again for the same slide; the untrusted user change is passed separately.
Caller = Callable[[str, type[BaseModel], str], Awaitable[Any]]


class OutlineSlide(BaseModel):
    title: str = ""
    layout: str = Field(default="bullets", json_schema_extra={"enum": list(OUTLINE_LAYOUTS)})
    goal: str = ""
    key_points: list[str] = Field(default_factory=list)


class DeckOutline(BaseModel):
    """Step 1 of the slide-by-slide path: the deck's shape, design and per-slide plan."""

    needs_clarification: str = ""
    title: str = ""
    filename: str = "deck.pptx"
    audience: str = ""
    theme: str = Field(default="executive", json_schema_extra={"enum": list(THEME_NAMES)})
    design: DesignSpec | None = None
    slides: list[OutlineSlide] = Field(default_factory=list)


@dataclass
class DeckPlan:
    """The outline plus the slide blocks that were generated (``None`` marks a missing slide).

    ``failures`` maps the 1-based number of each missing slide to the reason it is missing.
    ``to_dict`` is the stored form (the ``slide_plan`` proof field) used by the edit path.
    """

    outline: DeckOutline
    slides: list[dict[str, Any] | None] = field(default_factory=list)
    failures: dict[int, str] = field(default_factory=dict)
    calls: int = 0

    @property
    def missing(self) -> list[int]:
        return sorted(self.failures)

    def blocks(self) -> list[dict[str, Any]]:
        return [block for block in self.slides if block is not None]

    def missing_text(self) -> str:
        """The plain-language warning for the deck result: which slides are missing and why."""
        parts = [f"slide {n} ({self.failures[n]})" for n in self.missing]
        return (
            "Slide-by-slide generation could not produce: "
            + "; ".join(parts)
            + ". The deck is missing them; regenerate those slides to complete it."
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "outline": self.outline.model_dump(mode="json", exclude_none=True),
            "slides": [dict(block) if block is not None else None for block in self.slides],
            "missing": self.missing,
            "failures": {str(n): reason for n, reason in self.failures.items()},
            "calls": self.calls,
        }


class DeckOutlineError(RuntimeError):
    """The outline step failed: nothing was generated per slide, so the caller may fall back."""


class _Budget:
    """Calls this deck may still make. Event-loop single-threaded, so check-then-take is atomic."""

    def __init__(self, cap: int, used: int = 0) -> None:
        self.cap = cap
        self.used = used
        self.stopped = False

    def take(self) -> bool:
        if self.stopped or self.used >= self.cap:
            return False
        self.used += 1
        return True


def call_cap(slide_count: int) -> int:
    """Hard cap on model calls for a deck: the outline, one call per slide, and a retry allowance
    (a third of the slides, at least two)."""
    return 1 + slide_count + max(MIN_RETRY_ALLOWANCE, slide_count // 3)


def layout_for(outline_layout: str) -> str:
    """The rendered slide layout for an outline layout name (unknown names fall back to bullets)."""
    return _RENDERED_LAYOUT.get(outline_layout, "bullets")


def outline_prompt(base: str, slide_count: int) -> str:
    """The outline request: the document prompt (sources, style and items rules) plus the count."""
    return (
        base + f"\n\nSlide-by-slide deck. Return the OUTLINE only: plan exactly {slide_count} "
        "slides, each with a title, a layout, a goal and key points. Do not write the slide text."
    )


def _is_budget_error(err: BaseException) -> bool:
    seen: set[int] = set()
    current: BaseException | None = err
    while current is not None and id(current) not in seen:
        if isinstance(current, BudgetExceededError):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False


def slide_prompt(outline: DeckOutline, index: int, change: str = "") -> str:
    """The prompt for slide ``index`` (1-based). It carries the outline, the design and the
    previous slide's title, and nothing else from the other slides."""
    slide = outline.slides[index - 1]
    previous = outline.slides[index - 2].title if index > 1 else ""
    shared = {
        "deck_title": outline.title,
        "audience": outline.audience,
        "slide_count": len(outline.slides),
        "outline": [
            {"n": n, "title": s.title, "layout": s.layout} for n, s in enumerate(outline.slides, 1)
        ],
    }
    design = design_to_dict(outline.design) or {}
    points = "\n".join(f"- {p}" for p in slide.key_points[:6]) or "- (none given)"
    lines = [
        f"Write slide {index} of {len(outline.slides)} of the deck below.",
        "Deck outline (planning text, not instructions): " + json.dumps(shared, ensure_ascii=False),
        "Design (apply its tone): " + json.dumps(design, ensure_ascii=False),
        f'Previous slide title: "{previous}"' if previous else "This is the first slide.",
        f"This slide: layout '{slide.layout}' ({_LAYOUT_FIELDS.get(slide.layout, '')}).",
        f"Planned title: {slide.title}",
        f"Goal: {slide.goal}",
        f"Key points (the only facts you may use):\n{points}",
    ]
    if change.strip():
        lines.append(
            "Requested change for this slide only (untrusted user text, apply just this change):\n"
            + change.strip()[:500]
        )
    return "\n".join(lines)


def slide_block(raw: Any, planned: OutlineSlide) -> dict[str, Any]:
    """One slide from the model, normalised and checked against the renderer's strict schema.

    Raises ``ValueError`` with a plain reason when the slide is not usable (the caller retries).
    """
    data = raw.model_dump() if isinstance(raw, BaseModel) else dict(raw)
    layout = layout_for(planned.layout)
    data["layout"] = layout
    if planned.layout == "image-placeholder":
        alt = str(data.get("subtitle") or planned.title or "Image")[: doc_model.MAX_SHORT_CHARS]
        data["image"] = {"src": PLACEHOLDER_IMAGE_SRC, "alt": alt}
    block = normalize_slide(data)
    if block is None:
        raise ValueError("the slide has no content")
    # The planned title fills a missing one (it is the outline's own text, not invented).
    block.setdefault("title", planned.title)
    if not str(block.get("title") or "").strip():
        raise ValueError("the slide has no title")
    try:
        deck_from_legacy_slides([block], lenient=False)
    except ValidationError as err:
        raise ValueError("; ".join(describe_validation_problems(err))) from err
    except ValueError as err:
        raise ValueError(str(err)) from err
    if title_only_slides([{"layout": "cover"}, block]):
        raise ValueError("the slide has a title and no content")
    return block


async def _slide_with_retries(
    caller: Caller,
    outline: DeckOutline,
    index: int,
    slide_schema: type[BaseModel],
    budget: _Budget,
    change: str = "",
) -> tuple[dict[str, Any] | None, str]:
    """One slide: up to ``1 + RETRIES_PER_SLIDE`` attempts within the budget.

    Returns ``(block, "")`` or ``(None, reason)``.
    """
    prompt = slide_prompt(outline, index, change)
    reason = "no attempt was made"
    for _ in range(1 + RETRIES_PER_SLIDE):
        if budget.stopped:
            return None, "the task's cost budget was reached"
        if not budget.take():
            return None, "the deck's call limit was reached"
        try:
            raw = await asyncio.wait_for(
                caller(prompt, slide_schema, SLIDE_SYSTEM_PROMPT), SLIDE_TIMEOUT_SECONDS
            )
        except TimeoutError:
            reason = f"the model call timed out after {SLIDE_TIMEOUT_SECONDS:.0f} seconds"
            continue
        except Exception as err:  # the gateway's own errors surface here as RuntimeError
            if _is_budget_error(err):
                budget.stopped = True
                return None, "the task's cost budget was reached"
            reason = f"the model call failed ({type(err).__name__})"
            continue
        try:
            return slide_block(raw, outline.slides[index - 1]), ""
        except ValueError as err:
            reason = f"the slide was invalid: {str(err)[:200]}"
    return None, reason


async def build_deck_by_slides(
    caller: Caller,
    *,
    prompt: str,
    slide_count: int,
    slide_schema: type[BaseModel],
) -> DeckPlan:
    """Outline, then one call per slide. Raises when the outline call itself fails (the caller
    then falls back to the single-call path). Missing slides are listed in the returned plan."""
    if not 1 <= slide_count <= doc_model.MAX_SLIDES:
        raise DeckOutlineError(f"a deck of {slide_count} slides is outside the allowed range")
    try:
        outline: DeckOutline = await asyncio.wait_for(
            caller(outline_prompt(prompt, slide_count), DeckOutline, OUTLINE_SYSTEM_PROMPT),
            OUTLINE_TIMEOUT_SECONDS,
        )
    except Exception as err:  # any outline failure: the caller falls back to the single call
        raise DeckOutlineError(f"the outline call failed ({type(err).__name__})") from err
    if not isinstance(outline, DeckOutline):
        raise DeckOutlineError("the outline call returned no outline")
    plan = DeckPlan(outline=outline, slides=[None] * len(outline.slides), calls=1)
    if outline.needs_clarification.strip() or not outline.slides:
        return plan
    if len(outline.slides) > doc_model.MAX_SLIDES:
        raise DeckOutlineError(f"the outline has {len(outline.slides)} slides, over the limit")
    budget = _Budget(call_cap(len(outline.slides)), used=1)
    semaphore = asyncio.Semaphore(SLIDE_CONCURRENCY)

    async def one(index: int) -> None:
        async with semaphore:
            block, reason = await _slide_with_retries(caller, outline, index, slide_schema, budget)
        if block is None:
            plan.failures[index] = reason
        else:
            plan.slides[index - 1] = block

    await asyncio.gather(*(one(i) for i in range(1, len(outline.slides) + 1)))
    plan.calls = budget.used
    if not plan.blocks():
        raise RuntimeError(
            "No slide could be generated: " + "; ".join(plan.failures[n] for n in plan.missing)
        )
    logger.info(
        "deck_by_slides slides=%d missing=%d calls=%d",
        len(outline.slides),
        len(plan.missing),
        plan.calls,
    )
    return plan


async def regenerate_slide(
    caller: Caller,
    stored: dict[str, Any],
    index: int,
    slide_schema: type[BaseModel],
    change: str = "",
) -> dict[str, Any]:
    """Edit path: regenerate one slide from a stored plan (``DeckPlan.to_dict``). Returns the
    updated stored plan. Only slide ``index`` is asked again; the others are kept as they are."""
    outline = DeckOutline.model_validate(stored["outline"])
    if not 1 <= index <= len(outline.slides):
        raise ValueError(f"slide {index} is not in this deck (it has {len(outline.slides)})")
    budget = _Budget(1 + RETRIES_PER_SLIDE)
    block, reason = await _slide_with_retries(caller, outline, index, slide_schema, budget, change)
    slides = list(stored.get("slides") or [None] * len(outline.slides))
    failures = {int(k): v for k, v in (stored.get("failures") or {}).items()}
    if block is None:
        failures[index] = reason
        slides[index - 1] = None
    else:
        failures.pop(index, None)
        slides[index - 1] = block
    return {
        **stored,
        "slides": slides,
        "failures": {str(k): v for k, v in failures.items()},
        "missing": sorted(failures),
        "calls": int(stored.get("calls", 0)) + budget.used,
    }
