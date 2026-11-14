"""Gemini API key pool with round-robin selection, failure cooldown, and failover."""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import SecretStr

from app.core.api_key_text import clean_api_key
from app.core.metrics import (
    GEMINI_KEY_COOLDOWNS_TOTAL,
    GEMINI_KEY_POOL_BAD,
    GEMINI_KEY_POOL_COOLING,
    GEMINI_KEY_POOL_SIZE,
)

logger = logging.getLogger(__name__)

# Agent roles in fixed order. Role i gets key slot ``i mod N`` over the ordered effective keys, so
# distinct roles use distinct keys first and only wrap when there are more roles than keys.
ROLE_ORDER: tuple[str, ...] = (
    "planner",
    "executor",
    "research",
    "document",
    "email",
    "pm",
    "coding",
    "longdoc",
)
CHAPTER_PREFIX = "chapter:"

# A quota bucket: ``(model_id, tool)`` where tool is ``generate`` or ``grounding``.
Bucket = tuple[str, str]
TOOL_GENERATE = "generate"
TOOL_GROUNDING = "grounding"


def affinity_slot(affinity: str, pool_size: int) -> int:
    """Stable key slot (0-based index into the ordered keys) for an affinity name.

    * A known role in ``ROLE_ORDER``: its position modulo the pool size.
    * ``chapter:{i}``: ``(len(ROLE_ORDER) + i) mod N``, so chapters fill the keys after the role
      keys in ring order (with 17 keys, chapters 0..8 use key#9..key#17, then wrap to key#1).
    * Anything else: a SHA-256 based stable slot (never Python's per-process ``hash``).
    """
    if pool_size <= 0:
        raise ValueError("pool_size must be positive")
    if affinity in ROLE_ORDER:
        return ROLE_ORDER.index(affinity) % pool_size
    if affinity.startswith(CHAPTER_PREFIX):
        tail = affinity[len(CHAPTER_PREFIX) :]
        if tail.isdigit():
            return (len(ROLE_ORDER) + int(tail)) % pool_size
    digest = hashlib.sha256(affinity.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % pool_size


def is_invalid_key_error(exc: Any) -> bool:
    """True when the error says this API key itself is unusable (401/403, or 400 key invalid).

    Only the HTTP status and well-known provider key-fault markers are used; the text is never
    logged. Such an error is about the key's project, so another key may well succeed.
    """
    from google.genai import errors as genai_errors

    if not isinstance(exc, genai_errors.APIError):
        return False
    code = exc.code
    if code in (401, 403):
        return True
    if code == 400:
        text = str(exc).lower()
        return any(m in text for m in ("api_key_invalid", "api key not valid", "api key expired"))
    return False


def extract_retry_after(exc: Any) -> float | None:
    """Extract retry delay in seconds from exception attributes, details, or headers."""
    if exc is None:
        return None

    # 1. Direct attributes on exception
    for attr in ("retry_after", "retry_delay", "retryAfter", "retryDelay"):
        val = getattr(exc, attr, None)
        if isinstance(val, (int, float)) and val > 0:
            return float(val)
        if isinstance(val, str):
            match = re.search(r"([0-9]+(?:\.[0-9]+)?)", val)
            if match:
                return float(match.group(1))

    # 2. Response headers
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) if response is not None else None
    if headers and hasattr(headers, "get"):
        header_val = headers.get("retry-after") or headers.get("Retry-After")
        if header_val:
            match = re.search(r"([0-9]+(?:\.[0-9]+)?)", str(header_val))
            if match:
                return float(match.group(1))

    # 3. Google RPC status details (RetryInfo / retryDelay)
    details = getattr(exc, "details", None)
    if isinstance(details, (list, tuple)):
        for d in details:
            if isinstance(d, dict):
                delay = d.get("retryDelay") or d.get("retry_delay")
                if isinstance(delay, str):
                    match = re.search(r"([0-9]+(?:\.[0-9]+)?)", delay)
                    if match:
                        return float(match.group(1))
                elif isinstance(delay, (int, float)) and delay > 0:
                    return float(delay)
                elif isinstance(delay, dict) and "seconds" in delay:
                    return float(delay["seconds"])

    return None


def _scope_text(bucket: Bucket | None) -> str:
    """Log-safe scope of a cooldown: ``global`` or ``model/tool`` (never key text)."""
    return "global" if bucket is None else f"{bucket[0]}/{bucket[1]}"


@dataclass(frozen=True)
class KeySelection:
    """A selected key: the secret, its ``key#N`` label, and whether it is out of cooldown."""

    secret: SecretStr
    label: str
    healthy: bool

    def __repr__(self) -> str:
        return f"<KeySelection label={self.label} healthy={self.healthy}>"


