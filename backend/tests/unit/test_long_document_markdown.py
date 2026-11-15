"""Long notes as "a small PLAN, then Markdown CONTENT" (ADR-0020), offline with fake models.

* the plan call is small structured JSON (goal, must-cover list, needs_code, needs_table);
* every chapter is one TEXT call answered as Markdown, parsed deterministically; a chapter that
  fails is asked again once with the problem named, then falls back to the old structured call;
* chapter model calls are bounded by ``CHAPTER_CONCURRENCY``;
* chat: up to 5 pages ONE call (``spec.markdown``), 6 and above a background job;
* a 10-page run through the REAL browser renderer (skipped without Edge or Chrome).
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfReader
from reportlab.pdfgen import canvas

from app.automation.acceptance import pages_bounds, words_floor
from app.automation.browser_pdf import shutdown_browser_renderer
from app.automation.long_document import ChapterContent
from app.automation.pdf_engine import PdfRenderResult
from app.services import document_tools
from app.services.document_tools import (
    DocumentToolError,
    build_document,
    long_route,
)
from app.tasks import long_document as ld
from app.tasks.pipeline import DRAFT_KEY
from app.tasks.worker import SubtaskExecutionError
from tests.support.documents import requires_browser
from tests.support.longdoc import FakeLongDocGateway
from tests.unit.test_long_document_pipeline import Run
from tests.unit.test_page_fit import StepPdf, use_renderer

pytestmark = pytest.mark.usefixtures("nine_chapters")

RAGGED_TABLE = (
    "\n\n| Concept | Meaning | Example |\n|---|---|---|\n| stream | a pipeline |\n"
    "| lambda | an anonymous function | `x -> x` | surplus cell |\n"
)


class MarkdownGateway(FakeLongDocGateway):
    """Answers chapters as messy but realistic Markdown: a ragged table, an H1 title the prompt
    forbade, and the whole answer wrapped in a ```markdown fence."""

    async def generate_text(self, **kwargs: Any) -> Any:
        response = await super().generate_text(**kwargs)
        title = "Streams"
        text = f"# {title}\n\n" + response.text.replace(
            "\n## Key takeaways", RAGGED_TABLE + "\n## Key takeaways", 1
        )
        response.text = "```markdown\n" + text + "\n```"
        return response


# --------------------------------------------------------------------------- the plan call
async def test_the_plan_is_small_json_with_goal_must_cover_and_needs(tmp_path: Path) -> None:
    run = Run(tmp_path, FakeLongDocGateway(chapters=3), pages=10, form="notes")
    outline = (await ld.run_outline(run.gateway, run.job, run.nodes[0].input_data))["result"]  # type: ignore[arg-type]
    chapter = outline["outline"]["chapters"][0]
    assert {"goal", "needs_code", "needs_table", "subtopics", "target_words"} <= set(chapter)
    assert outline["outline"]["language"] == "" and outline["outline"]["cover_page"] is None
    assert run.gateway.calls == ["outline"]  # one call; the plan carries no prose


async def test_the_chapter_prompt_states_budget_depth_rules_and_the_plan_fields(
    tmp_path: Path,
) -> None:
    seen: list[str] = []

    class Spy(FakeLongDocGateway):
        async def generate_text(self, **kwargs: Any) -> Any:
            seen.append(str(kwargs["contents"][0]))
            seen.append(str(kwargs["system_instruction"]))
            return await super().generate_text(**kwargs)

    run = Run(tmp_path, Spy(chapters=3), pages=10, form="notes")
    outline = (await ld.run_outline(run.gateway, run.job, run.nodes[0].input_data))["result"]  # type: ignore[arg-type]
    outline["outline"]["language"] = "Hinglish"
    outline["outline"]["chapters"][0].update(
        goal="Write a stream pipeline", needs_code=True, needs_table=True
    )
    node = {**run.nodes[1].input_data, "dependency_outputs": {"outline": outline}}
    await ld.run_chapter(run.gateway, run.job, node)  # type: ignore[arg-type]
    user, system = seen[0], seen[1]
    assert "Write a stream pipeline" in user and "Must cover, in order" in user
    assert "runnable code examples" in user and "comparison or reference table" in user
    assert "written in Hinglish" in user
    assert "words of prose-equivalent" in user and "at least" in user
    assert "```java" in system and "> [!NOTE]" in system and "## Key takeaways" in system
    assert "filler" in system and "edge cases" in system  # the depth rules


