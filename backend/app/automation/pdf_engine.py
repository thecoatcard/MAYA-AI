"""PDF generation.

Primary renderer: the themed HTML from :mod:`app.automation.html_render` printed by the system
Edge or Chrome (see :mod:`app.automation.browser_pdf`). It handles Devanagari and other
Unicode scripts through the browser's own font fallback.

Fallback renderer: ReportLab, used only when no browser is available or it fails. The result
always says which renderer produced the file (``renderer``) and carries a warning when the
fallback was used, so a caller can never mistake a fallback file for a browser-rendered one.
ReportLab's built-in fonts cannot draw non-Latin scripts; that is reported as a warning too.
"""

from __future__ import annotations

import io
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    HRFlowable,
    Image,
    KeepTogether,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.automation.assets import decode_data_uri, make_image_resolver
from app.automation.browser_pdf import BrowserPdfRenderer, get_browser_renderer
from app.automation.design import fit_theme
from app.automation.html_render import (
    CHAPTER_MIN_H1,
    ImageResolver,
    h1_count,
    render_document_html,
    toc_entries,
    wants_chapter_breaks,
)
from app.automation.inline import Span, parse_inline
from app.automation.model import (
    BulletsBlock,
    CalloutBlock,
    CodeBlock,
    Document,
    HeadingBlock,
    ImageBlock,
    MetricsBlock,
    NumberedBlock,
    PageBreakBlock,
    ParagraphBlock,
    QuoteBlock,
    TableBlock,
    document_from_legacy,
)
from app.automation.themes import Theme, themed

logger = logging.getLogger(__name__)

RENDERER_BROWSER = "browser"
RENDERER_FALLBACK = "reportlab-fallback"

# Browser budget: the default (45 s) is for a report; a long document is printed with a budget
# that grows with its block count (about 12 blocks per page, see ``render_timeout_seconds``).
BASE_RENDER_TIMEOUT_SECONDS = 45.0
RENDER_SECONDS_PER_BLOCK = 0.12
MAX_RENDER_TIMEOUT_SECONDS = 240.0


def render_timeout_seconds(block_count: int) -> float:
    """Browser print budget for a document of ``block_count`` blocks (45 s up to 240 s)."""
    return min(
        MAX_RENDER_TIMEOUT_SECONDS,
        max(BASE_RENDER_TIMEOUT_SECONDS, 20.0 + block_count * RENDER_SECONDS_PER_BLOCK),
    )


@dataclass(frozen=True)
class PdfRenderResult:
    """Outcome of :func:`create_pdf_from_document`."""

    path: Path
    renderer: str  # RENDERER_BROWSER or RENDERER_FALLBACK
    theme: str
    warnings: list[str] = field(default_factory=list)
    browser: str | None = None  # for example "msedge 157.0.4341.42"
    # Browser prints made (a document with a contents list is printed twice to learn the pages).
    passes: int = 1
    # True: the contents list shows measured page numbers; False: they could not be measured so
    # it shows none (never a guess); None: the document has no contents list.
    toc_numbered: bool | None = None


# --------------------------------------------------------------------------- assets
def _image_warnings(document: Document, resolver: ImageResolver | None) -> list[str]:
    warnings: list[str] = []
    for block in document.blocks:
        if isinstance(block, ImageBlock) and not block.src.startswith("data:"):
            if resolver is None or resolver(block.src) is None:
                warnings.append(f"Image '{block.src}' could not be loaded; a placeholder was used")
    return warnings


def _has_non_latin(document: Document) -> bool:
    texts: list[str] = [document.title, document.subtitle or "", document.author or ""]
    for block in document.blocks:
        for key in ("text", "caption", "by"):
            texts.append(getattr(block, key, None) or "")
        for item in getattr(block, "items", None) or []:
            texts.append(item if isinstance(item, str) else f"{item.value} {item.label}")
        texts.extend(getattr(block, "headers", None) or [])
        for row in getattr(block, "rows", None) or []:
            texts.extend(row)
    return any(ord(ch) > 0x024F for text in texts for ch in text)


# --------------------------------------------------------------------------- reportlab fallback
def _hex(value: str) -> colors.Color:
    return colors.HexColor(value)


