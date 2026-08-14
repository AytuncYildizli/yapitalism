"""The install report: what the machine has, what that buys, what is missing.

Constructed environments rather than probes, which is the point of separating
detection from presentation — the report for a machine with no tmux and a stock
Superset is testable without owning one.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from yapitalism.cli import render_setup
from yapitalism.setup import (
    ClientFacts,
    Environment,
    SupersetFacts,
    TmuxFacts,
    capabilities_for,
    detect_clients,
    next_steps,
)

NO_TMUX = TmuxFacts(installed=False, server_running=False, pane_count=0, detail="not on PATH")
TMUX_UP = TmuxFacts(installed=True, server_running=True, pane_count=24, detail="server running, 24 pane(s)")
NO_SUPERSET = SupersetFacts(
    app_installed=False, host_live=False, build="unknown", organization_id="", detail="not installed"
)


def superset(build: str, *, credentials_ok: bool = True) -> SupersetFacts:
    return SupersetFacts(
        app_installed=True,
        host_live=True,
        build=build,
        organization_id="org-1",
        detail=f"host live at http://127.0.0.1:48900/trpc ({build} build)",
        credentials_ok=credentials_ok,
    )


def environment(
    *,
    tmux: TmuxFacts = NO_TMUX,
    agents: dict[str, bool] | None = None,
    sup: SupersetFacts = NO_SUPERSET,
    manifest_written: bool = False,
    clients: tuple[ClientFacts, ...] = (),
) -> Environment:
    return Environment(
        tmux=tmux,
        agents=agents if agents is not None else {"codex": False, "claude": False, "kimi": False},
        superset=sup,
        clients=clients,
        manifest_written=manifest_written,
        manifest_path=Path("/tmp/yapitalism-manifest.json"),
    )


class UsableBackendTests(unittest.TestCase):
    def test_a_bare_machine_has_no_usable_backend(self) -> None:
        self.assertEqual(environment().usable_backends, ())

    def test_installed_tmux_without_a_server_is_not_usable(self) -> None:
        """An installed binary is not a running server.

        Listing it would send someone to a backend whose first call fails.
        """
        idle = TmuxFacts(installed=True, server_running=False, pane_count=0, detail="no server running yet")
        self.assertEqual(environment(tmux=idle).usable_backends, ())

    def test_a_live_superset_without_a_manifest_is_not_usable(self) -> None:
        # The host being up says nothing about this tool being able to reach it.
        self.assertEqual(environment(sup=superset("guarded")).usable_backends, ())
        self.assertEqual(
            environment(sup=superset("guarded"), manifest_written=True).usable_backends,
            ("superset",),
        )


class StaleCredentialTests(unittest.TestCase):
    """A manifest on disk is not a working credential.

    Superset rotates its own auth token; the manifest is a copy taken at
    provisioning time. A report built from the host's FRESH token said "healthy"
    while every send returned 401 — a health check that does not use the credential
    the product uses is not checking the product.
    """

    def test_a_stale_credential_makes_the_backend_unusable(self) -> None:
        env = environment(sup=superset("guarded", credentials_ok=False), manifest_written=True)
        self.assertEqual(env.usable_backends, ())

    def test_no_guarantees_are_shown_for_a_host_that_refuses_us(self) -> None:
        """Listing host/host/host while every call 401s is the "installed
        successfully" report this command exists to replace."""
        env = environment(sup=superset("guarded", credentials_ok=False), manifest_written=True)
        self.assertEqual(capabilities_for(env), {})
        self.assertNotIn("registry", "\n".join(render_setup(env)))

    def test_the_advice_names_the_command_and_the_reason(self) -> None:
        steps = next_steps(
            environment(
                agents={"codex": True, "claude": False, "kimi": False},
                sup=superset("guarded", credentials_ok=False),
                manifest_written=True,
                clients=(ClientFacts("Codex", Path("/tmp/c.toml"), True, "http"),),
            )
        )
        self.assertEqual(len(steps), 1)
        self.assertIn("--force", steps[0])
        # "Re-run setup" without the reason reads like superstition the second time.
        self.assertIn("rotated its token", steps[0])

    def test_a_working_credential_restores_the_row(self) -> None:
        env = environment(sup=superset("guarded"), manifest_written=True)
        self.assertEqual(env.usable_backends, ("superset",))


