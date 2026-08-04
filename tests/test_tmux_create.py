"""Creating agent panes.

The argv builder is tested directly because it IS the security boundary: a
create tool reachable by voice must never be able to run anything but a
whitelisted agent. Asserting on the argv proves that without launching a real
agent, so these tests run anywhere.

The one test that really starts a session uses `tmux -L yapitalism-create-test`,
never the operator's server.
"""

from __future__ import annotations

import os
import subprocess
import unittest

SOCKET = "yapitalism-create-test"
# Deliberately NOT set at import time. Another tmux test module also sets this
# variable at import, so whichever imported last would silently own the socket
# for every module — these tests would then run against a server they did not
# create. It is bound per class below and restored afterwards instead.

from yapitalism.mcp.tmux import (  # noqa: E402
    AGENT_LAUNCHERS,
    TmuxError,
    build_new_session_args,
    validate_session_name,
)
from yapitalism.mcp.backends.base import CreateOutcome  # noqa: E402


def tmux(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["tmux", "-L", SOCKET, *args], capture_output=True, text=True, check=False
    )


class LauncherWhitelistTests(unittest.TestCase):
    def test_only_known_agent_runtimes_are_launchable(self) -> None:
        self.assertEqual(set(AGENT_LAUNCHERS), {"codex", "claude", "kimi"})

    def test_an_unknown_runtime_is_refused_and_names_what_is_allowed(self) -> None:
        with self.assertRaisesRegex(TmuxError, "known runtimes: claude, codex, kimi"):
            build_new_session_args("s1", "bash", "/tmp")

    def test_a_command_cannot_be_smuggled_through_the_runtime_name(self) -> None:
        # The runtime is an index into a fixed table, never argv. Each of these
        # would be arbitrary code execution reachable by voice if it resolved.
        for smuggled in (
            "sh",
            "codex; rm -rf /",
            "codex --dangerously-bypass-approvals-and-sandbox",
            "/bin/sh",
            "../../bin/sh",
            "",
        ):
            with self.subTest(runtime=smuggled):
                with self.assertRaises(TmuxError):
                    build_new_session_args("s1", smuggled, "/tmp")

    def test_the_launcher_is_terminated_so_it_cannot_be_read_as_flags(self) -> None:
        args = build_new_session_args("s1", "codex", "/tmp")
        self.assertEqual(args[-2:], ["--", "codex"])

    def test_argv_is_a_list_so_no_shell_is_ever_involved(self) -> None:
        args = build_new_session_args("s1", "claude", "/tmp")
        self.assertTrue(all(isinstance(a, str) for a in args))
        # Nothing that would need a shell to mean anything.
        self.assertNotIn(";", " ".join(args))
        self.assertNotIn("|", " ".join(args))

    def test_the_pane_id_is_requested_from_tmux_rather_than_scanned_for(self) -> None:
        # Racing list-panes could hand back a pane someone else just made.
        args = build_new_session_args("s1", "codex", "/tmp")
        self.assertIn("-P", args)
        self.assertIn("#{pane_id}", args)


class SessionNameTests(unittest.TestCase):
    def test_accepts_ordinary_names(self) -> None:
        for good in ("yap-codex-ab12ef", "s1", "A_b-9"):
            with self.subTest(name=good):
                self.assertEqual(validate_session_name(good), good)

    def test_rejects_tmux_target_separators(self) -> None:
        # ':' and '.' would address a different pane than the one reported back.
        for bad in ("a:b", "a.b", "sess:0.1"):
            with self.subTest(name=bad):
                with self.assertRaises(TmuxError):
                    validate_session_name(bad)

    def test_rejects_a_leading_dash_which_would_parse_as_a_flag(self) -> None:
        with self.assertRaises(TmuxError):
            validate_session_name("-d")

    def test_rejects_empty_whitespace_and_overlong_names(self) -> None:
        for bad in ("", " ", "a b", "x" * 33, "naïve"):
            with self.subTest(name=bad):
                with self.assertRaises(TmuxError):
                    validate_session_name(bad)


