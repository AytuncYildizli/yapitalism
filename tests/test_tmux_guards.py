"""The guards `TmuxBackend.send` applies before it types anything.

These were added without tests, which was the most serious gap a review of the
capability work found: the checks that decide whether a voice instruction reaches
a terminal at all were verified only by hand, on the backend most strangers will
actually use.

Runs against `tmux -L yapitalism-tmux-guard-test`, never the operator's server,
and the pane runs `cat` so nothing can execute even if Enter is sent. The runtime
is stubbed where a test needs the backend to believe an agent is present — the
alternative is launching a real agent inside a unit test, which would make
execution possible for no extra coverage.
"""

from __future__ import annotations

import os
import subprocess
import time
import unittest

# Deliberately the SAME socket the other tmux suites use. The env var is set at
# import time, so two different values mean whichever module imported last decides
# where BOTH suites send their commands - which passed in isolation and failed
# together, the most misleading way for a test to be wrong.
SOCKET = "yapitalism-tmux-test"
SESSION = "yap-guard-test"
os.environ["YAPITALISM_TMUX_SOCKET"] = SOCKET

from yapitalism.mcp.backends.tmux_backend import TmuxBackend  # noqa: E402
from yapitalism.mcp.tmux import capture_pane, send_literal  # noqa: E402

CODEX_EMPTY = "› Use /skills to list available skills"


def tmux(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["tmux", "-L", SOCKET, *args], capture_output=True, text=True, check=False
    )


class TmuxSendGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if subprocess.run(["which", "tmux"], capture_output=True).returncode != 0:
            raise unittest.SkipTest("tmux is not installed")
        # -L is explicit on every call, so this can never reach the real server.
        tmux("kill-server")
        tmux("new-session", "-d", "-s", SESSION, "-x", "200", "-y", "50", "--", "cat")

    @classmethod
    def tearDownClass(cls) -> None:
        tmux("kill-server")

    def setUp(self) -> None:
        self.backend = TmuxBackend()
        self.target = "tmux:%0"
        # A genuinely blank pane, not a cleared one. `cat` echoes everything, and
        # clear-history leaves the visible screen alone - so without respawning,
        # one test's typed text is still on screen for the next, which made an
        # "unreadable screen" test find a prompt line from three tests ago.
        tmux("respawn-pane", "-k", "-t", "%0", "--", "cat")
        time.sleep(0.2)

    def as_codex(self) -> None:
        """Make the backend believe a codex agent owns the pane.

        The pane really runs `cat`. Only the runtime lookup is stubbed, so the
        guards under test see what they would see in production while the pane
        stays incapable of executing anything.
        """
        self.backend._runtime_of = lambda target_id: "codex"  # type: ignore[method-assign]

    def screen(self) -> str:
        return capture_pane(self.target, 50)

    def test_a_pane_running_a_shell_is_refused_before_anything_is_typed(self) -> None:
        """Typing into a shell and pressing Enter IS running a command.

        `classify_tree` was written because a pane that used to run an agent and
        now runs a plain shell must never be treated as an agent target — and then
        nothing on the send path checked. Reachable by voice.
        """
        before = self.screen()
        outcome = self.backend.send(
            self.target, "echo owned", canary=None, client_token="shell-1"
        )
        self.assertEqual(outcome.phase, "rejected_not_an_agent")
        self.assertFalse(outcome.dispatched)
        self.assertEqual(self.screen(), before)

    def test_an_occupied_prompt_is_refused_and_the_text_is_left_alone(self) -> None:
        """send-keys appends; it does not replace.

        Without this guard the staged text and the instruction were submitted
        together as one corrupted message.
        """
        self.as_codex()
        send_literal(self.target, "› half a typed thought")
        outcome = self.backend.send(
            self.target, "run the tests", canary=None, client_token="occupied-1"
        )
        self.assertEqual(outcome.phase, "rejected_prompt_not_empty")
        self.assertFalse(outcome.dispatched)
        screen = self.screen()
        self.assertIn("half a typed thought", screen)
        self.assertNotIn("run the tests", screen)

    def test_an_unreadable_screen_is_refused_too(self) -> None:
        """No input line to judge — a menu, an overlay, a full-screen diff.

        Proceeding on "I could not tell" is exactly where the appending happens.
        """
        self.as_codex()
        send_literal(self.target, "a full-screen menu with no prompt line")
        outcome = self.backend.send(
            self.target, "run the tests", canary=None, client_token="unreadable-1"
        )
        self.assertEqual(outcome.phase, "rejected_prompt_unreadable")
        self.assertNotIn("run the tests", self.screen())

    def test_an_empty_codex_prompt_is_written_to(self) -> None:
        """The guards must not refuse everything.

        A test suite where every send is declined would pass while the product did
        nothing, so this pins the permitting case.
        """
        self.as_codex()
        send_literal(self.target, CODEX_EMPTY)
        outcome = self.backend.send(
            self.target, "run the tests", canary=None, client_token="ok-1"
        )
        self.assertEqual(outcome.phase, "injected")
        self.assertTrue(outcome.dispatched)
        self.assertIn("run the tests", self.screen())

    def test_a_replayed_token_is_refused_without_typing_again(self) -> None:
        self.as_codex()
        send_literal(self.target, CODEX_EMPTY)
        first = self.backend.send(
            self.target, "once only", canary=None, client_token="dup-1"
        )
        self.assertEqual(first.phase, "injected")
        # A clean screen for the replay. `cat` echoes, so the first send's text is
        # still on a marker line, and the prompt check now judges EVERY marker line
        # rather than only the bottom-most one - correctly refusing before the token
        # check is ever reached. Respawning isolates what this test is about.
        tmux("respawn-pane", "-k", "-t", "%0", "--", "cat")
        time.sleep(0.2)
        send_literal(self.target, CODEX_EMPTY)
        before = self.screen()
        second = self.backend.send(
            self.target, "once only", canary=None, client_token="dup-1"
        )
        self.assertEqual(second.phase, "duplicate_ignored")
        self.assertFalse(second.dispatched)
        self.assertEqual(self.screen(), before)

    def test_a_token_burned_by_an_ambiguous_write_is_not_called_a_duplicate(self) -> None:
        """"Duplicate" asserts the first attempt arrived. After an ambiguous
        failure nobody knows that, and saying it would claim a delivery that may
        never have happened."""
        self.as_codex()
        send_literal(self.target, CODEX_EMPTY)
        # The state a write that raised mid-flight leaves behind.
        self.backend._landed_tokens.add("ambig-1")
        self.backend._ambiguous_tokens.add("ambig-1")
        outcome = self.backend.send(
            self.target, "did it arrive?", canary=None, client_token="ambig-1"
        )
        self.assertEqual(outcome.phase, "duplicate_after_ambiguous_write")
        self.assertFalse(outcome.dispatched)
        self.assertNotIn("did it arrive?", self.screen())

    def test_a_recognised_dialog_still_wins_over_the_prompt_check(self) -> None:
        """Order matters: the dialog table is more specific than "has text".

        A trust prompt reported as `rejected_prompt_not_empty` would send the
        operator to `pane_clear` instead of telling them a human decision is
        waiting.
        """
        self.as_codex()
        send_literal(self.target, "Do you trust the contents of this directory?")
        outcome = self.backend.send(
            self.target, "run the tests", canary=None, client_token="trust-1"
        )
        self.assertEqual(outcome.phase, "rejected_trust_prompt")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
