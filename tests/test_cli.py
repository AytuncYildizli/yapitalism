from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from yapitalism.cli import main


class CliTests(unittest.TestCase):
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
                patch("yapitalism.cli.SupersetAdapter") as adapter_type,
                redirect_stdout(output),
            ):
                adapter_type.return_value.snapshot.return_value = snapshot
                self.assertEqual(main(["superset", "status", "--manifest", str(manifest)]), 0)
        rendered = output.getvalue()
        self.assertIn('"revision": 4', rendered)
        self.assertNotIn("private text", rendered)
        self.assertNotIn("top-secret", rendered)
        adapter_type.return_value.dispatch.assert_not_called()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