# --------------------------------------------------------------------------- chapters as Markdown
async def test_messy_markdown_chapters_become_blocks_without_failing(tmp_path: Path) -> None:
    run = Run(tmp_path, MarkdownGateway(chapters=3), pages=10, form="notes")
    deps = await run.stages()
    for node_id in ("chapter_1", "chapter_2", "chapter_3"):
        draft = deps[node_id][DRAFT_KEY]
        chapter = ChapterContent.from_dict(draft)
        kinds = {b["type"] for b in chapter.blocks}
        assert {"heading", "paragraph", "code", "table", "callout"} <= kinds
        assert chapter.blocks[0]["type"] == "paragraph"  # the forbidden H1 title was dropped
        ragged = next(
            b for b in chapter.blocks if b["type"] == "table" and "stream" in str(b["rows"])
        )
        assert all(len(row) == len(ragged["headers"]) for row in ragged["rows"])  # repaired
        assert len(chapter.takeaways) == 4 and len(chapter.exercises) == 5
        assert all(b["language"] == "java" for b in chapter.blocks if b["type"] == "code")
    assert len(run.gateway.calls) == 4  # the plan + one call per chapter, nothing else


async def test_a_failed_chapter_is_asked_again_once_with_the_problem_named(
    tmp_path: Path,
) -> None:
    prompts: list[str] = []

    class FirstAnswerEmpty(FakeLongDocGateway):
        async def generate_text(self, **kwargs: Any) -> Any:
            prompt = str(kwargs["contents"][0])
            prompts.append(prompt)
            if "Write chapter 2 of" in prompt and prompt.count("not usable") == 0:
                return type("R", (), {"text": "Sorry, I cannot do that."})()
            return await super().generate_text(**kwargs)

    run = Run(tmp_path, FirstAnswerEmpty(chapters=3), pages=10, form="notes")
    deps = await run.stages()
    assert DRAFT_KEY in deps["chapter_2"]  # the second answer was good
    second = [p for p in prompts if "Write chapter 2 of" in p]
    assert len(second) == 2
    assert "not usable" in second[1] and "came back empty or far too short" in second[1]
    assert run.gateway.calls.count("chapter:1") == 1  # the others were not asked twice


async def test_when_markdown_stays_too_thin_the_json_chapter_call_is_the_last_fallback(
    tmp_path: Path,
) -> None:
    class NoMarkdown(FakeLongDocGateway):
        async def generate_text(self, **kwargs: Any) -> Any:
            self.calls.append("markdown")
            return type("R", (), {"text": "..."})()

    gateway = NoMarkdown(chapters=3)
    run = Run(tmp_path, gateway, pages=10, form="notes")
    outline = (await ld.run_outline(gateway, run.job, run.nodes[0].input_data))["result"]  # type: ignore[arg-type]
    node = {**run.nodes[1].input_data, "dependency_outputs": {"outline": outline}}
    result = await ld.run_chapter(gateway, run.job, node)  # type: ignore[arg-type]
    assert gateway.calls[1:] == ["markdown", "markdown", "chapter:1"]  # markdown x2, then JSON
    assert result["result"][DRAFT_KEY]["words"] > 200  # a full chapter came from the fallback


