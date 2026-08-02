"""Real-tmux send tests on an isolated socket.

Every test runs against `tmux -L yapitalism-tmux-test`, never the operator's
real server, and the session runs `cat` so nothing can execute even if Enter
is sent.
"""

from __future__ import annotations

import os
import subprocess
import time
import unittest

SOCKET = "yapitalism-tmux-test"
SESSION = "yap-send-test"
os.environ["YAPITALISM_TMUX_SOCKET"] = SOCKET

from yapitalism.mcp.tmux import (  # noqa: E402
    capture_pane,
    list_panes,
    send_enter,
    send_literal,
)
from yapitalism.mcp.revision import RevisionTracker  # noqa: E402
from yapitalism.canary import normalize_terminal_text  # noqa: E402


def tmux(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["tmux", "-L", SOCKET, *args], capture_output=True, text=True, check=False
    )


class TmuxSendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if subprocess.run(["which", "tmux"], capture_output=True).returncode != 0:
            raise unittest.SkipTest("tmux is not installed")
        tmux("kill-server")
        # `cat` echoes typed text but can never execute it.
        tmux("new-session", "-d", "-s", SESSION, "-x", "80", "-y", "24", "cat")
        panes = [p for p in list_panes() if p.session_name == SESSION]
        if not panes:
            raise unittest.SkipTest("could not create an isolated tmux session")
        cls.pane = panes[0].target_id

    @classmethod
    def tearDownClass(cls) -> None:
        # Explicit -L: only ever kills the isolated test server.
        tmux("kill-server")

    def settle(self) -> None:
        time.sleep(0.2)

    def reset(self) -> None:
        tmux("send-keys", "-t", self.pane.removeprefix("tmux:"), "-X", "cancel")
        tmux("clear-history", "-t", self.pane.removeprefix("tmux:"))

    def assertPaneContains(self, needle: str) -> None:
        """Compare the way the acceptance path does.

        A pane hard-wraps at its width, so a raw `in` check produces a false
        negative the moment a word straddles the boundary — which is exactly
        what `normalize_terminal_text` removes by stripping ANSI and collapsing
        whitespace before matching.
        """
        haystack = normalize_terminal_text(capture_pane(self.pane, 20))
        self.assertIn(normalize_terminal_text(needle), haystack)

    def test_turkish_characters_round_trip_byte_exact(self) -> None:
        """The gate for the whole send path.

        `send-keys -l` with multi-byte UTF-8 under bracketed paste is the most
        likely silent corruption, and a mangled prompt reaching a coding agent
        is worse than a rejected one.
        """
        turkish = "ısşçöüğİŞÇÖÜĞ tamamlandı mı"
        self.reset()
        send_literal(self.pane, turkish)
        self.settle()
        self.assertPaneContains(turkish)

    def test_each_turkish_character_survives_individually(self) -> None:
        for character in "ışçöüğİŞÇÖÜĞâîû":
            with self.subTest(character=character):
                marker = f"X{character}X"
                self.reset()
                send_literal(self.pane, marker)
                self.settle()
                self.assertPaneContains(marker)

    def test_text_starting_with_a_dash_is_data_not_a_flag(self) -> None:
        self.reset()
        send_literal(self.pane, " --expect-revision 130")
        self.settle()
        self.assertPaneContains("--expect-revision 130")

    def test_shell_metacharacters_are_not_interpreted(self) -> None:
        payload = " $(echo pwned) `id` ; rm -rf / && echo no"
        self.reset()
        send_literal(self.pane, payload)
        self.settle()
        self.assertPaneContains("$(echo pwned)")
        self.assertNotIn("uid=", capture_pane(self.pane, 20))

    def test_enter_submits_without_altering_the_text(self) -> None:
        payload = "satır sonu testi"
        self.reset()
        send_literal(self.pane, payload)
        send_enter(self.pane)
        self.settle()
        self.assertPaneContains(payload)

    def test_the_operators_real_tmux_server_is_untouched(self) -> None:
        # The isolated socket must not be the default one.
        self.assertEqual(os.environ["YAPITALISM_TMUX_SOCKET"], SOCKET)
        sessions = {p.session_name for p in list_panes()}
        self.assertEqual(sessions, {SESSION})


class RevisionTrackerTests(unittest.TestCase):
    """tmux has no revision, so one is derived from content."""

    def test_first_observation_is_one(self) -> None:
        self.assertEqual(RevisionTracker().observe("%1", "hello"), 1)

    def test_identical_content_does_not_advance(self) -> None:
        tracker = RevisionTracker()
        tracker.observe("%1", "hello")
        self.assertEqual(tracker.observe("%1", "hello"), 1)

    def test_changed_content_advances(self) -> None:
        tracker = RevisionTracker()
        tracker.observe("%1", "hello")
        self.assertEqual(tracker.observe("%1", "hello world"), 2)

    def test_reverting_still_advances(self) -> None:
        # Otherwise an expected-revision check could pass against stale state.
        tracker = RevisionTracker()
        tracker.observe("%1", "a")
        tracker.observe("%1", "b")
        self.assertEqual(tracker.observe("%1", "a"), 3)

    def test_panes_are_tracked_independently(self) -> None:
        tracker = RevisionTracker()
        tracker.observe("%1", "a")
        tracker.observe("%1", "b")
        self.assertEqual(tracker.observe("%2", "z"), 1)


if __name__ == "__main__":
    unittest.main()
