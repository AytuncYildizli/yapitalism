"""A receipt may only claim what the host it is talking to actually enforces.

`SupersetBackend` used to declare `idempotent_dispatch`, `optimistic_revision`
and `empty_prompt_check` as constants. They described one machine — a fork host
whose `terminal.send` takes `expectRevision`, `clientToken` and
`requireEmptyPrompt` and refuses the write itself. A stock Superset build routes
`terminal.writeInput` and has none of that, so every stock user would have been
handed a GREEN asserting three guards nothing ever checked.

These tests pin the three host situations apart, and pin the fallback down to the
bytes it writes. Nothing is withheld from a stock host: the send happens, the
canary is still checked, two guards move to this process, and the receipt says
so.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from yapitalism.adapters.superset import SupersetAdapter, SupersetConfig
from yapitalism.mcp.backends.base import (
    CLIENT,
    GUARANTEES,
    HOST,
    AcceptanceOutcome,
    SendOutcome,
)
from yapitalism.mcp.backends.superset_backend import SupersetBackend
from yapitalism.mcp.receipt import build_receipt

from test_superset_adapter import (
    FakeTrpcServer,
    guarded_send_probe,
    result,
    snapshot_result,
)

TERMINAL = "terminal-1"
WORKSPACE = "workspace-1"


def write_manifest(directory: str, endpoint: str) -> Path:
    path = Path(directory) / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "endpoint": endpoint,
                "bearer_token": "token-for-tests",
                "workspace_id": WORKSPACE,
                "terminal_id": TERMINAL,
                "timeout_seconds": 3.0,
            }
        )
    )
    # The loader refuses anything group- or world-readable.
    os.chmod(path, 0o600)
    return path


#: How an EMPTY codex prompt renders. Read off a live pane on 2026-08-05: the
#: pane holding `once` showed that text, and the same pane after `pane_clear`
#: showed this placeholder.
IDLE_SCREEN = "some earlier output\n\n\u203a Use /skills to list available skills"
OCCUPIED_SCREEN = "some earlier output\n\n\u203a half a typed thought"


def stock_responder(
    written: list[str],
    *,
    revisions: list[int] | None = None,
    screen: str = IDLE_SCREEN,
    screens_after: list[str] | None = None,
) -> Any:
    """A host that routes writeInput and snapshot but 404s terminal.send.

    `screens_after` feeds the post-write snapshots, so a composer that keeps the
    text can be simulated separately from the pre-write judgement.
    """
    revs = iter(revisions or [7, 8])
    later = iter(screens_after or [])

    def responder(method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if path.endswith("terminal.send"):
            return {"__status__": 404}
        if path.endswith("terminal.snapshot"):
            shown = screen if not written else next(later, screen)
            return snapshot_result(text=shown, revision=next(revs, 8))
        if path.endswith("terminal.writeInput"):
            written.append(payload["data"])
            return result({"success": True})
        if path.endswith("workspace.list"):
            # Capabilities are gated on a call that authenticates, not merely one
            # that routes — a stale manifest used to report full guarantees while
            # every send returned 401.
            return result([{"id": WORKSPACE, "name": "ws"}])
        if path.endswith("terminal.list"):
            return result({"sessions": [{"terminalId": TERMINAL, "workspaceId": WORKSPACE}]})
        if path.endswith("terminalAgents.listByWorkspace"):
            # `agentId`, which is what the host really sends. This served
            # `agent.runtime` on `terminal.listSessions` — a procedure the host
            # 404s and a field no build has — so the guess passed its own test.
            return result([{"terminalId": TERMINAL, "agentId": "codex"}])
        raise AssertionError(f"unexpected procedure: {path}")

    return responder


class StockHostCapabilityTests(unittest.TestCase):
    def test_a_stock_host_reports_client_enforcement_not_host(self) -> None:
        written: list[str] = []
        with FakeTrpcServer(stock_responder(written)) as server, tempfile.TemporaryDirectory() as tmp:
            backend = SupersetBackend(manifest_path=write_manifest(tmp, server.endpoint))
            caps = backend.capabilities()
        self.assertEqual(caps.idempotent_dispatch, CLIENT)
        self.assertEqual(caps.optimistic_revision, CLIENT)
        # CLIENT rather than NONE, after a correction: reporting this unenforced
        # while writing anyway left the fallback appending to staged text and
        # submitting the merge. An accurate label on a corrupting write is worse
        # than a heuristic that refuses.
        self.assertEqual(caps.empty_prompt_check, CLIENT)
        self.assertEqual(caps.client_enforced, GUARANTEES)
        self.assertEqual(caps.degraded, ())
        # listSessions exists on a stock build, so this stays registry-backed.
        self.assertEqual(caps.runtime_detection, "registry")

    def test_a_guarded_host_reports_host_enforcement(self) -> None:
        def responder(method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
            if path.endswith("terminal.send"):
                # A 400 alone proved only that a procedure by this name exists.
                # What makes a host guarded is which fields it REQUIRES, so the
                # probe answer has to name them.
                return guarded_send_probe()
            if path.endswith("workspace.list"):
                # Capabilities are gated on a call that AUTHENTICATES, not just one
                # that routes: `procedure_exists` treats 401 as "present", so a stale
                # manifest used to report host/host/host while every send 401'd.
                return result([{"id": WORKSPACE, "name": "ws"}])
            raise AssertionError(f"unexpected procedure: {path}")

        with FakeTrpcServer(responder) as server, tempfile.TemporaryDirectory() as tmp:
            backend = SupersetBackend(manifest_path=write_manifest(tmp, server.endpoint))
            caps = backend.capabilities()
        for level in (
            caps.idempotent_dispatch,
            caps.optimistic_revision,
            caps.empty_prompt_check,
        ):
            self.assertEqual(level, HOST)
        self.assertEqual(caps.degraded, ())
        self.assertEqual(caps.client_enforced, ())

    def test_the_host_is_asked_once_not_per_send(self) -> None:
        probes: list[str] = []

        def responder(method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
            if path.endswith("terminal.send"):
                probes.append(path)
                return {"__status__": 400}
            if path.endswith("workspace.list"):
                return result([{"id": WORKSPACE, "name": "ws"}])
            raise AssertionError(f"unexpected procedure: {path}")

        with FakeTrpcServer(responder) as server, tempfile.TemporaryDirectory() as tmp:
            backend = SupersetBackend(manifest_path=write_manifest(tmp, server.endpoint))
            for _ in range(4):
                backend.capabilities()
        self.assertEqual(len(probes), 1)


class StockHostDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        # The settle and redraw waits are real behaviour with nothing to wait for
        # against a fake server.
        patcher = patch("yapitalism.adapters.superset.time.sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_fallback_writes_the_text_then_a_carriage_return(self) -> None:
        """The send must actually happen on a stock host, not degrade to nothing."""
        written: list[str] = []
        with FakeTrpcServer(stock_responder(written)) as server, tempfile.TemporaryDirectory() as tmp:
            adapter = SupersetAdapter(SupersetConfig.from_manifest(write_manifest(tmp, server.endpoint)))
            outcome = adapter.dispatch(
                "run the tests", expected_revision=7, client_token="tok-1", confirm=True
            )
        self.assertEqual(written, ["run the tests", "\r"])
        self.assertEqual(outcome.phase, "injected")
        self.assertTrue(outcome.dispatched)
        self.assertTrue(outcome.submit_sent)
        self.assertEqual(outcome.target_runtime, "codex")
        # No host delivery id exists here, and inventing one would put a
        # fabricated evidence reference in a receipt.
        self.assertIsNone(outcome.delivery_id)
        # The host never judged the prompt, and neither did this.
        # Judged EMPTY here before writing — weaker than the host's atomic check,
        # so saying "unknown" would now understate what was verified.
        self.assertEqual(outcome.prompt_status, "empty")

    def test_a_composer_that_keeps_the_text_is_reported_staged_not_sent(self) -> None:
        """The bug live testing found, and the reason submit is verified.

        `writeInput` reports success for delivering bytes, which says nothing about
        the composer having submitted them. Writing the text and the carriage
        return back to back left a real Codex pane holding the message: the TUI
        read the immediately-following input as a multi-line paste and kept the
        newline as a literal break. The first version asserted `submit_sent=True`
        from the write succeeding, so the message would have sat there forever
        while the receipt said it was sent.
        """
        written: list[str] = []
        # A host whose screen still shows the staged text after both Enters.
        responder = stock_responder(written, screen=IDLE_SCREEN, screens_after=[OCCUPIED_SCREEN] * 4)
        with FakeTrpcServer(responder) as server, tempfile.TemporaryDirectory() as tmp:
            adapter = SupersetAdapter(SupersetConfig.from_manifest(write_manifest(tmp, server.endpoint)))
            outcome = adapter.dispatch(
                "run the tests", expected_revision=7, client_token="tok-7", confirm=True
            )
        self.assertEqual(outcome.phase, "staged_not_submitted")
        self.assertFalse(outcome.submit_sent)
        # Enter was tried twice before giving up, and the text really was written.
        self.assertEqual(written.count("\r"), 2)
        self.assertIn("run the tests", written)

    def test_a_stale_revision_is_refused_without_writing(self) -> None:
        """optimistic_revision, enforced here rather than by the host."""
        written: list[str] = []
        with FakeTrpcServer(stock_responder(written, revisions=[99])) as server, tempfile.TemporaryDirectory() as tmp:
            adapter = SupersetAdapter(SupersetConfig.from_manifest(write_manifest(tmp, server.endpoint)))
            outcome = adapter.dispatch(
                "too late", expected_revision=7, client_token="tok-2", confirm=True
            )
        self.assertEqual(outcome.phase, "rejected_revision_changed")
        self.assertFalse(outcome.dispatched)
        # The assertion that matters: refusing must mean nothing was typed.
        self.assertEqual(written, [])

    def test_a_replayed_token_is_refused_without_writing_again(self) -> None:
        """idempotent_dispatch, enforced here. Fully effective: the repeats this
        guards against are this process's own."""
        written: list[str] = []
        with FakeTrpcServer(stock_responder(written, revisions=[7, 8, 8, 8])) as server, tempfile.TemporaryDirectory() as tmp:
            adapter = SupersetAdapter(SupersetConfig.from_manifest(write_manifest(tmp, server.endpoint)))
            first = adapter.dispatch(
                "once only", expected_revision=7, client_token="tok-3", confirm=True
            )
            # The identical request again — same token, same text, same expected
            # revision. The terminal has moved on since (revision 7 -> 8) because
            # the agent started working, which is precisely why the token has to
            # be checked before the revision.
            second = adapter.dispatch(
                "once only", expected_revision=7, client_token="tok-3", confirm=True
            )
        self.assertEqual(first.phase, "injected")
        self.assertEqual(second.phase, "duplicate_ignored")
        self.assertTrue(second.duplicate)
        self.assertFalse(second.dispatched)
        self.assertEqual(written, ["once only", "\r"])

    def test_an_occupied_prompt_is_refused_rather_than_appended_to(self) -> None:
        """The whole reason empty_prompt_check moved from NONE to CLIENT.

        writeInput does not replace staged text, it concatenates: a half-typed
        thought and a voice instruction would arrive as one corrupted message,
        submitted by our own carriage return.
        """
        written: list[str] = []
        responder = stock_responder(written, screen=OCCUPIED_SCREEN)
        with FakeTrpcServer(responder) as server, tempfile.TemporaryDirectory() as tmp:
            adapter = SupersetAdapter(SupersetConfig.from_manifest(write_manifest(tmp, server.endpoint)))
            outcome = adapter.dispatch(
                "run the tests", expected_revision=7, client_token="tok-5", confirm=True
            )
        self.assertEqual(outcome.phase, "rejected_prompt_not_empty")
        self.assertFalse(outcome.dispatched)
        self.assertEqual(outcome.prompt_status, "has_text")
        # Nothing typed at all — a refusal that still wrote would be the bug.
        self.assertEqual(written, [])

    def test_an_unreadable_screen_is_refused_too(self) -> None:
        """A menu or overlay has no input line to judge.

        Proceeding on "I could not tell" is precisely where the concatenation
        happens, so UNKNOWN refuses as well - and `pane_clear` is the way out,
        after which the screen becomes recognisable.
        """
        written: list[str] = []
        responder = stock_responder(written, screen="a full-screen menu\n1. Update now\n2. Later")
        with FakeTrpcServer(responder) as server, tempfile.TemporaryDirectory() as tmp:
            adapter = SupersetAdapter(SupersetConfig.from_manifest(write_manifest(tmp, server.endpoint)))
            outcome = adapter.dispatch(
                "run the tests", expected_revision=7, client_token="tok-6", confirm=True
            )
        self.assertEqual(outcome.phase, "rejected_prompt_unreadable")
        self.assertEqual(written, [])

    def test_multiline_text_is_refused_rather_than_split(self) -> None:
        """An embedded newline IS a submit when writing raw bytes.

        The host's `send` takes text and decides when to submit. Passing
        multi-line text through writeInput would deliver several separate
        instructions with the first arriving alone, which is worse than refusing.
        """
        written: list[str] = []
        with FakeTrpcServer(stock_responder(written)) as server, tempfile.TemporaryDirectory() as tmp:
            adapter = SupersetAdapter(SupersetConfig.from_manifest(write_manifest(tmp, server.endpoint)))
            for index, text in enumerate(("first line\nsecond line", "trailing\n", "cr\rinjected")):
                with self.subTest(text=text), self.assertRaises(ValueError) as caught:
                    adapter.dispatch(
                        text, expected_revision=7, client_token=f"tok-4-{index}", confirm=True
                    )
                self.assertIn("single line", str(caught.exception))
        self.assertEqual(written, [])


