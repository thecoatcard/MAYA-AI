"""Read-only IMAP for one short-lived session: list, search, fetch headers, fetch one message.

Nothing here can change the mailbox: folders are opened with EXAMINE (``readonly=True``), bodies are
fetched with ``BODY.PEEK`` (so nothing is marked read), and there is no store, copy, expunge,
append or delete call. User text reaches the server only through :func:`quote` (or one UTF-8
literal), never as raw protocol text.
"""

from __future__ import annotations

import imaplib
import re
import ssl
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from email import policy
from email.message import Message
from email.parser import BytesParser
from typing import Any, Final

from app.mail.config import MailboxConfig
from app.mail.errors import MailError, map_error

TIMEOUT_SECONDS: Final = 15.0
MAX_CONNECTIONS: Final = 3
MAX_MESSAGE_BYTES: Final = 10 * 1024 * 1024
LOGICAL_FOLDERS: Final = ("inbox", "sent", "drafts", "all")
_FOLDER_ALIASES: Final = {
    "inbox": "inbox",
    "sent": "sent",
    "sent mail": "sent",
    "sent items": "sent",
    "drafts": "drafts",
    "draft": "drafts",
    "all": "all",
    "all mail": "all",
}
_SPECIAL_USE: Final = {"\\sent": "sent", "\\drafts": "drafts", "\\all": "all"}
_NAME_GUESS: Final = {
    "sent": ("sent", "sent items", "sent mail", "sent messages"),
    "drafts": ("drafts", "draft"),
    "all": ("all mail",),
}
_MONTHS: Final = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
# One process-wide cap on open mailbox sessions: parallel tool calls must not open dozens.
_connections = threading.BoundedSemaphore(MAX_CONNECTIONS)
_CONTROL: Final = re.compile(r"[\x00-\x1f\x7f]")


def logical_folder(name: str) -> str:
    key = " ".join((name or "inbox").strip().lower().split())
    folder = _FOLDER_ALIASES.get(key)
    if folder is None:
        raise MailError(
            "Unknown folder. Use one of: INBOX, Sent, Drafts, All Mail.", "folder_not_found"
        )
    return folder


def quote(value: str) -> str:
    """An IMAP quoted string for ``value`` (ASCII, no control characters). Everything
    user-supplied that goes into a command passes through here: ``"`` and ``\\`` are escaped and
    control characters are refused, so a value can never end the string and add protocol text."""
    if _CONTROL.search(value):
        raise MailError("A search term contains a control character.", "bad_search")
    if not value.isascii():
        raise MailError("Internal: a non-ASCII term must use a literal.", "bad_search")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def imap_date(value: str) -> str:
    """``2026-11-05`` -> ``05-Nov-2026`` (locale independent; the input is validated)."""
    try:
        parsed = date.fromisoformat(value.strip())
    except ValueError:
        raise MailError("Dates must look like 2026-11-05 (year-month-day).", "bad_search") from None
    return f"{parsed.day:02d}-{_MONTHS[parsed.month - 1]}-{parsed.year}"


@dataclass(frozen=True, slots=True)
class Criteria:
    """A validated search. ``terms`` is the IMAP search key list; at most one UTF-8 value is sent
    as an IMAP literal (always the argument of the last key)."""

    terms: tuple[str, ...]
    literal: bytes | None = None
    charset_utf8: bool = False


def build_criteria(
    *,
    unread_only: bool = False,
    sender: str = "",
    to: str = "",
    subject: str = "",
    text: str = "",
    since: str = "",
    before: str = "",
) -> Criteria:
    terms: list[str] = []
    if unread_only:
        terms.append("UNSEEN")
    if since:
        terms += ["SINCE", imap_date(since)]
    if before:
        terms += ["BEFORE", imap_date(before)]
    texts = [
        (key, value.strip())
        for key, value in (("FROM", sender), ("TO", to), ("SUBJECT", subject), ("TEXT", text))
        if value and value.strip()
    ]
    non_ascii = [item for item in texts if not item[1].isascii()]
    if len(non_ascii) > 1:
        raise MailError(
            "Search with only one non-English (for example Hindi) term at a time.", "bad_search"
        )
    literal: bytes | None = None
    for key, value in (item for item in texts if item[1].isascii()):
        terms += [key, quote(value)]
    if non_ascii:
        key, value = non_ascii[0]
        if _CONTROL.search(value):
            raise MailError("A search term contains a control character.", "bad_search")
        terms.append(key)  # the literal follows this key as its argument
        literal = value.encode("utf-8")
    return Criteria(tuple(terms) or ("ALL",), literal, charset_utf8=literal is not None)


@dataclass(slots=True)
class FetchedItem:
    meta: str
    literal: bytes | None = None


