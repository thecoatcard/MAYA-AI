"""Markdown <-> Notion blocks, hand written (no dependency), deterministic and pure.

The chat model writes notes as Markdown. :func:`markdown_to_blocks` turns that into the exact Notion
block objects a write sends (so the approval shows the converted blocks), and
:func:`blocks_to_markdown` turns blocks read back from Notion into readable Markdown for
``notion_get_page``. Nothing here calls Notion.

Supported: headings (``####`` and deeper become heading_3), paragraphs, **bold**, *italic*,
~~strike~~, `inline code`, [links](url) as rich_text annotations (no literal markers are left),
bulleted / numbered / to-do lists with nesting, fenced code (language mapped to Notion's enum),
block quotes, callouts (``> [!NOTE]``), dividers, tables, images by https URL, ``<details>``
toggles and ``$$`` equations.

Limits respected: 2000 UTF-16 units per rich_text item, 100 items per block, two nesting levels per
request (deeper list levels are flattened into the second level with indentation text).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, Final

MAX_TEXT_CHARS: Final = 2000
MAX_RICH_TEXT_ITEMS: Final = 100
MAX_TABLE_ROWS: Final = 100
MAX_TABLE_COLUMNS: Final = 50
MAX_NESTING: Final = 2  # levels of list items in one request (top level counts as 1)
_CONTROL: Final = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

# Notion's code-block language enum (API version 2022-06-28).
NOTION_CODE_LANGUAGES: Final = frozenset(
    "abap abc agda arduino assembly bash basic bnf c c# c++ clojure coffeescript coq css dart "
    "dhall "
    "diff docker ebnf elixir elm erlang f# flow fortran gherkin glsl go graphql groovy haskell "
    "html "
    "idris java javascript json julia kotlin latex less lisp livescript llvm lua makefile markdown "
    "markup matlab mathematica mermaid nix objective-c ocaml pascal perl php powershell prolog "
    "protobuf purescript python r racket reason ruby rust sass scala scheme scss shell smalltalk "
    "solidity sql swift toml typescript verilog vhdl webassembly xml yaml".split()
) | {"plain text", "ascii art", "notion formula", "llvm ir", "vb.net", "visual basic"}
_LANGUAGE_ALIASES: Final = {
    "py": "python",
    "python3": "python",
    "js": "javascript",
    "node": "javascript",
    "jsx": "javascript",
    "ts": "typescript",
    "tsx": "typescript",
    "sh": "shell",
    "zsh": "shell",
    "console": "shell",
    "terminal": "shell",
    "cpp": "c++",
    "cc": "c++",
    "cs": "c#",
    "csharp": "c#",
    "fs": "f#",
    "yml": "yaml",
    "text": "plain text",
    "txt": "plain text",
    "plaintext": "plain text",
    "": "plain text",
    "md": "markdown",
    "ps1": "powershell",
    "objc": "objective-c",
    "kt": "kotlin",
    "rb": "ruby",
    "rs": "rust",
    "golang": "go",
    "dockerfile": "docker",
    "vb": "visual basic",
    "htm": "html",
    "jsonc": "json",
    "tex": "latex",
    "proto": "protobuf",
}
CALLOUTS: Final[dict[str, tuple[str, str]]] = {
    "NOTE": ("ℹ️", "blue_background"),
    "TIP": ("\U0001f4a1", "green_background"),
    "IMPORTANT": ("❗", "purple_background"),
    "WARNING": ("⚠️", "yellow_background"),
    "CAUTION": ("\U0001f6d1", "red_background"),
}
_ICON_TO_CALLOUT: Final = {icon: name for name, (icon, _color) in CALLOUTS.items()}


class MarkdownError(ValueError):
    """The Markdown cannot be turned into Notion blocks. ``str(error)`` is a plain sentence."""


def notion_language(raw: str) -> str:
    """The Notion code language for a fence's info string (unknown -> ``plain text``)."""
    first = raw.strip().split()[0].lower() if raw.strip() else ""
    name = _LANGUAGE_ALIASES.get(first, first)
    return name if name in NOTION_CODE_LANGUAGES else "plain text"


