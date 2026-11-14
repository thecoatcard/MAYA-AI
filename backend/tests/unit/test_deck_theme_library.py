"""The slide theme library: 25 themes, native graphics, icons, transitions and the HTML mirror."""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

import pytest
from lxml import etree
from pptx import Presentation

from app.automation import deck_icons, deck_transitions, themes
from app.automation.auto_design import default_design
from app.automation.deck_graphics import ART_MAX_BYTES, Ctx, background_art
from app.automation.deck_html import render_deck_html
from app.automation.deck_themes import (
    DECK_THEME_NAMES,
    DECK_THEME_SPECS,
    EDITORIAL_THEME_NAMES,
    GLASS_THEME_NAMES,
    tone_theme,
)
from app.automation.design import (
    AA_RATIO,
    FONT_NAMES,
    contrast_ratio,
    contrast_report,
    resolve_deck,
)
from app.automation.model import SLIDE_LAYOUTS, Deck, deck_from_dict
from app.automation.pptx_engine import create_deck
from app.automation.verifiers.pptx_verifier import verify_pptx
from app.services import document_tools as dt
from tests.support.documents import requires_browser, sample_deck

SPEC: dict[str, Any] = dt.CREATE_DOCUMENT_TOOL.parameters["properties"]["spec"]["properties"]


def _deck(theme: str, **fields: Any) -> Deck:
    slides = [
        {"layout": "hero_cover", "title": "Hello", "subtitle": "World"},
        {"layout": "bullets", "title": "Plain", "bullets": ["a", "b"]},
    ]
    return deck_from_dict({"title": "T", "theme": theme, "slides": slides, **fields})


# ------------------------------------------------------------------------------ the pack
def test_there_are_25_themes_in_two_families_registered_after_the_four_presets() -> None:
    assert len(GLASS_THEME_NAMES) == 12 and len(EDITORIAL_THEME_NAMES) == 13
    assert len(DECK_THEME_NAMES) == 25
    assert themes.THEME_NAMES[:4] == ("executive", "minimal", "midnight", "notes")
    assert themes.THEME_NAMES[4:] == DECK_THEME_NAMES
    assert themes.DOCUMENT_THEME_NAMES == themes.THEME_NAMES[:4]


@pytest.mark.parametrize("name", DECK_THEME_NAMES)
def test_every_pair_passes_aa_on_the_page_the_gradient_end_and_the_card(name: str) -> None:
    spec, theme = DECK_THEME_SPECS[name], themes.THEMES[name]
    assert min(contrast_report(theme).values()) >= AA_RATIO
    backgrounds = [spec.bg, theme.colors.card_bg] + ([spec.bg_end] if spec.bg_end else [])
    for background in backgrounds:
        for color in (spec.text, spec.muted, spec.accent):
            assert contrast_ratio(color, background) >= AA_RATIO, (name, color, background)
    assert contrast_ratio(spec.accent2, spec.bg) >= 3.0
    assert spec.heading_font in FONT_NAMES and spec.body_font in FONT_NAMES
    assert theme.dark == (name.startswith("glass-") and name != "glass-frost")


def test_glass_themes_are_dark_first_and_editorial_light_first() -> None:
    dark = [n for n in GLASS_THEME_NAMES if themes.THEMES[n].dark]
    assert len(dark) == 11
    assert not any(themes.THEMES[n].dark for n in EDITORIAL_THEME_NAMES)


def test_engine_registry_and_model_schema_enums_do_not_drift() -> None:
    slide = SPEC["slides"]["items"]["properties"]
    look = SPEC["design"]["properties"]
    assert SPEC["theme"]["enum"] == list(themes.THEME_NAMES) == look["base"]["enum"]
    assert slide["layout"]["enum"] == list(SLIDE_LAYOUTS)
    assert slide["cards"]["items"]["properties"]["icon"]["enum"] == list(deck_icons.ICON_NAMES)
    assert slide["steps"]["items"]["properties"]["icon"]["enum"] == list(deck_icons.ICON_NAMES)
    assert slide["transition"]["enum"] == ["", *deck_transitions.TRANSITION_NAMES]
    assert slide["chart"]["properties"]["type"]["enum"] == ["column", "bar", "line", "doughnut"]