class _ThemedCanvas(canvas.Canvas):
    """Canvas that prints ``Page N of M`` and a running header, using the theme muted color."""

    theme: Theme
    header_text: str = ""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._saved_page_states: list[dict[str, Any]] = []

    def showPage(self) -> None:
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()  # type: ignore[attr-defined]

    def save(self) -> None:
        total = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self._decorate(total)
            super().showPage()
        super().save()

    def _decorate(self, total_pages: int) -> None:
        theme = type(self).theme
        page = getattr(self, "_pageNumber", 1)
        width, height = A4
        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(_hex(theme.colors.muted))
        self.setStrokeColor(_hex(theme.colors.border))
        self.setLineWidth(0.5)
        if page > 1:
            self.drawString(18 * mm, height - 14 * mm, type(self).header_text[:90])
            self.line(18 * mm, height - 16 * mm, width - 18 * mm, height - 16 * mm)
        self.drawString(18 * mm, 12 * mm, "MAYA AI")
        self.drawRightString(width - 18 * mm, 12 * mm, f"Page {page} of {total_pages}")
        self.line(18 * mm, 16 * mm, width - 18 * mm, 16 * mm)
        self.restoreState()


def _markup(text: str, theme: Theme) -> str:
    """Inline spans to ReportLab paragraph markup (escaped)."""

    def one(span: Span) -> str:
        piece = escape(span.text).replace("\n", "<br/>")
        if span.code:
            piece = f'<font face="Courier" backColor="{theme.colors.code_bg}">{piece}</font>'
        if span.bold:
            piece = f"<b>{piece}</b>"
        if span.italic:
            piece = f"<i>{piece}</i>"
        if span.href:
            href = escape(span.href, {chr(34): "&quot;"})
            piece = f'<a href="{href}" color="{theme.colors.link}">{piece}</a>'
        return piece

    return "".join(one(s) for s in parse_inline(text))


