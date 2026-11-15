"""Editing a file IN PLACE, and replacing the original after approval (ADR-0022).

An edit request on an existing file writes the new content back to the SAME path, atomically
(temp file in the same folder, then os.replace), keeping the file name and extension. No "(edited)"
copy is created. Before overwriting, a bounded backup of the previous version is kept in
``.maya-backups`` (max versions bounded, oldest pruned). If the user explicitly asks for a
separate copy ("save as", "make a copy", "keep the original"), a copy is created instead.

Supported edits (the model only DECIDES the change, the code applies it):

* text, code, markdown, csv, json: the full new content (hard size cap), written as UTF-8;
* docx: ``[{paragraph_index, new_text}]`` plus optional appended paragraphs (python-docx keeps
  every style; the first run of an edited paragraph keeps its character formatting);
* xlsx: named operations ``[{op, sheet, ...}]`` (``sheet_ops``: add, rename or delete a column,
  add rows, sort, filter and copy, dedupe, fill a formula down, recalc totals, find and replace,
  text to number; each reports its counts) or single cells ``[{sheet, cell, value | formula}]``
  (openpyxl; charts or images of the original may not survive, which is reported, and is one
  reason the original is never replaced silently);
* pptx: ``[{slide, shape_index, new_text}]`` (python-pptx, first run formatting kept);
* pdf: NOT editable in place (a PDF keeps no editable layout). Redesign mode rebuilds a NEW PDF
  from the extracted text (:func:`edit_pdf_redesign`); :func:`not_editable_message` says so for
  every other PDF request. Images are never editable.

Edit preservation: a request that asks to keep the information (or not to change the content, or
to make the file more professional) is checked by :func:`compare_preservation`. Numbers, links
and names must be identical, and the content words must mostly survive. A version that fails is
refused (the caller discards it and says why); the original is never touched either way.
"""

from __future__ import annotations

import difflib
import hashlib
import os
import re
import uuid
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.automation.model import Document
from app.automation.sheet_ops import SheetOpError, apply_sheet_ops
from app.files.extract import (
    EDITABLE_TEXT_KINDS,
    decode_text,
    extract,
    pptx_shapes,
)

MAX_TEXT_EDIT_CHARS = 200_000
"""Hard cap on the full new content of a text edit (and on the size of a file we agree to edit)."""
MAX_OPS = 500
MAX_APPENDED_PARAGRAPHS = 200
MAX_DIFF_LINES = 200
MAX_DIFF_CHARS = 20_000
_CELL = re.compile(r"^[A-Za-z]{1,3}[1-9][0-9]{0,6}$")
_BAD_FORMULA = re.compile(
    r"(?i)\b(WEBSERVICE|HYPERLINK|CALL|REGISTER|EXEC|FILTERXML|RTD|DDE|SHELL)\s*\("
    r"|\[|\||\\\\|https?:|file:"
)
EDITABLE_KINDS = EDITABLE_TEXT_KINDS | {"docx", "xlsx", "pptx"}


class EditError(ValueError):
    """The edit cannot be applied. The message is plain words for the user."""


class NotEditableError(EditError):
    """The file type cannot be edited in place."""


def not_editable_message(kind: str, name: str) -> str:
    if kind == "pdf":
        return (
            f"'{name}' is a PDF, and editing a PDF while keeping its layout is not supported. "
            "I can redesign it instead: a NEW PDF rebuilt from its extracted text with your "
            "changes applied. Ask me to redesign it."
        )
    if kind == "image":
        return f"'{name}' is an image, and I cannot edit the pixels of an image."
    return f"'{name}' cannot be edited (type: {kind})."


@dataclass(slots=True)
class EditOutcome:
    path: Path
    diff: str
    changes: int
    warnings: list[str] = field(default_factory=list)


def unified_diff(old: str, new: str, label: str) -> str:
    lines = list(
        difflib.unified_diff(
            old.splitlines(), new.splitlines(), f"{label} (original)", f"{label} (new)", lineterm=""
        )
    )
    cut = len(lines) > MAX_DIFF_LINES
    text = "\n".join(lines[:MAX_DIFF_LINES])
    if cut or len(text) > MAX_DIFF_CHARS:
        text = text[:MAX_DIFF_CHARS] + "\n... (diff shortened)"
    return text


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def reserve_version(directory: Path, original_name: str, tag: str = "copy") -> Path:
    """Exclusively create ``name (copy).ext`` (then ``(copy 2)``...) and return its path."""
    stem, dot, ext = original_name.rpartition(".")
    if not dot or not stem:
        stem, ext = original_name, ""
    suffix = f".{ext}" if ext else ""
    for number in range(1, 1000):
        label = tag if number == 1 else f"{tag} {number}"
        candidate = directory / f"{stem} ({label}){suffix}"
        try:
            with open(candidate, "xb"):
                pass
            return candidate
        except FileExistsError:
            continue
    raise EditError(f"There are too many {tag} versions of this file already.")


