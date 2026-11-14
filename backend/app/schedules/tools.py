"""Chat and voice tools for scheduled reports (ADR-0033).

``schedule_report`` never creates anything: it gathers the details (missing ones come back as
question-card text for ``ask_user``) and stores a DRAFT that is shown to the user as a preview
card. The schedule exists only after the user clicks Create (``POST /schedules/drafts/{id}/
confirm``). A typed or spoken "yes" confirms nothing.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Final
from uuid import UUID

from app.gateway.schemas import ToolDefinition
from app.schedules.factory import ScheduleServices
from app.schedules.models import Cadence, Deliver, Depth, Draft, Schedule
from app.schedules.runner import AlreadyRunning, RunRefused
from app.schedules.service import (
    LimitReached,
    NotFound,
    ScheduleInput,
    SchedulePatch,
)
from app.schedules.validation import ScheduleValidationError
from app.services.deliverable_intent import CHAT, requested_deliverable

logger = logging.getLogger("maya.schedules")

SCHEDULE_REPORT: Final = "schedule_report"
LIST_SCHEDULES: Final = "list_schedules"
PAUSE_SCHEDULE: Final = "pause_schedule"
RESUME_SCHEDULE: Final = "resume_schedule"
DELETE_SCHEDULE: Final = "delete_schedule"
RUN_SCHEDULE_NOW: Final = "run_schedule_now"
SCHEDULE_TOOL_NAMES: Final = (
    SCHEDULE_REPORT,
    LIST_SCHEDULES,
    PAUSE_SCHEDULE,
    RESUME_SCHEDULE,
    DELETE_SCHEDULE,
    RUN_SCHEDULE_NOW,
)
# Voice exposes everything except delete (a misheard word must not erase a schedule).
VOICE_SCHEDULE_TOOL_NAMES: Final = tuple(n for n in SCHEDULE_TOOL_NAMES if n != DELETE_SCHEDULE)

SCHEDULE_RULES: Final = (
    "Scheduled reports ARE supported: never say you cannot schedule. If the user only asks "
    "whether you can add a scheduler, answer yes and ask with ask_user what to schedule, at "
    "what time, which days, where to send it (mail to the account email or another address) and "
    "in what format. When the user wants "
    "something done every day or at a fixed time (for example a daily 8 AM tech-news mail), call "
    "schedule_report. Never invent the time, the days or the recipient: if one is missing, the "
    "tool returns the questions; show them with ask_user (up to 3 per card) and call "
    "schedule_report again with the answers. schedule_report only shows a preview card: nothing "
    "is scheduled until the user clicks Create on it. A typed or spoken 'yes' does not create "
    "it; tell the user to click Create. Mail goes out through MAYA's own mail service, only to "
    "the account email or addresses verified in Settings > Schedules. Use a file format only "
    "when the user named one. Use list_schedules, pause_schedule, resume_schedule, "
    "run_schedule_now for existing schedules."
)

_SCHEDULE_PROPS: Final = {
    "name": {"type": "string", "description": "Short name, e.g. 'Tech news'."},
    "instructions": {
        "type": "string",
        "description": "What the report must cover, in the user's words (not invented).",
    },
    "topic": {"type": "string", "description": "The subject in a few words."},
    "time": {"type": "string", "description": "24-hour HH:MM the user said. Never guess."},
    "cadence_type": {"type": "string", "enum": ["daily", "weekdays", "weekly", "custom_days"]},
    "days": {
        "type": "array",
        "items": {"type": "string"},
        "description": "mon..sun, for weekly or custom_days.",
    },
    "timezone": {"type": "string", "description": "IANA name the user confirmed."},
    "mail": {"type": "boolean", "description": "Send the report by mail."},
    "recipients": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Addresses, or 'my account email'. Max 3.",
    },
    "voice_on_open": {"type": "boolean", "description": "Speak the update when MAYA opens."},
    "format": {
        "type": "string",
        "enum": ["chat", "pdf", "docx", "pptx", "xlsx", "md"],
        "description": "chat unless the user NAMED a file format.",
    },
    "sources_target": {"type": "integer", "description": "Sources per run, default 10, max 20."},
    "use_deep_research": {"type": "boolean"},
}

SCHEDULE_REPORT_TOOL: Final = ToolDefinition(
    name=SCHEDULE_REPORT,
    description=(
        "Use this whenever the user wants anything done daily, weekly, on chosen days or at a "
        "fixed time (a daily 8 AM news mail, a weekly summary), or asks to add a scheduler. "
        "MAYA CAN schedule: never refuse. Proposes a recurring report or task and shows a "
        "preview card; nothing is scheduled until the user clicks Create. Call it even when "
        "details are missing: it returns which ones to ask the user."
    ),
    parameters={
        "type": "object",
        "properties": _SCHEDULE_PROPS,
        "required": ["instructions"],
    },
)
_REF = {"schedule": {"type": "string", "description": "The schedule's id or name."}}
LIST_SCHEDULES_TOOL: Final = ToolDefinition(
    name=LIST_SCHEDULES, description="List the user's scheduled reports.", parameters={}
)
PAUSE_SCHEDULE_TOOL: Final = ToolDefinition(
    name=PAUSE_SCHEDULE,
    description="Pause a schedule (it stops running until resumed).",
    parameters={"type": "object", "properties": _REF, "required": ["schedule"]},
)
RESUME_SCHEDULE_TOOL: Final = ToolDefinition(
    name=RESUME_SCHEDULE,
    description="Resume a paused schedule.",
    parameters={"type": "object", "properties": _REF, "required": ["schedule"]},
)
DELETE_SCHEDULE_TOOL: Final = ToolDefinition(
    name=DELETE_SCHEDULE,
    description=(
        "Delete a schedule for good. Ask the user in plain words first and pass confirm=true "
        "only after they said yes."
    ),
    parameters={
        "type": "object",
        "properties": {**_REF, "confirm": {"type": "boolean"}},
        "required": ["schedule", "confirm"],
    },
)
RUN_SCHEDULE_NOW_TOOL: Final = ToolDefinition(
    name=RUN_SCHEDULE_NOW,
    description="Run a schedule once right now (its result appears like a normal run).",
    parameters={"type": "object", "properties": _REF, "required": ["schedule"]},
)
SCHEDULE_TOOL_DEFINITIONS: Final = (
    SCHEDULE_REPORT_TOOL,
    LIST_SCHEDULES_TOOL,
    PAUSE_SCHEDULE_TOOL,
    RESUME_SCHEDULE_TOOL,
    DELETE_SCHEDULE_TOOL,
    RUN_SCHEDULE_NOW_TOOL,
)


def _upper(schema: Any) -> Any:
    """Live (Gemini) declarations use upper-case JSON schema type names."""
    if isinstance(schema, dict):
        return {
            k: (v.upper() if k == "type" and isinstance(v, str) else _upper(v))
            for k, v in schema.items()
        }
    if isinstance(schema, list):
        return [_upper(v) for v in schema]
    return schema


def schedule_voice_declarations() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for tool in SCHEDULE_TOOL_DEFINITIONS:
        if tool.name not in VOICE_SCHEDULE_TOOL_NAMES:
            continue
        item: dict[str, Any] = {"name": tool.name, "description": tool.description}
        if tool.parameters:
            item["parameters"] = _upper(tool.parameters)
        out.append(item)
    return out


FILE_FORMATS_BY_KIND: Final = {
    "pdf": "pdf",
    "docx": "docx",
    "pptx": "pptx",
    "xlsx": "xlsx",
    "md": "md",
}
_ACCOUNT_WORDS: Final = ("my account", "my email", "my mail", "account email", "myself", "mere")


def _q(header: str, question: str, options: list[str]) -> dict[str, Any]:
    return {
        "header": header[:12],
        "question": question,
        "options": [{"label": o} for o in options],
    }


class ScheduleToolkit:
    def __init__(
        self,
        *,
        services: ScheduleServices,
        workspace_id: UUID,
        user_id: UUID,
        default_timezone: str | None,
        user_texts: Callable[[], list[str]],
        voice: bool = False,
    ) -> None:
        self._voice = voice
        self._s = services
        self._ws = workspace_id
        self._user = user_id
        self._tz = default_timezone
        self._user_texts = user_texts
        self.new_drafts: list[Draft] = []

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        try:
            if name == SCHEDULE_REPORT:
                return await self._propose(args)
            if name == LIST_SCHEDULES:
                return await self._list()
            if name in (PAUSE_SCHEDULE, RESUME_SCHEDULE):
                return await self._enable(args, name == RESUME_SCHEDULE)
            if name == DELETE_SCHEDULE:
                return await self._delete(args)
            if name == RUN_SCHEDULE_NOW:
                return await self._run(args)
        except ScheduleValidationError as error:
            return {"status": "invalid", "message": f"{error} Tell the user and ask to fix it."}
        except LimitReached as error:
            return {"status": "refused", "message": str(error)}
        except NotFound as error:
            return {"status": "not_found", "message": str(error)}
        except (AlreadyRunning, RunRefused) as error:
            return {"status": "refused", "message": str(error)}
        except Exception as error:  # noqa: BLE001 - a tool failure never fails the reply
            logger.warning("schedule_tool_failed tool=%s type=%s", name, type(error).__name__)
            return {"status": "error", "message": "The scheduler failed. Nothing was changed."}
        return {"status": "error", "message": "Unknown schedule tool."}

    # ------------------------------------------------------------------ propose

    async def _propose(self, args: dict[str, Any]) -> dict[str, Any]:
        account = await self._s.accounts.email_for(self._ws, self._user)
        instructions = str(args.get("instructions") or "").strip()
        time_text = str(args.get("time") or "").strip()
        ctype = str(args.get("cadence_type") or "").strip()
        raw_days = args.get("days")
        days: list[Any] = raw_days if isinstance(raw_days, list) else []
        timezone = str(args.get("timezone") or "").strip()
        questions: list[dict[str, Any]] = []
        if len(instructions) < 8:
            questions.append(_q("Include", "What should the report include?", []))
        if not time_text:
            questions.append(_q("Time", "At what time should it run?", []))
        if not ctype or (ctype in ("weekly", "custom_days") and not days):
            questions.append(
                _q(
                    "Days",
                    "Which days?",
                    ["Every day", "Weekdays", "Pick days"],
                )
            )
        if not timezone:
            options = [self._tz] if self._tz else []
            questions.append(
                _q(
                    "Time zone",
                    "Is this time zone right?" if self._tz else "Which time zone?",
                    [*options, "Another time zone"] if options else [],
                )
            )
        mail = args.get("mail")
        raw_recipients = [str(r) for r in args.get("recipients") or [] if str(r).strip()]
        mail_ready = self._s.sender is not None
        if not mail_ready and (mail or raw_recipients):
            return {
                "status": "mail_unavailable",
                "message": (
                    "Mail is not set up on this MAYA server, so a scheduled report cannot be "
                    "mailed. Tell the user this plainly and offer to schedule it for the chat "
                    "only (the report would appear in MAYA). Do not pretend it will be mailed."
                ),
            }
        if mail is None and not raw_recipients:
            questions.append(
                _q(
                    "Delivery",
                    "Where should the report go?",
                    ["Mail to me", "Mail to another address", "Chat only"]
                    if mail_ready
                    else ["Chat only"],
                )
            )
        elif mail and not raw_recipients:
            questions.append(
                _q("Recipient", "Who should get the mail?", ["My account email", "Another address"])
            )
        if questions:
            missing = [str(q["header"]) for q in questions]
            if self._voice:
                how = (
                    "Ask the user ALOUD, in one short spoken question at a time (start with "
                    f"{missing[0]!r}), wait for the answer, then call schedule_report again with "
                    "everything known so far. Do not read all questions at once."
                )
            else:
                how = (
                    "Call ask_user with exactly these questions (they fit on one card), then "
                    "call schedule_report again with the answers."
                )
            return {
                "status": "needs_details",
                "missing": missing,
                "questions": questions[:3],
                "message": f"Not scheduled yet. Missing: {', '.join(missing)}. {how} Do not "
                "guess the missing values.",
            }
        recipients: list[str] = []
        for raw in raw_recipients:
            lowered = raw.lower()
            if any(w in lowered for w in _ACCOUNT_WORDS) and "@" not in raw:
                if not account:
                    return {
                        "status": "needs_details",
                        "missing": ["Recipient"],
                        "questions": [_q("Recipient", "Which address should get the mail?", [])],
                        "message": (
                            "The account email is not verified, so it cannot be used. Tell the "
                            "user and ask for another address (it is verified in Settings > "
                            "Schedules). Do not guess one."
                        ),
                    }
                recipients.append(account)
                continue
            recipients.append(raw)
        use_mail = bool(mail) or bool(recipients)
        named = requested_deliverable(" ".join(self._user_texts()))
        wanted = str(args.get("format") or CHAT).lower()
        file_format = FILE_FORMATS_BY_KIND.get(named, CHAT)
        if wanted not in FILE_FORMATS_BY_KIND or wanted != file_format:
            wanted = file_format  # a file only when the user NAMED a format
        sources = args.get("sources_target")
        data = ScheduleInput(
            name=str(args.get("name") or args.get("topic") or "Scheduled report"),
            instructions=instructions,
            topic=str(args.get("topic") or ""),
            cadence=Cadence(
                type=ctype if ctype in ("daily", "weekdays", "weekly", "custom_days") else "daily",
                time=time_text,
                days=[str(d) for d in days],
                timezone=timezone,
            ),
            deliver=Deliver(
                mail=use_mail,
                recipients=recipients,
                chat_artifact=True,
                voice_on_open=bool(args.get("voice_on_open", True)),
            ),
            format=wanted,
            depth=Depth(
                sources_target=int(sources) if isinstance(sources, int | float) else 10,
                use_deep_research=bool(args.get("use_deep_research", False)),
            ),
        )
        draft = await self._s.service.create_draft(
            self._ws, self._user, data, account_email=account
        )
        self.new_drafts.append(draft)
        return {
            "status": "preview_shown",
            "preview": draft.preview,
            "message": (
                "A preview card with Create and Cancel is on screen. NOTHING is scheduled until "
                "the user clicks Create. In one or two sentences say what the card shows and ask "
                "them to review and click Create. Do not say it was scheduled."
            ),
        }

    # ------------------------------------------------------------------ others

    async def _resolve(self, ref: str) -> Schedule:
        doc = await self._s.service.get_doc(self._ws, self._user)
        key = ref.strip().casefold()
        for item in doc.items:
            if item.id == ref.strip() or item.name.casefold() == key:
                return item
        matches = [i for i in doc.items if key and key in i.name.casefold()]
        if len(matches) == 1:
            return matches[0]
        raise NotFound("No single schedule matches that name. Call list_schedules.")

    async def _list(self) -> dict[str, Any]:
        doc = await self._s.service.get_doc(self._ws, self._user)
        return {
            "status": "ok",
            "schedules": [
                {
                    "id": s.id,
                    "name": s.name,
                    "enabled": s.enabled,
                    "when": f"{s.cadence.type} at {s.cadence.time} ({s.cadence.timezone})",
                    "format": s.format,
                    "next_run_at": s.next_run_at.isoformat() if s.next_run_at else None,
                    "last_status": s.last_run.status if s.last_run else None,
                }
                for s in doc.items
            ],
        }

    async def _enable(self, args: dict[str, Any], enable: bool) -> dict[str, Any]:
        item = await self._resolve(str(args.get("schedule") or ""))
        await self._s.service.patch(self._ws, self._user, item.id, SchedulePatch(enabled=enable))
        word = "resumed" if enable else "paused"
        return {"status": "ok", "message": f"'{item.name}' is {word}."}

    async def _delete(self, args: dict[str, Any]) -> dict[str, Any]:
        item = await self._resolve(str(args.get("schedule") or ""))
        if args.get("confirm") is not True:
            return {
                "status": "needs_confirmation",
                "message": f"Ask the user to confirm deleting '{item.name}', then call again.",
            }
        await self._s.service.delete(self._ws, self._user, item.id)
        return {"status": "ok", "message": f"'{item.name}' was deleted."}

    async def _run(self, args: dict[str, Any]) -> dict[str, Any]:
        item = await self._resolve(str(args.get("schedule") or ""))
        if self._s.runner is None:
            return {"status": "error", "message": "The scheduler is not running."}
        await self._s.runner.run_now(self._ws, self._user, item.id)
        return {
            "status": "ok",
            "message": f"'{item.name}' is running now. The result shows up when it finishes.",
        }
