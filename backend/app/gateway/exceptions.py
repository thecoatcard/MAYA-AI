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
    """Raised when all fallbacks in a traversal chain have been attempted and failed."""


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
