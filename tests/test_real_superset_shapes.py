"""The shapes the SHIPPED Superset host actually uses.

Every fixture below was read off the running host on 2026-08-16 — from its tRPC
responses, its Zod validation errors, and `dist/main/host-service.js` extracted
read-only from `app.asar`. None of it is invented, which is the point: this
adapter had been written against a build that does not exist, and every test it
had passed because the tests invented the same shapes the code did.

What was wrong, all four in the same family:

  called                          the host actually has
  ------------------------------  -----------------------------------------
  terminal.listSessions   (404)   terminal.list
  session["agent"]["runtime"]     terminalAgents.listByWorkspace -> agentId
  snapshot requires `revision`    {terminalId, cols, rows, text} — no revision
  "does terminal.send exist?"     "what does terminal.send REQUIRE?"

The last one is the general lesson. `procedure_exists` proved a NAME was routed
and was used to conclude that the host enforced three guarantees; the shipped
`terminal.send` is an unguarded convenience that frames multi-line text as a
bracketed paste. A name is not a contract.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from typing import Any

from yapitalism.adapters.superset import SupersetAdapter, SupersetConfig
from yapitalism.mcp.backends.base import CLIENT
from yapitalism.mcp.backends.superset_backend import SupersetBackend

from test_capability_honesty import WORKSPACE, write_manifest
from test_superset_adapter import FakeTrpcServer, result

TERMINAL = "terminal-1"

#: Verbatim from the running host. Note what is missing: no `revision`.
REAL_SNAPSHOT = {
    "terminalId": TERMINAL,
    "cols": 93,
    "rows": 28,
    "text": "some output\n› Use /skills to list available skills",
}

#: `terminal.list` — live PTYs, and no agent anywhere in the row.
REAL_TERMINAL_LIST = {
    "sessions": [
        {
            "terminalId": TERMINAL,
            "workspaceId": WORKSPACE,
            "createdAt": 1785425889287,
            "exited": False,
            "exitCode": 0,
            "attached": False,
            "title": None,
        }
    ]
}

#: `terminalAgents.listByWorkspace` — a bare list, not wrapped, and the agent is
#: named `agentId`, not `runtime`.
REAL_AGENT_BINDINGS = [
    {
        "terminalId": TERMINAL,
        "workspaceId": WORKSPACE,
        "agentId": "codex",
        "agentSessionId": "019fba9d-ea0e-7e50-bcd5-fa3df5b83e42",
        "startedAt": 1785542354791,
        "lastEventAt": 1785543137739,
        "lastEventType": "Stop",
    }
]

#: What the host's validator says when `terminal.send` is given an empty input.
#: Exactly three required fields, and none of them is a guard.
UNGUARDED_SEND_ISSUES = [
    {"expected": "string", "code": "invalid_type", "path": ["terminalId"]},
    {"expected": "string", "code": "invalid_type", "path": ["workspaceId"]},
    {"expected": "string", "code": "invalid_type", "path": ["text"]},
]

#: What a guarded build's validator would say. Kept so the detection is tested in
#: both directions rather than only against the host we happen to have.
GUARDED_SEND_ISSUES = UNGUARDED_SEND_ISSUES + [
    {"expected": "number", "code": "invalid_type", "path": ["expectRevision"]},
    {"expected": "string", "code": "invalid_type", "path": ["clientToken"]},
]


def zod_400(issues: list[dict[str, Any]]) -> dict[str, Any]:
    """A tRPC 400 carrying a Zod issue list, framed the way the host frames it."""
    return {
        "__status__": 400,
        "error": {"json": {"message": json.dumps(issues), "code": -32600}},
    }


def real_host(
    *,
    send_issues: list[dict[str, Any]] | None = None,
    bindings: list[dict[str, Any]] | None = None,
    written: list[str] | None = None,
) -> Any:
    issues = UNGUARDED_SEND_ISSUES if send_issues is None else send_issues
    rows = REAL_AGENT_BINDINGS if bindings is None else bindings

    def responder(method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if path.endswith("terminal.send"):
            return zod_400(issues)
        if path.endswith("terminal.list"):
            return result(REAL_TERMINAL_LIST)
        if path.endswith("terminalAgents.listByWorkspace"):
            return result(rows)
        if path.endswith("terminal.snapshot"):
            return result(dict(REAL_SNAPSHOT))
        if path.endswith("terminal.writeInput"):
            if written is not None:
                written.append(payload["data"])
            return result({"success": True})
        if path.endswith("workspace.list"):
            return result([{"id": WORKSPACE, "name": "Watch Voice Lane"}])
        if path.endswith("terminal.listSessions"):
            # The name this adapter used to call. The host answers 404 for it, and
            # a test that silently served it would hide the whole defect.
            return {"__status__": 404}
        raise AssertionError(f"unexpected procedure: {path}")

    return responder


class SnapshotWithoutRevisionTests(unittest.TestCase):
    def test_a_snapshot_with_no_revision_is_read_not_refused(self) -> None:
        """Requiring `revision` made every Superset send raise before writing.

        The shipped snapshot has no such field, so this was not a degraded read —
        it was a total one, and it is why no send could complete.
        """
        with FakeTrpcServer(real_host()) as server, tempfile.TemporaryDirectory() as tmp:
            adapter = SupersetAdapter(
                SupersetConfig.from_manifest(write_manifest(tmp, server.endpoint))
            )
            snapshot = adapter.snapshot()
        self.assertEqual(snapshot.text, REAL_SNAPSHOT["text"])
        self.assertIsInstance(snapshot.revision, int)
        self.assertGreaterEqual(snapshot.revision, 0)

    def test_a_derived_revision_still_detects_a_change(self) -> None:
        """It cannot ORDER two changes, and it does not need to.

        The optimistic check asks one question — did this change under me — and a
        content-derived number answers exactly that. Anything stronger would have
        to come from the host, which is why `optimistic_revision` reports `client`.
        """
        moved = dict(REAL_SNAPSHOT, text=REAL_SNAPSHOT["text"] + "\nmore output")

        def responder(method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
            if path.endswith("terminal.snapshot"):
                return result(moved if calls.append(1) or len(calls) > 1 else dict(REAL_SNAPSHOT))
            return real_host()(method, path, payload)

        calls: list[int] = []
        with FakeTrpcServer(responder) as server, tempfile.TemporaryDirectory() as tmp:
            adapter = SupersetAdapter(
                SupersetConfig.from_manifest(write_manifest(tmp, server.endpoint))
            )
            first = adapter.snapshot().revision
            second = adapter.snapshot().revision
        self.assertNotEqual(first, second)

    def test_a_derived_revision_going_backwards_is_not_a_reset(self) -> None:
        """Found live, after the write had already landed.

        The acceptance poller aborts if the revision drops, which is correct for a
        HOST counter — it means the terminal was replaced under us. A derived
        revision is a hash of the screen and moves in both directions as output
        scrolls, so that guard killed every poll on the shipped build and turned a
        delivered message into an error.
        """
        import zlib

        from yapitalism.mcp.receipt import CANARY_PREFIX

        canary = CANARY_PREFIX + "A" * 32
        baseline = REAL_SNAPSHOT["text"]
        # Two screens chosen so the SECOND hashes lower than the first — the exact
        # shape the old guard called a reset. Picked by measuring, not by hoping.
        lower = next(
            candidate
            for candidate in (f"scrolled {n}" for n in range(5000))
            if zlib.crc32(candidate.encode()) < zlib.crc32(baseline.encode())
        )
        screens = [lower, f"{lower}\n{canary}"]

        def responder(method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
            if path.endswith("terminal.snapshot"):
                text = screens[min(len(seen), len(screens) - 1)]
                seen.append(1)
                return result(dict(REAL_SNAPSHOT, text=text))
            return real_host()(method, path, payload)

        seen: list[int] = []
        with FakeTrpcServer(responder) as server, tempfile.TemporaryDirectory() as tmp:
            adapter = SupersetAdapter(
                SupersetConfig.from_manifest(write_manifest(tmp, server.endpoint))
            )
            self.assertTrue(adapter.snapshot().revision_is_derived)
            outcome = adapter.await_canary(
                canary,
                baseline_revision=zlib.crc32(baseline.encode()),
                baseline_text=baseline,
                submitted_text="do the thing",
                command_id="derived-revision",
                timeout=5.0,
                poll_interval=0.01,
            )
        self.assertTrue(outcome.observed)

    def test_a_host_that_does_send_a_revision_is_believed(self) -> None:
        """A guarded build's counter must not be thrown away for a hash of text."""
        def responder(method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
            if path.endswith("terminal.snapshot"):
                return result(dict(REAL_SNAPSHOT, revision=41))
            return real_host()(method, path, payload)

        with FakeTrpcServer(responder) as server, tempfile.TemporaryDirectory() as tmp:
            adapter = SupersetAdapter(
                SupersetConfig.from_manifest(write_manifest(tmp, server.endpoint))
            )
            self.assertEqual(adapter.snapshot().revision, 41)


class AgentBindingTests(unittest.TestCase):
    def test_the_runtime_comes_from_the_agent_binding(self) -> None:
        """`agentId` on `terminalAgents.listByWorkspace`, not `agent.runtime`.

        Reading a field no build has returned "unknown" for every terminal, and the
        gate above it then refused every Superset send as `rejected_not_an_agent`.
        """
        with FakeTrpcServer(real_host()) as server, tempfile.TemporaryDirectory() as tmp:
            adapter = SupersetAdapter(
                SupersetConfig.from_manifest(write_manifest(tmp, server.endpoint))
            )
            self.assertEqual(adapter.registry_runtime(), "codex")

    def test_a_terminal_with_no_binding_is_unknown_and_refused(self) -> None:
        """A live PTY with no agent is a plain terminal. Writing there runs a command."""
        with FakeTrpcServer(real_host(bindings=[])) as server, tempfile.TemporaryDirectory() as tmp:
            manifest = write_manifest(tmp, server.endpoint)
            adapter = SupersetAdapter(SupersetConfig.from_manifest(manifest))
            self.assertEqual(adapter.registry_runtime(), "unknown")
            outcome = SupersetBackend(manifest_path=manifest).send(
                f"superset:{TERMINAL}", "echo owned", canary=None, client_token="t1"
            )
        self.assertEqual(outcome.phase, "rejected_not_an_agent")
        self.assertFalse(outcome.dispatched)

    def test_an_agent_this_project_cannot_launch_reads_as_unknown(self) -> None:
        """The host knows a dozen agents; this project has launchers for three.

        Over-refusing is the safe direction: `gemini` is genuinely an agent, and
        treating it as one we understand would be a guess about its prompt.
        """
        gemini = [dict(REAL_AGENT_BINDINGS[0], agentId="gemini")]
        with FakeTrpcServer(real_host(bindings=gemini)) as server, tempfile.TemporaryDirectory() as tmp:
            adapter = SupersetAdapter(
                SupersetConfig.from_manifest(write_manifest(tmp, server.endpoint))
            )
            self.assertEqual(adapter.registry_runtime(), "unknown")

    def test_panes_carry_the_agent_id_as_their_runtime(self) -> None:
        with FakeTrpcServer(real_host()) as server, tempfile.TemporaryDirectory() as tmp:
            backend = SupersetBackend(manifest_path=write_manifest(tmp, server.endpoint))
            panes = backend.list_panes()
        self.assertEqual([p.runtime for p in panes], ["codex"])

    def test_a_host_we_cannot_enumerate_is_not_reported_as_empty(self) -> None:
        """The failure that hid all of this for two days.

        `list_panes` swallows a per-workspace error so one bad workspace cannot
        hide the rest — but when the procedure itself is missing, every workspace
        fails, and a host we could not read at all reported zero terminals.
        """
        def broken(method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
            if path.endswith("terminal.list"):
                return {"__status__": 404}
            return real_host()(method, path, payload)

        from yapitalism.mcp.backends.base import BackendError

        with FakeTrpcServer(broken) as server, tempfile.TemporaryDirectory() as tmp:
            backend = SupersetBackend(manifest_path=write_manifest(tmp, server.endpoint))
            with self.assertRaises(BackendError) as caught:
                backend.list_panes()
        self.assertIn("does not route", str(caught.exception))

    def test_one_bad_workspace_still_does_not_hide_the_others(self) -> None:
        """The case the swallow was written for, which must keep working.

        A workspace that errors for its own reasons is skipped; a procedure the
        host does not route is not. The difference is whether the failure says
        anything about the rest of the host.
        """
        other = "workspace-2"

        def one_bad(method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
            if path.endswith("workspace.list"):
                return result([{"id": WORKSPACE, "name": "ok"}, {"id": other, "name": "bad"}])
            if path.endswith("terminal.list") and payload.get("workspaceId") == other:
                return {"__status__": 500}
            return real_host()(method, path, payload)

        with FakeTrpcServer(one_bad) as server, tempfile.TemporaryDirectory() as tmp:
            backend = SupersetBackend(manifest_path=write_manifest(tmp, server.endpoint))
            panes = backend.list_panes()
        self.assertEqual([p.runtime for p in panes], ["codex"])


class WriteThroughTheHostsOwnSendTests(unittest.TestCase):
    """Found by driving the published build through its own MCP surface.

    The direct test had called `capabilities()` first, which cached the answer, so
    the fallback was taken and none of this was visible. Order-dependent behaviour
    hides in exactly that gap.
    """

    def send(self, text: str, *, host_send: bool = True) -> tuple[Any, list[dict[str, Any]]]:
        seen: list[dict[str, Any]] = []
        empty = REAL_SNAPSHOT["text"]

        def responder(method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
            if path.endswith("terminal.send"):
                if not payload.get("terminalId"):
                    return zod_400(UNGUARDED_SEND_ISSUES) if host_send else {"__status__": 404}
                if not host_send:
                    return {"__status__": 404}
                seen.append(dict(payload, __procedure__="terminal.send"))
                return result({"terminalId": TERMINAL})
            if path.endswith("terminal.writeInput"):
                seen.append(dict(payload, __procedure__="terminal.writeInput"))
                return result({"success": True})
            if path.endswith("terminal.snapshot"):
                return result(dict(REAL_SNAPSHOT, text=empty))
            return real_host()(method, path, payload)

        with FakeTrpcServer(responder) as server, tempfile.TemporaryDirectory() as tmp:
            backend = SupersetBackend(manifest_path=write_manifest(tmp, server.endpoint))
            outcome = backend.send(
                f"superset:{TERMINAL}", text, canary=None, client_token="w1"
            )
        return outcome, seen

    def test_an_unguarded_host_is_never_sent_a_guarded_payload(self) -> None:
        """The guarded attempt used to LAND before we noticed it was unguarded.

        Zod strips fields it does not know, so `{terminalId, workspaceId, text}`
        validated, the message was written, and only then did parsing fail for want
        of a `phase` the response never had — a delivered message returned as a bare
        error, indistinguishable from nothing having happened.
        """
        _, seen = self.send("one line")
        writes = [call for call in seen if call["__procedure__"] == "terminal.send"]
        self.assertEqual(len(writes), 1)
        self.assertNotIn("expectRevision", writes[0])
        self.assertNotIn("clientToken", writes[0])

    def test_the_write_goes_through_the_hosts_send_when_it_routes(self) -> None:
        outcome, seen = self.send("one line")
        self.assertTrue(outcome.dispatched)
        self.assertEqual(outcome.phase, "injected")
        self.assertEqual([c["__procedure__"] for c in seen], ["terminal.send"])

    def test_multi_line_text_is_accepted_when_the_host_can_frame_it(self) -> None:
        """`prove_acceptance` appends the canary instruction, which adds a newline.

        Through raw `writeInput` a newline IS a submit, so the fallback refuses
        multi-line — which meant every proven send on the shipped host was refused.
        The host's own `send` frames it as a bracketed paste.
        """
        outcome, seen = self.send("first line\nsecond line")
        self.assertTrue(outcome.dispatched)
        self.assertIn("\n", seen[0]["text"])

    def test_multi_line_is_still_refused_where_only_writeInput_exists(self) -> None:
        from yapitalism.mcp.backends.base import BackendError

        with self.assertRaises(BackendError) as caught:
            self.send("first\nsecond", host_send=False)
        self.assertIn("single line", str(caught.exception))

    def test_submission_is_verified_rather_than_taken_on_trust(self) -> None:
        """`submit: true` is the host saying what it meant to do.

        `writeInput` reporting success for delivering bytes was measured to be
        exactly that kind of claim, and it left text staged while the receipt said
        sent. So the prompt is read afterwards either way.
        """
        staged = "› first line\nsecond line"

        def responder(method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
            if path.endswith("terminal.send"):
                if not payload.get("terminalId"):
                    return zod_400(UNGUARDED_SEND_ISSUES)
                writes.append(payload)
                return result({"terminalId": TERMINAL})
            if path.endswith("terminal.snapshot"):
                # Empty before the write, and the composer keeps the text after it.
                return result(
                    dict(REAL_SNAPSHOT, text=staged if writes else REAL_SNAPSHOT["text"])
                )
            if path.endswith("terminal.writeInput"):
                return result({"success": True})
            return real_host()(method, path, payload)

        writes: list[dict[str, Any]] = []
        with FakeTrpcServer(responder) as server, tempfile.TemporaryDirectory() as tmp:
            backend = SupersetBackend(manifest_path=write_manifest(tmp, server.endpoint))
            outcome = backend.send(
                f"superset:{TERMINAL}", "one line", canary=None, client_token="w2"
            )
        self.assertEqual(outcome.phase, "staged_not_submitted")
        self.assertFalse(outcome.dispatched)


class SendContractTests(unittest.TestCase):
    """A routed name is not an enforced guarantee."""

    def capabilities(self, issues: list[dict[str, Any]]) -> Any:
        with FakeTrpcServer(real_host(send_issues=issues)) as server, tempfile.TemporaryDirectory() as tmp:
            backend = SupersetBackend(manifest_path=write_manifest(tmp, server.endpoint))
            return backend.capabilities()

    def test_the_shipped_send_is_reported_as_client_enforced(self) -> None:
        """It exists, it routes, it works — and it guards nothing.

        `procedure_exists("terminal.send")` returning True was read as three host
        guarantees on every install of Superset there has ever been.
        """
        caps = self.capabilities(UNGUARDED_SEND_ISSUES)
        self.assertEqual(caps.idempotent_dispatch, CLIENT)
        self.assertEqual(caps.optimistic_revision, CLIENT)
        self.assertEqual(caps.empty_prompt_check, CLIENT)

    def test_a_send_that_requires_the_guards_is_reported_as_host_enforced(self) -> None:
        """Tested in both directions, so the detection is not just "always client"."""
        caps = self.capabilities(GUARDED_SEND_ISSUES)
        self.assertEqual(caps.idempotent_dispatch, "host")
        self.assertEqual(caps.optimistic_revision, "host")
        self.assertEqual(caps.empty_prompt_check, "host")

    def test_required_fields_are_read_from_the_hosts_own_validator(self) -> None:
        with FakeTrpcServer(real_host()) as server, tempfile.TemporaryDirectory() as tmp:
            adapter = SupersetAdapter(
                SupersetConfig.from_manifest(write_manifest(tmp, server.endpoint))
            )
            required = adapter._transport.required_input_fields("terminal.send")
        self.assertEqual(required, {"terminalId", "workspaceId", "text"})
        self.assertNotIn("expectRevision", required)

    def test_an_unanswerable_probe_claims_nothing(self) -> None:
        """Silence must not read as a guarantee.

        A host that 404s, times out or answers something unparseable yields an
        empty set, and an empty set can never contain the guards — so the failure
        direction is "client", never "host".
        """
        def mute(method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
            if path.endswith("terminal.send"):
                return {"__status__": 500}
            return real_host()(method, path, payload)

        with FakeTrpcServer(mute) as server, tempfile.TemporaryDirectory() as tmp:
            backend = SupersetBackend(manifest_path=write_manifest(tmp, server.endpoint))
            caps = backend.capabilities()
        self.assertEqual(caps.idempotent_dispatch, CLIENT)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
