"""Integration tests for Browser REST API Endpoints & Human Handoff Bridge (Phase 13)."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.v1.browser import _sessions, get_browser_runner, get_browser_settings
from app.auth.dependencies import get_principal
from app.auth.models import Principal
from app.main import create_app
from app.mcp.approvals import compute_payload_hash
from app.mcp.tokens import mint_approval_token
from app.sandbox.browser_runner import BrowserRunner
from app.sandbox.egress_filter import EgressSecurityFilter
from tests.support.settings import make_settings


@pytest.fixture
def workspace_id() -> UUID:
    return uuid4()


@pytest.fixture
def user_id() -> UUID:
    return uuid4()


@pytest.fixture
def principal(workspace_id: UUID, user_id: UUID) -> Principal:
    return Principal(
        user_id=user_id,
        workspace_id=workspace_id,
        role="member",
        session_id=uuid4(),
    )


@pytest.fixture
def custom_runner() -> BrowserRunner:
    runner = BrowserRunner(
        egress_filter=EgressSecurityFilter(dns_resolver=lambda h: ["93.184.216.34"]),
        use_simulation=True,
    )
    return runner


@pytest.fixture
def client(principal: Principal, custom_runner: BrowserRunner) -> TestClient:
    settings = make_settings()
    app = create_app(settings)
    app.dependency_overrides[get_principal] = lambda: principal
    app.dependency_overrides[get_browser_runner] = lambda: custom_runner
    app.dependency_overrides[get_browser_settings] = lambda: settings

    # Clean sessions
    _sessions.clear()

    return TestClient(app)


class TestBrowserApi:
    def test_navigate_allowed_url(self, client: TestClient, custom_runner: BrowserRunner) -> None:
        custom_runner.set_simulated_page(
            "https://example.com/start",
            html="<html><body><h1>Welcome Maya</h1></body></html>",
        )

        resp = client.post(
            "/api/v1/browser/action",
            json={
                "action_type": "navigate",
                "url": "https://example.com/start",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["current_url"] == "https://example.com/start"
        assert "Welcome Maya" in data["dom_summary"]
        assert data["bot_detected"] is False

    def test_navigate_blocked_by_ssrf_filter(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/browser/action",
            json={
                "action_type": "navigate",
                "url": "http://169.254.169.254/latest/meta-data/",
            },
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["error"] == "egress_security_violation"
        assert "egress security violation" in data["message"].lower()

    def test_navigate_detects_captcha_and_updates_takeover_state(
        self, client: TestClient, custom_runner: BrowserRunner
    ) -> None:
        session_id = "test-session-bot-1"
        custom_runner.set_simulated_page(
            "https://example.com/challenge",
            html="""
            <html>
            <body>
                <div class="cf-turnstile"></div>
                <p>Please verify you are human</p>
            </body>
            </html>
            """,
            session_id=session_id,
        )

        # 1. Dispatch action
        resp = client.post(
            "/api/v1/browser/action",
            json={
                "action_type": "navigate",
                "url": "https://example.com/challenge",
                "session_id": session_id,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["bot_detected"] is True
        assert data["bot_type"] == "cloudflare_turnstile"

        # 2. Query takeover state
        takeover_resp = client.get(f"/api/v1/browser/session/{session_id}/takeover")
        assert takeover_resp.status_code == 200
        state = takeover_resp.json()
        assert state["session_id"] == session_id
        assert state["status"] == "awaiting_human_takeover"
        assert state["challenge_detected"] is True
        assert state["challenge_type"] == "cloudflare_turnstile"
        assert state["screenshot_base64"] is not None

        # 3. Human signals completion via resume
        resume_resp = client.post(f"/api/v1/browser/session/{session_id}/resume")
        assert resume_resp.status_code == 200
        resumed = resume_resp.json()
        assert resumed["status"] == "resumed"

        # 4. Takeover state is cleared
        takeover_after = client.get(f"/api/v1/browser/session/{session_id}/takeover")
        assert takeover_after.status_code == 200
        assert takeover_after.json()["challenge_detected"] is False

    def test_click_requires_approval_token(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/browser/action",
            json={
                "action_type": "click",
                "selector": "#delete-btn",
            },
        )
        assert resp.status_code == 403
        data = resp.json()
        assert data["error"] == "approval_required"
        assert "requires a valid human approval token" in data["message"]

    def test_click_succeeds_with_valid_token(
        self,
        client: TestClient,
        principal: Principal,
        custom_runner: BrowserRunner,
    ) -> None:
        session_id = "test-session-click-1"
        custom_runner.set_simulated_page(
            "https://example.com/buttons",
            html="<html><body><button id='action-btn'>Confirm</button></body></html>",
            session_id=session_id,
        )

        # First navigate
        client.post(
            "/api/v1/browser/action",
            json={
                "action_type": "navigate",
                "url": "https://example.com/buttons",
                "session_id": session_id,
            },
        )

        settings = make_settings()
        secret_key = settings.jwt_signing_key.get_secret_value()

        args = {"selector": "#action-btn"}
        payload_hash = compute_payload_hash(
            tool="browser_click",
            args=args,
            workspace_id=principal.workspace_id,
            user_id=principal.user_id,
            idempotency_key=session_id,
        )

        token, _ = mint_approval_token(
            secret_key,
            user_id=principal.user_id,
            workspace_id=principal.workspace_id,
            request_id=uuid4(),
            tool_name="browser_click",
            payload_hash=payload_hash,
        )

        resp = client.post(
            "/api/v1/browser/action",
            json={
                "action_type": "click",
                "selector": "#action-btn",
                "session_id": session_id,
                "approval_token": token,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_workspace_isolation_blocks_cross_tenant_access(
        self, client: TestClient, custom_runner: BrowserRunner
    ) -> None:
        session_id = "tenant-a-session"
        # Seed session for a different workspace
        _sessions[session_id] = {
            "session_id": session_id,
            "workspace_id": uuid4(),  # Different workspace!
            "user_id": uuid4(),
            "current_url": "https://internal.corp",
            "status": "active",
            "challenge_detected": False,
            "challenge_type": None,
            "last_screenshot": None,
        }

        # Attempt to access takeover state
        resp = client.get(f"/api/v1/browser/session/{session_id}/takeover")
        assert resp.status_code == 404

        # Attempt to resume
        resp_resume = client.post(f"/api/v1/browser/session/{session_id}/resume")
        assert resp_resume.status_code == 404
