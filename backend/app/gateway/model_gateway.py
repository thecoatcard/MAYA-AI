"""Centralized Model Gateway for LLM operations, resilience, metering, and budgeting."""

import logging
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from google.genai import types
from pydantic import BaseModel

from app.gateway.client_adapter import create_client_from_settings
from app.gateway.cost_tracker import BudgetTracker, calculate_cost
from app.gateway.exceptions import (
    EmbeddingDimensionMismatchError,
    GatewayConfigurationError,
    StreamInterruptedError,
    StructuredOutputError,
)
from app.gateway.registry import ModelRegistry, ModelSpec
from app.gateway.resilience import ResiliencePolicy
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

    def _extract_usage(self, metadata: Any, spec: ModelSpec) -> TokenUsage:
        if metadata is None:
            usage = TokenUsage(
                prompt_tokens=0,
                candidates_tokens=0,
                estimated_cost_usd=Decimal("0.000000"),
                price_unknown=spec.price_unknown,
                is_estimated_cost_complete=not spec.price_unknown,
            )
            return usage

        prompt_tokens = getattr(metadata, "prompt_token_count", 0) or 0
        candidates_tokens = getattr(metadata, "candidates_token_count", 0) or 0
        cached_tokens = getattr(metadata, "cached_content_token_count", 0) or 0
        thoughts_tokens = getattr(metadata, "thoughts_token_count", 0) or 0
        tool_tokens = getattr(metadata, "tool_use_prompt_token_count", 0) or 0

        raw_usage = TokenUsage(
            prompt_tokens=prompt_tokens,
            candidates_tokens=candidates_tokens,
            cached_content_token_count=cached_tokens,
            thoughts_token_count=thoughts_tokens,
            tool_use_prompt_token_count=tool_tokens,
            price_unknown=spec.price_unknown,
        )

        cost, is_complete = calculate_cost(spec, raw_usage)
        raw_usage.estimated_cost_usd = cost
        raw_usage.is_estimated_cost_complete = is_complete
        return raw_usage

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

        config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            system_instruction=sanitize_text(system_instruction) if system_instruction else None,
        )

        async def _call_model(attempt_spec: ModelSpec, was_fallback: bool) -> GatewayResponse:
            response = await client.aio.models.generate_content(
                model=attempt_spec.model_id,
                contents=sanitized_contents,
                config=config,
            )
            text = response.text or ""
            usage = self._extract_usage(response.usage_metadata, attempt_spec)

            await self._record_budget(workspace_id, task_id, usage, is_tool_turn=False)

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
    ) -> AsyncIterator[GatewayStreamChunk]:
        """Stream generated text chunks.

        Retry and fallback are permitted only BEFORE the first chunk is emitted.
        Once streaming has yielded its first chunk, mid-flight errors raise StreamInterruptedError
        and usage is recorded as an estimate (is_usage_estimate=True).
        """
        self._validate_scope(task_id, workspace_id)
        spec = self._registry.get(alias)
        spec.check_supported_transport()

        sanitized_contents = sanitize_contents(contents)
        client = self._get_client()

        config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            system_instruction=sanitize_text(system_instruction) if system_instruction else None,
        )

        chain = self._registry.resolve_fallback_chain(alias) if allow_fallback else [alias]
        stream_opened = False
        active_stream = None
        active_spec = None
        active_was_fallback = False

        # Phase 1: Connect and acquire the stream with resilience prior to emitting first chunk
        for alias_idx, model_alias in enumerate(chain):
            candidate_spec = self._registry.get(model_alias)
            candidate_spec.check_supported_transport()
            was_fb = alias_idx > 0

            for attempt in range(self._resilience.max_retries_per_model + 1):
                try:
                    stream = client.aio.models.generate_content_stream(
                        model=candidate_spec.model_id,
                        contents=sanitized_contents,
                        config=config,
                    )
                    active_stream = stream
                    active_spec = candidate_spec
                    active_was_fallback = was_fb
                    stream_opened = True
                    break
                except Exception as exc:
                    cat = (
                        self._resilience.classify_gateway_error(exc)
                        if hasattr(self._resilience, "classify_gateway_error")
                        else "non_retryable"
                    )
                    if cat == "non_retryable":
                        raise exc
                    if (
                        cat == "retry_with_backoff"
                        and attempt < self._resilience.max_retries_per_model
                    ):
                        delay = self._resilience.base_delays[
                            min(attempt, len(self._resilience.base_delays) - 1)
                        ]
                        await self._resilience.sleep(self._resilience.random_uniform(0.0, delay))
                        continue
                    break

            if stream_opened:
                break

        if not stream_opened or active_stream is None or active_spec is None:
            raise GatewayConfigurationError(f"Could not open stream for alias '{alias}'")

        # Phase 2: Consume and yield chunks; any drop mid-flight commits to StreamInterruptedError
        accumulated_text: list[str] = []
        chunks_yielded = 0
        final_usage: TokenUsage | None = None

        try:
            async for chunk in active_stream:
                chunk_text = chunk.text or ""
                accumulated_text.append(chunk_text)
                chunks_yielded += 1

                chunk_usage = None
                if chunk.usage_metadata is not None:
                    final_usage = self._extract_usage(chunk.usage_metadata, active_spec)
                    chunk_usage = final_usage

                yield GatewayStreamChunk(
                    text=chunk_text,
                    model_id=active_spec.model_id,
                    alias_used=active_spec.alias,
                    was_fallback=active_was_fallback,
                    usage=chunk_usage,
                    is_final=False,
                )

            # Record metered budget on normal completion
            if final_usage is not None:
                await self._record_budget(workspace_id, task_id, final_usage, is_tool_turn=False)

        except Exception as exc:
            # Stream dropped mid-flight after chunks were already emitted
            est_tokens = len("".join(accumulated_text)) // 4
            partial_usage = TokenUsage(
                prompt_tokens=10,
                candidates_tokens=est_tokens,
                is_usage_estimate=True,
                price_unknown=active_spec.price_unknown,
            )
            cost, is_complete = calculate_cost(active_spec, partial_usage)
            partial_usage.estimated_cost_usd = cost
            partial_usage.is_estimated_cost_complete = is_complete

            await self._record_budget(workspace_id, task_id, partial_usage, is_tool_turn=False)

            raise StreamInterruptedError(
                f"Stream dropped mid-flight: {exc}",
                partial_text="".join(accumulated_text),
                chunks_yielded=chunks_yielded,
                partial_usage=partial_usage,
            ) from exc

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
        """Generate validated structured output with exactly one self-repair retry turn."""
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

        # Attempt 2: Exactly ONE self-repair turn using structured repair prompt
        logger.warning(
            "structured_output_validation_failed_attempting_repair alias=%s error=%s",
            alias,
            error,
        )
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
        """Generate response containing tool call requests. The gateway NEVER executes tools."""
        self._validate_scope(task_id, workspace_id)
        spec = self._registry.get(alias)
        spec.check_supported_transport()

        sanitized_contents = sanitize_contents(contents)
        client = self._get_client()

        # Format ToolDefinitions into SDK Tool declarations
        sdk_functions = [
            types.FunctionDeclaration(
                name=t.name,
                description=t.description,
                parameters=t.parameters,
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
            response = await client.aio.models.generate_content(
                model=attempt_spec.model_id,
                contents=sanitized_contents,
                config=config,
            )
            usage = self._extract_usage(response.usage_metadata, attempt_spec)

            call_requests: list[ToolCallRequest] = []
            if response.function_calls:
                for idx, fc in enumerate(response.function_calls):
                    call_requests.append(
                        ToolCallRequest(
                            call_id=f"call_{idx}_{fc.name}",
                            tool_name=fc.name,
                            arguments=fc.args or {},
                        )
                    )

            is_tool_turn = len(call_requests) > 0
            await self._record_budget(workspace_id, task_id, usage, is_tool_turn=is_tool_turn)

            return ToolGatewayResponse(
                call_requests=call_requests,
                text=response.text or "",
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
            is_tool_call_committed=False,
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

        config = types.EmbedContentConfig(output_dimensionality=768)

        async def _call_model(attempt_spec: ModelSpec, was_fallback: bool) -> EmbeddingResponse:
            response = await client.aio.models.embed_content(
                model=attempt_spec.model_id,
                contents=sanitized,
                config=config,
            )

            vectors: list[list[float]] = []
            for emb in response.embeddings or []:
                vec = list(emb.values or [])
                if len(vec) != 768:
                    raise EmbeddingDimensionMismatchError(
                        f"Model '{attempt_spec.model_id}' returned vector with {len(vec)} "
                        "dimensions, expected exactly 768 dimensions."
                    )
                vectors.append(vec)

            # Synthesize token usage for embedding input
            input_tokens = len(str(sanitized)) // 4
            usage = TokenUsage(
                prompt_tokens=input_tokens,
                candidates_tokens=0,
                price_unknown=attempt_spec.price_unknown,
            )
            cost, is_complete = calculate_cost(attempt_spec, usage)
            usage.estimated_cost_usd = cost
            usage.is_estimated_cost_complete = is_complete

            await self._record_budget(workspace_id, task_id, usage, is_tool_turn=False)

            return EmbeddingResponse(
                vectors=vectors,
                model_id=attempt_spec.model_id,
                usage=usage,
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
        """Count tokens for contents under the specified model alias."""
        spec = self._registry.get(alias)
        spec.check_supported_transport()

        sanitized = (
            sanitize_text(contents) if isinstance(contents, str) else sanitize_contents(contents)
        )
        client = self._get_client()

        response = await client.aio.models.count_tokens(
            model=spec.model_id,
            contents=sanitized,
        )

        return TokenCountResponse(
            total_tokens=response.total_tokens or 0,
            model_id=spec.model_id,
        )
