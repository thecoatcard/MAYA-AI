"""Build the decision service from settings (a no-op service without a key or when switched off)."""

from __future__ import annotations

from typing import Any

from app.core.config import Settings
from app.decisions.client import DecisionClient
from app.decisions.service import DecisionService
from app.gateway.key_pool import KeyPool


def build_decision_service(settings: Settings, key_pool: Any) -> DecisionService | None:
    """None when disabled or there is no key pool: callers then keep today's behaviour."""
    if not settings.decisions_enabled or not isinstance(key_pool, KeyPool):
        return None
    return DecisionService(
        DecisionClient(key_pool, model=settings.decisions_model),
        enabled=True,
        disabled_purposes=[p for p in settings.decisions_disabled_purposes.split(",") if p.strip()],
        timeout=settings.decisions_timeout_seconds,
        background_timeout=settings.decisions_background_timeout_seconds,
        max_per_minute=settings.decisions_max_per_minute,
        per_purpose_per_minute=max(1, settings.decisions_max_per_minute // 3),
    )
