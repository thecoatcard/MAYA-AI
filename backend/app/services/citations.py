"""Stable source numbers and the clean "Sources" list of a web-search answer.

The model cites as ``[n]`` and writes no URL. Numbers are global to the reply: a second search
continues after the first one's numbers, and a URL seen twice keeps its number. After the reply,
``SourceBook.block`` builds the list from the real search results only (cited numbers; all when
none is cited). The web client links ``[n]`` to this list (``apps/web/src/lib/citations.ts``) and
shows a ``[n]`` with no entry as plain text. Nothing here invents a link.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Final
from urllib.parse import urlsplit

SOURCES_MARKER: Final = "**Sources**"
_TITLE_MAX: Final = 120
_CITED = re.compile(r"\[(\d{1,3})\]")


def _host(url: str) -> str:
    host = urlsplit(url).hostname or ""
    return host.removeprefix("www.")


def _title(text: str) -> str:
    # One line, no brackets that would end the markdown link text early.
    cleaned = " ".join(text.split()).replace("[", "(").replace("]", ")")
    return cleaned[:_TITLE_MAX] or "Source"


class SourceBook:
    """The sources of one reply, numbered from 1 across every search."""

    def __init__(self) -> None:
        self._by_url: dict[str, int] = {}
        self._entries: dict[int, tuple[str, str]] = {}

    def __bool__(self) -> bool:
        return bool(self._entries)

    def renumber(self, sources: Sequence[Mapping[str, Any]]) -> None:
        """Give a search result's sources reply-wide numbers, in place (``n`` is rewritten)."""
        for source in sources:
            url = str(source.get("url") or "")
            if not url.startswith(("http://", "https://")):
                continue
            number = self._by_url.get(url)
            if number is None:
                number = len(self._by_url) + 1
                self._by_url[url] = number
                self._entries[number] = (url, _title(str(source.get("title") or url)))
            if isinstance(source, dict):
                source["n"] = number

    def block(self, reply_text: str) -> str:
        """The markdown list to append, or ``""`` when there is nothing to list."""
        if not self._entries:
            return ""
        cited = {int(m) for m in _CITED.findall(reply_text)} & set(self._entries)
        numbers = sorted(cited) or sorted(self._entries)
        lines = [
            f"{n}. [{self._entries[n][1]}]({self._entries[n][0]}) · {_host(self._entries[n][0])}"
            for n in numbers
        ]
        return "\n\n" + SOURCES_MARKER + "\n\n" + "\n".join(lines)


CITATION_RULE: Final = (
    "Cite a source only as [n], its number from the numbered sources, right after the claim. "
    "Never write a URL, a link, a host name, quotation marks or extra brackets around a marker, "
    "and do not write a Sources list: MAYA adds it."
)