# ---------------------------------------------------------------------------------- text


def edit_text(src: Path, new_content: str, dest: Path) -> EditOutcome:
    if len(new_content) > MAX_TEXT_EDIT_CHARS:
        raise EditError(f"The new content is longer than {MAX_TEXT_EDIT_CHARS} characters.")
    old = decode_text(src.read_bytes())
    if new_content == old:
        raise EditError("The edit changes nothing.")
    dest.write_text(new_content, encoding="utf-8", newline="")
    return EditOutcome(dest, unified_diff(old, new_content, src.name), changes=1)


# ---------------------------------------------------------------------------------- docx


def _set_paragraph_text(paragraph: Any, text: str) -> None:
    runs = paragraph.runs
    if runs:
        runs[0].text = text
        for run in runs[1:]:
            run.text = ""
    else:
        paragraph.add_run(text)


def _set_cell_text(cell: Any, text: str) -> None:
    """The cell's text, keeping the paragraph and first-run formatting the cell already has."""
    paragraphs = cell.paragraphs
    if paragraphs and paragraphs[0].runs:
        _set_paragraph_text(paragraphs[0], text)
        for extra in paragraphs[1:]:
            for run in extra.runs:
                run.text = ""
    else:
        cell.text = text


def _append_table(doc: Any, headers: list[str], rows: list[list[str]]) -> None:
    """Add a table that looks like the document's own tables.

    With an existing table of the same width, its XML is copied (style, header fill, borders,
    column widths, cell formatting) and only the text changes; otherwise the new table takes the
    existing table's style name. A document with no table gets python-docx's plain table.
    """
    import copy

    from docx.table import Table

    existing = doc.tables[-1] if doc.tables else None
    if existing is not None and len(existing.columns) == len(headers) and len(existing.rows) >= 2:
        clone = copy.deepcopy(existing._tbl)
        body_row = copy.deepcopy(clone.tr_lst[1])
        for tr in clone.tr_lst[1:]:
            clone.remove(tr)
        for _ in rows:
            clone.append(copy.deepcopy(body_row))
        doc.element.body.sectPr.addprevious(clone)
        table = Table(clone, existing._parent)
        for col, header in enumerate(headers):
            _set_cell_text(table.rows[0].cells[col], header)
        for r, row in enumerate(rows, start=1):
            for col, value in enumerate(row):
                _set_cell_text(table.rows[r].cells[col], value)
        return
    table = doc.add_table(rows=1 + len(rows), cols=len(headers))
    if existing is not None and existing.style is not None:
        table.style = existing.style
    for col, header in enumerate(headers):
        table.rows[0].cells[col].text = header
    for r, row in enumerate(rows, start=1):
        for col, value in enumerate(row):
            table.rows[r].cells[col].text = value


def edit_docx(src: Path, plan: dict[str, Any], dest: Path) -> EditOutcome:
    from docx import Document

    edits = list(plan.get("edits") or [])
    appended = [str(p) for p in (plan.get("append_paragraphs") or [])]
    if len(edits) > MAX_OPS or len(appended) > MAX_APPENDED_PARAGRAPHS:
        raise EditError("The edit is too large.")
    doc = Document(str(src))
    count = len(doc.paragraphs)
    lines: list[str] = []
    seen: set[int] = set()
    for edit in edits:
        try:
            index = int(edit["paragraph_index"])
            new_text = str(edit["new_text"])
        except (KeyError, TypeError, ValueError):
            raise EditError("Each document edit needs paragraph_index and new_text.") from None
        if not 0 <= index < count:
            raise EditError(f"paragraph_index {index} does not exist (0 to {count - 1}).")
        if index in seen:
            raise EditError(f"paragraph_index {index} is edited twice.")
        seen.add(index)
        paragraph = doc.paragraphs[index]
        old = paragraph.text
        if old == new_text:
            continue
        _set_paragraph_text(paragraph, new_text)
        lines.append(f"paragraph {index}:\n- {old}\n+ {new_text}")
    for text in appended:
        doc.add_paragraph(text)
        lines.append(f"appended paragraph:\n+ {text}")
    for table_spec in plan.get("append_tables") or []:
        headers = [str(h) for h in table_spec.get("headers") or []]
        rows = [[str(c) for c in row] for row in table_spec.get("rows") or []]
        if not headers or any(len(r) != len(headers) for r in rows) or len(rows) > 200:
            raise EditError("An appended table needs headers and rows of the same width.")
        _append_table(doc, headers, rows)
        lines.append(f"appended table: {len(rows)} rows x {len(headers)} columns")
    if not lines:
        raise EditError("The edit changes nothing.")
    doc.save(str(dest))
    return EditOutcome(dest, "\n".join(lines)[:MAX_DIFF_CHARS], changes=len(lines))


