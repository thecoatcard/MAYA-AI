"""Page-exact documents: count the REAL pages of a built file and fit the layout to a stated count.

A request such as "a 2 page PDF" states an exact page count. A page count is a layout result, so
the engine measures the file it built and repairs it:

    render -> count real pages -> change the density step -> render again (at most
    :data:`MAX_FIT_ROUNDS` re-renders) -> keep the closest file and say what it measured.

* PDF pages are counted with pypdf on the produced file.
* A DOCX has no fixed pages. Its pages are counted only by converting it with LibreOffice (the
  converter :mod:`app.automation.preview` already uses). With no converter on the machine the
  count is ``None``: it is never guessed. A word-count figure is offered separately and is
  always labelled an estimate.
* The repair here is deterministic. It first changes only the density step
  (``Document.fit_step``, see ``design.fit_sizes``: body size, line height, gaps and margins inside
  readable limits). Only when that ends exactly one page off, and only when the caller allows it
  (``adjust_content``), one more render adds a short, labelled Notes page or cuts a trailing
  appendix / sources section or page break. Changing the amount of real content is otherwise the
  caller's job, and only where a model callback exists (the task healer, the chat tool loop).

Nothing here raises for a missing tool or an unreadable file: the count is ``None`` instead.
"""

from __future__ import annotations

import logging
import math
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.automation.acceptance import words_per_page
from app.automation.design import FIT_MAX_STEP, FIT_MIN_STEP
from app.automation.docx_engine import create_document_from_model
from app.automation.long_document import word_count
from app.automation.model import (
    MAX_BLOCKS,
    Document,
    HeadingBlock,
    PageBreakBlock,
    ParagraphBlock,
)
from app.automation.pdf_engine import create_pdf_from_document
from app.automation.preview import _libreoffice_to_pdf, libreoffice_executable

logger = logging.getLogger(__name__)

MAX_FIT_ROUNDS = 3
FIT_REACH = 1.6
"""How far three density steps can move a page count (about 15 % per step). A file further from
the request than this is not re-rendered: only a change of content can fix it."""
DOCX_COUNT_TIMEOUT_SECONDS = 60.0

METHOD_PDF = "pypdf"
METHOD_DOCX = "libreoffice"
METHOD_NONE = "not_measured"


def count_pdf_pages(path: Path | str) -> int | None:
    """The page count of a PDF file read with pypdf, or ``None`` when it cannot be read."""
    try:
        from pypdf import PdfReader

        return len(PdfReader(str(path)).pages)
    except Exception as err:  # an unreadable file has no count; the verifier reports the file
        logger.warning("page_count_pdf_failed error=%s", type(err).__name__)
        return None


def count_docx_pages(
    path: Path | str, *, timeout_seconds: float = DOCX_COUNT_TIMEOUT_SECONDS
) -> int | None:
    """The page count of a DOCX as LibreOffice lays it out, or ``None`` when no converter is
    installed or the conversion fails. Never a guess. Blocking: call it from a worker thread."""
    executable = libreoffice_executable()
    if executable is None:
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="maya-pages-") as work:
            pdf = _libreoffice_to_pdf(executable, Path(path), Path(work), timeout_seconds)
            return count_pdf_pages(pdf)
    except (RuntimeError, OSError, subprocess.SubprocessError) as err:
        logger.warning("page_count_docx_failed error=%s", type(err).__name__)
        return None


def measure_pages(path: Path | str, kind: str) -> tuple[int | None, str]:
    """``(pages, method)`` of a built ``pdf`` or ``docx``; ``(None, "not_measured")`` otherwise."""
    if kind == "pdf":
        pages = count_pdf_pages(path)
        return pages, METHOD_PDF if pages is not None else METHOD_NONE
    if kind == "docx":
        pages = count_docx_pages(path)
        return pages, METHOD_DOCX if pages is not None else METHOD_NONE
    return None, METHOD_NONE


def estimate_pages(words: int, theme: str = "notes") -> int:
    """A page ESTIMATE from a word count (the theme's measured words per page). Not a count."""
    return max(1, math.ceil(max(0, words) / words_per_page(theme)))


FILL_MIN_RATIO = 0.55
"""A page below this share of the theme's words per page is thin (cover, contents and the last
page are exempt, as is one short page per forced page break)."""
_RUNNING_PAGE = re.compile(r"Page\s+\d+\s+of\s+\d+")