_NEW_RESPONSE = re.compile(rb"^\d+ \(")


def parse_fetch(data: Sequence[Any]) -> list[FetchedItem]:
    """The FETCH reply as items: the metadata text (UID, FLAGS, SIZE ...) and the literal, if any.
    A server may put some fields after the literal; they are joined to the same item."""
    items: list[FetchedItem] = []
    current: FetchedItem | None = None
    for part in data:
        if isinstance(part, tuple):
            current = FetchedItem(bytes(part[0]).decode("latin-1"), bytes(part[1]))
            items.append(current)
        elif isinstance(part, bytes):
            if current is None or _NEW_RESPONSE.match(part):
                current = FetchedItem(part.decode("latin-1"))
                items.append(current)
            else:
                current.meta += " " + part.decode("latin-1")
    return items


def meta_uid(meta: str) -> int | None:
    found = re.search(r"\bUID (\d+)", meta)
    return int(found.group(1)) if found else None


def meta_flags(meta: str) -> set[str]:
    found = re.search(r"FLAGS \(([^)]*)\)", meta)
    return {flag.lower() for flag in found.group(1).split()} if found else set()


def meta_size(meta: str) -> int:
    found = re.search(r"RFC822\.SIZE (\d+)", meta)
    return int(found.group(1)) if found else 0


def clean_header(value: object) -> str:
    """A header value as one safe line of text."""
    try:
        text = str(value or "")
    except Exception:  # noqa: BLE001 - a malformed header must not fail the listing
        return ""
    return " ".join(_CONTROL.sub(" ", text).split())


def parse_message(raw: bytes, *, headers_only: bool = False) -> Message[str, Any]:
    return BytesParser(policy=policy.default).parsebytes(raw, headersonly=headers_only)


@dataclass(slots=True)
class Summary:
    uid: int
    sender: str
    to: str
    subject: str
    date: str
    unread: bool
    size: int
    has_attachment: bool | None = None
    message_id: str = ""


@dataclass(slots=True)
class FullMessage:
    uid: int
    message: Message[str, Any] | None
    size: int
    too_large: bool = False
    headers: Message[str, Any] | None = None


