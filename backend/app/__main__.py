import argparse
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import uvicorn
from pydantic import ValidationError

from app.agents.checkpointer import ensure_compatible_event_loop
from app.core.config import Settings
from app.core.logging import install_secret_query_filter
from app.main import create_app

ENV_FILE_VARIABLE = "MAYA_ENV_FILE"


def resolve_env_file(argument: Path | None, environ: Mapping[str, str]) -> Path | None:
    """The one configuration file to load, or None for process environment variables only.

    ``--env-file`` wins, then ``MAYA_ENV_FILE``. Exactly that file is read: a ``.env.example``
    never pulls in a sibling private ``.env``, and no file is discovered implicitly, so the
    private root ``.env`` is used only when someone names it.
    """
    if argument is not None:
        return argument
    chosen = environ.get(ENV_FILE_VARIABLE, "").strip()
    return Path(chosen) if chosen else None


def uvicorn_log_config() -> dict[str, Any]:
    """Uvicorn's logging with token/password query values redacted on every handler."""
    config: dict[str, Any] = {
        **uvicorn.config.LOGGING_CONFIG,
    }
    config["filters"] = {"redact_secrets": {"()": "app.core.logging.SecretQueryFilter"}}
    config["handlers"] = {
        name: {**handler, "filters": ["redact_secrets"]}
        for name, handler in uvicorn.config.LOGGING_CONFIG["handlers"].items()
    }
    return config


def load_settings(parser: argparse.ArgumentParser, env_file: Path | None) -> Settings:
    if env_file is not None and not env_file.is_file():
        parser.error("The requested environment file does not exist")
    try:
        return Settings(_env_file=env_file)
    except ValidationError:
        parser.exit(2, "Invalid MAYA configuration; check required variables and URL formats.\n")


def serve(settings: Settings, port: int) -> None:
    """Run the API on loopback only.

    Forwarded-for headers are not trusted: the per-address rate limits key on the direct peer,
    and a client-chosen header would let anyone rotate their identity.
    """
    ensure_compatible_event_loop()
    install_secret_query_filter()
    uvicorn.run(
        create_app(settings),
        host="127.0.0.1",
        port=port,
        access_log=False,
        proxy_headers=False,
        log_config=uvicorn_log_config(),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local MAYA API")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--port", type=int, default=8000, choices=range(1, 65536), metavar="PORT")
    arguments = parser.parse_args()
    settings = load_settings(parser, resolve_env_file(arguments.env_file, os.environ))
    serve(settings, arguments.port)


if __name__ == "__main__":
    main()
