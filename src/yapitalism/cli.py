from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .adapters.superset import SupersetAdapter, SupersetConfig
from .mcp.backends.base import GUARANTEES
from .setup import Environment


def _state_root() -> Path:
    configured = os.environ.get("XDG_STATE_HOME")
    return (Path(configured) if configured else Path.home() / ".local" / "state") / "yapitalism"


_STDIO_FORM = '{"mcpServers": {"yapitalism": {"command": "yapitalism-mcp", "args": ["--stdio"]}}}'


def _registration_lines() -> list[str]:
    """The client registrations, carrying whatever auth the server will demand.

    HTTP is fail-closed since 0.3.0: the server mints a bearer token on first
    start and 401s without it, so a registration line without the token is an
    instruction to get locked out. The token itself is printed here — this runs
    in the operator's own terminal, which is exactly where their secret belongs —
    but the server only ever prints the PATH, because launchd keeps its stderr.
    """
    from .mcp.server import _http_token_path, load_or_create_http_token

    token = load_or_create_http_token()
    url = "http://127.0.0.1:8792/mcp"
    return [
        f"  token (0600):            {_http_token_path()}",
        f"  Codex:                   export YAPITALISM_MCP_TOKEN=$(cat {_http_token_path()})",
        f"                           codex mcp add yapitalism --url {url} "
        "--bearer-token-env-var YAPITALISM_MCP_TOKEN",
        f'  Claude Code:             claude mcp add --transport http yapitalism {url} '
        f'--header "Authorization: Bearer {token}"',
        "  Hermes:                  add under mcp_servers: in ~/.hermes/config.yaml ->",
        f"                           yapitalism: {{url: {url}, headers: "
        f"{{Authorization: Bearer {token}}}}}",
        f"  stdio clients (rest):    {_STDIO_FORM}",
        "                           (stdio needs no token: the OS decided who may spawn it)",
    ]


def _mark(present: bool) -> str:
    # Words, not colour: this output gets pasted into issues and read over SSH.
    return "yes" if present else "no "


def render_setup(env: Environment) -> list[str]:
    """The install report, as lines. Pure, so the whole thing is testable."""
    from .setup import capabilities_for, next_steps

    lines = ["What this machine has", ""]
    lines.append(f"  tmux            {_mark(env.tmux.installed)}  {env.tmux.detail}")
    for agent, present in env.agents.items():
        lines.append(f"  {agent:<15} {_mark(present)}  {'on PATH' if present else 'not on PATH'}")
    lines.append(f"  Superset        {_mark(env.superset.host_live)}  {env.superset.detail}")
    manifest = (
        f"present at {env.manifest_path}" if env.manifest_written else f"not written yet ({env.manifest_path})"
    )
    lines.append(f"  manifest        {_mark(env.manifest_written)}  {manifest}")
    for client in env.clients:
        if client.present:
            lines.append(f"  {client.name:<15} {_mark(True)}  {client.config_path} ({client.transport})")

    rows = capabilities_for(env)
    lines += ["", "What that buys you", ""]
    if not rows:
        lines.append("  nothing yet — no backend can carry a send on this machine")
    else:
        header = f"  {'backend':<10} " + " ".join(f"{name.replace('_', ' '):<19}" for name in GUARANTEES)
        lines += [header + "runtime", "  " + "-" * (len(header) + 5)]
        for backend, caps in rows.items():
            cells = " ".join(f"{getattr(caps, name):<19}" for name in GUARANTEES)
            lines.append(f"  {backend:<10} {cells}{caps.runtime_detection}")
        lines += [
            "",
            "  host   = the host refuses the write itself; check and write are one operation",
            "  client = this process checks, then writes; real, but not atomic",
            "  none   = nothing checks",
        ]

    steps = next_steps(env)
    if steps:
        lines += ["", "What is left", ""]
        lines += [f"  {index}. {step}" for index, step in enumerate(steps, start=1)]
    lines += [
        "",
        "Registering the server with a client",
        "",
        *_registration_lines(),
    ]
    return lines


def setup_report(args: argparse.Namespace) -> int:
    """Interview the machine, then offer the one action worth offering.

    The offer is gated on a TTY and on `--confirm` never being implied: a setup
    command that writes while someone is reading its output is indistinguishable
    from one that ignored them.
    """
    import sys

    from .setup import inspect

    env = inspect()
    for line in render_setup(env):
        print(line)
    if not (env.superset.host_live and not env.manifest_written):
        return 0
    print("")
    if not sys.stdin.isatty():
        print("Superset is live but its manifest is missing; see step above.")
        return 0
    answer = input("Write the Superset manifest now? [y/N] ").strip().lower()
    if answer not in ("y", "yes"):
        print("Left it alone.")
        return 0
    setup_args = argparse.Namespace(
        organization=None,
        output=_default_manifest_path(),
        confirm=True,
        force=False,
        timeout=8.0,
    )
    print("")
    return superset_setup(setup_args)


