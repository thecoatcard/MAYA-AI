"""Desktop entry point: ``python -m app.desktop --port N``.

Runs the API for the packaged desktop app: the local-owner bootstrap is switched on (ADR-0014),
the exported web app is served when ``MAYA_WEB_STATIC_DIR`` is set, and the server binds to
127.0.0.1 only. The parent process (Electron) owns this process through its stdin: when the
pipe closes, whether by a deliberate stop or a parent crash, the server shuts down gracefully.
"""

import argparse
import logging
import os
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

import uvicorn

from app.__main__ import load_settings, resolve_env_file, uvicorn_log_config
from app.agents.checkpointer import ensure_compatible_event_loop
from app.core.config import Settings
from app.core.logging import install_secret_query_filter
from app.core.static_web import mount_static_web
from app.main import create_app

logger = logging.getLogger("maya")
READ_CHUNK = 4096


def wait_for_stdin_close(stream: BinaryIO, on_close: Callable[[], None]) -> None:
    """Block until the stream reaches end-of-file (or fails), then call ``on_close`` once.

    Anything the parent writes is discarded; only the closing matters.
    """
    try:
        while stream.read(READ_CHUNK):
            pass
    except (OSError, ValueError):  # closed underneath us: the parent is gone as well
        pass
    on_close()


def start_stdin_watcher(stream: BinaryIO, server: uvicorn.Server) -> threading.Thread:
    def request_exit() -> None:
        server.should_exit = True

    thread = threading.Thread(
        target=wait_for_stdin_close, args=(stream, request_exit), name="stdin-watch", daemon=True
    )
    thread.start()
    return thread


def desktop_settings(parser: argparse.ArgumentParser, env_file: Path | None) -> Settings:
    base = load_settings(parser, env_file)
    return base.model_copy(update={"local_owner_bootstrap": True})


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the MAYA API for the desktop app")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--port", type=int, required=True, choices=range(1, 65536), metavar="PORT")
    arguments = parser.parse_args()
    settings = desktop_settings(parser, resolve_env_file(arguments.env_file, os.environ))

    ensure_compatible_event_loop()
    install_secret_query_filter()
    app = create_app(settings)
    if not any(getattr(route, "name", None) == "web" for route in app.routes):
        mount_static_web(app, settings)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=arguments.port,
            access_log=False,
            proxy_headers=False,
            log_config=uvicorn_log_config(),
        )
    )
    start_stdin_watcher(sys.stdin.buffer, server)
    server.run()


if __name__ == "__main__":
    main()
