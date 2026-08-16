"""Provisioning a manifest from the Superset install already on the machine.

This is the step that decides whether a stranger can use the Superset path at all.
Every test here is about refusing rather than guessing: a bearer token is being
copied from one file to another, and the failure modes are a stale endpoint, a
credential readable by other accounts, and a token in an error message.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from yapitalism.adapters.superset import SupersetConfig
from yapitalism.adapters.superset.provision import (
    HostRecord,
    ProvisionError,
    discover_hosts,
    manifest_payload,
    probe_binding,
    select_host,
    superset_home,
    write_manifest,
)

from test_superset_adapter import FakeTrpcServer, result

TOKEN = "SUPERSET-TOKEN-DO-NOT-LEAK"
ORG = "993686ba-7089-4e54-85c7-69b4d29c7f53"


def host_manifest(
    home: Path,
    *,
    organization: str = ORG,
    endpoint: str = "http://127.0.0.1:48900",
    pid: int | None = None,
    mode: int = 0o600,
    token: str = TOKEN,
) -> Path:
    """Write a manifest shaped exactly like the desktop app's."""
    directory = home / "host" / organization
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "pid": os.getpid() if pid is None else pid,
                "endpoint": endpoint,
                "authToken": token,
                "startedAt": 1785416233839,
                "organizationId": organization,
            }
        )
    )
    os.chmod(path, mode)
    return path


class DiscoveryTests(unittest.TestCase):
    def test_reads_the_app_manifest_and_normalises_the_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            host_manifest(home)
            (record,) = discover_hosts(home)
        self.assertEqual(record.organization_id, ORG)
        # The app stores a bare origin; this tool addresses the tRPC surface.
        self.assertEqual(record.endpoint, "http://127.0.0.1:48900/trpc")
        self.assertEqual(record.auth_token, TOKEN)
        self.assertTrue(record.alive)

    def test_a_world_readable_credential_is_skipped_not_copied(self) -> None:
        """Superset writes 0600. Looser means something changed it.

        Copying a token that other accounts can already read into a second file
        would spread the exposure instead of stopping the install.
        """
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            host_manifest(home, mode=0o644)
            self.assertEqual(discover_hosts(home), [])

    def test_a_non_loopback_endpoint_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            host_manifest(home, endpoint="http://169.254.169.254:48900")
            self.assertEqual(discover_hosts(home), [])

    def test_one_broken_organization_does_not_hide_a_working_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            host_manifest(home, organization="broken", mode=0o644)
            host_manifest(home, organization=ORG)
            records = discover_hosts(home)
        self.assertEqual([r.organization_id for r in records], [ORG])

    def test_superset_home_env_is_honoured(self) -> None:
        # The app sets and reads SUPERSET_HOME_DIR, so a non-default install has
        # to be discoverable rather than invisible.
        with patch.dict(os.environ, {"SUPERSET_HOME_DIR": "/somewhere/else"}):
            self.assertEqual(superset_home(), Path("/somewhere/else"))


class SelectionTests(unittest.TestCase):
    def record(self, *, pid: int, organization: str = ORG) -> HostRecord:
        return HostRecord(
            organization_id=organization,
            endpoint="http://127.0.0.1:48900/trpc",
            pid=pid,
            auth_token=TOKEN,
            source=Path("/tmp/manifest.json"),
        )

    def test_a_manifest_naming_a_dead_process_is_refused(self) -> None:
        """Superset leaves the file behind when it exits.

        A manifest on disk is not a running host, and provisioning from a stale
        one produces a file that looks right and fails on first use.
        """
        # PID 2**31-1 is not assignable on any platform this runs on.
        with self.assertRaises(ProvisionError) as caught:
            select_host([self.record(pid=2**31 - 1)])
        self.assertIn("process that is gone", str(caught.exception))

    def test_two_live_hosts_refuse_to_pick_for_you(self) -> None:
        records = [self.record(pid=os.getpid()), self.record(pid=os.getpid(), organization="other")]
        with self.assertRaises(ProvisionError) as caught:
            select_host(records)
        self.assertIn("--organization", str(caught.exception))

    def test_an_explicit_organization_selects_it(self) -> None:
        records = [self.record(pid=os.getpid()), self.record(pid=os.getpid(), organization="other")]
        self.assertEqual(select_host(records, "other").organization_id, "other")

    def test_no_manifest_at_all_says_what_to_do(self) -> None:
        with self.assertRaises(ProvisionError) as caught:
            select_host([])
        self.assertIn("Start the Superset desktop app", str(caught.exception))


class TokenSecrecyTests(unittest.TestCase):
    """A token must not reach a log, a traceback or a screenshot."""

    def test_repr_redacts_the_token(self) -> None:
        record = HostRecord(
            organization_id=ORG,
            endpoint="http://127.0.0.1:48900/trpc",
            pid=os.getpid(),
            auth_token=TOKEN,
            source=Path("/tmp/manifest.json"),
        )
        self.assertNotIn(TOKEN, repr(record))
        self.assertIn("<redacted>", repr(record))

    def test_refusal_messages_never_carry_the_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            path = host_manifest(home, mode=0o644)
            from yapitalism.adapters.superset.provision import _read_record

            with self.assertRaises(ProvisionError) as caught:
                _read_record(path)
        self.assertNotIn(TOKEN, str(caught.exception))


