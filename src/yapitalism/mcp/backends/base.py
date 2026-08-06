"""The contract every terminal backend answers.

One MCP surface, several backends. Target ids carry their namespace
(`tmux:%0`, `superset:<uuid>`) so routing never has to guess.

`BackendCapabilities` is the load-bearing part. Backends do not offer the same
guarantees: a guarded Superset host enforces an expected revision, a single-use
client token and an empty-prompt check before it writes, while `tmux send-keys`
has none of those. If both answered the same tools without declaring the
difference, a tmux receipt would look identical to a Superset one while proving
far less — a quiet downgrade on the operator's own machine, and a false GREEN on
someone else's.

Two refinements came from getting this wrong. Capabilities are a property of the
HOST, not of the backend class: the Superset values were constants describing one
fork build, which would have promised every stock user three guards their host
had never heard of. And a guarantee has a third state — enforced by this process
rather than by the host — which a boolean could not express, so a client-side
check and no check at all read identically.

Capabilities therefore travel with the receipt as HOST, CLIENT or NONE per
guarantee, and a verdict can only claim what was actually enforced, by whoever
actually enforced it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class BackendError(RuntimeError):
    """A backend could not answer. Never raised to claim success."""


#: The host refuses the write itself. Strongest: the check and the write are one
#: operation, so nothing can slip between them.
HOST = "host"
#: This process checks, then writes. A real guarantee against the mistakes it
#: covers, but not atomic — anything landing in the gap goes unseen. Never
#: describe it with the same words as HOST.
CLIENT = "client"
#: Nothing checks. The write goes out blind.
NONE = "none"

_ENFORCEMENT = (HOST, CLIENT, NONE)

#: The three guarantees a send can carry, in the order a receipt lists them.
GUARANTEES = ("idempotent_dispatch", "optimistic_revision", "empty_prompt_check")


@dataclass(frozen=True, slots=True)
class BackendCapabilities:
    """Who enforces each guarantee — not merely whether it exists.

    These were booleans, and the boolean hid the question that matters. A guard
    the host applies atomically and a guard this process applies a moment before
    writing are both "True", and collapsing them let one machine's arrangement
    read as a property of the product. Superset's fork host enforces all three;
    a stock Superset build has none of that machinery, and the same `True`
    would have promised its users something no host ever checked.

    So the values are HOST, CLIENT or NONE, and a receipt reports the last two
    separately. Nothing is withheld from anyone for lacking the fork — a
    CLIENT-enforced send still refuses a stale revision and a repeated token —
    but the verdict says who did the refusing.
    """

    #: Rejects a second write carrying a client token already seen.
    idempotent_dispatch: str
    #: Refuses to write when the terminal moved since it was read.
    optimistic_revision: str
    #: Refuses to write over text already staged in the prompt.
    empty_prompt_check: str
    #: "registry" when the host reports the runtime, "process_tree" when it is
    #: derived here. Derived detection is weaker: it can go stale between the
    #: check and the write.
    runtime_detection: str

    def __post_init__(self) -> None:
        for name in GUARANTEES:
            value = getattr(self, name)
            if value not in _ENFORCEMENT:
                raise ValueError(
                    f"{name} must be one of {_ENFORCEMENT}, got {value!r}. "
                    "Booleans were replaced deliberately: True could not say "
                    "whether the host or this process did the checking."
                )

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {name: getattr(self, name) for name in GUARANTEES}
        payload["runtime_detection"] = self.runtime_detection
        return payload

    def _at(self, level: str) -> tuple[str, ...]:
        return tuple(name for name in GUARANTEES if getattr(self, name) == level)

    @property
    def degraded(self) -> tuple[str, ...]:
        """Guarantees nothing checks. The receipt must carry these."""
        return self._at(NONE)

    @property
    def client_enforced(self) -> tuple[str, ...]:
        """Guarantees this process checks rather than the host.

        Reported separately from `degraded` because they are not absent, and
        separately from silence because they are not atomic.
        """
        return self._at(CLIENT)


@dataclass(frozen=True, slots=True)
class BackendPane:
    target_id: str
    label: str
    runtime: str
    width: int
    height: int
    dead: bool
    detail: str = ""
    #: The checked-out repo a terminal belongs to. This is what a person uses to
    #: identify a terminal out loud — the workspace name alone ("dasendeha") is
    #: meaningless without knowing which project it sits in.
    project: str = ""
    branch: str = ""
    #: The directory the terminal is actually in, last segment only. Carried
    #: separately from `project` because they disagree often enough to matter:
    #: project "yapitalism" lives in a folder still called "relayproof",
    #: "Superset Watch Voice" in "superset-watchos-voice-spike", "opty" in a
    #: nested "opty/opty". Someone naming a terminal out loud may reach for
    #: either word, so both have to be matchable.
    folder: str = ""
    #: Full path, for disambiguating two folders with the same last segment.
    #: Too long to speak; meant for the model to reason over, not read aloud.
    path: str = ""


@runtime_checkable
class TerminalBackend(Protocol):
    """Read surface. Sending is deliberately absent until receipts exist for it."""

    @property
    def namespace(self) -> str:
        """Target-id prefix this backend owns, e.g. `tmux`."""
        ...

    def capabilities(self) -> BackendCapabilities: ...

    def list_panes(self) -> list[BackendPane]: ...

    def read_pane(self, target_id: str, lines: int) -> str: ...


AGENT_RUNTIMES = frozenset({"codex", "claude", "kimi"})


def parse_target_id(target_id: str) -> tuple[str, str]:
    """Split `namespace:body`.

    A bare id is rejected rather than defaulted. Guessing the backend is how a
    command reaches the wrong machine.
    """
    if not isinstance(target_id, str) or ":" not in target_id:
        raise BackendError("target id must be namespaced, e.g. tmux:%0")
    namespace, _, body = target_id.partition(":")
    if not namespace or not body:
        raise BackendError("target id must be namespaced, e.g. tmux:%0")
    return namespace, body


@dataclass(frozen=True, slots=True)
class SendOutcome:
    """What a backend can honestly say immediately after writing."""

    phase: str
    dispatched: bool
    runtime: str
    revision_before: int | None = None
    revision_after: int | None = None
    delivery_ref: str | None = None
    reason: str = ""
    #: Set when this one send was made under guarantees that differ from the
    #: backend's usual answer. A receipt must describe THIS write, and a per-send
    #: override that reported the backend's standing capabilities would be a lie
    #: shaped exactly like the one the enforcement levels exist to prevent.
    capabilities_override: "BackendCapabilities | None" = None


@dataclass(frozen=True, slots=True)
class CreateOutcome:
    """What a backend can honestly say after being asked to start an agent.

    `created` and `runtime_confirmed` are separate on purpose. tmux will happily
    report a new session whose command died immediately — a missing binary, a
    launcher that exits — leaving a live pane running nothing. Reporting that as
    a started agent is the same class of lie as a YELLOW spoken as GREEN, so the
    pane's process tree has to agree before `runtime_confirmed` is true.
    """

    target_id: str
    created: bool
    runtime_requested: str
    runtime_observed: str
    session_name: str
    cwd: str
    reason: str = ""
    #: A known blocking prompt was seen in the pane, e.g. "trust_prompt". Empty
    #: means none was RECOGNISED, which is not the same as "the agent is ready"
    #: — this is pattern matching against states we have seen before, never
    #: proof of readiness. The first live run found the gap: an agent parked on
    #: a trust dialog reports runtime_confirmed and silently swallows the first
    #: instruction sent to it.
    blocked_on: str = ""
    #: Only set by a resume: "exact" when a recorded session id was used, "last"
    #: when the runtime was asked for its most recent session — which is NOT a
    #: guarantee it is the one anybody meant. Empty for a fresh start, where the
    #: question does not arise.
    fidelity: str = ""

    @property
    def runtime_confirmed(self) -> bool:
        return self.created and self.runtime_observed == self.runtime_requested

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "target_id": self.target_id,
            "created": self.created,
            "runtime_requested": self.runtime_requested,
            "runtime_observed": self.runtime_observed,
            "runtime_confirmed": self.runtime_confirmed,
            "session_name": self.session_name,
            "cwd": self.cwd,
            "reason": self.reason,
        }
        if self.blocked_on:
            payload["blocked_on"] = self.blocked_on
        if self.fidelity:
            payload["fidelity"] = self.fidelity
        return payload


@dataclass(frozen=True, slots=True)
class AcceptanceOutcome:
    """Whether the agent demonstrably processed the text.

    `observed=False` with `reason="no_canary"` means acceptance was never
    testable — not that it failed. Those are different verdicts and must not
    collapse into one.

    `pane_changed_recently` is deliberately named after what was measured and
    not after what one might wish it meant. It says the pane's text moved, which
    a spinner, a clock, a log tail or a second agent sharing the pane all
    produce without the intended agent doing anything. It must never be spoken
    as "the agent is working"; it is a hint about whether looking again is worth
    it, nothing more.
    """

    observed: bool
    attempts: int
    reason: str = ""
    #: The pane's text changed within the last idle window. NOT evidence that
    #: the intended agent is doing anything.
    pane_changed_recently: bool = False
    #: How long acceptance was actually waited for.
    waited_seconds: float = 0.0
