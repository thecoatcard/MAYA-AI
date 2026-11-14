"""Deterministic fakes for the long-document pipeline tests (no real model, no network).

``FakeLongDocGateway`` answers the pipeline's model calls with realistic blocks: sentences,
bullets, code, tables and callouts, sized by the word target in the prompt times ``yield_factor``
(below 1.0 simulates a model that writes too little). The outline is a structured call; a chapter
(and its expansion) is a TEXT call answered as Markdown (what the real model is asked for), and
``generate_structured`` still answers a chapter for the JSON fallback.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from app.automation.long_document import block_page_words
from app.tasks.dispatch import BlockSpec
from app.tasks.long_document import (
    ChapterSpec,
    OutlineChapterSpec,
    OutlineSpec,
    blocks_to_text,
)
from app.tasks.queue import SubtaskJob

TOPICS = [
    "Getting started with Java",
    "Variables, types and operators",
    "Control flow and methods",
    "Classes and objects",
    "Inheritance and interfaces",
    "Collections and generics",
    "Exceptions and I/O",
    "Streams and lambdas",
    "Concurrency",
    "The JVM and performance",
    "Testing and build tools",
    "Design patterns in practice",
]

_SENTENCES = [
    "A class is a blueprint that describes the state and behaviour of the objects built from it.",
    "The compiler checks every assignment against the declared type before the program runs.",
    "Prefer composition to inheritance when the relationship is not a strict is-a one.",
    "Each method should do one thing and return a value that makes the call site easy to read.",
    "Immutable objects are safe to share between threads without any extra locking.",
    "A common mistake is to compare strings with == instead of the equals method.",
    "The garbage collector reclaims memory that no live reference can reach any more.",
    "Generics let one algorithm work on many element types while the compiler keeps it type safe.",
    "Checked exceptions force the caller to decide how a failure of the callee is handled.",
    "Measure before you optimise: a profiler shows where the time really goes in a program.",
]
_CODE = (
    "public class Counter {\n"
    "    private int value;\n\n"
    "    public void increment() {\n"
    "        value++;\n"
    "    }\n\n"
    "    public int get() {\n"
    "        return value;\n"
    "    }\n"
    "}\n"
)


def _paragraph(seed: int, sentences: int = 5) -> str:
    return " ".join(_SENTENCES[(seed + i) % len(_SENTENCES)] for i in range(sentences))


def fake_chapter_blocks(title: str, prose_words: int, *, seed: int = 0) -> list[dict[str, Any]]:
    """Realistic chapter blocks (no level-1 heading) costing about ``prose_words`` of budget."""
    blocks: list[dict[str, Any]] = [
        {
            "type": "paragraph",
            "text": f"This chapter covers {title.lower()}. " + _paragraph(seed, 3),
        }
    ]
    spent = sum(block_page_words(b) for b in blocks)
    section = 0
    while spent < prose_words:
        section += 1
        number = seed * 10 + section
        group: list[dict[str, Any]] = [
            {"type": "heading", "level": 2, "text": f"{title}: topic {section}"},
            {"type": "paragraph", "text": _paragraph(number)},
            {"type": "paragraph", "text": _paragraph(number + 3, 4)},
            {"type": "bullets", "items": [_SENTENCES[(number + i) % 10] for i in range(3)]},
            {
                "type": "code",
                "language": "java",
                "text": _CODE.replace("Counter", f"Counter{number}"),
            },
            {
                "type": "callout",
                "kind": ("info", "success", "warning")[number % 3],
                "text": _paragraph(number, 1),
            },
        ]
        if section % 2 == 0:
            group.append({"type": "heading", "level": 3, "text": f"Details {section}"})
            group.append(
                {
                    "type": "table",
                    "headers": ["Concept", "Meaning", "Example"],
                    "rows": [[f"term {r}", _SENTENCES[r % 10][:40], f"x{r}"] for r in range(5)],
                    "caption": f"Table {number}",
                }
            )
        blocks.extend(group)
        spent += sum(block_page_words(b) for b in group)
    return blocks


def to_specs(blocks: list[dict[str, Any]]) -> list[BlockSpec]:
    return [BlockSpec.model_validate(b) for b in blocks]


def outline_spec(chapters: int, pages_each: int = 5, **fields: Any) -> OutlineSpec:
    return OutlineSpec(
        title=fields.pop("title", "Java from Basic to Advanced"),
        subtitle="Notes for learners",
        audience="beginners to experienced developers",
        level_progression="starts with syntax, ends with concurrency and the JVM",
        total_target_pages=chapters * pages_each,
        include_exercises=fields.pop("include_exercises", True),
        include_summary=fields.pop("include_summary", True),
        chapters=[
            OutlineChapterSpec(
                title=TOPICS[i % len(TOPICS)] if i < len(TOPICS) else f"Advanced topic {i + 1}",
                subtopics=[f"subtopic {i}.{j}" for j in range(4)],
                target_pages=pages_each,
                kind="code" if i % 3 == 1 else "concept",
            )
            for i in range(chapters)
        ],
        **fields,
    )


class FakeLongDocGateway:
    """A gateway whose ``generate_structured`` answers outline, chapter and expand calls."""

    client = None

    def __init__(
        self,
        *,
        chapters: int = 9,
        yield_factor: float = 1.0,
        expand_factor: float = 1.0,
        fail_chapters: set[int] | None = None,
        always_fail_chapters: set[int] | None = None,
        outline_chapters: int | None = None,
    ) -> None:
        self.chapters = chapters
        self.outline_chapters = outline_chapters if outline_chapters is not None else chapters
        self.yield_factor = yield_factor
        self.expand_factor = expand_factor
        self.fail_once = set(fail_chapters or ())
        self.fail_always = set(always_fail_chapters or ())
        self.calls: list[str] = []
        self.chapter_attempts: dict[int, int] = {}
        self.active = 0
        self.max_active = 0

    async def generate_structured(self, **kwargs: Any) -> Any:
        schema = kwargs["response_schema"]
        prompt = str(kwargs["contents"][0])
        if schema is OutlineSpec:
            self.calls.append("outline")
            return outline_spec(self.outline_chapters)
        if schema is ChapterSpec:
            return await self._chapter(prompt)
        raise AssertionError(f"unexpected schema {schema}")

    async def generate_text(self, **kwargs: Any) -> Any:
        """A chapter or expansion answered as Markdown (the real model's format)."""
        spec = await self._chapter(str(kwargs["contents"][0]))
        return SimpleNamespace(text=chapter_markdown(spec))

    async def _chapter(self, prompt: str) -> ChapterSpec:
        import asyncio

        expand = "must be longer" in prompt
        if expand:
            found = re.search(r"chapter\s+(\d+)\s*\(", prompt)
        else:
            found = re.search(r"Write chapter (\d+) of", prompt)
        assert found is not None, prompt[:200]
        number = int(found.group(1))
        self.calls.append(f"expand:{number}" if expand else f"chapter:{number}")
        if not expand:
            attempts = self.chapter_attempts.get(number, 0) + 1
            self.chapter_attempts[number] = attempts
            if number in self.fail_always or (number in self.fail_once and attempts == 1):
                raise RuntimeError(f"simulated model failure for chapter {number}")
        words = int(re.search(r"about (\d+) words of prose-equivalent", prompt).group(1))  # type: ignore[union-attr]
        factor = self.expand_factor if expand else self.yield_factor
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(
                0.05
            )  # a model call takes time: overlap between workers is observable
            title = TOPICS[(number - 1) % len(TOPICS)]
            blocks = fake_chapter_blocks(title, round(words * factor), seed=number)
            return ChapterSpec(
                title=title,
                blocks=to_specs(blocks),
                key_takeaways=[f"Remember point {i} of {title}" for i in range(4)],
                exercises=[
                    f"Exercise {i}: write a program about {title.lower()}" for i in range(5)
                ],
            )
        finally:
            self.active -= 1


def chapter_markdown(spec: ChapterSpec) -> str:
    """A chapter as the Markdown a model would write: body, then takeaways and exercises."""
    nl = chr(10)
    text = blocks_to_text([b.to_block() for b in spec.blocks], limit=10**9)
    if spec.key_takeaways:
        text += nl + "## Key takeaways" + nl + nl + nl.join(f"- {t}" for t in spec.key_takeaways)
    if spec.exercises:
        text += nl + nl + "## Exercises" + nl + nl
        text += nl.join(f"{n}. {e}" for n, e in enumerate(spec.exercises, 1))
    return text


def make_job(agent: str = "document") -> SubtaskJob:
    return SubtaskJob(
        workspace_id=uuid4(),
        user_id=uuid4(),
        task_id=uuid4(),
        subtask_id=uuid4(),
        assigned_agent=agent,
    )
