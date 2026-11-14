"""Acceptance criteria: the quantities a user states for a deliverable, and the honest check.

A request such as "notes on Java, 50 pages" or "an 8-slide deck" carries a requirement that the
file must meet. This module (pure, no I/O)

* extracts the stated quantities from the user's text (``extract_acceptance``: English, Hinglish,
  number words, ranges),
* decides when a request needs the chapter pipeline (``is_long_document``, ``plan_chapters``),
* scales the document agent's time budget with the length (``scaled_document_timeout``),
* and compares what was produced with what was asked for (``check_acceptance``), with tolerances
  because a page count is a layout result, not something a model can hit exactly.

The criteria travel inside the existing JSON ``subtasks.input_data["acceptance"]``, so no database
migration is needed. See ``docs/architecture/DOCUMENT_GENERATION.md`` and ADR-0020.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.automation import model as doc_model

# --------------------------------------------------------------------------- constants
WORDS_PER_PAGE = 450
"""The nominal prose page used by the length arithmetic (words floor, chapter budgets, prompts)."""
THEME_WORDS_PER_PAGE: dict[str, int] = {
    "notes": 310,
    "executive": 275,
    "minimal": 225,
    "midnight": 275,
}
"""MEASURED words per content page of mixed study content, per theme (real Edge print, A4).

Measured by ``tests/unit/test_theme_density.py`` and the calibration run behind it: four 1,000-word
chapters (headings, paragraphs, bullets, a 10-line code block, a callout and, every second section,
a 5-row table) printed with each theme; the value is the median word count of the interior pages
(cover, contents and each chapter's last page excluded): notes 309, executive 274, minimal 223,
midnight 274. Pure prose fills about 726 (notes), 631 (executive and midnight) and 500 (minimal)
words per page; a lean mix with fewer code blocks and tables about 550 (notes). The notes theme was
made denser for this (line height 1.55 -> 1.45, margins 20 -> 16 mm, block gap 8 -> 6 pt, tighter
list, table, callout and code padding): its interior median rose from 227 to 309 words per page.
Real lite output (live run 3) averaged 182 and 203 words per page over the whole PDF on the old
layout, cover, contents and chapter tails included."""
NOTES_WORDS_PER_PAGE = THEME_WORDS_PER_PAGE["notes"]
CHAPTER_CAPACITY_WORDS = 900
"""Words one chapter call really yields: lite writes 1,000-1,200 words per call even when asked
for 2,000, so chapters are planned at about 900 words each (see ``plan_chapters``)."""
TARGET_WORDS_FRACTION = 0.85
# Up to this many pages ONE model call writes plan and Markdown (chat stays fast); above it the
# plan call + one Markdown call per chapter runs as a background job (ADR-0020).
LONG_DOCUMENT_PAGES = 5
LONG_DOCUMENT_WORDS = 3_500
MAX_CHAPTERS = 16
MIN_CHAPTERS = 2
MIN_CHAPTERS_FROM_PAGES = 3
"""From ``CHAPTERS_FLOOR_PAGES`` pages up a document has at least this many chapters."""
CHAPTERS_FLOOR_PAGES = 9
TARGET_CHAPTER_PAGES = 5
FRONT_MATTER_PAGES = 3
"""Cover page plus a contents list of one or two pages: not available to the chapters."""
CHAPTER_TAIL_PAGES = 0.5
"""Average unused part of each chapter's last page (the next chapter starts on a new page)."""
MAX_PIPELINE_PAGES = 100
"""The largest document the pipeline plans for (the model caps are sized for it)."""

PAGES_LOW_FRACTION = 0.90
WORDS_FLOOR_FRACTION = 0.65
"""Words a page-count request must contain, as a fraction of ``pages * WORDS_PER_PAGE``.

Pages alone are a layout result: a cover, a contents list and a page break per chapter inflate the
count. Measured on a real run: "about 12 pages" gave a 15-page PDF (inside the -10%/+25% band)
with 3,400 words (227 words per page on average, 250-340 on the content pages). 0.65 x 12 x 450 =
3,510 rejects that file while a document that really has the length (content pages at 290+ words)
passes. Tune it here; the check, the expand round and the messages all read this constant."""
CHAPTER_WORDS_FLOOR_FRACTION = 0.75
"""A chapter that returns under this share of its word target gets one expansion call."""
PAGES_HIGH_FRACTION = 1.25
WORDS_MIN_FRACTION = 0.85
SEVERE_FRACTION = 0.5
"""Under half of what was asked for is a failure, not a warning."""

MIN_DOCUMENT_TIMEOUT_SECONDS = 240.0
BASE_TIMEOUT_SECONDS = 60.0
SECONDS_PER_PAGE = 6.0
MAX_DOCUMENT_TIMEOUT_SECONDS = 600.0

KEYS: tuple[str, ...] = ("pages", "slides", "words", "sheets", "rows")

# --------------------------------------------------------------------------- extraction
_UNITS: dict[str, str] = {
    "pages": r"pages?|pgs?|पेज(?:ों|ेस)?|पन्ने|पृष्ठ",
    "slides": r"slides?|स्लाइड(?:ें|्स|ों)?",
    "words": r"words?|शब्द",
    "sheets": r"sheets?|worksheets?",
}
_BOUNDS: dict[str, tuple[int, int]] = {
    "pages": (1, 1000),
    "slides": (1, doc_model.MAX_SLIDES),
    "words": (50, 300_000),
    "sheets": (1, 20),
    "rows": (2, 50_000),
}
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_NUM = r"\d[\d,]*(?:\.\d+)?"
_SMALL = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}
_TENS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
_NUMBER_WORD = "|".join([*_SMALL, *_TENS, "hundred", "and", "a"])
_WORD_PHRASE = rf"(?:(?:{_NUMBER_WORD})(?:[\s-]+(?:{_NUMBER_WORD}))*)"


