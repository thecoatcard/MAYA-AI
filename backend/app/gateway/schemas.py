"""Pydantic schemas and data contracts for the Model Gateway."""

from decimal import Decimal
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TokenUsage(BaseModel):
    """Token usage and pricing metrics for a gateway operation.

    Tracks prompt, candidates, cached content, thoughts, and tool prompt tokens,
    as well as calculated financial cost and estimate flags.

    ``estimated_cost_usd`` is ``None`` whenever the cost cannot be fully determined (unknown or
    overridden model price, or a rate needed for the reported tokens is missing). An unknown
    cost is never represented as ``0``; check ``is_estimated_cost_complete`` before using it.
    ``is_usage_estimate`` is True when the token counts themselves are estimates (for example an
    interrupted stream or a response that carried no usage metadata).
    """

    model_config = ConfigDict(extra="forbid")

    prompt_tokens: int = 0
    candidates_tokens: int = 0
    thoughts_token_count: int = 0
    tool_use_prompt_token_count: int = 0
    cached_content_token_count: int = 0
    total_token_count: int = 0
    estimated_cost_usd: Decimal | None = None
    price_unknown: bool = False
    is_estimated_cost_complete: bool = False
    is_usage_estimate: bool = False

    @model_validator(mode="after")
    def _compute_total_tokens(self) -> Self:
        if self.total_token_count == 0:
            total = (
                self.prompt_tokens
                + self.candidates_tokens
                + self.thoughts_token_count
                + self.tool_use_prompt_token_count
            )
            if total > 0:
                self.total_token_count = total
        return self


class GatewayResponse(BaseModel):
    """Standard unified response from model text or structured generation."""

    model_config = ConfigDict(extra="forbid")

    text: str
    # The model's thought summary for this call (empty unless thought summaries were requested).
    thought_summary: str = ""
    model_id: str
    alias_used: str
    was_fallback: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    estimated_cost_usd: Decimal | None = None
    usage: TokenUsage = Field(default_factory=TokenUsage)
    citations: list[dict[str, str]] = Field(default_factory=list)
    # ``key#N`` label of the pool key that served the call (never the key text); None without one.
    key_label: str | None = None

    @model_validator(mode="after")
    def _sync_usage(self) -> Self:
        """Mirror the flat convenience fields and ``usage`` so either can be supplied."""
        usage = self.usage
        usage_populated = (
            usage.prompt_tokens > 0
            or usage.candidates_tokens > 0
            or usage.cached_content_token_count > 0
            or usage.estimated_cost_usd is not None
        )
        if usage_populated:
            if self.input_tokens == 0:
                self.input_tokens = usage.prompt_tokens
            if self.output_tokens == 0:
                self.output_tokens = usage.candidates_tokens
            if self.cached_tokens == 0:
                self.cached_tokens = usage.cached_content_token_count
            if self.estimated_cost_usd is None:
                self.estimated_cost_usd = usage.estimated_cost_usd
        else:
            usage.prompt_tokens = self.input_tokens
            usage.candidates_tokens = self.output_tokens
            usage.cached_content_token_count = self.cached_tokens
            if self.estimated_cost_usd is not None:
                usage.estimated_cost_usd = self.estimated_cost_usd
                usage.is_estimated_cost_complete = True
            if usage.total_token_count == 0:
                usage.total_token_count = self.input_tokens + self.output_tokens
        return self


class ToolCallRequest(BaseModel):
    """Invocation request for a specific tool call returned by the model.

    ``thought_signature`` carries the provider's opaque signature for the call (when present);
    it must be echoed back with the call when the function response is sent.
    """

    model_config = ConfigDict(extra="forbid")

    call_id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    thought_signature: bytes | None = Field(default=None, repr=False)


class GatewayProgressEvent(BaseModel):
    """A live notice from the failover chain (never error text): a retry, a switch to the next
    model, or "still waiting" while no output has arrived yet."""

    model_config = ConfigDict(extra="forbid")

    kind: str  # "retry" | "fallback" | "waiting"
    alias: str
    attempt: int = 1
    limit: int = 1
    reason: str = ""
    elapsed_seconds: float = 0.0


class GatewayStreamChunk(BaseModel):
    """A streaming chunk emitted during generation: text, and/or function-call requests.

    ``tool_calls`` is empty unless ``generate_stream`` was given ``tools`` and the model asked to
    call one of them in this chunk. The gateway never executes a tool.
    """

    model_config = ConfigDict(extra="forbid")

    text: str = ""
    # A summary fragment of the model's reasoning (thought summaries on only). Never answer text.
    thought: str = ""
    tool_calls: list[ToolCallRequest] = Field(default_factory=list)
    model_id: str
    alias_used: str
    was_fallback: bool = False
    usage: TokenUsage | None = None
    is_final: bool = False
    finish_reason: str | None = None
    # Set only on a notice chunk (empty text) and only when the caller asked for progress events.
    progress: GatewayProgressEvent | None = None


class ToolDefinition(BaseModel):
    """Definition of a tool available for function calling."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class ToolGatewayResponse(BaseModel):
    """Gateway response containing model-requested tool calls."""

    model_config = ConfigDict(extra="forbid")

    call_requests: list[ToolCallRequest] = Field(default_factory=list)
    text: str | None = None
    model_id: str
    alias_used: str
    was_fallback: bool = False
    usage: TokenUsage = Field(default_factory=TokenUsage)

    @model_validator(mode="before")
    @classmethod
    def _populate_calls(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "tool_calls" in data and "call_requests" not in data:
                data["call_requests"] = data.pop("tool_calls")
        return data

    @property
    def tool_calls(self) -> list[ToolCallRequest]:
        return self.call_requests


class EmbeddingResponse(BaseModel):
    """Response containing generated vector embeddings."""

    model_config = ConfigDict(extra="forbid")

    vectors: list[list[float]]
    model_id: str
    usage: TokenUsage
    alias_used: str = ""
    was_fallback: bool = False


class TokenCountResponse(BaseModel):
    """Response containing calculated token count for given contents."""

    model_config = ConfigDict(extra="forbid")

    total_tokens: int
    model_id: str
    alias_used: str = ""
