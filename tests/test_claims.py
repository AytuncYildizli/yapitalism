from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from relayproof.claims import ConfirmationClaimStore


class ConfirmationClaimStoreTests(unittest.TestCase):
    def issue(self, store: ConfirmationClaimStore) -> None:
        store.issue(
            client_token="stable-token",
            command_id="cmd-1",
            text="private command",
            expected_revision=7,
            terminal_id="terminal-1",
        )

    def test_claim_survives_process_boundary_and_is_single_use(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ConfirmationClaimStore(Path(directory))
            self.issue(store)

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

    def test_identical_claim_can_be_reissued_before_consumption(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ConfirmationClaimStore(Path(directory))
            self.issue(store)
            time.sleep(1.1)
            self.issue(store)

    def test_consumed_token_cannot_be_reissued(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ConfirmationClaimStore(Path(directory))
            self.issue(store)
            store.consume(
                client_token="stable-token",
                text="private command",
                expected_revision=7,
                terminal_id="terminal-1",
            )
            with self.assertRaisesRegex(ValueError, "already consumed"):
                self.issue(store)

    def test_expired_claim_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ConfirmationClaimStore(root)
            self.issue(store)
            claim_path = next(root.glob("*.json"))
            payload = json.loads(claim_path.read_text(encoding="utf-8"))
            payload["expires_at"] = "2000-01-01T00:00:00+00:00"
            claim_path.write_text(json.dumps(payload), encoding="utf-8")
            claim_path.chmod(0o600)
            with self.assertRaisesRegex(ValueError, "expired"):
                store.consume(
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
            self.issue(store)

            files = list(root.glob("*.json"))
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
            self.assertNotIn("private command", files[0].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()


class ConfirmationClaimRecoveryTests(unittest.TestCase):
    """A claim that cannot authorize a send must never wedge its token.

    Both cases below previously left the token permanently unusable: `issue`
    reported success while `consume` rejected the claim forever.
    """

    ARGS = {
        "client_token": "stable-token",
        "command_id": "cmd-1",
        "text": "private command",
        "expected_revision": 7,
        "terminal_id": "terminal-1",
    }

    def consume(self, store: ConfirmationClaimStore) -> str:
        return store.consume(
            client_token="stable-token",
            text="private command",
            expected_revision=7,
            terminal_id="terminal-1",
        )

    def test_expired_claim_is_replaced_by_reissue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ConfirmationClaimStore(Path(directory))
            store.issue(**self.ARGS)

            path = next(Path(directory).glob("*.json"))
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["expires_at"] = "2000-01-01T00:00:00+00:00"
            path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "expired"):
                self.consume(store)

            store.issue(**self.ARGS)
            self.assertEqual(self.consume(store), "cmd-1")

    def test_truncated_claim_is_replaced_by_reissue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ConfirmationClaimStore(Path(directory))
            store.issue(**self.ARGS)

            # A crash part-way through a previous issue.
            path = next(Path(directory).glob("*.json"))
            path.write_text("", encoding="utf-8")

            store.issue(**self.ARGS)
            self.assertEqual(self.consume(store), "cmd-1")

    def test_claim_consumed_during_issue_is_not_resurrected(self) -> None:
        """The consumed marker is re-checked after the exclusive create.

        A slow `issue` that passed the marker check before a concurrent
        `consume` renamed the claim away would otherwise create a fresh live
        claim for a token that was already spent.
        """
        with tempfile.TemporaryDirectory() as directory:
            store = ConfirmationClaimStore(Path(directory))
            store.issue(**self.ARGS)
            self.assertEqual(self.consume(store), "cmd-1")

            with self.assertRaisesRegex(ValueError, "already consumed"):
                store.issue(**self.ARGS)
            self.assertEqual(list(Path(directory).glob("*.json")), [])

    def test_naive_expiry_is_treated_as_expired_not_a_crash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ConfirmationClaimStore(Path(directory))
            store.issue(**self.ARGS)
            path = next(Path(directory).glob("*.json"))
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["expires_at"] = "2999-01-01T00:00:00"  # no timezone
            path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "expired"):
                self.consume(store)
