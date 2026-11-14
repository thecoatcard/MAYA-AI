"""Last-resort document content built deterministically from earlier steps' outputs.

When the model's layout specification for a document is invalid even after normalisation and one
repair retry, the user's good research must not be lost to a formatting problem. These helpers
rebuild a plain, valid structure (title, headings, paragraphs, lists, a Sources list; or a simple
deck) from the outputs of the steps the document depends on. They only reshape text that already
exists: nothing is invented, and a step whose output is marked missing is skipped.
"""

from __future__ import annotations

import re
from typing import Any

from app.automation import model as doc_model

_BULLET = re.compile(r"^\s*(?:[-*•]|\+)\s+(.*)$")
_NUMBERED = re.compile(r"^\s*\d{1,3}[.)]\s+(.*)$")
_HASH_HEADING = re.compile(r"^\s*(#{1,6})\s+(.*)$")
_BOLD_LINE = re.compile(r"^\s*\*\*([^*]{2,120})\*\*:?\s*$")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_MAX_SOURCES = 40
_MAX_SLIDE_BULLETS_PER_SLIDE = 6


def dependency_texts(deps: dict[str, Any] | None) -> list[str]:
    """The text of each completed predecessor, in order (missing or empty outputs skipped)."""
    texts: list[str] = []
    for output in (deps or {}).values():
        if not isinstance(output, dict) or output.get("missing"):
            continue
        text = output.get("content") or output.get("summary")
        if isinstance(text, str) and text.strip():
            texts.append(text.strip())
    return texts


def dependency_sources(deps: dict[str, Any] | None) -> list[tuple[str, str]]:
    """Unique ``(title, url)`` pairs from the predecessors' citations."""
    seen: set[str] = set()
    sources: list[tuple[str, str]] = []
    for output in (deps or {}).values():
        if not isinstance(output, dict):
            continue
        for citation in output.get("citations") or []:
            if not isinstance(citation, dict):
                continue
            url = str(citation.get("url") or "").strip()
            title = str(citation.get("title") or url).strip()
            if not url.lower().startswith(("http://", "https://")) or url in seen:
                continue
            seen.add(url)
            sources.append((title[:150] or url, url))
    return sources[:_MAX_SOURCES]


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _paragraph_chunks(text: str) -> list[str]:
    """Split text that is over the paragraph limit on sentence boundaries."""
    limit = doc_model.MAX_PARAGRAPH_CHARS
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(text):
        if len(current) + len(sentence) + 1 > limit and current:
            chunks.append(current)
            current = ""
        current = f"{current} {sentence}".strip()
        while len(current) > limit:
            chunks.append(current[:limit])
            current = current[limit:]
    if current:
        chunks.append(current)
    return chunks


def blocks_from_text(text: str) -> list[dict[str, Any]]:
    """Markdown-ish text -> blocks (headings, bullets, numbered lists, paragraphs)."""
    blocks: list[dict[str, Any]] = []
    group: list[str] = []

    def flush() -> None:
        if not group:
            return
        lines = [g for g in group if g.strip()]
        group.clear()
        if not lines:
            return
        if all(_BULLET.match(line) for line in lines):
            items = [
                _clip(_BULLET.sub(r"\1", line), doc_model.MAX_LIST_ITEM_CHARS) for line in lines
            ]
            blocks.append({"type": "bullets", "items": items[: doc_model.MAX_LIST_ITEMS]})
        elif all(_NUMBERED.match(line) for line in lines):
            items = [
                _clip(_NUMBERED.sub(r"\1", line), doc_model.MAX_LIST_ITEM_CHARS) for line in lines
            ]
            blocks.append({"type": "numbered", "items": items[: doc_model.MAX_LIST_ITEMS]})
        else:
            for chunk in _paragraph_chunks(" ".join(line.strip() for line in lines)):
                blocks.append({"type": "paragraph", "text": chunk})

    for line in text.splitlines():
        heading = _HASH_HEADING.match(line)
        bold = _BOLD_LINE.match(line)
        if heading or bold:
            flush()
            if heading:
                level = min(len(heading.group(1)), 3)
                label = heading.group(2).strip().strip("*").strip()
            else:
                level, label = 2, (bold.group(1).strip() if bold else "")
            if label:
                blocks.append(
                    {
                        "type": "heading",
                        "level": level,
                        "text": _clip(label, doc_model.MAX_TITLE_CHARS),
                    }
                )
        elif not line.strip():
            flush()
        else:
            if group and (bool(_BULLET.match(line)) != bool(_BULLET.match(group[-1]))):
                flush()  # a list and the paragraph next to it are separate blocks
            group.append(line)
    flush()
    return blocks


