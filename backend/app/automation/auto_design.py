"""Good defaults for a file whose request (or spec) states no design.

A short request ("a 6 slide deck on AI in healthcare", "a sales sheet", "Java notes as PDF") must
already look designed. Where the model left a design field out, these deterministic helpers fill
it from the kind of document (no model call):

* :func:`pick_tone` reads the title, the request and the first text for report, finance, pitch,
  notes, kids, tech or health words (English and Hinglish) and returns a tone;
* :func:`default_design` turns a tone into a ``design`` dict (preset, palette, font pairing, cover,
  heading, table and callout styles); only fields the model left empty are filled, and a palette
  the model chose is never touched. ``resolve_design`` still repairs every colour for contrast;
* :func:`vary_layouts` gives a deck made of plain bullet slides a rhythm: cover, stat, cards,
  timeline, comparison, quote, two-column and closing slides chosen from each slide's own content.
  Slides the model gave another layout are never changed, and the slide count never changes;
* :func:`document_flags` decides a cover page and a table of contents for a longer document.

Everything is a pure function of its inputs, so editing a file again gives the same result (the
tool loop also stores the effective design with the file).
"""

from __future__ import annotations

import copy
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from app.automation.deck_themes import tone_theme

MAX_SAME_LAYOUT_RUN = 2
"""A deck of six or more slides never shows more than this many slides of one layout in a row
(for the slides this module may change)."""

# tone -> keywords. The first tone with the most hits wins; "report" is the fallback.
_TONE_WORDS: dict[str, list[str]] = {
    "finance": (
        "finance financial budget revenue sales profit loss invoice expense expenses quarter "
        "forecast bank accounting payroll salary tax cashflow ledger inventory stock price "
        "kharcha bikri hisab"
    ).split(),
    "pitch": (
        "pitch startup investor investors proposal launch marketing strategy roadmap brand "
        "keynote sales-deck funding"
    ).split(),
    "notes": (
        "notes study syllabus tutorial lecture java python javascript sql cheatsheet cheat "
        "course exam chapter learn learning revision padhai class programming tutorial guide "
        "interview"
    ).split(),
    "kids": (
        "kids kid children child bachcho bacchon story fun cartoon rhyme nursery toddler "
        "playful school-project"
    ).split(),
    "tech": (
        "ai ml api cloud cyber security software devops data blockchain machine robotics "
        "architecture platform saas llm"
    ).split(),
    "health": (
        "health healthcare medical medicine clinical hospital patient pharma wellness doctor "
        "nutrition fitness swasthya"
    ).split(),
}
_TONE_ORDER = ("kids", "notes", "finance", "pitch", "health", "tech")
_WORD = re.compile(r"[a-z][a-z-]+")

