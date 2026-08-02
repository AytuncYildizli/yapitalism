"""Waiting for proof for as long as the agent looks alive.

The point of the patient wait is to stop reporting on the clock instead of on
the agent: an agent that thinks for a minute and then answers was verified all
along, and calling that YELLOW teaches an operator to ignore YELLOW.

The thing these tests must pin down is that widening the window does NOT widen
what counts as proof. Pane movement decides whether to keep waiting. Only the
canary decides acceptance.
"""

from __future__ import annotations

import unittest

from yapitalism.mcp.backends.base import (
    AcceptanceOutcome,
    BackendCapabilities,
    BackendError,
    SendOutcome,
)
from yapitalism.mcp.receipt import build_receipt
from yapitalism.mcp.server import await_acceptance_patiently

CAPABILITIES = BackendCapabilities(True, True, True, "registry")


class ScriptedBackend:
    """A backend whose pane text and canary arrival are both scripted.

    `pane` yields what each read returns; `observe_after` is the number of
    acceptance polls before the canary shows up (None: never).
    """

    def __init__(self, pane: list[str], observe_after: int | None = None):
        self._pane = list(pane)
        self._observe_after = observe_after
        self.acceptance_calls = 0
        self.read_calls = 0

    def await_acceptance(
        self, target_id: str, canary: str | None, *, timeout: float = 8.0
    ) -> AcceptanceOutcome:
        self.acceptance_calls += 1
        if (
            self._observe_after is not None
            and self.acceptance_calls >= self._observe_after
        ):
            return AcceptanceOutcome(True, 1)
        return AcceptanceOutcome(False, 1, "canary_timeout")

    def read_pane(self, target_id: str, lines: int) -> str:
        self.read_calls += 1
        if self._pane:
            return self._pane.pop(0) if len(self._pane) > 1 else self._pane[0]
        # Empty script means "still producing output forever" - a finite list
        # runs dry and then reads as an idle pane, which is a different case.
        return f"line-{self.read_calls}"


class PatientWaitTests(unittest.TestCase):
    def test_a_slow_agent_is_verified_rather_than_timed_out(self) -> None:
        """The false negative this exists to remove."""
        # Pane keeps changing, so the wait keeps extending; the canary finally
        # arrives on the fourth poll.
        backend = ScriptedBackend(["a", "b", "c", "d", "e"], observe_after=4)
        outcome = await_acceptance_patiently(
            backend, "tmux:%1", "YAPITALISM_ACK_X", idle_timeout=5.0, max_wait=30.0
        )
        self.assertTrue(outcome.observed)
        self.assertFalse(outcome.pane_changed_recently)
        self.assertEqual(outcome.reason, "")

    def test_a_silent_pane_still_gives_up(self) -> None:
        # Nothing ever changes, so the idle timeout is never extended.
        backend = ScriptedBackend(["same", "same"], observe_after=None)
        outcome = await_acceptance_patiently(
            backend, "tmux:%1", "YAPITALISM_ACK_X", idle_timeout=0.4, max_wait=5.0
        )
        self.assertFalse(outcome.observed)
        self.assertFalse(outcome.pane_changed_recently)
        self.assertEqual(outcome.reason, "canary_timeout_pane_still")

    def test_the_hard_ceiling_bounds_a_pane_that_never_stops_moving(self) -> None:
        # A pane that changes forever must not hold the turn open forever.
        backend = ScriptedBackend([], observe_after=None)
        outcome = await_acceptance_patiently(
            backend, "tmux:%1", "YAPITALISM_ACK_X", idle_timeout=5.0, max_wait=1.0
        )
        self.assertFalse(outcome.observed)
        self.assertLess(outcome.waited_seconds, 4.0)
        self.assertEqual(outcome.reason, "canary_timeout_pane_moving")

    def test_movement_alone_is_never_acceptance(self) -> None:
        """The guarantee widening the window must not break.

        A pane can change for hundreds of polls. Without the canary it is still
        not accepted, no matter how alive it looks.
        """
        backend = ScriptedBackend([], observe_after=None)
        outcome = await_acceptance_patiently(
            backend, "tmux:%1", "YAPITALISM_ACK_X", idle_timeout=5.0, max_wait=1.0
        )
        self.assertFalse(outcome.observed)
        self.assertTrue(outcome.pane_changed_recently)
        receipt = build_receipt(
            SendOutcome(phase="injected", dispatched=True, runtime="codex"),
            outcome,
            CAPABILITIES,
        )
        self.assertEqual(receipt.status, "YELLOW")
        self.assertFalse(receipt.accepted)

    def test_no_canary_is_still_not_testable(self) -> None:
        backend = ScriptedBackend(["a"], observe_after=1)
        outcome = await_acceptance_patiently(backend, "tmux:%1", None)
        self.assertFalse(outcome.observed)
        self.assertEqual(outcome.reason, "no_canary")
        self.assertEqual(backend.acceptance_calls, 0)

    def test_losing_the_ability_to_read_stops_the_wait_without_claiming(self) -> None:
        class Blind(ScriptedBackend):
            def read_pane(self, target_id: str, lines: int) -> str:
                raise BackendError("pane vanished")

        outcome = await_acceptance_patiently(
            Blind(["x"], observe_after=None),
            "tmux:%1",
            "YAPITALISM_ACK_X",
            idle_timeout=5.0,
            max_wait=5.0,
        )
        self.assertFalse(outcome.observed)
        self.assertLess(outcome.waited_seconds, 4.0)


