"""Dynamic Planning Todo Tool.

Allows agents and models to dynamically plan, maintain, track, and update their
step-by-step task checklist and execution state:
- actions: "create", "update", "mark_done", "reorder", "view", "clear"
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.tools.base import BaseTool, ToolExecutionContext, ToolRisk
from app.tools.registry import ToolRegistry


class TodoItem(BaseModel):
    """A single task item in the dynamic plan checklist."""

    model_config = ConfigDict(extra="ignore")

    id: str
    title: str
    status: str = Field(default="pending")  # pending, in_progress, done, blocked
    priority: str = Field(default="normal")  # low, normal, high
    note: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass
class TodoStore:
    """In-memory and file-backed store for active todo checklists."""

    tasks: list[TodoItem] = field(default_factory=list)
    note: str | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def _next_id(self) -> str:
        existing_ids = {t.id for t in self.tasks}
        idx = len(self.tasks) + 1
        while f"task_{idx}" in existing_ids:
            idx += 1
        return f"task_{idx}"

    async def create(
        self, items: list[dict[str, Any] | str], note: str | None = None
    ) -> list[TodoItem]:
        async with self._lock:
            created: list[TodoItem] = []
            for item in items:
                now = datetime.now(UTC).isoformat()
                if isinstance(item, str):
                    task = TodoItem(
                        id=self._next_id(),
                        title=item.strip(),
                        status="pending",
                        priority="normal",
                        created_at=now,
                        updated_at=now,
                    )
                elif isinstance(item, dict):
                    task_id = str(item.get("id") or self._next_id())
                    task = TodoItem(
                        id=task_id,
                        title=str(item.get("title", f"Task {task_id}")).strip(),
                        status=str(item.get("status", "pending")),
                        priority=str(item.get("priority", "normal")),
                        note=item.get("note"),
                        created_at=now,
                        updated_at=now,
                    )
                else:
                    continue
                self.tasks.append(task)
                created.append(task)
            if note is not None:
                self.note = note
            return created

    async def update(self, items: list[dict[str, Any]], note: str | None = None) -> list[TodoItem]:
        async with self._lock:
            updated: list[TodoItem] = []
            task_map = {t.id: t for t in self.tasks}
            now = datetime.now(UTC).isoformat()

            for item in items:
                task_id = str(item.get("id", ""))
                task = task_map.get(task_id)
                if not task:
                    continue
                if "title" in item and item["title"]:
                    task.title = str(item["title"]).strip()
                if "status" in item and item["status"]:
                    task.status = str(item["status"])
                if "priority" in item and item["priority"]:
                    task.priority = str(item["priority"])
                if "note" in item:
                    task.note = item["note"]
                task.updated_at = now
                updated.append(task)

            if note is not None:
                self.note = note
            return updated

    async def mark_done(
        self, ids_or_items: list[str | dict[str, Any]], note: str | None = None
    ) -> list[TodoItem]:
        target_ids: set[str] = set()
        for x in ids_or_items:
            if isinstance(x, str):
                target_ids.add(x)
            elif isinstance(x, dict) and "id" in x:
                target_ids.add(str(x["id"]))

        async with self._lock:
            marked: list[TodoItem] = []
            now = datetime.now(UTC).isoformat()
            for task in self.tasks:
                if task.id in target_ids:
                    task.status = "done"
                    task.updated_at = now
                    marked.append(task)

            if note is not None:
                self.note = note
            return marked

    async def reorder(self, order_ids: list[str], note: str | None = None) -> list[TodoItem]:
        async with self._lock:
            task_map = {t.id: t for t in self.tasks}
            reordered: list[TodoItem] = []
            seen: set[str] = set()

            for tid in order_ids:
                if tid in task_map and tid not in seen:
                    reordered.append(task_map[tid])
                    seen.add(tid)

            # Append any remaining tasks
            for t in self.tasks:
                if t.id not in seen:
                    reordered.append(t)
                    seen.add(t.id)

            self.tasks = reordered
            if note is not None:
                self.note = note
            return self.tasks

    async def view(self) -> dict[str, Any]:
        async with self._lock:
            counts = {
                "total": len(self.tasks),
                "pending": sum(1 for t in self.tasks if t.status == "pending"),
                "in_progress": sum(1 for t in self.tasks if t.status == "in_progress"),
                "done": sum(1 for t in self.tasks if t.status == "done"),
                "blocked": sum(1 for t in self.tasks if t.status == "blocked"),
            }
            return {
                "ok": True,
                "tasks": [t.model_dump() for t in self.tasks],
                "counts": counts,
                "note": self.note,
            }

    async def clear(self) -> dict[str, Any]:
        async with self._lock:
            count = len(self.tasks)
            self.tasks.clear()
            self.note = None
            return {"ok": True, "cleared_tasks": count}


# Session-keyed registry of stores
_STORES: dict[str, TodoStore] = {}
_DEFAULT_STORE = TodoStore()


def get_todo_store(context: ToolExecutionContext | None = None) -> TodoStore:
    """Retrieve or create the TodoStore for the current context."""
    if context is None:
        return _DEFAULT_STORE
    key = str(context.session_id or context.workspace_id or "default")
    if key not in _STORES:
        _STORES[key] = TodoStore()
    return _STORES[key]


async def todo_manage(
    action: str,
    tasks: list[Any] | None = None,
    note: str | None = None,
    *,
    context: ToolExecutionContext | None = None,
) -> dict[str, Any]:
    """Execute dynamic planning actions against the active todo checklist."""
    store = get_todo_store(context)
    action_clean = (action or "").strip().lower()

    if action_clean == "create":
        if not tasks:
            return {"ok": False, "error": "Action 'create' requires 'tasks' list."}
        created = await store.create(tasks, note=note)
        view_state = await store.view()
        return {
            "ok": True,
            "action": "create",
            "created_count": len(created),
            **view_state,
        }

    elif action_clean == "update":
        if not tasks:
            return {"ok": False, "error": "Action 'update' requires 'tasks' list with IDs."}
        updated = await store.update(tasks, note=note)
        view_state = await store.view()
        return {
            "ok": True,
            "action": "update",
            "updated_count": len(updated),
            **view_state,
        }

    elif action_clean == "mark_done":
        if not tasks:
            return {"ok": False, "error": "Action 'mark_done' requires 'tasks' list of IDs."}
        marked = await store.mark_done(tasks, note=note)
        view_state = await store.view()
        return {
            "ok": True,
            "action": "mark_done",
            "marked_count": len(marked),
            **view_state,
        }

    elif action_clean == "reorder":
        if not tasks:
            return {"ok": False, "error": "Action 'reorder' requires 'tasks' list of IDs in order."}
        reordered = await store.reorder(
            [str(t if isinstance(t, str) else t.get("id")) for t in tasks], note=note
        )
        view_state = await store.view()
        return {
            "ok": True,
            "action": "reorder",
            "task_count": len(reordered),
            **view_state,
        }

    elif action_clean == "view":
        return await store.view()

    elif action_clean == "clear":
        return await store.clear()

    else:
        return {
            "ok": False,
            "error": (
                f"Unknown action '{action}'. "
                "Supported: 'create', 'update', 'mark_done', 'reorder', 'view', 'clear'."
            ),
        }


class TodoManageTool(BaseTool):
    """Dynamic planning tool to maintain task checklists."""

    name = "todo_manage"
    description = (
        "Manage the dynamic task checklist and execution plan: create tasks, update status "
        "(pending, in_progress, done, blocked), mark done, reorder, or view current progress."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "update", "mark_done", "reorder", "view", "clear"],
                "description": "The planning action to perform.",
            },
            "tasks": {
                "type": "array",
                "items": {"type": "object"},
                "description": (
                    "Tasks to process. For 'create': list of task items or strings. "
                    "For 'update': list of task objects with id and fields to update. "
                    "For 'mark_done' and 'reorder': list of task IDs."
                ),
            },
            "note": {
                "type": "string",
                "description": "Optional note or reasoning to accompany this checklist change.",
            },
        },
        "required": ["action"],
    }
    risk = ToolRisk.SAFE
    category = "planning"
    tags = ["todo", "planning", "tasks"]

    async def execute(self, context: ToolExecutionContext, **kwargs: Any) -> Any:
        action = kwargs.get("action", "view")
        tasks = kwargs.get("tasks")
        note = kwargs.get("note")
        return await todo_manage(action, tasks=tasks, note=note, context=context)


def register_todo_tools(registry: ToolRegistry) -> None:
    """Register the dynamic planning todo tool in the registry."""
    registry.register(TodoManageTool())
