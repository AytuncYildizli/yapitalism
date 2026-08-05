from __future__ import annotations

import unittest

from yapitalism.mcp.backends.base import (
    GUARANTEES,
    HOST,
    NONE,
    BackendCapabilities,
    BackendError,
    BackendPane,
    parse_target_id,
)
from yapitalism.mcp.backends.tmux_backend import TmuxBackend
from yapitalism.mcp.registry import BackendRegistry


class FakeBackend:
    def __init__(self, namespace: str, panes: list[BackendPane], *, strong: bool = True):
        self._namespace = namespace
        self._panes = panes
        self._strong = strong

    @property
    def namespace(self) -> str:
        return self._namespace

    def capabilities(self) -> BackendCapabilities:
        level = HOST if self._strong else NONE
        return BackendCapabilities(
            idempotent_dispatch=level,
            optimistic_revision=level,
            empty_prompt_check=level,
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
        self.assertEqual(capabilities.idempotent_dispatch, NONE)
        self.assertEqual(capabilities.optimistic_revision, NONE)
        self.assertEqual(capabilities.empty_prompt_check, NONE)
        # Nothing is client-enforced either. `send` does decline a RECOGNISED
        # blocking prompt, and calling that CLIENT would be the overclaim the
        # enforcement levels exist to stop: it matches seven known dialogs and
        # passes on everything unfamiliar.
        self.assertEqual(capabilities.client_enforced, ())
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

    def test_an_unreachable_host_is_granted_no_guarantees(self) -> None:
        """Capabilities come from the host, so no host means no claims.

        This test used to assert HOST for all three against a nonexistent
        manifest, because capabilities were a module constant describing the
        machine they were written on. That is exactly how a stock Superset user
        would have been promised three guards their host had never heard of.
        """
        from yapitalism.mcp.backends.superset_backend import SupersetBackend

        backend = SupersetBackend(manifest_path="/nonexistent/manifest.json")
        self.assertEqual(backend.namespace, "superset")
        capabilities = backend.capabilities()
        self.assertEqual(capabilities.degraded, GUARANTEES)
        self.assertEqual(capabilities.client_enforced, ())
        # Not "registry" either: listSessions reports the runtime, and a host
        # that cannot be reached reported nothing.
        self.assertEqual(capabilities.runtime_detection, "unknown")

    def test_tmux_runtime_detection_names_its_weaker_method(self) -> None:
        # tmux can only infer the runtime from a process tree that may go stale
        # between the check and the send. Superset's registry-backed answer is
        # asserted in test_capability_honesty, against a host that answers.
        self.assertEqual(TmuxBackend().capabilities().runtime_detection, "process_tree")

    def test_missing_manifest_is_a_backend_error_not_a_crash(self) -> None:
        from yapitalism.mcp.backends.superset_backend import SupersetBackend

        backend = SupersetBackend(manifest_path="/nonexistent/manifest.json")
        with self.assertRaisesRegex(BackendError, "manifest unusable"):
            backend.list_panes()

    def test_a_broken_superset_backend_does_not_hide_tmux_panes(self) -> None:
        from yapitalism.mcp.backends.superset_backend import SupersetBackend

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


class FolderIdentityTests(unittest.TestCase):
    """A pane has to be findable by whichever word the person said.

    Workspace names in real use are things like "dasendeha", "elo" and "b".
    Project and folder are what someone actually says out loud, and they
    disagree often enough to matter: project "yapitalism" lives in a folder
    still called "relayproof".
    """

    def _payload(self, **kw):
        from yapitalism.mcp.registry import _pane_payload

        pane = BackendPane(
            target_id="superset:x", label="yapitalism/main", runtime="claude",
            width=0, height=0, dead=False, **kw
        )
        return _pane_payload(pane, "superset", ())

    def test_folder_and_project_are_both_reported_when_they_differ(self) -> None:
        payload = self._payload(
            project="yapitalism",
            folder="relayproof",
            path="/Users/x/.superset/projects/relayproof",
        )
        self.assertEqual(payload["project"], "yapitalism")
        self.assertEqual(payload["folder"], "relayproof")
        self.assertEqual(payload["path"], "/Users/x/.superset/projects/relayproof")

    def test_absent_fields_are_omitted_rather_than_sent_empty(self) -> None:
        payload = self._payload(project="solo")
        for key in ("folder", "path", "branch"):
            self.assertNotIn(key, payload)

    def test_the_full_path_survives_for_disambiguation(self) -> None:
        """Two projects can end in the same folder name."""
        a = self._payload(project="opty", folder="opty", path="/Users/x/opty/opty")
        b = self._payload(project="other", folder="opty", path="/Users/x/other/opty")
        self.assertEqual(a["folder"], b["folder"])
        self.assertNotEqual(a["path"], b["path"])