class PromptDetectorSafetyTests(unittest.TestCase):
    """The two corruption paths a council review found in this detector."""

    def test_the_live_prompt_is_the_last_marker_line(self) -> None:
        """Codex echoes each submitted prompt with the same marker.

        Measured on a real pane: the instruction just sent sits ABOVE an empty
        composer. Judging every marker line and letting text win — which an earlier
        version did, to cover a hint-below-composer case that was reasoned about but
        never observed — refused every pane that had ever been sent to.
        """
        from yapitalism.prompt_state import EMPTY, HAS_TEXT, detect_prompt_state

        echoed = (
            "\u203a Reply with exactly the word READY\n"
            "\u2022 READY\n"
            "\u203a Use /skills to list available skills"
        )
        self.assertEqual(detect_prompt_state(echoed, "codex"), EMPTY)

        # And real staged text at the bottom still reads as text.
        staged = "\u203a an older instruction\n\u2022 done\n\u203a half a typed thought"
        self.assertEqual(detect_prompt_state(staged, "codex"), HAS_TEXT)

    def test_a_placeholder_a_human_could_type_is_not_listed(self) -> None:
        """Admissibility: an entry needs a token a person would not type.

        "explain this codebase" was listed. Somebody typing exactly that and pausing
        would have had their prompt judged empty. Every surviving entry carries
        `@filename` or `/skills`.
        """
        from yapitalism.prompt_state import _PLACEHOLDERS, HAS_TEXT, detect_prompt_state

        self.assertEqual(detect_prompt_state("\u203a Explain this codebase", "codex"), HAS_TEXT)
        # The admissible tokens: each is a literal a person does not type as
        # prose. `{feature}` and `/review` joined on 2026-08-23, both read off a
        # live codex 0.147 composer while building `yapitalism demo`.
        admissible = ("@filename", "/skills", "{feature}", "/review")
        for entry in _PLACEHOLDERS["codex"]:
            with self.subTest(entry=entry):
                self.assertTrue(
                    any(token in entry for token in admissible),
                    f"{entry!r} contains nothing a human would not type",
                )

    def test_all_empty_markers_still_read_empty(self) -> None:
        """The safe direction must not become "always refuse"."""
        from yapitalism.prompt_state import EMPTY, detect_prompt_state

        both = "output\n\u203a\n\u203a Use /skills to list available skills"
        self.assertEqual(detect_prompt_state(both, "codex"), EMPTY)

    def test_the_observed_opencode_empty_frame_is_recognised(self) -> None:
        from yapitalism.prompt_state import EMPTY, UNKNOWN, detect_prompt_state

        frame = (
            "┃\n"
            '┃  Ask anything... "Fix a TODO in the codebase"\n'
            "┃\n"
            "┃  Build · Ox Alpha Free (Unlimited)"
        )
        self.assertEqual(detect_prompt_state(frame, "opencode"), EMPTY)
        after_response = (
            "answer transcript\n"
            "┃\n"
            "┃\n"
            "┃\n"
            "┃  Build · Ox Alpha Free (Unlimited) OpenCode Zen"
        )
        self.assertEqual(detect_prompt_state(after_response, "opencode"), EMPTY)
        # The words without the measured frame are transcript, not proof.
        flat = 'Ask anything... "Fix a TODO in the codebase" BUILD Ox Alpha Free (Unlimited)'
        self.assertEqual(detect_prompt_state(flat, "opencode"), UNKNOWN)
        # Anything that is not the exact placeholder frame over-refuses.
        staged = "┃\n┃  half a typed thought\n┃\n┃  Build · Ox Alpha Free (Unlimited)"
        self.assertEqual(detect_prompt_state(staged, "opencode"), UNKNOWN)


