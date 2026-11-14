"""Run-time stages of the long-document pipeline (ADR-0020): plan, chapter, assemble, heal.

The plan is built in code by ``app.agents.long_document_plan``. This module does the work of its
steps:

* ``run_outline``: ONE small structured call, the plan (title, audience, language, design, page
  budget, chapters with goal, must-cover list, needs_code and needs_table; no prose).
* ``run_chapter``: one call per chapter, answered as MARKDOWN text (called from
  ``app.tasks.executor`` for steps marked ``executor: "chapter"``). ``app.automation.md_blocks``
  turns it into document blocks deterministically, so a bad table row or a truncated tail cannot
  fail the call. A chapter that fails is asked again once with the problem named; when the text
  still has too little, the old structured-JSON chapter call is the last fallback. Model calls of
  chapters are bounded by ``CHAPTER_CONCURRENCY``. A chapter writes ONLY its own chapter.
* ``assemble_context``: called by ``FleetSupport`` for the ``assemble`` step. It builds the
  document deterministically (no model call) from the chapters that exist and lists the ones that
  are missing.
* ``heal_assembly``: after the document agent measured the rendered PDF, one bounded expand or
  trim round (see ``docs/architecture/DOCUMENT_GENERATION.md``).

Nothing here talks to a real service on its own: the model gateway is passed in.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import logging
import re
import weakref
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, ValidationError

from app.agents.learning_book_plan import MODE as LB_MODE
from app.agents.learning_book_plan import detect_code_language
from app.agents.model_policy import LONG_OUTPUT_TOKENS, agent_call_kwargs, search_call_kwargs
from app.agents.prompts import learning_book as lb
from app.agents.prompts.long_document import (
    CHAPTER_KINDS,
    CHAPTER_MD_SYSTEM_PROMPT,
    CHAPTER_MD_USER_PROMPT,
    CHAPTER_RETRY_NOTE,
    CHAPTER_SYSTEM_PROMPT,
    CHAPTER_USER_PROMPT,
    EXPAND_USER_PROMPT,
    OUTLINE_SYSTEM_PROMPT,
    OUTLINE_USER_PROMPT,
)
from app.agents.requirements import PREFERENCES_KEY, language_pin, requirements_block
from app.agents.unsourced import UNSOURCED_PROMPT_RULE
from app.automation import model as doc_model
from app.automation.acceptance import (
    CHAPTER_WORDS_FLOOR_FRACTION,
    MAX_CHAPTERS,
    NOTES_WORDS_PER_PAGE,
    AcceptanceResult,
    pages_bounds,
    words_floor,
)
from app.automation.book_markdown import build_book_markdown, clean_chapter_markdown
from app.automation.design import DESIGN_PROMPT_RULE, DesignSpec, design_to_dict
from app.automation.form import form_for_context, render_options, style_rules
from app.automation.long_document import (
    Assembly,
    ChapterContent,
    chapters_to_expand,
    estimate_body_pages,
    normalize_outline,
    thinnest_chapters,
    trim_assembly,
    word_count,
)
from app.automation.md_blocks import (
    parse_markdown,
    split_closing_sections,
    unwrap_markdown_fence,
)
from app.automation.normalize import describe_validation_problems, normalize_blocks
from app.decisions.doc_quality import review_document
from app.tasks.dispatch import BlockSpec, unsourced_rules_apply
from app.tasks.pipeline import DRAFT_KEY, MARKDOWN_KEY
from app.tasks.queue import SubtaskJob
from app.tasks.rate_limit import is_rate_limited
from app.tasks.worker import NonRetryableSubtaskError, SubtaskExecutionError

if TYPE_CHECKING:
    from app.gateway.model_gateway import ModelGateway
    from app.tasks.dispatch import FleetSupport

logger = logging.getLogger(__name__)

DEFAULT_EXPAND_CONCURRENCY = 3
MAX_EXPAND_CHAPTERS = 6
MAX_CURRENT_TEXT_CHARS = 30_000
MAX_EXERCISES = 12
MIN_CHAPTER_WORDS = 30
CHAPTER_EXPAND_ASK = 1.25
# Model calls of chapters running at once in this process (a constant on purpose, no setting).
CHAPTER_CONCURRENCY = 3
_MAX_ERROR_CHARS = 300


class OutlineChapterSpec(BaseModel):
    """One chapter in the model's outline."""

    title: str
    subtopics: list[str] = Field(default_factory=list)
    target_pages: int = 4
    # A plain string with the allowed values in the schema: an unexpected kind is tolerated.
    kind: str = Field(default="concept", json_schema_extra={"enum": list(CHAPTER_KINDS)})
    # The rest of the plan for this chapter. ``subtopics`` is its must-cover list.
    goal: str = ""
    needs_code: bool = False
    needs_table: bool = False
    # Learning book only (ADR-0031): one sentence of the story in this chapter.
    story_beat: str = ""


class OutlineSpec(BaseModel):
    """The outline the model fills in (structured output)."""

    title: str
    subtitle: str = ""
    audience: str = ""
    level_progression: str = ""
    total_target_pages: int = 0
    # Non-empty ONLY when the request names no real subject: a question, and no invented outline.
    needs_clarification: str = ""
    user_topics: list[str] = Field(default_factory=list)
    include_exercises: bool = True
    include_summary: bool = True
    # Learning book only (ADR-0031): the person or scenario the whole book follows.
    story_premise: str = ""
    # The document language ("English", "Hindi", "Hinglish") and the cover/contents flags of the
    # plan. ``None`` means the request said nothing: the form (notes, report, book) decides.
    language: str = ""
    cover_page: bool | None = None
    toc: bool | None = None
    # The per-request visual design (automation/design.py), decided in this same call.
    design: DesignSpec | None = None
    chapters: list[OutlineChapterSpec] = Field(min_length=1, max_length=MAX_CHAPTERS * 2)


class ChapterSpec(BaseModel):
    """One chapter as the model writes it (structured output, flat block shape)."""

    title: str = ""
    blocks: list[BlockSpec] = Field(default_factory=list)
    key_takeaways: list[str] = Field(default_factory=list)
    exercises: list[str] = Field(default_factory=list)


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def _plain(err: ValueError) -> str:
    """Plain-language problems of a document validation error."""
    if isinstance(err, ValidationError):
        return "; ".join(describe_validation_problems(err))
    return str(err)


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items) or "- (cover the topic completely)"


