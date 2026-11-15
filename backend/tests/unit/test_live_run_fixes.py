"""Regression tests for the live-run findings: project retries, notes length, palette roles,
content guards."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.agents.long_document_plan import build_long_document_plan
from app.agents.requirements import detect_language, extract_style, language_pin
from app.agents.specialized.project_writer import (
    MARKER_NAME,
    _discard_partial,
    _project_directory,
    _write_files,
    _write_marker,
)
from app.automation.acceptance import (
    NOTES_WORDS_PER_PAGE,
    pages_bounds,
    plan_chapters,
    words_floor,
)
from app.automation.content_guard import strip_invented_contacts, title_only_slides
from app.automation.design import contrast_ratio, resolve_design
from app.tasks.dispatch import _apply_user_style, _final_design_notes
from app.tasks.long_document import drop_request_echoes, is_request_echo


# --------------------------------------------------------------------------- project retries
def test_artifact_mode_retries_reuse_one_project_directory(tmp_path: Path) -> None:
    first, _ = _project_directory(tmp_path, "my-site", "t1", folder_mode=False)
    (first / "index.html").write_text("old")
    (tmp_path / "my-site.zip").write_bytes(b"old")
    second, _ = _project_directory(tmp_path, "my-site", "t1", folder_mode=False)
    assert second == first and not any(second.iterdir())
    assert not (tmp_path / "my-site.zip").exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["my-site"]  # no my-site-2, -3


def test_folder_mode_reuses_only_this_tasks_directory(tmp_path: Path) -> None:
    mine, _ = _project_directory(tmp_path, "site", "t1", folder_mode=True)
    written = _write_files(mine, {"index.html": "<p>1</p>"})
    _write_marker(mine, "t1", written)
    (mine / "user-note.txt").write_text("keep me")
    again, reused = _project_directory(tmp_path, "site", "t1", folder_mode=True)
    assert again == mine and reused is True
    assert not (mine / "index.html").exists()  # only the files the marker lists were removed
    assert (mine / "user-note.txt").read_text() == "keep me"
    other, reused = _project_directory(tmp_path, "site", "t2", folder_mode=True)
    assert other != mine and reused is False  # another task's (or the user's) folder is untouched


def test_a_failed_run_leaves_no_half_project(tmp_path: Path) -> None:
    folder_dir, _ = _project_directory(tmp_path, "a", "t", folder_mode=True)
    (folder_dir / MARKER_NAME).write_text("{}")
    _discard_partial(folder_dir, None, True)
    assert not folder_dir.exists()
    art_dir, _ = _project_directory(tmp_path, "b", "t", folder_mode=False)
    (art_dir / "index.html").write_text("x")
    zip_path = tmp_path / "b.zip"
    zip_path.write_bytes(b"x")
    _discard_partial(art_dir, zip_path, False)
    assert not art_dir.exists() and not zip_path.exists()


# --------------------------------------------------------------------------- notes length
def test_a_12_page_notes_request_is_planned_to_the_words_floor() -> None:
    plan = plan_chapters({"pages": 12}, "notes")
    words = plan.pages_per_chapter * plan.chapters * NOTES_WORDS_PER_PAGE
    assert words >= words_floor(12)  # before: 7.2 body pages = 2,232 words against a 3,510 floor
    assert plan.pages_per_chapter * plan.chapters + 0.5 <= pages_bounds(12)[1]
    book = plan_chapters({"pages": 12})
    assert book.pages_per_chapter * book.chapters < plan.pages_per_chapter * plan.chapters
    _, nodes, _ = build_long_document_plan("12 pages Java notes", {"pages": 12})
    assert nodes[0].input_data["pipeline"]["body_pages"] >= 11


def test_the_request_wording_is_never_a_topic_or_subtopic() -> None:
    request = "12 pages Java notes"
    assert is_request_echo("Java notes", request) and is_request_echo("12 pages", request)
    assert not is_request_echo("Java", request) and not is_request_echo("Collections", request)
    chapters = [{"subtopics": ["Operators", "Java notes"]}, {"subtopics": ["Generics"]}]
    assert drop_request_echoes(chapters, request) == ["Java notes"]
    assert chapters[0]["subtopics"] == ["Operators"]


def test_language_is_pinned_to_the_requests_language() -> None:
    assert detect_language("12 pages Java notes") == "English"
    assert detect_language("mujhe java ke notes banao") == "Hinglish"
    assert detect_language("notes in Hindi") == "Hindi"
    pin = language_pin("12 pages Java notes")
    assert "English only" in pin and "Do not use Hindi or Hinglish" in pin


# --------------------------------------------------------------------------- colour roles
def test_dark_blue_theme_is_a_dark_page_not_just_an_accent() -> None:
    style = extract_style("dark blue minimal theme study guide on SQL joins, 6 pages")
    roles = style["palette_roles"]
    assert roles["background"] == "#0B1B3A" and style["theme"] == "minimal"
    ctx = {"requirements": {"user_request": "x", "style": style}}
    out: dict[str, Any] = {"file_type": "pdf", "theme": "executive"}
    _apply_user_style(out, ctx)
    assert out["theme"] == "minimal" and out["design"]["palette"]["background"] == "#0B1B3A"
    theme, _ = resolve_design(out["design"], "minimal")
    c = theme.colors
    assert theme.dark and c.page_bg == "#0B1B3A"
    assert contrast_ratio(c.text, c.page_bg) >= 4.5 and contrast_ratio(c.heading, c.page_bg) >= 4.5


def test_a_plain_colour_word_colours_the_headings_on_a_light_page() -> None:
    style = extract_style("a green report on solar")
    out: dict[str, Any] = {"file_type": "pdf", "theme": "executive"}
    _apply_user_style(out, {"requirements": {"style": style}})
    theme, _ = resolve_design(out["design"], "executive")
    assert not theme.dark and theme.colors.heading.upper() == "#0F766E"
    assert contrast_ratio(theme.colors.heading, theme.colors.page_bg) >= 4.5


def test_design_notes_of_a_discarded_design_are_not_reported() -> None:
    notes = ["3 slide(s) were reshaped", "design: surface #fff was too close to the text colour"]
    out = {"file_type": "pptx", "theme": "minimal"}  # the user's theme replaced the design
    assert _final_design_notes(notes, out, {}) == ["3 slide(s) were reshaped"]


# --------------------------------------------------------------------------- content guards
def test_invented_contact_lines_are_stripped_but_supplied_ones_stay() -> None:
    slides: list[dict[str, Any]] = [
        {"layout": "title", "title": "Java"},
        {
            "layout": "bullets",
            "title": "Contact",
            "bullets": [
                "Email: engineering@enterprise.internal",
                "Docs: www.made-up-corp.com/java",
                "Ask us at help@real.org",
                "Generics are great",
            ],
        },
    ]
    removed = strip_invented_contacts(slides, "please mention help@real.org")
    assert "engineering@enterprise.internal" in removed
    assert slides[1]["bullets"] == ["Ask us at help@real.org", "Generics are great"]


def test_title_only_content_slides_are_found() -> None:
    slides: list[dict[str, Any]] = [
        {"layout": "title", "title": "Deck"},
        {"layout": "bullets", "title": "Fine", "bullets": ["a"]},
        {"layout": "bullets", "title": "Empty", "bullets": []},
        {"layout": "closing", "title": "Thanks"},
    ]
    assert title_only_slides(slides) == [3]
