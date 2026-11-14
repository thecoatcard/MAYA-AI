"""Markdown -> the document block model (deterministic, hand-written, no dependency).

Long notes are written by the model as Markdown and turned into blocks here, not as one strict
JSON document (strict JSON truncates for long prose and one bad table row used to fail the whole
call). The parser is built for model output:

* **Lenient**: :func:`parse_markdown` never raises. A construct it does not know stays in the
  document as a paragraph; an unclosed code fence runs to the end of the text.
* **Lossless**: every word of the input ends up in some block, in order. Limits of the block model
  are met by splitting (a long paragraph, a 60-item list, a 300-row table, a 12k-character code
  block become several blocks), never by cutting text. Only Markdown syntax (markers, fences, the
  ``[!NOTE]`` tag, table separator rows, horizontal rules, the language on a fence) is consumed.
* **Always valid**: what it returns passes :func:`app.automation.model.document_from_dict`, so a
  table can no longer fail validation.

Block mapping: ``#`` to ``######`` -> heading (level 1 to 3; deeper levels become 3); paragraphs
keep inline ``**bold**``, ``*italic*``, `` `code` `` and ``[label](url)`` for the renderers; ``-``
``*`` ``+`` and ``1.`` lists -> bullets or numbered (a nested item stays a list item with a leading
``"– "``: the block model has no list nesting); fenced code with its language; GFM pipe tables
with the header row, ragged rows padded or merged into the last cell; ``> [!NOTE]``, ``[!TIP]``,
``[!WARNING]`` (also IMPORTANT, CAUTION, INFO, SUCCESS) -> callout, any other ``>`` quote -> quote;
``<!-- pagebreak -->`` (also ``\\pagebreak``, ``\\newpage``) -> page_break. A horizontal rule has no
block in the model and is dropped.

A response may start with a fenced ``json`` plan (title, design, flags): :func:`split_plan` cuts it
off and returns it as data.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.automation import model as doc_model
from app.automation.long_document import word_count

LIST_ITEM_NEST_PREFIX = "– "  # en dash: a nested list item (the model has one list level)
_PLAN_KEYS = frozenset(
    {"title", "subtitle", "design", "theme", "cover_page", "toc", "chapters", "pages", "language"}
)

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*([^\n]*)$")
_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$")
_HEADING_EMPTY = re.compile(r"^ {0,3}#{1,6}[ \t]*$")
_RULE = re.compile(r"^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")
_UNDERLINE = re.compile(r"^ {0,3}(?:=+|-+)[ \t]*$")
_LIST_ITEM = re.compile(r"^(\s*)([-*+•]|\d{1,4}[.)])[ \t]+(.*)$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(?:\|\s*:?-{1,}:?\s*)*\|?\s*$")
_PAGE_BREAK = re.compile(
    r"^\s*(?:<!--\s*page[ _-]?break\s*-->|\\(?:page|new)page|\[\[\s*page[_ ]break\s*\]\]"
    r"|-{3}\s*page[ _-]?break\s*-{3})\s*$",
    re.IGNORECASE,
)
_IMAGE = re.compile(r"^\s*!\[([^\]\n]*)\]\(([^()\s]+)(?:\s+\"[^\"]*\")?\)\s*$")
_CALLOUT_TAG = re.compile(r"^\[!\s*([A-Za-z]+)\s*\][ \t]*(.*)$", re.DOTALL)
_BOLD_LABEL = re.compile(
    r"^\*\*\s*(note|tip|warning|important|caution|info)\s*:?\s*\*\*\s*:?", re.I
)
_BY = re.compile(r"^(?:—|–|--|-)\s*(\S.{0,198})$")
_LANGUAGE = re.compile(r"^[A-Za-z0-9_+#.-]{0,30}$")

_CALLOUT_KINDS = {
    "note": "info",
    "info": "info",
    "tip": "success",
    "success": "success",
    "hint": "success",
    "warning": "warning",
    "important": "warning",
    "caution": "warning",
    "danger": "warning",
}


@dataclass
class MarkdownParse:
    """The blocks of a Markdown text and what was repaired on the way."""

    blocks: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def words(self) -> int:
        return word_count(self.blocks)


# --------------------------------------------------------------------------- text helpers
def split_text(text: str, limit: int) -> list[str]:
    """``text`` cut into pieces of at most ``limit`` characters at sentence, then word, edges.

    No character is dropped except the whitespace at a cut; a word longer than ``limit`` is cut
    inside the word as a last resort."""
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []
    pieces: list[str] = []
    rest = text
    while len(rest) > limit:
        window = rest[:limit]
        cut = max(window.rfind(". "), window.rfind("! "), window.rfind("? "), window.rfind("\n"))
        if cut < limit // 2:
            cut = window.rfind(" ")
        cut = cut + 1 if cut >= limit // 4 else limit
        pieces.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        pieces.append(rest)
    return [p for p in pieces if p]


def _split_lines(text: str, limit: int) -> list[str]:
    """Code text cut at line edges into chunks of at most ``limit`` characters."""
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.split("\n"):
        pieces = [line[i : i + limit] for i in range(0, len(line), limit)] or [""]
        for piece in pieces:
            if current and size + len(piece) + 1 > limit:
                chunks.append("\n".join(current))
                current, size = [], 0
            current.append(piece)
            size += len(piece) + 1
    if current:
        chunks.append("\n".join(current))
    return [c for c in chunks if c.strip()]


def _clean_heading(text: str) -> str:
    text = text.strip()
    for marker in ("**", "__"):
        if len(text) > 4 and text.startswith(marker) and text.endswith(marker):
            text = text[2:-2].strip()
    return text


NL = "\n"


def _lines(text: str | None) -> list[str]:
    return (text or "").replace("\r\n", NL).replace("\r", NL).split(NL)


def unwrap_markdown_fence(text: str) -> str:
    """The text without a ```markdown fence the model wrapped the WHOLE answer in."""
    lines = _lines(text)
    first = next((i for i, line in enumerate(lines) if line.strip()), None)
    last = next((i for i in range(len(lines) - 1, -1, -1) if lines[i].strip()), None)
    if first is None or last is None or last <= first:
        return text or ""
    opened = _FENCE.match(lines[first])
    if (
        opened is None
        or opened.group(2).strip().lower() not in ("markdown", "md")
        or lines[last].strip() != opened.group(1)
    ):
        return text
    return NL.join(lines[first + 1 : last])


# --------------------------------------------------------------------------- plan fence
def split_plan(text: str) -> tuple[dict[str, Any] | None, str]:
    """``(plan, body)``: a leading fenced ``json`` plan object is cut off and parsed.

    Only a fence at the very start (after blank lines) whose content is a JSON object with at
    least one plan key counts; anything else stays part of the Markdown (a JSON code example is
    code, not a plan). Never raises."""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    first = next((i for i, line in enumerate(lines) if line.strip()), None)
    if first is None:
        return None, text or ""
    opened = _FENCE.match(lines[first])
    if opened is None or opened.group(2).strip().lower() not in ("json", "json5", "jsonc"):
        return None, text
    fence = opened.group(1)
    for end in range(first + 1, len(lines)):
        stripped = lines[end].strip()
        if stripped.startswith(fence[0] * len(fence)) and set(stripped) <= {fence[0]}:
            try:
                plan = json.loads("\n".join(lines[first + 1 : end]))
            except ValueError:
                return None, text
            if isinstance(plan, dict) and _PLAN_KEYS & set(plan):
                return plan, "\n".join(lines[end + 1 :])
            return None, text
    return None, text


# --------------------------------------------------------------------------- the parser
def _is_special(line: str, nxt: str | None) -> bool:
    """Does ``line`` start a block other than a paragraph?"""
    return bool(
        _FENCE.match(line)
        or _HEADING.match(line)
        or _HEADING_EMPTY.match(line)
        or _RULE.match(line)
        or _PAGE_BREAK.match(line)
        or line.lstrip().startswith(">")
        or _LIST_ITEM.match(line.expandtabs(4))
        or _IMAGE.match(line)
        or _table_starts(line, nxt)
    )


def _table_starts(line: str, nxt: str | None) -> bool:
    if "|" not in line or nxt is None:
        return False
    if _TABLE_SEP.match(nxt) and "-" in nxt and ("|" in nxt or "|" in line):
        return bool(line.strip())
    return False


def _split_row(line: str) -> list[str]:
    """Cells of a pipe row: ``\\|`` and pipes inside backticks do not split."""
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|") and not text.endswith("\\|"):
        text = text[:-1]
    cells: list[str] = []
    current: list[str] = []
    in_code = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text) and text[i + 1] == "|":
            current.append("|")
            i += 2
            continue
        if ch == "`":
            in_code = not in_code
        if ch == "|" and not in_code:
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
        i += 1
    cells.append("".join(current).strip())
    return cells


