"""The plan for a long document: outline -> N chapters in parallel -> assemble (ADR-0020).

A request such as "notes on Java, 50 pages" cannot be one model call: the output of a single
structured call is a few pages. When the user states a length above ``LONG_DOCUMENT_PAGES`` pages
(or ``LONG_DOCUMENT_WORDS`` words), the plan is built here, in code and without a model call, so
it is always valid and never depends on the planner model following a long recipe:

``outline`` (plain LLM step: structured chapter list with a page budget)
  -> ``chapter_1`` ... ``chapter_N`` (each writes ONLY its chapter, in parallel, bounded by the
     worker pool; a chapter that fails twice is tolerated and reported as missing)
  -> ``assemble`` (the document agent: cover, contents, chapters, verify against the request).

The step ids and the ``pipeline`` markers in ``input_data`` are what the runtime
(``app.tasks.long_document``) and the worker (``app.tasks.pipeline``) key on.
"""

from __future__ import annotations

import re

from app.agents.plan_repair import PERSISTED_EXECUTOR_ROLE
from app.agents.state import SubTask
from app.automation.acceptance import ChapterPlan, plan_chapters
from app.automation.form import detect_form
from app.tasks.pipeline import STAGE_ASSEMBLE, STAGE_CHAPTER, STAGE_OUTLINE

OUTLINE_ID = "outline"
ASSEMBLE_ID = "assemble"
_DOCX = re.compile(r"\b(docx|word document|ms word|\.docx)\b", re.IGNORECASE)


def chapter_id(index: int) -> str:
    return f"chapter_{index}"


def file_type_for(instruction: str) -> str:
    """``docx`` when the user asked for a Word document, otherwise ``pdf``."""
    return "docx" if _DOCX.search(instruction or "") else "pdf"


def build_long_document_plan(
    instruction: str, acceptance: dict[str, int]
) -> tuple[str, list[SubTask], ChapterPlan]:
    """``(intent, subtasks, chapter plan)`` for a long-document request."""
    plan = plan_chapters(acceptance, detect_form(instruction))
    file_type = file_type_for(instruction)
    body_pages = round(plan.pages_per_chapter * plan.chapters, 1)
    shared = {"acceptance": dict(acceptance), "file_type": file_type}
    nodes: list[SubTask] = [
        SubTask(
            id=OUTLINE_ID,
            assigned_agent=PERSISTED_EXECUTOR_ROLE,
            description=(
                f"Outline the document: {plan.chapters} chapters, about {plan.target_pages} pages"
            ),
            dependencies=[],
            input_data={
                "executor": "outline",
                "critical": True,
                "step_label": "Plan the document",
                "pipeline": {
                    "stage": STAGE_OUTLINE,
                    "slots": plan.chapters,
                    "target_pages": plan.target_pages,
                    "body_pages": body_pages,
                    "pages_per_chapter": plan.pages_per_chapter,
                    "request": instruction[:2000],
                },
                **shared,
            },
        )
    ]
    for index in range(1, plan.chapters + 1):
        nodes.append(
            SubTask(
                id=chapter_id(index),
                assigned_agent=PERSISTED_EXECUTOR_ROLE,
                description=f"Chapter {index} of {plan.chapters} (title set from the outline)",
                dependencies=[OUTLINE_ID],
                input_data={
                    "executor": "chapter",
                    "step_label": f"Chapter {index} of {plan.chapters}",
                    # A chapter that fails for good must not sink the document: the assembler
                    # continues without it and the result lists it as missing.
                    "critical": False,
                    "pipeline": {
                        "stage": STAGE_CHAPTER,
                        "index": index,
                        "of": plan.chapters,
                    },
                    **shared,
                },
            )
        )
    nodes.append(
        SubTask(
            id=ASSEMBLE_ID,
            assigned_agent="document",
            description=(
                f"Assemble the {file_type.upper()}: cover page, contents, {plan.chapters} "
                f"chapters, then check it is about {plan.target_pages} pages"
            ),
            dependencies=[OUTLINE_ID, *(chapter_id(i) for i in range(1, plan.chapters + 1))],
            input_data={
                "critical": True,
                "step_label": "Assemble",
                "pipeline": {
                    "stage": STAGE_ASSEMBLE,
                    "slots": plan.chapters,
                    "target_pages": plan.target_pages,
                },
                **shared,
            },
        )
    )
    return "Write a long document", nodes, plan
