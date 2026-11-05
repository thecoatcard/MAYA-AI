"""Google GenAI SDK client adapter and offline factory.

Note:
    Live network operations against the Gemini API are NEVER executed by the automated
    test harness or agent without explicit owner authorization and budget caps.
    This adapter is offline-verified via mock contracts and unit tests.
    Status: NOT TESTED LIVE (offline verification only).
"""

from typing import TYPE_CHECKING

from google import genai
from google.genai import types

from app.gateway.exceptions import GatewayConfigurationError

if TYPE_CHECKING:
    from app.core.config import Settings


def build_genai_client(
    api_key: str | None,
    timeout_seconds: float = 30.0,
) -> genai.Client:
    """Build a Google GenAI Client configured with explicit API key and timeout.

    Never reads credentials implicitly from environment variables (GEMINI_API_KEY,
    GOOGLE_API_KEY, or GOOGLE_GENAI_USE_VERTEXAI).

    Raises:
        GatewayConfigurationError: If api_key is None or empty.
    """
    if not api_key or not api_key.strip():
        raise GatewayConfigurationError(
            "Cannot construct Gemini client: MAYA_GEMINI_API_KEY is not configured."
        )

    # SDK timeout is specified in milliseconds
    timeout_ms = int(timeout_seconds * 1000)

    return genai.Client(
        api_key=api_key.strip(),
        http_options=types.HttpOptions(
            timeout=timeout_ms,
            retry_options=None,  # Disable SDK-level retries; ModelGateway manages resilience
        ),
    )


def create_client_from_settings(settings: "Settings") -> genai.Client | None:
    """Construct client from application Settings if key is set, else return None."""
    if settings.gemini_api_key is None:
        return None
    key_str = settings.gemini_api_key.get_secret_value()
    if not key_str.strip():
        return None
    return build_genai_client(
        api_key=key_str,
        timeout_seconds=settings.gemini_request_timeout_seconds,
    )
