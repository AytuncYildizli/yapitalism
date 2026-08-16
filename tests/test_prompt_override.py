"""Overruling the host's empty-prompt verdict, and the fence around it.

The host counts an agent's own placeholder suggestion as staged input, so a send to
such a pane is refused forever and `pane_clear` cannot help. The correction is not to
route around the host with `writeInput` — that would discard revision atomicity,
token dedup, the host delivery id and multi-line capability to work around a
detector. It is to stop asking for the one check that is wrong, since
`requireEmptyPrompt` is a field this client sets and the host defaults off.

Every test here is about a way the override must NOT fire, or about the receipt not
sounding like an ordinary success.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from yapitalism.adapters.superset import SupersetConfig
from yapitalism.mcp.backends.base import AcceptanceOutcome, SendOutcome
from yapitalism.mcp.backends.superset_backend import (
    HOST_GUARDED,
    HOST_GUARDED_PROMPT_OVERRIDDEN,
    SupersetBackend,
)
from yapitalism.mcp.receipt import build_receipt

from test_capability_honesty import TERMINAL, WORKSPACE, write_manifest
from test_superset_adapter import (
    FakeTrpcServer,
    guarded_send_probe,
    result,
    snapshot_result,
)

PLACEHOLDER = "output\n\n› Use /skills to list available skills"
REAL_TEXT = "output\n\n› half a typed thought"


def host(
    sends: list[dict[str, Any]],
    *,
    screen: str = PLACEHOLDER,
    runtime: str = "codex",
    second_phase: str = "injected",
) -> Any:
    """A host that refuses the guarded send and accepts the unguarded retry."""

    def responder(method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if path.endswith("terminal.snapshot"):
            return snapshot_result(text=screen, revision=7)
        if path.endswith("terminal.list"):
            return result({"sessions": [{"terminalId": TERMINAL, "workspaceId": WORKSPACE}]})
        if path.endswith("terminalAgents.listByWorkspace"):
            return result([{"terminalId": TERMINAL, "agentId": runtime}])
        if path.endswith("workspace.list"):
            return result([{"id": WORKSPACE, "name": "ws"}])
        if path.endswith("terminal.send") and not payload.get("terminalId"):
            return guarded_send_probe()
        if path.endswith("terminal.send"):
            sends.append(payload)
            first = len(sends) == 1
            phase = "rejected_prompt_not_empty" if first else second_phase
            injected = phase == "injected"
            return result(
                {
                    "terminalId": TERMINAL,
                    "phase": phase,
                    "promptStatus": "has_text" if first else "empty",
                    "target": {"runtime": runtime},
                    "deliveryId": "11111111-1111-4111-8111-111111111111" if injected else None,
                    "submitSent": injected,
                    "duplicate": False,
                    "revisionBefore": 7,
                    "revisionAfter": 9 if injected else 7,
                }
            )
        raise AssertionError(f"unexpected procedure: {path}")

    return responder


class OverrideFenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = tempfile.TemporaryDirectory()
        self.addCleanup(self.state.cleanup)
        patcher = patch.dict(os.environ, {"XDG_STATE_HOME": self.state.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def send(self, responder: Any, **kwargs: Any) -> tuple[SendOutcome, list[dict[str, Any]]]:
        sends: list[dict[str, Any]] = []
        with FakeTrpcServer(responder(sends)) as server, tempfile.TemporaryDirectory() as tmp:
            backend = SupersetBackend(manifest_path=write_manifest(tmp, server.endpoint))
            backend.list_panes()
            outcome = backend.send(
                f"superset:{TERMINAL}", "run the tests", canary=None,
                client_token="tok-1", **kwargs,
            )
        return outcome, sends

    def test_without_the_flag_the_refusal_stands(self) -> None:
        """The default must never override. It is a decision, not a repair."""
        outcome, sends = self.send(lambda s: host(s))
        self.assertEqual(outcome.phase, "rejected_prompt_not_empty")
        self.assertFalse(outcome.dispatched)
        self.assertEqual(len(sends), 1)
        self.assertIs(outcome.capabilities_override, None)

    def test_with_the_flag_it_retries_unguarded_on_the_same_revision(self) -> None:
        outcome, sends = self.send(lambda s: host(s), override_host_prompt_check=True)
        self.assertEqual(outcome.phase, "injected")
        self.assertTrue(outcome.dispatched)
        self.assertEqual(len(sends), 2)
        # The client asked for the check the first time and not the second.
        self.assertTrue(sends[0]["requireEmptyPrompt"])
        self.assertFalse(sends[1]["requireEmptyPrompt"])
        # The SAME revision the verdict was made against — not a fresh read. A newer
        # revision would authorise a write at a moment nobody judged.
        self.assertEqual(sends[0]["expectRevision"], sends[1]["expectRevision"])
        # Revision and token stay host-enforced; only the prompt verdict moved.
        self.assertIs(outcome.capabilities_override, HOST_GUARDED_PROMPT_OVERRIDDEN)
        self.assertEqual(outcome.capabilities_override.optimistic_revision, "host")
        self.assertEqual(outcome.capabilities_override.empty_prompt_check, "client")

    def test_it_does_not_fire_when_this_side_also_sees_text(self) -> None:
        """The override rests entirely on holding falsifying evidence.

        With real staged text both readers agree, there is nothing to overrule, and
        the operator asking for it does not conjure evidence.
        """
        outcome, sends = self.send(
            lambda s: host(s, screen=REAL_TEXT), override_host_prompt_check=True
        )
        self.assertEqual(outcome.phase, "rejected_prompt_not_empty")
        self.assertEqual(len(sends), 1)

    def test_it_does_not_fire_for_claude_or_kimi(self) -> None:
        """No observed placeholders there, so both readers agree and the RED is real.

        Lifting it for them would be overruling a verdict this side never contradicted.
        """
        for runtime in ("claude", "kimi"):
            with self.subTest(runtime=runtime):
                outcome, sends = self.send(
                    lambda s, r=runtime: host(s, runtime=r), override_host_prompt_check=True
                )
                self.assertEqual(len(sends), 1)
                self.assertFalse(outcome.dispatched)

    def test_it_does_not_fire_for_any_other_refusal(self) -> None:
        """A hard whitelist, because nothing in the mechanism knows the difference.

        The empty prompt is the only predicate where this side holds contrary
        evidence. A trust prompt or a stale revision is not that.
        """

        def other(sends: list[dict[str, Any]]) -> Any:
            def responder(method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
                if path.endswith("terminal.snapshot"):
                    return snapshot_result(text=PLACEHOLDER, revision=7)
                if path.endswith("terminal.list"):
                    return result({"sessions": [{"terminalId": TERMINAL, "workspaceId": WORKSPACE}]})
                if path.endswith("terminalAgents.listByWorkspace"):
                    return result([{"terminalId": TERMINAL, "agentId": "codex"}])
                if path.endswith("workspace.list"):
                    return result([{"id": WORKSPACE, "name": "ws"}])
                if path.endswith("terminal.send") and not payload.get("terminalId"):
                    return guarded_send_probe()
                if path.endswith("terminal.send"):
                    sends.append(payload)
                    return result(
                        {
                            "terminalId": TERMINAL,
                            "phase": "rejected_revision_changed",
                            "promptStatus": "unknown",
                            "target": {"runtime": "codex"},
                            "deliveryId": None,
                            "submitSent": False,
                            "duplicate": False,
                            "revisionBefore": 9,
                            "revisionAfter": 9,
                        }
                    )
                raise AssertionError(path)

            return responder

        outcome, sends = self.send(other, override_host_prompt_check=True)
        self.assertEqual(outcome.phase, "rejected_revision_changed")
        self.assertEqual(len(sends), 1)

    def test_a_stale_revision_on_the_retry_terminates_the_attempt(self) -> None:
        """Never retried again. Re-reading for a fresher revision is the mistake."""
        outcome, sends = self.send(
            lambda s: host(s, second_phase="rejected_revision_changed"),
            override_host_prompt_check=True,
        )
        self.assertEqual(outcome.phase, "rejected_revision_changed")
        self.assertFalse(outcome.dispatched)
        self.assertEqual(len(sends), 2)

    def test_every_override_leaves_a_durable_line(self) -> None:
        """Otherwise the fix deletes the evidence that would justify or condemn it.

        "most idle Codex panes" was asserted and never counted.
        """
        self.send(lambda s: host(s), override_host_prompt_check=True)
        log = Path(self.state.name) / "yapitalism" / "prompt-overrides.jsonl"
        rows = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["runtime"], "codex")
        self.assertEqual(rows[0]["revision"], 7)
        self.assertEqual(rows[0]["reason"], "host_says_occupied_screen_says_empty")


class OverrideWordingTests(unittest.TestCase):
    def receipt(self, reason: str, caps: Any) -> Any:
        return build_receipt(
            SendOutcome(phase="injected", dispatched=True, runtime="codex", reason=reason),
            AcceptanceOutcome(observed=True, attempts=1),
            caps,
        )

    def test_the_override_is_spoken_as_a_thing_to_look_at(self) -> None:
        """The one deviation that survives the two-state collapse.

        Enforcement attribution does not change what the operator does, so it is no
        longer read aloud. This does: something was already staged in that prompt
        and their text was merged with it, so the pane may not say what they think
        it says. It is spoken as a consequence, not as an internal tier.
        """
        spoken = self.receipt("host_prompt_check_overridden", HOST_GUARDED_PROMPT_OVERRIDDEN).speak
        self.assertTrue(spoken.startswith("codex aldı."))
        self.assertIn("bekleyen metin vardı", spoken)
        self.assertIn("sen istediğin için", spoken)
        # And it still must not narrate the enforcement model.
        self.assertNotIn("host", spoken.lower())

    def test_an_ordinary_green_is_the_short_sentence(self) -> None:
        spoken = self.receipt("", HOST_GUARDED).speak
        self.assertEqual(spoken, "codex aldı.")


class ConfigShapeTests(unittest.TestCase):
    def test_the_adapter_asks_for_the_check_by_default(self) -> None:
        """The dial's default must stay on.

        `requireEmptyPrompt` is ours to set and the host defaults it OFF — which is
        the whole reason this correction is possible, and also why a careless edit
        here would silently disable the guard for every send.
        """
        import inspect

        from yapitalism.adapters.superset import SupersetAdapter

        signature = inspect.signature(SupersetAdapter.dispatch)
        self.assertIs(signature.parameters["require_empty_prompt"].default, True)

    def test_config_still_redacts_the_token(self) -> None:
        with patch("socket.create_connection"):
            config = SupersetConfig(
                endpoint="http://127.0.0.1:48900/trpc",
                bearer_token="DO-NOT-LEAK",
                workspace_id=WORKSPACE,
                terminal_id=TERMINAL,
                timeout_seconds=2.0,
            )
        self.assertNotIn("DO-NOT-LEAK", repr(config))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