def _table_blocks(
    header: list[str], rows: list[list[str]], notes: list[str]
) -> list[dict[str, Any]]:
    """Table block(s): ragged rows repaired, limits of the model met by splitting."""
    width = max(1, min(len(header), doc_model.MAX_TABLE_COLS))
    repaired = 0

    def fit(cells: list[str]) -> list[str]:
        nonlocal repaired
        if len(cells) > width:
            cells = [*cells[: width - 1], " | ".join(c for c in cells[width - 1 :] if c)]
            repaired += 1
        elif len(cells) < width:
            cells = [*cells, *([""] * (width - len(cells)))]
            repaired += 1
        return cells

    heads = fit(list(header))
    body = [fit(list(row)) for row in rows]
    if repaired:
        notes.append(f"table: {repaired} row(s) with the wrong number of cells were repaired")
    overflow: list[str] = []

    def clip(cell: str) -> str:
        if len(cell) <= doc_model.MAX_CELL_CHARS:
            return cell
        head, *rest = split_text(cell, doc_model.MAX_CELL_CHARS)
        overflow.extend(rest)
        return head

    heads = [clip(c) for c in heads]
    body = [[clip(c) for c in row] for row in body]
    out: list[dict[str, Any]] = []
    size = doc_model.MAX_TABLE_ROWS
    for start in range(0, max(1, len(body)), size):
        out.append({"type": "table", "headers": heads, "rows": body[start : start + size]})
    for text in overflow:
        out.extend(_paragraphs(text))
    return out


