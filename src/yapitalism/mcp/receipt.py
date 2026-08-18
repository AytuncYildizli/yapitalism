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

WHAT IS SPOKEN IS NARROWER THAN WHAT IS RECORDED, and deliberately so. The three
statuses, the phase, and the enforcement attribution all stay in `as_dict()`,
where the model reading the payload, the log, and `doctor` can see them. The
spoken line collapses to two things a person can act on:

    "codex aldı."                     -> nothing to do, carry on
    "Gönderilmedi: <sebep>. <çıkış>"  -> the text never left; act
    "Gönderdim ama ... doğrulayamadım" -> it DID leave; do not resend blindly

That last distinction is the one thing the collapse must not lose. A refusal and
an unproven delivery both mean "no confirmation", but they call for opposite
moves: the first can be retried, and retrying the second may deliver the message
twice. So a YELLOW line never says "tekrar göndereyim mi" — it offers to LOOK.

Enforcement attribution used to be read aloud on every GREEN ("bazı kontrolleri
host değil bu taraf yaptı"). It is the maintainer's honesty aesthetic and it is
invisible to the operator: there is no different action behind it. It now lives
in the payload only.
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
    *,
    clearing_known_useless: bool = False,
) -> Receipt:
    degraded = capabilities.degraded
    client = capabilities.client_enforced

    agent = _agent_name(send.runtime)

    if not send.dispatched:
        return Receipt(
            status="RED",
            phase=send.phase,
            accepted=False,
            reason=send.reason or send.phase,
            speak=_speak_rejected(
                send.phase, send.reason, agent, clearing_known_useless
            ),
            missing_guarantees=degraded,
            client_guarantees=client,
        )

    if acceptance.observed:
        if send.reason == "host_prompt_check_overridden":
            # Spoken because it changes what the operator may want to LOOK at, not
            # as attribution: something was already staged in that prompt and their
            # text was merged with it on their own instruction.
            speak = (
                f"{agent} aldı. Prompt'ta bekleyen metin vardı, "
                "sen istediğin için yine de gönderdim."
            )
        else:
            speak = f"{agent} aldı."
        return Receipt(
            status="GREEN",
            phase=send.phase,
            accepted=True,
            reason=send.reason if send.reason == "host_prompt_check_overridden" else "",
            speak=speak,
            missing_guarantees=degraded,
            client_guarantees=client,
        )

    # Every YELLOW below says GÖNDERDİM first and offers to LOOK, never to resend.
    # The text is already in the terminal; a second write is a second message.
    if acceptance.reason == "no_canary":
        return Receipt(
            status="YELLOW",
            phase=send.phase,
            accepted=False,
            reason="acceptance_not_testable",
            speak=f"Gönderdim ama {agent} aldı mı, doğrulayamadım.",
            missing_guarantees=degraded,
            client_guarantees=client,
        )

    if acceptance.pane_changed_recently:
        # The movement clause is deliberately about the TERMINAL, not the agent. A
        # spinner, a clock, a log tail or a second agent sharing the pane all move
        # the text without the intended agent doing anything, so "the agent is
        # still working" would be a claim the evidence cannot carry — the same
        # class of overclaim as speaking YELLOW as GREEN.
        return Receipt(
            status="YELLOW",
            phase=send.phase,
            accepted=False,
            reason=acceptance.reason or "canary_timeout_pane_moving",
            speak=(
                f"Gönderdim, terminalde hareket var ama {agent} aldı mı, "
                f"doğrulayamadım; {int(acceptance.waited_seconds)} saniye "
                "bekledim. Bakayım mı?"
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
            f"Gönderdim ama terminalde hiç hareket olmadı; {agent} aldı mı, "
            "doğrulayamadım. Bakayım mı?"
        ),
        missing_guarantees=degraded,
        client_guarantees=client,
    )


#: What the operator calls the thing they are talking to. The runtime name is
#: already the word they use out loud — "codex", "claude", "kimi" — so it is
#: spoken as-is, with no suffix that would need vowel harmony per runtime.
def _agent_name(runtime: str) -> str:
    return runtime.strip() or "ajan"


