"""MAYA AI Model Gateway package (Phase 03)."""

from app.gateway.cost_tracker import (
    BudgetTracker,
    calculate_cost,
    cost_to_micro_usd,
)
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
from app.gateway.schemas import (
    EmbeddingResponse,
    GatewayResponse,
    GatewayStreamChunk,
    TokenCountResponse,
    TokenUsage,
    ToolCallRequest,
    ToolDefinition,
    ToolGatewayResponse,
)

__all__ = [
    "ALLOWED_OVERRIDE_ALIASES",
    "BudgetExceededError",
    "BudgetTracker",
    "EmbeddingDimensionMismatchError",
    "EmbeddingResponse",
    "GatewayConfigurationError",
    "GatewayError",
    "GatewayFallbackExhaustedError",
    "GatewayResponse",
    "GatewayStartupError",
    "GatewayStreamChunk",
    "MODEL_ID_PATTERN",
    "ModelRegistry",
    "ModelSpec",
    "StreamInterruptedError",
    "StructuredOutputError",
    "TokenCountResponse",
    "TokenUsage",
    "ToolCallCommitmentError",
    "ToolCallRequest",
    "ToolDefinition",
    "ToolGatewayResponse",
    "UnsupportedAliasTransportError",
    "build_default_registry",
    "calculate_cost",
    "cost_to_micro_usd",
    "normalize_model_id",
]