def _unquote_name(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        return re.sub(r"\\(.)", r"\1", raw[1:-1])
    return raw


_LIST_LINE = re.compile(r'^\((?P<flags>[^)]*)\)\s+(?:"(?:[^"\\]|\\.)*"|NIL)\s+(?P<name>.+)$', re.S)


def parse_folders(data: Sequence[Any]) -> dict[str, str]:
    """logical name -> the server's folder name, from LIST (special-use flags first, names next)."""
    found: dict[str, str] = {"inbox": "INBOX"}
    names: list[str] = []
    for item in data:
        if isinstance(item, tuple):
            line = re.sub(r"\{\d+\}$", "", bytes(item[0]).decode("latin-1"))
            line += '"' + bytes(item[1]).decode("latin-1") + '"'
        elif isinstance(item, bytes):
            line = item.decode("latin-1")
        else:
            continue
        match = _LIST_LINE.match(line.strip())
        if match is None:
            continue
        flags = {flag.lower() for flag in match.group("flags").split()}
        if "\\noselect" in flags:
            continue
        name = _unquote_name(match.group("name"))
        names.append(name)
        for flag, logical in _SPECIAL_USE.items():
            if flag in flags:
                found.setdefault(logical, name)
    for logical, guesses in _NAME_GUESS.items():
        if logical in found:
            continue
        for name in names:
            if name.rsplit("/", 1)[-1].rsplit(".", 1)[-1].strip().lower() in guesses:
                found[logical] = name
                break
    return found


class ImapSession:
    """One connection: login, read-only operations, logout. Use as a context manager."""

    def __init__(self, config: MailboxConfig, *, timeout: float = TIMEOUT_SECONDS) -> None:
        self._config = config
        self._timeout = timeout
        self._imap: Any = None
        self._held = False
        self._folders: dict[str, str] | None = None

    def __enter__(self) -> ImapSession:
        if not _connections.acquire(timeout=self._timeout):
            raise MailError(
                "Too many mailbox sessions are open at once. Try again in a moment.",
                "busy",
                retryable=True,
            )
        self._held = True
        try:
            context = ssl.create_default_context()
            self._imap = imaplib.IMAP4_SSL(
                self._config.imap_host,
                self._config.imap_port,
                ssl_context=context,
                timeout=self._timeout,
            )
            self._imap.login(self._config.address, self._config.password)
        except BaseException as error:
            self._close()
            if isinstance(error, Exception):
                raise map_error(error, self._config, action="login") from None
            raise
        return self

    def __exit__(self, *_exc: object) -> None:
        self._close()

    def _close(self) -> None:
        imap, self._imap = self._imap, None
        if imap is not None:
            try:
                imap.logout()
            except Exception:  # noqa: BLE001, S110 - closing must never raise
                pass
        if self._held:
            self._held = False
            _connections.release()

    @contextmanager
    def _guard(self, action: str) -> Iterator[None]:
        try:
            yield
        except MailError:
            raise
        except Exception as error:
            raise map_error(error, self._config, action=action) from None

    def folders(self) -> dict[str, str]:
        if self._folders is None:
            with self._guard("folder list"):
                typ, data = self._imap.list()
            if typ != "OK":
                raise MailError("The mailbox folders could not be listed.", "folder_not_found")
            self._folders = parse_folders(data or [])
        return self._folders

    def select(self, logical: str) -> tuple[str, int]:
        """Open a folder read-only. Returns (the server's name, UIDVALIDITY)."""
        name = self.folders().get(logical)
        if name is None:
            raise MailError(
                f"This mailbox has no {logical} folder that MAYA can identify.", "folder_not_found"
            )
        with self._guard("folder open"):
            typ, _data = self._imap.select(quote(name), readonly=True)
            if typ != "OK":
                raise MailError(
                    "That mail folder was not found on this mailbox.", "folder_not_found"
                )
            _code, values = self._imap.response("UIDVALIDITY")
        validity = 0
        if values and values[0] is not None:
            try:
                validity = int(bytes(values[0]).decode("ascii", "ignore") or 0)
            except ValueError:
                validity = 0
        return name, validity

    def search(self, criteria: Criteria) -> list[int]:
        """UIDs matching ``criteria``, ascending."""
        args: list[str] = []
        if criteria.charset_utf8:
            args += ["CHARSET", "UTF-8"]
        args += list(criteria.terms)
        with self._guard("search"):
            if criteria.literal is not None:
                self._imap.literal = criteria.literal
            try:
                typ, data = self._imap.uid("SEARCH", *args)
            finally:
                self._imap.literal = None
        if typ != "OK":
            raise MailError("The mail search was refused by the server.", "bad_search")
        raw = data[0] if data and data[0] else b""
        return sorted(int(x) for x in bytes(raw).split() if x.isdigit())

    def summaries(self, uids: Sequence[int], *, structure: bool = False) -> list[Summary]:
        """Headers of ``uids`` in the order given (BODY.PEEK: nothing is marked read)."""
        if not uids:
            return []
        fields = "UID FLAGS RFC822.SIZE"
        if structure:
            fields += " BODYSTRUCTURE"
        fields += " BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE MESSAGE-ID)]"
        with self._guard("fetch"):
            typ, data = self._imap.uid("FETCH", ",".join(str(u) for u in uids), f"({fields})")
        if typ != "OK":
            raise MailError("The messages could not be fetched.", "fetch_failed")
        out: dict[int, Summary] = {}
        for item in parse_fetch(data or []):
            uid = meta_uid(item.meta)
            if uid is None:
                continue
            headers = parse_message(item.literal or b"", headers_only=True)
            out[uid] = Summary(
                uid=uid,
                sender=clean_header(headers.get("from")),
                to=clean_header(headers.get("to")),
                subject=clean_header(headers.get("subject")),
                date=clean_header(headers.get("date")),
                unread="\\seen" not in meta_flags(item.meta),
                size=meta_size(item.meta),
                has_attachment=(
                    bool(re.search(r'"attachment"', item.meta, re.IGNORECASE))
                    if structure
                    else None
                ),
                message_id=clean_header(headers.get("message-id")),
            )
        return [out[u] for u in uids if u in out]

    def message(self, uid: int) -> FullMessage:
        """One message, fetched with BODY.PEEK. ``message`` is None when it was not found."""
        with self._guard("fetch"):
            typ, data = self._imap.uid("FETCH", str(uid), "(UID RFC822.SIZE)")
        sizes = [meta_size(i.meta) for i in parse_fetch(data or []) if meta_uid(i.meta) == uid]
        if typ != "OK" or not sizes:
            return FullMessage(uid, None, 0)
        size = sizes[0]
        part = "BODY.PEEK[HEADER]" if size > MAX_MESSAGE_BYTES else "BODY.PEEK[]"
        with self._guard("fetch"):
            typ, data = self._imap.uid("FETCH", str(uid), f"(UID {part})")
        literal = next(
            (i.literal for i in parse_fetch(data or []) if meta_uid(i.meta) == uid and i.literal),
            None,
        )
        if typ != "OK" or literal is None:
            return FullMessage(uid, None, size)
        if size > MAX_MESSAGE_BYTES:
            return FullMessage(uid, None, size, too_large=True, headers=parse_message(literal))
        return FullMessage(uid, parse_message(literal), size)
