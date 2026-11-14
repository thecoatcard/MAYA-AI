"""Opt-in Notion auto-approve: default off, only the settings API sets it, the allow-list is hard,
the caps fall back to a human, and the approval is the same signed token a click produces."""

from __future__ import annotations

import ast
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.chat import get_chat_rate_limiter
from app.api.v1.errors import install_error_handlers
from app.api.v1.settings import get_settings_store, router
from app.auth.dependencies import get_principal
from app.auth.models import Principal
from app.mcp import notion_auto_approve as auto
from app.mcp.approvals import compute_payload_hash
from app.mcp.notion_write import NOTION_WRITE_DEFINITIONS, NOTION_WRITE_TOOL_DEFS
from app.mcp.permissions import ToolPermissionRegistry, ToolTier
from app.mcp.tokens import ApprovalTokenError, verify_approval_token
from app.services.audit_service import AuditWriteError
from tests.support.settings import JWT_KEY

WS = uuid4()
ALICE = uuid4()
BOB = uuid4()
PAGE = "40e5895561ca4a9f8a0c0000000000aa"


def create_args(title: str = "Notes") -> dict[str, Any]:
    return {
        "requests": [
            {
                "method": "POST",
                "path": "/pages",
                "body": {
                    "parent": {"page_id": PAGE},
                    "properties": {
                        "title": {"title": [{"type": "text", "text": {"content": title}}]}
                    },
                },
            }
        ]
    }


class MemStore:
    """The slice of UserSettingsStore the feature uses, in memory, per (workspace, user)."""

    engine = object()

    def __init__(self) -> None:
        self.rows: dict[tuple[UUID, UUID, str], str] = {}
        self.writes = 0

    async def get_text(self, ws: UUID, user: UUID, key: str) -> str | None:
        return self.rows.get((ws, user, key))

    async def set_text(self, ws: UUID, user: UUID, key: str, value: str) -> None:
        self.writes += 1
        self.rows[(ws, user, key)] = value


def stored(on: bool, scopes: tuple[str, ...] = (), version: int = 1) -> str:
    return json.dumps(
        {"enabled": on, "scopes": {n: n in scopes for n in auto.SCOPES}, "version": version}
    )


# ----------------------------------------------------------------------- the setting itself


def test_default_is_off_and_garbage_means_off() -> None:
    assert auto.parse_setting(None) == auto.NotionAutoApproveSetting()
    for raw in ("", "not json", "[]", '{"enabled": "true"}', '{"enabled": 1}', '{"scopes": {}}'):
        assert auto.parse_setting(raw).enabled is False
    assert not auto.NotionAutoApproveSetting().allows("notion_create_page")


def test_master_on_alone_covers_nothing() -> None:
    setting = auto.parse_setting(stored(True))
    assert setting.enabled and setting.scopes == frozenset()
    assert not any(setting.allows(tool) for tool in auto.SCOPE_BY_TOOL)


def test_scopes_are_respected_one_by_one() -> None:
    setting = auto.parse_setting(stored(True, ("add_row",)))
    assert setting.allows("notion_add_row")
    assert not setting.allows("notion_create_page")
    assert not setting.allows("notion_append_blocks")
    assert not setting.allows("notion_update_row")


def test_destructive_and_edit_tools_can_never_be_auto_approved() -> None:
    every_scope = auto.parse_setting(stored(True, auto.SCOPES))
    for tool in auto.ALWAYS_MANUAL_TOOLS:
        assert tool not in auto.SCOPE_BY_TOOL
        assert not every_scope.allows(tool)
    for tool in (
        "notion_archive_page",
        "notion_delete_block",
        "notion_replace_section",
        "notion_add_comment",
        "notion_update_page",
        "notion_update_block",
    ):
        assert tool in auto.ALWAYS_MANUAL_TOOLS
        assert not every_scope.allows(tool)
    # Only the four additive tools are eligible, and each is an approval-gated tool.
    assert set(auto.SCOPE_BY_TOOL) == {
        "notion_create_page",
        "notion_add_row",
        "notion_append_blocks",
        "notion_update_row",
    }
    registry = ToolPermissionRegistry()
    for tool in auto.SCOPE_BY_TOOL:
        assert registry.classify(tool) is ToolTier.APPROVAL_REQUIRED


