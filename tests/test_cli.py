from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from voice_receipt.cli import doctor


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


if __name__ == "__main__":
    unittest.main()