def _patterns() -> dict[str, tuple[re.Pattern[str], re.Pattern[str]]]:
    out: dict[str, tuple[re.Pattern[str], re.Pattern[str]]] = {}
    for key, unit in _UNITS.items():
        digits = re.compile(
            rf"(?<![\w.])({_NUM})\s*(k)?"
            rf"(?:\s*(?:-|–|—|to)\s*({_NUM})\s*(k)?)?"
            rf"\s*[-\s]?\s*(?:{unit})(?![\w])",
            re.IGNORECASE,
        )
        words = re.compile(rf"(?<![\w-])({_WORD_PHRASE})[\s-]*(?:{unit})(?![\w])", re.IGNORECASE)
        out[key] = (digits, words)
    return out


_PATTERNS = _patterns()


def _to_number(raw: str, kilo: str | None) -> float | None:
    try:
        value = float(raw.replace(",", ""))
    except ValueError:
        return None
    return value * 1000 if kilo else value


def _words_to_int(phrase: str) -> int | None:
    """``"fifty five"`` -> 55, ``"one hundred"`` -> 100 (up to 999); ``None`` if not a number."""
    tokens = [t for t in re.split(r"[\s-]+", phrase.lower()) if t]
    total = 0
    current = 0
    seen_number = False
    for token in tokens:
        if token in ("and", "a"):
            continue
        if token in _SMALL:
            current += _SMALL[token]
        elif token in _TENS:
            current += _TENS[token]
        elif token == "hundred":
            current = max(current, 1) * 100
        else:
            return None
        seen_number = True
    total += current
    return total if seen_number and total > 0 else None


def _within(key: str, value: float | None) -> int | None:
    if value is None:
        return None
    low, high = _BOUNDS[key]
    number = int(round(value))
    return number if low <= number <= high else None


_ROW_UNITS = r"rows?|records?|entries|entry|line\s+items?|पंक्तियाँ|पंक्तियों|पंक्ति"
_ROW_NOUNS = (
    r"products?|customers?|students?|employees?|users?|orders?|transactions?|invoices?|names?|"
    r"contacts?|leads?|people|persons?|vendors?|suppliers?|clients?|patients?|cities|companies|"
    r"books?|movies?|stocks?|sales|expenses|members?|teachers?|players?|items?|tasks?|tickets?|"
    r"accounts?|payments?|bills?|receipts?|skus?|stores?|shops?|dealers?|drivers?|vehicles?|"
    r"properties|listings?|jobs?|candidates?|applicants?|workers?|staff|emails?|addresses|"
    r"countries|vegetables|fruits|recipes|games|songs|courses|projects|expenses|leads"
)
_ROW_SHEET_CONTEXT = re.compile(
    r"(?i)\b(?:sheets?|spreadsheets?|excel|xlsx?|csv|tables?|worksheets?|workbooks?)\b|"
    r"शीट|एक्सेल"
)
_ROW_OTHER_UNIT = r"(?:pages?|slides?|words?|sheets?|columns?|cols?|tabs?|charts?|graphs?)"
_ROW_NOT_A_TOTAL = frozenset(
    "top best worst first last next only more another extra additional add aur plus upto "
    "least most around approx about".split()
)


