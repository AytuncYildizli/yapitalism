from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path
from typing import Any

from .adapters.superset import SupersetAdapter, SupersetConfig

from .model import EvidenceEvent, Leg, LegState, Provenance, Receipt


def receipt_from_fixture(payload: dict[str, Any]) -> Receipt:
    receipt = Receipt(
        command_id=str(payload["command_id"]),
        handoff_required=bool(payload.get("handoff_required", False)),
        handoff_destination=payload.get("handoff_destination"),
    )
    for raw in payload.get("events", []):
        receipt.record(
            EvidenceEvent(
                event_id=str(raw["event_id"]),
                command_id=receipt.command_id,
                leg=Leg(raw["leg"]),
                state=LegState(raw["state"]),
                kind=str(raw["kind"]),
                provenance=Provenance(raw["provenance"]),
                occurred_at=str(raw.get("occurred_at", "fixture")),
                reason=str(raw.get("reason", "")),
                evidence_ref=str(raw.get("evidence_ref", "")),
            )
        )
    return receipt


def doctor(path: Path) -> int:
    payload = json.loads(path.read_text(encoding="utf-8"))
    receipt = receipt_from_fixture(payload)
    print(receipt.summary())
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
    adapter = SupersetAdapter(SupersetConfig.from_manifest(args.manifest))
    command_id = args.client_token or str(uuid.uuid4())
    if not args.confirm_send:
        dispatched = adapter.dispatch(
            args.text,
            expected_revision=args.expect_revision,
            client_token=command_id,
            confirm=False,
        )
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

    baseline = adapter.snapshot()
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
    dispatched = adapter.dispatch(
        args.text,
        expected_revision=baseline.revision,
        client_token=command_id,
        confirm=True,
    )
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
    if args.command == "superset" and args.superset_command == "status":
        return superset_status(args.manifest, args.max_lines)
    if args.command == "superset" and args.superset_command == "send":
        return superset_send(args)
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
