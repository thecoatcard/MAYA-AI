import json
import logging

from app.core.logging import JsonFormatter, request_id_context, trace_id_context


def test_json_logging_redacts_secrets_and_urls() -> None:
    formatter = JsonFormatter(("example-only",))
    record = logging.LogRecord(
        "maya",
        logging.ERROR,
        "",
        1,
        "failed postgresql://maya:example-only@localhost/db Authorization: Bearer abc123",
        (),
        None,
    )
    output = formatter.format(record)
    assert "example-only" not in output
    assert "abc123" not in output
    assert "maya:example" not in output
    assert json.loads(output)["level"] == "ERROR"


def test_exception_message_is_not_serialized() -> None:
    error = ValueError("private connection details")
    record = logging.LogRecord(
        "maya", logging.ERROR, "", 1, "dependency unavailable", (), (ValueError, error, None)
    )
    output = JsonFormatter(()).format(record)
    assert "private connection details" not in output
    assert json.loads(output)["exception_type"] == "ValueError"


def test_correlation_context_is_included_and_reset() -> None:
    request_token = request_id_context.set("request-1")
    trace_token = trace_id_context.set("trace-1")
    try:
        record = logging.LogRecord("maya", logging.INFO, "", 1, "ready", (), None)
        payload = json.loads(JsonFormatter(()).format(record))
        assert payload["request_id"] == "request-1"
        assert payload["trace_id"] == "trace-1"
    finally:
        request_id_context.reset(request_token)
        trace_id_context.reset(trace_token)
    assert request_id_context.get() is None
