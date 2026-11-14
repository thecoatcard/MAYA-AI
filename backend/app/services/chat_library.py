"""Per-user chat library state behind the right-click menus: pin and archive flags, a locked
title, message feedback, Markdown export and the duplicate (branch) helpers.

Nothing here needs a migration. Two small JSON documents live in the existing per-user settings
store (``UserSettingsStore``), written with the same optimistic compare-and-set as the schedules:

* ``chat_session_flags``: ``{session_id: {pinned, pinned_at, archived, title_locked, dup}}``
  (at most 5000 ids; an entry with nothing set is removed; ids of deleted sessions are pruned).
* ``message_feedback``: ``{message_id: {value, ts, note, session_id, model_id}}`` (at most 2000
  entries, newest kept). It never holds message text and is never read into a prompt.

Reads and writes name the workspace and the user (row-level security still applies), so one
user's flags can never be seen or changed by another.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Final, Protocol, TypeVar
from uuid import UUID

from app.db.repositories.chat import ChatMessageRecord

logger = logging.getLogger("maya.chat.library")

SESSION_FLAGS_KEY: Final = "chat_session_flags"
FEEDBACK_KEY: Final = "message_feedback"
MAX_SESSION_FLAGS: Final = 5000
MAX_FEEDBACK: Final = 2000
MAX_TITLE_CHARS: Final = 120
MAX_NOTE_CHARS: Final = 500
MAX_DUPLICATE_MESSAGES: Final = 500
MAX_EXPORT_MESSAGES: Final = 5000
MAX_LIST_PAGES: Final = 20  # the "wide" session list reads at most 20 x 100 sessions
CURSOR_PREFIX: Final = "flags:"
_ATTEMPTS: Final = 8
_MAX_DOC_BYTES: Final = 900_000

# Control characters (C0, DEL, C1), line and paragraph separators, and lone surrogates.
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f  \ud800-\udfff]")
_FILENAME_UNSAFE = re.compile(r"[^A-Za-z0-9_-]+")
T = TypeVar("T")


class SettingsBackend(Protocol):
    async def get_text(self, workspace_id: UUID, user_id: UUID, key: str) -> str | None: ...

    async def compare_and_set_text(
        self, workspace_id: UUID, user_id: UUID, key: str, expected: str | None, value: str
    ) -> bool: ...


class DocConflict(RuntimeError):
    """The document kept changing under us; the caller may retry the request."""


# ---------------------------------------------------------------------------------- the store


class JsonDocStore:
    """One JSON object kept in one user-settings key, updated with compare-and-set."""

    def __init__(self, backend: SettingsBackend, key: str) -> None:
        self._backend = backend
        self._key = key

    @staticmethod
    def _parse(raw: str | None) -> dict[str, Any]:
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except ValueError:
            logger.warning("chat_library_document_unreadable")
            return {}
        return value if isinstance(value, dict) else {}

    async def load(self, workspace_id: UUID, user_id: UUID) -> dict[str, Any]:
        return self._parse(await self._backend.get_text(workspace_id, user_id, self._key))

    async def update(
        self,
        workspace_id: UUID,
        user_id: UUID,
        change: Callable[[dict[str, Any]], T],
        *,
        trim: Callable[[dict[str, Any]], None] | None = None,
    ) -> T:
        """Apply ``change`` to a fresh copy and store it atomically (retrying on a race).
        ``change`` must be a pure function of the document; an exception aborts the write."""
        for _ in range(_ATTEMPTS):
            raw = await self._backend.get_text(workspace_id, user_id, self._key)
            doc = self._parse(raw)
            result = change(doc)
            if trim is not None:
                trim(doc)
            text = json.dumps(doc, separators=(",", ":"), ensure_ascii=False, sort_keys=True)
            if len(text.encode()) > _MAX_DOC_BYTES:
                raise ValueError("document too large")
            if (raw or "") == text or (not doc and raw is None):
                return result
            if await self._backend.compare_and_set_text(
                workspace_id, user_id, self._key, raw, text
            ):
                return result
        raise DocConflict


# ------------------------------------------------------------------------- titles and flags


def clean_title(raw: str) -> str | None:
    """A sanitised title (whitespace collapsed, 1 to 120 characters), or ``None`` when it cannot
    be used (empty, too long, or holding control characters)."""
    title = " ".join(raw.split())
    if not title or len(title) > MAX_TITLE_CHARS or _CONTROL.search(title) is not None:
        return None
    return title


def _entry(doc: Mapping[str, Any], session_id: str) -> Mapping[str, Any]:
    value = doc.get(session_id)
    return value if isinstance(value, Mapping) else {}


def flags_of(doc: Mapping[str, Any], session_id: str) -> tuple[bool, bool]:
    """``(pinned, archived)`` of one session."""
    entry = _entry(doc, session_id)
    return entry.get("pinned") is True, entry.get("archived") is True


def pinned_at(doc: Mapping[str, Any], session_id: str) -> str:
    value = _entry(doc, session_id).get("pinned_at")
    return value if isinstance(value, str) else ""


def title_locked(doc: Mapping[str, Any], session_id: str) -> bool:
    """True once the user renamed the session: nothing may overwrite that title later."""
    return _entry(doc, session_id).get("title_locked") is True


def has_visibility_flags(doc: Mapping[str, Any]) -> bool:
    return any(
        isinstance(v, Mapping) and (v.get("pinned") is True or v.get("archived") is True)
        for v in doc.values()
    )


def apply_session_patch(
    doc: dict[str, Any],
    session_id: str,
    *,
    pinned: bool | None,
    archived: bool | None,
    lock_title: bool,
    now: str,
) -> tuple[bool, bool]:
    """Change one session's flags in ``doc``; returns the resulting ``(pinned, archived)``."""
    entry = dict(_entry(doc, session_id))
    if pinned is True and entry.get("pinned") is not True:
        entry["pinned"] = True
        entry["pinned_at"] = now
    elif pinned is False:
        entry.pop("pinned", None)
        entry.pop("pinned_at", None)
    if archived is True:
        entry["archived"] = True
    elif archived is False:
        entry.pop("archived", None)
    if lock_title:
        entry["title_locked"] = True
    if entry:
        doc[session_id] = entry
    else:
        doc.pop(session_id, None)
    return entry.get("pinned") is True, entry.get("archived") is True


