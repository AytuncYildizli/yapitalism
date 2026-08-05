"""Turns a send plus an acceptance check into a verdict a voice may speak.

Three outcomes, and the middle one is the whole point:

- ``GREEN``  the agent demonstrably processed the text (canary observed).
- ``YELLOW`` the write landed but processing was never proven — either no
  canary was supplied, or it never appeared. Unknown is not success.
- ``RED``    the backend refused the write, or it failed.

The verdict also carries who enforced each guarantee, which is finer than
whether. A tmux GREEN and a Superset GREEN are not interchangeable: a guarded
Superset host refused the write unless the revision matched, the token was
unused and the prompt was empty, while tmux checked none of those.

And a third case sits between them, which an earlier version of this file could
not express: a host without the guarded send, where this process checks the
revision and the token itself a moment before writing. That is a real guarantee
against a stale or repeated send and a weaker one than the host's, because the
check and the write are two operations rather than one. Reporting it as either
"enforced" or "missing" would be a lie in one direction or the other, so
`client_guarantees` is carried separately from `missing_guarantees`.

Saying all this is the difference between a receipt and a decoration.
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
    #: Guarantees this process enforced instead of the host. Not missing, and not
    #: the same as host-enforced: the check and the write are two operations, so
    #: anything landing between them is unseen.
    client_guarantees: tuple[str, ...] = ()

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
        if self.client_guarantees:
            payload["client_guarantees"] = list(self.client_guarantees)
        return payload


def build_receipt(
    send: SendOutcome,
    acceptance: AcceptanceOutcome,
    capabilities: BackendCapabilities,
) -> Receipt:
    degraded = capabilities.degraded
    client = capabilities.client_enforced

    if not send.dispatched:
        return Receipt(
            status="RED",
            phase=send.phase,
            accepted=False,
            reason=send.reason or send.phase,
            speak=_speak_rejected(send.phase, send.reason),
            missing_guarantees=degraded,
            client_guarantees=client,
        )

    if acceptance.observed:
        # Even a proven acceptance says what it could not check, and who did the
        # checking. A GREEN whose guards were applied here rather than by the host
        # is still a GREEN — the agent demonstrably processed the text — but the
        # two are not interchangeable and the sentence must not pretend they are.
        return Receipt(
            status="GREEN",
            phase=send.phase,
            accepted=True,
            reason="",
            speak="Ajan aldı ve işledi." + _speak_guard_caveat(degraded, client),
            missing_guarantees=degraded,
            client_guarantees=client,
        )

    if acceptance.reason == "no_canary":
        return Receipt(
            status="YELLOW",
            phase=send.phase,
            accepted=False,
            reason="acceptance_not_testable",
            speak="SARI: Metni gönderdim ama ajanın işlediğini doğrulayamadım.",
            missing_guarantees=degraded,
            client_guarantees=client,
        )

    if acceptance.pane_changed_recently:
        # Still YELLOW, and the wording is deliberately about the PANE, not the
        # agent. A spinner, a clock, a log tail or a second agent sharing the
        # pane all move the text without the intended agent doing anything, so
        # "the agent is still working" would be a claim the evidence cannot
        # carry — the same class of overclaim as speaking YELLOW as GREEN.
        return Receipt(
            status="YELLOW",
            phase=send.phase,
            accepted=False,
            reason=acceptance.reason or "canary_timeout_pane_moving",
            speak=(
                "SARI: Terminalde hareket var ama ajanın işlediğine dair kanıt "
                f"gelmedi; {int(acceptance.waited_seconds)} saniye bekledim. "
                "Tekrar bakmamı ister misin?"
            ),
            missing_guarantees=degraded,
            client_guarantees=client,
        )

    return Receipt(
        status="YELLOW",
        phase=send.phase,
        accepted=False,
        reason=acceptance.reason or "canary_timeout",
        speak=(
            "SARI: Gönderdim, terminalde hiç hareket olmadı ve ajanın "
            "işlediğine dair kanıt gelmedi."
        ),
        missing_guarantees=degraded,
        client_guarantees=client,
    )


def _speak_guard_caveat(
    degraded: tuple[str, ...], client: tuple[str, ...]
) -> str:
    """The caveat a GREEN has to carry, in the operator's words.

    Three cases rather than two. The old wording had one sentence for "something
    was unchecked", which would have described a client-enforced guard and an
    unchecked one identically — the exact collapse that let a stock host's
    receipt read like the fork's.
    """
    if degraded and client:
        return (
            " Bazı kontrolleri host değil bu taraf yaptı, bir kontrol de hiç "
            "yapılmadı."
        )
    if client:
        return (
            " Kontrolleri host değil bu taraf yaptı; yazmadan hemen önce baktı, "
            "yazmayı reddedebilecek olan host değildi."
        )
    if degraded:
        return " (bu backend yazmadan önce doğrulayamadığı kontroller var)"
    return ""


def _speak_rejected(phase: str, reason: str = "") -> str:
    if reason == "host_says_occupied_screen_says_empty":
        # Never advise clearing here: it has been measured not to work. The host
        # counts a Codex placeholder suggestion as staged text, and there is
        # nothing in the prompt for a clear to remove.
        return (
            "Host yazmayı reddetti ama ekranda prompt boş görünüyor; büyük "
            "olasılıkla ajanın kendi öneri metnini yazılmış sanıyor. Temizlemek "
            "burada işe yaramaz."
        )
    if phase == "rejected_trust_prompt":
        return (
            "Ajan bir güven onayı ekranında bekliyor; oraya yazmak menüden "
            "rastgele bir seçenek seçebilirdi, hiçbir şey yazmadım."
        )
    if phase == "rejected_auth_prompt":
        return "Ajan giriş ekranında bekliyor; hiçbir şey yazmadım."
    if phase == "rejected_confirm_prompt":
        return "Ajan bir onay bekliyor; hiçbir şey yazmadım."
    if phase == "rejected_prompt_not_empty":
        return (
            "Prompt alanında bekleyen metin var; hiçbir şey yazmadım. "
            "İstersen temizleyip tekrar deneyebilirim."
        )
    if phase == "rejected_prompt_unreadable":
        # Previously fell through to the generic line, so a real and specific
        # obstruction - an open menu or overlay - was reported as an unexplained
        # refusal. The information existed and was discarded at the last step.
        return (
            "Prompt alanı okunamadı, muhtemelen bir menü ya da katman açık; "
            "hiçbir şey yazmadım. Ne beklediğine bakabilirim."
        )
    if phase == "rejected_not_an_agent":
        # Typing into a shell and pressing Enter is running a command. The pane may
        # have been an agent when it was listed and be a shell now.
        return (
            "O panelde bir ajan çalışmıyor; oraya yazmak komut çalıştırmak olurdu, "
            "hiçbir şey göndermedim."
        )
    if phase == "duplicate_after_ambiguous_write":
        # NOT "I blocked a repeat": the first attempt may never have arrived.
        return (
            "Bu mesajın ilk denemesi yarıda kaldı, gidip gitmediği belirsiz; ikinci "
            "kez yazmadım. Paneli okuyup durumu söyleyebilirim."
        )
    if phase == "staged_not_submitted":
        # Observed live: the text reached a Codex composer but two Enters did not
        # submit it. The operator has to know the message is sitting there, or they
        # will believe it was delivered and wait.
        return (
            "Metni yazdım ama gönderilemedi; prompt'ta duruyor. Enter iki kez "
            "denendi, kabul edilmedi."
        )
    if phase.startswith("duplicate_"):
        return "Aynı mesajın tekrarını engelledim; ikinci kez yazmadım."
    if phase == "rejected_revision_changed":
        return "Terminal değişmiş; güvenli olmadığı için yazmadım."
    return "Yazma reddedildi; terminale hiçbir şey gitmedi."
