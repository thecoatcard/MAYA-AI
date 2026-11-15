"""A running task that needs one detail from the user: ask on a card, resume from that step.

A step that cannot continue without a detail (a Notion parent page, a recipient, a file name...)
raises :class:`NeedsInput` instead of failing. The worker pauses the step (no failed task, the
finished work such as the book Markdown stays), shows the existing ``ask_user`` card with options
fetched at that moment, and the answer resumes the same step. Answers are user data: they only
fill the named argument; the approval payload hash is computed afterwards from the final
arguments, so it is bound to the chosen value as usual.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

NEEDS_INPUT_KEY: Final = "needs_input"
KIND_NOTION_PARENT: Final = "notion_parent"
MAX_OPTIONS: Final = 6
DEFAULT_YES: Final = "Yes, use it by default"
DEFAULT_NO: Final = "No, just this time"
SUGGESTED: Final = "Suggested"


@dataclass(frozen=True, slots=True)
class Candidate:
    """One real option: what the user sees and the value that fills the argument."""

    label: str
    value: str
    detail: str = ""


class NeedsInput(Exception):  # noqa: N818 - a pause, not an error
    """Raised by a step that needs a detail from the user; ``options`` are real candidates."""

    def __init__(
        self,
        kind: str,
        question: str,
        *,
        header: str = "Input",
        options: Sequence[Candidate] = (),
        offer_default: bool = False,
    ) -> None:
        super().__init__(question)
        self.kind = kind
        self.question = question
        self.header = header
        self.options = list(options)[:MAX_OPTIONS]
        self.offer_default = offer_default


@dataclass(slots=True)
class Pause:
    """What is stored on the step (``input_data["needs_input"]``) while it waits."""

    kind: str
    ask_id: str
    options: dict[str, str] = field(default_factory=dict)  # label -> value
    offer_default: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "ask_id": self.ask_id,
            "options": dict(self.options),
            "offer_default": self.offer_default,
        }


def option_label(candidate: Candidate, *, first: bool) -> str:
    """The card label; the top-ranked option is marked ``Suggested``."""
    base = " ".join(candidate.label.split())[:60] or "Untitled"
    return f"{base} ({SUGGESTED})" if first else base


def card_questions(need: NeedsInput) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """``(ask_user questions, label -> value)`` for a pause. Free text stays on, so the user can
    paste a link instead (the "Other / paste a URL" choice)."""
    mapping: dict[str, str] = {}
    options: list[dict[str, Any]] = []
    for index, candidate in enumerate(need.options):
        label = option_label(candidate, first=index == 0)
        if label in mapping:
            continue
        mapping[label] = candidate.value
        options.append({"label": label, "description": candidate.detail[:120]})
    questions: list[dict[str, Any]] = [
        {
            "question": need.question,
            "header": need.header,
            "options": options,
            "allow_text": True,
        }
    ]
    if need.offer_default and options:
        questions.append(
            {
                "question": "Use this by default?",
                "header": "Default",
                "options": [{"label": DEFAULT_YES}, {"label": DEFAULT_NO}],
                "allow_text": False,
            }
        )
    return questions, mapping


@dataclass(frozen=True, slots=True)
class Resolved:
    value: str
    label: str
    make_default: bool


_URL_OR_ID: Final = re.compile(
    r"https?://\S+|[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-\S{4}-\S{4}-\S{12}"
)


def resolve_answer(
    pause: Mapping[str, Any], answers: Sequence[Mapping[str, Any]]
) -> Resolved | None:
    """The value the user gave: a chosen option's real value, or a pasted link / id. None when the
    answer holds neither (the step stays paused)."""
    options = pause.get("options")
    mapping: Mapping[str, str] = options if isinstance(options, Mapping) else {}
    first = answers[0] if answers else {}
    selected = [str(s) for s in (first.get("selected") or [])]
    text = str(first.get("text") or "").strip()
    value = label = ""
    for chosen in selected:
        if chosen in mapping:
            value, label = str(mapping[chosen]), chosen
            break
    if not value and text:
        found = _URL_OR_ID.search(text)
        if found is not None:
            value, label = found.group(0), text[:80]
    if not value:
        return None
    second = answers[1] if len(answers) > 1 else {}
    default = DEFAULT_YES in [str(s) for s in (second.get("selected") or [])]
    return Resolved(
        value=value, label=re.sub(r"\s*\(Suggested\)$", "", label), make_default=default
    )


_PAGE_ROW: Final = re.compile(
    r"^-\s*(?P<title>.+?)\s*\|\s*id\s+(?P<id>\S+)\s*\|\s*in a\s+(?P<parent>\S+)", re.MULTILINE
)


def candidates_from_search(search_text: str, *, limit: int = MAX_OPTIONS) -> list[Candidate]:
    """Real candidates from the ``notion_search`` tool text (most recently edited first)."""
    found: list[Candidate] = []
    for match in _PAGE_ROW.finditer(search_text or ""):
        title = match.group("title").strip()
        if not title or title.lower() in ("untitled", "(untitled)"):
            continue
        found.append(Candidate(title, match.group("id"), f"in a {match.group('parent')}"))
        if len(found) >= limit:
            break
    return found


DEFAULT_PARENT_KEY: Final = "notion_default_parent"


def format_default(value: str, label: str) -> str:
    """The stored setting: ``<page id or url>|<label>`` (set only after the user confirmed)."""
    return f"{value.strip()}|{' '.join(label.split())[:80]}"


def parse_default(stored: str | None) -> tuple[str, str] | None:
    """``(value, label)`` of the user's confirmed default Notion parent, or None."""
    if not stored or "|" not in stored:
        return None
    value, _sep, label = stored.partition("|")
    return (value.strip(), label.strip()) if value.strip() else None
