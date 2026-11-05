"""Unit tests for owner-run Gemini smoke diagnostic tool."""

from unittest.mock import AsyncMock, patch

import pytest
from google.genai.errors import APIError
from pydantic import SecretStr

from app.core.config import Settings
from app.tools.gemini_smoke import main, run_smoke
from tests.support.fake_gemini_client import FakeGeminiClient


def make_smoke_settings(**kwargs: object) -> Settings:
    base: dict[str, object] = {
        "database_url": SecretStr("postgresql+asyncpg://maya_app:secret@127.0.0.1:15432/maya"),
        "redis_url": SecretStr("redis://127.0.0.1:16379/0"),
        "jwt_signing_key": SecretStr("1234567890123456789012345678901234567890"),
        "otp_hmac_key": SecretStr("abcdefghijklmnopqrstuvwxyz0123456789abcdef"),
        "gemini_api_key": SecretStr("example-fake-gemini-key-for-test"),
    }
    base.update(kwargs)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_smoke_refuses_when_api_key_unset(capsys: pytest.CaptureFixture[str]) -> None:
    settings = make_smoke_settings(gemini_api_key=None)
    with patch("app.tools.gemini_smoke.Settings", return_value=settings):

        class Args:
            alias = "orchestrator"
            prompt = "hi"
            verify_free_tier = False
            confirm_paid_calls_up_to_inr_100 = False

        status = await run_smoke(Args())  # type: ignore[arg-type]
        assert status == 1
        captured = capsys.readouterr()
        assert "MAYA_GEMINI_API_KEY is not set" in captured.err


@pytest.mark.asyncio
async def test_smoke_safety_gate_blocks_paid_call_without_confirmation(
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = make_smoke_settings()
    with patch("app.tools.gemini_smoke.Settings", return_value=settings):

        class Args:
            alias = "orchestrator"
            prompt = "hi"
            verify_free_tier = False
            confirm_paid_calls_up_to_inr_100 = False  # Confirmation flag NOT passed

        status = await run_smoke(Args())  # type: ignore[arg-type]
        assert status == 2
        captured = capsys.readouterr()
        assert "Safety Gate Blocked" in captured.err
        assert "--confirm-paid-calls-up-to-inr-100" in captured.err


@pytest.mark.asyncio
async def test_smoke_free_tier_mode_succeeds_offline(capsys: pytest.CaptureFixture[str]) -> None:
    settings = make_smoke_settings()
    fake_client = FakeGeminiClient()
    fake_client.aio.models.default_text = "OK"

    with (
        patch("app.tools.gemini_smoke.Settings", return_value=settings),
        patch("app.tools.gemini_smoke.build_genai_client", return_value=fake_client),
    ):

        class Args:
            alias = "orchestrator"
            prompt = "test"
            verify_free_tier = True  # Free-tier mode authorized
            confirm_paid_calls_up_to_inr_100 = False

        status = await run_smoke(Args())  # type: ignore[arg-type]
        assert status == 0
        captured = capsys.readouterr()
        assert "Smoke Test Succeeded" in captured.out
        assert "Response: OK" in captured.out


@pytest.mark.asyncio
async def test_smoke_429_quota_limit_handled_cleanly(capsys: pytest.CaptureFixture[str]) -> None:
    settings = make_smoke_settings()
    fake_client = FakeGeminiClient()
    fake_client.aio.models.enqueue_errors("gemini-3.8-flash", [APIError(429, "Resource exhausted")])

    with (
        patch("app.tools.gemini_smoke.Settings", return_value=settings),
        patch("app.tools.gemini_smoke.build_genai_client", return_value=fake_client),
    ):

        class Args:
            alias = "orchestrator"
            prompt = "test"
            verify_free_tier = True
            confirm_paid_calls_up_to_inr_100 = False

        status = await run_smoke(Args())  # type: ignore[arg-type]
        assert status == 0
        captured = capsys.readouterr()
        assert "Quota Limit Reached: 429 Resource Exhausted" in captured.out


def test_main_cli_argument_parsing() -> None:
    with patch("app.tools.gemini_smoke.run_smoke", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = 0
        status = main(["--alias", "heavy", "--verify-free-tier"])
        assert status == 0
        mock_run.assert_awaited_once()
        args = mock_run.call_args.args[0]
        assert args.alias == "heavy"
        assert args.verify_free_tier is True
        assert args.confirm_paid_calls_up_to_inr_100 is False