def _row_pattern(unit: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?<![\w.])({_NUM})\s*(k)?(?:\s*(?:-|–|—|to)\s*({_NUM})\s*(k)?)?\s*"
        rf"(?:(?!{_ROW_OTHER_UNIT}\b)[A-Za-z][A-Za-z-]*\s+){{0,2}}?(?:{unit})(?![\w])",
        re.IGNORECASE,
    )


_ROW_UNIT_COUNT = _row_pattern(_ROW_UNITS)
_ROW_NOUN_COUNT = _row_pattern(_ROW_NOUNS)


def _extract_rows(cleaned: str) -> int | None:
    """The data-row count a user states for a sheet ("500 products ka sheet", "100 rows"), else
    ``None``. A plain noun counts only when the message also talks about a sheet, Excel or a
    table (so "5 tips on sleep" is not a row count); rows, records and entries always count."""
    patterns = [_ROW_UNIT_COUNT]
    if _ROW_SHEET_CONTEXT.search(cleaned):
        patterns.append(_ROW_NOUN_COUNT)
    for pattern in patterns:
        for match in pattern.finditer(cleaned):
            before = cleaned[: match.start()].split()
            if before and before[-1].lower().strip(",.:;()") in _ROW_NOT_A_TOTAL:
                continue
            low = _to_number(match.group(1), match.group(2))
            high = _to_number(match.group(3), match.group(4)) if match.group(3) else None
            value = (low + high) / 2 if low is not None and high is not None else low
            number = _within("rows", value)
            if number is not None:
                return number
    return None


def extract_acceptance(text: str | None) -> dict[str, int]:
    """Quantities the user stated, ``{"pages": 50}`` / ``{"slides": 8}`` / ``{}``.

    Only what is written is returned; nothing is inferred. A range ("40-50 pages") gives its
    midpoint (the check tolerates the rest). "A 50 page ka PDF", "50-page", "fifty pages",
    "२० पेज" and "2k words" are understood; "page 5" or "chapter 3" are not quantities.
    """
    if not text:
        return {}
    cleaned = text.translate(_DEVANAGARI_DIGITS)
    found: dict[str, int] = {}
    for key, (digits, words) in _PATTERNS.items():
        for match in digits.finditer(cleaned):
            low = _to_number(match.group(1), match.group(2))
            high = _to_number(match.group(3), match.group(4)) if match.group(3) else None
            value = (low + high) / 2 if low is not None and high is not None else low
            number = _within(key, value)
            if number is not None:
                found[key] = number
                break
        if key in found:
            continue
        for match in words.finditer(cleaned):
            number = _within(key, _words_to_int(match.group(1)))
            if number is not None:
                found[key] = number
                break
    rows = _extract_rows(cleaned)
    if rows is not None:
        found["rows"] = rows
    return found


APPROX_FRACTION = 0.15
"""'About', 'around' and 'approximately' accept this share either side of the stated number."""
RANGE_KEYS: tuple[str, ...] = ("pages", "slides", "rows")
_APPROX_BEFORE = re.compile(
    r"(?:\b(?:about|around|approx(?:imately)?|roughly|nearly|almost|circa|lagbhag|takreeban)\b|"
    r"~|लगभग|करीब)[\s.:]*$",
    re.IGNORECASE,
)


def approx_range(number: int) -> tuple[int, int]:
    """``(low, high)`` for 'about N': N -15% rounded up to N +15% rounded down (10 -> 9..11)."""
    # round() first: 100 * 1.15 is 114.99999999999999 in floating point, not 115.
    low = max(1, math.ceil(round(number * (1 - APPROX_FRACTION), 6)))
    high = max(low, math.floor(round(number * (1 + APPROX_FRACTION), 6)))
    return min(low, number), max(high, number)


def _range_of(cleaned: str, match: re.Match[str], key: str) -> tuple[int, int] | None:
    """The range a regex match states: ``8-10`` or an 'about N' qualifier right before it."""
    low = _to_number(match.group(1), match.group(2))
    high = _to_number(match.group(3), match.group(4)) if match.group(3) else None
    if low is not None and high is not None:
        first, second = _within(key, min(low, high)), _within(key, max(low, high))
        if first is not None and second is not None and first < second:
            return first, second
        return None
    number = _within(key, low)
    if number is None:
        return None
    if _APPROX_BEFORE.search(cleaned[max(0, match.start() - 24) : match.start()]):
        return approx_range(number)
    return None


