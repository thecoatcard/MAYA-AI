"""PPTX Presentation Generation Engine."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt

# Standard 16:9 Widescreen dimensions
SLIDE_WIDTH_INCHES = 13.333
SLIDE_HEIGHT_INCHES = 7.5


def create_presentation(
    slides_data: list[dict[str, Any]],
    output_path: Path | str,
) -> Path:
    """Generate a 16:9 widescreen PowerPoint presentation (.pptx).

    :param slides_data: List of slide dicts:
      - layout: "title" | "cards" | "bullets" | "metrics"
      - title: Slide header string
      - subtitle: Optional subtitle string
      - bullets: List of strings (for bullets layout)
      - cards: List of dicts with 'title' and 'body' (for cards layout)
      - metrics: List of dicts with 'value', 'label' (for metrics layout)
    :param output_path: Destination path on disk.
    :return: Resolved Path to generated .pptx file.
    """
    path = Path(output_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    prs = Presentation()
    prs.slide_width = Inches(SLIDE_WIDTH_INCHES)
    prs.slide_height = Inches(SLIDE_HEIGHT_INCHES)
    blank_layout = prs.slide_layouts[6]  # Blank slide

    for _s_idx, s_data in enumerate(slides_data):
        slide = prs.slides.add_slide(blank_layout)
        layout_type = s_data.get("layout", "bullets")

        # Slide Title Banner
        title_text = s_data.get("title", "")
        if layout_type == "title":
            # Big Center Title Slide
            tb = slide.shapes.add_textbox(Inches(1.5), Inches(2.2), Inches(10.3), Inches(3.0))
            tf = tb.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            p.text = title_text
            p.font.name = "Calibri"
            p.font.size = Pt(44)
            p.font.bold = True
            p.font.color.rgb = RGBColor(0x1F, 0x4E, 0x79)
            p.alignment = PP_ALIGN.CENTER

            subtitle_text = s_data.get("subtitle", "")
            if subtitle_text:
                p2 = tf.add_paragraph()
                p2.text = subtitle_text
                p2.font.name = "Calibri"
                p2.font.size = Pt(20)
                p2.font.color.rgb = RGBColor(0x59, 0x59, 0x59)
                p2.space_before = Pt(16)
                p2.alignment = PP_ALIGN.CENTER
            continue

        # Standard Content Slide Title
        tb_title = slide.shapes.add_textbox(Inches(0.8), Inches(0.6), Inches(11.7), Inches(1.0))
        tf_title = tb_title.text_frame
        tf_title.word_wrap = True
        pt = tf_title.paragraphs[0]
        pt.text = title_text
        pt.font.name = "Calibri"
        pt.font.size = Pt(28)
        pt.font.bold = True
        pt.font.color.rgb = RGBColor(0x1F, 0x4E, 0x79)

        if layout_type == "cards":
            cards = s_data.get("cards", [])
            card_count = max(1, min(len(cards), 3))
            gap = 0.4
            total_gap = gap * (card_count - 1)
            card_width = (11.7 - total_gap) / card_count
            top = 2.0
            height = 4.5

            for c_idx, c_info in enumerate(cards[:card_count]):
                left = 0.8 + c_idx * (card_width + gap)
                shape = slide.shapes.add_shape(
                    MSO_SHAPE.ROUNDED_RECTANGLE,
                    Inches(left),
                    Inches(top),
                    Inches(card_width),
                    Inches(height),
                )
                shape.fill.solid()
                shape.fill.fore_color.rgb = RGBColor(0xF2, 0xF4, 0xF8)
                shape.line.color.rgb = RGBColor(0xD9, 0xE1, 0xF2)

                tf_card = shape.text_frame
                tf_card.word_wrap = True
                p_c = tf_card.paragraphs[0]
                p_c.text = c_info.get("title", "")
                p_c.font.name = "Calibri"
                p_c.font.size = Pt(18)
                p_c.font.bold = True
                p_c.font.color.rgb = RGBColor(0x1F, 0x4E, 0x79)
                p_c.space_after = Pt(10)

                p_body = tf_card.add_paragraph()
                p_body.text = c_info.get("body", "")
                p_body.font.name = "Calibri"
                p_body.font.size = Pt(13)
                p_body.font.color.rgb = RGBColor(0x33, 0x33, 0x33)

        elif layout_type == "metrics":
            metrics = s_data.get("metrics", [])
            m_count = max(1, min(len(metrics), 4))
            gap = 0.5
            total_gap = gap * (m_count - 1)
            m_width = (11.7 - total_gap) / m_count

            for m_idx, m_info in enumerate(metrics[:m_count]):
                left = 0.8 + m_idx * (m_width + gap)
                tb_m = slide.shapes.add_textbox(
                    Inches(left), Inches(2.5), Inches(m_width), Inches(3.0)
                )
                tf_m = tb_m.text_frame
                tf_m.word_wrap = True

                pv = tf_m.paragraphs[0]
                pv.text = str(m_info.get("value", ""))
                pv.font.name = "Calibri"
                pv.font.size = Pt(40)
                pv.font.bold = True
                pv.font.color.rgb = RGBColor(0x1F, 0x4E, 0x79)
                pv.alignment = PP_ALIGN.CENTER

                pl = tf_m.add_paragraph()
                pl.text = str(m_info.get("label", ""))
                pl.font.name = "Calibri"
                pl.font.size = Pt(14)
                pl.font.color.rgb = RGBColor(0x59, 0x59, 0x59)
                pl.alignment = PP_ALIGN.CENTER
                pl.space_before = Pt(8)

        else:  # bullets layout
            bullets = s_data.get("bullets", [])
            tb_b = slide.shapes.add_textbox(Inches(1.0), Inches(2.0), Inches(11.3), Inches(4.8))
            tf_b = tb_b.text_frame
            tf_b.word_wrap = True

            for b_idx, b_text in enumerate(bullets):
                p_b = tf_b.paragraphs[0] if b_idx == 0 else tf_b.add_paragraph()
                p_b.text = f"•  {b_text}"
                p_b.font.name = "Calibri"
                p_b.font.size = Pt(16)
                p_b.font.color.rgb = RGBColor(0x26, 0x26, 0x26)
                p_b.space_after = Pt(12)

    prs.save(str(path))
    return path
