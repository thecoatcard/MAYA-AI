"""Live voice uses the same MCP tools, tiers and approval path as chat (ADR-0028)."""

from typing import Any
from uuid import uuid4

from app.mcp_client import voice as mcp_voice
from app.mcp_client.service import Identity
from tests.support.mcp_client_fakes import fake_definition, fake_service

ID = Identity(uuid4(), uuid4())


class Submitter:
    def __init__(self) -> None:
        self.submitted: list[dict[str, Any]] = []

    async def submit(self, **kwargs: Any) -> Any:
        self.submitted.append(kwargs)

        class Done:
            task_id = uuid4()
            is_new = True
            reason = None

        return Done()


async def test_voice_declares_capped_live_declarations_and_routes_like_chat() -> None:
    service = fake_service()
    try:
        turn = await mcp_voice.prepare_voice(service, ID, max_tools=4)
        assert turn is not None and len(turn.entries) == 4
        decls = mcp_voice.declarations(turn)
        assert all(d["parameters"]["type"] == "OBJECT" for d in decls)
        assert all(d["name"].startswith("mcp__fake__") for d in decls)

        submitter = Submitter()
        common: dict[str, Any] = {
            "submitter": submitter,
            "workspace_id": ID.workspace_id,
            "user_id": ID.user_id,
            "session_id": None,
            "scope": "s",
        }
        read = await mcp_voice.call_by_voice(
            turn, name="mcp__fake__echo", args={"text": "a"}, **common
        )
        assert read["ok"] is True and "echo:a" in read["output_untrusted"]
        assert submitter.submitted == []
        # A write tool is not among the first four (read-only tools are listed first), so offer
        # it explicitly the way the chat path does: it must only create an approval step.
        entries = await service.discover("fake", ID)
        write = next(e for e in entries if e.tool == "write_thing")
        turn.entries[write.model_name] = write
        proposed = await mcp_voice.call_by_voice(
            turn, name=write.model_name, args={"name": "n"}, **common
        )
        assert proposed["ok"] is True and proposed["status"] == "waiting_for_approval"
        (sent,) = submitter.submitted
        assert sent["source"] == "voice" and sent["steps"][0].tool == "mcp_client_call"
        assert (await service.call("fake", "count_calls", {}, ID)).text == "1"  # only the echo
    finally:
        await service.shutdown()


async def test_voice_gets_no_tools_when_nothing_is_enabled() -> None:
    service = fake_service(definition=fake_definition(enabled=False))
    assert await mcp_voice.prepare_voice(service, ID, max_tools=12) is None
    assert await mcp_voice.prepare_voice(None, ID, max_tools=12) is None
