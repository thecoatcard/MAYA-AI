"""The learning-book job end to end with a fake model (ADR-0031): plan, chapters, assembly.

Chapters are Markdown, the story thread (premise and earlier beats) reaches every chapter, at most
three chapter calls run at once, a failed chapter is reported as missing instead of sinking the
book, and for Notion the assembled Markdown is a real ``.md`` artifact with the contents marker.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from app.agents.learning_book_plan import build_learning_book_plan
from app.agents.specialized.document import DocumentAgent
from app.automation.book_markdown import describe_markdown
from app.mcp.notion_book import summarize
from app.tasks import long_document as ld
from app.tasks.dispatch import FleetSupport
from app.tasks.pipeline import DRAFT_KEY, MARKDOWN_KEY, light_output
from tests.support.learning_book import PREMISE, FakeBookGateway, beat
from tests.support.longdoc import make_job

REQUEST = "make master study notes on data structures in notion"


def saved_markdown(root: Path) -> str:
    """The one ``.md`` file the document agent wrote under ``root``."""
    (saved,) = list(root.rglob("*.md"))
    return saved.read_text(encoding="utf-8")


class BookRun:
    """The stages run the way the worker runs them (chapters in parallel)."""

    def __init__(
        self, tmp_path: Path, gateway: FakeBookGateway, request: str = REQUEST, **plan: Any
    ) -> None:
        self.gateway = gateway
        self.tmp_path = tmp_path
        self.job = make_job()
        _, self.nodes, self.facts = build_learning_book_plan(request, **plan)
        for node in self.nodes:
            node.input_data = {
                **(node.input_data or {}),
                "requirements": {"user_request": request, "style": {}},
            }
        self.support = FleetSupport(gateway=gateway, artifacts_dir=tmp_path)  # type: ignore[arg-type]
        self.agent = DocumentAgent(artifacts_base_dir=tmp_path)

    async def stages(self) -> dict[str, Any]:
        outline = await ld.run_outline(self.gateway, self.job, self.nodes[0].input_data)  # type: ignore[arg-type]
        deps: dict[str, Any] = {"outline": outline["result"]}

        async def chapter(node: Any) -> tuple[str, dict[str, Any] | None]:
            data = {**node.input_data, "dependency_outputs": {"outline": outline["result"]}}
            try:
                result = await ld.run_chapter(self.gateway, self.job, data)  # type: ignore[arg-type]
            except Exception:  # noqa: BLE001 - a failed chapter step is tolerated
                return node.id, None
            return node.id, result["result"]

        chapter_nodes = [n for n in self.nodes if n.id.startswith("chapter_")]
        for node_id, result in await asyncio.gather(*(chapter(n) for n in chapter_nodes)):
            deps[node_id] = result if result is not None else {"missing": True}
        return deps

    async def assemble(self, deps: dict[str, Any]) -> Any:
        node = next(n for n in self.nodes if n.id == "assemble")
        ctx = {**node.input_data, "description": node.description, "dependency_outputs": deps}
        built = await self.support.build_context(self.job, ctx)
        return built, await self.agent.execute_subtask(self.job, built)


async def test_the_plan_call_uses_the_book_prompts_and_returns_10_chapters(tmp_path: Path) -> None:
    run = BookRun(tmp_path, FakeBookGateway())
    outline = (await ld.run_outline(run.gateway, run.job, run.nodes[0].input_data))["result"]  # type: ignore[arg-type]
    book = outline["outline"]
    assert "curriculum designer" in run.gateway.plan_systems[0]
    assert "story_beat" in run.gateway.plan_systems[0]
    assert (
        "Learner level: BEGINNER" in run.gateway.plan_prompts[0]
        or "BEGINNER" in (run.gateway.plan_prompts[0])
    )
    assert 8 <= len(book["chapters"]) <= 12
    assert book["mode"] == "learning_book" and book["destination"] == "notion"
    assert book["story_premise"] == PREMISE
    assert [c["story_beat"] for c in book["chapters"]][:2] == [beat(1), beat(2)]
    assert book["include_exercises"] is False and book["code_language"] == ""


async def test_every_chapter_is_markdown_and_continues_the_same_story(tmp_path: Path) -> None:
    run = BookRun(tmp_path, FakeBookGateway())
    deps = await run.stages()
    chapters = [k for k in deps if k.startswith("chapter_")]
    assert len(chapters) == 10
    for key in chapters:
        out = deps[key]
        assert DRAFT_KEY in out and MARKDOWN_KEY in out
        assert out[MARKDOWN_KEY].lstrip().startswith("STORY-OPEN-")  # its own H1 was dropped
        assert "```python" in out[MARKDOWN_KEY] and "$$" in out[MARKDOWN_KEY]
        assert MARKDOWN_KEY not in (light_output(out) or {})  # not sent in events
    prompts = run.gateway.chapter_prompts
    assert PREMISE in prompts[1] and PREMISE in prompts[10]
    assert "this chapter opens the story" in prompts[1]
    assert beat(1) in prompts[2] and beat(2) not in prompts[2].split("In THIS chapter")[0]
    for earlier in range(1, 6):
        assert beat(earlier) in prompts[6]  # the story so far reaches chapter 6
    assert beat(7) not in prompts[6]  # and nothing from later chapters
    assert "learning book" in run.gateway.chapter_systems[3].lower()
    assert 'ONLY "#", "##" and "###"' in run.gateway.chapter_systems[3]


async def test_at_most_three_chapter_calls_run_at_once(tmp_path: Path) -> None:
    run = BookRun(tmp_path, FakeBookGateway())
    await run.stages()
    assert 1 < run.gateway.max_active <= ld.CHAPTER_CONCURRENCY == 3


async def test_the_code_language_of_the_request_reaches_every_chapter(tmp_path: Path) -> None:
    run = BookRun(
        tmp_path, FakeBookGateway(), request="make study notes on dsa in python in notion"
    )
    await run.stages()
    for number in (1, 4, 6):
        assert "use Python for every code example" in run.gateway.chapter_systems[number]
        assert "java" not in run.gateway.chapter_systems[number].lower()


async def test_the_saved_preference_decides_the_language_when_the_request_names_none(
    tmp_path: Path,
) -> None:
    run = BookRun(tmp_path, FakeBookGateway(), request="make study notes on dsa in notion")
    run.nodes[0].input_data["user_preferences"] = "About the user: prefers Go for coding."
    await run.stages()
    assert "use Go for every code example" in run.gateway.chapter_systems[2]


async def test_without_a_source_only_the_native_search_is_asked_and_sources_need_real_links(
    tmp_path: Path,
) -> None:
    found = FakeBookGateway(sources=[{"title": "Docs", "url": "https://example.org/a"}])
    run = BookRun(tmp_path, found)
    deps = await run.stages()
    assert found.search_calls == 1
    assert deps["outline"]["outline"]["sources"] == [
        {"title": "Docs", "url": "https://example.org/a"}
    ]
    assert "https://example.org/a" in found.chapter_prompts[1]  # the opening lists it
    assert "https://example.org/a" not in found.chapter_prompts[2]
    none = FakeBookGateway(sources=[])
    run2 = BookRun(tmp_path / "b", none)
    deps2 = await run2.stages()
    assert deps2["outline"]["outline"]["sources"] == []
    assert "Explored Sources" not in none.chapter_prompts[1]  # nothing fetched, nothing cited


async def test_a_supplied_file_is_the_source_and_no_search_runs(tmp_path: Path) -> None:
    gateway = FakeBookGateway()
    run = BookRun(tmp_path, gateway)
    data = {
        **run.nodes[0].input_data,
        "dependency_outputs": {
            "attachment:abc": {
                "agent": "attachment",
                "name": "lecture.txt",
                "content": "Arrays store items in order.",
            }
        },
    }
    await ld.run_outline(gateway, run.job, data)  # type: ignore[arg-type]
    assert gateway.search_calls == 0
    assert "Arrays store items in order." in gateway.plan_prompts[0]


async def test_for_notion_the_assembly_is_one_markdown_file_with_the_contents_marker(
    tmp_path: Path,
) -> None:
    run = BookRun(tmp_path, FakeBookGateway())
    deps = await run.stages()
    built, result = await run.assemble(deps)
    assert built["file_type"] == "md" and built["filename"].endswith(".md")
    assert result.status.value == "success"
    text = saved_markdown(tmp_path)
    assert text.count("\n# Chapter ") + text.startswith("# Chapter ") == 10
    assert "[toc]" in text and text.index("[toc]") < text.index("# Chapter 1:")
    assert "####" not in text and "\\$" not in text
    facts = describe_markdown(text)
    assert facts["code_blocks"] >= 10 and facts["tables"] >= 10 and facts["equations"] >= 10
    assert summarize(text)["chapters"] == 10


async def test_a_missing_chapter_is_listed_in_the_book_never_hidden(tmp_path: Path) -> None:
    run = BookRun(tmp_path, FakeBookGateway(fail_chapters={4}))
    deps = await run.stages()
    built, result = await run.assemble(deps)
    text = saved_markdown(tmp_path)
    assert "Not included: Chapter 4" in text and "# Chapter 4:" not in text
    assert "# Chapter 3:" in text and "# Chapter 5:" in text
    assert any("Chapter 4" in w for w in built["spec_warnings"])
    assert result.status.value == "success"


@pytest.mark.parametrize("where", ["pdf", "docx"])
async def test_for_a_file_the_book_goes_through_the_document_assembly(
    tmp_path: Path, where: str
) -> None:
    run = BookRun(tmp_path, FakeBookGateway(), request=f"make study notes on dsa as a {where}")
    deps = await run.stages()
    built = await run.support.build_context(
        run.job,
        {
            **run.nodes[-1].input_data,
            "description": "assemble",
            "dependency_outputs": deps,
        },
    )
    assert built["file_type"] == where and built["blocks"]  # blocks, not a Markdown file
    assert any(b.get("type") == "code" for b in built["blocks"])