def trim_flags(doc: dict[str, Any]) -> None:
    """Keep at most 5000 entries: pinned ones first, then the most recently pinned."""
    if len(doc) <= MAX_SESSION_FLAGS:
        return
    ranked = sorted(
        doc,
        key=lambda k: (_entry(doc, k).get("pinned") is True, pinned_at(doc, k)),
        reverse=True,
    )
    for key in ranked[MAX_SESSION_FLAGS:]:
        del doc[key]


def prune_flags(doc: dict[str, Any], known_ids: set[str]) -> bool:
    """Drop the flags of sessions that no longer exist. True when something was removed."""
    stale = [key for key in doc if key not in known_ids]
    for key in stale:
        del doc[key]
    return bool(stale)


def order_sessions[S](
    items: Sequence[S], *, id_of: Callable[[S], str], doc: Mapping[str, Any], archived: str
) -> list[S]:
    """Filter by ``archived`` ("false", "true" or "all") and put pinned sessions first (most
    recently pinned first); the rest keep their incoming (newest first) order."""
    kept: list[S] = []
    for item in items:
        is_archived = flags_of(doc, id_of(item))[1]
        if archived == "false" and is_archived:
            continue
        if archived == "true" and not is_archived:
            continue
        kept.append(item)
    pinned = [i for i in kept if flags_of(doc, id_of(i))[0]]
    pinned.sort(key=lambda i: pinned_at(doc, id_of(i)), reverse=True)
    return pinned + [i for i in kept if not flags_of(doc, id_of(i))[0]]


def wide_offset(cursor: str | None) -> int | None:
    """The offset a ``flags:<n>`` cursor stands for; ``None`` for no cursor or a foreign one."""
    if cursor is None or not cursor.startswith(CURSOR_PREFIX):
        return None
    digits = cursor[len(CURSOR_PREFIX) :]
    if not digits.isascii() or not digits.isdigit() or len(digits) > 7:
        raise ValueError("invalid cursor")
    return int(digits)


def duplicate_digest(key: str, session_id: UUID, from_message_id: UUID | None) -> str:
    """What marks the session a keyed duplicate request created (the key itself is not stored)."""
    raw = f"{key}|{session_id}|{from_message_id or ''}".encode()
    return hashlib.sha256(raw).hexdigest()[:32]