class ProbeTests(unittest.TestCase):
    def responder(self, *, real_agent_id: bool = True) -> Any:
        def respond(method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
            if path.endswith("workspace.list"):
                return result([{"id": "ws-1", "name": "mahobrain"}])
            if path.endswith("terminal.list"):
                return result(
                    {"sessions": [{"terminalId": "term-1", "workspaceId": "ws-1"}]}
                )
            if path.endswith("terminalAgents.listByWorkspace"):
                binding: dict[str, Any] = {"terminalId": "term-1"}
                # `agentId` is the host's word. The alternative here is the name
                # this code guessed twice, kept so the shape is pinned rather than
                # tolerated: accepting both would hide the next rename.
                binding["agentId" if real_agent_id else "runtime"] = "codex"
                return result([binding])
            raise AssertionError(f"unexpected procedure: {path}")

        return respond

    def record_for(self, endpoint: str) -> HostRecord:
        return HostRecord(
            organization_id=ORG,
            endpoint=endpoint,
            pid=os.getpid(),
            auth_token=TOKEN,
            source=Path("/tmp/manifest.json"),
        )

    def test_the_runtime_is_read_from_the_hosts_agent_id(self) -> None:
        """Third name tried for one value, and the first one that was read.

        It was `session["runtime"]`, then `agent["runtime"]`; both were reasoned
        about rather than looked up, and each reported "no terminal is running
        codex, claude or kimi" against a host running plenty. The host calls it
        `agentId`, on `terminalAgents.listByWorkspace`.
        """
        with FakeTrpcServer(self.responder()) as server:
            binding = probe_binding(self.record_for(f"{server.endpoint}"))
        self.assertEqual(binding.terminal_id, "term-1")
        self.assertEqual(binding.runtime, "codex")
        self.assertEqual(binding.workspace_name, "mahobrain")

    def test_a_runtime_key_is_not_accepted_in_place_of_agent_id(self) -> None:
        # Pins the shape rather than tolerating both: accepting a key the host
        # never sends would hide the next rename instead of failing on it.
        with FakeTrpcServer(self.responder(real_agent_id=False)) as server:
            with self.assertRaises(ProvisionError) as caught:
                probe_binding(self.record_for(f"{server.endpoint}"))
        self.assertIn("no terminal is running", str(caught.exception))

    def test_a_rejected_token_names_the_source_file(self) -> None:
        def respond(method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
            return {"__status__": 401}

        with FakeTrpcServer(respond) as server:
            with self.assertRaises(ProvisionError) as caught:
                probe_binding(self.record_for(f"{server.endpoint}"))
        message = str(caught.exception)
        self.assertIn("did not accept the token", message)
        self.assertIn("manifest.json", message)
        self.assertNotIn(TOKEN, message)


class WriteTests(unittest.TestCase):
    def payload(self) -> dict[str, object]:
        from yapitalism.adapters.superset.provision import Binding

        record = HostRecord(
            organization_id=ORG,
            endpoint="http://127.0.0.1:48900/trpc",
            pid=os.getpid(),
            auth_token=TOKEN,
            source=Path("/tmp/manifest.json"),
        )
        binding = Binding(
            workspace_id="ws-1",
            workspace_name="mahobrain",
            terminal_id="term-1",
            runtime="codex",
            terminal_count=1,
        )
        return manifest_payload(record, binding)

    def test_written_manifest_is_0600_and_loads_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "manifest.json"
            write_manifest(self.payload(), path)
            self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")
            # The real test of provisioning: the loader accepts what setup wrote.
            config = SupersetConfig.from_manifest(path)
        self.assertEqual(config.terminal_id, "term-1")
        self.assertEqual(config.workspace_id, "ws-1")
        self.assertNotIn(TOKEN, repr(config))

    def test_an_existing_manifest_is_not_clobbered_silently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            write_manifest(self.payload(), path)
            with self.assertRaises(ProvisionError) as caught:
                write_manifest(self.payload(), path)
            self.assertIn("--force", str(caught.exception))
            # Force replaces it rather than failing.
            write_manifest(self.payload(), path, force=True)
            self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")


class WritePathTests(unittest.TestCase):
    """A fresh install must not be handed a file named after the old project."""

    def test_a_new_install_gets_the_current_filename(self) -> None:
        from yapitalism.mcp.backends import superset_backend as backend

        with tempfile.TemporaryDirectory() as tmp:
            default = Path(tmp) / "yapitalism-manifest.json"
            legacy = Path(tmp) / "relayproof-manifest.json"
            with (
                patch.object(backend, "_MANIFEST_DEFAULT", default),
                patch.object(backend, "_MANIFEST_LEGACY", legacy),
                patch.dict(os.environ, {}, clear=False),
            ):
                os.environ.pop("YAPITALISM_SUPERSET_MANIFEST", None)
                # Neither exists — every new install. resolve_manifest_path falls
                # back to the legacy name, which is right for reading and wrong
                # for writing.
                self.assertEqual(backend.manifest_write_path(), default)
                self.assertEqual(backend.resolve_manifest_path(), legacy)
                # An existing legacy file is refreshed in place rather than left
                # stale beside a new one.
                legacy.write_text("{}")
                self.assertEqual(backend.manifest_write_path(), legacy)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
