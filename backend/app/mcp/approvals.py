"""Canonical payload serialization and deterministic SHA-256 hashing (Decision D5)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID


def canonicalize_data(value: Any) -> Any:
    """Recursively transform data into a deterministic canonical representation (RFC 8785)."""
    if isinstance(value, dict):
        return {
            str(k): canonicalize_data(v)
            for k, v in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [canonicalize_data(item) for item in value]
    if isinstance(value, set):
        return [canonicalize_data(item) for item in sorted(value, key=str)]
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        # Normalize decimals so 1.0 and 1.00 produce identical representations
        normalized = value.normalize()
        if normalized == Decimal(0):
            return 0
        return str(normalized)
    if isinstance(value, float):
        # RFC 8785 §3.2.2.3: -0.0 MUST be serialized as 0
        if value == 0.0 or value == -0.0:
            return 0
        # Format floats canonically without trailing zeroes after decimal if integer
        if value.is_integer():
            return int(value)
        return value
    return value


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Strict JSON parser hook rejecting duplicate object keys (RFC 8785)."""
    res: dict[str, Any] = {}
    for key, val in pairs:
        if key in res:
            raise ValueError(f"Duplicate JSON key rejected: {key}")
        res[key] = val
    return res


def parse_json_safely(raw_json: str) -> Any:
    """Parse JSON string while strictly rejecting duplicate object keys."""
    return json.loads(raw_json, object_pairs_hook=_reject_duplicate_keys)


def canonical_json(data: Any) -> str:
    """Serialize data into a deterministic JSON string with sorted keys (RFC 8785)."""
    canonical_data = canonicalize_data(data)
    return json.dumps(
        canonical_data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


@dataclass(frozen=True, slots=True)
class ApprovalPayload:
    """Cryptographic execution payload for an approval-gated tool invocation."""

    tool: str
    args: dict[str, Any]
    workspace_id: UUID
    user_id: UUID
    idempotency_key: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "args": self.args,
            "idempotency_key": self.idempotency_key,
            "tool": self.tool,
            "user_id": str(self.user_id),
            "workspace_id": str(self.workspace_id),
        }

    def canonical_json(self) -> str:
        return canonical_json(self.to_dict())

    def compute_hash(self) -> str:
        data_str = self.canonical_json().encode("utf-8")
        return hashlib.sha256(data_str).hexdigest()


def compute_payload_hash(
    tool: str,
    args: dict[str, Any],
    workspace_id: UUID,
    user_id: UUID,
    idempotency_key: str,
) -> str:
    """Calculate deterministic SHA-256 digest over the canonical execution payload."""
    payload = ApprovalPayload(
        tool=tool,
        args=args,
        workspace_id=workspace_id,
        user_id=user_id,
        idempotency_key=idempotency_key,
    )
    return payload.compute_hash()
