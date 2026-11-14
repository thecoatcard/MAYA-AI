"""The long-document pipeline end to end with fake models: plan -> outline -> chapters -> assemble.

No real Gemini and no network. Most tests print the PDF with a deterministic fake renderer whose
page count follows the estimate (so every branch is reachable); ``test_long_document_render.py``
repeats the key checks with the real Edge/Chrome renderer when one is installed.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from reportlab.pdfgen import canvas

from app.agents.long_document_plan import build_long_document_plan
from app.agents.planner import (
    DAGPlanner,
    PlanDecompositionResponse,
    SubTaskPlanItem,
    validate_dag,
)
from app.agents.schemas import AgentExecutionStatus, SpecialistAgentResult
from app.agents.specialized.document import DocumentAgent
from app.automation.acceptance import extract_acceptance
from app.automation.long_document import ChapterContent, estimate_body_pages
from app.automation.pdf_engine import PdfRenderResult
from app.tasks import long_document as ld
from app.tasks.dispatch import FleetSupport
from app.tasks.executor import execute_subtask_with_agent
from app.tasks.pipeline import DRAFT_KEY, length_notice, light_output, retitles, stage_of
from app.tasks.queue import SubtaskJob
from app.tasks.worker import NonRetryableSubtaskError, SubtaskExecutionError
from tests.support.longdoc import FakeLongDocGateway, make_job

pytestmark = pytest.mark.usefixtures("nine_chapters")

REQUEST_50 = (
    "make a notes on java from basic to advance make sure pdf is of 50 pages and well "
    "formatted and well documented"
)


class FakePdf:
    """Stands in for ``create_pdf_from_document``: writes a PDF with ``density`` x the estimate."""

    def __init__(self, density: float = 1.0) -> None:
        self.density = density
        self.page_counts: list[int] = []

    def __call__(self, document: Any, output_path: Path | str, **_: Any) -> PdfRenderResult:
        blocks = [b.model_dump() for b in document.blocks]
        breaks = sum(1 for b in blocks if b["type"] == "page_break") + 1
        pages = max(1, round((3 + estimate_body_pages(blocks) + 0.5 * breaks) * self.density))
        self.page_counts.append(pages)
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        pdf = canvas.Canvas(str(path))
        for number in range(1, pages + 1):
            # About 330 words a page, the measured density of a content page (a one-line page
            # would be rejected by the words floor: it looks like layout inflation).
            for line in range(30):
                pdf.drawString(
                    72, 780 - line * 13, f"Page {number} line {line} " + "alpha beta gamma " * 3
                )
            pdf.showPage()
        pdf.save()
        return PdfRenderResult(
            path=path,
            renderer="browser",
            theme=document.theme,
            browser="fake 1.0",
            toc_numbered=True,
        )


@pytest.fixture
def fake_pdf(monkeypatch: pytest.MonkeyPatch) -> FakePdf:
    fake = FakePdf()
    monkeypatch.setattr("app.agents.specialized.document.create_pdf_from_document", fake)
    return fake


class Run:
    """The pipeline stages run the way the worker runs them (chapters in parallel)."""

    def __init__(
        self,
        tmp_path: Path,
        gateway: FakeLongDocGateway,
        pages: int = 50,
        form: str = "book",
    ) -> None:
        self.gateway = gateway
        self.tmp_path = tmp_path
        self.pages = pages
        self.job = make_job()
        text = REQUEST_50.replace("50", str(pages))
        self.acceptance = extract_acceptance(text)
        # The user's own words decide notes / report / book; most tests exercise the book layout
        # (cover, contents, a page per chapter), the notes layout has its own test below. The
        # plan is sized for the same form the document is rendered in.
        wording = f"make a {form} on java, {pages} pages" if form != "notes" else text
        _, self.nodes, self.plan = build_long_document_plan(wording, self.acceptance)
        for node in self.nodes:
            node.input_data = {
                **(node.input_data or {}),
                "requirements": {"user_request": wording, "style": {}},
            }
        self.support = FleetSupport(gateway=gateway, artifacts_dir=tmp_path)  # type: ignore[arg-type]
        self.agent = DocumentAgent(artifacts_base_dir=tmp_path)

    async def stages(self, skip: set[int] | None = None) -> dict[str, Any]:
        outline = await ld.run_outline(self.gateway, self.job, self.nodes[0].input_data)  # type: ignore[arg-type]
        deps: dict[str, Any] = {"outline": outline["result"]}

        async def chapter(node: Any) -> tuple[str, dict[str, Any] | None]:
            data = {**node.input_data, "dependency_outputs": {"outline": outline["result"]}}
            if node.input_data["pipeline"]["index"] in (skip or set()):
                return node.id, None
            result = await ld.run_chapter(self.gateway, self.job, data)  # type: ignore[arg-type]
            return node.id, result["result"]

        for node_id, result in await asyncio.gather(*(chapter(n) for n in self.nodes[1:-1])):
            deps[node_id] = result if result is not None else {"missing": True, "reason": "failed"}
        return deps

    async def assemble(self, deps: dict[str, Any]) -> SpecialistAgentResult:
        node = self.nodes[-1]
        ctx = {**node.input_data, "description": node.description, "dependency_outputs": deps}
        built = await self.support.build_context(self.job, ctx)
        return await self.agent.execute_subtask(self.job, built)


# --------------------------------------------------------------------------- planning
class _NeverCalled:
    client = None

    async def generate_structured(self, **_: Any) -> Any:
        raise AssertionError("a long document is planned in code, without a model call")


async def test_planner_builds_the_chapter_pipeline_for_a_50_page_request() -> None:
    intent, nodes = await DAGPlanner(gateway=_NeverCalled()).plan(REQUEST_50)  # type: ignore[arg-type]
    ids = [n.id for n in nodes]
    assert ids[0] == "outline" and ids[-1] == "assemble"
    chapters = [n for n in nodes if n.id.startswith("chapter_")]
    assert 8 <= len(chapters) <= 12
    outline, assemble = nodes[0], nodes[-1]
    assert outline.input_data["executor"] == "outline" and outline.assigned_agent == "file"
    assert all(c.dependencies == ["outline"] for c in chapters)
    assert all(c.input_data["executor"] == "chapter" for c in chapters)
    assert all(c.input_data["critical"] is False for c in chapters)  # sibling failure tolerance
    assert assemble.assigned_agent == "document" and assemble.input_data["critical"] is True
    assert set(assemble.dependencies) == {"outline", *(c.id for c in chapters)}
    assert all(n.input_data["acceptance"] == {"pages": 50} for n in nodes)
    assert "long document" in intent.lower()
    validate_dag(nodes)  # acyclic, within the 20-subtask and edge limits


async def test_planner_keeps_a_single_call_document_for_three_pages_and_carries_the_length() -> (
    None
):
    class _Planner:
        client = None

        async def generate_structured(self, **_: Any) -> Any:
            return PlanDecompositionResponse(
                intent="short pdf",
                subtasks=[
                    SubTaskPlanItem(
                        id="task_1", assigned_agent="document", description="Create the 3 page PDF"
                    )
                ],
            )

    _, nodes = await DAGPlanner(gateway=_Planner()).plan("write a 3 pages pdf on tea")  # type: ignore[arg-type]
    assert [n.assigned_agent for n in nodes] == ["document"]
    assert nodes[0].input_data["acceptance"] == {"pages": 3}
    assert not any(n.id.startswith("chapter_") for n in nodes)


@pytest.mark.parametrize(("pages", "chapters"), [(100, 16), (400, 16), (9, 3), (12, 3)])
def test_fan_out_is_capped_and_the_plan_stays_valid(pages: int, chapters: int) -> None:
    _, nodes, plan = build_long_document_plan(f"{pages} pages", {"pages": pages})
    assert plan.chapters == chapters
    assert len([n for n in nodes if n.id.startswith("chapter_")]) == chapters
    assert len(nodes) <= 20
    validate_dag(nodes)


def test_a_docx_request_plans_a_docx() -> None:
    _, nodes, _ = build_long_document_plan("a 30 pages word document on go", {"pages": 30})
    assert nodes[-1].input_data["file_type"] == "docx"


# --------------------------------------------------------------------------- stages
async def test_chapters_run_in_parallel_and_each_writes_only_its_own_chapter(
    tmp_path: Path,
) -> None:
    run = Run(tmp_path, FakeLongDocGateway(chapters=9))
    deps = await run.stages()
    assert (
        run.gateway.max_active > 1
    )  # the stages are independent: the worker pool runs them in parallel
    chapter_calls = [c for c in run.gateway.calls if c.startswith("chapter:")]
    assert sorted(chapter_calls) == sorted(f"chapter:{i}" for i in range(1, 10))
    for node_id, output in deps.items():
        if node_id == "outline":
            continue
        draft = output[DRAFT_KEY]
        assert not any(b["type"] == "page_break" for b in draft["blocks"])
        assert output["chapter"]["words"] > 200
        assert DRAFT_KEY not in (light_output(output) or {})  # events and the plan stay light


async def test_a_chapter_prompt_names_the_other_chapters_and_the_outline(tmp_path: Path) -> None:
    seen: list[str] = []

    class _Spy(FakeLongDocGateway):
        async def generate_structured(self, **kwargs: Any) -> Any:
            seen.append(str(kwargs["contents"][0]))
            return await super().generate_structured(**kwargs)

        async def generate_text(self, **kwargs: Any) -> Any:
            seen.append(str(kwargs["contents"][0]))
            return await super().generate_text(**kwargs)

    run = Run(tmp_path, _Spy(chapters=9))
    await run.stages()
    chapter_prompt = next(p for p in seen if "Write chapter 3 of 9" in p)
    assert "Control flow and methods" in chapter_prompt  # its own title
    assert "Getting started with Java" in chapter_prompt  # another chapter's title
    assert "Level progression" in chapter_prompt
    assert "Must cover, in order" in chapter_prompt  # the plan's must-cover list


async def test_outline_with_too_many_chapters_is_merged_and_with_too_few_leaves_spare_steps(
    tmp_path: Path,
) -> None:
    many = Run(tmp_path, FakeLongDocGateway(chapters=9, outline_chapters=14))
    outline = (await ld.run_outline(many.gateway, many.job, many.nodes[0].input_data))["result"][  # type: ignore[arg-type]
        "outline"
    ]
    assert len(outline["chapters"]) == 9
    assert abs(sum(c["target_pages"] for c in outline["chapters"]) - outline["body_pages"]) < 1.5

    few = Run(tmp_path, FakeLongDocGateway(chapters=9, outline_chapters=6))
    deps = await few.stages()
    spare = deps["chapter_9"]
    assert spare["skipped"] is True and DRAFT_KEY not in spare
    result = await few.assemble(deps)  # the spare steps are simply not part of the document
    assert result.verification_proof.get("warnings") is not None or result.status


async def test_a_chapter_with_an_invalid_layout_or_no_outline_fails_precisely(
    tmp_path: Path,
) -> None:
    run = Run(tmp_path, FakeLongDocGateway(chapters=9))
    node = run.nodes[1]
    with pytest.raises(NonRetryableSubtaskError, match="outline"):
        await ld.run_chapter(run.gateway, run.job, {**node.input_data})  # type: ignore[arg-type]

    class _Empty(FakeLongDocGateway):
        # A chapter that comes back empty as Markdown, asked again, and empty in the JSON
        # fallback too: the step fails precisely (never a silent empty chapter).
        async def generate_text(self, **kwargs: Any) -> Any:
            self.calls.append("markdown")
            return type("R", (), {"text": ""})()

        async def generate_structured(self, **kwargs: Any) -> Any:
            if kwargs["response_schema"] is ld.ChapterSpec:
                self.calls.append("json")
                return ld.ChapterSpec(title="x", blocks=[])
            return await super().generate_structured(**kwargs)

    empty = Run(tmp_path, _Empty(chapters=9))
    outline = await ld.run_outline(empty.gateway, empty.job, empty.nodes[0].input_data)  # type: ignore[arg-type]
    with pytest.raises(SubtaskExecutionError, match="empty"):
        await ld.run_chapter(
            empty.gateway,  # type: ignore[arg-type]
            empty.job,
            {**empty.nodes[1].input_data, "dependency_outputs": {"o": outline["result"]}},
        )
    # Markdown, ONE retry with the problem named, then the structured fallback as the last try.
    assert empty.gateway.calls[-3:] == ["markdown", "markdown", "json"]


async def test_the_executor_routes_outline_and_chapter_steps_to_the_pipeline(
    tmp_path: Path,
) -> None:
    run = Run(tmp_path, FakeLongDocGateway(chapters=9))
    out = await execute_subtask_with_agent(run.job, run.nodes[0].input_data, gateway=run.gateway)  # type: ignore[arg-type]
    assert out["result"]["outline"]["chapters"]
    chapter = await execute_subtask_with_agent(
        run.job,
        {**run.nodes[2].input_data, "dependency_outputs": {"outline": out["result"]}},
        gateway=run.gateway,  # type: ignore[arg-type]
    )
    assert chapter["result"]["chapter"]["index"] == 2


# --------------------------------------------------------------------------- assemble + acceptance
async def test_assembly_meets_the_requested_length_and_records_requested_vs_actual(
    tmp_path: Path, fake_pdf: FakePdf
) -> None:
    run = Run(tmp_path, FakeLongDocGateway(chapters=9))
    result = await run.assemble(await run.stages())
    proof = result.verification_proof
    assert result.status == AgentExecutionStatus.SUCCESS, result.summary
    assert 45 <= proof["total_pages"] <= 63
    assert proof["acceptance"]["met"] is True
    assert proof["acceptance"]["requested"] == {"pages": 50}
    assert proof["acceptance"]["actual"]["pages"] == proof["total_pages"]
    assert "Length check passed" in result.summary
    assert "healing" not in proof, proof.get("healing")  # nothing needed fixing


async def test_assembled_document_structure_cover_contents_and_chapters_in_order(
    tmp_path: Path,
) -> None:
    run = Run(tmp_path, FakeLongDocGateway(chapters=9), form="notes")
    deps = await run.stages()
    node = run.nodes[-1]
    ctx = await run.support.build_context(
        run.job, {**node.input_data, "description": node.description, "dependency_outputs": deps}
    )
    # "notes" in the user's words: no cover page, a contents list (50 pages), chapters flow on.
    assert ctx["theme"] == "notes" and ctx["cover_page"] is False and ctx["toc"] is True
    blocks = ctx["blocks"]
    h1 = [b["text"] for b in blocks if b["type"] == "heading" and b["level"] == 1]
    assert h1[:3] == [
        "Chapter 1: Getting started with Java",
        "Chapter 2: Variables, types and operators",
        "Chapter 3: Control flow and methods",
    ]
    assert h1[-1] == "Exercises"
    assert blocks[0]["type"] == "heading"  # no leading page break
    assert not any(b["type"] == "page_break" for b in blocks)  # notes flow without breaks
    last = 1
    for block in blocks:
        if block["type"] == "heading":
            assert block["level"] <= last + 1
            last = block["level"]
    assert ctx["filename"].endswith("Java_from_Basic_to_Advanced.pdf")


async def test_a_book_request_keeps_cover_contents_and_a_page_per_chapter(
    tmp_path: Path,
) -> None:
    run = Run(tmp_path, FakeLongDocGateway(chapters=9))
    deps = await run.stages()
    node = run.nodes[-1]
    requirements = {"user_request": "write a book on Java, accent #B91C1C", "style": {}}
    ctx = await run.support.build_context(
        run.job,
        {
            **node.input_data,
            "requirements": requirements,
            "description": node.description,
            "dependency_outputs": deps,
        },
    )
    assert ctx["cover_page"] is True and ctx["toc"] is True
    assert sum(1 for b in ctx["blocks"] if b["type"] == "page_break") == 9


async def test_a_too_short_result_gets_one_expand_round_then_passes(
    tmp_path: Path, fake_pdf: FakePdf, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Switch the per-chapter floor off here: this test is about the measured-file heal round
    # (the chapter floor has its own tests in test_fa_agent_fixes.py).
    monkeypatch.setattr(ld, "CHAPTER_WORDS_FLOOR_FRACTION", 0.0)
    gateway = FakeLongDocGateway(chapters=9, yield_factor=0.5)
    run = Run(tmp_path, gateway)
    deps = await run.stages()
    gateway.max_active = 0  # measure the expand round only (the 9 chapters were written 9-wide)
    result = await run.assemble(deps)
    proof = result.verification_proof
    assert len(fake_pdf.page_counts) == 2  # exactly one heal round: render, expand, re-render
    assert fake_pdf.page_counts[0] < 45 <= proof["total_pages"] <= 63
    assert result.status == AgentExecutionStatus.SUCCESS and proof["acceptance"]["met"] is True
    trail = proof["healing"]
    assert trail[0]["attempt"] == "expand_chapters" and "more depth" in trail[0]["reason"]
    expands = [c for c in gateway.calls if c.startswith("expand:")]
    assert 1 <= len(expands) <= 6
    assert gateway.max_active <= 3  # the default expand concurrency


async def test_the_expand_round_respects_the_concurrency_bound(
    tmp_path: Path, fake_pdf: FakePdf
) -> None:
    gateway = FakeLongDocGateway(chapters=9, yield_factor=0.45)
    run = Run(tmp_path, gateway)
    run.support.expand_concurrency = 2
    deps = await run.stages()
    gateway.max_active = 0  # measure the expand round only
    await run.assemble(deps)
    assert any(c.startswith("expand:") for c in gateway.calls)
    assert gateway.max_active <= 2


async def test_still_short_after_the_round_is_reported_honestly_and_not_as_a_plain_success(
    tmp_path: Path, fake_pdf: FakePdf
) -> None:
    class _NoExpand(FakeLongDocGateway):
        async def _chapter(self, prompt: str) -> Any:
            if "must be longer" in prompt:
                raise RuntimeError("model unavailable")
            return await super()._chapter(prompt)

    fake_pdf.density = 0.7  # prints 70% of the estimate: about 42 pages for the 50 requested
    run = Run(tmp_path, _NoExpand(chapters=9, yield_factor=1.0))
    result = await run.assemble(await run.stages())
    proof = result.verification_proof
    produced = proof["total_pages"]
    assert 25 <= produced < 45
    assert proof["acceptance"]["met"] is False
    # The message now carries pages AND words (requested vs produced).
    assert proof["acceptance"]["message"].startswith(
        f"Requested 50 pages, produced {produced} pages and "
    )
    assert "words" in proof["acceptance"]["message"]
    # Count policy (deliberate update): the plain note ("You asked for ...; the file has ...") is
    # what the user reads, and the verdict is a warning on a delivered file.
    note = proof["acceptance"]["note"]
    assert note.startswith(f"You asked for 50 pages; the file has {produced} pages and ")
    assert proof["acceptance"]["status"] == "warning"
    # Mild shortfall: the file is kept, the task completes WITH the note (never silently).
    assert result.status == AgentExecutionStatus.SUCCESS
    assert note in result.summary
    assert note in proof["warnings"]


async def test_far_too_short_is_still_delivered_with_the_true_count(
    tmp_path: Path, fake_pdf: FakePdf
) -> None:
    class _NoExpand(FakeLongDocGateway):
        async def _chapter(self, prompt: str) -> Any:
            if "must be longer" in prompt:
                raise RuntimeError("model unavailable")
            return await super()._chapter(prompt)

    fake_pdf.density = 0.3  # the owner's case: a handful of pages for a 50 page request
    run = Run(tmp_path, _NoExpand(chapters=9))
    result = await run.assemble(await run.stages())
    produced = result.verification_proof["total_pages"]
    assert produced < 25
    # Count policy (deliberate update): under half of the request used to FAIL the step (the file
    # stayed on disk but the task read "Completed with errors"). It is now delivered, the verdict
    # is a warning and the result states the true count plainly.
    assert result.status == AgentExecutionStatus.SUCCESS
    assert result.artifact_paths and result.verification_proof["status"] == "verified"
    assert f"You asked for 50 pages; the file has {produced} pages" in result.summary
    assert result.verification_proof["acceptance"]["status"] == "warning"
    assert result.verification_proof["acceptance"]["met"] is False


async def test_too_long_is_trimmed_by_dropping_low_priority_sections_not_mid_section(
    tmp_path: Path, fake_pdf: FakePdf
) -> None:
    run = Run(tmp_path, FakeLongDocGateway(chapters=9, yield_factor=1.8))
    result = await run.assemble(await run.stages())
    proof = result.verification_proof
    assert fake_pdf.page_counts[0] > 63
    assert 45 <= proof["total_pages"] <= 63 and proof["acceptance"]["met"] is True
    reason = proof["healing"][0]["reason"]
    assert proof["healing"][0]["attempt"] == "trim_sections"
    assert reason.index("Exercises") < reason.index("key takeaways")  # exercises go first
    # Whatever was dropped was whole: no block list ends in the middle of a section's content.
    assert result.status == AgentExecutionStatus.SUCCESS


async def test_a_missing_chapter_is_listed_and_the_rest_is_assembled(
    tmp_path: Path, fake_pdf: FakePdf
) -> None:
    run = Run(tmp_path, FakeLongDocGateway(chapters=9, always_fail_chapters={4}))
    result = await run.assemble(await run.stages(skip={4}))
    proof = result.verification_proof
    assert result.status == AgentExecutionStatus.SUCCESS
    assert any("Chapter 4: Classes and objects" in w for w in proof["warnings"])
    assert any(h["attempt"] == "missing_chapters" for h in proof["healing"])
    assert "Chapter 4" in result.summary


async def test_a_chapter_that_failed_its_step_is_retried_once_by_the_assembler(
    tmp_path: Path, fake_pdf: FakePdf
) -> None:
    gateway = FakeLongDocGateway(chapters=9)
    run = Run(tmp_path, gateway)
    deps = await run.stages(skip={4})
    node = run.nodes[-1]
    ctx = {**node.input_data, "description": node.description, "dependency_outputs": deps}
    built = await run.support.build_context(run.job, ctx)

    assert gateway.chapter_attempts[4] == 1  # one extra try, made by the assembler
    assert not built.get("spec_warnings")  # recovered: nothing is missing
    assert not any(
        b.get("type") == "callout" and "Not included" in b["text"] for b in built["blocks"]
    )


async def test_a_chapter_that_stays_missing_leaves_a_visible_note_in_the_document(
    tmp_path: Path, fake_pdf: FakePdf
) -> None:
    gateway = FakeLongDocGateway(chapters=9, always_fail_chapters={4})
    run = Run(tmp_path, gateway)
    deps = await run.stages(skip={4})
    node = run.nodes[-1]
    ctx = {**node.input_data, "description": node.description, "dependency_outputs": deps}
    built = await run.support.build_context(run.job, ctx)

    # The assembler tries the chapter once more, and a chapter run asks its model twice (the
    # chapter's own retry with the problem named): two calls, never a loop.
    assert gateway.chapter_attempts[4] == 2
    notes = [b for b in built["blocks"] if str(b.get("text", "")).startswith("Not included:")]
    assert len(notes) == 1 and notes[0]["type"] == "callout"
    assert "Chapter 4: Classes and objects" in notes[0]["text"]


async def test_assembly_without_any_chapter_or_outline_fails_clearly(tmp_path: Path) -> None:
    run = Run(tmp_path, FakeLongDocGateway(chapters=9))
    deps = await run.stages()
    run.support.gateway = FakeLongDocGateway(chapters=9, always_fail_chapters=set(range(1, 10)))  # type: ignore[assignment]
    node = run.nodes[-1]
    only_outline = {"outline": deps["outline"]}
    with pytest.raises(NonRetryableSubtaskError, match="None of the 9 chapters"):
        await run.support.build_context(
            run.job, {**node.input_data, "dependency_outputs": only_outline}
        )
    with pytest.raises(NonRetryableSubtaskError, match="no outline"):
        await run.support.build_context(run.job, {**node.input_data, "dependency_outputs": {}})


def test_document_timeout_follows_the_requested_pages() -> None:
    agent = DocumentAgent(artifacts_base_dir=".")
    assert agent.execution_timeout_for({"acceptance": {"pages": 50}}) == 360.0
    assert agent.execution_timeout_for({"acceptance": {"pages": 3}}) == 240.0
    assert agent.execution_timeout_for({}) is None
    assert agent.execution_timeout_for({"acceptance": {"slides": 8}}) is None


def test_chapter_dict_round_trip_and_heading_levels() -> None:
    chapter = ChapterContent(
        index=2,
        title="OOP",
        blocks=[
            {"type": "heading", "level": 1, "text": "stray h1"},
            {"type": "heading", "level": 3, "text": "jumps a level"},
            {"type": "paragraph", "text": "text words"},
        ],
    )
    levels = [b["level"] for b in chapter.render_blocks() if b["type"] == "heading"]
    assert levels == [1, 2, 3]  # chapter heading, h1 demoted to 2, no jump 2 -> 4
    again = ChapterContent.from_dict(chapter.to_dict())
    assert again.title == "OOP" and again.words == chapter.words


def _job() -> SubtaskJob:
    return make_job()


async def test_single_call_documents_carry_the_length_into_the_prompt_and_heal_once(
    tmp_path: Path, fake_pdf: FakePdf
) -> None:
    """A 3 page request stays a single model call; a miss is regenerated once with the numbers."""
    from app.tasks.dispatch import BlockSpec, DocumentSpec

    prompts: list[str] = []

    class _Gateway:
        client = None

        async def generate_structured(self, **kwargs: Any) -> DocumentSpec:
            prompts.append(str(kwargs["contents"][0]))
            repeat = 30 if len(prompts) > 1 else 1
            return DocumentSpec(
                file_type="pdf",
                filename="Tea.pdf",
                title="Tea",
                blocks=[BlockSpec(type="heading", level=1, text="Tea")]
                + [BlockSpec(type="paragraph", text="Tea is a drink. " * 60)] * repeat,
            )

    fake_pdf.density = 0.3  # a one page result for the first version
    support = FleetSupport(gateway=_Gateway(), artifacts_dir=tmp_path)  # type: ignore[arg-type]
    agent = DocumentAgent(artifacts_base_dir=tmp_path)
    job = _job()
    ctx = await support.build_context(
        job, {"description": "Create the 3 page PDF on tea", "acceptance": {"pages": 3}}
    )
    assert "about 3 pages" in prompts[0] and "verified against it" in prompts[0]
    result = await agent.execute_subtask(job, ctx)
    assert len(prompts) == 2 and "did not meet the user's requirement" in prompts[1]
    assert "Requested 3 pages, produced" in prompts[1]
    assert result.verification_proof["healing"][-1]["attempt"] == "regenerate_for_length"
    assert result.verification_proof["acceptance"]["requested"] == {"pages": 3}


def test_the_chat_reply_sets_an_honest_expectation_for_a_long_document() -> None:
    _, nodes, _ = build_long_document_plan(REQUEST_50, {"pages": 50})
    notice = length_notice(nodes)
    assert notice == (
        "This is a long document: about 9 chapters (about 50 pages). It will take several minutes."
    )
    from app.services.chat_service import _notice_instruction

    assert notice in _notice_instruction(notice)
    short = [nodes[0].model_copy(update={"input_data": {}})]
    assert length_notice(short) is None and stage_of({}) is None


def test_chapter_steps_get_real_titles_and_unused_steps_are_marked() -> None:
    outline = [{"index": 1, "title": "Basics"}, {"index": 2, "title": "OOP basics"}]
    titles = retitles([("a", 1), ("b", 2), ("c", 3)], outline)
    assert titles["a"] == ("Chapter 1: Basics", True)
    assert titles["b"] == ("Chapter 2: OOP basics", True)
    assert titles["c"][1] is False and "not needed" in titles["c"][0]


async def test_docx_of_a_long_document_has_cover_and_contents_without_page_numbers(
    tmp_path: Path,
) -> None:
    """DOCX: same content, no fixed pages, so a page requirement is not claimed for it."""
    run = Run(tmp_path, FakeLongDocGateway(chapters=9))
    deps = await run.stages()
    node = run.nodes[-1]
    ctx = await run.support.build_context(
        run.job, {**node.input_data, "description": node.description, "dependency_outputs": deps}
    )
    ctx = {**ctx, "file_type": "docx", "filename": ctx["filename"].replace(".pdf", ".docx")}
    result = await run.agent.execute_subtask(run.job, ctx)
    assert result.status == AgentExecutionStatus.SUCCESS, result.summary
    proof = result.verification_proof
    assert proof["file_type"] == "docx" and proof["acceptance"]["requested"] == {}
    from docx import Document as WordDocument

    texts = [p.text for p in WordDocument(result.artifact_paths[0]).paragraphs]
    assert "Contents" in texts and "Chapter 1: Getting started with Java" in texts


async def test_other_deliverables_check_their_stated_quantities(tmp_path: Path) -> None:
    """Slides must match exactly (one repair round); sheets are checked and reported."""
    agent = DocumentAgent(artifacts_base_dir=tmp_path)
    job = make_job()
    slides = [{"layout": "title", "title": "Deck"}] + [
        {"layout": "bullets", "title": f"Point {i}", "bullets": ["a"]} for i in range(1, 7)
    ]
    calls: list[str] = []

    async def healer(*args: Any) -> dict[str, Any] | None:
        calls.append("healed")
        return {
            "slides": [*slides, {"layout": "closing", "title": "End"}],
            "healing": [{"attempt": "regenerate_for_length", "reason": "x"}],
        }

    base = {
        "filename": "d.pptx",
        "file_type": "pptx",
        "slides": slides,
        "acceptance": {"slides": 8},
    }
    healed = await agent.execute_subtask(job, {**base, "acceptance_healer": healer})
    assert calls == ["healed"] and healed.status == AgentExecutionStatus.SUCCESS, healed.summary
    assert healed.verification_proof["acceptance"]["actual"] == {"slides": 8}

    # Count policy (deliberate update): 7 slides for a stated 8 with no healer used to FAIL
    # ("Requested 8 slides, produced 7"). The deck is fitted in code to exactly 8 and delivered.
    wrong = await agent.execute_subtask(job, base)
    assert wrong.status == AgentExecutionStatus.SUCCESS, wrong.summary
    assert wrong.verification_proof["acceptance"]["actual"] == {"slides": 8}
    assert wrong.verification_proof["acceptance"]["status"] == "met"

    sheets = [{"title": "S1", "headers": ["a"], "rows": [["1"]]}]
    short = await agent.execute_subtask(
        job,
        {"filename": "w.xlsx", "file_type": "xlsx", "sheets": sheets, "acceptance": {"sheets": 3}},
    )
    # One of three sheets: sheets cannot be invented, so the workbook is DELIVERED with a warning
    # and the honest numbers (it used to fail the step when under half of the request).
    assert short.status == AgentExecutionStatus.SUCCESS
    assert short.verification_proof["acceptance"]["status"] == "warning"
    assert "You asked for 3 sheets; the file has 1 sheet" in short.summary
