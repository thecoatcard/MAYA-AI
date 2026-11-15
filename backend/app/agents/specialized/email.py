"""Email Specialist Agent with autonomous drafting and approval-gated sending (Phase 09).

Production mail goes only through the user's own mailbox (Settings > Connections, or the server's
``MAYA_MAIL_*`` mailbox) over SMTP. The ``GoogleGmailService`` path is legacy: the registry no
longer passes it, so the Google access token is never used to draft or send mail. The send is not
routed through the MCP gateway, so the only send gate is the approval token the worker passes in
``has_valid_approval_token``.
"""

from __future__ import annotations

import logging
import re
from typing import Any, cast

import httpx

from app.agents.base import BaseSpecialistAgent
from app.agents.schemas import (
    AgentExecutionStatus,
    SpecialistAgentResult,
    SpecialistAgentRole,
)
from app.mail.errors import MailError
from app.mail.service import MailService
from app.mail.smtp import OutgoingMail
from app.mcp.client_manager import MCPClientManager
from app.mcp.permissions import PermissionDeniedError, ToolPermissionRegistry, ToolTier
from app.tasks.queue import SubtaskJob
from mcp_servers.google_adapter.gmail import GoogleGmailService

logger = logging.getLogger(__name__)

MAX_SUBJECT_CHARS = 200
MAX_BODY_CHARS = 50_000
_ADDRESS = re.compile(
    r"^[^@\s<>,;\"'()\[\]\\]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}$"
)
GMAIL_NOT_CONNECTED = (
    "No mailbox is connected, so no email can be drafted or sent. Connect your mailbox in "
    "Settings > Connections."
)


def normalize_recipient(raw: str) -> str:
    """Return exactly one well-formed email address, or raise ``ValueError`` (actionable text).

    Rejects empty values, several recipients and anything with header-injection characters, so
    a model-written or injected value cannot add Cc/Bcc lines or extra recipients.
    """
    value = (raw or "").strip()
    if not value:
        raise ValueError("The email has no recipient address.")
    if not _ADDRESS.match(value):
        raise ValueError(
            f"'{value[:80]}' is not a single valid email address. Give exactly one recipient "
            "address such as name@example.com."
        )
    return value


def _gmail_failure(exc: Exception, action: str) -> tuple[str, str]:
    """User-facing message and short error code for a Gmail call failure (no URL, no token)."""
    if isinstance(exc, PermissionDeniedError):
        return (f"Gmail refused to {action}: approval is required first.", "approval_required")
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code in (401, 403):
            return (
                f"Gmail rejected the credentials while trying to {action} (HTTP {code}). The "
                "access token is expired or lacks the Gmail compose/send scope: refresh "
                "MAYA_GOOGLE_ACCESS_TOKEN and run the task again.",
                f"gmail_http_{code}",
            )
        if code == 429:
            return (
                f"Gmail is rate limiting requests (HTTP 429) while trying to {action}. "
                "Wait a minute and try again.",
                "gmail_http_429",
            )
        if code >= 500:
            return (
                f"Gmail is temporarily unavailable (HTTP {code}) while trying to {action}.",
                f"gmail_http_{code}",
            )
        return (f"Gmail could not {action} (HTTP {code}).", f"gmail_http_{code}")
    if isinstance(exc, httpx.TimeoutException):
        return (f"Gmail did not answer in time while trying to {action}.", "gmail_timeout")
    return (f"Gmail could not {action}: {exc.__class__.__name__}.", "gmail_error")