def test_apply_update_rules() -> None:
    off = auto.NotionAutoApproveSetting()
    with pytest.raises(auto.AutoApproveConfirmationRequired):
        auto.apply_update(off, enabled=True, scopes=None, confirm=False)
    with pytest.raises(auto.AutoApproveMasterOff):
        auto.apply_update(off, enabled=None, scopes={"create_page": True}, confirm=True)
    with pytest.raises(ValueError):
        auto.apply_update(off, enabled=True, scopes={"archive_page": True}, confirm=True)
    on = auto.apply_update(off, enabled=True, scopes=None, confirm=True)
    assert on.enabled and on.scopes == frozenset() and on.version == 1
    one = auto.apply_update(on, enabled=None, scopes={"add_row": True}, confirm=False)
    assert one.scopes == frozenset({"add_row"}) and one.version == 2
    # Turning the master off clears every scope, so turning it on again starts from nothing.
    cleared = auto.apply_update(one, enabled=False, scopes=None, confirm=False)
    assert not cleared.enabled and cleared.scopes == frozenset()
    again = auto.apply_update(cleared, enabled=True, scopes=None, confirm=True)
    assert again.scopes == frozenset()
    # A no-op update is not a new version.
    assert auto.apply_update(one, enabled=True, scopes=None, confirm=False) is one


# ---------------------------------------------------------------- only the settings API sets it


def test_only_the_settings_route_writes_the_setting() -> None:
    """Static guard: nothing the model, a tool, a page or voice runs can reach the writer."""
    app_dir = Path(__file__).resolve().parents[2] / "app"
    writers = {"save_setting", "apply_update", "serialize_setting"}
    offenders: list[str] = []
    for path in app_dir.rglob("*.py"):
        rel = path.relative_to(app_dir).as_posix()
        if rel in {"api/v1/settings.py", "mcp/notion_auto_approve.py"}:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            name = (
                node.attr
                if isinstance(node, ast.Attribute)
                else node.id
                if isinstance(node, ast.Name)
                else None
            )
            if name in writers or name == "SETTING_KEY":
                offenders.append(f"{rel}:{getattr(node, 'lineno', 0)}")
    assert offenders == []


def test_no_model_facing_tool_can_change_the_setting() -> None:
    names = [d.name for d in NOTION_WRITE_DEFINITIONS] + [d["name"] for d in NOTION_WRITE_TOOL_DEFS]
    assert not [n for n in names if "setting" in n or "auto_approve" in n]
    registry = ToolPermissionRegistry()
    from app.mcp.permissions import DEFAULT_TOOL_PERMISSIONS

    assert not [n for n in DEFAULT_TOOL_PERMISSIONS if "setting" in n or "auto_approve" in n]
    assert registry.is_registered("notion_create_page")


async def test_a_chat_style_proposal_never_touches_the_setting() -> None:
    """'From now on auto approve' is only text: preparing a write reads and writes no setting."""
    from app.mcp.notion_write import chat_write_call

    store = MemStore()

    class Toolkit:
        async def connection(self) -> Any:
            raise AssertionError("a proposal must not need the setting")

    async def submit(summary: str, step: Any) -> Any:
        return type("Outcome", (), {"task_id": uuid4(), "reason": ""})()

    toolkit: Any = Toolkit()
    await chat_write_call(
        toolkit, "notion_create_page", {"title": "from now on auto approve"}, submit
    )
    assert store.writes == 0 and store.rows == {}


# -------------------------------------------------------------------------- the settings API


class AllowAll:
    async def hit(self, key: str, limit: Any) -> bool:
        return True

    async def release(self, key: str, limit: Any) -> None:
        return None