# ---------------------------------------------------------------------------------- xlsx


def edit_xlsx(src: Path, plan: dict[str, Any], dest: Path) -> EditOutcome:
    from openpyxl import load_workbook

    ops = list(plan.get("ops") or [])
    if not ops:
        raise EditError("The edit changes nothing.")
    if len(ops) > MAX_OPS:
        raise EditError("The edit is too large.")
    named: list[Mapping[str, Any]] = [o for o in ops if isinstance(o, dict) and o.get("op")]
    if named:
        # Named operations (sort, add column, dedupe ...): see ``sheet_ops``.
        if len(named) != len(ops):
            raise EditError("Do not mix named operations with single-cell edits in one call.")
        try:
            done = apply_sheet_ops(src, named, dest)
        except SheetOpError as error:
            raise EditError(str(error)) from None
        return EditOutcome(
            dest,
            "\n".join(done.lines)[:MAX_DIFF_CHARS],
            changes=done.changes,
            warnings=done.warnings,
        )
    workbook = load_workbook(str(src))
    warnings: list[str] = []
    lines: list[str] = []
    try:
        if any(getattr(ws, "_charts", None) or getattr(ws, "_images", None) for ws in workbook):
            warnings.append(
                "The original has charts or images; the new version may not keep them. "
                "Check it before replacing the original."
            )
        for op in ops:
            try:
                sheet_name = str(op["sheet"])
                cell_ref = str(op["cell"]).upper()
            except (KeyError, TypeError):
                raise EditError("Each spreadsheet edit needs sheet and cell.") from None
            if sheet_name not in workbook.sheetnames:
                raise EditError(f"The sheet '{sheet_name}' does not exist.")
            if not _CELL.match(cell_ref):
                raise EditError(f"'{cell_ref}' is not a valid cell address.")
            formula = op.get("formula")
            value = op.get("value")
            if formula is not None:
                text = str(formula)
                if not text.startswith("=") or _BAD_FORMULA.search(text) or len(text) > 500:
                    raise EditError(f"The formula in {cell_ref} is not allowed.")
                new_value: Any = text
            elif value is not None:
                if isinstance(value, str) and value.startswith("="):
                    raise EditError(f"{cell_ref}: a value that starts with '=' must be a formula.")
                if isinstance(value, str) and len(value) > 5000:
                    raise EditError(f"The value in {cell_ref} is too long.")
                new_value = value
            else:
                raise EditError(f"{cell_ref}: give a value or a formula.")
            cell = workbook[sheet_name][cell_ref]
            old = cell.value
            cell.value = new_value
            lines.append(f"{sheet_name}!{cell_ref}: {old!r} -> {new_value!r}")
        workbook.save(str(dest))
    finally:
        workbook.close()
    return EditOutcome(
        dest, "\n".join(lines)[:MAX_DIFF_CHARS], changes=len(lines), warnings=warnings
    )


# ---------------------------------------------------------------------------------- pptx


def _set_shape_text(shape: Any, text: str) -> None:
    frame = shape.text_frame
    lines = text.split("\n")
    paragraphs = list(frame.paragraphs)
    for index, line in enumerate(lines):
        if index < len(paragraphs):
            paragraph = paragraphs[index]
            if paragraph.runs:
                paragraph.runs[0].text = line
                for run in paragraph.runs[1:]:
                    run.text = ""
            else:
                paragraph.text = line
        else:
            frame.add_paragraph().text = line
    for paragraph in paragraphs[len(lines) :]:
        for run in paragraph.runs:
            run.text = ""


