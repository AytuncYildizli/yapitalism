"""Resuming an agent, and the fidelity distinction that makes it honest.

Ported from a frozen Superset fork worktree. The argv table matters, but the
reason this is worth porting at all is `fidelity`: `exact` and `last` are
different claims, and a resume that cannot tell them apart produces an agent that
comes up looking correct while holding the wrong conversation.

The argv builders are pure, which is the point — they are the security boundary,
so they can be asserted on without launching anything.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest

SOCKET = "yapitalism-tmux-test"
os.environ["YAPITALISM_TMUX_SOCKET"] = SOCKET

from yapitalism.mcp.resume import (  # noqa: E402
    EXACT,
    LAST,
    RESUMABLE_RUNTIMES,
    resume_argv_for,
    speak_fidelity,
)
from yapitalism.mcp.tmux import (  # noqa: E402
    AGENT_LAUNCHERS,
    TmuxError,
    build_resume_session_args,
)

UUID = "0199a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b"


class ResumeArgvTests(unittest.TestCase):
    def test_a_recorded_session_id_is_used_verbatim_and_marked_exact(self) -> None:
        expected = {
            "codex": ("codex", "resume", UUID),
            "claude": ("claude", "--resume", UUID),
            "kimi": ("kimi", "--session", UUID),
        }
        for runtime, argv in expected.items():
            with self.subTest(runtime=runtime):
                plan = resume_argv_for(runtime, UUID)
                assert plan is not None
                self.assertEqual(plan.argv, argv)
                self.assertEqual(plan.fidelity, EXACT)

    def test_no_session_id_asks_for_the_most_recent_and_says_so(self) -> None:
        expected = {
            "codex": ("codex", "resume", "--last"),
            "claude": ("claude", "--continue"),
            "kimi": ("kimi", "--continue"),
        }
        for runtime, argv in expected.items():
            with self.subTest(runtime=runtime):
                plan = resume_argv_for(runtime)
                assert plan is not None
                self.assertEqual(plan.argv, argv)
                self.assertEqual(plan.fidelity, LAST)

    def test_an_unusable_session_id_is_refused_not_downgraded(self) -> None:
        """The whole reason this is a port and not a rewrite.

        Turning "resume this session" into "resume whatever ran last" would come up
        looking correct and be a different conversation — invisible to everyone.
        """
        for bad in (
            "; rm -rf /",
            "--last",
            "../../etc/passwd",
            "id with spaces",
            "",
            "x" * 129,
            "sess\nid",
        ):
            with self.subTest(session_id=bad):
                self.assertIsNone(resume_argv_for("codex", bad))

    def test_a_runtime_with_no_resume_form_is_refused(self) -> None:
        for runtime in ("shell", "bash", "unknown", "", "CODEX"):
            with self.subTest(runtime=runtime):
                self.assertIsNone(resume_argv_for(runtime, UUID))

    def test_every_launchable_runtime_is_also_resumable(self) -> None:
        # A runtime this server can start but not resume would be a gap someone
        # discovers when their agent dies, which is the worst moment to find it.
        self.assertEqual(set(RESUMABLE_RUNTIMES), set(AGENT_LAUNCHERS))

    def test_the_two_fidelities_are_spoken_differently(self) -> None:
        exact, last = speak_fidelity(EXACT), speak_fidelity(LAST)
        self.assertNotEqual(exact, last)
        # `last` must carry the doubt, or the distinction dies at the last step.
        self.assertIn("garanti yok", last)
        self.assertNotIn("garanti yok", exact)


class ResumeSessionArgvTests(unittest.TestCase):
    """The argv handed to tmux — asserted, never launched."""

    def test_the_command_replaces_only_what_follows_the_terminator(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            args = build_resume_session_args("yap-r", "codex", cwd, UUID)
        self.assertIn("--", args)
        terminator = args.index("--")
        self.assertEqual(args[terminator + 1 :], ["codex", "resume", UUID])
        # Everything the fresh-start builder guards is still in front of it.
        self.assertEqual(args[0], "new-session")
        self.assertIn("-d", args)
        self.assertIn("-P", args)
        self.assertIn(os.path.realpath(cwd), args)

    def test_a_session_id_cannot_smuggle_a_flag_or_a_shell_fragment(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            for bad in ("--last", "; id", "$(id)", "-x"):
                with self.subTest(session_id=bad), self.assertRaises(TmuxError) as caught:
                    build_resume_session_args("yap-r", "codex", cwd, bad)
                self.assertIn("not a usable session id", str(caught.exception))

    def test_an_unknown_runtime_says_which_ones_exist(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            with self.assertRaises(TmuxError) as caught:
                build_resume_session_args("yap-r", "bash", cwd)
        self.assertIn("unknown runtime", str(caught.exception))

    def test_the_session_name_and_directory_are_still_validated(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            for name in ("-bad", "has:colon", "has.dot", "x" * 33, ""):
                with self.subTest(name=name), self.assertRaises(TmuxError):
                    build_resume_session_args(name, "codex", cwd, UUID)
        with self.assertRaises(TmuxError):
            build_resume_session_args("yap-r", "codex", "/nonexistent/dir", UUID)


class ResumeToolShapeTests(unittest.TestCase):
    def test_the_tool_refuses_a_bad_session_id_without_starting_anything(self) -> None:
        if subprocess.run(["which", "tmux"], capture_output=True).returncode != 0:
            raise unittest.SkipTest("tmux is not installed")
        from yapitalism.mcp.server import panes_resume

        before = subprocess.run(
            ["tmux", "-L", SOCKET, "list-sessions"], capture_output=True, text=True
        ).stdout
        with tempfile.TemporaryDirectory() as cwd:
            # @mcp.tool returns the plain function in this fastmcp version, so it
            # is callable directly - no .fn wrapper to reach through.
            result = panes_resume(runtime="codex", cwd=cwd, session_id="--last")
        self.assertFalse(result["ok"])
        self.assertIn("not a usable session id", str(result["error"]))
        after = subprocess.run(
            ["tmux", "-L", SOCKET, "list-sessions"], capture_output=True, text=True
        ).stdout
        self.assertEqual(before, after)

    def test_fidelity_reaches_the_payload(self) -> None:
        from yapitalism.mcp.backends.base import CreateOutcome

        payload = CreateOutcome(
            target_id="tmux:%9",
            created=True,
            runtime_requested="codex",
            runtime_observed="codex",
            session_name="yap-r",
            cwd="/tmp",
            fidelity=LAST,
        ).as_dict()
        self.assertEqual(payload["fidelity"], LAST)
        # A fresh start has no fidelity question, so the key stays absent rather
        # than carrying an empty string for a caller to misread.
        fresh = CreateOutcome(
            target_id="tmux:%9",
            created=True,
            runtime_requested="codex",
            runtime_observed="codex",
            session_name="yap-c",
            cwd="/tmp",
        ).as_dict()
        self.assertNotIn("fidelity", fresh)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
