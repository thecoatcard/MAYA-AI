"""Google Calendar Service Adapter (Phase 08)."""

from __future__ import annotations

import logging
from typing import Any, cast

import httpx

logger = logging.getLogger(__name__)


class GoogleCalendarService:
    """Google Calendar API adapter for read-only event retrieval."""

    def __init__(
        self,
        access_token: str,
        base_url: str = "https://www.googleapis.com/calendar/v3",
    ) -> None:
        self.access_token = access_token
        self.base_url = base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def list_events(
        self,
        calendar_id: str = "primary",
        time_min: str | None = None,
        time_max: str | None = None,
        max_results: int = 20,
    ) -> list[dict[str, Any]]:
        """List upcoming events from specified calendar (READ tier)."""
        url = f"{self.base_url}/calendars/{calendar_id}/events"
        params: dict[str, Any] = {
            "maxResults": max_results,
            "singleEvents": "true",
            "orderBy": "startTime",
        }
        if time_min:
            params["timeMin"] = time_min
        if time_max:
            params["timeMax"] = time_max

        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=self._headers(), params=params, timeout=15.0)
            resp.raise_for_status()
            data = resp.json()
            return cast(list[dict[str, Any]], data.get("items", []))

    async def get_event(self, event_id: str, calendar_id: str = "primary") -> dict[str, Any]:
        """Fetch details for a specific event (READ tier)."""
        url = f"{self.base_url}/calendars/{calendar_id}/events/{event_id}"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=self._headers(), timeout=15.0)
            resp.raise_for_status()
            return cast(dict[str, Any], resp.json())
