from __future__ import annotations

import unittest

from relayproof.model import EvidenceEvent, Leg, LegState, Provenance, Receipt, Status


def event(
    number: int,
    leg: Leg,
    state: LegState = LegState.SUCCEEDED,
    *,
    kind: str = "stage.proven",
    provenance: Provenance = Provenance.API,
    reason: str = "",
    sequence: int | None = None,
    supersedes: str | None = None,
) -> EvidenceEvent:
    if leg is Leg.ACCEPT and state is LegState.SUCCEEDED and kind == "stage.proven":
        kind = "canary.observed"
        provenance = Provenance.TERMINAL_DIFF
    return EvidenceEvent(
        event_id=f"evt-{number}",
        command_id="cmd-1",
        leg=leg,
        state=state,
        kind=kind,
        provenance=provenance,
        occurred_at="fixture",
        reason=reason,
        sequence=sequence,
        supersedes=supersedes,
    )


class ReceiptTests(unittest.TestCase):
    def test_empty_receipt_is_yellow(self) -> None:
        self.assertIs(Receipt("cmd-1").status, Status.YELLOW)

    def test_failed_leg_is_red(self) -> None:
        receipt = Receipt("cmd-1")
        receipt.record(event(1, Leg.ACCEPT, LegState.FAILED, kind="canary.missed"))
        self.assertIs(receipt.status, Status.RED)
        self.assertIn("reason=canary.missed", receipt.summary())

    def test_all_required_legs_are_green(self) -> None:
        receipt = Receipt("cmd-1")
        for number, leg in enumerate(Leg, start=1):
            receipt.record(event(number, leg))
        self.assertIs(receipt.status, Status.GREEN)

    def test_revision_movement_cannot_prove_acceptance(self) -> None:
        with self.assertRaisesRegex(ValueError, "accept requires"):
            event(
                1,
                Leg.ACCEPT,
                kind="terminal.revision",
                provenance=Provenance.TERMINAL_DIFF,
            )

    def test_inference_cannot_prove_success(self) -> None:
        with self.assertRaisesRegex(ValueError, "inference cannot prove"):
            event(1, Leg.DISPATCH, provenance=Provenance.INFERRED)

    def test_unknown_handoff_blocks_green(self) -> None:
        receipt = Receipt("cmd-1", handoff_required=True)
        for number, leg in enumerate(Leg, start=1):
            receipt.record(event(number, leg))
        self.assertIs(receipt.status, Status.YELLOW)
        self.assertIn("handoff_destination", receipt.summary())

    def test_identity_dimensions_persist_without_changing_proof_authority(self) -> None:
        identity_event = EvidenceEvent(
            event_id="evt-identity",
            command_id="cmd-1",
            leg=Leg.DISPATCH,
            state=LegState.PENDING,
            kind="terminal.send.pending",
            provenance=Provenance.API,
            actor_id="operator:efe",
            source_id="cli:local",
            target_id="terminal:one",
            session_id="session:one",
            delivery_id="delivery:one",
        )
        receipt = Receipt("cmd-1")
        receipt.record(identity_event)

        self.assertIs(receipt.status, Status.YELLOW)
        self.assertEqual(identity_event.as_dict()["actor_id"], "operator:efe")
        self.assertEqual(identity_event.as_dict()["target_id"], "terminal:one")

    def test_empty_or_oversized_identity_dimension_is_rejected(self) -> None:
        for actor_id in (" ", "x" * 513):
            with self.assertRaisesRegex(ValueError, "actor_id"):
                EvidenceEvent(
                    event_id="evt-identity",
                    command_id="cmd-1",
                    leg=Leg.CAPTURE,
                    state=LegState.PENDING,
                    kind="terminal.snapshot",
                    provenance=Provenance.API,
                    actor_id=actor_id,
                )

    def test_context_only_snapshot_cannot_downgrade_proven_capture(self) -> None:
        receipt = Receipt("cmd-1")
        for number, leg in enumerate(Leg, start=1):
            receipt.record(event(number, leg, sequence=number))
        receipt.record(
            EvidenceEvent(
                event_id="evt-context",
                command_id="cmd-1",
                leg=Leg.CAPTURE,
                state=LegState.PENDING,
                kind="terminal.snapshot",
                provenance=Provenance.API,
                reason="context_only",
                sequence=10,
            )
        )

        self.assertIs(receipt.status, Status.GREEN)

    def test_duplicate_event_is_idempotent(self) -> None:
        receipt = Receipt("cmd-1")
        evidence = event(1, Leg.CAPTURE)
        receipt.record(evidence)
        receipt.record(evidence)
        self.assertEqual(len(receipt.events), 1)

    def test_superseded_failure_does_not_poison_status_or_summary(self) -> None:
        receipt = Receipt("cmd-1")
        receipt.record(
            event(1, Leg.ACCEPT, LegState.FAILED, kind="canary.missed", reason="timeout", sequence=1)
        )
        receipt.record(
            event(2, Leg.ACCEPT, kind="canary.observed", sequence=2, supersedes="evt-1")
        )

        self.assertIs(receipt.status, Status.YELLOW)
        self.assertNotIn("failed=accept", receipt.summary())

    def test_latest_success_hides_prior_unsuperseded_failure_from_summary(self) -> None:
        receipt = Receipt("cmd-1")
        receipt.record(event(1, Leg.DISPATCH, LegState.FAILED, reason="ambiguous", sequence=1))
        receipt.record(event(2, Leg.DISPATCH, sequence=2))

        self.assertIs(receipt.status, Status.YELLOW)
        self.assertNotIn("failed=dispatch", receipt.summary())

    def test_sequence_not_wall_clock_controls_latest_event(self) -> None:
        receipt = Receipt("cmd-1")
        receipt.record(event(2, Leg.DISPATCH, LegState.FAILED, sequence=2))
        receipt.record(event(1, Leg.DISPATCH, sequence=1))

        self.assertIs(receipt.status, Status.RED)


if __name__ == "__main__":
    unittest.main()
