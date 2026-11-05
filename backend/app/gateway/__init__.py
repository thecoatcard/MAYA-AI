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
    InputSanitizationError,
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
from app.gateway.sanitizer import (
    sanitize_contents,
    sanitize_text,
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
from app.gateway.validator import (
    format_repair_prompt,
    parse_and_validate_json,
    strip_code_fences,
    validate_structured_output,
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
    "InputSanitizationError",
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
    "format_repair_prompt",
    "normalize_model_id",
    "parse_and_validate_json",
    "sanitize_contents",
    "sanitize_text",
    "strip_code_fences",
    "validate_structured_output",
]
