"""Save EXISTING content to Notion: pick the source, keep it as it is, build the approval step.

"Add this notes in Notion as it is" refers to text that already exists in the conversation (the
last long answer, a file MAYA built, a file or text the user attached). It is a SAVE, never a
writing job: nothing here calls a model, nothing is regenerated, and the content is converted by
``app.mcp.notion_markdown`` exactly as it is. The one approval step is
``notion_create_page_from_job`` with the Markdown inline (``notion_book.prepare_book_write``
stores it in the job folder and binds the approval to its SHA-256, so the approved bytes are the
written bytes).

Source choice is deterministic from the wording; one ``ask_user`` card is shown only when two
candidates are really plausible.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Final

from app.files.extract import decode_text, docx_paragraphs, extract, kind_for
from app.services import required_details

NOTION_TOOL: Final = "notion_create_page_from_job"
MIN_SUBSTANTIVE_CHARS: Final = 300
MAX_INLINE_CHARS: Final = 250_000
MESSAGE_LABEL: Final = "My last answer"
MAX_ARTIFACT_OPTIONS: Final = 3
PDF_NOTE: Final = (
    "This is text read back from a PDF: headings and lists may be approximate and the layout "
    "(columns, page breaks) is not kept."
)
_TEXT_TYPES: Final = frozenset({"md", "markdown", "txt", "text", "html", "htm", "docx", "pdf"})

# Status, start and task-result boilerplate is never "the notes".
_BOILERPLATE: Final = re.compile(
    r"^\s*(?:started\b|completed\b|done\b|task\b|i(?:'|’)?ve started|i have started|"
    r"working on\b|your .{0,60}\b(?:is|are) (?:ready|saved|built)\b|.{0,80}\(built \d+ pages?\))",
    re.IGNORECASE,
)
_FILE_WORD: Final = re.compile(
    r"\b(?:pdf|docx?|word|file|document|markdown|html|txt)\b", re.IGNORECASE
)
_MESSAGE_WORD: Final = re.compile(
    r"\b(?:answer|reply|response|message|jawab|upar|above|last|previous)\b", re.IGNORECASE
)
_THIS_WORD: Final = re.compile(r"\b(?:this|these|ye|yeh|isko|inko|ise|inhe|it)\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Source:
    """One piece of existing content. ``text`` is filled for a message, an attachment or pasted
    text; an artifact is read lazily from ``path`` (:func:`load`)."""

    kind: str  # message | artifact | attachment | pasted
    label: str
    text: str = ""
    path: Path | None = None
    file_type: str = ""


@dataclass(frozen=True, slots=True)
class Choice:
    """``source`` is the pick; ``ask`` the plausible candidates when none could be picked."""

    source: Source | None = None
    ask: tuple[Source, ...] = ()


# ------------------------------------------------------------------------------ candidates


def is_boilerplate(text: str) -> bool:
    return bool(_BOILERPLATE.match(text or ""))


def last_assistant_message(texts: Sequence[str]) -> str | None:
    """The newest substantive assistant text (oldest first in, newest wins): long enough to be
    notes and not a start / status / "built 3 pages" line."""
    for text in reversed(list(texts)):
        clean = (text or "").strip()
        if len(clean) >= MIN_SUBSTANTIVE_CHARS and not is_boilerplate(clean):
            return clean
    return None


def artifact_sources(files: Sequence[Mapping[str, Any]], root: Path | None) -> list[Source]:
    """The chat's files that can be read as text, newest first (``files`` oldest first)."""
    found: list[Source] = []
    if root is None:
        return found
    for entry in reversed(list(files)):
        name = str(entry.get("name") or "")
        key = str(entry.get("storage_key") or "")
        file_type = str(entry.get("file_type") or "").lower() or kind_for(name)
        if not name or not key or file_type not in _TEXT_TYPES:
            continue
        path = (Path(root) / key).resolve()
        try:
            path.relative_to(Path(root).resolve())
        except ValueError:
            continue
        if path.is_file():
            found.append(Source("artifact", name, path=path, file_type=file_type))
    return found


def has_existing_content(texts: Sequence[str], files: Sequence[Mapping[str, Any]]) -> bool:
    """True when something earlier in the chat could be saved (a long answer or a readable file
    named by the state or in a message)."""
    if last_assistant_message(texts) is not None:
        return True
    if any(str(f.get("file_type") or "").lower() in _TEXT_TYPES for f in files):
        return True
    return any(
        re.search(r"\.(?:pdf|docx|md|txt|html)\b", text or "", re.IGNORECASE) for text in texts
    )


# --------------------------------------------------------------------------------- choosing


def _norm(text: str) -> str:
    return re.sub(r"[\s_\-.]+", " ", text.lower()).strip()


