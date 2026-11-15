"""Pydantic schemas for Browser and Computer Automation (Phase 13, Decisions D4, D5, D14)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class BrowserActionRequest(BaseModel):
    """Specification of an action to execute in the sandboxed browser."""

    action_type: Literal["navigate", "click", "fill", "screenshot", "get_dom"]
    url: str | None = Field(default=None, description="Target URL for navigate actions")
    selector: str | None = Field(default=None, description="CSS or text selector for click/fill")
    input_text: str | None = Field(default=None, description="Input text to enter into form")
    screenshot_required: bool = Field(default=True, description="Capture screenshot after action")
    session_id: str | None = Field(default=None, description="Session ID for multi-step browser")
    approval_token: str | None = Field(
        default=None,
        description="Single-use approval token required for mutating actions (Decision D5)",
    )


class BrowserActionResult(BaseModel):
    """Execution result and telemetry from a sandboxed browser action."""

    success: bool
    current_url: str = ""
    dom_summary: str = ""
    screenshot_base64: str | None = None
    bot_detected: bool = False
    bot_type: str | None = None
    error_message: str | None = None
    action_type: str | None = None
    execution_time_seconds: float = 0.0
