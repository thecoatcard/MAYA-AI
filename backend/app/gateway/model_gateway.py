"""Centralized Model Gateway for LLM operations, resilience, metering, and budgeting."""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from google.genai import types
from pydantic import BaseModel

from app.gateway.client_adapter import create_client_from_settings
from app.gateway.cost_tracker import BudgetTracker, calculate_cost, cost_to_micro_usd
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
ONE_MILLION = 1_000_000

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
    admission_token: str | None


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
        self._pending_partial_usage: set[asyncio.Task[None]] = set()

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

    async def _acquire_budget_call(
        self,
        workspace_id: UUID | None,
        task_id: UUID | None,
        spec: ModelSpec,
        *,
        for_tool_call: bool = False,
        for_embedding: bool = False,
        reservation_micro_usd: int = 0,
    ) -> str | None:
        """Atomically admit a provider attempt under the task budget."""
        enforcing = self._settings.budget_enforcement_mode == "enforce"
        if enforcing:
            if task_id is None or workspace_id is None:
                raise BudgetExceededError(
                    "Enforced billable calls require both task_id and workspace_id"
                )
            if self._budget_tracker is None:
                raise BudgetExceededError(
                    "Enforced billable calls require an available budget tracker"
                )
        if self._budget_tracker is not None:
            return await self._budget_tracker.acquire_call(
                workspace_id,
                task_id,
                for_tool_call=for_tool_call,
                price_known=self._has_known_price(spec, for_embedding=for_embedding),
                **({"reservation_micro_usd": reservation_micro_usd} if enforcing else {}),
            )
        return None

    @staticmethod
    def _has_known_price(spec: ModelSpec, *, for_embedding: bool = False) -> bool:
        """Whether every price category this operation may use is configured."""
        if spec.price_unknown or spec.cost_per_1m_input is None:
            return False
        if for_embedding:
            return True
        return spec.cost_per_1m_output is not None

    def _effective_output_limit(self, spec: ModelSpec, requested: int | None) -> int | None:
        """Bound each provider response to a registered per-model output limit."""
        if self._settings.budget_enforcement_mode != "enforce":
            return requested
        model_limit = spec.context_output_limit
        if model_limit is None or model_limit <= 0:
            raise BudgetExceededError(
                f"Model '{spec.alias}' has no finite output-token limit for enforced budgeting"
            )
        if requested is not None and requested <= 0:
            raise ValueError("max_output_tokens must be positive")
        return min(requested, model_limit) if requested is not None else model_limit

    def _reservation_micro_usd(
        self,
        spec: ModelSpec,
        *,
        output_tokens: int = 0,
        embedding_items: int = 0,
    ) -> int:
        """Calculate a conservative registry-price upper bound for one provider attempt."""
        if self._settings.budget_enforcement_mode != "enforce":
            return 0
        if spec.price_unknown or spec.cost_per_1m_input is None:
            return 0  # Admission below rejects unknown pricing before provider execution.
        if output_tokens and spec.cost_per_1m_output is None:
            return 0
        input_limit = spec.context_input_limit * max(1, embedding_items)
        amount = Decimal(input_limit) * spec.cost_per_1m_input
        if output_tokens and spec.cost_per_1m_output is not None:
            amount += Decimal(output_tokens) * spec.cost_per_1m_output
        return cost_to_micro_usd(amount / Decimal(ONE_MILLION))

    def _reserved_usage(
        self,
        spec: ModelSpec,
        *,
        output_tokens: int = 0,
        embedding_items: int = 0,
    ) -> TokenUsage:
        """Usage fallback that conservatively accounts the full reservation on failed attempts."""
        input_tokens = spec.context_input_limit * max(1, embedding_items)
        estimated_cost = (
            Decimal(
                self._reservation_micro_usd(
                    spec, output_tokens=output_tokens, embedding_items=embedding_items
                )
            )
            / Decimal(ONE_MILLION)
            if self._has_known_price(spec, for_embedding=bool(embedding_items))
            else None
        )
        return TokenUsage(
            prompt_tokens=input_tokens,
            candidates_tokens=output_tokens,
            estimated_cost_usd=estimated_cost,
            price_unknown=spec.price_unknown,
            is_estimated_cost_complete=estimated_cost is not None,
            is_usage_estimate=True,
        )

    async def _release_budget_call(
        self, workspace_id: UUID | None, task_id: UUID | None, token: str | None
    ) -> None:
        """Best-effort lease cleanup that preserves cancellation and observes deferred errors."""
        if self._budget_tracker is None or token is None:
            return
        release_task = asyncio.create_task(
            self._budget_tracker.release_call(workspace_id, task_id, token)
        )
        try:
            await asyncio.shield(release_task)
        except asyncio.CancelledError:
            self._pending_partial_usage.add(release_task)
            release_task.add_done_callback(self._observe_budget_release)
            raise
        except Exception as exc:
            logger.warning("gateway_budget_admission_release_failed error=%s", type(exc).__name__)

    def _observe_budget_release(self, task: asyncio.Task[None]) -> None:
        self._pending_partial_usage.discard(task)
        if task.cancelled():
            logger.warning("gateway_budget_admission_release_cancelled")
            return
        error = task.exception()
        if error is not None:
            logger.warning(
                "gateway_budget_admission_release_failed error=%s",
                type(error).__name__,
            )

    async def _record_budget(
        self,
        workspace_id: UUID | None,
        task_id: UUID | None,
        usage: TokenUsage,
        is_tool_turn: bool = False,
        reservation_token: str | None = None,
    ) -> None:
        if self._budget_tracker is not None:
            kwargs: dict[str, Any] = {
                "workspace_id": workspace_id,
                "task_id": task_id,
                "usage": usage,
                "is_tool_turn": is_tool_turn,
            }
            if reservation_token is not None:
                kwargs["reservation_token"] = reservation_token
            await self._budget_tracker.record_usage(**kwargs)

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

        async def _call_model(attempt_spec: ModelSpec, was_fallback: bool) -> GatewayResponse:
            output_limit = self._effective_output_limit(attempt_spec, max_output_tokens)
            reservation = self._reservation_micro_usd(attempt_spec, output_tokens=output_limit or 0)
            reserved_usage = self._reserved_usage(attempt_spec, output_tokens=output_limit or 0)
            token = await self._acquire_budget_call(
                workspace_id,
                task_id,
                attempt_spec,
                reservation_micro_usd=reservation,
            )
            accounting_attempted = False
            try:
                config = types.GenerateContentConfig(
                    temperature=temperature,
                    max_output_tokens=output_limit,
                    system_instruction=(
                        sanitize_text(system_instruction) if system_instruction else None
                    ),
                )
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
                accounting_attempted = True
                await self._record_budget(
                    workspace_id,
                    task_id,
                    usage,
                    is_tool_turn=False,
                    reservation_token=token,
                )
                raise_if_blocked(response)

                return GatewayResponse(
                    text=text,
                    model_id=attempt_spec.model_id,
                    alias_used=attempt_spec.alias,
                    was_fallback=was_fallback,
                    usage=usage,
                )
            except BaseException:
                if token is not None and not accounting_attempted:
                    await self._record_partial_usage(
                        workspace_id, task_id, reserved_usage, reservation_token=token
                    )
                raise
            finally:
                await self._release_budget_call(workspace_id, task_id, token)

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

        async def _open(attempt_spec: ModelSpec, was_fallback: bool) -> _OpenedStream:
            output_limit = self._effective_output_limit(attempt_spec, max_output_tokens)
            reservation = self._reservation_micro_usd(attempt_spec, output_tokens=output_limit or 0)
            reserved_usage = self._reserved_usage(attempt_spec, output_tokens=output_limit or 0)
            token = await self._acquire_budget_call(
                workspace_id,
                task_id,
                attempt_spec,
                reservation_micro_usd=reservation,
            )
            stream: Any = None
            accounting_attempted = False
            try:
                config = types.GenerateContentConfig(
                    temperature=temperature,
                    max_output_tokens=output_limit,
                    system_instruction=(
                        sanitize_text(system_instruction) if system_instruction else None
                    ),
                )
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
                    try:
                        raise_if_blocked(first_chunk)
                    except ContentBlockedError:
                        usage = self._extract_usage(
                            first_chunk.usage_metadata,
                            attempt_spec,
                            prompt_estimate=prompt_estimate,
                        )
                        accounting_attempted = True
                        await self._record_partial_usage(
                            workspace_id, task_id, usage, reservation_token=token
                        )
                        raise
                return _OpenedStream(
                    attempt_spec, was_fallback, stream, iterator, first_chunk, token
                )
            except BaseException:
                await _close_stream_quietly(stream)
                if token is not None and not accounting_attempted:
                    await self._record_partial_usage(
                        workspace_id, task_id, reserved_usage, reservation_token=token
                    )
                await self._release_budget_call(workspace_id, task_id, token)
                raise

        opened, _, _ = await self._resilience.execute(
            self._registry, alias, _open, allow_fallback=allow_fallback
        )

        accumulated_text: list[str] = []
        chunks_yielded = 0
        latest_usage: TokenUsage | None = None

        try:
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
                        try:
                            raise_if_blocked(chunk)
                        except ContentBlockedError:
                            if chunk.usage_metadata is not None:
                                latest_usage = self._extract_usage(
                                    chunk.usage_metadata, opened.spec
                                )
                            raise
            except Exception as exc:
                partial_usage = self._partial_stream_usage(
                    opened.spec, latest_usage, prompt_estimate, accumulated_text
                )
                await self._record_partial_usage(
                    workspace_id,
                    task_id,
                    partial_usage,
                    reservation_token=opened.admission_token,
                )
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
                await self._record_partial_usage(
                    workspace_id,
                    task_id,
                    partial_usage,
                    reservation_token=opened.admission_token,
                )
                raise
            finally:
                await _close_stream_quietly(opened.stream)

            if latest_usage is None:
                latest_usage = self._extract_usage(
                    None,
                    opened.spec,
                    prompt_estimate=prompt_estimate,
                    output_text="".join(accumulated_text),
                )
            await self._record_budget(
                workspace_id,
                task_id,
                latest_usage,
                is_tool_turn=False,
                reservation_token=opened.admission_token,
            )
        finally:
            await self._release_budget_call(workspace_id, task_id, opened.admission_token)

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
        self,
        workspace_id: UUID | None,
        task_id: UUID | None,
        usage: TokenUsage,
        *,
        reservation_token: str | None = None,
    ) -> None:
        """Best-effort accounting without masking the original stream failure."""
        task = asyncio.create_task(
            self._record_budget(workspace_id, task_id, usage, reservation_token=reservation_token)
        )
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # A second cancellation must not discard the shielded write or its eventual error.
            self._pending_partial_usage.add(task)
            task.add_done_callback(self._observe_partial_usage)
            raise
        except Exception as exc:
            self._log_partial_usage_failure(exc)

    @staticmethod
    def _log_partial_usage_failure(exc: BaseException) -> None:
        if isinstance(exc, BudgetExceededError):
            logger.warning("gateway_partial_stream_usage_exceeded_budget")
        else:
            logger.warning(
                "gateway_partial_stream_usage_record_failed error=%s", type(exc).__name__
            )

    def _observe_partial_usage(self, task: asyncio.Task[None]) -> None:
        self._pending_partial_usage.discard(task)
        if task.cancelled():
            logger.warning("gateway_partial_stream_usage_record_cancelled")
            return
        exc = task.exception()
        if exc is not None:
            self._log_partial_usage_failure(exc)

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
        candidate_aliases = (
            self._registry.resolve_fallback_chain(alias) if allow_fallback else [alias]
        )
        last_error: str | None = None

        for current_alias in candidate_aliases:
            # Attempt 1: Standard generation
            response = await self.generate_text(
                alias=current_alias,
                contents=contents,
                system_instruction=system_instruction,
                task_id=task_id,
                workspace_id=workspace_id,
                allow_fallback=False,
            )

            model, error = parse_and_validate_json(response.text, response_schema)
            if model is not None:
                return model

            # Attempt 2: Exactly ONE self-repair turn using structured repair prompt.
            # The validation detail is deliberately not logged: it describes model output.
            logger.warning(
                "structured_output_validation_failed_attempting_repair alias=%s", current_alias
            )
            repair_prompt = format_repair_prompt(
                response.text, error or "Schema mismatch", response_schema
            )
            repair_contents = [*contents, repair_prompt]

            repair_response = await self.generate_text(
                alias=current_alias,
                contents=repair_contents,
                system_instruction=system_instruction,
                task_id=task_id,
                workspace_id=workspace_id,
                allow_fallback=False,
            )

            repaired_model, second_error = parse_and_validate_json(
                repair_response.text, response_schema
            )
            if repaired_model is not None:
                return repaired_model

            last_error = second_error
            logger.warning(
                "structured_output_repair_failed alias=%s error=%s", current_alias, second_error
            )

        raise StructuredOutputError(
            f"Structured output schema validation failed after 1 self-repair attempt: {last_error}"
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
        max_output_tokens: int | None = None,
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

        async def _call_model(attempt_spec: ModelSpec, was_fallback: bool) -> ToolGatewayResponse:
            output_limit = self._effective_output_limit(attempt_spec, max_output_tokens)
            reservation = self._reservation_micro_usd(attempt_spec, output_tokens=output_limit or 0)
            reserved_usage = self._reserved_usage(attempt_spec, output_tokens=output_limit or 0)
            token = await self._acquire_budget_call(
                workspace_id,
                task_id,
                attempt_spec,
                for_tool_call=True,
                reservation_micro_usd=reservation,
            )
            accounting_attempted = False
            try:
                config = types.GenerateContentConfig(
                    temperature=temperature,
                    max_output_tokens=output_limit,
                    system_instruction=(
                        sanitize_text(system_instruction) if system_instruction else None
                    ),
                    tools=cast(list[Any] | None, sdk_tools),
                )
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
                accounting_attempted = True
                await self._record_budget(
                    workspace_id,
                    task_id,
                    usage,
                    is_tool_turn=is_tool_turn,
                    reservation_token=token,
                )
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
            except BaseException:
                if token is not None and not accounting_attempted:
                    await self._record_partial_usage(
                        workspace_id, task_id, reserved_usage, reservation_token=token
                    )
                raise
            finally:
                await self._release_budget_call(workspace_id, task_id, token)

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
        embedding_items = max(1, len(sanitized)) if isinstance(sanitized, list) else 1

        async def _call_model(attempt_spec: ModelSpec, was_fallback: bool) -> Any:
            reservation = self._reservation_micro_usd(attempt_spec, embedding_items=embedding_items)
            reserved_usage = self._reserved_usage(attempt_spec, embedding_items=embedding_items)
            token = await self._acquire_budget_call(
                workspace_id,
                task_id,
                attempt_spec,
                for_embedding=True,
                reservation_micro_usd=reservation,
            )
            accounting_attempted = False
            try:
                response = await client.aio.models.embed_content(
                    model=attempt_spec.model_id,
                    contents=sanitized,
                    config=config,
                )
                usage = self._price(
                    TokenUsage(
                        prompt_tokens=estimate_tokens(sanitized),
                        price_unknown=attempt_spec.price_unknown,
                        is_usage_estimate=True,
                    ),
                    attempt_spec,
                )
                # Meter a received response before validating its vectors, including malformed ones.
                accounting_attempted = True
                await self._record_budget(
                    workspace_id,
                    task_id,
                    usage,
                    is_tool_turn=False,
                    reservation_token=token,
                )
                return response
            except BaseException:
                if token is not None and not accounting_attempted:
                    await self._record_partial_usage(
                        workspace_id, task_id, reserved_usage, reservation_token=token
                    )
                raise
            finally:
                await self._release_budget_call(workspace_id, task_id, token)

        response, attempt_spec, was_fallback = await self._resilience.execute(
            self._registry,
            alias,
            _call_model,
            allow_fallback=allow_fallback,
        )

        # A received response can be billed even when its vectors are unusable. Accounting occurs
        # in the attempt callback before it releases the admission lease.
        usage = self._price(
            TokenUsage(
                prompt_tokens=estimate_tokens(sanitized),
                price_unknown=attempt_spec.price_unknown,
                is_usage_estimate=True,
            ),
            attempt_spec,
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
            raise GatewayError(f"Model '{attempt_spec.model_id}' returned no embedding vectors.")

        return EmbeddingResponse(
            vectors=vectors,
            model_id=attempt_spec.model_id,
            usage=usage,
            alias_used=attempt_spec.alias,
            was_fallback=was_fallback,
        )

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
