"""Schedule CRUD, settings, drafts and the unannounced-updates feed. Everything is per user."""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.core.clock import Clock
from app.schedules.models import (
    MAX_SCHEDULES,
    Cadence,
    Deliver,
    Depth,
    Doc,
    Draft,
    Kind,
    LastRun,
    PendingRun,
    RunFormat,
    RunMode,
    Schedule,
)
from app.schedules.next_run import CadenceError, load_zone, next_run
from app.schedules.store import ScheduleStore
from app.schedules.validation import (
    INSTRUCTIONS_MAX,
    INSTRUCTIONS_MIN,
    NAME_MAX,
    TOPIC_MAX,
    ScheduleValidationError,
    clean_text,
    estimate_model_calls,
    validate_cadence,
    validate_deliver,
    validate_depth,
    validate_format,
)

DRAFT_TTL: Final = timedelta(hours=2)
MAX_DRAFTS: Final = 10


class NotFound(LookupError):
    """No such schedule / draft for this user."""


class VersionConflict(RuntimeError):
    """The schedule changed since the client read it (optimistic versioning)."""


class LimitReached(ValueError):
    """At most ``MAX_SCHEDULES`` schedules per user."""


class ScheduleInput(BaseModel):
    """``POST /schedules`` body and the payload of a chat draft."""

    model_config = ConfigDict(extra="forbid")

    name: str
    instructions: str
    topic: str = ""
    kind: Kind = "report"
    cadence: Cadence = Field(default_factory=Cadence)
    deliver: Deliver = Field(default_factory=Deliver)
    format: RunFormat = "chat"
    depth: Depth = Field(default_factory=Depth)


class SchedulePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    instructions: str | None = None
    topic: str | None = None
    enabled: bool | None = None
    cadence: Cadence | None = None
    deliver: Deliver | None = None
    format: RunFormat | None = None
    depth: Depth | None = None
    version: int | None = None  # optional optimistic check


@dataclass(frozen=True)
class Checked:
    name: str
    instructions: str
    topic: str
    kind: Kind
    cadence: Cadence
    deliver: Deliver
    format: str
    depth: Depth


def check_input(data: ScheduleInput) -> Checked:
    """Validate and sanitise one schedule definition (raises ``ScheduleValidationError``)."""
    name = clean_text(data.name, "name", maximum=NAME_MAX, minimum=2)
    instructions = clean_text(
        data.instructions, "instructions", maximum=INSTRUCTIONS_MAX, minimum=INSTRUCTIONS_MIN
    )
    topic = clean_text(data.topic, "topic", maximum=TOPIC_MAX) if data.topic else ""
    return Checked(
        name=name.replace("\n", " "),
        instructions=instructions,
        topic=topic.replace("\n", " "),
        kind=data.kind,
        cadence=validate_cadence(data.cadence),
        deliver=validate_deliver(data.deliver),
        format=validate_format(data.format),
        depth=validate_depth(data.depth),
    )


def describe(checked: Checked, *, account_email: str | None = None) -> str:
    """The exact configuration in words (the preview card text)."""
    cadence = checked.cadence
    when = {
        "daily": "Every day",
        "weekdays": "Every weekday",
        "weekly": f"Every {_day_word(cadence.days[0]) if cadence.days else 'week'}",
        "custom_days": "Every " + ", ".join(_day_word(d) for d in cadence.days),
    }[cadence.type]
    parts = [f"{when} at {cadence.time} ({cadence.timezone})", checked.topic or checked.name]
    if checked.deliver.mail:
        parts.append("mail to " + ", ".join(checked.deliver.recipients))
    if checked.format == "chat":
        parts.append("chat only" if not checked.deliver.mail else "also in chat")
    else:
        parts.append(f"{checked.format.upper()} file in Artifacts")
    if checked.deliver.voice_on_open:
        parts.append("spoken update when MAYA opens")
    parts.append(f"~{checked.depth.sources_target} sources/run")
    parts.append(f"est. {estimate_model_calls(checked.depth)} model calls/run")
    return " · ".join(parts)


_DAY_WORDS = {
    "mon": "Monday",
    "tue": "Tuesday",
    "wed": "Wednesday",
    "thu": "Thursday",
    "fri": "Friday",
    "sat": "Saturday",
    "sun": "Sunday",
}


def _day_word(day: str) -> str:
    return _DAY_WORDS.get(day, day)


