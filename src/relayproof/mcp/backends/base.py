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