def _speak_rejected(
    phase: str,
    reason: str = "",
    agent: str = "ajan",
    clearing_known_useless: bool = False,
) -> str:
    """One shape: GÖNDERİLMEDİ, why, and the way out.

    The lead word is fixed. A refusal is the one case where the operator can
    safely retry, and it has to be distinguishable from an unproven delivery by
    the first word alone — the rest of the sentence may not be heard.
    """
    if reason == "host_says_occupied_screen_says_empty":
        # Never advise clearing here: it has been measured not to work. The host
        # counts a Codex placeholder suggestion as staged text, and there is
        # nothing in the prompt for a clear to remove.
        return (
            "Gönderilmedi: prompt boş görünüyor ama dolu sayılıyor, muhtemelen "
            f"{agent} kendi öneri metnini yazılmış sanıyor. Temizlemek burada "
            "işe yaramaz."
        )
    if phase == "rejected_trust_prompt":
        return (
            f"Gönderilmedi: {agent} bir güven onayı ekranında bekliyor, oraya "
            "yazmak menüden rastgele bir seçenek seçebilirdi."
        )
    if phase == "rejected_auth_prompt":
        return f"Gönderilmedi: {agent} giriş ekranında bekliyor."
    if phase == "rejected_confirm_prompt":
        return f"Gönderilmedi: {agent} bir onay bekliyor."
    if phase == "rejected_prompt_not_empty":
        if clearing_known_useless:
            return (
                "Gönderilmedi: prompt alanında bekleyen metin var ve temizlemeyi "
                f"denedim, {agent} tuşlara cevap vermiyor. Bu panele makinede "
                "bakman gerekiyor."
            )
        return (
            "Gönderilmedi: prompt alanında bekleyen metin var. İstersen "
            "temizleyip tekrar deneyebilirim."
        )
    if phase == "rejected_prompt_unreadable" and clearing_known_useless:
        return (
            "Gönderilmedi: prompt alanı okunamıyor ve temizlemeyi denedim, "
            f"{agent} tuşlara cevap vermiyor. Bu panele makinede bakman gerekiyor."
        )
    if phase == "rejected_prompt_unreadable":
        # Previously fell through to the generic line, so a real and specific
        # obstruction - an open menu or overlay - was reported as an unexplained
        # refusal. The information existed and was discarded at the last step.
        return (
            "Gönderilmedi: prompt alanı okunamadı, muhtemelen bir menü ya da "
            "katman açık. Ne beklediğine bakabilirim."
        )
    if phase == "rejected_not_an_agent":
        # Typing into a shell and pressing Enter is running a command. The pane may
        # have been an agent when it was listed and be a shell now.
        return (
            "Gönderilmedi: orada bir ajan çalışmıyor, yazsaydım komut "
            "çalıştırmış olurdum."
        )
    if phase == "duplicate_after_ambiguous_write":
        # NOT "I blocked a repeat": the first attempt may never have arrived.
        return (
            "Gönderilmedi: bu mesajın ilk denemesi yarıda kaldı, gidip gitmediği "
            "belirsiz. Paneli okuyup durumu söyleyebilirim."
        )
    if phase == "staged_not_submitted":
        # Observed live: the text reached a Codex composer but two Enters did not
        # submit it. The operator has to know the message is sitting there, or they
        # will believe it was delivered and wait.
        return (
            "Gönderilmedi: metin prompt'ta duruyor, Enter iki kez denendi ama "
            "kabul edilmedi."
        )
    if phase.startswith("duplicate_"):
        return "Gönderilmedi: aynı mesajın tekrarıydı, ikinci kez yazmadım."
    if phase == "rejected_revision_changed":
        return "Gönderilmedi: terminal değişmiş, güvenli olmadığı için yazmadım."
    return "Gönderilmedi: terminale hiçbir şey gitmedi."
