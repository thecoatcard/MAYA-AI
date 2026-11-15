"""Deterministic first routing step for a chat message (no model call).

The router decides only the clear cases, in this order:

- ``agent``: "create an agent" requests (the custom-agent draft path, unchanged).
- ``approval``: a verb that sends, deletes, replaces, publishes or merges something outside the
  chat. Never run inline: only an approval-gated task may do it, and the model is told so.
- ``answer``: questions, explanations, drafts, translations and advice with no file intent. The
  model answers inline with no file or task tools.
- ``task``: big or background work (more than 8 pages, more than 12 slides, more than 2000 words,
  a website or multi-file project, research with sources, a repository review, "in the
  background", a schedule) and a binary deliverable (PDF, PowerPoint, Word, Excel) that names a
  topic. The message goes to the task submitter as written.
- ``model``: everything else that might be a small text file (md, txt, csv, json, html, code),
  a bare request ("create a ppt" with no topic) or an edit of an attached file. The model chooses
  between an inline answer, an inline file and a task using the tools it is given.

Matching is case-insensitive and covers English and Hinglish. Patterns are deliberately narrow:
when in doubt the router returns ``model`` (which may still answer inline), not ``task``.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from app.agents.custom.intent import is_create_agent_request

MAX_INLINE_PAGES: Final = 8
MAX_INLINE_SLIDES: Final = 12
MAX_INLINE_WORDS: Final = 2000


class Route(StrEnum):
    ANSWER = "answer"
    MODEL = "model"
    TASK = "task"
    APPROVAL = "approval"
    AGENT = "agent"
    # An image request: the model may only call generate_image (never submit_task).
    IMAGE = "image"


@dataclass(frozen=True, slots=True)
class Decision:
    route: Route
    reason: str


_CREATION = re.compile(
    r"\b(?:make|create|build|write|generate|prepare|produce|design|draft|save|export|convert|"
    r"compile|banao|banaiye|bana|bnao|bna|likh|likho|likhna|chahiye|chaiye)\b"
)
# A memory or preference statement ("yaad rakho: ...", "remember that ...", "save this
# preference"): the memory tool handles it. A noun like "reports" inside it is not a file request.
_MEMORY_STATEMENT = re.compile(
    r"(?:\b(?:yaad|yad|yaaad)\s*(?:rakh\w*|kar\s*lo|karlo)\b|याद\s*रख\w*|"
    r"\bremember\s+(?:that|this|my|me|i|we|to\s+always|:)|^remember\b|\bplease\s+remember\b|"
    r"\bsave\s+(?:this|my|the)\s+(?:preference|setting|choice)s?\b|"
    r"\b(?:meri|mera)\s+preference\b|\bnote\s+(?:kar\s*lo|karlo|rakho)\b)"
)
# Typing "approved" in the chat approves nothing: only the Approve button on the task card does.
_APPROVAL_IN_CHAT = re.compile(
    r"^(?:ok(?:ay)?\s+|yes\s+|haan?\s+|ji\s+)?(?:i\s+)?(?:approved?|approve\s+(?:it|this|kar\s*do|karo)|"
    r"approval\s+(?:de\s*diya|de\s*do|diya)|manzoor|manjoor)(?:\s+(?:hai|h|kar\s*diya))?[\s.!]*$"
)
# "<address> par ek test mail draft karo": drafting an email to a named address is the
# approval-gated email step (the draft is never only chat text).
_EMAIL_DRAFT_VERB = re.compile(r"\b(?:draft|compose|write|likho|likh|bana|banao|prepare)\b")
_EDIT = re.compile(
    r"\b(?:fix|edit|change|update|add|remove|correct|improve|translate|rewrite|format|"
    r"badlo|badal|sudhaar|theek|hata)\b"
)
_BINARY = re.compile(
    r"\b(?:pdfs?|pptx?|powerpoint|power\s+point|slide\s*decks?|decks?|presentations?|slideshow|"
    r"slides?|docx|word\s+documents?|ms\s+word|xlsx|excel|spreadsheets?|workbooks?|sheets?)\b"
)
_BINARY_EXT = re.compile(r"\.(?:pdf|pptx|docx|xlsx)\b")
_TEXT_FILE = re.compile(
    r"\b(?:csv|json|md|markdown|txt|html?|css|js|tsx|jsx|py|java|sql|yaml|yml|xml|toml|readme)\b"
)
_TEXT_EXT = re.compile(
    r"\.(?:md|txt|csv|json|html?|css|js|tsx|jsx|py|java|c|cpp|cs|go|rs|sql|ya?ml|xml|toml|ini|log)\b"
)
_TEXT_NOUN = re.compile(
    r"\b(?:notes?|reports?|resumes?|cvs?|cover\s+letters?|letters?|essays?|checklists?|"
    r"study\s+guides?|guides?|budgets?|plans?|itineraries|templates?|proposals?|todo\s+list|"
    r"text\s+files?)\b"
)
_PROJECT_NOUN = re.compile(
    r"\b(?:websites?|web\s*sites?|landing\s+pages?|portfolios?|multi[-\s]?files?|multiple\s+files|"
    r"multi[-\s]?page|web\s*apps?|full[-\s]?stack|complete\s+(?:app|project|website)|ebooks?|"
    r"e-books?|handbooks?|chapters?)\b"
)
_BACKGROUND = re.compile(
    r"\b(?:in\s+the\s+background|in\s+background|background\s+(?:me|mein|mai|main|task|job)|"
    r"run\s+(?:it\s+|this\s+)?in\s+(?:the\s+)?background|as\s+a\s+background\s+task|"
    r"task\s+(?:banao|bana\s*do|bana\s*dena|bnao)|as\s+a\s+task|schedul(?:e|ed|ing)|"
    r"every\s+(?:day|week|morning|month|monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
    r"daily\s+at|recurring|har\s+(?:din|hafte|mahine)|roz\s+(?:subah|raat))\b"
)
_SCHEDULE_REQUEST = re.compile(
    r"\b(?:schedul(?:e|es|ed|ing|er)|recurring|daily|weekly|weekdays|"
    r"every\s+(?:day|week|morning|evening|night|weekday|monday|tuesday|wednesday|thursday|friday|"
    r"saturday|sunday)|har\s+(?:din|hafte|subah)|roz(?:ana)?)\b"
)
_RESEARCH = re.compile(
    r"\b(?:research(?:e[sd])?|cite|citing|citations?|"
    r"with\s+(?:credible\s+|reliable\s+|online\s+)?(?:sources?|citations?|references?))\b"
)
_REPO_REVIEW = re.compile(r"\b(?:github|repo(?:sitory)?|codebase|pull\s+request)\b")
_REPO_ACTION = re.compile(r"\b(?:review|analy[sz]e|audit|findings)\b")
_PAGES = re.compile(r"\b(\d{1,4})[\s-]*(?:pages?|pgs?|page\s+ki|pages\s+ka|pannon|panne)\b")
_SLIDES = re.compile(r"\b(\d{1,3})[\s-]*(?:slides?|slide\s+ki|slides\s+ki|slide\s+ka)\b")
_WORDS = re.compile(r"\b(\d[\d,]*)[\s-]*(?:words?|shabd|shabdon)\b")
_QUESTION_START = re.compile(
    r"^(?:what|why|how|when|where|who|whom|whose|which|is|are|can|could|does|do|explain|define|"
    r"describe|tell|difference|kya|kyu|kyun|kaise|kab|kahan|kaun|kaunsa|samjhao|samjha|batao|"
    r"bata|bataiye)\b"
)
_APPROVAL = re.compile(
    r"\b(?:send|sends|sending|forward|forwards|bhej(?:o|do|dena|dein|diya|di|na|ni|iye|a)?|"
    r"publish|deploy|delete|deletes|erase|overwrite|replace\s+(?:the\s+|my\s+)?original|"
    r"pull\s+request|merge|post\s+(?:it\s+)?(?:on|to))\b"
)
_AS_FILE = re.compile(
    r"\b(?:as\s+a\s+(?:file|text\s+file|document)|in\s+a\s+file|file\s+(?:me|mein)|download)\b"
)
# Phase 2 (ADR-0026): a request for current facts from the web. Research and creation words are
# matched earlier, so "research X with sources" and "make a report" never end here.
_WEB_LOOKUP = re.compile(
    r"\b(?:latest|news|today'?s|aaj\s+ki|abhi\s+ki|right\s+now|"
    r"search\s+(?:the\s+)?(?:web|internet|online)|google\s+(?:it|this|for|search)|look\s+up|"
    r"stock\s+price|weather|price\s+of)\b"
)
_EMAIL_NOUN = re.compile(r"\b(?:e-?mails?|mails?|gmail|inbox)\b|@")
_NEGATION = re.compile(r"\b(?:not|don'?t|dont|never|nahi|nahin|mat|na|without|bina)\s*$")
_SEND_ME = re.compile(r"\bsend\s+me\b")
_TOKEN = re.compile(r"[a-z]+")

_FILLER: Final = frozenset(
    "a an the ek ki ka ke ko mujhe mere meri liye me mein main mai to for of on about with and "
    "please pls do dena dijiye de dein kar karo jisme ho hai hain ye yeh wo woh this that my i you "
    "can could will would just quick small chhota chhoti short simple one new in into from by as "
    "is are it its be some any all".split()
)
_NOUN_TOKENS: Final = frozenset(
    "pdf pdfs ppt ppts pptx powerpoint presentation presentations slide slides deck decks docx "
    "word document documents xlsx excel spreadsheet spreadsheets sheet sheets workbook workbooks "
    "csv json md markdown txt html htm css js tsx jsx py java sql yaml yml xml toml ini readme "
    "todo notes note report reports resume cv letter letters essay essays checklist guide guides "
    "budget plan plans "
    "template templates file files page pages words ebook handbook website landing portfolio "
    "chapters itinerary proposal".split()
)
_VERB_TOKENS: Final = frozenset(
    "make create build write generate prepare produce design draft save export convert compile "
    "banao banaiye bana bnao bna likh likho likhna chahiye chaiye".split()
)


def _approval_request(lower: str) -> bool:
    """True when a send, delete, replace, publish or merge verb is used and not negated.

    "send me the file" is delivery to the user, not an outside action, so it is not a match.
    """
    stripped = _SEND_ME.sub(" ", lower)
    for match in _APPROVAL.finditer(stripped):
        if not _NEGATION.search(stripped[max(0, match.start() - 25) : match.start()]):
            return True
    return re.search(r"\breplace\s+(?:the\s+|my\s+)?original\b", stripped) is not None


def _is_question(lower: str, value: str) -> bool:
    return value.endswith("?") or _QUESTION_START.match(lower) is not None


def _size_over_limit(lower: str) -> str | None:
    for match in _PAGES.finditer(lower):
        if int(match.group(1)) > MAX_INLINE_PAGES:
            return "pages"
    for match in _SLIDES.finditer(lower):
        if int(match.group(1)) > MAX_INLINE_SLIDES:
            return "slides"
    for match in _WORDS.finditer(lower):
        if int(match.group(1).replace(",", "")) > MAX_INLINE_WORDS:
            return "words"
    return None


def _is_bare(lower: str) -> bool:
    """True when nothing but the verb and the deliverable noun is named (no topic, no content)."""
    for token in _TOKEN.findall(lower):
        if token not in _FILLER and token not in _NOUN_TOKENS and token not in _VERB_TOKENS:
            return False
    return True


_IMAGE_NOUN = re.compile(
    r"\b(?:images?|pictures?|photos?|photographs?|portraits?|illustrations?|logos?|drawings?|"
    r"paintings?|sketch(?:es)?|wallpapers?|artwork|diagrams?|tasveer\w*|tasvir\w*|chitra)\b"
)
_IMAGE_VERB = re.compile(r"\b(?:draw|paint|sketch|illustrate)\b")


def _is_image_request(lower: str, creation: bool) -> bool:
    """An image noun with a make or draw verb, and no document noun (a deck, PDF or text file that
    happens to show pictures is not an image request)."""
    if _IMAGE_NOUN.search(lower) is None or not (creation or _IMAGE_VERB.search(lower)):
        return False
    return not (
        _BINARY.search(lower)
        or _BINARY_EXT.search(lower)
        or _TEXT_FILE.search(lower)
        or _TEXT_EXT.search(lower)
        or _TEXT_NOUN.search(lower)
        or _PROJECT_NOUN.search(lower)
    )


# An edit of the file already made or shown in this conversation ("slide 3 mein ek point add
# karo"). Only used when the conversation has a live file; matching is typo tolerant.
EDIT_WORKING_FILE_REASON: Final = "edit_working_file"
_ANY_FORMAT: Final = frozenset({"pdf", "docx", "pptx", "xlsx", "html", "md", "txt", "csv", "json"})
_SHEET_FORMATS: Final = frozenset({"xlsx", "csv", "docx", "pdf"})
# Part noun -> the file formats it can refer to. Generic parts fit any format.
_PART_FORMATS: Final[dict[str, frozenset[str]]] = {
    "slide": frozenset({"pptx"}),
    "slides": frozenset({"pptx"}),
    "page": frozenset({"pdf", "docx", "pptx"}),
    "pages": frozenset({"pdf", "docx", "pptx"}),
    "paragraph": frozenset({"pdf", "docx", "md", "txt"}),
    "section": frozenset({"pdf", "docx", "md", "html"}),
    "row": _SHEET_FORMATS,
    "column": _SHEET_FORMATS,
    "cell": _SHEET_FORMATS,
    "sheet": frozenset({"xlsx"}),
    "total": _SHEET_FORMATS,
    "heading": _ANY_FORMAT,
    "title": _ANY_FORMAT,
    "table": _ANY_FORMAT,
    "chart": _ANY_FORMAT,
    "font": _ANY_FORMAT,
    "colour": _ANY_FORMAT,
    "color": _ANY_FORMAT,
    "theme": _ANY_FORMAT,
    "point": _ANY_FORMAT,
    "bullet": _ANY_FORMAT,
    "image": _ANY_FORMAT,
    "text": _ANY_FORMAT,
    "line": _ANY_FORMAT,
    "footer": _ANY_FORMAT,
    "header": _ANY_FORMAT,
    "margin": _ANY_FORMAT,
}
_EDIT_VERBS: Final = frozenset(
    "add remove change update correct fix edit replace rename bold resize shorten lengthen "
    "reduce increase decrease move swap reorder insert set badlo badal badhao badha ghatao "
    "ghata hata hatao jodo jod dalo daalo daal karo kardo kar kijiye kijiyega".split()
)
_NEW_FILE_VERBS: Final = frozenset(
    "make create build write generate prepare produce design draft save export convert compile "
    "banao banaiye bana bnao bna likho likh likhna".split()
)
_REFERENCE_WORDS: Final = frozenset(
    "isko isse ise isme ismein is ye yeh this it uska uski iska iski isme".split()
)


def _fuzzy_in(token: str, vocabulary: Iterable[str]) -> str | None:
    """The vocabulary word this token is (exactly, or by a one or two letter typo), else None."""
    words = tuple(vocabulary)
    if token in words:
        return token
    if len(token) < 4:
        return None
    close = difflib.get_close_matches(token, words, n=1, cutoff=0.8)
    return close[0] if close else None


def _edits_working_file(lower: str, live_formats: frozenset[str]) -> bool:
    """True when the message edits a part of the live file (and does not ask for a new file)."""
    tokens = _TOKEN.findall(lower)
    if not tokens or len(tokens) > 24:
        return False
    if any(t in _NEW_FILE_VERBS for t in tokens):
        return False
    if any(t in ("naya", "nayi", "nai", "new", "another") for t in tokens) and not any(
        t in ("add", "insert", "jodo") for t in tokens
    ):
        return False
    has_verb = any(_fuzzy_in(t, _EDIT_VERBS) or t in ("ad", "ade") for t in tokens)
    if not has_verb:
        return False
    for token in tokens:
        part = _fuzzy_in(token, _PART_FORMATS)
        if part is not None and _PART_FORMATS[part] & live_formats:
            return True
    # A short imperative that points at the file ("isko chhota karo").
    return len(tokens) <= 8 and any(t in _REFERENCE_WORDS for t in tokens)


def states_explicit_spec(text: str) -> bool:
    """True when the user named a count (pages, slides, words) or a file format.

    A model hint (ADR-0029) never overrides such a message: what the user spelled out wins.
    """
    lower = " ".join((text or "").split()).lower()
    return any(
        pattern.search(lower) is not None
        for pattern in (
            _PAGES,
            _SLIDES,
            _WORDS,
            _BINARY,
            _BINARY_EXT,
            _TEXT_FILE,
            _TEXT_EXT,
        )
    )


def route_message(
    text: str,
    *,
    has_attachments: bool = False,
    live_formats: frozenset[str] = frozenset(),
    schedules: bool = False,
    tool_loop: bool = False,
) -> Decision:
    """The deterministic route for one message. ``model`` means the model must choose.

    ``live_formats`` are the formats of the file(s) already made or shown in this conversation;
    a message that edits one of them is an edit turn, never a new background task.
    When ``tool_loop`` is True, binary deliverables, project requests, and text length limits
    route to the model with the unified agent runtime rather than preemptively forcing a task.
    """
    value = " ".join((text or "").split())
    lower = value.lower()
    if not value:
        return Decision(Route.ANSWER, "empty")
    if is_create_agent_request(value):
        return Decision(Route.AGENT, "create_agent")
    if (
        _MEMORY_STATEMENT.search(lower)
        and not (
            _BINARY.search(lower)
            or _BINARY_EXT.search(lower)
            or _TEXT_FILE.search(lower)
            or _TEXT_EXT.search(lower)
        )
        and not _approval_request(lower)
    ):
        return Decision(Route.ANSWER, "memory_statement")
    if _APPROVAL_IN_CHAT.match(lower):
        return Decision(Route.ANSWER, "approval_in_chat")
    if (
        "@" in lower
        and re.search(r"\b(?:e-?mails?|mails?|gmail)\b", lower)
        and _EMAIL_DRAFT_VERB.search(lower)
        and not _is_question(lower, value)
        and not (_BINARY.search(lower) or _TEXT_FILE.search(lower))
    ):
        return Decision(Route.APPROVAL, "approval_email")
    if schedules and _SCHEDULE_REQUEST.search(lower):
        # With the scheduler on, "schedule a daily report" is for schedule_report (ADR-0033),
        # not a one-off background task.
        return Decision(Route.ANSWER, "schedule_request")
    creation = _CREATION.search(lower) is not None
    image_request = _is_image_request(lower, creation)
    if _is_question(lower, value) and not creation and not image_request:
        return Decision(Route.ANSWER, "web_lookup" if _WEB_LOOKUP.search(lower) else "question")
    if _approval_request(lower):
        email = _EMAIL_NOUN.search(lower) is not None
        return Decision(Route.APPROVAL, "approval_email" if email else "approval_verb")
    if image_request:
        return Decision(Route.IMAGE, "image_request")
    if _BACKGROUND.search(lower):
        return Decision(Route.TASK, "background")
    if _RESEARCH.search(lower):
        return Decision(Route.TASK, "research")
    if _REPO_REVIEW.search(lower) and _REPO_ACTION.search(lower):
        return Decision(Route.TASK, "repository_review")
    binary = _BINARY.search(lower) is not None or _BINARY_EXT.search(lower) is not None
    text_file = _TEXT_FILE.search(lower) is not None or _TEXT_EXT.search(lower) is not None
    text_noun = _TEXT_NOUN.search(lower) is not None
    if (creation or binary or text_file or text_noun) and (size := _size_over_limit(lower)):
        return Decision(Route.MODEL if tool_loop else Route.TASK, f"over_limit_{size}")
    if creation and _PROJECT_NOUN.search(lower):
        return Decision(Route.MODEL if tool_loop else Route.TASK, "project")
    if live_formats and _edits_working_file(lower, live_formats):
        return Decision(Route.MODEL, EDIT_WORKING_FILE_REASON)
    if binary and not has_attachments and not _is_bare(lower):
        return Decision(Route.MODEL if tool_loop else Route.TASK, "binary_deliverable")
    if binary or text_file:
        return Decision(Route.MODEL, "file_candidate")
    if creation and text_noun:
        return Decision(Route.MODEL, "text_deliverable")
    if has_attachments and _EDIT.search(lower):
        return Decision(Route.MODEL, "edit_attached")
    if _AS_FILE.search(lower):
        return Decision(Route.MODEL, "file_delivery")
    if _WEB_LOOKUP.search(lower) and not creation:
        return Decision(Route.ANSWER, "web_lookup")
    return Decision(Route.ANSWER, "no_file_intent")


__all__ = [
    "EDIT_WORKING_FILE_REASON",
    "Decision",
    "Route",
    "route_message",
    "states_explicit_spec",
]
