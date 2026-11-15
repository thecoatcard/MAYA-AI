"""Model Gateway startup verification and remote model discovery."""

import logging
from typing import Any

from app.core.config import Settings
from app.gateway.exceptions import GatewayStartupError
from app.gateway.registry import ModelRegistry, normalize_model_id

logger = logging.getLogger("maya")


async def verify_gateway_at_startup(
    registry: ModelRegistry,
    settings: Settings,
    client: Any = None,
) -> None:
    """Verify Model Gateway integrity at application startup.

    1. Offline Validation (always runs): validates registry fallback chains and integrity.
    2. Remote Model Discovery (optional):
       - If settings.gemini_verify_models_on_startup is False: logs info and skips.
       - If settings.gemini_verify_models_on_startup is True:
         - If settings.gemini_api_key is None or blank: logs warning and skips gracefully.
         - If client is None: logs warning and skips gracefully.
         - Otherwise: lists available models from provider; every enabled alias with
           verify_at_startup=True that is missing is logged and marked unavailable in the
           registry (fallback chains skip it); startup continues.

    Raises:
        GatewayStartupError: If the discovery query itself fails.
    """
    # Step 1: Always validate static configuration integrity
    registry.validate_configuration()

    # Step 2: Config-gated remote verification
    if not settings.gemini_verify_models_on_startup:
        logger.info("gateway_startup_remote_check_disabled")
        return

    has_api_key = False
    if getattr(settings, "effective_gemini_api_keys", None):
        has_api_key = True
    elif settings.gemini_api_key is not None and settings.gemini_api_key.get_secret_value().strip():
        has_api_key = True

    if not has_api_key:
        logger.warning("gateway_startup_remote_check_skipped_no_api_key")
        return

    if client is None:
        logger.warning("gateway_startup_remote_check_skipped_no_client")
        return

    try:
        model_pager = await client.aio.models.list()
        available_model_ids: set[str] = set()

        # Handle either async iterator or list/page response
        if hasattr(model_pager, "__aiter__"):
            async for m in model_pager:
                name = getattr(m, "name", "")
                available_model_ids.add(normalize_model_id(name))
        else:
            for m in model_pager:
                name = getattr(m, "name", "")
                available_model_ids.add(normalize_model_id(name))

        # A missing model is logged, never fatal: the gateway marks it unavailable so fallback
        # chains skip it, and the chat still starts on the models that exist.
        missing = 0
        for alias, spec in registry.specs.items():
            if spec.enabled and spec.verify_at_startup and spec.model_id not in available_model_ids:
                missing += 1
                registry.mark_unavailable(alias)
                logger.error(
                    "gateway_startup_model_unavailable alias=%s model=%s "
                    "(skipped in fallback chains; check the model id in .env)",
                    alias,
                    spec.model_id,
                )
        if missing:
            return

        logger.info("gateway_startup_remote_models_verified")
    except GatewayStartupError:
        raise
    except Exception as exc:
        # Only the exception class is reported: provider error text is never logged.
        logger.error("gateway_startup_remote_check_failed error=%s", type(exc).__name__)
        raise GatewayStartupError(f"Startup model discovery failed ({type(exc).__name__})") from exc
