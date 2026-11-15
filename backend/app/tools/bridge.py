"""Bridges voice deliverables and chat tools into the Unified Tool Registry.

Ensures that tools across chat, voice, and background tasks share a single
unified tool registry with standardized risk categorization, schemas, and
execution interfaces.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

from app.gateway.schemas import ToolDefinition as GatewayToolDef
from app.tools.base import (
    BaseTool,
    ToolCallable,
    ToolExecutionContext,
    ToolRisk,
)
from app.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from app.voice.tools import VoiceToolRegistry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Voice Tool Adapters
# ---------------------------------------------------------------------------

VOICE_TOOL_RISK_MAP: dict[str, ToolRisk] = {
    "submit_task": ToolRisk.APPROVAL_REQUIRED,
    "get_task_status": ToolRisk.SAFE,
    "recall_memory": ToolRisk.SAFE,
    "save_memory": ToolRisk.APPROVAL_REQUIRED,
    "generate_image": ToolRisk.APPROVAL_REQUIRED,
    "web_search": ToolRisk.SAFE,
    "ask_user": ToolRisk.SAFE,
    "show_in_chat": ToolRisk.SAFE,
    "report_step_review": ToolRisk.SAFE,
}


class VoiceToolAdapter(BaseTool):
    """Bridges a VoiceTool declaration into the Unified BaseTool interface."""

    def __init__(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        voice_registry: VoiceToolRegistry | None = None,
        risk: ToolRisk | None = None,
        category: str = "voice",
        tags: list[str] | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters
        self.risk = risk or VOICE_TOOL_RISK_MAP.get(name, ToolRisk.SAFE)
        self.category = category
        self.tags = list(tags) if tags is not None else ["voice"]
        self._voice_registry = voice_registry

    async def execute(self, context: ToolExecutionContext, **kwargs: Any) -> Any:
        if self._voice_registry is not None:
            from app.voice.tools import VoiceExecutionContext

            # Coerce context IDs to UUIDs if valid
            ws_id = (
                context.workspace_id
                if isinstance(context.workspace_id, uuid.UUID)
                else (
                    uuid.UUID(str(context.workspace_id)) if context.workspace_id else uuid.uuid4()
                )
            )
            u_id = (
                context.user_id
                if isinstance(context.user_id, uuid.UUID)
                else (uuid.UUID(str(context.user_id)) if context.user_id else uuid.uuid4())
            )

            voice_context = VoiceExecutionContext(
                workspace_id=ws_id,
                user_id=u_id,
                session_id=context.session_id,
                call_id=context.call_id,
            )
            return await self._voice_registry.execute_tool_async(self.name, kwargs, voice_context)

        return {
            "ok": True,
            "tool": self.name,
            "status": "bridged_execution",
            "args": kwargs,
        }


def bridge_voice_tools(
    registry: ToolRegistry,
    voice_registry: VoiceToolRegistry | None = None,
) -> list[BaseTool]:
    """Register voice tools into the unified ToolRegistry."""
    from app.voice.tools import (
        VoiceToolRegistry as RealVoiceRegistry,
    )

    vr = voice_registry or RealVoiceRegistry()
    # Enable memory and review to extract all declarations
    try:
        vr.set_memory_handlers(
            recall=lambda _: async_noop({"memories": []}),
            save=lambda _: async_noop({"saved": True}),
        )
    except Exception:
        pass

    bridged: list[BaseTool] = []
    for decl in vr.get_tool_declarations():
        name = decl["name"]
        adapter = VoiceToolAdapter(
            name=name,
            description=decl.get("description", f"Voice tool {name}"),
            parameters=decl.get("parameters", {}),
            voice_registry=voice_registry,
            risk=VOICE_TOOL_RISK_MAP.get(name, ToolRisk.SAFE),
            category="voice",
        )
        registry.register(adapter, overwrite=True)
        bridged.append(adapter)

    return bridged


async def async_noop(res: Any) -> Any:
    return res


# ---------------------------------------------------------------------------
# Chat Tool Adapters
# ---------------------------------------------------------------------------

CHAT_TOOL_RISK_MAP: dict[str, ToolRisk] = {
    "web_search": ToolRisk.SAFE,
    "send_email": ToolRisk.APPROVAL_REQUIRED,
    "recall_memory": ToolRisk.SAFE,
    "ask_user": ToolRisk.SAFE,
    "create_file_inline": ToolRisk.APPROVAL_REQUIRED,
    "edit_attached_file": ToolRisk.APPROVAL_REQUIRED,
    "create_document": ToolRisk.APPROVAL_REQUIRED,
    "start_job": ToolRisk.APPROVAL_REQUIRED,
    "check_job": ToolRisk.SAFE,
    "schedule_report": ToolRisk.APPROVAL_REQUIRED,
    "list_schedules": ToolRisk.SAFE,
    "pause_schedule": ToolRisk.APPROVAL_REQUIRED,
    "resume_schedule": ToolRisk.APPROVAL_REQUIRED,
    "delete_schedule": ToolRisk.APPROVAL_REQUIRED,
    "run_schedule_now": ToolRisk.APPROVAL_REQUIRED,
}


class ChatGatewayToolAdapter(BaseTool):
    """Adapts a Gateway ToolDefinition into a unified BaseTool."""

    def __init__(
        self,
        gateway_tool: GatewayToolDef,
        risk: ToolRisk | None = None,
        category: str = "chat",
        handler: ToolCallable | None = None,
        tags: list[str] | None = None,
    ) -> None:
        self.name = gateway_tool.name
        self.description = gateway_tool.description
        self.parameters = dict(gateway_tool.parameters)
        self.risk = risk or CHAT_TOOL_RISK_MAP.get(self.name, ToolRisk.SAFE)
        self.category = category
        self.tags = list(tags) if tags is not None else ["chat"]
        self._handler = handler

    async def execute(self, context: ToolExecutionContext, **kwargs: Any) -> Any:
        if self._handler is not None:
            res = self._handler(context=context, **kwargs)
            if hasattr(res, "__await__"):
                return await res
            return res
        return {
            "ok": True,
            "tool": self.name,
            "status": "gateway_tool_delegated",
            "args": kwargs,
        }


def bridge_chat_tools(registry: ToolRegistry) -> list[BaseTool]:
    """Register core chat tools into the unified ToolRegistry."""
    from app.schedules.tools import (
        DELETE_SCHEDULE_TOOL,
        LIST_SCHEDULES_TOOL,
        PAUSE_SCHEDULE_TOOL,
        RESUME_SCHEDULE_TOOL,
        RUN_SCHEDULE_NOW_TOOL,
        SCHEDULE_REPORT_TOOL,
    )
    from app.services.ask_user import ASK_USER_TOOL
    from app.services.document_tools import (
        CHECK_JOB_TOOL,
        CREATE_DOCUMENT_TOOL,
        START_JOB_TOOL,
    )
    from app.services.inline_files import (
        CREATE_FILE_TOOL,
        EDIT_FILE_TOOL,
    )
    from app.services.loop_tools import (
        RECALL_MEMORY_TOOL,
        SEND_EMAIL_TOOL,
        WEB_SEARCH_TOOL,
    )

    core_chat_tools: list[GatewayToolDef] = [
        WEB_SEARCH_TOOL,
        SEND_EMAIL_TOOL,
        RECALL_MEMORY_TOOL,
        ASK_USER_TOOL,
        CREATE_FILE_TOOL,
        EDIT_FILE_TOOL,
        CREATE_DOCUMENT_TOOL,
        START_JOB_TOOL,
        CHECK_JOB_TOOL,
        SCHEDULE_REPORT_TOOL,
        LIST_SCHEDULES_TOOL,
        PAUSE_SCHEDULE_TOOL,
        RESUME_SCHEDULE_TOOL,
        DELETE_SCHEDULE_TOOL,
        RUN_SCHEDULE_NOW_TOOL,
    ]

    bridged: list[BaseTool] = []
    for gtool in core_chat_tools:
        adapter = ChatGatewayToolAdapter(
            gateway_tool=gtool,
            risk=CHAT_TOOL_RISK_MAP.get(gtool.name, ToolRisk.SAFE),
            category="chat",
        )
        registry.register(adapter, overwrite=True)
        bridged.append(adapter)

    return bridged


def create_unified_registry(
    *,
    include_workspace: bool = True,
    include_todo: bool = True,
    include_voice: bool = True,
    include_chat: bool = True,
    voice_registry: VoiceToolRegistry | None = None,
) -> ToolRegistry:
    """Factory creating a unified registry pre-populated with tools across the system."""
    from app.tools.todo import register_todo_tools
    from app.tools.workspace import register_workspace_tools

    reg = ToolRegistry("unified")

    if include_workspace:
        register_workspace_tools(reg)
    if include_todo:
        register_todo_tools(reg)
    if include_voice:
        bridge_voice_tools(reg, voice_registry=voice_registry)
    if include_chat:
        bridge_chat_tools(reg)

    return reg
