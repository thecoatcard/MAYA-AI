import asyncio
import threading
from collections.abc import AsyncIterator, Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app import __main__
from app.core.http import MAX_BODY_BYTES, MaxBodySizeMiddleware
from app.core.security import Overloaded, PasswordService
from app.main import create_app
from tests.integration.test_auth_api import Api, make_api
from tests.support.passwords import FAST_PARAMETERS
from tests.support.settings import make_settings


@pytest.fixture
def api() -> Iterator[Api]:
    yield from make_api()


# ------------------------------------------------------------------ request body limit


def echo_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(MaxBodySizeMiddleware, max_bytes=100)

    @app.post("/echo")
    async def echo(request: Request) -> dict[str, int]:
        return {"bytes": len(await request.body())}

    return app


def test_bodies_up_to_the_limit_pass() -> None:
    client = TestClient(echo_app())
    assert client.post("/echo", content=b"x" * 100).json() == {"bytes": 100}


def test_a_declared_oversize_body_is_refused_before_it_is_read() -> None:
    response = TestClient(echo_app()).post("/echo", content=b"x" * 101)
    assert (response.status_code, response.json()) == (413, {"error": "payload_too_large"})


async def test_a_streamed_body_that_outgrows_the_limit_is_cut_off() -> None:
    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(10):
            yield b"x" * 30  # no Content-Length: the size is only known as it arrives

    transport = httpx.ASGITransport(app=echo_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/echo", content=chunks())
    assert (response.status_code, response.json()) == (413, {"error": "payload_too_large"})


def test_the_application_applies_the_documented_limit(api: Api) -> None:
    ok = api.client.post(
        "/api/v1/auth/token", content=b"{}", headers={"Content-Type": "application/json"}
    )
    assert ok.status_code == 422  # reached the route and failed validation, not the size guard
    big = api.client.post(
        "/api/v1/auth/token",
        content=b" " * (MAX_BODY_BYTES + 1),
        headers={"Content-Type": "application/json"},
    )
    assert (big.status_code, big.json()) == (413, {"error": "payload_too_large"})


# ------------------------------------------------------------------- credential caching


def test_token_responses_are_marked_uncacheable(api: Api) -> None:
    account = api.register_and_verify()
    login = api.post("token", username=account["username"], password=account["password"])
    assert login.headers["Cache-Control"] == "no-store"
    assert login.headers["Pragma"] == "no-cache"
    refreshed = api.post("refresh", refresh_token=login.json()["refresh_token"])
    assert refreshed.headers["Cache-Control"] == "no-store"


# ---------------------------------------------------------------- client address trust


def test_the_server_is_started_without_trusting_forwarded_headers() -> None:
    with (
        patch("app.__main__.uvicorn.run") as run,
        patch.object(__main__, "Settings", return_value=make_settings()),
        patch.object(__main__, "create_app", return_value=MagicMock()),
        patch("sys.argv", ["app"]),
    ):
        __main__.main()
    assert run.call_args.kwargs["proxy_headers"] is False
    assert run.call_args.kwargs["host"] == "127.0.0.1"


def test_a_client_supplied_forwarded_for_header_does_not_change_the_rate_limit_key(
    api: Api,
) -> None:
    body = {"username": "nobody_at_all", "password": "not the password!!"}
    for index in range(10):
        api.client.post(
            "/api/v1/auth/token", json=body, headers={"X-Forwarded-For": f"10.0.0.{index}"}
        )
    blocked = api.client.post(
        "/api/v1/auth/token", json=body, headers={"X-Forwarded-For": "10.9.9.9"}
    )
    assert blocked.status_code == 429


# ----------------------------------------------------------- password hashing resources


async def test_hashing_runs_on_its_own_threads_not_the_default_executor() -> None:
    service = PasswordService(FAST_PARAMETERS, concurrency=2)
    names: list[str] = []
    original = service._hasher.hash  # noqa: SLF001

    def record(value: str) -> str:
        names.append(threading.current_thread().name)
        return original(value)

    with patch.object(service, "_hasher", MagicMock(hash=record)):
        await service.hash("a sufficiently long password")
    assert names and all(name.startswith("maya-hash") for name in names)


async def test_a_flood_beyond_the_queue_is_refused_instead_of_piling_up() -> None:
    service = PasswordService(FAST_PARAMETERS, concurrency=1, queue=2)
    gate = threading.Event()
    original = service._hasher.hash  # noqa: SLF001

    def slow(value: str) -> str:
        gate.wait(timeout=5)
        return original(value)

    with patch.object(service, "_hasher", MagicMock(hash=slow)):
        tasks = [
            asyncio.create_task(service.hash("a sufficiently long password")) for _ in range(6)
        ]
        await asyncio.sleep(0.05)
        refused = [
            task for task in tasks if task.done() and isinstance(task.exception(), Overloaded)
        ]
        assert len(refused) == 3  # one running, two queued, the rest refused at once
        gate.set()
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
    assert sum(isinstance(outcome, str) for outcome in outcomes) == 3


def test_overload_is_answered_with_503(api: Api) -> None:
    async def busy(*_: Any) -> str:
        raise Overloaded

    with patch.object(api.harness.passwords, "hash", AsyncMock(side_effect=Overloaded)):
        response = api.client.post("/api/v1/auth/register", json=api.registration())
    assert (response.status_code, response.json()) == (503, {"error": "service_unavailable"})


def test_create_app_installs_the_body_guard() -> None:
    with patch("app.main.Resources"):
        app = create_app(make_settings())
    assert any(
        getattr(middleware, "cls", None) is MaxBodySizeMiddleware
        for middleware in app.user_middleware
    )
