"""Document quality review with the decision model (ADR-0029).

A text rubric (score thin..excellent, plus placeholder / repetitive / off-topic / style-match
yes-no questions) for a chapter, a deck or a finished file. For a PDF that already exists on disk
(within the size limit) the PDF itself is passed as a ``document`` block for one extra visual
score; otherwise the review is text only.

The deterministic checks (page count, verifier, acceptance) stay the authority. A review can only
ADD a finding that feeds the existing repair round or a visible note; it never marks a file good
and never replaces a failed check. Any failure returns None and nothing changes.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.decisions import runtime as decisions
from app.decisions.questions import Answers, is_no, noul, score, untrusted_block

MAX_TEXT_CHARS = 14_000
# The service accepts a PDF up to 50 MB overall; stay well under the inline-data limit.
MAX_PDF_BYTES = 15 * 1024 * 1024
FLAG_THRESHOLD = 0.85
THIN_BELOW = 1.0  # prob-weighted index on thin(0)..excellent(4)
VISUAL_POOR_BELOW = 1.0
QUALITY_LEVELS = (
    "Thin: mostly empty, a stub or filler.",
    "Weak: generic, shallow or repetitive.",
    "Adequate: correct and usable but plain.",
    "Good: specific, well organised and useful.",
    "Excellent: rich, specific and polished.",
)
VISUAL_LEVELS = (
    "Poor: broken layout, overflow, overlap or unreadable.",
    "Fair: visible layout problems.",
    "Good: clean and readable.",
    "Excellent: polished and well designed.",
)


@dataclass(frozen=True)
class DocReview:
    reasons: tuple[str, ...] = ()
    quality: float | None = None
    visual: float | None = None
    checked: tuple[str, ...] = field(default_factory=tuple)

    @property
    def flagged(self) -> bool:
        return bool(self.reasons)

    def as_dict(self) -> dict[str, Any]:
        return {
            "flagged": self.flagged,
            "reasons": list(self.reasons),
            "quality": self.quality,
            "visual": self.visual,
            "checked": list(self.checked),
            "advisory": True,
        }

    def note(self) -> str:
        """Plain visible text: only what was actually checked and found."""
        return (
            "An automated model review of the "
            + " and ".join(self.checked)
            + " flagged: "
            + "; ".join(self.reasons)
            + ". This is advisory; the length and file checks above are unchanged."
        )


def flatten(ctx: Mapping[str, Any]) -> str:
    """Plain text of a document spec (blocks, sections, slides, sheets or content)."""
    parts: list[str] = []

    def add(value: Any) -> None:
        if isinstance(value, str):
            if value.strip():
                parts.append(value.strip())
        elif isinstance(value, Mapping):
            for v in value.values():
                add(v)
        elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
            for v in value:
                add(v)

    for key in ("blocks", "sections", "slides", "sheets", "content"):
        if ctx.get(key):
            add(ctx[key])
    text = "\n".join(parts)
    return text if len(text) <= MAX_TEXT_CHARS else text[:MAX_TEXT_CHARS] + "\n[truncated]"


def _questions(kind: str, with_visual: bool) -> list[Any]:
    qs: list[Any] = [
        score(
            "quality", f"How good is the {kind} as a deliverable for the request?", QUALITY_LEVELS
        ),
        noul(
            "placeholder",
            f"Is any part of the {kind} empty or a placeholder (lorem ipsum, TODO, [insert ...], "
            "'content goes here')?",
        ),
        noul("repetitive", f"Is the {kind} repetitive, with the same points or filler repeated?"),
        noul("off_topic", f"Is a significant part of the {kind} off-topic for the request?"),
        noul(
            "style_match",
            f"Does the {kind} match the style, tone, audience and language the request asked for?",
        ),
    ]
    if with_visual:
        qs.append(score("visual", "How good is the visual layout of the PDF pages?", VISUAL_LEVELS))
    return qs


def _interpret(answers: Answers, kind: str, with_visual: bool) -> DocReview:
    reasons: list[str] = []
    checked = [f"{kind} text"]
    if answers.yes("placeholder", FLAG_THRESHOLD):
        reasons.append("part of it looks empty or a placeholder")
    if answers.yes("repetitive", FLAG_THRESHOLD):
        reasons.append("it looks repetitive")
    if answers.yes("off_topic", FLAG_THRESHOLD):
        reasons.append("part of it looks off-topic")
    if is_no(answers.noul("style_match"), FLAG_THRESHOLD):
        reasons.append("its style may differ from what was asked")
    quality = answers.score("quality")
    if quality is not None and quality.score < THIN_BELOW:
        reasons.append("it looks thin")
    visual_answer = answers.score("visual") if with_visual else None
    if visual_answer is not None:
        checked.append("rendered PDF pages")
        if visual_answer.score < VISUAL_POOR_BELOW:
            reasons.append("the page layout looks poor")
    return DocReview(
        tuple(reasons),
        quality.score if quality else None,
        visual_answer.score if visual_answer else None,
        tuple(checked),
    )


async def _read_pdf(path: Path) -> bytes | None:
    def read() -> bytes | None:
        try:
            if path.suffix.lower() != ".pdf" or path.stat().st_size > MAX_PDF_BYTES:
                return None
            return path.read_bytes()
        except OSError:
            return None

    return await asyncio.to_thread(read)


async def review_document(
    request: str,
    text: str,
    kind: str,
    *,
    pdf_path: Path | None = None,
    background: bool = True,
) -> DocReview | None:
    """A review of ``text`` (and the PDF when one exists); None when there is no answer."""
    if not decisions.available("doc_quality"):
        return None
    pdf = await _read_pdf(pdf_path) if pdf_path is not None else None
    if len(text.strip()) < 40 and pdf is None:
        return None
    head = untrusted_block("user request", request[:3000])
    body: str | list[dict[str, Any]]
    if pdf is not None:
        body = [
            {"type": "text", "text": head},
            {
                "type": "document",
                "mime_type": "application/pdf",
                "data": base64.b64encode(pdf).decode("ascii"),
            },
        ]
    else:
        body = head + "\n" + untrusted_block(kind, text, limit=MAX_TEXT_CHARS)
    answers = await decisions.decide(
        "doc_quality", body, _questions(kind, pdf is not None), background=background
    )
    if answers is None:
        return None
    return _interpret(answers, kind, pdf is not None)