async def test_a_chapter_that_cannot_be_written_is_reported_never_dropped_silently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway = FakeLongDocGateway(chapters=3, always_fail_chapters={2})
    run = Run(tmp_path, gateway, pages=10, form="notes")
    outline = (await ld.run_outline(gateway, run.job, run.nodes[0].input_data))["result"]  # type: ignore[arg-type]
    node = {**run.nodes[2].input_data, "dependency_outputs": {"outline": outline}}
    with pytest.raises(SubtaskExecutionError, match="Chapter model call failed"):
        await ld.run_chapter(gateway, run.job, node)  # type: ignore[arg-type]
    assert gateway.chapter_attempts[2] == 2  # one try and ONE retry, never a loop

    deps = await run.stages(skip={2})  # the worker marks it failed; assembly goes on without it
    ctx = {
        **run.nodes[-1].input_data,
        "description": run.nodes[-1].description,
        "dependency_outputs": deps,
    }
    built = await run.support.build_context(run.job, ctx)
    note = next(b for b in built["blocks"] if str(b.get("text", "")).startswith("Not included:"))
    assert note["type"] == "callout" and "Chapter 2" in note["text"]
    assert any("Chapter 2" in w for w in built["spec_warnings"])  # partial / failed_steps source


async def test_chapter_model_calls_never_exceed_the_concurrency_bound(tmp_path: Path) -> None:
    gateway = FakeLongDocGateway(chapters=9)
    run = Run(tmp_path, gateway, pages=50)  # nine chapters started at once by the worker pool
    await run.stages()
    assert 1 < gateway.max_active <= ld.CHAPTER_CONCURRENCY == 3


async def test_an_expansion_is_one_markdown_call_and_never_a_second(tmp_path: Path) -> None:
    gateway = FakeLongDocGateway(chapters=3, yield_factor=0.4, expand_factor=1.0)
    run = Run(tmp_path, gateway, pages=10, form="notes")
    outline = (await ld.run_outline(gateway, run.job, run.nodes[0].input_data))["result"]  # type: ignore[arg-type]
    node = {**run.nodes[1].input_data, "dependency_outputs": {"outline": outline}}
    result = await ld.run_chapter(gateway, run.job, node)  # type: ignore[arg-type]
    assert gateway.calls.count("expand:1") == 1 and result["result"]["chapter"]["expanded"]


def test_chapter_from_markdown_lifts_takeaways_and_exercises_and_respects_the_flags() -> None:
    brief = {"index": 1, "title": "Streams", "target_pages": 3, "target_words": 900}
    body = "Intro " * 40 + "\n\n## Part\n\n" + "More text here. " * 30
    text = body + "\n\n## Key takeaways\n\n- a\n- b\n\n## Exercises\n\n1. one\n2. two\n"
    full = ld.chapter_from_markdown(
        text, brief, {"include_exercises": True, "include_summary": True}
    )
    assert full.takeaways == ["a", "b"] and full.exercises == ["one", "two"]
    assert not any("Key takeaways" in str(b) for b in full.blocks)
    plain = ld.chapter_from_markdown(
        text, brief, {"include_exercises": False, "include_summary": False}
    )
    assert plain.takeaways == [] and plain.exercises == []
    with pytest.raises(ld.ChapterTooThinError, match="empty or far too short"):
        ld.chapter_from_markdown("## only a heading", brief, {})


# --------------------------------------------------------------------------- the real renderer
@requires_browser
async def test_plan_plus_three_markdown_chapters_make_a_ten_page_pdf_in_the_real_browser(
    tmp_path: Path,
) -> None:
    try:
        probe = Run(tmp_path, FakeLongDocGateway(chapters=3), pages=10, form="notes")
        gateway = MarkdownGateway(chapters=probe.plan.chapters)
        run = Run(tmp_path, gateway, pages=10, form="notes")
        result = await run.assemble(await run.stages())
        assert result.status.value == "success", result.summary
        path = Path(result.artifact_paths[0])
        pages = len(PdfReader(str(path)).pages)
        low, high = pages_bounds(10)
        assert low <= pages <= high, pages
        words = sum(len((p.extract_text() or "").split()) for p in PdfReader(str(path)).pages)
        assert words >= words_floor(10)
        text = " ".join(p.extract_text() or "" for p in PdfReader(str(path)).pages)
        lowered = text.lower()
        assert "java" in lowered and "contents" in lowered  # healing may trim the closing sections
        # one plan call and one call per chapter (3): four model calls in total
        assert len(gateway.calls) == 1 + probe.plan.chapters
        print(f"\nMD 10-page: pages={pages} words={words} calls={len(gateway.calls)}")
    finally:
        shutdown_browser_renderer()


