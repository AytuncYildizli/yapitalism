"""Read-only tmux inspection.

Shell-free throughout: every call passes an argument list, so no quoting,
globbing, or metacharacter in a pane's content can alter the command that runs.
"""

from __future__ import annotations

import os
import re
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
)

_SHELLS = frozenset({"sh", "bash", "zsh", "fish", "dash", "ksh", "tcsh", "csh"})
_AGENTS: tuple[tuple[str, str], ...] = (
    ("codex", "codex"),
    ("claude", "claude"),
    ("kimi", "kimi"),
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


def _socket_args() -> list[str]:
    # Tests point this at a throwaway server so the operator's real tmux is
    # never reachable from a test run.
    socket = os.environ.get("YAPITALISM_TMUX_SOCKET")
    return ["-L", socket] if socket else []


def _run(args: list[str], timeout: int = _TIMEOUT_SECONDS) -> str:
    try:
        completed = subprocess.run(
            ["tmux", *_socket_args(), *args],
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
    by_parent: dict[int, list[tuple[int, int, str]]] = {}
    by_pid: dict[int, tuple[int, int, str]] = {}
    for row in rows:
        pid, ppid, _ = row
        by_pid[pid] = row
        by_parent.setdefault(ppid, []).append(row)

    if pane_pid not in by_pid:
        return "unknown"

    queue = [by_pid[pane_pid]]
    seen: set[int] = set()
    saw_shell = False
    while queue:
        pid, _, args = queue.pop(0)
        if pid in seen:
            continue
        seen.add(pid)
        names = _basenames(args)
        for needle, runtime in _AGENTS:
            if needle in names:
                return runtime
        if names and names[0] in _SHELLS:
            saw_shell = True
        queue.extend(by_parent.get(pid, []))
    return "shell" if saw_shell else "unknown"


def parse_ps_table(raw: str) -> list[tuple[int, int, str]]:
    rows: list[tuple[int, int, str]] = []
    for line in raw.splitlines():
        match = re.match(r"^\s*(\d+)\s+(\d+)\s+(.*)$", line)
        if match:
            rows.append((int(match.group(1)), int(match.group(2)), match.group(3)))
    return rows


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


def list_panes() -> list[TmuxPane]:
    raw = _run(["list-panes", "-a", "-F", "\t".join(_PANE_FIELDS)])
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
            )
        )
    return panes


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
