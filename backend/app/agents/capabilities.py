"""What this deployment can actually do for the user, and honest refusal of what it cannot.

The planner must not substitute generic web research for a request that needs the user's PRIVATE
data (their GitHub repositories, inbox, calendar) or an integration that is not connected: the
search can never answer it and the task would only fail later with a confusing error. The set of
configured capabilities is derived from settings once at start (booleans only, never a secret) and
is given to the planner prompt; a small deterministic pre-check catches the clear cases before any
model call, and the planner's own ``unsupported`` outcome catches the rest.

A refusal is ``{ok: False, reason, hint}`` (``Refusal``): ``reason`` says in plain words what
cannot be done, ``hint`` says what to connect. No task is created for it.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING
from uuid import UUID

from app.mail.config import mailbox_from_settings

if TYPE_CHECKING:
    from app.core.config import Settings


@dataclass(frozen=True, slots=True)
class Capabilities:
    """Configured integrations (booleans derived from settings; never the credentials)."""

    github: bool = False
    gmail: bool = False
    calendar: bool = False
    notion: bool = False
    web_search: bool = True
    # The user's own mailbox (app password): read in chat, send after approval.
    mailbox: bool = False

    def prompt_block(self) -> str:
        def mark(flag: bool) -> str:
            return "CONNECTED" if flag else "NOT connected"

        return (
            "Capabilities of THIS deployment (plan only what is possible), by agent:\n"
            f'- "research" agent: web search with citations: {mark(self.web_search)}; '
            f"GitHub (list the user's repositories, open pull requests): {mark(self.github)}\n"
            '- "builder" agent: documents, decks, sheets, websites and file edits (always '
            "available)\n"
            f'- "operator" agent: email (send after approval, through Gmail or a connected '
            f"mailbox): {mark(self.gmail or self.mailbox)}; "
            f"Google Calendar (daily briefing): {mark(self.calendar)}; "
            f"Notion (tasks briefing): {mark(self.notion)}\n"
            "- Files: the user can attach files (the system passes them to the steps as "
            'input_files) and connect ONE local folder. Plan a "file_edit" step to edit an '
            "attached or folder file in place (the previous version is kept in a backup), and "
            'ONE "project" step to build a '
            "website. Results are saved in the connected folder; with no folder connected they "
            "go to MAYA's storage and the result says so.\n"
            + (
                "Reading the user's mail is done by the chat's mail tools, never by a step: do "
                "not plan a step that reads the inbox. "
                if self.mailbox
                else "Reading the user's inbox is NOT supported. "
            )
            + "Reading files outside the attached files and the connected folder is NOT "
            "supported."
        )


@dataclass(frozen=True, slots=True)
class Refusal:
    """A request this deployment cannot run, with what to do about it (the chat result shape)."""

    reason: str
    hint: str
    ok: bool = False
    code: str = "unsupported"
    # ``unsupported`` or ``needs_clarification`` (the request lacks a subject; ``question`` asks
    # for it and ``missing`` names what is missing).
    status: str = "unsupported"
    question: str = ""
    missing: tuple[str, ...] = ()

    def as_result(self) -> dict[str, object]:
        result: dict[str, object] = {
            "ok": False,
            "code": self.code,
            "status": self.status,
            "reason": self.reason,
            "hint": self.hint,
        }
        if self.question:
            result["question"] = self.question
            result["missing"] = list(self.missing)
        return result


class UnsupportedRequestError(Exception):
    """The planner decided the request needs something that is not available."""

    def __init__(self, reason: str, hint: str = "", *, refusal: Refusal | None = None) -> None:
        super().__init__(reason)
        self.refusal = refusal or Refusal(reason=reason, hint=hint)


_current = Capabilities()


def configure(settings: Settings) -> Capabilities:
    """Install the capabilities derived from validated settings (called once at start)."""
    global _current
    _current = Capabilities(
        github=settings.github_token is not None
        and bool(settings.github_token.get_secret_value().strip()),
        gmail=False,  # mail goes only through the user's mailbox (the `mailbox` flag below)
        calendar=settings.google_access_token is not None
        and bool(settings.google_access_token.get_secret_value().strip()),
        notion=settings.notion_token is not None
        and bool(settings.notion_token.get_secret_value().strip()),
        mailbox=mailbox_from_settings(settings) is not None,
    )
    return _current


def reset() -> None:
    global _current, _user_connections, _user_mailbox, _user_calendar
    _current = Capabilities()
    _user_connections = None
    _user_mailbox = None
    _user_calendar = None


# Per-user connections (Settings > Connections). The app installs one async function that says
# which of GitHub / Notion the given user can use (their own saved token, else the server default).
UserConnections = Callable[[UUID, UUID], Awaitable[tuple[bool, bool]]]
_user_connections: UserConnections | None = None
# Whether the user has a mailbox (their own saved one, else the server's).
UserMailbox = Callable[[UUID, UUID], Awaitable[bool]]
_user_mailbox: UserMailbox | None = None
_bound: ContextVar[Capabilities | None] = ContextVar("maya_capabilities", default=None)
# Whether the user may use the server's Google Calendar (the deployment owner only).
UserCalendar = Callable[[UUID, UUID], Awaitable[bool]]
_user_calendar: UserCalendar | None = None


def set_user_calendar(resolver: UserCalendar | None) -> None:
    global _user_calendar
    _user_calendar = resolver


def set_user_connections(resolver: UserConnections | None) -> None:
    global _user_connections
    _user_connections = resolver


def set_user_mailbox(resolver: UserMailbox | None) -> None:
    global _user_mailbox
    _user_mailbox = resolver


async def for_user(workspace_id: UUID, user_id: UUID) -> Capabilities:
    """The deployment's capabilities with GitHub and Notion decided for THIS user."""
    caps = _current
    if _user_mailbox is not None:
        caps = replace(caps, mailbox=await _user_mailbox(workspace_id, user_id))
    if _user_calendar is not None:
        # The server-level calendar flag is only true for the deployment owner.
        caps = replace(caps, calendar=caps.calendar and await _user_calendar(workspace_id, user_id))
    if _user_connections is None:
        return caps
    github, notion = await _user_connections(workspace_id, user_id)
    return replace(caps, github=github, notion=notion)