# --------------------------------------------------------------------------- learning book
def _preferences_text(input_data: dict[str, Any]) -> str:
    value = input_data.get(PREFERENCES_KEY)
    return value if isinstance(value, str) else ""


def _provided_material(input_data: dict[str, Any]) -> str:
    """What the user supplied for the book (an attached file or pasted text), as untrusted input.

    Only readable attachments count; with none, the web search companions are used instead."""
    pieces: list[str] = []
    for key, output in (input_data.get("dependency_outputs") or {}).items():
        if (
            isinstance(output, dict)
            and output.get("agent") == "attachment"
            and str(output.get("content") or "").strip()
        ):
            pieces.append(f"[{output.get('name') or key}] {str(output['content'])[:3000]}")
    return "\n".join(pieces)[: lb.MAX_SOURCE_CHARS]


async def _companion_sources(
    gateway: ModelGateway, job: SubtaskJob, request: str
) -> tuple[str, list[dict[str, str]]]:
    """A short brief and the sources that were REALLY found by MAYA's grounded web search.

    Best effort: any failure, or an answer without sources, gives ``("", [])`` and the book is
    written without a source list (it never lists a source that was not fetched)."""
    prompt = lb.SOURCE_BRIEF_PROMPT.format(today=_today(), topic=request[:600])
    try:
        response = await gateway.generate_text(
            **search_call_kwargs("research"),
            contents=[prompt],
            workspace_id=job.workspace_id,
            task_id=job.task_id,
            enable_google_search=True,
        )
    except Exception as err:  # noqa: BLE001 - sources are an extra, never a reason to fail
        logger.warning("book_sources_unavailable task=%s error=%s", job.task_id, type(err).__name__)
        return "", []
    sources: list[dict[str, str]] = []
    for item in list(getattr(response, "citations", None) or [])[:4]:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or item.get("uri") or "").strip()
        if url.lower().startswith("https://") and all(s["url"] != url for s in sources):
            sources.append({"title": str(item.get("title") or url)[:120], "url": url[:500]})
    if not sources:
        return "", []
    return str(getattr(response, "text", "") or "").strip()[: lb.MAX_SOURCE_CHARS], sources


# --------------------------------------------------------------------------- outline
def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w+#.]+", " ", text.lower()).split())


_FORMAT_WORDS = frozenset(
    "notes note pdf docx doc word document documents pages page guide handbook book report "
    "summary cheatsheet sheet cheat study material file ebook".split()
)


def is_request_echo(topic: str, request: str) -> bool:
    """True when ``topic`` is only the request's own framing ("Java notes", "12 pages notes").

    Such an entry is not a subject: it must never become a subtopic, a bullet or a contents line.
    The subject alone ("Java", "Collections") is kept.
    """
    words = _norm(topic).split()
    if not words:
        return True
    request_words = set(_norm(request).split())
    return all(w in request_words or w.isdigit() for w in words) and any(
        w in _FORMAT_WORDS or w.isdigit() for w in words
    )


def drop_request_echoes(chapters: list[dict[str, Any]], request: str) -> list[str]:
    """Remove echo subtopics from the chapters; returns what was removed."""
    removed: list[str] = []
    for chapter in chapters:
        kept = []
        for sub in chapter.get("subtopics") or []:
            if is_request_echo(str(sub), request):
                removed.append(str(sub))
            else:
                kept.append(sub)
        chapter["subtopics"] = kept
    return removed


def ensure_topics_covered(chapters: list[dict[str, Any]], topics: list[str]) -> list[str]:
    """Make every topic the user listed appear in some chapter (title or subtopics).

    A topic that is missing is added as a subtopic of the chapter that already shares the most
    words with it (the one with the fewest subtopics on a tie), so the writer of that chapter
    covers it. Returns the topics that had to be added.
    """
    added: list[str] = []
    if not chapters:
        return added
    haystacks = [
        _norm(" ".join([str(c.get("title") or ""), *map(str, c.get("subtopics") or [])]))
        for c in chapters
    ]
    for topic in topics:
        wanted = _norm(topic)
        if not wanted or any(wanted in h for h in haystacks):
            continue
        words = set(wanted.split())

        def score(i: int, words: set[str] = words) -> tuple[int, int]:
            overlap = len(words & set(haystacks[i].split()))
            return overlap, -len(chapters[i].get("subtopics") or [])

        best = max(range(len(chapters)), key=score)
        chapters[best].setdefault("subtopics", []).append(topic)
        haystacks[best] = f"{haystacks[best]} {wanted}"
        added.append(topic)
    return added


def outline_from_dependencies(deps: dict[str, Any] | None) -> dict[str, Any] | None:
    """The outline dict from the outline step's output among a step's dependency outputs."""
    for output in (deps or {}).values():
        if isinstance(output, dict) and isinstance(output.get("outline"), dict):
            return dict(output["outline"])
    return None


def outline_text(outline: dict[str, Any]) -> str:
    """A readable text of the outline (the step's ``content``)."""
    lines = [str(outline.get("title") or "Document")]
    for chapter in outline.get("chapters") or []:
        lines.append(
            f"{chapter['index']}. {chapter['title']} ({chapter['kind']}, about "
            f"{chapter['target_pages']} pages)"
        )
        lines.extend(f"   - {topic}" for topic in chapter.get("subtopics") or [])
    return "\n".join(lines)