def _render_reportlab(document: Document, path: Path, resolver: ImageResolver | None) -> None:
    theme = fit_theme(
        themed(document.theme, document.accent_color, document.design), document.fit_step
    )
    c, s = theme.colors, theme.sizes
    base = getSampleStyleSheet()["Normal"]
    body = ParagraphStyle(
        "body",
        parent=base,
        fontName="Helvetica",
        fontSize=s.body_pt,
        leading=s.body_pt * 1.45,
        textColor=_hex(c.text),
        spaceAfter=s.block_gap_pt * 0.7,
    )
    small = ParagraphStyle("small", parent=body, fontSize=s.small_pt + 0.5, textColor=_hex(c.muted))
    heads = {
        level: ParagraphStyle(
            f"h{level}",
            parent=body,
            fontName="Helvetica-Bold",
            fontSize=size,
            leading=size * 1.25,
            textColor=_hex(color),
            spaceBefore=s.block_gap_pt * 1.4,
            spaceAfter=s.block_gap_pt * 0.6,
            keepWithNext=True,
        )
        for level, size, color in (
            (1, s.h1_pt, c.heading),
            (2, s.h2_pt, c.heading),
            (3, s.h3_pt, c.primary),
        )
    }
    cell = ParagraphStyle("cell", parent=body, fontSize=s.body_pt - 0.5, spaceAfter=0)
    cell_head = ParagraphStyle(
        "cellhead", parent=cell, fontName="Helvetica-Bold", textColor=_hex(c.table_header_text)
    )

    story: list[Any] = [
        Paragraph(
            "MAYA AI",
            ParagraphStyle(
                "brand",
                parent=small,
                fontName="Helvetica-Bold",
                textColor=_hex(c.accent),
                spaceAfter=6,
            ),
        ),
        Paragraph(
            escape(document.title),
            ParagraphStyle(
                "title",
                parent=body,
                fontName="Helvetica-Bold",
                fontSize=s.title_pt - 4,
                leading=(s.title_pt - 4) * 1.15,
                textColor=_hex(c.heading),
                spaceAfter=4,
            ),
        ),
    ]
    if document.subtitle:
        story.append(
            Paragraph(
                _markup(document.subtitle, theme),
                ParagraphStyle("sub", parent=body, fontSize=s.subtitle_pt, textColor=_hex(c.muted)),
            )
        )
    meta = " · ".join(p for p in (document.author, document.date) if p)
    if meta:
        story.append(Paragraph(escape(meta), small))
    story.append(
        HRFlowable(width="100%", thickness=2, color=_hex(c.accent), spaceBefore=4, spaceAfter=12)
    )

    previous_was_break = True  # drop leading page breaks
    blocks = list(document.blocks)
    while blocks and isinstance(blocks[-1], PageBreakBlock):
        blocks.pop()
    for block in blocks:
        is_break = isinstance(block, PageBreakBlock)
        if is_break and previous_was_break:
            continue
        previous_was_break = is_break
        if isinstance(block, HeadingBlock):
            story.append(Paragraph(_markup(block.text, theme), heads[block.level]))
        elif isinstance(block, ParagraphBlock):
            story.append(Paragraph(_markup(block.text, theme), body))
        elif isinstance(block, BulletsBlock | NumberedBlock):
            kind = "bullet" if isinstance(block, BulletsBlock) else "1"
            items: list[Any] = [ListItem(Paragraph(_markup(i, theme), body)) for i in block.items]
            story.append(
                ListFlowable(items, bulletType=kind, leftIndent=16, bulletColor=_hex(c.accent))
            )
        elif isinstance(block, TableBlock):
            data: list[list[Any]] = [
                [Paragraph(_markup(h, theme), cell_head) for h in block.headers]
            ]
            data += [[Paragraph(_markup(x, theme), cell) for x in row] for row in block.rows]
            table = Table(data, hAlign="LEFT", repeatRows=1)
            table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), _hex(c.table_header_bg)),
                        (
                            "ROWBACKGROUNDS",
                            (0, 1),
                            (-1, -1),
                            [_hex(c.page_bg), _hex(c.table_stripe)],
                        ),
                        ("GRID", (0, 0), (-1, -1), 0.5, _hex(c.border)),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("TOPPADDING", (0, 0), (-1, -1), 5),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ]
                )
            )
            story.append(Spacer(1, 2))
            story.append(table)
            if block.caption:
                story.append(Paragraph(_markup(block.caption, theme), small))
            story.append(Spacer(1, 8))
        elif isinstance(block, CalloutBlock):
            fg, bg = getattr(c, f"{block.kind}_fg"), getattr(c, f"{block.kind}_bg")
            box = Table(
                [
                    [
                        Paragraph(
                            _markup(block.text, theme),
                            ParagraphStyle(
                                "callout", parent=body, textColor=_hex(fg), spaceAfter=0
                            ),
                        )
                    ]
                ],
                colWidths=[None],
            )
            box.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), _hex(bg)),
                        ("LINEBEFORE", (0, 0), (0, -1), 3, _hex(fg)),
                        ("LEFTPADDING", (0, 0), (-1, -1), 9),
                        ("TOPPADDING", (0, 0), (-1, -1), 7),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                    ]
                )
            )
            story.extend([KeepTogether(box), Spacer(1, 8)])
        elif isinstance(block, MetricsBlock):
            row = [
                [
                    Paragraph(
                        f"<b>{_markup(m.value, theme)}</b><br/>"
                        f'<font size="{s.small_pt + 0.5}" color="{c.muted}">'
                        f"{_markup(m.label, theme)}</font>",
                        ParagraphStyle(
                            "metric",
                            parent=body,
                            fontSize=s.h1_pt,
                            leading=s.h1_pt * 1.3,
                            textColor=_hex(c.heading),
                            spaceAfter=0,
                        ),
                    )
                    for m in block.items
                ]
            ]
            grid = Table(row, hAlign="LEFT")
            grid.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), _hex(c.surface)),
                        ("BOX", (0, 0), (-1, -1), 0.5, _hex(c.border)),
                        ("INNERGRID", (0, 0), (-1, -1), 0.5, _hex(c.border)),
                        ("TOPPADDING", (0, 0), (-1, -1), 8),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                    ]
                )
            )
            story.extend([grid, Spacer(1, 8)])
        elif isinstance(block, QuoteBlock):
            quote = f"<i>{_markup(block.text, theme)}</i>"
            if block.by:
                quote += (
                    f'<br/><font size="{s.small_pt + 0.5}" color="{c.muted}">'
                    f"— {_markup(block.by, theme)}</font>"
                )
            story.append(
                Paragraph(
                    quote,
                    ParagraphStyle(
                        "quote",
                        parent=body,
                        leftIndent=14,
                        borderPadding=4,
                        textColor=_hex(c.heading),
                        fontSize=s.body_pt + 1,
                    ),
                )
            )
        elif isinstance(block, ImageBlock):
            story.append(_reportlab_image(block, resolver, small))
        elif isinstance(block, CodeBlock):
            story.append(
                Preformatted(
                    block.text,
                    ParagraphStyle(
                        "code",
                        parent=body,
                        fontName="Courier",
                        fontSize=s.code_pt,
                        leading=s.code_pt * 1.4,
                        backColor=_hex(c.code_bg),
                        borderPadding=6,
                        textColor=_hex(c.code_text),
                    ),
                )
            )
            story.append(Spacer(1, 6))
        elif isinstance(block, PageBreakBlock):
            story.append(PageBreak())

    def paint_background(canv: canvas.Canvas, _doc: Any) -> None:
        if theme.dark:
            canv.saveState()
            canv.setFillColor(_hex(c.page_bg))
            canv.rect(0, 0, A4[0], A4[1], stroke=0, fill=1)
            canv.restoreState()

    canvas_cls = type(
        "ThemedCanvas", (_ThemedCanvas,), {"theme": theme, "header_text": document.title}
    )
    template = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=22 * mm,
        bottomMargin=22 * mm,
        title=document.title,
        author=document.author or "MAYA AI",
    )
    template.build(
        story,
        onFirstPage=paint_background,
        onLaterPages=paint_background,
        canvasmaker=canvas_cls,  # type: ignore[arg-type]
    )