class WorkingDirectoryTests(unittest.TestCase):
    def test_a_missing_directory_is_refused_before_anything_starts(self) -> None:
        with self.assertRaisesRegex(TmuxError, "working directory does not exist"):
            build_new_session_args("s1", "codex", "/nonexistent/path/xyz")

    def test_a_file_is_not_a_working_directory(self) -> None:
        with self.assertRaises(TmuxError):
            build_new_session_args("s1", "codex", __file__)

    def test_the_directory_is_resolved_to_an_absolute_path(self) -> None:
        args = build_new_session_args("s1", "codex", "~")
        cwd = args[args.index("-c") + 1]
        self.assertTrue(os.path.isabs(cwd))
        self.assertFalse(cwd.startswith("~"))

    def test_geometry_bounds_are_enforced(self) -> None:
        for width, height in ((0, 50), (5000, 50), (200, 0), (200, 5000)):
            with self.subTest(width=width, height=height):
                with self.assertRaises(TmuxError):
                    build_new_session_args("s1", "codex", "/tmp", width, height)


class CreateOutcomeTests(unittest.TestCase):
    """The distinction the receipt model exists to preserve."""

    def test_a_created_session_is_not_a_running_agent(self) -> None:
        outcome = CreateOutcome(
            target_id="tmux:%9",
            created=True,
            runtime_requested="codex",
            runtime_observed="shell",
            session_name="s1",
            cwd="/tmp",
            reason="runtime_not_observed",
        )
        self.assertTrue(outcome.created)
        # A live pane running nothing must never read as a started agent.
        self.assertFalse(outcome.runtime_confirmed)

    def test_confirmed_only_when_the_process_tree_agrees(self) -> None:
        outcome = CreateOutcome(
            target_id="tmux:%9",
            created=True,
            runtime_requested="codex",
            runtime_observed="codex",
            session_name="s1",
            cwd="/tmp",
        )
        self.assertTrue(outcome.runtime_confirmed)

    def test_a_failed_creation_is_never_confirmed(self) -> None:
        outcome = CreateOutcome(
            target_id="",
            created=False,
            runtime_requested="codex",
            runtime_observed="codex",
            session_name="s1",
            cwd="/tmp",
        )
        self.assertFalse(outcome.runtime_confirmed)


