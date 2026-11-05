from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.support.settings import make_settings


def test_liveness_readiness_and_correlation() -> None:
    with patch("app.main.Resources") as factory:
        resources = factory.return_value
        resources.check = AsyncMock(return_value=None)
        resources.close = AsyncMock()
        resources.verify_runtime_role = AsyncMock(return_value=None)
        app = create_app(make_settings())
        with TestClient(app) as client:
            response = client.get("/api/v1/healthz", headers={"X-Request-ID": "untrusted"})
            assert response.status_code == 200
            assert response.json() == {"status": "alive"}
            UUID(response.headers["X-Request-ID"])
            assert response.headers["X-Request-ID"] != "untrusted"
            assert client.get("/api/v1/readyz").json() == {"status": "ready"}
        resources.close.assert_awaited_once()


def test_readiness_failure_is_generic() -> None:
    from app.core.resources import DependencyUnavailable

    with patch("app.main.Resources") as factory:
        resources = factory.return_value
        resources.check = AsyncMock(side_effect=[None, DependencyUnavailable()])
        resources.close = AsyncMock()
        resources.verify_runtime_role = AsyncMock(return_value=None)
        app = create_app(make_settings())
        with TestClient(app) as client:
            response = client.get("/api/v1/readyz")
            assert response.status_code == 503
            assert response.json() == {"status": "unavailable"}
            assert client.get("/api/v1/healthz").status_code == 200
        resources.close.assert_awaited_once()


def test_startup_failure_closes_resources() -> None:
    from app.core.resources import DependencyUnavailable

    with patch("app.main.Resources") as factory:
        resources = factory.return_value
        resources.check = AsyncMock(side_effect=DependencyUnavailable())
        resources.close = AsyncMock()
        resources.verify_runtime_role = AsyncMock(return_value=None)
        app = create_app(make_settings())
        with pytest.raises(DependencyUnavailable), TestClient(app):
            pytest.fail("Startup must not succeed")
        resources.close.assert_awaited_once()