def fallback_blocks(deps: dict[str, Any] | None, title: str) -> list[dict[str, Any]]:
    """A plain report from the predecessors' text and citations, or ``[]`` if there is none."""
    texts = dependency_texts(deps)
    if not texts:
        return []
    blocks: list[dict[str, Any]] = [
        {"type": "heading", "level": 1, "text": _clip(title or "Report", doc_model.MAX_TITLE_CHARS)}
    ]
    for text in texts:
        parsed = blocks_from_text(text)
        # The report already has a level-1 title; demote a repeated one so levels do not skip.
        for block in parsed:
            if block["type"] == "heading" and block["level"] == 1:
                block["level"] = 2
        blocks.extend(parsed)
    sources = dependency_sources(deps)
    if sources:
        blocks.append({"type": "heading", "level": 2, "text": "Sources"})
        blocks.append(
            {
                "type": "numbered",
                "items": [_clip(f"{t}: {u}", doc_model.MAX_LIST_ITEM_CHARS) for t, u in sources],
            }
        )
    return blocks[: doc_model.MAX_BLOCKS]


def _slide_bullet(text: str) -> str:
    """One bullet that fits the slide limit: the first sentence, clipped on a word boundary."""
    first = _SENTENCE_END.split(text.strip())[0]
    limit = doc_model.MAX_SLIDE_BULLET_CHARS
    if len(first) <= limit:
        return first
    cut = first[: limit - 1].rsplit(" ", 1)[0]
    return (cut or first[: limit - 1]).rstrip(",;: ") + "…"


def fallback_slides(
    deps: dict[str, Any] | None, title: str, expected: int | None = None
) -> list[dict[str, Any]]:
    """A simple deck (title, content slides, sources) from the predecessors' text."""
    texts = dependency_texts(deps)
    if not texts:
        return []
    sections: list[tuple[str, list[str]]] = []
    current_title = "Overview"
    current: list[str] = []
    for text in texts:
        for block in blocks_from_text(text):
            if block["type"] == "heading":
                if current:
                    sections.append((current_title, current))
                current_title, current = _clip(block["text"], doc_model.MAX_SLIDE_TITLE_CHARS), []
            elif block["type"] in ("bullets", "numbered"):
                current.extend(_slide_bullet(i) for i in block["items"])
            elif block["type"] == "paragraph":
                current.extend(_slide_bullet(s) for s in _SENTENCE_END.split(block["text"]) if s)
    if current:
        sections.append((current_title, current))
    if not sections:
        return []

    sources = dependency_sources(deps)
    wanted = expected if expected and expected >= 3 else None
    body_slots = (wanted - 2) if wanted else None  # title + body slides + closing
    slides: list[dict[str, Any]] = [
        {
            "layout": "cover",
            "title": _clip(title or "Presentation", doc_model.MAX_SLIDE_TITLE_CHARS),
        }
    ]
    body: list[dict[str, Any]] = []
    for section_title, bullets in sections:
        for index in range(0, len(bullets), _MAX_SLIDE_BULLETS_PER_SLIDE):
            chunk = bullets[index : index + _MAX_SLIDE_BULLETS_PER_SLIDE]
            suffix = f" ({index // _MAX_SLIDE_BULLETS_PER_SLIDE + 1})" if index else ""
            body.append(
                {
                    "layout": "bullets",
                    "title": _clip(section_title + suffix, doc_model.MAX_SLIDE_TITLE_CHARS),
                    "bullets": chunk,
                }
            )
    if body_slots is not None:
        if len(body) > body_slots:
            body = body[:body_slots]
        elif body:
            # Too few slides: split the fullest slides in two so the requested count is met.
            while len(body) < body_slots:
                index = max(range(len(body)), key=lambda i: len(body[i]["bullets"]))
                if len(body[index]["bullets"]) < 2:
                    break
                half = len(body[index]["bullets"]) // 2
                moved = body[index]["bullets"][half:]
                body[index]["bullets"] = body[index]["bullets"][:half]
                body.insert(
                    index + 1,
                    {
                        "layout": "bullets",
                        "title": body[index]["title"] + " (cont.)",
                        "bullets": moved,
                    },
                )
    slides.extend(body)
    if sources:
        slides.append(
            {
                "layout": "closing",
                "title": "Sources",
                "bullets": [
                    _clip(f"{t}: {u}", doc_model.MAX_SLIDE_BULLET_CHARS) for t, u in sources
                ][: doc_model.MAX_SLIDE_BULLETS],
            }
        )
    else:
        slides.append({"layout": "closing", "title": "Summary"})
    return slides[: doc_model.MAX_SLIDES]