def extract_ranges(text: str | None) -> dict[str, tuple[int, int]]:
    """Ranges the user wrote for a count: ``"8-10 pages"`` -> ``{"pages": (8, 10)}`` and
    ``"about 10 slides"`` -> ``{"slides": (9, 11)}`` (+-15%). Only a count that is a range or is
    qualified by 'about' / 'around' appears; a plain ``"10 slides"`` has no range (it is exact).
    The count itself is still ``extract_acceptance(text)`` (a range gives its midpoint)."""
    if not text:
        return {}
    cleaned = text.translate(_DEVANAGARI_DIGITS)
    found: dict[str, tuple[int, int]] = {}
    for key in ("pages", "slides"):
        for match in _PATTERNS[key][0].finditer(cleaned):
            low = _to_number(match.group(1), match.group(2))
            high = _to_number(match.group(3), match.group(4)) if match.group(3) else None
            value = (low + high) / 2 if low is not None and high is not None else low
            if _within(key, value) is None:
                continue
            span = _range_of(cleaned, match, key)
            if span is not None:
                found[key] = span
            break  # the same first usable match extract_acceptance reads
    patterns = [_ROW_UNIT_COUNT]
    if _ROW_SHEET_CONTEXT.search(cleaned):
        patterns.append(_ROW_NOUN_COUNT)
    for pattern in patterns:
        for match in pattern.finditer(cleaned):
            before = cleaned[: match.start()].split()
            if before and before[-1].lower().strip(",.:;()") in _ROW_NOT_A_TOTAL - {
                "about",
                "around",
                "approx",
                "least",
                "most",
            }:
                continue
            low = _to_number(match.group(1), match.group(2))
            high = _to_number(match.group(3), match.group(4)) if match.group(3) else None
            value = (low + high) / 2 if low is not None and high is not None else low
            if _within("rows", value) is None:
                continue
            span = _range_of(cleaned, match, "rows")
            if span is not None:
                found["rows"] = span
            return found
    return found


def normalize_acceptance(value: Any) -> dict[str, int]:
    """A clean ``{key: positive int}`` from stored JSON (unknown keys and bad values dropped)."""
    if not isinstance(value, dict):
        return {}
    out: dict[str, int] = {}
    for key in KEYS:
        raw = value.get(key)
        if isinstance(raw, bool) or not isinstance(raw, int | float):
            continue
        number = int(raw)
        if number > 0:
            out[key] = number
    return out


# --------------------------------------------------------------------------- planning
def requested_pages(acceptance: dict[str, int]) -> int | None:
    """Pages asked for, from ``pages`` or (estimated) from ``words``; ``None`` if neither."""
    if acceptance.get("pages"):
        return acceptance["pages"]
    if acceptance.get("words"):
        return max(1, math.ceil(acceptance["words"] / WORDS_PER_PAGE))
    return None


def is_long_document(acceptance: dict[str, int]) -> bool:
    """True when the request needs the outline -> chapters -> assemble pipeline."""
    return (acceptance.get("pages", 0) > LONG_DOCUMENT_PAGES) or (
        acceptance.get("words", 0) > LONG_DOCUMENT_WORDS
    )


@dataclass(frozen=True)
class ChapterPlan:
    """How a long document is split: ``chapters`` nodes of about ``pages_per_chapter`` each."""

    target_pages: int
    chapters: int
    pages_per_chapter: float
    capped: bool = False  # the request was larger than the pipeline plans for

    @property
    def words_per_chapter(self) -> int:
        return round(self.pages_per_chapter * WORDS_PER_PAGE)


def words_per_page(theme: str = "notes") -> int:
    """The measured words per content page of ``theme`` (``THEME_WORDS_PER_PAGE``)."""
    return THEME_WORDS_PER_PAGE.get(theme, NOTES_WORDS_PER_PAGE)


NOTES_FRONT_MATTER_PAGES = 0.5
"""A flowing document (notes, report) has no cover: only a title block and a compact contents."""
WORDS_FLOOR_PLAN_MARGIN = 1.1
"""A flowing document is planned 10% above its words floor, so a normal shortfall still passes."""


