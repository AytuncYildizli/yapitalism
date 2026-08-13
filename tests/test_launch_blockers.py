"""The defects a five-model launch council found, each pinned by the thing it broke.

Every test here is named after a way the product lied or could execute something
nobody asked for. They are grouped by the issue an external reviewer filed, because
those numbers are the shared vocabulary now.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
import threading
import unittest
from typing import Any
from unittest.mock import patch

from yapitalism.mcp.backends.base import AcceptanceOutcome, BackendError, SendOutcome
from yapitalism.mcp.backends.superset_backend import SupersetBackend

from test_capability_honesty import TERMINAL, WORKSPACE, write_manifest
from test_superset_adapter import FakeTrpcServer, result, snapshot_result

#: Tests run from tests/ as well as from the repo root, so paths are anchored.
REPO = pathlib.Path(__file__).resolve().parent.parent

IDLE = "output\n\n› Use /skills to list available skills"
OTHER_TERMINAL = "terminal-2"


def host(
    calls: list[dict[str, Any]],
    *,
    runtime: str = "codex",
    send_runtime: str | None = None,
) -> Any:
    """A Superset host whose reported runtime is controllable per surface."""

    def responder(method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if path.endswith("terminal.snapshot"):
            return snapshot_result(text=IDLE, revision=7)
        if path.endswith("workspace.list"):
            return result([{"id": WORKSPACE, "name": "ws"}])
        if path.endswith("terminal.listSessions"):
            # Two terminals so a cross-pane token check is reachable; with one, the
            # workspace-binding guard fires first and hides it.
            return result(
                {
                    "sessions": [
                        {"terminalId": TERMINAL, "agent": {"runtime": runtime}},
                        {"terminalId": OTHER_TERMINAL, "agent": {"runtime": runtime}},
                    ]
                }
            )
        if path.endswith("terminal.send"):
            calls.append(payload)
            return result(
                {
                    "terminalId": TERMINAL,
                    "phase": "injected",
                    "promptStatus": "empty",
                    "target": {"runtime": send_runtime or runtime},
                    "deliveryId": "11111111-1111-4111-8111-111111111111",
                    "submitSent": True,
                    "duplicate": False,
                    "revisionBefore": 7,
                    "revisionAfter": 9,
                }
            )
        raise AssertionError(f"unexpected procedure: {path}")

    return responder


class Issue16SupersetRuntimeGate(unittest.TestCase):
    """A voice turn must never reach a pane that is running a shell.

    tmux learned this and refuses up front. The Superset path dispatched first and
    reported the runtime afterwards, so the shell had already run the text.
    """

    def send(self, responder: Any) -> tuple[SendOutcome, list[dict[str, Any]]]:
        calls: list[dict[str, Any]] = []
        with FakeTrpcServer(responder(calls)) as server, tempfile.TemporaryDirectory() as tmp:
            backend = SupersetBackend(manifest_path=write_manifest(tmp, server.endpoint))
            backend.list_panes()
            outcome = backend.send(
                f"superset:{TERMINAL}", "echo owned", canary=None, client_token="t1"
            )
        return outcome, calls

    def test_a_shell_pane_is_refused_before_the_write(self) -> None:
        outcome, calls = self.send(lambda c: host(c, runtime="shell"))
        self.assertEqual(outcome.phase, "rejected_not_an_agent")
        self.assertFalse(outcome.dispatched)
        # The assertion that matters: no terminal.send was issued at all.
        self.assertEqual(calls, [])

    def test_an_unknown_runtime_is_refused_too(self) -> None:
        outcome, calls = self.send(lambda c: host(c, runtime="unknown"))
        self.assertEqual(outcome.phase, "rejected_not_an_agent")
        self.assertEqual(calls, [])

    def test_a_runtime_that_changes_under_us_is_not_reported_as_delivered(self) -> None:
        """Defence in depth: the registry said agent, the host says shell.

        The write cannot be recalled. Refusing to call it a delivered instruction is
        what is left.
        """
        outcome, calls = self.send(lambda c: host(c, runtime="codex", send_runtime="shell"))
        self.assertEqual(len(calls), 1)
        self.assertEqual(outcome.phase, "rejected_not_an_agent")
        self.assertFalse(outcome.dispatched)

    def test_an_agent_pane_still_goes_through(self) -> None:
        outcome, calls = self.send(lambda c: host(c))
        self.assertEqual(outcome.phase, "injected")
        self.assertTrue(outcome.dispatched)
        self.assertEqual(len(calls), 1)


class Issue17AcceptanceIsOperationScoped(unittest.TestCase):
    """Proof belongs to one send, not to the backend.

    FastMCP runs sync tools on a threadpool and the registry hands every call the
    same backend instance, so a second send used to overwrite the first's proof
    context before the first awaited — proving A's canary against B's evidence.
    """

    def test_a_second_send_does_not_steal_the_first_ones_context(self) -> None:
        calls: list[dict[str, Any]] = []
        with FakeTrpcServer(host(calls)) as server, tempfile.TemporaryDirectory() as tmp:
            backend = SupersetBackend(manifest_path=write_manifest(tmp, server.endpoint))
            backend.list_panes()
            target = f"superset:{TERMINAL}"
            backend.send(target, "first", canary=None, client_token="tok-A")
            backend.send(target, "second", canary=None, client_token="tok-B")
            first = backend._contexts["tok-A"]
            second = backend._contexts["tok-B"]
        self.assertEqual(first.submitted_text, "first")
        self.assertEqual(second.submitted_text, "second")

    def test_a_refused_send_leaves_no_context_to_clobber_with(self) -> None:
        """A RED used to overwrite a successful send's proof context."""
        calls: list[dict[str, Any]] = []
        with FakeTrpcServer(host(calls, runtime="shell")) as server, tempfile.TemporaryDirectory() as tmp:
            backend = SupersetBackend(manifest_path=write_manifest(tmp, server.endpoint))
            backend.list_panes()
            backend.send(f"superset:{TERMINAL}", "x", canary=None, client_token="tok-R")
            self.assertEqual(backend._contexts, {})

    def test_awaiting_without_a_token_is_refused(self) -> None:
        """"The most recent send" is exactly the guess that caused the mix-up."""
        calls: list[dict[str, Any]] = []
        with FakeTrpcServer(host(calls)) as server, tempfile.TemporaryDirectory() as tmp:
            backend = SupersetBackend(manifest_path=write_manifest(tmp, server.endpoint))
            backend.list_panes()
            with self.assertRaises(BackendError) as caught:
                backend.await_acceptance(
                    f"superset:{TERMINAL}", "YAPITALISM_ACK_00", client_token=None
                )
        self.assertIn("client token", str(caught.exception))

    def test_a_token_from_another_pane_is_refused(self) -> None:
        """A token belongs to one pane; answering across panes is the same mix-up."""
        calls: list[dict[str, Any]] = []
        with FakeTrpcServer(host(calls)) as server, tempfile.TemporaryDirectory() as tmp:
            backend = SupersetBackend(manifest_path=write_manifest(tmp, server.endpoint))
            backend.list_panes()
            backend.send(f"superset:{TERMINAL}", "x", canary=None, client_token="tok-P")
            with self.assertRaises(BackendError) as caught:
                backend.await_acceptance(
                    f"superset:{OTHER_TERMINAL}", "YAPITALISM_ACK_00", client_token="tok-P"
                )
        self.assertIn("different pane", str(caught.exception))


