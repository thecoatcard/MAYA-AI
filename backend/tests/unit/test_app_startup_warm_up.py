"""Application startup: the PDF browser warm-up runs in the background and never stops startup."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app import main
from app.automation import browser_pdf
from app.automation.browser_pdf import BrowserPdfRenderer
from app.main import create_app
from tests.support.settings import make_settings


def _resources(factory: Any) -> None:
    resources = factory.return_value
    resources.check = AsyncMock(return_value=None)
    resources.close = AsyncMock()
    resources.verify_runtime_role = AsyncMock(return_value=None)


def test_startup_calls_the_warm_up_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(main, "start_browser_warm_up", lambda: calls.append("warm"))
    with patch("app.main.Resources") as factory:
        _resources(factory)
        with TestClient(create_app(make_settings(browser_pdf_warm_up=True))) as client:
            assert client.get("/api/v1/healthz").status_code == 200
    assert calls == ["warm"]


def test_startup_skips_the_warm_up_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(main, "start_browser_warm_up", lambda: calls.append("warm"))
    with patch("app.main.Resources") as factory:
        _resources(factory)
        with TestClient(create_app(make_settings(browser_pdf_warm_up=False))) as client:
            assert client.get("/api/v1/readyz").status_code == 200
    assert calls == []


def test_a_failing_warm_up_does_not_stop_the_app(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(self: BrowserPdfRenderer) -> None:
        raise RuntimeError("browser exploded at start")

    monkeypatch.setattr(browser_pdf, "browser_available", lambda: True)
    monkeypatch.setattr(BrowserPdfRenderer, "warm_up", boom)
    with patch("app.main.Resources") as factory:
        _resources(factory)
        with TestClient(create_app(make_settings(browser_pdf_warm_up=True))) as client:
            assert client.get("/api/v1/healthz").json() == {"status": "alive"}
            assert client.get("/api/v1/readyz").json() == {"status": "ready"}
