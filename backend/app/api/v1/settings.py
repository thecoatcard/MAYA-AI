"""``/api/v1/settings/chat``: the caller's own chat preferences (ADR-0016)."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.v1.chat import get_chat_rate_limiter, get_chat_settings
from app.auth.dependencies import get_principal
from app.auth.errors import RateLimited
from app.auth.models import Principal
from app.core.config import Settings
from app.core.ratelimit import Limit, RateLimiter
from app.db.repositories.user_settings import UserSettingsStore
from app.mcp import notion_auto_approve as auto

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])

_WINDOW_SECONDS = 60
_LIMIT_PER_MINUTE = 60


class ChatSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_classifier: bool | None = None
    memory_in_chat: bool | None = None

    @model_validator(mode="after")
    def _something_to_change(self) -> "ChatSettingsUpdate":
        if self.intent_classifier is None and self.memory_in_chat is None:
            raise ValueError("at least one setting is required")
        return self


class ChatSettingsOut(BaseModel):
    intent_classifier: bool
    classifier_available: bool
    memory_in_chat: bool


def get_settings_store(request: Request) -> UserSettingsStore:
    engine = request.app.state.resources.engine
    assert isinstance(engine, AsyncEngine)
    return UserSettingsStore(engine)


async def _limit(principal: Principal, limiter: RateLimiter) -> None:
    name = f"settings:{principal.workspace_id}:{principal.user_id}"
    if not await limiter.hit(name, Limit(_LIMIT_PER_MINUTE, _WINDOW_SECONDS)):
        raise RateLimited(retry_after=_WINDOW_SECONDS)


Store = Annotated[UserSettingsStore, Depends(get_settings_store)]
Limiter = Annotated[RateLimiter, Depends(get_chat_rate_limiter)]
AppSettings = Annotated[Settings, Depends(get_chat_settings)]
Caller = Annotated[Principal, Depends(get_principal)]


@router.get("/chat", response_model=ChatSettingsOut)
async def get_chat_settings_route(
    principal: Caller, store: Store, limiter: Limiter, settings: AppSettings
) -> ChatSettingsOut:
    await _limit(principal, limiter)
    enabled = await store.intent_classifier_enabled(principal.workspace_id, principal.user_id)
    memory = await store.memory_in_chat_enabled(principal.workspace_id, principal.user_id)
    return ChatSettingsOut(
        intent_classifier=enabled,
        classifier_available=settings.chat_intent_classifier,
        memory_in_chat=memory,
    )


@router.put("/chat", response_model=ChatSettingsOut)
async def put_chat_settings(
    body: ChatSettingsUpdate,
    principal: Caller,
    store: Store,
    limiter: Limiter,
    settings: AppSettings,
) -> ChatSettingsOut:
    """Idempotent; only the fields sent change. Enabling the classifier while the deployment
    switch is off is stored but has no effect. ``memory_in_chat`` defaults to on."""
    await _limit(principal, limiter)
    if body.intent_classifier is not None:
        await store.set_intent_classifier(
            principal.workspace_id, principal.user_id, body.intent_classifier
        )
    if body.memory_in_chat is not None:
        await store.set_memory_in_chat(
            principal.workspace_id, principal.user_id, body.memory_in_chat
        )
    return ChatSettingsOut(
        intent_classifier=await store.intent_classifier_enabled(
            principal.workspace_id, principal.user_id
        ),
        classifier_available=settings.chat_intent_classifier,
        memory_in_chat=await store.memory_in_chat_enabled(
            principal.workspace_id, principal.user_id
        ),
    )


# --- Notion auto-approve (default OFF). The ONLY place the setting can be written: an
# authenticated request from the Settings page. Nothing a model, tool, page or e-mail produces
# reaches this route. See app/mcp/notion_auto_approve.py.


class NotionAutoApproveScopes(BaseModel):
    model_config = ConfigDict(extra="forbid")

    create_page: bool | None = None
    add_row: bool | None = None
    append_blocks: bool | None = None
    update_row: bool | None = None


class NotionAutoApproveUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    scopes: NotionAutoApproveScopes | None = None
    # Must be true when this request turns auto-approval on: the Settings dialog sets it only
    # after the owner confirmed what happens and what stays manual.
    confirm: bool = False

    @model_validator(mode="after")
    def _something_to_change(self) -> "NotionAutoApproveUpdate":
        if self.enabled is None and self.scopes is None:
            raise ValueError("at least one setting is required")
        return self


class NotionAutoApproveLast(BaseModel):
    at: datetime
    tool: str | None


class NotionAutoApproveOut(BaseModel):
    enabled: bool
    scopes: dict[str, bool]
    version: int
    hourly_cap: int
    daily_cap: int
    used_last_hour: int
    used_today: int
    always_manual: list[str]
    last_auto_approved: NotionAutoApproveLast | None


async def _auto_out(
    store: UserSettingsStore, principal: Principal, setting: auto.NotionAutoApproveSetting
) -> NotionAutoApproveOut:
    usage = await auto.load_usage(store.engine, principal.workspace_id, principal.user_id)
    return NotionAutoApproveOut(
        enabled=setting.enabled,
        scopes={name: name in setting.scopes for name in auto.SCOPES},
        version=setting.version,
        hourly_cap=auto.HOURLY_CAP,
        daily_cap=auto.DAILY_CAP,
        used_last_hour=usage.last_hour,
        used_today=usage.last_day,
        always_manual=list(auto.ALWAYS_MANUAL_LABELS),
        last_auto_approved=(
            NotionAutoApproveLast(at=usage.last_at, tool=usage.last_tool)
            if usage.last_at is not None
            else None
        ),
    )


@router.get("/notion-auto-approve", response_model=NotionAutoApproveOut)
async def get_notion_auto_approve(
    principal: Caller, store: Store, limiter: Limiter
) -> NotionAutoApproveOut:
    await _limit(principal, limiter)
    setting = await auto.load_setting(store, principal.workspace_id, principal.user_id)
    return await _auto_out(store, principal, setting)


@router.put("/notion-auto-approve", response_model=NotionAutoApproveOut)
async def put_notion_auto_approve(
    body: NotionAutoApproveUpdate, principal: Caller, store: Store, limiter: Limiter
) -> NotionAutoApproveOut:
    await _limit(principal, limiter)
    current = await auto.load_setting(store, principal.workspace_id, principal.user_id)
    scopes = body.scopes.model_dump(exclude_none=True) if body.scopes is not None else None
    try:
        updated = auto.apply_update(
            current, enabled=body.enabled, scopes=scopes, confirm=body.confirm
        )
    except auto.AutoApproveConfirmationRequired as error:
        raise HTTPException(
            status_code=409, detail={"error": "confirmation_required", "message": str(error)}
        ) from error
    except auto.AutoApproveMasterOff as error:
        raise HTTPException(
            status_code=409, detail={"error": "turn_on_first", "message": str(error)}
        ) from error
    if updated is not current:
        await auto.save_setting(store, principal.workspace_id, principal.user_id, updated)
    return await _auto_out(store, principal, updated)
