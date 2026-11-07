"""PDF Document Generation Engine using ReportLab."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    HRFlowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


class NumberedCanvas(canvas.Canvas):
    """Canvas that computes two-pass total page numbers (e.g. 'Page 1 of 3')."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._saved_page_states: list[dict[str, Any]] = []

    def showPage(self) -> None:
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()  # type: ignore[attr-defined]

    def save(self) -> None:
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, total_pages: int) -> None:
        self.saveState()
        self.setFont("Helvetica", 9)
        self.setFillColor(colors.HexColor("#7F7F7F"))

        # Running header (pages 2+)
        if getattr(self, "_pageNumber", 1) > 1:
            self.drawString(54, 750, "MAYA AI — Research & Analysis Deliverable")
            self.setStrokeColor(colors.HexColor("#D9D9D9"))
            self.setLineWidth(0.5)
            self.line(54, 742, 558, 742)

        # Running footer
        page_num = getattr(self, "_pageNumber", 1)
        page_text = f"Page {page_num} of {total_pages}"
        self.drawRightString(558, 40, page_text)
        self.drawString(54, 40, "CONFIDENTIAL & PROPRIETARY")
        self.setStrokeColor(colors.HexColor("#D9D9D9"))
        self.setLineWidth(0.5)
        self.line(54, 52, 558, 52)
        self.restoreState()


def create_pdf(
    title: str,
    sections: list[dict[str, Any]],
    output_path: Path | str,
    subtitle: str | None = None,
) -> Path:
    """Generate a multi-page styled PDF document.

    :param title: Main document title
    :param sections: List of section dicts:
      - type: "heading1" | "heading2" | "paragraph" | "table" | "page_break"
      - text: Content string
      - headers: List of column header strings (for table)
      - rows: List of list of cell strings (for table)
    :param output_path: Destination path on disk
    :param subtitle: Optional document subtitle
    :return: Resolved Path to generated PDF
    """
    path = Path(output_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(path),
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.85 * inch,
        bottomMargin=0.85 * inch,
    )

    styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=24,
        leading=28,
        textColor=colors.HexColor("#1F4E79"),
        alignment=0,
        spaceAfter=6,
    )

    subtitle_style = ParagraphStyle(
        "DocSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=12,
        leading=16,
        textColor=colors.HexColor("#595959"),
        spaceAfter=14,
    )

    h1_style = ParagraphStyle(
        "DocH1",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=16,
        leading=20,
        textColor=colors.HexColor("#1F4E79"),
        spaceBefore=14,
        spaceAfter=6,
        keepWithNext=True,
    )

    h2_style = ParagraphStyle(
        "DocH2",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=17,
        textColor=colors.HexColor("#2E75B6"),
        spaceBefore=10,
        spaceAfter=4,
        keepWithNext=True,
    )

    body_style = ParagraphStyle(
        "DocBody",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#262626"),
        spaceAfter=6,
    )

    story: list[Any] = []

    # Title block
    story.append(Paragraph(title, title_style))
    if subtitle:
        story.append(Paragraph(subtitle, subtitle_style))
    story.append(
        HRFlowable(
            width="100%",
            thickness=1.5,
            color=colors.HexColor("#1F4E79"),
            spaceBefore=0,
            spaceAfter=14,
        )
    )

    for item in sections:
        itype = item.get("type", "paragraph")

        if itype == "heading1":
            story.append(Paragraph(item.get("text", ""), h1_style))
        elif itype == "heading2":
            story.append(Paragraph(item.get("text", ""), h2_style))
        elif itype == "paragraph":
            story.append(Paragraph(item.get("text", ""), body_style))
        elif itype == "page_break":
            story.append(PageBreak())
        elif itype == "table":
            headers = item.get("headers", [])
            rows = item.get("rows", [])
            table_data = []
            if headers:
                table_data.append([Paragraph(f"<b>{h}</b>", body_style) for h in headers])
            for row in rows:
                table_data.append([Paragraph(str(c), body_style) for c in row])

            if table_data:
                t = Table(table_data, hAlign="LEFT")
                t.setStyle(
                    TableStyle(
                        [
                            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F4F8")),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                            ("TOPPADDING", (0, 0), (-1, -1), 5),
                            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D9D9D9")),
                        ]
                    )
                )
                story.append(Spacer(1, 4))
                story.append(t)
                story.append(Spacer(1, 8))

    doc.build(story, canvasmaker=NumberedCanvas)
    return path
