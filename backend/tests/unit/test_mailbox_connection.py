"""The mailbox as a per-user connection: sealed storage, resolution order, isolation, the test
endpoint (fake IMAP/SMTP, nothing is sent) and that the password never leaves."""

from __future__ import annotations

import base64
import imaplib
import logging
import os
import smtplib
from collections.abc import Iterator
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.agents import capabilities
from app.agents.schemas import AgentExecutionStatus
from app.agents.specialized.email import EmailAgent
from app.connections.crypto import ConnectionCipher
from app.connections.ownership import DeploymentOwnership
from app.connections.providers import ConnectionInputError
from app.connections.service import ConnectionService, EnvDefaults
from app.mail.config import MailboxConfig
from app.mcp.client_manager import MCPClientManager
from app.mcp.gateway import McpGateway, builtin_spec
from app.mcp.permissions import ToolPermissionRegistry
from app.mcp.scope import tool_call_scope
from app.tasks.queue import SubtaskJob
from tests.support.mail_fakes import (
    ADDRESS,
    APP_PASSWORD,
    FakeMailServer,
    install,
    standard_mailbox,
)
from tests.unit.test_connections import MemStore, make_client, principal

KEY = base64.urlsafe_b64encode(os.urandom(32)).decode()
URL = "/api/v1/settings/connections/mail"
GROUPED = "fake appp assw ord0"  # how Google shows the app password; spaces are removed on save
OTHER_ADDRESS = "bob@example.com"


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeMailServer]:
    fake = standard_mailbox()
    install(monkeypatch, fake)
    yield fake


def svc(
    store: MemStore | None = None, env: EnvDefaults | None = None, owner: UUID | None = None
) -> ConnectionService:
    return ConnectionService(
        store or MemStore(),
        ConnectionCipher.from_setting(KEY),
        env,
        deployment_owner=DeploymentOwnership(None, {str(owner)}) if owner else None,
    )


async def save_alice(service: ConnectionService, ws: UUID, user: UUID, **kw: Any) -> Any:
    args: dict[str, Any] = {
        "address": ADDRESS,
        "app_password": GROUPED,
        "imap_host": None,
        "imap_port": None,
        "smtp_host": None,
        "smtp_port": None,
    }
    args.update(kw)
    return await service.save_mailbox(ws, user, **args)


# ----------------------------------------------------------------------------- storage


async def test_the_mailbox_is_sealed_as_one_document_and_status_has_no_password() -> None:
    store = MemStore()
    service = svc(store)
    ws, user = uuid4(), uuid4()
    status = await save_alice(service, ws, user)
    raw = store.rows[(ws, user, "connection:mail")]
    # At rest: neither the password nor the address nor a host is readable in the stored value.
    for secret in (APP_PASSWORD, GROUPED, ADDRESS, "gmail.com"):
        assert secret not in raw
    assert raw.startswith("v1.")
    assert APP_PASSWORD not in repr(status) and ADDRESS not in repr(status)
    assert status.mailbox is not None and status.mailbox["password_saved"] is True
    assert status.mailbox["address"] == "m***@gmail.com"
    box = await service.resolve_mailbox(ws, user)
    assert box is not None and box.password == APP_PASSWORD  # spaces removed
    assert APP_PASSWORD not in repr(box)


async def test_a_sealed_value_copied_to_another_user_does_not_decrypt() -> None:
    store = MemStore()
    service = svc(store)
    ws, alice, bob = uuid4(), uuid4(), uuid4()
    await save_alice(service, ws, alice)
    store.rows[(ws, bob, "connection:mail")] = store.rows[(ws, alice, "connection:mail")]
    assert await service.resolve_mailbox(ws, bob) is None
    assert (await service.status_of(ws, bob, "mail")).state == "unreadable"


async def test_editing_hosts_keeps_the_saved_password_and_disconnect_deletes_it() -> None:
    store = MemStore()
    service = svc(store)
    ws, user = uuid4(), uuid4()
    await save_alice(service, ws, user)
    await service.save_mailbox(
        ws,
        user,
        address=None,
        app_password=None,
        imap_host="imap.gmail.com",
        imap_port=993,
        smtp_host="smtp.gmail.com",
        smtp_port=587,
    )
    box = await service.resolve_mailbox(ws, user)
    assert box is not None and box.password == APP_PASSWORD and box.smtp_port == 587
    await service.disconnect(ws, user, "mail")
    assert (ws, user, "connection:mail") not in store.rows
    assert await service.resolve_mailbox(ws, user) is None


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"address": "not-an-address"}, "address_format"),
        ({"app_password": ""}, "password_missing"),
        ({"imap_host": "localhost"}, "host_format"),
        ({"imap_host": "10.0.0.5"}, "host_forbidden"),
        ({"smtp_host": "127.0.0.1"}, "host_forbidden"),
        ({"smtp_host": "mail.corp.internal"}, "host_format"),
        ({"imap_port": 143}, "port_format"),
        ({"smtp_port": 25}, "port_format"),
    ],
)
async def test_bad_input_is_rejected_with_a_reason(override: dict[str, Any], code: str) -> None:
    with pytest.raises(ConnectionInputError) as err:
        await save_alice(svc(), uuid4(), uuid4(), **override)
    assert err.value.code == code
    assert APP_PASSWORD not in err.value.message


