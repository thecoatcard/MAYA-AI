"""Task step verification with the decision model (ADR-0029).

After a step produced its result, three questions: does it satisfy the request, is anything
empty or a placeholder, and how complete is it. A clear negative marks the step as "may be
incomplete" so the task shows as finished WITH PROBLEMS (the existing partial machinery). The
review can only add that mark: it never marks a step complete, never retries or hides a failure.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from typing import Any

from app.decisions import runtime as decisions
from app.decisions.questions import is_no, noul, score, untrusted_block

logger = logging.getLogger("maya.decisions")

# Calibration (owner report: ~90% of tasks showed "Completed with errors"): a model opinion alone
# is a NOTE. It only counts as a problem (``flagged``) when it is strong AND a deterministic
# check agrees (placeholder text, an error marker, a failed step proof). Only in the "enforce"
# mode does a flagged review change the task status.
MODES = ("off", "advisory", "enforce")
PLACEHOLDER_RE = re.compile(
    r"lorem ipsum|\[insert[^\]]{0,60}\]|\[your [a-z ]{1,30}\]|\bTODO\b|\bTBD\b"
    r"|\bplaceholder text\b",
    re.IGNORECASE,
)
ERROR_MARKER_RE = re.compile(
    r"traceback \(most recent call last\)|^\s*(error|exception|failed)\b\s*[:\-]|"
    r"internal server error|an error occurred while",
    re.IGNORECASE,
)
VISIBILITY_RULE = (
    "Rule: the result may be a review, summary or answer ABOUT material you cannot see (a file, "
    "repository, page or earlier step). Do not penalise it for that. Answer no only when the "
    "result clearly does not address the request at all."
)

MAX_CHARS = 8_000
HEAD_CHARS = 5_500
MIN_RESULT_CHARS = 20
NO_THRESHOLD = 0.85
YES_THRESHOLD = 0.85
LEVELS = (
    "Empty, a stub or unrelated to the request.",
    "Partial: important parts are missing or only outlined.",
    "Mostly complete: small gaps remain.",
    "Complete: everything asked for is present.",
)
# A prob-weighted level index below this means "clearly not complete".
INCOMPLETE_BELOW = 1.0
KEY = "decision_review"
# Count policy: a page, slide or row count that differs from the request is delivered with a note
# by the document step itself. The review must never turn it into "may be incomplete".
COUNT_RULE = (
    "Rule: a page, slide, sheet or row count that differs from the number in the request is NOT "
    "a gap and NOT a reason to answer no. The file was fitted and delivered, and the difference is "
    "reported separately. Judge only the content."
)
THIN_RULE = (
    "Rule: the research behind this step found little or no source material. A short, honest "
    "result that says what could not be found is COMPLETE for what exists. Answer no to gaps "
    "unless text is a placeholder or filler."
)


def result_text(output: Mapping[str, Any] | None) -> str:
    """The step result as bounded plain text (no huge fields, no proof blobs). A long result keeps
    its start AND its end (a conclusion lives at the end), never only a cut-off head."""
    if not output:
        return ""
    for key in ("result", "summary", "text", "content"):
        value = output.get(key)
        if isinstance(value, str) and value.strip():
            return _excerpt(value)
    return _excerpt(json.dumps(output, default=str, ensure_ascii=False))


def _excerpt(text: str) -> str:
    if len(text) <= MAX_CHARS:
        return text
    tail = MAX_CHARS - HEAD_CHARS
    return text[:HEAD_CHARS] + "\n[... middle omitted ...]\n" + text[-tail:]


def deterministic_evidence(text: str, proof: Mapping[str, Any] | None = None) -> list[str]:
    """Hard, non-model signs that a result is really broken (never a guess)."""
    found: list[str] = []
    if PLACEHOLDER_RE.search(text):
        found.append("placeholder text")
    if ERROR_MARKER_RE.search(text[:600]):
        found.append("error marker")
    if isinstance(proof, Mapping) and (proof.get("error") or proof.get("verified") is False):
        found.append("failed step check")
    return found


_mode = "advisory"


def set_mode(mode: str) -> None:
    """Install the configured mode (MAYA_STEP_REVIEW_MODE) at startup; unknown means advisory."""
    global _mode
    _mode = mode if mode in MODES else "advisory"


def current_mode() -> str:
    return _mode


async def review_step(
    goal: str,
    step: str,
    output: Mapping[str, Any] | None,
    *,
    files: list[str] | None = None,
    thin_input: bool = False,
    proof: Mapping[str, Any] | None = None,
    mode: str | None = None,
) -> dict[str, Any] | None:
    """``{"flagged", "reasons", "evidence", "mode"}`` or None when there is no answer.

    ``reasons`` are the model's strong objections. ``flagged`` is True only when there is also
    deterministic ``evidence`` (placeholder, error marker, failed step check); a model opinion
    alone is advisory (shown as a note, never a problem). ``mode`` ("off" | "advisory" |
    "enforce", default from settings) is recorded: the task status only changes in "enforce".

    ``thin_input``: the research behind this step found little or nothing (it reported that no web
    sources were available). The step then wrote an honest short result, which is COMPLETE for what
    exists: only a placeholder or empty filler is flagged, never "incomplete" or "does not satisfy".
    """
    mode = mode or current_mode()
    text = result_text(output)
    if mode == "off" or len(text) < MIN_RESULT_CHARS or not decisions.available("task_verify"):
        return None
    file_line = f"\nFiles produced: {', '.join(files[:8])}" if files else "\nFiles produced: none"
    body = (
        untrusted_block("user request", goal[:3000])
        + f"\nStep: {step[:300]}"
        + file_line
        + "\n"
        + COUNT_RULE
        + "\n"
        + VISIBILITY_RULE
        + "\n"
        + (THIN_RULE + "\n" if thin_input else "")
        + untrusted_block("step result", text)
    )
    answers = await decisions.decide(
        "task_verify",
        body,
        [
            noul(
                "satisfies_request",
                "Does the step result satisfy what the step was asked to do for the request?",
            ),
            noul(
                "has_gaps",
                "Is any part of the step result empty, a placeholder (lorem ipsum, TODO, "
                "[insert ...]), repeated filler, or clearly missing?",
            ),
            score("completeness", "How complete is the step result?", LEVELS),
        ],
        background=True,
    )
    if answers is None:
        logger.info("step_review outcome=timeout mode=%s", mode)
        return None
    reasons: list[str] = []
    if not thin_input and is_no(answers.noul("satisfies_request"), NO_THRESHOLD):
        reasons.append("the result does not appear to satisfy the request")
    if answers.yes("has_gaps", YES_THRESHOLD):
        reasons.append("part of the result looks empty or a placeholder")
    if not thin_input and answers.score_below("completeness", INCOMPLETE_BELOW):
        reasons.append("the result looks incomplete")
    evidence = deterministic_evidence(text, proof)
    flagged = bool(reasons) and bool(evidence)
    outcome = "flagged" if flagged else "noted" if reasons else "clear"
    logger.info(
        "step_review outcome=%s mode=%s reasons=%d evidence=%d",
        outcome,
        mode,
        len(reasons),
        len(evidence),
    )
    return {"flagged": flagged, "reasons": reasons, "evidence": evidence, "mode": mode}
