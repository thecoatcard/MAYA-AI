"""Deterministic grounding checks for chat replies. Pure functions, no I/O, no model call.

Three things the chat turn needs so a reply never says more than the system knows:

* :func:`is_followup` - a short "continue"-type message (typos included, matched with ``difflib``)
  must be answered from the CURRENT state of this conversation's jobs (:func:`followup_block`),
  not by repeating the earlier assistant message.
* :func:`claims_action_done` - the reply says something was created, saved, sent, added or
  prepared. The chat turn compares that with the tool results of the same turn and appends
  :func:`action_claim_correction` when nothing succeeded (and no job is pending).
* :func:`needs_data_read` / :func:`ungrounded_notice` - the question is about the user's own
  Notion or GitHub data, but no read tool succeeded: the reply must say what could not be read.

False positives are possible (a regex reads sentences, not intent); every check is conservative
and its correction is one honest sentence, never a deletion of the reply.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

# ------------------------------------------------------------------------------- follow-ups

_FOLLOWUPS: Final = frozenset(
    {
        "continue",
        "go on",
        "go ahead",
        "carry on",
        "proceed",
        "next",
        "next step",
        "ok",
        "okay",
        "k",
        "haan",
        "han",
        "ha",
        "yes",
        "yep",
        "yeah",
        "done",
        "and",
        "then",
        "aage",
        "aage badho",
        "aage badhao",
        "phir",
        "fir",
        "aur",
        "jari rakho",
        "chalu rakho",
        "kya hua",
        "status",
        "update",
        "any update",
        "so",
    }
)
_FUZZY_MIN_LENGTH: Final = 5
_FUZZY_CUTOFF: Final = 0.8
_WORD: Final = re.compile(r"[^\W_]+", re.UNICODE)


def is_followup(text: str) -> bool:
    """True for a very short 'continue'-like message, tolerating typos ('contniue')."""
    if "?" in text and len(text) > 20:
        return False
    words = _WORD.findall(text.lower())
    if not 1 <= len(words) <= 3:
        return False
    phrase = " ".join(words)
    if phrase in _FOLLOWUPS:
        return True
    if len(phrase) < _FUZZY_MIN_LENGTH:
        return False
    return bool(difflib.get_close_matches(phrase, _FOLLOWUPS, n=1, cutoff=_FUZZY_CUTOFF))


@dataclass(frozen=True, slots=True)
class SessionJob:
    """One job (task) of this conversation, read from the task rows."""

    task_id: UUID
    title: str
    status: str  # the task status: awaiting_approval, executing, pending, completed, failed ...
    tool: str = ""
    payload_hash: str = ""
    error: str = ""
    failed_steps: tuple[str, ...] = ()

    @property
    def active(self) -> bool:
        return self.status not in _TERMINAL

    @property
    def waiting(self) -> bool:
        return self.status == "awaiting_approval"


_TERMINAL: Final = frozenset({"completed", "failed", "cancelled", "canceled", "aborted"})


def payload_hash(tool: str, args: Mapping[str, Any]) -> str:
    """A stable hash of one tool call's exact arguments (the same payload, the same hash)."""
    text = json.dumps({"tool": tool, "args": args}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def status_words(job: SessionJob) -> str:
    """The plain status of a job."""
    if job.waiting:
        return "waiting for the user's approval (the approval card is in the chat above)"
    if job.status == "completed":
        if job.failed_steps:
            return "finished with problems: " + "; ".join(job.failed_steps[:2])
        return "done"
    if job.status == "failed":
        return "failed" + (f": {job.error}" if job.error else "")
    if job.status in ("cancelled", "canceled", "aborted"):
        return "cancelled"
    return "still running"


def followup_block(jobs: Sequence[SessionJob]) -> str:
    """The system text for a follow-up turn: the real state, and not to repeat an earlier reply."""
    lines = [
        "CURRENT STATE OF THIS CONVERSATION (read from the task records just now). The user's "
        "message is a short follow-up such as 'continue'."
    ]
    if jobs:
        for job in jobs[:5]:
            lines.append(f"- {job.title[:100] or 'A job'}: {status_words(job)}")
    else:
        lines.append("- No job is pending or running for this conversation.")
    lines.append(
        "Answer with this state ONCE, in one or two short sentences, in the user's language. Do "
        "NOT restate, repeat or re-announce an earlier assistant message. Do NOT call a tool to "
        "create something that is already listed above (it would be a duplicate). If nothing is "
        "pending, say so and ask in one short question what they want to continue."
    )
    return "\n".join(lines)


# ------------------------------------------------------------------------------- action claims

_THINGS: Final = (
    r"(?:page|pages|row|rows|database|db|notes?|file|files|task|tasks|job|jobs|request|approval|"
    r"email|mail|e-mail|event|reminder|draft|comment|block|blocks|entry|record|document|deck|"
    r"report|issue|pull\s+request|pr|message|memory|agent|calendar|invite|section|heading|title|"
    r"column|status|notion|github|reply|ticket|backup|folder)"
)
_THINGS_RE: Final = re.compile(rf"\b{_THINGS}\b", re.IGNORECASE)
_PAST: Final = (
    r"(?:created|saved|sent|added|updated|deleted|removed|archived|scheduled|written|submitted|"
    r"queued|prepared|drafted|posted|uploaded|renamed|moved|completed|booked|changed|edited|"
    r"replaced|appended|inserted|published|set\s+up|made|generated|built|stored|recorded)"
)
_CLAIM_EN: Final = re.compile(
    rf"\b(?:i(?:'ve|’ve|\s+have|\s+just|\s+already)?|we(?:'ve|’ve|\s+have)?)\s+(?:just\s+|already\s+|successfully\s+)?{_PAST}\b[^.!?\n]{{0,80}}\b{_THINGS}\b"
    rf"|\b{_THINGS}\b[^.!?\n]{{0,60}}\b(?:has|have|was|were|is|are)\s+(?:now\s+|been\s+|successfully\s+)+{_PAST}\b"
    rf"|\bsuccessfully\s+{_PAST}\b[^.!?\n]{{0,60}}\b{_THINGS}\b",
    re.IGNORECASE,
)
_CLAIM_HI: Final = re.compile(
    r"\b(?:maine|maine\s+aapka|humne)?\b[^.!?\n]{0,60}\b(?:kar\s+diya|kar\s+di|bana\s+diya|bana\s+di|"
    r"bhej\s+diya|bhej\s+di|jod\s+diya|jod\s+di|add\s+kar\s+diya|save\s+kar\s+diya|"
    r"update\s+kar\s+diya|likh\s+diya|taiyar\s+kar\s+diya|ban\s+gaya|ban\s+gayi|hata\s+diya)\b",
    re.IGNORECASE,
)
_NOT_A_CLAIM: Final = re.compile(
    r"\b(?:not|no|never|could\s+not|couldn'?t|can'?t|cannot|unable|failed|didn'?t|haven'?t|"
    r"hasn'?t|won'?t|wasn'?t|nahi|nahin|nhi|can|could|would|will|shall|if|once|after|when|"
    r"earlier|previously|before|pehle|last\s+time|above|already\s+waiting|still\s+waiting|"
    r"waiting\s+for|yet)\b",
    re.IGNORECASE,
)
_PENDING_CUES: Final = re.compile(
    r"\b(?:prepared|queued|submitted|drafted|approval|waiting|pending|taiyar|request)\b",
    re.IGNORECASE,
)


def claims_action_done(text: str) -> bool:
    """True when a sentence says a persistent thing was created, saved, sent, added or prepared.

    Sentences that are negated, conditional, a question, or about an earlier turn do not count.
    """
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        if "?" in sentence or _NOT_A_CLAIM.search(sentence):
            continue
        if _CLAIM_EN.search(sentence) or (
            _CLAIM_HI.search(sentence) and _THINGS_RE.search(sentence)
        ):
            return True
    return False


def claim_is_about_pending(text: str) -> bool:
    """True when the claim is about preparing or queueing (a pending approval can back it)."""
    return any(
        (_CLAIM_EN.search(s) or (_CLAIM_HI.search(s) and _THINGS_RE.search(s)))
        and _PENDING_CUES.search(s)
        for s in re.split(r"(?<=[.!?])\s+|\n+", text)
    )


def action_claim_correction(
    failures: Sequence[tuple[str, str]], pending_titles: Sequence[str]
) -> str:
    """One honest sentence to append when a reply claims an action that no tool this turn did."""
    parts = ["\n\nCorrection: nothing was created, saved or sent by this reply."]
    if failures:
        tool, reason = failures[-1]
        parts.append(f"The last attempt ({tool.replace('_', ' ')}) failed: {reason}")
    else:
        parts.append("No tool completed an action in this turn.")
    if pending_titles:
        parts.append(
            "Still waiting for your approval: "
            + "; ".join(t[:80] for t in pending_titles[:3])
            + "."
        )
    else:
        parts.append("Nothing is pending for your approval either.")
    return " ".join(parts)


# ------------------------------------------------------------------------- ungrounded answers

_READ_CUE: Final = re.compile(
    r"\b(?:what|which|show|list|read|see|check|summari[sz]e|tell|find|open|dikha\w*|kya|batao|"
    r"bata\w*|dekh\w*|kitne|kaun)\b",
    re.IGNORECASE,
)
_WRITE_CUE: Final = re.compile(
    r"\b(?:add|create|make|write|append|update|change|delete|remove|archive|replace|edit|rename|"
    r"banao|bana|likh\w*|jod\w*|badal\w*|hata\w*)\b",
    re.IGNORECASE,
)
_NOTION: Final = re.compile(r"\bnotion\b|\bmy\s+(?:notes|workspace|databases?)\b", re.IGNORECASE)
_GITHUB: Final = re.compile(
    r"\bgithub\b|\b(?:my|mere)\s+(?:repos?|repositories|pull\s+requests?|prs|commits)\b",
    re.IGNORECASE,
)
_DISCLOSES: Final = re.compile(
    r"\b(?:could\s+not|couldn'?t|can'?t|cannot|unable|not\s+connected|failed|didn'?t\s+(?:read|work)|"
    r"nahi|nahin|not\s+shared|no\s+access)\b|नहीं",
    re.IGNORECASE,
)


def needs_data_read(text: str) -> str | None:
    """'Notion' or 'GitHub' when the message asks about the user's own data there (a read), else
    None. A request to change something is not a read."""
    if _WRITE_CUE.search(text) or not _READ_CUE.search(text):
        return None
    if _NOTION.search(text):
        return "Notion"
    if _GITHUB.search(text):
        return "GitHub"
    return None


def ungrounded_notice(source: str, reply: str, reason: str) -> str:
    """The sentence appended when the question needed ``source`` data and no read succeeded.

    Empty when the reply already says it could not read it, or is only a question back.
    """
    stripped = reply.strip()
    if not stripped or stripped.endswith("?") or _DISCLOSES.search(stripped):
        return ""
    why = f" ({reason.rstrip('.')})" if reason else ""
    return (
        f"\n\nNote: I could not read your {source} in this reply{why}, so nothing above about it "
        "comes from your data."
    )


__all__ = [
    "SessionJob",
    "action_claim_correction",
    "claim_is_about_pending",
    "claims_action_done",
    "followup_block",
    "is_followup",
    "needs_data_read",
    "payload_hash",
    "status_words",
    "ungrounded_notice",
]