def _reportlab_image(
    block: ImageBlock, resolver: ImageResolver | None, caption_style: ParagraphStyle
) -> Any:
    src = (
        block.src if block.src.startswith("data:") else (resolver(block.src) if resolver else None)
    )
    data = decode_data_uri(src) if src else None
    if data is None:
        return Paragraph(f"[Image unavailable: {escape(block.alt)}]", caption_style)
    try:
        image = Image(io.BytesIO(data))
        max_width = A4[0] - 36 * mm
        scale = min(1.0, max_width / image.drawWidth) if image.drawWidth else 1.0
        image.drawWidth *= scale
        image.drawHeight *= scale
    except Exception:
        return Paragraph(f"[Image unavailable: {escape(block.alt)}]", caption_style)
    parts: list[Any] = [image]
    if block.caption:
        parts.append(Paragraph(escape(block.caption), caption_style))
    return KeepTogether(parts)


# --------------------------------------------------------------------------- browser print
def heading_pages(path: Path, entries: list[tuple[int, int, str]]) -> dict[int, int]:
    """Page number (1-based) of each contents heading, read from the PDF's named destinations.

    The HTML gives every heading of a document with a contents list an ``id``; the browser writes
    each one as a named destination, so the page is exact, not guessed from the text. A heading
    whose destination is missing is simply absent from the result.
    """
    reader = PdfReader(str(path))
    destinations = reader.named_destinations
    pages: dict[int, int] = {}
    for index, _level, _text in entries:
        destination = destinations.get(f"/h{index}") or destinations.get(f"h{index}")
        if destination is None:
            continue
        try:
            number = reader.get_destination_page_number(destination)
        except Exception:
            continue
        if number is not None:
            pages[index] = int(number) + 1
    return pages


def _page_count(path: Path) -> int:
    try:
        return len(PdfReader(str(path)).pages)
    except Exception:
        return 0