def plan_chapters(acceptance: dict[str, int], form: str | None = None) -> ChapterPlan:
    """Chapter count for the request, sized to what one model call really writes.

    ``target_words = pages x notes words per page x 0.85`` and ``chapters = ceil(target_words /
    900)``, from 3 to 16 (900 words is what lite yields per chapter call). 12 pages -> 4 chapters,
    50 pages -> 15, 100 pages -> 16; a larger request is planned as 100 pages (``capped``) and the
    honest result text says so.

    ``form`` ``notes`` or ``report`` (no cover, no page break per chapter): the page budget loses
    only a half-page title block and no chapter tails, and the chapters are planned to at least
    the words floor (``words_floor``) of the requested pages, capped by the accepted page band.
    Before this a 12-page notes request planned 7.2 body pages (2,200 words) although the words
    check wants 3,510. ``None`` keeps the book layout.
    """
    pages = requested_pages(acceptance) or LONG_DOCUMENT_PAGES + 1
    capped = pages > MAX_PIPELINE_PAGES
    target = min(pages, MAX_PIPELINE_PAGES)
    flowing = form in ("notes", "report")
    front = NOTES_FRONT_MATTER_PAGES if flowing else FRONT_MATTER_PAGES
    body_pages = max(2.0, target - front)
    target_words = target * NOTES_WORDS_PER_PAGE * TARGET_WORDS_FRACTION
    chapters = max(
        MIN_CHAPTERS_FROM_PAGES, min(MAX_CHAPTERS, math.ceil(target_words / CHAPTER_CAPACITY_WORDS))
    )
    if flowing:
        wanted = words_floor(target) * WORDS_FLOOR_PLAN_MARGIN / NOTES_WORDS_PER_PAGE
        ceiling = pages_bounds(target)[1] - front
        body_pages = max(body_pages, min(wanted, ceiling))
    else:
        # Each chapter's half-empty last page is paid for out of the page budget, not on top.
        body_pages = max(2.0, body_pages - chapters * CHAPTER_TAIL_PAGES)
    return ChapterPlan(
        target_pages=target,
        chapters=chapters,
        pages_per_chapter=round(body_pages / chapters, 1),
        capped=capped,
    )


def scaled_document_timeout(pages: int | None) -> float:
    """Document-agent time budget: 60 s plus 6 s per page, at least 240 s, at most 10 minutes."""
    if not pages or pages <= 0:
        return MIN_DOCUMENT_TIMEOUT_SECONDS
    return min(
        MAX_DOCUMENT_TIMEOUT_SECONDS,
        max(MIN_DOCUMENT_TIMEOUT_SECONDS, BASE_TIMEOUT_SECONDS + SECONDS_PER_PAGE * pages),
    )


# --------------------------------------------------------------------------- checking
def words_floor(pages: int) -> int:
    """Fewest words a document of ``pages`` pages may contain (see ``WORDS_FLOOR_FRACTION``)."""
    return math.ceil(pages * WORDS_PER_PAGE * WORDS_FLOOR_FRACTION)


