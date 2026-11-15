"""Unified Agent Runtime for MAYA AI.

Implements the Antigravity/Cowork-style iterative ReAct agent loop:
- Multi-round model generation with tool calling (configurable max_rounds, e.g. 30-50).
- Live event streaming: TextDeltaEvent, ToolCallEvent, ToolResultEvent,
  ApprovalNeededEvent, TodoStateEvent, DoneEvent.
- Three-tier tool risk checks:
  - SAFE: Executes autonomously.
  - APPROVAL_REQUIRED: Prompts approval_callback before execution; denies if unapproved.
  - RESTRICTED: Halts loop immediately.
- Contextual multi-turn dialogue management feeding function calls and responses back into history.
- Live synchronization with TodoStore for real-time task checklist tracking.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.agent.events import (
    AgentEvent,
    ApprovalNeededEvent,
    DoneEvent,
    TextDeltaEvent,
    TodoStateEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from app.gateway.model_gateway import (
    ModelGateway,
    tool_call_content,
    tool_response_content,
)
from app.gateway.schemas import (
    GatewayStreamChunk,
    ToolCallRequest,
)
from app.gateway.schemas import (
    ToolDefinition as GatewayToolDefinition,
)
from app.tools.base import (
    BaseTool,
    ToolDefinition,
    ToolExecutionContext,
    ToolRisk,
)
from app.tools.registry import ToolRegistry, get_global_registry
from app.tools.todo import get_todo_store

logger = logging.getLogger(__name__)


@dataclass
class RuntimeBudget:
    """Execution constraints and budget limits for the agent loop."""

    max_rounds: int = 30
    max_tokens: int | None = None
    max_cost_usd: float | Decimal | None = None
    timeout_seconds: float | None = None


@dataclass
class AgentExecutionResult:
    """Final result produced by the AgentRuntime execution loop."""

    final_text: str
    rounds_used: int
    tool_calls_count: int
    total_tokens: int
    status: str  # completed, max_rounds_reached, budget_exceeded, restricted_tool_rejected, error
    messages: list[dict[str, Any]] = field(default_factory=list)
    events: list[AgentEvent] = field(default_factory=list)
    error: str | None = None


EventCallback = Callable[[AgentEvent], Any | Awaitable[Any]]
ApprovalCallback = Callable[..., Any | Awaitable[Any]]
ModelCaller = Callable[..., Any]


class AgentRuntime:
    """Iterative ReAct execution engine powering MAYA AI agents."""

    def __init__(
        self,
        gateway: ModelGateway | None = None,
        model_alias: str = "default",
        model_caller: ModelCaller | None = None,
        default_budget: RuntimeBudget | None = None,
        system_instruction: str | None = None,
    ) -> None:
        self._gateway = gateway
        self._model_alias = model_alias
        self._model_caller = model_caller
        self._default_budget = default_budget or RuntimeBudget()
        self._system_instruction = system_instruction

    async def _emit(
        self, event: AgentEvent, callback: EventCallback | None, log: list[AgentEvent]
    ) -> None:
        """Append event to log and invoke callback if provided."""
        log.append(event)
        if callback is not None:
            try:
                res = callback(event)
                if inspect.isawaitable(res):
                    await res
            except Exception as exc:
                logger.warning("Error in event callback: %s", exc)

    async def _invoke_approval(
        self,
        callback: ApprovalCallback,
        call: ToolCallRequest,
    ) -> bool:
        """Safely invoke user approval callback across flexible signatures."""
        try:
            sig = inspect.signature(callback)
            params = set(sig.parameters.keys())
            param_count = len(sig.parameters)

            if "call" in params:
                res = callback(call=call)
            elif {"tool_name", "arguments"}.issubset(params):
                res = callback(
                    tool_name=call.tool_name, arguments=call.arguments, call_id=call.call_id
                )
            elif param_count == 1:
                res = callback(call)
            elif param_count == 2:
                res = callback(call.tool_name, call.arguments)
            elif param_count >= 3:
                res = callback(call.tool_name, call.arguments, call.call_id)
            else:
                res = callback(
                    tool_name=call.tool_name, arguments=call.arguments, call_id=call.call_id
                )

            if inspect.isawaitable(res):
                res = await res
            return bool(res)
        except Exception as exc:
            logger.warning("Error invoking approval callback: %s", exc)
            return False

    async def _execute_tool(
        self,
        tool: BaseTool,
        call: ToolCallRequest,
        context: ToolExecutionContext,
    ) -> Any:
        """Execute a tool safely, returning result or capturing exceptions."""
        call_ctx = ToolExecutionContext(
            workspace_root=context.workspace_root,
            workspace_id=context.workspace_id,
            user_id=context.user_id,
            session_id=context.session_id,
            call_id=call.call_id,
            extra=dict(context.extra),
        )
        try:
            return await tool.execute(call_ctx, **call.arguments)
        except Exception as exc:
            logger.exception("Tool '%s' execution raised exception: %s", call.tool_name, exc)
            return {"ok": False, "error": str(exc)}

    async def _stream_model_chunks(
        self,
        history: list[dict[str, Any]],
        tools: list[ToolDefinition],
        system_instruction: str | None,
        model_alias: str,
    ) -> AsyncGenerator[GatewayStreamChunk, None]:
        """Obtain a stream of chunks from custom model_caller or ModelGateway."""
        if self._model_caller is not None:
            raw_output = self._model_caller(
                history,
                tools=tools,
                system_instruction=system_instruction,
                model_alias=model_alias,
            )
            if inspect.isasyncgen(raw_output):
                async for chunk in raw_output:
                    if isinstance(chunk, GatewayStreamChunk):
                        yield chunk
            elif inspect.isgenerator(raw_output):
                for chunk in raw_output:
                    if isinstance(chunk, GatewayStreamChunk):
                        yield chunk
            elif inspect.isawaitable(raw_output):
                result = await raw_output
                if isinstance(result, GatewayStreamChunk):
                    yield result
                elif isinstance(result, list):
                    for item in result:
                        if isinstance(item, GatewayStreamChunk):
                            yield item
                        elif isinstance(item, dict):
                            yield GatewayStreamChunk(
                                text=item.get("text", ""),
                                thought=item.get("thought", ""),
                                tool_calls=[
                                    ToolCallRequest(**tc) if isinstance(tc, dict) else tc
                                    for tc in item.get("tool_calls", [])
                                ],
                                model_id=item.get("model_id", "mock"),
                                alias_used=model_alias,
                            )
                elif isinstance(result, dict):
                    yield GatewayStreamChunk(
                        text=result.get("text", ""),
                        thought=result.get("thought", ""),
                        tool_calls=[
                            ToolCallRequest(**tc) if isinstance(tc, dict) else tc
                            for tc in result.get("tool_calls", [])
                        ],
                        model_id=result.get("model_id", "mock"),
                        alias_used=model_alias,
                    )
                else:
                    text_val = getattr(result, "text", "") or ""
                    tc_val = getattr(result, "tool_calls", []) or []
                    yield GatewayStreamChunk(
                        text=text_val,
                        tool_calls=tc_val,
                        model_id=getattr(result, "model_id", "mock"),
                        alias_used=model_alias,
                    )
            elif isinstance(raw_output, (list, tuple)):
                for item in raw_output:
                    if isinstance(item, GatewayStreamChunk):
                        yield item
                    elif isinstance(item, dict):
                        yield GatewayStreamChunk(
                            text=item.get("text", ""),
                            thought=item.get("thought", ""),
                            tool_calls=[
                                ToolCallRequest(**tc) if isinstance(tc, dict) else tc
                                for tc in item.get("tool_calls", [])
                            ],
                            model_id=item.get("model_id", "mock"),
                            alias_used=model_alias,
                        )
                    else:
                        text_val = getattr(item, "text", "") or ""
                        tc_val = getattr(item, "tool_calls", []) or []
                        yield GatewayStreamChunk(
                            text=text_val,
                            tool_calls=tc_val,
                            model_id=getattr(item, "model_id", "mock"),
                            alias_used=model_alias,
                        )
            elif isinstance(raw_output, GatewayStreamChunk):
                yield raw_output
            elif isinstance(raw_output, dict):
                yield GatewayStreamChunk(
                    text=raw_output.get("text", ""),
                    thought=raw_output.get("thought", ""),
                    tool_calls=[
                        ToolCallRequest(**tc) if isinstance(tc, dict) else tc
                        for tc in raw_output.get("tool_calls", [])
                    ],
                    model_id=raw_output.get("model_id", "mock"),
                    alias_used=model_alias,
                )
            else:
                text_val = getattr(raw_output, "text", "") or ""
                tc_val = getattr(raw_output, "tool_calls", []) or []
                yield GatewayStreamChunk(
                    text=text_val,
                    tool_calls=tc_val,
                    model_id=getattr(raw_output, "model_id", "mock"),
                    alias_used=model_alias,
                )
            return

        if self._gateway is not None:
            gateway_tools = [
                GatewayToolDefinition(
                    name=t.name,
                    description=t.description,
                    parameters=t.parameters,
                )
                for t in tools
            ]
            async for chunk in self._gateway.generate_stream(
                alias=model_alias,
                contents=history,
                tools=gateway_tools if gateway_tools else None,
                system_instruction=system_instruction,
            ):
                yield chunk
            return

        raise RuntimeError(
            "AgentRuntime requires either a ModelGateway or a model_caller to execute."
        )

    async def run(
        self,
        messages: list[dict[str, Any]] | str,
        tools: ToolRegistry | list[BaseTool] | None = None,
        context: ToolExecutionContext | None = None,
        budget: RuntimeBudget | int | None = None,
        on_event: EventCallback | None = None,
        approval_callback: ApprovalCallback | None = None,
        system_instruction: str | None = None,
        model_alias: str | None = None,
    ) -> AgentExecutionResult:
        """Run the iterative agent loop until completion, round cap, or error."""
        # 1. Normalize messages
        if isinstance(messages, str):
            history: list[dict[str, Any]] = [{"role": "user", "parts": [{"text": messages}]}]
        else:
            history = list(messages)

        # 2. Normalize tools and registry
        registry: ToolRegistry
        if isinstance(tools, ToolRegistry):
            registry = tools
        elif isinstance(tools, list):
            registry = ToolRegistry("runtime_tools")
            for t in tools:
                registry.register(t)
        elif tools is None:
            registry = get_global_registry()
        else:
            registry = ToolRegistry("empty")

        tool_definitions: list[ToolDefinition] = registry.definitions()

        # 3. Normalize context and budget
        exec_context = context if context is not None else ToolExecutionContext()
        exec_budget: RuntimeBudget
        if budget is None:
            exec_budget = self._default_budget
        elif isinstance(budget, int):
            exec_budget = RuntimeBudget(max_rounds=budget)
        elif isinstance(budget, dict):
            exec_budget = RuntimeBudget(**budget)
        else:
            exec_budget = budget

        sys_inst = system_instruction or self._system_instruction
        active_alias = model_alias or self._model_alias

        events_history: list[AgentEvent] = []
        rounds_used = 0
        total_tool_calls = 0
        total_tokens = 0
        accumulated_text = ""

        # Emit initial TodoStateEvent if tasks already exist in context's TodoStore
        todo_store = get_todo_store(exec_context)
        if todo_store.tasks:
            todo_view = await todo_store.view()
            await self._emit(
                TodoStateEvent(
                    tasks=todo_view["tasks"],
                    counts=todo_view["counts"],
                    note=todo_view.get("note"),
                    round_index=0,
                ),
                on_event,
                events_history,
            )

        # Main iterative ReAct loop
        for round_idx in range(exec_budget.max_rounds):
            rounds_used += 1

            # Check token budget before starting round
            if exec_budget.max_tokens is not None and total_tokens >= exec_budget.max_tokens:
                err_msg = (
                    f"Token budget of {exec_budget.max_tokens} exceeded (used {total_tokens})."
                )
                done_evt = DoneEvent(
                    final_text=accumulated_text,
                    rounds_used=rounds_used,
                    total_tool_calls=total_tool_calls,
                    total_tokens=total_tokens,
                    status="budget_exceeded",
                    error=err_msg,
                    round_index=round_idx,
                )
                await self._emit(done_evt, on_event, events_history)
                return AgentExecutionResult(
                    final_text=accumulated_text,
                    rounds_used=rounds_used,
                    tool_calls_count=total_tool_calls,
                    total_tokens=total_tokens,
                    status="budget_exceeded",
                    messages=history,
                    events=events_history,
                    error=err_msg,
                )

            round_text_parts: list[str] = []
            round_tool_calls: list[ToolCallRequest] = []

            # Stream chunks from model
            try:
                async for chunk in self._stream_model_chunks(
                    history=history,
                    tools=tool_definitions,
                    system_instruction=sys_inst,
                    model_alias=active_alias,
                ):
                    if chunk.usage is not None:
                        total_tokens += chunk.usage.total_token_count

                    if chunk.thought:
                        await self._emit(
                            TextDeltaEvent(delta="", thought=chunk.thought, round_index=round_idx),
                            on_event,
                            events_history,
                        )

                    if chunk.text:
                        round_text_parts.append(chunk.text)
                        await self._emit(
                            TextDeltaEvent(delta=chunk.text, round_index=round_idx),
                            on_event,
                            events_history,
                        )

                    if chunk.tool_calls:
                        round_tool_calls.extend(chunk.tool_calls)
            except Exception as stream_err:
                logger.exception(
                    "Error during model streaming in round %d: %s", round_idx, stream_err
                )
                err_str = f"Model streaming failed: {stream_err}"
                done_evt = DoneEvent(
                    final_text=accumulated_text,
                    rounds_used=rounds_used,
                    total_tool_calls=total_tool_calls,
                    total_tokens=total_tokens,
                    status="error",
                    error=err_str,
                    round_index=round_idx,
                )
                await self._emit(done_evt, on_event, events_history)
                return AgentExecutionResult(
                    final_text=accumulated_text,
                    rounds_used=rounds_used,
                    tool_calls_count=total_tool_calls,
                    total_tokens=total_tokens,
                    status="error",
                    messages=history,
                    events=events_history,
                    error=err_str,
                )

            round_text = "".join(round_text_parts)
            if round_text:
                accumulated_text = round_text

            # If no tool calls requested, generation is complete
            if not round_tool_calls:
                done_evt = DoneEvent(
                    final_text=accumulated_text,
                    rounds_used=rounds_used,
                    total_tool_calls=total_tool_calls,
                    total_tokens=total_tokens,
                    status="completed",
                    round_index=round_idx,
                )
                await self._emit(done_evt, on_event, events_history)
                return AgentExecutionResult(
                    final_text=accumulated_text,
                    rounds_used=rounds_used,
                    tool_calls_count=total_tool_calls,
                    total_tokens=total_tokens,
                    status="completed",
                    messages=history,
                    events=events_history,
                )

            # Process tool calls
            executed_calls: list[tuple[ToolCallRequest, Any]] = []
            halt_loop = False
            halt_status = ""
            halt_error: str | None = None

            for call in round_tool_calls:
                tool = registry.get(call.tool_name)
                if tool is None:
                    err = f"Tool '{call.tool_name}' is not registered."
                    res = {"ok": False, "error": err}
                    await self._emit(
                        ToolResultEvent(
                            call_id=call.call_id,
                            tool_name=call.tool_name,
                            result=res,
                            error=err,
                            round_index=round_idx,
                        ),
                        on_event,
                        events_history,
                    )
                    executed_calls.append((call, res))
                    continue

                risk = (
                    getattr(tool, "risk_level", None)
                    or getattr(tool, "risk", None)
                    or ToolRisk.SAFE
                )
                if isinstance(risk, str):
                    risk = ToolRisk(risk)

                # RESTRICTED: Halt immediately
                if risk == ToolRisk.RESTRICTED:
                    err = f"Tool '{call.tool_name}' is RESTRICTED and cannot be executed."
                    await self._emit(
                        ToolCallEvent(
                            call_id=call.call_id,
                            tool_name=call.tool_name,
                            arguments=call.arguments,
                            risk_level=risk,
                            round_index=round_idx,
                        ),
                        on_event,
                        events_history,
                    )
                    await self._emit(
                        ToolResultEvent(
                            call_id=call.call_id,
                            tool_name=call.tool_name,
                            result=None,
                            error=err,
                            round_index=round_idx,
                        ),
                        on_event,
                        events_history,
                    )
                    halt_loop = True
                    halt_status = "restricted_tool_rejected"
                    halt_error = err
                    break

                # APPROVAL_REQUIRED: Check approval callback
                elif risk == ToolRisk.APPROVAL_REQUIRED:
                    await self._emit(
                        ApprovalNeededEvent(
                            call_id=call.call_id,
                            tool_name=call.tool_name,
                            arguments=call.arguments,
                            risk_level=risk,
                            round_index=round_idx,
                        ),
                        on_event,
                        events_history,
                    )

                    is_approved = False
                    if approval_callback is not None:
                        is_approved = await self._invoke_approval(approval_callback, call)

                    if not is_approved:
                        err = f"Tool '{call.tool_name}' execution was denied or not approved."
                        res = {"ok": False, "error": err}
                        await self._emit(
                            ToolResultEvent(
                                call_id=call.call_id,
                                tool_name=call.tool_name,
                                result=res,
                                error=err,
                                round_index=round_idx,
                            ),
                            on_event,
                            events_history,
                        )
                        executed_calls.append((call, res))
                        continue

                    # Approved -> execute tool
                    await self._emit(
                        ToolCallEvent(
                            call_id=call.call_id,
                            tool_name=call.tool_name,
                            arguments=call.arguments,
                            risk_level=risk,
                            round_index=round_idx,
                        ),
                        on_event,
                        events_history,
                    )
                    total_tool_calls += 1
                    result = await self._execute_tool(tool, call, exec_context)
                    err_text: str | None = None
                    if (
                        isinstance(result, dict)
                        and not result.get("ok", True)
                        and "error" in result
                    ):
                        err_text = str(result["error"])
                    await self._emit(
                        ToolResultEvent(
                            call_id=call.call_id,
                            tool_name=call.tool_name,
                            result=result,
                            error=err_text,
                            round_index=round_idx,
                        ),
                        on_event,
                        events_history,
                    )
                    executed_calls.append((call, result))

                # SAFE: Execute autonomously
                else:
                    await self._emit(
                        ToolCallEvent(
                            call_id=call.call_id,
                            tool_name=call.tool_name,
                            arguments=call.arguments,
                            risk_level=risk,
                            round_index=round_idx,
                        ),
                        on_event,
                        events_history,
                    )
                    total_tool_calls += 1
                    result = await self._execute_tool(tool, call, exec_context)
                    err_text = None
                    if (
                        isinstance(result, dict)
                        and not result.get("ok", True)
                        and "error" in result
                    ):
                        err_text = str(result["error"])
                    await self._emit(
                        ToolResultEvent(
                            call_id=call.call_id,
                            tool_name=call.tool_name,
                            result=result,
                            error=err_text,
                            round_index=round_idx,
                        ),
                        on_event,
                        events_history,
                    )
                    executed_calls.append((call, result))

            # Sync TodoStore state live if updated or present
            todo_store = get_todo_store(exec_context)
            if todo_store.tasks:
                todo_view = await todo_store.view()
                await self._emit(
                    TodoStateEvent(
                        tasks=todo_view["tasks"],
                        counts=todo_view["counts"],
                        note=todo_view.get("note"),
                        round_index=round_idx,
                    ),
                    on_event,
                    events_history,
                )

            # If restricted tool was rejected, halt execution loop immediately
            if halt_loop:
                done_evt = DoneEvent(
                    final_text=accumulated_text,
                    rounds_used=rounds_used,
                    total_tool_calls=total_tool_calls,
                    total_tokens=total_tokens,
                    status=halt_status,
                    error=halt_error,
                    round_index=round_idx,
                )
                await self._emit(done_evt, on_event, events_history)
                return AgentExecutionResult(
                    final_text=accumulated_text,
                    rounds_used=rounds_used,
                    tool_calls_count=total_tool_calls,
                    total_tokens=total_tokens,
                    status=halt_status,
                    messages=history,
                    events=events_history,
                    error=halt_error,
                )

            # Append model turn and tool response turn to history
            model_parts: list[dict[str, Any]] = []
            if round_text.strip():
                model_parts.append({"text": round_text})
            for call, _ in executed_calls:
                model_parts.extend(tool_call_content(call)["parts"])
            history.append({"role": "model", "parts": model_parts})

            response_parts: list[dict[str, Any]] = []
            for call, res in executed_calls:
                res_dict = res if isinstance(res, dict) else {"result": res}
                response_parts.extend(tool_response_content(call, res_dict)["parts"])
            history.append({"role": "user", "parts": response_parts})

        # Max rounds reached
        err_msg = f"Max execution rounds ({exec_budget.max_rounds}) reached."
        done_evt = DoneEvent(
            final_text=accumulated_text,
            rounds_used=rounds_used,
            total_tool_calls=total_tool_calls,
            total_tokens=total_tokens,
            status="max_rounds_reached",
            error=err_msg,
            round_index=exec_budget.max_rounds - 1,
        )
        await self._emit(done_evt, on_event, events_history)
        return AgentExecutionResult(
            final_text=accumulated_text,
            rounds_used=rounds_used,
            tool_calls_count=total_tool_calls,
            total_tokens=total_tokens,
            status="max_rounds_reached",
            messages=history,
            events=events_history,
            error=err_msg,
        )

    async def run_stream(
        self,
        messages: list[dict[str, Any]] | str,
        tools: ToolRegistry | list[BaseTool] | None = None,
        context: ToolExecutionContext | None = None,
        budget: RuntimeBudget | int | None = None,
        approval_callback: ApprovalCallback | None = None,
        system_instruction: str | None = None,
        model_alias: str | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Stream events in real time from the iterative agent loop."""
        queue: asyncio.Queue[AgentEvent | Exception | None] = asyncio.Queue()

        async def _on_event(evt: AgentEvent) -> None:
            await queue.put(evt)

        async def _runner() -> None:
            try:
                await self.run(
                    messages=messages,
                    tools=tools,
                    context=context,
                    budget=budget,
                    on_event=_on_event,
                    approval_callback=approval_callback,
                    system_instruction=system_instruction,
                    model_alias=model_alias,
                )
            except Exception as exc:
                await queue.put(exc)
            finally:
                await queue.put(None)

        task = asyncio.create_task(_runner())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass


