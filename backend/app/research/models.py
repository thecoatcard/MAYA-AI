"""Plain data of a deep-research run."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final
from urllib.parse import urlsplit

_TWO_LEVEL: Final = frozenset(
    {"co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "co.in", "co.jp", "com.br", "co.nz", "co.za"}
)


def site_of(url_or_host: str) -> str:
    """The site (a registrable-domain heuristic) of a URL: ``news.bbc.co.uk`` -> ``bbc.co.uk``."""
    host = (urlsplit(url_or_host).hostname if "//" in url_or_host else url_or_host) or ""
    host = host.lower().rstrip(".").removeprefix("www.")
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    tail2 = ".".join(labels[-2:])
    return ".".join(labels[-3:]) if tail2 in _TWO_LEVEL else tail2


@dataclass(slots=True)
class Candidate:
    url: str
    title: str
    snippet: str = ""
    published: str | None = None
    domain: str = ""
    hits: int = 1
    score: float = 0.0


@dataclass(frozen=True, slots=True)
class Source:
    """A page that was read. ``text`` is untrusted data; ``wrapped`` is the same text inside the
    untrusted-data envelope, the only form that may be given to a model."""

    url: str
    final_url: str
    title: str
    domain: str
    published: str | None
    text: str
    wrapped: str
    flagged: bool = False  # the page tried to instruct an AI; its text is data and was not obeyed


@dataclass(frozen=True, slots=True)
class Skip:
    url: str
    domain: str
    code: str  # paywall, login, blocked, robots, rate_limited, empty, not_text, timeout, ...
    detail: str = ""


@dataclass(slots=True)
class Note:
    """What one source says, from the per-source map step. Quotes are verified verbatim."""

    index: int  # the citation number [n]
    facts: list[str] = field(default_factory=list)
    numbers: list[str] = field(default_factory=list)
    quotes: list[str] = field(default_factory=list)
    stance: str = ""
    date: str | None = None


@dataclass(slots=True)
class Read:
    """A read source with its citation number and its notes."""

    index: int
    source: Source
    note: Note
