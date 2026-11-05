"""MAYA AI Model Gateway package (Phase 03)."""

from app.gateway.cache_manager import (
    CACHE_KEY_PREFIX,
    ContextCacheManager,
    compute_content_hash,
)
from app.gateway.client_adapter import (
    build_genai_client,
    create_client_from_settings,
)
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
from app.gateway.model_gateway import ModelGateway
from app.gateway.registry import (
    ALLOWED_OVERRIDE_ALIASES,
    MODEL_ID_PATTERN,
    ModelRegistry,
    ModelSpec,
    build_default_registry,
    normalize_model_id,
)
from app.gateway.resilience import (
    ResiliencePolicy,
    classify_gateway_error,
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
    "CACHE_KEY_PREFIX",
    "ContextCacheManager",
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
    "ModelGateway",
    "ModelRegistry",
    "ModelSpec",
    "ResiliencePolicy",
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
    "build_genai_client",
    "calculate_cost",
    "classify_gateway_error",
    "compute_content_hash",
    "cost_to_micro_usd",
    "create_client_from_settings",
    "format_repair_prompt",
    "normalize_model_id",
    "parse_and_validate_json",
    "sanitize_contents",
    "sanitize_text",
    "strip_code_fences",
    "validate_structured_output",
]