@contextmanager
def bound(caps: Capabilities) -> Iterator[None]:
    """Make ``current()`` answer with ``caps`` inside the block (planner and prechecks read it)."""
    token = _bound.set(caps)
    try:
        yield
    finally:
        _bound.reset(token)


def current() -> Capabilities:
    """The bound per-user capabilities, else (no database: single-user) the deployment's.

    Once per-user resolvers exist the unbound value must not claim server-level flags, so it
    reports nothing connected: callers that need a user's flags bind ``for_user`` first."""
    bound_caps = _bound.get()
    if bound_caps is not None:
        return bound_caps
    if _user_connections is not None or _user_mailbox is not None or _user_calendar is not None:
        return replace(
            _current, github=False, notion=False, calendar=False, mailbox=False, gmail=False
        )
    return _current


_GITHUB_REPOS = re.compile(
    r"\b(?:list|show|display|get|fetch|see|what(?:'s| are| is)?|which|give me|dikha(?:o|na)?)\b"
    r".{0,40}\b(?:my|our|mere|meri|mera)\b.{0,25}\b(?:repos?|repositories)\b"
    r"|^\W*(?:my|our)\s+(?:repos?|repositories)\W*$",
    re.IGNORECASE,
)
_INBOX = re.compile(
    r"\b(?:read|check|show|list|summari[sz]e|fetch)\b.{0,30}\b(?:my|our)\b.{0,20}"
    r"\b(?:e-?mails?|inbox|gmail)\b",
    re.IGNORECASE,
)
_MY_CALENDAR = re.compile(r"\b(?:my|our)\b.{0,20}\b(?:calendar|meetings)\b", re.IGNORECASE)
_MY_NOTION = re.compile(r"\b(?:my|our)\b.{0,20}\bnotion\b", re.IGNORECASE)


_GITHUB_COMMITS = re.compile(
    r"\b(?:recent(?:ly)?|last|latest)\s+(?:commit|push)"
    r"|\bwhat\s+(?:did|have)\s+i\s+(?:commit|push)"
    r"|\bmy\s+(?:commits|pushes)\b",
    re.IGNORECASE,
)


def is_repo_listing_request(instruction: str) -> bool:
    """True when the user asks to list/see THEIR repositories or their latest commits, without
    naming a repository. A request that names a repository URL is a read of that repository."""
    from app.mcp.connectors.github import find_repo_urls  # local: keeps imports light

    text = instruction or ""
    if not (_GITHUB_REPOS.search(text) or _GITHUB_COMMITS.search(text)):
        return False
    return not find_repo_urls(text)


def profile_login(instruction: str) -> str | None:
    """The login of a github.com profile URL the user names (``github.com/<login>``), else None.

    The request is then a listing of that account's public repositories, which needs no
    connection."""
    from app.mcp.connectors.github import find_profile_login  # local: keeps imports light

    return find_profile_login(instruction or "")


_REVIEW_WORDS = re.compile(
    r"\b(?:review|audit|analy[sz]e|analysis|inspect|assess|critique|code\s*review|look\s+at|"
    r"check|evaluate|summari[sz]e|explain|go\s+through|read)\b",
    re.IGNORECASE,
)


def repo_review_target(instruction: str) -> str | None:
    """``owner/name`` when the user asks to review/read a github.com repository by URL, else None.

    Deterministic (no model call). Public repositories need no token, so no connection is needed.
    """
    from app.mcp.connectors.github import find_repo_urls

    text = instruction or ""
    if not _REVIEW_WORDS.search(text):
        return None
    urls = find_repo_urls(text)
    return urls[0].full_name if urls else None


