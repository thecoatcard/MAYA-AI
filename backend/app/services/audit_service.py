"""Tamper-evident audit writer and verifier (plan section 4.2, 11.4, 14).

Canonical row form (the exact bytes that are hashed), version 1::

    row_hash = sha256( prev_hash_bytes || canonical_json )

* ``prev_hash_bytes`` is the previous row's ``row_hash`` in the same workspace, or empty bytes
  for the first row (stored as ``prev_hash = NULL``).
* ``canonical_json`` is
  ``json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)``
  encoded as UTF-8, where ``obj`` has exactly these keys: ``workspace_id``, ``user_ref``,
  ``task_id``, ``subtask_id``, ``approval_id``, ``server_name``, ``tool_name``,
  ``permission_level``, ``input_redacted``, ``output_redacted``, ``status``, ``duration_ms`` and
  ``created_at``. UUIDs are lowercase hyphenated strings, ``created_at`` is UTC ISO-8601 with
  microseconds and ``+00:00``. Payloads are stored after redaction; floats are stored as strings
  so the JSONB round trip cannot change the hashed bytes.

The chain is per workspace. Writers are serialised per workspace with
``pg_advisory_xact_lock``, held until the caller's transaction ends.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Final, NamedTuple
from uuid import UUID

from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog
from app.memory.scrubber import PiiScrubber

logger = logging.getLogger(__name__)

INTERNAL_SERVER: Final[str] = "maya"
"""``server_name`` used for non-tool events; ``tool_name`` then carries the action
(for example ``approval.approved``, ``task.started``, ``auth.login``)."""
CATEGORIES: Final[frozenset[str]] = frozenset({"auth", "task", "tool", "approval", "security"})
AUDIT_STATUSES: Final[frozenset[str]] = frozenset(
    {
        "success",
        "failed",
        "denied",
        "pending",
        "rejected",
        "warning",
        "aborted",
        "timeout",
        "expired",
    }
)
PERMISSION_LEVELS: Final[frozenset[str]] = frozenset({"READ", "WRITE", "SENSITIVE", "RESTRICTED"})

_SECRET_KEY = re.compile(
    r"(pass(word|wd)?|secret|token|api[-_]?key|authorization|cookie|credential|private[-_]?key"
    r"|card[-_]?number|cvv|ssn)",
    re.IGNORECASE,
)
_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|api[-_]?key|access[-_]?token|token)\s*[=:]\s*[^\s,;&\"']+"
)
_API_KEY = re.compile(r"\b(?:sk|pk|rk|xox[abprs])[-_][A-Za-z0-9_\-]{16,}\b")
_REDACTED = "[REDACTED_SECRET]"
_MAX_STRING = 8192
_MAX_DEPTH = 12
_scrubber = PiiScrubber()


class AuditWriteError(RuntimeError):
    """The audit row could not be written. Security decisions must fail closed on this."""


class ChainVerification(NamedTuple):
    valid: bool
    first_bad_row_id: int | None
    count: int  # type: ignore[assignment]


def redact_text(value: str) -> str:
    """Remove secrets and PII from one string."""
    clean = _scrubber.scrub(value).clean_text
    clean = _API_KEY.sub(_REDACTED, clean)
    clean = _ASSIGNMENT.sub(lambda m: f"{m.group(1)}={_REDACTED}", clean)
    return clean[:_MAX_STRING]


def redact_payload(value: Any, _depth: int = 0) -> Any:
    """Recursively redact a JSON-like payload. Returns JSON-safe data (floats become strings)."""
    if _depth > _MAX_DEPTH:
        return "[TRUNCATED]"
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        return str(value)
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            out[name] = _REDACTED if _SECRET_KEY.search(name) else redact_payload(item, _depth + 1)
        return out
    if isinstance(value, list | tuple | set | frozenset):
        return [redact_payload(item, _depth + 1) for item in value]
    return redact_text(str(value))


def _canonical(
    *,
    workspace_id: UUID,
    user_ref: str,
    task_id: UUID | None,
    subtask_id: UUID | None,
    approval_id: UUID | None,
    server_name: str | None,
    tool_name: str | None,
    permission_level: str | None,
    input_redacted: Any,
    output_redacted: Any,
    status: str,
    duration_ms: int | None,
    created_at: datetime,
) -> bytes:
    def u(v: UUID | None) -> str | None:
        return str(v) if v is not None else None

    obj = {
        "workspace_id": str(workspace_id),
        "user_ref": user_ref,
        "task_id": u(task_id),
        "subtask_id": u(subtask_id),
        "approval_id": u(approval_id),
        "server_name": server_name,
        "tool_name": tool_name,
        "permission_level": permission_level,
        "input_redacted": input_redacted,
        "output_redacted": output_redacted,
        "status": status,
        "duration_ms": duration_ms,
        "created_at": created_at.astimezone(UTC).isoformat(timespec="microseconds"),
    }
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def compute_row_hash(prev_hash: bytes | None, canonical: bytes) -> bytes:
    return hashlib.sha256((prev_hash or b"") + canonical).digest()


def workspace_lock_key(workspace_id: UUID) -> int:
    """Stable signed 64-bit advisory-lock key for a workspace's audit chain."""
    digest = hashlib.sha256(b"maya:audit:" + workspace_id.bytes).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def pseudonymise_user_ref(user_ref: str, workspace_id: UUID) -> str:
    """Stable, non-reversible pseudonym for GDPR purge (same input gives same output)."""
    digest = hashlib.sha256(
        b"maya-audit-pseudonym-v1|" + workspace_id.bytes + b"|" + user_ref.encode()
    ).hexdigest()
    return f"pseud:{digest[:32]}"


