"""The identity a send holds while it types.

"The pane's runtime looks like claude" and "the same claude process that was
admitted is still under the text" are different claims. The gate makes the
first; this bracket makes the second, at the only moment it matters — after
typing, before Enter. Typing is recoverable; Enter into a pane where the agent
just died into a shell is command execution.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from yapitalism.mcp.backends import tmux_backend as tb
from yapitalism.mcp.backends.base import AcceptanceOutcome, SendOutcome
from yapitalism.mcp.receipt import build_receipt
from yapitalism.mcp.tmux import agent_process, classify_tree

_IDLE_CODEX = "earlier output\n› Use /skills to list available skills"
_FP_A = (4242, "codex", "Mon Jan  1 00:00:00 2026")
_FP_B = (5151, "codex", "Tue Jan  2 00:00:00 2026")

_ROWS = [
    (100, 1, "tmux"),
    (200, 100, "-zsh"),
    (300, 200, "node /opt/homebrew/bin/codex"),
    (400, 300, "/opt/codex/vendor/aarch64-apple-darwin/codex"),
]


class AgentProcessTests(unittest.TestCase):
    def test_the_fingerprinted_pid_is_the_one_that_classified(self) -> None:
        # Same walk, same argv[0] rule: the pid returned must be the process
        # whose executable name made classify_tree say "codex".
        self.assertEqual(classify_tree(_ROWS, 200), "codex")
        self.assertEqual(agent_process(_ROWS, 200), 400)

    def test_a_shell_pane_has_no_agent_pid(self) -> None:
        self.assertEqual(classify_tree(_ROWS[:2], 200), "shell")
        self.assertIsNone(agent_process(_ROWS[:2], 200))


class BracketTests(unittest.TestCase):
    def _send(self, fingerprints):
        enters: list[str] = []
        with patch.object(tb.TmuxBackend, "_runtime_of", return_value="codex"), patch.object(
            tb.TmuxBackend, "_fingerprint_of", side_effect=fingerprints
        ), patch.object(tb, "capture_pane", return_value=_IDLE_CODEX), patch.object(
            tb, "send_literal"
        ), patch.object(
            tb, "send_enter", lambda target: enters.append(target)
        ), patch.object(tb, "time"):
            outcome = tb.TmuxBackend().send(
                "tmux:%1", "hello", canary="X", client_token=f"fp-{id(fingerprints)}"
            )
        return outcome, enters

    def test_a_stable_identity_lets_the_send_through(self) -> None:
        outcome, enters = self._send([_FP_A, _FP_A])
        self.assertTrue(outcome.dispatched)
        self.assertEqual(len(enters), 1)

    def test_a_changed_identity_stops_the_enter(self) -> None:
        # The pid changed between typing and submit: the text is sitting in
        # whatever owns the pane now, and Enter must never be pressed.
        outcome, enters = self._send([_FP_A, _FP_B])
        self.assertEqual(outcome.phase, "staged_agent_changed")
        self.assertFalse(outcome.dispatched)
        self.assertEqual(enters, [])

    def test_a_vanished_agent_is_refused_before_typing(self) -> None:
        outcome, enters = self._send([None])
        self.assertEqual(outcome.phase, "rejected_not_an_agent")
        self.assertFalse(outcome.dispatched)
        self.assertEqual(enters, [])

    def test_the_refusal_warns_about_the_staged_text(self) -> None:
        receipt = build_receipt(
            SendOutcome(
                phase="staged_agent_changed", dispatched=False, runtime="codex"
            ),
            AcceptanceOutcome(False, 0, "not_dispatched"),
            tb.TmuxBackend().capabilities(),
        )
        self.assertEqual(receipt.status, "RED")
        self.assertTrue(receipt.speak.startswith("Not sent:"))
        self.assertIn("Enter was never pressed", receipt.speak)
        self.assertIn("still sitting there", receipt.speak)


if __name__ == "__main__":
    unittest.main()