def edit_pptx(src: Path, plan: dict[str, Any], dest: Path) -> EditOutcome:
    from pptx import Presentation

    ops = list(plan.get("ops") or [])
    if not ops:
        raise EditError("The edit changes nothing.")
    if len(ops) > MAX_OPS:
        raise EditError("The edit is too large.")
    deck = Presentation(str(src))
    slides = list(deck.slides)
    lines: list[str] = []
    for op in ops:
        try:
            number = int(op["slide"])
            shape_index = int(op["shape_index"])
            new_text = str(op["new_text"])
        except (KeyError, TypeError, ValueError):
            raise EditError("Each slide edit needs slide, shape_index and new_text.") from None
        if not 1 <= number <= len(slides):
            raise EditError(f"Slide {number} does not exist (1 to {len(slides)}).")
        shapes = dict(pptx_shapes(slides[number - 1]))
        if shape_index not in shapes:
            raise EditError(f"Slide {number} has no text shape {shape_index}.")
        shape = slides[number - 1].shapes[shape_index]
        old = shapes[shape_index]
        if old == new_text:
            continue
        _set_shape_text(shape, new_text)
        lines.append(f"slide {number} shape {shape_index}:\n- {old}\n+ {new_text}")
    if not lines:
        raise EditError("The edit changes nothing.")
    deck.save(str(dest))
    return EditOutcome(dest, "\n".join(lines)[:MAX_DIFF_CHARS], changes=len(lines))


# --------------------------------------------------------------------------- dispatcher


def apply_edit(kind: str, src: Path, plan: dict[str, Any], dest: Path) -> EditOutcome:
    """Apply the model's ``plan`` for ``kind`` to produce ``dest`` (a reserved new path)."""
    if kind in EDITABLE_TEXT_KINDS:
        return edit_text(src, str(plan.get("new_content", "")), dest)
    if kind == "docx":
        return edit_docx(src, plan, dest)
    if kind == "xlsx":
        return edit_xlsx(src, plan, dest)
    if kind == "pptx":
        return edit_pptx(src, plan, dest)
    if kind == "pdf" and isinstance(plan.get("document"), Document):
        return edit_pdf_redesign(src, plan, dest)
    raise NotEditableError(not_editable_message(kind, src.name))


def edit_pdf_redesign(src: Path, plan: dict[str, Any], dest: Path) -> EditOutcome:
    """Build a NEW PDF from the :class:`Document` the caller made from the PDF's text. The source
    layout is not kept; the source file is only read here (extraction happens in the caller)."""
    from app.automation.pdf_engine import create_pdf_from_document

    document = plan["document"]
    if not isinstance(document, Document):
        raise EditError("The redesign needs a document model.")
    rendered = create_pdf_from_document(document, dest)
    return EditOutcome(
        dest,
        f"PDF rebuilt from the text of '{src.name}': {len(document.blocks)} blocks. "
        "The original layout is not kept.",
        changes=1,
        warnings=list(rendered.warnings),
    )


# ------------------------------------------------------------------ edit preservation

