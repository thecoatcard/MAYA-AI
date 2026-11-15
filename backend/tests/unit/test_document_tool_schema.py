"""The create_document tool schema and prompt match the document engine (no drift).

The schema imports its allowed values from the engine, so these tests fail when someone adds a
layout, block type, theme, font or design style (or a field of the dispatch spec models) without
the tool seeing it, or when the prompt names a screen the web app does not have. No model call.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.automation import design, model, pptx_engine, themes
from app.automation.design import DesignSpec
from app.services import document_tools as dt
from app.tasks.dispatch import BlockSpec, SlideSpec

SPEC = dt.CREATE_DOCUMENT_TOOL.parameters["properties"]["spec"]["properties"]
ALLOWED_KEYWORDS = {
    "type",
    "properties",
    "items",
    "enum",
    "required",
    "minimum",
    "maximum",
    "description",
}


def _walk(node: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        found.append(node)
        for key in ("properties",):
            for child in (node.get(key) or {}).values():
                found.extend(_walk(child))
        if isinstance(node.get("items"), dict):
            found.extend(_walk(node["items"]))
    return found


def test_closed_values_come_from_the_engine() -> None:
    slide = SPEC["slides"]["items"]["properties"]
    block = SPEC["blocks"]["items"]["properties"]
    look = SPEC["design"]["properties"]
    assert slide["layout"]["enum"] == list(model.SLIDE_LAYOUTS)
    assert block["type"]["enum"] == list(model.BLOCK_TYPES)
    assert SPEC["theme"]["enum"] == list(themes.THEME_NAMES) == look["base"]["enum"]
    assert look["heading_font"]["enum"] == list(design.FONT_NAMES) == look["body_font"]["enum"]
    assert look["cover"]["enum"] == list(design.COVER_STYLES)
    assert look["heading_style"]["enum"] == list(design.HEADING_STYLES)
    assert look["table_style"]["enum"] == list(design.TABLE_STYLES)
    assert look["callout_style"]["enum"] == list(design.CALLOUT_STYLES)
    assert look["density"]["enum"] == list(design.DENSITIES)
    assert look["shape_style"]["enum"] == list(design.SHAPE_STYLES)
    assert look["decorations"]["items"]["enum"] == list(design.DECORATIONS)
    assert dt.CREATE_DOCUMENT_TOOL.parameters["properties"]["format"]["enum"] == list(
        dt.DOCUMENT_FORMATS
    )


def test_every_slide_layout_the_deck_engine_draws_is_in_the_schema() -> None:
    builder = pptx_engine._DeckBuilder
    drawn = {name for name in model.SLIDE_LAYOUTS if hasattr(builder, f"_layout_{name}")}
    listed = set(SPEC["slides"]["items"]["properties"]["layout"]["enum"])
    assert drawn == listed  # a listed layout the engine cannot draw would fail at build time
    extra = SlideSpec.model_fields["layout"].json_schema_extra
    assert isinstance(extra, dict) and extra.get("enum") == list(model.SLIDE_LAYOUTS)


def test_schema_fields_match_the_dispatch_spec_models() -> None:
    assert set(SPEC["blocks"]["items"]["properties"]) == set(BlockSpec.model_fields)
    slide_fields = set(SlideSpec.model_fields)
    assert set(SPEC["slides"]["items"]["properties"]) == slide_fields
    assert set(SPEC["design"]["properties"]) == set(DesignSpec.model_fields)
    assert set(SPEC["design"]["properties"]["palette"]["properties"]) == set(
        design.DesignPalette.model_fields
    )


def test_numeric_limits_come_from_the_engine() -> None:
    look = SPEC["design"]["properties"]
    assert (look["body_pt"]["minimum"], look["body_pt"]["maximum"]) == (
        design.MIN_BODY_PT,
        design.MAX_BODY_PT,
    )
    assert (look["heading_scale"]["minimum"], look["heading_scale"]["maximum"]) == (
        design.MIN_SCALE,
        design.MAX_SCALE,
    )


def test_the_schema_uses_only_keywords_the_gateway_passes_through() -> None:
    """The gateway hands the schema to Gemini unchanged (``parameters_json_schema``)."""
    for tool in dt.LOOP_TOOL_DEFINITIONS:
        for node in _walk(tool.parameters):
            assert set(node) <= ALLOWED_KEYWORDS, (tool.name, set(node) - ALLOWED_KEYWORDS)


def test_the_prompt_lists_the_engine_values_and_the_result_fields() -> None:
    prompt = dt.TOOL_LOOP_ADDENDUM
    for layout in model.SLIDE_LAYOUTS:
        assert layout in prompt
    for theme in themes.THEME_NAMES:
        assert theme in prompt
    for block in model.BLOCK_TYPES:
        assert block in prompt
    for field in ("pages_requested", "length_note", "visual_review", "ONE short line"):
        assert field in prompt


def test_the_prompt_names_only_screens_the_web_app_has() -> None:
    web = Path(__file__).resolve().parents[3] / "apps" / "web" / "src"
    if not web.is_dir():
        return  # a backend-only checkout has no web source to compare with
    source = " ".join(p.read_text(encoding="utf-8") for p in web.rglob("*.tsx"))
    for element in dt.LOOP_UI_ELEMENTS:
        for quoted in re.findall(r"(?:Settings > )?([A-Z][A-Za-z ]{3,})", element.split("(")[0]):
            word = quoted.strip().split(" ")[0]
            if word in {"Settings", "Download", "Artifacts", "Connections", "Files", "Tasks"}:
                assert word in source, f"'{word}' from '{element}' is not in apps/web/src"
