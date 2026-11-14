"""Required-details gate for notes, document, deck, sheet, research and Notion-save requests.

A pure, deterministic check (no model call, no I/O) that says which details a request still
lacks BEFORE any task or file is created: the ``topic`` (what it is about), the ``depth`` (notes
only), the Notion ``destination`` and the code ``language`` (programming notes only).

The check reads the USER's own words and the open conversation, never the model's instruction and
never saved memory or standing preferences: a preference such as "likes Java" may pick the code
language, but it is never a topic. The chat path shows the missing slots as ONE ``ask_user`` card
(``build_questions``); the task service uses the same function as a backstop (``backstop_refusal``)
so the model cannot bypass it, and ``topic_supported`` rejects a task whose topic does not come
from the user.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from app.agents import capabilities
from app.agents.learning_book_plan import detect_code_language, is_programming_topic

__all__ = [
    "GateInput",
    "Missing",
    "build_questions",
    "check",
    "parse_notion_pages",
    "topic_supported",
]

SLOT_TOPIC: Final = "topic"
SLOT_DEPTH: Final = "depth"
SLOT_DESTINATION: Final = "destination"
SLOT_LANGUAGE: Final = "language"
SLOT_SOURCE: Final = "source"
DEPTH_OPTIONS: Final = ("Quick", "Standard", "Full book")
CODE_LANGUAGE_OPTIONS: Final = ("Python", "Java", "JavaScript", "C++")
MAX_PAGE_OPTIONS: Final = 4
SKIPPED: Final = "(skipped)"

_NOTES: Final = re.compile(r"\bnotes?\b", re.IGNORECASE)
_NOT_STUDY: Final = re.compile(
    r"\b(meeting|release|patch|sticky|speaker|call|lab|field|stage)\s+notes?\b"
    r"|\bnotes?\s+(app|taking|section)\b|\b(take|add|jot|leave)\s+(down\s+)?(a\s+)?notes?\b"
    r"|\b(my|existing|old|saved)\s+notes?\b|\b(find|search|show|open|read|list|delete|edit)\b"
    r".{0,24}\bnotes?\b"
    r"|\bnotes?\.[a-z0-9]{2,4}\b",  # a file named notes.md is not study notes
    re.IGNORECASE,
)
_DOCUMENT: Final = re.compile(
    r"\b(?:pdfs?|docx?|word\s+docs?|word\s+documents?|documents?|reports?|essays?|articles?|"
    r"ebooks?|handbooks?|brochures?)\b",
    re.IGNORECASE,
)
_DECK: Final = re.compile(
    r"\b(?:ppts?|pptx|powerpoints?|presentations?|slides?|slideshows?|decks?)\b", re.IGNORECASE
)
_SHEET: Final = re.compile(r"\b(?:xlsx|excel|spreadsheets?|sheets?|csv)\b", re.IGNORECASE)
_RESEARCH: Final = re.compile(r"\bresearch\b", re.IGNORECASE)
_MAKE: Final = re.compile(
    r"\b(?:make|create|write|prepare|generate|build|draft|compose|produce|banao|bana|banado|"
    r"banana|banaiye|likho|likh|likhna|tayar|taiyar|chahiye|chahie|need|want|give|do|dedo|de|"
    r"karo|kar|kardo|krdo|kro)\b",
    re.IGNORECASE,
)
_NOTION: Final = re.compile(r"\bnotion\b", re.IGNORECASE)
_SAVE: Final = re.compile(
    r"\b(?:save|add|put|store|upload|export|send|push|write|create|make|likh\w*|daal\w*|dal\w*|"
    r"bhej\w*|rakh\w*|save\s*kar\w*|banao|bana)\b",
    re.IGNORECASE,
)
_NOTION_URL: Final = re.compile(
    r"https?://(?:[\w-]+\.)?notion\.(?:so|site)/\S+|notion://\S+|\b[0-9a-f]{32}\b", re.IGNORECASE
)
_NOTION_NAMED: Final = re.compile(
    r"\bnotion\b[^.\n]{0,40}[\"'“‘`][^\"'”’`]{2,80}[\"'”’`]"
    r"|\b(?:page|database|db)\s+(?:named|called|titled)\s+\S+"
    r"|\b(?:in|into|under|inside)\s+(?:my\s+|the\s+)?[\"'“‘`][^\"'”’`]{2,80}[\"'”’`]\s+"
    r"(?:page|database|db)\b",
    re.IGNORECASE,
)
_DEPTH_GIVEN: Final = re.compile(
    r"\b\d+\s*(?:-\s*\d+\s*)?(?:pages?|pgs?|words?|chapters?|slides?|sections?|lines?|paragraphs?)\b"
    r"|\b(?:full\s+book|book|quick|short|brief|concise|one[- ]pager|cheat\s*sheet|summary|"
    r"standard|medium|crisp|outline)\b",
    re.IGNORECASE,
)
# "on this", "isi topic par", "is par": the subject is the previous message, file or document.
_REFERENCE: Final = re.compile(
    r"\b(?:is|iss|isi|issi|us|usi|isko|iska|iske|isme|uspar)\s+(?:topic\s+|file\s+|document\s+|"
    r"message\s+|subject\s+)?(?:par|pe|ke\s+bare|ke\s+baare|per|ko|ka|ki|ke)\b"
    r"|\b(?:isi|usi|same)\s+(?:topic|subject|cheez)\b"
    r"|\b(?:this|that|these|same|above|previous|last|earlier)\s+(?:topic|subject|file|doc|"
    r"document|message|chat|conversation|content|one)\b"
    r"|\b(?:on|about|from|of|for)\s+(?:this|that|it|the\s+above)\b|\bisi\s+par\b|\bis\s+par\b",
    re.IGNORECASE,
)
_HINGLISH: Final = re.compile(
    r"\b(?:banao|bana|banado|karo|kar|kardo|likho|chahiye|mein|me|par|ka|ki|ke|ko|hai|nahi|"
    r"isi|iss|is\s+par)\b",
    re.IGNORECASE,
)
_NAMED_LANGUAGE: Final = re.compile(
    r"(?<!\w)(?:python|java|javascript|typescript|c\+\+|cpp|c#|csharp|golang|rust|kotlin|swift|"
    r"ruby|php|scala|sql|dart)(?!\w)",
    re.IGNORECASE,
)
# "add this notes in notion", "save the pdf to notion", "isko notion me daal do": the content to
# save already exists in the conversation; it is not a topic and nothing is written anew.
_EXISTING_REF: Final = re.compile(
    r"\b(?:this|these|that|those|the|above|previous|last|same|ye|yeh|is|iss|ise|inhe|yahi|"
    r"upar\s+wale|upar\s+ke)\s+(?:notes?|pdf|pdfs|file|doc|docx|document|answer|content|text|"
    r"summary|reply|message|book|chapters?)\b"
    r"|\b(?:isko|inko|ise|inhe|usko)\b|\b(?:add|save|put|store|copy|paste)\s+(?:it|this|these)\b",
    re.IGNORECASE,
)
_AS_IS: Final = re.compile(
    r"\b(?:as\s+it\s+is|as\s+is|as-is|verbatim|word\s+for\s+word|jaisa\s+hai(?:\s+waisa)?|"
    r"jyon\s+ka\s+tyon|bina\s+badle|without\s+(?:any\s+)?(?:change|changes|rewriting|editing))\b",
    re.IGNORECASE,
)
_SAVE_EXISTING_VERB: Final = re.compile(
    r"\b(?:save|add|put|store|upload|export|push|copy|paste|move|daal\w*|dal\w*|rakh\w*|"
    r"save\s*kar\w*|bhej\w*|likh\s*do|write)\b",
    re.IGNORECASE,
)
# Verbs that ask for NEW content: the request is a writing job, never a save of existing text.
_NEW_CONTENT_VERB: Final = re.compile(
    r"\b(?:generate|explain|research|summari[sz]e|expand|rewrite|draft|compose|banao|bana|banado|"
    r"likho|samjhao|detail\s+me)\b",
    re.IGNORECASE,
)
# With no explicit reference, a verb of making means the user wants something written.
_CREATE_VERB: Final = re.compile(
    r"\b(?:make|create|prepare|build|produce|banao|bana|banado|banana|chahiye|chahie|need|want|"
    r"likho|likh)\b",
    re.IGNORECASE,
)
_SAVE_FILLER: Final = frozenset(
    "kardo karo kar kr do dedo dena de jaisa waisa hai jyon tyon ka tyon bhi please pls".split()
)
_ANSWER_PART: Final = re.compile(
    r"(?:^|(?<=;)|(?<=\n))\s*(Topic|Depth|Destination|Language|Source)\s*:\s*([^;\n]*)",
    re.IGNORECASE,
)
_TOKEN: Final = re.compile(r"[^\W_]{2,}", re.UNICODE)
# Extra no-subject words on top of the capabilities vocabulary (headers, requests to save, etc.).
_EXTRA_STOP: Final = frozenset(
    """
    save add put store upload export send push notion topic depth destination language skipped
    research karke kr krdo kro kijiye karein likho likh likhna daalo dalo daal bhej bhejo rakh
    rakho rakhna mere liye lie mera meri mujhe humein hume apna apne apni tum aap please pls
    detail details detailed detail complete comprehensive proper properly thoroughly
    hinglish english hindi simple easy language bhasha mein
    which what who how when where why there here then than so if not no yes ok okay haan han ha
    full book quick standard isi iss issi usi uska iska isko usko same above
    """.split()
)


@dataclass(frozen=True, slots=True)
class GateInput:
    """What the gate may read. ``request`` is the user's own message (on an answer turn: the
    request that was asked about plus the answer). ``preferences`` is the saved-memory text: it is
    only ever used for the code language."""

    request: str
    history_user: Sequence[str] = ()
    has_attachment: bool = False
    has_prior_content: bool = False  # an open or earlier document / assistant answer exists
    preferences: str = ""
    notion_connected: bool = True
    has_default_destination: bool = False  # the user confirmed a default Notion parent


@dataclass(frozen=True, slots=True)
class Missing:
    slots: tuple[str, ...] = ()
    kind: str = ""
    hinglish: bool = False

    def __bool__(self) -> bool:
        return bool(self.slots)


def _answers(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for match in _ANSWER_PART.finditer(text or ""):
        found[match.group(1).lower()] = match.group(2).strip()
    return found


def _answered(value: str | None) -> bool:
    return value is not None and bool(value.strip()) and value.strip().lower() != SKIPPED


def _base(text: str) -> str:
    return _ANSWER_PART.sub(" ", text or "")


def strip_answers(text: str) -> str:
    """``text`` without its ``Header: value`` card-answer fields (the request itself)."""
    return _base(text).strip()


def _tokens(text: str) -> list[str]:
    cleaned = re.sub(r"https?://\S+|notion://\S+", " ", text.lower())
    return [t for t in _TOKEN.findall(cleaned) if not t.isdigit()]


def _topic_tokens(text: str) -> list[str]:
    """Words that could name a subject: not a deliverable, verb of making, filler or number."""
    return [
        t
        for t in _tokens(text)
        if t not in capabilities._NO_TOPIC and t not in _EXTRA_STOP  # noqa: SLF001
    ]


def topic_words(text: str) -> list[str]:
    """The topic-like words of a request (for an advisory ranking)."""
    return _topic_tokens(_base(text))[:8]


def weak_topic(data: GateInput) -> bool:
    """True when the only evidence of a topic is a single word (the gate passed, but it is
    ambiguous and the decision model may be asked)."""
    base = _base(data.request)
    return (
        bool(detect_kind(base))
        and not data.has_attachment
        and not _answers(data.request)
        and len(_topic_tokens(base)) == 1
        and not _REFERENCE.search(base)
    )


def references_existing(text: str) -> bool:
    """True when the message points at content that already exists ("this notes", "isko")."""
    return _EXISTING_REF.search(_base(text)) is not None


def is_as_is(text: str) -> bool:
    """True when the user wants the content copied unchanged ("as it is", "jaisa hai waisa")."""
    return _AS_IS.search(_base(text)) is not None


def wants_save_existing(text: str, *, has_prior_content: bool = True) -> bool:
    """True when ``text`` asks to save content that already exists to Notion (a save, not a
    writing job): Notion plus a save verb, no verb that asks for NEW content, and either an
    explicit reference ("add this notes in notion", "as it is") or, with prior content, no
    subject of its own ("Notion me save kardo")."""
    base = _base(text)
    if not (_NOTION.search(base) and _SAVE_EXISTING_VERB.search(base)):
        return False
    if _NEW_CONTENT_VERB.search(base):
        return False
    if _EXISTING_REF.search(base) or _AS_IS.search(base):
        return True
    if not has_prior_content or _CREATE_VERB.search(base):
        return False
    return not [t for t in _topic_tokens(base) if t not in _SAVE_FILLER]


def detect_kind(text: str) -> str:
    """``notes``, ``document``, ``deck``, ``sheet``, ``research``, ``notion_save`` or ``""``.

    A deliverable only counts when the message asks to make it (a question about what a PDF is
    never starts the gate); ``research`` counts when it is the request itself."""
    body = text or ""
    if _NOTION.search(body) and wants_save_existing(body):
        return "notion_save"  # a save of existing content: no depth, no language, no topic
    makes = _MAKE.search(body) is not None
    if _NOTES.search(body) and not _NOT_STUDY.search(body) and makes:
        return "notes"
    if _DECK.search(body) and makes:
        return "deck"
    if _SHEET.search(body) and makes:
        return "sheet"
    if _DOCUMENT.search(body) and makes:
        return "document"
    if _RESEARCH.search(body) and (makes or len(_tokens(body)) <= 6):
        return "research"
    if _NOTION.search(body) and _SAVE.search(body):
        return "notion_save"
    return ""


def _saves_to_notion(text: str) -> bool:
    return _NOTION.search(text) is not None and _SAVE.search(text) is not None


def _topic_present(base: str, data: GateInput, answer: str | None) -> bool:
    if answer is not None:
        return _answered(answer) and bool(_topic_tokens(answer) or _tokens(answer))
    if data.has_attachment:
        return True  # the attached file is the subject
    if _topic_tokens(base):
        return True
    if _REFERENCE.search(base) and (data.history_user or data.has_prior_content):
        return True
    return False


def _destination_present(base: str, answer: str | None, default: bool = False) -> bool:
    if default:
        return True
    if answer is not None:
        return _answered(answer)
    return _NOTION_URL.search(base) is not None or _NOTION_NAMED.search(base) is not None


def check(data: GateInput) -> Missing:
    """The required slots that ``data.request`` still lacks (empty: nothing to ask)."""
    request = data.request or ""
    given = _answers(request)
    base = _base(request)
    kind = detect_kind(base)
    if not kind:
        return Missing()
    slots: list[str] = []
    hinglish = _HINGLISH.search(base) is not None
    notion_save_only = kind == "notion_save"
    # "Notion me save karo" right after a document exists saves THAT document: no topic needed.
    needs_topic = not notion_save_only or not (data.has_prior_content or data.has_attachment)
    if (
        notion_save_only
        and needs_topic
        and (_EXISTING_REF.search(base) or _AS_IS.search(base))
        and wants_save_existing(base, has_prior_content=False)
    ):
        # "add this notes in notion" with nothing to refer to: ask WHICH notes, never the topic.
        needs_topic = False
        if not _answered(given.get(SLOT_SOURCE)):
            slots.append(SLOT_SOURCE)
    if needs_topic and not _topic_present(base, data, given.get(SLOT_TOPIC)):
        slots.append(SLOT_TOPIC)
    if kind == "notes" and not _answered(given.get(SLOT_DEPTH)):
        if SLOT_DEPTH not in given and not _DEPTH_GIVEN.search(base):
            slots.append(SLOT_DEPTH)
    if _saves_to_notion(base) and not _destination_present(
        base, given.get(SLOT_DESTINATION), data.has_default_destination
    ):
        slots.append(SLOT_DESTINATION)
    if kind == "notes" and SLOT_TOPIC not in slots:
        topic_text = " ".join([base, given.get(SLOT_TOPIC, "")])
        # The topic may itself name the language ("Java streams"): that is the code language.
        names_language = _NAMED_LANGUAGE.search(topic_text)
        if (
            is_programming_topic(topic_text)
            and not names_language
            and not detect_code_language(topic_text, data.preferences)
            and not _answered(given.get(SLOT_LANGUAGE))
        ):
            slots.append(SLOT_LANGUAGE)
    return Missing(tuple(slots), kind, hinglish)


GUIDANCE: Final = (
    "Required details: for notes, a document, a deck, a sheet, research or a save to Notion, the "
    "topic must come from the user's own words in this conversation (or an attached or open "
    "file). Saved memory and standing preferences may only choose the code language or format "
    "defaults, never the topic: never invent or guess a topic from them. The server asks the user "
    "for a missing topic, depth or Notion page itself; do not write text that says the work is "
    "done. After a task starts, never say it is finished, ready, created or saved (a Notion save "
    "is only real once its write step reports VERIFIED): say it started and where progress is. "
    "Notes, detailed or long content for Notion are never written with notion tools: start the "
    "learning-book job instead, and never say it is too large for Notion. Reply in the language "
    "of the user's clear request; one stray foreign utterance never switches your language: keep "
    "the previous one and say you did not catch that."
)

# ------------------------------------------------------------------------------- the card

_TEXT: Final = {
    "topic": (
        "What should the {noun} be about?",
        "{noun} kis topic par chahiye?",
    ),
    "depth": ("How deep should the notes be?", "Notes kitne detail mein chahiye?"),
    "destination": (
        "Which Notion page or database should it be saved in?",
        "Notion mein kis page ya database mein save karun?",
    ),
    "destination_unconnected": (
        "Notion is not connected. Connect it in Settings > Connections, then tell me where to "
        "save it (a page link works).",
        "Notion connect nahi hai. Settings > Connections mein connect karo, phir page ka link do.",
    ),
    "language": (
        "Which programming language should the code examples use?",
        "Code examples kis programming language mein chahiye?",
    ),
    "source": (
        "Which notes should I save? I found nothing earlier in this chat. Paste the text or "
        "attach the file.",
        "Kaun se notes save karun? Is chat mein pehle kuch nahi mila. Text paste karo ya file "
        "attach karo.",
    ),
    "source_pick": (
        "Which notes should I save?",
        "Kaun se notes save karun?",
    ),
}
_NOUN: Final = {
    "notes": "notes",
    "document": "document",
    "deck": "presentation",
    "sheet": "sheet",
    "research": "research",
    "notion_save": "page",
}
_HEADERS: Final = {
    SLOT_TOPIC: "Topic",
    SLOT_DEPTH: "Depth",
    SLOT_DESTINATION: "Destination",
    SLOT_LANGUAGE: "Language",
    SLOT_SOURCE: "Source",
}


def _line(key: str, hinglish: bool, noun: str = "") -> str:
    english, hindi = _TEXT[key]
    return (hindi if hinglish else english).format(noun=noun.capitalize() if hinglish else noun)


def build_questions(
    missing: Missing,
    *,
    suggested_topic: str = "",
    notion_pages: Sequence[str] = (),
    notion_connected: bool = True,
    source_options: Sequence[tuple[str, str]] = (),
) -> list[dict[str, Any]]:
    """The ``ask_user`` questions (at most 3) for the missing slots, ready for ``clamp_questions``.

    ``source_options`` are ``(label, description)`` pairs of real existing content (the last
    answer, files of this chat) for the "Which notes?" card.

    ``suggested_topic`` is a subject the user really discussed in this conversation (shown as a
    labelled suggestion, never invented); ``notion_pages`` are titles found by a real Notion
    search. With no real candidates the question is a plain text question."""
    noun = _NOUN.get(missing.kind, "document")
    questions: list[dict[str, Any]] = []
    for slot in missing.slots:
        if len(questions) >= 3:
            break
        if slot == SLOT_TOPIC:
            text = _line("topic", missing.hinglish, noun)
            suggestion = " ".join(suggested_topic.split())[:60]
            if suggestion:
                text += f" (earlier in this chat: {suggestion})"
            questions.append({"question": text, "header": _HEADERS[slot], "options": []})
        elif slot == SLOT_DEPTH:
            questions.append(
                {
                    "question": _line("depth", missing.hinglish),
                    "header": _HEADERS[slot],
                    "options": [
                        {"label": "Quick", "description": "About 2 pages: the key points only"},
                        {"label": "Standard", "description": "About 10 pages"},
                        {
                            "label": "Full book",
                            "description": "8 to 12 chapters, chapter by chapter",
                        },
                    ],
                    "allow_text": True,
                }
            )
        elif slot == SLOT_DESTINATION:
            if not notion_connected:
                questions.append(
                    {
                        "question": _line("destination_unconnected", missing.hinglish),
                        "header": _HEADERS[slot],
                        "options": [],
                    }
                )
            else:
                options = [
                    {
                        "label": title[:80],
                        "description": "Suggested"
                        if index == 0
                        else "A page the integration can see",
                    }
                    for index, title in enumerate(
                        t for t in dict.fromkeys(notion_pages) if t.strip()
                    )
                    if index < MAX_PAGE_OPTIONS
                ]
                if len(options) == 1:  # a card needs 2 options; free text stays on too
                    options.append({"label": "A different page", "description": "Paste its link"})
                questions.append(
                    {
                        "question": _line("destination", missing.hinglish),
                        "header": _HEADERS[slot],
                        "options": options,
                        "allow_text": True,
                    }
                )
        elif slot == SLOT_SOURCE:
            options = [
                {"label": label[:80], "description": description[:160]}
                for label, description in source_options[:MAX_PAGE_OPTIONS]
            ]
            if options:
                options.append({"label": "I will paste the text", "description": "Type it below"})
            questions.append(
                {
                    "question": _line("source_pick" if options else "source", missing.hinglish),
                    "header": _HEADERS[slot],
                    "options": options,
                    "allow_text": True,
                }
            )
        elif slot == SLOT_LANGUAGE:
            questions.append(
                {
                    "question": _line("language", missing.hinglish),
                    "header": _HEADERS[slot],
                    "options": [{"label": name} for name in CODE_LANGUAGE_OPTIONS],
                    "allow_text": True,
                }
            )
    return questions


_PAGE_LINE: Final = re.compile(r"^-\s*(.+?)\s*\|\s*id\s+\S+", re.MULTILINE)


def parse_notion_pages(search_text: str) -> list[str]:
    """Page titles from the ``notion_search`` tool's text (``- Title | id ... | url`` lines)."""
    titles = [m.group(1).strip() for m in _PAGE_LINE.finditer(search_text or "")]
    return [t for t in titles if t and t.lower() not in ("untitled", "(untitled)")][:10]