async def run_outline(
    gateway: ModelGateway, job: SubtaskJob, input_data: dict[str, Any]
) -> dict[str, Any]:
    """Plan the chapters. Returns the worker's ``{"result", "proof"}`` shape."""
    pipeline = input_data.get("pipeline") or {}
    slots = int(pipeline.get("slots") or 0)
    body_pages = float(pipeline.get("body_pages") or 0)
    request = str(pipeline.get("request") or input_data.get("description") or "")
    if slots < 1 or body_pages <= 0:
        raise NonRetryableSubtaskError("The outline step has no chapter count or page budget")
    book = pipeline.get("mode") == LB_MODE
    sources: list[dict[str, str]] = []
    brief = ""
    if book:
        code_language = str(pipeline.get("code_language") or "")
        if not code_language:
            code_language = detect_code_language(request, _preferences_text(input_data))
        material = _provided_material(input_data)
        if material:
            brief = material
        else:
            brief, sources = await _companion_sources(gateway, job, request)
        prompt = lb.build_plan_prompt(
            today=_today(),
            request=request,
            chapters=slots,
            total_words=int(pipeline.get("total_words") or round(body_pages * 450)),
            pages_per_chapter=pipeline.get("pages_per_chapter"),
            body_pages=round(body_pages),
            level=str(pipeline.get("level") or lb.DEFAULT_LEVEL),
            code_language=code_language,
            language=str(pipeline.get("notes_language") or "English"),
            material=brief,
        ) + requirements_block(input_data)
    else:
        prompt = (
            OUTLINE_USER_PROMPT.format(
                today=_today(),
                request=request,
                chapters=slots,
                body_pages=round(body_pages),
                pages_per_chapter=pipeline.get("pages_per_chapter"),
                language_pin=language_pin(request),
            )
            + requirements_block(input_data)
            + style_rules(form_for_context(input_data))
            + DESIGN_PROMPT_RULE
        )
    try:
        spec: OutlineSpec = await gateway.generate_structured(
            **agent_call_kwargs("longdoc"),
            max_output_tokens=LONG_OUTPUT_TOKENS,
            contents=[prompt],
            response_schema=OutlineSpec,
            system_instruction=lb.PLAN_SYSTEM_PROMPT if book else OUTLINE_SYSTEM_PROMPT,
            workspace_id=job.workspace_id,
            task_id=job.task_id,
        )
    except Exception as err:
        raise SubtaskExecutionError(
            f"Outline model call failed: {str(err)[:_MAX_ERROR_CHARS]}"
        ) from err
    if spec.needs_clarification.strip():
        raise NonRetryableSubtaskError(
            "I need a little more detail before writing this: "
            + spec.needs_clarification.strip()[:300]
        )
    chapters = normalize_outline([c.model_dump() for c in spec.chapters], slots, body_pages)
    drop_request_echoes(chapters, request)
    topics = [t.strip() for t in spec.user_topics if t.strip() and not is_request_echo(t, request)][
        :40
    ]
    topics_added = ensure_topics_covered(chapters, topics)
    outline = {
        "title": spec.title.strip() or "Study notes",
        "subtitle": spec.subtitle.strip(),
        "audience": spec.audience.strip(),
        "level_progression": spec.level_progression.strip(),
        "include_exercises": bool(spec.include_exercises),
        "include_summary": bool(spec.include_summary),
        "language": spec.language.strip(),
        "cover_page": spec.cover_page,
        "toc": spec.toc,
        "design": design_to_dict(spec.design),
        "slots": slots,
        "body_pages": body_pages,
        "target_pages": pipeline.get("target_pages"),
        "request": request,
        "chapters": chapters,
        "topics": topics,
        "topics_added": topics_added,
    }
    if book:
        outline.update(
            mode=LB_MODE,
            level=str(pipeline.get("level") or lb.DEFAULT_LEVEL),
            code_language=code_language,
            notes_language=str(pipeline.get("notes_language") or "English"),
            destination=str(pipeline.get("destination") or "pdf"),
            story_premise=spec.story_premise.strip()[:600],
            sources=sources,
            source_brief=brief[: lb.MAX_SOURCE_CHARS],
            include_exercises=False,
            include_summary=False,
        )
    text = outline_text(outline)
    return {
        "result": {
            "summary": f"Outline: {len(chapters)} chapters for '{outline['title']}'",
            "content": text,
            "outline": outline,
            "agent": "executor",
            "citations": [],
        },
        "proof": {
            "status": "unverified",
            "kind": "llm_outline",
            "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "char_count": len(text),
        },
    }


# --------------------------------------------------------------------------- chapters
_CALLOUT_TAGS = {"info": "NOTE", "success": "TIP", "warning": "WARNING"}


def blocks_to_text(blocks: list[dict[str, Any]], limit: int = MAX_CURRENT_TEXT_CHARS) -> str:
    """Markdown of blocks (the "current chapter" shown to an expand call, in the same format the
    model is asked to write)."""
    lines: list[str] = []
    for block in blocks:
        kind = block.get("type")
        if kind == "heading":
            lines.append(f"{'#' * int(block.get('level') or 2)} {block.get('text')}")
        elif kind == "paragraph":
            lines.append(str(block.get("text") or ""))
        elif kind == "quote":
            lines.append(f"> {block.get('text')}")
        elif kind == "callout":
            tag = _CALLOUT_TAGS.get(str(block.get("kind")), "NOTE")
            lines.append(f"> [!{tag}] {block.get('text')}")
        elif kind == "bullets":
            lines.extend(f"- {item}" for item in block.get("items") or [])
        elif kind == "numbered":
            lines.extend(f"{n}. {item}" for n, item in enumerate(block.get("items") or [], 1))
        elif kind == "code":
            lines.append(f"```{block.get('language') or ''}\n{block.get('text')}\n```")
        elif kind == "table":
            headers = [str(h) for h in block.get("headers") or []]
            lines.append("| " + " | ".join(headers) + " |")
            lines.append("|" + "---|" * len(headers))
            lines.extend(
                "| " + " | ".join(str(c) for c in r) + " |" for r in block.get("rows") or []
            )
        lines.append("")
    text = "\n".join(lines)
    return text if len(text) <= limit else text[:limit] + "\n[... shortened ...]"


def chapter_from_spec(
    spec: ChapterSpec, brief: dict[str, Any], outline: dict[str, Any]
) -> ChapterContent:
    """Validate the model's chapter into :class:`ChapterContent` or raise a retryable error."""
    blocks, _notes = normalize_blocks([b.to_block() for b in spec.blocks])
    blocks = [b for b in blocks if b.get("type") not in ("page_break", "image")]
    try:
        doc_model.document_from_dict({"title": "chapter", "blocks": blocks})
    except ValueError as err:
        problems = _plain(err)
        raise SubtaskExecutionError(
            f"Chapter {brief['index']} has an invalid layout: {problems[:_MAX_ERROR_CHARS]}"
        ) from err
    if word_count(blocks) < MIN_CHAPTER_WORDS:
        raise SubtaskExecutionError(f"Chapter {brief['index']} came back empty")
    takeaways = [t.strip() for t in spec.key_takeaways if t.strip()][:8]
    exercises = [e.strip() for e in spec.exercises if e.strip()][:MAX_EXERCISES]
    return ChapterContent(
        index=int(brief["index"]),
        title=str(brief["title"]),
        kind=str(brief.get("kind") or "concept"),
        target_pages=float(brief.get("target_pages") or 5),
        target_words=int(brief.get("target_words") or 0),
        blocks=blocks,
        exercises=exercises if outline.get("include_exercises") else [],
        takeaways=takeaways if outline.get("include_summary") else [],
    )


