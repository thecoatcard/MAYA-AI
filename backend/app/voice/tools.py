"""Voice Tool Registry and Decoupled Task Handlers (Decisions D3 & D5).

Enforces:
1. Zero MCP tools in voice context (Decision D3, amended by the owner for memory: see ADR-0015
   addendum). The voice model holds submit_task, get_task_status and, only when the user's memory
   is on, recall_memory (read) and save_memory (low-risk write, never credentials). Nothing else.
2. Voice Approval Shielding (Decision D5). Any attempt to approve an action via voice is blocked.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from app.agents.capabilities import needs_clarification
from app.mcp.github_repo_tools import REPO_TOOL_NAMES, repo_voice_declarations
from app.research import intent as research_intent
from app.services.ask_user import ASK_USER_NAME, ask_user_voice_declaration
from app.services.deliverable_intent import downgrade_voice_deliverable, wants_deep_research
from app.services.image_gen import (
    IMAGE_TOOL_DESCRIPTION,
    IMAGE_TOOL_NAME,
    IMAGE_TOOL_PROMPT_DESCRIPTION,
    IMAGE_TOOL_STYLE_DESCRIPTION,
)
from app.services.loop_tools import WEB_SEARCH_NAME
from app.voice.web_voice import web_search_voice_declaration


class MCPToolRegistrationBlockedError(Exception):
    """Raised when an attempt is made to register MCP tools into the voice context."""


class VoiceApprovalProhibitedError(Exception):
    """Raised when an attempt is made to approve an action via voice (Decision D5)."""


@dataclass(frozen=True)
class VoiceExecutionContext:
    """Contextual metadata passed to voice tool executions."""

    workspace_id: UUID
    user_id: UUID
    session_id: str | None = None
    # Provider function-call id and a per-connection nonce: together they make the task
    # idempotency key stable across retries of one call and distinct across calls.
    call_id: str | None = None
    scope: str | None = None


# Types for external hook handlers
TaskSubmitter = Callable[[str, dict[str, Any], VoiceExecutionContext], Awaitable[dict[str, Any]]]
StatusGetter = Callable[[str, VoiceExecutionContext], Awaitable[dict[str, Any]]]
MemoryHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
RepoHandler = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
# generate_image: the handler calls the image model (services/image_gen.py); the live model never
# draws the picture itself.
ImageHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
# web_search: the handler runs the chat search stack (app/search) with a voice time budget.
WebHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]

RequiredCheck = Callable[[str], Awaitable[list[dict[str, Any]] | None]]
SUBMIT_TASK: Final = "submit_task"
GET_TASK_STATUS: Final = "get_task_status"
RECALL_MEMORY: Final = "recall_memory"
SAVE_MEMORY: Final = "save_memory"
REPORT_STEP_REVIEW: Final = "report_step_review"
SHOW_IN_CHAT: Final = "show_in_chat"
BASE_TOOLS: Final = (SUBMIT_TASK, GET_TASK_STATUS)


def show_in_chat_declaration() -> dict[str, Any]:
    """Live declaration of show_in_chat: puts content MAYA composes into the chat."""
    return {
        "name": SHOW_IN_CHAT,
        "description": (
            "Put content you compose into the chat so the user can read it: code, a list, notes, "
            "a table, steps or a summary. Use it whenever the user asks to see, show, get, put or "
            "share something in the chat or on screen, or wants something too long to say. Write "
            "the COMPLETE content yourself as Markdown (a fenced code block with a language for "
            "code). Never read it aloud; after it succeeds say one short sentence."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "content": {
                    "type": "STRING",
                    "description": (
                        "The complete Markdown to show: a fenced code block with a language, "
                        "notes with headings and bullets, a table, or numbered steps."
                    ),
                },
                "title": {"type": "STRING", "description": "Optional short title."},
            },
            "required": ["content"],
        },
    }


MEMORY_TOOLS: Final = (RECALL_MEMORY, SAVE_MEMORY)
DELIVERABLES: Final = (
    "pptx",
    "pdf",
    "docx",
    "xlsx",
    "notion",
    "report",
    "notes",
    "research",
    "deep_research",
    "none",
)
# A task brief needs at least this many words: "create a ppt" (3 words, no topic) is caught by the
# deliverable check below, "research" or "do it" by this one. A real brief is longer.
MIN_BRIEF_WORDS: Final = 3
_WORDS = re.compile(r"[^\W_]+", re.UNICODE)


def clarification_result(question: str, missing: list[str], reason: str) -> dict[str, Any]:
    """The needs_clarification outcome (same shape the task service returns): no task exists."""
    return {
        "ok": False,
        "status": "needs_clarification",
        "reason": reason,
        "hint": question,
        "question": question,
        "missing": missing,
        "message": (
            "No task was started. Ask the user exactly this one short question, in the user's "
            "language, then wait for the answer. Do not retry submit_task until they answer."
        ),
    }


def validate_task_brief(instruction: str, deliverable: str | None) -> dict[str, Any] | None:
    """A needs_clarification result when the brief is empty, too short or names a deliverable but
    no subject; ``None`` when it is specific enough to submit. Deterministic, no model call."""
    text = (instruction or "").strip()
    if not text:
        return clarification_result(
            "What would you like me to work on?", ["topic"], "The request was empty."
        )
    refusal = needs_clarification(text)
    if refusal is not None:
        return clarification_result(refusal.question, list(refusal.missing), refusal.reason)
    if len(_WORDS.findall(text)) < MIN_BRIEF_WORDS:
        wants = (deliverable or "none").lower()
        noun = "task" if wants in ("none", "research", "") else "file"
        return clarification_result(
            f"What exactly should the {noun} cover, and how long should it be?",
            ["topic", "length"],
            "The brief is too short to start a task.",
        )
    return None


class VoiceToolRegistry:
    """Registry managing the strictly allowed voice tools for Gemini Live."""

    APPROVAL_PROHIBITED_MESSAGE = "Approvals must be confirmed in the secure web interface."

    def __init__(self) -> None:
        self._task_submitter: TaskSubmitter | None = None
        self._status_getter: StatusGetter | None = None
        self._recall_handler: MemoryHandler | None = None
        self._save_handler: MemoryHandler | None = None
        self._repo_handler: RepoHandler | None = None
        self._image_handler: ImageHandler | None = None
        self._review_handler: MemoryHandler | None = None
        self._web_handler: WebHandler | None = None
        self._ask_handler: MemoryHandler | None = None
        self._chat_handler: MemoryHandler | None = None
        self._required_check: RequiredCheck | None = None
        self._connector_handler: RepoHandler | None = None
        self._connector_names: frozenset[str] = frozenset()
        self._connector_declarations: list[dict[str, Any]] = []

    @property
    def memory_enabled(self) -> bool:
        """True when the two memory tools are declared (memory on for this user)."""
        return self._recall_handler is not None and self._save_handler is not None

    def set_memory_handlers(self, recall: MemoryHandler, save: MemoryHandler) -> None:
        """Enable recall_memory and save_memory. Never called when memory is off."""
        self._recall_handler = recall
        self._save_handler = save

    def set_review_handler(self, handler: MemoryHandler) -> None:
        """Enable report_step_review (the live model's verdict on a finished task step). Set only
        when task updates are spoken; the handler accepts only step tokens the harness issued."""
        self._review_handler = handler

    @property
    def review_enabled(self) -> bool:
        return self._review_handler is not None

    def set_image_handler(self, handler: ImageHandler) -> None:
        """Enable generate_image. Only the image service's handler is set (never by default)."""
        self._image_handler = handler

    @property
    def image_enabled(self) -> bool:
        return self._image_handler is not None

    def set_web_handler(self, handler: WebHandler) -> None:
        """Enable web_search (read-only; the same search stack as chat)."""
        self._web_handler = handler

    @property
    def web_enabled(self) -> bool:
        return self._web_handler is not None

    def set_required_check(self, check: RequiredCheck) -> None:
        """Enable the required-details gate: ``check(instruction)`` returns the ask_user
        questions for what the user's own words still lack (topic, Notion page), or None."""
        self._required_check = check

    def set_ask_handler(self, handler: MemoryHandler) -> None:
        """Enable ask_user: the handler shows the question card in the chat. It never approves
        or runs anything (a card answer is only the user's reply)."""
        self._ask_handler = handler

    @property
    def ask_enabled(self) -> bool:
        return self._ask_handler is not None

    def set_chat_handler(self, handler: MemoryHandler) -> None:
        """Enable show_in_chat: the handler saves the model's own Markdown as an assistant message
        and tells the client live. Set whenever a conversation can store messages."""
        self._chat_handler = handler

    @property
    def chat_enabled(self) -> bool:
        return self._chat_handler is not None

    def set_repo_handler(self, handler: RepoHandler) -> None:
        """Enable the six read-only GitHub repository tools (owner request, ADR-0015
        addendum): the handler runs them with the caller's own GitHub connection."""
        self._repo_handler = handler

    @property
    def repo_enabled(self) -> bool:
        return self._repo_handler is not None

    def set_connector_tools(
        self,
        handler: RepoHandler,
        names: tuple[str, ...],
        declarations: list[dict[str, Any]],
    ) -> None:
        """Enable read-only connector tools (Notion, show_code): ``handler(name, args)`` runs them
        with the caller's own connection; ``declarations`` are their Live function declarations."""
        self._connector_handler = handler
        self._connector_names = frozenset(names)
        self._connector_declarations = list(declarations)

    @property
    def connector_enabled(self) -> bool:
        return self._connector_handler is not None

    @property
    def allowed_tool_names(self) -> tuple[str, ...]:
        return (
            *BASE_TOOLS,
            *(MEMORY_TOOLS if self.memory_enabled else ()),
            *(REPO_TOOL_NAMES if self.repo_enabled else ()),
            *((IMAGE_TOOL_NAME,) if self.image_enabled else ()),
            *((WEB_SEARCH_NAME,) if self.web_enabled else ()),
            *((ASK_USER_NAME,) if self.ask_enabled else ()),
            *((SHOW_IN_CHAT,) if self.chat_enabled else ()),
            *((REPORT_STEP_REVIEW,) if self.review_enabled else ()),
            *(sorted(self._connector_names) if self.connector_enabled else ()),
        )

    async def _missing_details(self, instruction: str) -> dict[str, Any] | None:
        """The needs_clarification result (and the question card) when the user's own words
        lack a required detail; None when the task may start. Fails open."""
        if self._required_check is None:
            return None
        try:
            questions = await self._required_check(instruction)
        except Exception:  # noqa: BLE001 - the task service re-checks
            return None
        if not questions:
            return None
        shown = False
        if self._ask_handler is not None:
            shown = (await self._ask_handler({"questions": questions})).get("status") == "shown"
        first = str(questions[0].get("question") or "What should it be about?")
        result = clarification_result(
            first,
            [str(q.get("header") or "").lower() for q in questions],
            "The request is missing details.",
        )
        if shown:
            result["message"] = (
                "No task was started. The question card is on screen. Say the first question "
                "aloud in one short sentence, in the user's language, then wait for the answer. "
                "Do not guess a topic and do not call submit_task again until they answer."
            )
        return result

    def set_task_submitter(self, submitter: TaskSubmitter) -> None:
        """Inject the handler for submit_task."""
        self._task_submitter = submitter

    def set_status_getter(self, getter: StatusGetter) -> None:
        """Inject the handler for get_task_status."""
        self._status_getter = getter

    def get_tool_declarations(self) -> list[dict[str, Any]]:
        """Return the function declarations allowed for the voice model.

        ``submit_task`` and ``get_task_status`` always; ``recall_memory`` and ``save_memory`` only
        when memory is on for this user. ``forget_memory`` is never declared (see ADR-0015).
        """
        declarations: list[dict[str, Any]] = [
            {
                "name": SUBMIT_TASK,
                "description": (
                    "Start a background task for real work: a file the user NAMED (PDF, "
                    "PowerPoint, Word, spreadsheet, Notion page) or multi-step research. Never "
                    "choose a file format the user did not say: notes, a report or research "
                    "without a format word is answered in the chat (deliverable 'none'). "
                    "For 'deep research' use deliverable 'deep_research'. Do NOT "
                    "use it for questions, chit-chat, explanations or small things you can answer "
                    "yourself. Call it only after you know the subject and the essentials (length, "
                    "audience, format, language) and the user agreed to the plan, unless the "
                    "request was already complete and an order. Never call it with a generic "
                    "brief such as 'create a ppt'; if the topic is missing, ask the user one "
                    "short question first. If it answers needs_clarification, ask that question "
                    "and do not retry."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "instruction": {
                            "type": "STRING",
                            "description": (
                                "The full, self-contained brief written from what the user "
                                "actually said: subject or topic, scope, length, audience, "
                                "language. Never a generic request."
                            ),
                        },
                        "deliverable": {
                            "type": "STRING",
                            "enum": list(DELIVERABLES),
                            "description": (
                                "The file the user explicitly named (pdf, pptx, docx, xlsx, "
                                "notion), 'deep_research' when they asked for deep research, or "
                                "'none' for research or other work without a file. Never pick "
                                "a file format yourself."
                            ),
                        },
                    },
                    "required": ["instruction", "deliverable"],
                },
            },
            {
                "name": GET_TASK_STATUS,
                "description": (
                    "Retrieve current status, progress, and output summary for an active "
                    "or completed background task."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "task_id": {
                            "type": "STRING",
                            "description": "The unique identifier of the task.",
                        },
                    },
                    "required": ["task_id"],
                },
            },
        ]
        if self.memory_enabled:
            declarations.extend(
                [
                    {
                        "name": RECALL_MEMORY,
                        "description": (
                            "Look up what MAYA remembers about the user. Use it when the user "
                            "asks about themselves, their preferences, or something they told "
                            "MAYA before ('what is my favourite colour', 'do you remember...'). "
                            "Read-only."
                        ),
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {
                                "query": {
                                    "type": "STRING",
                                    "description": "What to look up, in the user's words.",
                                },
                            },
                            "required": ["query"],
                        },
                    },
                    {
                        "name": SAVE_MEMORY,
                        "description": (
                            "Save one fact or preference about the user to long-term memory. Use "
                            "it only when the user clearly asks you to remember or note "
                            "something, or states a stable preference they want kept. Never for "
                            "passwords, keys, payment data, small talk or other people's private "
                            "data."
                        ),
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {
                                "text": {
                                    "type": "STRING",
                                    "description": "One short, self-contained sentence.",
                                },
                                "kind": {
                                    "type": "STRING",
                                    "enum": ["preference", "fact", "project"],
                                    "description": "What kind of memory this is.",
                                },
                            },
                            "required": ["text"],
                        },
                    },
                ]
            )
        if self.repo_enabled:
            declarations.extend(repo_voice_declarations())
        if self.web_enabled:
            declarations.append(web_search_voice_declaration())
        if self.ask_enabled:
            declarations.append(ask_user_voice_declaration())
        if self.image_enabled:
            declarations.append(
                {
                    "name": IMAGE_TOOL_NAME,
                    "description": (
                        IMAGE_TOOL_DESCRIPTION
                        + " Call it once per request. Then say in one short sentence that the "
                        "image is ready in the chat."
                    ),
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "prompt": {
                                "type": "STRING",
                                "description": IMAGE_TOOL_PROMPT_DESCRIPTION,
                            },
                            "style_hint": {
                                "type": "STRING",
                                "description": IMAGE_TOOL_STYLE_DESCRIPTION,
                            },
                        },
                        "required": ["prompt"],
                    },
                }
            )
        if self.chat_enabled:
            declarations.append(show_in_chat_declaration())
        if self.review_enabled:
            declarations.append(
                {
                    "name": REPORT_STEP_REVIEW,
                    "description": (
                        "Record your review of a finished task step, only when a task update asks "
                        "for it. Pass the step_id given in that update, verdict ok or problem, "
                        "and a short note. It is stored silently: never announce that you called "
                        "it, and speak only if the verdict is problem."
                    ),
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "step_id": {
                                "type": "STRING",
                                "description": "The step_id from the task update, copied exactly.",
                            },
                            "verdict": {"type": "STRING", "enum": ["ok", "problem"]},
                            "note": {
                                "type": "STRING",
                                "description": "One short sentence: what is fine or what is wrong.",
                            },
                        },
                        "required": ["step_id", "verdict", "note"],
                    },
                }
            )
        if self.connector_enabled:
            declarations.extend(self._connector_declarations)
        return declarations

    def register_tool(self, name: str, handler: Any) -> None:
        """Block registration of arbitrary or MCP tools (Decision D3)."""
        raise MCPToolRegistrationBlockedError(
            f"Zero MCP tools allowed in voice context (Decision D3). Attempted: {name}"
        )

    def register_mcp_tool(self, name: str) -> None:
        """Block registration of MCP tools (Decision D3)."""
        raise MCPToolRegistrationBlockedError(
            f"Zero MCP tools allowed in voice context (Decision D3). Attempted: {name}"
        )

    def approve_action(self, approval_id: str, context: VoiceExecutionContext) -> None:
        """Block approval actions from voice channel (Decision D5)."""
        raise VoiceApprovalProhibitedError(self.APPROVAL_PROHIBITED_MESSAGE)

    def execute_tool(
        self, name: str, args: dict[str, Any], context: VoiceExecutionContext
    ) -> dict[str, Any]:
        """Synchronously execute a tool or raise if approval / blocked."""
        return asyncio.run(self.execute_tool_async(name, args, context))

    async def execute_tool_async(
        self, name: str, args: dict[str, Any], context: VoiceExecutionContext
    ) -> dict[str, Any]:
        """Asynchronously execute a voice tool."""
        # Decision D5 enforcement: block any approval-related tool names
        lower_name = name.lower()
        if "approve" in lower_name or "confirm_approval" in lower_name or "totp" in lower_name:
            raise VoiceApprovalProhibitedError(self.APPROVAL_PROHIBITED_MESSAGE)

        if name == SUBMIT_TASK:
            instruction = str(args.get("instruction") or args.get("intent") or "").strip()
            deliverable = args.get("deliverable")
            params = args.get("params", {})
            params = dict(params) if isinstance(params, dict) else {}
            if isinstance(deliverable, str):
                # Owner rule: a file kind the user's words do not name becomes a chat answer.
                deliverable = downgrade_voice_deliverable(instruction, deliverable)
            if (isinstance(deliverable, str) and deliverable.lower() == "deep_research") or (
                wants_deep_research(instruction)
            ):
                # The same engine as the composer toggle (the task runs the deep-research step).
                if research_intent.needs_topic(instruction):
                    return clarification_result(
                        research_intent.missing_topic_question(),
                        ["topic"],
                        "Deep research needs a topic.",
                    )
                params["deep_research"] = True
                deliverable = "none"
            if isinstance(deliverable, str) and deliverable.lower() in DELIVERABLES:
                params.setdefault("deliverable", deliverable.lower())
            unclear = validate_task_brief(
                instruction, deliverable if isinstance(deliverable, str) else None
            )
            if unclear is not None:
                return unclear
            missing = await self._missing_details(instruction)
            if missing is not None:
                return missing
            if self._task_submitter is not None:
                return _relay_outcome(await self._task_submitter(instruction, params, context))
            return {
                "task_id": f"task-mock-{context.workspace_id}",
                "status": "pending",
                "message": f"Task submitted: {instruction}",
            }

        elif name == GET_TASK_STATUS:
            task_id = str(args.get("task_id", ""))
            if self._status_getter is not None:
                return await self._status_getter(task_id, context)
            return {
                "task_id": task_id,
                "status": "in_progress",
                "progress": 0.0,
            }

        elif name == RECALL_MEMORY and self._recall_handler is not None:
            return await self._recall_handler(dict(args))

        elif name == SAVE_MEMORY and self._save_handler is not None:
            return await self._save_handler(dict(args))

        elif name == REPORT_STEP_REVIEW and self._review_handler is not None:
            return await self._review_handler(dict(args))

        elif name in REPO_TOOL_NAMES and self._repo_handler is not None:
            return await self._repo_handler(name, dict(args))

        elif name == WEB_SEARCH_NAME and self._web_handler is not None:
            return await self._web_handler(dict(args))

        elif name == ASK_USER_NAME and self._ask_handler is not None:
            return await self._ask_handler(dict(args))

        elif name == SHOW_IN_CHAT and self._chat_handler is not None:
            return await self._chat_handler(dict(args))

        elif name == IMAGE_TOOL_NAME and self._image_handler is not None:
            return await self._image_handler(dict(args))

        elif name in self._connector_names and self._connector_handler is not None:
            return await self._connector_handler(name, dict(args))

        else:
            raise ValueError(
                f"Unsupported voice tool: '{name}'. Only "
                f"{', '.join(self.allowed_tool_names)} are permitted."
            )


def _relay_outcome(result: dict[str, Any]) -> dict[str, Any]:
    """Pass the submitter's outcome to the model. A structured refusal (``ok`` is false:
    ``needs_clarification`` or ``unsupported``) gets an instruction that no task exists, so the
    model asks the question or states the reason instead of retrying or claiming a start."""
    if not isinstance(result, dict) or result.get("ok") is not False:
        return result
    relayed = dict(result)
    relayed.pop("task_id", None)
    if relayed.get("status") == "needs_clarification":
        question = str(relayed.get("question") or relayed.get("hint") or "")
        relayed.setdefault(
            "message",
            "No task was started. Ask the user exactly this one short question, in the user's "
            f"language, then wait: {question}",
        )
    else:
        relayed.setdefault(
            "message",
            "No task was started. Tell the user the reason and the hint in one or two plain "
            "sentences in their language. Do not claim a task exists.",
        )
    return relayed