# --------------------------------------------------------------------------- sources and items
_SOURCES_TITLE = re.compile(r"^\s*(sources?|references?|bibliography|citations?)\b", re.IGNORECASE)
_ITEM_NOUNS = (
    "tips?|points?|ideas?|steps?|reasons?|ways?|items?|facts?|habits?|rules?|lessons?|"
    "mistakes?|benefits?|strategies|tricks?|techniques?|practices|principles|takeaways?|"
    "examples?|questions?|hacks?|myths?|exercises?|recommendations?"
)
_ITEM_COUNT = re.compile(rf"\b(\d{{1,2}})\s+(?:\w+\s+)?(?:{_ITEM_NOUNS})\b", re.IGNORECASE)
_SLIDE_FRAME_LAYOUTS = frozenset({"cover", "title", "closing", "section"})


def sources_prompt_block(deps: dict[str, Any] | None) -> str:
    """The grounded sources of the predecessors as a numbered list for the document prompt."""
    sources = dependency_sources(deps)
    if not sources:
        return ""
    lines = "\n".join(f"[{i}] {t} - {u}" for i, (t, u) in enumerate(sources, start=1))
    return (
        "\nGrounded sources found by the research steps (the ONLY sources you may cite; never "
        "invent a URL). Cite them as [n] where you use them and end the document with a "
        f"'Sources' section that lists them with their URLs:\n{lines}\n"
    )


def _has_sources_blocks(blocks: list[dict[str, Any]]) -> bool:
    return any(
        b.get("type") == "heading" and _SOURCES_TITLE.match(str(b.get("text") or ""))
        for b in blocks
    )


def ensure_sources(
    fields: dict[str, Any], deps: dict[str, Any] | None, expected_slides: int | None = None
) -> bool:
    """Add a Sources block/slide built from the predecessors' citations when the document has
    none. Only grounded URLs are used. Returns True when something was added."""
    sources = dependency_sources(deps)
    if not sources:
        return False
    if fields.get("blocks"):
        blocks = list(fields["blocks"])
        if _has_sources_blocks(blocks) or len(blocks) + 2 > doc_model.MAX_BLOCKS:
            return False
        blocks.append({"type": "heading", "level": 2, "text": "Sources"})
        blocks.append(
            {
                "type": "numbered",
                "items": [
                    _clip(f"{t}: {u}", doc_model.MAX_LIST_ITEM_CHARS)
                    for t, u in sources[: doc_model.MAX_LIST_ITEMS]
                ],
            }
        )
        fields["blocks"] = blocks
        return True
    slides = fields.get("slides")
    if slides:
        slides = list(slides)
        if any(_SOURCES_TITLE.match(str(s.get("title") or "")) for s in slides):
            return False
        if expected_slides and len(slides) >= expected_slides:
            # The exact slide count wins: the sources go into the last slide's speaker notes.
            last = dict(slides[-1])
            listing = "Sources: " + "; ".join(f"{t} - {u}" for t, u in sources)
            last["notes"] = (str(last.get("notes") or "") + "\n" + listing).strip()
            slides[-1] = last
            fields["slides"] = slides
            return True
        if len(slides) + 1 > doc_model.MAX_SLIDES:
            return False
        slides.append(
            {
                "layout": "closing",
                "title": "Sources",
                "bullets": [
                    _clip(f"{t}: {u}", doc_model.MAX_SLIDE_BULLET_CHARS) for t, u in sources
                ][: doc_model.MAX_SLIDE_BULLETS],
            }
        )
        fields["slides"] = slides
        return True
    return False


def requested_item_count(description: str) -> int | None:
    """``5`` for "5 tips for sleep" / "give me 7 key points" (2 to 30), else ``None``."""
    match = _ITEM_COUNT.search(description or "")
    if not match:
        return None
    count = int(match.group(1))
    return count if 2 <= count <= 30 else None


def produced_item_count(file_type: str, fields: dict[str, Any]) -> int:
    """How many items the produced content lists: content slides (or the bullets of one slide)
    for a deck, the longest list or the number of sub-headings for a document."""
    if file_type == "pptx":
        slides = fields.get("slides") or []
        content = [s for s in slides if s.get("layout") not in _SLIDE_FRAME_LAYOUTS]
        most = max((len(s.get("bullets") or []) for s in content), default=0)
        return max(len(content), most)
    blocks = fields.get("blocks") or []
    longest = max(
        (len(b.get("items") or []) for b in blocks if b.get("type") in ("bullets", "numbered")),
        default=0,
    )
    headings = sum(
        1
        for b in blocks
        if b.get("type") == "heading"
        and int(b.get("level") or 1) >= 2
        and not _SOURCES_TITLE.match(str(b.get("text") or ""))
    )
    return max(longest, headings)
