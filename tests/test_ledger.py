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


class LedgerSymlinkTests(unittest.TestCase):
    def test_append_refuses_a_symlinked_ledger(self) -> None:
        """The ledger is the evidence of record; it must not be redirected.

        Without O_NOFOLLOW an append writes into the link target and the mode
        fix lands on that target instead, corrupting an unrelated file while
        leaving no ledger at the intended path.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            victim = root / "victim.txt"
            victim.write_text("do not touch\n", encoding="utf-8")
            path = root / "events.jsonl"
            path.symlink_to(victim)

            event = EvidenceEvent(
                event_id="evt-1",
                command_id="cmd-1",
                leg=Leg.DISPATCH,
                state=LegState.SUCCEEDED,
                kind="terminal.send",
                provenance=Provenance.API,
            )
            with self.assertRaises(OSError):
                JsonlLedger(path).append(event)
            self.assertEqual(victim.read_text(encoding="utf-8"), "do not touch\n")