class ChapterTooThinError(SubtaskExecutionError):
    """The chapter text had (almost) no usable content: the only error that is retried with the
    problem named, and then falls back to the structured chapter call."""


def chapter_from_markdown(
    text: str, brief: dict[str, Any], outline: dict[str, Any]
) -> ChapterContent:
    """The chapter written as Markdown, as :class:`ChapterContent` (or a retryable error).

    The chapter's own title and level-1 heading are dropped (the assembler writes "Chapter N:
    title"); a closing "Key takeaways" and "Exercises" section become the takeaways and exercises
    lists. Nothing else is removed: a table that was ragged is repaired by the parser, not
    rejected."""
    parsed = parse_markdown(unwrap_markdown_fence(text))
    blocks = [b for b in parsed.blocks if b.get("type") not in ("page_break", "image")]
    title = " ".join(str(brief.get("title") or "").lower().split())
    while blocks and blocks[0].get("type") == "heading":
        first = " ".join(str(blocks[0].get("text") or "").lower().split())
        if int(blocks[0].get("level") or 2) == 1 or first == title:
            blocks.pop(0)
        else:
            break
    blocks, takeaways, exercises = split_closing_sections(blocks)
    words = word_count(blocks)
    if words < MIN_CHAPTER_WORDS:
        raise ChapterTooThinError(
            f"Chapter {brief['index']} came back empty or far too short ({words} words)"
        )
    try:
        doc_model.document_from_dict({"title": "chapter", "blocks": blocks})
    except ValueError as err:
        raise SubtaskExecutionError(
            f"Chapter {brief['index']} has an invalid layout: {_plain(err)[:_MAX_ERROR_CHARS]}"
        ) from err
    return ChapterContent(
        index=int(brief["index"]),
        title=str(brief["title"]),
        kind=str(brief.get("kind") or "concept"),
        target_pages=float(brief.get("target_pages") or 5),
        target_words=int(brief.get("target_words") or 0),
        blocks=blocks,
        exercises=[e.strip() for e in exercises if e.strip()][:MAX_EXERCISES]
        if outline.get("include_exercises")
        else [],
        takeaways=[t.strip() for t in takeaways if t.strip()][:8]
        if outline.get("include_summary")
        else [],
    )


def _chapter_list(outline: dict[str, Any]) -> str:
    return "\n".join(
        f"{c['index']}. {c['title']} - {c['kind']}" for c in outline.get("chapters") or []
    )


def _word_window(target_words: int) -> tuple[int, int]:
    return round(target_words * 0.85), round(target_words * 1.3)


_GATES: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    weakref.WeakKeyDictionary()
)


def _gate() -> asyncio.Semaphore:
    """The semaphore that bounds concurrent chapter model calls (one per event loop)."""
    loop = asyncio.get_running_loop()
    gate = _GATES.get(loop)
    if gate is None:
        gate = _GATES[loop] = asyncio.Semaphore(CHAPTER_CONCURRENCY)
    return gate


async def _call_markdown(
    gateway: ModelGateway,
    job: SubtaskJob,
    prompt: str,
    chapter_index: int,
    system: str = CHAPTER_MD_SYSTEM_PROMPT,
) -> str:
    """One chapter call answered as text. ``chapter:{index}`` spreads chapters over the keys."""
    async with _gate():
        try:
            response = await gateway.generate_text(
                **agent_call_kwargs(f"chapter:{chapter_index}"),
                max_output_tokens=LONG_OUTPUT_TOKENS,
                contents=[prompt],
                system_instruction=system,
                workspace_id=job.workspace_id,
                task_id=job.task_id,
            )
        except Exception as err:
            raise SubtaskExecutionError(
                f"Chapter model call failed: {str(err)[:_MAX_ERROR_CHARS]}"
            ) from err
    return str(getattr(response, "text", "") or "")


async def _call_json(
    gateway: ModelGateway, job: SubtaskJob, prompt: str, chapter_index: int
) -> ChapterSpec:
    """The old structured chapter call: the last fallback when Markdown gave too little."""
    async with _gate():
        try:
            spec: ChapterSpec = await gateway.generate_structured(
                **agent_call_kwargs(f"chapter:{chapter_index}"),
                max_output_tokens=LONG_OUTPUT_TOKENS,
                contents=[prompt],
                response_schema=ChapterSpec,
                system_instruction=CHAPTER_SYSTEM_PROMPT,
                workspace_id=job.workspace_id,
                task_id=job.task_id,
            )
        except Exception as err:
            raise SubtaskExecutionError(
                f"Chapter model call failed: {str(err)[:_MAX_ERROR_CHARS]}"
            ) from err
    return spec


async def _write_chapter(
    gateway: ModelGateway,
    job: SubtaskJob,
    prompt: str,
    brief: dict[str, Any],
    outline: dict[str, Any],
    *,
    json_prompt: str | None = None,
    system: str = CHAPTER_MD_SYSTEM_PROMPT,
    capture: dict[str, str] | None = None,
) -> ChapterContent:
    """Write one chapter as Markdown.

    ``json_prompt`` set means a full chapter run: a failed first answer is asked again ONCE with
    the problem named, and when that answer is still too thin the structured call (``json_prompt``)
    is the last try. Without it (an expansion) there is exactly one attempt. A rate limit is never
    retried here: the worker backs off and retries the step."""
    index = int(brief["index"])
    attempts = 2 if json_prompt is not None else 1
    problem = ""
    error: SubtaskExecutionError | None = None
    for _attempt in range(attempts):
        try:
            text = await _call_markdown(
                gateway,
                job,
                prompt + (CHAPTER_RETRY_NOTE.format(problem=problem) if problem else ""),
                index,
                system,
            )
            chapter = chapter_from_markdown(text, brief, outline)
            if capture is not None:
                capture["markdown"] = text  # the learning book keeps the model's own Markdown
            return chapter
        except SubtaskExecutionError as err:
            if is_rate_limited(err):
                raise
            error, problem = err, str(err)[:_MAX_ERROR_CHARS]
            logger.warning(
                "chapter_attempt_failed task=%s chapter=%s error=%s", job.task_id, index, problem
            )
    assert error is not None  # noqa: S101 - the loop ran at least once
    if json_prompt is not None and isinstance(error, ChapterTooThinError):
        logger.warning("chapter_json_fallback task=%s chapter=%s", job.task_id, index)
        return chapter_from_spec(await _call_json(gateway, job, json_prompt, index), brief, outline)
    raise error


