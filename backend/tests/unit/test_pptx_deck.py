"""Native themed PPTX decks: every layout, notes, fit rules, legacy API, verifier proof."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.util import Inches

from app.automation import model
from app.automation.model import Deck
from app.automation.pptx_engine import (
    RENDERER,
    SlideFitError,
    create_deck,
    create_presentation,
    deck_from_legacy_slides,
)
from app.automation.themes import THEME_NAMES, get_theme
from app.automation.verifiers.pptx_verifier import verify_pptx
from tests.support.documents import HINGLISH, sample_deck


def _texts(slide: Any) -> list[str]:
    out: list[str] = []
    for shape in slide.shapes:
        if shape.has_text_frame and shape.text_frame.text.strip():
            out.append(shape.text_frame.text)
        if getattr(shape, "has_table", False) and shape.has_table:
            for row in shape.table.rows:
                out.extend(cell.text for cell in row.cells)
    return out


@pytest.mark.parametrize("theme", THEME_NAMES)
def test_deck_with_every_layout_renders_and_verifies(tmp_path: Path, theme: str) -> None:
    deck = sample_deck(theme)
    assert {s.layout for s in deck.slides} == set(model.SLIDE_LAYOUTS)

    result = create_deck(deck, tmp_path / f"{theme}.pptx")
    assert result.renderer == RENDERER == "python-pptx-native"
    assert result.theme == theme and result.slide_count == len(deck.slides)
    assert result.warnings == []

    report = verify_pptx(result.path, expected_slides=len(deck.slides))
    assert report.status == "verified", report.errors
    assert "no_text_overflow_estimated" in report.checks_passed
    assert report.metadata["total_slides"] == len(deck.slides)
    assert report.metadata["text_shapes"] > len(deck.slides)
    assert report.metadata["slides_with_notes"] == 2
    # One sample picture, plus the procedural background art on each title or section slide of a
    # glass theme (a library feature; the classic presets draw none).
    art = sum(
        1
        for s in deck.slides
        if s.layout in ("cover", "hero_cover", "section", "section_break", "closing")
    )
    glass = theme.startswith("glass-")
    # image_text carries a second sample picture in the library layouts.
    assert report.metadata["picture_shapes"] == 2 + (art if glass else 0)
    assert report.metadata["table_shapes"] == 1


def test_text_is_native_editable_text_not_images(tmp_path: Path) -> None:
    result = create_deck(sample_deck(), tmp_path / "d.pptx")
    prs = Presentation(str(result.path))
    pictures = [
        s for slide in prs.slides for s in slide.shapes if s.shape_type == MSO_SHAPE_TYPE.PICTURE
    ]
    assert len(pictures) == 2  # the image slide and the image_text slide carry a picture
    for index, expected in (
        (0, "MAYA AI Board Review"),
        (1, "Where we are"),
        (2, "Highlights"),
        (3, "Wins and risks"),
        (4, "Pillars"),
        (5, "Numbers"),
        (6, "It just works."),
        (7, "Revenue"),
        (8, "Roadmap"),
        (9, "Diagram"),
        (12, "Thank you"),
    ):
        assert any(expected in text for text in _texts(prs.slides[index])), index
    # Inline markup is applied as run formatting, not printed as asterisks.
    bullets = " ".join(_texts(prs.slides[2]))
    assert "voice" in bullets and "**" not in bullets
    assert HINGLISH in bullets


def test_speaker_notes_are_written_as_plain_text(tmp_path: Path) -> None:
    prs = Presentation(str(create_deck(sample_deck(), tmp_path / "n.pptx").path))
    assert prs.slides[0].notes_slide.notes_text_frame.text == "Open with the vision."
    assert prs.slides[2].notes_slide.notes_text_frame.text == "Talk about roadmap."
    assert not prs.slides[1].has_notes_slide


def test_slide_numbers_and_footer_are_native_fields(tmp_path: Path) -> None:
    prs = Presentation(str(create_deck(sample_deck(), tmp_path / "f.pptx").path))
    xml = prs.slides[2]._element.xml
    assert 'type="slidenum"' in xml
    assert any("Board Review" in t for t in _texts(prs.slides[2]))
    assert 'type="slidenum"' not in prs.slides[0]._element.xml  # title slide stays clean


@pytest.mark.parametrize("theme", THEME_NAMES)
def test_theme_colors_and_fonts_drive_the_slides(tmp_path: Path, theme: str) -> None:
    t = get_theme(theme)
    prs = Presentation(str(create_deck(sample_deck(theme), tmp_path / "t.pptx").path))
    xml = prs.slides[2]._element.xml
    assert t.colors.slide_bg.lstrip("#").upper() in xml
    assert t.colors.accent.lstrip("#").upper() in xml
    assert f'typeface="{t.fonts.body_font}"' in xml
    assert f'typeface="{t.fonts.complex_script_font}"' in xml  # Devanagari-capable run font


def test_hyperlinks_in_text_become_real_links(tmp_path: Path) -> None:
    deck = Deck.model_validate(
        {
            "title": "L",
            "slides": [
                {
                    "layout": "bullets",
                    "title": "Links",
                    "bullets": ["[ok](https://example.com) and [bad](javascript:alert)"],
                }
            ],
        }
    )
    prs = Presentation(str(create_deck(deck, tmp_path / "l.pptx").path))
    targets = [r.target_ref for r in prs.slides[0].part.rels.values() if r.is_external]
    assert targets == ["https://example.com"]


def test_all_shapes_stay_inside_the_slide(tmp_path: Path) -> None:
    prs = Presentation(str(create_deck(sample_deck(), tmp_path / "b.pptx").path))
    for number, slide in enumerate(prs.slides, start=1):
        for shape in slide.shapes:
            assert shape.left >= 0 and shape.top >= 0, number
            assert shape.left + shape.width <= int(prs.slide_width or 0) + 10, number
            assert shape.top + shape.height <= int(prs.slide_height or 0) + 10, number


def test_long_text_shrinks_the_font_instead_of_overflowing(tmp_path: Path) -> None:
    # Six bullets is the most a bullets slide holds (LAYOUT_ITEM_LIMITS); longer ones split.
    long_bullets = ["Long bullet sentence with many words in it. " * 4] * 6
    short = Deck.model_validate(
        {"title": "D", "slides": [{"layout": "bullets", "title": "S", "bullets": ["short"]}]}
    )
    long = Deck.model_validate(
        {
            "title": "D",
            "slides": [
                {"layout": "bullets", "title": "S", "bullets": [b[:160] for b in long_bullets]}
            ],
        }
    )

    def body_size(deck: Deck) -> float:
        prs = Presentation(str(create_deck(deck, tmp_path / "x.pptx").path))
        for shape in prs.slides[0].shapes:
            if shape.has_text_frame and "bullet" in shape.text_frame.text.lower():
                return float(shape.text_frame.paragraphs[0].runs[0].font.size.pt)
            if shape.has_text_frame and shape.text_frame.text == "short":
                return float(shape.text_frame.paragraphs[0].runs[0].font.size.pt)
        raise AssertionError("body text box not found")

    assert body_size(long) < body_size(short)
    assert verify_pptx(tmp_path / "x.pptx").status == "verified"


def test_text_that_does_not_fit_is_split_and_reported(tmp_path: Path) -> None:
    deck = Deck.model_validate(
        {
            "title": "D",
            "slides": [
                {"layout": "section", "title": "fine"},
                {
                    "layout": "cards",
                    "title": "Too much",
                    "cards": [{"title": "t" * 80, "body": "b" * 400}] * 4,
                },
            ],
        }
    )
    result = create_deck(deck, tmp_path / "e.pptx")
    assert result.slide_count == 3  # section + the cards slide split into two halves
    assert any("slide 2 (cards) text did not fit" in r for r in result.repairs)
    assert any("Too much (cont.)" in t for t in _texts(Presentation(str(result.path)).slides[2]))
    assert verify_pptx(result.path, expected_slides=3).status == "verified"


def test_text_that_cannot_fit_even_alone_raises_a_precise_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Splitting is what normally rescues a slide; with nothing left to split, the error stands.
    from app.automation import pptx_engine

    monkeypatch.setattr(pptx_engine, "halve", lambda slide: None)
    deck = Deck.model_validate(
        {
            "title": "D",
            "slides": [
                {"layout": "section", "title": "fine"},
                {
                    "layout": "cards",
                    "title": "Too much",
                    "cards": [{"title": "t" * 80, "body": "b" * 400}] * 4,
                },
            ],
        }
    )
    with pytest.raises(SlideFitError, match=r"Slide 2: card text does not fit"):
        create_deck(deck, tmp_path / "e.pptx")


def test_table_slide_uses_a_native_table_with_theme_header(tmp_path: Path) -> None:
    deck = Deck.model_validate(
        {
            "theme": "executive",
            "title": "D",
            "slides": [
                {
                    "layout": "table",
                    "title": "T",
                    "table": {
                        "headers": ["A", "B"],
                        "rows": [[str(i), "v"] for i in range(model.MAX_SLIDE_TABLE_ROWS)],
                    },
                }
            ],
        }
    )
    prs = Presentation(str(create_deck(deck, tmp_path / "t.pptx").path))
    (table_shape,) = [s for s in prs.slides[0].shapes if getattr(s, "has_table", False)]
    assert len(table_shape.table.rows) == model.MAX_SLIDE_TABLE_ROWS + 1
    assert verify_pptx(tmp_path / "t.pptx").status == "verified"


def test_unresolvable_image_path_gets_a_placeholder_and_a_warning(tmp_path: Path) -> None:
    deck = Deck.model_validate(
        {
            "title": "D",
            "slides": [
                {"layout": "image", "title": "I", "image": {"src": "img/none.png", "alt": "Gone"}}
            ],
        }
    )
    result = create_deck(deck, tmp_path / "i.pptx")
    assert any("img/none.png" in w for w in result.warnings)
    prs = Presentation(str(result.path))
    assert any("Image unavailable: Gone" in t for t in _texts(prs.slides[0]))


# ------------------------------------------------------------------------- legacy API
def test_legacy_create_presentation_still_works_for_legacy_layouts(tmp_path: Path) -> None:
    slides: list[dict[str, Any]] = [
        {"layout": "title", "title": "Legacy deck", "subtitle": "Sub"},
        {
            "layout": "cards",
            "title": "Cards",
            "cards": [{"title": "A", "body": "a body"}, {"title": "B", "body": "b body"}],
        },
        {
            "layout": "metrics",
            "title": "Metrics",
            "metrics": [{"value": "1", "label": "one"}, {"value": "2", "label": "two"}],
        },
        {"layout": "bullets", "title": "Bullets", "bullets": ["x", "y"]},
    ]
    out = create_presentation(slides, tmp_path / "legacy.pptx")
    assert isinstance(out, Path) and out.is_file()
    report = verify_pptx(out, expected_slides=4)
    assert report.status == "verified", report.errors
    assert len(Presentation(str(out)).slides) == 4


def test_legacy_defaults_missing_layout_and_title() -> None:
    deck = deck_from_legacy_slides([{"bullets": ["a"]}, {"layout": "bullets", "bullets": []}])
    assert [s.layout for s in deck.slides] == ["bullets", "bullets"]
    assert deck.slides[0].title == "Untitled"
    assert deck.title == "Untitled"


def test_strict_mode_reports_incomplete_slides_instead_of_filling_them() -> None:
    with pytest.raises(ValueError, match="needs a title"):
        deck_from_legacy_slides([{"layout": "bullets", "bullets": ["a"]}], lenient=False)
    with pytest.raises(ValueError, match="at least one bullet"):
        deck_from_legacy_slides([{"layout": "bullets", "title": "T"}], lenient=False)


def test_unknown_layout_falls_back_to_bullets_and_is_reported(tmp_path: Path) -> None:
    deck = deck_from_legacy_slides([{"layout": "carousel", "title": "x", "bullets": ["a"]}])
    assert deck.slides[0].layout == "bullets"
    assert any("carousel" in note and "bullets" in note for note in deck.normalize_notes)
    result = create_deck(deck, tmp_path / "fallback.pptx")
    assert any("carousel" in r for r in result.repairs)


def test_verifier_flags_text_that_would_overflow_its_box(tmp_path: Path) -> None:
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(2), Inches(0.5))
    box.text_frame.word_wrap = True
    box.text_frame.text = "This sentence is far too long to fit inside the tiny box. " * 6
    path = tmp_path / "overflow.pptx"
    prs.save(str(path))
    report = verify_pptx(path)
    assert report.status == "failed"
    assert any("text may overflow its box" in e for e in report.errors)


def test_verifier_flags_picture_only_decks(tmp_path: Path) -> None:
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    import base64
    import io

    from tests.support.documents import TINY_PNG

    slide.shapes.add_picture(
        io.BytesIO(base64.b64decode(TINY_PNG.split(",", 1)[1])), Inches(1), Inches(1), Inches(3)
    )
    path = tmp_path / "pictures.pptx"
    prs.save(str(path))
    report = verify_pptx(path)
    assert report.status == "failed"
    assert any("No editable text" in e for e in report.errors)
