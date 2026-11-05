"""MAYA AI Model Gateway package (Phase 03)."""

from app.gateway.exceptions import (
    BudgetExceededError,
    EmbeddingDimensionMismatchError,
    GatewayConfigurationError,
    GatewayError,
    GatewayFallbackExhaustedError,
    GatewayStartupError,
    StreamInterruptedError,
    StructuredOutputError,
    ToolCallCommitmentError,
    UnsupportedAliasTransportError,
)
from app.gateway.registry import (
    ALLOWED_OVERRIDE_ALIASES,
    MODEL_ID_PATTERN,
    ModelRegistry,
    ModelSpec,
    build_default_registry,
    normalize_model_id,
)

__all__ = [
    "ALLOWED_OVERRIDE_ALIASES",
    "BudgetExceededError",
    "EmbeddingDimensionMismatchError",
    "GatewayConfigurationError",
    "GatewayError",
    "GatewayFallbackExhaustedError",
    "GatewayStartupError",
    "MODEL_ID_PATTERN",
    "ModelRegistry",
    "ModelSpec",
    "StreamInterruptedError",
    "StructuredOutputError",
    "ToolCallCommitmentError",
    "UnsupportedAliasTransportError",
    "build_default_registry",
    "normalize_model_id",
]
