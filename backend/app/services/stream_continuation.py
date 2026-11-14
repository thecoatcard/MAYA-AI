"""Seamless continuation of a chat reply whose model stream stopped early.

Two things end a model turn before the answer is complete and neither is the user's fault:

* the stream dies after some text (503 overload, connection reset, stall):
  ``StreamInterruptedError`` and, after the first chunk, the gateway cannot fail over by itself;
* the provider stops at its output cap (``finish_reason=MAX_TOKENS``).

The chat service handles both the same way: the text so far becomes the previous model turn, a
short continuation instruction follows, and a new stream (the gateway picks another key or model)
carries on. ``OverlapTrimmer`` removes a repeated prefix from the continuation so the stitched text
reads as one message. Nothing here touches the network or stores content; the caller owns state.
"""

from __future__ import annotations

from typing import Any, Final

# Recoveries per reply after a stream dies (503, reset, stall). Then the honest "interrupted" state.
MAX_INTERRUPTION_RECOVERIES: Final = 3
# Continuations per reply after finish_reason=MAX_TOKENS. A cap, not a limit on the answer's
# length: 8 x 65,536 tokens is far beyond any chat reply, and it stops a model that never ends.
MAX_TOKEN_CONTINUATIONS: Final = 8
# Seconds without a chunk (after text started) before a stream counts as stalled.
STREAM_STALL_SECONDS: Final = 45.0

CONTINUE_PROMPT: Final = (
    "Continue exactly from where you stopped. Do not repeat anything already written and do not "
    "add a preface or an apology; the text above is your previous turn. Pick up mid-sentence if "
    "that is where it ended."
)

# How much of the previous text is compared with the start of the continuation, and how much of
# the continuation is held back to find the overlap.
_TAIL_CHARS: Final = 240
_HOLD_CHARS: Final = 160
_MIN_OVERLAP: Final = 4


def continuation_contents(contents: list[Any], partial_text: str) -> list[Any]:
    """``contents`` plus the partial answer as a model turn and the continue instruction."""
    return [
        *contents,
        {"role": "model", "parts": [{"text": partial_text}]},
        {"role": "user", "parts": [{"text": CONTINUE_PROMPT}]},
    ]


def trim_overlap(tail: str, head: str) -> str:
    """``head`` without a prefix that repeats the end of ``tail``.

    The longest suffix of ``tail`` that is also a prefix of ``head`` is dropped (at least
    ``_MIN_OVERLAP`` characters, or all of ``head`` when it is wholly inside the tail's end). A
    repeated first word or two is removed the same way, so a model that restates the last words
    does not show them twice.
    """
    if not tail or not head:
        return head
    window = tail[-_TAIL_CHARS:]
    best = 0
    for size in range(min(len(window), len(head)), 0, -1):
        if window.endswith(head[:size]) and (size >= _MIN_OVERLAP or size == len(head)):
            best = size
            break
    if best:
        return head[best:]
    # A repeated first word (the model restates the last word before going on).
    last_words = window.split()
    first_words = head.lstrip().split(" ", 1)
    if last_words and first_words and first_words[0] and last_words[-1] == first_words[0]:
        rest = first_words[1] if len(first_words) > 1 else ""
        return (" " if head[:1].isspace() else "") + rest if rest else ""
    return head


class OverlapTrimmer:
    """Holds the first characters of a continuation, drops what repeats ``tail``, then passes
    everything through. ``feed`` returns the text safe to show now; ``flush`` the held rest."""

    def __init__(self, tail: str) -> None:
        self._tail = tail
        self._held = ""
        self._done = False

    @property
    def done(self) -> bool:
        return self._done

    def feed(self, text: str) -> str:
        if self._done or not text:
            return text
        self._held += text
        if len(self._held) < _HOLD_CHARS:
            return ""
        return self.flush()

    def flush(self) -> str:
        if self._done:
            return ""
        self._done = True
        held, self._held = self._held, ""
        return trim_overlap(self._tail, held)