def payload_hash(payload: dict[str, Any], preview: str) -> str:
    blob = json.dumps({"p": payload, "t": preview}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


class ScheduleService:
    def __init__(self, store: ScheduleStore, clock: Clock) -> None:
        self.store = store
        self._clock = clock

    # ------------------------------------------------------------------ reads

    async def get_doc(self, workspace_id: UUID, user_id: UUID) -> Doc:
        doc = await self.store.load(workspace_id, user_id)
        if doc.items:
            await self.store.index.register(workspace_id, user_id)
        return doc

    # ---------------------------------------------------------------- schedules

    async def create(
        self, workspace_id: UUID, user_id: UUID, data: ScheduleInput | Checked
    ) -> Schedule:
        checked = data if isinstance(data, Checked) else check_input(data)
        now = self._clock.now()

        def change(doc: Doc) -> Schedule:
            if len(doc.items) >= MAX_SCHEDULES:
                raise LimitReached(
                    f"You can have at most {MAX_SCHEDULES} schedules. Delete one first."
                )
            schedule = Schedule(
                id=f"sch_{secrets.token_hex(8)}",
                name=checked.name,
                instructions=checked.instructions,
                topic=checked.topic,
                kind=checked.kind,
                cadence=checked.cadence,
                deliver=checked.deliver,
                format=checked.format,
                depth=checked.depth,
                created_at=now,
                next_run_at=next_run(checked.cadence, now),
            )
            doc.items.append(schedule)
            return schedule

        return await self.store.update(workspace_id, user_id, change)

    async def patch(
        self, workspace_id: UUID, user_id: UUID, schedule_id: str, patch: SchedulePatch
    ) -> Schedule:
        now = self._clock.now()

        def change(doc: Doc) -> Schedule:
            item = _find(doc, schedule_id)
            if patch.version is not None and patch.version != item.version:
                raise VersionConflict("This schedule changed elsewhere. Reload and try again.")
            current = ScheduleInput(
                name=patch.name if patch.name is not None else item.name,
                instructions=(
                    patch.instructions if patch.instructions is not None else item.instructions
                ),
                topic=patch.topic if patch.topic is not None else item.topic,
                kind=item.kind,
                cadence=patch.cadence or item.cadence,
                deliver=patch.deliver or item.deliver,
                format=patch.format or item.format,
                depth=patch.depth or item.depth,
            )
            checked = check_input(current)
            schedule_changed = checked.cadence != item.cadence
            item.name = checked.name
            item.instructions = checked.instructions
            item.topic = checked.topic
            item.cadence = checked.cadence
            item.deliver = checked.deliver
            item.format = checked.format  # type: ignore[assignment]
            item.depth = checked.depth
            if patch.enabled is not None and patch.enabled != item.enabled:
                item.enabled = patch.enabled
                item.paused_reason = "" if patch.enabled else "user"
                if patch.enabled:
                    item.consecutive_failures = 0
                    item.retry_at = None
                    schedule_changed = True  # no catch-up for the time it was paused
            if item.enabled and (schedule_changed or item.next_run_at is None):
                item.next_run_at = next_run(item.cadence, now)
            if not item.enabled:
                item.next_run_at = None
            item.version += 1
            return item.model_copy(deep=True)

        return await self.store.update(workspace_id, user_id, change)

    async def delete(self, workspace_id: UUID, user_id: UUID, schedule_id: str) -> None:
        def change(doc: Doc) -> None:
            item = _find(doc, schedule_id)
            doc.items.remove(item)

        await self.store.update(workspace_id, user_id, change)

    async def set_settings(
        self,
        workspace_id: UUID,
        user_id: UUID,
        *,
        run_mode: RunMode | None = None,
        voice_on_open: bool | None = None,
        wake_registered: bool | None = None,
        timezone: str | None = None,
    ) -> Doc:
        if timezone:
            try:
                load_zone(timezone)
            except CadenceError as error:
                raise ScheduleValidationError(str(error), "timezone") from None

        def change(doc: Doc) -> Doc:
            if timezone:
                doc.settings.timezone = timezone.strip()
            if run_mode is not None:
                doc.settings.run_mode = run_mode
            if voice_on_open is not None:
                doc.settings.voice_on_open = voice_on_open
            if wake_registered is not None:
                doc.settings.wake_registered = wake_registered
            return doc

        return await self.store.update(workspace_id, user_id, change)

    # ------------------------------------------------------------------- drafts

    async def create_draft(
        self, workspace_id: UUID, user_id: UUID, data: ScheduleInput, *, account_email: str | None
    ) -> Draft:
        checked = check_input(data)
        preview = describe(checked, account_email=account_email)
        payload = {
            "name": checked.name,
            "instructions": checked.instructions,
            "topic": checked.topic,
            "kind": checked.kind,
            "cadence": checked.cadence.model_dump(),
            "deliver": checked.deliver.model_dump(),
            "format": checked.format,
            "depth": checked.depth.model_dump(),
        }
        now = self._clock.now()
        draft = Draft(
            id=f"drf_{secrets.token_hex(8)}",
            created_at=now,
            expires_at=now + DRAFT_TTL,
            payload_hash=payload_hash(payload, preview),
            schedule=payload,
            preview=preview,
        )

        def change(doc: Doc) -> Draft:
            live = [d for d in doc.drafts if d.expires_at > now and d.status == "pending"]
            doc.drafts = [*live[-(MAX_DRAFTS - 1) :], draft]
            return draft

        return await self.store.update(workspace_id, user_id, change)

    async def get_draft(self, workspace_id: UUID, user_id: UUID, draft_id: str) -> Draft:
        doc = await self.store.load(workspace_id, user_id)
        for draft in doc.drafts:
            if draft.id == draft_id:
                return draft
        raise NotFound("That schedule proposal no longer exists.")

    async def confirm_draft(
        self, workspace_id: UUID, user_id: UUID, draft_id: str
    ) -> tuple[Schedule, Draft]:
        """Create exactly what the card showed. Click-only: called by the REST route and nothing
        a model, tool or voice turn can reach."""
        now = self._clock.now()
        draft = await self.get_draft(workspace_id, user_id, draft_id)
        if draft.status == "confirmed" and draft.schedule_id:
            doc = await self.store.load(workspace_id, user_id)
            return _find(doc, draft.schedule_id), draft
        if draft.status != "pending":
            raise NotFound("That schedule proposal was cancelled.")
        if draft.expires_at <= now:
            raise NotFound("That schedule proposal expired. Ask MAYA again.")
        if payload_hash(draft.schedule, draft.preview) != draft.payload_hash:
            raise NotFound("That schedule proposal is not valid any more.")
        schedule = await self.create(
            workspace_id, user_id, ScheduleInput.model_validate(draft.schedule)
        )

        def change(doc: Doc) -> Draft:
            for d in doc.drafts:
                if d.id == draft_id:
                    d.status = "confirmed"
                    d.schedule_id = schedule.id
                    return d.model_copy()
            raise NotFound("That schedule proposal no longer exists.")

        return schedule, await self.store.update(workspace_id, user_id, change)

    async def cancel_draft(self, workspace_id: UUID, user_id: UUID, draft_id: str) -> None:
        def change(doc: Doc) -> None:
            for d in doc.drafts:
                if d.id == draft_id:
                    if d.status == "pending":
                        d.status = "cancelled"
                    return
            raise NotFound("That schedule proposal no longer exists.")

        await self.store.update(workspace_id, user_id, change)

    # ------------------------------------------------------------------ updates

    async def updates(self, workspace_id: UUID, user_id: UUID) -> list[dict[str, Any]]:
        doc = await self.store.load(workspace_id, user_id)
        items: list[dict[str, Any]] = []
        for s in doc.items:
            run = s.last_run
            if run is None or run.announced or run.status in ("running", "skipped"):
                continue
            items.append(
                {
                    "schedule_id": s.id,
                    "name": s.name,
                    "run_at": run.at,
                    "status": run.status,
                    "summary": run.summary,
                    "spoken_summary": run.spoken_summary,
                    "format": s.format,
                    "artifact_ids": run.artifact_ids,
                    "needs_attention": run.status in ("needs_attention", "failed")
                    or s.paused_reason == "failures",
                    "voice_on_open": s.deliver.voice_on_open and doc.settings.voice_on_open,
                    "reason": run.reason,
                    "mail_status": run.mail_status,
                }
            )
        items.sort(key=lambda i: i["run_at"])
        return items

    async def ack(self, workspace_id: UUID, user_id: UUID, schedule_id: str) -> None:
        def change(doc: Doc) -> None:
            item = _find(doc, schedule_id)
            if item.last_run is not None:
                item.last_run.announced = True

        await self.store.update(workspace_id, user_id, change)


def _find(doc: Doc, schedule_id: str) -> Schedule:
    for item in doc.items:
        if item.id == schedule_id:
            return item
    raise NotFound("That schedule does not exist.")


def pending_run_of(schedule: Schedule) -> PendingRun | None:
    return schedule.pending_run


__all__ = [
    "Checked",
    "LastRun",
    "LimitReached",
    "NotFound",
    "ScheduleInput",
    "SchedulePatch",
    "ScheduleService",
    "ScheduleValidationError",
    "VersionConflict",
    "check_input",
    "describe",
    "payload_hash",
]
