"""Honest task-start text and the completion-claim guard (system-written, not model-written).

When a chat turn creates a task, the first thing the user sees is ``started_text`` (written here,
never by the model). Any later model text of the same turn that claims the work is DONE
("taiyar kar diya", "I have saved it", "ready") is removed by ``strip_completion_claims`` and the
reply ends with ``STILL_WORKING``: a task that has only just started has produced nothing yet, and
a Notion save is never reported before its write step is VERIFIED.
"""

from __future__ import annotations

import re
from typing import Final

__all__ = [
    "NO_APPROVAL_NOTE",
    "STILL_WORKING",
    "guard_text",
    "started_text",
    "strip_approval_claims",
    "strip_completion_claims",
]

STILL_WORKING: Final = "(still working)"

_CLAIMS: Final = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        # Hinglish: "taiyar kar diya gaya hai", "ban gaya", "bana diya", "save ho gaya", ...
        r"\b(?:taiyar|tayar|tyaar|ready)\s+(?:kar\s+)?(?:diya|di|dia|hai|ho\s+gay[ai])\b",
        r"\b(?:kar|kr)\s+diya(?:\s+gaya)?(?:\s+hai)?\b",
        r"\bban\s+gay[ai]\b|\bbana\s+(?:diya|di|dia)\b|\bbanaya\s+(?:gaya|hai)\b",
        r"\b(?:ho|hogaya)\s+gay[ai]\b|\bhogay[ai]\b|\bpura\s+(?:ho|kar)\b",
        r"\b(?:save|likh|add|upload|bhej)\s+(?:kar\s+)?(?:diya|di|dia|ho\s+gay[ai])\b",
        r"\bgaya\s+hai\b|\bgayi\s+hai\b",
        # English: "I have saved/created ...", "it is ready/done/completed/created/saved".
        r"\bI(?:'ve|’ve| have)\s+(?:just\s+|already\s+|now\s+)?(?:saved|created|made|prepared|"
        r"written|completed|finished|generated|built|added|put|sent|uploaded|compiled)\b",
        r"\b(?:is|are|was|were|has\s+been|have\s+been)\s+(?:now\s+|already\s+)?"
        r"(?:ready|done|completed|created|saved|finished|generated|prepared|available)\b",
        r"\b(?:all\s+)?(?:done|completed|finished)\s*[!.]|\bready\s+(?:for\s+you|to\s+use)\b",
        r"\bsuccessfully\s+(?:saved|created|completed|added|uploaded)\b",
    )
)
# A sentence that is a question, a denial or about the future never counts as a claim.
_NOT_A_CLAIM: Final = re.compile(
    r"\b(?:not|no|never|could\s+not|couldn'?t|can'?t|cannot|unable|failed|didn'?t|haven'?t|"
    r"won'?t|wasn'?t|nahi|nahin|nhi|will|shall|would|once|when|until|after|jab|jaise|hoga|"
    r"jayega|jaayega|jayegi|before|shuru|start|started|starting|begin|began|running|chalu)\b",
    re.IGNORECASE,
)
_SENTENCES: Final = re.compile(r"(?<=[.!?।])\s+|\n+")


def started_text(title: str, steps: int | None) -> str:
    """The first words the user reads after a task is created."""
    name = " ".join((title or "your task").split())[:120] or "your task"
    tail = "I will update you here (and by voice if voice is on) when it is done."
    if steps and steps > 0:
        count = "1 step" if steps == 1 else f"{steps} steps"
        return f"Started: {name}. It has {count}; {tail}"
    return f"Started: {name}. {tail}"


def _is_claim(sentence: str) -> bool:
    if sentence.rstrip().endswith("?") or _NOT_A_CLAIM.search(sentence):
        return False
    return any(pattern.search(sentence) for pattern in _CLAIMS)


def strip_completion_claims(text: str) -> tuple[str, bool]:
    """``(text without the sentences that claim completion, whether any was removed)``."""
    kept: list[str] = []
    changed = False
    for sentence in _SENTENCES.split(text or ""):
        if not sentence.strip():
            continue
        if _is_claim(sentence):
            changed = True
            continue
        kept.append(sentence.strip())
    return " ".join(kept), changed


def guard_text(model_text: str, started: str) -> str:
    """The whole reply of a turn that created a task: the system-written ``started`` text, then
    whatever useful text the model wrote with every completion claim removed, then
    ``STILL_WORKING`` when something was removed."""
    rest, changed = strip_completion_claims(model_text)
    parts = [started]
    if rest:
        parts.append(rest)
    if changed:
        parts.append(STILL_WORKING)
    return " ".join(parts)


NO_APPROVAL_NOTE: Final = "No approval card exists yet: nothing is waiting for you to approve."
_APPROVAL_MENTION: Final = re.compile(
    r"\bapprov\w*|\btask\s+card\b|\bapprove\s+par\b|\bclick\s+karein\b", re.IGNORECASE
)


def strip_approval_claims(text: str) -> tuple[str, bool]:
    """Remove sentences that point the user at an approval or task card. Call it only for a turn
    that created no task or approval: the card the sentence names does not exist."""
    kept: list[str] = []
    changed = False
    for sentence in _SENTENCES.split(text or ""):
        if not sentence.strip():
            continue
        if _APPROVAL_MENTION.search(sentence) and not sentence.rstrip().endswith("?"):
            changed = True
            continue
        kept.append(sentence.strip())
    return " ".join(kept), changed
