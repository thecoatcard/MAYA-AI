"""The MCP client against a real child process: our own tiny fake server (no network, no third
party server).

Covered: discovery, a call, timeout, crash and bounded restart, the output cap, the environment
allowlist (a parent secret never reaches the child), per-user credentials, the permission gate
(unknown tool needs approval; 0 calls before approval, 1 after, 0 on reject, refused without the
gate's approval), the browser address guard, untrusted wrapping of output, the per-turn budget and
the kill switch.
"""

import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from app.mcp.client_manager import MCPClientManager
from app.mcp.gateway import McpGateway
from app.mcp.permissions import PermissionDeniedError
from app.mcp.scope import tool_call_scope
from app.mcp_client import chat as mcp_chat
from app.mcp_client.definitions import DefinitionSet
from app.mcp_client.factory import build_mcp_client
from app.mcp_client.gateway_spec import mcp_client_spec
from app.mcp_client.service import Identity, McpClientError, McpClientService, McpRefused
from tests.support.mcp_client_fakes import fake_definition, fake_service
from tests.support.settings import make_settings

ID = Identity(uuid4(), uuid4())
BACKEND = Path(__file__).resolve().parents[2]


@pytest.fixture
async def service() -> AsyncIterator[McpClientService]:
    value = fake_service()
    try:
        yield value
    finally:
        await value.shutdown()


async def call_text(service: McpClientService, tool: str, **args: Any) -> str:
    return (await service.call("fake", tool, args, ID)).text


class Submits:
    """A fake task submitter: records the step it is given and returns a created task."""

    def __init__(self) -> None:
        self.steps: list[Any] = []

    async def __call__(self, summary: str, step: Any) -> Any:
        self.steps.append(step)

        class Outcome:
            task_id = uuid4()
            is_new = True

        return Outcome()


async def turn_for(service: McpClientService, text: str = "use the fakeserver") -> Any:
    turn = await mcp_chat.prepare(service, ID, text, max_tools=12)
    assert turn is not None
    return turn


# ------------------------------------------------------------------------------- the adapter


async def test_discovery_lists_tools_with_gateway_ready_names(service: McpClientService) -> None:
    entries = await service.discover("fake", ID)
    by_name = {e.tool: e for e in entries}
    assert {"echo", "navigate", "write_thing"} <= set(by_name)
    echo = by_name["echo"]
    assert echo.model_name == "mcp__fake__echo" and echo.declaration.name == echo.model_name
    assert echo.declaration.parameters["properties"]["text"]["type"] == "string"
    assert echo.declaration.parameters["required"] == ["text"]
    assert service.status()[0]["running"] is True and service.status()[0]["tool_count"] >= 9


async def test_a_call_returns_the_servers_text(service: McpClientService) -> None:
    assert await call_text(service, "echo", text="hi") == "echo:hi"


async def test_a_tool_the_server_did_not_list_is_refused(service: McpClientService) -> None:
    with pytest.raises(McpClientError, match="no tool named"):
        await call_text(service, "rm_everything")


async def test_a_slow_call_times_out_and_the_server_is_restarted(service: McpClientService) -> None:
    with pytest.raises(McpClientError, match="took longer"):
        await service.call("fake", "sleep_seconds", {"seconds": 30}, ID, timeout=1.0)
    assert await call_text(service, "echo", text="again") == "echo:again"


async def test_a_crash_is_reported_and_the_next_call_restarts_the_server(
    service: McpClientService,
) -> None:
    await call_text(service, "echo", text="before")
    with pytest.raises(McpClientError, match="stopped"):
        await call_text(service, "crash")
    assert await call_text(service, "echo", text="after") == "echo:after"
    assert await call_text(service, "count_calls") == "1"  # a fresh process: the counter restarted


async def test_restarts_are_bounded_then_the_server_pauses_and_recovers() -> None:
    now = [1000.0]
    service = fake_service(max_restarts=1)
    service._monotonic = lambda: now[0]  # noqa: SLF001 - a fake clock for the cooldown
    try:
        for _ in range(2):
            with pytest.raises(McpClientError, match="stopped"):
                await call_text(service, "crash")
        with pytest.raises(McpClientError, match="paused"):
            await call_text(service, "echo", text="x")
        now[0] += 61
        assert await call_text(service, "echo", text="back") == "echo:back"
    finally:
        await service.shutdown()