def pages_bounds(requested: int) -> tuple[int, int]:
    """Accepted page range: -10% / +25%, and at least one page either way for short documents."""
    low = max(1, (9 * requested) // 10)
    high = max((5 * requested + 3) // 4, requested + 1)
    return low, high


@dataclass
class AcceptanceResult:
    """What was asked for, what was produced, and whether that is acceptable."""

    requested: dict[str, int]
    actual: dict[str, int]
    met: bool
    severe: bool = False
    shortfalls: list[str] = field(default_factory=list)
    # The same gaps in the plain words the user reads ("You asked for 10 pages; the file has 9").
    plain: list[str] = field(default_factory=list)

    @property
    def message(self) -> str:
        """``"Requested 50 pages, produced 38"`` for what is not met (empty when met)."""
        return "; ".join(self.shortfalls)

    @property
    def note(self) -> str:
        """What the user is told about a count that was not reached (empty when met)."""
        return " ".join(self.plain) if self.plain else self.message

    @property
    def status(self) -> str:
        """``met`` or ``warning``. A count that differs is NEVER a failure: the file is delivered
        with this note (``severe`` only says how far off it is)."""
        return "met" if self.met else "warning"

    def as_proof(self) -> dict[str, Any]:
        proof: dict[str, Any] = {
            "requested": dict(self.requested),
            "actual": dict(self.actual),
            "met": self.met,
            "status": self.status,
        }
        if self.shortfalls:
            proof["message"] = self.message
            proof["note"] = self.note
        return proof


def _label(key: str, count: int) -> str:
    singular = {
        "pages": "page",
        "slides": "slide",
        "words": "word",
        "sheets": "sheet",
        "rows": "row",
    }[key]
    return f"{count} {singular if count == 1 else key}"


def check_acceptance(
    acceptance: dict[str, int],
    measured: dict[str, int | None],
    *,
    exact_pages: bool = False,
    ranges: Mapping[str, tuple[int, int]] | None = None,
) -> AcceptanceResult:
    """Compare the measured file with the stated quantities.

    ``exact_pages`` (a short document whose layout the engine can fit, see
    ``app.automation.page_fit``): the page count must equal the request. Without it a page count
    is accepted inside the band below, which lets "2 pages" pass with 1 or 3.

    ``ranges`` (``extract_ranges``: "8-10 pages", "about 10 slides"): a pages, slides or rows
    count inside its range is met, whatever ``exact_pages`` says.

    ``measured`` holds what could be measured (``None`` or a missing key: not measurable, for
    example pages of a DOCX, which is then neither checked nor claimed). Pages are accepted
    within -10% / +25%, slides and sheets exactly, words at 85% or more.

    A count that is not met is a WARNING, never a rejection: the result says what was asked and
    what the file has (``note``) and the caller still delivers the file. ``severe`` (under half
    of the request) only records how far off it is; it no longer fails a step.
    """
    requested: dict[str, int] = {}
    actual: dict[str, int] = {}
    shortfalls: list[str] = []
    plain: list[str] = []
    severe = False
    spans = ranges or {}
    for key in KEYS:
        want = acceptance.get(key)
        got = measured.get(key)
        if not want or got is None:
            continue
        requested[key] = want
        actual[key] = got
        if key in spans and key in RANGE_KEYS:
            low, high = spans[key]
            ok = low <= got <= high
        elif key == "pages":
            low, high = (want, want) if exact_pages else pages_bounds(want)
            ok = low <= got <= high
        elif key == "words":
            ok = got >= math.ceil(want * WORDS_MIN_FRACTION)
        else:
            ok = got == want
        if ok and key == "pages":
            words = measured.get("words")
            floor = words_floor(want)
            if words is not None and words < floor:
                # Inside the page band but thin: layout inflated the page count.
                shortfalls.append(
                    f"Requested {_label(key, want)}, produced {_label(key, got)} but only "
                    f"{words:,} words (expected about {want * WORDS_PER_PAGE:,})"
                )
                plain.append(
                    f"You asked for {_label(key, want)}; the file has {_label(key, got)} but "
                    f"only {words:,} words (about {want * WORDS_PER_PAGE:,} are expected for "
                    "that length)."
                )
                actual["words"] = words
                if words < floor * SEVERE_FRACTION:
                    severe = True
            continue
        if ok:
            continue
        extra = ""
        if key == "pages" and measured.get("words") is not None:
            words = int(measured["words"] or 0)
            actual["words"] = words
            extra = f" and {words:,} words"
            if words < words_floor(want):
                extra += f" (about {want * WORDS_PER_PAGE:,} words are expected for that length)"
        shortfalls.append(
            f"Requested {_label(key, want)}, produced {_label(key, got) if extra else got}{extra}"
        )
        plain.append(f"You asked for {_label(key, want)}; the file has {_label(key, got)}{extra}.")
        if got < want * SEVERE_FRACTION:
            severe = True
    return AcceptanceResult(
        requested=requested,
        actual=actual,
        met=not shortfalls,
        severe=severe,
        shortfalls=shortfalls,
        plain=plain,
    )


__all__ = [
    "FRONT_MATTER_PAGES",
    "LONG_DOCUMENT_PAGES",
    "LONG_DOCUMENT_WORDS",
    "MAX_CHAPTERS",
    "MAX_PIPELINE_PAGES",
    "NOTES_WORDS_PER_PAGE",
    "THEME_WORDS_PER_PAGE",
    "WORDS_PER_PAGE",
    "AcceptanceResult",
    "ChapterPlan",
    "CHAPTER_WORDS_FLOOR_FRACTION",
    "WORDS_FLOOR_FRACTION",
    "APPROX_FRACTION",
    "approx_range",
    "check_acceptance",
    "extract_acceptance",
    "extract_ranges",
    "is_long_document",
    "normalize_acceptance",
    "pages_bounds",
    "plan_chapters",
    "requested_pages",
    "scaled_document_timeout",
    "words_floor",
    "words_per_page",
]
