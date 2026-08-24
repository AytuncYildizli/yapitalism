"""Read-only tmux inspection.

Shell-free throughout: every call passes an argument list, so no quoting,
globbing, or metacharacter in a pane's content can alter the command that runs.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass

_MAX_LINES = 1000
_TIMEOUT_SECONDS = 5

_PANE_FIELDS = (
    "#{pane_id}",
    "#{session_name}",
    "#{window_index}",
    "#{pane_index}",
    "#{pane_pid}",
    "#{pane_current_command}",
    "#{pane_width}",
    "#{pane_height}",
    "#{pane_dead}",
    "#{pane_current_path}",
)

_SHELLS = frozenset({"sh", "bash", "zsh", "fish", "dash", "ksh", "tcsh", "csh"})
_AGENTS: tuple[tuple[str, str], ...] = (
    ("codex", "codex"),
    ("claude", "claude"),
    ("kimi", "kimi"),
    ("opencode", "opencode"),
)


class TmuxError(RuntimeError):
    """A tmux invocation failed or returned something unusable."""


@dataclass(frozen=True, slots=True)
class TmuxPane:
    target_id: str
    session_name: str
    window_index: int
    pane_index: int
    pane_pid: int
    current_command: str
    width: int
    height: int
    dead: bool
    runtime: str
    current_path: str = ""


def _socket_args() -> list[str]:
    # Tests point this at a throwaway server so the operator's real tmux is
    # never reachable from a test run.
    socket = os.environ.get("YAPITALISM_TMUX_SOCKET")
    return ["-L", socket] if socket else []


# Where package managers put tmux when it is not on the server's PATH. A
# launchd service gets a minimal PATH, and a server started before
# `brew install tmux` keeps its stale one — both made a working tmux invisible
# and told the operator to restart something. Resolved per call, not cached,
# so installing tmux is enough.
_TMUX_FALLBACKS = (
    "/opt/homebrew/bin/tmux",
    "/usr/local/bin/tmux",
    "/home/linuxbrew/.linuxbrew/bin/tmux",
    "/usr/bin/tmux",
)


def _tmux_binary() -> str:
    found = shutil.which("tmux")
    if found:
        return found
    for candidate in _TMUX_FALLBACKS:
        if os.access(candidate, os.X_OK):
            return candidate
    raise TmuxError("tmux is not installed")


def _run(args: list[str], timeout: int = _TIMEOUT_SECONDS) -> str:
    try:
        completed = subprocess.run(
            [_tmux_binary(), *_socket_args(), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise TmuxError("tmux is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise TmuxError("tmux call timed out") from exc
    if completed.returncode != 0:
        # tmux writes its reason to stderr; keep it bounded.
        raise TmuxError(completed.stderr.strip()[:200] or "tmux call failed")
    return completed.stdout


def _basenames(args: str) -> list[str]:
    return [
        token.lstrip("-").rsplit("/", 1)[-1]
        for token in args.split()
        if token
    ]


def classify_tree(rows: list[tuple[int, int, str]], pane_pid: int) -> str:
    """Resolve what is actually running in a pane from the process tree.

    This is a security boundary, not a convenience. A pane that used to run an
    agent and now runs a plain shell must never be treated as an agent target,
    or a spoken instruction becomes an arbitrary shell command.
    """
    known, agent, saw_shell = _scan_tree(rows, pane_pid)
    if not known:
        return "unknown"
    if agent is not None:
        return agent[1]
    return "shell" if saw_shell else "unknown"


def agent_process(rows: list[tuple[int, int, str]], pane_pid: int) -> int | None:
    """The pid of the process that makes this pane an agent, or None.

    Same walk, same argv[0] rule as `classify_tree` — by construction, because
    both read one shared scan. Two implementations of the boundary would drift,
    and the drifted one would be the one an attacker gets to pick.
    """
    _, agent, _ = _scan_tree(rows, pane_pid)
    return agent[0] if agent is not None else None


def _scan_tree(
    rows: list[tuple[int, int, str]], pane_pid: int
) -> tuple[bool, tuple[int, str] | None, bool]:
    """(pane known, first agent node as (pid, runtime), saw a shell)."""
    by_parent: dict[int, list[tuple[int, int, str]]] = {}
    by_pid: dict[int, tuple[int, int, str]] = {}
    for row in rows:
        pid, ppid, _ = row
        by_pid[pid] = row
        by_parent.setdefault(ppid, []).append(row)

    if pane_pid not in by_pid:
        return False, None, False

    queue = [by_pid[pane_pid]]
    seen: set[int] = set()
    saw_shell = False
    while queue:
        pid, _, args = queue.pop(0)
        if pid in seen:
            continue
        seen.add(pid)
        names = _basenames(args)
        # argv[0] ONLY — the executable, not every token on the command line.
        #
        # This matched any token anywhere in any descendant's argv, so a plain shell
        # running `python3 -c '...' codex` in the background classified as codex.
        # Measured on an isolated socket: a zsh pane reported runtime='codex' while
        # pane_current_command was 'zsh'. That is the security boundary this function
        # exists to be, inverted into a way through it — a spoken instruction typed
        # into a shell and submitted is command execution.
        #
        # argv[0] still finds the real thing. Measured on a live codex pane: the pane
        # process is `node /opt/homebrew/bin/codex` (argv[0] = node, no match) and its
        # child execs `.../vendor/aarch64-apple-darwin/codex` (argv[0] = codex, match).
        # An agent that never execs a binary of its own name reads as a shell, which
        # over-refuses rather than typing into something unrecognised.
        executable = names[0] if names else ""
        for needle, runtime in _AGENTS:
            if executable == needle:
                return True, (pid, runtime), saw_shell
        if executable in _SHELLS:
            saw_shell = True
        queue.extend(by_parent.get(pid, []))
    return True, None, saw_shell


def parse_ps_table(raw: str) -> list[tuple[int, int, str]]:
    rows: list[tuple[int, int, str]] = []
    for line in raw.splitlines():
        match = re.match(r"^\s*(\d+)\s+(\d+)\s+(.*)$", line)
        if match:
            rows.append((int(match.group(1)), int(match.group(2)), match.group(3)))
    return rows


def pid_start_time(pid: int) -> str:
    """When this pid started, as ps prints it — the anti-reuse half of identity.

    A pid alone can be recycled by the OS; a pid plus its exact start time
    cannot be, in practice. Empty string when the process is gone or ps fails,
    which callers must treat as "identity unknown", never as a match.
    """
    try:
        completed = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return completed.stdout.strip()


def agent_fingerprint(pane_id: str) -> tuple[int, str, str] | None:
    """(pid, runtime, start_time) of the agent process owning this pane, or None.

    This is the identity a send holds while it types: captured after the gate
    admits the pane, verified again immediately before Enter. "The pane's
    runtime looks like claude" and "the same claude process that was admitted
    is still the one under the text" are different claims, and only the second
    survives an agent exiting into a shell mid-write.
    """
    try:
        raw = _run(["display-message", "-p", "-t", pane_id, "#{pane_pid}"])
    except TmuxError:
        return None
    try:
        pane_pid = int(raw.strip())
    except ValueError:
        return None
    _, agent, _ = _scan_tree(_process_table(), pane_pid)
    if agent is None:
        return None
    pid, runtime = agent
    started = pid_start_time(pid)
    if not started:
        # The process died between the scan and the ps: identity unknown.
        return None
    return pid, runtime, started


def _process_table() -> list[tuple[int, int, str]]:
    try:
        completed = subprocess.run(
            ["ps", "-ax", "-o", "pid=,ppid=,args="],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return parse_ps_table(completed.stdout)


#: What tmux says when no server has been started yet. Not a failure: tmux is
#: installed and working, there is simply nothing running. It reached the operator
#: as a raw socket path — "error connecting to /tmp/tmux-0/default (No such file or
#: directory)" — which is the shape of something broken, on the most common state a
#: fresh machine can be in.
_NO_SERVER = ("no server running on", "error connecting to")


def _no_server_yet(message: str) -> bool:
    lowered = message.lower()
    return any(phrase in lowered for phrase in _NO_SERVER)


def list_panes() -> list[TmuxPane]:
    try:
        raw = _run(["list-panes", "-a", "-F", "\t".join(_PANE_FIELDS)])
    except TmuxError as error:
        if _no_server_yet(str(error)):
            # An empty list is the honest answer, and it is not the "empty list from
            # a broken backend" this project refuses elsewhere: nothing failed, and
            # there is genuinely nothing to list. `tmux is not installed` still
            # raises, because that IS the caller's problem to hear about.
            return []
        raise
    processes = _process_table()
    panes: list[TmuxPane] = []
    for line in raw.splitlines():
        fields = line.split("\t")
        if len(fields) != len(_PANE_FIELDS):
            continue
        pane_pid = int(fields[4])
        panes.append(
            TmuxPane(
                target_id=f"tmux:{fields[0]}",
                session_name=fields[1],
                window_index=int(fields[2]),
                pane_index=int(fields[3]),
                pane_pid=pane_pid,
                current_command=fields[5],
                width=int(fields[6]),
                height=int(fields[7]),
                dead=fields[8] == "1",
                runtime=classify_tree(processes, pane_pid),
                current_path=fields[9],
            )
        )
    return panes


# The complete set of things this server will start. A create tool that took a
# command string would be remote code execution reachable by voice, so the
# runtime name is an index into this table and never argv itself. Extra flags
# are deliberately not accepted from callers for the same reason.
AGENT_LAUNCHERS: dict[str, tuple[str, ...]] = {
    "codex": ("codex",),
    "claude": ("claude",),
    "kimi": ("kimi",),
}

# tmux treats ':' and '.' as target separators, so a name containing either
# would address a different pane than the one reported back. A leading '-'
# would parse as a flag.
_SESSION_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")


def validate_session_name(name: str) -> str:
    if not _SESSION_NAME.fullmatch(name):
        raise TmuxError(
            "session name must be 1-32 chars of letters, digits, _ or -, "
            "and must not start with '-'"
        )
    return name


def require_installed(runtime: str) -> None:
    """Refuse a runtime whose binary is not on PATH, before anything is started.

    Deliberately NOT part of `build_new_session_args`. That function is the
    security boundary and is pure on purpose — its tests assert on argv without
    launching anything, on machines (CI included) where no agent is installed.
    Putting an environment probe inside it made those tests unable to build argv
    at all. Construction and the state of the machine are separate questions.

    Called from the two functions that actually launch. Measured on a clean box:
    `panes_create codex` with no codex made a session, the pane died at once, tmux
    exited for want of sessions, and the operator was handed "no server running on
    /tmp/tmux-0/default" — a socket path, for a missing program.

    The launcher name comes from the closed `AGENT_LAUNCHERS` table and never from
    the caller, so this cannot be turned into a probe for arbitrary binaries.
    """
    launcher = AGENT_LAUNCHERS.get(runtime)
    if launcher is None:
        return  # not our refusal to make; the builders name the known runtimes
    if shutil.which(launcher[0]) is None:
        raise TmuxError(
            f"{runtime} is not installed — nothing was started. "
            f"Install it and make sure `{launcher[0]}` is on PATH."
        )


def build_new_session_args(
    session_name: str,
    runtime: str,
    cwd: str,
    width: int = 200,
    height: int = 50,
) -> list[str]:
    """Build the argv for creating a detached agent session.

    Pure and separately tested: this is the security boundary, and asserting on
    the argv proves the whitelist and the `--` terminator hold without having to
    launch a real agent.
    """
    validate_session_name(session_name)
    launcher = AGENT_LAUNCHERS.get(runtime)
    if launcher is None:
        known = ", ".join(sorted(AGENT_LAUNCHERS))
        raise TmuxError(f"unknown runtime {runtime!r}; known runtimes: {known}")
    if not isinstance(width, int) or not 20 <= width <= 1000:
        raise TmuxError("width must be between 20 and 1000")
    if not isinstance(height, int) or not 5 <= height <= 1000:
        raise TmuxError("height must be between 5 and 1000")
    resolved = os.path.realpath(os.path.expanduser(cwd))
    if not os.path.isdir(resolved):
        raise TmuxError(f"working directory does not exist: {cwd}")
    return [
        "new-session",
        "-d",
        "-s",
        session_name,
        "-c",
        resolved,
        "-x",
        str(width),
        "-y",
        str(height),
        # Print the new pane's id so the caller never has to guess which pane it
        # just made — racing a list-panes could return someone else's new pane.
        "-P",
        "-F",
        "#{pane_id}",
        "--",
        *launcher,
    ]


def build_resume_session_args(
    session_name: str,
    runtime: str,
    cwd: str,
    session_id: str | None = None,
    width: int = 200,
    height: int = 50,
) -> list[str]:
    """Argv for a detached session that RESUMES an agent instead of starting one.

    Shares every guard with build_new_session_args - name validation, dimension
    bounds, a real directory, the `--` terminator - and differs only in what
    follows it, which comes from the closed resume table rather than
    AGENT_LAUNCHERS. Pure and separately tested for the same reason: the argv IS
    the security boundary, and asserting on it proves the terminator and the
    whitelist hold without launching an agent.
    """
    from .resume import resume_argv_for

    plan = resume_argv_for(runtime, session_id)
    if plan is None:
        if runtime not in AGENT_LAUNCHERS:
            known = ", ".join(sorted(AGENT_LAUNCHERS))
            raise TmuxError(f"unknown runtime {runtime!r}; known runtimes: {known}")
        raise TmuxError(
            f"session id {session_id!r} is not a usable session id; refusing rather "
            "than resuming whatever ran last"
        )
    args = build_new_session_args(session_name, runtime, cwd, width, height)
    # Everything up to and including `--` is already validated; replace only the
    # command after it.
    terminator = args.index("--")
    return [*args[: terminator + 1], *plan.argv]


def new_resumed_session(
    session_name: str,
    runtime: str,
    cwd: str,
    session_id: str | None = None,
    width: int = 200,
    height: int = 50,
) -> tuple[str, str]:
    """Resume one known agent in a fresh detached session.

    Returns (target_id, fidelity). The fidelity travels with the pane id because
    a caller that only learns "a pane exists" cannot say which conversation is in
    it.
    """
    from .resume import resume_argv_for

    args = build_resume_session_args(session_name, runtime, cwd, session_id, width, height)
    require_installed(runtime)
    plan = resume_argv_for(runtime, session_id)
    assert plan is not None  # build_resume_session_args already refused otherwise
    pane_id = _run(args).strip()
    if not re.fullmatch(r"%\d+", pane_id):
        raise TmuxError(f"tmux did not report a usable pane id: {pane_id[:80]!r}")
    return f"tmux:{pane_id}", plan.fidelity


def new_agent_session(
    session_name: str,
    runtime: str,
    cwd: str,
    width: int = 200,
    height: int = 50,
) -> str:
    """Create a detached session running one known agent. Returns its target id."""
    args = build_new_session_args(session_name, runtime, cwd, width, height)
    require_installed(runtime)
    pane_id = _run(args).strip()
    if not re.fullmatch(r"%\d+", pane_id):
        raise TmuxError(f"tmux did not report a usable pane id: {pane_id[:80]!r}")
    return f"tmux:{pane_id}"


def observe_runtime(target_id: str) -> str:
    """What is actually running in one pane, right now."""
    for pane in list_panes():
        if pane.target_id == target_id:
            return pane.runtime
    return "unknown"


def pane_id_from_target(target_id: str) -> str:
    if not target_id.startswith("tmux:"):
        raise TmuxError("target id must be tmux-namespaced, e.g. tmux:%0")
    pane_id = target_id[len("tmux:") :]
    if not re.fullmatch(r"%\d+", pane_id):
        raise TmuxError("invalid tmux pane id")
    return pane_id


def capture_pane(target_id: str, lines: int = 200) -> str:
    if not isinstance(lines, int) or not 0 < lines <= _MAX_LINES:
        raise TmuxError(f"lines must be between 1 and {_MAX_LINES}")
    pane_id = pane_id_from_target(target_id)
    return _run(["capture-pane", "-p", "-t", pane_id, "-S", f"-{lines}"])


def send_literal(target_id: str, text: str) -> None:
    """Type text into a pane without interpreting it.

    `-l` sends the bytes literally and `--` stops flag parsing, so text
    beginning with `-` is data. The argument list means no shell is involved at
    any point, so metacharacters cannot execute.
    """
    if not text:
        raise TmuxError("text must not be empty")
    pane_id = pane_id_from_target(target_id)
    _run(["send-keys", "-t", pane_id, "-l", "--", text])


def send_enter(target_id: str) -> None:
    """Submit whatever is currently staged in the pane."""
    pane_id = pane_id_from_target(target_id)
    _run(["send-keys", "-t", pane_id, "Enter"])


# The complete set of keys this server will send to unstick a pane. Closed for
# the same reason AGENT_LAUNCHERS is closed: a free-form key tool reachable by
# voice is arbitrary input into someone's terminal.
#
# Enter is deliberately absent and must stay absent. Escape cancels; Enter
# commits. A misdirected Escape loses a half-typed thought. A misdirected Enter
# picks whatever menu item is highlighted — on this machine that was
# "1. Update now (runs `npm install -g @openai/codex`)".
CLEAR_ACTIONS: dict[str, tuple[str, ...]] = {
    # Dismiss a dialog or cancel the current input. What Norget's open MCP menu
    # needs.
    "escape": ("Escape",),
    # readline/emacs "kill line": empties an input that already holds text.
    # What Mahobrain's pending prompt needs.
    "clear-line": ("C-u",),
    # Some TUIs clear on Escape only after leaving an inner mode.
    "escape-twice": ("Escape", "Escape"),
}


def send_clear_action(target_id: str, action: str) -> tuple[str, ...]:
    """Send one named unstick action. Returns the keys actually sent."""
    keys = CLEAR_ACTIONS.get(action)
    if keys is None:
        known = ", ".join(sorted(CLEAR_ACTIONS))
        raise TmuxError(f"unknown clear action {action!r}; known: {known}")
    pane_id = pane_id_from_target(target_id)
    for key in keys:
        # One key per call, named — never a literal string, so nothing here can
        # be read as text to type.
        _run(["send-keys", "-t", pane_id, key])
    return keys
