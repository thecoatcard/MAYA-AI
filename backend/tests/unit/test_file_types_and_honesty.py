"""File-type choice, safe project zips, text deliverables and the honest research policy."""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from app.automation.filetype import ALL_TYPES, detect_file_type, needs_project
from app.automation.zipper import (
    MAX_ENTRY_BYTES,
    ZipSafetyError,
    build_project_zip,
    safe_entry_name,
    verify_zip,
)
from app.services.artifact_service import resolve_mime_type
from app.tasks.dispatch import (
    NOT_VERIFIED_LINE,
    RESEARCH_FAILED_MESSAGE,
    DocumentSpec,
    FleetSupport,
    _check_text_file,
    needs_web_facts,
    research_policy,
    stamp_text,
)
from app.tasks.queue import SubtaskJob
from app.tasks.worker import NonRetryableSubtaskError


@pytest.mark.parametrize(
    "request_text,expected",
    [
        ("make a vanilla web page for my bakery", "project"),
        ("build a website for my gym with css and js", "project"),
        ("create a single web page for my event", "html"),
        ("write a python script that renames files", "py"),
        ("export the contacts as a csv", "csv"),
        ("give me the config as json", "json"),
        ("write the README in markdown", "md"),
        ("a plain text packing list", "txt"),
        ("a 10 slide pitch deck for my startup", "pptx"),
        ("track my monthly budget in a spreadsheet", "xlsx"),
        ("a formal pdf report on churn", "pdf"),
        ("a Word document cover letter", "docx"),
        ("a sql query to find duplicate users", "sql"),
        ("tell me about solar panels", None),
    ],
)
def test_detect_file_type(request_text: str, expected: str | None) -> None:
    assert detect_file_type(request_text) == expected


def test_single_file_wins_and_project_helper() -> None:
    assert needs_project("a website")
    assert not needs_project("a website as one file")
    assert detect_file_type("a single-file website") == "html"
    assert {"md", "html", "py", "csv", "json"} <= set(ALL_TYPES)


# ----------------------------------------------------------------------------- zip
SITE = {
    "index.html": "<!doctype html><title>x</title>",
    "css/style.css": "body{}",
    "js/script.js": "1;",
    "README.md": "# hi",
}


def test_zip_contents_extensions_and_determinism(tmp_path: Path) -> None:
    a, b = tmp_path / "a.zip", tmp_path / "b.zip"
    names = build_project_zip(SITE, a, folder="my-site")
    build_project_zip(dict(reversed(list(SITE.items()))), b, folder="my-site")
    assert a.read_bytes() == b.read_bytes()  # sorted entries, fixed timestamps
    assert names == sorted(f"my-site/{n}" for n in SITE)
    with zipfile.ZipFile(a) as archive:
        assert archive.read("my-site/css/style.css") == b"body{}"
        assert {n.rpartition(".")[2] for n in archive.namelist()} == {"html", "css", "js", "md"}
    assert verify_zip(a) == []


def test_zip_allows_react_and_modern_project_extensions(tmp_path: Path) -> None:
    for ext in ("jsx", "tsx", "mjs", "yaml", "toml", "json"):
        assert safe_entry_name(f"src/file.{ext}") == f"src/file.{ext}"

    react_project = {
        "package.json": '{"name": "todo-app"}',
        "src/App.jsx": "export default function App() { return <div>Todo</div>; }",
        "src/components/TodoItem.jsx": (
            "export default function TodoItem() { return <li>item</li>; }"
        ),
        "src/index.css": "body { margin: 0; }",
    }
    out = tmp_path / "react_app.zip"
    names = build_project_zip(react_project, out, folder="react-app")
    assert len(names) == 4
    assert verify_zip(out) == []
    with zipfile.ZipFile(out) as archive:
        assert b"TodoItem" in archive.read("react-app/src/components/TodoItem.jsx")


@pytest.mark.parametrize(
    "name",
    [
        "../evil.html",
        "a/../../evil.html",
        "/etc/passwd.txt",
        "C:/x.html",
        "..\\evil.html",
        "a\\b.html",
        "",
        "con.html",
        "noext",
        "run.exe",
        "a/./b.html",
        "bad\x00.html",
    ],
)
def test_zip_slip_and_unsafe_names_are_rejected(name: str, tmp_path: Path) -> None:
    with pytest.raises(ZipSafetyError):
        safe_entry_name(name)
    target = tmp_path / "x.zip"
    with pytest.raises(ZipSafetyError):
        build_project_zip({"index.html": "x", name: "y"}, target, folder="p")
    assert not target.exists()  # nothing is written when a name is unsafe


