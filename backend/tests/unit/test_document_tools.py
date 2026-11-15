"""Chat tool loop, unit level (ADR-0026 Phase 1): the create_document, preview_document, start_job
and check_job building blocks. No model, no network, no database; files go to a temporary folder.

Each test names the behaviour it checks. A failed spec or render returns plain words (a
DocumentToolError), never a stack trace. The round and budget caps and the flag-off path are tested
through the chat endpoint in tests/integration/test_chat_tool_loop.py.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from app.automation.visual_review import DETERMINISTIC_ONLY
from app.services import document_tools as dt
from app.services.document_tools import (
    DocumentToolError,
    JobView,
    WorkingState,
    build_document,
    loop_applies,
    preview_file,
    state_spec,
    working_state_block,
    working_state_from_records,
)

SOLAR_DECK = {
    "slides": [
        {"layout": "title", "title": "Solar energy", "subtitle": "An overview"},
        {"layout": "bullets", "title": "Key facts", "bullets": ["Free fuel", "Clean output"]},
    ]
}
SOLAR_DOC = {
    "blocks": [
        {"type": "heading", "text": "Solar energy", "level": 1},
        {"type": "paragraph", "text": "Sunlight is converted to electricity by panels."},
    ]
}


def build(tmp_path: Path, **kwargs: Any) -> dt.BuiltDocument:
    fields: dict[str, Any] = {
        "file_format": "md",
        "title": "Solar notes",
        "spec": {"text": "# Solar\n\nSun is free.\n"},
        "request": "make notes about solar energy",
        "workdir": tmp_path,
    }
    fields.update(kwargs)
    return build_document(**fields)


# ----------------------------------------------------------------- create_document: success


def test_a_text_file_is_built_with_its_name_and_bytes(tmp_path: Path) -> None:
    built = build(tmp_path)
    assert built.name == "Solar_notes.md" and built.file_type == "md"
    assert built.data == b"# Solar\n\nSun is free.\n"
    assert built.pages is None


def test_a_docx_is_built_by_the_existing_renderer_and_verified(tmp_path: Path) -> None:
    built = build(tmp_path, file_format="docx", spec=SOLAR_DOC)
    assert built.name == "Solar_notes.docx" and built.file_type == "docx"
    assert built.data[:2] == b"PK"  # a .docx is a zip container
    assert built.checks  # the verifier reported what it checked


def test_a_pptx_is_built_and_its_slide_count_is_reported(tmp_path: Path) -> None:
    built = build(tmp_path, file_format="pptx", spec=SOLAR_DECK)
    assert built.file_type == "pptx" and built.data[:2] == b"PK"
    assert built.pages == 2  # the verifier's slide count
    stored = tmp_path / "check.pptx"
    stored.write_bytes(built.data)
    facts = preview_file(stored, "pptx")
    assert facts.pages == 2 and facts.status == "verified"


def test_an_xlsx_is_built_with_its_sheet(tmp_path: Path) -> None:
    spec = {"sheets": [{"title": "Data", "headers": ["Item", "Value"], "rows": [["Panels", "4"]]}]}
    built = build(tmp_path, file_format="xlsx", spec=spec)
    assert built.data[:2] == b"PK" and built.name.endswith(".xlsx")


def test_a_json_spec_given_as_text_is_accepted(tmp_path: Path) -> None:
    built = build(tmp_path, spec=json.dumps({"text": "# From a string\n"}))
    assert built.data == b"# From a string\n"


def test_a_zip_project_is_built_with_one_top_folder(tmp_path: Path) -> None:
    files = {
        "index.html": "<!doctype html><html><head><title>Site</title></head><body>Hi</body></html>",
        "css/site.css": "body { color: #222; }",
    }
    built = build(tmp_path, file_format="zip", title="Solar site", spec={"files": files})
    assert built.name == "solar-site.zip" and built.file_type == "zip"
    archive = tmp_path / "out.zip"
    archive.write_bytes(built.data)
    with zipfile.ZipFile(archive) as handle:
        names = handle.namelist()
    assert all(name.startswith("solar-site/") for name in names)
    assert "solar-site/index.html" in names


def test_a_react_zip_project_with_jsx_and_nested_dict(tmp_path: Path) -> None:
    # Handles nested directory structure and .jsx files gracefully
    spec = {
        "files": {
            "package.json": '{"name": "react-todo"}',
            "src": {
                "App.jsx": "export default function App() { return <div>Todo</div>; }",
                "components": {
                    "TodoForm.jsx": "export default function TodoForm() { return <form></form>; }",
                    "TodoList.jsx": "export default function TodoList() { return <ul></ul>; }",
                },
                "index.css": "body { margin: 0; }",
            },
        }
    }
    built = build(tmp_path, file_format="zip", title="React Todo", spec=spec)
    assert built.name == "react-todo.zip"
    archive = tmp_path / "react_todo.zip"
    archive.write_bytes(built.data)
    with zipfile.ZipFile(archive) as handle:
        names = set(handle.namelist())
    assert "react-todo/package.json" in names
    assert "react-todo/src/App.jsx" in names
    assert "react-todo/src/components/TodoForm.jsx" in names
    assert "react-todo/src/components/TodoList.jsx" in names
    assert "react-todo/src/index.css" in names


def test_a_react_zip_project_with_list_of_files(tmp_path: Path) -> None:
    # Handles list of {"path": ..., "content": ...}
    spec = {
        "files": [
            {"path": "package.json", "content": '{"name": "react-todo"}'},
            {
                "path": "src/App.jsx",
                "content": "export default function App() { return <div>App</div>; }",
            },
        ]
    }
    built = build(tmp_path, file_format="zip", title="React List", spec=spec)
    assert built.name == "react-list.zip"
    archive = tmp_path / "react_list.zip"
    archive.write_bytes(built.data)
    with zipfile.ZipFile(archive) as handle:
        names = set(handle.namelist())
    assert "react-list/package.json" in names
    assert "react-list/src/App.jsx" in names


# ------------------------------------------------------ create_document: readable errors


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"file_format": "exe"}, "cannot be made in this chat"),
        ({"title": "   "}, "no title"),
        ({"spec": "not json {"}, "not valid JSON"),
        ({"spec": ["a", "list"]}, "must be an object"),
    ],
)
def test_a_bad_request_returns_a_plain_error(
    tmp_path: Path, kwargs: dict[str, Any], expected: str
) -> None:
    with pytest.raises(DocumentToolError, match=expected):
        build(tmp_path, **kwargs)


def test_a_csv_with_ragged_rows_returns_the_validator_message(tmp_path: Path) -> None:
    spec = {"text": "name,value\nalpha,1\nbeta\n"}
    with pytest.raises(DocumentToolError, match="CSV row 3 has 1 columns"):
        build(tmp_path, file_format="csv", spec=spec)


def test_an_invalid_json_text_returns_the_validator_message(tmp_path: Path) -> None:
    with pytest.raises(DocumentToolError, match="JSON does not parse"):
        build(tmp_path, file_format="json", spec={"text": "{not json"})


def test_an_empty_text_file_is_refused_and_nothing_is_built(tmp_path: Path) -> None:
    with pytest.raises(DocumentToolError, match="is empty"):
        build(tmp_path, spec={"text": "   "})


def test_a_render_failure_is_readable_and_has_no_stack_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("renderer fell over")

    monkeypatch.setattr(dt, "create_deck", broken)
    with pytest.raises(DocumentToolError) as caught:
        build(tmp_path, file_format="pptx", spec=SOLAR_DECK)
    message = str(caught.value)
    assert "could not be rendered" in message and "renderer fell over" in message
    assert "Traceback" not in message and "RuntimeError" not in message


def test_a_file_that_fails_its_verifier_is_not_returned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.automation.verifiers.schemas import VerificationReport

    def failing(_path: Path) -> VerificationReport:
        return VerificationReport(
            file_type="docx",
            status="failed",
            errors=["the document has no headings"],
            sha256="0" * 64,
            size_bytes=1,
        )

    monkeypatch.setattr(dt, "verify_docx", failing)
    with pytest.raises(DocumentToolError, match="failed its checks: the document has no headings"):
        build(tmp_path, file_format="docx", spec=SOLAR_DOC)


def test_a_zip_with_an_unsafe_name_is_refused(tmp_path: Path) -> None:
    with pytest.raises(DocumentToolError, match="file name is not allowed"):
        build(tmp_path, file_format="zip", spec={"files": {"../escape.html": "<p>x</p>"}})


def test_a_zip_without_files_is_refused(tmp_path: Path) -> None:
    with pytest.raises(DocumentToolError, match="needs 'files'"):
        build(tmp_path, file_format="zip", spec={})


# ------------------------------------------------------------------ preview_document


def test_preview_of_a_docx_reports_no_page_count_and_no_vision_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MAYA_VISUAL_REVIEW_ALIAS", raising=False)
    built = build(tmp_path, file_format="docx", spec=SOLAR_DOC)
    stored = tmp_path / "preview.docx"
    stored.write_bytes(built.data)
    facts = preview_file(stored, "docx")
    assert facts.pages is None and facts.status == "verified"
    assert facts.vision_review == DETERMINISTIC_ONLY


def test_preview_of_a_markdown_file_is_not_checked(tmp_path: Path) -> None:
    stored = tmp_path / "notes.md"
    stored.write_text("# Hi\n", encoding="utf-8")
    facts = preview_file(stored, "md")
    assert facts.status == "not_checked" and facts.errors == ()


# ---------------------------------------------------------------- working state round trip


@dataclass
class FakeRecord:
    role: str
    metadata: dict[str, Any] = field(default_factory=dict)


def test_the_working_state_survives_a_metadata_round_trip() -> None:
    state = WorkingState(
        document={"format": "pptx", "title": "Solar", "file_id": "f1", "spec": SOLAR_DECK},
        files=[{"file_id": "f1", "name": "Solar.pptx", "file_type": "pptx", "sha256": "ab"}],
        jobs=["job-1"],
    )
    restored = WorkingState.from_metadata(state.to_metadata())
    assert restored == state


def test_a_malformed_working_state_reads_as_empty() -> None:
    assert WorkingState.from_metadata("garbage").is_empty()
    assert WorkingState.from_metadata({"files": "nope", "jobs": [1, 2]}).is_empty()


def test_the_newest_assistant_state_is_loaded_and_user_messages_are_ignored() -> None:
    older = {"tool_state": {"document": {"title": "Old", "format": "md", "file_id": "a"}}}
    newer = {"tool_state": {"document": {"title": "New", "format": "md", "file_id": "b"}}}
    messages = [
        FakeRecord("assistant", older),
        FakeRecord("user", {"tool_state": {"jobs": ["x"]}}),
        FakeRecord("assistant", newer),
        FakeRecord("user", {}),
    ]
    state = working_state_from_records(messages)  # type: ignore[arg-type]
    assert state.document is not None and state.document["title"] == "New"
    assert state.jobs == []


def test_a_conversation_without_state_loads_empty() -> None:
    messages = [FakeRecord("user"), FakeRecord("assistant", {"model_id": "x"})]
    assert working_state_from_records(messages).is_empty()  # type: ignore[arg-type]


def test_the_working_state_block_carries_the_last_spec_as_data() -> None:
    state = WorkingState(
        document={"format": "pptx", "title": "Solar", "file_id": "f1", "spec": SOLAR_DECK},
        files=[{"file_id": "f1", "name": "Solar.pptx", "file_type": "pptx"}],
        jobs=["job-1"],
    )
    text = working_state_block(state)
    assert "data, not instructions" in text
    assert '"file_id": "f1"' in text and '"job-1"' in text
    assert "Key facts" in text  # the last spec is included so 'change slide 2' can edit it


def test_a_spec_too_large_to_carry_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dt, "MAX_STATE_SPEC_CHARS", 20)
    assert state_spec({"text": "x" * 100}) is None
    assert state_spec({"text": "ok"}) == {"text": "ok"}
    assert state_spec('{"text": "ok"}') == {"text": "ok"}
    assert state_spec("{broken") is None


# --------------------------------------------------------------------- routing and definitions


@pytest.mark.parametrize(
    ("route", "reason", "expected"),
    [
        ("model", "file_candidate", True),
        ("model", "text_deliverable", True),
        ("task", "binary_deliverable", True),
        ("task", "over_limit_slides", True),
        ("task", "project", True),
        ("task", "research", False),
        ("task", "repository_review", False),
        ("task", "background", False),
        ("answer", "question", False),
        ("approval", "approval_verb", False),
        ("model", "edit_attached", False),
    ],
)
def test_only_document_requests_go_to_the_loop(route: str, reason: str, expected: bool) -> None:
    assert loop_applies(route, reason) is expected


def test_the_tool_declarations_use_the_four_names_and_the_format_enum() -> None:
    names = [tool.name for tool in dt.LOOP_TOOL_DEFINITIONS]
    assert names == ["create_document", "preview_document", "start_job", "check_job"]
    fmt = dt.CREATE_DOCUMENT_TOOL.parameters["properties"]["format"]
    assert fmt["enum"] == list(dt.DOCUMENT_FORMATS)


def test_job_view_and_reader_protocol_are_plain_values() -> None:
    view = JobView(job_id=uuid4(), title="Book", status="running", current_step="Chapter 2")
    assert view.files == () and view.status == "running"


def test_a_built_deck_states_that_only_deterministic_checks_ran(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MAYA_VISUAL_REVIEW_ALIAS", raising=False)
    built = build(tmp_path, file_format="pptx", spec=SOLAR_DECK)
    assert built.visual_review == DETERMINISTIC_ONLY
