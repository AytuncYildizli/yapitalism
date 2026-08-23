from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

from .adapters.superset import SupersetAdapter, SupersetConfig, TrpcError
from .claims import ConfirmationClaimStore
from .ledger import JsonlLedger
from .mcp.backends.base import GUARANTEES
from .setup import Environment
from .model import EvidenceEvent, Leg, LegState, Provenance, Receipt, Status


def _event_from_raw(raw: dict[str, object], *, command_id: str | None = None) -> EvidenceEvent:
    sequence_raw = raw.get("sequence")
    if sequence_raw is not None and (isinstance(sequence_raw, bool) or not isinstance(sequence_raw, int)):
        raise ValueError("event sequence must be an integer")
    return EvidenceEvent(
        event_id=str(raw["event_id"]),
        command_id=command_id or str(raw["command_id"]),
        leg=Leg(raw["leg"]),
        state=LegState(raw["state"]),
        kind=str(raw["kind"]),
        provenance=Provenance(raw["provenance"]),
        occurred_at=str(raw.get("occurred_at", "fixture")),
        reason=str(raw.get("reason", "")),
        evidence_ref=str(raw.get("evidence_ref", "")),
        sequence=sequence_raw,
        supersedes=(str(raw["supersedes"]) if raw.get("supersedes") is not None else None),
        actor_id=(str(raw["actor_id"]) if raw.get("actor_id") is not None else None),
        source_id=(str(raw["source_id"]) if raw.get("source_id") is not None else None),
        target_id=(str(raw["target_id"]) if raw.get("target_id") is not None else None),
        session_id=(str(raw["session_id"]) if raw.get("session_id") is not None else None),
        delivery_id=(str(raw["delivery_id"]) if raw.get("delivery_id") is not None else None),
    )


def receipt_from_fixture(payload: dict[str, Any]) -> Receipt:
    receipt = Receipt(
        command_id=str(payload["command_id"]),
        handoff_required=bool(payload.get("handoff_required", False)),
        handoff_destination=payload.get("handoff_destination"),
    )
    for raw in payload.get("events", []):
        receipt.record(_event_from_raw(raw, command_id=receipt.command_id))
    return receipt


def doctor(path: Path) -> int:
    payload = json.loads(path.read_text(encoding="utf-8"))
    receipt = receipt_from_fixture(payload)
    print(receipt.summary())
    return 0


def _state_root() -> Path:
    configured = os.environ.get("XDG_STATE_HOME")
    return (Path(configured) if configured else Path.home() / ".local" / "state") / "yapitalism"


def receipt_show(ledger_path: Path, command_id: str) -> int:
    # One locked read for both verification and projection. Verifying and then
    # re-reading would let a writer swap the file in between, so the projected
    # rows would not be the verified ones.
    verification, all_rows = JsonlLedger(ledger_path).verified_rows()
    if not verification.valid:
        print(
            json.dumps(
                {"command_id": command_id, "reason": "ledger_unverified", "detail": verification.reason},
                sort_keys=True,
            )
        )
        return 2
    rows = [row for row in all_rows if row.get("command_id") == command_id]
    if not rows:
        print(json.dumps({"command_id": command_id, "reason": "receipt_not_found"}, sort_keys=True))
        return 2
    receipt = receipt_from_fixture({"command_id": command_id, "events": rows})
    if receipt.status is Status.GREEN:
        # The ledger stores events only — it has no handoff fields — so this
        # projection cannot rule out a claimed handoff with an unknown
        # destination, which ADR-0001 says must block GREEN. Demote the verdict
        # rather than appending a caveat: a suffix on a line that still reads
        # GREEN is not a gate, because every consumer matching on the status
        # word keeps seeing success.
        print(f"{Status.YELLOW.value} command={command_id} pending=handoff_unrecorded")
        return 0
    print(receipt.summary())
    return 0


