"""Progress steps: a live, human-readable list of what MAYA is doing for one user turn or task.

A step is shown to the user the way ChatGPT and Claude show their work. It is a short label and an
optional short detail, never file contents, prompts or secrets. Thinking steps carry a *summary* of
the model's reasoning as the provider returns it (Gemini thought summaries). Raw chain-of-thought is
never requested, stored or forwarded.

The contract the web UI builds against (SSE ``progress`` event and assistant message metadata key
``progress``) is the ``ProgressStep`` model below. Field names and allowed values are the contract.

Emission is fire and forget: ``ProgressRecorder`` keeps a bounded queue that the caller drains
between model chunks. A full queue drops the oldest event and counts it. Nothing here awaits, so a
slow client or a failing event can never block the model stream or a worker transaction.
"""

from __future__ import annotations

import re
import time
from collections import deque
from collections.abc import Callable
from typing import Final, Literal, Self, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

ProgressKind = Literal[
    "plan",
    "think",
    "tool",
    "step",
    "render",
    "check",
    "repair",
    "save",
    "search",
    "email",
    "image",
    "voice",
    "note",
]
ProgressState = Literal["running", "done", "failed", "skipped"]

PROGRESS_KINDS: Final[frozenset[str]] = frozenset(get_args(ProgressKind))
PROGRESS_STATES: Final[frozenset[str]] = frozenset(get_args(ProgressState))
TERMINAL_STATES: Final[frozenset[str]] = frozenset({"done", "failed", "skipped"})

LABEL_MAX: Final = 120
# Shown once per turn when the saved-preferences block was added (the label never holds its text).
STANDING_PREFS_LABEL: Final = "Using your saved preferences"
DETAIL_MAX: Final = 280
THOUGHT_MAX: Final = 6000
# Narration the model wrote between tool calls (kind ``note``), as one cleaned line.
NOTE_MAX: Final = 1200
NOTE_LABEL: Final = "Note"
ID_MAX: Final = 64
# Most steps one run may hold (the persisted list is capped at the same size).
MAX_STEPS: Final = 200
# Queued events not yet drained. The oldest is dropped beyond this (bounded memory).
MAX_PENDING: Final = 200
# Thought text is coalesced: at most one event per interval while it grows, plus one at finish.
THOUGHT_EVENT_INTERVAL_SECONDS: Final = 0.5

_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")
_SPACES = re.compile(r"[ \t]+")
# Best-effort scrubbing of credential-shaped text. Labels come from code, but details can echo
# provider or tool output, so they are scrubbed as well.
_SECRET_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"sk-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-~+/]+=*"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|token|secret|password|passwd)\b\s*[:=]\s*\S+"
    ),
    re.compile(r"\b[A-Fa-f0-9]{32,}\b"),
)
_REDACTED: Final = "[redacted]"
_ELLIPSIS: Final = "…"


_CONTROL_KEEP_NEWLINES = re.compile(r"[\x00-\x09\x0b-\x1f\x7f-\x9f\u2028\u2029]")
_EXTRA_BLANK_LINES = re.compile(r"\n{3,}")
_SPACES_AROUND_NEWLINE = re.compile(r" *\n *")


def clean_text(
    value: str, limit: int, *, keep_edges: bool = False, keep_newlines: bool = False
) -> str:
    """Scrubbed text no longer than ``limit`` characters (ending in an ellipsis).

    Single-line by default. ``keep_edges`` keeps the spaces at the ends, for stream fragments that
    are joined later. ``keep_newlines`` keeps line breaks (at most one blank line in a row), for
    the model's reasoning summary, whose paragraphs and headings are shown as Markdown.
    """
    if keep_newlines:
        text = value.replace("\r\n", "\n").replace("\r", "\n")
        text = _CONTROL_KEEP_NEWLINES.sub(" ", text)
    else:
        text = _CONTROL.sub(" ", value)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(_REDACTED, text)
    text = _SPACES.sub(" ", text)
    if keep_newlines:
        text = _SPACES_AROUND_NEWLINE.sub("\n", text)
        text = _EXTRA_BLANK_LINES.sub("\n\n", text)
    text = text if keep_edges else text.strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + _ELLIPSIS
    return text