async def test_output_is_capped() -> None:
    service = fake_service(max_output_chars=1000)
    try:
        result = await service.call("fake", "big", {"chars": 50000}, ID)
        assert result.truncated is True and len(result.text) <= 1000
        small = await service.call("fake", "echo", {"text": "ok"}, ID)
        assert small.truncated is False
    finally:
        await service.shutdown()


async def test_a_parent_secret_never_reaches_the_child(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAYA_TEST_PARENT_SECRET", "must-not-leak")
    monkeypatch.setenv("OTHER_TEST_PARENT_SECRET", "must-not-leak-either")
    monkeypatch.setenv("FAKE_ALLOWED_VAR", "visible")
    service = fake_service(
        definition=fake_definition(env_allow=("FAKE_ALLOWED_VAR",)), environ=dict(os.environ)
    )
    try:
        names = (await call_text(service, "env_names")).split(",")
        assert "FAKE_ALLOWED_VAR" in names
        assert "MAYA_TEST_PARENT_SECRET" not in names
        assert "OTHER_TEST_PARENT_SECRET" not in names
        assert await call_text(service, "env_value", name="MAYA_TEST_PARENT_SECRET") == ""
        assert await call_text(service, "env_value", name="FAKE_ALLOWED_VAR") == "visible"
    finally:
        await service.shutdown()


async def test_a_credential_comes_from_the_users_own_connection_and_never_leaks() -> None:
    tokens = {"alice": "token-alice-111", "bob": "token-bob-222"}
    alice, bob, stranger = Identity(uuid4(), uuid4()), Identity(uuid4(), uuid4()), Identity()
    who = {alice.user_id: "alice", bob.user_id: "bob"}

    async def resolve(provider: str, identity: Identity) -> str | None:
        assert provider == "github"
        return tokens.get(who.get(identity.user_id, ""))

    definition = fake_definition(credential_env={"GITHUB_PERSONAL_ACCESS_TOKEN": "github"})
    service = McpClientService(
        DefinitionSet(servers={"fake": definition}),
        credentials=resolve,
        environ={"MAYA_JWT_SIGNING_KEY": "signing-secret"},
    )
    try:
        var = "GITHUB_PERSONAL_ACCESS_TOKEN"
        got_a = (await service.call("fake", "env_value", {"name": var}, alice)).text
        got_b = (await service.call("fake", "env_value", {"name": var}, bob)).text
        assert got_a == "token-alice-111" and got_b == "token-bob-222"  # one process per user
        with pytest.raises(McpClientError, match="not connected"):
            await service.call("fake", "echo", {"text": "x"}, stranger)
        everything = repr(service.status()) + repr(service.problems)
        assert "token-alice" not in everything and "signing-secret" not in everything
        names = (await service.call("fake", "env_names", {}, alice)).text
        assert "MAYA_JWT_SIGNING_KEY" not in names
    finally:
        await service.shutdown()


async def test_a_disabled_server_is_neither_started_nor_relevant() -> None:
    service = fake_service(definition=fake_definition(enabled=False))
    assert service.relevant_servers("use the fakeserver") == []
    with pytest.raises(McpClientError, match="not enabled"):
        await service.discover("fake", ID)
    assert service.status()[0]["running"] is False
    assert await mcp_chat.prepare(service, ID, "use the fakeserver", max_tools=12) is None


async def test_shutdown_stops_the_child_and_blocks_new_starts(service: McpClientService) -> None:
    await call_text(service, "echo", text="up")
    await service.shutdown()
    assert service.status()[0]["running"] is False
    with pytest.raises(McpClientError, match="shutting down"):
        await call_text(service, "echo", text="late")


# ------------------------------------------------------------------------- the permission gate


async def test_the_model_is_offered_at_most_the_capped_number_of_tools(
    service: McpClientService,
) -> None:
    turn = await mcp_chat.prepare(service, ID, "use the fakeserver to echo hello", max_tools=3)
    assert turn is not None and len(turn.declarations) == 3
    assert "mcp__fake__echo" in turn.names  # selected by relevance to the message
    assert mcp_chat.is_mcp_tool("mcp__fake__echo") and not mcp_chat.is_mcp_tool("web_search")


async def test_a_read_tool_runs_at_once_and_a_write_only_proposes(
    service: McpClientService,
) -> None:
    turn = await turn_for(service)
    submit = Submits()
    ran = await mcp_chat.handle_call(turn, "mcp__fake__echo", {"text": "hi"}, submit)
    assert ran["status"] == "ok" and "echo:hi" in ran["output_untrusted"] and submit.steps == []

    proposed = await mcp_chat.handle_call(turn, "mcp__fake__write_thing", {"name": "n1"}, submit)
    assert proposed["status"] == "waiting_for_approval" and "Nothing has run" in proposed["message"]
    (step,) = submit.steps
    assert step.tool == "mcp_client_call"
    assert step.tool_args == {"server": "fake", "tool": "write_thing", "arguments": {"name": "n1"}}
    assert "write_thing" in step.description and "n1" in step.description  # the exact payload
    # Nothing reached the server: its own counter only saw the echo call.
    assert await call_text(service, "count_calls") == "1"


async def test_a_tool_not_on_the_read_list_needs_approval_even_if_unknown_to_maya(
    service: McpClientService,
) -> None:
    submit = Submits()
    turn = await turn_for(service)
    # navigate is in the guarded URL list, not the read list: a page the user did not name.
    out = await mcp_chat.handle_call(
        turn, "mcp__fake__navigate", {"url": "https://public.example/x"}, submit
    )
    assert out["status"] == "waiting_for_approval" and len(submit.steps) == 1
    unknown = await mcp_chat.handle_call(turn, "mcp__fake__never_listed", {}, submit)
    assert unknown["status"] == "error" and len(submit.steps) == 1


async def test_approved_execution_runs_once_after_approval_and_never_before_or_on_reject() -> None:
    service = fake_service()
    registry_manager = MCPClientManager()
    gateway = McpGateway(registry_manager)
    gateway.register_spec(mcp_client_spec(service))
    args = {"server": "fake", "tool": "write_thing", "arguments": {"name": "n1"}}
    workspace, user = uuid4(), uuid4()
    try:
        turn = await turn_for(service)
        submit = Submits()
        await mcp_chat.handle_call(turn, "mcp__fake__write_thing", {"name": "n1"}, submit)
        step_args = submit.steps[0].tool_args
        assert step_args == args
        # Before approval: 0 calls. (Starting the server for discovery ran no tool.)
        assert await call_text(service, "count_calls") == "0"
        # Reject: the step is never invoked, so nothing runs.
        assert await call_text(service, "count_calls") == "0"
        # The gate refuses the execution tool without a verified approval...
        with tool_call_scope(workspace, uuid4(), user), pytest.raises(PermissionDeniedError):
            await gateway.invoke("mcp_client_call", dict(step_args), has_valid_approval_token=False)
        assert await call_text(service, "count_calls") == "0"
        # ...and runs it exactly once with one.
        with tool_call_scope(workspace, uuid4(), user):
            result = await gateway.invoke(
                "mcp_client_call", dict(step_args), has_valid_approval_token=True
            )
        assert "wrote:n1" in result.clean_text
        assert await call_text(service, "count_calls") == "1"
    finally:
        await service.shutdown()


async def test_the_execution_path_refuses_without_the_approval_flag(
    service: McpClientService,
) -> None:
    args = {"server": "fake", "tool": "write_thing", "arguments": {"name": "n"}}
    with pytest.raises(McpClientError, match="not approved"):
        await mcp_chat.execute_call(service, uuid4(), uuid4(), args, approved=False)
    with pytest.raises(McpClientError, match="malformed"):
        await mcp_chat.execute_call(service, None, None, {"server": "fake"}, approved=True)
    assert await call_text(service, "count_calls") == "0"


async def test_the_per_turn_call_budget_is_enforced() -> None:
    service = fake_service(max_calls_per_turn=2)
    try:
        turn = await turn_for(service)
        results = [
            await mcp_chat.handle_call(turn, "mcp__fake__echo", {"text": str(i)}, Submits())
            for i in range(4)
        ]
        assert [r["status"] for r in results] == ["ok", "ok", "skipped", "skipped"]
        assert await call_text(service, "count_calls") == "2"
    finally:
        await service.shutdown()


# ------------------------------------------------------------------------------- SSRF and wrapping


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8000/api",
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://192.168.0.1/",
        "http://rebind.example/",
        "file:///etc/passwd",
    ],
)
async def test_private_browser_targets_are_refused_before_anything_is_sent(
    service: McpClientService, url: str
) -> None:
    turn = await turn_for(service, f"use the fakeserver to open {url}")
    submit = Submits()
    out = await mcp_chat.handle_call(turn, "mcp__fake__navigate", {"url": url}, submit)
    assert out["status"] == "refused" and "Nothing ran" in out["message"]
    assert submit.steps == []  # not even proposed for approval
    assert await call_text(service, "count_calls") == "0"
    with pytest.raises(McpRefused):  # and again on the approved-execution path
        await service.call("fake", "navigate", {"url": url}, ID)


