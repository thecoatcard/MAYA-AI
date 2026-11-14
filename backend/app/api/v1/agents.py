"""``/api/v1/agents``: the unified agents and the user's custom agents (ADR-0023).

Custom agents are files in the connected folder (``.maya/agents/<name>.md``); this API only
previews, writes and edits those files through the SafePath-guarded store. Nothing is written
until the client sends ``confirm: true`` for the exact text it was shown by ``/custom/preview``
(that is the approval for the write). Every route is bearer-authenticated and scoped to the
caller.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.agents import capabilities
from app.agents.custom import (
    CAPABILITY_TOOLS,
    CustomAgent,
    CustomAgentError,
    CustomAgentStore,
    allowed_capabilities,
    render_definition,
    validate_definition,
)
from app.agents.custom.draft import draft_custom_agent
from app.agents.unified import catalogue
from app.api.v1.files import get_file_services
from app.auth.dependencies import get_principal
from app.auth.models import Principal
from app.files.access import FileAccessError, FileContext, context_from
from app.files.services import FileServices

logger = logging.getLogger("maya")

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])
NO_STORE: Final = {"Cache-Control": "no-store"}


async def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


router.dependencies.append(Depends(_no_store))
Caller = Annotated[Principal, Depends(get_principal)]
Services = Annotated[FileServices, Depends(get_file_services)]


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status, content={"error": code, "message": message}, headers=NO_STORE
    )


class AgentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=60)
    description: str = Field(max_length=400)
    instructions: str = Field(max_length=8000)
    capabilities: list[str] = Field(default_factory=list, max_length=8)
    model: str | None = Field(default=None, max_length=20)
    enabled: bool = True
    confirm: bool = False

    def to_agent(self) -> CustomAgent:
        from app.agents.custom.definition import clean_text

        return CustomAgent(
            name=self.name.strip().lower(),
            description=clean_text(self.description, 240),
            instructions=clean_text(self.instructions, 6000),
            capabilities=tuple(dict.fromkeys(c.strip().lower() for c in self.capabilities if c)),
            model=(self.model or "").strip().lower() or None,
            enabled=self.enabled,
        )


class EnabledBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class DraftBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: str = Field(min_length=5, max_length=1000)


def _context(principal: Principal, services: FileServices) -> FileContext:
    return context_from(services, principal.workspace_id, principal.user_id)


async def _folder_connected(ctx: FileContext) -> bool:
    try:
        await ctx.root()
    except FileAccessError:
        return False
    return True


@router.get("", response_model=None)
async def list_agents(principal: Caller, services: Services) -> dict[str, Any]:
    caps = await capabilities.for_user(principal.workspace_id, principal.user_id)
    available = {
        "research": caps.web_search,
        "browser": caps.web_search,
        "document": True,
        "project": True,
        "file_edit": True,
        "coding": False,
        "email": caps.gmail or caps.mailbox,
        "pm": caps.calendar or caps.notion,
    }
    ctx = _context(principal, services)
    connected = await _folder_connected(ctx)
    custom = (
        [item.as_dict() for item in await CustomAgentStore(ctx).list_all()] if connected else []
    )
    return {
        "agents": catalogue(available),
        "custom": custom,
        "folder_connected": connected,
        "capabilities": {
            name: {"tools": list(tools), "allowed": name in allowed_capabilities()}
            for name, tools in CAPABILITY_TOOLS.items()
        },
    }


async def _checked(
    body: AgentBody, principal: Principal, services: FileServices
) -> tuple[CustomAgent, CustomAgentStore] | JSONResponse:
    ctx = _context(principal, services)
    if not await _folder_connected(ctx):
        return _error(409, "no_folder", "Connect a working folder first (Settings).")
    try:
        return validate_definition(body.to_agent()), CustomAgentStore(ctx)
    except CustomAgentError as err:
        return _error(422, err.code, str(err))


@router.post("/custom/preview", response_model=None)
async def preview(body: AgentBody, principal: Caller, services: Services) -> Any:
    """The exact file that would be written. Nothing is written."""
    checked = await _checked(body, principal, services)
    if isinstance(checked, JSONResponse):
        return checked
    agent, _store = checked
    return {
        "path": f".maya/agents/{agent.name}.md",
        "content": render_definition(agent),
        "agent": agent.as_dict(),
    }


@router.post("/custom", status_code=201, response_model=None)
async def create(body: AgentBody, principal: Caller, services: Services) -> Any:
    if not body.confirm:
        return _error(428, "confirmation_required", "Preview the agent and confirm to write it.")
    checked = await _checked(body, principal, services)
    if isinstance(checked, JSONResponse):
        return checked
    agent, store = checked
    try:
        path = await store.create(agent)
    except CustomAgentError as err:
        return _error(409 if err.code == "exists" else 422, err.code, str(err))
    except FileAccessError as err:
        return _error(400, "invalid_path", str(err))
    return {"path": path, "agent": agent.as_dict()}


@router.put("/custom/{name}", response_model=None)
async def update(name: str, body: AgentBody, principal: Caller, services: Services) -> Any:
    if not body.confirm:
        return _error(428, "confirmation_required", "Preview the change and confirm to save it.")
    if body.name.strip().lower() != name:
        return _error(422, "name_mismatch", "The name in the body must match the URL.")
    checked = await _checked(body, principal, services)
    if isinstance(checked, JSONResponse):
        return checked
    agent, store = checked
    try:
        path = await store.update(agent)
    except CustomAgentError as err:
        return _error(404 if err.code == "not_found" else 422, err.code, str(err))
    except FileAccessError as err:
        return _error(400, "invalid_path", str(err))
    return {"path": path, "agent": agent.as_dict()}


@router.post("/custom/{name}/enabled", response_model=None)
async def set_enabled(name: str, body: EnabledBody, principal: Caller, services: Services) -> Any:
    store = CustomAgentStore(_context(principal, services))
    try:
        agent = await store.set_enabled(name, body.enabled)
    except CustomAgentError as err:
        return _error(404 if err.code in ("not_found", "bad_name") else 422, err.code, str(err))
    except FileAccessError as err:
        return _error(409, "no_folder", str(err))
    return {"agent": agent.as_dict()}


@router.post("/custom/draft", response_model=None)
async def draft(body: DraftBody, principal: Caller, services: Services, request: Request) -> Any:
    """Draft an agent from a sentence with the model. Validated and previewed, never written."""
    ctx = _context(principal, services)
    if not await _folder_connected(ctx):
        return _error(409, "no_folder", "Connect a working folder first (Settings).")
    gateway = request.app.state.resources.model_gateway
    try:
        agent, path, content = await draft_custom_agent(
            gateway, body.request, principal.workspace_id
        )
    except CustomAgentError as err:
        return _error(422, err.code, str(err))
    except Exception:
        logger.warning("custom_agent_draft_failed")
        return _error(502, "draft_failed", "Could not draft the agent right now. Try again.")
    return {"path": path, "content": content, "agent": agent.as_dict()}