@dataclass(frozen=True)
class FillReport:
    """Words on each page of a built PDF against what one page of its theme holds."""

    words: tuple[int, ...]
    capacity: int
    thin: tuple[int, ...]  # 1-based page numbers that are too empty

    @property
    def ok(self) -> bool:
        return not self.thin

    @property
    def note(self) -> str:
        """One plain sentence for the model and the user (empty when every page is filled)."""
        if not self.thin:
            return ""
        pages = ", ".join(str(n) for n in self.thin[:8])
        return (
            f"Page{'s' if len(self.thin) > 1 else ''} {pages} "
            f"{'are' if len(self.thin) > 1 else 'is'} less than half full "
            f"(a page of this theme holds about {self.capacity} words; the thinnest has "
            f"{min(self.words[n - 1] for n in self.thin)})"
        )


def page_word_counts(path: Path | str, title: str = "") -> list[int]:
    """Words of body text on each page of a PDF (running header and footer words removed)."""
    from pypdf import PdfReader

    counts: list[int] = []
    for page in PdfReader(str(path)).pages:
        text = page.extract_text() or ""
        text = _RUNNING_PAGE.sub(" ", text).replace("MAYA AI", " ")
        if title:
            text = text.replace(title, " ", 1) if text.lstrip().startswith(title) else text
        counts.append(len(text.split()))
    return counts


def fill_report(
    counts: Sequence[int],
    *,
    theme: str = "notes",
    leading_pages: int = 0,
    page_breaks: int = 0,
) -> FillReport:
    """Which interior pages are too empty. ``leading_pages`` (cover, contents) and the last page
    are exempt, and ``page_breaks`` short pages are allowed (each forced break can end a page
    early); the emptiest remaining pages beyond that allowance are the ones reported."""
    capacity = words_per_page(theme)
    floor = capacity * FILL_MIN_RATIO
    interior = [(n, c) for n, c in enumerate(counts, start=1) if leading_pages < n < len(counts)]
    short = sorted((c, n) for n, c in interior if c < floor)
    flagged = short[max(0, page_breaks) :] if page_breaks else short
    return FillReport(tuple(counts), capacity, tuple(sorted(n for _c, n in flagged)))


def _plural(count: int, unit: str) -> str:
    return f"{count} {unit[:-1] if count == 1 else unit}"


@dataclass(frozen=True)
class PageFit:
    """What was asked for, what the built file really has, and how that was learned."""

    requested: int
    measured: int | None
    method: str = METHOD_NONE
    unit: str = "pages"  # "pages" (pdf, docx) or "slides" (pptx)
    rounds: int = 0  # re-renders made to fit the count
    fit_step: int = 0  # density step of the kept file (0 = the theme as it is)
    estimate: int | None = None  # from the word count; only when nothing could be measured
    kind: str = ""
    # ``(low, high)`` the user accepts ("8-10 pages", "about 10 slides"); ``None``: exactly
    # ``requested``.
    accepted: tuple[int, int] | None = None
    # What was done to the content to reach the count (a Notes page added, trailing appendix cut).
    adjustment: str = ""

    @property
    def exact(self) -> bool:
        """True when the count is what the user accepts (the stated number or inside its range)."""
        if self.measured is None:
            return False
        if self.accepted is not None:
            return self.accepted[0] <= self.measured <= self.accepted[1]
        return self.measured == self.requested

    @property
    def note(self) -> str:
        """One plain sentence for the result the model and the user read."""
        asked = _plural(self.requested, self.unit)
        if self.measured is None:
            label = self.kind.upper() or "file"
            text = (
                f"Asked for {asked}. The page count of this {label} could not be measured on "
                "this machine (no working LibreOffice converter)"
            )
            if self.estimate is not None:
                text += (
                    f"; it is about {_plural(self.estimate, self.unit)} by word count, which is "
                    "an estimate only, not a measurement"
                )
            return text + "."
        if self.exact and self.measured != self.requested and self.accepted is not None:
            low, high = self.accepted
            text = (
                f"The file has {_plural(self.measured, self.unit)}, inside the {low}-{high} "
                "you asked for"
            )
        elif self.exact:
            text = f"The file has {_plural(self.measured, self.unit)}, as asked"
            if self.fit_step:
                text += " (the layout density was adjusted to fit)"
        else:
            text = f"You asked for {asked}; the file has {self.measured}"
            if self.rounds:
                text += (
                    "; the layout density was adjusted within readable limits and could not "
                    f"reach {self.requested}"
                )
        if self.adjustment:
            text += f"; {self.adjustment}"
        if self.method == METHOD_DOCX:
            text += "; pages were counted with LibreOffice, and Word can paginate differently"
        return text + "."

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "unit": self.unit,
            "requested": self.requested,
            "measured": self.measured,
            "exact": self.exact,
            "method": self.method,
            "rounds": self.rounds,
            "fit_step": self.fit_step,
            "note": self.note,
        }
        if self.estimate is not None:
            data["estimate"] = self.estimate
        if self.accepted is not None:
            data["accepted"] = list(self.accepted)
        if self.adjustment:
            data["adjustment"] = self.adjustment
        return data


