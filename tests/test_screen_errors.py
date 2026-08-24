"""The screen names the failure; the receipt must say the name, not a shrug.

Born from the first cross-machine send: the pane said `API Error: 401 ·
Please run /login` for the whole 45-second wait, and the receipt spoke a
generic "terminalde hareket var ama doğrulayamadım". The diagnosis was on
screen, already read, and thrown away.

The wording constraint these tests pin: a marker match is a claim about the
SCREEN, never about the agent — the operator's own message could contain the
words "rate limit". The spoken line therefore quotes what the screen shows.
"""

from __future__ import annotations

import unittest

from yapitalism.mcp.backends.base import (
    AcceptanceOutcome,
    BackendCapabilities,
    SendOutcome,
)
from yapitalism.mcp.receipt import build_receipt
from yapitalism.mcp.server import await_acceptance_patiently
from yapitalism.screen_errors import find_agent_error

from tests.test_patient_acceptance import ScriptedBackend

CAPABILITIES = BackendCapabilities("host", "host", "host", "registry")

_THE_REAL_SCREEN = (
    "❯ Reply with exactly one word: merhaba\n"
    "  ⎿  Please run /login · API Error: 401 "
    '{"type":"error","error":{"type":"authentication_error"}}\n'
)


class FindAgentErrorTests(unittest.TestCase):
    def test_the_401_that_started_this_is_found(self) -> None:
        self.assertEqual(find_agent_error(_THE_REAL_SCREEN), "please run /login")

    def test_matching_is_case_insensitive(self) -> None:
        self.assertEqual(find_agent_error("RATE LIMIT exceeded"), "rate limit")

    def test_a_clean_screen_finds_nothing(self) -> None:
        self.assertEqual(find_agent_error("❯ merhaba\n⏺ merhaba\n"), "")

    def test_deep_scrollback_is_history_not_verdict(self) -> None:
        # An error 40 lines up was recovered from; it must not own the receipt.
        old_error = "api error: 401\n" + "\n".join(f"line {i}" for i in range(40))
        self.assertEqual(find_agent_error(old_error), "")


class PatientWaitReadsTheScreenTests(unittest.TestCase):
    def test_a_timeout_next_to_a_named_failure_carries_the_name(self) -> None:
        backend = ScriptedBackend([_THE_REAL_SCREEN], observe_after=None)
        outcome = await_acceptance_patiently(
            backend, "tmux:%1", "YAPITALISM_ACK_X", idle_timeout=0.3, max_wait=1.0
        )
        self.assertFalse(outcome.observed)
        self.assertEqual(outcome.agent_error, "please run /login")

    def test_a_clean_timeout_carries_nothing(self) -> None:
        backend = ScriptedBackend(["same", "same"], observe_after=None)
        outcome = await_acceptance_patiently(
            backend, "tmux:%1", "YAPITALISM_ACK_X", idle_timeout=0.3, max_wait=1.0
        )
        self.assertEqual(outcome.agent_error, "")

    def test_success_never_carries_an_error(self) -> None:
        # The canary arriving is the verdict; a stale error string in the
        # scrollback must not smear a proven acceptance.
        backend = ScriptedBackend([_THE_REAL_SCREEN], observe_after=1)
        outcome = await_acceptance_patiently(
            backend, "tmux:%1", "YAPITALISM_ACK_X", idle_timeout=0.3, max_wait=1.0
        )
        self.assertTrue(outcome.observed)
        self.assertEqual(outcome.agent_error, "")


class ReceiptSpeaksTheDiagnosisTests(unittest.TestCase):
    def test_yellow_quotes_the_screen_instead_of_shrugging(self) -> None:
        outcome = AcceptanceOutcome(
            False, 3, "canary_timeout_pane_still", False, 45.0, "please run /login"
        )
        receipt = build_receipt(
            SendOutcome(phase="injected", dispatched=True, runtime="claude"),
            outcome,
            CAPABILITIES,
        )
        self.assertEqual(receipt.status, "YELLOW")
        self.assertEqual(receipt.reason, "agent_error_on_screen")
        # Still says SENT first — the write DID land — and still offers
        # to look rather than to resend.
        self.assertTrue(receipt.speak.startswith("Sent"))
        self.assertIn("please run /login", receipt.speak)
        self.assertIn("Want me to look?", receipt.speak)

    def test_the_diagnosis_outranks_the_movement_shrug(self) -> None:
        # A retry loop moves the pane forever; "hareket var" was the exact
        # sentence that hid tonight's 401.
        outcome = AcceptanceOutcome(
            False, 3, "canary_timeout_pane_moving", True, 45.0, "api error: 401"
        )
        receipt = build_receipt(
            SendOutcome(phase="injected", dispatched=True, runtime="claude"),
            outcome,
            CAPABILITIES,
        )
        self.assertEqual(receipt.reason, "agent_error_on_screen")
        self.assertIn("api error: 401", receipt.speak)

    def test_a_clean_timeout_still_speaks_the_old_way(self) -> None:
        outcome = AcceptanceOutcome(False, 3, "canary_timeout_pane_moving", True, 45.0)
        receipt = build_receipt(
            SendOutcome(phase="injected", dispatched=True, runtime="claude"),
            outcome,
            CAPABILITIES,
        )
        self.assertEqual(receipt.reason, "canary_timeout_pane_moving")


if __name__ == "__main__":
    unittest.main()
