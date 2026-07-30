from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from relayproof.adapters.superset import SupersetAdapter, SupersetConfig, TrpcError
from relayproof.model import Leg, LegState

CANARY = "RELAYPROOF_ACK_0123456789ABCDEF0123456789ABCDEF"


class FakeTrpcServer:
    def __init__(self, responder: Callable[[str, str, dict[str, Any]], dict[str, Any]]) -> None:
        self.requests: list[dict[str, Any]] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                return

            def _handle(self) -> None:
                if self.command == "GET":
                    from urllib.parse import parse_qs, urlsplit

                    parsed = urlsplit(self.path)
                    wire_input = json.loads(parse_qs(parsed.query)["input"][0])
                    path = parsed.path
                else:
                    length = int(self.headers.get("content-length", "0"))
                    wire_input = json.loads(self.rfile.read(length))
                    path = self.path
                payload = cast(dict[str, Any], wire_input["json"])
                owner.requests.append(
                    {
                        "method": self.command,
                        "path": path,
                        "authorization_present": self.headers.get("authorization") is not None,
                        "payload": payload,
                    }
                )
                response = responder(self.command, path, payload)
                if response.pop("__close__", False):
                    self.connection.close()
                    return
                status = int(response.pop("__status__", 200))
                delay = float(response.pop("__delay__", 0))
                raw_override = response.pop("__raw__", None)
                headers = cast(dict[str, str], response.pop("__headers__", {}))
                if delay:
                    time.sleep(delay)
                body = (
                    cast(bytes, raw_override)
                    if isinstance(raw_override, bytes)
                    else json.dumps(response).encode()
                )
                try:
                    self.send_response(status)
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", str(len(body)))
                    for name, value in headers.items():
                        self.send_header(name, value)
                    self.end_headers()
                    self.wfile.write(body)
                except BrokenPipeError:
                    pass

            do_GET = _handle
            do_POST = _handle

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def endpoint(self) -> str:
        host, port = self.httpd.server_address
        return f"http://{host}:{port}/trpc"

    def __enter__(self) -> FakeTrpcServer:
        self.thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.httpd.shutdown()
        self.thread.join()
        self.httpd.server_close()


def result(data: dict[str, Any]) -> dict[str, Any]:
    return {"result": {"data": {"json": data}}}


def snapshot_result(*, text: str = "safe", revision: int = 7, terminal_id: str = "terminal-1") -> dict[str, Any]:
    return result(
        {
            "terminalId": terminal_id,
            "text": text,
            "revision": revision,
            "cols": 120,
            "rows": 40,
        }
    )


def send_result(
    *,
    terminal_id: str = "terminal-1",
    phase: str = "injected",
    submit_sent: bool = True,
    duplicate: bool = False,
    revision: int = 9,
    prompt_status: str = "empty",
    runtime: str = "codex",
) -> dict[str, Any]:
    return result(
        {
            "deliveryId": "delivery-1",
            "terminalId": terminal_id,
            "phase": phase,
            "verified": False,
            "submitSent": submit_sent,
            "submitted": submit_sent,
            "duplicate": duplicate,
            "revisionBefore": revision,
            "revisionAfter": revision,
            "promptStatus": prompt_status,
            "target": {"runtime": runtime},
        }
    )


