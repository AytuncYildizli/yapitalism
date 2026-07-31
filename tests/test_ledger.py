from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from relayproof.ledger import JsonlLedger
from relayproof.model import EvidenceEvent, Leg, LegState, Provenance


def evidence(event_id: str = "evt-1", *, command_id: str = "cmd-1") -> EvidenceEvent:
    return EvidenceEvent(
        event_id=event_id,
        command_id=command_id,
        leg=Leg.CAPTURE,
        state=LegState.SUCCEEDED,
        kind="user.intent_reported",
        provenance=Provenance.USER_REPORT,
        occurred_at="fixture",
    )


class LedgerTests(unittest.TestCase):
    def test_append_is_jsonl_and_mode_0600(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger" / "events.jsonl"
            ledger = JsonlLedger(path)
            ledger.append(evidence())
            rows = ledger.read()
            self.assertEqual(rows[0]["event_id"], "evt-1")
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(path.parent).st_mode & 0o777, 0o700)
            self.assertNotIn("raw_transcript", rows[0])

    def test_append_assigns_monotonic_sequence_and_hash_chain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = JsonlLedger(Path(directory) / "events.jsonl")
            ledger.append(evidence("evt-1"))
            ledger.append(evidence("evt-2"))

            rows = ledger.read()
            self.assertEqual([row["sequence"] for row in rows], [1, 2])
            self.assertEqual(rows[0]["prev_hash"], "")
            self.assertEqual(rows[1]["prev_hash"], rows[0]["event_hash"])
            self.assertTrue(ledger.verify().valid)

    def test_duplicate_event_id_is_idempotent_but_mutation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = JsonlLedger(Path(directory) / "events.jsonl")
            ledger.append(evidence("evt-1"))
            ledger.append(evidence("evt-1"))
            self.assertEqual(len(ledger.read()), 1)

            with self.assertRaisesRegex(ValueError, "different payload"):
                ledger.append(evidence("evt-1", command_id="cmd-other"))

    def test_verify_names_schema_sequence_and_link_failures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            ledger = JsonlLedger(path)
            ledger.append(evidence("evt-1"))
            ledger.append(evidence("evt-2"))
            original = [dict(row) for row in ledger.read()]

            cases = (
                ("schema_version", 99, "schema_version_mismatch"),
                ("sequence", 3, "sequence_mismatch"),
                ("prev_hash", "wrong", "previous_hash_mismatch"),
            )
            for field, value, reason in cases:
                rows = [dict(row) for row in original]
                rows[1][field] = value
                path.write_text(
                    "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
                    encoding="utf-8",
                )
                path.chmod(0o600)
                self.assertEqual(ledger.verify().reason, reason)

    def test_verify_detects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            ledger = JsonlLedger(path)
            ledger.append(evidence("evt-1"))
            row = ledger.read()[0]
            row["reason"] = "rewritten"
            path.write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")
            path.chmod(0o600)

            result = ledger.verify()
            self.assertFalse(result.valid)
            self.assertEqual(result.reason, "event_hash_mismatch")


if __name__ == "__main__":
    unittest.main()