def _render_in_browser(
    active: BrowserPdfRenderer,
    document: Document,
    resolver: ImageResolver | None,
    path: Path,
    warnings: list[str],
) -> tuple[Any, int, bool | None]:
    """Print ``document``.

    Layout decision: a document with >= 3 level-1 sections is first printed flowing; if that is
    >= 6 pages, it is printed again with every level-1 heading (chapter) on a new page (see
    ``html_render.wants_chapter_breaks``). A short report keeps its flow.

    A contents list is then printed again with its numbers: the previous print learns the page of
    each heading (the page-number column is reserved, so the layout cannot move). The numbered
    print is itself measured and kept only when the numbers it shows equal the real pages of its
    final layout. Otherwise the print without numbers is kept: a wrong number is worse than
    none."""
    timeout = render_timeout_seconds(len(document.blocks))
    default_budget = getattr(active, "timeout_seconds", BASE_RENDER_TIMEOUT_SECONDS)
    extra: dict[str, Any] = {"timeout_seconds": timeout} if timeout > default_budget else {}
    chapters = False
    first = active.render(render_document_html(document, resolve_image=resolver), path, **extra)
    if h1_count(document) >= CHAPTER_MIN_H1 and wants_chapter_breaks(document, _page_count(path)):
        chapters = True
        first = active.render(
            render_document_html(document, resolve_image=resolver, chapter_breaks=True),
            path,
            **extra,
        )
    if not document.toc:
        return first, 1, None
    entries = toc_entries(document)
    measured = heading_pages(path, entries)
    if not entries or len(measured) != len(entries):
        warnings.append(
            "The contents list has no page numbers: the page of some headings could not be measured"
        )
        return first, 1, False
    second_path = path.with_name(path.name + ".numbered.tmp")
    try:
        html = render_document_html(
            document, resolve_image=resolver, toc_pages=measured, chapter_breaks=chapters
        )
        active.render(html, second_path, **extra)
        if heading_pages(second_path, entries) == measured:
            os.replace(second_path, path)
            return first, 2, True
    finally:
        second_path.unlink(missing_ok=True)
    warnings.append(
        "The contents list has no page numbers: they moved between two prints, so none are shown"
    )
    return first, 2, False


# --------------------------------------------------------------------------- public API
def create_pdf_from_document(
    document: Document,
    output_path: Path | str,
    *,
    assets_root: Path | str | None = None,
    renderer: BrowserPdfRenderer | None = None,
) -> PdfRenderResult:
    """Render ``document`` to a PDF.

    Uses the system Edge/Chrome renderer; if none is available or it fails, falls back to
    ReportLab and records ``renderer == "reportlab-fallback"`` plus a warning. Blocking: call it
    from a worker thread (``asyncio.to_thread``) inside an event loop.
    """
    path = Path(output_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    resolver = make_image_resolver(assets_root)
    warnings = _image_warnings(document, resolver)

    try:
        active = renderer if renderer is not None else get_browser_renderer()
        info, passes, numbered = _render_in_browser(active, document, resolver, path, warnings)
        return PdfRenderResult(
            path=path,
            renderer=RENDERER_BROWSER,
            theme=document.theme,
            warnings=warnings,
            browser=f"{info.channel} {info.version}",
            passes=passes,
            toc_numbered=numbered,
        )
    except Exception as err:
        logger.warning("browser_pdf_failed_using_fallback", extra={"error": str(err)[:300]})
        warnings.append(
            f"Browser renderer unavailable or failed ({str(err)[:200]}); "
            "used the ReportLab fallback renderer, so styling is simpler"
        )

    if document.toc or document.cover_page:
        warnings.append(
            "The ReportLab fallback prints no cover page or contents list; install Microsoft "
            "Edge or Google Chrome for the full layout"
        )
    if _has_non_latin(document):
        warnings.append(
            "The ReportLab fallback cannot draw non-Latin scripts (for example Devanagari); "
            "such characters may be missing or wrong. Install Microsoft Edge or Google Chrome."
        )
    _render_reportlab(document, path, resolver)
    return PdfRenderResult(
        path=path, renderer=RENDERER_FALLBACK, theme=document.theme, warnings=warnings
    )


def create_pdf(
    title: str,
    sections: list[dict[str, Any]],
    output_path: Path | str,
    subtitle: str | None = None,
    theme: str = "executive",
) -> Path:
    """Legacy entry point: generate a styled PDF from ``sections`` dicts.

    :param title: Main document title
    :param sections: List of section dicts:
      - type: "heading1" | "heading2" | "heading3" | "paragraph" | "bullet" | "callout" |
        "table" | "page_break"
      - text: Content string; items: list of strings (bullet)
      - headers / rows: table header strings and row lists
    :param output_path: Destination path on disk
    :param subtitle: Optional document subtitle
    :param theme: Theme name (default ``executive``)
    :return: Resolved Path to the generated PDF
    """
    document = document_from_legacy(title, sections, subtitle=subtitle, theme=theme)
    return create_pdf_from_document(document, output_path).path


__all__ = [
    "RENDERER_BROWSER",
    "RENDERER_FALLBACK",
    "PdfRenderResult",
    "create_pdf",
    "create_pdf_from_document",
    "make_image_resolver",
]