async def record_audit(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    user_ref: str,
    status: str,
    action: str | None = None,
    server_name: str | None = None,
    tool_name: str | None = None,
    permission_level: str | None = None,
    task_id: UUID | None = None,
    subtask_id: UUID | None = None,
    approval_id: UUID | None = None,
    input: Any = None,  # noqa: A002 - mirrors the column name
    output: Any = None,
    duration_ms: int | None = None,
) -> int:
    """Append one hash-chained audit row inside the caller's tenant transaction.

    ``action`` (``category.verb``, for example ``task.started``) is for non-tool events; it is
    stored as ``tool_name`` with ``server_name='maya'``. Returns the new row id. Raises
    :class:`AuditWriteError` on any failure (after logging at ERROR); the caller decides, and
    security decisions must fail closed. The insert runs in a SAVEPOINT so a failure does not
    abort the caller's transaction.
    """
    try:
        if action is not None:
            if server_name or tool_name:
                raise ValueError("action cannot be combined with server_name/tool_name")
            server_name, tool_name = INTERNAL_SERVER, action
        if status not in AUDIT_STATUSES:
            raise ValueError(f"unknown audit status {status!r}")
        if permission_level is not None and permission_level not in PERMISSION_LEVELS:
            raise ValueError(f"unknown permission level {permission_level!r}")
        if not user_ref:
            raise ValueError("user_ref is required")
        in_red = redact_payload(input) if input is not None else None
        out_red = redact_payload(output) if output is not None else None
        if in_red is not None and not isinstance(in_red, dict):
            in_red = {"value": in_red}
        if out_red is not None and not isinstance(out_red, dict):
            out_red = {"value": out_red}
        created_at = datetime.now(UTC)
        async with session.begin_nested():
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:k)"), {"k": workspace_lock_key(workspace_id)}
            )
            prev = (
                await session.execute(
                    select(AuditLog.row_hash)
                    .where(AuditLog.workspace_id == workspace_id)
                    .order_by(AuditLog.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            prev_bytes = bytes(prev) if prev is not None else None
            canonical = _canonical(
                workspace_id=workspace_id,
                user_ref=user_ref,
                task_id=task_id,
                subtask_id=subtask_id,
                approval_id=approval_id,
                server_name=server_name,
                tool_name=tool_name,
                permission_level=permission_level,
                input_redacted=in_red,
                output_redacted=out_red,
                status=status,
                duration_ms=duration_ms,
                created_at=created_at,
            )
            row_id = (
                await session.execute(
                    insert(AuditLog)
                    .values(
                        workspace_id=workspace_id,
                        user_ref=user_ref,
                        task_id=task_id,
                        subtask_id=subtask_id,
                        approval_id=approval_id,
                        server_name=server_name,
                        tool_name=tool_name,
                        permission_level=permission_level,
                        input_redacted=in_red,
                        output_redacted=out_red,
                        status=status,
                        duration_ms=duration_ms,
                        prev_hash=prev_bytes,
                        row_hash=compute_row_hash(prev_bytes, canonical),
                        created_at=created_at,
                    )
                    .returning(AuditLog.id)
                )
            ).scalar_one()
        return int(row_id)
    except Exception as exc:
        logger.error("audit_write_failed", extra={"error_type": type(exc).__name__})
        raise AuditWriteError("audit row could not be written") from exc


def _row_canonical(row: AuditLog) -> bytes:
    return _canonical(
        workspace_id=row.workspace_id,
        user_ref=row.user_ref,
        task_id=row.task_id,
        subtask_id=row.subtask_id,
        approval_id=row.approval_id,
        server_name=row.server_name,
        tool_name=row.tool_name,
        permission_level=row.permission_level,
        input_redacted=row.input_redacted,
        output_redacted=row.output_redacted,
        status=row.status,
        duration_ms=row.duration_ms,
        created_at=row.created_at,
    )


async def verify_chain(session: AsyncSession, workspace_id: UUID) -> ChainVerification:
    """Recompute the workspace chain. Returns (valid, first_bad_row_id, count)."""
    expected_prev: bytes | None = None
    count = 0
    last_id = 0
    while True:
        rows = (
            (
                await session.execute(
                    select(AuditLog)
                    .where(AuditLog.workspace_id == workspace_id, AuditLog.id > last_id)
                    .order_by(AuditLog.id)
                    .limit(1000)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return ChainVerification(True, None, count)
        for row in rows:
            count += 1
            stored_prev = bytes(row.prev_hash) if row.prev_hash is not None else None
            ok = stored_prev == expected_prev and bytes(row.row_hash) == compute_row_hash(
                stored_prev, _row_canonical(row)
            )
            if not ok:
                return ChainVerification(False, row.id, count)
            expected_prev = bytes(row.row_hash)
            last_id = row.id


async def purge_user_ref(
    admin_session: AsyncSession, workspace_id: UUID, user_ref: str
) -> tuple[int, str]:
    """GDPR purge: pseudonymise ``user_ref`` and re-seal the workspace chain.

    Needs UPDATE on ``audit_log``, which the runtime role (maya_app) deliberately lacks, so it
    must run on the migrator/administrative connection. Returns (rows_changed, pseudonym).
    Changing a row changes its hash, so the whole workspace chain is recomputed in order.
    """
    pseud = pseudonymise_user_ref(user_ref, workspace_id)
    await admin_session.execute(
        text("SELECT pg_advisory_xact_lock(:k)"), {"k": workspace_lock_key(workspace_id)}
    )
    rows = (
        (
            await admin_session.execute(
                select(AuditLog).where(AuditLog.workspace_id == workspace_id).order_by(AuditLog.id)
            )
        )
        .scalars()
        .all()
    )
    changed = 0
    prev: bytes | None = None
    for row in rows:
        if row.user_ref == user_ref:
            row.user_ref = pseud
            changed += 1
        row.prev_hash = prev
        row.row_hash = compute_row_hash(prev, _row_canonical(row))
        prev = bytes(row.row_hash)
    await admin_session.flush()
    return changed, pseud