def test_a_theme_applies_its_own_palette_radius_and_shadow() -> None:
    _theme, style, notes = resolve_deck({"base": "glass-aurora"}, None)
    assert notes == [] and style.family == "glass" and style.shape_style == "glass"
    assert style.background_end
    assert style.corner_radius == DECK_THEME_SPECS["glass-aurora"].radius[1]
    _theme, editorial, _ = resolve_deck(None, "editorial-paper")
    assert editorial.family == "editorial" and editorial.transition == "none"
    _theme, classic, _ = resolve_deck(None, "executive")
    assert classic.family == "classic" and classic.transition == "none"


@pytest.mark.parametrize(
    ("tone", "family"),
    [
        ("pitch", "glass"),
        ("tech", "glass"),
        ("report", "editorial"),
        ("finance", "editorial"),
        ("notes", "editorial"),
        ("kids", "editorial"),
        ("health", "editorial"),
    ],
)
def test_auto_design_picks_a_library_theme_by_tone_for_decks_only(tone: str, family: str) -> None:
    design = default_design(None, tone, deck=True)
    assert design["base"] == tone_theme(tone) and DECK_THEME_SPECS[design["base"]].family == family
    assert "palette" not in design and "heading_font" not in design
    assert default_design(None, tone, deck=False)["base"] not in DECK_THEME_SPECS
    assert default_design(None, "finance", deck=True)["base"] == "editorial-navy"
    assert default_design(None, "kids", deck=True)["base"] == "editorial-citrus"


def test_a_user_palette_on_a_library_base_wins_and_stays_readable() -> None:
    design = {"base": "glass-aurora", "palette": {"accent": "#223344"}}
    theme, _style, notes = resolve_deck(design, None)
    assert contrast_ratio(theme.colors.accent, theme.colors.page_bg) >= AA_RATIO
    assert any("accent" in n for n in notes)


# -------------------------------------------------------------------------------- art
def test_background_art_is_small_cached_and_only_for_glass() -> None:
    theme, style, _ = resolve_deck(None, "glass-sunset")
    art = background_art(Ctx(theme, style))
    assert art is not None and 0 < len(art.data) <= ART_MAX_BYTES
    assert art.data[:8] == b"\x89PNG\r\n\x1a\n"
    again = background_art(Ctx(theme, style))
    assert again is not None and again.data is art.data  # cached bytes
    theme, style, _ = resolve_deck(None, "editorial-paper")
    assert background_art(Ctx(theme, style)) is None


# ------------------------------------------------------------------------------ icons
def test_about_forty_icons_parse_inside_the_grid_and_unknown_names_fall_back() -> None:
    assert len(deck_icons.ICON_NAMES) >= 40
    for name in deck_icons.ICON_NAMES:
        segments = deck_icons.icon_segments(name)
        assert segments and segments[0].op == "M", name
        points = [p for seg in segments for p in seg.points]
        assert all(-0.01 <= x <= 24.01 and -0.01 <= y <= 24.01 for x, y in points), name
        assert deck_icons.icon_svg_path(name)
    assert deck_icons.canonical_icon("Rocket_Launch") == "rocket"
    assert deck_icons.icon_segments("no-such-icon") is None


def test_the_path_parser_handles_relative_commands_and_arcs() -> None:
    square = deck_icons.parse_path("M1 1h4v4H1z")
    assert [s.op for s in square] == ["M", "L", "L", "L", "Z"]
    assert square[2].points[0] == (5.0, 5.0)
    circle = deck_icons.parse_path("M2 12a10 10 0 1 0 20 0a10 10 0 1 0-20 0z")
    assert sum(1 for s in circle if s.op == "C") == 4
    xs = [p[0] for s in circle for p in s.points]
    assert min(xs) == pytest.approx(2) and max(xs) == pytest.approx(22)
    with pytest.raises(ValueError, match="needs"):
        deck_icons.parse_path("M1")


