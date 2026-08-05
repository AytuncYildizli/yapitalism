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

from yapitalism.adapters.superset import SupersetAdapter, SupersetConfig
from yapitalism.mcp.backends.base import (
    CLIENT,
    HOST,
    NONE,
    AcceptanceOutcome,
    SendOutcome,
)
from yapitalism.mcp.backends.superset_backend import SupersetBackend
from yapitalism.mcp.receipt import build_receipt

from test_superset_adapter import FakeTrpcServer, result, snapshot_result

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


def stock_responder(
    written: list[str], *, revisions: list[int] | None = None
) -> Any:
    """A host that routes writeInput and snapshot but 404s terminal.send."""
    revs = iter(revisions or [7, 8])

    def responder(method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if path.endswith("terminal.send"):
            return {"__status__": 404}
        if path.endswith("terminal.snapshot"):
            return snapshot_result(text="idle", revision=next(revs, 8))
        if path.endswith("terminal.writeInput"):
            written.append(payload["data"])
            return result({"success": True})
        if path.endswith("terminal.listSessions"):
            return result({"sessions": [{"terminalId": TERMINAL, "runtime": "codex"}]})
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
        # Not approximated. snapshot returns a screen, and deciding emptiness from
        # it means reimplementing the host's detector by eye.
        self.assertEqual(caps.empty_prompt_check, NONE)
        self.assertEqual(caps.client_enforced, ("idempotent_dispatch", "optimistic_revision"))
        self.assertEqual(caps.degraded, ("empty_prompt_check",))
        # listSessions exists on a stock build, so this stays registry-backed.
        self.assertEqual(caps.runtime_detection, "registry")

    def test_a_guarded_host_reports_host_enforcement(self) -> None:
        def responder(method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
            if path.endswith("terminal.send"):
                # Present, and rejecting the empty probe payload — 400, not 404.
                return {"__status__": 400}
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
            raise AssertionError(f"unexpected procedure: {path}")

        with FakeTrpcServer(responder) as server, tempfile.TemporaryDirectory() as tmp:
            backend = SupersetBackend(manifest_path=write_manifest(tmp, server.endpoint))
            for _ in range(4):
                backend.capabilities()
        self.assertEqual(len(probes), 1)


class StockHostDispatchTests(unittest.TestCase):
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
        self.assertEqual(outcome.prompt_status, "unknown")
        self.assertFalse(outcome.prompt_verified)

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


class ReceiptWordingTests(unittest.TestCase):
    """A client-enforced GREEN is still a GREEN, and must not sound identical."""

    def receipt(self, caps: Any) -> Any:
        return build_receipt(
            SendOutcome(phase="injected", dispatched=True, runtime="codex"),
            AcceptanceOutcome(observed=True, attempts=1),
            caps,
        )

    def test_host_enforced_green_carries_no_caveat(self) -> None:
        from yapitalism.mcp.backends.superset_backend import _HOST_GUARDED

        receipt = self.receipt(_HOST_GUARDED)
        self.assertEqual(receipt.status, "GREEN")
        self.assertEqual(receipt.speak, "Ajan aldı ve işledi.")
        self.assertNotIn("client_guarantees", receipt.as_dict())

    def test_client_enforced_green_says_who_checked(self) -> None:
        from yapitalism.mcp.backends.superset_backend import _CLIENT_GUARDED

        receipt = self.receipt(_CLIENT_GUARDED)
        self.assertEqual(receipt.status, "GREEN")
        # The two wordings must differ, or the distinction exists only in a field
        # nobody hears.
        self.assertIn("bu taraf", receipt.speak)
        payload = receipt.as_dict()
        self.assertEqual(
            payload["client_guarantees"], ["idempotent_dispatch", "optimistic_revision"]
        )
        self.assertEqual(payload["missing_guarantees"], ["empty_prompt_check"])

    def test_tmux_green_still_reads_as_the_weakest(self) -> None:
        from yapitalism.mcp.backends.tmux_backend import _CAPABILITIES

        receipt = self.receipt(_CAPABILITIES)
        self.assertEqual(receipt.status, "GREEN")
        self.assertEqual(receipt.as_dict()["missing_guarantees"], list(_CAPABILITIES.degraded))
        self.assertNotIn("client_guarantees", receipt.as_dict())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
