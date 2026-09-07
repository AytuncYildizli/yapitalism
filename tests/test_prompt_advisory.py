"""Claude Code's suggested prompt is not a draft — and a real draft still is.

Observed live 2026-09-06: Claude Code renders a SUGGESTED next prompt on its
input line ("❯ devam et, paralel kısımları yap"), indistinguishable in a
snapshot from text a person typed, and both backends refused every send to that
pane — stopping the operator on every turn. The operator's decision: for Claude
Code, text in the composer is advisory. The send proceeds and the receipt says
the prompt held text, so if it WAS a draft the operator hears that instead of a
clean "got it". Every other runtime keeps the refusal, because there a non-empty
composer really is somebody's draft. Dialogs and unreadable screens stay
refused for everyone.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from yapitalism.adapters.superset import DispatchResult
from yapitalism.mcp.backends import superset_backend as sb
from yapitalism.mcp.backends import tmux_backend as tb
from yapitalism.mcp.backends.base import AcceptanceOutcome, SendOutcome
from yapitalism.mcp.receipt import build_receipt

_SUGGESTION_SCREEN = "some transcript\n\n❯ devam et, paralel kısımları yap\n  ? for shortcuts\n"
_CODEX_DRAFT_SCREEN = "earlier output\n› fix the flaky auth test and"
_FP = (4242, "claude", "Mon Jan  1 00:00:00 2026")


class TmuxAdvisoryTests(unittest.TestCase):
    def _send(self, runtime: str, screen: str, after: str | None = None):
        enters: list[str] = []
        fp = (4242, runtime, "Mon Jan  1 00:00:00 2026")
        # The screen before the write, then whatever the pane shows after Enter.
        screens = iter([screen] + [after if after is not None else screen] * 10)
        with patch.object(tb.TmuxBackend, "_runtime_of", return_value=runtime), patch.object(
            tb.TmuxBackend, "_fingerprint_of", return_value=fp
        ), patch.object(tb, "capture_pane", lambda *a, **k: next(screens)), patch.object(
            tb, "send_literal"
        ), patch.object(tb, "send_enter", lambda t: enters.append(t)), patch.object(
            tb, "time"
        ):
            outcome = tb.TmuxBackend().send(
                "tmux:%1", "hello", canary="X", client_token=f"adv-{runtime}-{len(screen)}"
            )
        return outcome, enters

    def test_a_claude_suggestion_is_sent_and_said(self) -> None:
        moved = "some transcript\n\n✳ Thinking…\n❯ \n"
        outcome, enters = self._send("claude", _SUGGESTION_SCREEN, after=moved)
        self.assertTrue(outcome.dispatched)
        self.assertEqual(outcome.reason, "prompt_text_advisory")
        # Exactly one Enter: a second one could submit a fresh suggestion.
        self.assertEqual(len(enters), 1)

    def test_a_still_screen_after_the_advisory_send_is_staged_not_guessed(self) -> None:
        # Movement is the only submit proof this runtime allows; none means the
        # message may be sitting there, and the receipt must say so.
        outcome, enters = self._send("claude", _SUGGESTION_SCREEN)
        self.assertFalse(outcome.dispatched)
        self.assertEqual(outcome.phase, "staged_not_submitted")
        self.assertEqual(len(enters), 1)

    def test_a_codex_draft_is_still_refused(self) -> None:
        # The protection this change must NOT remove.
        outcome, enters = self._send("codex", _CODEX_DRAFT_SCREEN)
        self.assertFalse(outcome.dispatched)
        self.assertEqual(outcome.phase, "rejected_prompt_not_empty")
        self.assertEqual(enters, [])

    def test_an_unreadable_claude_screen_is_still_refused(self) -> None:
        # A menu or overlay is not a suggestion; typing there selects things.
        outcome, enters = self._send("claude", "Select an option\n  1. Yes\n  2. No\n")
        self.assertFalse(outcome.dispatched)
        self.assertEqual(outcome.phase, "rejected_prompt_unreadable")
        self.assertEqual(enters, [])


def _result(phase: str, *, dispatched: bool) -> DispatchResult:
    return DispatchResult(
        client_token="t",
        terminal_id="t-1",
        delivery_id="d-1" if dispatched else None,
        phase=phase,
        submit_sent=dispatched,
        duplicate=False,
        revision_before=7,
        expected_revision=7,
        prompt_status="occupied" if not dispatched else "empty",
        target_runtime="claude",
        revision_after=8 if dispatched else None,
    )


class _FakeAdapter:
    def __init__(self, first_phase: str):
        self.dispatches: list[dict] = []
        self._first = first_phase
        self.config = SimpleNamespace(terminal_id="t-1", workspace_id="w-1")

    def registry_runtime(self) -> str:
        return "claude"

    def snapshot(self, max_lines: int = 1000):
        return SimpleNamespace(text=_SUGGESTION_SCREEN, revision=7)

    def validate_canary(self, *args, **kwargs) -> None:
        return None

    def dispatch(self, text, **kwargs):
        self.dispatches.append(kwargs)
        if len(self.dispatches) == 1 and self._first == "rejected_prompt_not_empty":
            return _result("rejected_prompt_not_empty", dispatched=False)
        return _result("injected", dispatched=True)

    def host_enforces_send_guards(self) -> bool:
        return True


class SupersetAdvisoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._state = tempfile.TemporaryDirectory()
        os.environ["XDG_STATE_HOME"] = self._state.name
        self.addCleanup(self._state.cleanup)
        self.addCleanup(lambda: os.environ.pop("XDG_STATE_HOME", None))

    def _send(self, adapter):
        backend = sb.SupersetBackend("/nonexistent/manifest.json")
        with patch.object(sb.SupersetBackend, "_adapter_for", return_value=adapter):
            return backend.send("superset:t-1", "hello", canary=None, client_token="tok-1")

    def test_a_host_prompt_refusal_on_claude_is_redispatched_without_the_requirement(self) -> None:
        adapter = _FakeAdapter("rejected_prompt_not_empty")
        outcome = self._send(adapter)
        self.assertTrue(outcome.dispatched)
        self.assertEqual(outcome.reason, "prompt_text_advisory")
        self.assertEqual(len(adapter.dispatches), 2)
        # First ask the host normally; only its refusal earns the second, narrower ask.
        self.assertNotIn("require_empty_prompt", adapter.dispatches[0])
        self.assertFalse(adapter.dispatches[1]["require_empty_prompt"])
        # Revision and token discipline survive: same revision, fresh token.
        self.assertEqual(adapter.dispatches[1]["expected_revision"], 7)
        self.assertNotEqual(adapter.dispatches[1]["client_token"], "tok-1")

    def test_a_clean_first_dispatch_is_not_touched(self) -> None:
        adapter = _FakeAdapter("injected")
        outcome = self._send(adapter)
        self.assertTrue(outcome.dispatched)
        self.assertEqual(len(adapter.dispatches), 1)
        self.assertNotEqual(outcome.reason, "prompt_text_advisory")


class ReceiptWordingTests(unittest.TestCase):
    def test_the_green_says_the_prompt_held_text(self) -> None:
        receipt = build_receipt(
            SendOutcome(phase="injected", dispatched=True, runtime="claude",
                        reason="prompt_text_advisory"),
            AcceptanceOutcome(True, 1),
            tb.TmuxBackend().capabilities(),
        )
        self.assertEqual(receipt.status, "GREEN")
        self.assertEqual(receipt.reason, "prompt_text_advisory")
        self.assertTrue(receipt.speak.startswith("claude got it."))
        self.assertIn("prompt showed text", receipt.speak)


if __name__ == "__main__":
    unittest.main()
