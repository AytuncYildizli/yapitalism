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

    def test_a_dialog_is_recognised_when_it_owns_the_input_line(self) -> None:
        """A dialog REPLACES the composer, so it reads as text, and the table names it.

        The naming matters: `rejected_trust_prompt` tells the operator a human
        decision is waiting, while `rejected_prompt_not_empty` sends them to
        `pane_clear`, which cannot answer a dialog.
        """
        self.as_codex()
        send_literal(self.target, "Do you trust the contents of this directory?")
        outcome = self.backend.send(
            self.target, "run the tests", canary=None, client_token="trust-1"
        )
        self.assertEqual(outcome.phase, "rejected_trust_prompt")

    def test_a_dialog_scrolled_off_the_screen_no_longer_blocks(self) -> None:
        """The bug that made the whole tmux path unusable.

        `send` captures 1000 lines for revision tracking, and the dialog table used
        to match anywhere in them — so a pane that had ever shown a trust prompt was
        refused for the rest of its life, and `pane_clear` could not help because the
        text sat in scrollback rather than in the prompt. Every pane `panes_create`
        makes shows one, so create-then-send never worked.

        Asserted on the pure function rather than through a `cat` pane: the rule is
        about which region of a capture is "now", and a fixture that has to reproduce
        a TUI's redraw to express that tests the fixture.
        """
        from yapitalism.mcp.backends.tmux_backend import detect_blocking_prompt

        dialog = "Do you trust the contents of this directory?"
        scrolled = dialog + "\n" + "\n".join(f"output {i}" for i in range(60))
        self.assertEqual(detect_blocking_prompt(scrolled), "")
        # Still on screen, still blocking.
        self.assertEqual(detect_blocking_prompt(dialog + "\noutput"), "trust_prompt")

    def test_a_shell_pane_showing_a_dialog_is_still_refused_as_a_shell(self) -> None:
        """The gate is a boundary; the dialog table is a naming heuristic.

        Both were covered alone, and the interaction between them was not — which is
        how an ordering that put the keyword match first passed 286 tests. A pane
        running a plain shell whose screen holds a dialog phrase (an agent that just
        exited, a catted log) was reported as `rejected_trust_prompt`: the operator
        was sent to answer a dialog that is not there, and the refusal that means
        "writing here runs a command" never reached them.

        The send was refused either way. What was wrong was which refusal, and that
        a boundary had become a side effect of a keyword match.
        """
        from unittest.mock import patch

        from yapitalism.mcp.backends import tmux_backend as tb

        screen = "Do you trust the contents of this directory?\n1. Yes\n2. No"
        with patch.object(tb.TmuxBackend, "_runtime_of", return_value="bash"), patch.object(
            tb, "capture_pane", return_value=screen
        ):
            outcome = tb.TmuxBackend().send(
                "tmux:%1", "hello", canary=None, client_token="shell-dialog"
            )
        self.assertEqual(outcome.phase, "rejected_not_an_agent")
        self.assertFalse(outcome.dispatched)

    def test_the_runtime_is_observed_once_per_send(self) -> None:
        """Three readings of a changing pane are three different panes.

        Each `_runtime_of` is a `tmux list-panes -a` plus a `ps -A`. Calling it per
        decision inside the held lock meant the value that picked the prompt markers,
        the value the gate admitted, and the value in the receipt were independent
        observations — so a receipt could name a runtime the guards never saw.
        """
        from unittest.mock import patch

        from yapitalism.mcp.backends import tmux_backend as tb

        calls = []

        def counting(self, target_id):  # noqa: ANN001
            calls.append(target_id)
            return "codex"

        idle = "earlier output\n› Use /skills to list available skills"
        with patch.object(tb.TmuxBackend, "_runtime_of", counting), patch.object(
            tb, "capture_pane", return_value=idle
        ), patch.object(tb, "send_literal"), patch.object(tb, "send_enter"), patch.object(
            tb, "time"
        ):
            outcome = tb.TmuxBackend().send(
                "tmux:%1", "hello", canary="X", client_token="once"
            )
        self.assertTrue(outcome.dispatched)
        self.assertEqual(len(calls), 1)

    def test_the_dialog_window_survives_a_pane_padded_with_blank_rows(self) -> None:
        """A capture is padded with the pane's empty rows.

        In a pane whose content sits at the top, a tail window that counts raw lines
        lands entirely in that padding and sees nothing — so the check would silently
        stop working on exactly the panes it was written for.
        """
        from yapitalism.mcp.backends.tmux_backend import detect_blocking_prompt

        padded = "Do you trust the contents of this directory?" + "\n" * 200
        self.assertEqual(detect_blocking_prompt(padded), "trust_prompt")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
