"""Gmail Service Adapter with permission ceilings (Phase 08)."""

from __future__ import annotations

import base64
import logging
from email.message import EmailMessage
from typing import Any, cast

import httpx

from app.mcp.permissions import PermissionDeniedError

logger = logging.getLogger(__name__)


class GoogleGmailService:
    """Gmail API adapter enforcing READ, autonomous WRITE (draft), and APPROVAL_REQUIRED (send)."""

    def __init__(
        self,
        access_token: str,
        base_url: str = "https://gmail.googleapis.com/gmail/v1",
    ) -> None:
        self.access_token = access_token
        self.base_url = base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _encode_message(self, to: str, subject: str, body: str) -> str:
        """Encode RFC 2822 email message to base64url string."""
        msg = EmailMessage()
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        raw_bytes = msg.as_bytes()
        return base64.urlsafe_b64encode(raw_bytes).decode("ascii")

    async def search_emails(self, query: str = "", max_results: int = 10) -> list[dict[str, Any]]:
        """Search messages in user's Gmail mailbox (READ tier)."""
        url = f"{self.base_url}/users/me/messages"
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                url,
                headers=self._headers(),
                params={"q": query, "maxResults": max_results},
                timeout=15.0,
            )
            resp.raise_for_status()
            data = resp.json()
            return cast(list[dict[str, Any]], data.get("messages", []))

    async def read_thread(self, thread_id: str) -> dict[str, Any]:
        """Fetch full email thread details (READ tier)."""
        url = f"{self.base_url}/users/me/threads/{thread_id}"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=self._headers(), timeout=15.0)
            resp.raise_for_status()
            return cast(dict[str, Any], resp.json())

    async def create_draft(self, to: str, subject: str, body: str) -> dict[str, Any]:
        """Create an email draft autonomously (WRITE tier)."""
        raw_encoded = self._encode_message(to, subject, body)
        url = f"{self.base_url}/users/me/drafts"
        payload = {"message": {"raw": raw_encoded}}

        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers=self._headers(), json=payload, timeout=20.0)
            resp.raise_for_status()
            return cast(dict[str, Any], resp.json())

    async def send_email(
        self,
        to: str,
        subject: str,
        body: str,
        has_valid_approval_token: bool = False,
    ) -> dict[str, Any]:
        """Send an email requiring approval token validation (APPROVAL_REQUIRED tier)."""
        if not has_valid_approval_token:
            raise PermissionDeniedError(
                "Execution of APPROVAL_REQUIRED tool 'gmail_send' "
                "forbidden without valid approval token",
                tool_name="gmail_send",
            )

        raw_encoded = self._encode_message(to, subject, body)
        url = f"{self.base_url}/users/me/messages/send"
        payload = {"raw": raw_encoded}

        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers=self._headers(), json=payload, timeout=20.0)
            resp.raise_for_status()
            return cast(dict[str, Any], resp.json())