def choose(
    request: str,
    message: str | None,
    artifacts: Sequence[Source],
    attached: Sequence[Source] = (),
    *,
    explicit: bool = False,
) -> Choice:
    """Pick the source from the wording. ``request`` may carry a ``Source: ...`` card answer.

    ``explicit`` means ``message`` is the very message the user pointed at (the message menu's
    ``source_message_id``): it wins over every other candidate and the wording."""
    answers = required_details._answers(request)  # noqa: SLF001 - one parser for card answers
    answer = (answers.get("source") or "").strip()
    text = _norm(required_details.strip_answers(request))
    message_source = Source("message", MESSAGE_LABEL, text=message) if message else None
    if explicit and message_source is not None:
        return Choice(message_source)
    if answer:
        wanted = _norm(answer)
        if message_source is not None and wanted == _norm(MESSAGE_LABEL):
            return Choice(message_source)
        for item in artifacts:
            if _norm(item.label) == wanted:
                return Choice(item)
        if len(answer) >= 40:  # the user pasted the text itself
            return Choice(Source("pasted", "Pasted text", text=answer))
    if attached:
        return Choice(attached[0]) if len(attached) == 1 else Choice(ask=tuple(attached))
    for item in artifacts:
        stem = _norm(Path(item.label).stem)
        if stem and (stem in text or _norm(item.label) in text):
            return Choice(item)
    if _FILE_WORD.search(text) and artifacts:
        for word, types in (
            ("pdf", {"pdf"}),
            ("docx", {"docx"}),
            ("doc", {"docx"}),
            ("word", {"docx"}),
            ("markdown", {"md", "markdown"}),
            ("html", {"html", "htm"}),
            ("txt", {"txt", "text"}),
        ):
            if re.search(rf"\b{word}\b", text):
                typed = [a for a in artifacts if a.file_type in types]
                if typed:
                    return Choice(typed[0])
        return Choice(artifacts[0])
    if _MESSAGE_WORD.search(text) and message_source is not None:
        return Choice(message_source)
    if message_source is not None and artifacts:
        if _THIS_WORD.search(text):
            return Choice(message_source)
        return Choice(ask=(message_source, *artifacts[:MAX_ARTIFACT_OPTIONS]))
    if message_source is not None:
        return Choice(message_source)
    if artifacts:
        return Choice(artifacts[0])
    return Choice()


def source_options(candidates: Sequence[Source]) -> list[tuple[str, str]]:
    """``(label, description)`` pairs for the "Which notes?" card."""
    options: list[tuple[str, str]] = []
    for item in candidates:
        if item.kind == "message":
            first = " ".join(item.text.split())[:80]
            options.append((item.label, f"The long answer above: {first}"))
        else:
            options.append((item.label, f"A file from this chat ({item.file_type})"))
    return options


# ----------------------------------------------------------------------------- extracting