def _paragraphs(text: str) -> list[dict[str, Any]]:
    return [
        {"type": "paragraph", "text": piece}
        for piece in split_text(text, doc_model.MAX_PARAGRAPH_CHARS)
    ]


def _list_blocks(kind: str, items: list[str]) -> list[dict[str, Any]]:
    flat: list[str] = []
    for item in items:
        flat.extend(split_text(item, doc_model.MAX_LIST_ITEM_CHARS))
    return [
        {"type": kind, "items": flat[i : i + doc_model.MAX_LIST_ITEMS]}
        for i in range(0, len(flat), doc_model.MAX_LIST_ITEMS)
    ]


def _callout_or_quote(lines: list[str]) -> list[dict[str, Any]]:
    inner = [re.sub(r"^ {0,3}> ?", "", line) for line in lines]
    while inner and not inner[-1].strip():
        inner.pop()
    while inner and not inner[0].strip():
        inner.pop(0)
    if not inner:
        return []
    joined = "\n".join(inner)
    tag = _CALLOUT_TAG.match(joined.strip())
    kind: str | None = None
    text = joined
    if tag and tag.group(1).lower() in _CALLOUT_KINDS:
        kind = _CALLOUT_KINDS[tag.group(1).lower()]
        text = tag.group(2)
    else:
        label = _BOLD_LABEL.match(joined.strip())
        if label:
            kind = _CALLOUT_KINDS[label.group(1).lower()]
    if kind:
        flat = " ".join(text.split())
        if not flat:
            return []
        return [
            {"type": "callout", "kind": kind, "text": piece}
            for piece in split_text(flat, doc_model.MAX_PARAGRAPH_CHARS)
        ]
    by: str | None = None
    body_text = joined
    if len(inner) > 1:
        attribution = _BY.match(inner[-1].strip())
        if attribution:
            by = attribution.group(1).strip()
            body_text = NL.join(inner[:-1])
    quote = " ".join(body_text.split())
    blocks: list[dict[str, Any]] = [
        {"type": "quote", "text": piece}
        for piece in split_text(quote, doc_model.MAX_PARAGRAPH_CHARS)
    ]
    if by and blocks:
        blocks[-1]["by"] = by
    return blocks


