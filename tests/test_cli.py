from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from relayproof.adapters.superset import CanaryResult, DispatchResult, TerminalSnapshot, TrpcError
from relayproof.claims import ConfirmationClaimStore
from relayproof.cli import doctor, main
from relayproof.ledger import JsonlLedger
from relayproof.model import EvidenceEvent, Leg, LegState, Provenance


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
                        "CANARY-1",
                        "--expect-revision",
                        "3",
                        "--ledger",
                        str(ledger_path),
                        "--claim-dir",
                        str(claim_dir),
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
            projected = StringIO()
            with redirect_stdout(projected):
                self.assertEqual(
                    main(["receipt", "show", "stable-token", "--ledger", str(ledger_path)]),
                    0,
                )
            self.assertIn("YELLOW command=stable-token", projected.getvalue())

    def test_receipt_show_rejects_unknown_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = StringIO()
            with redirect_stdout(output):
                code = main(
                    [
                        "receipt",
                        "show",
                        "missing-command",
                        "--ledger",
                        str(Path(directory) / "events.jsonl"),
                    ]
                )
            self.assertEqual(code, 2)
            self.assertIn("receipt_not_found", output.getvalue())

    def test_confirm_without_claim_blocks_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest.json"
            manifest.write_text("{}", encoding="utf-8")
            baseline = SimpleNamespace(revision=7, text="clean")
            adapter = MagicMock()
            adapter.config.terminal_id = "terminal-1"
            adapter.snapshot.return_value = baseline
            output = StringIO()
            with patch("relayproof.cli.SupersetConfig.from_manifest"), patch(
                "relayproof.cli.SupersetAdapter", return_value=adapter
            ), redirect_stdout(output):
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
                        "--client-token",
                        "missing-token",
                        "--confirm-send",
                        "--ledger",
                        str(root / "events.jsonl"),
                        "--claim-dir",
                        str(root / "claims"),
                    ]
                )
            self.assertEqual(code, 2)
            self.assertIn("confirmation_claim_rejected", output.getvalue())
            self.assertNotIn("private command", output.getvalue())
            adapter.dispatch.assert_not_called()

    def test_full_dry_run_confirm_flow_persists_dispatch_and_accept(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest.json"
            ledger = root / "events.jsonl"
            claims = root / "claims"
            manifest.write_text("{}", encoding="utf-8")
            adapter = MagicMock()
            adapter.config.terminal_id = "terminal-1"
            adapter.snapshot.return_value = TerminalSnapshot("terminal-1", "clean", 7, 80, 24)

            def dispatch(_text: str, *, expected_revision: int, client_token: str, confirm: bool):
                return DispatchResult(
                    client_token=client_token,
                    terminal_id="terminal-1",
                    delivery_id="11111111-1111-4111-8111-111111111111" if confirm else None,
                    phase="injected" if confirm else "dry_run",
                    submit_sent=confirm,
                    duplicate=False,
                    revision_before=expected_revision,
                    expected_revision=expected_revision,
                    prompt_status="empty" if confirm else "unknown",
                    target_runtime="claude" if confirm else "unknown",
                    revision_after=8 if confirm else None,
                    dry_run=not confirm,
                )

            adapter.dispatch.side_effect = dispatch
            adapter.await_canary.return_value = CanaryResult(
                True,
                1,
                8,
                EvidenceEvent(
                    event_id="accept-1",
                    command_id="stable-token",
                    leg=Leg.ACCEPT,
                    state=LegState.SUCCEEDED,
                    kind="canary.observed",
                    provenance=Provenance.TERMINAL_DIFF,
                ),
            )
            common = [
                "superset", "send", "--manifest", str(manifest),
                "--text", "private command",
                "--canary", "RELAYPROOF_ACK_0123456789ABCDEF0123456789ABCDEF",
                "--expect-revision", "7",
                "--client-token", "stable-token",
                "--ledger", str(ledger),
                "--claim-dir", str(claims),
            ]
            with patch("relayproof.cli.SupersetConfig.from_manifest"), patch(
                "relayproof.cli.SupersetAdapter", return_value=adapter
            ), redirect_stdout(StringIO()):
                self.assertEqual(main(common), 0)
                self.assertEqual(main([*common, "--confirm-send"]), 0)

            rows = JsonlLedger(ledger).read()
            self.assertEqual(
                [row["kind"] for row in rows],
                ["terminal.send.dry_run", "terminal.send", "canary.observed"],
            )

    def test_ambiguous_confirm_is_persisted_without_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = root / "events.jsonl"
            claims = root / "claims"
            ConfirmationClaimStore(claims).issue(
                client_token="stable-token",
                command_id="stable-token",
                text="private command",
                expected_revision=7,
                terminal_id="terminal-1",
            )
            adapter = MagicMock()
            adapter.config.terminal_id = "terminal-1"
            adapter.snapshot.return_value = TerminalSnapshot("terminal-1", "clean", 7, 80, 24)
            adapter.dispatch.side_effect = TrpcError("Superset tRPC transport failed")
            output = StringIO()
            with patch("relayproof.cli.SupersetConfig.from_manifest"), patch(
                "relayproof.cli.SupersetAdapter", return_value=adapter
            ), redirect_stdout(output):
                code = main(
                    [
                        "superset", "send", "--manifest", str(root / "manifest.json"),
                        "--text", "private command",
                        "--canary", "RELAYPROOF_ACK_0123456789ABCDEF0123456789ABCDEF",
                        "--expect-revision", "7",
                        "--client-token", "stable-token", "--confirm-send",
                        "--ledger", str(ledger), "--claim-dir", str(claims),
                    ]
                )
            self.assertEqual(code, 2)
            self.assertIn("transport_ambiguous", output.getvalue())
            rows = JsonlLedger(ledger).read()
            self.assertEqual(rows[0]["kind"], "terminal.send.ambiguous")
            self.assertEqual(adapter.dispatch.call_count, 1)

    def test_ledger_verify_reports_chain_head_and_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            JsonlLedger(path).append(
                EvidenceEvent(
                    event_id="evt-1",
                    command_id="cmd-1",
                    leg=Leg.CAPTURE,
                    state=LegState.SUCCEEDED,
                    kind="user.intent_reported",
                    provenance=Provenance.USER_REPORT,
                )
            )
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["ledger", "verify", "--ledger", str(path)]), 0)
            self.assertIn('"valid": true', output.getvalue())
            self.assertIn('"event_count": 1', output.getvalue())

            rows = JsonlLedger(path).read()
            rows[0]["reason"] = "tampered"
            path.write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")
            path.chmod(0o600)
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["ledger", "verify", "--ledger", str(path)]), 2)
            self.assertIn("event_hash_mismatch", output.getvalue())

    def test_legacy_ledger_blocks_send_before_adapter_activity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = root / "legacy.jsonl"
            legacy = EvidenceEvent(
                event_id="legacy-1",
                command_id="cmd-old",
                leg=Leg.CAPTURE,
                state=LegState.SUCCEEDED,
                kind="user.intent_reported",
                provenance=Provenance.USER_REPORT,
            )
            ledger.write_text(json.dumps(legacy.as_dict()) + "\n", encoding="utf-8")
            ledger.chmod(0o600)
            adapter = MagicMock()
            output = StringIO()
            with patch("relayproof.cli.SupersetConfig.from_manifest"), patch(
                "relayproof.cli.SupersetAdapter", return_value=adapter
            ), redirect_stdout(output):
                code = main(
                    [
                        "superset", "send", "--manifest", str(root / "manifest.json"),
                        "--text", "private command",
                        "--canary", "RELAYPROOF_ACK_0123456789ABCDEF0123456789ABCDEF",
                        "--expect-revision", "7",
                        "--ledger", str(ledger), "--claim-dir", str(root / "claims"),
                    ]
                )
            self.assertEqual(code, 2)
            self.assertIn("ledger_migration_required", output.getvalue())
            adapter.snapshot.assert_not_called()
            adapter.dispatch.assert_not_called()

    def test_legacy_ledger_migration_is_non_destructive_and_verified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "legacy.jsonl"
            output_path = root / "v1.jsonl"
            legacy = EvidenceEvent(
                event_id="legacy-1",
                command_id="cmd-old",
                leg=Leg.CAPTURE,
                state=LegState.SUCCEEDED,
                kind="user.intent_reported",
                provenance=Provenance.USER_REPORT,
            )
            original = json.dumps(legacy.as_dict(), sort_keys=True) + "\n"
            source.write_text(original, encoding="utf-8")
            source.chmod(0o600)
            output = StringIO()
            with redirect_stdout(output):
                code = main(
                    ["ledger", "migrate", "--source", str(source), "--output", str(output_path)]
                )
            self.assertEqual(code, 0)
            self.assertEqual(source.read_text(encoding="utf-8"), original)
            self.assertTrue(JsonlLedger(output_path).verify().valid)
            self.assertIn('"migrated_events": 1', output.getvalue())

    def test_superset_confirmed_send_snapshots_and_rejects_revision_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest.json"
            ledger = root / "events.jsonl"
            claims = root / "claims"
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
                        "--ledger",
                        str(ledger),
                        "--claim-dir",
                        str(claims),
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
