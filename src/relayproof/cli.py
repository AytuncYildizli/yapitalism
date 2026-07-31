from __future__ import annotations

import argparse
import json
import os
import uuid
from pathlib import Path
from typing import Any

from .adapters.superset import SupersetAdapter, SupersetConfig, TrpcError
from .claims import ConfirmationClaimStore
from .ledger import JsonlLedger
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
    return (Path(configured) if configured else Path.home() / ".local" / "state") / "relayproof"


def receipt_show(ledger_path: Path, command_id: str) -> int:
    ledger = JsonlLedger(ledger_path)
    # Verify the chain before projecting. `read()` parses rows but checks no
    # hashes, so without this an edited row — acceptance flipped to succeeded —
    # projects GREEN even though `ledger verify` would reject the file. The
    # chain is only worth having if the consumer that prints verdicts uses it.
    verification = ledger.verify()
    if not verification.valid:
        print(
            json.dumps(
                {"command_id": command_id, "reason": "ledger_unverified", "detail": verification.reason},
                sort_keys=True,
            )
        )
        return 2
    rows = [row for row in ledger.read() if row.get("command_id") == command_id]
    if not rows:
        print(json.dumps({"command_id": command_id, "reason": "receipt_not_found"}, sort_keys=True))
        return 2
    receipt = receipt_from_fixture({"command_id": command_id, "events": rows})
    summary = receipt.summary()
    if receipt.status is Status.GREEN:
        # The ledger stores events only. It carries no handoff fields, so this
        # projection cannot rule out a claimed handoff with an unknown
        # destination — which ADR-0001 says must block GREEN. Label the verdict
        # as leg-only rather than letting it read as a full receipt.
        summary += " handoff=unrecorded"
    print(summary)
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
        ledger.append(dispatched.to_evidence(command_id))
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
    try:
        dispatched = adapter.dispatch(
            args.text,
            expected_revision=baseline.revision,
            # The token the claim authorized, not the raw argument. They are
            # equal today only because issue() is always called with
            # command_id == client_token; nothing enforces that, and a
            # divergence would silently send Superset an idempotency key that
            # no longer matches the receipt's identity.
            client_token=command_id,
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
    parser = argparse.ArgumentParser(prog="relayproof")
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

    superset_parser = subcommands.add_parser("superset", help="operate a Superset terminal")
    superset_commands = superset_parser.add_subparsers(dest="superset_command", required=True)
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
    if args.command == "superset" and args.superset_command == "status":
        return superset_status(args.manifest, args.max_lines)
    if args.command == "superset" and args.superset_command == "send":
        return superset_send(args)
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