# tone -> design. ``deck`` entries apply to pptx only. Palette colours are repaired for contrast
# by ``resolve_design``; the dark deck presets carry no palette so their own colours stay.
_PRESETS: dict[str, dict[str, Any]] = {
    "report": {
        "base": "executive",
        "mood": "sober report",
        "palette": {"primary": "#1F3A5F", "accent": "#B45309", "link": "#1D4ED8"},
        "heading_font": "Georgia",
        "body_font": "Calibri",
        "cover": "band",
        "heading_style": "rule",
        "table_style": "striped",
        "callout_style": "bar",
    },
    "finance": {
        "base": "executive",
        "mood": "precise finance",
        "palette": {"primary": "#0F3D3E", "accent": "#A16207", "link": "#0F766E"},
        "heading_font": "Cambria",
        "body_font": "Calibri",
        "cover": "band",
        "heading_style": "rule",
        "table_style": "lined",
        "callout_style": "bar",
    },
    "pitch": {
        "base": "minimal",
        "mood": "bold modern pitch",
        "palette": {"primary": "#5B21B6", "accent": "#D97706", "link": "#6D28D9"},
        "heading_font": "Segoe UI",
        "body_font": "Segoe UI",
        "cover": "bold",
        "heading_style": "bar",
        "table_style": "filled",
        "callout_style": "tinted",
    },
    "notes": {
        "base": "notes",
        "mood": "clean study notes",
        "palette": {"primary": "#0B3D5C", "accent": "#0F766E", "link": "#0B5FA5"},
        "heading_font": "Calibri",
        "body_font": "Calibri",
        "cover": "plain",
        "heading_style": "bar",
        "table_style": "striped",
        "callout_style": "tinted",
        "density": "compact",
    },
    "kids": {
        "base": "minimal",
        "mood": "bright playful",
        "palette": {"primary": "#6D28D9", "accent": "#C2410C", "link": "#6D28D9"},
        "heading_font": "Trebuchet MS",
        "body_font": "Verdana",
        "cover": "bold",
        "heading_style": "plain",
        "table_style": "filled",
        "callout_style": "outlined",
        "density": "airy",
        "corner_radius": 20,
    },
    "tech": {
        "base": "minimal",
        "mood": "modern tech",
        "palette": {"primary": "#0369A1", "accent": "#7C3AED", "link": "#0369A1"},
        "heading_font": "Segoe UI",
        "body_font": "Calibri",
        "cover": "sidebar",
        "heading_style": "rule",
        "table_style": "striped",
        "callout_style": "bar",
    },
    "health": {
        "base": "minimal",
        "mood": "calm clinical",
        "palette": {"primary": "#0F766E", "accent": "#0369A1", "link": "#0F766E"},
        "heading_font": "Calibri",
        "body_font": "Calibri",
        "cover": "band",
        "heading_style": "rule",
        "table_style": "striped",
        "callout_style": "tinted",
    },
}
# Decks: dark presets for the bold tones, decorations and panel look per tone.
_DECK: dict[str, dict[str, Any]] = {
    "pitch": {
        "base": "midnight",
        "palette": None,
        "shape_style": "soft-shadow",
        "decorations": ["top_bar", "divider_line"],
    },
    "tech": {
        "base": "midnight",
        "palette": None,
        "shape_style": "soft-shadow",
        "decorations": ["top_bar", "divider_line"],
    },
    "kids": {"decorations": ["corner_circle"], "shape_style": "flat"},
    "notes": {"decorations": ["side_bar"], "shape_style": "outlined"},
    "report": {"decorations": ["divider_line"], "shape_style": "flat"},
    "finance": {"decorations": ["divider_line"], "shape_style": "outlined"},
    "health": {"decorations": ["divider_line"], "shape_style": "soft-shadow"},
}


def pick_tone(*texts: str) -> str:
    """The tone of a request: the one whose keywords occur most in ``texts`` (ties go to the
    order kids, notes, finance, pitch, health, tech), else ``report``."""
    words = _WORD.findall(" ".join(t for t in texts if t).lower()[:4000])
    counts: dict[str, int] = {}
    for tone, vocabulary in _TONE_WORDS.items():
        keys = set(vocabulary)
        counts[tone] = sum(1 for w in words if w in keys)
    best = max(counts.values(), default=0)
    if best == 0:
        return "report"
    return next(t for t in _TONE_ORDER if counts.get(t) == best)