class RealSessionTests(unittest.TestCase):
    """Actually create a session, on a throwaway socket."""

    @classmethod
    def setUpClass(cls) -> None:
        if subprocess.run(["which", "tmux"], capture_output=True).returncode != 0:
            raise unittest.SkipTest("tmux is not installed")
        # Bound here, not at import, so this class always drives its own socket
        # no matter which test module was imported last.
        cls._previous_socket = os.environ.get("YAPITALISM_TMUX_SOCKET")
        os.environ["YAPITALISM_TMUX_SOCKET"] = SOCKET
        tmux("kill-server")

    @classmethod
    def tearDownClass(cls) -> None:
        # Explicit -L: only ever kills the isolated test server.
        tmux("kill-server")
        if cls._previous_socket is None:
            os.environ.pop("YAPITALISM_TMUX_SOCKET", None)
        else:
            os.environ["YAPITALISM_TMUX_SOCKET"] = cls._previous_socket

    def test_a_missing_binary_yields_created_but_unconfirmed(self) -> None:
        """The honest-reporting path, forced deterministically.

        `AGENT_LAUNCHERS` is patched to a name that cannot exist, which is what
        a machine without the agent installed looks like. tmux still makes the
        session, so the caller must be told it exists AND that nothing runs in
        it — reporting only "created" would leave an orphan nobody cleans up.
        """
        from unittest import mock

        from yapitalism.mcp.backends.tmux_backend import TmuxBackend

        fake = dict(AGENT_LAUNCHERS, codex=("yapitalism-no-such-binary",))
        with mock.patch.dict(
            "yapitalism.mcp.tmux.AGENT_LAUNCHERS", fake, clear=True
        ):
            outcome = TmuxBackend().create_pane(
                "yap-missing", "codex", "/tmp", timeout=1.5
            )

        self.assertTrue(outcome.created)
        self.assertFalse(outcome.runtime_confirmed)
        self.assertEqual(outcome.reason, "runtime_not_observed")
        self.assertNotEqual(outcome.target_id, "")

    def test_a_duplicate_session_name_is_an_error_not_a_silent_reuse(self) -> None:
        from unittest import mock

        from yapitalism.mcp.backends.base import BackendError
        from yapitalism.mcp.backends.tmux_backend import TmuxBackend

        fake = dict(AGENT_LAUNCHERS, codex=("cat",))
        with mock.patch.dict(
            "yapitalism.mcp.tmux.AGENT_LAUNCHERS", fake, clear=True
        ):
            backend = TmuxBackend()
            backend.create_pane("yap-dupe", "codex", "/tmp", timeout=0.5)
            # Silently handing back the existing session would send the next
            # instruction to an agent already doing something else.
            with self.assertRaises(BackendError):
                backend.create_pane("yap-dupe", "codex", "/tmp", timeout=0.5)

    def test_the_operators_real_tmux_server_is_untouched(self) -> None:
        # An explicit -L socket, and not the default server.
        self.assertEqual(os.environ["YAPITALISM_TMUX_SOCKET"], SOCKET)
        # Only sessions this class created live here. If this ever saw the
        # operator's sessions, the isolation above has silently broken.
        listed = tmux("list-sessions", "-F", "#{session_name}").stdout.split()
        self.assertTrue(
            all(name.startswith("yap-") for name in listed),
            f"unexpected sessions on the test socket: {listed}",
        )


if __name__ == "__main__":
    unittest.main()


class BlockingPromptTests(unittest.TestCase):
    """A confirmed runtime is not a ready agent.

    Found on the first real end-to-end run: claude parked on its trust-folder
    dialog reported runtime_confirmed, swallowed the instruction sent to it, and
    the send came back an honest YELLOW whose cause was invisible.
    """

    def test_recognises_the_trust_dialog_seen_live(self) -> None:
        from yapitalism.mcp.backends.tmux_backend import detect_blocking_prompt

        # Verbatim from the pane in that run.
        pane = (
            "Claude Code'll be able to read, edit, and execute files here.\n"
            "Security guide\n"
            "> 1. Yes, I trust this folder\n"
            "  2. No, exit\n"
            "Enter to confirm . Esc to cancel"
        )
        self.assertEqual(detect_blocking_prompt(pane), "trust_prompt")

    def test_recognises_auth_and_confirm_prompts(self) -> None:
        from yapitalism.mcp.backends.tmux_backend import detect_blocking_prompt

        self.assertEqual(detect_blocking_prompt("Sign in to continue"), "auth_prompt")
        self.assertEqual(
            detect_blocking_prompt("Press Enter to continue"), "confirm_prompt"
        )

    def test_an_ordinary_prompt_is_not_flagged(self) -> None:
        from yapitalism.mcp.backends.tmux_backend import detect_blocking_prompt

        self.assertEqual(
            detect_blocking_prompt('> Try "fix typecheck errors"\nmanual mode on'), ""
        )

    def test_no_match_means_unrecognised_not_ready(self) -> None:
        """The claim this must never make.

        Empty is the absence of a recognised block, not evidence of readiness.
        An unknown blocking dialog returns "" too, so nothing downstream may
        read "" as a green light.
        """
        from yapitalism.mcp.backends.tmux_backend import detect_blocking_prompt

        unknown_block = "?? Awaiting license acceptance [1] accept [2] quit"
        self.assertEqual(detect_blocking_prompt(unknown_block), "")

    def test_blocked_on_is_only_serialised_when_something_was_seen(self) -> None:
        clear = CreateOutcome(
            target_id="tmux:%1", created=True, runtime_requested="claude",
            runtime_observed="claude", session_name="s", cwd="/tmp",
        )
        self.assertNotIn("blocked_on", clear.as_dict())
        blocked = CreateOutcome(
            target_id="tmux:%1", created=True, runtime_requested="claude",
            runtime_observed="claude", session_name="s", cwd="/tmp",
            blocked_on="trust_prompt",
        )
        self.assertEqual(blocked.as_dict()["blocked_on"], "trust_prompt")
        # Still confirmed: it IS running. Readiness is the separate question.
        self.assertTrue(blocked.runtime_confirmed)


