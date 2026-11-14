"""Query clean-up and intent signals for the specialised free providers.

A search query is a few keywords. A model sometimes pastes the whole user request, including
formatting instructions ("Provide a day-wise breakdown including..."), which gets a search engine
to block or to answer nothing. ``clean_query`` keeps the first sentence, drops instruction tails
and caps the length.
"""

from __future__ import annotations

import re
from typing import Final

MAX_QUERY_CHARS: Final = 200

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")
_INSTRUCTION = re.compile(
    r"\b(?:please\s+)?(?:provide|include|including|give me|make sure|ensure|format|list each|"
    r"show me|present|summari[sz]e|explain)\b",
    re.IGNORECASE,
)
_LEADING_POLITE = re.compile(r"^(?:please|kindly|can you|could you)\s+", re.IGNORECASE)

_HN = re.compile(r"\b(?:hacker\s?news|show hn|ask hn|news\.ycombinator)\b", re.IGNORECASE)
_ARXIV = re.compile(
    r"\barxiv\b|\bpreprints?\b|\bresearch papers?\b|\bscientific papers?\b|"
    r"\bpapers? (?:on|about)\b",
    re.IGNORECASE,
)
_STACK = re.compile(
    r"\bstack\s?overflow\b|\bstack\s?exchange\b|\btraceback\b|\bexception\b|\bsegfault\b|"
    r"\b(?:syntax|type|value|key|attribute|import|runtime)error\b|"
    r"\bhow (?:do i|to|can i) .{0,60}\b(?:in|with|using) "
    r"(?:python|javascript|typescript|java|c\+\+|c#|rust|go|golang|sql|bash|react|node|pandas|"
    r"django|flask|fastapi|docker|git|css|html|kotlin|swift|php|ruby)\b",
    re.IGNORECASE,
)


def clean_query(query: str, *, limit: int = MAX_QUERY_CHARS) -> str:
    """A short keyword query: whitespace collapsed, the first sentence only, an instruction tail
    ("Provide a breakdown including...") removed, at most ``limit`` characters (on a word end)."""
    text = " ".join(query.split())
    if not text:
        return text
    text = _LEADING_POLITE.sub("", text)
    first = _SENTENCE_END.split(text, maxsplit=1)[0]
    if len(first.split()) >= 3:
        text = first
    match = _INSTRUCTION.search(text)
    if match is not None and len(text[: match.start()].split()) >= 2:
        text = text[: match.start()]
    text = text.strip(" ,;:-")
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] if " " in text[:limit] else text[:limit]
    return text.strip() or " ".join(query.split())[:limit]


def wants_hacker_news(query: str) -> bool:
    return _HN.search(query) is not None


def wants_arxiv(query: str) -> bool:
    return _ARXIV.search(query) is not None


def wants_stackexchange(query: str) -> bool:
    return _STACK.search(query) is not None