def _code_blocks(language: str, body: list[str]) -> list[dict[str, Any]]:
    while body and not body[-1].strip():
        body.pop()
    while body and not body[0].strip():
        body.pop(0)
    if not body:
        return []
    lang = language.strip().split(" ")[0].lower() if language.strip() else ""
    lang = lang.removeprefix("{").removesuffix("}").lstrip(".")
    if not _LANGUAGE.match(lang):
        lang = ""
    return [
        {"type": "code", "language": lang, "text": chunk}
        for chunk in _split_lines("\n".join(body), doc_model.MAX_CODE_CHARS)
    ]


def parse_markdown(text: str) -> MarkdownParse:
    """Markdown -> blocks. Never raises; see the module docstring for the mapping."""
    try:
        return _parse(text or "")
    except Exception:  # noqa: BLE001 - leniency is the contract: a parser bug must not lose text
        paragraphs = [p for p in re.split(r"\n\s*\n", str(text or "")) if p.strip()]
        blocks = [b for p in paragraphs for b in _paragraphs(" ".join(p.split()))]
        return MarkdownParse(blocks, ["markdown could not be parsed; kept as plain paragraphs"])


def _parse(text: str) -> MarkdownParse:
    lines = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = MarkdownParse()
    blocks = out.blocks
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            blocks.extend(_paragraphs(" ".join(s.strip() for s in paragraph if s.strip())))
            paragraph.clear()

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        nxt = lines[i + 1] if i + 1 < n else None
        if not line.strip():
            flush()
            i += 1
            continue
        fence = _FENCE.match(line)
        if fence:
            flush()
            marker, info = fence.group(1), fence.group(2)
            if marker[0] == "`" and "`" in info:
                paragraph.append(line)  # "```code```" on one line is inline code, not a fence
                i += 1
                continue
            body: list[str] = []
            i += 1
            closed = False
            while i < n:
                stripped = lines[i].strip()
                if (
                    stripped.startswith(marker[0] * len(marker))
                    and set(stripped) == {marker[0]}
                    and len(stripped) >= len(marker)
                ):
                    closed = True
                    i += 1
                    break
                body.append(lines[i])
                i += 1
            if not closed:
                out.notes.append("a code block was not closed; it was closed at the end")
            blocks.extend(_code_blocks(info, body))
            continue
        if _PAGE_BREAK.match(line):
            flush()
            blocks.append({"type": "page_break"})
            i += 1
            continue
        heading = _HEADING.match(line)
        if heading:
            flush()
            level = min(3, len(heading.group(1)))
            title = _clean_heading(heading.group(2))
            if title:
                if len(title) <= doc_model.MAX_TITLE_CHARS:
                    blocks.append({"type": "heading", "level": level, "text": title})
                else:
                    blocks.extend(_paragraphs(title))
            i += 1
            continue
        if _HEADING_EMPTY.match(line) or _RULE.match(line) or _UNDERLINE.match(line):
            flush()
            i += 1
            continue
        if line.lstrip().startswith(">"):
            flush()
            quoted: list[str] = []
            while i < n and lines[i].lstrip().startswith(">"):
                quoted.append(lines[i])
                i += 1
            blocks.extend(_callout_or_quote(quoted))
            continue
        if _table_starts(line, nxt):
            flush()
            header = _split_row(line)
            i += 2
            rows: list[list[str]] = []
            while i < n and lines[i].strip() and "|" in lines[i] and not _FENCE.match(lines[i]):
                rows.append(_split_row(lines[i]))
                i += 1
            blocks.extend(_table_blocks(header, rows, out.notes))
            continue
        item = _LIST_ITEM.match(line.expandtabs(4))
        if item:
            flush()
            i = _list(lines, i, blocks)
            continue
        image = _IMAGE.match(line)
        if image:
            flush()
            alt, src = image.group(1).strip(), image.group(2)
            try:
                doc_model.validate_image_ref(src)
            except ValueError:
                blocks.extend(_paragraphs(alt))
            else:
                if alt and len(alt) <= doc_model.MAX_SHORT_CHARS:
                    blocks.append({"type": "image", "src": src, "alt": alt})
                else:
                    blocks.extend(_paragraphs(alt))
            i += 1
            continue
        paragraph.append(line)
        i += 1
        # a paragraph continues until a blank line or the start of another block
        while i < n and lines[i].strip():
            after = lines[i + 1] if i + 1 < n else None
            if _is_special(lines[i], after):
                break
            paragraph.append(lines[i])
            i += 1
        flush()
    flush()
    return out