def _blank(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    return value is None or value in ("", [], {}) or (isinstance(value, int | float) and value == 0)


def default_design(
    design: Mapping[str, Any] | None, tone: str, *, deck: bool = False
) -> dict[str, Any]:
    """``design`` with every field the model left blank filled from ``tone``'s preset.

    A palette the model supplied is kept whole (it is a coherent choice; the base preset fills its
    gaps); an empty palette takes the tone's. For a deck the bold tones start from a dark preset.
    """
    preset = copy.deepcopy(_PRESETS.get(tone, _PRESETS["report"]))
    library = tone_theme(tone) if deck else None
    if library is not None:
        # A deck takes a slide-library theme (glass for pitch and tech, editorial for the rest):
        # its own palette, fonts, shape style and decorations, so none of the preset's are set.
        for key in ("palette", "heading_font", "body_font", "corner_radius"):
            preset.pop(key, None)
        preset["base"] = library
    elif deck:
        extra = copy.deepcopy(_DECK.get(tone, {}))
        if "palette" in extra and extra["palette"] is None:
            preset.pop("palette", None)
            extra.pop("palette")
        preset.update(extra)
    out = copy.deepcopy(dict(design or {}))
    for key, value in preset.items():
        if key == "palette":
            if _blank(out.get("palette")) or not any(dict(out.get("palette") or {}).values()):
                out["palette"] = value
            continue
        if _blank(out.get(key)):
            out[key] = value
    return out


def xlsx_colors(design: Mapping[str, Any] | None, tone: str) -> tuple[str, str]:
    """``(primary, accent)`` hex colours for a workbook: the design's palette, else the tone's."""
    palette = dict((design or {}).get("palette") or {})
    preset = _PRESETS.get(tone, _PRESETS["report"])["palette"]
    base = str((design or {}).get("base") or "")
    if base and not palette.get("primary"):
        from app.automation.themes import THEMES

        theme = THEMES.get(base)
        if theme is not None and not palette.get("primary"):
            palette["primary"] = theme.colors.primary
            palette.setdefault("accent", theme.colors.accent)
    return (
        str(palette.get("primary") or preset["primary"]),
        str(palette.get("accent") or preset["accent"]),
    )


# ------------------------------------------------------------------------------ documents


def _block_text(block: Mapping[str, Any]) -> str:
    parts: list[str] = [str(block.get("text") or "")]
    for key in ("items", "bullets"):
        parts.extend(str(x) for x in (block.get(key) or []) if isinstance(x, str))
    return " ".join(parts)


def document_flags(blocks: Sequence[Mapping[str, Any]]) -> dict[str, bool]:
    """``cover_page`` and ``toc`` for a document of ``blocks`` that stated no page count.

    A cover page needs a document that is more than a note (about 450 words and a few sections);
    a table of contents needs several sections and about 1200 words. Short documents get neither:
    a one-page answer with a cover page is worse, not better.
    """
    words = sum(len(_block_text(b).split()) for b in blocks)
    headings = sum(
        1 for b in blocks if b.get("type") == "heading" and int(b.get("level") or 1) <= 2
    )
    return {"cover_page": words >= 450 and headings >= 3, "toc": words >= 1200 and headings >= 5}


# ----------------------------------------------------------------------------------- decks

_CLOSING_TITLE = re.compile(
    r"(?i)\b(thank|thanks|questions|q\s*&\s*a|next\s+steps?|conclusion|takeaways?|summary|"
    r"wrap[- ]?up|dhanyavad|shukriya)\b"
)
_PROCESS_TITLE = re.compile(
    r"(?i)\b(steps?|phases?|stages?|process|timeline|roadmap|journey|history|evolution|"
    r"how it works|workflow|lifecycle|plan)\b"
)
_VS_TITLE = re.compile(r"(?i)^(?P<a>.+?)\s+(?:vs\.?|versus|v/s)\s+(?P<b>.+)$")
_STAT_ITEM = re.compile(
    r"^\s*(?P<value>[$€£₹]?\s?\d[\d,.]*\s?(?:%|[kKmMbB]\b|x\b|cr\b|lakh\b|million\b|billion\b)?"
    r"\+?)\s*(?:[:\-–—]\s*|\s+)(?P<label>\S.*)$"
)
_YEAR_ITEM = re.compile(
    r"^\s*(?:(?:19|20)\d{2}|Q[1-4]\s?(?:19|20)?\d{0,2}|Phase\s?\d+|Step\s?\d+)\b"
)
_QUOTE_ITEM = re.compile(r'^\s*["“‘\'](?P<q>.{20,}?)["”’\']\s*(?:[-–—]\s*(?P<by>.{2,60}))?$')


def _split_label(item: str) -> tuple[str, str] | None:
    """``("Label", "detail")`` for ``Label: detail`` / ``Label - detail`` items, else ``None``."""
    match = re.match(r"^\s*(?P<l>[^:–—]{2,40}?)\s*(?::|\s[–—-]\s)\s*(?P<d>\S.*)$", item)
    if not match:
        return None
    return match.group("l").strip(" *_"), match.group("d").strip()


def _bullets(slide: Mapping[str, Any]) -> list[str]:
    return [str(b) for b in (slide.get("bullets") or []) if str(b).strip()]


def _as_cover(slide: dict[str, Any], bullets: list[str]) -> dict[str, Any]:
    out = {k: v for k, v in slide.items() if k not in ("bullets",)}
    out["layout"] = "cover"
    if bullets and not out.get("subtitle"):
        out["subtitle"] = bullets[0][:200]
    return out


def _convert(
    slide: dict[str, Any], *, first: bool, last: bool, total: int
) -> dict[str, Any] | None:
    """A better layout for one plain bullet slide from its own content, or ``None``."""
    bullets = _bullets(slide)
    title = str(slide.get("title") or "")
    if first and len(bullets) <= 1 and not slide.get("table"):
        return _as_cover(slide, bullets)
    if last and total >= 4 and _CLOSING_TITLE.search(title) and len(bullets) <= 6:
        return {**slide, "layout": "closing"}
    if not bullets:
        return None
    quote = _QUOTE_ITEM.match(bullets[0]) if len(bullets) == 1 else None
    if quote:
        return {
            "layout": "quote",
            "quote": quote.group("q").strip(),
            "by": (quote.group("by") or "").strip() or None,
            "title": title,
            "notes": slide.get("notes"),
        }
    stats = [_STAT_ITEM.match(b) for b in bullets]
    if 1 <= len(bullets) <= 3 and all(stats):
        metrics = [
            {"value": s.group("value").strip()[:40], "label": s.group("label").strip()[:100]}
            for s in stats
            if s is not None
        ]
        return {
            **{k: v for k, v in slide.items() if k != "bullets"},
            "layout": "stat_callout",
            "metrics": metrics,
        }
    versus = _VS_TITLE.match(title)
    if versus and 2 <= len(bullets) <= 12:
        cut = (len(bullets) + 1) // 2
        return {
            **{k: v for k, v in slide.items() if k != "bullets"},
            "layout": "comparison",
            "left": {"heading": versus.group("a").strip()[:80], "bullets": bullets[:cut][:6]},
            "right": {"heading": versus.group("b").strip()[:80], "bullets": bullets[cut:][:6]},
        }
    if 2 <= len(bullets) <= 6 and (
        _PROCESS_TITLE.search(title) or sum(1 for b in bullets if _YEAR_ITEM.match(b)) >= 2
    ):
        steps = []
        for item in bullets:
            split = _split_label(item)
            steps.append(
                {"title": split[0][:80], "detail": split[1][:240]}
                if split
                else {"title": item[:80], "detail": ""}
            )
        return {
            **{k: v for k, v in slide.items() if k != "bullets"},
            "layout": "timeline",
            "steps": steps,
        }
    labelled = [_split_label(b) for b in bullets]
    if 3 <= len(bullets) <= 4 and all(labelled):
        cards = [
            {"title": pair[0][:80], "body": pair[1][:400], "glyph": f"{i:02d}"}
            for i, pair in enumerate(labelled, start=1)
            if pair is not None
        ]
        return {
            **{k: v for k, v in slide.items() if k != "bullets"},
            "layout": "cards",
            "cards": cards,
        }
    return None


def _break_run(slide: dict[str, Any], previous: str) -> dict[str, Any] | None:
    """A second-choice layout for a bullet slide inside a run of the same layout."""
    bullets = _bullets(slide)
    if len(bullets) >= 4 and previous != "two_column":
        cut = (len(bullets) + 1) // 2
        return {
            **{k: v for k, v in slide.items() if k != "bullets"},
            "layout": "two_column",
            "left": {"heading": "", "bullets": bullets[:cut][:6]},
            "right": {"heading": "", "bullets": bullets[cut:][:6]},
        }
    if len(bullets) == 3 and previous != "cards" and all(len(b) <= 70 for b in bullets):
        cards = [
            {"title": b[:80], "body": "", "glyph": f"{i:02d}"} for i, b in enumerate(bullets, 1)
        ]
        return {
            **{k: v for k, v in slide.items() if k != "bullets"},
            "layout": "cards",
            "cards": cards,
        }
    return None


def layout_of(slide: Mapping[str, Any]) -> str:
    raw = str(slide.get("layout") or "bullets").strip().lower()
    return {"title": "cover", "metrics": "stat_callout"}.get(raw, raw)


def max_run(layouts: Iterable[str]) -> int:
    """The longest run of one layout in a row."""
    best = run = 0
    last = None
    for layout in layouts:
        run = run + 1 if layout == last else 1
        last = layout
        best = max(best, run)
    return best


def vary_layouts(slides: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The deck's slides with plain bullet slides re-laid out from their own content.

    Only slides whose layout is ``bullets`` change, and only into a layout that holds the same
    words (never a lossy one). Slides the model chose another layout for stay as they are, so a
    deck the model designed is untouched. Decks under four slides are left alone.
    """
    out = [dict(s) for s in slides]
    total = len(out)
    if total < 4:
        return out
    for index, slide in enumerate(out):
        if layout_of(slide) != "bullets":
            continue
        converted = _convert(slide, first=index == 0, last=index == total - 1, total=total)
        if converted is not None:
            out[index] = converted
    if total < 6:
        return out
    # Break every run longer than MAX_SAME_LAYOUT_RUN among the slides this module may change.
    for index in range(MAX_SAME_LAYOUT_RUN, total):
        window = [layout_of(out[index - k]) for k in range(MAX_SAME_LAYOUT_RUN + 1)]
        if len(set(window)) != 1:
            continue
        slide = out[index]
        if layout_of(slide) != "bullets":
            continue
        replacement = _break_run(slide, layout_of(out[index - 1]))
        if replacement is not None:
            out[index] = replacement
    return out