def test_zip_size_and_count_caps_and_duplicates(tmp_path: Path) -> None:
    with pytest.raises(ZipSafetyError):
        build_project_zip({"index.html": "x" * (MAX_ENTRY_BYTES + 1)}, tmp_path / "a.zip")
    with pytest.raises(ZipSafetyError):
        build_project_zip({f"f{i}.txt": "x" for i in range(65)}, tmp_path / "b.zip")
    with pytest.raises(ZipSafetyError):
        build_project_zip({"A.html": "x", "a.html": "y"}, tmp_path / "c.zip")
    with pytest.raises(ZipSafetyError):
        build_project_zip({}, tmp_path / "d.zip")
    with pytest.raises(ZipSafetyError):
        build_project_zip({"index.html": "x"}, tmp_path / "e.zip", folder="../up")


def test_verify_zip_flags_a_hostile_archive(tmp_path: Path) -> None:
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr("../../evil.html", "x")
    assert any("traversal" in p for p in verify_zip(bad))
    (tmp_path / "n.zip").write_bytes(b"not a zip")
    assert verify_zip(tmp_path / "n.zip") == ["not a valid zip file"]


def test_zip_is_served_as_application_zip() -> None:
    assert resolve_mime_type("site.zip", "zip") == "application/zip"


# ----------------------------------------------------------------------------- text files
def test_text_file_validation() -> None:
    assert _check_text_file("py", "print(1)\n", "") == "print(1)\n"
    assert _check_text_file("json", '{"a": 1}', "")
    assert _check_text_file("csv", "a,b\n1,2\n", "")
    assert _check_text_file("html", "<!doctype html><title>x</title><p>hi</p>", "")
    for kind, bad in (
        ("py", "def ("),
        ("json", "{a:"),
        ("csv", "a,b\n1\n"),
        ("md", "   "),
        ("html", "<div><p>unclosed"),
    ):
        with pytest.raises(ValueError):
            _check_text_file(kind, bad, "")


# ----------------------------------------------------------------------------- research
def test_needs_web_facts_is_decided_in_one_place() -> None:
    assert needs_web_facts("latest smartphone prices in India")
    assert needs_web_facts("a report with citations on solar")
    assert needs_web_facts("market size of EVs in 2026")
    assert not needs_web_facts("study notes on photosynthesis")
    assert not needs_web_facts("a poem about rain")


def _ctx(**extra: Any) -> dict[str, Any]:
    return {"sources_unavailable": True, "dependency_outputs": {}, **extra}


def test_research_policy() -> None:
    cited = {"r": {"citations": [{"url": "https://a.example/x", "title": "A"}]}}
    assert research_policy("latest prices", {"dependency_outputs": cited}) == "ok"
    assert research_policy("latest prices", {"dependency_outputs": {}}) == "ok"  # not degraded
    assert research_policy("latest prices", _ctx()) == "fail"
    # Research failed for a request that claims no web facts: built from general knowledge, never
    # refused (this used to be "fail", which refused every ordinary topic).
    assert research_policy("notes on photosynthesis", _ctx()) == "general"
    assert research_policy("Angular notes from basic to advanced, detailed", _ctx()) == "general"
    assert research_policy("latest Java 25 features with sources", _ctx()) == "fail"
    # ...and the user may explicitly allow general knowledge (English or Hinglish).
    for phrase in (
        "notes on photosynthesis from general knowledge",
        "bina research ke notes banao",
        "apni knowledge se banao",
        "build it without web sources",
    ):
        assert research_policy(phrase, _ctx()) == "general", phrase
    assert research_policy("x", _ctx(requirements={"user_request": "from general knowledge"})) == (
        "general"
    )
    mine = {"attachment:1": {"agent": "attachment", "content": "my own text"}}
    assert research_policy("latest prices", _ctx(dependency_outputs=mine)) == "general"


class _Gateway:
    client = None

    def __init__(self, spec: DocumentSpec) -> None:
        self.spec = spec
        self.calls = 0

    async def generate_structured(self, **kwargs: Any) -> DocumentSpec:
        self.calls += 1
        return self.spec


