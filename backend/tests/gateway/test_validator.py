"""Unit tests for structured output JSON validation and self-repair prompt generation."""

import pytest
from pydantic import BaseModel, Field

from app.gateway.exceptions import StructuredOutputError
from app.gateway.validator import (
    format_repair_prompt,
    parse_and_validate_json,
    strip_code_fences,
    validate_structured_output,
)


class UserProfile(BaseModel):
    name: str = Field(min_length=1)
    age: int = Field(ge=0)
    tags: list[str] = Field(default_factory=list)


def test_strip_code_fences() -> None:
    # Markdown json fence
    assert strip_code_fences('```json\n{"foo": "bar"}\n```') == '{"foo": "bar"}'
    # Generic code fence
    assert strip_code_fences('```\n{"foo": "bar"}\n```') == '{"foo": "bar"}'
    # Without fences
    assert strip_code_fences('  {"foo": "bar"}  ') == '{"foo": "bar"}'


def test_parse_and_validate_json_valid() -> None:
    raw = '```json\n{"name": "Alice", "age": 30, "tags": ["admin", "developer"]}\n```'
    model, error = parse_and_validate_json(raw, UserProfile)
    assert error is None
    assert isinstance(model, UserProfile)
    assert model.name == "Alice"
    assert model.age == 30
    assert model.tags == ["admin", "developer"]


def test_parse_and_validate_json_syntax_error() -> None:
    raw = '{"name": "Alice", "age": 30, tags: invalid}'
    model, error = parse_and_validate_json(raw, UserProfile)
    assert model is None
    assert error is not None
    assert "Invalid JSON syntax" in error


def test_parse_and_validate_json_schema_error() -> None:
    # Missing required 'age', age wrong type
    raw = '{"name": "Alice", "age": -5}'
    model, error = parse_and_validate_json(raw, UserProfile)
    assert model is None
    assert error is not None
    assert "Schema validation error" in error


def test_parse_and_validate_json_not_object_or_array() -> None:
    raw = '"just a plain string"'
    model, error = parse_and_validate_json(raw, UserProfile)
    assert model is None
    assert error is not None
    assert "Expected JSON object or array" in error


def test_format_repair_prompt() -> None:
    raw = '{"name": "Alice", "age": "thirty"}'
    error = "Schema validation error: age: Input should be a valid integer"
    prompt = format_repair_prompt(raw, error, UserProfile)
    assert "The previous response failed schema validation" in prompt
    assert error in prompt
    assert raw in prompt
    assert "UserProfile" in prompt or "age" in prompt
    assert "Please provide ONLY the corrected, valid JSON" in prompt


def test_validate_structured_output_helper() -> None:
    valid_raw = '{"name": "Bob", "age": 25}'
    model = validate_structured_output(valid_raw, UserProfile)
    assert model.name == "Bob"
    assert model.age == 25

    invalid_raw = '{"name": "", "age": 25}'
    with pytest.raises(StructuredOutputError, match="Structured output validation failed"):
        validate_structured_output(invalid_raw, UserProfile)
