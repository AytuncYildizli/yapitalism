"""Clearing a blocked prompt on Superset.

The interesting assertions here are about what CANNOT happen. `terminal.writeInput`
is the one host procedure with no revision guard, no client token and no
empty-prompt check, so every protection for it lives on this side and is only as
good as these tests.
"""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

from yapitalism.adapters.superset import (
    _CLEAR_SEQUENCES,
    SupersetAdapter,
    SupersetConfig,
)
from yapitalism.mcp.tmux import CLEAR_ACTIONS

from test_superset_adapter import FakeTrpcServer, result, snapshot_result


def config(endpoint: str) -> SupersetConfig:
    with patch("socket.create_connection"):
        return SupersetConfig(
            endpoint=endpoint,
            bearer_token="token-for-tests",
            workspace_id="workspace-1",
            terminal_id="terminal-1",
            timeout_seconds=2.0,
        )


class ClearPromptTest(unittest.TestCase):
    def setUp(self) -> None:
        # The redraw wait is real behaviour but there is nothing to wait for
        # against a fake server.
        patcher = patch("yapitalism.adapters.superset.time.sleep")
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)

    def test_sends_the_control_byte_not_the_action_name(self) -> None:
        """The whole point: `clear-line` must reach the PTY as 0x15.

        If this ever regressed to sending the literal string "clear-line", the
        agent would receive typed text instead of a kill-line — which is exactly
        the failure the tool exists to fix, now caused by the tool.
        """

        def responder(method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
            if path.endswith("terminal.snapshot"):
                return snapshot_result(text="half-typed thought")
            self.assertEqual(method, "POST")
            self.assertEqual(path, "/trpc/terminal.writeInput")
            self.assertEqual(payload["data"], "\x15")
            self.assertEqual(payload["terminalId"], "terminal-1")
            self.assertEqual(payload["workspaceId"], "workspace-1")
            return result({"success": True})

        with FakeTrpcServer(responder) as server:
            outcome = SupersetAdapter(config(server.endpoint)).clear_prompt("clear-line")
        self.assertEqual(outcome.action, "clear-line")

    def test_escape_and_escape_twice_send_the_documented_bytes(self) -> None:
        for action, expected in (("escape", "\x1b"), ("escape-twice", "\x1b\x1b")):
            written: list[str] = []

            def responder(
                method: str, path: str, payload: dict[str, Any]
            ) -> dict[str, Any]:
                if path.endswith("terminal.snapshot"):
                    return snapshot_result()
                written.append(payload["data"])
                return result({"success": True})

            with self.subTest(action=action), FakeTrpcServer(responder) as server:
                SupersetAdapter(config(server.endpoint)).clear_prompt(action)
                self.assertEqual(written, [expected])

    def test_unknown_action_is_refused_before_any_request(self) -> None:
        """A rejected action must not reach the network at all.

        Asserting only on the exception would pass even if the write had already
        happened, so the request log is what actually proves it.
        """

        def responder(method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
            return snapshot_result()

        with FakeTrpcServer(responder) as server:
            adapter = SupersetAdapter(config(server.endpoint))
            for action in ("enter", "Enter", "C-c", "rm -rf /", "", "escape;escape"):
                with self.subTest(action=action), self.assertRaises(ValueError) as caught:
                    adapter.clear_prompt(action)
                self.assertIn("unknown clear action", str(caught.exception))
            self.assertEqual(server.requests, [])

    def test_enter_is_not_reachable_by_any_key_in_the_table(self) -> None:
        """Regression guard, mirroring the tmux table.

        Escape cancels, Enter commits. On the machine that motivated this, a
        stray Enter would have picked "1. Update now (runs npm install -g ...)".
        """
        for sequence in _CLEAR_SEQUENCES.values():
            self.assertNotIn("\r", sequence)
            self.assertNotIn("\n", sequence)

    def test_never_claims_the_prompt_is_empty(self) -> None:
        """Even with the text visibly changing, emptiness stays unclaimed.

        The host's prompt detector lives in `terminal.send`; `snapshot` returns
        text and revision only. Reporting `prompt_empty: True` from a diff would
        be reimplementing that detector by eye.
        """
        texts = iter(["text still here", ""])

        def responder(method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
            if path.endswith("terminal.snapshot"):
                return snapshot_result(text=next(texts), revision=11)
            return result({"success": True})

        with FakeTrpcServer(responder) as server:
            outcome = SupersetAdapter(config(server.endpoint)).clear_prompt("clear-line")
        self.assertTrue(outcome.text_changed)
        self.assertIsNone(outcome.prompt_empty)

    def test_reports_both_revisions_since_the_host_checks_neither(self) -> None:
        revisions = iter([4, 9])

        def responder(method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
            if path.endswith("terminal.snapshot"):
                return snapshot_result(revision=next(revisions))
            # writeInput's schema has no expectRevision, so nothing was sent.
            self.assertNotIn("expectRevision", payload)
            self.assertNotIn("clientToken", payload)
            return result({"success": True})

        with FakeTrpcServer(responder) as server:
            outcome = SupersetAdapter(config(server.endpoint)).clear_prompt("escape")
        self.assertEqual((outcome.revision_before, outcome.revision_after), (4, 9))

    def test_waits_for_the_redraw_before_reading_back(self) -> None:
        def responder(method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
            if path.endswith("terminal.snapshot"):
                return snapshot_result()
            return result({"success": True})

        with FakeTrpcServer(responder) as server:
            SupersetAdapter(config(server.endpoint)).clear_prompt("escape")
        self.sleep.assert_called_once()

    def test_action_vocabulary_matches_tmux_exactly(self) -> None:
        """One word per action, whichever backend owns the pane.

        The operator says "clear the line" without knowing which backend a pane
        belongs to, so a name that existed on only one side would be a trap the
        voice layer could not see.
        """
        self.assertEqual(set(_CLEAR_SEQUENCES), set(CLEAR_ACTIONS))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