class EmailAgent(BaseSpecialistAgent):
    """Specialist agent for email triage, autonomous draft creation, and gated delivery."""

    role = SpecialistAgentRole.EMAIL
    permission_ceiling = ToolTier.APPROVAL_REQUIRED
    execution_timeout_seconds = 60.0
    assigned_tools = frozenset(
        {
            "read_emails",
            "gmail_search",
            "gmail_read_thread",
            "draft_email",
            "gmail_create_draft",
            "gmail_send",
        }
    )

    def __init__(
        self,
        gmail_service: GoogleGmailService | None = None,
        permission_registry: ToolPermissionRegistry | None = None,
        client_manager: MCPClientManager | None = None,
        mail_service: MailService | None = None,
        connections: Any | None = None,
    ) -> None:
        super().__init__(permission_registry=permission_registry, client_manager=client_manager)
        self.gmail_service = gmail_service
        # The app-password mailbox (SMTP): used only when the Gmail API is not configured.
        self.mail_service = mail_service
        # Per-user mailboxes (Settings > Connections): the sender is the task owner's mailbox.
        self._connections = connections

    async def _mailbox_for_caller(self, job: SubtaskJob) -> MailService | None:
        """The task owner's mailbox (the job carries workspace and user), never another user's."""
        if self._connections is None:
            return self.mail_service
        return cast(
            "MailService | None",
            await self._connections.mail_service(job.workspace_id, job.user_id),
        )

    def availability_error(self) -> str | None:
        if self.gmail_service or self.mail_service or self._connections is not None:
            return None  # with per-user mailboxes the check happens when the step runs
        return GMAIL_NOT_CONNECTED

    def _failed(self, summary: str, code: str, **proof: Any) -> SpecialistAgentResult:
        return SpecialistAgentResult(
            agent_role=self.role,
            status=AgentExecutionStatus.FAILED,
            summary=summary,
            verification_proof={"error": code, **proof},
        )

    async def _send_with_mailbox(
        self, job: SubtaskJob, to: str, subject: str, body: str, draft_id: str
    ) -> SpecialistAgentResult:
        """The approved send over the user's app-password mailbox (SMTP). Never retried."""
        service = await self._mailbox_for_caller(job)
        if service is None:
            return self._failed(GMAIL_NOT_CONNECTED, "missing_gmail_service")
        try:
            outcome = await service.send(OutgoingMail(to=(to,), subject=subject, body=body))
        except MailError as err:
            logger.warning("mailbox_send_failed code=%s", err.code)
            return self._failed(
                f"{err} The email was not retried automatically; check your Sent folder "
                "before sending again.",
                err.code,
            )
        suffix = (
            " It was found in the Sent folder."
            if outcome.in_sent
            else " The server accepted it; it could not be confirmed in the Sent folder."
        )
        return SpecialistAgentResult(
            agent_role=self.role,
            status=AgentExecutionStatus.SUCCESS,
            summary=f"Email sent to '{to}' (server accepted).{suffix}",
            verification_proof={
                "draft_id": draft_id,
                "message_id": outcome.message_id,
                "status": "verified" if outcome.in_sent else "unverified",
            },
        )

    async def execute_subtask(
        self,
        job: SubtaskJob,
        context: dict[str, Any] | None = None,
    ) -> SpecialistAgentResult:
        """Draft autonomously; send only when the worker passes a verified approval token."""
        ctx = context or {}
        subject = " ".join(str(ctx.get("subject", "")).split())
        body = str(ctx.get("body", "")).strip()
        has_approval = ctx.get("has_valid_approval_token") is True

        if not self.gmail_service and not self.mail_service and self._connections is None:
            return self._failed(GMAIL_NOT_CONNECTED, "missing_gmail_service")
        if (
            not self.gmail_service
            and self._connections is not None
            and await self._mailbox_for_caller(job) is None
        ):
            return self._failed(GMAIL_NOT_CONNECTED, "missing_gmail_service")

        try:
            to = normalize_recipient(str(ctx.get("to", "")))
        except ValueError as err:
            return self._failed(str(err), "invalid_recipient")
        if not subject or not body:
            return self._failed("The email needs both a subject and a body.", "empty_message")
        if len(subject) > MAX_SUBJECT_CHARS or len(body) > MAX_BODY_CHARS:
            return self._failed(
                f"The email is too long (subject up to {MAX_SUBJECT_CHARS} and body up to "
                f"{MAX_BODY_CHARS} characters).",
                "message_too_long",
            )

        draft_id = str(ctx.get("draft_id", "")).strip()

        if not has_approval:
            # Draft (WRITE tier) only, reusing an existing draft; never send without approval.
            if not draft_id and self.gmail_service:
                try:
                    draft = await self.gmail_service.create_draft(to=to, subject=subject, body=body)
                except Exception as err:  # noqa: BLE001
                    message, code = _gmail_failure(err, "create the draft")
                    logger.warning("gmail_create_draft_failed code=%s", code)
                    return self._failed(message, code)
                draft_id = str(draft.get("id", ""))
            return SpecialistAgentResult(
                agent_role=self.role,
                status=AgentExecutionStatus.PARTIAL,
                summary=(
                    f"Draft email to '{to}' created successfully. Awaiting human approval to send."
                ),
                verification_proof={"draft_id": draft_id},
                requires_human_approval=True,
                approval_context={
                    "tool": "gmail_send",
                    "tool_args": {"to": to, "subject": subject, "body": body},
                    "to": to,
                    "subject": subject,
                    "body": body,
                    "draft_id": draft_id,
                },
            )

        # Approved (APPROVAL_REQUIRED tier). Send directly: creating another draft here would
        # leave a stale copy in the user's Drafts folder. A send is never retried automatically
        # (the worker treats the failure as terminal) so a retry cannot send twice.
        if not self.gmail_service:
            return await self._send_with_mailbox(job, to, subject, body, draft_id)
        try:
            sent = await self.gmail_service.send_email(
                to=to, subject=subject, body=body, has_valid_approval_token=True
            )
        except Exception as err:  # noqa: BLE001
            message, code = _gmail_failure(err, "send the email")
            logger.warning("gmail_send_failed code=%s", code)
            return self._failed(
                f"{message} The email was not retried automatically; check your Sent folder "
                "before sending again.",
                code,
            )

        message_id = str(sent.get("id", ""))
        return SpecialistAgentResult(
            agent_role=self.role,
            status=AgentExecutionStatus.SUCCESS,
            summary=f"Email successfully sent to '{to}'.",
            verification_proof={
                "draft_id": draft_id,
                "message_id": message_id,
                # Gmail returning a message id is the only evidence of delivery we have.
                "status": "verified" if message_id else "unverified",
            },
        )