@dataclass(frozen=True)
class FitOutcome[T]:
    """The kept render of :func:`fit_to_pages`."""

    result: T
    measured: int | None
    step: int
    rounds: int


def within_reach(requested: int, measured: int) -> bool:
    """True when a density change alone can plausibly move ``measured`` pages to ``requested``.

    ``measured`` pages hold between ``measured - 1`` and ``measured`` pages of content.
    """
    if measured > requested:
        return (measured - 1) <= requested * FIT_REACH
    return measured * FIT_REACH > requested - 1


def fit_to_pages[T](
    requested: int,
    render: Callable[[int], tuple[T, int | None]],
    *,
    max_rounds: int = MAX_FIT_ROUNDS,
    accepted: tuple[int, int] | None = None,
) -> FitOutcome[T]:
    """Render at density step 0, then up to ``max_rounds`` more steps toward ``requested`` pages.

    ``render(step)`` builds the file at that step and returns ``(its result, measured pages)``;
    each call must leave its result usable after later calls (the kept one may not be the last).
    The search stops when the count is exact, when a step overshoots to the other side (no step
    in between exists), when the count cannot be measured, or when the request is out of reach.
    The closest count is kept; on a tie the step nearer to the theme's own layout wins.
    ``accepted`` ("8-10 pages"): a count inside that span is as good as the exact number.
    """

    def fits(count: int) -> bool:
        if accepted is not None and accepted[0] <= count <= accepted[1]:
            return True
        return count == requested

    result, measured = render(0)
    best = FitOutcome(result, measured, 0, 0)
    if measured is None or fits(measured) or not within_reach(requested, measured):
        return best
    direction = -1 if measured > requested else 1
    rounds = 0
    for count in range(1, max(0, min(max_rounds, MAX_FIT_ROUNDS)) + 1):
        step = direction * count
        if not FIT_MIN_STEP <= step <= FIT_MAX_STEP:
            break
        result, got = render(step)
        rounds += 1
        if got is None:
            break
        if (
            best.measured is None
            or (fits(got) and not fits(best.measured))
            or abs(got - requested) < abs(best.measured - requested)
        ):
            best = FitOutcome(result, got, step, rounds)
        if fits(got) or (got - requested) * direction > 0:
            break  # exact, or past the target: nothing between two steps can be chosen
    return FitOutcome(best.result, best.measured, best.step, rounds)


@dataclass(frozen=True)
class PagedBuild:
    """A built pdf or docx, fitted to a stated page count when one was given."""

    path: Path
    warnings: tuple[str, ...]
    renderer: str
    browser: str | None
    fit: PageFit | None  # None when no count was stated