# ------------------------------------------------------------------------- resolution order


def env_with_mailbox() -> EnvDefaults:
    return EnvDefaults(
        mailbox=MailboxConfig(address=OTHER_ADDRESS, password="fake-env-password-12345")
    )


async def test_user_mailbox_wins_then_environment_then_none() -> None:
    ws, alice, bob = uuid4(), uuid4(), uuid4()
    # Deliberate change: the server mailbox is the deployment owner's only (here: bob).
    service = svc(env=env_with_mailbox(), owner=bob)
    await save_alice(service, ws, alice)
    mine = await service.resolve_mailbox(ws, alice)
    assert mine is not None and mine.address == ADDRESS
    fallback = await service.resolve_mailbox(ws, bob)
    assert fallback is not None and fallback.address == OTHER_ADDRESS
    status = await service.status_of(ws, bob, "mail")
    assert status.state == "server_default" and status.source == "environment"
    assert await svc().resolve_mailbox(ws, bob) is None
    assert (await svc().status_of(ws, bob, "mail")).state == "not_connected"


async def test_user_b_never_sees_or_uses_user_a_mailbox() -> None:
    service = svc()
    ws, alice, bob = uuid4(), uuid4(), uuid4()
    await save_alice(service, ws, alice)
    assert await service.resolve_mailbox(ws, bob) is None
    assert await service.mail_service(ws, bob) is None
    assert (await service.status_of(ws, bob, "mail")).state == "not_connected"
    assert await service.resolve_mailbox(uuid4(), alice) is None  # another workspace


async def test_capabilities_mailbox_is_decided_per_user() -> None:
    service = svc()
    ws, alice, bob = uuid4(), uuid4(), uuid4()
    await save_alice(service, ws, alice)

    async def has_mailbox(w: UUID, u: UUID) -> bool:
        return await service.resolve_mailbox(w, u) is not None

    capabilities.set_user_mailbox(has_mailbox)
    try:
        assert (await capabilities.for_user(ws, alice)).mailbox is True
        assert (await capabilities.for_user(ws, bob)).mailbox is False
        with capabilities.bound(await capabilities.for_user(ws, bob)):
            refusal = capabilities.precheck("check my inbox")
            assert refusal is not None and "Settings > Connections" in refusal.hint
    finally:
        capabilities.reset()


# ------------------------------------------------------------------------------ the test step


async def test_test_connection_reports_each_half_and_sends_nothing(
    server: FakeMailServer,
) -> None:
    service = svc()
    ws, user = uuid4(), uuid4()
    await save_alice(service, ws, user)
    result = await service.test(ws, user, "mail")
    assert result.ok and result.steps is not None
    assert [(label, ok) for label, ok, _ in result.steps] == [
        ("Incoming (IMAP)", True),
        ("Outgoing (SMTP)", True),
    ]
    assert server.sent == [] and server.logins == 1 and len(server.smtp_logins) == 1
    status = await service.status_of(ws, user, "mail")
    assert status.state == "connected" and status.last_tested_at is not None


async def test_wrong_password_names_the_failing_step_without_the_password(
    server: FakeMailServer,
) -> None:
    service = svc()
    ws, user = uuid4(), uuid4()
    await save_alice(service, ws, user, app_password="fake-wrong-password-1")
    result = await service.test(ws, user, "mail")
    assert not result.ok and result.steps is not None
    assert all(not ok for _label, ok, _msg in result.steps)
    assert (
        "fake-wrong-password-1" not in repr(result) and "Settings > Connections" in result.message
    )
    assert (await service.status_of(ws, user, "mail")).state == "error"


async def test_smtp_only_failure_is_reported_as_the_outgoing_step(server: FakeMailServer) -> None:
    server.smtp_login_error = smtplib.SMTPAuthenticationError(535, b"nope")
    service = svc()
    ws, user = uuid4(), uuid4()
    await save_alice(service, ws, user)
    result = await service.test(ws, user, "mail")
    assert not result.ok and result.code == "smtp_failed"
    assert result.steps is not None and result.steps[0][1] is True and result.steps[1][1] is False