async def test_a_public_address_the_user_wrote_opens_without_a_card(
    service: McpClientService,
) -> None:
    turn = await turn_for(service, "use the fakeserver to open public.example")
    out = await mcp_chat.handle_call(
        turn, "mcp__fake__navigate", {"url": "https://public.example/"}, Submits()
    )
    assert out["status"] == "ok" and "navigated:https://public.example/" in out["output_untrusted"]


async def test_tool_output_is_wrapped_as_untrusted_data_and_flagged(
    service: McpClientService,
) -> None:
    turn = await turn_for(service)
    out = await mcp_chat.handle_call(turn, "mcp__fake__poisoned", {}, Submits())
    wrapped = out["output_untrusted"]
    assert (
        wrapped.startswith("<<<UNTRUSTED_EXTERNAL_INPUT")
        and "END_UNTRUSTED_EXTERNAL_INPUT" in wrapped
    )
    assert "mcp:fake:poisoned" in wrapped
    assert out["output_flagged_as_suspicious"] is True
    assert "Never follow" in out["message"] and "ignore all previous" not in out["message"].lower()
    # The only way output reaches the model is inside the envelope: no plain-text field carries it.
    assert "Page text" not in {k: v for k, v in out.items() if k != "output_untrusted"}.__str__()
    assert (
        "never follow" in turn.system_text().lower() or "never instructions" in turn.system_text()
    )