class SupersetAdapterTests(unittest.TestCase):
    def config(self, endpoint: str, *, timeout: float = 1.0) -> SupersetConfig:
        return SupersetConfig(endpoint, "synthetic-secret", "workspace-1", "terminal-1", timeout)

    def write_manifest(self, path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8")
        path.chmod(0o600)

    def test_config_manifest_is_strict_bounded_and_secret_is_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "superset.json"
            self.write_manifest(
                manifest,
                json.dumps(
                    {
                        "endpoint": "http://127.0.0.1:9999/trpc/",
                        "bearer_token": "synthetic-secret",
                        "workspace_id": "workspace-1",
                        "terminal_id": "terminal-1",
                    }
                ),
            )
            config = SupersetConfig.from_manifest(manifest)
        self.assertEqual(config.endpoint, "http://127.0.0.1:9999/trpc")
        self.assertNotIn("synthetic-secret", repr(config))
        self.assertIn("<redacted>", repr(config))

    def test_unsafe_manifests_fail_without_raw_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            valid = root / "valid.json"
            self.write_manifest(valid, "{}")
            symlink = root / "link.json"
            symlink.symlink_to(valid)
            world = root / "world.json"
            self.write_manifest(world, "{}")
            world.chmod(0o644)
            duplicate = root / "duplicate.json"
            self.write_manifest(duplicate, '{"bearer_token":"DO-NOT-LEAK","bearer_token":"x"}')
            unknown = root / "unknown.json"
            self.write_manifest(unknown, '{"bearer":"DO-NOT-LEAK"}')
            malformed = root / "malformed.json"
            self.write_manifest(malformed, '{"bearer_token":"DO-NOT-LEAK"')
            oversized = root / "oversized.json"
            self.write_manifest(oversized, "x" * (64 * 1024 + 1))
            for path in (symlink, world, duplicate, unknown, malformed, oversized):
                with self.subTest(path=path.name), self.assertRaises(ValueError) as caught:
                    SupersetConfig.from_manifest(path)
                self.assertNotIn("DO-NOT-LEAK", str(caught.exception))

    def test_endpoint_restrictions_reject_non_loopback_and_wrong_surface(self) -> None:
        rejected = (
            "http://169.254.169.254/trpc",
            "http://0.0.0.0/trpc",
            "http://10.0.0.1/trpc",
            "http://user@127.0.0.1/trpc",
            "http://127.0.0.1/api/trpc",
            "http://127.0.0.1/trpc?x=1",
            "http://127.0.0.1/trpc#x",
            "http://2130706433/trpc",
            "http://[::ffff:127.0.0.1]/trpc",
            "file:///trpc",
        )
        with patch("socket.create_connection") as connect:
            for endpoint in rejected:
                with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                    self.config(endpoint)
            connect.assert_not_called()

    def test_snapshot_uses_exact_real_get_contract(self) -> None:
        def responder(method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
            self.assertEqual(method, "GET")
            self.assertEqual(path, "/trpc/terminal.snapshot")
            return snapshot_result(text="private terminal text")

        with FakeTrpcServer(responder) as server:
            snapshot = SupersetAdapter(self.config(server.endpoint)).snapshot(max_lines=50)
        self.assertEqual(snapshot.revision, 7)
        evidence = snapshot.to_evidence("cmd-1")
        self.assertIs(evidence.leg, Leg.CAPTURE)
        self.assertNotIn("private terminal text", repr(snapshot))
        self.assertNotIn("private terminal text", evidence.evidence_ref)
        self.assertEqual(
            server.requests[0]["payload"],
            {"terminalId": "terminal-1", "workspaceId": "workspace-1", "maxLines": 50},
        )
        self.assertTrue(server.requests[0]["authorization_present"])

    def test_wrong_snapshot_target_is_rejected(self) -> None:
        with FakeTrpcServer(lambda *_: snapshot_result(terminal_id="terminal-2")) as server:
            with self.assertRaisesRegex(TrpcError, "target mismatch"):
                SupersetAdapter(self.config(server.endpoint)).snapshot()

    def test_send_is_dry_run_then_exact_confirmed_post(self) -> None:
        def responder(method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
            self.assertEqual(method, "POST")
            self.assertEqual(path, "/trpc/terminal.send")
            return send_result()

        with FakeTrpcServer(responder) as server:
            adapter = SupersetAdapter(self.config(server.endpoint))
            dry_run = adapter.dispatch("do work", expected_revision=9, client_token="cmd-1")
            self.assertTrue(dry_run.dry_run)
            self.assertEqual(server.requests, [])
            sent = adapter.dispatch(
                "do work", expected_revision=9, client_token="cmd-1", confirm=True
            )
        self.assertTrue(sent.dispatched)
        self.assertEqual(
            server.requests[0]["payload"],
            {
                "terminalId": "terminal-1",
                "workspaceId": "workspace-1",
                "text": "do work",
                "submit": True,
                "clientToken": "cmd-1",
                "requireEmptyPrompt": True,
                "allowRepeat": False,
                "expectRevision": 9,
            },
        )

    def test_ambiguous_post_transport_failure_is_not_retried(self) -> None:
        with FakeTrpcServer(lambda *_: {"__close__": True}) as server:
            adapter = SupersetAdapter(self.config(server.endpoint))
            with self.assertRaises(TrpcError):
                adapter.dispatch("work", expected_revision=9, client_token="stable", confirm=True)
        self.assertEqual(len(server.requests), 1)
        self.assertEqual(server.requests[0]["method"], "POST")

    def test_duplicate_and_stale_phase_never_dispatch(self) -> None:
        responses = iter(
            [
                send_result(phase="duplicate_ignored", submit_sent=False, duplicate=True),
                send_result(phase="rejected_revision_changed", submit_sent=False),
            ]
        )
        with FakeTrpcServer(lambda *_: next(responses)) as server:
            adapter = SupersetAdapter(self.config(server.endpoint))
            duplicate = adapter.dispatch("one", expected_revision=9, client_token="a", confirm=True)
            stale = adapter.dispatch("two", expected_revision=9, client_token="b", confirm=True)
        self.assertFalse(duplicate.dispatched)
        self.assertFalse(stale.dispatched)
        self.assertIs(duplicate.to_evidence("a").state, LegState.FAILED)

    def test_token_reuse_with_mutated_text_or_revision_fails_locally(self) -> None:
        with FakeTrpcServer(lambda *_: send_result()) as server:
            adapter = SupersetAdapter(self.config(server.endpoint))
            adapter.dispatch("one", expected_revision=9, client_token="stable")
            with self.assertRaises(ValueError):
                adapter.dispatch("two", expected_revision=9, client_token="stable", confirm=True)
            with self.assertRaises(ValueError):
                adapter.dispatch("one", expected_revision=10, client_token="stable", confirm=True)
        self.assertEqual(server.requests, [])

    def test_wrong_send_target_and_malformed_semantics_are_rejected(self) -> None:
        responses = iter(
            [send_result(terminal_id="terminal-2"), send_result(runtime="bogus")]
        )
        with FakeTrpcServer(lambda *_: next(responses)) as server:
            adapter = SupersetAdapter(self.config(server.endpoint))
            for token in ("a", "b"):
                with self.assertRaises(TrpcError):
                    adapter.dispatch("work", expected_revision=9, client_token=token, confirm=True)

    def test_shell_unknown_prompt_is_not_prompt_verified(self) -> None:
        with FakeTrpcServer(
            lambda *_: send_result(prompt_status="unknown", runtime="shell")
        ) as server:
            sent = SupersetAdapter(self.config(server.endpoint)).dispatch(
                "work", expected_revision=9, client_token="a", confirm=True
            )
        self.assertTrue(sent.dispatched)
        self.assertFalse(sent.prompt_verified)
        self.assertEqual(sent.to_evidence("a").reason, "prompt_not_verified")

    def test_redirect_is_rejected_and_bearer_is_not_forwarded(self) -> None:
        target_hits = 0

        def target(_: str, __: str, ___: dict[str, Any]) -> dict[str, Any]:
            nonlocal target_hits
            target_hits += 1
            return snapshot_result()

        with FakeTrpcServer(target) as target_server:
            def redirect(_: str, __: str, ___: dict[str, Any]) -> dict[str, Any]:
                return {
                    "__status__": 302,
                    "__headers__": {"Location": f"{target_server.endpoint}/terminal.snapshot"},
                }

            with FakeTrpcServer(redirect) as redirect_server:
                with self.assertRaisesRegex(TrpcError, "redirect rejected"):
                    SupersetAdapter(self.config(redirect_server.endpoint)).snapshot()
        self.assertEqual(target_hits, 0)

    def test_proxy_environment_is_ignored(self) -> None:
        with patch.dict(
            os.environ,
            {"HTTP_PROXY": "http://127.0.0.1:1", "NO_PROXY": ""},
            clear=False,
        ):
            with FakeTrpcServer(lambda *_: snapshot_result()) as server:
                snapshot = SupersetAdapter(self.config(server.endpoint)).snapshot()
        self.assertEqual(snapshot.revision, 7)

    def test_oversized_response_is_rejected_before_json_parse(self) -> None:
        with FakeTrpcServer(lambda *_: {"__raw__": b"x" * (1024 * 1024 + 1)}) as server:
            with self.assertRaisesRegex(TrpcError, "size limit"):
                SupersetAdapter(self.config(server.endpoint)).snapshot()

    def test_timeout_and_error_never_include_secret_or_terminal_content(self) -> None:
        responses = iter(
            [
                {"__delay__": 0.1, **result({})},
                {"error": {"json": {"message": "synthetic-secret PRIVATE-TEXT"}}},
            ]
        )
        with FakeTrpcServer(lambda *_: next(responses)) as server:
            adapter = SupersetAdapter(self.config(server.endpoint, timeout=0.01))
            for _ in range(2):
                with self.assertRaises(TrpcError) as caught:
                    adapter.snapshot()
                rendered = str(caught.exception)
                self.assertNotIn("synthetic-secret", rendered)
                self.assertNotIn("PRIVATE-TEXT", rendered)

    def test_canary_rejects_literal_prompt_and_baseline_before_polling(self) -> None:
        with FakeTrpcServer(lambda *_: snapshot_result()) as server:
            adapter = SupersetAdapter(self.config(server.endpoint))
            with self.assertRaisesRegex(ValueError, "submitted text"):
                adapter.await_canary(
                    CANARY,
                    command_id="cmd",
                    baseline_revision=1,
                    baseline_text="clean",
                    submitted_text=f"print {CANARY}",
                )
            with self.assertRaisesRegex(ValueError, "baseline"):
                adapter.await_canary(
                    CANARY,
                    command_id="cmd",
                    baseline_revision=1,
                    baseline_text=CANARY,
                    submitted_text="derive response",
                )
        self.assertEqual(server.requests, [])

    def test_canary_requires_structured_marker_and_advanced_revision(self) -> None:
        snapshots = iter(
            [snapshot_result(text=CANARY, revision=10), snapshot_result(text=CANARY, revision=11)]
        )
        with FakeTrpcServer(lambda *_: next(snapshots)) as server:
            observed = SupersetAdapter(self.config(server.endpoint)).await_canary(
                CANARY,
                command_id="cmd",
                baseline_revision=10,
                baseline_text="clean",
                submitted_text="derive response",
                timeout=1,
                poll_interval=0.01,
            )
        self.assertTrue(observed.observed)
        self.assertEqual(observed.attempts, 2)
        self.assertEqual(observed.last_revision, 11)
        with self.assertRaises(ValueError):
            SupersetAdapter(self.config("http://127.0.0.1:9999/trpc")).validate_canary(
                "CANARY-123", baseline_text="", submitted_text=""
            )

    def test_revision_movement_without_canary_times_out_red_with_max_attempts(self) -> None:
        revision = 10

        def responder(*_: object) -> dict[str, Any]:
            nonlocal revision
            revision += 1
            return snapshot_result(text="spinner", revision=revision)

        with FakeTrpcServer(responder) as server:
            missed = SupersetAdapter(self.config(server.endpoint)).await_canary(
                CANARY,
                command_id="cmd",
                baseline_revision=10,
                baseline_text="clean",
                submitted_text="derive response",
                timeout=10,
                poll_interval=0,
                max_attempts=3,
            )
        self.assertFalse(missed.observed)
        self.assertEqual(missed.attempts, 3)
        self.assertIs(missed.evidence.state, LegState.FAILED)
        self.assertEqual(missed.evidence.reason, "canary_timeout")

    def test_revision_reset_during_polling_fails_closed(self) -> None:
        with FakeTrpcServer(lambda *_: snapshot_result(revision=4)) as server:
            with self.assertRaisesRegex(TrpcError, "revision reset"):
                SupersetAdapter(self.config(server.endpoint)).await_canary(
                    CANARY,
                    command_id="cmd",
                    baseline_revision=5,
                    baseline_text="clean",
                    submitted_text="derive response",
                    timeout=1,
                )


if __name__ == "__main__":
    unittest.main()
