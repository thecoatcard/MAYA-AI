"""Centralized Model Gateway for LLM operations, resilience, metering, and budgeting."""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

from google.genai import types
from pydantic import BaseModel

from app.gateway.client_adapter import create_client_from_settings
from app.gateway.cost_tracker import BudgetTracker, calculate_cost
from app.gateway.exceptions import (
    BudgetExceededError,
    ContentBlockedError,
    EmbeddingDimensionMismatchError,
    GatewayConfigurationError,
    GatewayError,
    StreamInterruptedError,
    StructuredOutputError,
)
from app.gateway.registry import ModelRegistry, ModelSpec
from app.gateway.resilience import ResiliencePolicy, describe_error
from app.gateway.sanitizer import sanitize_contents, sanitize_text
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
)

if TYPE_CHECKING:
    from app.core.config import Settings

logger = logging.getLogger(__name__)

EMBEDDING_DIMENSIONS = 768
_CHARS_PER_TOKEN = 4

# Finish reasons that mean the provider refused to return the content (never retried).
_BLOCKING_FINISH_REASONS = frozenset(
    {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY"}
)


def estimate_tokens(payload: Any) -> int:
    """Rough token estimate (about 4 characters per token).

    Used only when the provider reports no usage (interrupted streams, missing metadata,
    embeddings). Results are always flagged ``is_usage_estimate=True``.
    """
    if isinstance(payload, str):
        text = payload
    else:
        try:
            text = json.dumps(payload, default=str, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(payload)
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _enum_name(value: Any) -> str:
    return str(getattr(value, "name", value))


def raise_if_blocked(response: Any) -> None:
    """Raise ContentBlockedError if the provider blocked the prompt or the candidate.

    The SDK does not raise for safety blocks; it returns a response without usable text. The
    block must be surfaced explicitly and must never trigger a retry or a fallback.
    """
    feedback = getattr(response, "prompt_feedback", None)
    block_reason = getattr(feedback, "block_reason", None) if feedback is not None else None
    if block_reason is not None:
        raise ContentBlockedError(
            f"Prompt was blocked by the provider (reason: {_enum_name(block_reason)})"
        )
    candidates = getattr(response, "candidates", None) or []
    if candidates:
        finish_reason = getattr(candidates[0], "finish_reason", None)
        if finish_reason is not None and _enum_name(finish_reason) in _BLOCKING_FINISH_REASONS:
            raise ContentBlockedError(
                f"Response was blocked by the provider (finish reason: {_enum_name(finish_reason)})"
            )


@dataclass
class _OpenedStream:
    """A provider stream whose first chunk has been received (retry/fallback no longer allowed)."""

    spec: ModelSpec
    was_fallback: bool
    stream: Any
    iterator: Any
    first_chunk: Any | None


async def _close_stream_quietly(stream: Any) -> None:
    aclose = getattr(stream, "aclose", None) if stream is not None else None
    if aclose is None:
        return
    try:
        await aclose()
    except Exception:  # best effort: closing must never mask the original outcome
        logger.debug("gateway_stream_close_failed")


class ModelGateway:
    """Gateway service coordinating model calls across role aliases, fallbacks, and budgets."""

    def __init__(
        self,
        registry: ModelRegistry,
        settings: "Settings",
        client: Any = None,
        budget_tracker: BudgetTracker | None = None,
        resilience_policy: ResiliencePolicy | None = None,
    ) -> None:
        self._registry = registry
        self._settings = settings
        self._client = client or create_client_from_settings(settings)
        self._budget_tracker = budget_tracker
        self._resilience = resilience_policy or ResiliencePolicy()

    @property
    def client(self) -> Any:
        """The provider client, or None when no API key is configured."""
        return self._client

    async def aclose(self) -> None:
        """Release the provider client's HTTP resources (best effort, safe without a client)."""
        aio = getattr(self._client, "aio", None)
        aclose = getattr(aio, "aclose", None)
        if aclose is None:
            return
        try:
            await aclose()
        except Exception:  # shutdown must not fail because the client was already closed
            logger.debug("gateway_client_close_failed")

    def _get_client(self) -> Any:
        if self._client is None:
            raise GatewayConfigurationError(
                "Cannot perform model operations: Gemini client is not initialized "
                "(MAYA_GEMINI_API_KEY is unset or blank)."
            )
        return self._client

    def _validate_scope(self, task_id: UUID | None, workspace_id: UUID | None) -> None:
        if task_id is not None and workspace_id is None:
            raise ValueError("workspace_id is required whenever task_id is specified")

    @staticmethod
    def _price(usage: TokenUsage, spec: ModelSpec) -> TokenUsage:
        cost, is_complete = calculate_cost(spec, usage)
        usage.estimated_cost_usd = cost
        usage.is_estimated_cost_complete = is_complete
        return usage

    def _extract_usage(
        self,
        metadata: Any,
        spec: ModelSpec,
        *,
        prompt_estimate: int = 0,
        output_text: str = "",
    ) -> TokenUsage:
        """Map provider usage metadata to TokenUsage.

        When the provider returned no metadata the tokens are estimated from the request and
        response sizes and flagged ``is_usage_estimate=True``; usage is never reported as zero.
        """
        if metadata is None:
            return self._price(
                TokenUsage(
                    prompt_tokens=prompt_estimate,
                    candidates_tokens=estimate_tokens(output_text) if output_text else 0,
                    price_unknown=spec.price_unknown,
                    is_usage_estimate=True,
                ),
                spec,
            )

        return self._price(
            TokenUsage(
                prompt_tokens=getattr(metadata, "prompt_token_count", 0) or 0,
                candidates_tokens=getattr(metadata, "candidates_token_count", 0) or 0,
                cached_content_token_count=getattr(metadata, "cached_content_token_count", 0) or 0,
                thoughts_token_count=getattr(metadata, "thoughts_token_count", 0) or 0,
                tool_use_prompt_token_count=getattr(metadata, "tool_use_prompt_token_count", 0)
                or 0,
                price_unknown=spec.price_unknown,
            ),
            spec,
        )

    async def _ensure_budget(
        self,
        workspace_id: UUID | None,
        task_id: UUID | None,
        *,
        for_tool_call: bool = False,
    ) -> None:
        """Refuse to start a provider attempt once an enforced cap is reached."""
        if self._budget_tracker is not None:
            await self._budget_tracker.ensure_within_budget(
                workspace_id, task_id, for_tool_call=for_tool_call
            )

    async def _record_budget(
        self,
        workspace_id: UUID | None,
        task_id: UUID | None,
        usage: TokenUsage,
        is_tool_turn: bool = False,
    ) -> None:
        if self._budget_tracker is not None:
            await self._budget_tracker.record_usage(
                workspace_id=workspace_id,
                task_id=task_id,
                usage=usage,
                is_tool_turn=is_tool_turn,
            )

    async def generate_text(
        self,
        alias: str,
        contents: list[Any],
        system_instruction: str | None = None,
        temperature: float = 0.7,
        max_output_tokens: int | None = None,
        task_id: UUID | None = None,
        workspace_id: UUID | None = None,
        allow_fallback: bool = True,
    ) -> GatewayResponse:
        """Generate text from a model role alias with fallback resilience and cost metering."""
        self._validate_scope(task_id, workspace_id)
        spec = self._registry.get(alias)
        spec.check_supported_transport()

        sanitized_contents = sanitize_contents(contents)
        client = self._get_client()
        prompt_estimate = estimate_tokens(sanitized_contents)

        config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            system_instruction=sanitize_text(system_instruction) if system_instruction else None,
        )

        async def _call_model(attempt_spec: ModelSpec, was_fallback: bool) -> GatewayResponse:
            await self._ensure_budget(workspace_id, task_id)
            response = await client.aio.models.generate_content(
                model=attempt_spec.model_id,
                contents=sanitized_contents,
                config=config,
            )
            text = response.text or ""
            usage = self._extract_usage(
                response.usage_metadata,
                attempt_spec,
                prompt_estimate=prompt_estimate,
                output_text=text,
            )

            # Usage is recorded before a block is raised: blocked prompts can still be billed.
            await self._record_budget(workspace_id, task_id, usage, is_tool_turn=False)
            raise_if_blocked(response)

            return GatewayResponse(
                text=text,
                model_id=attempt_spec.model_id,
                alias_used=attempt_spec.alias,
                was_fallback=was_fallback,
                usage=usage,
            )

        result, _, _ = await self._resilience.execute(
            self._registry,
            alias,
            _call_model,
            allow_fallback=allow_fallback,
        )
        return result

    async def generate_stream(
        self,
        alias: str,
        contents: list[Any],
        system_instruction: str | None = None,
        temperature: float = 0.7,
        max_output_tokens: int | None = None,
        task_id: UUID | None = None,
        workspace_id: UUID | None = None,
        allow_fallback: bool = True,
    ) -> AsyncGenerator[GatewayStreamChunk, None]:
        """Stream generated text chunks.

        Retry and fallback are permitted only until the first chunk has been received: the
        stream is opened and its first chunk awaited inside the resilience policy. After that,
        any failure raises StreamInterruptedError carrying the partial text and an estimated
        usage record (is_usage_estimate=True), which is also recorded against the budget.
        Cancellation and early close record the same estimate and always close the SDK stream.

        Arguments are validated when iteration starts (this is an async generator).
        """
        self._validate_scope(task_id, workspace_id)
        spec = self._registry.get(alias)
        spec.check_supported_transport()

        sanitized_contents = sanitize_contents(contents)
        client = self._get_client()
        prompt_estimate = estimate_tokens(sanitized_contents)

        config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            system_instruction=sanitize_text(system_instruction) if system_instruction else None,
        )

        async def _open(attempt_spec: ModelSpec, was_fallback: bool) -> _OpenedStream:
            await self._ensure_budget(workspace_id, task_id)
            stream: Any = None
            try:
                # The SDK method is a coroutine that resolves to an async iterator; HTTP status
                # errors (429, 5xx) are raised here, before any chunk exists.
                stream = await client.aio.models.generate_content_stream(
                    model=attempt_spec.model_id,
                    contents=sanitized_contents,
                    config=config,
                )
                iterator = stream.__aiter__()
                try:
                    first_chunk = await iterator.__anext__()
                except StopAsyncIteration:
                    first_chunk = None
                if first_chunk is not None:
                    raise_if_blocked(first_chunk)
                return _OpenedStream(attempt_spec, was_fallback, stream, iterator, first_chunk)
            except BaseException:
                await _close_stream_quietly(stream)
                raise

        opened, _, _ = await self._resilience.execute(
            self._registry, alias, _open, allow_fallback=allow_fallback
        )

        accumulated_text: list[str] = []
        chunks_yielded = 0
        latest_usage: TokenUsage | None = None

        try:
            chunk = opened.first_chunk
            while chunk is not None:
                chunk_text = chunk.text or ""
                accumulated_text.append(chunk_text)
                chunks_yielded += 1

                chunk_usage = None
                if chunk.usage_metadata is not None:
                    latest_usage = self._extract_usage(chunk.usage_metadata, opened.spec)
                    chunk_usage = latest_usage

                finish_reason = None
                if chunk.candidates:
                    raw_reason = chunk.candidates[0].finish_reason
                    finish_reason = _enum_name(raw_reason) if raw_reason is not None else None

                yield GatewayStreamChunk(
                    text=chunk_text,
                    model_id=opened.spec.model_id,
                    alias_used=opened.spec.alias,
                    was_fallback=opened.was_fallback,
                    usage=chunk_usage,
                    is_final=finish_reason is not None,
                    finish_reason=finish_reason,
                )

                try:
                    chunk = await opened.iterator.__anext__()
                except StopAsyncIteration:
                    chunk = None
                if chunk is not None:
                    raise_if_blocked(chunk)

        except Exception as exc:
            partial_usage = self._partial_stream_usage(
                opened.spec, latest_usage, prompt_estimate, accumulated_text
            )
            await self._record_partial_usage(workspace_id, task_id, partial_usage)
            raise StreamInterruptedError(
                f"Stream interrupted after {chunks_yielded} chunk(s) ({describe_error(exc)})",
                partial_text="".join(accumulated_text),
                chunks_yielded=chunks_yielded,
                partial_usage=partial_usage,
            ) from exc
        except (asyncio.CancelledError, GeneratorExit):
            # The caller cancelled or stopped iterating: what was already generated is billed.
            partial_usage = self._partial_stream_usage(
                opened.spec, latest_usage, prompt_estimate, accumulated_text
            )
            await self._record_partial_usage(workspace_id, task_id, partial_usage)
            raise
        finally:
            await _close_stream_quietly(opened.stream)

        # Normal completion only (every failure path above re-raises). Recorded outside the try
        # block so that a BudgetExceededError here is not mistaken for a stream failure.
        if latest_usage is None:
            latest_usage = self._extract_usage(
                None,
                opened.spec,
                prompt_estimate=prompt_estimate,
                output_text="".join(accumulated_text),
            )
        await self._record_budget(workspace_id, task_id, latest_usage, is_tool_turn=False)

    def _partial_stream_usage(
        self,
        spec: ModelSpec,
        latest_usage: TokenUsage | None,
        prompt_estimate: int,
        accumulated_text: list[str],
    ) -> TokenUsage:
        """Usage for a stream that did not finish: always an estimate."""
        if latest_usage is not None:
            partial = latest_usage.model_copy(update={"is_usage_estimate": True})
            return partial
        return self._extract_usage(
            None, spec, prompt_estimate=prompt_estimate, output_text="".join(accumulated_text)
        )

    async def _record_partial_usage(
        self, workspace_id: UUID | None, task_id: UUID | None, usage: TokenUsage
    ) -> None:
        """Record an interrupted stream's estimate; never lets a budget error mask the cause."""
        try:
            await asyncio.shield(self._record_budget(workspace_id, task_id, usage))
        except BudgetExceededError:
            logger.warning("gateway_partial_stream_usage_exceeded_budget")

    async def generate_structured[T: BaseModel](
        self,
        alias: str,
        contents: list[Any],
        response_schema: type[T],
        system_instruction: str | None = None,
        task_id: UUID | None = None,
        workspace_id: UUID | None = None,
        allow_fallback: bool = True,
    ) -> T:
        """Generate validated structured output with exactly one self-repair retry turn.

        The model is instructed through the prompt only; provider-side JSON mode
        (``response_schema``) is not used, so output is always parsed and validated here.
        """
        self._validate_scope(task_id, workspace_id)

        # Attempt 1: Standard generation
        response = await self.generate_text(
            alias=alias,
            contents=contents,
            system_instruction=system_instruction,
            task_id=task_id,
            workspace_id=workspace_id,
            allow_fallback=allow_fallback,
        )

        model, error = parse_and_validate_json(response.text, response_schema)
        if model is not None:
            return model

        # Attempt 2: Exactly ONE self-repair turn using structured repair prompt.
        # The validation detail is deliberately not logged: it describes model output.
        logger.warning("structured_output_validation_failed_attempting_repair alias=%s", alias)
        repair_prompt = format_repair_prompt(
            response.text, error or "Schema mismatch", response_schema
        )
        repair_contents = [*contents, repair_prompt]

        repair_response = await self.generate_text(
            alias=alias,
            contents=repair_contents,
            system_instruction=system_instruction,
            task_id=task_id,
            workspace_id=workspace_id,
            allow_fallback=allow_fallback,
        )

        repaired_model, second_error = parse_and_validate_json(
            repair_response.text, response_schema
        )
        if repaired_model is not None:
            return repaired_model

        raise StructuredOutputError(
            "Structured output schema validation failed after 1 self-repair attempt: "
            f"{second_error}"
        )

    async def generate_with_tools(
        self,
        alias: str,
        contents: list[Any],
        tools: list[ToolDefinition],
        system_instruction: str | None = None,
        temperature: float = 0.7,
        task_id: UUID | None = None,
        workspace_id: UUID | None = None,
        allow_fallback: bool = True,
    ) -> ToolGatewayResponse:
        """Generate response containing tool call requests. The gateway NEVER executes tools.

        This operation is non-streaming: the complete response (including every tool call) is
        received before it is returned, so nothing the caller can act on is ever retried.
        """
        self._validate_scope(task_id, workspace_id)
        spec = self._registry.get(alias)
        spec.check_supported_transport()

        sanitized_contents = sanitize_contents(contents)
        client = self._get_client()
        prompt_estimate = estimate_tokens(sanitized_contents)

        # Tool parameters are standard JSON Schema; parameters_json_schema accepts them as-is.
        sdk_functions = [
            types.FunctionDeclaration(
                name=t.name,
                description=t.description,
                parameters_json_schema=t.parameters or None,
            )
            for t in tools
        ]
        sdk_tools = [types.Tool(function_declarations=sdk_functions)] if sdk_functions else None

        config = types.GenerateContentConfig(
            temperature=temperature,
            system_instruction=sanitize_text(system_instruction) if system_instruction else None,
            tools=sdk_tools,
        )

        async def _call_model(attempt_spec: ModelSpec, was_fallback: bool) -> ToolGatewayResponse:
            await self._ensure_budget(workspace_id, task_id, for_tool_call=True)
            response = await client.aio.models.generate_content(
                model=attempt_spec.model_id,
                contents=sanitized_contents,
                config=config,
            )
            text = response.text or ""
            usage = self._extract_usage(
                response.usage_metadata,
                attempt_spec,
                prompt_estimate=prompt_estimate,
                output_text=text,
            )

            call_requests: list[ToolCallRequest] = []
            for idx, fc in enumerate(response.function_calls or []):
                call_requests.append(
                    ToolCallRequest(
                        call_id=getattr(fc, "id", None) or f"call_{idx}_{fc.name}",
                        tool_name=fc.name,
                        arguments=dict(fc.args or {}),
                    )
                )

            is_tool_turn = len(call_requests) > 0
            await self._record_budget(workspace_id, task_id, usage, is_tool_turn=is_tool_turn)
            if not call_requests and not text:
                raise_if_blocked(response)

            return ToolGatewayResponse(
                call_requests=call_requests,
                text=text,
                model_id=attempt_spec.model_id,
                alias_used=attempt_spec.alias,
                was_fallback=was_fallback,
                usage=usage,
            )

        result, _, _ = await self._resilience.execute(
            self._registry,
            alias,
            _call_model,
            allow_fallback=allow_fallback,
        )
        return result

    async def embed_content(
        self,
        alias: str,
        contents: list[Any] | str,
        task_id: UUID | None = None,
        workspace_id: UUID | None = None,
        allow_fallback: bool = False,
    ) -> EmbeddingResponse:
        """Generate embedding vectors, strictly enforcing 768 dimensions.

        allow_fallback defaults to False because different embedding models produce
        non-comparable vector spaces.
        """
        self._validate_scope(task_id, workspace_id)
        spec = self._registry.get(alias)
        spec.check_supported_transport()

        sanitized = (
            sanitize_text(contents) if isinstance(contents, str) else sanitize_contents(contents)
        )
        client = self._get_client()

        config = types.EmbedContentConfig(output_dimensionality=EMBEDDING_DIMENSIONS)

        async def _call_model(attempt_spec: ModelSpec, was_fallback: bool) -> EmbeddingResponse:
            await self._ensure_budget(workspace_id, task_id)
            response = await client.aio.models.embed_content(
                model=attempt_spec.model_id,
                contents=sanitized,
                config=config,
            )

            vectors: list[list[float]] = []
            for emb in response.embeddings or []:
                vec = list(emb.values or [])
                if len(vec) != EMBEDDING_DIMENSIONS:
                    raise EmbeddingDimensionMismatchError(
                        f"Model '{attempt_spec.model_id}' returned vector with {len(vec)} "
                        f"dimensions, expected exactly {EMBEDDING_DIMENSIONS} dimensions."
                    )
                vectors.append(vec)
            if not vectors:
                raise GatewayError(
                    f"Model '{attempt_spec.model_id}' returned no embedding vectors."
                )

            # The embedding API reports no token usage, so input size is estimated and flagged.
            usage = self._price(
                TokenUsage(
                    prompt_tokens=estimate_tokens(sanitized),
                    price_unknown=attempt_spec.price_unknown,
                    is_usage_estimate=True,
                ),
                attempt_spec,
            )
            await self._record_budget(workspace_id, task_id, usage, is_tool_turn=False)

            return EmbeddingResponse(
                vectors=vectors,
                model_id=attempt_spec.model_id,
                usage=usage,
                alias_used=attempt_spec.alias,
                was_fallback=was_fallback,
            )

        result, _, _ = await self._resilience.execute(
            self._registry,
            alias,
            _call_model,
            allow_fallback=allow_fallback,
        )
        return result

    async def count_tokens(
        self,
        alias: str,
        contents: list[Any] | str,
    ) -> TokenCountResponse:
        """Count tokens for contents under the specified model alias.

        Retries 429/timeouts on the same model only: token counts are model-specific, so a
        fallback model's count would be wrong for the requested alias.
        """
        spec = self._registry.get(alias)
        spec.check_supported_transport()

        sanitized = (
            sanitize_text(contents) if isinstance(contents, str) else sanitize_contents(contents)
        )
        client = self._get_client()

        async def _call_model(attempt_spec: ModelSpec, was_fallback: bool) -> TokenCountResponse:
            response = await client.aio.models.count_tokens(
                model=attempt_spec.model_id,
                contents=sanitized,
            )
            return TokenCountResponse(
                total_tokens=response.total_tokens or 0,
                model_id=attempt_spec.model_id,
                alias_used=attempt_spec.alias,
            )

        result, _, _ = await self._resilience.execute(
            self._registry, alias, _call_model, allow_fallback=False
        )
        return result
