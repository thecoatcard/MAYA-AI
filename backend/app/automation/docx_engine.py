"""DOCX Document Generation Engine."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor


def _set_cell_background(cell: Any, fill_hex: str) -> None:
    """Set background color of a table cell."""
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill_hex)
    tc_pr.append(shd)


def create_document(
    sections: list[dict[str, Any]],
    output_path: Path | str,
    title: str = "MAYA AI Generated Report",
    subtitle: str | None = None,
) -> Path:
    """Generate a styled Microsoft Word document (.docx).

    :param sections: List of section dicts:
      - type: "heading1" | "heading2" | "heading3" | "paragraph" | "bullet" | "table" | "callout"
      - text: Content string for headings/paragraphs
      - items: List of strings for bullets
      - headers: List of column header strings (for table)
      - rows: List of list of cell strings (for table)
    :param output_path: Destination path on disk.
    :param title: Main document title.
    :param subtitle: Optional document subtitle.
    :return: Resolved Path to generated .docx file.
    """
    path = Path(output_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    doc = Document()

    # Set page margins (1 inch)
    for section in doc.sections:
        section.top_margin = Inches(1.0)
        section.bottom_margin = Inches(1.0)
        section.left_margin = Inches(1.0)
        section.right_margin = Inches(1.0)

    # Document Title Block
    title_p = doc.add_paragraph()
    title_p.paragraph_format.space_before = Pt(0)
    title_p.paragraph_format.space_after = Pt(4)
    title_run = title_p.add_run(title)
    title_run.font.name = "Calibri"
    title_run.font.size = Pt(24)
    title_run.font.bold = True
    title_run.font.color.rgb = RGBColor(0x1F, 0x4E, 0x79)

    if subtitle:
        sub_p = doc.add_paragraph()
        sub_p.paragraph_format.space_after = Pt(18)
        sub_run = sub_p.add_run(subtitle)
        sub_run.font.name = "Calibri"
        sub_run.font.size = Pt(13)
        sub_run.font.color.rgb = RGBColor(0x59, 0x59, 0x59)

    for item in sections:
        item_type = item.get("type", "paragraph")

        if item_type == "heading1":
            h1 = doc.add_heading(item.get("text", ""), level=1)
            h1.paragraph_format.space_before = Pt(14)
            h1.paragraph_format.space_after = Pt(6)
            for run in h1.runs:
                run.font.name = "Calibri"
                run.font.color.rgb = RGBColor(0x1F, 0x4E, 0x79)

        elif item_type == "heading2":
            h2 = doc.add_heading(item.get("text", ""), level=2)
            h2.paragraph_format.space_before = Pt(10)
            h2.paragraph_format.space_after = Pt(4)
            for run in h2.runs:
                run.font.name = "Calibri"
                run.font.color.rgb = RGBColor(0x2E, 0x75, 0xB6)

        elif item_type == "heading3":
            h3 = doc.add_heading(item.get("text", ""), level=3)
            h3.paragraph_format.space_before = Pt(8)
            h3.paragraph_format.space_after = Pt(2)
            for run in h3.runs:
                run.font.name = "Calibri"
                run.font.color.rgb = RGBColor(0x1F, 0x38, 0x64)

        elif item_type == "paragraph":
            p = doc.add_paragraph()
            p.paragraph_format.space_after = Pt(6)
            p.paragraph_format.line_spacing = 1.15
            run = p.add_run(item.get("text", ""))
            run.font.name = "Calibri"
            run.font.size = Pt(11)
            run.font.color.rgb = RGBColor(0x26, 0x26, 0x26)

        elif item_type == "bullet":
            for b_text in item.get("items", []):
                bp = doc.add_paragraph(style="List Bullet")
                bp.paragraph_format.space_after = Pt(3)
                brun = bp.add_run(b_text)
                brun.font.name = "Calibri"
                brun.font.size = Pt(10.5)

        elif item_type == "callout":
            cp = doc.add_paragraph()
            cp.paragraph_format.left_indent = Inches(0.4)
            cp.paragraph_format.right_indent = Inches(0.4)
            cp.paragraph_format.space_before = Pt(6)
            cp.paragraph_format.space_after = Pt(6)
            crun = cp.add_run(item.get("text", ""))
            crun.font.name = "Calibri"
            crun.font.italic = True
            crun.font.size = Pt(10.5)
            crun.font.color.rgb = RGBColor(0x40, 0x40, 0x40)

        elif item_type == "table":
            headers = item.get("headers", [])
            rows = item.get("rows", [])
            col_count = len(headers) if headers else (len(rows[0]) if rows else 0)

            if col_count > 0:
                table = doc.add_table(rows=0, cols=col_count)
                table.alignment = WD_TABLE_ALIGNMENT.CENTER
                table.style = "Table Grid"

                # Header row
                if headers:
                    hdr_row = table.add_row()
                    for idx, h_text in enumerate(headers):
                        cell = hdr_row.cells[idx]
                        cell.text = str(h_text)
                        _set_cell_background(cell, "1F4E79")
                        for p in cell.paragraphs:
                            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                            for r in p.runs:
                                r.font.name = "Calibri"
                                r.font.bold = True
                                r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)

                # Data rows
                for row_data in rows:
                    row = table.add_row()
                    for idx, val in enumerate(row_data):
                        cell = row.cells[idx]
                        cell.text = str(val)
                        for p in cell.paragraphs:
                            for r in p.runs:
                                r.font.name = "Calibri"
                                r.font.size = Pt(10)

                doc.add_paragraph().paragraph_format.space_after = Pt(6)

    doc.save(str(path))
    return path
