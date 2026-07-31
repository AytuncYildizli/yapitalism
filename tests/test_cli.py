from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from relayproof.cli import doctor, main
from relayproof.ledger import JsonlLedger


class CliTests(unittest.TestCase):
    def test_doctor_projects_red_fixture(self) -> None:
        payload = {
            "command_id": "cmd-red",
            "events": [
                {
                    "event_id": "evt-1",
                    "leg": "accept",
                    "state": "failed",
                    "kind": "canary.missed",
                    "provenance": "terminal_diff",
                    "reason": "canary_timeout",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "fixture.json"
            fixture.write_text(json.dumps(payload), encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(doctor(fixture), 0)
            self.assertEqual(
                output.getvalue().strip(),
                "RED command=cmd-red failed=accept reason=canary_timeout",
            )

    def test_superset_status_is_read_only_and_does_not_print_terminal_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "endpoint": "http://127.0.0.1/trpc",
                        "bearer_token": "top-secret",
                        "workspace_id": "workspace-1",
                        "terminal_id": "terminal-1",
                    }
                ),
                encoding="utf-8",
            )
            manifest.chmod(0o600)
            snapshot = SimpleNamespace(
                terminal_id="terminal-1", revision=4, cols=80, rows=24, text="private text"
            )
            output = StringIO()
            with (
                patch("relayproof.cli.SupersetAdapter") as adapter_type,
                redirect_stdout(output),
            ):
                adapter_type.return_value.snapshot.return_value = snapshot
                self.assertEqual(main(["superset", "status", "--manifest", str(manifest)]), 0)
        rendered = output.getvalue()
        self.assertIn('"revision": 4', rendered)
        self.assertNotIn("private text", rendered)
        self.assertNotIn("top-secret", rendered)
        adapter_type.return_value.dispatch.assert_not_called()

    def test_superset_send_defaults_to_zero_network_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "endpoint": "http://127.0.0.1:1/trpc",
                        "bearer_token": "top-secret",
                        "workspace_id": "workspace-1",
                        "terminal_id": "terminal-1",
                    }
                ),
                encoding="utf-8",
            )
            manifest.chmod(0o600)
            output = StringIO()
            with redirect_stdout(output):
                code = main(
                    [
                        "superset",
                        "send",
                        "--manifest",
                        str(manifest),
                        "--text",
                        "private command",
                        "--canary",
                        "CANARY-1",
                        "--expect-revision",
                        "3",
                    ]
                )
        self.assertEqual(code, 0)
        rendered = output.getvalue()
        self.assertIn('"dry_run": true', rendered)
        self.assertIn('"confirmation_required": true', rendered)
        self.assertNotIn("private command", rendered)
        self.assertNotIn("top-secret", rendered)

    def test_superset_dry_run_persists_receipt_and_confirmation_claim(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest.json"
            ledger_path = root / "events.jsonl"
            claim_dir = root / "claims"
            manifest.write_text(
                json.dumps(
                    {
                        "endpoint": "http://127.0.0.1:1/trpc",
                        "bearer_token": "top-secret",
                        "workspace_id": "workspace-1",
                        "terminal_id": "terminal-1",
                    }
                ),
                encoding="utf-8",
            )
            manifest.chmod(0o600)
            output = StringIO()
            with redirect_stdout(output):
                code = main(
                    [
                        "superset",
                        "send",
                        "--manifest",
                        str(manifest),
                        "--text",
                        "private command",
                        "--canary",
                        "RELAYPROOF_ACK_0123456789ABCDEF0123456789ABCDEF",
                        "--expect-revision",
                        "3",
                        "--client-token",
                        "stable-token",
                        "--ledger",
                        str(ledger_path),
                        "--claim-dir",
                        str(claim_dir),
                    ]
                )

            self.assertEqual(code, 0)
            rows = JsonlLedger(ledger_path).read()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["kind"], "terminal.send.dry_run")
            self.assertNotIn("private command", ledger_path.read_text(encoding="utf-8"))
            self.assertNotIn("top-secret", ledger_path.read_text(encoding="utf-8"))
            self.assertEqual(len(list(claim_dir.glob("*.json"))), 1)
            self.assertIn('"command_id": "stable-token"', output.getvalue())

    def test_superset_confirmed_send_snapshots_and_rejects_revision_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "endpoint": "http://127.0.0.1/trpc",
                        "bearer_token": "top-secret",
                        "workspace_id": "workspace-1",
                        "terminal_id": "terminal-1",
                    }
                ),
                encoding="utf-8",
            )
            manifest.chmod(0o600)
            output = StringIO()
            baseline = SimpleNamespace(revision=8, text="private baseline")
            with (
                patch("relayproof.cli.SupersetAdapter") as adapter_type,
                redirect_stdout(output),
            ):
                adapter_type.return_value.snapshot.return_value = baseline
                code = main(
                    [
                        "superset",
                        "send",
                        "--manifest",
                        str(manifest),
                        "--text",
                        "private command",
                        "--canary",
                        "RELAYPROOF_ACK_0123456789ABCDEF0123456789ABCDEF",
                        "--expect-revision",
                        "7",
                        "--confirm-send",
                        "--client-token",
                        "stable-token",
                    ]
                )
        self.assertEqual(code, 2)
        adapter_type.return_value.snapshot.assert_called_once_with(max_lines=1000)
        adapter_type.return_value.dispatch.assert_not_called()
        adapter_type.return_value.await_canary.assert_not_called()
        self.assertNotIn("private baseline", output.getvalue())
        self.assertNotIn("private command", output.getvalue())
        self.assertNotIn("top-secret", output.getvalue())

    def test_superset_confirmed_send_without_client_token_is_zero_network_rejected(self) -> None:
        output = StringIO()
        with patch("relayproof.cli.SupersetAdapter") as adapter_type, redirect_stdout(output):
            code = main(
                [
                    "superset",
                    "send",
                    "--manifest",
                    "/does/not/need/to/exist.json",
                    "--text",
                    "private command",
                    "--canary",
                    "RELAYPROOF_ACK_0123456789ABCDEF0123456789ABCDEF",
                    "--expect-revision",
                    "7",
                    "--confirm-send",
                ]
            )
        self.assertEqual(code, 2)
        adapter_type.assert_not_called()
        self.assertIn("client_token_required_for_confirmed_send", output.getvalue())
        self.assertNotIn("private command", output.getvalue())


if __name__ == "__main__":
    unittest.main()