def find_duplicate(doc: Mapping[str, Any], digest: str) -> str | None:
    for session_id, entry in doc.items():
        if isinstance(entry, Mapping) and entry.get("dup") == digest:
            return str(session_id)
    return None


# ---------------------------------------------------------------------------------- feedback


def feedback_of(doc: Mapping[str, Any], message_id: str) -> str | None:
    value = doc.get(message_id)
    if isinstance(value, Mapping) and value.get("value") in ("up", "down"):
        return str(value["value"])
    return None


def set_feedback(
    doc: dict[str, Any],
    message_id: str,
    *,
    value: str | None,
    note: str | None,
    session_id: str,
    model_id: str | None,
    ts: str,
) -> None:
    """Store (or, for ``None``, clear) the caller's feedback. Never stores the message text."""
    if value is None:
        doc.pop(message_id, None)
        return
    entry: dict[str, Any] = {"value": value, "ts": ts, "session_id": session_id}
    if note:
        entry["note"] = note
    if model_id:
        entry["model_id"] = model_id
    doc[message_id] = entry


def trim_feedback(doc: dict[str, Any]) -> None:
    """Keep the 2000 newest entries."""
    if len(doc) <= MAX_FEEDBACK:
        return
    newest = sorted(
        doc,
        key=lambda k: str(doc[k].get("ts", "")) if isinstance(doc[k], Mapping) else "",
        reverse=True,
    )
    for key in newest[MAX_FEEDBACK:]:
        del doc[key]


def drop_feedback_of_session(doc: dict[str, Any], session_id: str) -> bool:
    gone = [
        key
        for key, value in doc.items()
        if isinstance(value, Mapping) and value.get("session_id") == session_id
    ]
    for key in gone:
        del doc[key]
    return bool(gone)


# ----------------------------------------------------------------------------------- export


def export_filename(title: str | None) -> str:
    """A safe ASCII ``.md`` file name from the session title."""
    slug = _FILENAME_UNSAFE.sub("-", (title or "").strip()).strip("-._")[:60].strip("-._")
    return f"{slug or 'chat'}.md"


def _line(text: str, limit: int = 200) -> str:
    return _CONTROL.sub(" ", text).strip()[:limit]


def _attached_names(record: ChatMessageRecord) -> list[str]:
    """File names of the attachments (user) or created files (assistant), in order."""
    names: list[str] = []
    for key in ("attachments", "files_created"):
        items = record.metadata.get(key)
        for item in items if isinstance(items, list) else []:
            name = item.get("name") if isinstance(item, Mapping) else None
            if isinstance(name, str) and _line(name) and _line(name) not in names:
                names.append(_line(name))
    return names


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def export_markdown(
    title: str | None, records: Sequence[ChatMessageRecord], *, exported_at: datetime
) -> str:
    """The conversation as Markdown: a heading per turn (You / MAYA) with its time, the text as
    stored (so a Sources list stays), and ``Attached: name`` lines for files. Progress steps,
    tool calls, task cards, usage and every other metadata field are left out."""
    heading = _line(title or "") or "Chat"
    parts = [f"# {heading}", f"_Exported from MAYA on {_stamp(exported_at)}_"]
    for record in records:
        if record.sender not in ("user", "orchestrator"):
            continue
        label = "You" if record.sender == "user" else "MAYA"
        body = [f"## {label} - {_stamp(record.created_at)}"]
        if record.content.strip():
            body.append(record.content.strip())
        body.extend(f"Attached: {name}" for name in _attached_names(record))
        parts.append("\n\n".join(body))
    return "\n\n".join(parts) + "\n"


# --------------------------------------------------------------------------------- duplicate


def copyable_turns(
    records: Sequence[ChatMessageRecord], from_message_id: UUID | None
) -> list[tuple[str, str]]:
    """``(sender, text)`` of the turns a duplicate copies: up to and including
    ``from_message_id`` (all when ``None``), as plain text only, the last 500 at most. Task-result
    messages, empty turns and every metadata field (tasks, approvals, files, progress) are
    dropped."""
    turns: list[tuple[str, str]] = []
    for record in records:
        if record.sender in ("user", "orchestrator"):
            text = record.content.strip()
            if text and record.metadata.get("kind") != "task_result":
                turns.append((record.sender, record.content))
        if from_message_id is not None and record.id == from_message_id:
            break
    return turns[-MAX_DUPLICATE_MESSAGES:]


def now_iso() -> str:
    return datetime.now(UTC).isoformat()