class _HtmlToMarkdown(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("h1", "h2", "h3"):
            self.out.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag in ("h4", "h5", "h6"):
            self.out.append("\n\n#### ")
        elif tag == "li":
            self.out.append("\n- ")
        elif tag in ("p", "div", "br", "tr", "pre"):
            self.out.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in ("p", "h1", "h2", "h3", "h4", "h5", "h6"):
            self.out.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.out.append(data)


def html_to_markdown(html: str) -> str:
    parser = _HtmlToMarkdown()
    parser.feed(html)
    return clean_blank_lines("".join(parser.out))


def clean_blank_lines(text: str) -> str:
    """Collapse runs of 3+ blank lines to one blank line; strip trailing spaces."""
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip("\n")


def _docx_markdown(path: Path) -> str:
    from docx import Document

    doc = Document(str(path))
    lines: list[str] = []
    for _index, style, text in docx_paragraphs(doc):
        if not text.strip():
            lines.append("")
            continue
        head = re.match(r"Heading (\d)", style)
        if head:
            lines.append("#" * min(int(head.group(1)), 3) + " " + text.strip())
        elif style == "Title":
            lines.append("# " + text.strip())
        elif "List" in style:
            lines.append("- " + text.strip())
        else:
            lines.append(text.strip())
    for table in doc.tables:
        rows = [" | ".join(c.text.replace("\n", " ").strip() for c in r.cells) for r in table.rows]
        if rows:
            lines.append("")
            lines.append("| " + rows[0] + " |")
            lines.append("|" + " --- |" * len(table.rows[0].cells))
            lines += ["| " + row + " |" for row in rows[1:]]
    return clean_blank_lines("\n".join(lines))


def _read_file(path: Path, file_type: str) -> tuple[str, str]:
    """``(markdown, note)`` for one stored file. An unreadable file gives ``("", reason)``."""
    if file_type in ("md", "markdown", "txt", "text"):
        return decode_text(path.read_bytes()), ""
    if file_type in ("html", "htm"):
        return html_to_markdown(decode_text(path.read_bytes())), ""
    if file_type == "docx":
        text = _docx_markdown(path)
        return (text, "") if text else ("", "This Word file has no readable text.")
    if file_type == "pdf":
        got = extract(path, "pdf", max_chars=MAX_INLINE_CHARS)
        if not got.readable:
            return "", got.note or "This PDF has no readable text."
        text = re.sub(r"(?m)^== Page \d+\s*$\n?", "", got.text)
        return clean_blank_lines(text), " ".join(x for x in (PDF_NOTE, got.note) if x)
    return "", "This file type cannot be read."


async def load(source: Source) -> tuple[str, str]:
    """The Markdown text of ``source`` and an honest note (``("", note)`` when unreadable)."""
    if source.kind != "artifact":
        return source.text, ""
    if source.path is None:
        return "", "The file is gone."
    try:
        return await asyncio.to_thread(_read_file, source.path, source.file_type)
    except Exception as error:  # noqa: BLE001 - a bad file is a plain sentence, not a crash
        return "", f"The file could not be read ({type(error).__name__})."


# ----------------------------------------------------------------------------- the content


def title_for(markdown: str, source: Source) -> str:
    """The first heading, else the file name without its extension, else the first line."""
    for line in markdown.split("\n"):
        stripped = line.strip()
        if stripped.startswith("#"):
            title = re.sub(r"^#+\s*", "", stripped).strip()
            if title:
                return title[:200]
    if source.kind == "artifact":
        stem = Path(source.label).stem.replace("_", " ").replace("-", " ").strip()
        if stem:
            return stem[:200]
    first = next((ln.strip() for ln in markdown.split("\n") if ln.strip()), "Notes")
    return first[:80]


def prepare_content(markdown: str, *, as_is: bool) -> str:
    """Verbatim when ``as_is`` (not one character changes). Otherwise only light cleanup of blank
    lines and spacing: no rewording and no added facts."""
    return markdown if as_is else clean_blank_lines(markdown)


def build_step(
    *,
    title: str,
    markdown: str,
    parent_id: str,
    parent_label: str,
    as_is: bool,
    source: Source,
    note: str,
) -> Any:
    """The one approval-gated step: ``notion_create_page_from_job`` with the Markdown inline."""
    from app.agents.state import SubTask  # local: keeps services free of the agents package

    return SubTask(
        id="step_1",
        assigned_agent="pm",
        description=f"Save the existing content '{title[:80]}' to Notion (needs your approval)",
        tool=NOTION_TOOL,
        tool_args={},
        input_data={
            "critical": True,
            "step_label": "Save the notes to Notion",
            "notion_book": {
                "parent_page_id": parent_id,
                "parent_label": parent_label,
                "title": title,
                "inline_markdown": markdown,
                "as_is": as_is,
                "source_label": source.label,
                "source_note": note,
            },
        },
    )


# ----------------------------------------------------------------------------- the parent

_PAGE_ID_LINE: Final = re.compile(r"^-\s*(.+?)\s*\|\s*id\s+(\S+)", re.MULTILINE)


async def resolve_parent(toolkit: Any, destination: str) -> tuple[str, str]:
    """``(page id, label)`` of the Notion parent the user named (a link, an id or a title found
    by a real search), else ``("", "")``: the step then asks again with real pages. Never guesses
    a page the user did not name, and never raises."""
    from app.connections.providers import ConnectionInputError, normalize_notion_database_id

    text = " ".join((destination or "").split())
    if not text or text.lower() == required_details.SKIPPED:
        return "", ""
    try:
        return normalize_notion_database_id(text[:600]), ""
    except ConnectionInputError:
        pass
    if toolkit is None:
        return "", ""
    try:
        result = await asyncio.wait_for(
            toolkit.call("notion_search", {"query": text[:100], "scope": "workspace", "limit": 8}),
            timeout=8,
        )
    except Exception:  # noqa: BLE001 - no parent: the step asks
        return "", ""
    if not isinstance(result, Mapping) or not result.get("ok"):
        return "", ""
    pages = [
        (m.group(1).strip(), m.group(2).strip())
        for m in _PAGE_ID_LINE.finditer(str(result.get("result") or ""))
    ]
    exact = [p for p in pages if p[0].casefold() == text.casefold()]
    if len(exact) != 1:
        return "", ""
    try:
        return normalize_notion_database_id(exact[0][1]), exact[0][0]
    except ConnectionInputError:
        return "", ""


def destination_text(request: str) -> str:
    """The Notion destination the request names: the card answer, a Notion link, or ``""``."""
    given = required_details._answers(request).get("destination")  # noqa: SLF001
    if given and given.strip() and given.strip().lower() != required_details.SKIPPED:
        return given.strip()
    match = required_details._NOTION_URL.search(request or "")  # noqa: SLF001
    return match.group(0) if match else ""
