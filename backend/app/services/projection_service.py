"""Relational state projection engine syncing LangGraph checkpoints to tasks and subtasks."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_DNS, NAMESPACE_URL, UUID, uuid5

from pydantic import TypeAdapter
from pydantic_core import PydanticSerializationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.agents.state import AgentExecutionState
from app.db.tenant import tenant_transaction

logger = logging.getLogger(__name__)

_JSON_VALUE_ADAPTER: TypeAdapter[Any] = TypeAdapter(Any)


def _pydantic_json_default(value: Any) -> Any:
    """Convert supported non-JSON values without changing standard JSON encoding."""
    try:
        return _JSON_VALUE_ADAPTER.dump_python(value, mode="json")
    except PydanticSerializationError as err:
        raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable") from err


def _json_object_key(value: Any) -> str:
    """Convert JSON-compatible scalar keys using Python JSON's native spelling."""
    if isinstance(value, str):
        return value
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value, allow_nan=False)

    try:
        serialized = _JSON_VALUE_ADAPTER.dump_python(value, mode="json")
    except PydanticSerializationError as err:
        raise TypeError(
            f"Object of type {type(value).__name__} is not JSON serializable as an object key"
        ) from err
    if isinstance(serialized, str):
        return serialized
    if serialized is None:
        return "null"
    if isinstance(serialized, bool):
        return "true" if serialized else "false"
    if isinstance(serialized, (int, float)):
        return json.dumps(serialized, allow_nan=False)
    raise TypeError(f"Object of type {type(value).__name__} is not a JSON scalar key")


def _normalize_json_object_keys(value: Any, ancestors: set[int] | None = None) -> Any:
    """Normalize nested mappings so supported Python key types survive JSON encoding."""
    if not isinstance(value, (dict, list, tuple)):
        return value
    if ancestors is None:
        ancestors = set()
    value_id = id(value)
    if value_id in ancestors:
        raise ValueError("Circular reference detected")
    ancestors.add(value_id)
    try:
        if isinstance(value, dict):
            return {
                _json_object_key(key): _normalize_json_object_keys(item, ancestors)
                for key, item in value.items()
            }
        return [_normalize_json_object_keys(item, ancestors) for item in value]
    finally:
        ancestors.remove(value_id)


def _dump_projection_json(value: Any) -> str:
    return json.dumps(
        _normalize_json_object_keys(value),
        default=_pydantic_json_default,
        allow_nan=False,
    )


_UPSERT_TASK = text(
    """
    INSERT INTO tasks (
        id, workspace_id, session_id, user_id, idempotency_key, title,
        instruction, dag_plan, status, tool_turns, completed_at
    ) VALUES (
        :id, :workspace_id, :session_id, :user_id, :idempotency_key, :title,
        :instruction, CAST(:dag_plan AS jsonb), :status, :tool_turns, :completed_at
    )
    ON CONFLICT (workspace_id, id) DO UPDATE SET
        dag_plan = EXCLUDED.dag_plan,
        status = EXCLUDED.status,
        tool_turns = EXCLUDED.tool_turns,
        completed_at = EXCLUDED.completed_at
    RETURNING id
    """
)

_UPSERT_SUBTASK = text(
    """
    INSERT INTO subtasks (
        id, workspace_id, task_id, assigned_agent, description,
        dependencies, status, input_data, output_result, verification_proof,
        error_message, retry_count, completed_at
    ) VALUES (
        :id, :workspace_id, :task_id, :assigned_agent, :description,
        CAST(:dependencies AS jsonb), :status, CAST(:input_data AS jsonb),
        CAST(:output_result AS jsonb), CAST(:verification_proof AS jsonb),
        :error_message, :retry_count, :completed_at
    )
    ON CONFLICT (workspace_id, id) DO UPDATE SET
        assigned_agent = EXCLUDED.assigned_agent,
        description = EXCLUDED.description,
        dependencies = EXCLUDED.dependencies,
        status = EXCLUDED.status,
        input_data = EXCLUDED.input_data,
        output_result = EXCLUDED.output_result,
        verification_proof = EXCLUDED.verification_proof,
        error_message = EXCLUDED.error_message,
        retry_count = EXCLUDED.retry_count,
        completed_at = EXCLUDED.completed_at
    RETURNING id
    """
)


def map_plan_status_to_task_status(plan_status: str, has_pending_approval: bool) -> str:
    """Map LangGraph plan_status to tasks.status database constraint values."""
    if has_pending_approval or plan_status == "paused":
        return "awaiting_approval"
    if plan_status == "planning":
        return "pending"
    if plan_status == "executing":
        return "executing"
    if plan_status == "completed":
        return "completed"
    if plan_status == "failed":
        return "failed"
    return "pending"