def suggestion_from_history(history_user: Sequence[str]) -> str:
    """A subject the user really mentioned earlier in this conversation (the newest message that
    carries topic words), else ``""``. Never invented."""
    for text in reversed(list(history_user)):
        if detect_kind(_base(text)) and not _topic_tokens(_base(text)):
            continue
        words = _topic_tokens(_base(text))
        if words:
            return " ".join(words[:4])
    return ""


# -------------------------------------------------------------------- never invent a topic


_LANGUAGE_AS_CODE: Final = re.compile(
    r"(?<!\w)(?:in|using|with|use|via)\s+(?:the\s+)?(?:python|java|javascript|typescript|c\+\+|cpp|c#|"
    r"csharp|golang|rust|kotlin|swift|ruby|php|scala|sql|dart)(?!\w)"
    r"|(?<!\w)(?:python|java|javascript|typescript|c\+\+|cpp|c#|csharp|golang|rust|kotlin|swift|"
    r"ruby|php|scala|sql|dart)\s+(?:code|solutions?|examples?|snippets?)(?!\w)",
    re.IGNORECASE,
)


def _stem(token: str) -> str:
    return token[:5] if len(token) > 5 else token


def topic_supported(instruction: str, user_texts: Sequence[str], *, grounded: bool = False) -> bool:
    """False when the topic of a task ``instruction`` did not come from the conversation.

    Lenient on purpose (translations and synonyms must pass): a shared word, compared by a
    5-letter stem, is enough, and so is any topic word in what the user said when nothing is
    shared. It fails when (a) the user's messages carry no topic word at all while the
    instruction has one, or (b) the instruction makes a programming language the SUBJECT
    ("Java Collections") that the user never named. A language named only as the code language
    ("in Java", "Java code examples") is allowed: a saved preference may pick it. ``grounded``:
    an attached file, an open document or a reference ("isi par") supplies the topic."""
    wanted = {_stem(t) for t in _topic_tokens(_base(instruction))}
    if not wanted or grounded:
        return True
    said_text = " ".join(_base(t) for t in user_texts)
    if _REFERENCE.search(said_text):
        return True
    said_topic = {_stem(t) for t in _topic_tokens(said_text)}
    if not said_topic:
        return False
    subject_text = _LANGUAGE_AS_CODE.sub(" ", _base(instruction))
    said_all = said_text.lower()
    for match in _NAMED_LANGUAGE.finditer(subject_text):
        if match.group(0).lower() not in said_all:
            return False
    return True


