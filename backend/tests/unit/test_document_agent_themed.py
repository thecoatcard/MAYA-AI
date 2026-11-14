"""DocumentAgent routing for pdf/pptx/docx through the themed renderers, and its honest proof."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from app.agents.schemas import AgentExecutionStatus
from app.agents.specialized.document import DocumentAgent
from app.automation import pdf_engine
from app.automation.browser_pdf import BrowserUnavailableError
from app.tasks.queue import SubtaskJob
from tests.support.documents import HINGLISH, requires_browser

BLOCKS: list[dict[str, Any]] = [
    {"type": "heading", "level": 1, "text": "Summary"},
    {"type": "paragraph", "text": f"Body with **bold** text. {HINGLISH}"},
    {"type": "table", "headers": ["A", "B"], "rows": [["1", "2"]]},
    {"type": "page_break"},
    {"type": "heading", "level": 1, "text": "Appendix"},
    {"type": "paragraph", "text": "More."},
]
SLIDES: list[dict[str, Any]] = [
    {"layout": "title", "title": "Deck", "subtitle": "Sub", "notes": "say hi"},
    {"layout": "bullets", "title": "Points", "bullets": ["a", "b"]},
    {
        "layout": "timeline",
        "title": "Roadmap",
        "steps": [{"title": "Q1", "detail": "x"}, {"title": "Q2", "detail": "y"}],
    },
]


def _job() -> SubtaskJob:
    return SubtaskJob(
        workspace_id=uuid4(),
        user_id=uuid4(),
        task_id=uuid4(),
        subtask_id=uuid4(),
        assigned_agent="document",
    )


class _Broken:
    def render(self, html: str, output_path: Path | str) -> Any:
        raise BrowserUnavailableError("No usable Microsoft Edge or Google Chrome was found")


@pytest.fixture
def force_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pdf_engine, "get_browser_renderer", lambda: _Broken())


async def test_pdf_fallback_is_recorded_in_the_proof(tmp_path: Path, force_fallback: None) -> None:
    agent = DocumentAgent(artifacts_base_dir=tmp_path)
    result = await agent.execute_subtask(
        _job(),
        {
            "filename": "report.pdf",
            "file_type": "pdf",
            "title": "Report",
            "theme": "midnight",
            "blocks": BLOCKS,
        },
    )
    proof = result.verification_proof
    assert result.status == AgentExecutionStatus.SUCCESS, proof
    assert proof["status"] == "verified"
    assert proof["renderer"] == "reportlab-fallback"
    assert proof["theme"] == "midnight"
    assert proof["browser"] is None
    assert any("ReportLab fallback" in w for w in proof["warnings"])
    assert proof["total_pages"] >= 2
    assert proof["file_checksum"] and proof["size_bytes"] > 0


@requires_browser
async def test_pdf_uses_the_real_browser_and_says_so(tmp_path: Path) -> None:
    agent = DocumentAgent(artifacts_base_dir=tmp_path)
    result = await agent.execute_subtask(
        _job(),
        {
            "filename": "report.pdf",
            "file_type": "pdf",
            "title": f"Report {HINGLISH}",
            "theme": "executive",
            "blocks": BLOCKS,
        },
    )
    proof = result.verification_proof
    assert result.status == AgentExecutionStatus.SUCCESS, proof
    assert proof["renderer"] == "browser" and proof["warnings"] == []
    assert proof["theme"] == "executive"
    assert proof["browser"].split()[0] in {"msedge", "chrome"}
    assert proof["fonts"]


async def test_legacy_pdf_sections_and_content_still_work(
    tmp_path: Path, force_fallback: None
) -> None:
    agent = DocumentAgent(artifacts_base_dir=tmp_path)
    legacy = await agent.execute_subtask(
        _job(),
        {
            "filename": "legacy.pdf",
            "doc_type": "pdf",
            "title": "Legacy",
            "sections": [{"type": "heading1", "text": "H"}, {"type": "paragraph", "text": "p"}],
        },
    )
    plain = await agent.execute_subtask(
        _job(), {"filename": "plain.pdf", "doc_type": "pdf", "content": "Just text."}
    )
    for result in (legacy, plain):
        assert result.status == AgentExecutionStatus.SUCCESS, result.verification_proof
        assert result.verification_proof["theme"] == "executive"  # default


async def test_pptx_routes_through_the_deck_renderer_with_theme_and_notes(tmp_path: Path) -> None:
    agent = DocumentAgent(artifacts_base_dir=tmp_path)
    result = await agent.execute_subtask(
        _job(),
        {
            "filename": "deck.pptx",
            "file_type": "pptx",
            "title": "Deck",
            "theme": "minimal",
            "slides": SLIDES,
            "expected_slides": 3,
        },
    )
    proof = result.verification_proof
    assert result.status == AgentExecutionStatus.SUCCESS, proof
    assert proof["renderer"] == "python-pptx-native"
    assert proof["theme"] == "minimal"
    assert proof["total_slides"] == 3
    assert proof["slides_with_notes"] == 1
    assert "no_text_overflow_estimated" in proof["checks_passed"]


async def test_pptx_expected_slide_mismatch_is_fitted_never_rejected(tmp_path: Path) -> None:
    # Count policy (deliberate update): 3 slides for a stated 8 used to FAIL the step ("Slide count
    # mismatch"). The deck is now fitted in code to exactly 8 slides and delivered.
    agent = DocumentAgent(artifacts_base_dir=tmp_path)
    result = await agent.execute_subtask(
        _job(),
        {"filename": "d.pptx", "file_type": "pptx", "slides": SLIDES, "expected_slides": 8},
    )
    assert result.status == AgentExecutionStatus.SUCCESS
    proof = result.verification_proof
    assert proof["status"] == "verified" and proof["total_slides"] == 8
    assert proof["acceptance"]["met"] is True and proof["acceptance"]["status"] == "met"
    assert any(h["attempt"] == "fit_slide_count" for h in proof["healing"])


async def test_docx_is_routed_and_verified(tmp_path: Path) -> None:
    agent = DocumentAgent(artifacts_base_dir=tmp_path)
    result = await agent.execute_subtask(
        _job(),
        {
            "filename": "memo.docx",
            "file_type": "docx",
            "title": "Memo",
            "theme": "midnight",
            "blocks": BLOCKS,
        },
    )
    proof = result.verification_proof
    assert result.status == AgentExecutionStatus.SUCCESS, proof
    assert proof["file_type"] == "docx"
    assert proof["renderer"] == "python-docx-native" and proof["theme"] == "midnight"
    assert proof["status"] == "verified"


async def test_docx_is_detected_from_the_filename(tmp_path: Path) -> None:
    result = await DocumentAgent(artifacts_base_dir=tmp_path).execute_subtask(
        _job(), {"filename": "memo.docx", "title": "Memo", "blocks": BLOCKS}
    )
    assert result.verification_proof["file_type"] == "docx"


@pytest.mark.parametrize(
    ("file_type", "extra", "needle"),
    [
        ("pdf", {"blocks": [{"type": "html", "text": "<b>x</b>"}]}, "html"),
        ("pdf", {"theme": "neon", "blocks": BLOCKS}, "unknown theme 'neon'"),
        (
            "docx",
            {"blocks": [{"type": "image", "src": "http://evil.example/a.png", "alt": "x"}]},
            "not a URL",
        ),
        ("pptx", {"slides": [{"layout": "bullets", "title": "No bullets"}]}, "at least one bullet"),
    ],
)
async def test_invalid_specs_fail_with_a_precise_message_and_render_nothing(
    tmp_path: Path, file_type: str, extra: dict[str, Any], needle: str
) -> None:
    name = f"bad.{file_type}"
    result = await DocumentAgent(artifacts_base_dir=tmp_path).execute_subtask(
        _job(), {"filename": name, "file_type": file_type, "title": "Bad", **extra}
    )
    assert result.status == AgentExecutionStatus.FAILED
    proof = result.verification_proof
    assert proof["status"] == "failed"
    assert any(needle in e for e in proof["errors"]), proof["errors"]
    assert "Invalid document specification" in proof["errors"][0]
    assert not _find(tmp_path, name)


async def test_an_unknown_slide_layout_renders_as_bullets_and_is_reported(tmp_path: Path) -> None:
    result = await DocumentAgent(artifacts_base_dir=tmp_path).execute_subtask(
        _job(),
        {
            "filename": "fallback.pptx",
            "file_type": "pptx",
            "title": "Fallback",
            "slides": [{"layout": "carousel", "title": "x", "bullets": ["a"]}],
        },
    )
    assert result.status == AgentExecutionStatus.SUCCESS, result.verification_proof
    assert any("carousel" in note for note in result.verification_proof.get("repairs", []))


def _find(root: Path, name: str) -> list[Path]:
    return list(root.rglob(name))


def _write_workspace_image(workspace: Path) -> None:
    import base64

    from tests.support.documents import TINY_PNG

    (workspace / "img").mkdir(parents=True)
    (workspace / "img" / "p.png").write_bytes(base64.b64decode(TINY_PNG.split(",", 1)[1]))


async def test_workspace_images_are_resolved_for_documents(tmp_path: Path) -> None:
    job = _job()
    _write_workspace_image(tmp_path / str(job.workspace_id))
    result = await DocumentAgent(artifacts_base_dir=tmp_path).execute_subtask(
        job,
        {
            "filename": "i.docx",
            "title": "I",
            "blocks": [{"type": "image", "src": "img/p.png", "alt": "pixel"}],
        },
    )
    assert result.status == AgentExecutionStatus.SUCCESS
    assert result.verification_proof["warnings"] == []
