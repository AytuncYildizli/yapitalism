"""A refusal may only offer a way out that has not already failed.

Found by driving 0.2.5 through its MCP surface against a wedged codex pane:

    pane_send  -> "Gönderilmedi: ... İstersen temizleyip tekrar deneyebilirim."
    pane_clear -> "Tuşu gönderdim ama terminalde hiçbir şey değişmedi."
    pane_clear --action clear-line -> the same
    pane_send  -> the SAME sentence, offering the same clear again

Every one of those sentences is true. In sequence they are a loop, and a voice
operator has no other way out of it — which makes the honesty worthless. The tool
knew the clear had done nothing and said nothing about it.

This is the same failure as advising `pane_clear` for a Superset host that counts a
placeholder as staged text: a remedy that cannot succeed, offered with confidence.
"""

from __future__ import annotations

import unittest

from yapitalism.mcp.backends.base import AcceptanceOutcome, SendOutcome
from yapitalism.mcp.backends.tmux_backend import TMUX_CAPABILITIES
from yapitalism.mcp.receipt import build_receipt


def refusal(phase: str, *, useless: bool) -> str:
    return build_receipt(
        SendOutcome(phase=phase, dispatched=False, runtime="codex"),
        AcceptanceOutcome(observed=False, attempts=0),
        TMUX_CAPABILITIES,
        clearing_known_useless=useless,
    ).speak


class RefusalOffersSomethingThatWorksTests(unittest.TestCase):
    def test_the_first_refusal_offers_the_clear(self) -> None:
        line = refusal("rejected_prompt_not_empty", useless=False)
        self.assertIn("temizleyip tekrar deneyebilirim", line)

    def test_after_a_clear_that_did_nothing_it_offers_something_else(self) -> None:
        line = refusal("rejected_prompt_not_empty", useless=True)
        self.assertNotIn("temizleyip tekrar deneyebilirim", line)
        self.assertIn("cevap vermiyor", line)
        self.assertIn("makinede", line)

    def test_an_unreadable_prompt_gets_the_same_treatment(self) -> None:
        """The other phase a stuck pane produces, and it had the same loop."""
        first = refusal("rejected_prompt_unreadable", useless=False)
        after = refusal("rejected_prompt_unreadable", useless=True)
        self.assertNotEqual(first, after)
        self.assertIn("makinede", after)

    def test_it_still_leads_with_the_same_word(self) -> None:
        """The two-state rule holds: RED is RED, whatever the way out is."""
        for phase in ("rejected_prompt_not_empty", "rejected_prompt_unreadable"):
            for useless in (False, True):
                with self.subTest(phase=phase, useless=useless):
                    self.assertTrue(
                        refusal(phase, useless=useless).startswith("Gönderilmedi:")
                    )

    def test_no_other_refusal_changes(self) -> None:
        """Only the two phases a clear could plausibly fix are affected.

        A shell pane or a trust dialog is not something clearing was offered for, so
        remembering a failed clear must not rewrite those sentences.
        """
        for phase in ("rejected_not_an_agent", "rejected_trust_prompt", "staged_not_submitted"):
            with self.subTest(phase=phase):
                self.assertEqual(
                    refusal(phase, useless=False), refusal(phase, useless=True)
                )


class ClearMemoryTests(unittest.TestCase):
    """The memory is per pane, bounded, and forgets when a pane recovers."""

    def setUp(self) -> None:
        from yapitalism.mcp import server

        self.server = server
        # Isolated, so one test cannot leave a pane marked stuck for another.
        from yapitalism.tokens import BoundedTokens

        self._saved = server._clears_that_changed_nothing
        server._clears_that_changed_nothing = BoundedTokens(capacity=8)
        self.addCleanup(setattr, server, "_clears_that_changed_nothing", self._saved)

    def test_a_clear_that_moved_nothing_is_remembered(self) -> None:
        self.server._remember_clear_outcome("tmux:%2", changed=False)
        self.assertTrue(self.server._clearing_is_known_useless("tmux:%2"))

    def test_a_pane_that_recovers_is_forgotten(self) -> None:
        """As important as remembering. A pane that starts responding again must
        not be described as stuck for the rest of the process."""
        self.server._remember_clear_outcome("tmux:%2", changed=False)
        self.server._remember_clear_outcome("tmux:%2", changed=True)
        self.assertFalse(self.server._clearing_is_known_useless("tmux:%2"))

    def test_it_is_per_pane(self) -> None:
        self.server._remember_clear_outcome("tmux:%2", changed=False)
        self.assertFalse(self.server._clearing_is_known_useless("tmux:%9"))

    def test_it_is_bounded(self) -> None:
        """An unbounded set keyed on caller-supplied ids is a slow leak."""
        for index in range(50):
            self.server._remember_clear_outcome(f"tmux:%{index}", changed=False)
        self.assertFalse(self.server._clearing_is_known_useless("tmux:%0"))
        self.assertTrue(self.server._clearing_is_known_useless("tmux:%49"))



class ArgvZeroIsTheExecutableTests(unittest.TestCase):
    """A shell must never classify as an agent because of a word in a child's argv.

    `classify_tree` searched every token of every descendant, so a plain shell
    running `python3 -c '...' codex` in the background became `runtime="codex"` —
    and `pane_send` then typed a spoken instruction into a shell and pressed Enter,
    which is command execution. Measured on an isolated tmux socket: the pane
    reported `runtime='codex'` while `pane_current_command` was `zsh`.

    Reported by the pre-announcement council; confirmed before being believed, and
    the replacement rule was chosen by measuring a live agent pane rather than
    reasoning about one.
    """

    def test_a_word_in_a_childs_arguments_is_not_a_runtime(self) -> None:
        from yapitalism.mcp.tmux import classify_tree

        rows = [
            (100, 1, "-zsh"),
            (101, 100, "python3 -c import time; time.sleep(120) codex"),
        ]
        self.assertEqual(classify_tree(rows, 100), "shell")

    def test_a_real_agent_is_still_found_through_the_tree(self) -> None:
        """The measured shape of a live codex pane on 2026-08-18.

        The pane process is `node /opt/homebrew/bin/codex` — argv[0] is `node`, so
        argv[0]-only matching would miss it there. Its child execs the vendored
        binary, whose argv[0] basename IS `codex`, which is what matches. Both rows
        matter: drop the tree walk and real panes stop being recognised.
        """
        from yapitalism.mcp.tmux import classify_tree

        rows = [
            (26815, 1, "node /opt/homebrew/bin/codex"),
            (
                27106,
                26815,
                "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/"
                "@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/codex",
            ),
        ]
        self.assertEqual(classify_tree(rows, 26815), "codex")

    def test_a_path_containing_the_name_is_not_enough(self) -> None:
        """`/home/codex/scripts/run.sh` is a path, not an agent."""
        from yapitalism.mcp.tmux import classify_tree

        rows = [(200, 1, "-bash"), (201, 200, "/home/codex/scripts/run.sh --watch")]
        self.assertEqual(classify_tree(rows, 200), "shell")

if __name__ == "__main__":  # pragma: no cover
    unittest.main()
