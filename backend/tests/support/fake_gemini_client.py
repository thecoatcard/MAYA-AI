"""Offline test double for Google GenAI Client (aio.models).

Produces real google.genai.types response objects without contacting any external network.
Allows configurable error injection (429, 500, timeouts), streaming drops, and tool calls.
"""

from collections.abc import AsyncIterator
from typing import Any

from google.genai import types


class FakeAsyncModels:
    """Async models namespace simulating client.aio.models."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.errors_by_model: dict[str, list[Exception]] = {}
        self.default_text: str = "Fake model response text"
        self.queued_text_responses: list[str] = []
        self.default_tool_calls: list[dict[str, Any]] | None = None
        self.embedding_dimension: int = 768
        self.stream_error_after_chunk: int | None = None
        self.prompt_token_count: int = 100
        self.candidates_token_count: int = 50
        self.cached_content_token_count: int = 0
        self.thoughts_token_count: int = 0
        self.tool_use_prompt_token_count: int = 0

    def enqueue_errors(self, model: str, errors: list[Exception]) -> None:
        """Enqueue exceptions to be raised in sequence when model is invoked."""
        self.errors_by_model.setdefault(model, []).extend(errors)

    def _check_injected_error(self, model: str) -> None:
        if model in self.errors_by_model and self.errors_by_model[model]:
            raise self.errors_by_model[model].pop(0)

    async def generate_content(
        self,
        *,
        model: str,
        contents: Any,
        config: Any = None,
    ) -> types.GenerateContentResponse:
        self.calls.append(
            {
                "method": "generate_content",
                "model": model,
                "contents": contents,
                "config": config,
            }
        )
        self._check_injected_error(model)

        if self.default_tool_calls:
            parts = [
                types.Part.from_function_call(name=tc["name"], args=tc["args"])
                for tc in self.default_tool_calls
            ]
        elif self.queued_text_responses:
            parts = [types.Part.from_text(text=self.queued_text_responses.pop(0))]
        else:
            parts = [types.Part.from_text(text=self.default_text)]

        usage = types.GenerateContentResponseUsageMetadata(
            prompt_token_count=self.prompt_token_count,
            candidates_token_count=self.candidates_token_count,
            cached_content_token_count=self.cached_content_token_count,
            thoughts_token_count=self.thoughts_token_count,
            tool_use_prompt_token_count=self.tool_use_prompt_token_count,
            total_token_count=self.prompt_token_count + self.candidates_token_count,
        )

        return types.GenerateContentResponse(
            candidates=[
                types.Candidate(
                    content=types.Content(parts=parts, role="model"),
                    finish_reason=types.FinishReason.STOP,
                )
            ],
            usage_metadata=usage,
            model_version=model,
        )

    async def generate_content_stream(
        self,
        *,
        model: str,
        contents: Any,
        config: Any = None,
    ) -> AsyncIterator[types.GenerateContentResponse]:
        self.calls.append(
            {
                "method": "generate_content_stream",
                "model": model,
                "contents": contents,
                "config": config,
            }
        )
        self._check_injected_error(model)

        # Yield chunks simulating a streaming response
        words = self.default_text.split(" ")
        for idx, word in enumerate(words):
            if self.stream_error_after_chunk is not None and idx >= self.stream_error_after_chunk:
                raise ConnectionResetError("Simulated network stream dropout")

            chunk_text = word + (" " if idx < len(words) - 1 else "")
            is_last = idx == len(words) - 1
            usage = None
            if is_last:
                usage = types.GenerateContentResponseUsageMetadata(
                    prompt_token_count=self.prompt_token_count,
                    candidates_token_count=self.candidates_token_count,
                    cached_content_token_count=self.cached_content_token_count,
                    thoughts_token_count=self.thoughts_token_count,
                    tool_use_prompt_token_count=self.tool_use_prompt_token_count,
                    total_token_count=self.prompt_token_count + self.candidates_token_count,
                )

            yield types.GenerateContentResponse(
                candidates=[
                    types.Candidate(
                        content=types.Content(
                            parts=[types.Part.from_text(text=chunk_text)],
                            role="model",
                        ),
                        finish_reason=types.FinishReason.STOP if is_last else None,
                    )
                ],
                usage_metadata=usage,
                model_version=model,
            )

    async def embed_content(
        self,
        *,
        model: str,
        contents: Any,
        config: Any = None,
    ) -> types.EmbedContentResponse:
        self.calls.append(
            {
                "method": "embed_content",
                "model": model,
                "contents": contents,
                "config": config,
            }
        )
        self._check_injected_error(model)

        vector = [0.01] * self.embedding_dimension
        return types.EmbedContentResponse(
            embeddings=[types.ContentEmbedding(values=vector)],
        )

    async def count_tokens(
        self,
        *,
        model: str,
        contents: Any,
    ) -> types.CountTokensResponse:
        self.calls.append(
            {
                "method": "count_tokens",
                "model": model,
                "contents": contents,
            }
        )
        self._check_injected_error(model)
        return types.CountTokensResponse(total_tokens=42)


class FakeAio:
    """Async client container (client.aio)."""

    def __init__(self) -> None:
        self.models = FakeAsyncModels()


class FakeGeminiClient:
    """Offline test double matching google.genai.Client interface."""

    def __init__(self) -> None:
        self.aio = FakeAio()
