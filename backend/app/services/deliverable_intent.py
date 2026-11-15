"""What the user's OWN words ask MAYA to produce (deterministic, no model call).

Owner rule: unless the user named a file or a destination (PDF, slides, Word, sheet, Notion, mail,
...), MAYA answers in chat and never makes a file by herself. ``requested_deliverable`` is the one
decision every path that can start a file uses: the chat tools, the planner, the long-document and
learning-book routers, the deep-research engine and the voice ``submit_task``.

It returns ``"chat"`` or one of ``FILE_KINDS``. Words such as notes, report, summary, research or
essay are NOT format words. Matching is case-insensitive and covers English and Hinglish.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Final

CHAT: Final = "chat"
# pdf, pptx (slides, deck), docx (Word), xlsx (sheet), csv, md, txt, html (web page), zip (project
# or code files), notion (a Notion page), mail (sent through the approval path), file (a file,
# format not named).
FILE_KINDS: Final = (
    "pdf",
    "pptx",
    "docx",
    "xlsx",
    "csv",
    "md",
    "txt",
    "html",
    "zip",
    "notion",
    "mail",
    "file",
)
# Kinds the voice model may name for submit_task (it must never pick one the user did not say).
VOICE_FILE_DELIVERABLES: Final = frozenset({"pdf", "pptx", "docx", "xlsx", "notion", "report"})

_VERB = (
    r"(?:make|create|build|write|generate|prepare|produce|design|draft|save|export|convert|"
    r"compile|give|send|download|need|want|banao|banaiye|bana|bnao|bna|banado|banana|likho|likh|"
    r"likhna|chahiye|chaiye|de\s*do|dedo|dena)"
)
_VERB_RE = re.compile(rf"\b{_VERB}\b|बना|लिख|चाहिए")
# A noun that is the topic, not the product: "notes on slide design", "about presentations".
_TOPIC_PREFIX = r"(?<!\bon\s)(?<!\babout\s)(?<!\bof\s)(?<!\bregarding\s)(?<!\bin\s)"

# Strong format words: naming one is enough.
_STRONG: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("pdf", re.compile(r"\bpdfs?\b|\.pdf\b|पीडीएफ|पीडिएफ")),
    (
        "pptx",
        re.compile(
            r"\bpptx?s?\b|\.pptx?\b|\bpower\s*point\b|\bslide\s*decks?\b|\bslideshow\b|पीपीटी|पावरपॉइंट"
        ),
    ),
    (
        "docx",
        re.compile(r"\bdocx\b|\.docx\b|\bms\s+word\b|\bword\s+(?:doc|docs|document|file)s?\b"),
    ),
    ("xlsx", re.compile(r"\bxlsx?\b|\.xlsx\b|\bexcel\b|एक्सेल")),
    ("csv", re.compile(r"\bcsv\b|\.csv\b")),
    ("md", re.compile(r"\bmarkdown\b|\.md\b|\bmd\s+(?:file|me|mein)\b|\breadme\b")),
    ("txt", re.compile(r"\.txt\b|\btxt\b|\b(?:plain\s+)?text\s+files?\b")),
    (
        "html",
        re.compile(
            r"\bhtml\s+(?:page|file)\b|\.html?\b|\blanding\s+pages?\b|\bweb\s*(?:site|page)s?\b"
        ),
    ),
    (
        "zip",
        re.compile(
            r"\bzip\b|\bproject\s+files?\b|\bcode\s+files?\b|\bsource\s+files?\b|"
            r"\bmulti[-\s]?files?\b|\bmultiple\s+files\b"
        ),
    ),
)
# Weak format words (could be the topic): they count only next to a creation or delivery verb.
_WEAK: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    (
        "pptx",
        re.compile(rf"{_TOPIC_PREFIX}\b(?:slides?|decks?|presentations?|ppt)\b|स्लाइड"),
    ),
    (
        "docx",
        re.compile(rf"{_TOPIC_PREFIX}\bdoc\b"),
    ),
    (
        "xlsx",
        re.compile(
            rf"{_TOPIC_PREFIX}(?<!cheat\s)\b(?:spreadsheets?|sheets?|workbooks?|tables?\s+in\s+excel)\b"
        ),
    ),
    (
        "zip",
        re.compile(
            rf"{_TOPIC_PREFIX}\b(?:(?:web\s*|react\s*|node\s*|vue\s*|full[-\s]?stack\s*)?apps?|"
            rf"(?:web\s*|react\s*|software\s*)?ap{{1,2}}l?ications?|projects?)\b"
        ),
    ),
)
_GENERIC_FILE = re.compile(
    rf"{_TOPIC_PREFIX}\b(?:files?|documents?|डॉक्यूमेंट|फाइल)\b|\bdownload\b|\bdownloadable\b"
)
# "as a file", "in a file", "file me", "download this": delivery wording that needs no verb.
_DELIVERY = re.compile(
    r"\b(?:as\s+an?\s+(?:file|document|download)|in\s+an?\s+(?:file|document)|"
    r"file\s+(?:me|mein|mai|main)|download(?:able)?)\b"
)
_NOTION = re.compile(r"\bnotion\b|नोशन")
_NOTION_ACTION = re.compile(
    rf"\b(?:page|database|db|me|mein|mai|main|par|pe|ko|to|in|into|on)\b|{_VERB}|\bsave|\badd\b"
)
_MAIL = re.compile(
    r"\b(?:e-?mail|mail|gmail)\s+(?:it|this|that|me|karo|kar\s*do|kardo|kar|bhejo|bhej\s*do|"
    r"bhej|to)\b|\b(?:send|bhej\w*)\b.{0,24}\b(?:by|via|through|on)\s+e-?mail\b|"
    r"\bmail\s+(?:kar|bhej)\w*\b|\b(?:it|this|isko|ise)\s+(?:e-?mail|mail)\b"
)
# A follow-up that points at earlier content ("make it a pdf" has a format word; these do not).
_SAVE_THIS = re.compile(
    r"\bsave\s+(?:this|it|that)\b(?!\s+(?:to\s+memory|as\s+(?:a\s+)?(?:preference|memory)))|"
    r"\b(?:isko|ise|isey|yeh|ye)\s+save\s+(?:kar|karo|kardo|kar\s*do)\b|"
    r"\b(?:make|turn)\s+(?:it|this|that)\s+(?:into\s+)?(?:a\s+)?file\b|"
    r"\b(?:isko|ise)\s+file\s+(?:bana|banao|me|mein)\b"
)
_FOLLOW_UP_WORDS = frozenset(
    "it this that isko ise isey ye yeh wo woh same above uska iska yes ok okay haan han ji "
    "kar karo kardo do dedo please pls make bana banao as a an in into the to of file".split()
)
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)

_NOTES_WORDS = re.compile(
    r"\b(?:notes?|reports?|essays?|summar(?:y|ies|ise|ize)|study\s+guides?|guides?|"
    r"itinerar(?:y|ies)|proposals?|resumes?|cvs?|cover\s+letters?|handbooks?|ebooks?|"
    r"write[-\s]?ups?|article|articles)\b"
)
OFFER_LINE: Final = "Want this as a PDF, Word, slides or a Notion page?"

_DEEP = re.compile(
    r"\bdeep(?:ly)?[\s-]+(?:research(?:ed|ing)?|dive)\b|"
    r"\bdeeply\s+research\b|\bresearch\s+deeply\b|\bdeep\s+research\b|"
    r"\bgehri\s+research\b|\bgehra\s+research\b|\bgehri\s+khoj\b|\bdeep\s+reserch\b|"
    r"गहरी\s+रिसर्च|डीप\s+रिसर्च"
)


def _check(text: str) -> str | None:
    """The kind this one message names, or ``None``."""
    lower = " ".join((text or "").split()).lower()
    if not lower:
        return None
    for kind, pattern in _STRONG:
        if pattern.search(lower):
            return kind
    if _NOTION.search(lower) and _NOTION_ACTION.search(lower):
        return "notion"
    if _MAIL.search(lower):
        return "mail"
    has_verb = _VERB_RE.search(lower) is not None
    for kind, pattern in _WEAK:
        if pattern.search(lower) and (has_verb or _DELIVERY.search(lower)):
            return kind
    if _SAVE_THIS.search(lower):
        return "file"
    if _DELIVERY.search(lower):
        return "file"
    if _GENERIC_FILE.search(lower) and has_verb:
        return "file"
    return None


def _is_follow_up(text: str) -> bool:
    """True for a short message that adds no topic of its own ("haan kar do", "make it", "ok")."""
    tokens = [t.lower() for t in _TOKEN.findall(text or "")]
    return 0 < len(tokens) <= 8 and all(t in _FOLLOW_UP_WORDS for t in tokens)


def requested_deliverable(text: str, history: Sequence[str] = ()) -> str:
    """``"chat"`` unless the user's words name a file or destination, else one of ``FILE_KINDS``.

    ``history`` holds the user's EARLIER messages of the same conversation. It matters only for a
    short follow-up that names no topic of its own ("haan kar do", "ok make it") after a message
    that named a format; "isko pdf bana do" names its format itself and needs no history.
    """
    found = _check(text)
    if found is not None:
        return found
    if history and _is_follow_up(text):
        for earlier in reversed(list(history)[-4:]):
            earlier_kind = _check(earlier)
            if earlier_kind is not None:
                return earlier_kind
    return CHAT


def wants_file(text: str, history: Sequence[str] = ()) -> bool:
    """True when the user asked for a file or a Notion page (mail is a send, not a file)."""
    kind = requested_deliverable(text, history)
    return kind not in (CHAT, "mail")


def wants_deep_research(text: str) -> bool:
    """True when the message asks for deep research ("deep research karo", "deeply research")."""
    lower = " ".join((text or "").split()).lower()
    return _DEEP.search(lower) is not None


def offers_format_line(text: str, history: Sequence[str] = ()) -> bool:
    """True for a chat-only request for notes, a report or a similar write-up (where ONE optional
    line offering a format helps). Never for a plain question or a request that named a format."""
    if requested_deliverable(text, history) != CHAT:
        return False
    return _NOTES_WORDS.search(" ".join((text or "").split()).lower()) is not None


def downgrade_voice_deliverable(instruction: str, deliverable: str | None) -> str | None:
    """The deliverable a voice ``submit_task`` may use. A file kind the words of the brief do not
    name becomes ``"none"`` (a chat answer); an honest kind is kept."""
    kind = (deliverable or "").lower()
    if kind not in VOICE_FILE_DELIVERABLES:
        return deliverable
    return deliverable if requested_deliverable(instruction) != CHAT else "none"