class SpokenDifferenceTests(unittest.TestCase):
    """Both are YELLOW; they must not sound the same."""

    def _receipt(self, acceptance: AcceptanceOutcome):
        return build_receipt(
            SendOutcome(phase="injected", dispatched=True, runtime="codex"),
            acceptance,
            CAPABILITIES,
        )

    def test_pane_movement_is_reported_as_pane_movement_not_as_the_agent(self) -> None:
        receipt = self._receipt(
            AcceptanceOutcome(
                False, 3, "canary_timeout_pane_moving", True, 42.0
            )
        )
        self.assertEqual(receipt.status, "YELLOW")
        self.assertIn("Terminalde hareket", receipt.speak)
        # It must NOT claim the agent is working - a spinner moves the pane.
        self.assertNotIn("Ajan hâlâ çalışıyor", receipt.speak)
        self.assertIn("42", receipt.speak)

    def test_a_silent_pane_is_reported_as_no_movement(self) -> None:
        receipt = self._receipt(
            AcceptanceOutcome(False, 3, "canary_timeout_pane_still", False, 8.0)
        )
        self.assertEqual(receipt.status, "YELLOW")
        self.assertIn("hareket", receipt.speak)

    def test_neither_is_ever_spoken_as_done(self) -> None:
        for acceptance in (
            AcceptanceOutcome(False, 1, "canary_timeout_pane_moving", True, 9.0),
            AcceptanceOutcome(False, 1, "canary_timeout_pane_still", False, 9.0),
        ):
            receipt = self._receipt(acceptance)
            self.assertEqual(receipt.status, "YELLOW")
            self.assertTrue(receipt.speak.startswith("SARI"))


if __name__ == "__main__":
    unittest.main()


class BlockingPromptRefusalTests(unittest.TestCase):
    """Refusing to type into a dialog.

    Not only a receipt concern. A blocking prompt is usually a menu, so text
    plus Enter can select one of its options — on the first live run the pane
    was on "1. Yes, I trust this folder / 2. No, exit".
    """

    def _receipt(self, phase: str):
        return build_receipt(
            SendOutcome(phase=phase, dispatched=False, runtime="claude", reason="x"),
            AcceptanceOutcome(False, 0, "not_dispatched"),
            CAPABILITIES,
        )

    def test_a_refused_send_is_red_and_says_nothing_was_written(self) -> None:
        receipt = self._receipt("rejected_trust_prompt")
        self.assertEqual(receipt.status, "RED")
        self.assertFalse(receipt.accepted)
        self.assertIn("hiçbir şey yazmadım", receipt.speak)

    def test_each_blocking_kind_gets_its_own_wording(self) -> None:
        trust = self._receipt("rejected_trust_prompt").speak
        auth = self._receipt("rejected_auth_prompt").speak
        self.assertNotEqual(trust, auth)
        self.assertIn("güven", trust)
        self.assertIn("giriş", auth)

    def test_refusal_never_reads_as_delivered(self) -> None:
        for phase in (
            "rejected_trust_prompt",
            "rejected_auth_prompt",
            "rejected_confirm_prompt",
        ):
            with self.subTest(phase=phase):
                receipt = self._receipt(phase)
                self.assertEqual(receipt.status, "RED")
                self.assertFalse(receipt.accepted)
