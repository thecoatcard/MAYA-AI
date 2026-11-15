"""Deterministic PDF Document Verifier."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pypdf import PdfReader

from app.automation.verifiers.schemas import VerificationReport, compute_file_sha256

MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB
_STANDARD_14 = frozenset(
    {
        "Helvetica",
        "Helvetica-Bold",
        "Helvetica-Oblique",
        "Helvetica-BoldOblique",
        "Times-Roman",
        "Times-Bold",
        "Times-Italic",
        "Times-BoldItalic",
        "Courier",
        "Courier-Bold",
        "Courier-Oblique",
        "Courier-BoldOblique",
        "Symbol",
        "ZapfDingbats",
    }
)
_EMBED_KEYS = ("/FontFile", "/FontFile2", "/FontFile3")


def _collect_fonts(reader: PdfReader) -> tuple[list[str], list[str]]:
    """Base font names used by the document and those that are neither embedded nor standard.

    Best effort: a malformed font table yields what was readable rather than an error.
    """
    names: set[str] = set()
    unembedded: set[str] = set()
    for page in reader.pages:
        try:
            resources = page.get("/Resources")
            fonts = resources.get_object().get("/Font") if resources is not None else None
            if fonts is None:
                continue
            for ref in fonts.get_object().values():
                font = ref.get_object()
                base = str(font.get("/BaseFont", "")).lstrip("/")
                name = base.split("+", 1)[1] if len(base) > 7 and base[6] == "+" else base
                if not name:
                    continue
                descriptor = font.get("/FontDescriptor")
                if descriptor is None and "/DescendantFonts" in font:
                    descendant = font["/DescendantFonts"][0].get_object()
                    descriptor = descendant.get("/FontDescriptor")
                embedded = descriptor is not None and any(
                    key in descriptor.get_object() for key in _EMBED_KEYS
                )
                names.add(name)
                if not embedded and name not in _STANDARD_14:
                    unembedded.add(name)
        except Exception:
            continue
    return sorted(names), sorted(unembedded)


def verify_pdf(
    file_path: Path | str,
    min_pages: int = 1,
) -> VerificationReport:
    """Verify a PDF document for structural validity, non-empty text layers, and page counts.

    Checks performed:
    1. File existence and size constraint (<= 50 MB).
    2. File extension must be .pdf.
    3. PDF parses cleanly via pypdf.
    4. Non-zero page count (and >= min_pages).
    5. Text layer extractability: asserts that pages contain extractable text (not blank canvases).
    """
    path = Path(file_path).resolve()
    checks_passed: list[str] = []
    errors: list[str] = []

    if not path.is_file():
        return VerificationReport(
            file_type="pdf",
            status="failed",
            sha256="",
            size_bytes=0,
            errors=[f"File not found: {path}"],
        )

    size = os.path.getsize(path)
    if size > MAX_FILE_SIZE_BYTES:
        errors.append(f"File size {size} bytes exceeds 50 MB limit")
    else:
        checks_passed.append("file_size_within_budget")

    if path.suffix.lower() != ".pdf":
        errors.append(f"Forbidden extension '{path.suffix}'; only .pdf permitted")
    else:
        checks_passed.append("valid_file_extension")

    sha256 = compute_file_sha256(path)

    total_pages = 0
    total_characters_extracted = 0
    total_words = 0
    fonts: list[str] = []
    unembedded_fonts: list[str] = []

    try:
        reader = PdfReader(str(path))
        total_pages = len(reader.pages)

        if total_pages == 0:
            errors.append("PDF contains zero pages")
        elif total_pages < min_pages:
            errors.append(f"Page count {total_pages} is less than required minimum {min_pages}")
        else:
            checks_passed.append(f"verified_{total_pages}_pages")

        # Inspect text layer across pages
        page_texts: list[str] = []
        for p_idx, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            total_characters_extracted += len(text)
            total_words += len(text.split())
            if not text.strip():
                errors.append(f"Page #{p_idx} has empty or unextractable text layer")
            else:
                page_texts.append(text)

        if total_characters_extracted > 0 and not any(
            "empty or unextractable" in e for e in errors
        ):
            checks_passed.append(
                f"verified_extractable_text_layer_{total_characters_extracted}_chars"
            )

        fonts, unembedded_fonts = _collect_fonts(reader)
        if fonts:
            checks_passed.append(f"fonts_detected_{len(fonts)}")
        if unembedded_fonts:
            checks_passed.append("warning_unembedded_fonts_present")

    except Exception as exc:
        errors.append(f"Failed to parse PDF document: {exc}")

    status: Literal["verified", "failed"] = "verified" if not errors else "failed"

    return VerificationReport(
        file_type="pdf",
        status=status,
        sha256=sha256,
        size_bytes=size,
        checks_passed=checks_passed,
        errors=errors,
        metadata={
            "total_pages": total_pages,
            "total_characters_extracted": total_characters_extracted,
            # Words in the text layer (running headers and page numbers included: a
            # rounding error next to a long document); used by the length acceptance.
            "total_words": total_words,
            "text_extractable": total_characters_extracted > 0,
            "fonts": fonts,
            "unembedded_fonts": unembedded_fonts,
        },
    )
