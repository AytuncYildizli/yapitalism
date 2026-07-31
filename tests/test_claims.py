from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from relayproof.claims import ConfirmationClaimStore


class ConfirmationClaimStoreTests(unittest.TestCase):
    def test_claim_survives_process_boundary_and_is_single_use(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ConfirmationClaimStore(Path(directory))
            store.issue(
                client_token="stable-token",
                command_id="cmd-1",
                text="private command",
                expected_revision=7,
                terminal_id="terminal-1",
            )

            reloaded = ConfirmationClaimStore(Path(directory))
            command_id = reloaded.consume(
                client_token="stable-token",
                text="private command",
                expected_revision=7,
                terminal_id="terminal-1",
            )

            self.assertEqual(command_id, "cmd-1")
            with self.assertRaisesRegex(ValueError, "not available"):
                reloaded.consume(
                    client_token="stable-token",
                    text="private command",
                    expected_revision=7,
                    terminal_id="terminal-1",
                )

    def test_claim_rejects_mutated_payload_without_exposing_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ConfirmationClaimStore(Path(directory))
            store.issue(
                client_token="stable-token",
                command_id="cmd-1",
                text="original private command",
                expected_revision=7,
                terminal_id="terminal-1",
            )

            with self.assertRaisesRegex(ValueError, "does not match") as raised:
                store.consume(
                    client_token="stable-token",
                    text="mutated private command",
                    expected_revision=7,
                    terminal_id="terminal-1",
                )
            self.assertNotIn("original private command", str(raised.exception))
            self.assertNotIn("mutated private command", str(raised.exception))

    def test_claim_file_is_owner_only_and_contains_no_raw_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ConfirmationClaimStore(root)
            store.issue(
                client_token="stable-token",
                command_id="cmd-1",
                text="private command",
                expected_revision=7,
                terminal_id="terminal-1",
            )

            files = list(root.glob("*.json"))
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
            self.assertNotIn("private command", files[0].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