def test_icons_are_native_freeforms_and_unknown_icons_become_numbered_badges(
    tmp_path: Path,
) -> None:
    deck = deck_from_dict(
        {
            "title": "T",
            "theme": "glass-aurora",
            "slides": [
                {
                    "layout": "icon_cards",
                    "title": "I",
                    "cards": [
                        {"title": "A", "body": "x", "icon": "rocket"},
                        {"title": "B", "body": "y", "icon": "nonexistent"},
                    ],
                }
            ],
        }
    )
    result = create_deck(deck, tmp_path / "i.pptx")
    slide = Presentation(str(result.path)).slides[0]
    names = [s.name for s in slide.shapes]
    assert "visual:icon-rocket" in names and "visual:badge" in names
    icon = next(s for s in slide.shapes if s.name == "visual:icon-rocket")
    xml = etree.tostring(icon._element).decode()
    assert "custGeom" in xml and "cubicBezTo" in xml and 'cap="rnd"' in xml
    assert "prstGeom" not in xml


# -------------------------------------------------------------------------- transitions
def _slide_xml_names(path: Path) -> list[list[str]]:
    out = []
    with zipfile.ZipFile(path) as z:
        for name in sorted(n for n in z.namelist() if n.startswith("ppt/slides/slide")):
            if name.endswith(".xml"):
                root = etree.fromstring(z.read(name))
                out.append([etree.QName(c).localname for c in root])
    return out


def test_transition_xml_is_in_schema_order_and_reopens(tmp_path: Path) -> None:
    deck = _deck("glass-aurora")
    pushed = deck.slides[1].model_copy(update={"transition": "push"})
    deck = deck.model_copy(update={"slides": [deck.slides[0], pushed]})
    result = create_deck(deck, tmp_path / "t.pptx")
    orders = _slide_xml_names(result.path)
    assert orders == [["cSld", "clrMapOvr", "transition"]] * 2
    for order in orders:
        assert deck_transitions.order_is_valid(order)
    with zipfile.ZipFile(result.path) as z:
        first = z.read("ppt/slides/slide1.xml").decode()
        second = z.read("ppt/slides/slide2.xml").decode()
    assert '<p:transition spd="med"><p:fade/></p:transition>' in first
    assert '<p:transition spd="med"><p:push dir="u"/></p:transition>' in second
    assert "AlternateContent" not in first + second and "morph" not in (first + second).lower()
    assert len(Presentation(str(result.path)).slides) == 2
    assert verify_pptx(result.path, expected_slides=2).status == "verified"


def test_default_transition_is_fade_for_glass_none_for_editorial_and_explicit_wins(
    tmp_path: Path,
) -> None:
    editorial = create_deck(_deck("editorial-paper"), tmp_path / "e.pptx")
    assert all("transition" not in order for order in _slide_xml_names(editorial.path))
    classic = create_deck(_deck("executive"), tmp_path / "c.pptx")
    assert all("transition" not in order for order in _slide_xml_names(classic.path))
    asked = create_deck(_deck("editorial-paper", transition="wipe"), tmp_path / "w.pptx")
    with zipfile.ZipFile(asked.path) as z:
        assert '<p:wipe dir="l"/>' in z.read("ppt/slides/slide1.xml").decode()
    off = create_deck(_deck("glass-aurora", transition="none"), tmp_path / "n.pptx")
    assert all("transition" not in order for order in _slide_xml_names(off.path))


def test_transition_names_are_checked_and_synonyms_folded() -> None:
    assert deck_transitions.canonical_transition("Dissolve") == "fade"
    assert deck_transitions.canonical_transition("") == ""
    assert deck_transitions.canonical_transition("morph") is None
    with pytest.raises(ValueError, match="unknown transition"):
        _deck("glass-aurora", transition="morph")
    cover = deck_transitions.build_transition("cover")
    assert cover is not None and cover[0].get("dir") == "l"
    assert deck_transitions.build_transition("none") is None
    assert not deck_transitions.order_is_valid(["cSld", "transition", "clrMapOvr"])


