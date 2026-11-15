"""Server-level connections (MAYA_GITHUB_TOKEN, MAYA_NOTION_*, MAYA_MAIL_*, Google Calendar) belong
to the deployment owner only (MAYA_ENV_CONNECTIONS_USERS). A is the owner (in the allow-list, env
tokens set); B is any other user with no connection of their own and must reach nothing."""

from __future__ import annotations

import base64
import os
import re
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents import capabilities
from app.agents.specialized.email import EmailAgent
from app.agents.specialized.pm import NO_SOURCE_CONNECTED, PMAgent
from app.api.v1.chat import get_chat_rate_limiter
from app.api.v1.connections import build_connection_service, router
from app.api.v1.errors import install_error_handlers
from app.auth.dependencies import get_principal
from app.auth.models import Principal
from app.connections.crypto import ConnectionCipher
from app.connections.ownership import DeploymentOwnership, ownership_from_settings
from app.connections.service import ConnectionService, EnvDefaults
from app.mail.config import MailboxConfig
from app.mcp.gateway import builtin_spec
from app.mcp.notion_tools import NOT_CONNECTED_MESSAGE, NotionToolError
from app.mcp.scope import tool_call_scope
from app.services.chat_repo import ChatRepoTools
from app.tasks.queue import SubtaskJob
from tests.unit.test_connections import AllowAll, MemStore

KEY = base64.urlsafe_b64encode(os.urandom(32)).decode()
GH = "ghp_" + "e" * 36
NOTION = "ntn_" + "n" * 44
DB = "0123456789abcdef0123456789abcdef"
OWNER_EMAIL = "owner@example.com"
ENV = EnvDefaults(
    github_token=GH,
    notion_token=NOTION,
    notion_database_id=DB,
    mailbox=MailboxConfig(address=OWNER_EMAIL, password="fake-env-password-12345"),
)


def make(
    allowed: set[str] | str | None = None,
    emails: dict[UUID, str] | None = None,
    store: MemStore | None = None,
    fail: bool = False,
) -> ConnectionService:
    async def lookup(ws: UUID, user: UUID) -> str | None:
        if fail:
            raise RuntimeError("db down")
        return (emails or {}).get(user)

    owner = DeploymentOwnership(lookup, allowed if allowed is not None else set())
    return ConnectionService(
        store or MemStore(), ConnectionCipher.from_setting(KEY), ENV, deployment_owner=owner
    )


class World:
    def __init__(self) -> None:
        self.ws, self.a, self.b = uuid4(), uuid4(), uuid4()
        self.svc = make({OWNER_EMAIL}, {self.a: OWNER_EMAIL, self.b: "bob@example.com"})


def job(w: World, user: UUID) -> SubtaskJob:
    return SubtaskJob(
        workspace_id=w.ws, user_id=user, task_id=uuid4(), subtask_id=uuid4(), assigned_agent="pm"
    )


# ------------------------------------------------------------------------------------ service


async def test_service_matrix_owner_gets_env_other_gets_nothing() -> None:
    w = World()
    for provider in ("github", "notion"):
        got = await w.svc.resolve(w.ws, w.a, provider)
        assert got is not None and got.source == "environment"
        assert (await w.svc.status_of(w.ws, w.a, provider)).state == "server_default"
        assert await w.svc.resolve(w.ws, w.b, provider) is None
        status = await w.svc.status_of(w.ws, w.b, provider)
        assert status.state == "not_connected" and status.source is None
    box = await w.svc.resolve_mailbox(w.ws, w.a)
    assert box is not None and box.address == OWNER_EMAIL
    assert await w.svc.resolve_mailbox(w.ws, w.b) is None
    assert await w.svc.mail_service(w.ws, w.b) is None
    assert (await w.svc.status_of(w.ws, w.b, "mail")).state == "not_connected"
    assert await w.svc.github_connector(w.ws, w.b) is None
    assert await w.svc.notion_connector(w.ws, w.b) is None


async def test_owner_can_also_be_named_by_user_id() -> None:
    user = uuid4()
    svc = make({str(user)})
    assert await svc.is_deployment_owner(uuid4(), user)


async def test_empty_allow_list_means_nobody_and_service_without_owner_too() -> None:
    svc = make(set(), {})
    ws, user = uuid4(), uuid4()
    assert await svc.resolve(ws, user, "github") is None
    bare = ConnectionService(MemStore(), ConnectionCipher.from_setting(KEY), ENV)
    assert await bare.resolve(ws, user, "github") is None
    assert await bare.resolve_mailbox(ws, user) is None
    assert bare.server_defaults_blocked and svc.server_defaults_blocked