PRESERVE_REQUEST = re.compile(
    r"(?i)\b(?:keep|preserve|retain)\s+(?:(?:all|the|every|its|that|these|those|of)\s+){0,3}"
    r"(?:information|content|facts|details|numbers|data|text|wording|figures)\b"
    r"|\b(?:do\s+not|don't|never)\s+(?:change|alter|edit|remove|delete)\s+"
    r"(?:the\s+|any\s+|its\s+)?(?:content|information|facts|numbers|data|figures|text|wording)\b"
    r"|\bwithout\s+(?:changing|altering|removing)\s+(?:the\s+|any\s+)?"
    r"(?:content|information|facts|numbers|data|figures)\b"
    r"|\bmake\s+it\s+(?:more\s+|look\s+more\s+)?professional\b"
)
REDESIGN_REQUEST = re.compile(r"(?i)\b(?:redesign|re-design|rebuild|restyle)\b")
MAX_MISSING_WORD_RATIO = 0.10
"""Share of the content words of the original that a new version may lose (reflow loses none)."""
_PAGE_MARKER = re.compile(r"(?m)^== Page \d+\s*$")
_NUMBER = re.compile(r"\d+(?:[.,:/-]\d+)*%?")
_LINK = re.compile(r"(?i)\b(?:https?://|www\.)\S+|\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
_WORD = re.compile(r"[^\W\d_]{4,}")
_SENTENCE_END = re.compile(r"[.!?]\s+")
_STOP_WORDS = frozenset(
    (
        "this that with from have will your their there which about into they them than "
        "then also been were what when where these those such more most very just should "
        "would could other some each only over under between"
    ).split()
)


def requests_preservation(text: str) -> bool:
    """True when the request asks to keep the content (or to make the file more professional)."""
    return bool(PRESERVE_REQUEST.search(text or ""))


def requests_redesign(text: str) -> bool:
    return bool(REDESIGN_REQUEST.search(text or ""))


COPY_REQUEST = re.compile(
    r"(?i)\b(?:save\s+as|make\s+(?:a\s+)?copy|create\s+(?:a\s+)?copy|keep\s+(?:the\s+)?original|"
    r"as\s+a\s+new\s+(?:copy|file|version)|into\s+a\s+new\s+(?:copy|file|version)|"
    r"save\s+(?:it\s+)?to\s+a\s+new\s+(?:copy|file)|don'?t\s+overwrite|do\s+not\s+overwrite|"
    r"separate\s+(?:copy|file))\b"
)


def requests_copy(text: str) -> bool:
    """True when the request explicitly asks for a new or separate copy."""
    return bool(COPY_REQUEST.search(text or ""))


@dataclass(frozen=True, slots=True)
class PreservationReport:
    ok: bool
    reasons: list[str]
    missing_word_ratio: float


def _clean(text: str) -> str:
    """Whitespace-collapsed text without the page markers of the PDF view (line breaks and
    re-wrapping must never change the result)."""
    return " ".join(_PAGE_MARKER.sub(" ", text).split())


def numbers_in(text: str) -> list[str]:
    return [m.replace(",", "") for m in _NUMBER.findall(text)]


def _links(text: str) -> list[str]:
    return [m.rstrip(".,;:)]}>\"'") for m in _LINK.findall(text)]


def _names(text: str) -> list[str]:
    names: list[str] = []
    for sentence in _SENTENCE_END.split(text):
        tokens = re.findall(r"[^\W\d_][\w'-]*", sentence)
        names.extend(t for t in tokens[1:] if t[:1].isupper() and len(t) >= 2)
    return names


def _content_words(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text) if w.lower() not in _STOP_WORDS]


def _difference(label: str, before: list[str], after: list[str]) -> str | None:
    removed = sorted((Counter(before) - Counter(after)).elements())
    added = sorted((Counter(after) - Counter(before)).elements())
    if not removed and not added:
        return None
    parts = []
    if removed:
        parts.append("removed " + ", ".join(repr(x) for x in removed[:8]))
    if added:
        parts.append("added " + ", ".join(repr(x) for x in added[:8]))
    return f"{label} changed: {'; '.join(parts)}"


def compare_preservation(
    before: str,
    after: str,
    *,
    check_facts: bool = True,
    max_missing_ratio: float = MAX_MISSING_WORD_RATIO,
) -> PreservationReport:
    """Compare the text of an original and a new version.

    ``check_facts`` requires numbers, dates, links and names to be identical (both removed and
    added ones are reported). Independently, the share of the original's content words missing
    from the new text must stay within ``max_missing_ratio``. Word order, line breaks and
    re-wrapping never matter, so a reflow passes.
    """
    old, new = _clean(before), _clean(after)
    reasons: list[str] = []
    if check_facts:
        for label, extractor in (
            ("numbers", numbers_in),
            ("links", _links),
            ("names", _names),
        ):
            difference = _difference(label, extractor(old), extractor(new))
            if difference:
                reasons.append(difference)
    old_words = Counter(_content_words(old))
    total = sum(old_words.values())
    missing = sum((old_words - Counter(_content_words(new))).values())
    ratio = missing / total if total else 0.0
    if total and ratio > max_missing_ratio:
        reasons.append(
            f"{missing} of {total} content words of the original ({ratio:.0%}) are missing "
            f"from the new version (the limit is {max_missing_ratio:.0%})"
        )
    return PreservationReport(ok=not reasons, reasons=reasons, missing_word_ratio=ratio)


def verify_new_version(kind: str, path: Path) -> list[str]:
    """Reopen the written file; returns the checks that passed. Raises :class:`EditError`."""
    if not path.is_file() or path.stat().st_size == 0:
        raise EditError("The new version is empty.")
    if kind in EDITABLE_TEXT_KINDS:
        decode_text(path.read_bytes())
        return ["file_written", "decodes_as_text"]
    view = extract(path, kind, max_chars=2_000)
    if view.note and not view.readable and "could not be read" in view.note:
        raise EditError("The new version could not be opened again, so it was discarded.")
    return ["file_written", "reopens_cleanly"]


