"""Security events from the authentication flows.

Events carry only fixed, code-defined names and outcomes, never an e-mail address, username,
code, password or token. Durable, workspace-bound audit rows are written later by the audit
chain writer (Phase 07); until then these structured log lines are the only record.
"""

import logging
from typing import Protocol

logger = logging.getLogger("maya.auth")


class AuthEvents(Protocol):
    def emit(self, event: str, *, outcome: str, reason: str | None = None) -> None: ...


class LoggingAuthEvents:
    def emit(self, event: str, *, outcome: str, reason: str | None = None) -> None:
        logger.info("auth_event event=%s outcome=%s reason=%s", event, outcome, reason or "-")
