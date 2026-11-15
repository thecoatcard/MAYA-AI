"""Opt-in auto-approval of a few Notion writes (default OFF, owner request).

What this module is, and is not
-------------------------------
It does NOT bypass the approval gate. When the owner turned the setting on in Settings, the
server records the approval for one exact proposal through the SAME repository call and the SAME
signed, single-use token a human click produces (``mint_approval_token`` bound to the payload
hash, ``ApprovalRepository.approve``). The worker still verifies that token against the payload
hash, claims it atomically and runs the tool, whose read-back check still runs. Change one byte of
the payload afterwards and the token no longer verifies.

Only the authenticated settings API (``app/api/v1/settings.py``) writes the setting; nothing the
model, a tool, a Notion page, an e-mail or the voice channel produces can reach it. The set of
tools that may ever be auto-approved is an allow-list of four additive tools below; everything
else (archive, delete, replace a section, comments, edits of existing blocks) is always manual
and that is not configurable.

``decision_channel``: the column's CHECK constraint only allows ``web`` and ``api`` (migration
0009), and a new value would need a migration, so an auto-approval is stored as ``api`` (the
decide route only ever writes ``web``) and carries the distinct marker ``auto_setting`` in its
audit row.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.db.models import AuditLog
from app.db.repositories.approvals import ApprovalRecord, ApprovalRepository
from app.db.repositories.user_settings import UserSettingsStore
from app.db.tenant import tenant_session
from app.mcp.tokens import mint_approval_token
from app.services.audit_service import AuditWriteError, record_audit

logger = logging.getLogger(__name__)

SETTING_KEY: Final = "notion_auto_approve"

# tool name -> scope switch. This allow-list is the ONLY thing that can ever be auto-approved.
SCOPE_BY_TOOL: Final[dict[str, str]] = {
    "notion_create_page": "create_page",
    "notion_add_row": "add_row",
    "notion_append_blocks": "append_blocks",
    "notion_update_row": "update_row",
}
SCOPES: Final[tuple[str, ...]] = tuple(SCOPE_BY_TOOL.values())

# Never auto-approvable, whatever the setting says (hard-coded on purpose; the test suite pins it).
ALWAYS_MANUAL_TOOLS: Final[frozenset[str]] = frozenset(
    {
        "notion_archive_page",
        "notion_delete_block",
        "notion_replace_section",
        "notion_update_page",
        "notion_update_block",
        "notion_add_comment",
        # A whole book page from a finished job (ADR-0031): always read by a human first.
        "notion_create_page_from_job",
    }
)
ALWAYS_MANUAL_LABELS: Final[tuple[str, ...]] = (
    "Moving a page to the trash (archive)",
    "Deleting a block",
    "Replacing a section of a page",
    "Editing the text of an existing page or block",
    "Comments",
    "Large pages (over 300 blocks), such as a whole study book written by a job",
    "Anything outside your own Notion connection",
)
LARGE_WRITE_BLOCKS: Final = 300
"""A write of more blocks than this is never auto-approved, whatever the scope says."""

HOURLY_CAP: Final = 30
DAILY_CAP: Final = 200

AUDIT_APPROVED: Final = "approval.auto_approved"
AUDIT_DECLINED: Final = "approval.auto_declined"
CHANNEL_MARKER: Final = "auto_setting"
STORED_CHANNEL: Final = "api"
TOKEN_TTL_SECONDS: Final = 300


def block_count(args: dict[str, Any]) -> int:
    """The blocks a payload would write: ``blocks`` (a job-built page) or the children of its
    requests. Unknown shapes count as 0 (the payload validation then decides)."""
    declared = args.get("blocks")
    if isinstance(declared, int) and not isinstance(declared, bool):
        return declared
    total = 0
    for request in args.get("requests") or []:
        body = request.get("body") if isinstance(request, dict) else None
        children = body.get("children") if isinstance(body, dict) else None
        total += len(children) if isinstance(children, list) else 0
    return total


@dataclass(frozen=True, slots=True)
class NotionAutoApproveSetting:
    enabled: bool = False
    scopes: frozenset[str] = frozenset()
    version: int = 0

    def allows(self, tool: str) -> bool:
        scope = SCOPE_BY_TOOL.get(tool)
        return (
            self.enabled
            and scope is not None
            and tool not in ALWAYS_MANUAL_TOOLS
            and scope in self.scopes
        )


class AutoApproveConfirmationRequired(ValueError):
    """Turning the master switch on needs the explicit confirmation from the Settings dialog."""


class AutoApproveMasterOff(ValueError):
    """A scope can only be switched on while the master switch is on."""


def parse_setting(raw: str | None) -> NotionAutoApproveSetting:
    """The stored setting; anything missing, malformed or not exactly ``true`` means OFF."""
    if not raw:
        return NotionAutoApproveSetting()
    try:
        data = json.loads(raw)
    except ValueError:
        return NotionAutoApproveSetting()
    if not isinstance(data, dict) or data.get("enabled") is not True:
        version = data.get("version") if isinstance(data, dict) else 0
        return NotionAutoApproveSetting(version=version if isinstance(version, int) else 0)
    stored = data.get("scopes")
    scopes = (
        frozenset(s for s in SCOPES if isinstance(stored, dict) and stored.get(s) is True)
        if isinstance(stored, dict)
        else frozenset()
    )
    version = data.get("version")
    return NotionAutoApproveSetting(True, scopes, version if isinstance(version, int) else 1)


def apply_update(
    current: NotionAutoApproveSetting,
    *,
    enabled: bool | None,
    scopes: dict[str, bool] | None,
    confirm: bool,
) -> NotionAutoApproveSetting:
    """The setting after an update from the Settings API. Pure; raises on an unsafe request."""
    new_enabled = current.enabled if enabled is None else enabled
    if new_enabled and not current.enabled and confirm is not True:
        raise AutoApproveConfirmationRequired("Confirm what auto-approval does before enabling it.")
    # Turning the master on never switches a scope on by itself: the owner picks each one.
    new_scopes = set(current.scopes) if (new_enabled and current.enabled) else set()
    for name, value in (scopes or {}).items():
        if name not in SCOPES:
            raise ValueError(f"unknown scope {name!r}")
        if value and not new_enabled:
            raise AutoApproveMasterOff("Turn on auto-approval first, then choose what it covers.")
        if value:
            new_scopes.add(name)
        else:
            new_scopes.discard(name)
    result = NotionAutoApproveSetting(new_enabled, frozenset(new_scopes), current.version + 1)
    if (result.enabled, result.scopes) == (current.enabled, current.scopes):
        return current
    return result


def serialize_setting(setting: NotionAutoApproveSetting) -> str:
    return json.dumps(
        {
            "enabled": setting.enabled,
            "scopes": {name: name in setting.scopes for name in SCOPES},
            "version": setting.version,
        },
        sort_keys=True,
    )


async def load_setting(
    store: UserSettingsStore, workspace_id: UUID, user_id: UUID
) -> NotionAutoApproveSetting:
    return parse_setting(await store.get_text(workspace_id, user_id, SETTING_KEY))


async def save_setting(
    store: UserSettingsStore, workspace_id: UUID, user_id: UUID, setting: NotionAutoApproveSetting
) -> None:
    await store.set_text(workspace_id, user_id, SETTING_KEY, serialize_setting(setting))


@dataclass(frozen=True, slots=True)
class Usage:
    last_hour: int
    last_day: int
    last_at: datetime | None
    last_tool: str | None


async def _count_since(
    session: AsyncSession, workspace_id: UUID, user_id: UUID, since: datetime
) -> int:
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.workspace_id == workspace_id,
                    AuditLog.user_ref == str(user_id),
                    AuditLog.server_name == "maya",
                    AuditLog.tool_name == AUDIT_APPROVED,
                    AuditLog.created_at >= since,
                )
            )
        ).scalar_one()
    )


async def usage_in_session(
    session: AsyncSession, workspace_id: UUID, user_id: UUID, now: datetime | None = None
) -> Usage:
    current = now or datetime.now(UTC)
    hour = await _count_since(session, workspace_id, user_id, current - timedelta(hours=1))
    day = await _count_since(session, workspace_id, user_id, current - timedelta(days=1))
    last = (
        await session.execute(
            select(AuditLog.created_at, AuditLog.input_redacted)
            .where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.user_ref == str(user_id),
                AuditLog.server_name == "maya",
                AuditLog.tool_name == AUDIT_APPROVED,
            )
            .order_by(AuditLog.id.desc())
            .limit(1)
        )
    ).first()
    tool = None
    if last is not None and isinstance(last.input_redacted, dict):
        raw_tool = last.input_redacted.get("tool")
        tool = raw_tool if isinstance(raw_tool, str) else None
    return Usage(hour, day, last.created_at if last is not None else None, tool)


async def load_usage(engine: AsyncEngine, workspace_id: UUID, user_id: UUID) -> Usage:
    async with tenant_session(engine, workspace_id, user_id) as session:
        return await usage_in_session(session, workspace_id, user_id)


@dataclass(frozen=True, slots=True)
class AutoPlan:
    """What the approver decided for one proposal before its approval request is created."""

    allowed: bool
    setting_version: int = 0
    scope: str = ""
    note: str = ""
    info: str = ""  # shown on the card only; unlike ``note`` it does not record a decline


MANUAL = AutoPlan(False)
BOOK_TOOL: Final = "notion_create_page_from_job"


def large_write_info(blocks: int) -> str:
    """The plain line on the approval card when auto-approve is on but a large page is manual."""
    return f"Large page ({blocks} blocks): auto-approve does not apply, please review."


class NotionAutoApprover:
    """Decides, at proposal time, whether the owner's setting covers a Notion write, and records
    the approval through the same repository and token as a human decision."""

    def __init__(self, store: UserSettingsStore, signing_key: str | bytes) -> None:
        self._store = store
        self._key = signing_key

    async def plan(
        self,
        session: AsyncSession,
        *,
        workspace_id: UUID,
        user_id: UUID,
        tool: str,
        args: dict[str, Any],
    ) -> AutoPlan:
        """``MANUAL`` unless master and scope allow, the payload is a valid write for ``tool``
        and the rolling caps have room. A cap fallback carries a plain note for the card."""
        blocks = block_count(args)
        if tool == BOOK_TOOL or (tool in SCOPE_BY_TOOL and blocks > LARGE_WRITE_BLOCKS):
            # A large page (a whole book) is always read by a human first: that rule stays. When
            # the owner has auto-approve on, the card says plainly why it is not used here.
            try:
                master_on = (await load_setting(self._store, workspace_id, user_id)).enabled
            except Exception:  # noqa: BLE001 - no note, still manual
                master_on = False
            return AutoPlan(False, info=large_write_info(blocks)) if master_on else MANUAL
        if tool not in SCOPE_BY_TOOL or tool in ALWAYS_MANUAL_TOOLS:
            return MANUAL
        try:
            setting = await load_setting(self._store, workspace_id, user_id)
        except Exception:  # noqa: BLE001 - an unreadable setting means manual approval
            logger.warning("notion_auto_approve_setting_unreadable")
            return MANUAL
        if not setting.allows(tool):
            return MANUAL
        from app.mcp.notion_write import NotionWriteError, validate_requests

        try:
            validate_requests(tool, args.get("requests"))
        except NotionWriteError:
            return MANUAL
        use = await usage_in_session(session, workspace_id, user_id)
        if use.last_hour >= HOURLY_CAP or use.last_day >= DAILY_CAP:
            which = (
                f"{HOURLY_CAP} per hour" if use.last_hour >= HOURLY_CAP else f"{DAILY_CAP} per day"
            )
            return AutoPlan(
                False,
                note=(
                    f"Auto-approve is paused for now: you reached the limit of {which}. "
                    "Please approve this one yourself."
                ),
            )
        return AutoPlan(True, setting.version, SCOPE_BY_TOOL[tool])

    async def approve(
        self,
        session: AsyncSession,
        repo: ApprovalRepository,
        record: ApprovalRecord,
        plan: AutoPlan,
        *,
        workspace_id: UUID,
        user_id: UUID,
    ) -> str | None:
        """Approve ``record`` on the owner's behalf; return the token, or ``None`` (the request
        then simply stays pending for a human). The approval and its audit row are one unit."""
        now = datetime.now(UTC)
        try:
            async with session.begin_nested():
                token, jti = mint_approval_token(
                    self._key,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    request_id=record.id,
                    tool_name=record.tool_name,
                    payload_hash=record.payload_hash,
                    ttl_seconds=TOKEN_TTL_SECONDS,
                    now=now,
                )
                expires = await repo.approve(
                    record.id,
                    approval_token=token,
                    token_jti=jti,
                    channel=STORED_CHANNEL,
                    now=now,
                )
                if expires is None:
                    return None
                await record_audit(
                    session,
                    workspace_id=workspace_id,
                    user_ref=str(user_id),
                    status="success",
                    action=AUDIT_APPROVED,
                    task_id=record.task_id,
                    subtask_id=record.subtask_id,
                    approval_id=record.id,
                    input={
                        "tool": record.tool_name,
                        "scope": plan.scope,
                        "payload_hash": record.payload_hash,
                        "setting_version": plan.setting_version,
                        "decision_channel": CHANNEL_MARKER,
                        "stored_channel": STORED_CHANNEL,
                    },
                )
        except AuditWriteError:
            logger.error("notion_auto_approve_audit_failed")
            return None
        return token

    async def record_declined(
        self, session: AsyncSession, record: ApprovalRecord, *, workspace_id: UUID, user_id: UUID
    ) -> None:
        """Audit a cap fallback (best effort; the request is pending for a human either way)."""
        try:
            await record_audit(
                session,
                workspace_id=workspace_id,
                user_ref=str(user_id),
                status="warning",
                action=AUDIT_DECLINED,
                task_id=record.task_id,
                subtask_id=record.subtask_id,
                approval_id=record.id,
                input={"tool": record.tool_name, "reason": "rate_cap"},
            )
        except AuditWriteError:
            logger.error("notion_auto_approve_decline_audit_failed")
