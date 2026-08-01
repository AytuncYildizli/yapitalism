from __future__ import annotations

import unittest

from relayproof.mcp.backends.base import (
    BackendCapabilities,
    BackendError,
    BackendPane,
    parse_target_id,
)
from relayproof.mcp.backends.tmux_backend import TmuxBackend
from relayproof.mcp.registry import BackendRegistry


class FakeBackend:
    def __init__(self, namespace: str, panes: list[BackendPane], *, strong: bool = True):
        self._namespace = namespace
        self._panes = panes
        self._strong = strong

    @property
    def namespace(self) -> str:
        return self._namespace

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            idempotent_dispatch=self._strong,
            optimistic_revision=self._strong,
            empty_prompt_check=self._strong,
            runtime_detection="registry" if self._strong else "process_tree",
        )

    def list_panes(self) -> list[BackendPane]:
        return self._panes

    def read_pane(self, target_id: str, lines: int) -> str:
        return f"{target_id}:{lines}"


class BrokenBackend(FakeBackend):
    def list_panes(self) -> list[BackendPane]:
        raise BackendError("host unreachable")


def pane(target_id: str, runtime: str = "codex") -> BackendPane:
    return BackendPane(
        target_id=target_id, label="w:0.0", runtime=runtime, width=80, height=24, dead=False
    )


class TargetIdTests(unittest.TestCase):
    def test_namespaced_id_splits(self) -> None:
        self.assertEqual(parse_target_id("tmux:%0"), ("tmux", "%0"))

    def test_bare_id_is_rejected_rather_than_defaulted(self) -> None:
        # Guessing a backend is how a command reaches the wrong machine.
        for bad in ("%0", "", "tmux:", ":%0"):
            with self.assertRaises(BackendError):
                parse_target_id(bad)


class CapabilityTests(unittest.TestCase):
    def test_tmux_declares_the_guarantees_it_cannot_make(self) -> None:
        capabilities = TmuxBackend().capabilities()
        self.assertFalse(capabilities.idempotent_dispatch)
        self.assertFalse(capabilities.optimistic_revision)
        self.assertFalse(capabilities.empty_prompt_check)
        self.assertEqual(capabilities.runtime_detection, "process_tree")
        self.assertEqual(
            set(capabilities.degraded),
            {"idempotent_dispatch", "optimistic_revision", "empty_prompt_check"},
        )

    def test_a_fully_capable_backend_reports_nothing_degraded(self) -> None:
        self.assertEqual(FakeBackend("superset", []).capabilities().degraded, ())


class RegistryTests(unittest.TestCase):
    def test_resolves_by_namespace(self) -> None:
        tmux = FakeBackend("tmux", [])
        superset = FakeBackend("superset", [])
        registry = BackendRegistry([tmux, superset])
        self.assertIs(registry.resolve("tmux:%0"), tmux)
        self.assertIs(registry.resolve("superset:abc"), superset)

    def test_unknown_namespace_names_what_is_registered(self) -> None:
        registry = BackendRegistry([FakeBackend("tmux", [])])
        with self.assertRaisesRegex(BackendError, "registered: tmux"):
            registry.resolve("ssh:box1")

    def test_duplicate_namespace_is_refused(self) -> None:
        registry = BackendRegistry([FakeBackend("tmux", [])])
        with self.assertRaises(BackendError):
            registry.register(FakeBackend("tmux", []))

    def test_weak_backend_panes_carry_their_missing_guarantees(self) -> None:
        registry = BackendRegistry(
            [
                FakeBackend("superset", [pane("superset:a")], strong=True),
                FakeBackend("tmux", [pane("tmux:%0")], strong=False),
            ]
        )
        panes, errors = registry.list_all()
        self.assertEqual(errors, [])
        by_id = {p["target_id"]: p for p in panes}
        # The strong backend claims nothing extra...
        self.assertNotIn("missing_guarantees", by_id["superset:a"])
        # ...and the weak one cannot borrow its guarantees by staying silent.
        self.assertIn("missing_guarantees", by_id["tmux:%0"])

    def test_one_failing_backend_does_not_hide_or_silence_the_others(self) -> None:
        registry = BackendRegistry(
            [
                BrokenBackend("superset", []),
                FakeBackend("tmux", [pane("tmux:%0")], strong=False),
            ]
        )
        panes, errors = registry.list_all()
        self.assertEqual([p["target_id"] for p in panes], ["tmux:%0"])
        # An empty list from a broken backend would read as "no terminals
        # there", which is a different claim from "I could not look".
        self.assertEqual(errors, [{"backend": "superset", "error": "host unreachable"}])



class SupersetBackendTests(unittest.TestCase):
    """Contract checks that need no live host and no credentials."""

    def test_namespace_and_capabilities(self) -> None:
        from relayproof.mcp.backends.superset_backend import SupersetBackend

        backend = SupersetBackend(manifest_path="/nonexistent/manifest.json")
        self.assertEqual(backend.namespace, "superset")
        capabilities = backend.capabilities()
        self.assertTrue(capabilities.idempotent_dispatch)
        self.assertTrue(capabilities.optimistic_revision)
        self.assertTrue(capabilities.empty_prompt_check)
        # Superset enforces all three, so nothing is degraded — this is the
        # contrast that stops tmux borrowing its guarantees.
        self.assertEqual(capabilities.degraded, ())

    def test_runtime_detection_is_not_claimed_as_a_pre_write_check(self) -> None:
        from relayproof.mcp.backends.superset_backend import SupersetBackend

        # The host reports runtime only in a send response, so a read cannot
        # pre-filter a non-agent target the way the tmux process tree can.
        self.assertEqual(
            SupersetBackend(manifest_path="/nonexistent").capabilities().runtime_detection,
            "host_on_dispatch",
        )

    def test_missing_manifest_is_a_backend_error_not_a_crash(self) -> None:
        from relayproof.mcp.backends.superset_backend import SupersetBackend

        backend = SupersetBackend(manifest_path="/nonexistent/manifest.json")
        with self.assertRaisesRegex(BackendError, "manifest unusable"):
            backend.list_panes()

    def test_a_broken_superset_backend_does_not_hide_tmux_panes(self) -> None:
        from relayproof.mcp.backends.superset_backend import SupersetBackend

        registry = BackendRegistry(
            [
                SupersetBackend(manifest_path="/nonexistent/manifest.json"),
                FakeBackend("tmux", [pane("tmux:%0")], strong=False),
            ]
        )
        panes, errors = registry.list_all()
        self.assertEqual([p["target_id"] for p in panes], ["tmux:%0"])
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["backend"], "superset")

if __name__ == "__main__":
    unittest.main()
