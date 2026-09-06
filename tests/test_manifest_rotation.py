"""A rotated manifest must reach a long-lived server without a restart.

Measured live: the Superset host rotated its token, `setup --force` rewrote
the manifest, and a fresh MCP session saw every pane while a long-lived stdio
client kept answering `Superset tRPC HTTP 401` — because `_connect` cached the
adapter, and every terminal-bound adapter derived from it, forever.

The fix these tests pin is deliberately not a retry: `_connect` stats the
manifest on every call and rebuilds the adapter when the file changed, BEFORE
any request is made. Nothing is replayed — an ambiguous write stays exactly as
ambiguous as it was — the next call simply carries the credential the file
holds now.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yapitalism.mcp.backends import superset_backend as sb


def _write_manifest(path: Path, token: str) -> None:
    path.write_text(
        json.dumps(
            {
                "endpoint": "http://127.0.0.1:9/trpc",
                "bearer_token": token,
                "workspace_id": "w-1",
                "terminal_id": "t-1",
                "timeout_seconds": 3.0,
            }
        )
    )
    os.chmod(path, 0o600)


class _FakeAdapter:
    """Records construction; refuses to be used as a network client."""

    built: list[str] = []

    def __init__(self, config) -> None:
        self.config = config
        type(self).built.append(config.bearer_token)

    def __getattr__(self, name: str):
        raise AssertionError(
            f"_connect must make no requests; something called .{name}()"
        )


class ManifestRotationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.manifest = Path(self._dir.name) / "manifest.json"
        _write_manifest(self.manifest, "token-old")
        _FakeAdapter.built = []
        patcher = patch.object(sb, "SupersetAdapter", _FakeAdapter)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._dir.cleanup)
        self.backend = sb.SupersetBackend(self.manifest)

    def test_an_unchanged_manifest_keeps_its_adapter(self) -> None:
        first = self.backend._connect()
        second = self.backend._connect()
        self.assertIs(first, second)
        self.assertEqual(_FakeAdapter.built, ["token-old"])

    def test_a_rotated_manifest_rebuilds_without_a_restart(self) -> None:
        """The regression: the 401-forever client."""
        self.backend._connect()
        # Rotate. A different token length changes the size; the explicit
        # utime moves mtime even on filesystems with coarse timestamps.
        _write_manifest(self.manifest, "token-new-longer")
        os.utime(self.manifest, ns=(1, 1))
        adapter = self.backend._connect()
        self.assertEqual(adapter.config.bearer_token, "token-new-longer")
        self.assertEqual(_FakeAdapter.built, ["token-old", "token-new-longer"])

    def test_rotation_drops_every_terminal_bound_adapter_too(self) -> None:
        # The derived adapters embed the same dead credential; keeping them
        # would fix panes_list while every send to a known terminal still 401s.
        self.backend._connect()
        self.backend._adapters["t-2"] = object()
        self.backend._workspace_of["t-2"] = "w-1"
        _write_manifest(self.manifest, "token-new-longer")
        os.utime(self.manifest, ns=(1, 1))
        self.backend._connect()
        self.assertEqual(self.backend._adapters, {})
        self.assertEqual(self.backend._workspace_of, {})

    def test_the_refresh_is_preflight_and_makes_no_requests(self) -> None:
        # No retry, no replay: _FakeAdapter raises on ANY method call, so this
        # test fails the moment someone teaches the refresh to probe the host —
        # the step a future "retry the write once" would need.
        self.backend._connect()
        _write_manifest(self.manifest, "token-new-longer")
        os.utime(self.manifest, ns=(1, 1))
        self.backend._connect()

    def test_a_manifest_deleted_after_connect_surfaces_as_absence(self) -> None:
        self.backend._connect()
        self.manifest.unlink()
        with self.assertRaises(sb.BackendUnavailable):
            self.backend._connect()


if __name__ == "__main__":
    unittest.main()
