"""``/api/v1/schedules``: scheduled reports and tasks (ADR-0033, docs/api/SCHEDULES.md).

Every route acts on the signed-in user's own schedules only. Creating from a chat card goes
through ``/drafts/{id}/confirm``, which only a click on the card calls.
"""

from datetime import datetime
from typing import Annotated, Any, Literal, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.chat import get_chat_rate_limiter
from app.auth.dependencies import get_principal
from app.auth.errors import RateLimited
from app.auth.models import Principal
from app.core.ratelimit import Limit, RateLimiter
from app.schedules.factory import ScheduleServices
from app.schedules.models import Doc, Schedule
from app.schedules.runner import AlreadyRunning, RunRefused
from app.schedules.service import (
    LimitReached,
    NotFound,
    ScheduleInput,
    SchedulePatch,
    VersionConflict,
)
from app.schedules.store import StoreConflict
from app.schedules.validation import ScheduleValidationError

router = APIRouter(prefix="/api/v1/schedules", tags=["schedules"])

_WINDOW_SECONDS = 60
_LIMIT_PER_MINUTE = 60

Caller = Annotated[Principal, Depends(get_principal)]
Limiter = Annotated[RateLimiter, Depends(get_chat_rate_limiter)]


def get_schedule_services(request: Request) -> ScheduleServices:
    services = getattr(request.app.state, "schedule_services", None)
    if not isinstance(services, ScheduleServices):
        raise HTTPException(status_code=503, detail={"error": "scheduler_unavailable"})
    return services


Services = Annotated[ScheduleServices, Depends(get_schedule_services)]


async def _limit(principal: Principal, limiter: RateLimiter) -> None:
    name = f"schedules:{principal.workspace_id}:{principal.user_id}"
    if not await limiter.hit(name, Limit(_LIMIT_PER_MINUTE, _WINDOW_SECONDS)):
        raise RateLimited(retry_after=_WINDOW_SECONDS)


def _fail(status: int, error: str, message: str = "", fields: list[str] | None = None) -> NoReturn:
    body: dict[str, Any] = {"error": error}
    if message:
        body["message"] = message
    if fields:
        body["fields"] = fields
    raise HTTPException(status_code=status, detail=body)


def _item(schedule: Schedule) -> dict[str, Any]:
    return schedule.model_dump(mode="json")


def _settings(doc: Doc) -> dict[str, Any]:
    return doc.settings.model_dump(mode="json")


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_mode: Literal["app_open", "wake_pc"] | None = None
    voice_on_open: bool | None = None
    # Stored after the desktop app confirmed the Windows task was created or removed.
    wake_registered: bool | None = None
    # The device's IANA time zone (the web app sends it); the default for new schedules.
    timezone: str | None = Field(default=None, max_length=64)


class RecipientRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(max_length=320)


class RecipientConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(max_length=320)
    code: str = Field(min_length=4, max_length=12)


async def _guard(call: Any) -> Any:
    """Run a service call and map its domain errors to the documented answers."""
    try:
        return await call
    except ScheduleValidationError as error:
        _fail(422, "validation_error", str(error), [error.field] if error.field else None)
    except NotFound as error:
        _fail(404, "not_found", str(error))
    except LimitReached as error:
        _fail(409, "schedule_limit", str(error))
    except VersionConflict as error:
        _fail(409, "version_conflict", str(error))
    except StoreConflict as error:
        _fail(409, "conflict", str(error))
    except AlreadyRunning as error:
        _fail(409, "already_running", str(error))
    except RunRefused as error:
        _fail(422, "run_refused", str(error))
    except ValueError as error:
        _fail(422, "validation_error", str(error))


@router.get("")
async def list_schedules(principal: Caller, limiter: Limiter, services: Services) -> dict[str, Any]:
    await _limit(principal, limiter)
    doc = await services.service.get_doc(principal.workspace_id, principal.user_id)
    account = await services.accounts.email_for(principal.workspace_id, principal.user_id)
    return {
        "items": [_item(s) for s in doc.items],
        "settings": _settings(doc),
        # Additive helpers for the Settings page.
        "account_email": account,
        "verified_recipients": sorted(doc.verified),
        "mail_configured": services.sender is not None,
        "max_schedules": 5,
    }


@router.post("", status_code=201)
async def create_schedule(
    body: ScheduleInput, principal: Caller, limiter: Limiter, services: Services
) -> dict[str, Any]:
    await _limit(principal, limiter)
    schedule = await _guard(
        services.service.create(principal.workspace_id, principal.user_id, body)
    )
    return _item(schedule)