def _default_manifest_path() -> Path:
    """Where a provisioned manifest belongs, per the backend's own rule."""
    from .mcp.backends.superset_backend import manifest_write_path

    return manifest_write_path()


def _existing_binding(path: Path) -> tuple[str, str] | None:
    """The (workspace, terminal) a manifest already names, if one is there."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return str(payload["workspace_id"]), str(payload["terminal_id"])
    except (OSError, ValueError, KeyError):
        return None


def superset_setup(args: argparse.Namespace) -> int:
    """Provision a manifest from the Superset install already on this machine.

    Prints a plan and writes nothing unless asked, matching `send`'s dry-run
    default. The plan is worth having on its own: it is also the diagnostic for
    "why can't this tool see my Superset".

    The token is never printed. It is read, proven against the host, and written
    to a 0600 file — an installer's terminal is a place people paste into chat.
    """
    from .adapters.superset.provision import (
        ProvisionError,
        discover_hosts,
        manifest_payload,
        probe_binding,
        select_host,
        write_manifest,
    )

    try:
        record = select_host(discover_hosts(), args.organization)
        print(f"host        {record.endpoint}  (organization {record.organization_id})")
        print(f"source      {record.source}  mode 0600, pid {record.pid} alive")
        # Carry the existing binding into the probe so a token refresh does not
        # also require a running agent — the failure that makes a rotated token
        # unrepairable is the one `setup` now tells people to fix.
        binding = probe_binding(record, timeout=args.timeout, previous=_existing_binding(args.output))
    except ProvisionError as error:
        print(f"cannot provision: {error}")
        return 1
    print(
        f"workspace   {binding.workspace_name} ({binding.workspace_id}) "
        f"— {binding.terminal_count} terminal(s)"
    )
    print(f"binding     {binding.terminal_id} running {binding.runtime}")
    print("token       read and accepted by the host, not printed")
    payload = manifest_payload(record, binding, timeout=args.timeout)
    if not args.confirm:
        print(f"\nwould write {args.output} (0600). Re-run with --confirm to write it.")
        return 0
    try:
        written = write_manifest(payload, args.output, force=args.force)
    except ProvisionError as error:
        print(f"cannot provision: {error}")
        return 1
    print(f"\nwrote       {written} (0600)")
    return 0


def superset_status(manifest: Path, max_lines: int | None) -> int:
    adapter = SupersetAdapter(SupersetConfig.from_manifest(manifest))
    snapshot = adapter.snapshot(max_lines=max_lines)
    print(
        json.dumps(
            {
                "terminal_id": snapshot.terminal_id,
                "revision": snapshot.revision,
                "cols": snapshot.cols,
                "rows": snapshot.rows,
            },
            sort_keys=True,
        )
    )
    return 0


def doctor_live() -> int:
    """Is the thing actually working, right now?

    `setup` answers "what does this machine have"; this answers "is it healthy" —
    the running server found and version-matched, the token present, each backend
    answering, and the guarantee table read from live capabilities. The table
    lives HERE rather than in the launch pitch: who enforced which guarantee is a
    diagnostic for the person debugging, not a thing to reason about per send.
    """
    import urllib.request

    from . import __version__
    from .mcp.server import DEFAULT_HOST, DEFAULT_PORT, _http_token_path

    lines: list[str] = ["", "Service", ""]
    port = int(os.environ.get("YAPITALISM_MCP_PORT", DEFAULT_PORT))
    url = f"http://{DEFAULT_HOST}:{port}/mcp"
    token = ""
    try:
        token = Path(_http_token_path()).read_text().strip()
    except OSError:
        pass
    body = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "doctor", "version": "0"}},
    }).encode()
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, data=body, headers=headers, method="POST"),
            timeout=5,
        ) as response:
            raw = response.read(65536).decode(errors="replace")
        served = ""
        marker = '"version":"'
        if '"serverInfo"' in raw:
            tail = raw.split('"serverInfo"', 1)[1]
            if marker in tail:
                served = tail.split(marker, 1)[1].split('"', 1)[0]
        if served == __version__:
            lines.append(f"  server     yes  {url} serving {served}")
        elif served:
            lines.append(
                f"  server     OLD  {url} serves {served}, installed is "
                f"{__version__} — restart the service to pick up the new code"
            )
        else:
            lines.append(f"  server     ???  {url} answered but named no version")
    except OSError as error:
        lines.append(f"  server     no   {url} did not answer ({error})")
    lines.append(
        f"  token      {'yes' if token else 'no '}  {_http_token_path()}"
        + ("" if token else "  (minted on the server's first HTTP start)")
    )

    lines += ["", "Backends, live", ""]
    from .mcp.backends.superset_backend import SupersetBackend
    from .mcp.backends.tmux_backend import TmuxBackend
    from .mcp.backends.base import BackendError, BackendUnavailable

    for backend in (TmuxBackend(), SupersetBackend()):
        try:
            panes = backend.list_panes()
            agents = sum(1 for pane in panes if pane.runtime in ("codex", "claude", "kimi", "opencode"))
            lines.append(
                f"  {backend.namespace:<9} yes  {len(panes)} pane(s), {agents} running an agent"
            )
        except BackendUnavailable as absent:
            lines.append(f"  {backend.namespace:<9} --   {absent}")
        except BackendError as error:
            lines.append(f"  {backend.namespace:<9} NO   {error}")

    lines += ["", "Who enforces what (host = atomic refusal, client = check-then-write, none = nothing)", ""]
    for backend in (TmuxBackend(), SupersetBackend()):
        try:
            caps = backend.capabilities()
            lines.append(
                f"  {backend.namespace:<9} dispatch={caps.idempotent_dispatch} "
                f"revision={caps.optimistic_revision} prompt={caps.empty_prompt_check} "
                f"runtime={caps.runtime_detection}"
            )
        except Exception as error:  # noqa: BLE001 - a diagnostic must not die mid-report
            lines.append(f"  {backend.namespace:<9} could not be asked ({error})")

    for line in lines:
        print(line)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="yapitalism")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser(
        "doctor",
        help="is it healthy right now: server, token, backends, and who enforces what",
    )
    subcommands.add_parser(
        "setup",
        help="interview this machine: what it has, what that buys, what is missing",
    )

    watch_parser = subcommands.add_parser(
        "watch",
        help="watch agent panes and notify when one silently waits, breaks or dies",
    )
    watch_parser.add_argument(
        "--interval", type=float, default=300.0,
        help="seconds between polls (default 300)",
    )
    watch_parser.add_argument(
        "--notify", default="",
        help="URL to POST findings to, one plain-text line each "
        "(ntfy.sh topics and generic webhooks both work); omitted, findings only print",
    )
    watch_parser.add_argument(
        "--once", action="store_true",
        help="one poll and exit, for cron; state resets per run, so each run "
        "reports everything currently noteworthy rather than only transitions",
    )

    demo_parser = subcommands.add_parser(
        "demo",
        help="start a throwaway agent, send one proven message, show the receipt",
    )
    demo_parser.add_argument(
        "--runtime", default="", help="which agent to start (default: first of codex/claude/kimi on PATH)"
    )
    demo_parser.add_argument(
        "--keep", action="store_true", help="leave the demo session running afterwards"
    )

    superset_parser = subcommands.add_parser("superset", help="operate a Superset terminal")
    superset_commands = superset_parser.add_subparsers(dest="superset_command", required=True)
    setup_parser = superset_commands.add_parser(
        "setup", help="provision a manifest from the Superset app on this machine"
    )
    setup_parser.add_argument(
        "--organization", help="organization id, when more than one host is live"
    )
    setup_parser.add_argument("--output", type=Path, default=_default_manifest_path())
    setup_parser.add_argument(
        "--confirm",
        action="store_true",
        help="write the manifest (default: report what it would do)",
    )
    setup_parser.add_argument(
        "--force", action="store_true", help="replace an existing manifest"
    )
    setup_parser.add_argument("--timeout", type=float, default=8.0)

    status_parser = superset_commands.add_parser("status", help="read terminal snapshot metadata")
    status_parser.add_argument("--manifest", required=True, type=Path)
    status_parser.add_argument("--max-lines", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    # The MCP Registry resolves this package to its same-named console script and
    # passes `--stdio`. That script is this CLI, and the MCP server is the separate
    # `yapitalism-mcp` entry point - so every registry-driven client died on an
    # argparse error before it could send `initialize`. The product IS the MCP
    # server, and its listing could not start it.
    #
    # A shim rather than a renamed entry point: the published server.json already
    # names this script, and changing the package's argv shape would strand the
    # listing that is live right now. `yapitalism-mcp` keeps working unchanged.
    raw = sys.argv[1:] if argv is None else argv
    if "--stdio" in raw:
        from .mcp.server import main as mcp_main

        return mcp_main(list(raw))

    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        return doctor_live()
    if args.command == "demo":
        from .demo import run_demo

        return run_demo(args.runtime, keep=args.keep)
    if args.command == "watch":
        from .watch import run_watch

        return run_watch(interval=args.interval, notify_url=args.notify, once=args.once)
    if args.command == "setup":
        return setup_report(args)
    if args.command == "superset" and args.superset_command == "setup":
        return superset_setup(args)
    if args.command == "superset" and args.superset_command == "status":
        return superset_status(args.manifest, args.max_lines)
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