def _job() -> SubtaskJob:
    return SubtaskJob(
        workspace_id=uuid4(),
        user_id=uuid4(),
        task_id=uuid4(),
        subtask_id=uuid4(),
        assigned_agent="document",
    )


async def test_failed_research_with_current_facts_fails_before_any_model_call(
    tmp_path: Path,
) -> None:
    spec = DocumentSpec(file_type="pdf", filename="r.pdf", title="R")
    gateway = _Gateway(spec)
    support = FleetSupport(gateway=gateway, artifacts_dir=tmp_path)  # type: ignore[arg-type]
    ctx = _ctx(description="PPT on the latest EV prices with sources", file_type="pptx")
    with pytest.raises(NonRetryableSubtaskError) as err:
        await support._document_context(_job(), ctx)
    assert RESEARCH_FAILED_MESSAGE in str(err.value)
    assert gateway.calls == 0


async def test_general_request_without_sources_is_built_and_not_stamped(tmp_path: Path) -> None:
    # A general request claims no web facts: the file builds and carries no "Not source-verified"
    # line (the stamp used to be applied to every request whose research failed).
    spec = DocumentSpec(
        file_type="md",
        filename="notes.md",
        title="Photosynthesis",
        text="# Photosynthesis\n\n- Plants make sugar from light.\n",
    )
    support = FleetSupport(gateway=_Gateway(spec), artifacts_dir=tmp_path)  # type: ignore[arg-type]
    out = await support._document_context(
        _job(), _ctx(description="study notes on photosynthesis from general knowledge")
    )
    assert out["file_type"] == "md" and out["filename"].endswith(".md")
    assert NOT_VERIFIED_LINE not in out["content"]


async def test_web_claim_built_from_general_knowledge_is_stamped(tmp_path: Path) -> None:
    # The user allowed general knowledge but asked for current facts: the file claims web facts
    # that no source backs, so it is stamped.
    spec = DocumentSpec(
        file_type="md",
        filename="notes.md",
        title="Photosynthesis",
        text="# Photosynthesis\n\n- Plants make sugar from light.\n",
    )
    support = FleetSupport(gateway=_Gateway(spec), artifacts_dir=tmp_path)  # type: ignore[arg-type]
    out = await support._document_context(
        _job(),
        _ctx(description="latest photosynthesis facts, from general knowledge"),
    )
    assert NOT_VERIFIED_LINE in out["content"]
    assert out["content"].startswith("> ")


async def test_angular_notes_build_when_research_failed(tmp_path: Path) -> None:
    # The owner's case: detailed notes on a general topic, research failed, nothing is refused.
    spec = DocumentSpec(
        file_type="md",
        filename="angular.md",
        title="Angular",
        text="# Angular\n\n## Basics\n\n- Components.\n",
    )
    gateway = _Gateway(spec)
    support = FleetSupport(gateway=gateway, artifacts_dir=tmp_path)  # type: ignore[arg-type]
    request = (
        "make a beautiful notes on Angular from basic to advanced, make sure notes are detailed"
    )
    out = await support._document_context(
        _job(),
        _ctx(
            description=request,
            requirements={"user_request": request},
            missing_dependencies=["step_1"],
            dependency_outputs={"step_1": {"missing": True, "reason": "no sources"}},
        ),
    )
    assert out["file_type"] == "md"
    assert gateway.calls == 1
    assert NOT_VERIFIED_LINE not in out["content"]


async def test_html_request_gives_html_not_docx(tmp_path: Path) -> None:
    page = "<!doctype html><html><body><h1>Hi</h1></body></html>"
    spec = DocumentSpec(file_type="html", filename="page.html", title="Hi", text=page)
    support = FleetSupport(gateway=_Gateway(spec), artifacts_dir=tmp_path)  # type: ignore[arg-type]
    out = await support._document_context(_job(), {"description": "a single web page for my cafe"})
    assert out["file_type"] == "html" and out["filename"].endswith(".html")
    assert out["content"] == page