class RefusalWordingTests(unittest.TestCase):
    """A RED must never advise something that has been measured not to work, and
    must never imply a delivery that may not have happened."""

    def speak(self, phase: str, reason: str = "") -> str:
        from yapitalism.mcp.backends.superset_backend import HOST_GUARDED

        return build_receipt(
            SendOutcome(phase=phase, dispatched=False, runtime="codex", reason=reason),
            AcceptanceOutcome(observed=False, attempts=0),
            HOST_GUARDED,
        ).speak

    def test_a_host_screen_disagreement_does_not_advise_clearing(self) -> None:
        """Measured live: the host refused an idle Codex pane whose prompt held only
        a placeholder, and pane_clear left it untouched twice. The generic line told
        the operator to clear it, which is a loop."""
        line = self.speak("rejected_prompt_not_empty", "host_says_occupied_screen_says_empty")
        self.assertIn("Temizlemek burada işe yaramaz", line)
        generic = self.speak("rejected_prompt_not_empty")
        self.assertIn("temizleyip", generic)
        self.assertNotEqual(line, generic)

    def test_an_ambiguous_write_is_not_spoken_as_a_blocked_repeat(self) -> None:
        line = self.speak("duplicate_after_ambiguous_write")
        self.assertIn("belirsiz", line)
        # The genuine-duplicate wording asserts the first one arrived.
        self.assertNotIn("Aynı mesajın tekrarını engelledim", line)

    def test_a_shell_pane_refusal_says_why_it_matters(self) -> None:
        self.assertIn("komut", self.speak("rejected_not_an_agent"))

    def test_staged_text_is_reported_as_not_sent(self) -> None:
        """The operator must know the message is sitting in the prompt, or they
        wait for a reply that cannot come."""
        line = self.speak("staged_not_submitted")
        self.assertIn("prompt", line)
        self.assertTrue(line.startswith("Gönderilmedi"), line)

    def test_every_refusal_leads_with_the_same_word(self) -> None:
        """The lead word is the whole signal on a voice channel.

        A refusal is the one outcome the operator may safely retry, and it has to
        be separable from an unproven delivery by the first word alone — the rest
        of the sentence is often not heard.
        """
        phases = (
            "rejected_trust_prompt",
            "rejected_auth_prompt",
            "rejected_confirm_prompt",
            "rejected_prompt_not_empty",
            "rejected_prompt_unreadable",
            "rejected_not_an_agent",
            "rejected_revision_changed",
            "duplicate_after_ambiguous_write",
            "duplicate_seen_token",
            "staged_not_submitted",
            "something_nobody_has_named_yet",
        )
        for phase in phases:
            with self.subTest(phase=phase):
                self.assertTrue(self.speak(phase).startswith("Gönderilmedi:"))
        self.assertTrue(
            self.speak(
                "rejected_prompt_not_empty", "host_says_occupied_screen_says_empty"
            ).startswith("Gönderilmedi:")
        )