def ledger_verify(ledger_path: Path) -> int:
    result = JsonlLedger(ledger_path).verify()
    print(
        json.dumps(
            {
                "valid": result.valid,
                "event_count": result.event_count,
                "chain_head": result.chain_head,
                "reason": result.reason,
            },
            sort_keys=True,
        )
    )
    return 0 if result.valid else 2


def ledger_manifest(ledger_path: Path) -> int:
    manifest = JsonlLedger(ledger_path).manifest()
    print(
        json.dumps(
            {
                "authority": manifest.authority,
                "valid": manifest.valid,
                "schema_version": manifest.schema_version,
                "projection_version": manifest.projection_version,
                "event_count": manifest.event_count,
                "command_count": manifest.command_count,
                "first_sequence": manifest.first_sequence,
                "last_sequence": manifest.last_sequence,
                "chain_head": manifest.chain_head,
                "verification_reason": manifest.verification_reason,
            },
            sort_keys=True,
        )
    )
    return 0 if manifest.valid else 2


def ledger_migrate(source_path: Path, output_path: Path) -> int:
    if source_path.resolve() == output_path.resolve() or output_path.exists():
        print(json.dumps({"migrated": False, "reason": "migration_output_must_be_new"}, sort_keys=True))
        return 2
    try:
        source_rows = JsonlLedger(source_path).read()
        if any(
            row.get("schema_version") is not None
            or row.get("sequence") is not None
            or row.get("event_hash") is not None
            for row in source_rows
        ):
            raise ValueError("source is not a legacy ledger")
        output = JsonlLedger(output_path)
        for row in source_rows:
            output.append(_event_from_raw(row))
        verification = output.verify()
        if not verification.valid:
            raise ValueError("migrated ledger failed verification")
    except (KeyError, OSError, ValueError):
        print(json.dumps({"migrated": False, "reason": "ledger_migration_failed"}, sort_keys=True))
        return 2
    print(
        json.dumps(
            {
                "migrated": True,
                "migrated_events": verification.event_count,
                "chain_head": verification.chain_head,
                "source_unchanged": True,
            },
            sort_keys=True,
        )
    )
    return 0


_HTTP_FORM = "codex mcp add yapitalism --url http://127.0.0.1:8792/mcp"
_STDIO_FORM = '{"mcpServers": {"yapitalism": {"command": "yapitalism-mcp", "args": ["--stdio"]}}}'
# Hermes takes the same URL, but its `mcp add` insists on an interactive tool
# picker, so the honest instruction is the config block it actually reads —
# a command that stalls in a script is not an instruction.
_HERMES_FORM = (
    "add under mcp_servers: in ~/.hermes/config.yaml -> "
    "yapitalism: {url: http://127.0.0.1:8792/mcp}"
)


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
        f"  URL clients (Codex):     {_HTTP_FORM}",
        f"  Hermes:                  {_HERMES_FORM}",
        f"  stdio clients (rest):    {_STDIO_FORM}",
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