# ---------------------------------------------------------------------------------- rich text


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def chunk_text(text: str, limit: int = MAX_TEXT_CHARS) -> list[str]:
    """``text`` cut into pieces of at most ``limit`` UTF-16 units (how Notion counts), never
    inside a character. The pieces joined are exactly ``text``."""
    pieces: list[str] = []
    current: list[str] = []
    size = 0
    for char in text:
        units = 2 if ord(char) > 0xFFFF else 1
        if size + units > limit:
            pieces.append("".join(current))
            current, size = [], 0
        current.append(char)
        size += units
    if current:
        pieces.append("".join(current))
    return pieces


_Span = tuple[str, frozenset[str], str | None]  # (text, annotations, link url)
_ANNOTATION_KEYS: Final = ("bold", "italic", "strikethrough", "code")
_LINK: Final = re.compile(r"\[([^\]\n]*)\]\(\s*(<?[^)\s>]+>?)(?:\s+\"[^\"]*\")?\s*\)")
_PUNCT_ESCAPABLE: Final = set("\\`*_{}[]()#+-.!|~<>$")


def _is_word(char: str) -> bool:
    return char.isalnum()


def _find_close(text: str, start: int, marker: str) -> int:
    """Index of the closing ``marker`` for an opener ending at ``start``, or -1."""
    pos = start
    while True:
        pos = text.find(marker, pos)
        if pos == -1:
            return -1
        if pos > start and text[pos - 1] == "\\":
            pos += 1
            continue
        if pos == start or text[pos - 1].isspace():
            pos += len(marker)
            continue
        if marker in ("*", "_") and text[pos : pos + 2] == marker * 2:
            pos += 2  # a doubled marker belongs to bold, not to this italic
            continue
        if marker == "_" and pos + 1 < len(text) and _is_word(text[pos + 1]):
            pos += 1
            continue
        return pos


def _parse_inline(text: str, ann: frozenset[str], link: str | None) -> list[_Span]:
    spans: list[_Span] = []
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            spans.append(("".join(buffer), ann, link))
            buffer.clear()

    i, n = 0, len(text)
    while i < n:
        char = text[i]
        if char == "\\" and i + 1 < n and text[i + 1] in _PUNCT_ESCAPABLE:
            buffer.append(text[i + 1])
            i += 2
            continue
        if char == "`":
            run = len(text[i:]) - len(text[i:].lstrip("`"))
            close = text.find("`" * run, i + run)
            if close != -1 and text[i + run : close].strip():
                flush()
                spans.append((text[i + run : close].strip(), ann | {"code"}, link))
                i = close + run
                continue
        if char == "[" and link is None:
            match = _LINK.match(text, i)
            if match is not None:
                url = match.group(2).strip("<>")
                if url.lower().startswith(("http://", "https://", "mailto:")):
                    flush()
                    inner = _parse_inline(match.group(1), ann, url)
                    spans.extend(inner or [(url, ann, url)])
                    i = match.end()
                    continue
        for marker, name in (("**", "bold"), ("__", "bold"), ("~~", "strikethrough")):
            if text.startswith(marker, i) and i + 2 < n and not text[i + 2].isspace():
                if marker == "__" and i > 0 and _is_word(text[i - 1]):
                    continue
                close = _find_close(text, i + 2, marker)
                if close != -1:
                    flush()
                    spans.extend(_parse_inline(text[i + 2 : close], ann | {name}, link))
                    i = close + 2
                    break
        else:
            if char in "*_" and not text.startswith(char * 2, i):
                opener_ok = i + 1 < n and not text[i + 1].isspace()
                if char == "_" and i > 0 and _is_word(text[i - 1]):
                    opener_ok = False
                if opener_ok:
                    close = _find_close(text, i + 1, char)
                    if close != -1:
                        flush()
                        spans.extend(_parse_inline(text[i + 1 : close], ann | {"italic"}, link))
                        i = close + 1
                        continue
            buffer.append(char)
            i += 1
            continue
        continue
    flush()
    return spans


def _merge(spans: list[_Span]) -> list[_Span]:
    merged: list[_Span] = []
    for text, ann, link in spans:
        if not text:
            continue
        if merged and merged[-1][1] == ann and merged[-1][2] == link:
            merged[-1] = (merged[-1][0] + text, ann, link)
        else:
            merged.append((text, ann, link))
    return merged


def _item(text: str, ann: frozenset[str], link: str | None) -> dict[str, Any]:
    content: dict[str, Any] = {"content": text}
    if link:
        content["link"] = {"url": link}
    item: dict[str, Any] = {"type": "text", "text": content}
    if ann:
        item["annotations"] = {key: True for key in _ANNOTATION_KEYS if key in ann}
    return item


