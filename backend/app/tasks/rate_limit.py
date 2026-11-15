"""Recognise a provider quota / overload failure (HTTP 429, 408 or 503) behind any wrapper.

The implementation lives in ``app.core.provider_errors`` (so the MCP connectors can use it
without importing the task package); this module keeps the import path the task code uses.
"""

from __future__ import annotations

from app.core.provider_errors import RATE_LIMIT_STATUS_CODES, is_rate_limited, provider_error_info

__all__ = ["RATE_LIMIT_STATUS_CODES", "is_rate_limited", "provider_error_info"]