class Issue18TmuxSerialisation(unittest.TestCase):
    def test_one_lock_per_pane_and_it_is_stable(self) -> None:
        """Per pane, not global: the race is within a pane.

        A global lock would serialise a whole fleet for no safety gain.
        """
        from yapitalism.mcp.backends.tmux_backend import TmuxBackend

        backend = TmuxBackend()
        a1 = backend._pane_lock("tmux:%0")
        a2 = backend._pane_lock("tmux:%0")
        b = backend._pane_lock("tmux:%1")
        self.assertIs(a1, a2)
        self.assertIsNot(a1, b)

    def test_the_lock_is_created_once_under_concurrency(self) -> None:
        from yapitalism.mcp.backends.tmux_backend import TmuxBackend

        backend = TmuxBackend()
        seen: list[Any] = []
        barrier = threading.Barrier(8)

        def grab() -> None:
            barrier.wait()
            seen.append(backend._pane_lock("tmux:%7"))

        threads = [threading.Thread(target=grab) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len({id(x) for x in seen}), 1)


class Issue19DispatchEvidenceSurvives(unittest.TestCase):
    """A proven write must not be reported as if nothing happened.

    One try/except around send AND observation turned an observation failure into a
    bare error indistinguishable from "not sent" — inviting a retry, which on tmux
    is a second write.
    """

    def test_an_observation_failure_becomes_yellow_not_an_error(self) -> None:
        from yapitalism.mcp import server

        class Backend:
            namespace = "tmux"

            def send(self, *a: Any, **k: Any) -> SendOutcome:
                return SendOutcome(phase="injected", dispatched=True, runtime="codex")

            def capabilities(self) -> Any:
                from yapitalism.mcp.backends.tmux_backend import TMUX_CAPABILITIES

                return TMUX_CAPABILITIES

            def list_panes(self) -> list[Any]:
                return []

        def boom(*a: Any, **k: Any) -> AcceptanceOutcome:
            raise BackendError("pane vanished after write")

        with (
            patch.object(server.registry, "resolve", lambda _t: Backend()),
            patch.object(server, "await_acceptance_patiently", boom),
        ):
            out = server.pane_send(target_id="tmux:%0", text="run it")

        self.assertTrue(out["ok"])
        self.assertEqual(out["status"], "YELLOW")
        self.assertEqual(out["phase"], "injected")
        self.assertIn("acceptance_observation_failed", str(out["reason"]))