def build_paged_file(
    document: Document,
    kind: str,
    out: Path,
    *,
    assets_root: Path,
    requested: int | None,
    workdir: Path,
    render_pdf: Callable[..., Any] = create_pdf_from_document,
    render_docx: Callable[..., Any] = create_document_from_model,
    accepted: tuple[int, int] | None = None,
    adjust_content: bool = False,
) -> PagedBuild:
    """Render ``document`` as ``pdf`` or ``docx`` into ``out``; with ``requested`` pages, fit it.

    Blocking (browser print, LibreOffice): call it from a worker thread. The fit renders the
    SAME content at other density steps in ``workdir`` and copies the closest one to ``out``.
    ``render_pdf`` and ``render_docx`` are the renderers (a caller or a test may pass its own).

    ``accepted`` ("8-10 pages"): a count inside the span needs no fit. ``adjust_content`` (count
    policy): when the density fit ends exactly one page off, one more render changes the CONTENT
    a little: a short, labelled Notes page is added (one page short) or a trailing appendix /
    sources section or trailing page break is cut (one page over). The adjusted file is kept only
    when it lands on the requested count; otherwise the density-fitted file stays. A count that
    still cannot be reached is reported by ``fit``, never hidden, and never rejects the file.
    """

    def render_doc(doc: Document, target: Path) -> tuple[PagedBuild, int | None]:
        target.parent.mkdir(parents=True, exist_ok=True)
        if kind == "pdf":
            pdf = render_pdf(doc, target, assets_root=assets_root)
            built = PagedBuild(pdf.path, tuple(pdf.warnings), pdf.renderer, pdf.browser, None)
        else:
            docx = render_docx(doc, target, assets_root=assets_root)
            built = PagedBuild(docx.path, tuple(docx.warnings), docx.renderer, None, None)
        return built, None if requested is None else measure_pages(built.path, kind)[0]

    def render(step: int) -> tuple[PagedBuild, int | None]:
        target = out if requested is None else workdir / f"fit{step:+d}" / out.name
        fitted = document if step == 0 else document.model_copy(update={"fit_step": step})
        return render_doc(fitted, target)

    if requested is None:
        return render(0)[0]
    kept = fit_to_pages(requested, render, accepted=accepted)
    built = kept.result
    measured = kept.measured
    adjustment = ""
    low, high = accepted if accepted is not None else (requested, requested)
    if (
        adjust_content
        and measured is not None
        and not low <= measured <= high
        and abs(measured - requested) == 1
    ):
        changed = _adjusted_document(document, kept.step, measured < requested)
        if changed is not None:
            candidate, said = changed
            adjusted, count = render_doc(candidate, workdir / "adjust" / out.name)
            if count is not None and low <= count <= high:
                built, measured, adjustment = adjusted, count, said
    if built.path != out:
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(built.path, out)
    method = METHOD_PDF if kind == "pdf" else METHOD_DOCX
    estimate: int | None = None
    if measured is None:
        method = METHOD_NONE
        estimate = estimate_pages(
            word_count([block.model_dump() for block in document.blocks]), document.theme
        )
    fit = PageFit(
        requested=requested,
        measured=measured,
        method=method,
        rounds=kept.rounds,
        fit_step=kept.step,
        estimate=estimate,
        kind=kind,
        accepted=accepted,
        adjustment=adjustment,
    )
    return PagedBuild(out, built.warnings, built.renderer, built.browser, fit)


_TRAILING_SECTION = re.compile(
    r"^\s*(appendix|appendices|notes|sources?|references?|bibliography|further reading)\b",
    re.IGNORECASE,
)
NOTES_PAGE_TEXT = (
    "This page was added so the document has the number of pages you asked for. "
    "Use it for your own notes."
)


def _adjusted_document(document: Document, step: int, short: bool) -> tuple[Document, str] | None:
    """``document`` with one page's worth of content changed, and a plain sentence on what changed.

    ``short``: a labelled Notes page (page break, heading, one paragraph) goes at the end. Else:
    trailing page breaks, then a trailing appendix / sources / notes section, are removed.
    ``None`` when nothing safe can be changed (the document is left as it is).
    """
    blocks = list(document.blocks)
    base = document if step == 0 else document.model_copy(update={"fit_step": step})
    if short:
        extra = [
            PageBreakBlock(),
            HeadingBlock(level=2, text="Notes"),
            ParagraphBlock(text=NOTES_PAGE_TEXT),
        ]
        if len(blocks) + len(extra) > MAX_BLOCKS:
            return None
        return (
            base.model_copy(update={"blocks": [*blocks, *extra]}),
            "a labelled Notes page was added to reach the count",
        )
    trimmed = list(blocks)
    removed_break = False
    while len(trimmed) > 1 and trimmed[-1].type == "page_break":
        trimmed.pop()
        removed_break = True
    heading_at = next(
        (
            index
            for index in range(len(trimmed) - 1, 0, -1)
            if trimmed[index].type == "heading"
            and _TRAILING_SECTION.match(getattr(trimmed[index], "text", ""))
        ),
        None,
    )
    if heading_at is not None and heading_at > 0:
        later_headings = any(b.type == "heading" for b in trimmed[heading_at + 1 :])
        if not later_headings:
            name = str(getattr(trimmed[heading_at], "text", "the last section")).strip()
            kept = trimmed[:heading_at]
            while len(kept) > 1 and kept[-1].type == "page_break":
                kept.pop()  # the cut section's own page break goes with it
            return (
                base.model_copy(update={"blocks": kept}),
                f"the trailing '{name}' section was removed to reach the count",
            )
    if removed_break:
        return (
            base.model_copy(update={"blocks": trimmed}),
            "trailing blank space (a page break) was removed to reach the count",
        )
    return None


__all__ = [
    "FIT_REACH",
    "MAX_FIT_ROUNDS",
    "METHOD_DOCX",
    "METHOD_NONE",
    "METHOD_PDF",
    "FILL_MIN_RATIO",
    "FillReport",
    "FitOutcome",
    "PageFit",
    "PagedBuild",
    "build_paged_file",
    "count_docx_pages",
    "count_pdf_pages",
    "estimate_pages",
    "fill_report",
    "fit_to_pages",
    "measure_pages",
    "page_word_counts",
    "within_reach",
]
