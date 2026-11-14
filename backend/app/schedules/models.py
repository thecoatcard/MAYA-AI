"""Schedule data model. Stored as one JSON document in the user's settings (no migration).

Field names are the REST contract (``docs/api/SCHEDULES.md``) and must not change.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_SCHEDULES: Final = 5
MAX_RECIPIENTS: Final = 3
DEFAULT_SOURCES: Final = 10
MAX_SOURCES: Final = 20
MAX_SUMMARY_CHARS: Final = 6000
MAX_SPOKEN_CHARS: Final = 900
DAY_NAMES: Final = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

Kind = Literal["report", "task"]
CadenceType = Literal["daily", "weekdays", "weekly", "custom_days"]
RunFormat = Literal["chat", "pdf", "docx", "pptx", "xlsx", "md"]
RunMode = Literal["app_open", "wake_pc"]
MissedPolicy = Literal["run_once"]
RunStatus = Literal[
    "completed", "failed", "needs_attention", "skipped", "running", "paused_after_failures"
]
FILE_FORMATS: Final = ("pdf", "docx", "pptx", "xlsx", "md")


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Cadence(_Model):
    type: CadenceType = "daily"
    time: str = "08:00"  # 'HH:MM', 24 h, in ``timezone``
    days: list[str] = Field(default_factory=list)  # 'mon'..'sun'; weekly / custom_days only
    timezone: str = "UTC"  # IANA name


class Deliver(_Model):
    mail: bool = False
    recipients: list[str] = Field(default_factory=list)  # max 3, each verified before sending
    chat_artifact: bool = True
    voice_on_open: bool = True  # tell me the update by voice when MAYA is next opened


class Depth(_Model):
    sources_target: int = DEFAULT_SOURCES
    use_deep_research: bool = False


class LastRun(_Model):
    at: datetime
    status: RunStatus
    task_id: str | None = None
    summary: str = ""  # the report as Markdown (capped), shown on the card
    spoken_summary: str = ""  # short, no URLs: what voice says
    announced: bool = False
    artifact_ids: list[str] = Field(default_factory=list)
    mail_status: str | None = None  # sent | partial | mail_not_configured | not_requested | failed
    reason: str = ""  # plain reason for failed / needs_attention / skipped


class PendingRun(_Model):
    """A run that has started and whose task has not finished yet (internal)."""

    task_id: str
    occurrence: str  # the occurrence key (UTC ISO of the planned time)
    started_at: datetime
    attempt: int = 1


class Schedule(_Model):
    id: str
    name: str
    instructions: str
    topic: str = ""
    kind: Kind = "report"
    cadence: Cadence = Field(default_factory=Cadence)
    deliver: Deliver = Field(default_factory=Deliver)
    format: RunFormat = "chat"
    depth: Depth = Field(default_factory=Depth)
    enabled: bool = True
    created_at: datetime
    last_run: LastRun | None = None
    next_run_at: datetime | None = None
    missed_policy: MissedPolicy = "run_once"
    version: int = 1
    # Additive, informational for the Settings page.
    consecutive_failures: int = 0
    paused_reason: str = ""  # "" | "user" | "failures"
    # Internal run bookkeeping (not part of the documented contract; clients ignore it).
    pending_run: PendingRun | None = None
    retry_at: datetime | None = None
    last_occurrence: str | None = None


class Settings(_Model):
    run_mode: RunMode = "app_open"
    voice_on_open: bool = True
    wake_registered: bool = False
    timezone: str = ""  # the device's IANA zone, sent by the web app (default for new schedules)


class VerifiedRecipient(_Model):
    verified_at: datetime


class PendingCode(_Model):
    code_hash: str
    expires_at: datetime
    attempts: int = 0
    sent_at: list[datetime] = Field(default_factory=list)  # for the 3-per-hour limit


class Draft(_Model):
    """A schedule proposed in chat or by voice, waiting for the user's click."""

    id: str
    created_at: datetime
    expires_at: datetime
    status: Literal["pending", "confirmed", "cancelled"] = "pending"
    payload_hash: str
    schedule: dict[str, Any]  # the exact create payload shown on the card
    preview: str  # the card text (also covered by the hash)
    schedule_id: str | None = None


class Doc(_Model):
    """The whole stored document (settings key ``schedules``)."""

    rev: int = 0
    items: list[Schedule] = Field(default_factory=list)
    settings: Settings = Field(default_factory=Settings)
    verified: dict[str, VerifiedRecipient] = Field(default_factory=dict)
    pending_codes: dict[str, PendingCode] = Field(default_factory=dict)
    drafts: list[Draft] = Field(default_factory=list)