def _list(lines: list[str], start: int, blocks: list[dict[str, Any]]) -> int:
    """Consume the list that starts at ``start``; returns the index after it."""
    first = _LIST_ITEM.match(lines[start].expandtabs(4))
    assert first is not None  # noqa: S101 - the caller matched this line
    base = len(first.group(1))
    ordered = first.group(2)[0].isdigit()
    items: list[str] = []
    i = start
    n = len(lines)
    while i < n:
        raw = lines[i].expandtabs(4)
        match = _LIST_ITEM.match(raw)
        if match and not _RULE.match(raw):
            indent = len(match.group(1))
            is_ordered = match.group(2)[0].isdigit()
            if indent <= base + 1 and is_ordered != ordered:
                break  # another list type at the top level starts a new block
            body = match.group(3).strip()
            if indent > base + 1:
                body = LIST_ITEM_NEST_PREFIX + body
            items.append(body)
            i += 1
            continue
        if not raw.strip():
            # a blank line continues the list only when the next line is an item of it
            look = i + 1
            while look < n and not lines[look].strip():
                look += 1
            following = _LIST_ITEM.match(lines[look].expandtabs(4)) if look < n else None
            if following and not _RULE.match(lines[look]) and len(following.group(1)) >= base:
                i = look
                continue
            break
        if raw.startswith(" ") and items and not _is_special(lines[i], None):
            items[-1] = f"{items[-1]} {raw.strip()}"  # a wrapped line of the previous item
            i += 1
            continue
        break
    blocks.extend(_list_blocks("numbered" if ordered else "bullets", items))
    return i


# --------------------------------------------------------------------------- chapter helpers
_TAKEAWAY_HEADING = re.compile(r"\b(key\s+takeaways?|takeaways?|summary|chapter\s+summary)\b", re.I)
_EXERCISE_HEADING = re.compile(
    r"\b(exercises?|practice(?:\s+questions?)?|practise|review\s+questions?|quiz)\b", re.I
)


def split_closing_sections(
    blocks: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """``(body, takeaways, exercises)``: trailing "Key takeaways" and "Exercises" sections of a
    chapter are lifted out of the blocks as plain item lists (the assembler places them: the
    takeaways at the end of the chapter, the exercises in one closing section).

    Only a level 2 or 3 heading that is followed by nothing but list blocks is lifted; any other
    shape stays in the chapter untouched, so no text is lost."""
    body = list(blocks)
    takeaways: list[str] = []
    exercises: list[str] = []
    while True:
        starts = [
            i
            for i, b in enumerate(body)
            if b.get("type") == "heading" and int(b.get("level") or 2) >= 2
        ]
        if not starts:
            break
        at = starts[-1]
        tail = body[at + 1 :]
        title = str(body[at].get("text") or "")
        if not tail or any(b.get("type") not in ("bullets", "numbered") for b in tail):
            break
        items = [str(item) for b in tail for item in b.get("items") or []]
        if _EXERCISE_HEADING.search(title) and not exercises:
            exercises = items
        elif _TAKEAWAY_HEADING.search(title) and not takeaways:
            takeaways = items
        else:
            break
        del body[at:]
    return body, takeaways, exercises


__all__ = [
    "MarkdownParse",
    "parse_markdown",
    "split_closing_sections",
    "split_plan",
    "split_text",
    "unwrap_markdown_fence",
]