def backstop_refusal(
    rows: Sequence[tuple[str, str, Mapping[str, Any]]], instruction: str, *, has_files: bool
) -> capabilities.Refusal | None:
    """The task service's second line of defence. ``rows`` are the session's newest messages as
    ``(sender, content, metadata)``, newest first. Returns a ``needs_clarification`` refusal when
    the user's own request lacks its topic or Notion page, or when the instruction's topic was not
    said by the user; None otherwise (also when the latest message is no work request)."""
    ordered = list(reversed(rows))
    users = [
        {"content": content, "answers_to": (meta or {}).get("answers_to")}
        for sender, content, meta in ordered
        if sender == "user"
    ]
    if not users:
        return None
    assistant = [c for sender, c, _meta in ordered if sender != "user"]
    request = backstop_request(users)
    earlier = [str(u["content"]) for u in users[:-1]]
    prior = any(len(c) > 400 for c in assistant)
    data = GateInput(
        request=request, history_user=earlier, has_attachment=has_files, has_prior_content=prior
    )
    missing = check(data)
    if not missing:
        return None
    # "Notion me save karo" saves a document the chat knows about and this view cannot see:
    # only the destination is enforced for it here.
    enforced = (
        (SLOT_DESTINATION,) if missing.kind == "notion_save" else (SLOT_TOPIC, SLOT_DESTINATION)
    )
    wanted = tuple(s for s in missing.slots if s in enforced)
    if not wanted:
        return None
    questions = build_questions(Missing(wanted, missing.kind, missing.hinglish))
    text = " ".join(str(q["question"]) for q in questions)
    return capabilities.Refusal(
        reason="The request is missing details.",
        hint=text,
        code="needs_clarification",
        status="needs_clarification",
        question=text,
        missing=wanted,
    )


def instruction_invents_topic(
    rows: Sequence[tuple[str, str, Mapping[str, Any]]], instruction: str, *, has_files: bool
) -> bool:
    """True when the latest user message is a work request but the instruction's topic is not in
    the conversation (see ``topic_supported``)."""
    ordered = list(reversed(rows))
    users = [c for sender, c, _meta in ordered if sender == "user"]
    if not users or not detect_kind(_base(users[-1])):
        return False
    texts = [c for _sender, c, _meta in ordered]
    return not topic_supported(instruction, texts, grounded=has_files)


def backstop_request(user_texts: Sequence[Mapping[str, Any]]) -> str:
    """The request text for the gate from the newest user messages (oldest first): the newest
    message, and on an answer turn the message that was asked about before it."""
    if not user_texts:
        return ""
    newest = str(user_texts[-1].get("content") or "")
    if user_texts[-1].get("answers_to") and len(user_texts) > 1:
        return f"{user_texts[-2].get('content') or ''}\n{newest}".strip()
    return newest
