"""A tiny JSON-schema subset for tool arguments (no new dependency).

Supported keywords: ``type`` (object, array, string, number, integer, boolean, null),
``properties``, ``required``, ``items``, ``enum``, ``minimum``/``maximum``, ``maxLength``,
``maxItems``. Anything else is ignored, so a schema can never make validation more permissive than
"the keywords above hold". Depth and size are bounded.
"""

from __future__ import annotations

from typing import Any, Final

MAX_DEPTH: Final = 8
MAX_ARGS_BYTES: Final = 50 * 1024

_TYPES: Final = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "null": type(None),
}


def _type_ok(value: Any, expected: str) -> bool:
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    wanted = _TYPES.get(expected)
    return wanted is None or isinstance(value, wanted)


def validate(value: Any, schema: Any, path: str = "args", depth: int = 0) -> list[str]:
    """Problems found (empty when ``value`` satisfies the supported keywords of ``schema``)."""
    if depth > MAX_DEPTH or not isinstance(schema, dict):
        return []
    problems: list[str] = []
    expected = schema.get("type")
    if isinstance(expected, str) and not _type_ok(value, expected):
        return [f"{path} must be of type {expected}"]
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        problems.append(f"{path} must be one of {enum[:10]}")
    if isinstance(value, str):
        limit = schema.get("maxLength")
        if isinstance(limit, int) and len(value) > limit:
            problems.append(f"{path} is longer than {limit} characters")
    if isinstance(value, int | float) and not isinstance(value, bool):
        low, high = schema.get("minimum"), schema.get("maximum")
        if isinstance(low, int | float) and value < low:
            problems.append(f"{path} must be at least {low}")
        if isinstance(high, int | float) and value > high:
            problems.append(f"{path} must be at most {high}")
    if isinstance(value, list):
        cap = schema.get("maxItems")
        if isinstance(cap, int) and len(value) > cap:
            problems.append(f"{path} has more than {cap} items")
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(value[:200]):
                problems.extend(validate(item, items, f"{path}[{index}]", depth + 1))
    if isinstance(value, dict):
        required = schema.get("required")
        if isinstance(required, list):
            problems.extend(f"{path}.{key} is required" for key in required if key not in value)
        properties = schema.get("properties")
        if isinstance(properties, dict):
            for key, sub in properties.items():
                if key in value:
                    problems.extend(validate(value[key], sub, f"{path}.{key}", depth + 1))
    return problems[:10]


__all__ = ["MAX_ARGS_BYTES", "validate"]