async def test_imap_disabled_has_its_own_sentence(server: FakeMailServer) -> None:
    server.login_error = imaplib.IMAP4.error("[ALERT] IMAP access is disabled for your domain")
    service = svc()
    ws, user = uuid4(), uuid4()
    await save_alice(service, ws, user)
    result = await service.test(ws, user, "mail")
    assert result.steps is not None and "IMAP is turned off" in result.steps[0][2]


# ---------------------------------------------------------------------------------- the API


def test_api_save_test_disconnect_never_return_or_log_the_password(
    server: FakeMailServer, caplog: pytest.LogCaptureFixture
) -> None:
    client = make_client(svc(), principal(uuid4(), uuid4()))
    caplog.set_level(logging.DEBUG)
    put = client.put(URL, json={"address": ADDRESS, "token": GROUPED})
    assert put.status_code == 200
    body = put.json()
    assert body["test"]["ok"] and body["connection"]["state"] == "connected"
    assert body["connection"]["mailbox"]["address"] == "m***@gmail.com"
    listing = client.get("/api/v1/settings/connections")
    mail = next(c for c in listing.json()["connections"] if c["provider"] == "mail")
    assert mail["kind"] == "mailbox" and mail["mailbox"]["password_saved"] is True
    test = client.post(f"{URL}/test")
    gone = client.delete(URL)
    for response in (put, listing, test, gone):
        for secret in (APP_PASSWORD, GROUPED, ADDRESS):
            assert secret not in response.text
    assert APP_PASSWORD not in caplog.text and GROUPED not in caplog.text
    assert gone.json()["connection"]["state"] == "not_connected"


def test_api_validation_errors_do_not_echo_the_password() -> None:
    client = make_client(svc(), principal(uuid4(), uuid4()))
    bad = client.put(URL, json={"address": "nope", "token": GROUPED})
    assert bad.status_code == 422 and bad.json()["error"] == "address_format"
    assert GROUPED not in bad.text and APP_PASSWORD not in bad.text
    host = client.put(URL, json={"address": ADDRESS, "token": GROUPED, "imap_host": "10.1.1.1"})
    assert host.status_code == 422 and host.json()["error"] == "host_forbidden"


def test_api_users_are_isolated(server: FakeMailServer) -> None:
    service = svc()
    ws, alice, bob = uuid4(), uuid4(), uuid4()
    make_client(service, principal(ws, alice)).put(URL, json={"address": ADDRESS, "token": GROUPED})
    seen = make_client(service, principal(ws, bob)).get("/api/v1/settings/connections")
    mail = next(c for c in seen.json()["connections"] if c["provider"] == "mail")
    assert mail["state"] == "not_connected" and mail["mailbox"] is None
    assert ADDRESS not in seen.text


# ------------------------------------------------------ run time: send uses the user's mailbox


def job_for(ws: UUID, user: UUID) -> SubtaskJob:
    return SubtaskJob(
        workspace_id=ws, user_id=user, task_id=uuid4(), subtask_id=uuid4(), assigned_agent="email"
    )


async def test_the_approved_send_uses_the_requesting_users_mailbox_only(
    server: FakeMailServer,
) -> None:
    service = svc()
    ws, alice, bob = uuid4(), uuid4(), uuid4()
    await save_alice(service, ws, alice)
    gateway = McpGateway(MCPClientManager(permission_registry=ToolPermissionRegistry()))
    gateway.register_spec(builtin_spec(github_token=None, connections=service))
    args = {"to": ["rahul@example.com"], "subject": "Hi", "body": "Hello"}
    try:
        with tool_call_scope(ws, uuid4(), bob):  # Bob has no mailbox: nothing is sent
            with pytest.raises(Exception, match="No mailbox is connected"):
                await gateway.invoke("mail_send", args, has_valid_approval_token=True)
        assert server.sent == []
        with tool_call_scope(ws, uuid4(), alice):
            result = await gateway.invoke("mail_send", args, has_valid_approval_token=True)
        assert len(server.sent) == 1 and server.sent[0]["from"] == ADDRESS
        assert APP_PASSWORD not in result.clean_text and GROUPED not in result.clean_text
    finally:
        await gateway.close()


async def test_the_email_agent_sends_from_the_task_owners_mailbox(server: FakeMailServer) -> None:
    service = svc()
    ws, alice, bob = uuid4(), uuid4(), uuid4()
    await save_alice(service, ws, alice)
    agent = EmailAgent(connections=service)
    ctx = {"to": "rahul@example.com", "subject": "Hi", "body": "Hello"}
    no_box = await agent.execute_subtask(
        job_for(ws, bob), {**ctx, "has_valid_approval_token": True}
    )
    assert no_box.status is AgentExecutionStatus.FAILED and server.sent == []
    sent = await agent.execute_subtask(
        job_for(ws, alice), {**ctx, "has_valid_approval_token": True}
    )
    assert sent.status is AgentExecutionStatus.SUCCESS and len(server.sent) == 1
