"""Small pure helpers for the long-document pipeline that the worker needs (no model, no I/O).

The pipeline nodes carry ``input_data["pipeline"] = {"stage": "outline" | "chapter" | "assemble",
...}`` (see ``app.agents.long_document_plan``). The worker uses these helpers to keep the heavy
chapter drafts out of events and to give the chapter steps their real titles once the outline
exists. They live apart from ``app.tasks.long_document`` (which calls the model and imports the
dispatch layer) so the worker can import them without a cycle.
"""

from __future__ import annotations

from typing import Any

STAGE_OUTLINE = "outline"
STAGE_CHAPTER = "chapter"
STAGE_ASSEMBLE = "assemble"
DRAFT_KEY = "draft"
"""Output key holding a chapter's full blocks: large, so it is not sent in events or the plan."""
MARKDOWN_KEY = "draft_markdown"
"""Output key holding a learning-book chapter's Markdown (ADR-0031): large, like the draft."""


def stage_of(input_data: dict[str, Any] | None) -> str | None:
    """The pipeline stage of a step's input, or ``None`` for an ordinary step."""
    pipeline = (input_data or {}).get("pipeline")
    stage = pipeline.get("stage") if isinstance(pipeline, dict) else None
    return stage if isinstance(stage, str) else None


def light_output(output: dict[str, Any] | None) -> dict[str, Any] | None:
    """``output`` without the chapter draft (for events and the stored plan)."""
    if not isinstance(output, dict) or (DRAFT_KEY not in output and MARKDOWN_KEY not in output):
        return output
    return {k: v for k, v in output.items() if k not in (DRAFT_KEY, MARKDOWN_KEY)}


def chapter_title(index: int, title: str) -> str:
    """The step title shown in the Work panel."""
    return f"Chapter {index}: {title}"[:200]


def retitles(
    chapter_steps: list[tuple[str, int]], outline_chapters: list[dict[str, Any]]
) -> dict[str, tuple[str, bool]]:
    """New titles for the chapter steps once the outline exists.

    ``chapter_steps`` is ``[(step id, chapter index)]``; the result maps a step id to
    ``(title, used)``: ``used`` is False for a step with no chapter in the outline (the outline
    came back shorter than the planned number of chapters), which then writes nothing.
    """
    by_index = {int(c.get("index") or 0): str(c.get("title") or "") for c in outline_chapters}
    out: dict[str, tuple[str, bool]] = {}
    for step_id, index in chapter_steps:
        title = by_index.get(index)
        if title:
            out[step_id] = (chapter_title(index, title), True)
        else:
            out[step_id] = (f"Chapter {index}: not needed (the outline has fewer chapters)", False)
    return out


def length_notice(nodes: list[Any]) -> str | None:
    """The honest expectation for the chat reply when a plan is the long-document pipeline."""
    for node in nodes:
        data = getattr(node, "input_data", None) or {}
        if stage_of(data) == STAGE_OUTLINE:
            pipeline = data["pipeline"]
            if pipeline.get("mode") == "learning_book":
                return (
                    f"This is a learning book: {pipeline.get('slots')} chapters (about "
                    f"{pipeline.get('total_words')} words), written one chapter at a time. It will "
                    "take several minutes."
                )
            return (
                f"This is a long document: about {pipeline.get('slots')} chapters "
                f"(about {pipeline.get('target_pages')} pages). It will take several minutes."
            )
    return None