def _chapter_values(outline: dict[str, Any], brief: dict[str, Any], total: int) -> dict[str, Any]:
    low, high = _word_window(int(brief["target_words"]))
    return {
        "today": _today(),
        "number": brief["index"],
        "total": total,
        "title": outline["title"],
        "audience": outline.get("audience") or "the reader named in the request",
        "level_progression": outline.get("level_progression") or "gentle to advanced",
        "chapter_list": _chapter_list(outline),
        "chapter_title": brief["title"],
        "kind": brief.get("kind") or "concept",
        "subtopics": _bullets(list(brief.get("subtopics") or [])),
        "target_pages": brief["target_pages"],
        "target_words": brief["target_words"],
        "min_words": low,
        "max_words": high,
        "takeaways": "3 to 6 key takeaways"
        if outline.get("include_summary")
        else "none (leave out the section)",
        "exercises": "4 to 8 exercises"
        if outline.get("include_exercises")
        else "none (leave out the section)",
        "request": outline.get("request") or "",
        "language_pin": language_pin(str(outline.get("request") or "")),
    }


def _chapter_needs(outline: dict[str, Any], brief: dict[str, Any]) -> str:
    wants = []
    if brief.get("needs_code"):
        wants.append("real, runnable code examples with the language named on each fenced block")
    if brief.get("needs_table"):
        wants.append("at least one comparison or reference table")
    language = str(outline.get("language") or "").strip()
    if language:
        wants.append(f"written in {language}")
    return ("Needs: " + "; ".join(wants) + ".\n") if wants else ""


def _chapter_prompt(outline: dict[str, Any], brief: dict[str, Any], total: int) -> str:
    """The Markdown chapter prompt."""
    return CHAPTER_MD_USER_PROMPT.format(
        **_chapter_values(outline, brief, total),
        goal=brief.get("goal") or "teach the topics below completely and correctly",
        needs=_chapter_needs(outline, brief),
    )


def _chapter_prompt_json(outline: dict[str, Any], brief: dict[str, Any], total: int) -> str:
    """The prompt of the structured fallback call."""
    return CHAPTER_USER_PROMPT.format(**_chapter_values(outline, brief, total))


async def run_chapter(
    gateway: ModelGateway, job: SubtaskJob, input_data: dict[str, Any]
) -> dict[str, Any]:
    """Write one chapter. Returns the worker's ``{"result", "proof"}`` shape."""
    pipeline = input_data.get("pipeline") or {}
    index = int(pipeline.get("index") or 0)
    total = int(pipeline.get("of") or 0)
    outline = outline_from_dependencies(input_data.get("dependency_outputs"))
    if outline is None:
        raise NonRetryableSubtaskError("The outline is not available, so no chapter can be written")
    brief = next(
        (c for c in outline.get("chapters") or [] if int(c.get("index") or 0) == index), None
    )
    if brief is None:
        return {
            "result": {
                "summary": f"Chapter {index} is not needed: the outline has fewer chapters.",
                "skipped": True,
                "agent": "executor",
                "citations": [],
            },
            "proof": {"status": "skipped", "kind": "chapter_unused"},
        }
    extras = (
        requirements_block(input_data)
        + style_rules(form_for_context(input_data))
        + (UNSOURCED_PROMPT_RULE if unsourced_rules_apply(input_data) else "")
    )
    book = outline.get("mode") == LB_MODE
    captured: dict[str, str] = {}
    if book:
        # The learning book (ADR-0031): its own chapter prompt (story thread, section template);
        # the user's saved preferences and requirements are added like for any chapter.
        chapter_prompt = lb.build_chapter_prompt(
            outline, brief, total, today=_today()
        ) + requirements_block(input_data)
        chapter = await _write_chapter(
            gateway,
            job,
            chapter_prompt,
            brief,
            outline,
            json_prompt=_chapter_prompt_json(outline, brief, total) + extras,
            system=lb.chapter_system_prompt(outline),
            capture=captured,
        )
    else:
        chapter = await _write_chapter(
            gateway,
            job,
            _chapter_prompt(outline, brief, total) + extras,
            brief,
            outline,
            json_prompt=_chapter_prompt_json(outline, brief, total) + extras,
        )
    expansion = ""
    floor = round(chapter.target_words * CHAPTER_WORDS_FLOOR_FRACTION)
    # Measured in the pipeline's own unit (prose-equivalent words, 450 a page: code and table rows
    # cost less text than their share of a page), the unit the chapter prompt asked in.
    measured = _equivalent_words(chapter)
    # ADR-0029: an advisory model review of the chapter text. A flagged chapter uses the SAME one
    # expansion call below (the rounds are unchanged); no answer leaves this step as it was.
    review = await review_document(
        str(outline.get("request") or outline.get("title") or ""),
        f"Chapter {chapter.index}: {chapter.title}" + chr(10) + blocks_to_text(chapter.blocks),
        "chapter",
    )
    short = bool(chapter.target_words) and measured < floor
    weak = review is not None and review.flagged and bool(chapter.target_words)
    if short or weak:
        # One expansion call with the measured numbers; never a second.
        before = measured
        if short:
            why = (
                f"has chapter {chapter.index} at only {before} words against "
                f"{chapter.target_words} planned"
            )
        else:
            reasons = "; ".join(review.reasons) if review is not None else ""
            why = (
                f"was reviewed and found weak ({reasons}): rewrite it with specific, "
                "non-repetitive content and no placeholders"
            )
        try:
            chapter = await _rewrite_longer(
                gateway,
                job,
                outline,
                chapter,
                why,
                # Ask for more than the target: a model that fell short once tends to again.
                round(chapter.target_words * CHAPTER_EXPAND_ASK),
                capture=captured,
            )
            expansion = f"; expanded once from {before} words"
        except Exception as err:  # noqa: BLE001 - the short first version is still a chapter
            logger.warning(
                "chapter_expand_failed task=%s chapter=%s error=%s",
                job.task_id,
                index,
                type(err).__name__,
            )
            expansion = f"; still short ({before} words, expansion failed)"
        else:
            if _equivalent_words(chapter) < floor:
                expansion += f", still short ({_equivalent_words(chapter)} words)"
    return {
        "result": {
            "summary": (
                f"Chapter {chapter.index}: {chapter.title} "
                f"({chapter.words} words, target {chapter.target_words}{expansion})"
            ),
            "chapter": {
                "index": chapter.index,
                "title": chapter.title,
                "words": chapter.words,
                "target_words": chapter.target_words,
                "expanded": expansion.startswith("; expanded"),
            },
            DRAFT_KEY: chapter.to_dict(),
            **(
                {
                    MARKDOWN_KEY: clean_chapter_markdown(
                        captured.get("markdown") or blocks_to_text(chapter.blocks), chapter.title
                    )
                }
                if book
                else {}
            ),
            "agent": "executor",
            "citations": [],
        },
        "proof": {
            "status": "unverified",
            "kind": "llm_chapter",
            "words": chapter.words,
            "blocks": len(chapter.blocks),
            **({"quality_review": review.as_dict()} if review is not None else {}),
        },
    }


