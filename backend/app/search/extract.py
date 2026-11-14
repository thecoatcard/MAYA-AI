"""Main-text extraction from an HTML page, with ``lxml`` (already a dependency).

A small readability heuristic instead of an extra library: drop scripts, navigation and other
page furniture, pick the container whose paragraphs carry the most text (preferring ``<article>``
and ``<main>``), and return its headings, paragraphs and list items. Also reads the title and the
publication date from the usual meta tags, JSON-LD and ``<time>``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, cast

from lxml import html as lxml_html
from lxml.html import HtmlElement

_DROP_TAGS: Final = (
    "script",
    "style",
    "noscript",
    "template",
    "nav",
    "header",
    "footer",
    "aside",
    "form",
    "iframe",
    "svg",
    "button",
    "select",
    "dialog",
)
_FURNITURE = re.compile(
    r"(comment|sidebar|footer|nav|menu|cookie|consent|banner|advert|promo|share|social|related|"
    r"newsletter|popup|modal|subscribe|breadcrumb|pagination|sponsor)",
    re.IGNORECASE,
)
_TEXT_TAGS: Final = ("h1", "h2", "h3", "h4", "p", "li", "blockquote", "pre")
_DATE_META: Final = (
    "article:published_time",
    "og:article:published_time",
    "og:published_time",
    "datepublished",
    "pubdate",
    "publishdate",
    "date",
    "dc.date",
    "dc.date.issued",
    "parsely-pub-date",
    "sailthru.date",
)
_JSON_DATE = re.compile(r'"datePublished"\s*:\s*"([^"]{8,40})"')
_MIN_PARAGRAPH: Final = 40


@dataclass(frozen=True, slots=True)
class ExtractedPage:
    title: str
    published: str | None
    text: str


def _squash(value: str) -> str:
    return " ".join(value.split())


def _valid_date(raw: str | None) -> str | None:
    """The date as given when it parses (ISO 8601 or an RFC 2822-like form), else ``None``."""
    if not raw:
        return None
    text = raw.strip()
    candidate = text.replace("Z", "+00:00") if text.endswith("Z") else text
    try:
        datetime.fromisoformat(candidate)
    except ValueError:
        try:
            from email.utils import parsedate_to_datetime

            parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
    return text[:40]


def _meta(root: Any, names: tuple[str, ...]) -> str | None:
    for element in root.iter("meta"):
        key = (
            element.get("property") or element.get("name") or element.get("itemprop") or ""
        ).lower()
        if key in names and element.get("content"):
            return str(element.get("content"))
    return None


def _date(root: Any, raw_html: str) -> str | None:
    found = _valid_date(_meta(root, _DATE_META))
    if found:
        return found
    match = _JSON_DATE.search(raw_html)
    if match and (found := _valid_date(match.group(1))):
        return found
    for element in root.iter("time"):
        if found := _valid_date(element.get("datetime")):
            return found
    for script in root.iter("script"):
        if script.get("type") == "application/ld+json" and script.text:
            try:
                data = json.loads(script.text)
            except ValueError:
                continue
            items = data if isinstance(data, list) else [data]
            for item in items:
                if isinstance(item, dict) and (found := _valid_date(item.get("datePublished"))):
                    return found
    return None


def _title(root: Any) -> str:
    og = _meta(root, ("og:title", "twitter:title"))
    if og and og.strip():
        return _squash(og)[:200]
    for element in root.iter("title"):
        if element.text and element.text.strip():
            return _squash(element.text)[:200]
    for element in root.iter("h1"):
        text = _squash(element.text_content())
        if text:
            return text[:200]
    return ""


def _is_furniture(element: Any) -> bool:
    marker = f"{element.get('class') or ''} {element.get('id') or ''}"
    return bool(marker.strip()) and _FURNITURE.search(marker) is not None


def _score(container: Any) -> float:
    """Characters of real paragraphs, minus the characters that sit inside links."""
    total = 0.0
    for paragraph in container.iter("p"):
        text = _squash(paragraph.text_content())
        if len(text) < _MIN_PARAGRAPH:
            continue
        link_chars = sum(len(_squash(a.text_content())) for a in paragraph.iter("a"))
        total += max(0.0, len(text) - 1.5 * link_chars)
    return total


def _best_container(body: Any) -> Any:
    for tag in ("article", "main"):
        candidates = [e for e in body.iter(tag) if _score(e) >= 200]
        if candidates:
            return max(candidates, key=_score)
    for element in body.iter("div", "section"):
        if element.get("role") == "main" and _score(element) >= 200:
            return element
    best, best_score = body, _score(body)
    for element in body.iter("div", "section", "td"):
        score = _score(element)
        if score > best_score:
            best, best_score = element, score
    # A parent holds all of its child's paragraphs: descend while one child holds nearly all.
    while True:
        children = [c for c in best.iterchildren("div", "section") if _score(c) >= 0.9 * best_score]
        if not children:
            return best
        best = max(children, key=_score)
        best_score = _score(best)


def extract_page(raw_html: str, *, max_chars: int = 20_000) -> ExtractedPage:
    """The title, publication date and main text of ``raw_html``. Empty text when none is found."""
    try:
        root = cast(HtmlElement, lxml_html.fromstring(raw_html))
    except (ValueError, lxml_html.etree.ParserError):
        return ExtractedPage("", None, "")
    title = _title(root)
    published = _date(root, raw_html)
    for tag in _DROP_TAGS:
        for element in [cast(HtmlElement, e) for e in root.iter(tag)]:
            element.drop_tree()
    for element in [cast(HtmlElement, e) for e in root.iter()]:
        if element is not root and isinstance(element.tag, str) and _is_furniture(element):
            if element.getparent() is not None:
                element.drop_tree()
    bodies = list(root.iter("body"))
    body = bodies[0] if bodies else root
    container = _best_container(body)
    lines: list[str] = []
    previous = ""
    for element in container.iter(*_TEXT_TAGS):
        if element.tag in ("li", "blockquote") and element.xpath(".//p"):
            continue  # its paragraphs are read on their own
        text = _squash(element.text_content())
        if not text or text == previous:
            continue
        if element.tag == "li" and len(text) < 25:
            continue  # menu-like entries
        if element.tag != "li" and element.tag not in ("h1", "h2", "h3", "h4") and len(text) < 20:
            continue
        previous = text
        lines.append(text)
    text = "\n\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0]
    return ExtractedPage(title=title, published=published, text=text)


_PDF_MAX_PAGES: Final = 60


def extract_pdf(body: bytes, *, max_chars: int) -> ExtractedPage:
    """The text of a PDF (first pages only) with ``pypdf``; empty text when it cannot be read
    (scanned or encrypted PDFs have no text layer: they are reported as unreadable, never OCRed)."""
    import io

    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(body))
        if reader.is_encrypted:
            return ExtractedPage("", None, "")
        parts: list[str] = []
        size = 0
        for page in reader.pages[:_PDF_MAX_PAGES]:
            text = _squash(page.extract_text() or "")
            if text:
                parts.append(text)
                size += len(text)
            if size >= max_chars:
                break
        meta = reader.metadata
        title = _squash(str(meta.title)) if meta is not None and meta.title else ""
        created = _valid_date(str(meta.get("/CreationDate", "")) if meta is not None else None)
    except Exception:  # noqa: BLE001 - a broken PDF is "no text", never an error
        return ExtractedPage("", None, "")
    return ExtractedPage(title[:300], created, "\n\n".join(parts)[:max_chars])
