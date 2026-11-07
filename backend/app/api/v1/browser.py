"""Browser REST API Endpoints and Human Takeover Bridge (Phase 13, Decisions D4, D5, D14)."""

from __future__ import annotations

import logging
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.auth.dependencies import get_principal
from app.auth.models import Principal
from app.core.config import Settings
from app.mcp.approvals import compute_payload_hash
from app.mcp.tokens import (
    ApprovalTokenError,
    verify_approval_token,
)
from app.sandbox.browser_runner import BrowserRunner
from app.sandbox.browser_schemas import BrowserActionRequest, BrowserActionResult
from app.sandbox.egress_filter import SecurityEgressViolationError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/browser", tags=["browser"])

# Module-level default runner and session store
_runner = BrowserRunner(use_simulation=True)
_sessions: dict[str, dict[str, Any]] = {}


def get_browser_runner() -> BrowserRunner:
    """Dependency provider for BrowserRunner instance."""
    return _runner


def get_browser_settings(request: Request) -> Settings | None:
    """Dependency provider retrieving settings from application state."""
    return getattr(request.app.state, "settings", None)


class BrowserTakeoverStateResponse(BaseModel):
    """Status payload representing human browser takeover state."""

    session_id: str
    status: str
    current_url: str
    challenge_detected: bool
    challenge_type: str | None = None
    screenshot_base64: str | None = None
    message: str


class BrowserResumeResponse(BaseModel):
    """Response payload when human signals completion of CAPTCHA/challenge."""

    session_id: str
    status: str
    current_url: str
    message: str


@router.post("/action", response_model=BrowserActionResult)
async def dispatch_browser_action(
    request: BrowserActionRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    runner: Annotated[BrowserRunner, Depends(get_browser_runner)],
    settings: Annotated[Settings | None, Depends(get_browser_settings)] = None,
) -> BrowserActionResult:
    """Execute a browser action under tenant RLS and 4-tier permission constraints."""
    session_id = request.session_id or str(uuid4())

    # Verify session tenancy if already created
    if session_id in _sessions:
        if _sessions[session_id]["workspace_id"] != principal.workspace_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "session_not_found",
                    "message": f"Browser session '{session_id}' not found.",
                },
            )
    else:
        _sessions[session_id] = {
            "session_id": session_id,
            "workspace_id": principal.workspace_id,
            "user_id": principal.user_id,
            "current_url": "about:blank",
            "status": "active",
            "challenge_detected": False,
            "challenge_type": None,
            "last_screenshot": None,
        }

    # 4-Tier Permission Gate: click & fill actions require approval tokens (Decision D4, D5)
    if request.action_type in ("click", "fill"):
        if not request.approval_token:
            msg = f"Action '{request.action_type}' requires a valid human approval token."
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": "approval_required", "message": msg},
            )

        # Build args for hash comparison
        tool_args = {}
        if request.action_type == "click":
            tool_args["selector"] = request.selector or ""
            tool_name = "browser_click"
        else:
            tool_args["selector"] = request.selector or ""
            tool_args["value"] = request.input_text or ""
            tool_name = "browser_fill_form"

        expected_hash = compute_payload_hash(
            tool=tool_name,
            args=tool_args,
            workspace_id=principal.workspace_id,
            user_id=principal.user_id,
            idempotency_key=session_id,
        )

        try:
            secret_key = (
                settings.jwt_signing_key.get_secret_value() if settings is not None else "0" * 32
            )
            verify_approval_token(
                request.approval_token,
                secret_key,
                expected_workspace_id=principal.workspace_id,
                expected_user_id=principal.user_id,
                expected_tool_name=tool_name,
                expected_payload_hash=expected_hash,
            )
        except ApprovalTokenError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "invalid_approval_token",
                    "message": f"Approval token verification failed: {exc}",
                },
            ) from exc

    # Execute action under egress security filter
    # Ensure request carries the resolved session_id
    action_req = BrowserActionRequest(
        action_type=request.action_type,
        url=request.url,
        selector=request.selector,
        input_text=request.input_text,
        screenshot_required=request.screenshot_required,
        session_id=session_id,
        approval_token=request.approval_token,
    )

    try:
        result = await runner.execute_action(action_req)
    except SecurityEgressViolationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "egress_security_violation",
                "message": f"Egress security violation: {exc}",
            },
        ) from exc

    # Update session state
    session_data = _sessions[session_id]
    session_data["current_url"] = result.current_url
    if result.screenshot_base64:
        session_data["last_screenshot"] = result.screenshot_base64

    if result.bot_detected:
        session_data["status"] = "awaiting_human_takeover"
        session_data["challenge_detected"] = True
        session_data["challenge_type"] = result.bot_type
        logger.warning(
            "Anti-bot challenge '%s' detected in session %s. Yielding to human takeover.",
            result.bot_type,
            session_id,
        )

    return result


@router.get(
    "/session/{session_id}/takeover",
    response_model=BrowserTakeoverStateResponse,
)
async def get_session_takeover_state(
    session_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
) -> BrowserTakeoverStateResponse:
    """Retrieve human browser takeover status and screenshot stream."""
    session = _sessions.get(session_id)
    if session is None or session["workspace_id"] != principal.workspace_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "session_not_found",
                "message": f"Browser session '{session_id}' not found.",
            },
        )

    session_data = _sessions[session_id]
    msg = (
        f"Anti-bot challenge '{session_data.get('challenge_type')}' requires manual resolution."
        if session_data.get("challenge_detected")
        else "Session is active and ready for automation."
    )

    return BrowserTakeoverStateResponse(
        session_id=session_id,
        status=session_data["status"],
        current_url=session_data["current_url"],
        challenge_detected=session_data["challenge_detected"],
        challenge_type=session_data.get("challenge_type"),
        screenshot_base64=session_data.get("last_screenshot"),
        message=msg,
    )


@router.post(
    "/session/{session_id}/resume",
    response_model=BrowserResumeResponse,
)
async def resume_session_after_takeover(
    session_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
) -> BrowserResumeResponse:
    """Signal human completion of CAPTCHA/challenge and resume automated execution."""
    session = _sessions.get(session_id)
    if session is None or session["workspace_id"] != principal.workspace_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "session_not_found",
                "message": f"Browser session '{session_id}' not found.",
            },
        )

    session_data = _sessions[session_id]
    session_data["status"] = "active"
    session_data["challenge_detected"] = False
    session_data["challenge_type"] = None

    return BrowserResumeResponse(
        session_id=session_id,
        status="resumed",
        current_url=session_data["current_url"],
        message="Browser automation session resumed following human resolution.",
    )