# --------------------------------------------------------------------------- assemble
MAX_ASSEMBLE_CHAPTER_RETRIES = 3  # one more try per missing chapter, at most this many chapters


def _missing_chapters(
    outline: dict[str, Any], drafts: dict[int, ChapterContent]
) -> list[tuple[int, str]]:
    return [
        (int(c["index"]), str(c["title"]))
        for c in outline.get("chapters") or []
        if int(c["index"]) not in drafts
    ]


def missing_chapters_note(missing: list[tuple[int, str]]) -> list[dict[str, Any]]:
    """The visible note a document carries when chapters could not be written (never silent)."""
    if not missing:
        return []
    names = "; ".join(f"Chapter {i}: {t}" for i, t in missing)
    return [
        {
            "type": "callout",
            "kind": "warning",
            "text": (
                f"Not included: {names}. This part could not be written, so the document is "
                "incomplete. Ask MAYA to write it again."
            )[: doc_model.MAX_PARAGRAPH_CHARS],
        }
    ]


def _assembly_blocks(assembly: Assembly) -> list[dict[str, Any]]:
    """The assembled blocks, followed by the missing-chapter note when a chapter is absent."""
    blocks = assembly.document_dict(theme="notes", author="", date="")["blocks"]
    return [*blocks, *missing_chapters_note(assembly.missing)]


async def _retry_missing_chapters(
    support: FleetSupport,
    job: SubtaskJob,
    ctx: dict[str, Any],
    deps: dict[str, Any],
    outline: dict[str, Any],
    drafts: dict[int, ChapterContent],
    markdowns: dict[int, str] | None = None,
) -> None:
    """One more try for each chapter that failed its own step retries (bounded, never raises).

    Fills ``drafts`` in place with the chapters that now exist. Skipped without a model gateway."""
    gateway = support.gateway
    if gateway is None:
        return
    todo = [i for i, _ in _missing_chapters(outline, drafts)][:MAX_ASSEMBLE_CHAPTER_RETRIES]
    total = len(outline.get("chapters") or [])
    gate = asyncio.Semaphore(max(1, getattr(support, "expand_concurrency", 1)))

    async def one(index: int) -> dict[str, Any]:
        async with gate:
            data = {**ctx, "pipeline": {"index": index, "of": total}, "dependency_outputs": deps}
            return await run_chapter(gateway, job, data)

    results = await asyncio.gather(*(one(i) for i in todo), return_exceptions=True)
    for index, result in zip(todo, results, strict=True):
        body = (result.get("result") or {}) if isinstance(result, dict) else {}
        draft = body.get(DRAFT_KEY)
        if isinstance(draft, dict):
            drafts[index] = ChapterContent.from_dict(draft)
            text = body.get(MARKDOWN_KEY)
            if markdowns is not None and isinstance(text, str) and text.strip():
                markdowns[index] = text
        else:
            logger.warning("assemble_chapter_retry_failed task=%s chapter=%s", job.task_id, index)


def _safe_title(title: str) -> str:
    return title.strip() or "Study notes"


async def assemble_context(
    support: FleetSupport, job: SubtaskJob, ctx: dict[str, Any]
) -> dict[str, Any]:
    """The document context for the ``assemble`` step, built without a model call."""
    # local: dispatch imports this module lazily
    from app.tasks.dispatch import _apply_user_style, safe_filename

    deps = ctx.get("dependency_outputs") or {}
    outline = outline_from_dependencies(deps)
    if outline is None:
        raise NonRetryableSubtaskError(
            "The outline step produced no outline, so nothing can be assembled"
        )
    drafts: dict[int, ChapterContent] = {}
    markdowns: dict[int, str] = {}
    for output in deps.values():
        if isinstance(output, dict) and isinstance(output.get(DRAFT_KEY), dict):
            chapter = ChapterContent.from_dict(output[DRAFT_KEY])
            drafts[chapter.index] = chapter
            if isinstance(output.get(MARKDOWN_KEY), str):
                markdowns[chapter.index] = output[MARKDOWN_KEY]
    missing = _missing_chapters(outline, drafts)
    if missing:
        await _retry_missing_chapters(support, job, ctx, deps, outline, drafts, markdowns)
        recovered = [index for index, _ in missing if index in drafts]
        missing = _missing_chapters(outline, drafts)
        if recovered:
            logger.info("assemble_chapter_recovered task=%s chapters=%s", job.task_id, recovered)
    if not drafts:
        raise NonRetryableSubtaskError(
            f"None of the {len(outline.get('chapters') or [])} chapters could be written, "
            "so there is no document to assemble"
        )
    if outline.get("mode") == LB_MODE and outline.get("destination") == "notion":
        return _notion_book_context(job, ctx, outline, drafts, markdowns, missing)
    body_pages = float((ctx.get("pipeline") or {}).get("body_pages") or 0)
    options = render_options(ctx, int(body_pages) or None)
    options.setdefault("theme", "notes")
    options["toc"] = True  # a long document always has a contents list; notes drop the cover
    if outline.get("cover_page") is not None:
        options["cover_page"] = bool(outline["cover_page"])  # the plan's own flag
    assembly = Assembly(
        cover_page=bool(options["cover_page"]),
        page_breaks=bool(options["page_breaks"]),
        title=_safe_title(str(outline.get("title") or "")),
        subtitle=str(outline.get("subtitle") or ""),
        chapters=[drafts[i] for i in sorted(drafts)],
        missing=missing,
        with_exercises=bool(outline.get("include_exercises")),
    )
    document_blocks = _assembly_blocks(assembly)
    out = dict(ctx)
    file_type = str(ctx.get("file_type") or "pdf")
    document = assembly.document_dict(
        theme=str(options["theme"]), author="MAYA AI", date=_today(), options=options
    )
    document["blocks"] = document_blocks
    design = outline.get("design") or None
    if design:
        document["design"] = design
    try:
        doc_model.document_from_dict(document)
    except ValueError as err:
        raise NonRetryableSubtaskError(
            f"The assembled document is invalid: {_plain(err)[:_MAX_ERROR_CHARS]}"
        ) from err
    out.update(
        file_type=file_type,
        filename=f"{job.task_id}/{safe_filename(assembly.title, file_type)}",
        title=document["title"],
        subtitle=document["subtitle"] or "",
        theme=document["theme"],
        author=document["author"] or "",
        date=document["date"] or "",
        cover_page=document["cover_page"],
        toc=document["toc"],
        density=document["density"],
        accent_color=document["accent_color"],
        blocks=document["blocks"],
    )
    if design:
        out["design"] = design
    _apply_user_style(out, ctx)
    document["design"] = out.get("design")
    out["assembly"] = assembly
    out["outline"] = outline
    if missing:
        out["spec_warnings"] = [
            *(ctx.get("spec_warnings") or []),
            "Missing from the document (the chapter could not be written): "
            + "; ".join(f"Chapter {i}: {t}" for i, t in missing),
        ]
        out["spec_healing"] = [
            *(ctx.get("spec_healing") or []),
            {
                "attempt": "missing_chapters",
                "reason": "; ".join(f"Chapter {i}: {t}" for i, t in missing),
            },
        ]
    return out