def rich_text_from_markdown(text: str) -> list[dict[str, Any]]:
    """Notion rich_text for one line (or paragraph) of inline Markdown. Pieces over 2000 UTF-16
    units are split inside the span (a span is never split when it fits)."""
    clean = _CONTROL.sub("", text)
    items: list[dict[str, Any]] = []
    for span_text, ann, link in _merge(_parse_inline(clean, frozenset(), None)):
        items.extend(_item(piece, ann, link) for piece in chunk_text(span_text))
    return items


def plain_rich_text(text: str) -> list[dict[str, Any]]:
    """Rich text for ``text`` with no Markdown interpretation (code, equations, table-less text)."""
    clean = _CONTROL.sub("", text)
    return [_item(piece, frozenset(), None) for piece in chunk_text(clean)]


# ------------------------------------------------------------------------------- block building


def _split_items(items: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    if not items:
        return [[]]
    return [
        items[start : start + MAX_RICH_TEXT_ITEMS]
        for start in range(0, len(items), MAX_RICH_TEXT_ITEMS)
    ]


def _text_blocks(
    kind: str, items: list[dict[str, Any]], extra: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """One block per 100 rich_text items (a block over the limit is split, never truncated)."""
    blocks = []
    for group in _split_items(items):
        body: dict[str, Any] = {"rich_text": group}
        if extra:
            body.update(extra)
        blocks.append({"object": "block", "type": kind, kind: body})
    return blocks


_FENCE: Final = re.compile(r"^(\s*)(`{3,}|~{3,})\s*([^`\s]*)[^`]*$")
_HEADING: Final = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_RULE: Final = re.compile(r"^\s{0,3}([-*_])(?:\s*\1){2,}\s*$")
_LIST: Final = re.compile(r"^(\s*)([-*+]|\d{1,9}[.)])[ ]+(.*)$")
_CHECKBOX: Final = re.compile(r"^\[([ xX])\]\s+(.*)$")
_TABLE_SEP: Final = re.compile(r"^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?\s*$")
_IMAGE: Final = re.compile(r"^\s*!\[([^\]]*)\]\(\s*(\S+?)(?:\s+\"[^\"]*\")?\s*\)\s*$")
_CALLOUT: Final = re.compile(r"^\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*(.*)$", re.IGNORECASE)
_TOC: Final = re.compile(r"^\s*\[(?:toc|table of contents)\]\s*$", re.IGNORECASE)
_DETAILS_OPEN: Final = re.compile(r"^\s*<details[^>]*>\s*(.*)$", re.IGNORECASE)
_DETAILS_CLOSE: Final = re.compile(r"</details>\s*$", re.IGNORECASE)
_SUMMARY: Final = re.compile(r"<summary[^>]*>(.*?)</summary>", re.IGNORECASE | re.DOTALL)


def _is_block_start(line: str, nxt: str | None) -> bool:
    if not line.strip():
        return True
    return bool(
        _FENCE.match(line)
        or _HEADING.match(line)
        or _RULE.match(line)
        or _LIST.match(line)
        or line.lstrip().startswith((">", "$$"))
        or _DETAILS_OPEN.match(line)
        or _IMAGE.match(line)
        or _TOC.match(line)
        or (nxt is not None and "|" in line and _TABLE_SEP.match(nxt) and "|" in nxt)
    )


def _split_row(line: str) -> list[str]:
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


def _table_blocks(rows: list[list[str]]) -> list[dict[str, Any]]:
    width = max(len(r) for r in rows)
    if width > MAX_TABLE_COLUMNS:
        raise MarkdownError(f"A table has {width} columns; Notion tables allow at most 50.")
    header, body = rows[0], rows[1:]
    header = header + [""] * (width - len(header))
    blocks: list[dict[str, Any]] = []
    step = MAX_TABLE_ROWS - 1
    groups = [body[s : s + step] for s in range(0, len(body), step)] or [[]]
    for group in groups:
        children = []
        for row in [header, *group]:
            cells = (row + [""] * (width - len(row)))[:width]
            children.append(
                {
                    "object": "block",
                    "type": "table_row",
                    "table_row": {"cells": [rich_text_from_markdown(c) for c in cells]},
                }
            )
        blocks.append(
            {
                "object": "block",
                "type": "table",
                "table": {
                    "table_width": width,
                    "has_column_header": True,
                    "has_row_header": False,
                    "children": children,
                },
            }
        )
    return blocks


class _Item:
    __slots__ = ("checked", "children", "indent", "kind", "text")

    def __init__(self, indent: int, kind: str, text: str, checked: bool | None = None) -> None:
        self.indent = indent
        self.kind = kind
        self.text = text
        self.checked = checked
        self.children: list[Any] = []  # _Item or an already built block (code under a step)


def _item_blocks(item: _Item, depth: int, indent_text: str = "") -> list[dict[str, Any]]:
    """The block(s) for one list item (``depth`` 1 is the top level). The top level keeps its
    children nested; items below the second level are flattened into it as siblings with
    indentation text, so nothing is lost and no request nests deeper than two levels."""
    extra = {"checked": bool(item.checked)} if item.kind == "to_do" else None
    blocks = _text_blocks(item.kind, rich_text_from_markdown(indent_text + item.text), extra)
    first = blocks[0]
    if depth < MAX_NESTING:
        nested: list[dict[str, Any]] = []
        for child in item.children:
            nested.extend(_item_blocks(child, depth + 1) if isinstance(child, _Item) else [child])
        if nested:
            first[item.kind]["children"] = nested
        return blocks
    siblings: list[dict[str, Any]] = []
    for child in item.children:
        if isinstance(child, _Item):
            siblings.extend(_item_blocks(child, depth, indent_text + " "))
        else:
            siblings.append(child)
    return [*blocks, *siblings]


def _code_blocks(code: str, language: str) -> list[dict[str, Any]]:
    items = plain_rich_text(code)
    return _text_blocks("code", items, {"language": language})


def markdown_to_blocks(markdown: str) -> list[dict[str, Any]]:
    """The Notion block objects for ``markdown`` (empty input gives no blocks)."""
    text = _CONTROL.sub("", markdown.replace("\r\n", "\n").replace("\r", "\n")).replace(
        "\t", "    "
    )
    return _parse_lines(text.split("\n"), 0)


def _parse_lines(lines: list[str], depth: int) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        nxt = lines[i + 1] if i + 1 < n else None
        if not line.strip():
            i += 1
            continue
        fence = _FENCE.match(line)
        if fence:
            indent, marker, info = len(fence.group(1)), fence.group(2), fence.group(3)
            body: list[str] = []
            i += 1
            while i < n and not re.match(
                rf"^\s*{re.escape(marker[0])}{{{len(marker)},}}\s*$", lines[i]
            ):
                body.append(lines[i][indent:] if lines[i][:indent].strip() == "" else lines[i])
                i += 1
            i += 1  # the closing fence (an unclosed fence runs to the end)
            blocks.extend(_code_blocks("\n".join(body), notion_language(info)))
            continue
        stripped = line.strip()
        if stripped.startswith("$$"):
            expr = stripped[2:]
            if expr.endswith("$$") and len(expr) >= 2:
                expr = expr[:-2]
                i += 1
            else:
                parts = [expr] if expr.strip() else []
                i += 1
                while i < n and not lines[i].strip().endswith("$$"):
                    parts.append(lines[i])
                    i += 1
                if i < n:
                    tail = lines[i].strip()[:-2]
                    if tail.strip():
                        parts.append(tail)
                i += 1
                expr = "\n".join(parts)
            if expr.strip():
                blocks.append(
                    {
                        "object": "block",
                        "type": "equation",
                        "equation": {"expression": expr.strip()[:1000]},
                    }
                )
            continue
        if depth == 0 and _TOC.match(line):
            # A ``[toc]`` line becomes Notion's table-of-contents block (a page-level block).
            blocks.append(
                {
                    "object": "block",
                    "type": "table_of_contents",
                    "table_of_contents": {"color": "default"},
                }
            )
            i += 1
            continue
        details = _DETAILS_OPEN.match(line)
        if details:
            inner: list[str] = []
            rest = details.group(1)
            level = 1
            if rest.strip():
                inner.append(rest)
            i += 1
            while i < n:
                if _DETAILS_OPEN.match(lines[i]):
                    level += 1
                if _DETAILS_CLOSE.search(lines[i]):
                    level -= 1
                    if level == 0:
                        before = _DETAILS_CLOSE.sub("", lines[i])
                        if before.strip():
                            inner.append(before)
                        i += 1
                        break
                inner.append(lines[i])
                i += 1
            joined = "\n".join(inner)
            summary = _SUMMARY.search(joined)
            title = summary.group(1).strip() if summary else "Details"
            if summary:
                joined = joined[: summary.start()] + joined[summary.end() :]
            toggle = _text_blocks("toggle", rich_text_from_markdown(title or "Details"))[0]
            kids = _parse_lines(joined.split("\n"), depth + 1) if depth < 1 else []
            if depth >= 1 and joined.strip():
                kids = _text_blocks("paragraph", rich_text_from_markdown(" ".join(joined.split())))
            if kids:
                toggle["toggle"]["children"] = kids[:100]
            blocks.append(toggle)
            continue
        heading = _HEADING.match(line)
        if heading:
            level = min(len(heading.group(1)), 3)
            blocks.extend(
                _text_blocks(f"heading_{level}", rich_text_from_markdown(heading.group(2)))
            )
            i += 1
            continue
        if _RULE.match(line):
            blocks.append({"object": "block", "type": "divider", "divider": {}})
            i += 1
            continue
        image = _IMAGE.match(line)
        if image:
            url = image.group(2)
            if url.lower().startswith("https://") and utf16_len(url) <= 2000:
                body_img: dict[str, Any] = {"type": "external", "external": {"url": url}}
                if image.group(1).strip():
                    body_img["caption"] = plain_rich_text(image.group(1).strip())
                blocks.append({"object": "block", "type": "image", "image": body_img})
            else:
                label = image.group(1).strip() or "image"
                blocks.extend(_text_blocks("paragraph", plain_rich_text(f"{label} ({url})")))
            i += 1
            continue
        if nxt is not None and "|" in line and "|" in nxt and _TABLE_SEP.match(nxt):
            rows = [_split_row(line)]
            i += 2
            while i < n and lines[i].strip() and "|" in lines[i]:
                rows.append(_split_row(lines[i]))
                i += 1
            blocks.extend(_table_blocks(rows))
            continue
        if stripped.startswith(">"):
            quote: list[str] = []
            while i < n and lines[i].strip().startswith(">"):
                quote.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            blocks.extend(_quote_blocks(quote))
            continue
        if _LIST.match(line):
            i, items = _collect_list(lines, i)
            for top in items:
                blocks.extend(_item_blocks(top, 1))
            continue
        para = [stripped]
        i += 1
        while i < n:
            after = lines[i + 1] if i + 1 < n else None
            if _is_block_start(lines[i], after):
                break
            para.append(lines[i].strip())
            i += 1
        joined_text = ""
        for index, piece in enumerate(para):
            if index:
                joined_text += "\n" if para[index - 1].endswith(("  ", "\\")) else " "
            joined_text += piece.rstrip("\\")
        blocks.extend(_text_blocks("paragraph", rich_text_from_markdown(joined_text)))
    return blocks


def _quote_blocks(lines: list[str]) -> list[dict[str, Any]]:
    first = _CALLOUT.match(lines[0].strip()) if lines else None
    if first:
        icon, color = CALLOUTS[first.group(1).upper()]
        body_lines = ([first.group(2)] if first.group(2).strip() else []) + [
            line for line in lines[1:]
        ]
        text = "\n".join(line.strip() for line in body_lines).strip("\n")
        blocks = _text_blocks(
            "callout",
            rich_text_from_markdown(text),
            {"icon": {"type": "emoji", "emoji": icon}, "color": color},
        )
        return blocks
    paragraphs: list[str] = []
    current: list[str] = []
    for line in [*lines, ""]:
        if line.strip():
            current.append(line.strip())
        elif current:
            paragraphs.append("\n".join(current))
            current = []
    return _text_blocks("quote", rich_text_from_markdown("\n\n".join(paragraphs)))


def _collect_list(lines: list[str], start: int) -> tuple[int, list[_Item]]:
    """The items of one list (nested by indentation) starting at ``start``; returns the index of
    the first line after the list."""
    items: list[_Item] = []
    stack: list[_Item] = []
    i, n = start, len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            # A blank line continues the list only when an indented list item follows.
            j = i
            while j < n and not lines[j].strip():
                j += 1
            if j < n and _LIST.match(lines[j]) and stack:
                i = j
                continue
            break
        match = _LIST.match(line)
        if match:
            indent = len(match.group(1))
            marker, body = match.group(2), match.group(3)
            kind = "numbered_list_item" if marker[0].isdigit() else "bulleted_list_item"
            checked: bool | None = None
            box = _CHECKBOX.match(body)
            if box:
                kind, checked, body = "to_do", box.group(1).lower() == "x", box.group(2)
            while stack and stack[-1].indent >= indent:
                stack.pop()
            item = _Item(indent, kind, body.strip(" "), checked)
            if stack:
                stack[-1].children.append(item)
            else:
                items.append(item)
            stack.append(item)
            i += 1
            continue
        fence = _FENCE.match(line)
        if fence and stack and len(fence.group(1)) > stack[0].indent - 1 + 0:
            marker, info, indent = fence.group(2), fence.group(3), len(fence.group(1))
            owner = next((s for s in reversed(stack) if s.indent < indent), stack[-1])
            body_lines: list[str] = []
            i += 1
            while i < n and not re.match(
                rf"^\s*{re.escape(marker[0])}{{{len(marker)},}}\s*$", lines[i]
            ):
                body_lines.append(lines[i][indent:] if not lines[i][:indent].strip() else lines[i])
                i += 1
            i += 1
            owner.children.extend(_code_blocks("\n".join(body_lines), notion_language(info)))
            continue
        if line.startswith((" ", " ")) and stack:
            stack[-1].text += " " + line.strip()  # a wrapped line of the same item
            i += 1
            continue
        break
    return i, items


# ------------------------------------------------------------------------- blocks -> Markdown

_LIST_KINDS: Final = frozenset({"bulleted_list_item", "numbered_list_item", "to_do"})
_TEXT_KINDS: Final = (
    "paragraph heading_1 heading_2 heading_3 bulleted_list_item numbered_list_item to_do quote "
    "callout toggle code"
).split()


def _rich_to_markdown(parts: Any) -> str:
    if not isinstance(parts, list):
        return ""
    out: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        text = str(part.get("plain_text") or (part.get("text") or {}).get("content") or "")
        if not text:
            continue
        ann = part.get("annotations") or {}
        if isinstance(ann, dict):
            if ann.get("code"):
                text = f"`{text}`"
            if ann.get("bold"):
                text = f"**{text}**"
            if ann.get("italic"):
                text = f"*{text}*"
            if ann.get("strikethrough"):
                text = f"~~{text}~~"
        href = part.get("href") or ((part.get("text") or {}).get("link") or {}).get("url")
        if href:
            text = f"[{text}]({href})"
        out.append(text)
    return "".join(out)


def _plain_of(parts: Any) -> str:
    if not isinstance(parts, list):
        return ""
    return "".join(
        str(p.get("plain_text") or (p.get("text") or {}).get("content") or "")
        for p in parts
        if isinstance(p, dict)
    )


def blocks_to_markdown(
    blocks: list[dict[str, Any]],
    children_of: Callable[[dict[str, Any]], list[dict[str, Any]]] | None = None,
    *,
    indent: int = 0,
) -> list[str]:
    """Readable Markdown lines for Notion blocks. ``children_of(block)`` returns the already read
    children of a block (``None`` when they were not read); unreadable types are named, not
    dropped silently."""
    lines: list[str] = []
    pad = "  " * indent
    number = 0
    previous_was_list = False
    for block in blocks:
        kind = str(block.get("type") or "")
        data = block.get(kind) if isinstance(block.get(kind), dict) else {}
        assert isinstance(data, dict)
        kids = children_of(block) if children_of is not None else []
        if previous_was_list and kind not in _LIST_KINDS and lines and lines[-1] != "":
            lines.append("")
        previous_was_list = kind in _LIST_KINDS
        number = number + 1 if kind == "numbered_list_item" else 0
        text = _rich_to_markdown(data.get("rich_text"))
        if kind == "paragraph":
            lines.append(pad + text if text else "")
        elif kind.startswith("heading_"):
            lines.append(f"{'#' * int(kind[-1])} {text}")
        elif kind == "bulleted_list_item":
            lines.append(f"{pad}- {text}")
        elif kind == "numbered_list_item":
            lines.append(f"{pad}{number}. {text}")
        elif kind == "to_do":
            lines.append(f"{pad}- [{'x' if data.get('checked') else ' '}] {text}")
        elif kind == "code":
            language = str(data.get("language") or "plain text")
            code = _plain_of(data.get("rich_text"))
            body = [pad + row if row else row for row in code.split(chr(10))]
            fence = f"{pad}```{'' if language == 'plain text' else language}"
            lines.extend([fence, *body, f"{pad}```"])
        elif kind == "quote":
            lines.extend(f"> {row}" for row in (text or "").split("\n"))
        elif kind == "callout":
            icon = (
                (data.get("icon") or {}).get("emoji")
                if isinstance(data.get("icon"), dict)
                else None
            )
            name = _ICON_TO_CALLOUT.get(str(icon or ""))
            rows = (text or "").split("\n")
            head = f"> [!{name}] {rows[0]}" if name else f"> {icon + ' ' if icon else ''}{rows[0]}"
            lines.append(head)
            lines.extend(f"> {row}" for row in rows[1:])
        elif kind == "divider":
            lines.append("---")
        elif kind == "equation":
            lines.append(f"$$ {data.get('expression', '')} $$")
        elif kind == "image":
            file = data.get("external") or data.get("file") or {}
            url = file.get("url", "") if isinstance(file, dict) else ""
            lines.append(f"![{_plain_of(data.get('caption'))}]({url})")
        elif kind == "toggle":
            lines.append(f"<details><summary>{text}</summary>")
            lines.extend(blocks_to_markdown(kids, children_of))
            lines.append("</details>")
            kids = []
        elif kind == "table":
            rows_md = []
            for row in kids:
                cells = (row.get("table_row") or {}).get("cells") or []
                rows_md.append(
                    "| "
                    + " | ".join(_rich_to_markdown(c).replace("|", "\\|") for c in cells)
                    + " |"
                )
            if rows_md:
                width = int(data.get("table_width") or 1)
                lines.append(rows_md[0])
                lines.append("|" + " --- |" * width)
                lines.extend(rows_md[1:])
            kids = []
        elif kind == "child_page":
            lines.append(f"[sub-page: {data.get('title') or 'untitled'}]")
        elif kind == "child_database":
            lines.append(f"[sub-database: {data.get('title') or 'untitled'}]")
        elif kind in ("bookmark", "embed", "link_preview", "video", "file", "pdf"):
            lines.append(f"[{kind}: {data.get('url') or ''}]")
        elif kind == "link_to_page":
            lines.append("[link to another page]")
        elif kind in ("table_row", "column_list", "column", "synced_block"):
            lines.extend(blocks_to_markdown(kids, children_of, indent=indent))
            kids = []
        else:
            lines.append(f"[{kind or 'unknown'} block not shown]")
        if kids and kind in (
            "bulleted_list_item",
            "numbered_list_item",
            "to_do",
            "paragraph",
            "quote",
            "callout",
        ):
            lines.extend(blocks_to_markdown(kids, children_of, indent=indent + 1))
        if kind not in _LIST_KINDS:
            lines.append("")
    return lines


# ------------------------------------------------------------------- leftover Markdown check

_LEFTOVER: Final = (
    (re.compile(r"\*\*\S"), "'**'"),
    (re.compile(r"(?m)^\s{0,3}#{1,6}\s+\S"), "a '#' heading marker"),
    (re.compile(r"`{3}"), "a code fence"),
    (re.compile(r"(?m)^\s*\|?\s*:?-{3,}:?\s*\|"), "a table separator row"),
    (re.compile(r"(?m)^\s*-{3,}\s*$"), "a '---' rule"),
    (re.compile(r"~~\S"), "'~~'"),
)
_SKIP_LEFTOVER: Final = frozenset({"code", "equation", "table_row", "child_page"})


def leftover_markdown(blocks: list[dict[str, Any]]) -> list[str]:
    """Raw Markdown tokens still visible in the text of ``blocks`` (read back from Notion), as
    short plain findings. Code and equation blocks are skipped: they may contain such text."""
    findings: list[str] = []
    for number, block in enumerate(blocks, start=1):
        kind = str(block.get("type") or "")
        if kind in _SKIP_LEFTOVER:
            continue
        data = block.get(kind)
        text = _plain_of(data.get("rich_text")) if isinstance(data, dict) else ""
        for pattern, name in _LEFTOVER:
            if pattern.search(text):
                findings.append(f"block {number} ({kind}) still shows {name} as raw text")
                break
    return findings


__all__ = [
    "CALLOUTS",
    "MAX_NESTING",
    "NOTION_CODE_LANGUAGES",
    "MarkdownError",
    "blocks_to_markdown",
    "chunk_text",
    "leftover_markdown",
    "markdown_to_blocks",
    "notion_language",
    "plain_rich_text",
    "rich_text_from_markdown",
    "utf16_len",
]
