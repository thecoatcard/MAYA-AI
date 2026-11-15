"""Google OAuth 2.0 Token Refresh and Scope Management (Phase 08)."""

from __future__ import annotations

import logging
from typing import Any, cast

import httpx

logger = logging.getLogger(__name__)

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/calendar.readonly",
]


class GoogleOAuthHandler:
    """Manages Google OAuth tokens and refresh workflows."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str | None = None,
        token_url: str = "https://oauth2.googleapis.com/token",
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.token_url = token_url

    async def refresh_access_token(self) -> dict[str, Any]:
        """Exchange refresh token for a fresh access token."""
        if not self.refresh_token:
            raise ValueError("Cannot refresh access token without a configured refresh token.")

        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token,
            "grant_type": "refresh_token",
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(self.token_url, data=payload, timeout=15.0)
            resp.raise_for_status()
            return cast(dict[str, Any], resp.json())
