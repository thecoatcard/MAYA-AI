"""Turn tracking: turns Live events into TRANSCRIPT / TURN_STATE frames and finished turns.

Pure state machine (no I/O). A turn is one user utterance (spoken or typed) or one assistant
reply. The connection persists every ``FinishedTurn`` as soon as it is returned, so a crash loses
at most the turn in progress.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from app.voice import protocol


@dataclass
class FinishedTurn:
    turn_id: str
    role: str  # "user" | "assistant"
    text: str
    source: str  # "spoken" | "typed"
    interrupted: bool = False
    incomplete: bool = False


@dataclass
class TurnOutput:
    frames: list[dict[str, Any]] = field(default_factory=list)
    finished: list[FinishedTurn] = field(default_factory=list)


@dataclass
class _Open:
    turn_id: str
    role: str
    source: str
    text: str = ""


class TurnTracker:
    def __init__(self) -> None:
        self._user: _Open | None = None
        self._assistant: _Open | None = None
        self.state = "listening"

    @property
    def current_turn_id(self) -> str | None:
        open_turn = self._assistant or self._user
        return open_turn.turn_id if open_turn else None

    def _set_state(self, out: TurnOutput, state: str, turn_id: str | None) -> None:
        if state != self.state:
            self.state = state
            out.frames.append(protocol.turn_state(turn_id, state))

    def _finish(
        self,
        out: TurnOutput,
        turn: _Open | None,
        *,
        interrupted: bool = False,
        incomplete: bool = False,
    ) -> None:
        if turn is None:
            return
        if turn.text.strip():
            out.frames.append(
                protocol.transcript(
                    turn.turn_id, turn.role, "", True, source=turn.source, text=turn.text
                )
            )
            out.finished.append(
                FinishedTurn(
                    turn.turn_id, turn.role, turn.text, turn.source, interrupted, incomplete
                )
            )

    def _finish_user(self, out: TurnOutput) -> None:
        self._finish(out, self._user)
        self._user = None

    def user_delta(self, text: str) -> TurnOutput:
        out = TurnOutput()
        if not text:
            return out
        if self._assistant is not None:  # user spoke over a reply the provider never closed
            self._finish(out, self._assistant, incomplete=True)
            self._assistant = None
        if self._user is None:
            self._user = _Open(uuid4().hex, "user", "spoken")
        self._user.text += text
        if self.state != "thinking":
            self._set_state(out, "listening", self._user.turn_id)
        out.frames.append(protocol.transcript(self._user.turn_id, "user", text, False))
        return out

    def user_text(self, text: str) -> TurnOutput:
        """A typed message: complete immediately, then MAYA is thinking."""
        out = TurnOutput()
        self._finish_user(out)
        if self._assistant is not None:
            self._finish(out, self._assistant, incomplete=True)
            self._assistant = None
        turn = _Open(uuid4().hex, "user", "typed", text)
        self._finish(out, turn)
        self._set_state(out, "thinking", turn.turn_id)
        return out

    def user_input_ended(self) -> TurnOutput:
        """Client VAD says the user stopped talking."""
        out = TurnOutput()
        if self._user is not None:
            self._set_state(out, "thinking", self._user.turn_id)
        return out

    def model_output(self, text: str = "") -> TurnOutput:
        """Model audio or transcript arrived: opens the reply (closing the user turn)."""
        out = TurnOutput()
        if self._assistant is None:
            self._finish_user(out)
            self._assistant = _Open(uuid4().hex, "assistant", "spoken")
        self._set_state(out, "speaking", self._assistant.turn_id)
        if text:
            self._assistant.text += text
            out.frames.append(
                protocol.transcript(self._assistant.turn_id, "assistant", text, False)
            )
        return out

    def turn_complete(self) -> TurnOutput:
        out = TurnOutput()
        self._finish_user(out)
        turn_id = self._assistant.turn_id if self._assistant else None
        self._finish(out, self._assistant)
        self._assistant = None
        self._set_state(out, "listening", turn_id)
        return out

    def interrupted(self) -> TurnOutput:
        out = TurnOutput()
        if self._assistant is None:
            return out  # nothing was being said
        turn_id = self._assistant.turn_id
        self._finish(out, self._assistant, interrupted=True)
        self._assistant = None
        self.state = "interrupted"
        out.frames.append(protocol.turn_state(turn_id, "interrupted"))
        self._set_state(out, "listening", None)
        return out

    def flush(self) -> TurnOutput:
        """Connection closing: save what is open, marked incomplete."""
        out = TurnOutput()
        self._finish(out, self._user, incomplete=True)
        self._finish(out, self._assistant, incomplete=True)
        self._user = self._assistant = None
        return out