# -------------------------------------------------------------------- graphics in pptx
@pytest.mark.parametrize("theme", ["glass-aurora", "glass-frost", "editorial-citrus"])
def test_library_graphics_are_native_shapes(tmp_path: Path, theme: str) -> None:
    result = create_deck(sample_deck(theme), tmp_path / "s.pptx")
    prs = Presentation(str(result.path))
    xml = "".join(etree.tostring(s._element).decode() for s in prs.slides)
    assert "roundRect" in xml and "outerShdw" in xml and "gradFill" in xml
    assert "chevron" in xml and "<a:alpha" in xml
    charts = [sh for s in prs.slides for sh in s.shapes if getattr(sh, "has_chart", False)]
    assert len(charts) == 1
    assert verify_pptx(result.path).status == "verified"


def test_charts_use_the_palette_and_doughnut_gets_a_ring(tmp_path: Path) -> None:
    deck = deck_from_dict(
        {
            "title": "T",
            "theme": "glass-aurora",
            "slides": [
                {
                    "layout": "chart",
                    "title": f"C {kind}",
                    "chart": {
                        "type": kind,
                        "categories": ["a", "b", "c"],
                        "series": [{"name": "s", "values": [1, 2, 3]}],
                    },
                }
                for kind in ("column", "bar", "line", "doughnut")
            ],
        }
    )
    result = create_deck(deck, tmp_path / "c.pptx")
    prs = Presentation(str(result.path))
    xmls = [
        etree.tostring(
            next(sh for sh in s.shapes if getattr(sh, "has_chart", False)).chart._chartSpace
        ).decode()
        for s in prs.slides
    ]
    accent = DECK_THEME_SPECS["glass-aurora"].accent.lstrip("#")
    assert all(accent in xml for xml in xmls)
    assert 'holeSize val="62"' in xmls[3]


def test_chart_series_must_match_the_categories() -> None:
    with pytest.raises(ValueError, match="values for 2 categories"):
        deck_from_dict(
            {
                "title": "T",
                "slides": [
                    {
                        "layout": "chart",
                        "title": "C",
                        "chart": {
                            "categories": ["a", "b"],
                            "series": [{"name": "s", "values": [1]}],
                        },
                    }
                ],
            }
        )


def test_over_full_library_slides_are_split_not_dropped(tmp_path: Path) -> None:
    deck = deck_from_dict(
        {
            "title": "T",
            "theme": "glass-aurora",
            "slides": [
                {
                    "layout": "process",
                    "title": "P",
                    "steps": [{"title": f"s{i}", "detail": "d"} for i in range(6)],
                }
            ],
        }
    )
    result = create_deck(deck, tmp_path / "p.pptx")
    assert result.slide_count == 2 and any("split" in r for r in result.repairs)


# ------------------------------------------------------------------------ html mirror
@pytest.mark.parametrize("theme", ["glass-aurora", "editorial-sand", "executive"])
def test_the_html_mirror_draws_every_library_layout(theme: str) -> None:
    html = render_deck_html(sample_deck(theme))
    assert html.count('<section class="page"') >= len(SLIDE_LAYOUTS)
    assert "<svg" in html and "linear-gradient" in html
    assert render_deck_html(sample_deck(theme)) == html  # deterministic


@requires_browser
def test_a_glass_deck_renders_to_real_pixels_through_the_browser(tmp_path: Path) -> None:
    from PIL import Image

    from app.automation.browser_pdf import get_browser_renderer, shutdown_browser_renderer

    try:
        shot = get_browser_renderer().screenshot_pages(
            render_deck_html(sample_deck("glass-aurora")), tmp_path, max_pages=3
        )
    finally:
        shutdown_browser_renderer()
    image = Image.open(shot.paths[0]).convert("RGB").crop((0, 0, 794, 447))
    colours = {image.getpixel((x, y)) for x in range(0, 794, 40) for y in range(0, 447, 40)}
    assert len(colours) > 20
