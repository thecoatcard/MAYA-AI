"""The document specification the model fills in: prompt contract, validation, proof fields."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from app.agents.schemas import AgentExecutionStatus, SpecialistAgentResult, SpecialistAgentRole
from app.automation import model as doc_model
from app.automation.themes import THEME_NAMES
from app.tasks.dispatch import (
    DOCUMENT_SYSTEM_PROMPT,
    DocumentSpec,
    FleetSupport,
    SlideSpec,
)
from app.tasks.queue import SubtaskJob
from app.tasks.worker import NonRetryableSubtaskError


class _Gateway:
    client = None

    def __init__(self, spec: DocumentSpec) -> None:
        self.spec = spec
        self.calls: list[dict[str, Any]] = []

    async def generate_structured(self, **kwargs: Any) -> DocumentSpec:
        self.calls.append(kwargs)
        return self.spec


def _job() -> SubtaskJob:
    return SubtaskJob(
        workspace_id=uuid4(),
        user_id=uuid4(),
        task_id=uuid4(),
        subtask_id=uuid4(),
        assigned_agent="document",
    )


def _support(spec: DocumentSpec, tmp_path: Path) -> tuple[FleetSupport, _Gateway]:
    gateway = _Gateway(spec)
    return FleetSupport(gateway=gateway, artifacts_dir=tmp_path), gateway  # type: ignore[arg-type]


def _spec(**fields: Any) -> DocumentSpec:
    base: dict[str, Any] = {"file_type": "pdf", "filename": "Report!!.pdf", "title": "Report"}
    base.update(fields)
    return DocumentSpec.model_validate(base)


def test_prompt_forbids_html_and_lists_every_theme_block_and_layout() -> None:
    prompt = DOCUMENT_SYSTEM_PROMPT
    assert "NEVER output HTML" in prompt
    for theme in THEME_NAMES:
        assert theme in prompt
    for block_type in doc_model.BLOCK_TYPES:
        assert block_type in prompt, block_type
    for layout in doc_model.SLIDE_LAYOUTS:
        assert layout in prompt, layout
    for file_type in ("pdf", "docx", "xlsx", "pptx"):
        assert file_type in prompt
    assert str(doc_model.MAX_BLOCKS) in prompt and str(doc_model.MAX_SLIDES) in prompt


def test_spec_accepts_legacy_and_new_fields() -> None:
    legacy = DocumentSpec(
        file_type="pdf",
        filename="a.pdf",
        title="T",
        sections=[{"type": "heading1", "text": "H"}],
    )
    assert legacy.theme == "executive" and legacy.blocks == []
    new = DocumentSpec(
        file_type="docx",
        filename="a.docx",
        title="T",
        theme="midnight",
        blocks=[{"type": "paragraph", "text": "x"}],
        slides=[SlideSpec(layout="timeline", title="T", steps=[{"title": "a"}, {"title": "b"}])],
    )
    assert new.file_type == "docx" and new.slides[0].layout == "timeline"
    # The original four legacy layouts and fields are still constructible.
    SlideSpec(layout="cards", title="C", cards=[{"title": "a", "body": "b"}])
    SlideSpec(layout="metrics", title="M", metrics=[{"value": "1", "label": "l"}])


async def test_valid_blocks_flow_into_the_context(tmp_path: Path) -> None:
    spec = _spec(
        theme="minimal",
        author="MAYA",
        date="2026-11-09",
        blocks=[{"type": "heading", "level": 1, "text": "H"}, {"type": "paragraph", "text": "p"}],
    )
    support, gateway = _support(spec, tmp_path)
    job = _job()
    ctx = await support.build_context(job, {"description": "Write a report on scooters"})
    assert ctx["file_type"] == "pdf" and ctx["theme"] == "minimal"
    assert ctx["blocks"] == [b.to_block() for b in spec.blocks] and ctx["author"] == "MAYA"
    assert ctx["filename"] == f"{job.task_id}/Report.pdf"
    assert gateway.calls[0]["system_instruction"] == DOCUMENT_SYSTEM_PROMPT
    assert gateway.calls[0]["response_schema"] is DocumentSpec


async def test_legacy_sections_still_flow_into_the_context(tmp_path: Path) -> None:
    spec = _spec(sections=[{"type": "heading1", "text": "H"}, {"type": "paragraph", "text": "p"}])
    support, _ = _support(spec, tmp_path)
    ctx = await support.build_context(_job(), {"description": "x"})
    assert [s["type"] for s in ctx["sections"]] == ["heading1", "paragraph"]
    assert "blocks" not in ctx


async def test_pptx_context_carries_new_layouts_and_expected_slide_count(tmp_path: Path) -> None:
    spec = _spec(
        file_type="pptx",
        filename="deck.pptx",
        theme="midnight",
        slides=[
            SlideSpec(layout="title", title="T"),
            SlideSpec(layout="quote", quote="q", by="me", notes="n"),
        ],
    )
    support, _ = _support(spec, tmp_path)
    ctx = await support.build_context(_job(), {"description": "Make a 2-slide deck on scooters"})
    assert ctx["theme"] == "midnight" and ctx["expected_slides"] == 2
    assert [s["layout"] for s in ctx["slides"]] == ["cover", "quote"]
    assert ctx["slides"][1]["notes"] == "n"


@pytest.mark.parametrize(
    ("fields", "needle"),
    [
        ({"blocks": [{"type": "marquee", "text": "x"}]}, "marquee"),
        ({"blocks": [{"type": "paragraph", "text": ""}]}, "blocks.0.paragraph.text"),
        (
            {"blocks": [{"type": "image", "src": "https://evil.example/a.png", "alt": "x"}]},
            "not a URL",
        ),
        ({"theme": "neon", "blocks": [{"type": "paragraph", "text": "x"}]}, "unknown theme"),
        ({"file_type": "pptx", "filename": "d.pptx", "slides": []}, "at least 1 item"),
        (
            {
                "file_type": "pptx",
                "filename": "d.pptx",
                "slides": [SlideSpec(layout="bullets", title="no bullets")],
            },
            "needs at least one bullet",
        ),
    ],
)
async def test_invalid_model_output_is_nonretryable_with_a_precise_message(
    tmp_path: Path, fields: dict[str, Any], needle: str
) -> None:
    support, _ = _support(_spec(**fields), tmp_path)
    with pytest.raises(NonRetryableSubtaskError) as err:
        await support.build_context(_job(), {"description": "x"})
    assert "invalid" in str(err.value) and needle in str(err.value)


async def test_empty_specification_is_still_a_runtime_error(tmp_path: Path) -> None:
    support, _ = _support(_spec(), tmp_path)
    with pytest.raises(RuntimeError, match="empty document specification"):
        await support.build_context(_job(), {"description": "x"})


def test_build_result_exposes_renderer_theme_and_warnings_in_the_stored_proof(
    tmp_path: Path,
) -> None:
    file = tmp_path / "a.pdf"
    file.write_bytes(b"%PDF-1.4")
    support = FleetSupport(gateway=None, artifacts_dir=tmp_path)
    result = SpecialistAgentResult(
        agent_role=SpecialistAgentRole.DOCUMENT,
        status=AgentExecutionStatus.SUCCESS,
        summary="ok",
        verification_proof={
            "filename": "a.pdf",
            "file_type": "pdf",
            "status": "verified",
            "renderer": "reportlab-fallback",
            "theme": "midnight",
            "warnings": ["Browser renderer unavailable"],
            "checks_passed": ["verified_1_pages"],
            "errors": [],
            "file_checksum": "ab" * 32,
            "size_bytes": 8,
        },
        artifact_paths=[str(file)],
    )
    built = support.build_result(_job(), result)
    report = built["proof"]["report"]
    assert report["renderer"] == "reportlab-fallback"
    assert report["theme"] == "midnight"
    assert report["warnings"] == ["Browser renderer unavailable"]
    assert built["proof"]["status"] == "verified"
