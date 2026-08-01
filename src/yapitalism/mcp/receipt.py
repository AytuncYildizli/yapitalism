"""Turns a send plus an acceptance check into a verdict a voice may speak.

Three outcomes, and the middle one is the whole point:

- ``GREEN``  the agent demonstrably processed the text (canary observed).
- ``YELLOW`` the write landed but processing was never proven — either no
  canary was supplied, or it never appeared. Unknown is not success.
- ``RED``    the backend refused the write, or it failed.

The verdict also carries the guarantees the backend did NOT enforce. A tmux
GREEN and a Superset GREEN are not interchangeable: Superset's host refused the
write unless the revision matched, the token was unused and the prompt was
empty, while tmux checked none of those. Saying so is the difference between a
receipt and a decoration.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from .backends.base import AcceptanceOutcome, BackendCapabilities, SendOutcome

CANARY_PREFIX = "YAPITALISM_ACK_"  # renames with the package


def new_canary() -> str:
    return CANARY_PREFIX + uuid4().hex.upper()


def canary_instruction(canary: str) -> str:
    """Ask the agent to emit the marker without ever writing it out.

    The marker must not appear contiguously in the submitted text, or the
    pane's echo of the prompt would satisfy acceptance on its own — proving
    that the terminal can display characters, not that an agent read anything.

    Splitting on whitespace is not enough: the matcher tolerates a hard wrap
    inside the token, so whitespace-separated halves would still match. The
    pieces are therefore separated by real words.
    """
    body = canary[len(CANARY_PREFIX) :]
    first, second = body[:16], body[16:]
    return (
        "When you have finished, print one line that is "
        f"{CANARY_PREFIX} then {first} then {second}, "
        "concatenated with no spaces between the three parts."
    )


@dataclass(frozen=True, slots=True)
class Receipt:
    status: str
    phase: str
    accepted: bool
    reason: str
    speak: str
    missing_guarantees: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "status": self.status,
            "phase": self.phase,
            "accepted": self.accepted,
            "speak": self.speak,
        }
        if self.reason:
            payload["reason"] = self.reason
        if self.missing_guarantees:
            payload["missing_guarantees"] = list(self.missing_guarantees)
        return payload


def build_receipt(
    send: SendOutcome,
    acceptance: AcceptanceOutcome,
    capabilities: BackendCapabilities,
) -> Receipt:
    degraded = capabilities.degraded

    if not send.dispatched:
        return Receipt(
            status="RED",
            phase=send.phase,
            accepted=False,
            reason=send.reason or send.phase,
            speak=_speak_rejected(send.phase),
            missing_guarantees=degraded,
        )

    if acceptance.observed:
        # Even a proven acceptance says what it could not check.
        suffix = ""
        if degraded:
            suffix = " (bu backend yazmadan önce doğrulayamadığı kontroller var)"
        return Receipt(
            status="GREEN",
            phase=send.phase,
            accepted=True,
            reason="",
            speak="Ajan aldı ve işledi." + suffix,
            missing_guarantees=degraded,
        )

    if acceptance.reason == "no_canary":
        return Receipt(
            status="YELLOW",
            phase=send.phase,
            accepted=False,
            reason="acceptance_not_testable",
            speak="SARI: Metni gönderdim ama ajanın işlediğini doğrulayamadım.",
            missing_guarantees=degraded,
        )

    return Receipt(
        status="YELLOW",
        phase=send.phase,
        accepted=False,
        reason=acceptance.reason or "canary_timeout",
        speak="SARI: Gönderdim, ajanın işlediğine dair kanıt gelmedi.",
        missing_guarantees=degraded,
    )


def _speak_rejected(phase: str) -> str:
    if phase == "rejected_prompt_not_empty":
        return "Prompt alanında bekleyen metin var; hiçbir şey yazmadım."
    if phase.startswith("duplicate_"):
        return "Aynı mesajın tekrarını engelledim; ikinci kez yazmadım."
    if phase == "rejected_revision_changed":
        return "Terminal değişmiş; güvenli olmadığı için yazmadım."
    return "Yazma reddedildi; terminale hiçbir şey gitmedi."
