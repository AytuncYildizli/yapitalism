"""tmux backend.

Honest about what it cannot do. `tmux send-keys` has no notion of an expected
revision, no client-token dedup, and no way to refuse a write when the agent's
prompt already holds text — so all three capability flags are False. Runtime is
derived from the pane's process tree, which is weaker than a host registry
because a pane can change what it runs between the check and the write.
"""

from __future__ import annotations

import time

from ...adapters.superset import _canary_could_appear, _structured_canary_observed
from ..revision import RevisionTracker
from ..tmux import TmuxError, capture_pane, list_panes, send_enter, send_literal
from .base import (
    AcceptanceOutcome,
    BackendCapabilities,
    BackendError,
    BackendPane,
    SendOutcome,
)

_CAPABILITIES = BackendCapabilities(
    idempotent_dispatch=False,
    optimistic_revision=False,
    empty_prompt_check=False,
    runtime_detection="process_tree",
)


class TmuxBackend:
    def __init__(self) -> None:
        self._revisions = RevisionTracker()

    @property
    def namespace(self) -> str:
        return "tmux"

    def capabilities(self) -> BackendCapabilities:
        return _CAPABILITIES

    def list_panes(self) -> list[BackendPane]:
        try:
            panes = list_panes()
        except TmuxError as error:
            raise BackendError(str(error)) from None
        return [
            BackendPane(
                target_id=pane.target_id,
                label=f"{pane.session_name}:{pane.window_index}.{pane.pane_index}",
                runtime=pane.runtime,
                width=pane.width,
                height=pane.height,
                dead=pane.dead,
                detail=pane.current_command,
            )
            for pane in panes
        ]

    def read_pane(self, target_id: str, lines: int) -> str:
        try:
            return capture_pane(target_id, lines)
        except TmuxError as error:
            raise BackendError(str(error)) from None


    def send(
        self,
        target_id: str,
        text: str,
        *,
        canary: str | None,
        client_token: str,
    ) -> SendOutcome:
        """Type text and submit it.

        tmux offers no expected-revision guard, no client-token dedup, and no
        way to refuse a write when the prompt already holds text. `client_token`
        is accepted for interface parity and deliberately unused: pretending to
        deduplicate would be worse than declaring the gap in capabilities.
        """
        try:
            before = capture_pane(target_id, 1000)
            revision_before = self._revisions.observe(target_id, before)
            if canary is not None:
                if _canary_could_appear(before, canary):
                    raise BackendError("canary was already present in the pane")
                if _canary_could_appear(text, canary):
                    raise BackendError("canary must not occur in the submitted text")
            send_literal(target_id, text)
            send_enter(target_id)
            after = capture_pane(target_id, 1000)
        except TmuxError as error:
            raise BackendError(str(error)) from None
        return SendOutcome(
            phase="injected",
            dispatched=True,
            runtime=self._runtime_of(target_id),
            revision_before=revision_before,
            revision_after=self._revisions.observe(target_id, after),
        )

    def await_acceptance(
        self, target_id: str, canary: str | None, *, timeout: float = 8.0
    ) -> AcceptanceOutcome:
        if canary is None:
            # Never testable, which is not the same as failed.
            return AcceptanceOutcome(False, 0, "no_canary")
        deadline = time.monotonic() + timeout
        attempts = 0
        while time.monotonic() < deadline:
            attempts += 1
            try:
                text = capture_pane(target_id, 1000)
            except TmuxError as error:
                raise BackendError(str(error)) from None
            if _structured_canary_observed(text, canary):
                return AcceptanceOutcome(True, attempts)
            time.sleep(min(0.3, max(0.0, deadline - time.monotonic())))
        return AcceptanceOutcome(False, attempts, "canary_timeout")

    def _runtime_of(self, target_id: str) -> str:
        for pane in self.list_panes():
            if pane.target_id == target_id:
                return pane.runtime
        return "unknown"