# --------------------------------------------------------------------------- clarification
_DELIVERABLES = re.compile(
    r"\b(?:ppts?|pptx|powerpoints?|presentations?|slides?|slideshows?|decks?|pdfs?|docx?|"
    r"word\s+docs?|word\s+documents?|documents?|xlsx|excel|spreadsheets?|sheets?|reports?|"
    r"notes?|ebooks?|brochures?|handbooks?|guides?)\b",
    re.IGNORECASE,
)
_WORD = re.compile(r"[^\W\d_]{2,}", re.UNICODE)
# Words that carry no subject: deliverables, verbs of making, fillers, English and Hinglish.
_NO_TOPIC = frozenset(
    """
    a an the of on about for with to in into at by as and or but is are was be am i me my mine we
    our us you your it its this that these those some any one something anything new nice good
    great best simple short quick small big long brief detailed full complete proper professional
    sample dummy random basic test demo please pls kindly can could would will shall should may
    just now also need want like wanted needed require create created make made makes making
    generate generated build prepare produce write give get do does done prepared draft design
    designed produce file files format type kind page pages slide slides word words sheet sheets
    ppt ppts pptx powerpoint powerpoints presentation presentations slideshow deck decks pdf pdfs
    doc docs docx word document documents xlsx excel spreadsheet spreadsheets report reports notes
    note ebook brochure handbook guide guides deliverable
    mujhe mujhko mera meri mere hamara hamari humein ek koi kuch kuchh bana banao banani banana
    banaiye banado bana de do dena dijiye dedo karo kar kare karna karni kariye chahiye chahie
    hai hain tha thi hoga hogi ho ka ki ke ko se me mein par liye lie wala wali wale aur ya
    toh to bhi hi na nahi yeh ye woh vo abhi jaldi zara thoda thodi ek
    """.split()
)


def needs_clarification(instruction: str) -> Refusal | None:
    """A "needs a topic" outcome for a deliverable request that names no subject, else None.

    Deterministic and conservative. The text must name a deliverable (ppt, pdf, report, notes ...)
    and, after removing deliverable words, verbs of making, stop words, fillers and numbers
    (English and Hinglish), nothing topical may remain. Any leftover word (a subject such as
    "solar", "AI", "Java", "Q3 sales") means the request is NOT blocked.
    """
    text = instruction or ""
    if not _DELIVERABLES.search(text):
        return None
    cleaned = re.sub(r"https?://\S+", " ", text)
    for token in _WORD.findall(cleaned.lower()):
        if token not in _NO_TOPIC:
            return None
    # A token that mixes letters and digits ("q3", "gpt4") is a subject too.
    if re.search(r"\b[^\W\d_]+\d+\w*|\b\d+[^\W\d_]+\w*", cleaned):
        return None
    from app.automation.acceptance import extract_acceptance  # local: keeps imports light

    wants_deck = bool(re.search(r"ppt|powerpoint|presentation|slide|deck", text, re.IGNORECASE))
    noun = "presentation" if wants_deck else "document"
    missing = ["topic"]
    question = f"What should the {noun} be about"
    if not extract_acceptance(text):
        missing.append("length")
        question += ", and roughly how many " + ("slides" if wants_deck else "pages")
    question += "?"
    return Refusal(
        reason=f"The {noun} needs a topic.",
        hint=question,
        code="needs_clarification",
        status="needs_clarification",
        question=question,
        missing=tuple(missing),
    )


def precheck(
    instruction: str, caps: Capabilities | None = None, *, attached: bool = False
) -> Refusal | None:
    """A refusal for a clear private-data request that this deployment cannot serve, else None.

    Deterministic (no model call). A repository listing is allowed when GitHub is connected: the
    ``github_list_repositories`` tool answers it. ``attached`` is True when the request comes with
    the user's own file: that file is the subject, so "make a ppt of this" needs no topic.
    """
    caps = caps or current()
    text = instruction or ""
    clarification = None if attached else needs_clarification(text)
    if clarification is not None:
        return clarification
    if is_repo_listing_request(text) and not caps.github:
        return Refusal(
            reason=(
                "I can't list your repositories yet. Connect GitHub in Settings > Connections so "
                "I can see your repositories."
            ),
            hint=(
                "Open Settings > Connections, connect GitHub, then ask again. A web search cannot "
                "see your private repositories."
            ),
        )
    if _INBOX.search(text):
        if caps.mailbox:
            return Refusal(
                reason="I can't read your inbox from a background task.",
                hint="Ask me in the chat (for example 'check my mail') and I will read it there.",
            )
        return Refusal(
            reason="I can't read your inbox: no mailbox is connected.",
            hint=(
                "Connect your mailbox in Settings > Connections (an app password) to let "
                "me read your mail in the chat. I can draft an email for you either way (it "
                "always asks for your approval before sending)."
            ),
        )
    if _MY_CALENDAR.search(text) and not caps.calendar:
        return Refusal(
            reason="I can't read your calendar: Google Calendar is not connected.",
            hint=(
                "Google Calendar is set up by the server administrator (MAYA_GOOGLE_ACCESS_TOKEN); "
                "it is not in Settings > Connections yet."
            ),
        )
    if _MY_NOTION.search(text) and not caps.notion:
        return Refusal(
            reason="I can't read your Notion: Notion is not connected.",
            hint=(
                "Open Settings > Connections, add your Notion token and database, press Test "
                "connection, then ask again."
            ),
        )
    return None