@router.get("/updates")
async def schedule_updates(
    principal: Caller, limiter: Limiter, services: Services
) -> dict[str, Any]:
    await _limit(principal, limiter)
    items = await services.service.updates(principal.workspace_id, principal.user_id)
    return {"items": [_public_update(i) for i in items]}


def _public_update(item: dict[str, Any]) -> dict[str, Any]:
    out = dict(item)
    run_at = out.get("run_at")
    if isinstance(run_at, datetime):
        out["run_at"] = run_at.isoformat()
    return out


@router.post("/updates/{schedule_id}/ack", status_code=204)
async def ack_update(
    schedule_id: str, principal: Caller, limiter: Limiter, services: Services
) -> Response:
    await _limit(principal, limiter)
    await _guard(services.service.ack(principal.workspace_id, principal.user_id, schedule_id))
    return Response(status_code=204)


@router.put("/settings")
async def put_settings(
    body: SettingsUpdate, principal: Caller, limiter: Limiter, services: Services
) -> dict[str, Any]:
    await _limit(principal, limiter)
    doc = await _guard(
        services.service.set_settings(
            principal.workspace_id,
            principal.user_id,
            run_mode=body.run_mode,
            voice_on_open=body.voice_on_open,
            wake_registered=body.wake_registered,
            timezone=body.timezone,
        )
    )
    return _settings(doc)


@router.post("/recipients/request")
async def request_recipient_code(
    body: RecipientRequest, principal: Caller, limiter: Limiter, services: Services
) -> dict[str, Any]:
    await _limit(principal, limiter)
    result = await _guard(
        services.verifier.request(principal.workspace_id, principal.user_id, body.email)
    )
    return dict(result)


@router.post("/recipients/confirm")
async def confirm_recipient(
    body: RecipientConfirm, principal: Caller, limiter: Limiter, services: Services
) -> dict[str, bool]:
    await _limit(principal, limiter)
    await services.verifier.confirm(
        principal.workspace_id, principal.user_id, body.email, body.code
    )
    return {"verified": True}


@router.get("/drafts/{draft_id}")
async def get_draft(
    draft_id: str, principal: Caller, limiter: Limiter, services: Services
) -> dict[str, Any]:
    await _limit(principal, limiter)
    draft = await _guard(
        services.service.get_draft(principal.workspace_id, principal.user_id, draft_id)
    )
    shown: dict[str, Any] = draft.model_dump(mode="json", exclude={"payload_hash"})
    return shown


@router.post("/drafts/{draft_id}/confirm", status_code=201)
async def confirm_draft(
    draft_id: str, principal: Caller, limiter: Limiter, services: Services
) -> dict[str, Any]:
    """Click-only: creates exactly the schedule the card showed."""
    await _limit(principal, limiter)
    schedule, _draft = await _guard(
        services.service.confirm_draft(principal.workspace_id, principal.user_id, draft_id)
    )
    return _item(schedule)


@router.post("/drafts/{draft_id}/cancel", status_code=204)
async def cancel_draft(
    draft_id: str, principal: Caller, limiter: Limiter, services: Services
) -> Response:
    await _limit(principal, limiter)
    await _guard(services.service.cancel_draft(principal.workspace_id, principal.user_id, draft_id))
    return Response(status_code=204)


@router.patch("/{schedule_id}")
async def patch_schedule(
    schedule_id: str,
    body: SchedulePatch,
    principal: Caller,
    limiter: Limiter,
    services: Services,
) -> dict[str, Any]:
    await _limit(principal, limiter)
    schedule = await _guard(
        services.service.patch(principal.workspace_id, principal.user_id, schedule_id, body)
    )
    return _item(schedule)


@router.delete("/{schedule_id}", status_code=204)
async def delete_schedule(
    schedule_id: str, principal: Caller, limiter: Limiter, services: Services
) -> Response:
    await _limit(principal, limiter)
    await _guard(services.service.delete(principal.workspace_id, principal.user_id, schedule_id))
    return Response(status_code=204)


@router.post("/{schedule_id}/run-now")
async def run_now(
    schedule_id: str, principal: Caller, limiter: Limiter, services: Services
) -> dict[str, str]:
    await _limit(principal, limiter)
    if services.runner is None:
        _fail(503, "scheduler_unavailable", "The scheduler is not running.")
    task_id = await _guard(
        services.runner.run_now(principal.workspace_id, principal.user_id, schedule_id)
    )
    return {"task_id": task_id}
