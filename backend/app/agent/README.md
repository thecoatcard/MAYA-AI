# Unified Agent Runtime

The iterative ReAct agent loop and runtime engine for MAYA AI digital employees.

## Overview

The `app.agent` module implements an Antigravity/Cowork-style iterative ReAct loop capable of multi-round reasoning and tool execution across modalities (chat, background tasks, and voice):

- `runtime.py`: Core `AgentRuntime` engine and `run()` entrypoint executing multi-round turns (configurable `RuntimeBudget`, supporting up to 30–50+ rounds). Enforces three-tier risk policies:
  - `SAFE`: Autonomous execution with zero side-effects.
  - `APPROVAL_REQUIRED`: Dispatches `ApprovalNeededEvent` and queries `approval_callback` before invoking; denies gracefully if unapproved.
  - `RESTRICTED`: Halts the execution loop immediately and emits `DoneEvent(status="restricted_tool_rejected")`.
- `events.py`: Streaming lifecycle events emitted during execution:
  - `TextDeltaEvent`: Streamed text deltas and thought summaries.
  - `ToolCallEvent`: Tool call initiation notifications.
  - `ToolResultEvent`: Completed tool results or errors.
  - `ApprovalNeededEvent`: Human-in-the-loop confirmation requests.
  - `TodoStateEvent`: Dynamic checklist state updates synced live with `TodoStore`.
  - `DoneEvent`: Terminal event signaling completion, round exhaustion, or budget limits.
- `loop.py`: Convenience re-exports and `run_agent_loop` interface.

## Usage

```python
from app.agent import AgentRuntime, RuntimeBudget
from app.tools.registry import get_global_registry

runtime = AgentRuntime()
result = await runtime.run(
    messages="Analyze workspace and summarize status",
    tools=get_global_registry(),
    budget=RuntimeBudget(max_rounds=30, max_tokens=20000),
    on_event=lambda evt: print(evt),
)
print(result.final_text)
```
