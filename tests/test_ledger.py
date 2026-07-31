from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from relayproof.ledger import JsonlLedger
from relayproof.model import EvidenceEvent, Leg, LegState, Provenance


class LedgerTests(unittest.TestCase):
    def test_append_is_jsonl_and_mode_0600(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger" / "events.jsonl"
            ledger = JsonlLedger(path)
            ledger.append(
                EvidenceEvent(
                    event_id="evt-1",
                    command_id="cmd-1",
                    leg=Leg.CAPTURE,
                    state=LegState.SUCCEEDED,
                    kind="user.intent_reported",
                    provenance=Provenance.USER_REPORT,
                    occurred_at="fixture",
                )
            )
            rows = ledger.read()
            self.assertEqual(rows[0]["event_id"], "evt-1")
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(path.parent).st_mode & 0o777, 0o700)
            self.assertNotIn("raw_transcript", rows[0])


if __name__ == "__main__":
    unittest.main()