def _notion_book_context(
    job: SubtaskJob,
    ctx: dict[str, Any],
    outline: dict[str, Any],
    drafts: dict[int, ChapterContent],
    markdowns: dict[int, str],
    missing: list[tuple[int, str]],
) -> dict[str, Any]:
    """The assemble context of a learning book for Notion: ONE Markdown file (no model call).

    The document agent writes it as an ``.md`` artifact; the approval of the Notion page is bound
    to its exact bytes (``app.mcp.notion_book``)."""
    from app.tasks.dispatch import safe_filename  # local: dispatch imports this module lazily

    chapters = [
        (
            index,
            drafts[index].title,
            markdowns.get(index) or blocks_to_text(drafts[index].blocks, limit=10**9),
        )
        for index in sorted(drafts)
    ]
    title = _safe_title(str(outline.get("title") or ""))
    markdown = build_book_markdown(
        subtitle=str(outline.get("subtitle") or ""), chapters=chapters, missing=missing
    )
    out = {k: v for k, v in ctx.items() if k not in ("acceptance", "blocks", "sections")}
    out.update(
        file_type="md",
        filename=f"{job.task_id}/{safe_filename(title, 'md')}",
        title=title,
        content=markdown,
        outline=outline,
    )
    if missing:
        names = "; ".join(f"Chapter {i}: {t}" for i, t in missing)
        out["spec_warnings"] = [
            *(ctx.get("spec_warnings") or []),
            f"Missing from the book (the chapter could not be written): {names}",
        ]
    return out


# --------------------------------------------------------------------------- heal
def _pages_target(outcome: AcceptanceResult) -> tuple[int, int] | None:
    """``(requested pages, actual pages)`` from the outcome (words are converted to pages)."""
    if "pages" in outcome.requested:
        return outcome.requested["pages"], outcome.actual["pages"]
    if "words" in outcome.requested:
        return (
            max(1, round(outcome.requested["words"] / NOTES_WORDS_PER_PAGE)),
            max(0, round(outcome.actual["words"] / NOTES_WORDS_PER_PAGE)),
        )
    return None


def _equivalent_words(chapter: ChapterContent) -> int:
    return round(estimate_body_pages(chapter.blocks) * NOTES_WORDS_PER_PAGE)


async def _rewrite_longer(
    gateway: ModelGateway,
    job: SubtaskJob,
    outline: dict[str, Any],
    chapter: ChapterContent,
    shortfall_text: str,
    new_words: int,
    capture: dict[str, str] | None = None,
) -> ChapterContent:
    """One rewrite of ``chapter`` to about ``new_words`` words (more depth, never filler)."""
    brief = {
        "index": chapter.index,
        "title": chapter.title,
        "kind": chapter.kind,
        "target_pages": round(new_words / NOTES_WORDS_PER_PAGE, 1),
        "target_words": new_words,
    }
    spec_topics = next(
        (c.get("subtopics") or [] for c in outline["chapters"] if c["index"] == chapter.index), []
    )
    prompt = EXPAND_USER_PROMPT.format(
        today=_today(),
        shortfall_text=shortfall_text,
        number=chapter.index,
        chapter_title=chapter.title,
        target_words=new_words,
        current_words=round(estimate_body_pages(chapter.blocks) * NOTES_WORDS_PER_PAGE),
        min_words=round(new_words * 0.85),
        other_titles=", ".join(
            str(c["title"]) for c in outline["chapters"] if c["index"] != chapter.index
        ),
        title=outline["title"],
        audience=outline.get("audience") or "the reader named in the request",
        level_progression=outline.get("level_progression") or "gentle to advanced",
        subtopics="; ".join(spec_topics),
        takeaways="3 to 6 key takeaways" if outline.get("include_summary") else "none",
        exercises="4 to 8 exercises" if outline.get("include_exercises") else "none",
        current_text=blocks_to_text(chapter.blocks),
        language_pin=language_pin(str(outline.get("request") or "")),
    )
    attempt: dict[str, str] = {}
    rewritten = await _write_chapter(gateway, job, prompt, brief, outline, capture=attempt)
    if rewritten.words <= chapter.words:
        raise SubtaskExecutionError(f"Chapter {chapter.index} did not get longer")
    if capture is not None and attempt:
        capture.update(attempt)
    return rewritten


async def _expand_one(
    support: FleetSupport,
    job: SubtaskJob,
    outline: dict[str, Any],
    chapter: ChapterContent,
    shortfall_text: str,
    new_words: int,
    gate: asyncio.Semaphore,
) -> ChapterContent:
    async with gate:
        return await _rewrite_longer(
            support.gateway,  # type: ignore[arg-type]
            job,
            outline,
            chapter,
            shortfall_text,
            new_words,
        )