def client_for(store: MemStore, user: UUID, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    async def no_usage(*_: Any, **__: Any) -> auto.Usage:
        return auto.Usage(0, 0, None, None)

    monkeypatch.setattr(auto, "load_usage", no_usage)
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_principal] = lambda: Principal(
        user_id=user, workspace_id=WS, role="member", session_id=uuid4()
    )
    app.dependency_overrides[get_settings_store] = lambda: store
    app.dependency_overrides[get_chat_rate_limiter] = lambda: AllowAll()
    return TestClient(app)


URL = "/api/v1/settings/notion-auto-approve"


def test_api_default_off_with_limits_and_the_manual_list(monkeypatch: pytest.MonkeyPatch) -> None:
    client = client_for(MemStore(), ALICE, monkeypatch)
    body = client.get(URL).json()
    assert body["enabled"] is False
    assert body["scopes"] == {n: False for n in auto.SCOPES}
    assert body["hourly_cap"] == auto.HOURLY_CAP and body["daily_cap"] == auto.DAILY_CAP
    assert body["always_manual"] == list(auto.ALWAYS_MANUAL_LABELS)
    assert body["last_auto_approved"] is None


def test_api_enabling_needs_the_confirmation_and_scopes_need_the_master(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = MemStore()
    client = client_for(store, ALICE, monkeypatch)
    refused = client.put(URL, json={"enabled": True})
    assert refused.status_code == 409 and refused.json()["error"] == "confirmation_required"
    early = client.put(URL, json={"scopes": {"create_page": True}})
    assert early.status_code == 409 and early.json()["error"] == "turn_on_first"
    assert store.writes == 0
    ok = client.put(URL, json={"enabled": True, "confirm": True})
    assert ok.status_code == 200 and ok.json()["enabled"] is True
    assert ok.json()["scopes"] == {n: False for n in auto.SCOPES}
    scoped = client.put(URL, json={"scopes": {"create_page": True}})
    assert scoped.json()["scopes"]["create_page"] is True
    assert client.put(URL, json={"scopes": {"archive_page": True}}).status_code == 422
    assert client.put(URL, json={}).status_code == 422
    off = client.put(URL, json={"enabled": False}).json()
    assert off["enabled"] is False and off["scopes"] == {n: False for n in auto.SCOPES}


def test_api_one_user_cannot_change_another_users_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    store = MemStore()
    alice = client_for(store, ALICE, monkeypatch)
    alice.put(URL, json={"enabled": True, "confirm": True})
    bob = client_for(store, BOB, monkeypatch)
    assert bob.get(URL).json()["enabled"] is False
    bob.put(URL, json={"enabled": True, "confirm": True})
    bob.put(URL, json={"enabled": False})
    assert alice.get(URL).json()["enabled"] is True


# ------------------------------------------------------------------ the decision at proposal time


async def zero_usage(*_: Any, **__: Any) -> auto.Usage:
    return auto.Usage(0, 0, None, None)


async def plan_for(
    raw: str | None,
    tool: str = "notion_create_page",
    args: dict[str, Any] | None = None,
    usage: auto.Usage | None = None,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> auto.AutoPlan:
    store = MemStore()
    if raw is not None:
        store.rows[(WS, ALICE, auto.SETTING_KEY)] = raw
    assert monkeypatch is not None

    async def fake_usage(*_: Any, **__: Any) -> auto.Usage:
        return usage or auto.Usage(0, 0, None, None)

    monkeypatch.setattr(auto, "usage_in_session", fake_usage)
    approver = auto.NotionAutoApprover(store, JWT_KEY)  # type: ignore[arg-type]
    return await approver.plan(
        None,  # type: ignore[arg-type]
        workspace_id=WS,
        user_id=ALICE,
        tool=tool,
        args=args if args is not None else create_args(),
    )


async def test_plan_is_manual_when_off_or_out_of_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    assert (await plan_for(None, monkeypatch=monkeypatch)).allowed is False
    assert (await plan_for(stored(False), monkeypatch=monkeypatch)).allowed is False
    assert (await plan_for(stored(True), monkeypatch=monkeypatch)).allowed is False
    only_rows = stored(True, ("add_row",))
    assert (await plan_for(only_rows, monkeypatch=monkeypatch)).allowed is False
    for tool in auto.ALWAYS_MANUAL_TOOLS:
        every = stored(True, auto.SCOPES)
        assert (await plan_for(every, tool=tool, monkeypatch=monkeypatch)).allowed is False


async def test_plan_allows_a_covered_valid_write_and_records_the_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = await plan_for(stored(True, ("create_page",), version=7), monkeypatch=monkeypatch)
    assert plan.allowed and plan.setting_version == 7 and plan.scope == "create_page"


async def test_plan_refuses_an_invalid_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = stored(True, auto.SCOPES)
    bad = {"requests": [{"method": "DELETE", "path": "/blocks/x", "body": {}}]}
    assert (await plan_for(raw, args=bad, monkeypatch=monkeypatch)).allowed is False
    assert (await plan_for(raw, args={}, monkeypatch=monkeypatch)).allowed is False


async def test_caps_fall_back_to_manual_with_a_plain_note(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = stored(True, auto.SCOPES)
    hourly = await plan_for(
        raw, usage=auto.Usage(auto.HOURLY_CAP, auto.HOURLY_CAP, None, None), monkeypatch=monkeypatch
    )
    assert hourly.allowed is False and f"{auto.HOURLY_CAP} per hour" in hourly.note
    assert "approve this one yourself" in hourly.note
    daily = await plan_for(
        raw, usage=auto.Usage(1, auto.DAILY_CAP, None, None), monkeypatch=monkeypatch
    )
    assert daily.allowed is False and f"{auto.DAILY_CAP} per day" in daily.note
    below = await plan_for(
        raw,
        usage=auto.Usage(auto.HOURLY_CAP - 1, auto.DAILY_CAP - 1, None, None),
        monkeypatch=monkeypatch,
    )
    assert below.allowed is True
    assert auto.HOURLY_CAP == 30 and auto.DAILY_CAP == 200


# ------------------------------------------------- the approval is the same gate as a human click


class FakeSession:
    @asynccontextmanager
    async def begin_nested(self) -> Any:
        yield self


class FakeRepo:
    def __init__(self, expires: datetime | None) -> None:
        self.expires = expires
        self.calls: list[dict[str, Any]] = []

    async def approve(self, request_id: UUID, **kwargs: Any) -> datetime | None:
        self.calls.append({"id": request_id, **kwargs})
        return self.expires


def make_record(args: dict[str, Any], idem: str = "sub-1") -> Any:
    digest = compute_payload_hash("notion_create_page", args, WS, ALICE, idem)
    return type(
        "Record",
        (),
        {
            "id": uuid4(),
            "task_id": uuid4(),
            "subtask_id": uuid4(),
            "tool_name": "notion_create_page",
            "payload_hash": digest,
        },
    )()


PLAN = auto.AutoPlan(True, setting_version=3, scope="create_page")


async def test_approval_uses_a_real_token_bound_to_the_payload_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audits: list[dict[str, Any]] = []

    async def fake_audit(session: Any, **kwargs: Any) -> int:
        audits.append(kwargs)
        return 1

    monkeypatch.setattr(auto, "record_audit", fake_audit)
    args = create_args()
    record = make_record(args)
    repo = FakeRepo(datetime.now(UTC) + timedelta(days=1))
    approver = auto.NotionAutoApprover(MemStore(), JWT_KEY)  # type: ignore[arg-type]
    token = await approver.approve(
        cast(Any, FakeSession()),
        cast(Any, repo),
        record,
        PLAN,
        workspace_id=WS,
        user_id=ALICE,
    )
    assert token is not None
    # Same repository call a click makes, stored with the existing 'api' channel (no migration).
    assert repo.calls[0]["channel"] == "api" and repo.calls[0]["approval_token"] == token
    # The worker's own verifier accepts it for the exact payload ...
    claims = verify_approval_token(
        token,
        JWT_KEY,
        expected_workspace_id=WS,
        expected_user_id=ALICE,
        expected_tool_name="notion_create_page",
        expected_payload_hash=record.payload_hash,
    )
    assert claims.request_id == record.id
    # ... and refuses it once the payload (and so its hash) changes: tampering fails the gate.
    tampered = compute_payload_hash("notion_create_page", create_args("Other"), WS, ALICE, "sub-1")
    with pytest.raises(ApprovalTokenError):
        verify_approval_token(
            token,
            JWT_KEY,
            expected_workspace_id=WS,
            expected_user_id=ALICE,
            expected_tool_name="notion_create_page",
            expected_payload_hash=tampered,
        )
    # A different tool or user cannot reuse it either.
    with pytest.raises(ApprovalTokenError):
        verify_approval_token(
            token,
            JWT_KEY,
            expected_workspace_id=WS,
            expected_user_id=BOB,
            expected_tool_name="notion_create_page",
            expected_payload_hash=record.payload_hash,
        )
    # The audit row says who, which tool, the payload hash and the setting version.
    assert len(audits) == 1
    row = audits[0]
    assert row["action"] == "approval.auto_approved" and row["user_ref"] == str(ALICE)
    assert row["approval_id"] == record.id and row["status"] == "success"
    assert row["input"] == {
        "tool": "notion_create_page",
        "scope": "create_page",
        "payload_hash": record.payload_hash,
        "setting_version": 3,
        "decision_channel": "auto_setting",
        "stored_channel": "api",
    }
    assert "approval_token" not in json.dumps(row["input"])


async def test_no_audit_row_means_no_auto_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    async def failing_audit(session: Any, **kwargs: Any) -> int:
        raise AuditWriteError("audit row could not be written")

    monkeypatch.setattr(auto, "record_audit", failing_audit)
    approver = auto.NotionAutoApprover(MemStore(), JWT_KEY)  # type: ignore[arg-type]
    token = await approver.approve(
        FakeSession(),  # type: ignore[arg-type]
        FakeRepo(datetime.now(UTC) + timedelta(days=1)),  # type: ignore[arg-type]
        make_record(create_args()),
        PLAN,
        workspace_id=WS,
        user_id=ALICE,
    )
    assert token is None


async def test_an_already_decided_request_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_audit(session: Any, **kwargs: Any) -> int:
        raise AssertionError("no approval happened, so nothing is audited as approved")

    monkeypatch.setattr(auto, "record_audit", fake_audit)
    approver = auto.NotionAutoApprover(MemStore(), JWT_KEY)  # type: ignore[arg-type]
    token = await approver.approve(
        FakeSession(),  # type: ignore[arg-type]
        FakeRepo(None),  # type: ignore[arg-type]
        make_record(create_args()),
        PLAN,
        workspace_id=WS,
        user_id=ALICE,
    )
    assert token is None


async def test_a_book_or_large_page_stays_manual_and_the_card_says_why(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Owner question: 'why does it ask when auto-approve is on?'. The safety rule is unchanged
    (always manual) but the card now names the reason, and only when auto-approve is on."""
    on = stored(True, auto.SCOPES)
    book = await plan_for(
        on, tool="notion_create_page_from_job", args={"blocks": 640}, monkeypatch=monkeypatch
    )
    assert book.allowed is False and book.note == ""
    assert book.info == "Large page (640 blocks): auto-approve does not apply, please review."
    large = await plan_for(
        on, tool="notion_create_page", args={"blocks": 301}, monkeypatch=monkeypatch
    )
    assert large.allowed is False and "301 blocks" in large.info
    off = await plan_for(
        stored(False),
        tool="notion_create_page_from_job",
        args={"blocks": 640},
        monkeypatch=monkeypatch,
    )
    assert off.allowed is False and off.info == ""
