"""Voice announcer check with the decision model (ADR-0029).

For a finished plan step (never a final result, a failure, an approval or a question), is the
update worth interrupting the user for? Only a clear "no" suppresses the spoken step update; the
task's final result is always still announced. No answer (error, timeout, off) means speak, as
before. Latency is kept tiny with a short timeout.
"""

from __future__ import annotations

from app.decisions import runtime as decisions
from app.decisions.questions import is_no, noul

TIMEOUT_SECONDS = 1.5
NO_THRESHOLD = 0.9


async def worth_interrupting(
    task_title: str, step_title: str, step_number: int, total_steps: int, excerpt: str
) -> bool:
    """False only when the model clearly says the step update is not worth interrupting for."""
    if not decisions.available("voice_digest"):
        return True
    text = (
        f"Task: {task_title[:120]}\nFinished step {step_number} of {total_steps}: "
        f"{step_title[:120]}\nResult excerpt: {excerpt[:300]}"
    )
    answers = await decisions.decide(
        "voice_digest",
        text,
        [
            noul(
                "worth_interrupting",
                "Would a busy user want to be interrupted in a voice conversation to hear that "
                "this intermediate step finished (it has a meaningful result, not routine "
                "bookkeeping)?",
            )
        ],
        timeout=TIMEOUT_SECONDS,
    )
    if answers is None:
        return True
    return not is_no(answers.noul("worth_interrupting"), NO_THRESHOLD)
