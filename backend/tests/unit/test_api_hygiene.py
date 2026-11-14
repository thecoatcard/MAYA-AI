"""Env-file selection, log redaction, error envelope, static web, metrics label, desktop stdin."""

import logging
import os
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from app.__main__ import resolve_env_file, uvicorn_log_config
from app.api.v1.errors import install_error_handlers
from app.api.v1.mcp_admin import normalize_transport
from app.core.config import Settings
from app.core.logging import SecretQueryFilter, install_secret_query_filter, redact_secret_queries
from app.core.metrics import route_label
from app.core.static_web import mount_static_web
from app.desktop import wait_for_stdin_close
from tests.support.settings import make_settings

JWT = ".".join(("header" + "x" * 12, "payload" + "y" * 12, "signature" + "z" * 12))


# ---------------------------------------------------------------- environment file selection


def test_an_example_file_does_not_pull_in_the_private_sibling(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("MAYA_SECRET=1\n")
    example = tmp_path / ".env.example"
    example.write_text("X=1\n")
    assert resolve_env_file(example, {}) == example  # exactly the file named, nothing else


def test_no_file_is_discovered_implicitly() -> None:
    assert resolve_env_file(None, {}) is None
    assert resolve_env_file(None, {"MAYA_ENV_FILE": "  "}) is None


def test_the_environment_variable_selects_a_file_and_the_argument_wins() -> None:
    assert resolve_env_file(None, {"MAYA_ENV_FILE": "a.env"}) == Path("a.env")
    assert resolve_env_file(Path("b.env"), {"MAYA_ENV_FILE": "a.env"}) == Path("b.env")


# ---------------------------------------------------------------------------- log redaction


@pytest.mark.parametrize(
    "line",
    [
        f"WebSocket /api/v1/voice/live?token={JWT} [accepted]",
        f'"GET /x?access_token={JWT}&a=1 HTTP/1.1"',
        f"url=/y?refresh_token={JWT}",
        "POST /z?password=hunter2hunter2&u=1",
        "GET /p?API_KEY=abcdef123456",
    ],
)
def test_secret_query_values_are_redacted(line: str) -> None:
    redacted = redact_secret_queries(line)
    assert JWT not in redacted and "hunter2" not in redacted and "abcdef123456" not in redacted
    assert "[redacted]" in redacted


def test_non_secret_queries_are_left_alone() -> None:
    line = "GET /api/v1/chat/sessions?limit=20&cursor=abc"
    assert redact_secret_queries(line) == line


def test_the_filter_redacts_formatted_arguments_of_any_record() -> None:
    record = logging.LogRecord(
        "uvicorn.error", logging.INFO, "", 1, "%s %s", ("WebSocket", f"/v?token={JWT}"), None
    )
    assert SecretQueryFilter().filter(record) is True
    assert JWT not in record.getMessage()


def test_installed_on_the_server_loggers_and_the_uvicorn_log_config() -> None:
    install_secret_query_filter()
    install_secret_query_filter()  # idempotent
    logger = logging.getLogger("uvicorn.error")
    assert sum(isinstance(item, SecretQueryFilter) for item in logger.filters) == 1
    config = uvicorn_log_config()
    assert all("redact_secrets" in handler["filters"] for handler in config["handlers"].values())


def test_a_real_uvicorn_style_line_is_clean_after_the_logger_filter(
    caplog: pytest.LogCaptureFixture,
) -> None:
    install_secret_query_filter()
    logger = logging.getLogger("uvicorn.error")
    stream = MagicMock()
    handler = logging.StreamHandler(stream)
    handler.addFilter(SecretQueryFilter())
    logger.addHandler(handler)
    try:
        logger.warning("%s %s", "WebSocket", f"/api/v1/voice/live?token={JWT}")
    finally:
        logger.removeHandler(handler)
    written = "".join(call.args[0] for call in stream.write.call_args_list)
    assert JWT not in written and "token=[redacted]" in written


# --------------------------------------------------------------------------- settings


def test_defaults_are_safe() -> None:
    settings = make_settings()
    assert settings.local_owner_bootstrap is False
    assert settings.environment == "production"
    assert settings.docs_enabled is False
    assert settings.fastapi_docs_kwargs() == {
        "docs_url": None,
        "redoc_url": None,
        "openapi_url": None,
    }
    assert settings.allowed_hosts == ["localhost", "127.0.0.1"]
    assert settings.web_static_dir is None
    assert "http://localhost:3000" in settings.voice_allowed_origins


def test_docs_follow_the_environment_unless_set_explicitly() -> None:
    assert make_settings(environment="development").docs_enabled is True
    assert make_settings(environment="development", api_docs_enabled=False).docs_enabled is False
    assert make_settings(api_docs_enabled=True).fastapi_docs_kwargs()["openapi_url"] == (
        "/openapi.json"
    )


def test_list_settings_accept_comma_separated_and_json_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAYA_ALLOWED_HOSTS", "localhost, 127.0.0.1,app.local")
    monkeypatch.setenv(
        "MAYA_VOICE_ALLOWED_ORIGINS", '["https://a.example","http://b.example:3000/"]'
    )
    values: dict[str, Any] = {
        "database_url": "postgresql+asyncpg://maya_app:example-only@localhost:5432/maya",
        "redis_url": "redis://localhost:6379/0",
        "jwt_signing_key": "j" * 40,
        "otp_hmac_key": "o" * 40,
    }
    settings = Settings(**values)
    assert settings.allowed_hosts == ["localhost", "127.0.0.1", "app.local"]
    assert settings.voice_allowed_origins == ["https://a.example", "http://b.example:3000"]


@pytest.mark.parametrize("origin", ["ftp://x.example", "not a url", "https://x.example/path"])
def test_bad_voice_origins_are_rejected(origin: str) -> None:
    with pytest.raises(ValueError):
        make_settings(voice_allowed_origins=[origin])


# ---------------------------------------------------------------------- error envelope


def _error_app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/text")
    async def text() -> None:
        raise HTTPException(status_code=400, detail="An idempotency key must be provided.")

    @app.get("/server")
    async def server() -> None:
        raise HTTPException(status_code=500, detail="internal path /srv/secret.py")

    @app.get("/dict")
    async def dictionary() -> None:
        raise HTTPException(status_code=503, detail={"error": "model_unavailable"})

    return app


def test_a_string_detail_keeps_its_message_in_the_standard_envelope() -> None:
    response = TestClient(_error_app()).get("/text")
    assert response.status_code == 400
    assert response.json() == {
        "error": "http_error",
        "message": "An idempotency key must be provided.",
    }


def test_server_errors_do_not_leak_their_detail_and_dicts_pass_through() -> None:
    client = TestClient(_error_app())
    assert client.get("/server").json() == {"error": "http_error"}
    assert client.get("/dict").json() == {"error": "model_unavailable"}
    assert client.get("/missing").json() == {"error": "not_found"}


# ------------------------------------------------------------------------ MCP transport


def test_http_is_an_alias_of_streamable_http() -> None:
    assert normalize_transport("http") == "streamable_http"
    assert normalize_transport("streamable_http") == "streamable_http"
    assert normalize_transport("stdio") == "stdio"
    with pytest.raises(HTTPException) as unknown:
        normalize_transport("carrier-pigeon")
    assert unknown.value.status_code == 422


# ------------------------------------------------------------------------ metrics label


def test_the_metrics_label_is_the_route_template_not_the_raw_path() -> None:
    app = FastAPI()
    seen: list[str] = []

    @app.middleware("http")
    async def record(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        seen.append(route_label(request.scope))
        return response

    @app.get("/api/v1/tasks/{task_id}")
    async def task(task_id: str) -> dict[str, str]:
        return {"id": task_id}

    client = TestClient(app)
    client.get(f"/api/v1/tasks/{uuid4()}")
    client.get(f"/api/v1/tasks/{uuid4()}")
    client.get("/nothing/here/123")
    assert seen == ["/api/v1/tasks/{task_id}", "/api/v1/tasks/{task_id}", "unmatched"]


# ------------------------------------------------------------------------------ static web


@pytest.fixture
def web(tmp_path: Path) -> TestClient:
    root = tmp_path / "out"
    (root / "_next").mkdir(parents=True)
    (root / "index.html").write_text("<html>home</html>")
    (root / "chat.html").write_text("<html>chat</html>")
    (root / "_next" / "app.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("outside")
    app = FastAPI()

    @app.get("/api/v1/ping")
    async def ping() -> dict[str, str]:
        return {"ok": "yes"}

    assert mount_static_web(app, make_settings(web_static_dir=root)) is True
    return TestClient(app)


def test_the_exported_app_is_served_with_a_restrictive_csp(web: TestClient) -> None:
    home = web.get("/")
    assert home.status_code == 200 and "home" in home.text
    csp = home.headers["content-security-policy"]
    assert "default-src 'self'" in csp and "object-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert home.headers["x-content-type-options"] == "nosniff"
    assert "chat" in web.get("/chat").text
    assert web.get("/_next/app.js").status_code == 200


def test_unknown_pages_fall_back_to_the_shell_but_api_and_assets_still_404(web: TestClient) -> None:
    assert "home" in web.get("/some/client/route").text
    assert web.get("/api/v1/ping").json() == {"ok": "yes"}  # real routes win
    assert web.get("/api/v1/unknown").status_code == 404
    assert "home" not in web.get("/api/v1/unknown").text
    assert web.get("/missing.js").status_code == 404


def test_traversal_cannot_leave_the_directory(web: TestClient) -> None:
    for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/..%2fsecret.txt"):
        response = web.get(path)
        assert "outside" not in response.text


def test_nothing_is_mounted_without_the_setting_and_a_bad_directory_is_an_error(
    tmp_path: Path,
) -> None:
    app = FastAPI()
    assert mount_static_web(app, make_settings()) is False
    with pytest.raises(ValueError):
        mount_static_web(app, make_settings(web_static_dir=tmp_path / "missing"))


# ------------------------------------------------------------------- desktop stdin shutdown


def test_closing_the_pipe_triggers_the_shutdown_callback() -> None:
    read_fd, write_fd = os.pipe()
    reader = os.fdopen(read_fd, "rb")
    fired = threading.Event()
    thread = threading.Thread(target=wait_for_stdin_close, args=(reader, fired.set))
    thread.start()
    with os.fdopen(write_fd, "wb") as writer:
        writer.write(b"ignored data")
        writer.flush()
        assert not fired.wait(0.2)  # data alone does not stop the server
    thread.join(timeout=5)  # the parent closing (or crashing) ends the stream
    assert fired.is_set()
    reader.close()


def test_a_stream_that_fails_also_shuts_down() -> None:
    stream = SimpleNamespace(read=MagicMock(side_effect=OSError("handle is invalid")))
    calls: list[bool] = []
    wait_for_stdin_close(stream, lambda: calls.append(True))  # type: ignore[arg-type]
    assert calls == [True]
