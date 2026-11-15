"""Unit tests for Canonical Payload Serialization and SHA-256 Hashing (Decision D5)."""

from decimal import Decimal
from uuid import UUID, uuid4

from app.mcp.approvals import ApprovalPayload, canonical_json, compute_payload_hash


def test_canonical_json_sorts_keys_and_eliminates_whitespace() -> None:
    data1 = {"b": 2, "a": 1, "c": {"z": 26, "y": 25}}
    data2 = {"c": {"y": 25, "z": 26}, "a": 1, "b": 2}

    json1 = canonical_json(data1)
    json2 = canonical_json(data2)

    assert json1 == json2
    assert " " not in json1
    assert "\n" not in json1
    assert json1 == '{"a":1,"b":2,"c":{"y":25,"z":26}}'


def test_payload_hash_is_deterministic_across_dictionary_order() -> None:
    ws = uuid4()
    user = uuid4()
    key = "idem_key_123"

    args1 = {"recipient": "alice@example.com", "subject": "Hello", "body": "World"}
    args2 = {"body": "World", "subject": "Hello", "recipient": "alice@example.com"}

    hash1 = compute_payload_hash("gmail_send", args1, ws, user, key)
    hash2 = compute_payload_hash("gmail_send", args2, ws, user, key)

    assert hash1 == hash2
    assert len(hash1) == 64  # SHA-256 hex length


def test_payload_hash_is_sensitive_to_any_modification() -> None:
    ws = uuid4()
    user = uuid4()
    key = "idem_key_123"
    args = {"to": "bob@example.com", "amount": 100}

    base_hash = compute_payload_hash("execute_payment", args, ws, user, key)

    # Change tool name
    assert compute_payload_hash("other_tool", args, ws, user, key) != base_hash

    # Change an argument value
    modified_args = {"to": "bob@example.com", "amount": 101}
    assert compute_payload_hash("execute_payment", modified_args, ws, user, key) != base_hash

    # Add extra argument
    extra_args = {"to": "bob@example.com", "amount": 100, "memo": "test"}
    assert compute_payload_hash("execute_payment", extra_args, ws, user, key) != base_hash

    # Change workspace
    assert compute_payload_hash("execute_payment", args, uuid4(), user, key) != base_hash

    # Change user
    assert compute_payload_hash("execute_payment", args, ws, uuid4(), key) != base_hash

    # Change idempotency key
    assert compute_payload_hash("execute_payment", args, ws, user, "other_key") != base_hash


def test_approval_payload_dataclass_methods() -> None:
    ws = UUID("00000000-0000-0000-0000-000000000001")
    user = UUID("00000000-0000-0000-0000-000000000002")
    payload = ApprovalPayload(
        tool="git_push",
        args={"remote": "origin", "branch": "main"},
        workspace_id=ws,
        user_id=user,
        idempotency_key="key_1",
    )

    d = payload.to_dict()
    assert d["tool"] == "git_push"
    assert d["user_id"] == str(user)
    assert d["workspace_id"] == str(ws)

    c_json = payload.canonical_json()
    assert '"tool":"git_push"' in c_json
    assert '"branch":"main"' in c_json

    computed = payload.compute_hash()
    assert computed == compute_payload_hash(
        "git_push", {"branch": "main", "remote": "origin"}, ws, user, "key_1"
    )


def test_canonical_number_formatting() -> None:
    data_float_int = {"val": 5.0}
    data_int = {"val": 5}
    assert canonical_json(data_float_int) == canonical_json(data_int)

    data_decimal1 = {"amount": Decimal("10.50")}
    data_decimal2 = {"amount": Decimal("10.5")}
    assert canonical_json(data_decimal1) == canonical_json(data_decimal2)


def test_rfc_8785_edge_cases() -> None:
    import pytest

    from app.mcp.approvals import parse_json_safely

    # 1. Negative zero normalized to 0
    assert canonical_json({"val": -0.0}) == '{"val":0}'
    assert canonical_json({"val": 0.0}) == '{"val":0}'

    # 2. Duplicate keys rejected
    with pytest.raises(ValueError, match="Duplicate JSON key rejected: key"):
        parse_json_safely('{"key": 1, "key": 2}')

    # 3. Unicode UTF-8 preserved directly without \\u escapes
    data_unicode = {"city": "München", "symbol": "🚀"}
    canonical = canonical_json(data_unicode)
    assert canonical == '{"city":"München","symbol":"🚀"}'
    assert "\\u" not in canonical

    # 4. Array ordering preserved
    assert canonical_json({"arr": [3, 1, 2]}) == '{"arr":[3,1,2]}'

    # 5. Null properties preserved
    assert canonical_json({"present": None}) == '{"present":null}'
