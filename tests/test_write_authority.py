"""Authentication says who is calling; authority says what they may do.

The token gate (0.3.0) answered the first question and silently answered the
second with "everything". These tests pin the split: stdio keeps its write
authority (the OS made that trust decision at spawn), HTTP writes are refused
until the machine's operator says yes once, and read-only refuses writes on
every transport while reading stays whole. A corrupt authority file fails
CLOSED — a truncated write must never become a privilege escalation.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from yapitalism.authority import (
    authority_path,
    http_writes_allowed,
    set_http_writes,
)
from yapitalism.mcp import server


class _AuthorityFile(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        os.environ["YAPITALISM_AUTHORITY"] = str(
            Path(self._dir.name) / "authority.json"
        )

    def tearDown(self) -> None:
        os.environ.pop("YAPITALISM_AUTHORITY", None)
        self._dir.cleanup()


class AuthorityFileTests(_AuthorityFile):
    def test_absent_file_denies_http_writes(self) -> None:
        self.assertFalse(http_writes_allowed())

    def test_allow_is_recorded_and_owner_only(self) -> None:
        path = set_http_writes(True)
        self.assertTrue(http_writes_allowed())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_deny_wins_after_allow(self) -> None:
        set_http_writes(True)
        set_http_writes(False)
        self.assertFalse(http_writes_allowed())

    def test_a_corrupt_file_fails_closed(self) -> None:
        authority_path().write_text("{not json")
        self.assertFalse(http_writes_allowed())


class WriteGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = dict(server._authority_state)

    def tearDown(self) -> None:
        server._authority_state.clear()
        server._authority_state.update(self._saved)

    def test_stdio_writes_pass(self) -> None:
        server._authority_state.update(
            {"transport": "stdio", "read_only": False, "http_writes": False}
        )
        self.assertIsNone(server._refuse_write("sending"))

    def test_http_without_permission_is_refused_with_the_fix_named(self) -> None:
        server._authority_state.update(
            {"transport": "http", "read_only": False, "http_writes": False}
        )
        refused = server._refuse_write("sending")
        self.assertIsNotNone(refused)
        self.assertEqual(refused["status"], "RED")
        self.assertEqual(refused["reason"], "http_writes_not_allowed")
        self.assertTrue(refused["speak"].startswith("Not sent:"))
        self.assertIn("allow-http-writes", refused["speak"])
        self.assertEqual(refused["origin"], "http")

    def test_http_with_permission_passes(self) -> None:
        server._authority_state.update(
            {"transport": "http", "read_only": False, "http_writes": True}
        )
        self.assertIsNone(server._refuse_write("sending"))

    def test_read_only_refuses_everywhere_even_stdio(self) -> None:
        server._authority_state.update(
            {"transport": "stdio", "read_only": True, "http_writes": True}
        )
        refused = server._refuse_write("sending")
        self.assertEqual(refused["reason"], "read_only_server")
        self.assertIn("Reading and watching still work", refused["speak"])


if __name__ == "__main__":
    unittest.main()
