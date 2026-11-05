"""Validation and self-repair prompt generation for structured model outputs."""

import json
import re

from pydantic import BaseModel, ValidationError

from app.gateway.exceptions import StructuredOutputError

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*([\s\S]*?)\s*```$", re.IGNORECASE | re.DOTALL)


def strip_code_fences(text: str) -> str:
    """Strip markdown code fence wrapper (```json ... ``` or ``` ... ```) if present."""
    trimmed = text.strip()
    match = _CODE_FENCE_RE.match(trimmed)
    if match:
        return match.group(1).strip()
    return trimmed


def parse_and_validate_json[T: BaseModel](
    raw_text: str, schema: type[T]
) -> tuple[T | None, str | None]:
    """Attempt to parse and validate a raw JSON string against a Pydantic schema.

    Returns:
        tuple[T | None, str | None]: (validated_model, None) on success,
        or (None, error_description) on failure.
    """
    cleaned = strip_code_fences(raw_text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        return None, f"Invalid JSON syntax at line {exc.lineno}, column {exc.colno}: {exc.msg}"

    if not isinstance(data, dict) and not isinstance(data, list):
        return None, f"Expected JSON object or array, got {type(data).__name__}"

    try:
        model = schema.model_validate(data)
        return model, None
    except ValidationError as exc:
        return None, f"Schema validation error: {exc}"


def format_repair_prompt[T: BaseModel](raw_text: str, error_message: str, schema: type[T]) -> str:
    """Generate the structured self-repair prompt for a single corrective retry turn."""
    schema_json = json.dumps(schema.model_json_schema(), indent=2)
    return (
        "The previous response failed schema validation.\n\n"
        f"Validation error:\n{error_message}\n\n"
        f"Original output:\n{raw_text}\n\n"
        f"Required JSON Schema:\n{schema_json}\n\n"
        "Please provide ONLY the corrected, valid JSON conforming strictly to the schema. "
        "Do not include markdown code fences, notes, or any extraneous explanation."
    )


def validate_structured_output[T: BaseModel](raw_text: str, schema: type[T]) -> T:
    """Validate a raw text response against a schema or raise StructuredOutputError."""
    model, error = parse_and_validate_json(raw_text, schema)
    if model is None:
        raise StructuredOutputError(f"Structured output validation failed: {error}")
    return model
