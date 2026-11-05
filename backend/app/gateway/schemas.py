"""Pydantic schemas and data contracts for the Model Gateway."""

from decimal import Decimal
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TokenUsage(BaseModel):
    """Token usage and pricing metrics for a gateway operation.

    Tracks prompt, candidates, cached content, thoughts, and tool prompt tokens,
    as well as calculated financial cost and estimate flags.
    """

    model_config = ConfigDict(extra="forbid")

    prompt_tokens: int = 0
    candidates_tokens: int = 0
    thoughts_token_count: int = 0
    tool_use_prompt_token_count: int = 0
    cached_content_token_count: int = 0
    total_token_count: int = 0
    estimated_cost_usd: Decimal = Field(default=Decimal("0.000000"))
    price_unknown: bool = False
    is_estimated_cost_complete: bool = True
    is_usage_estimate: bool = False

    @model_validator(mode="after")
    def _compute_total_tokens(self) -> Self:
        if self.total_token_count == 0 and (self.prompt_tokens + self.candidates_tokens > 0):
            self.total_token_count = (
                self.prompt_tokens + self.candidates_tokens + self.thoughts_token_count
            )
        return self


class GatewayResponse(BaseModel):
    """Standard unified response from model text or structured generation."""

    model_config = ConfigDict(extra="forbid")

    text: str
    model_id: str
    alias_used: str
    was_fallback: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    estimated_cost_usd: Decimal = Field(default=Decimal("0.000000"))
    usage: TokenUsage = Field(default_factory=TokenUsage)

    @model_validator(mode="after")
    def _sync_usage(self) -> Self:
        if (
            self.usage.prompt_tokens > 0
            or self.usage.candidates_tokens > 0
            or self.usage.cached_content_token_count > 0
            or self.usage.estimated_cost_usd != Decimal("0.000000")
        ):
            if self.input_tokens == 0:
                self.input_tokens = self.usage.prompt_tokens
            if self.output_tokens == 0:
                self.output_tokens = self.usage.candidates_tokens
            if self.cached_tokens == 0:
                self.cached_tokens = self.usage.cached_content_token_count
            if self.estimated_cost_usd == Decimal("0.000000"):
                self.estimated_cost_usd = self.usage.estimated_cost_usd
        else:
            self.usage.prompt_tokens = self.input_tokens
            self.usage.candidates_tokens = self.output_tokens
            self.usage.cached_content_token_count = self.cached_tokens
            self.usage.estimated_cost_usd = self.estimated_cost_usd
            if self.usage.total_token_count == 0:
                self.usage.total_token_count = self.input_tokens + self.output_tokens
        return self


class GatewayStreamChunk(BaseModel):
    """A streaming text chunk emitted during generation."""

    model_config = ConfigDict(extra="forbid")

    text: str = ""
    model_id: str
    alias_used: str
    was_fallback: bool = False
    usage: TokenUsage | None = None
    is_final: bool = False
    finish_reason: str | None = None


class ToolDefinition(BaseModel):
    """Definition of a tool available for function calling."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class ToolCallRequest(BaseModel):
    """Invocation request for a specific tool call returned by the model."""

    model_config = ConfigDict(extra="forbid")

    call_id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


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