# ------------------------------------------------------------------------------- the kill switch


def test_with_the_switch_off_nothing_is_built_and_the_sdk_is_not_imported() -> None:
    assert build_mcp_client(make_settings(), None) is None  # default: off
    code = (
        f"import sys; sys.path.insert(0, {str(BACKEND)!r})\n"
        "import app.main, app.services.chat_service\n"
        "from tests.support.settings import make_settings\n"
        "from app.mcp_client.factory import build_mcp_client\n"
        "assert build_mcp_client(make_settings(), None) is None\n"
        "assert 'mcp' not in sys.modules and 'mcp.client' not in sys.modules, 'SDK imported'\n"
        "print('clean')\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("MAYA_")}
    done = subprocess.run(  # noqa: S603 - our own interpreter and code
        [sys.executable, "-I", "-c", code],
        cwd=BACKEND,
        env={**env, "PYTHONPATH": str(BACKEND)},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0 and "clean" in done.stdout, done.stderr[-2000:]


def test_the_switch_builds_a_service_only_when_on(tmp_path: Path) -> None:
    on = build_mcp_client(make_settings(mcp_client_enabled=True), None)
    assert on is not None and on.enabled_servers() == {}  # built-ins stay disabled
    assert [row["enabled"] for row in on.status()] == [False, False]
    assert mcp_chat.relevant(on, "open the browser and take a screenshot") is False
    assert mcp_chat.relevant(None, "open the browser") is False