async def test_lookup_error_fails_closed_and_is_not_cached() -> None:
    ws, user = uuid4(), uuid4()
    calls: list[int] = []
    healthy = False

    async def lookup(w: UUID, u: UUID) -> str | None:
        calls.append(1)
        if not healthy:
            raise RuntimeError("db down")
        return OWNER_EMAIL

    owner = DeploymentOwnership(lookup, {OWNER_EMAIL})
    assert await owner.is_owner(ws, user) is False
    healthy = True
    assert await owner.is_owner(ws, user) is True  # the earlier error was not cached
    assert await owner.is_owner(ws, user) is True
    assert len(calls) == 2  # the success is cached
    svc = make({OWNER_EMAIL}, {user: OWNER_EMAIL}, fail=True)
    assert await svc.resolve(ws, user, "github") is None


async def test_unreadable_saved_credential_never_falls_back_even_for_the_owner() -> None:
    w = World()
    store = MemStore()
    svc = make({OWNER_EMAIL}, {w.a: OWNER_EMAIL}, store=store)
    store.rows[(w.ws, w.a, "connection:github")] = "v1.garbage"
    assert await svc.resolve(w.ws, w.a, "github") is None
    assert (await svc.status_of(w.ws, w.a, "github")).state == "unreadable"


async def test_b_saves_own_connection_and_gets_source_user() -> None:
    w = World()
    await w.svc.save(w.ws, w.b, "github", token=GH.replace("e", "f"), database_id=None)
    got = await w.svc.resolve(w.ws, w.b, "github")
    assert got is not None and got.source == "user" and got.token != GH


def test_ownership_from_settings_parses_list() -> None:
    class S:
        env_connections_users = " Owner@Example.com , ,abc "

    owner = ownership_from_settings(S(), None)
    assert owner.configured


# ------------------------------------------------------------------------------- chat tools


async def test_chat_tools_a_connected_b_honest_not_connected_without_data() -> None:
    w = World()
    mine = ChatRepoTools(w.svc, w.ws, w.a)
    theirs = ChatRepoTools(w.svc, w.ws, w.b)
    assert await mine.available("hello") and not await theirs.available("hello")
    assert await mine.notion_available("hello") and not await theirs.notion_available("hello")
    assert await mine.mail_service() is not None and await theirs.mail_service() is None
    # Naming Notion still offers the tool; the answer is the plain message with no data.
    assert await theirs.notion_available("what is in my notion")
    with pytest.raises(NotionToolError) as err:
        await theirs.new_notion_toolkit().run("notion_list_tasks", {})
    assert str(err.value) == NOT_CONNECTED_MESSAGE
    assert NOTION not in str(err.value) and DB not in str(err.value)


# ------------------------------------------------------------------------- gateway builtin tools


async def call_tool(spec: Any, name: str, args: dict[str, Any]) -> str:
    config = await spec.build()
    transport = config.transport
    await transport.start()
    reply = await transport.send_request("tools/call", {"name": name, "arguments": args})
    return str(reply["content"][0]["text"])


async def test_gateway_tools_resolve_per_caller_and_b_gets_no_data() -> None:
    w = World()
    # With a connection service the server values are not even passed in (see main.py), and the
    # per-caller resolution is the only path.
    spec = builtin_spec(github_token=None, connections=w.svc)
    with tool_call_scope(w.ws, uuid4(), w.b):
        from app.mcp.transports.base import MCPError

        with pytest.raises(MCPError) as err:
            await call_tool(spec, "notion_list_tasks", {})
        assert "not connected" in str(err.value).lower()
        assert NOTION not in str(err.value)
    assert await w.svc.notion_connector(w.ws, w.a) is not None  # A resolves through the same path


# ------------------------------------------------------------------------------------- agents


class FakeCalendar:
    async def list_events(self, **_: Any) -> list[dict[str, Any]]:
        return [{"summary": "OWNER SECRET MEETING"}]


