"""The second receipt: how the TURN ended, never whether the work is right.

The expensive fleet failure is an agent that has been sitting on a yes/no
question for forty minutes while the operator thought it was working. These
tests pin the vocabulary: `ended` is about the screen going idle, and no
spoken line may round it up to "done" or "complete"; a blocking dialog and a
named failure outrank everything; a pane whose agent died is `exited`, not a
celebrated idle prompt.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from yapitalism.mcp import server
from yapitalism.turn import DIALOG, ERROR, PROMPT_EMPTY, TurnReceipt, glance

_EMPTY_CLAUDE = "some transcript\n\n❯ \n  ? for shortcuts\n"
_BUSY_CLAUDE = "✳ Scampering… (1m 2s)\n\n❯ \n  esc to interrupt\n"
_TRUST_DIALOG = (
    "Quick safety check: Is this a project you created or one you trust?\n"
    "❯ 1. Yes, I trust this folder\n  2. No, exit\n"
)
_LOGIN_ERROR = "  ⎿  Please run /login · API Error: 401\n❯ \n"


class GlanceTests(unittest.TestCase):
    def test_a_dialog_outranks_everything(self) -> None:
        state, detail = glance(_TRUST_DIALOG + _LOGIN_ERROR, "claude")
        self.assertEqual(state, DIALOG)
        self.assertTrue(detail)

    def test_a_named_failure_outranks_an_empty_prompt(self) -> None:
        state, detail = glance(_LOGIN_ERROR, "claude")
        self.assertEqual(state, ERROR)
        self.assertEqual(detail, "please run /login")

    def test_an_empty_prompt_reads_as_empty(self) -> None:
        state, _ = glance(_EMPTY_CLAUDE, "claude")
        self.assertEqual(state, PROMPT_EMPTY)


class SpokenTurnTests(unittest.TestCase):
    def test_ended_is_never_spoken_as_done(self) -> None:
        spoken = TurnReceipt("tmux:%1", "claude", "ended", "", 42.0).speak()
        self.assertIn("turn ended", spoken)
        self.assertIn("not that the work is correct", spoken)
        for forbidden in ("done", "complete", "finished", "success"):
            self.assertNotIn(forbidden, spoken.lower())

    def test_waiting_input_names_the_blocker(self) -> None:
        spoken = TurnReceipt(
            "tmux:%1", "claude", "waiting_input", "trust_prompt", 3.0
        ).speak()
        self.assertIn("waiting on you", spoken)
        self.assertIn("trust_prompt", spoken)

    def test_running_offers_to_look_and_claims_nothing(self) -> None:
        spoken = TurnReceipt("tmux:%1", "codex", "running", "", 120.0).speak()
        self.assertIn("Still going", spoken)
        self.assertNotIn("working", spoken)


class _ScriptedPaneBackend:
    """A backend whose screen and pane table are both scripted."""

    def __init__(self, screens: list[str], runtime: str = "claude"):
        self._screens = list(screens)
        self.runtime = runtime
        self.dead = False

    def list_panes(self):
        return [
            SimpleNamespace(target_id="tmux:%9", runtime=self.runtime, dead=self.dead)
        ]

    def read_pane(self, target_id: str, lines: int) -> str:
        if len(self._screens) > 1:
            return self._screens.pop(0)
        return self._screens[0]


class AwaitTurnTests(unittest.TestCase):
    def setUp(self) -> None:
        self._poll = server.TURN_POLL_SECONDS
        server.TURN_POLL_SECONDS = 0.05
        self._registry = server.registry

    def tearDown(self) -> None:
        server.TURN_POLL_SECONDS = self._poll
        server.registry = self._registry

    def _install(self, backend) -> None:
        server.registry = SimpleNamespace(resolve=lambda target_id: backend)

    def test_a_stable_empty_prompt_ends_the_turn(self) -> None:
        self._install(_ScriptedPaneBackend([_BUSY_CLAUDE, _EMPTY_CLAUDE]))
        result = server._await_turn("tmux:%9", idle_seconds=0.1, timeout_seconds=30.0)
        self.assertEqual(result["turn"], "ended")
        self.assertTrue(result["ok"])
        self.assertIn("tail", result)

    def test_a_dialog_is_reported_immediately_as_waiting(self) -> None:
        self._install(_ScriptedPaneBackend([_TRUST_DIALOG]))
        result = server._await_turn("tmux:%9", idle_seconds=0.1, timeout_seconds=30.0)
        self.assertEqual(result["turn"], "waiting_input")
        self.assertIn("waiting on you", result["speak"])

    def test_a_named_failure_is_reported_not_timed_out(self) -> None:
        self._install(_ScriptedPaneBackend([_LOGIN_ERROR]))
        result = server._await_turn("tmux:%9", idle_seconds=0.1, timeout_seconds=30.0)
        self.assertEqual(result["turn"], "agent_error")
        self.assertEqual(result["detail"], "please run /login")

    def test_an_agent_that_becomes_a_shell_is_exited_not_ended(self) -> None:
        backend = _ScriptedPaneBackend([_BUSY_CLAUDE, _EMPTY_CLAUDE])
        self._install(backend)
        backend.runtime = "shell"
        result = server._await_turn("tmux:%9", idle_seconds=0.1, timeout_seconds=30.0)
        self.assertEqual(result["turn"], "exited")
        for forbidden in ("ended", "done"):
            self.assertNotIn(forbidden, result["speak"].lower())

    def test_a_forever_changing_pane_times_out_as_running(self) -> None:
        screens = [f"line {i}\n✳ thinking\n" for i in range(400)]
        self._install(_ScriptedPaneBackend(screens))
        result = server._await_turn("tmux:%9", idle_seconds=5.0, timeout_seconds=0.5)
        self.assertEqual(result["turn"], "running")
        self.assertIn("Still going", result["speak"])

    def test_ended_reverifies_the_agent_before_claiming(self) -> None:
        # The screen looks idle, but the agent died between polls: the frozen
        # prompt of a dead pane must not be celebrated as an ended turn.
        backend = _ScriptedPaneBackend([_EMPTY_CLAUDE])
        self._install(backend)
        original = backend.list_panes
        calls = {"n": 0}

        def flaky_list():
            calls["n"] += 1
            if calls["n"] > 1:
                backend.dead = True
            return original()

        backend.list_panes = flaky_list
        result = server._await_turn("tmux:%9", idle_seconds=0.1, timeout_seconds=30.0)
        self.assertEqual(result["turn"], "exited")


if __name__ == "__main__":
    unittest.main()