async def heal_assembly(
    support: FleetSupport,
    job: SubtaskJob,
    ctx: dict[str, Any],
    outcome: AcceptanceResult,
    measured: dict[str, int | None],
) -> dict[str, Any] | None:
    """One bounded expand (too short) or trim (too long) round for an assembled document.

    Returns the context fields to merge (new ``blocks``, the ``assembly`` and a ``healing``
    trail) or ``None`` when nothing could be changed. The caller re-renders and re-checks.
    """
    assembly: Assembly | None = ctx.get("assembly")
    outline: dict[str, Any] | None = ctx.get("outline")
    pair = _pages_target(outcome)
    if assembly is None or outline is None or pair is None:
        return None
    target, actual = pair
    low, high = pages_bounds(target)
    words = measured.get("words")
    floor = words_floor(target)
    words_low = words is not None and words < floor
    tried: list[str] = list(ctx.get("heal_tried") or [])

    def mark(result: dict[str, Any] | None, step: str) -> dict[str, Any] | None:
        if result is not None:
            result["heal_tried"] = [*tried, step]
        return result

    if actual > high:
        if not words_low:
            # Enough words: trim low-priority sections, but never below the words floor.
            trimmed = _trim(assembly, target, actual, words=words, floor=floor)
            if trimmed is not None:
                return mark(trimmed, "trim")
            if "compact" not in tried:
                return mark(_compact(actual, words, target), "compact")
            return None
        # Too many pages AND too few words: compact the layout, then expand, and only then trim.
        if "compact" not in tried:
            return mark(_compact(actual, words, target), "compact")
        if "expand" not in tried:
            expanded = await _expand(support, job, ctx, assembly, outline, target, actual, words)
            if expanded is not None:
                return mark(expanded, "expand")
        return mark(_trim(assembly, target, actual), "trim")
    # Pages are a layout result: a page count inside the band with too few words is thin, not
    # long enough. It gets the same expand round, sized in words.
    thin = "pages" in outcome.requested and words_low
    if actual < low or thin:
        return mark(
            await _expand(support, job, ctx, assembly, outline, target, actual, words), "expand"
        )
    return None


def _compact(actual: int, words: int | None, target: int) -> dict[str, Any]:
    """Render the same content denser (``density: compact``); the caller re-measures."""
    return {
        "density": "compact",
        "healing": [
            {
                "attempt": "compact_layout",
                "reason": f"{actual} pages"
                + (f" and {words:,} words" if words is not None else "")
                + f" against {target} requested: printed denser to keep every section",
            }
        ],
    }


async def _expand(
    support: FleetSupport,
    job: SubtaskJob,
    ctx: dict[str, Any],
    assembly: Assembly,
    outline: dict[str, Any],
    target: int,
    actual: int,
    words: int | None = None,
) -> dict[str, Any] | None:
    shortfall = max(1.0, float(target - actual))
    expected_words = target * NOTES_WORDS_PER_PAGE
    shortfall_text = f"was {actual} pages but {target} were required"
    if words is not None and words < words_floor(target):
        # Words, not pages: aim at the requested length, at least 10% above the words floor (85%
        # of the nominal length alone is BELOW the floor, so the round could never pass).
        aim = max(round(expected_words * 0.85), round(words_floor(target) * 1.1))
        shortfall = max(shortfall, (aim - words) / NOTES_WORDS_PER_PAGE)
        shortfall_text = (
            f"has {actual} pages but only {words:,} words (about {expected_words:,} are needed "
            f"for {target} pages)"
        )
    count = chapters_to_expand(assembly.chapters, shortfall, MAX_EXPAND_CHAPTERS)
    chosen = thinnest_chapters(assembly.chapters, count)
    # Aim a little over the shortfall: the estimate of what a rewrite yields is rough.
    extra_words = round(shortfall * 1.15 * NOTES_WORDS_PER_PAGE / max(1, len(chosen)))
    gate = asyncio.Semaphore(
        max(1, getattr(support, "expand_concurrency", DEFAULT_EXPAND_CONCURRENCY))
    )
    jobs = [
        _expand_one(
            support,
            job,
            outline,
            chapter,
            shortfall_text,
            round(estimate_body_pages(chapter.blocks) * NOTES_WORDS_PER_PAGE) + extra_words,
            gate,
        )
        for chapter in chosen
    ]
    results = await asyncio.gather(*jobs, return_exceptions=True)
    done: list[int] = []
    failed: list[str] = []
    for chapter, result in zip(chosen, results, strict=True):
        if isinstance(result, ChapterContent):
            position = assembly.chapters.index(chapter)
            assembly.chapters[position] = result
            done.append(chapter.index)
        else:
            reason = "rate limited" if is_rate_limited(result) else str(result)[:120]
            failed.append(f"chapter {chapter.index} ({reason})")
    if not done:
        logger.warning("expand_round_no_chapter_changed task=%s", job.task_id)
        return None
    reason = (
        f"{actual} pages"
        + (f" and {words:,} words" if words is not None else "")
        + f" against {target} requested: rewrote "
        f"{', '.join(f'chapter {i}' for i in sorted(done))} with more depth"
    )
    if failed:
        reason += f"; could not rewrite {', '.join(failed)}"
    return {
        "blocks": _assembly_blocks(assembly),
        "assembly": assembly,
        "healing": [{"attempt": "expand_chapters", "reason": reason}],
    }


def _trim(
    assembly: Assembly,
    target: int,
    actual: int,
    *,
    words: int | None = None,
    floor: int | None = None,
) -> dict[str, Any] | None:
    estimate = assembly.estimated_pages()
    scale = actual / estimate if estimate > 0 else 1.0
    # Calibrate the estimate with the real page count, then aim a little over the request.
    aim = target * 1.1 / scale if scale > 0 else target * 1.1
    if words is not None and floor is not None:
        # Words guard: try the trim on a copy and refuse it when it would undo the words floor.
        trial = copy.deepcopy(assembly)
        before = trial.total_words()
        if not trim_assembly(trial, round(aim)):
            return None
        if words - (before - trial.total_words()) < floor:
            return None
    dropped = trim_assembly(assembly, round(aim))
    if not dropped:
        return None
    return {
        "blocks": _assembly_blocks(assembly),
        "assembly": assembly,
        "healing": [
            {
                "attempt": "trim_sections",
                "reason": f"{actual} pages against {target} requested: dropped "
                + "; ".join(dropped[:8])
                + (" ..." if len(dropped) > 8 else ""),
            }
        ],
    }
