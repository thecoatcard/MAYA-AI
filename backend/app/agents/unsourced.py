"""Honest handling of content written without web sources.

When grounded search is unavailable (quota) or the request needed no research, a document, deck or
outline may only contain what the user supplied and general, hedged statements. This module holds
the shared wording, the prompt rule, and a deterministic scan that finds specific figures
(percentages, currency amounts, numbers with units, years in a claim, quotes) so they can be
rephrased once and otherwise reported as a warning. Nothing here calls a model.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

# Result text of a research step that could not retrieve any source.
NO_SOURCES_TEXT = "No web sources could be retrieved (search quota)"
# Final task warning when the deliverable was written without web sources.
UNSOURCED_WARNING_PREFIX = "Written without web sources"
UNSOURCED_WARNING = f"{UNSOURCED_WARNING_PREFIX}: figures were not verified."
# Visible line inside the deliverable (front matter, last slide notes or footer line).
PREPARED_WITHOUT_SOURCES_LINE = "Prepared without web sources: no figures are cited."
# Warning when the post-check still finds specific figures after one repair call.
FIGURES_REMAIN_WARNING = (
    "Written without web sources: some specific figures could not be removed and are not verified: "
    "{examples}."
)

UNSOURCED_PROMPT_RULE = (
    "\nNo web sources are available for this task. Do NOT write specific statistics, "
    "percentages, currency amounts, prices, dates attached to a claim, named studies, surveys, "
    "reports or quotes unless the user gave them in the task. Write general, clearly hedged "
    "statements instead (for example 'many teams report', 'typically', 'often'), and never "
    "invent a number to make a point sound precise."
)

REPAIR_INSTRUCTION = (
    "The text contains specific figures that cannot be verified because no web sources were "
    "available: {examples}. Rephrase every such sentence without invented figures (no "
    "percentages, amounts, prices, named studies or quotes). Keep everything else unchanged."
)

_UNITS = (
    r"(?:%|percent|per\s?cent|x\b|k\b|bn\b|million|billion|trillion|thousand|users?|"
    r"customers?|people|employees|companies|mph|km/h|kmh|kg|kw|kwh|mw|gb|tb|fps|mah|"
    r"usd|eur|inr|gbp|dollars?|euros?|rupees?|crore|lakh)"
)
_CURRENCY = (
    r"(?:[$€£₹]|\b(?:USD|EUR|INR|GBP|Rs\.?)\s?)\s?\d[\d,.]*"
    r"(?:\s?(?:k\b|m\b|bn\b|million|billion))?"
)
_FIGURE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\d[\d,.]*\s?%"),
    re.compile(_CURRENCY, re.I),
    re.compile(rf"\b\d[\d,.]*\s?{_UNITS}", re.I),
)
# "in 2023", "by 2030": a year used as a dated claim.
_YEAR_CLAIM = re.compile(r"\b(?:in|by|since|until|during|from)\s+(?:19|20)\d{2}\b", re.I)
_QUOTE = re.compile(r"[\"“][^\"”]{25,}[\"”]")
# Numbers that carry no claim: the sections the user asked for ("5 tips", "3 slides") are not
# statistics, and small counts in a list are fine.
_BENIGN_UNITS = re.compile(
    r"\b\d{1,2}\s?(?:pages?|words?|slides?|tips?|steps?|minutes?|mins?)\b", re.I
)


def find_invented_figures(texts: Iterable[str], allowed_text: str = "") -> list[str]:
    """Specific figures in ``texts`` that the user's own request (``allowed_text``) lacks.

    A figure the user wrote in the task is allowed. Returns unique matches in first-seen order.
    """
    allowed = allowed_text.lower()
    found: list[str] = []
    seen: set[str] = set()
    for text in texts:
        if not text:
            continue
        scrubbed = _BENIGN_UNITS.sub(" ", text)
        for pattern in (*_FIGURE_PATTERNS, _YEAR_CLAIM, _QUOTE):
            for match in pattern.finditer(scrubbed):
                figure = " ".join(match.group(0).split())
                key = figure.lower()
                if key in seen or key in allowed or any(key in other for other in seen):
                    continue
                seen.add(key)
                found.append(figure)
    return found


def collect_texts(value: Any, out: list[str] | None = None) -> list[str]:
    """Every string in a (slide / block / section) structure, for scanning."""
    texts = out if out is not None else []
    if isinstance(value, str):
        texts.append(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            if key in ("type", "layout", "kind", "level", "src", "language", "theme"):
                continue
            collect_texts(item, texts)
    elif isinstance(value, (list, tuple)):
        for item in value:
            collect_texts(item, texts)
    return texts
