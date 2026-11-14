"""Render a built file to PNG page images for the visual review (Level 2, local only).

Methods, in order of fidelity:

* ``libreoffice``: a DOCX or PPTX is converted to PDF by LibreOffice (``soffice --headless``)
  and the PDF pages are drawn with ``pdftoppm`` (poppler) when that tool is installed. Found
  through ``MAYA_LIBREOFFICE_PATH``, the PATH, or the standard Windows install folder. Never
  required: when it is missing the next method is used.
* ``edge_html_mirror``: the document's HTML mirror (:func:`render_document_html`) is screenshot
  by Edge through the existing browser renderer. This is an APPROXIMATION: the slices are
  A4-height windows of the page and do not follow the print page breaks or the real fonts. The
  result says so (``approximation=True``).
* ``unavailable``: neither is possible (for example a PPTX without LibreOffice, which has no
  HTML mirror). Nothing is drawn; the reasons are in ``notes``.

PyMuPDF is NOT used: it is not a declared dependency (pyproject.toml). A PDF is therefore only
previewed through the HTML mirror, and only when the caller has the :class:`Document` it came
from.

Limits: at most :data:`PREVIEW_MAX_PAGES` pages are drawn, and the whole step shares one time
budget. The functions block; async callers run them in a worker thread.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from app.automation.browser_pdf import (
    BrowserPdfRenderer,
    BrowserRenderError,
    BrowserUnavailableError,
    get_browser_renderer,
)
from app.automation.html_render import render_document_html
from app.automation.model import Document

logger = logging.getLogger(__name__)

PREVIEW_MAX_PAGES = 30
PREVIEW_TIMEOUT_SECONDS = 90.0
LIBREOFFICE_ENV = "MAYA_LIBREOFFICE_PATH"
_WINDOWS_LIBREOFFICE = (
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
)
_RASTER_DPI = "80"
APPROXIMATION_NOTE = (
    "This preview is an HTML approximation: Edge drew the document's HTML mirror, so page "
    "breaks, fonts and spacing can differ from the real file."
)

PreviewMethod = Literal["libreoffice", "edge_html_mirror", "unavailable"]


@dataclass(frozen=True)
class PreviewResult:
    method: PreviewMethod
    pages: list[Path]
    total_pages: int | None
    approximation: bool
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "method": self.method,
            "pages_drawn": len(self.pages),
            "total_pages": self.total_pages,
            "approximation": self.approximation,
            "notes": list(self.notes),
        }


def libreoffice_executable() -> Path | None:
    """The LibreOffice ``soffice`` executable, or ``None``. An explicitly configured path that
    does not exist is ``None`` too: it is never silently replaced by another install."""
    configured = os.environ.get(LIBREOFFICE_ENV, "").strip()
    if configured:
        candidate = Path(configured)
        return candidate if candidate.is_file() else None
    for name in ("soffice", "libreoffice"):
        located = shutil.which(name)
        if located:
            return Path(located)
    for raw in _WINDOWS_LIBREOFFICE:
        candidate = Path(raw)
        if candidate.is_file():
            return candidate
    return None


def _libreoffice_to_pdf(executable: Path, source: Path, out_dir: Path, timeout: float) -> Path:
    profile = (out_dir / "lo-profile").resolve()
    command = [
        str(executable),
        f"-env:UserInstallation={profile.as_uri()}",
        "--headless",
        "--norestore",
        "--convert-to",
        "pdf",
        "--outdir",
        str(out_dir),
        str(source),
    ]
    completed = subprocess.run(  # noqa: S603 - fixed argument list, no shell
        command, capture_output=True, timeout=timeout, check=False
    )
    produced = out_dir / f"{source.stem}.pdf"
    if completed.returncode != 0 or not produced.is_file():
        raise RuntimeError("LibreOffice could not convert the file to PDF.")
    return produced


def _draw_pdf_pages(pdf: Path, out_dir: Path, max_pages: int, timeout: float) -> list[Path]:
    rasteriser = shutil.which("pdftoppm")
    if rasteriser is None:
        raise RuntimeError("no PDF rasteriser (pdftoppm) is installed")
    command = [
        rasteriser,
        "-png",
        "-r",
        _RASTER_DPI,
        "-f",
        "1",
        "-l",
        str(max_pages),
        str(pdf),
        str(out_dir / "page"),
    ]
    subprocess.run(command, capture_output=True, timeout=timeout, check=False)  # noqa: S603
    return sorted(out_dir.glob("page-*.png"))[:max_pages]


def _pdf_page_count(pdf: Path) -> int | None:
    try:
        from pypdf import PdfReader

        return len(PdfReader(str(pdf)).pages)
    except Exception:  # a count is informative only
        return None


def _remaining(deadline: float) -> float:
    return max(1.0, deadline - time.monotonic())


def _edge_mirror(
    document: Document,
    out_dir: Path,
    max_pages: int,
    deadline: float,
    renderer: BrowserPdfRenderer | None,
) -> tuple[list[Path], int]:
    html = render_document_html(document)
    active = renderer if renderer is not None else get_browser_renderer()
    shot = active.screenshot_pages(
        html, out_dir, max_pages=max_pages, timeout_seconds=_remaining(deadline)
    )
    return shot.paths, shot.total_pages


def render_preview(
    source: Path,
    kind: str,
    *,
    out_dir: Path,
    document: Document | None = None,
    renderer: BrowserPdfRenderer | None = None,
    max_pages: int = PREVIEW_MAX_PAGES,
    timeout_seconds: float = PREVIEW_TIMEOUT_SECONDS,
) -> PreviewResult:
    """Draw up to ``max_pages`` pages of ``source`` (a file of ``kind``) into ``out_dir``.

    ``document`` is the model the file was built from; it is needed for the HTML mirror (PDF
    always, DOCX when LibreOffice is missing). Never raises for a missing tool: the reasons are
    returned in ``notes``.
    """
    limit = max(1, min(int(max_pages), PREVIEW_MAX_PAGES))
    deadline = time.monotonic() + max(1.0, float(timeout_seconds))
    out_dir.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []

    if kind in ("docx", "pptx"):
        executable = libreoffice_executable()
        if executable is None:
            notes.append("LibreOffice is not installed, so the real file was not drawn.")
        else:
            try:
                pdf = _libreoffice_to_pdf(executable, source, out_dir, _remaining(deadline))
                pages = _draw_pdf_pages(pdf, out_dir, limit, _remaining(deadline))
                if pages:
                    return PreviewResult(
                        method="libreoffice",
                        pages=pages,
                        total_pages=_pdf_page_count(pdf),
                        approximation=False,
                        notes=notes,
                    )
                notes.append("LibreOffice produced a PDF, but no page image could be drawn.")
            except (RuntimeError, OSError, subprocess.SubprocessError) as err:
                logger.warning("preview_libreoffice_failed error=%s", type(err).__name__)
                notes.append(f"LibreOffice preview failed ({str(err)[:160]}).")

    if document is None:
        if kind == "pdf":
            notes.append(
                "PDF pages are drawn only from an HTML mirror, and no mirror was given. "
                "PyMuPDF is not a declared dependency."
            )
        else:
            notes.append("No HTML mirror exists for this file type, so it cannot be previewed.")
        return PreviewResult("unavailable", [], None, False, notes)

    try:
        pages, total = _edge_mirror(document, out_dir, limit, deadline, renderer)
    except (BrowserUnavailableError, BrowserRenderError, OSError) as err:
        logger.warning("preview_edge_failed error=%s", type(err).__name__)
        notes.append(f"Edge page preview failed ({str(err)[:160]}).")
        return PreviewResult("unavailable", [], None, False, notes)
    notes.append(APPROXIMATION_NOTE)
    return PreviewResult("edge_html_mirror", pages, total, True, notes)


__all__ = [
    "APPROXIMATION_NOTE",
    "LIBREOFFICE_ENV",
    "PREVIEW_MAX_PAGES",
    "PREVIEW_TIMEOUT_SECONDS",
    "PreviewResult",
    "libreoffice_executable",
    "render_preview",
]
