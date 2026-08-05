"""What this machine has, what that buys, and what is still missing.

An install that prints "installed successfully" tells someone nothing they can
act on. This one interviews the machine instead: it looks for the two backends,
the agent binaries, the running host and the MCP clients, then shows the
guarantees each available backend actually provides — the same HOST / CLIENT /
NONE values a receipt will carry, read from the real capability objects rather
than from prose that can drift away from them.

Detection is separated from presentation on purpose. Every probe here returns
data, so the whole report can be tested without a terminal, and nothing is
claimed present without having been looked for.

Nothing in this module writes anything. The report ends in the exact commands to
run, because editing somebody's client config behind a progress bar is how an
install becomes something people undo.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .adapters.superset import SupersetAdapter, SupersetConfig
from .adapters.superset.provision import HostRecord, ProvisionError, discover_hosts, select_host
from .mcp.backends.base import BackendCapabilities

#: The agents a pane may be running. Same list the tmux launcher table accepts,
#: because offering to start something this tool cannot address would be a lie.
AGENTS = ("codex", "claude", "kimi")

_PROBE_TIMEOUT = 5


@dataclass(frozen=True, slots=True)
class TmuxFacts:
    installed: bool
    server_running: bool
    pane_count: int
    detail: str


@dataclass(frozen=True, slots=True)
class SupersetFacts:
    app_installed: bool
    #: A host manifest was found AND names a live process.
    host_live: bool
    #: "guarded" when the host routes terminal.send, "stock" when it does not,
    #: "unknown" when no live host could be asked. Never guessed from a version
    #: number or an app name.
    build: str
    organization_id: str
    detail: str


@dataclass(frozen=True, slots=True)
class ClientFacts:
    name: str
    config_path: Path
    present: bool
    #: How this client wants to be pointed at the server.
    transport: str


@dataclass(frozen=True, slots=True)
class Environment:
    tmux: TmuxFacts
    agents: dict[str, bool]
    superset: SupersetFacts
    clients: tuple[ClientFacts, ...]
    manifest_written: bool
    manifest_path: Path
    host: HostRecord | None = field(default=None, repr=False)

    @property
    def usable_backends(self) -> tuple[str, ...]:
        """Backends that could actually carry a send right now.

        tmux needs a running server; Superset needs a live host AND the manifest
        this tool loads. A backend that would fail on first use is not listed,
        because the point of the report is to be actionable.
        """
        usable: list[str] = []
        if self.tmux.installed and self.tmux.server_running:
            usable.append("tmux")
        if self.superset.host_live and self.manifest_written:
            usable.append("superset")
        return tuple(usable)


def detect_tmux() -> TmuxFacts:
    binary = shutil.which("tmux")
    if binary is None:
        return TmuxFacts(False, False, 0, "not on PATH")
    socket = os.environ.get("YAPITALISM_TMUX_SOCKET")
    args = ["tmux", *(["-L", socket] if socket else []), "list-panes", "-a", "-F", "#{pane_id}"]
    try:
        done = subprocess.run(
            args, capture_output=True, text=True, timeout=_PROBE_TIMEOUT, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return TmuxFacts(True, False, 0, "installed, but listing panes failed")
    if done.returncode != 0:
        # "no server running" is the ordinary case on a fresh machine, not an
        # error worth alarming anybody about.
        return TmuxFacts(True, False, 0, "installed, no server running yet")
    panes = [line for line in done.stdout.splitlines() if line.strip()]
    where = f" on socket {socket}" if socket else ""
    return TmuxFacts(True, True, len(panes), f"server running{where}, {len(panes)} pane(s)")


def detect_agents() -> dict[str, bool]:
    return {agent: shutil.which(agent) is not None for agent in AGENTS}


def _superset_app_installed() -> bool:
    return any(Path("/Applications").glob("Superset*.app"))


def detect_superset() -> tuple[SupersetFacts, HostRecord | None]:
    """Find the host and ask it which build it is.

    The build is established by asking whether `terminal.send` is routed, never
    by an app name or a version string. That distinction is the whole reason the
    capability table below can be trusted.
    """
    app = _superset_app_installed()
    records = discover_hosts()
    if not records:
        detail = (
            "app installed, but no host manifest — start it once"
            if app
            else "not installed"
        )
        return SupersetFacts(app, False, "unknown", "", detail), None
    try:
        record = select_host(records)
    except ProvisionError as error:
        return SupersetFacts(app, False, "unknown", "", str(error)), None
    adapter = SupersetAdapter(
        SupersetConfig(
            endpoint=record.endpoint,
            bearer_token=record.auth_token,
            workspace_id="pending-discovery",
            terminal_id="pending-discovery",
            timeout_seconds=float(_PROBE_TIMEOUT),
        )
    )
    guarded = adapter.host_enforces_send_guards()
    build = "guarded" if guarded else "stock"
    detail = (
        f"host live at {record.endpoint} ({build} build)"
        if app
        else f"host live at {record.endpoint} ({build} build), app not in /Applications"
    )
    return SupersetFacts(app, True, build, record.organization_id, detail), record


#: Where the clients this tool has been used with keep their MCP config, and how
#: each wants to be addressed. Codex takes a URL; the rest spawn the process.
_CLIENTS: tuple[tuple[str, str, str], ...] = (
    ("Codex", ".codex/config.toml", "http"),
    ("Claude Code", ".claude.json", "stdio"),
    ("Claude Desktop", "Library/Application Support/Claude/claude_desktop_config.json", "stdio"),
    ("Cursor", ".cursor/mcp.json", "stdio"),
)


def detect_clients(home: Path | None = None) -> tuple[ClientFacts, ...]:
    root = home or Path.home()
    return tuple(
        ClientFacts(name=name, config_path=root / relative, present=(root / relative).exists(), transport=transport)
        for name, relative, transport in _CLIENTS
    )


def inspect(home: Path | None = None) -> Environment:
    from .mcp.backends.superset_backend import resolve_manifest_path

    superset, record = detect_superset()
    manifest = resolve_manifest_path()
    return Environment(
        tmux=detect_tmux(),
        agents=detect_agents(),
        superset=superset,
        clients=detect_clients(home),
        manifest_written=manifest.exists(),
        manifest_path=manifest,
        host=record,
    )


def capabilities_for(env: Environment) -> dict[str, BackendCapabilities]:
    """The real capability objects for each usable backend.

    Read from the backends themselves rather than restated here. A table that
    hard-coded these words would be a second source of truth about guarantees,
    which is the mistake this project already made once at a larger scale.
    """
    from .mcp.backends.superset_backend import CLIENT_GUARDED, HOST_GUARDED
    from .mcp.backends.tmux_backend import TMUX_CAPABILITIES

    rows: dict[str, BackendCapabilities] = {}
    for backend in env.usable_backends:
        if backend == "tmux":
            rows["tmux"] = TMUX_CAPABILITIES
        elif backend == "superset":
            rows["superset"] = HOST_GUARDED if env.superset.build == "guarded" else CLIENT_GUARDED
    return rows


def next_steps(env: Environment) -> list[str]:
    """What is left to do, in the order it has to happen.

    Only steps that are actually outstanding. A checklist that re-lists finished
    work trains people to skim it.
    """
    steps: list[str] = []
    if not any(env.agents.values()):
        steps.append(
            "install at least one agent CLI (codex, claude or kimi) — there is "
            "nothing to talk to without one"
        )
    if env.superset.host_live and not env.manifest_written:
        steps.append(f"yapitalism superset setup --confirm    # writes {env.manifest_path}")
    if not env.tmux.installed and not env.superset.host_live:
        steps.append("install tmux, or start Superset — both backends are absent")
    if not any(client.present for client in env.clients):
        steps.append(
            "no MCP client config found; register the server with whichever client "
            "you use (see the two forms above)"
        )
    return steps