class Issue20RegistryEntryPoint(unittest.TestCase):
    def test_the_registry_resolved_command_completes_an_mcp_handshake(self) -> None:
        """The exact shape the MCP Registry runs: package-named script + --stdio.

        `server.json` names the package, uvx resolves it to the same-named console
        script, and that script was the argparse CLI — so every registry-driven
        client died before `initialize`. Schema validation cannot catch this; only
        running the resolved command can.
        """
        script = pathlib.Path(sys.executable).parent / "yapitalism"
        if not script.exists():
            raise unittest.SkipTest("console script not installed in this environment")

        proc = subprocess.Popen(
            [str(script), "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            proc.stdin.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {},
                            "clientInfo": {"name": "registry-shape", "version": "1"},
                        },
                    }
                )
                + "\n"
            )
            proc.stdin.flush()
            reply = json.loads(proc.stdout.readline())
        finally:
            proc.kill()
        self.assertEqual(reply["result"]["serverInfo"]["name"], "yapitalism")

    def test_server_json_still_names_the_script_the_shim_covers(self) -> None:
        """If the listing ever stops passing --stdio, the shim stops applying."""
        payload = json.loads((REPO / "server.json").read_text())
        package = payload["packages"][0]
        self.assertEqual(package["identifier"], "yapitalism")
        args = [a.get("name") or a.get("value") for a in package.get("packageArguments", [])]
        self.assertIn("--stdio", args)


class Issue21EvidenceBeforeAuthorization(unittest.TestCase):
    def test_a_failed_ledger_append_leaves_no_consumable_claim(self) -> None:
        """Write-ahead logging, in the component whose job is that evidence is
        authoritative.

        Reversed, a crash between issue and append left a live claim able to
        authorize a confirmed mutation whose dry run was never recorded.
        """
        source = (REPO / "src/yapitalism/cli.py").read_text()
        send = source[source.index("def superset_send(") :]
        body = send[: send.index("\ndef ")]
        append_at = body.index("ledger.append(dispatched.to_evidence(command_id))")
        issue_at = body.index("claims.issue(")
        self.assertLess(append_at, issue_at)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