@dataclass
class KeyEntry:
    """Internal key entry storing secret and non-reversible identifier."""

    secret: SecretStr
    label: str
    cooling_until: float = 0.0
    # Quota and availability are per (model id, tool) per project, so a 429 for one bucket must
    # not cool the key for other models or tools: ``bucket -> monotonic time it recovers``.
    bucket_cooling: dict[Bucket, float] = field(default_factory=dict)
    is_bad: bool = False
    reported_bad_log: bool = False

    def cooling_deadline(self, bucket: Bucket | None) -> float:
        """When this key is usable again for ``bucket`` (global cooldown and that bucket's)."""
        if bucket is None:
            return self.cooling_until
        return max(self.cooling_until, self.bucket_cooling.get(bucket, 0.0))

    def is_cooling(self, now: float, bucket: Bucket | None = None) -> bool:
        return now < self.cooling_deadline(bucket)

    def any_cooling(self, now: float) -> bool:
        return now < max([self.cooling_until, *self.bucket_cooling.values()])

    def __repr__(self) -> str:
        return f"<KeyEntry label={self.label} bad={self.is_bad}>"


class KeyPool:
    """Thread-safe round-robin pool of Gemini API keys with health tracking."""

    def __init__(
        self,
        keys: Sequence[SecretStr | str],
        *,
        clock: Callable[[], float] = time.monotonic,
        default_quota_cooldown: float = 60.0,
        default_unavailable_cooldown: float = 10.0,
    ) -> None:
        self._clock = clock
        self._default_quota_cooldown = default_quota_cooldown
        self._default_unavailable_cooldown = default_unavailable_cooldown
        self._lock = threading.Lock()
        self._index = 0

        self._entries: list[KeyEntry] = []
        self._by_secret: dict[str, KeyEntry] = {}

        for idx, k in enumerate(keys, start=1):
            raw = clean_api_key(k.get_secret_value() if isinstance(k, SecretStr) else str(k))
            secret = SecretStr(raw)
            if raw and raw not in self._by_secret:
                entry = KeyEntry(secret=secret, label=f"key#{idx}")
                self._entries.append(entry)
                self._by_secret[raw] = entry

        self._update_metrics_locked()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def __repr__(self) -> str:
        with self._lock:
            now = self._clock()
            cooling = sum(1 for e in self._entries if not e.is_bad and e.any_cooling(now))
            bad = sum(1 for e in self._entries if e.is_bad)
            return f"<KeyPool total={len(self._entries)} cooling={cooling} bad={bad}>"

    @property
    def is_empty(self) -> bool:
        with self._lock:
            return len(self._entries) == 0

    @property
    def cooling_count(self) -> int:
        with self._lock:
            now = self._clock()
            return sum(1 for e in self._entries if not e.is_bad and e.any_cooling(now))

    @property
    def bad_count(self) -> int:
        with self._lock:
            return sum(1 for e in self._entries if e.is_bad)

    def _update_metrics_locked(self) -> None:
        now = self._clock()
        cooling = sum(1 for e in self._entries if not e.is_bad and e.any_cooling(now))
        GEMINI_KEY_POOL_SIZE.set(len(self._entries))
        GEMINI_KEY_POOL_COOLING.set(cooling)
        GEMINI_KEY_POOL_BAD.set(sum(1 for e in self._entries if e.is_bad))

    def label_for(self, key: SecretStr | str) -> str | None:
        """The ``key#N`` label of a pool key, or None when it is not in the pool."""
        raw = key.get_secret_value() if isinstance(key, SecretStr) else str(key).strip()
        with self._lock:
            entry = self._by_secret.get(raw)
            return entry.label if entry else None

    def role_map(self) -> dict[str, str]:
        """Role -> ``key#N`` label for every known role (labels only, never key text)."""
        with self._lock:
            n = len(self._entries)
            if n == 0:
                return {}
            return {role: self._entries[affinity_slot(role, n)].label for role in ROLE_ORDER}

    def log_assignments(self) -> None:
        """Log the pool size and the role -> label map once (labels only)."""
        mapping = self.role_map()
        with self._lock:
            n = len(self._entries)
            chapters_start = (
                self._entries[affinity_slot(f"{CHAPTER_PREFIX}0", n)].label if n else "none"
            )
        logger.info(
            "gemini_key_pool_ready size=%d role_map=%s chapters_start=%s",
            n,
            ",".join(f"{role}={label}" for role, label in mapping.items()) or "none",
            chapters_start,
        )

    def select(
        self,
        affinity: str | None = None,
        exclude: Collection[str] = (),
        bucket: Bucket | None = None,
    ) -> KeySelection | None:
        """Pick a key in O(pool): the affinity key if healthy, else the next healthy in ring order.

        ``bucket`` (model id, tool) makes health bucket-aware: a key cooling only for another
        bucket is still healthy here. Without affinity this is round-robin. Labels in
        ``exclude`` (keys this call already tried) are never returned. When no eligible key is
        healthy, the eligible one recovering soonest is returned with ``healthy=False``. ``None``
        means every key is excluded or bad.

        Raises:
            ValueError: the pool is empty or every key is invalid or unauthorized.
        """
        with self._lock:
            if not self._entries:
                raise ValueError("KeyPool is empty.")
            if all(e.is_bad for e in self._entries):
                raise ValueError("All keys in KeyPool are invalid or unauthorized.")

            now = self._clock()
            n = len(self._entries)
            start = affinity_slot(affinity, n) if affinity else self._index
            for offset in range(n):
                idx = (start + offset) % n
                entry = self._entries[idx]
                if entry.is_bad or entry.label in exclude or entry.is_cooling(now, bucket):
                    continue
                if not affinity:
                    self._index = (idx + 1) % n
                return KeySelection(entry.secret, entry.label, True)

            eligible = [e for e in self._entries if not e.is_bad and e.label not in exclude]
            if not eligible:
                return None
            soonest = min(eligible, key=lambda e: e.cooling_deadline(bucket))
            return KeySelection(soonest.secret, soonest.label, False)

    def get_next(
        self, affinity: str | None = None, exclude: Collection[str] = ()
    ) -> tuple[SecretStr, str]:
        """Select a key (see ``select``) as ``(secret, label)``."""
        selection = self.select(affinity, exclude)
        if selection is None:
            raise ValueError("No eligible key in KeyPool for this call.")
        return selection.secret, selection.label

    def report_success(self, key: SecretStr | str, bucket: Bucket | None = None) -> None:
        """Clear the key's cooldowns on a successful call (a success proves the key works)."""
        raw = key.get_secret_value() if isinstance(key, SecretStr) else str(key).strip()
        with self._lock:
            entry = self._by_secret.get(raw)
            if entry and not entry.is_bad:
                entry.cooling_until = 0.0
                if bucket is not None:
                    entry.bucket_cooling.pop(bucket, None)
                self._update_metrics_locked()

    @staticmethod
    def _cool(entry: KeyEntry, bucket: Bucket | None, until: float) -> None:
        if bucket is None:
            entry.cooling_until = max(entry.cooling_until, until)
        else:
            entry.bucket_cooling[bucket] = max(entry.bucket_cooling.get(bucket, 0.0), until)

    def report_failure(
        self,
        key: SecretStr | str,
        kind: str | Exception | None = None,
        *,
        retry_after: float | None = None,
        bucket: Bucket | None = None,
    ) -> None:
        """Record error and apply appropriate cooldown or bad status.

        With ``bucket`` a quota or availability failure cools only that ``(model, tool)`` bucket
        of the key; without it the whole key cools. An invalid key (400/401/403) is always global.
        """
        raw = key.get_secret_value() if isinstance(key, SecretStr) else str(key).strip()
        with self._lock:
            entry = self._by_secret.get(raw)
            if not entry:
                return

            now = self._clock()
            hint = retry_after
            if hint is None and isinstance(kind, Exception):
                hint = extract_retry_after(kind)

            is_auth_error = False
            is_quota_error = False
            is_unavailable_error = False

            if isinstance(kind, Exception):
                from google.genai import errors as genai_errors

                code = kind.code if isinstance(kind, genai_errors.APIError) else None
                if is_invalid_key_error(kind):
                    is_auth_error = True
                elif code == 429:
                    is_quota_error = True
                elif code in (500, 502, 503, 504) or isinstance(kind, (TimeoutError, OSError)):
                    is_unavailable_error = True
                elif hint is not None:
                    is_quota_error = True
                elif any(x in str(kind).lower() for x in ("rate", "quota", "429", "exhausted")):
                    is_quota_error = True
            elif isinstance(kind, str):
                k = kind.lower()
                auth_keywords = ("401", "403", "auth", "unauthenticated", "permission_denied")
                if any(x in k for x in auth_keywords):
                    is_auth_error = True
                elif any(x in k for x in ("429", "quota", "resource_exhausted")):
                    is_quota_error = True
                elif any(x in k for x in ("503", "500", "unavailable", "timeout")):
                    is_unavailable_error = True
                elif hint is not None:
                    is_quota_error = True

            metric_labels: dict[str, str] = {"key": entry.label}
            if bucket is not None:
                metric_labels["model"], metric_labels["tool"] = bucket

            if is_auth_error:
                entry.is_bad = True
                if not entry.reported_bad_log:
                    entry.reported_bad_log = True
                    logger.warning("gemini_api_key_marked_bad label=%s", entry.label)
                    GEMINI_KEY_COOLDOWNS_TOTAL.inc(labels={**metric_labels, "reason": "invalid"})
            elif is_quota_error:
                duration = hint if (hint is not None and hint > 0) else self._default_quota_cooldown
                self._cool(entry, bucket, now + duration)
                GEMINI_KEY_COOLDOWNS_TOTAL.inc(labels={**metric_labels, "reason": "quota"})
                logger.warning(
                    "gemini_api_key_quota_cooldown label=%s scope=%s duration=%.1fs",
                    entry.label,
                    _scope_text(bucket),
                    duration,
                )
            elif is_unavailable_error:
                duration = (
                    hint if (hint is not None and hint > 0) else self._default_unavailable_cooldown
                )
                self._cool(entry, bucket, now + duration)
                GEMINI_KEY_COOLDOWNS_TOTAL.inc(labels={**metric_labels, "reason": "unavailable"})
                logger.warning(
                    "gemini_api_key_unavailable_cooldown label=%s scope=%s duration=%.1fs",
                    entry.label,
                    _scope_text(bucket),
                    duration,
                )

            self._update_metrics_locked()