async def test_pm_agent_calendar_is_owner_only_with_connections() -> None:
    w = World()
    pm = PMAgent(calendar_service=FakeCalendar(), connections=w.svc)  # type: ignore[arg-type]
    assert await pm.availability_error_for(job(w, w.a)) is None
    assert await pm.availability_error_for(job(w, w.b)) == NO_SOURCE_CONNECTED
    result = await pm.execute_subtask(job(w, w.b))
    assert result.status.value.lower() == "failed"
    assert "OWNER SECRET MEETING" not in str(result.summary)
    # Without a connection service (single-user, no database) the calendar stays available.
    solo = PMAgent(calendar_service=FakeCalendar())  # type: ignore[arg-type]
    assert await solo.availability_error_for(job(w, w.b)) is None


async def test_email_agent_uses_the_callers_mailbox_only() -> None:
    w = World()
    agent = EmailAgent(connections=w.svc)
    assert await agent._mailbox_for_caller(job(w, w.a)) is not None
    assert await agent._mailbox_for_caller(job(w, w.b)) is None


async def test_coding_agent_github_resolves_per_job_user() -> None:
    w = World()
    # CodingAgent reads ``connections.github_connector(job.workspace_id, job.user_id)``.
    assert await w.svc.github_connector(w.ws, w.a) is not None
    assert await w.svc.github_connector(w.ws, w.b) is None


# ------------------------------------------------------------------------------ capabilities


async def test_capabilities_flags_per_user_and_unbound_current_claims_nothing() -> None:
    w = World()
    capabilities.reset()
    try:
        capabilities._current = capabilities.Capabilities(
            github=True, notion=True, calendar=True, mailbox=True
        )

        async def pair(ws: UUID, user: UUID) -> tuple[bool, bool]:
            return (
                await w.svc.is_connected(ws, user, "github"),
                await w.svc.is_connected(ws, user, "notion"),
            )

        async def mailbox(ws: UUID, user: UUID) -> bool:
            return await w.svc.resolve_mailbox(ws, user) is not None

        capabilities.set_user_connections(pair)
        capabilities.set_user_mailbox(mailbox)
        capabilities.set_user_calendar(w.svc.is_deployment_owner)
        a = await capabilities.for_user(w.ws, w.a)
        b = await capabilities.for_user(w.ws, w.b)
        assert (a.github, a.notion, a.calendar, a.mailbox) == (True,) * 4
        assert (b.github, b.notion, b.calendar, b.mailbox) == (False,) * 4
        unbound = capabilities.current()
        assert not (unbound.github or unbound.notion or unbound.calendar or unbound.mailbox)
    finally:
        capabilities.reset()


# --------------------------------------------------------------------------------- endpoint


def client(w: World, user: UUID) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router)
    me = Principal(user_id=user, workspace_id=w.ws, role="member", session_id=uuid4())
    app.dependency_overrides[get_principal] = lambda: me
    app.dependency_overrides[build_connection_service] = lambda: w.svc
    app.dependency_overrides[get_chat_rate_limiter] = lambda: AllowAll()
    return TestClient(app)


def test_status_endpoint_owner_sees_server_default_other_sees_not_connected() -> None:
    w = World()
    mine = {
        c["provider"]: c
        for c in client(w, w.a).get("/api/v1/settings/connections").json()["connections"]
    }
    theirs_body = client(w, w.b).get("/api/v1/settings/connections").json()
    theirs = {c["provider"]: c for c in theirs_body["connections"]}
    assert mine["github"]["state"] == "server_default"
    for item in theirs.values():
        assert item["state"] == "not_connected" and item["source"] is None
    assert theirs_body["server_defaults_blocked"] is False  # an owner is named


def test_status_endpoint_flags_blocked_when_nobody_is_named() -> None:
    w = World()
    w.svc = make(set(), {})
    body = client(w, w.a).get("/api/v1/settings/connections").json()
    assert body["server_defaults_blocked"] is True
    assert all(c["state"] == "not_connected" for c in body["connections"])


# --------------------------------------------------------------------------------- static scan

_ALLOWED = (
    "connections/",
    "mail/config.py",
    "main.py",
    "agents/capabilities.py",
    "core/config.py",
)
_READS = re.compile(
    r"\b(?:settings|configuration|config)\.(notion_token|github_token|google_access_token|"
    r"mail_app_password)\b"
)


def test_no_module_reads_server_tokens_outside_the_allow_list() -> None:
    root = Path(__file__).resolve().parents[2] / "app"
    offenders = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        if rel.startswith(_ALLOWED) or rel in _ALLOWED:
            continue
        if _READS.search(path.read_text(encoding="utf-8")):
            offenders.append(rel)
    assert offenders == [], (
        f"server-level tokens must be read only via ConnectionService: {offenders}"
    )