async def test_docx_for_a_python_request_is_repaired_to_py(tmp_path: Path) -> None:
    wrong = DocumentSpec.model_validate(
        {
            "file_type": "docx",
            "filename": "s.docx",
            "title": "S",
            "blocks": [{"type": "paragraph", "text": "print(1)"}],
        }
    )
    right = DocumentSpec(file_type="py", filename="s.py", title="S", text="print(1)\n")

    class _Seq(_Gateway):
        async def generate_structured(self, **kwargs: Any) -> DocumentSpec:
            self.calls += 1
            return wrong if self.calls == 1 else right

    support = FleetSupport(gateway=_Seq(wrong), artifacts_dir=tmp_path)  # type: ignore[arg-type]
    out = await support._document_context(
        _job(), {"description": "write a python script that prints one"}
    )
    assert out["file_type"] == "py" and out["filename"].endswith(".py")


def test_stamp_text_by_format() -> None:
    line = "Not source-verified."
    html = stamp_text("<html><body><p>x</p></body></html>", line)
    assert html.index(line) > html.index("<body>")
    assert stamp_text("# T\n\ntext", line).startswith("> ")
    assert stamp_text("plain words", line).startswith(line)
    assert stamp_text(f"{line}\n\nx", line).count(line) == 1


async def test_failed_research_builds_an_ordinary_topic_instead_of_refusing(
    tmp_path: Path,
) -> None:
    # Failed research for a request that claims no web facts builds from general knowledge. It
    # used to be refused with "build from general knowledge", which the owner called the bug.
    spec = DocumentSpec(file_type="md", filename="n.md", title="Java", text="# Java\n\n- x\n")
    support = FleetSupport(gateway=_Gateway(spec), artifacts_dir=tmp_path)  # type: ignore[arg-type]
    ctx = _ctx(description="Create a 10-slide Advanced Java deck", sources_unavailable=True)
    out = await support._document_context(_job(), ctx)
    assert out["file_type"] == "md"
    assert NOT_VERIFIED_LINE not in out["content"]
    # No research dependency at all: unchanged, the deck is built.
    out = await support._document_context(
        _job(), {"description": "Create a 10-slide Advanced Java deck"}
    )
    assert out["file_type"] == "md"


async def test_hinglish_general_request_builds_when_research_failed(tmp_path: Path) -> None:
    spec = DocumentSpec(file_type="md", filename="java.md", title="Java", text="# Java\n\n- x\n")
    support = FleetSupport(gateway=_Gateway(spec), artifacts_dir=tmp_path)  # type: ignore[arg-type]
    out = await support._document_context(_job(), _ctx(description="Java ke notes banao"))
    assert out["file_type"] == "md"
    assert NOT_VERIFIED_LINE not in out["content"]


def test_depth_rule_for_detailed_requests_only_without_a_stated_length() -> None:
    from app.tasks.dispatch import DEPTH_RULE, depth_rule

    angular = (
        "make a beautiful notes on Angular from basic to advanced, make sure notes are detailed"
    )
    assert depth_rule(angular, {"requirements": {"user_request": angular}}, {}) == DEPTH_RULE
    assert depth_rule("Java ke vistaar se notes", {}, {}) == DEPTH_RULE
    assert depth_rule("notes on Angular", {}, {}) == ""
    # A stated length keeps its own rule; the depth line is not added on top of it.
    assert depth_rule(angular, {}, {"pages": 5}) == ""


def test_the_no_sources_prompt_rule_applies_only_to_web_claims() -> None:
    from app.tasks.dispatch import unsourced_rules_apply

    assert not unsourced_rules_apply({"description": "Angular notes", "sources_unavailable": True})
    assert unsourced_rules_apply(
        {"description": "latest Angular release", "sources_unavailable": True}
    )
    assert not unsourced_rules_apply({"description": "latest Angular release"})


def test_the_task_warning_is_only_for_requests_that_claim_web_facts() -> None:
    from types import SimpleNamespace

    from app.agents.unsourced import UNSOURCED_WARNING
    from app.tasks.worker import _unsourced_warnings

    def steps(description: str) -> list[Any]:
        research = SimpleNamespace(
            assigned_agent="research",
            status="failed",
            description="search",
            input_data={},
            verification_proof=None,
            output_result=None,
        )
        document = SimpleNamespace(
            assigned_agent="document",
            status="done",
            description=description,
            input_data={"requirements": {"user_request": description}},
            verification_proof=None,
            output_result=None,
        )
        return [research, document]

    assert _unsourced_warnings(steps("Angular notes from basic to advanced, detailed")) == []
    assert _unsourced_warnings(steps("latest Java 25 features with sources")) == [UNSOURCED_WARNING]
