"""Serve the exported web app (``next export`` output) from the API process.

Used by the desktop build: one local origin serves both the UI and ``/api/v1``. Enabled only
when ``MAYA_WEB_STATIC_DIR`` is set. ``main.py`` calls ``mount_static_web(app, settings)`` after
every router is included, because the mount answers every path no earlier route claimed.
"""

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.types import Scope

from app.core.config import Settings

logger = logging.getLogger("maya")

# Paths owned by the API; they must 404 as API errors, never fall back to the HTML shell.
RESERVED_PREFIXES = ("api/", "metrics", "docs", "redoc", "openapi.json")

CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'self'",
        # The exported Next.js pages carry inline bootstrap scripts and styles.
        "script-src 'self' 'unsafe-inline'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "font-src 'self' data:",
        "media-src 'self' blob:",
        "worker-src 'self' blob:",
        "connect-src 'self' ws://127.0.0.1:* ws://localhost:*",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    )
)
SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


class SpaStaticFiles(StaticFiles):
    """Static files with ``/chat`` -> ``chat.html`` and an HTML fallback for client routes."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await self._lookup(path, scope)
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value
        return response

    async def _lookup(self, path: str, scope: Scope) -> Response:
        if path.replace("\\", "/").lstrip("/").startswith(RESERVED_PREFIXES):
            raise HTTPException(status_code=404)
        try:
            return await super().get_response(path, scope)
        except HTTPException as error:
            if error.status_code != 404:
                raise
        if "." not in Path(path).name:  # a page route, not a missing asset
            for candidate in (f"{path.strip('/')}.html", "index.html"):
                try:
                    return await super().get_response(candidate, scope)
                except HTTPException as error:
                    if error.status_code != 404:
                        raise
        raise HTTPException(status_code=404)


def mount_static_web(app: FastAPI, settings: Settings) -> bool:
    """Mount the exported web app when configured. Returns whether it was mounted."""
    directory = settings.web_static_dir
    if directory is None:
        return False
    if not directory.is_dir():
        raise ValueError("MAYA_WEB_STATIC_DIR is not a directory")
    app.mount("/", SpaStaticFiles(directory=directory, html=True), name="web")
    logger.info("web_static_mounted")
    return True
