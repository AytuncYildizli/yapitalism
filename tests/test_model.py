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


class SupersessionScopeTests(unittest.TestCase):
    """Supersession may correct a leg's own record and nothing else."""

    def all_legs_green(self) -> Receipt:
        receipt = Receipt(command_id="cmd-1")
        for number, leg in enumerate(Leg, start=1):
            receipt.record(event(number, leg, sequence=number))
        return receipt

    def test_cross_leg_supersession_cannot_hide_a_failed_leg(self) -> None:
        receipt = self.all_legs_green()
        self.assertIs(receipt.status, Status.GREEN)

        receipt.record(
            event(90, Leg.ACCEPT, LegState.FAILED, kind="canary.missed", reason="canary_timeout", sequence=90)
        )
        self.assertIs(receipt.status, Status.RED)

        # A CAPTURE event must not be able to delete the ACCEPT failure and
        # resurrect the older ACCEPT success.
        receipt.record(
            event(91, Leg.CAPTURE, sequence=91, supersedes="evt-90")
        )
        self.assertIs(receipt.status, Status.RED)

    def test_same_leg_supersession_still_applies(self) -> None:
        receipt = self.all_legs_green()
        receipt.record(
            event(90, Leg.WORK, LegState.FAILED, kind="work.stalled", reason="stalled", sequence=90)
        )
        self.assertIs(receipt.status, Status.RED)

        receipt.record(event(91, Leg.WORK, sequence=91, supersedes="evt-90"))
        self.assertIs(receipt.status, Status.GREEN)

    def test_supersedes_pointing_at_an_absent_event_changes_nothing(self) -> None:
        receipt = self.all_legs_green()
        receipt.record(event(92, Leg.WORK, sequence=92, supersedes="evt-does-not-exist"))
        self.assertIs(receipt.status, Status.GREEN)
