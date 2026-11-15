"""Attached files as task sources (ADR-0022): the upload text reaches the document step, a failed
research step does not block a request about the file, an edit works on the upload id, and an
unreadable file fails with a message that names it. Real small docx/txt/png files in temp
directories; a fake gateway captures every prompt. No network, no database."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from app.agents.plan_repair import attached_source_repairs
from app.agents.state import SubTask
from app.files.attachments import attachment_dependency_outputs
from app.files.edit_step import DocxEditItem, DocxEditPlan, run_file_edit_step
from app.tasks.dispatch import (
    NOT_VERIFIED_LINE,
    DocumentSpec,
    FleetSupport,
    SlideSpec,
    _dependency_digest,
    research_policy,
)
from app.tasks.queue import SubtaskJob
from app.tasks.worker import NonRetryableSubtaskError
from tests.support.files import (
    USER,
    WORKSPACE,
    make_docx,
    make_job,
)

BUDGET = "Budget for the launch: 5000 euros for travel and 1200 euros for print."
HINDI = "Yeh budget ka saaransh hai: travel ke liye paanch hazaar rupaye, print ke liye baarah sau."


class CapturingGateway:
    """Returns one fixed answer and keeps every prompt it was sent."""

    client = None

    def __init__(self, answer: Any) -> None:
        self.answer = answer
        self.prompts: list[str] = []

    async def generate_structured(self, **kwargs: Any) -> Any:
        self.prompts.append(str(kwargs["contents"][0]))
        return self.answer


def _job() -> SubtaskJob:
    return SubtaskJob(
        workspace_id=WORKSPACE,
        user_id=USER,
        task_id=uuid4(),
        subtask_id=uuid4(),
        assigned_agent="document",
    )


def _deck(title: str = "Launch budget") -> DocumentSpec:
    return DocumentSpec(
        file_type="pptx",
        filename="launch.pptx",
        title=title,
        slides=[SlideSpec(layout="bullets", title=title, bullets=["Travel 5000 EUR"])],
    )


def _step_ctx(
    description: str, attached: dict[str, Any], *, research_failed: bool = True, **extra: Any
) -> dict[str, Any]:
    """The context a document step gets when its research dependency failed (as the worker
    builds it: attachment outputs merged into the dependency outputs)."""
    deps: dict[str, Any] = dict(attached)
    missing: list[str] = []
    if research_failed:
        deps["step_1"] = {"missing": True, "reason": "no search results"}
        missing = ["step_1"]
    return {
        "description": description,
        "requirements": {"user_request": description},
        "dependency_outputs": deps,
        "missing_dependencies": missing,
        "sources_unavailable": research_failed,
        **extra,
    }


def _upload(services: Any, name: str, data: bytes) -> str:
    saved: str = services.uploads.save(WORKSPACE, USER, name, data).id
    return saved


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _first_paragraph(path: Path) -> str:
    from docx import Document

    return str(Document(str(path)).paragraphs[0].text)


# --------------------------------------------------------------- the attachment as a source


async def test_attachment_output_carries_the_text_and_a_named_reason(
    file_env_no_folder: Any, tmp_path: Path
) -> None:
    services = file_env_no_folder
    docx = make_docx(tmp_path / "budget.docx", [BUDGET])
    upload = _upload(services, "budget.docx", docx.read_bytes())
    png = _upload(services, "photo.png", b"\x89PNG\r\n\x1a\n" + b"0" * 40)

    outputs = await attachment_dependency_outputs(WORKSPACE, [upload, png, str(uuid4())])
    by_name = {v["name"]: v for v in outputs.values() if v.get("name")}
    assert by_name["budget.docx"]["content"].startswith("Budget for the launch")
    assert by_name["budget.docx"]["readable"] is True
    # An unreadable file has no content (so it is never mistaken for the user's material) and
    # says why.
    assert by_name["photo.png"]["content"] == "" and by_name["photo.png"]["readable"] is False
    assert "cannot read the pixels" in by_name["photo.png"]["note"]
    # An id that is not in the workspace is reported, not silently dropped.
    missing = [v for v in outputs.values() if not v.get("name")]
    assert len(missing) == 1 and missing[0]["readable"] is False


def test_digest_keeps_the_attached_text_whole_even_with_a_long_research_output(
    tmp_path: Path,
) -> None:
    attached = {
        "attachment:abc12345": {
            "name": "budget.txt",
            "summary": "Attached file 'budget.txt' (text)",
            "content": HINDI * 60,  # non-ASCII text, longer than the step's JSON budget
            "readable": True,
            "agent": "attachment",
        }
    }
    research = {"step_1": {"summary": "x" * 30_000, "citations": []}}
    digest = _dependency_digest({"dependency_outputs": {**research, **attached}})
    assert HINDI * 60 in digest  # not escaped to \u sequences, not cut off


def test_the_research_step_is_removed_and_the_document_is_the_attachment_step() -> None:
    """Planner output with a research step for a request about a provided file: research is not
    the source and not critical; the document step keeps the file as its input."""
    nodes = [
        SubTask(
            id="step_1",
            assigned_agent="research",
            description="Research budget facts",
            dependencies=[],
            input_data={"critical": True},
        ),
        SubTask(
            id="step_2",
            assigned_agent="document",
            description="make ppt of this",
            dependencies=["step_1"],
            input_data={"critical": True},
        ),
    ]
    kept = attached_source_repairs(nodes, "make ppt of this", ["upload-1"])
    assert [n.id for n in kept] == ["step_2"]
    assert kept[0].dependencies == []  # no edge to the removed research step


def test_a_request_that_needs_web_facts_keeps_research_as_optional(tmp_path: Path) -> None:
    nodes = [
        SubTask(
            id="step_1",
            assigned_agent="research",
            description="latest EV prices",
            dependencies=[],
            input_data={"critical": True},
        ),
        SubTask(
            id="step_2",
            assigned_agent="document",
            description="make ppt of this with the latest EV prices",
            dependencies=["step_1"],
            input_data={"critical": True},
        ),
    ]
    kept = attached_source_repairs(nodes, "make ppt of this with the latest EV prices", ["u"])
    assert [n.id for n in kept] == ["step_1", "step_2"]
    assert kept[0].input_data["critical"] is False  # a failed search cannot fail the task
    assert kept[1].dependencies == ["step_1"]


def test_an_edit_request_with_only_research_gets_a_file_edit_step() -> None:
    nodes = [
        SubTask(
            id="step_1",
            assigned_agent="research",
            description="budget 5000 se 7500 kar do",
            dependencies=[],
            input_data={"critical": True},
        )
    ]
    kept = attached_source_repairs(nodes, "budget 5000 se 7500 kar do", ["upload-1"])
    assert len(kept) == 1
    assert kept[0].assigned_agent == "file"
    assert kept[0].input_data["executor"] == "file_edit"
    assert kept[0].dependencies == []  # the planned research step it replaces is gone


def test_without_attachments_the_plan_is_unchanged() -> None:
    nodes = [
        SubTask(
            id="step_1",
            assigned_agent="research",
            description="latest EV prices",
            dependencies=[],
            input_data={"critical": True},
        )
    ]
    assert attached_source_repairs(nodes, "latest EV prices", []) is nodes
    assert nodes[0].input_data["critical"] is True


# ------------------------------------------------------------- the research bypass (the case)


@pytest.mark.parametrize("kind", ["txt", "docx"])
async def test_failed_research_does_not_stop_a_deck_built_from_the_attached_text(
    file_env_no_folder: Any, tmp_path: Path, kind: str
) -> None:
    services = file_env_no_folder
    if kind == "docx":
        data = make_docx(tmp_path / "budget.docx", [BUDGET]).read_bytes()
    else:
        data = BUDGET.encode()
    upload = _upload(services, f"budget.{kind}", data)
    attached = await attachment_dependency_outputs(WORKSPACE, [upload])
    assert BUDGET in str(next(iter(attached.values()))["content"])
    ctx = _step_ctx("make ppt of this", attached, file_type="pptx")
    gateway = CapturingGateway(_deck())
    support = FleetSupport(gateway=gateway, artifacts_dir=tmp_path)  # type: ignore[arg-type]

    out = await support._document_context(_job(), ctx)  # no refusal

    assert out["file_type"] == "pptx"
    assert gateway.prompts, "the document step must call the model"
    assert BUDGET in gateway.prompts[0]  # the builder saw the user's own text
    assert "Research could not fetch sources" not in gateway.prompts[0]
    # Nothing is claimed about the web, so nothing is stamped "Not source-verified".
    assert NOT_VERIFIED_LINE not in json.dumps(out, default=str)


def test_research_policy_for_attached_material() -> None:
    attached = {"attachment:a": {"agent": "attachment", "content": BUDGET, "readable": True}}
    ctx = _step_ctx("make ppt of this", attached)
    assert research_policy("make ppt of this", ctx) == "ok"
    assert research_policy("latest EV prices from this file", ctx) == "general"


async def test_a_web_fact_request_with_attached_text_is_stamped_not_refused(
    file_env_no_folder: Any, tmp_path: Path
) -> None:
    services = file_env_no_folder
    upload = _upload(services, "budget.txt", BUDGET.encode())
    attached = await attachment_dependency_outputs(WORKSPACE, [upload])
    ctx = _step_ctx("make ppt of this with the latest EV prices", attached, file_type="pptx")
    spec = DocumentSpec(
        file_type="md",
        filename="notes.md",
        title="EV",
        text="# EV\n\n- Travel 5000 EUR.\n",
    )
    support = FleetSupport(gateway=CapturingGateway(spec), artifacts_dir=tmp_path)  # type: ignore[arg-type]
    out = await support._document_context(_job(), ctx)
    assert NOT_VERIFIED_LINE in out["content"]


async def test_an_unreadable_attachment_fails_the_step_and_names_the_file(tmp_path: Path) -> None:
    attached = {
        "attachment:deadbeef": {
            "agent": "attachment",
            "name": "scan.pdf",
            "content": "",
            "readable": False,
            "note": "This PDF has no extractable text (it is probably a scan or images).",
        }
    }
    gateway = CapturingGateway(_deck())
    support = FleetSupport(gateway=gateway, artifacts_dir=tmp_path)  # type: ignore[arg-type]
    with pytest.raises(NonRetryableSubtaskError) as err:
        await support._document_context(_job(), _step_ctx("make ppt of this", attached))
    message = str(err.value)
    assert "scan.pdf" in message and "no extractable text" in message
    assert gateway.prompts == []  # refused before any model call


# ------------------------------------------------------------------- edits on the upload id


async def test_an_attached_docx_is_edited_by_upload_id_and_the_original_is_untouched(
    file_env_no_folder: Any, tmp_path: Path
) -> None:
    services = file_env_no_folder
    data = make_docx(tmp_path / "b.docx", ["Budget: 5000"]).read_bytes()
    upload = _upload(services, "budget.docx", data)
    original = services.uploads.path_of(services.uploads.get(WORKSPACE, upload))
    before = await asyncio.to_thread(_sha, original)
    gateway = CapturingGateway(
        DocxEditPlan(edits=[DocxEditItem(paragraph_index=0, new_text="Budget: 7500")])
    )

    output = await run_file_edit_step(
        gateway,
        make_job(),
        {"input_files": [upload], "description": "budget 5000 se 7500 kar do"},
    )

    assert await asyncio.to_thread(_sha, original) == before  # the original upload is untouched
    new_version = Path(output["proof"]["storage_uri"])
    assert new_version != original and await asyncio.to_thread(new_version.is_file)
    assert output["result"]["original"] == "budget.docx"
    assert output["result"]["saved_in_folder"] is False
    assert "Budget: 5000" in gateway.prompts[0]  # the model saw the attached text
    assert "Budget: 7500" in await asyncio.to_thread(_first_paragraph, new_version)


async def test_an_attached_image_edit_names_the_file_and_says_why(
    file_env_no_folder: Any,
) -> None:
    services = file_env_no_folder
    upload = _upload(services, "photo.png", b"\x89PNG\r\n\x1a\n" + b"0" * 40)
    request = {"input_files": [upload], "description": "edit"}
    with pytest.raises(NonRetryableSubtaskError) as err:
        await run_file_edit_step(CapturingGateway(DocxEditPlan()), make_job(), request)
    assert "photo.png" in str(err.value) and "image" in str(err.value)


async def test_a_folder_file_is_still_edited_by_name(file_env: Any) -> None:
    from app.files.edit_step import TextEditPlan

    _services, folder = file_env
    (folder / "plan.txt").write_text("budget 5000\n")
    gateway = CapturingGateway(TextEditPlan(new_content="budget 7500\n"))
    output = await run_file_edit_step(
        gateway, make_job(), {"path": "plan.txt", "description": "budget 5000 se 7500 kar do"}
    )
    # ADR-0022: an edit updates the file in place and keeps the previous version as a backup.
    assert (folder / "plan.txt").read_text() == "budget 7500\n"
    backup = output["result"]["backup"]
    assert backup is not None
    assert await asyncio.to_thread(Path(backup).read_text) == "budget 5000\n"
    assert output["result"]["original"] == "plan.txt"
    assert output["result"]["saved_in_folder"] is True
