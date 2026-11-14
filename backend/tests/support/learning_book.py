"""A fake model for the learning-book pipeline tests (no real model, no network).

``FakeBookGateway`` answers the plan call (structured: 10 chapters with story beats) and every
chapter call (text: Markdown with the sections, code, a table, an equation and an algorithm trace
the real prompt asks for). It records every prompt, so the tests can check what each call was told.
"""

from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace
from typing import Any

from app.tasks.long_document import OutlineChapterSpec, OutlineSpec

TITLES = [
    "The Story Begins",
    "Foundations and Vocabulary",
    "Why This Exists",
    "Core Building Blocks",
    "How It Works Inside",
    "Brute Force Problem Solving",
    "Better and Optimized Solutions",
    "Real-World Patterns",
    "Advanced Ideas",
    "Practice, Interviews and Revision",
    "Extra One",
    "Extra Two",
]

PREMISE = "Asha, a junior developer, must make her team's app fast before a demo."


def beat(number: int) -> str:
    return f"STORY-BEAT-{number}: Asha learns step {number} and her app improves."


def outline(chapters: int, **fields: Any) -> OutlineSpec:
    return OutlineSpec(
        title=fields.pop("title", "Master Notes on Data Structures"),
        subtitle="A story-based learning book",
        audience="a complete beginner",
        level_progression="foundations to advanced",
        total_target_pages=chapters * 2,
        story_premise=PREMISE,
        include_exercises=False,
        include_summary=False,
        user_topics=["data structures"],
        chapters=[
            OutlineChapterSpec(
                title=TITLES[i],
                subtopics=[f"idea {i}.{j}" for j in range(4)],
                target_pages=2,
                kind="code" if i in (5, 6) else "concept",
                goal=(
                    "Solve the problem with brute force, then better, then optimized"
                    if i in (5, 6)
                    else f"Understand part {i + 1}"
                ),
                needs_code=i in (5, 6),
                needs_table=i == 6,
                story_beat=beat(i + 1),
            )
            for i in range(chapters)
        ],
        **fields,
    )


_PARAGRAPH = (
    "Asha reads the first idea slowly and writes it in her own words. Every term is explained "
    "before it is used, and each small step leads to the next one without any jump. "
)


def chapter_text(number: int, title: str, words: int) -> str:
    body = "\n\n".join(_PARAGRAPH * 4 for _ in range(max(1, words // 100)))
    return (
        f"# {title}\n\nSTORY-OPEN-{number}: {_PARAGRAPH}\n\n"
        f"## Part {number}\n\n### Understanding the Concept\n\n{body}\n\n"
        "### How Does It Work?\n\n1. Read the input\n   - value is 3\n2. Compare the values\n"
        "   - 3 is larger than 2\n\n"
        "```python\nfor x in items:\n    print(x)\n```\n\n"
        "| Idea | Time | Space |\n|---|---|---|\n| brute | O(n^2) | O(1) |\n"
        "| better | O(n) | O(n) |\n\n"
        "$$\\sum_{i=1}^{n} i = \\frac{n(n+1)}{2}$$\n\n"
        "### Chapter Takeaway\n\nYou can now explain this part to a friend.\n"
    )


class FakeBookGateway:
    client = None

    def __init__(
        self,
        *,
        chapters: int = 10,
        fail_chapters: set[int] | None = None,
        sources: list[dict[str, str]] | None = None,
        words: int = 900,
    ) -> None:
        self.chapters = chapters
        self.fail_chapters = fail_chapters or set()
        self.sources = sources if sources is not None else []
        self.words = words
        self.plan_prompts: list[str] = []
        self.plan_systems: list[str] = []
        self.chapter_prompts: dict[int, str] = {}
        self.chapter_systems: dict[int, str] = {}
        self.search_calls = 0
        self.active = 0
        self.max_active = 0

    async def generate_structured(self, **kwargs: Any) -> Any:
        schema = kwargs["response_schema"]
        assert schema is OutlineSpec, f"unexpected structured call {schema}"
        self.plan_prompts.append(str(kwargs["contents"][0]))
        self.plan_systems.append(str(kwargs.get("system_instruction") or ""))
        return outline(self.chapters)

    async def generate_text(self, **kwargs: Any) -> Any:
        prompt = str(kwargs["contents"][0])
        if kwargs.get("enable_google_search"):
            self.search_calls += 1
            return SimpleNamespace(text="A brief of what sources agree on.", citations=self.sources)
        found = re.search(r"Write chapter (\d+) of (\d+)", prompt)
        assert found is not None, prompt[:200]
        number = int(found.group(1))
        if number in self.fail_chapters:
            raise RuntimeError(f"simulated model failure for chapter {number}")
        self.chapter_prompts[number] = prompt
        self.chapter_systems[number] = str(kwargs.get("system_instruction") or "")
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.02)
            return SimpleNamespace(text=chapter_text(number, TITLES[number - 1], self.words))
        finally:
            self.active -= 1
