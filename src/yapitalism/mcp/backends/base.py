"""The contract every terminal backend answers.

One MCP surface, several backends. Target ids carry their namespace
(`tmux:%0`, `superset:<uuid>`) so routing never has to guess.

`BackendCapabilities` is the load-bearing part. Backends do not offer the same
guarantees: Superset's host enforces an expected revision, a single-use client
token, and an empty-prompt check before it writes, while `tmux send-keys` has
none of those. If both answered the same tools without declaring the
difference, a tmux receipt would look identical to a Superset one while proving
far less — a quiet downgrade on the operator's own machine, and a false GREEN
on someone else's. Capabilities travel with the receipt so a verdict can only
claim what the backend actually enforced.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class BackendError(RuntimeError):
    """A backend could not answer. Never raised to claim success."""


@dataclass(frozen=True, slots=True)
class BackendCapabilities:
    #: The backend rejects a second write carrying a client token it already saw.
    idempotent_dispatch: bool
    #: The backend refuses to write when the terminal moved since it was read.
    optimistic_revision: bool
    #: The backend refuses to write over text already staged in the prompt.
    empty_prompt_check: bool
    #: "registry" when the host reports the runtime, "process_tree" when it is
    #: derived here. Derived detection is weaker: it can go stale between the
    #: check and the write.
    runtime_detection: str

    def as_dict(self) -> dict[str, object]:
        return {
            "idempotent_dispatch": self.idempotent_dispatch,
            "optimistic_revision": self.optimistic_revision,
            "empty_prompt_check": self.empty_prompt_check,
            "runtime_detection": self.runtime_detection,
        }

    @property
    def degraded(self) -> tuple[str, ...]:
        """Guarantees this backend does NOT provide, for the receipt to carry."""
        missing: list[str] = []
        if not self.idempotent_dispatch:
            missing.append("idempotent_dispatch")
        if not self.optimistic_revision:
            missing.append("optimistic_revision")
        if not self.empty_prompt_check:
            missing.append("empty_prompt_check")
        return tuple(missing)


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

    @property
    def runtime_confirmed(self) -> bool:
        return self.created and self.runtime_observed == self.runtime_requested

    def as_dict(self) -> dict[str, object]:
        return {
            "target_id": self.target_id,
            "created": self.created,
            "runtime_requested": self.runtime_requested,
            "runtime_observed": self.runtime_observed,
            "runtime_confirmed": self.runtime_confirmed,
            "session_name": self.session_name,
            "cwd": self.cwd,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class AcceptanceOutcome:
    """Whether the agent demonstrably processed the text.

    `observed=False` with `reason="no_canary"` means acceptance was never
    testable — not that it failed. Those are different verdicts and must not
    collapse into one.
    """

    observed: bool
    attempts: int
    reason: str = ""
