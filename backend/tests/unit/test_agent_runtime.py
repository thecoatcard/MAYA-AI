"""Comprehensive unit tests for the Unified Agent Runtime.

Tests:
1. Single-turn text response streaming and event dispatch.
2. Multi-step tool use (read_file -> write_file -> done).
3. Approval gate triggering for APPROVAL_REQUIRED tools (both approved and denied paths).
4. Rejection and immediate halting on RESTRICTED tools.
5. Budget and round limits enforcement (max_rounds and max_tokens).
6. Live TodoStore dynamic planning synchronization via TodoStateEvent.
7. Graceful error handling for missing tools and tool execution exceptions.
"""

from __future__ import annotations

import tempfile
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest

from app.agent.events import (
    AgentEvent,
    ApprovalNeededEvent,
    DoneEvent,
    TextDeltaEvent,
    TodoStateEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from app.agent.runtime import (
    AgentExecutionResult,
    AgentRuntime,
    RuntimeBudget,
    run,
)
from app.gateway.schemas import GatewayStreamChunk, TokenUsage, ToolCallRequest
from app.tools.base import (
    BaseTool,
    ToolExecutionContext,
    ToolRisk,
    tool,
)
from app.tools.registry import ToolRegistry
from app.tools.todo import TodoManageTool
from app.tools.workspace import (
    workspace_read_file,
    workspace_write_file,
)


class MockModel:
    """Helper to simulate multi-round model chunk streaming."""

    def __init__(self, rounds: list[list[GatewayStreamChunk]]) -> None:
        self.rounds = rounds
        self.current_round = 0

    def __call__(
        self,
        history: list[dict[str, Any]],
        tools: list[Any] | None = None,
        system_instruction: str | None = None,
        model_alias: str = "mock",
    ) -> list[GatewayStreamChunk]:
        if self.current_round < len(self.rounds):
            chunks = self.rounds[self.current_round]
            self.current_round += 1
            return chunks
        return [GatewayStreamChunk(text="Finished.", model_id="mock", alias_used=model_alias)]


@pytest.mark.asyncio
async def test_single_turn_text_response() -> None:
    """Verify single-turn text streaming without tool calls."""
    chunks = [
        GatewayStreamChunk(text="Hello ", model_id="test", alias_used="test"),
        GatewayStreamChunk(text="world!", model_id="test", alias_used="test"),
    ]
    model = MockModel([chunks])
    runtime = AgentRuntime(model_caller=model)

    events: list[AgentEvent] = []

    def on_event(evt: AgentEvent) -> None:
        events.append(evt)

    result = await runtime.run(
        messages="Say hello",
        on_event=on_event,
    )

    assert isinstance(result, AgentExecutionResult)
    assert result.status == "completed"
    assert result.final_text == "Hello world!"
    assert result.rounds_used == 1
    assert result.tool_calls_count == 0

    text_events = [e for e in events if isinstance(e, TextDeltaEvent)]
    assert len(text_events) == 2
    assert text_events[0].delta == "Hello "
    assert text_events[1].delta == "world!"

    done_events = [e for e in events if isinstance(e, DoneEvent)]
    assert len(done_events) == 1
    assert done_events[0].status == "completed"
    assert done_events[0].final_text == "Hello world!"


@pytest.mark.asyncio
async def test_multi_step_tool_use() -> None:
    """Verify multi-step tool use: read_file -> write_file -> done text."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        source_file = root / "source.txt"
        source_file.write_text("Secret Data 123", encoding="utf-8")
        target_file = root / "dest.txt"

        registry = ToolRegistry("workspace_test")

        @tool(
            name="read_file",
            description="Read file",
            risk=ToolRisk.SAFE,
        )
        async def read_tool(path: str, context: ToolExecutionContext) -> dict[str, Any]:
            return await workspace_read_file(path, context=context)

        @tool(
            name="write_file",
            description="Write file",
            risk=ToolRisk.SAFE,
        )
        async def write_tool(
            path: str, content: str, context: ToolExecutionContext
        ) -> dict[str, Any]:
            return await workspace_write_file(path, content=content, context=context)

        registry.register(read_tool)
        registry.register(write_tool)

        # Round 1: Model calls read_file
        round_1 = [
            GatewayStreamChunk(
                text="Reading file...",
                tool_calls=[
                    ToolCallRequest(
                        call_id="call_read_1",
                        tool_name="read_file",
                        arguments={"path": "source.txt"},
                    )
                ],
                model_id="test",
                alias_used="test",
            )
        ]

        # Round 2: Model receives content, calls write_file
        round_2 = [
            GatewayStreamChunk(
                text="Writing to destination...",
                tool_calls=[
                    ToolCallRequest(
                        call_id="call_write_2",
                        tool_name="write_file",
                        arguments={"path": "dest.txt", "content": "Copied: Secret Data 123"},
                    )
                ],
                model_id="test",
                alias_used="test",
            )
        ]

        # Round 3: Model completes task with final response
        round_3 = [
            GatewayStreamChunk(
                text="Successfully copied data to dest.txt.",
                model_id="test",
                alias_used="test",
            )
        ]

        model = MockModel([round_1, round_2, round_3])
        runtime = AgentRuntime(model_caller=model)

        events: list[AgentEvent] = []
        ctx = ToolExecutionContext(workspace_root=root)

        res = await runtime.run(
            messages="Copy source.txt to dest.txt",
            tools=registry,
            context=ctx,
            on_event=events.append,
        )

        assert res.status == "completed"
        assert res.rounds_used == 3
        assert res.tool_calls_count == 2
        assert "Successfully copied data" in res.final_text
        assert target_file.exists()
        assert target_file.read_text(encoding="utf-8") == "Copied: Secret Data 123"

        # Check emitted tool events
        tool_call_evts = [e for e in events if isinstance(e, ToolCallEvent)]
        assert len(tool_call_evts) == 2
        assert tool_call_evts[0].tool_name == "read_file"
        assert tool_call_evts[1].tool_name == "write_file"

        tool_res_evts = [e for e in events if isinstance(e, ToolResultEvent)]
        assert len(tool_res_evts) == 2
        assert tool_res_evts[0].result["ok"] is True
        assert tool_res_evts[1].result["ok"] is True


@pytest.mark.asyncio
async def test_approval_gate_approved() -> None:
    """Verify APPROVAL_REQUIRED tool executes when approved by callback."""
    action_executed = False

    class SendEmailTool(BaseTool):
        name = "send_email"
        description = "Sends an email externally"
        parameters = {"type": "object", "properties": {"to": {"type": "string"}}}
        risk = ToolRisk.APPROVAL_REQUIRED

        async def execute(self, context: ToolExecutionContext, **kwargs: Any) -> Any:
            nonlocal action_executed
            action_executed = True
            return {"ok": True, "sent_to": kwargs.get("to")}

    registry = ToolRegistry("approval_test")
    registry.register(SendEmailTool())

    round_1 = [
        GatewayStreamChunk(
            text="Sending email...",
            tool_calls=[
                ToolCallRequest(
                    call_id="call_email_1",
                    tool_name="send_email",
                    arguments={"to": "ceo@example.com"},
                )
            ],
            model_id="test",
            alias_used="test",
        )
    ]
    round_2 = [
        GatewayStreamChunk(
            text="Email successfully sent.",
            model_id="test",
            alias_used="test",
        )
    ]

    model = MockModel([round_1, round_2])
    runtime = AgentRuntime(model_caller=model)

    events: list[AgentEvent] = []
    approval_prompted = False

    async def approve_all(tool_name: str, arguments: dict[str, Any], call_id: str) -> bool:
        nonlocal approval_prompted
        approval_prompted = True
        assert tool_name == "send_email"
        assert arguments["to"] == "ceo@example.com"
        return True

    res = await runtime.run(
        messages="Send email to CEO",
        tools=registry,
        on_event=events.append,
        approval_callback=approve_all,
    )

    assert res.status == "completed"
    assert approval_prompted is True
    assert action_executed is True

    approval_evts = [e for e in events if isinstance(e, ApprovalNeededEvent)]
    assert len(approval_evts) == 1
    assert approval_evts[0].tool_name == "send_email"
    assert approval_evts[0].risk_level == ToolRisk.APPROVAL_REQUIRED


@pytest.mark.asyncio
async def test_approval_gate_rejected() -> None:
    """Verify APPROVAL_REQUIRED tool is denied when approval callback returns False."""
    action_executed = False

    class FormatDiskTool(BaseTool):
        name = "format_disk"
        description = "Format disk drive"
        parameters = {"type": "object", "properties": {}}
        risk = ToolRisk.APPROVAL_REQUIRED

        async def execute(self, context: ToolExecutionContext, **kwargs: Any) -> Any:
            nonlocal action_executed
            action_executed = True
            return {"ok": True}

    registry = ToolRegistry("approval_deny_test")
    registry.register(FormatDiskTool())

    round_1 = [
        GatewayStreamChunk(
            tool_calls=[
                ToolCallRequest(
                    call_id="call_format_1",
                    tool_name="format_disk",
                    arguments={},
                )
            ],
            model_id="test",
            alias_used="test",
        )
    ]
    round_2 = [
        GatewayStreamChunk(
            text="Understood, format was denied. Aborting.",
            model_id="test",
            alias_used="test",
        )
    ]

    model = MockModel([round_1, round_2])
    runtime = AgentRuntime(model_caller=model)

    events: list[AgentEvent] = []

    def reject_all(call: ToolCallRequest) -> bool:
        return False

    res = await runtime.run(
        messages="Format the drive",
        tools=registry,
        on_event=events.append,
        approval_callback=reject_all,
    )

    assert res.status == "completed"
    assert action_executed is False
    assert "format was denied" in res.final_text

    tool_res_evts = [e for e in events if isinstance(e, ToolResultEvent)]
    assert len(tool_res_evts) == 1
    assert tool_res_evts[0].result["ok"] is False
    assert tool_res_evts[0].error is not None and "denied or not approved" in tool_res_evts[0].error


@pytest.mark.asyncio
async def test_restricted_tool_rejection_halts_immediately() -> None:
    """Verify RESTRICTED tool call is halted immediately without executing."""
    executed = False

    class ExfiltrateTool(BaseTool):
        name = "exfiltrate_passwords"
        description = "Steal passwords"
        parameters = {"type": "object", "properties": {}}
        risk = ToolRisk.RESTRICTED

        async def execute(self, context: ToolExecutionContext, **kwargs: Any) -> Any:
            nonlocal executed
            executed = True
            return {"stolen": True}

    registry = ToolRegistry("restricted_test")
    registry.register(ExfiltrateTool())

    round_1 = [
        GatewayStreamChunk(
            tool_calls=[
                ToolCallRequest(
                    call_id="call_nuke_1",
                    tool_name="exfiltrate_passwords",
                    arguments={},
                )
            ],
            model_id="test",
            alias_used="test",
        )
    ]
    # Round 2 should NEVER be called
    round_2 = [
        GatewayStreamChunk(
            text="Should not happen",
            model_id="test",
            alias_used="test",
        )
    ]

    model = MockModel([round_1, round_2])
    runtime = AgentRuntime(model_caller=model)

    events: list[AgentEvent] = []

    res = await runtime.run(
        messages="Steal passwords",
        tools=registry,
        on_event=events.append,
    )

    assert res.status == "restricted_tool_rejected"
    assert executed is False
    assert "RESTRICTED" in str(res.error)

    done_evts = [e for e in events if isinstance(e, DoneEvent)]
    assert len(done_evts) == 1
    assert done_evts[0].status == "restricted_tool_rejected"


@pytest.mark.asyncio
async def test_budget_max_rounds_enforcement() -> None:
    """Verify that loop terminates when max_rounds limit is reached."""
    # Model generates infinite tool calls
    infinite_round = [
        GatewayStreamChunk(
            tool_calls=[
                ToolCallRequest(
                    call_id="call_ping",
                    tool_name="ping",
                    arguments={},
                )
            ],
            model_id="test",
            alias_used="test",
        )
    ]

    @tool(name="ping", description="Ping", risk=ToolRisk.SAFE)
    def ping() -> dict[str, str]:
        return {"pong": "ok"}

    registry = ToolRegistry("budget_test")
    registry.register(ping)

    model = MockModel([infinite_round, infinite_round, infinite_round, infinite_round])
    runtime = AgentRuntime(model_caller=model)

    events: list[AgentEvent] = []

    # Limit to 2 rounds
    budget = RuntimeBudget(max_rounds=2)

    res = await runtime.run(
        messages="Loop forever",
        tools=registry,
        budget=budget,
        on_event=events.append,
    )

    assert res.status == "max_rounds_reached"
    assert res.rounds_used == 2
    assert "Max execution rounds" in str(res.error)


@pytest.mark.asyncio
async def test_budget_max_tokens_enforcement() -> None:
    """Verify that loop terminates when token budget is exceeded."""
    round_1 = [
        GatewayStreamChunk(
            text="Round 1",
            usage=TokenUsage(prompt_tokens=50, candidates_tokens=50, total_token_count=100),
            tool_calls=[
                ToolCallRequest(
                    call_id="call_1",
                    tool_name="step",
                    arguments={},
                )
            ],
            model_id="test",
            alias_used="test",
        )
    ]
    round_2 = [
        GatewayStreamChunk(
            text="Round 2",
            usage=TokenUsage(prompt_tokens=50, candidates_tokens=50, total_token_count=100),
            model_id="test",
            alias_used="test",
        )
    ]

    @tool(name="step", description="Step", risk=ToolRisk.SAFE)
    def step() -> dict[str, str]:
        return {"ok": "1"}

    registry = ToolRegistry("token_budget_test")
    registry.register(step)

    model = MockModel([round_1, round_2])
    runtime = AgentRuntime(model_caller=model)

    events: list[AgentEvent] = []

    # Token budget capped at 80 tokens (round 1 consumes 100)
    budget = RuntimeBudget(max_tokens=80, max_rounds=10)

    res = await runtime.run(
        messages="Start",
        tools=registry,
        budget=budget,
        on_event=events.append,
    )

    assert res.status == "budget_exceeded"
    assert "Token budget of 80 exceeded" in str(res.error)


@pytest.mark.asyncio
async def test_todo_store_live_streaming() -> None:
    """Verify live TodoStateEvent emission when TodoStore tasks update."""
    registry = ToolRegistry("todo_runtime_test")
    registry.register(TodoManageTool())

    round_1 = [
        GatewayStreamChunk(
            text="Planning tasks...",
            tool_calls=[
                ToolCallRequest(
                    call_id="todo_create_1",
                    tool_name="todo_manage",
                    arguments={
                        "action": "create",
                        "tasks": ["Implement feature", "Write tests"],
                    },
                )
            ],
            model_id="test",
            alias_used="test",
        )
    ]
    round_2 = [
        GatewayStreamChunk(
            text="Finishing task 1...",
            tool_calls=[
                ToolCallRequest(
                    call_id="todo_mark_2",
                    tool_name="todo_manage",
                    arguments={"action": "mark_done", "tasks": ["task_1"]},
                )
            ],
            model_id="test",
            alias_used="test",
        )
    ]
    round_3 = [
        GatewayStreamChunk(
            text="All tasks updated.",
            model_id="test",
            alias_used="test",
        )
    ]

    model = MockModel([round_1, round_2, round_3])
    runtime = AgentRuntime(model_caller=model)

    events: list[AgentEvent] = []
    ctx = ToolExecutionContext(session_id="test_session_todo")

    res = await runtime.run(
        messages="Plan and execute work",
        tools=registry,
        context=ctx,
        on_event=events.append,
    )

    assert res.status == "completed"

    todo_evts = [e for e in events if isinstance(e, TodoStateEvent)]
    assert len(todo_evts) >= 2
    # First state after create has 2 tasks
    assert len(todo_evts[0].tasks) == 2
    assert todo_evts[0].counts["pending"] == 2
    # Second state after mark_done has 1 done
    assert todo_evts[1].counts["done"] == 1


@pytest.mark.asyncio
async def test_unregistered_tool_handling() -> None:
    """Verify model calling an unregistered tool returns an error payload without crashing."""
    round_1 = [
        GatewayStreamChunk(
            tool_calls=[
                ToolCallRequest(
                    call_id="call_ghost_1",
                    tool_name="non_existent_tool",
                    arguments={"foo": "bar"},
                )
            ],
            model_id="test",
            alias_used="test",
        )
    ]
    round_2 = [
        GatewayStreamChunk(
            text="Tool was not found, falling back.",
            model_id="test",
            alias_used="test",
        )
    ]

    model = MockModel([round_1, round_2])
    runtime = AgentRuntime(model_caller=model)

    events: list[AgentEvent] = []
    res = await runtime.run(
        messages="Call something unknown",
        tools=ToolRegistry("empty"),
        on_event=events.append,
    )

    assert res.status == "completed"
    assert "falling back" in res.final_text

    result_evts = [e for e in events if isinstance(e, ToolResultEvent)]
    assert len(result_evts) == 1
    assert result_evts[0].result["ok"] is False
    assert result_evts[0].error is not None and "is not registered" in result_evts[0].error


@pytest.mark.asyncio
async def test_top_level_run_convenience_function() -> None:
    """Verify top-level run() convenience function works identically."""
    chunks = [
        GatewayStreamChunk(text="Top level works!", model_id="test", alias_used="test"),
    ]
    model = MockModel([chunks])

    res = await run(
        messages="Test top level",
        model_caller=model,
    )

    assert res.status == "completed"
    assert res.final_text == "Top level works!"


@pytest.mark.asyncio
async def test_tool_execution_exception_resilience() -> None:
    """Verify tool raising an exception is caught and returned as an error result."""

    @tool(name="failing_tool", description="Always fails", risk=ToolRisk.SAFE)
    def failing_tool() -> dict[str, str]:
        raise ValueError("Network timeout inside tool")

    registry = ToolRegistry("fail_test")
    registry.register(failing_tool)

    round_1 = [
        GatewayStreamChunk(
            tool_calls=[
                ToolCallRequest(
                    call_id="call_fail_1",
                    tool_name="failing_tool",
                    arguments={},
                )
            ],
            model_id="test",
            alias_used="test",
        )
    ]
    round_2 = [
        GatewayStreamChunk(
            text="Tool encountered an error as expected.",
            model_id="test",
            alias_used="test",
        )
    ]

    model = MockModel([round_1, round_2])
    runtime = AgentRuntime(model_caller=model)

    events: list[AgentEvent] = []
    res = await runtime.run(
        messages="Try failing tool",
        tools=registry,
        on_event=events.append,
    )

    assert res.status == "completed"
    assert "Tool encountered an error" in res.final_text

    result_evts = [e for e in events if isinstance(e, ToolResultEvent)]
    assert len(result_evts) == 1
    assert result_evts[0].result["ok"] is False
    assert (
        result_evts[0].error is not None and "Network timeout inside tool" in result_evts[0].error
    )


@pytest.mark.asyncio
async def test_multiple_tool_calls_in_single_turn() -> None:
    """Verify model requesting multiple tool calls in a single turn executes all of them."""
    calls_executed: list[str] = []

    @tool(name="step_a", description="Step A", risk=ToolRisk.SAFE)
    def step_a() -> dict[str, str]:
        calls_executed.append("a")
        return {"step": "A done"}

    @tool(name="step_b", description="Step B", risk=ToolRisk.SAFE)
    def step_b() -> dict[str, str]:
        calls_executed.append("b")
        return {"step": "B done"}

    registry = ToolRegistry("multi_test")
    registry.register(step_a)
    registry.register(step_b)

    round_1 = [
        GatewayStreamChunk(
            tool_calls=[
                ToolCallRequest(call_id="c1", tool_name="step_a", arguments={}),
                ToolCallRequest(call_id="c2", tool_name="step_b", arguments={}),
            ],
            model_id="test",
            alias_used="test",
        )
    ]
    round_2 = [
        GatewayStreamChunk(
            text="Both steps completed.",
            model_id="test",
            alias_used="test",
        )
    ]

    model = MockModel([round_1, round_2])
    runtime = AgentRuntime(model_caller=model)

    events: list[AgentEvent] = []
    res = await runtime.run(
        messages="Run both steps",
        tools=registry,
        on_event=events.append,
    )

    assert res.status == "completed"
    assert calls_executed == ["a", "b"]
    assert res.tool_calls_count == 2


@pytest.mark.asyncio
async def test_approval_required_without_callback_denies() -> None:
    """Verify APPROVAL_REQUIRED tool is safely denied if no approval_callback is provided."""

    @tool(name="risky_op", description="Risky operation", risk=ToolRisk.APPROVAL_REQUIRED)
    def risky_op() -> dict[str, str]:
        return {"success": "dangerous"}

    registry = ToolRegistry("no_cb_test")
    registry.register(risky_op)

    round_1 = [
        GatewayStreamChunk(
            tool_calls=[ToolCallRequest(call_id="c_risk", tool_name="risky_op", arguments={})],
            model_id="test",
            alias_used="test",
        )
    ]
    round_2 = [
        GatewayStreamChunk(
            text="Operation denied due to missing approval callback.",
            model_id="test",
            alias_used="test",
        )
    ]

    model = MockModel([round_1, round_2])
    runtime = AgentRuntime(model_caller=model)

    events: list[AgentEvent] = []
    res = await runtime.run(
        messages="Perform risky op",
        tools=registry,
        approval_callback=None,  # No callback provided
        on_event=events.append,
    )

    assert res.status == "completed"
    tool_results = [e for e in events if isinstance(e, ToolResultEvent)]
    assert len(tool_results) == 1
    assert tool_results[0].result["ok"] is False
    assert tool_results[0].error is not None and "denied or not approved" in tool_results[0].error


@pytest.mark.asyncio
async def test_async_generator_model_caller() -> None:
    """Verify AgentRuntime works when model_caller is an async generator."""

    async def async_model(history: Any, **kwargs: Any) -> AsyncGenerator[GatewayStreamChunk, None]:
        yield GatewayStreamChunk(text="Async chunk 1 ", model_id="test", alias_used="test")
        yield GatewayStreamChunk(text="Async chunk 2", model_id="test", alias_used="test")

    runtime = AgentRuntime(model_caller=async_model)
    res = await runtime.run(messages="Test async model caller")

    assert res.status == "completed"
    assert res.final_text == "Async chunk 1 Async chunk 2"