def _optional_text(value: str | None, limit: int, *, keep_newlines: bool = False) -> str | None:
    if value is None:
        return None
    cleaned = clean_text(value, limit, keep_newlines=keep_newlines)
    return cleaned or None


class ProgressStep(BaseModel):
    """One step of a run. Every state change of a step is one event with the same ``step_id``.

    Allowed values:
    - ``kind``: plan, think, tool, step, render, check, repair, save, search, email, image, voice,
      note
    - ``state``: running, done, failed, skipped (running moves to one of the other three)
    - ``thought``: summary text, only for kind ``think``, at most 6000 characters
    - ``index`` and ``total``: both optional, ``1 <= index <= total`` (for "slide 4 of 10")
    - ``elapsed_ms``: milliseconds since the step started, never negative
    - ``note``: the model's narration between two tool calls, required for and only allowed on
      kind ``note``, at most 1200 characters; it is shown in order among the steps
    - ``text_len``: live only (never stored): how many characters of streamed reply text the note
      replaces, so a client that already showed that text can remove it instead of showing it twice
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str = Field(min_length=1, max_length=ID_MAX)
    step_id: str = Field(min_length=1, max_length=ID_MAX)
    parent_id: str | None = Field(default=None, min_length=1, max_length=ID_MAX)
    kind: ProgressKind
    label: str = Field(min_length=1, max_length=LABEL_MAX)
    detail: str | None = Field(default=None, max_length=DETAIL_MAX)
    state: ProgressState
    index: int | None = Field(default=None, ge=1)
    total: int | None = Field(default=None, ge=1)
    elapsed_ms: int = Field(default=0, ge=0)
    thought: str | None = Field(default=None, max_length=THOUGHT_MAX)
    note: str | None = Field(default=None, max_length=NOTE_MAX)
    text_len: int | None = Field(default=None, ge=0)

    @field_validator("label", mode="before")
    @classmethod
    def _label(cls, value: object) -> str:
        cleaned = clean_text(str(value), LABEL_MAX)
        if not cleaned:
            raise ValueError("label must not be empty")
        return cleaned

    @field_validator("detail", "thought", "note", mode="before")
    @classmethod
    def _cap_text(cls, value: object, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        limit = {"thought": THOUGHT_MAX, "note": NOTE_MAX}.get(str(info.field_name), DETAIL_MAX)
        return _optional_text(str(value), limit, keep_newlines=str(info.field_name) == "thought")

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.thought is not None and self.kind != "think":
            raise ValueError("thought is only allowed on kind 'think'")
        if self.kind == "note" and self.note is None:
            raise ValueError("a note step needs its text")
        if self.note is not None and self.kind != "note":
            raise ValueError("note is only allowed on kind 'note'")
        if self.text_len is not None and self.kind != "note":
            raise ValueError("text_len is only allowed on kind 'note'")
        if self.index is not None and self.total is not None and self.index > self.total:
            raise ValueError("index must not be greater than total")
        return self


def can_transition(current: str, new: str) -> bool:
    """A step starts as running and ends once: running -> done, failed or skipped. Terminal states
    never change. A step may also start directly in a terminal state (a one-shot note)."""
    if new not in PROGRESS_STATES:
        return False
    if current == new:
        return current == "running"
    return current == "running" or current not in TERMINAL_STATES


class _Open:
    """Book-keeping for one step held by a recorder."""

    __slots__ = ("step", "started", "emitted_at")

    def __init__(self, step: ProgressStep, started: float) -> None:
        self.step = step
        self.started = started
        self.emitted_at = started


class ProgressRecorder:
    """The steps of one run, with a bounded queue of events to send.

    Methods never raise for a bad step: an invalid value is dropped with a counted reason so the
    work it describes always goes on. ``drain`` returns the queued events in order.
    """

    def __init__(
        self,
        run_id: str,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_steps: int = MAX_STEPS,
        max_pending: int = MAX_PENDING,
    ) -> None:
        self._run_id = clean_text(run_id, ID_MAX) or "run"
        self._clock = clock
        self._max_steps = max_steps
        self._steps: dict[str, _Open] = {}
        self._pending: deque[ProgressStep] = deque(maxlen=max_pending)
        self._counter = 0
        self.dropped = 0  # events dropped from a full queue, or steps beyond max_steps
        self.rejected = 0  # steps refused by validation (never raised to the caller)

    @property
    def run_id(self) -> str:
        return self._run_id

    def _new_id(self, step_id: str | None) -> str:
        if step_id:
            return clean_text(step_id, ID_MAX)
        self._counter += 1
        return f"s{self._counter}"

    def _enqueue(self, open_step: _Open) -> None:
        if len(self._pending) == self._pending.maxlen:
            self.dropped += 1
        elapsed = max(0, int((self._clock() - open_step.started) * 1000))
        open_step.step = open_step.step.model_copy(update={"elapsed_ms": elapsed})
        self._pending.append(open_step.step)
        open_step.emitted_at = self._clock()

    def start(
        self,
        kind: str,
        label: str,
        *,
        step_id: str | None = None,
        parent_id: str | None = None,
        detail: str | None = None,
        index: int | None = None,
        total: int | None = None,
        state: str = "running",
        thought: str | None = None,
        note: str | None = None,
        text_len: int | None = None,
    ) -> str | None:
        """Begin a step (or record a finished one). Returns its id, or None when refused."""
        sid = self._new_id(step_id)
        if sid in self._steps:
            self.rejected += 1
            return None
        if len(self._steps) >= self._max_steps:
            self.dropped += 1
            return None
        try:
            step = ProgressStep(
                run_id=self._run_id,
                step_id=sid,
                parent_id=parent_id,
                kind=kind,
                label=label,
                detail=detail,
                state=state,
                index=index,
                total=total,
                thought=thought,
                note=note,
                text_len=text_len,
            )
        except ValueError:
            self.rejected += 1
            return None
        now = self._clock()
        open_step = _Open(step, now)
        self._steps[sid] = open_step
        self._enqueue(open_step)
        return sid

    def update(
        self,
        step_id: str,
        state: str,
        *,
        detail: str | None = None,
        index: int | None = None,
        total: int | None = None,
    ) -> bool:
        """Move a step to a new state (and optionally a new detail or index). False if refused."""
        open_step = self._steps.get(step_id)
        if open_step is None or not can_transition(open_step.step.state, state):
            self.rejected += 1
            return False
        changes: dict[str, object] = {"state": state}
        if detail is not None:
            changes["detail"] = _optional_text(detail, DETAIL_MAX)
        if index is not None:
            changes["index"] = index
        if total is not None:
            changes["total"] = total
        try:
            open_step.step = ProgressStep.model_validate({**open_step.step.model_dump(), **changes})
        except ValueError:
            self.rejected += 1
            return False
        self._enqueue(open_step)
        return True

    def finish(self, step_id: str, state: str = "done", *, detail: str | None = None) -> bool:
        return self.update(step_id, state, detail=detail)

    def append_thought(self, step_id: str, text: str) -> None:
        """Add summary text to a think step. Events are coalesced; ``finish`` always sends one."""
        open_step = self._steps.get(step_id)
        if open_step is None or open_step.step.kind != "think" or not text:
            return
        current = open_step.step.thought or ""
        if len(current) >= THOUGHT_MAX:
            return  # the summary is full; later fragments add nothing the user can read
        # Stream fragments carry their own spacing, so they are joined as they arrive.
        combined = clean_text(current + text, THOUGHT_MAX, keep_edges=True, keep_newlines=True)
        open_step.step = open_step.step.model_copy(update={"thought": combined or None})
        if self._clock() - open_step.emitted_at >= THOUGHT_EVENT_INTERVAL_SECONDS:
            self._enqueue(open_step)

    def close_open(self, state: str = "failed", *, detail: str | None = None) -> list[str]:
        """Finish every step still running (a turn that ends early). Returns the ids closed."""
        closed: list[str] = []
        for sid, open_step in self._steps.items():
            if open_step.step.state == "running" and self.finish(sid, state, detail=detail):
                closed.append(sid)
        return closed

    def close_children(self, parent_id: str, state: str = "failed") -> list[str]:
        """Finish the steps still running under ``parent_id`` (a tool that ended early)."""
        closed: list[str] = []
        for sid, open_step in self._steps.items():
            step = open_step.step
            if step.parent_id == parent_id and step.state == "running" and self.finish(sid, state):
                closed.append(sid)
        return closed

    def drain(self) -> list[ProgressStep]:
        """Queued events in order, emptying the queue. Never blocks."""
        events = list(self._pending)
        self._pending.clear()
        return events

    def snapshot(self) -> list[ProgressStep]:
        """The latest version of every step, in the order they started (for persistence)."""
        return [open_step.step for open_step in self._steps.values()]

    def snapshot_dicts(self) -> list[dict[str, object]]:
        """``snapshot`` as JSON-ready dicts with empty optional fields left out."""
        # ``text_len`` is a live-stream hint only: a reloaded reply already lacks the removed text.
        return [
            step.model_dump(mode="json", exclude_none=True, exclude={"text_len"})
            for step in self.snapshot()
        ]


def parse_steps(raw: object) -> list[ProgressStep]:
    """Stored steps read back tolerantly: a malformed entry is skipped, never raised."""
    if not isinstance(raw, list):
        return []
    steps: list[ProgressStep] = []
    for item in raw[:MAX_STEPS]:
        try:
            steps.append(ProgressStep.model_validate(item))
        except ValueError:
            continue
    return steps


_TOOL_KINDS: Final[dict[str, tuple[str, str]]] = {
    "web_search": ("search", "Searching the web"),
    "google_search": ("search", "Searching the web"),
    "fetch_web_page": ("search", "Reading a web page"),
    "recall_memory": ("search", "Checking saved memory"),
    "create_document": ("render", "Creating document"),
    "preview_document": ("render", "Previewing document"),
    "sheet_summary": ("check", "Reading the sheet"),
    "edit_sheet": ("render", "Editing the sheet"),
    "create_file_inline": ("render", "Creating file"),
    "edit_attached_file": ("render", "Editing file"),
    "check_job": ("check", "Checking progress"),
    "send_email": ("email", "Drafting email"),
    "mail_list_recent": ("search", "Reading your mail"),
    "mail_search": ("search", "Reading your mail"),
    "mail_read": ("search", "Reading your mail"),
    "mail_reply": ("email", "Sending mail (needs approval)"),
    "generate_image": ("image", "Generating image"),
    "save_memory": ("save", "Saving to memory"),
    "forget_memory": ("save", "Updating memory"),
    "submit_task": ("step", "Starting background task"),
    "start_job": ("step", "Starting background job"),
    "create_custom_agent": ("step", "Drafting custom agent"),
    "show_code": ("step", "Reading code"),
    "runtime_tool_search": ("search", "Looking for a tool"),
    "runtime_tool_propose": ("step", "Building a tool (needs approval)"),
    "runtime_tool_run": ("step", "Running a tool"),
    "mcp_client_call": ("step", "Using an external tool (needs approval)"),
}


# Connector tools are named ``<connector>_<verb>_...``. A read is labelled as a read; anything else
# (a write, or a draft that waits for approval) gets a neutral label that claims no change.
_CONNECTOR_NAMES: Final[tuple[tuple[str, str], ...]] = (
    ("github_", "GitHub"),
    ("notion_", "Notion"),
)
_MCP_LABELS: Final[dict[str, str]] = {"github": "GitHub", "playwright": "Playwright"}
_READ_VERBS: Final = ("get_", "list_", "read_", "search", "review_")


def tool_step(tool_name: str) -> tuple[str, str]:
    """(kind, label) for a tool call. Unknown tools are a generic tool step with a neutral label.

    The label comes from the tool name only, never from its arguments or its result.
    """
    known = _TOOL_KINDS.get(tool_name)
    if known is not None:
        return known
    if tool_name.startswith("mcp__"):
        # mcp__<server>__<tool>: only the server's name is shown, never the tool or its arguments.
        server = tool_name[len("mcp__") :].split("__", 1)[0]
        return "tool", f"Using {_MCP_LABELS.get(server, server.replace('-', ' ').title())}"
    for prefix, product in _CONNECTOR_NAMES:
        if tool_name.startswith(prefix):
            if tool_name[len(prefix) :].startswith(_READ_VERBS):
                return "search", f"Reading {product}"
            return "tool", f"Using {product}"
    return "tool", "Using a tool"
