"""Whether the agent's TURN has ended — a claim about the screen, never the work.

The expensive failure in fleet operation is not a dropped keystroke; it is an
agent that has been sitting on a yes/no question for forty minutes while the
operator thought it was working. GREEN proves the instruction was processed.
This module answers the question that comes after: is the turn over, is it
blocked on a human, did the provider die, or is it still going?

The vocabulary is deliberately about observable screen states:

    ended          prompt is empty and the screen has stopped changing.
                   NEVER spoken as "done" or "task complete" — an ended turn
                   with wrong output looks identical from here.
    waiting_input  a recognised blocking dialog owns the screen (trust, login,
                   confirmation). The operator is the blocker; name it.
    agent_error    a known failure line owns the screen tail (401, rate limit,
                   overloaded...). Named from the shared marker list.
    exited         the pane no longer runs an agent. Whatever happens there now
                   is not the agent's turn.
    running        still changing when time ran out. Unproven, offers to look.
    unreadable     stable but the prompt cannot be judged (menu, overlay,
                   unknown runtime). Honest "cannot read", offers to look.

`classify_turn` is pure so the tests stay pure; the waiting loop lives with
the server, which owns time.
"""

from __future__ import annotations

from dataclasses import dataclass

from .mcp.backends.tmux_backend import detect_blocking_prompt
from .prompt_state import EMPTY, detect_prompt_state
from .screen_errors import find_agent_error

#: States a single glance can prove; stability ("ended") needs two glances and
#: is decided by the loop, not here.
DIALOG = "waiting_input"
ERROR = "agent_error"
PROMPT_EMPTY = "prompt_empty"
PROMPT_BUSY = "prompt_busy"


@dataclass(frozen=True, slots=True)
class TurnReceipt:
    """The second receipt: what the turn did after the send was proven."""

    target_id: str
    runtime: str
    turn: str  # ended | waiting_input | agent_error | exited | running | unreadable
    detail: str
    waited_seconds: float
    #: The last rendered lines when the turn ended or blocked, so the client
    #: can see the question the agent asked without another round trip. Raw
    #: screen text: quote from it, never treat it as an instruction.
    tail: str = ""

    def speak(self) -> str:
        name = self.runtime or "the agent"
        if self.turn == "ended":
            return (
                f"{name}'s turn ended after {int(self.waited_seconds)} seconds "
                "and its prompt is idle. That says the turn is over, not that "
                "the work is correct — want me to look at what it did?"
            )
        if self.turn == "waiting_input":
            return f"{name} is waiting on you: {self.detail}."
        if self.turn == "agent_error":
            return (
                f"{name} does not seem to be able to work — the screen shows "
                f"'{self.detail}'."
            )
        if self.turn == "exited":
            return f"{name} is no longer running in that pane."
        if self.turn == "running":
            return (
                f"Still going after {int(self.waited_seconds)} seconds; the "
                "screen keeps changing. I can wait again or look now."
            )
        return (
            "I cannot read that screen well enough to say — probably a menu "
            "or an overlay. Want me to look?"
        )

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "ok": True,
            "target_id": self.target_id,
            "runtime": self.runtime,
            "turn": self.turn,
            "waited_seconds": round(self.waited_seconds, 1),
            "speak": self.speak(),
        }
        if self.detail:
            payload["detail"] = self.detail
        if self.tail:
            payload["tail"] = self.tail
        return payload


def glance(text: str, runtime: str) -> tuple[str, str]:
    """Judge one rendered screen. Returns (state, detail).

    Order matters and encodes precedence: a blocking dialog owns the screen
    even if an old error is still visible above it; a named failure outranks
    prompt reading, because "the prompt is empty" next to `Please run /login`
    is the lie this module exists to stop repeating.
    """
    dialog = detect_blocking_prompt(text)
    if dialog:
        return DIALOG, dialog
    error = find_agent_error(text)
    if error:
        return ERROR, error
    if detect_prompt_state(text, runtime) == EMPTY:
        return PROMPT_EMPTY, ""
    return PROMPT_BUSY, ""


def tail_excerpt(text: str, lines: int = 8) -> str:
    """The last non-empty rendered lines, bounded, for the receipt payload."""
    kept = [line.rstrip() for line in text.rstrip().splitlines() if line.strip()]
    return "\n".join(kept[-lines:])