class CapabilityTableTests(unittest.TestCase):
    def test_a_stock_host_shows_client_enforcement(self) -> None:
        rows = capabilities_for(environment(sup=superset("stock"), manifest_written=True))
        caps = rows["superset"]
        self.assertEqual(caps.idempotent_dispatch, "client")
        self.assertEqual(caps.optimistic_revision, "client")
        self.assertEqual(caps.empty_prompt_check, "client")

    def test_a_guarded_host_shows_host_enforcement(self) -> None:
        rows = capabilities_for(environment(sup=superset("guarded"), manifest_written=True))
        caps = rows["superset"]
        self.assertEqual(caps.idempotent_dispatch, "host")
        self.assertEqual(caps.empty_prompt_check, "host")

    def test_the_table_reads_the_real_backend_objects(self) -> None:
        """No second source of truth about guarantees.

        If this table restated the levels in its own words, it could drift from
        what a receipt says — the same mistake as hard-coding them per backend,
        one layer up.
        """
        from yapitalism.mcp.backends.superset_backend import CLIENT_GUARDED, HOST_GUARDED
        from yapitalism.mcp.backends.tmux_backend import TMUX_CAPABILITIES

        rows = capabilities_for(
            environment(tmux=TMUX_UP, sup=superset("stock"), manifest_written=True)
        )
        self.assertIs(rows["tmux"], TMUX_CAPABILITIES)
        self.assertIs(rows["superset"], CLIENT_GUARDED)
        guarded = capabilities_for(environment(sup=superset("guarded"), manifest_written=True))
        self.assertIs(guarded["superset"], HOST_GUARDED)


class NextStepTests(unittest.TestCase):
    def test_no_agent_installed_is_the_first_thing_said(self) -> None:
        steps = next_steps(environment(tmux=TMUX_UP))
        self.assertIn("install at least one agent CLI", steps[0])

    def test_a_live_host_without_a_manifest_gets_the_exact_command(self) -> None:
        steps = next_steps(
            environment(agents={"codex": True, "claude": False, "kimi": False}, sup=superset("guarded"))
        )
        self.assertTrue(any("yapitalism superset setup --confirm" in step for step in steps))

    def test_finished_work_is_not_relisted(self) -> None:
        """A checklist that repeats completed items trains people to skim it."""
        steps = next_steps(
            environment(
                tmux=TMUX_UP,
                agents={"codex": True, "claude": True, "kimi": True},
                sup=superset("guarded"),
                manifest_written=True,
                clients=(ClientFacts("Codex", Path("/tmp/config.toml"), True, "http"),),
            )
        )
        self.assertEqual(steps, [])

    def test_both_backends_absent_says_so_once(self) -> None:
        steps = next_steps(environment(agents={"codex": True, "claude": False, "kimi": False}))
        self.assertTrue(any("install tmux, or start Superset" in step for step in steps))


class RenderTests(unittest.TestCase):
    def render(self, env: Environment) -> str:
        return "\n".join(render_setup(env))

    def test_a_bare_machine_is_told_plainly_that_nothing_works_yet(self) -> None:
        text = self.render(environment())
        self.assertIn("no backend can carry a send on this machine", text)
        # And it must not print a capability table implying otherwise.
        self.assertNotIn("process_tree", text)

    def test_the_report_ends_in_both_registration_forms(self) -> None:
        text = self.render(environment(tmux=TMUX_UP))
        self.assertIn("codex mcp add yapitalism --url http://127.0.0.1:8792/mcp", text)
        self.assertIn('"args": ["--stdio"]', text)

    def test_the_enforcement_legend_appears_with_the_table(self) -> None:
        """The words host/client/none are meaningless without it."""
        text = self.render(environment(tmux=TMUX_UP))
        self.assertIn("the host refuses the write itself", text)
        self.assertIn("this process checks, then writes", text)

    def test_absent_clients_are_not_listed_as_present(self) -> None:
        clients = (
            ClientFacts("Codex", Path("/tmp/codex.toml"), True, "http"),
            ClientFacts("Cursor", Path("/tmp/cursor.json"), False, "stdio"),
        )
        text = self.render(environment(tmux=TMUX_UP, clients=clients))
        self.assertIn("Codex", text)
        self.assertNotIn("cursor.json", text)

    def test_a_stock_superset_row_differs_visibly_from_a_guarded_one(self) -> None:
        stock = self.render(environment(sup=superset("stock"), manifest_written=True))
        guarded = self.render(environment(sup=superset("guarded"), manifest_written=True))
        self.assertIn("client", stock)
        self.assertNotEqual(stock, guarded)


class ClientDetectionTests(unittest.TestCase):
    def test_only_files_that_exist_are_reported_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".codex").mkdir()
            (home / ".codex" / "config.toml").write_text("")
            found = {client.name: client.present for client in detect_clients(home)}
        self.assertTrue(found["Codex"])
        self.assertFalse(found["Cursor"])
        self.assertFalse(found["Claude Desktop"])

    def test_codex_is_the_url_client_and_the_others_are_stdio(self) -> None:
        # Codex takes --url; the rest spawn the process themselves. Getting this
        # backwards produces a config that silently never connects.
        transports = {client.name: client.transport for client in detect_clients(Path("/nonexistent"))}
        self.assertEqual(transports["Codex"], "http")
        self.assertEqual(transports["Claude Desktop"], "stdio")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
