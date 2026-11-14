"""Model Gateway exception hierarchy."""

from typing import Any


class GatewayError(Exception):
    """Base exception for all Model Gateway errors."""


class GatewayConfigurationError(GatewayError):
    """Raised when the model registry or gateway configuration is invalid."""


class GatewayStartupError(GatewayError):
    """Raised when mandatory startup verification fails."""


class UnsupportedAliasTransportError(GatewayError):
    """Raised when an operation is attempted on an alias whose transport is unsupported."""


class GatewayFallbackExhaustedError(GatewayError):
    """Raised when all fallbacks in a traversal chain have been attempted and failed.

    ``attempts`` is how many model calls were made, ``reason`` the low-cardinality reason of the
    last failure (``unavailable``, ``timeout``, ``quota``, ...) and ``overloaded`` is True when
    the failures were service-wide (5xx, timeouts or quota), not a bad request.
    """

    def __init__(
        self,
        message: str,
        *,
        attempts: int = 0,
        reason: str = "",
        overloaded: bool = False,
        deadline_exceeded: bool = False,
        models_tried: int = 0,
        keys_tried: int = 0,
    ) -> None:
        super().__init__(message)
        self.models_tried = models_tried
        self.keys_tried = keys_tried
        self.attempts = attempts
        self.reason = reason
        self.overloaded = overloaded
        self.deadline_exceeded = deadline_exceeded

    @property
    def user_message(self) -> str:
        """Plain-language text for end users (never contains key labels or provider text)."""
        return overloaded_user_message(self.models_tried, self.keys_tried or self.attempts)


def overloaded_user_message(models_tried: int, keys_tried: int) -> str:
    """The one plain sentence a user sees when every model and key was tried (no key labels,
    no provider text). Shared by chat, tasks and voice."""
    models = max(models_tried, 1)
    keys = max(keys_tried, 1)
    return (
        f"Gemini is overloaded right now. MAYA tried {models} model{'s' if models != 1 else ''} "
        f"and {keys} key{'s' if keys != 1 else ''}; please try again in a minute."
    )


class ToolCallCommitmentError(GatewayError):
    """Raised when retry or fallback is attempted after tool calls have started emitting."""


class StreamInterruptedError(GatewayError):
    """Raised when an active streaming response is terminated mid-flight."""

    def __init__(
        self,
        message: str,
        *,
        partial_text: str = "",
        chunks_yielded: int = 0,
        partial_usage: Any = None,
    ) -> None:
        super().__init__(message)
        self.partial_text = partial_text
        self.chunks_yielded = chunks_yielded
        self.partial_usage = partial_usage


class StructuredOutputError(GatewayError):
    """Raised when structured output schema validation fails after self-repair."""


class EmbeddingDimensionMismatchError(GatewayError):
    """Raised when an embedding model returns vectors with unexpected dimensions."""


class BudgetExceededError(GatewayError):
    """Raised when a task exceeds approved financial or tool-turn budgets in enforce mode."""


class ContentBlockedError(GatewayError):
    """Raised when the provider blocks a prompt or response (safety, recitation, blocklist).

    Blocks are never retried and never trigger fallback to another model.
    """


class InputSanitizationError(GatewayError):
    """Raised when input violates sanitization constraints (e.g. invalid UTF-8, length limit)."""
