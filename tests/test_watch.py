"""The watcher reports transitions, reads only, and never invents urgency.

This is the wallet-approval catch as a feature: the first production watcher
(an LLM on a cron) found an agent silently holding a wallet-transaction
approval for six hours. The built-in version must catch the same four states
with no model in the loop — and must go quiet once a pane recovers, because a
watcher that repeats itself trains its operator to mute it.
"""

from __future__ import annotations

import unittest

from yapitalism.watch import Finding, Watcher, classify_pane

CODEX_IDLE = "some output\n› Use /skills to list available skills"
CODEX_OCCUPIED = "some output\n› half a typed thought"
TRUST_DIALOG = "Do you trust the contents of this directory?\n1. Yes\n2. No"
OUTAGE = "→ Read src/x.ts\n┃  Service Unavailable\n┃"


def pane(target: str, runtime: str = "codex", folder: str = "relayproof") -> dict:
    return {"target_id": target, "runtime": runtime, "folder": folder}


class ClassifyTests(unittest.TestCase):
    def test_an_idle_agent_is_not_news(self) -> None:
        self.assertIsNone(classify_pane("codex", CODEX_IDLE, was_occupied=False))

    def test_a_dialog_is_blocked(self) -> None:
        kind, detail = classify_pane("codex", TRUST_DIALOG, was_occupied=False)
        self.assertEqual(kind, "blocked")
        self.assertEqual(detail, "trust_prompt")

    def test_provider_failure_text_is_an_outage(self) -> None:
        """Measured 2026-08-18: a 503 in the composer reads as a wedged agent
        from outside, and the operator's first theory was that we broke it."""
        kind, detail = classify_pane("codex", OUTAGE, was_occupied=False)
        self.assertEqual(kind, "outage")
        self.assertEqual(detail, "service unavailable")

    def test_held_text_needs_two_polls(self) -> None:
        """One poll is a person typing; two is a message parked unsent."""
        self.assertIsNone(classify_pane("codex", CODEX_OCCUPIED, was_occupied=False))
        kind, _ = classify_pane("codex", CODEX_OCCUPIED, was_occupied=True)
        self.assertEqual(kind, "occupied")


class TransitionTests(unittest.TestCase):
    def observe(self, watcher: Watcher, screen: str, runtime: str = "codex"):
        return watcher.observe([pane("superset:t1", runtime)], lambda _t: screen)

    def test_a_blocked_pane_is_announced_once(self) -> None:
        watcher = Watcher()
        first = self.observe(watcher, TRUST_DIALOG)
        self.assertEqual([f.kind for f in first], ["blocked"])
        second = self.observe(watcher, TRUST_DIALOG)
        self.assertEqual(second, [], "still-blocked must not repeat")

    def test_recovery_is_announced_and_then_silence(self) -> None:
        watcher = Watcher()
        self.observe(watcher, TRUST_DIALOG)
        recovered = self.observe(watcher, CODEX_IDLE)
        self.assertEqual([f.kind for f in recovered], ["recovered"])
        self.assertEqual(self.observe(watcher, CODEX_IDLE), [])

    def test_an_agent_that_dies_is_reported_as_exited(self) -> None:
        """A pane whose measured runtime went agent -> not-agent is a dead
        agent, not a quiet one — the exit-watch recipe, in code."""
        watcher = Watcher()
        self.observe(watcher, CODEX_IDLE)
        findings = watcher.observe(
            [pane("superset:t1", runtime="unknown")], lambda _t: ""
        )
        self.assertEqual([f.kind for f in findings], ["exited"])
        self.assertEqual(findings[0].runtime, "codex", "names what died, not what remains")

    def test_a_pane_that_was_never_an_agent_is_ignored(self) -> None:
        watcher = Watcher()
        findings = watcher.observe(
            [pane("tmux:%0", runtime="shell")], lambda _t: TRUST_DIALOG
        )
        self.assertEqual(findings, [])

    def test_the_occupied_memory_is_per_pane(self) -> None:
        watcher = Watcher()
        panes = [pane("superset:a"), pane("superset:b")]
        screens = {"superset:a": CODEX_OCCUPIED, "superset:b": CODEX_IDLE}
        watcher.observe(panes, lambda t: screens[t])
        findings = watcher.observe(panes, lambda t: screens[t])
        self.assertEqual([(f.target_id, f.kind) for f in findings],
                         [("superset:a", "occupied")])


class SpokenTests(unittest.TestCase):
    def test_the_notification_is_a_sentence_not_a_payload(self) -> None:
        line = Finding("superset:t1", "claude", "muhabbit", "blocked", "trust_prompt").spoken()
        self.assertIn("muhabbit", line)
        self.assertIn("claude", line)
        self.assertNotIn("superset:t1", line, "target ids are payload, never spoken")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class HostStateTests(unittest.TestCase):
    def test_a_waiting_input_pane_is_blocked_without_reading_it(self) -> None:
        """The host's own agent registry beats a screen heuristic.

        Superset bindings carry the last agent event, and PermissionRequest maps
        to waiting_input in panes_list — so the watcher can announce it without a
        single pane_read, and a read failure cannot hide it.
        """
        watcher = Watcher()
        waiting = dict(pane("superset:w1", "claude", "muhabbit"), command="waiting_input")

        def refuse(_t):  # pragma: no cover - must never be called
            raise AssertionError("waiting_input must not need a read")

        findings = watcher.observe([waiting], refuse)
        self.assertEqual([f.kind for f in findings], ["blocked"])
        self.assertEqual(watcher.observe([waiting], refuse), [], "announced once")