def superset_send(args: argparse.Namespace) -> int:
    command_id = args.client_token or str(uuid.uuid4())
    if args.confirm_send and args.client_token is None:
        print(
            json.dumps(
                {
                    "dispatched": False,
                    "reason": "client_token_required_for_confirmed_send",
                },
                sort_keys=True,
            )
        )
        return 2
    ledger = JsonlLedger(args.ledger)
    try:
        ledger.require_appendable()
    except (OSError, ValueError):
        print(
            json.dumps(
                {"command_id": command_id, "dispatched": False, "reason": "ledger_migration_required"},
                sort_keys=True,
            )
        )
        return 2
    adapter = SupersetAdapter(SupersetConfig.from_manifest(args.manifest))
    claims = ConfirmationClaimStore(args.claim_dir)
    if not args.confirm_send:
        dispatched = adapter.dispatch(
            args.text,
            expected_revision=args.expect_revision,
            client_token=command_id,
            confirm=False,
        )
        # Evidence FIRST, authorization second. Reversed, a crash or a failed append
        # between the two left a live claim that could authorize a confirmed mutation
        # while the dry run it rests on was never durably recorded - authorization
        # outliving its own evidence, in the component whose whole job is that the
        # evidence is authoritative. This is write-ahead logging, and the codebase
        # already applies the discipline to its one-shot POST.
        try:
            ledger.append(dispatched.to_evidence(command_id))
        except (ValueError, PermissionError, OSError):
            print(
                json.dumps(
                    {"command_id": command_id, "dispatched": False, "reason": "dry_run_evidence_unrecorded"},
                    sort_keys=True,
                )
            )
            return 2
        try:
            claims.issue(
                client_token=command_id,
                command_id=command_id,
                text=args.text,
                expected_revision=args.expect_revision,
                terminal_id=adapter.config.terminal_id,
            )
        except (ValueError, PermissionError, OSError):
            print(
                json.dumps(
                    {"command_id": command_id, "dispatched": False, "reason": "confirmation_claim_rejected"},
                    sort_keys=True,
                )
            )
            return 2
        print(
            json.dumps(
                {
                    "command_id": command_id,
                    "confirmation_required": True,
                    "dry_run": dispatched.dry_run,
                    "expected_revision": args.expect_revision,
                    "target_terminal_id": adapter.config.terminal_id,
                },
                sort_keys=True,
            )
        )
        return 0

    # Keep the baseline and polling windows identical so a pre-existing marker
    # cannot enter the wider polling view after unrelated revision movement.
    baseline = adapter.snapshot(max_lines=1000)
    if baseline.revision != args.expect_revision:
        print(
            json.dumps(
                {
                    "command_id": command_id,
                    "dispatched": False,
                    "reason": "baseline_revision_mismatch",
                    "baseline_revision": baseline.revision,
                },
                sort_keys=True,
            )
        )
        return 2
    adapter.validate_canary(
        args.canary,
        baseline_text=baseline.text,
        submitted_text=args.text,
    )
    try:
        command_id = claims.consume(
            client_token=command_id,
            text=args.text,
            expected_revision=baseline.revision,
            terminal_id=adapter.config.terminal_id,
        )
    except (ValueError, PermissionError, OSError):
        print(
            json.dumps(
                {"command_id": command_id, "dispatched": False, "reason": "confirmation_claim_rejected"},
                sort_keys=True,
            )
        )
        return 2
    if command_id != args.client_token:
        # The claim's command_id and the client token are the same value today,
        # but nothing enforces it. If they ever diverge, one of them keys the
        # POST and the other labels the receipt, so a send could be dispatched
        # under an idempotency key that no receipt refers to. Refuse instead of
        # silently choosing.
        print(
            json.dumps(
                {"command_id": command_id, "dispatched": False, "reason": "claim_identity_mismatch"},
                sort_keys=True,
            )
        )
        return 2
    try:
        dispatched = adapter.dispatch(
            args.text,
            expected_revision=baseline.revision,
            # The client token, not the claim's command_id. Superset keyed the
            # dry-run on this exact token, so the confirmed send must reuse it
            # or the idempotency key changes and one logical confirmation can
            # become two POSTs. The equality assertion above makes the
            # relationship explicit instead of silently preferring either one.
            client_token=args.client_token,
            confirm=True,
        )
    except TrpcError:
        ledger.append(
            EvidenceEvent(
                event_id=str(uuid.uuid4()),
                command_id=command_id,
                leg=Leg.DISPATCH,
                state=LegState.FAILED,
                kind="terminal.send.ambiguous",
                provenance=Provenance.API,
                reason="transport_ambiguous",
                evidence_ref=f"terminal:{adapter.config.terminal_id}",
            )
        )
        print(
            json.dumps(
                {"command_id": command_id, "dispatched": False, "reason": "transport_ambiguous"},
                sort_keys=True,
            )
        )
        return 2
    ledger.append(dispatched.to_evidence(command_id))
    if not dispatched.dispatched:
        print(
            json.dumps(
                {
                    "command_id": command_id,
                    "dispatched": False,
                    "phase": dispatched.phase,
                },
                sort_keys=True,
            )
        )
        return 2
    canary = adapter.await_canary(
        args.canary,
        command_id=command_id,
        baseline_revision=baseline.revision,
        baseline_text=baseline.text,
        submitted_text=args.text,
        timeout=args.canary_timeout,
        poll_interval=args.poll_interval,
    )
    ledger.append(canary.evidence)
    print(
        json.dumps(
            {
                "command_id": command_id,
                "dispatched": True,
                "accepted": canary.observed,
                "attempts": canary.attempts,
                "last_revision": canary.last_revision,
            },
            sort_keys=True,
        )
    )
    return 0 if canary.observed else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="yapitalism")
    subcommands = parser.add_subparsers(dest="command", required=True)
    doctor_parser = subcommands.add_parser("doctor", help="project a scrubbed fixture")
    doctor_parser.add_argument("fixture", type=Path)

    receipt_parser = subcommands.add_parser("receipt", help="project persisted receipt evidence")
    receipt_commands = receipt_parser.add_subparsers(dest="receipt_command", required=True)
    receipt_show_parser = receipt_commands.add_parser("show", help="show one projected receipt")
    receipt_show_parser.add_argument("command_id")
    receipt_show_parser.add_argument("--ledger", type=Path, default=_state_root() / "events.jsonl")

    ledger_parser = subcommands.add_parser("ledger", help="verify the authoritative evidence ledger")
    ledger_commands = ledger_parser.add_subparsers(dest="ledger_command", required=True)
    ledger_verify_parser = ledger_commands.add_parser("verify", help="verify sequence and hash-chain integrity")
    ledger_verify_parser.add_argument("--ledger", type=Path, default=_state_root() / "events.jsonl")
    ledger_manifest_parser = ledger_commands.add_parser("manifest", help="summarize authority and projection state")
    ledger_manifest_parser.add_argument("--ledger", type=Path, default=_state_root() / "events.jsonl")
    ledger_migrate_parser = ledger_commands.add_parser("migrate", help="copy a legacy ledger into a new chained file")
    ledger_migrate_parser.add_argument("--source", type=Path, required=True)
    ledger_migrate_parser.add_argument("--output", type=Path, required=True)

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

    send_parser = superset_commands.add_parser("send", help="dry-run or send and prove a canary")
    send_parser.add_argument("--manifest", required=True, type=Path)
    send_parser.add_argument("--text", required=True)
    send_parser.add_argument("--canary", required=True)
    send_parser.add_argument("--expect-revision", required=True, type=int)
    send_parser.add_argument("--client-token")
    send_parser.add_argument("--ledger", type=Path, default=_state_root() / "events.jsonl")
    send_parser.add_argument("--claim-dir", type=Path, default=_state_root() / "claims")
    send_parser.add_argument(
        "--confirm-send",
        action="store_true",
        help="perform the side-effecting terminal.send call (default: dry-run)",
    )
    send_parser.add_argument("--canary-timeout", type=float, default=8.0)
    send_parser.add_argument("--poll-interval", type=float, default=0.3)
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
        return doctor(args.fixture)
    if args.command == "receipt" and args.receipt_command == "show":
        return receipt_show(args.ledger, args.command_id)
    if args.command == "ledger" and args.ledger_command == "verify":
        return ledger_verify(args.ledger)
    if args.command == "ledger" and args.ledger_command == "manifest":
        return ledger_manifest(args.ledger)
    if args.command == "ledger" and args.ledger_command == "migrate":
        return ledger_migrate(args.source, args.output)
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
    if args.command == "superset" and args.superset_command == "send":
        return superset_send(args)
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