class ClearActionWhitelistTests(unittest.TestCase):
    """Unsticking a pane must not become a free-form key tool.

    Escape cancels; Enter commits. A misdirected Escape loses a half-typed
    thought. A misdirected Enter picks whatever menu item is highlighted, which
    on this machine was "1. Update now (runs `npm install -g @openai/codex`)".
    """

    def test_enter_is_not_a_clear_action_and_never_becomes_one(self) -> None:
        from yapitalism.mcp.tmux import CLEAR_ACTIONS

        for keys in CLEAR_ACTIONS.values():
            self.assertNotIn("Enter", keys)
            self.assertNotIn("C-m", keys)
            self.assertNotIn("KPEnter", keys)

    def test_the_action_set_is_closed(self) -> None:
        from yapitalism.mcp.tmux import CLEAR_ACTIONS

        self.assertEqual(set(CLEAR_ACTIONS), {"escape", "clear-line", "escape-twice"})

    def test_an_unknown_action_is_refused_and_names_what_is_allowed(self) -> None:
        from yapitalism.mcp.tmux import send_clear_action

        with self.assertRaisesRegex(TmuxError, "known: clear-line, escape"):
            send_clear_action("tmux:%1", "Enter")

    def test_arbitrary_keys_cannot_be_smuggled_through_the_action_name(self) -> None:
        from yapitalism.mcp.tmux import send_clear_action

        for smuggled in ("Enter", "C-c", "rm -rf /", "Escape Enter", "", "1"):
            with self.subTest(action=smuggled):
                with self.assertRaises(TmuxError):
                    send_clear_action("tmux:%1", smuggled)


class ClearNeverClaimsEmptinessTests(unittest.TestCase):
    def test_prompt_empty_is_reported_as_unknown_not_as_true(self) -> None:
        """tmux declares empty_prompt_check False; clearing cannot invent it."""
        import unittest.mock as mock

        from yapitalism.mcp.backends.tmux_backend import TmuxBackend

        with mock.patch(
            "yapitalism.mcp.backends.tmux_backend.capture_pane",
            side_effect=["before text", "after text"],
        ), mock.patch(
            "yapitalism.mcp.backends.tmux_backend.send_clear_action",
            return_value=("Escape",),
        ):
            result = TmuxBackend().clear_prompt("tmux:%1", "escape")

        self.assertIsNone(result["prompt_empty"])
        self.assertTrue(result["pane_changed"])
        self.assertEqual(result["keys_sent"], ["Escape"])

    def test_a_recognised_block_disappearing_is_reported(self) -> None:
        import unittest.mock as mock

        from yapitalism.mcp.backends.tmux_backend import TmuxBackend

        with mock.patch(
            "yapitalism.mcp.backends.tmux_backend.capture_pane",
            side_effect=["Press enter to continue", "normal prompt"],
        ), mock.patch(
            "yapitalism.mcp.backends.tmux_backend.send_clear_action",
            return_value=("Escape",),
        ):
            result = TmuxBackend().clear_prompt("tmux:%1", "escape")

        self.assertEqual(result["blocking_before"], "confirm_prompt")
        self.assertEqual(result["blocking_after"], "")
        self.assertTrue(result["recognised_block_cleared"])