def resolve_subtask_uuid(task_id: UUID, subtask_id: str) -> UUID:
    """Deterministically convert a subtask string identifier to a UUID."""
    try:
        return UUID(subtask_id)
    except ValueError:
        return uuid5(task_id, subtask_id)


class StateProjectionService:
    """Projects LangGraph AgentExecutionState into relational tasks and subtasks tables.

    All mutations are idempotent upserts scoped by workspace_id to preserve tenant isolation.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def project_state(
        self,
        state: AgentExecutionState,
        *,
        task_id: UUID | None = None,
        idempotency_key: str | None = None,
        title: str | None = None,
        connection: AsyncConnection | None = None,
    ) -> UUID:
        """Project current execution state into the relational database.

        If connection is None, creates a tenant_transaction scoped to state.workspace_id.
        """
        workspace_id = UUID(state.workspace_id)
        session_id = UUID(state.session_id)
        user_id = UUID(state.user_id)

        context_idempotency_key = state.shared_context.get("idempotency_key")
        resolved_idempotency_key = (
            idempotency_key
            if idempotency_key is not None
            else (str(context_idempotency_key) if context_idempotency_key is not None else None)
        )

        # Resolve primary task UUID. An idempotency key is workspace-scoped and stable even
        # when retries arrive with a different session ID.
        if task_id is not None:
            resolved_task_id = task_id
        elif state.shared_context.get("task_id") is not None:
            resolved_task_id = UUID(str(state.shared_context["task_id"]))
        elif resolved_idempotency_key is not None:
            resolved_task_id = uuid5(
                NAMESPACE_URL,
                f"maya:tasks:{workspace_id}:idempotency:{resolved_idempotency_key}",
            )
        else:
            resolved_task_id = uuid5(NAMESPACE_DNS, f"{workspace_id}:{session_id}")

        if resolved_idempotency_key is None:
            resolved_idempotency_key = f"task_{resolved_task_id.hex}"

        if title:
            resolved_title = title
        elif "title" in state.shared_context:
            resolved_title = str(state.shared_context["title"])
        elif state.user_instruction:
            resolved_title = state.user_instruction[:200]
        else:
            resolved_title = "Task"

        has_pending_approval = state.pending_approval is not None or any(
            st.status == "awaiting_approval" for st in state.active_plan
        )
        task_status = map_plan_status_to_task_status(state.plan_status, has_pending_approval)

        now = datetime.now(UTC)
        completed_at = now if task_status in ("completed", "failed") else None

        # Build DAG plan JSON
        dag_plan_dict = {
            "intent": state.shared_context.get("classified_intent", ""),
            "subtasks": [st.model_dump() for st in state.active_plan],
            "plan_status": state.plan_status,
            "completed_task_ids": state.completed_task_ids,
        }

        task_params = {
            "id": resolved_task_id,
            "workspace_id": workspace_id,
            "session_id": session_id,
            "user_id": user_id,
            "idempotency_key": resolved_idempotency_key,
            "title": resolved_title,
            "instruction": state.user_instruction,
            "dag_plan": _dump_projection_json(dag_plan_dict),
            "status": task_status,
            "tool_turns": len(state.execution_trace),
            "completed_at": completed_at,
        }

        async def _execute_projection(conn: AsyncConnection) -> None:
            # 1. Upsert tasks table row
            await conn.execute(_UPSERT_TASK, task_params)

            # 2. Upsert each subtask row
            for st in state.active_plan:
                st_uuid = resolve_subtask_uuid(resolved_task_id, st.id)
                st_completed_at = now if st.status in ("completed", "failed") else None

                output_json = (
                    _dump_projection_json(st.output_result)
                    if st.output_result is not None
                    else None
                )
                proof_json = (
                    _dump_projection_json(st.verification_proof)
                    if st.verification_proof is not None
                    else None
                )

                subtask_params = {
                    "id": st_uuid,
                    "workspace_id": workspace_id,
                    "task_id": resolved_task_id,
                    "assigned_agent": st.assigned_agent,
                    "description": st.description,
                    "dependencies": json.dumps(st.dependencies),
                    "status": st.status,
                    "input_data": _dump_projection_json(st.input_data),
                    "output_result": output_json,
                    "verification_proof": proof_json,
                    "error_message": st.error_message,
                    "retry_count": st.retry_count,
                    "completed_at": st_completed_at,
                }
                await conn.execute(_UPSERT_SUBTASK, subtask_params)

        if connection is not None:
            await _execute_projection(connection)
        else:
            async with tenant_transaction(self._engine, workspace_id) as conn:
                await _execute_projection(conn)

        return resolved_task_id
