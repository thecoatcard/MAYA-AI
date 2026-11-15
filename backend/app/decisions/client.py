"""REST client for the Gemini decision model (ADR-0029).

POST ``{base}/v1beta/decisions`` with the key in the ``x-goog-api-key`` header (never in the URL,
never logged). The key comes from the same ``KeyPool`` the model gateway uses, so rotation,
cooldowns and failover are shared. The response shape comes from Google's documentation and has
NOT been verified against the live service: parsing is strict about what it accepts and every
problem is raised as a ``DecisionError`` with a short reason code (never content or keys).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from app.decisions.questions import (
    Answer,
    Answers,
    ChoiceAnswer,
    NoulAnswer,
    Question,
    ScoreAnswer,
)
from app.gateway.key_pool import KeyPool

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com"
DECISIONS_PATH = "/v1beta/decisions"
_BUCKET_TOOL = "decide"
_SAFETY_CODES = {"safety", "blocklist", "content_blocked", "blocked", "prohibited_content"}
_RETRYABLE = {429, 500, 502, 503, 504}


class DecisionError(Exception):
    """A decision call failed. ``reason`` is a short stable code; the message holds no content."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class DecisionBlocked(DecisionError):
    """The service refused the content (safety/blocklist). Not a fault of the service."""


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    f = float(value)
    return f if math.isfinite(f) else None


def _unit(value: Any) -> float | None:
    f = _num(value)
    return f if f is not None and 0.0 <= f <= 1.0 else None


def _confidence(raw: Mapping[str, Any]) -> float | None:
    return _unit(raw.get("confidence"))


def parse_answer(question: Question, raw: Any) -> Answer | None:
    """One answer parsed against its question type, or None when it does not fit."""
    if not isinstance(raw, Mapping):
        return None
    if question.type == "noul":
        p = _unit(raw.get("noul"))
        return None if p is None else NoulAnswer(p, _confidence(raw))
    if question.type == "choice":
        picked = raw.get("choice")
        probs_raw = raw.get("probabilities")
        options = question.criteria if isinstance(question.criteria, Mapping) else {}
        if not isinstance(picked, str) or picked not in options:
            return None
        probs: dict[str, float] = {}
        if isinstance(probs_raw, Mapping):
            for k, v in probs_raw.items():
                p = _unit(v)
                if isinstance(k, str) and k in options and p is not None:
                    probs[k] = p
        if picked not in probs:
            return None  # a pick without its probability cannot be thresholded
        return ChoiceAnswer(picked, probs, _confidence(raw))
    levels = question.criteria if isinstance(question.criteria, Sequence) else []
    s = _num(raw.get("score"))
    if s is None or not 0.0 <= s <= max(len(levels) - 1, 0):
        return None
    probs_list = raw.get("probabilities")
    plist: list[float] = []
    if isinstance(probs_list, Sequence) and not isinstance(probs_list, (str, bytes)):
        plist = [p for p in (_unit(v) for v in probs_list) if p is not None]
    legend = raw.get("legend")
    return ScoreAnswer(s, legend if isinstance(legend, str) else None, plist, _confidence(raw))


def parse_response(questions: Sequence[Question], body: Any) -> Answers:
    """Validate a 200 body. Raises ``DecisionError('malformed')`` when nothing usable is there."""
    if not isinstance(body, Mapping):
        raise DecisionError("malformed")
    answers = body.get("answers")
    if not isinstance(answers, Mapping):
        raise DecisionError("malformed")
    parsed: dict[str, Answer] = {}
    for q in questions:
        a = parse_answer(q, answers.get(q.key))
        if a is not None:
            parsed[q.key] = a
    if not parsed:
        raise DecisionError("malformed")
    return Answers(parsed)


def _error_code(body: Any) -> str:
    err = body.get("error") if isinstance(body, Mapping) else None
    if isinstance(err, Mapping):
        for field_name in ("code", "status", "reason"):
            v = err.get(field_name)
            if isinstance(v, str):
                return v.lower()
    return ""


class DecisionClient:
    """Calls the decision endpoint with failover over the shared key pool."""

    def __init__(
        self,
        key_pool: KeyPool | None,
        *,
        model: str = "gemini-decision-1",
        base_url: str = DEFAULT_BASE_URL,
        http: httpx.AsyncClient | None = None,
        max_attempts: int = 2,
    ) -> None:
        self._pool = key_pool
        self._model = model
        self._url = base_url.rstrip("/") + DECISIONS_PATH
        self._http = http
        self._owns_http = http is None
        self._max_attempts = max(1, min(max_attempts, 2))  # one retry at most

    @property
    def model(self) -> str:
        return self._model

    async def aclose(self) -> None:
        if self._http is not None and self._owns_http:
            await self._http.aclose()

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient()
        return self._http

    async def create(
        self,
        input_value: str | Sequence[Mapping[str, Any]],
        questions: Sequence[Question],
        *,
        timeout: float,  # noqa: ASYNC109 - an HTTP timeout, not an asyncio one
    ) -> Answers:
        if self._pool is None or self._pool.is_empty:
            raise DecisionError("no_key")
        if not questions:
            raise DecisionError("no_questions")
        payload = {
            "model": self._model,
            "input": input_value if isinstance(input_value, str) else list(input_value),
            "questions": {q.key: q.to_payload() for q in questions},
        }
        bucket = (self._model, _BUCKET_TOOL)
        tried: list[str] = []
        last = DecisionError("unavailable")
        for _ in range(self._max_attempts):
            try:
                selection = self._pool.select("decision", exclude=tried, bucket=bucket)
            except ValueError:
                raise DecisionError("no_key") from None
            if selection is None:
                break
            tried.append(selection.label)
            try:
                resp = await self._client().post(
                    self._url,
                    json=payload,
                    headers={"x-goog-api-key": selection.secret.get_secret_value()},
                    timeout=timeout,
                )
            except httpx.TimeoutException:
                self._pool.report_failure(selection.secret, "timeout", bucket=bucket)
                raise DecisionError("timeout") from None
            except httpx.HTTPError:
                self._pool.report_failure(selection.secret, "unavailable", bucket=bucket)
                last = DecisionError("transport")
                continue
            status = resp.status_code
            if status == 200:
                try:
                    body = resp.json()
                except ValueError:
                    raise DecisionError("malformed") from None
                answers = parse_response(questions, body)
                self._pool.report_success(selection.secret, bucket)
                return answers
            try:
                err_body: Any = resp.json()
            except ValueError:
                err_body = None
            code = _error_code(err_body)
            if status == 400 and code in _SAFETY_CODES:
                raise DecisionBlocked("safety_block")
            if status in (401, 403) or (status == 400 and "api_key" in code.replace(" ", "_")):
                self._pool.report_failure(selection.secret, "401", bucket=bucket)
                last = DecisionError("auth")
                continue
            if status in _RETRYABLE:
                self._pool.report_failure(
                    selection.secret, "429" if status == 429 else "503", bucket=bucket
                )
                last = DecisionError(f"http_{status}")
                continue
            raise DecisionError(f"http_{status}")
        raise last
