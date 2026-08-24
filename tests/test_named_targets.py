"""Names a voice can say, refused the moment they stop being true.

The risk in a name is silence: tmux reuses pane ids, so the `%2` that
"billing" was bound to last week can be a different agent — or a shell —
today. These tests pin the contract: exact unique match only, re-verified
against a live listing on every use, and a stale name is a REFUSAL that says
how to fix it, never a silent retarget.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yapitalism.mcp import server
from yapitalism.names import load_names, names_path, save_name, validate_name


class _NamesFile(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        os.environ["YAPITALISM_NAMES"] = str(Path(self._dir.name) / "names.json")

    def tearDown(self) -> None:
        os.environ.pop("YAPITALISM_NAMES", None)
        self._dir.cleanup()


class NamesFileTests(_NamesFile):
    def test_round_trip_and_owner_only_permissions(self) -> None:
        save_name("billing", {"target_id": "tmux:%4", "runtime": "codex", "folder": "api"})
        self.assertEqual(load_names()["billing"]["target_id"], "tmux:%4")
        self.assertEqual(names_path().stat().st_mode & 0o777, 0o600)

    def test_a_corrupt_file_raises_instead_of_vanishing_names(self) -> None:
        names_path().parent.mkdir(parents=True, exist_ok=True)
        names_path().write_text("[1,2]")
        with self.assertRaises(ValueError):
            load_names()

    def test_validation_rejects_shadows_and_noise(self) -> None:
        self.assertEqual(validate_name("billing-codex"), "")
        self.assertNotEqual(validate_name("tmux"), "")
        self.assertNotEqual(validate_name("mbp3", peer_names=("mbp3",)), "")
        self.assertNotEqual(validate_name("Has Spaces"), "")
        self.assertNotEqual(validate_name("a" * 40), "")


def _live_pane(target_id: str, runtime: str) -> dict[str, str]:
    return {"target_id": target_id, "runtime": runtime, "folder": "api"}


class ResolveTargetTests(_NamesFile):
    def test_a_real_id_passes_through_without_a_lookup(self) -> None:
        resolved, refusal = server._resolve_target("tmux:%0")
        self.assertEqual(resolved, "tmux:%0")
        self.assertIsNone(refusal)

    def test_an_unknown_name_lists_what_exists(self) -> None:
        save_name("billing", {"target_id": "tmux:%4", "runtime": "codex", "folder": ""})
        _, refusal = server._resolve_target("bling")
        self.assertIsNotNone(refusal)
        self.assertIn("billing", refusal["error"])
        self.assertIn("panes_name", refusal["error"])

    def test_a_live_matching_binding_resolves(self) -> None:
        save_name("billing", {"target_id": "tmux:%4", "runtime": "codex", "folder": ""})
        with patch.object(
            server, "_find_live_pane", return_value=_live_pane("tmux:%4", "codex")
        ):
            resolved, refusal = server._resolve_target("billing")
        self.assertEqual(resolved, "tmux:%4")
        self.assertIsNone(refusal)

    def test_a_vanished_pane_is_a_refusal_not_a_guess(self) -> None:
        save_name("billing", {"target_id": "tmux:%4", "runtime": "codex", "folder": ""})
        with patch.object(server, "_find_live_pane", return_value=None):
            _, refusal = server._resolve_target("billing")
        self.assertEqual(refusal["reason"], "stale_name")
        self.assertIn("no longer exists", refusal["error"])

    def test_a_changed_runtime_is_refused_never_retargeted(self) -> None:
        # The exact failure names exist for: %4 was codex, tmux reused it, and
        # now a shell (or a different agent) wears the id.
        save_name("billing", {"target_id": "tmux:%4", "runtime": "codex", "folder": ""})
        with patch.object(
            server, "_find_live_pane", return_value=_live_pane("tmux:%4", "shell")
        ):
            _, refusal = server._resolve_target("billing")
        self.assertEqual(refusal["reason"], "stale_name")
        self.assertIn("now runs shell", refusal["error"])


class PeerCreateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = dict(server._authority_state)

    def tearDown(self) -> None:
        server._authority_state.clear()
        server._authority_state.update(self._saved)

    def test_remote_create_gate_refuses_http_with_the_fix_named(self) -> None:
        server._authority_state.update(
            {"transport": "http", "read_only": False,
             "http_writes": True, "remote_create": False}
        )
        refused = server._refuse_create("starting an agent")
        self.assertEqual(refused["reason"], "remote_create_not_allowed")
        self.assertIn("allow-remote-create", refused["speak"])

    def test_remote_create_rides_on_nothing_it_needs_both_permissions(self) -> None:
        server._authority_state.update(
            {"transport": "http", "read_only": False,
             "http_writes": False, "remote_create": True}
        )
        refused = server._refuse_create("starting an agent")
        self.assertEqual(refused["reason"], "http_writes_not_allowed")

    def test_a_machine_argument_forwards_the_whole_tool(self) -> None:
        calls: list[tuple[str, dict]] = []

        class FakePeer:
            def call_tool(self, tool: str, arguments: dict) -> dict:
                calls.append((tool, arguments))
                return {"ok": True, "target_id": "tmux:%7", "runtime": "codex"}

            def brand(self, payload: dict) -> dict:
                branded = dict(payload)
                branded["target_id"] = "mbp3:" + payload["target_id"]
                return branded

        with patch.object(server.peers, "named", return_value=FakePeer()):
            result = server.panes_create("codex", "/Users/squanch/proj", machine="mbp3")
        self.assertEqual(calls[0][0], "panes_create")
        # The receipt is the peer's, ids re-namespaced and nothing else touched.
        self.assertEqual(result["target_id"], "mbp3:tmux:%7")

    def test_an_unknown_machine_lists_the_peers(self) -> None:
        with patch.object(server.peers, "named", return_value=None), patch.object(
            type(server.peers), "names", property(lambda self: ("mbp3",))
        ):
            result = server.panes_create("codex", "/tmp", machine="studio")
        self.assertFalse(result["ok"])
        self.assertIn("mbp3", result["error"])


if __name__ == "__main__":
    unittest.main()