class ReceiptWordingTests(unittest.TestCase):
    """Enforcement attribution is recorded, and is not read aloud.

    Who enforced which guarantee is real and stays in the payload for the model,
    the log and `doctor`. It was also spoken on every GREEN, where it is
    invisible: there is no different action behind "the host checked this" and
    "this side checked this". The tests below pin both halves — the payload still
    tells the three cases apart, and the sentence no longer tries to.
    """

    def receipt(self, caps: Any) -> Any:
        return build_receipt(
            SendOutcome(phase="injected", dispatched=True, runtime="codex"),
            AcceptanceOutcome(observed=True, attempts=1),
            caps,
        )

    def test_a_green_names_the_agent_and_says_nothing_else(self) -> None:
        from yapitalism.mcp.backends.superset_backend import HOST_GUARDED

        receipt = self.receipt(HOST_GUARDED)
        self.assertEqual(receipt.status, "GREEN")
        self.assertEqual(receipt.speak, "codex aldı.")
        self.assertNotIn("client_guarantees", receipt.as_dict())

    def test_client_enforcement_is_recorded_not_spoken(self) -> None:
        from yapitalism.mcp.backends.superset_backend import CLIENT_GUARDED

        receipt = self.receipt(CLIENT_GUARDED)
        self.assertEqual(receipt.status, "GREEN")
        payload = receipt.as_dict()
        self.assertEqual(payload["client_guarantees"], list(GUARANTEES))
        # Nothing is unchecked on this path any more, so there is no
        # missing_guarantees list to carry.
        self.assertNotIn("missing_guarantees", payload)
        self.assertEqual(receipt.speak, "codex aldı.")

    def test_tmux_green_records_a_mix_of_checked_and_unchecked(self) -> None:
        """tmux checks two of three, so it is neither the old all-NONE nor a
        client-clean path. The payload keeps that apart; the sentence does not."""
        from yapitalism.mcp.backends.tmux_backend import TMUX_CAPABILITIES

        receipt = self.receipt(TMUX_CAPABILITIES)
        payload = receipt.as_dict()
        self.assertEqual(receipt.status, "GREEN")
        self.assertEqual(payload["client_guarantees"], ["idempotent_dispatch", "empty_prompt_check"])
        self.assertEqual(payload["missing_guarantees"], ["optimistic_revision"])
        self.assertEqual(receipt.speak, "codex aldı.")

    def test_no_spoken_line_carries_enforcement_vocabulary(self) -> None:
        """The regression this file exists to prevent, pointed the other way.

        The wording used to leak the internal model out loud — "host", "revision",
        "token", "backend". None of it changes what the operator does.
        """
        from yapitalism.mcp.backends.superset_backend import (
            CLIENT_GUARDED,
            HOST_GUARDED,
            UNKNOWN_HOST,
        )
        from yapitalism.mcp.backends.tmux_backend import TMUX_CAPABILITIES

        jargon = ("host", "revision", "token", "backend", "guarantee", "canary")
        lines = [self.receipt(caps).speak for caps in
                 (HOST_GUARDED, CLIENT_GUARDED, UNKNOWN_HOST, TMUX_CAPABILITIES)]
        for phase in ("rejected_prompt_not_empty", "rejected_not_an_agent",
                      "staged_not_submitted", "rejected_revision_changed"):
            lines.append(
                build_receipt(
                    SendOutcome(phase=phase, dispatched=False, runtime="codex"),
                    AcceptanceOutcome(observed=False, attempts=0),
                    HOST_GUARDED,
                ).speak
            )
        for line in lines:
            with self.subTest(line=line):
                lowered = line.lower()
                for word in jargon:
                    self.assertNotIn(word, lowered)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
