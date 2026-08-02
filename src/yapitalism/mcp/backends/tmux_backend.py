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
from ..tmux import (
    TmuxError,
    capture_pane,
    list_panes,
    new_agent_session,
    observe_runtime,
    send_enter,
    send_literal,
)
from .base import (
    AcceptanceOutcome,
    BackendCapabilities,
    BackendError,
    BackendPane,
    CreateOutcome,
    SendOutcome,
)

# Blocking states seen in real agent startups, matched against the pane after
# the runtime is confirmed. This is recognition, not proof: a state absent from
# this table reads as "nothing recognised", never as "ready". Named accordingly
# so nobody downstream can mistake one for the other.
_BLOCKING_PROMPTS: tuple[tuple[str, str], ...] = (
    # Observed live: claude parks here on a directory it has not seen before,
    # and swallows the first instruction sent to it.
    ("trust this folder", "trust_prompt"),
    ("do you trust", "trust_prompt"),
    ("yes, i trust", "trust_prompt"),
    ("sign in to", "auth_prompt"),
    ("log in to", "auth_prompt"),
    ("paste your api key", "auth_prompt"),
    ("press enter to continue", "confirm_prompt"),
)


def detect_blocking_prompt(pane_text: str) -> str:
    """Label a known blocking prompt, or "" when none is recognised."""
    haystack = pane_text.lower()
    for needle, label in _BLOCKING_PROMPTS:
        if needle in haystack:
            return label
    return ""


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


    def create_pane(
        self,
        session_name: str,
        runtime: str,
        cwd: str,
        *,
        timeout: float = 10.0,
    ) -> CreateOutcome:
        """Start one known agent in a fresh detached session.

        The pane id comes from tmux itself rather than a list-panes scan, so a
        session someone else creates in the same moment can never be mistaken
        for this one.

        Creation and confirmation are reported separately. tmux returns a pane
        id the instant the session exists, which is before the agent has
        execed — and if the binary is missing the pane survives with the command
        already dead. So the process tree is polled until it agrees, and a
        timeout is reported as an unconfirmed runtime rather than a failure:
        the session really does exist and the caller must be told about it, or
        it becomes an orphan nobody knows to clean up.
        """
        try:
            target_id = new_agent_session(session_name, runtime, cwd)
        except TmuxError as error:
            raise BackendError(str(error)) from None

        deadline = time.monotonic() + timeout
        observed = "unknown"
        while time.monotonic() < deadline:
            try:
                observed = observe_runtime(target_id)
            except TmuxError as error:
                raise BackendError(str(error)) from None
            if observed == runtime:
                break
            time.sleep(min(0.3, max(0.0, deadline - time.monotonic())))

        # A confirmed runtime is not a ready agent. Look for a blocking prompt
        # before anyone sends work into what is actually a dialog box.
        try:
            blocked_on = detect_blocking_prompt(capture_pane(target_id, 60))
        except TmuxError:
            blocked_on = ""

        return CreateOutcome(
            target_id=target_id,
            created=True,
            runtime_requested=runtime,
            runtime_observed=observed,
            session_name=session_name,
            cwd=cwd,
            reason="" if observed == runtime else "runtime_not_observed",
            blocked_on=blocked_on,
        )

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

            # Refuse rather than type into a dialog. This is not only a receipt
            # concern: a blocking prompt is usually a menu, so text followed by
            # Enter can SELECT one of its options. On the first live run the
            # pane was sitting on "1. Yes, I trust this folder / 2. No, exit"
            # and the send went straight into it.
            #
            # tmux cannot enforce an empty prompt, which is why
            # empty_prompt_check is declared False - but declining a state we
            # can positively recognise is strictly better than writing blind.
            blocking = detect_blocking_prompt(before)
            if blocking:
                return SendOutcome(
                    phase=f"rejected_{blocking}",
                    dispatched=False,
                    runtime=self._runtime_of(target_id),
                    reason=blocking,
                )

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