# --------------------------------------------------------------------------- chat: one call vs job
def test_up_to_five_pages_stay_one_call_and_six_and_above_are_a_job() -> None:
    assert long_route("pdf", None, "make a 5 page java stream notes in a pdf") is None
    route = long_route("pdf", None, "make a 6 page java stream notes in a pdf")
    assert route is not None and route.pages == 6 and route.chapters >= 3
    assert long_route("docx", None, "10 pages java notes word document") is not None
    assert long_route("pdf", None, "6 pages notes", "file-1") is None  # an edit is never routed
    assert long_route("pdf", 9, "java notes") is None  # the model's own count is a plan only


JAVA_MD = (
    "## Streams\n\nJava **streams** process data lazily.\n\n```java\nlist.stream().map(x -> x);\n"
    "```\n\n| Op | Kind |\n|---|---|\n| map | intermediate |\n| filter |\n\n"
    "> [!TIP] Prefer `toList()`.\n\n- one\n  - nested\n\n## Key takeaways\n\n- lazy\n"
)


def _build(tmp_path: Path, spec: dict[str, Any], **kw: Any) -> document_tools.BuiltDocument:
    work = tmp_path / f"w{len(list(tmp_path.iterdir()))}"
    work.mkdir()
    return build_document(
        file_format=kw.pop("file_format", "pdf"),
        title=kw.pop("title", "Java Streams"),
        spec=spec,
        request=kw.pop("request", "java stream notes pdf"),
        workdir=work,
        **kw,
    )


def test_a_markdown_spec_is_one_call_and_never_fails_on_a_ragged_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_renderer(monkeypatch, StepPdf(lambda step: 3))
    built = _build(tmp_path, {"theme": "notes", "markdown": JAVA_MD})
    assert built.file_type == "pdf" and built.effective_spec is not None
    kinds = [b["type"] for b in built.effective_spec["blocks"]]
    assert kinds == [
        "heading",
        "paragraph",
        "code",
        "table",
        "callout",
        "bullets",
        "heading",
        "bullets",
    ]
    table = built.effective_spec["blocks"][3]
    assert table["rows"] == [["map", "intermediate"], ["filter", ""]]
    assert any("repaired" in w for w in built.warnings)  # said, not silent
    assert "markdown" not in built.effective_spec  # the stored spec is the blocks that were drawn


def test_a_docx_markdown_spec_builds_with_the_same_parser(tmp_path: Path) -> None:
    built = _build(tmp_path, {"theme": "notes", "markdown": JAVA_MD}, file_format="docx")
    assert built.file_type == "docx" and len(built.data) > 2000


def test_a_plan_fence_in_the_markdown_fills_missing_fields_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_renderer(monkeypatch, StepPdf(lambda step: 2))
    md = '```json\n{"subtitle": "From the plan", "theme": "midnight"}\n```\n' + JAVA_MD
    built = _build(tmp_path, {"theme": "notes", "markdown": md})
    assert built.effective_spec is not None
    assert built.effective_spec["theme"] == "notes"  # the call's own field wins
    assert built.effective_spec["subtitle"] == "From the plan"
    assert built.effective_spec["blocks"][0]["type"] == "heading"  # the plan is not content


def test_empty_markdown_is_a_fixable_error(tmp_path: Path) -> None:
    with pytest.raises(DocumentToolError, match="no content") as caught:
        _build(tmp_path, {"markdown": "---\n"})  # a rule is syntax: nothing to print
    assert caught.value.repairable


