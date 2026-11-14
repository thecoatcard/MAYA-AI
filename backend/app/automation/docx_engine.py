"""DOCX generation: the same :class:`Document` model and theme as the PDF renderer.

Headings use real Word heading styles, lists use Word list styles, tables repeat their header
row, and callouts, quotes and code are shaded paragraphs. Everything is editable text.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from docx import Document as WordDocument
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.image.image import Image as WordImage
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Mm, Pt, RGBColor

from app.automation.assets import decode_data_uri, make_image_resolver
from app.automation.design import fit_theme
from app.automation.html_render import CHAPTER_MIN_H1, CHAPTER_MIN_PAGES, ImageResolver, h1_count
from app.automation.inline import Span, parse_inline, xml_safe
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

RENDERER = "python-docx-native"
MAX_IMAGE_WIDTH_IN = 6.0


@dataclass(frozen=True)
class DocxRenderResult:
    path: Path
    renderer: str
    theme: str
    warnings: list[str] = field(default_factory=list)


def _rgb(value: str) -> RGBColor:
    return RGBColor.from_string(value.lstrip("#").upper())


def _hex(value: str) -> str:
    return value.lstrip("#").upper()


_PPR_ORDER = (
    "w:pStyle",
    "w:keepNext",
    "w:keepLines",
    "w:pageBreakBefore",
    "w:framePr",
    "w:widowControl",
    "w:numPr",
    "w:suppressLineNumbers",
    "w:pBdr",
    "w:shd",
    "w:tabs",
    "w:suppressAutoHyphens",
    "w:kinsoku",
    "w:wordWrap",
    "w:overflowPunct",
    "w:topLinePunct",
    "w:autoSpaceDE",
    "w:autoSpaceDN",
    "w:bidi",
    "w:adjustRightInd",
    "w:snapToGrid",
    "w:spacing",
    "w:ind",
    "w:contextualSpacing",
    "w:mirrorIndents",
    "w:suppressOverlap",
    "w:jc",
    "w:textDirection",
    "w:textAlignment",
    "w:textboxTightWrap",
    "w:outlineLvl",
    "w:divId",
    "w:cnfStyle",
    "w:rPr",
    "w:sectPr",
    "w:pPrChange",
)


def _insert_ordered(p_pr: Any, element: Any, tag: str) -> None:
    """Insert a paragraph-property child at its schema position (Word rejects bad order)."""
    p_pr.insert_element_before(element, *_PPR_ORDER[_PPR_ORDER.index(tag) + 1 :])


def _shade(element_pr: Any, fill: str) -> None:
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), _hex(fill))
    if element_pr.tag == qn("w:pPr"):
        _insert_ordered(element_pr, shd, "w:shd")
    else:
        element_pr.append(shd)


def _paragraph_border(paragraph: Any, color: str, side: str = "left", size: int = 24) -> None:
    pPr = paragraph._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    edge = OxmlElement(f"w:{side}")
    edge.set(qn("w:val"), "single")
    edge.set(qn("w:sz"), str(size))
    edge.set(qn("w:space"), "6")
    edge.set(qn("w:color"), _hex(color))
    borders.append(edge)
    _insert_ordered(pPr, borders, "w:pBdr")


def _set_rfonts(rPr: Any, name: str, complex_font: str) -> None:
    fonts = rPr.get_or_add_rFonts()
    for attr in ("asciiTheme", "hAnsiTheme", "eastAsiaTheme", "cstheme"):
        key = qn(f"w:{attr}")
        if key in fonts.attrib:
            del fonts.attrib[key]
    for attr in ("ascii", "hAnsi", "eastAsia"):
        fonts.set(qn(f"w:{attr}"), name)
    fonts.set(qn("w:cs"), complex_font)


_WORDS_PER_PAGE = 450
_MAX_KEEP_CODE_LINES = 12


def _approx_words(document: Document) -> int:
    """Rough word count of the printed text (a page is about 450 words)."""
    total = 0
    for block in document.blocks:
        if isinstance(block, HeadingBlock | ParagraphBlock | CalloutBlock | QuoteBlock):
            total += len(block.text.split())
        elif isinstance(block, BulletsBlock | NumberedBlock):
            total += sum(len(i.split()) for i in block.items)
        elif isinstance(block, CodeBlock):
            total += len(block.text.split())
        elif isinstance(block, TableBlock):
            total += sum(len(c.split()) for row in block.rows for c in row)
    return total


CALLOUT_LABELS = {"info": "Note", "success": "Tip", "warning": "Warning"}


class _DocxBuilder:
    def __init__(self, document: Document, resolver: ImageResolver | None) -> None:
        self.model = document
        self.theme: Theme = fit_theme(
            themed(document.theme, document.accent_color, document.design), document.fit_step
        )
        self.resolver = resolver
        self.warnings: list[str] = []
        self.doc = WordDocument()
        self._last_heading = 0
        self._heading_adjusted = False
        # Chapter rule (same as the PDF): >= 3 level-1 headings and about 6 pages of text start
        # each level-1 heading on a new page. DOCX has no fixed pages, so length is estimated.
        self._chapter_breaks = h1_count(document) >= CHAPTER_MIN_H1 and (
            _approx_words(document) >= CHAPTER_MIN_PAGES * _WORDS_PER_PAGE
        )
        self._page_top = False  # a page break was just written, so a heading is already at the top

    # ------------------------------------------------------------------ setup
    def _style_font(
        self, style: Any, name: str, size: float, color: str, bold: bool | None
    ) -> None:
        style.font.size = Pt(size)
        style.font.color.rgb = _rgb(color)
        if bold is not None:
            style.font.bold = bold
        _set_rfonts(style.element.get_or_add_rPr(), name, self.theme.fonts.complex_script_font)

    def _setup(self) -> None:
        theme, c, f, s = self.theme, self.theme.colors, self.theme.fonts, self.theme.sizes
        doc = self.doc
        section = doc.sections[0]
        section.page_width, section.page_height = Mm(210), Mm(297)
        for side in ("top_margin", "bottom_margin", "left_margin", "right_margin"):
            setattr(section, side, Mm(s.page_margin_mm))

        styles = doc.styles
        self._style_font(styles["Normal"], f.body_font, s.body_pt + 0.5, c.text, None)
        styles["Normal"].paragraph_format.space_after = Pt(s.block_gap_pt * 0.7)
        styles["Normal"].paragraph_format.line_spacing = s.line_height - 0.2
        for name, size, color in (
            ("Heading 1", s.h1_pt, c.heading),
            ("Heading 2", s.h2_pt, c.heading),
            ("Heading 3", s.h3_pt, c.primary),
        ):
            self._style_font(styles[name], f.heading_font, size, color, True)
            styles[name].paragraph_format.space_before = Pt(s.block_gap_pt * 1.6)
            styles[name].paragraph_format.space_after = Pt(s.block_gap_pt * 0.5)
            styles[name].paragraph_format.keep_with_next = True
            styles[name].paragraph_format.keep_together = True
            styles[name].paragraph_format.widow_control = True
        styles["Normal"].paragraph_format.widow_control = True
        self._style_font(styles["Title"], f.heading_font, s.title_pt, c.heading, True)
        for list_style in ("List Bullet", "List Number"):
            self._style_font(styles[list_style], f.body_font, s.body_pt + 0.5, c.text, None)

        if theme.dark:
            background = OxmlElement("w:background")
            background.set(qn("w:color"), _hex(c.page_bg))
            doc.element.insert(0, background)
            settings = doc.settings.element
            flag = OxmlElement("w:displayBackgroundShape")
            zoom = settings.find(qn("w:zoom"))
            if zoom is not None:
                zoom.addnext(flag)
            else:
                settings.insert(0, flag)

        header = section.header.paragraphs[0]
        self._run(header, self.model.title[:90], size=s.small_pt, color=c.muted)
        footer = section.footer.paragraphs[0]
        brand = " ".join((self.model.author or "MAYA AI").split())[:40] or "MAYA AI"
        self._run(footer, f"{brand}    Page ", size=s.small_pt, color=c.muted)
        self._field(footer, "PAGE", s.small_pt, c.muted)
        self._run(footer, " of ", size=s.small_pt, color=c.muted)
        self._field(footer, "NUMPAGES", s.small_pt, c.muted)

        props = doc.core_properties
        props.title = self.model.title
        props.author = self.model.author or "MAYA AI"
        props.comments = f"theme={self.model.theme}; renderer={RENDERER}"

    def _field(self, paragraph: Any, instruction: str, size: float, color: str) -> None:
        fld = OxmlElement("w:fldSimple")
        fld.set(qn("w:instr"), instruction)
        run = paragraph.add_run("1")
        run.font.size = Pt(size)
        run.font.color.rgb = _rgb(color)
        fld.append(run._r)
        paragraph._p.append(fld)

    # ------------------------------------------------------------------ runs
    def _run(
        self,
        paragraph: Any,
        text: str,
        *,
        size: float | None = None,
        color: str | None = None,
        bold: bool = False,
        italic: bool = False,
        font: str | None = None,
    ) -> Any:
        run = paragraph.add_run(xml_safe(text))
        if font or size or color:
            _set_rfonts(
                run._r.get_or_add_rPr(),
                font or self.theme.fonts.body_font,
                self.theme.fonts.complex_script_font,
            )
        if size:
            run.font.size = Pt(size)
        if color:
            run.font.color.rgb = _rgb(color)
        if bold:
            run.font.bold = True
        if italic:
            run.font.italic = True
        return run

    def _hyperlink(self, paragraph: Any, span: Span, size: float | None) -> None:
        assert span.href is not None
        rel_id = paragraph.part.relate_to(span.href, RELATIONSHIP_TYPE.HYPERLINK, is_external=True)
        link = OxmlElement("w:hyperlink")
        link.set(qn("r:id"), rel_id)
        run = paragraph.add_run(xml_safe(span.text))
        run.font.color.rgb = _rgb(self.theme.colors.link)
        run.font.underline = True
        run.font.bold = span.bold or None
        run.font.italic = span.italic or None
        if size:
            run.font.size = Pt(size)
        link.append(run._r)
        paragraph._p.append(link)

    def _spans(
        self,
        paragraph: Any,
        text: str,
        *,
        size: float | None = None,
        color: str | None = None,
        bold: bool = False,
        italic: bool = False,
        font: str | None = None,
    ) -> None:
        theme = self.theme
        for span in parse_inline(text):
            if span.href:
                self._hyperlink(paragraph, span, size)
                continue
            pieces = span.text.split("\n")
            for number, piece in enumerate(pieces):
                if number:
                    paragraph.add_run().add_break()
                if piece:
                    self._run(
                        paragraph,
                        piece,
                        size=size,
                        color=color,
                        bold=bold or span.bold,
                        italic=italic or span.italic,
                        font=theme.fonts.mono_font if span.code else font,
                    )

    # ------------------------------------------------------------------ blocks
    def build(self) -> None:
        self._setup()
        self._title_block()
        if self.model.cover_page:
            self.doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
            self._page_top = True
        if self.model.toc:
            self._contents()
        blocks = list(self.model.blocks)
        while blocks and isinstance(blocks[-1], PageBreakBlock):
            blocks.pop()
        previous_break = True
        for block in blocks:
            is_break = isinstance(block, PageBreakBlock)
            if is_break and previous_break:
                continue
            previous_break = is_break
            self._block(block)
            self._page_top = is_break
        if self._heading_adjusted:
            self.warnings.append(
                "Heading levels were adjusted so each heading follows its parent level"
            )

    def _contents(self) -> None:
        """A contents list (chapter and section headings) on its own page. DOCX has no fixed
        pages, so it lists the headings without page numbers (Word can build a live table of
        contents from the heading styles)."""
        c, f, s = self.theme.colors, self.theme.fonts, self.theme.sizes
        title = self.doc.add_paragraph()
        self._run(title, "Contents", size=s.h1_pt, color=c.heading, bold=True, font=f.heading_font)
        for block in self.model.blocks:
            if isinstance(block, HeadingBlock) and block.level <= 2:
                entry = self.doc.add_paragraph()
                entry.paragraph_format.left_indent = Inches(0.25 * (block.level - 1))
                entry.paragraph_format.space_after = Pt(3)
                self._run(
                    entry,
                    block.text,
                    size=s.body_pt + 0.5,
                    color=c.heading if block.level == 1 else c.text,
                    bold=block.level == 1,
                )
        self.doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
        self._page_top = True

    def _title_block(self) -> None:
        c, f, s = self.theme.colors, self.theme.fonts, self.theme.sizes
        brand = self.doc.add_paragraph()
        brand.paragraph_format.space_after = Pt(4)
        self._run(brand, "MAYA AI", size=s.small_pt + 1, color=c.accent, bold=True)
        title = self.doc.add_paragraph(style="Title")
        title.paragraph_format.space_after = Pt(4)
        self._run(
            title,
            self.model.title,
            size=s.title_pt,
            color=c.heading,
            bold=True,
            font=f.heading_font,
        )
        if self.model.subtitle:
            sub = self.doc.add_paragraph()
            sub.paragraph_format.space_after = Pt(4)
            self._spans(sub, self.model.subtitle, size=s.subtitle_pt, color=c.muted)
        meta = " · ".join(p for p in (self.model.author, self.model.date) if p)
        last = self.doc.add_paragraph()
        last.paragraph_format.space_after = Pt(14)
        if meta:
            self._run(last, meta, size=s.small_pt + 1, color=c.muted)
        _paragraph_border(last, c.accent, side="bottom", size=18)

    def _block(self, block: Any) -> None:
        c, s = self.theme.colors, self.theme.sizes
        doc = self.doc
        if isinstance(block, HeadingBlock):
            level = min(block.level, self._last_heading + 1)
            if level != block.level:
                self._heading_adjusted = True
            self._last_heading = level
            heading = doc.add_paragraph(style=f"Heading {level}")
            if level == 1 and self._chapter_breaks and not self._page_top:
                heading.paragraph_format.page_break_before = True
            self._spans(heading, block.text)
        elif isinstance(block, ParagraphBlock):
            self._spans(doc.add_paragraph(), block.text)
        elif isinstance(block, BulletsBlock):
            for item in block.items:
                paragraph = doc.add_paragraph(style="List Bullet")
                paragraph.paragraph_format.space_after = Pt(3)
                self._spans(paragraph, item)
        elif isinstance(block, NumberedBlock):
            for number, item in enumerate(block.items, start=1):
                paragraph = doc.add_paragraph()
                paragraph.paragraph_format.left_indent = Inches(0.35)
                paragraph.paragraph_format.first_line_indent = Inches(-0.25)
                paragraph.paragraph_format.space_after = Pt(3)
                self._run(paragraph, f"{number}.  ", color=c.accent, bold=True)
                self._spans(paragraph, item)
        elif isinstance(block, TableBlock):
            self._table(block)
        elif isinstance(block, CalloutBlock):
            fg, bg = getattr(c, f"{block.kind}_fg"), getattr(c, f"{block.kind}_bg")
            paragraph = doc.add_paragraph()
            paragraph.paragraph_format.left_indent = Inches(0.15)
            paragraph.paragraph_format.right_indent = Inches(0.1)
            paragraph.paragraph_format.keep_together = True
            self._run(
                paragraph,
                f"{CALLOUT_LABELS.get(block.kind, block.kind).upper()}  ",
                size=s.small_pt,
                color=fg,
                bold=True,
            )
            self._spans(paragraph, block.text, color=fg)
            _shade(paragraph._p.get_or_add_pPr(), bg)
            _paragraph_border(paragraph, fg)
        elif isinstance(block, MetricsBlock):
            self._metrics(block)
        elif isinstance(block, QuoteBlock):
            paragraph = doc.add_paragraph()
            paragraph.paragraph_format.left_indent = Inches(0.3)
            self._spans(
                paragraph,
                block.text,
                size=s.body_pt + 1.5,
                color=c.heading,
                italic=True,
                font=self.theme.fonts.heading_font,
            )
            if block.by:
                paragraph.add_run().add_break()
                self._run(paragraph, f"— {block.by}", size=s.small_pt + 1, color=c.muted)
            _paragraph_border(paragraph, c.accent)
        elif isinstance(block, ImageBlock):
            self._image(block)
        elif isinstance(block, CodeBlock):
            paragraph = doc.add_paragraph()
            lines = block.text.split("\n")
            # A long listing may split across pages; a short one stays whole.
            paragraph.paragraph_format.keep_together = len(lines) <= _MAX_KEEP_CODE_LINES
            paragraph.paragraph_format.widow_control = True
            for number, line in enumerate(lines):
                if number:
                    paragraph.add_run().add_break()
                self._run(
                    paragraph,
                    line,
                    size=s.code_pt,
                    color=c.code_text,
                    font=self.theme.fonts.mono_font,
                )
            _shade(paragraph._p.get_or_add_pPr(), c.code_bg)
        elif isinstance(block, PageBreakBlock):
            doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
        else:  # pragma: no cover - the model union is closed
            raise TypeError(f"Unsupported block type: {type(block).__name__}")

    def _table_borders(self, table: Any) -> None:
        tbl_pr = table._tbl.tblPr
        borders = OxmlElement("w:tblBorders")
        for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
            node = OxmlElement(f"w:{edge}")
            node.set(qn("w:val"), "single")
            node.set(qn("w:sz"), "4")
            node.set(qn("w:space"), "0")
            node.set(qn("w:color"), _hex(self.theme.colors.border))
            borders.append(node)
        tbl_pr.insert_element_before(
            borders,
            "w:shd",
            "w:tblLayout",
            "w:tblCellMar",
            "w:tblLook",
            "w:tblCaption",
            "w:tblDescription",
        )

    def _table(self, block: TableBlock) -> None:
        c, s = self.theme.colors, self.theme.sizes
        table = self.doc.add_table(rows=0, cols=len(block.headers))
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.style = "Table Grid"
        self._table_borders(table)
        header = table.add_row()
        tr_pr = header._tr.get_or_add_trPr()
        tr_pr.append(OxmlElement("w:tblHeader"))
        for index, text in enumerate(block.headers):
            cell = header.cells[index]
            _shade(cell._tc.get_or_add_tcPr(), c.table_header_bg)
            self._spans(
                cell.paragraphs[0], text, size=s.body_pt, color=c.table_header_text, bold=True
            )
        for row_number, values in enumerate(block.rows, start=1):
            row = table.add_row()
            row._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
            for index, text in enumerate(values):
                cell = row.cells[index]
                if row_number % 2 == 0:
                    _shade(cell._tc.get_or_add_tcPr(), c.table_stripe)
                self._spans(cell.paragraphs[0], text, size=s.body_pt, color=c.text)
        if block.caption:
            caption = self.doc.add_paragraph()
            self._spans(caption, block.caption, size=s.small_pt, color=c.muted, italic=True)
        else:
            self.doc.add_paragraph().paragraph_format.space_after = Pt(2)

    def _metrics(self, block: MetricsBlock) -> None:
        c, s = self.theme.colors, self.theme.sizes
        table = self.doc.add_table(rows=1, cols=len(block.items))
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        self._table_borders(table)
        for index, metric in enumerate(block.items):
            cell = table.rows[0].cells[index]
            _shade(cell._tc.get_or_add_tcPr(), c.surface)
            value = cell.paragraphs[0]
            value.alignment = WD_ALIGN_PARAGRAPH.CENTER
            self._spans(
                value,
                metric.value,
                size=s.h1_pt + 3,
                color=c.heading,
                bold=True,
                font=self.theme.fonts.heading_font,
            )
            label = cell.add_paragraph()
            label.alignment = WD_ALIGN_PARAGRAPH.CENTER
            self._spans(label, metric.label, size=s.small_pt + 1, color=c.muted)
        self.doc.add_paragraph().paragraph_format.space_after = Pt(2)

    def _image(self, block: ImageBlock) -> None:
        src = (
            block.src
            if block.src.startswith("data:")
            else (self.resolver(block.src) if self.resolver else None)
        )
        blob = decode_data_uri(src) if src else None
        paragraph = self.doc.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        placed = False
        if blob is not None:
            try:
                native_in = float(WordImage.from_blob(blob).width.inches)
                shape = paragraph.add_run().add_picture(
                    io.BytesIO(blob), width=Inches(min(MAX_IMAGE_WIDTH_IN, native_in))
                )
                shape._inline.docPr.set("descr", xml_safe(block.alt))
                placed = True
            except Exception:
                logger.warning("docx_image_failed", exc_info=True)
        if not placed:
            self.warnings.append(
                f"Image '{block.src[:60]}' could not be loaded; a placeholder was used"
            )
            self._run(
                paragraph,
                f"[Image unavailable: {block.alt}]",
                color=self.theme.colors.muted,
                italic=True,
            )
        if block.caption:
            caption = self.doc.add_paragraph()
            caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
            self._spans(
                caption,
                block.caption,
                size=self.theme.sizes.small_pt,
                color=self.theme.colors.muted,
                italic=True,
            )


def create_document_from_model(
    document: Document,
    output_path: Path | str,
    *,
    assets_root: Path | str | None = None,
) -> DocxRenderResult:
    """Render ``document`` to a .docx with the document's theme."""
    path = Path(output_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    builder = _DocxBuilder(document, make_image_resolver(assets_root))
    builder.build()
    builder.doc.save(str(path))
    return DocxRenderResult(
        path=path, renderer=RENDERER, theme=document.theme, warnings=builder.warnings
    )


def create_document(
    sections: list[dict[str, Any]],
    output_path: Path | str,
    title: str = "MAYA AI Generated Report",
    subtitle: str | None = None,
    theme: str = "executive",
) -> Path:
    """Legacy entry point: generate a styled Microsoft Word document (.docx).

    :param sections: List of section dicts:
      - type: "heading1" | "heading2" | "heading3" | "paragraph" | "bullet" | "table" | "callout"
      - text: Content string for headings/paragraphs
      - items: List of strings for bullets
      - headers: List of column header strings (for table)
      - rows: List of list of cell strings (for table)
    :param output_path: Destination path on disk.
    :param title: Main document title.
    :param subtitle: Optional document subtitle.
    :param theme: Theme name (default ``executive``).
    :return: Resolved Path to generated .docx file.
    """
    document = document_from_legacy(title, sections, subtitle=subtitle, theme=theme)
    return create_document_from_model(document, output_path).path


__all__ = [
    "RENDERER",
    "DocxRenderResult",
    "create_document",
    "create_document_from_model",
]
