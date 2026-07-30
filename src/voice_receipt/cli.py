from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="voice-receipt")
    subcommands = parser.add_subparsers(dest="command", required=True)
    doctor_parser = subcommands.add_parser("doctor", help="project a scrubbed fixture")
    doctor_parser.add_argument("fixture", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        return doctor(args.fixture)
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