def describe_change(old: Path, new: Path, kind: str) -> str:
    """A bounded diff between two files of the same kind (for the approval payload)."""
    if kind in EDITABLE_TEXT_KINDS:
        return unified_diff(decode_text(old.read_bytes()), decode_text(new.read_bytes()), old.name)
    before = extract(old, kind, max_chars=60_000, indexed=True).text
    after = extract(new, kind, max_chars=60_000, indexed=True).text
    return unified_diff(before, after, old.name) or "The two versions have the same text."


# --------------------------------------------------------------------- replace original

BACKUP_DIR_NAME = ".maya-backups"
MAX_BACKUPS_PER_FILE = 5


def backup_name(name: str, when: datetime | None = None) -> str:
    stamp = (when or datetime.now(UTC)).strftime("%Y%m%d-%H%M%S")
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem:
        stem, ext = name, ""
    return f"{stem} (original, {stamp}){'.' + ext if ext else ''}"


def create_backup(target: Path, backup_dir: Path | None = None) -> Path:
    """Keep ONE bounded backup of ``target`` in ``.maya-backups``, pruning oldest versions.

    Backups are kept outside the user's workspace/normal file listings. When more than
    ``MAX_BACKUPS_PER_FILE`` backups exist for this file, the oldest are pruned.
    """
    if not target.is_file():
        raise EditError("The file to backup does not exist.")
    dir_path = backup_dir or (target.parent / BACKUP_DIR_NAME)
    dir_path.mkdir(parents=True, exist_ok=True)

    stem, dot, ext = target.name.rpartition(".")
    if not dot or not stem:
        stem, ext = target.name, ""
    suffix = f".{ext}" if ext else ""
    prefix = f"{stem} (original, "

    existing: list[Path] = []
    try:
        for entry in dir_path.iterdir():
            if entry.is_file() and entry.name.startswith(prefix) and entry.name.endswith(suffix):
                existing.append(entry)
    except OSError:
        pass

    existing.sort(key=lambda p: (p.stat().st_mtime, p.name))
    while len(existing) >= MAX_BACKUPS_PER_FILE:
        oldest = existing.pop(0)
        try:
            oldest.unlink(missing_ok=True)
        except OSError:
            pass

    from app.files.services import collision_safe_copy

    return collision_safe_copy(target, dir_path, backup_name(target.name))


def apply_edit_in_place(
    kind: str,
    target: Path,
    plan: dict[str, Any],
    *,
    keep_backup: bool = True,
    backup_dir: Path | None = None,
) -> tuple[EditOutcome, Path | None]:
    """Apply ``plan`` to ``target`` in place atomically (temp file in same folder, then os.replace).

    A temporary file in the same folder is written and verified first. If valid, a bounded backup
    of the original is kept in ``.maya-backups`` (pruning oldest), and the temporary file atomically
    replaces ``target``. If the edit or verification fails, ``target`` is left completely untouched.
    """
    if not target.is_file():
        raise EditError("The original file does not exist.")
    temp = target.parent / f".maya-edit-{uuid.uuid4().hex[:12]}.tmp"
    try:
        outcome = apply_edit(kind, target, plan, temp)
        verify_new_version(kind, temp)
        backup_path: Path | None = None
        if keep_backup:
            backup_path = create_backup(target, backup_dir=backup_dir)
        os.replace(temp, target)
        return EditOutcome(target, outcome.diff, outcome.changes, outcome.warnings), backup_path
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


@dataclass(frozen=True, slots=True)
class ReplaceOutcome:
    status: str  # "replaced" | "already_replaced"
    backup: Path | None


def replace_original(target: Path, new_version: Path) -> ReplaceOutcome:
    """Copy ``new_version`` over ``target``, keeping a backup in ``.maya-backups``.
    Idempotent: when the target already has the new content nothing changes. The swap is an
    atomic ``os.replace`` of a temporary file in the same folder, so a crash leaves either the
    old or the new content, and the backup is made first."""
    if target == new_version:
        raise EditError("The new version and the original are the same file.")
    if not target.is_file():
        raise EditError("The original file does not exist.")
    if not new_version.is_file():
        raise EditError("The new version does not exist.")
    if sha256_of(target) == sha256_of(new_version):
        return ReplaceOutcome("already_replaced", None)

    backup = create_backup(target)
    temp = target.parent / f".maya-replace-{uuid.uuid4().hex[:12]}.tmp"
    try:
        with open(temp, "xb") as out, open(new_version, "rb") as src:
            while block := src.read(1024 * 1024):
                out.write(block)
        os.replace(temp, target)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
    return ReplaceOutcome("replaced", backup)