async def run(
    messages: list[dict[str, Any]] | str,
    tools: ToolRegistry | list[BaseTool] | None = None,
    context: ToolExecutionContext | None = None,
    budget: RuntimeBudget | int | None = None,
    on_event: EventCallback | None = None,
    approval_callback: ApprovalCallback | None = None,
    **kwargs: Any,
) -> AgentExecutionResult:
    """Convenience top-level entry point to execute the AgentRuntime."""
    runtime = AgentRuntime(**kwargs)
    return await runtime.run(
        messages=messages,
        tools=tools,
        context=context,
        budget=budget,
        on_event=on_event,
        approval_callback=approval_callback,
    )


async def run_stream(
    messages: list[dict[str, Any]] | str,
    tools: ToolRegistry | list[BaseTool] | None = None,
    context: ToolExecutionContext | None = None,
    budget: RuntimeBudget | int | None = None,
    approval_callback: ApprovalCallback | None = None,
    **kwargs: Any,
) -> AsyncGenerator[AgentEvent, None]:
    """Convenience top-level generator to stream events from AgentRuntime."""
    runtime = AgentRuntime(**kwargs)
    async for event in runtime.run_stream(
        messages=messages,
        tools=tools,
        context=context,
        budget=budget,
        approval_callback=approval_callback,
    ):
        yield event