def test_continue_and_expand_append_to_the_earlier_document_and_keep_its_design(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_renderer(monkeypatch, StepPdf(lambda step: 3))
    design = {"base": "minimal", "mood": "plum", "heading_font": "Georgia"}
    first = _build(tmp_path, {"theme": "minimal", "design": design, "markdown": JAVA_MD})
    state = {
        "format": "pdf",
        "title": "Java Streams",
        "file_id": "f1",
        "spec": first.effective_spec,
        "design_state": first.design_state,
    }
    more = "## Collectors\n\nGroup with `Collectors.groupingBy`.\n"
    second = _build(
        tmp_path,
        {"markdown": more, "append": True},
        previous=state,
        edits_file_id="f1",
        request="continue",
    )
    assert second.effective_spec is not None and first.effective_spec is not None
    blocks = second.effective_spec["blocks"]
    assert blocks[: len(first.effective_spec["blocks"])] == first.effective_spec["blocks"]
    assert blocks[-2]["text"] == "Collectors" and "groupingBy" in blocks[-1]["text"]
    kept, original = second.effective_spec["design"], first.effective_spec["design"]
    assert all(kept[k] == original[k] for k in ("base", "mood", "heading_font"))  # look kept
    assert second.effective_spec["theme"] == "minimal"
    # without the earlier spec the model is told to send everything, in plain words
    with pytest.raises(DocumentToolError, match="COMPLETE markdown"):
        _build(tmp_path, {"markdown": more, "append": True}, edits_file_id="f1")


class WordsPdf:
    """A renderer whose pages hold the given word counts (thin interior pages are observable)."""

    def __init__(self, counts: list[int]) -> None:
        self.counts = counts

    def __call__(self, document: Any, output_path: Path | str, **_: Any) -> PdfRenderResult:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        pdf = canvas.Canvas(str(path))
        for number, count in enumerate(self.counts, start=1):
            words = [f"w{number}x{i}" for i in range(count)]
            for line in range(0, len(words), 10):
                pdf.drawString(40, 800 - (line // 10) * 12, " ".join(words[line : line + 10]))
            pdf.showPage()
        pdf.save()
        return PdfRenderResult(path=path, renderer="fake", theme=document.theme)


def test_a_thin_interior_page_in_a_markdown_document_is_sent_back_once_then_noted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_renderer(monkeypatch, WordsPdf([300, 300, 30, 300, 300]))  # type: ignore[arg-type]
    with pytest.raises(DocumentToolError) as caught:
        _build(tmp_path, {"theme": "notes", "markdown": JAVA_MD}, length_retry=True)
    assert caught.value.repairable and "Page 3" in str(caught.value)
    built = _build(tmp_path, {"theme": "notes", "markdown": JAVA_MD}, length_retry=False)
    assert any("Page 3" in w for w in built.warnings)  # delivered, with the thin page named


@requires_browser
def test_a_five_page_markdown_request_is_fitted_to_five_pages_by_the_real_browser(
    tmp_path: Path,
) -> None:
    body = "\n\n".join(
        f"## Topic {i}\n\n" + chapter_markdown_paragraph(i) + f"\n\n```java\nint a = {i};\n```"
        for i in range(1, 12)
    )
    try:
        built = _build(
            tmp_path,
            {"theme": "notes", "markdown": body},
            request="make 5 pages java notes pdf",
            pages=5,
        )
        reader = PdfReader(io.BytesIO(built.data))
        assert built.length is not None and built.pages == len(reader.pages)
        assert 4 <= len(reader.pages) <= 7
    finally:
        shutdown_browser_renderer()


def chapter_markdown_paragraph(seed: int) -> str:
    return " ".join(
        f"Sentence {seed}-{n} explains one idea about streams in plain words." for n in range(14)
    )


async def test_asyncio_gate_is_per_event_loop() -> None:
    """The semaphore is created lazily per loop, so tests and workers never share one."""
    gate = ld._gate()
    assert gate is ld._gate()
    other: list[Any] = []

    def make() -> None:
        async def inner() -> None:
            other.append(ld._gate())

        asyncio.run(inner())

    await asyncio.to_thread(make)
    assert other and other[0] is not gate
