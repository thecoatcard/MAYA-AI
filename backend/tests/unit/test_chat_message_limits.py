"""Chat message size: no fixed character limit; the body guard and the model budget bound it."""

import json

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.core.http import CHAT_MAX_BODY_BYTES, MAX_BODY_BYTES, MaxBodySizeMiddleware
from app.services.chat_service import ChatValidationError, parse_message_request

CHAT_PATH = "/api/v1/chat/message"


def test_a_long_message_is_accepted_by_the_parser() -> None:
    content = "a" * 50_000
    command = parse_message_request(json.dumps({"content": content}).encode())
    assert command.content == content


def test_a_message_just_under_the_old_8000_limit_is_still_accepted() -> None:
    assert len(parse_message_request(json.dumps({"content": "b" * 9_000}).encode()).content) == 9000


@pytest.mark.parametrize("content", ["", "   \n\t "])
def test_empty_or_whitespace_only_content_is_still_refused(content: str) -> None:
    with pytest.raises(ChatValidationError) as error:
        parse_message_request(json.dumps({"content": content}).encode())
    assert error.value.fields == ("content",)


def test_control_characters_are_still_refused() -> None:
    with pytest.raises(ChatValidationError) as error:
        parse_message_request(b'{"content": "bell\\u0007"}')
    assert error.value.fields == ("content",)


def echo_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        MaxBodySizeMiddleware,
        max_bytes=MAX_BODY_BYTES,
        path_limits={CHAT_PATH: CHAT_MAX_BODY_BYTES},
    )

    @app.post(CHAT_PATH)
    async def chat(request: Request) -> dict[str, int]:
        return {"bytes": len(await request.body())}

    @app.post("/other")
    async def other(request: Request) -> dict[str, int]:
        return {"bytes": len(await request.body())}

    return app


def test_the_chat_route_accepts_a_body_up_to_four_mib() -> None:
    client = TestClient(echo_app())
    body = b"x" * CHAT_MAX_BODY_BYTES
    assert client.post(CHAT_PATH, content=body).json() == {"bytes": CHAT_MAX_BODY_BYTES}


def test_the_chat_route_refuses_a_five_mib_body_with_a_clear_413() -> None:
    response = TestClient(echo_app()).post(CHAT_PATH, content=b"x" * (5 * 1024 * 1024))
    assert response.status_code == 413
    body = response.json()
    assert body["error"] == "payload_too_large"
    assert body["message"] == "The request body is larger than the 4096 KiB limit for this route."


def test_other_routes_keep_the_64_kib_limit() -> None:
    response = TestClient(echo_app()).post("/other", content=b"x" * (MAX_BODY_BYTES + 1))
    assert response.status_code == 413
    assert response.json()["error"] == "payload_too_large"
