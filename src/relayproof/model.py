from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class Status(str, Enum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"


class Leg(str, Enum):
    CAPTURE = "capture"
    DISPATCH = "dispatch"
    ACCEPT = "accept"
    WORK = "work"
    DELIVER = "deliver"


class LegState(str, Enum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Provenance(str, Enum):
    API = "api"
    TERMINAL_DIFF = "terminal_diff"
    UI_OBSERVATION = "ui_observation"
    USER_REPORT = "user_report"
    INFERRED = "inferred"


_ACCEPTANCE_KINDS = frozenset({"canary.observed", "agent.acknowledged"})
_REQUIRED_LEGS = tuple(Leg)


@dataclass(frozen=True, slots=True)
class EvidenceEvent:
    event_id: str
    command_id: str
    leg: Leg
    state: LegState
    kind: str
    provenance: Provenance
    occurred_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    reason: str = ""
    evidence_ref: str = ""
    sequence: int | None = None
    supersedes: str | None = None

    def __post_init__(self) -> None:
        if not self.event_id.strip() or not self.command_id.strip() or not self.kind.strip():
            raise ValueError("event_id, command_id, and kind are required")
        if self.state is LegState.SUCCEEDED and self.provenance is Provenance.INFERRED:
            raise ValueError("inference cannot prove a successful leg")
        if (
            self.leg is Leg.ACCEPT
            and self.state is LegState.SUCCEEDED
            and self.kind not in _ACCEPTANCE_KINDS
        ):
            raise ValueError("accept requires canary.observed or agent.acknowledged")
        if self.sequence is not None and self.sequence <= 0:
            raise ValueError("sequence must be positive when provided")
        if self.supersedes is not None and not self.supersedes.strip():
            raise ValueError("supersedes must be non-empty when provided")

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["leg"] = self.leg.value
        payload["state"] = self.state.value
        payload["provenance"] = self.provenance.value
        return payload


@dataclass(slots=True)
class Receipt:
    command_id: str
    handoff_required: bool = False
    handoff_destination: str | None = None
    _events: list[EvidenceEvent] = field(default_factory=lambda: list[EvidenceEvent](), repr=False)

    def record(self, event: EvidenceEvent) -> None:
        if event.command_id != self.command_id:
            raise ValueError("event command_id does not match receipt")
        if any(existing.event_id == event.event_id for existing in self._events):
            return
        self._events.append(event)

    @property
    def events(self) -> tuple[EvidenceEvent, ...]:
        return tuple(self._events)

    def latest_by_leg(self) -> dict[Leg, EvidenceEvent]:
        latest: dict[Leg, EvidenceEvent] = {}
        for event in self.effective_events():
            latest[event.leg] = event
        return latest

    def effective_events(self) -> tuple[EvidenceEvent, ...]:
        indexed = list(enumerate(self._events))
        ordered = [
            event
            for _, event in sorted(
                indexed,
                key=lambda pair: (
                    pair[1].sequence if pair[1].sequence is not None else pair[0] + 1,
                    pair[0],
                ),
            )
        ]
        superseded = {event.supersedes for event in ordered if event.supersedes is not None}
        return tuple(event for event in ordered if event.event_id not in superseded)

    @property
    def status(self) -> Status:
        latest = self.latest_by_leg()
        if any(event.state is LegState.FAILED for event in latest.values()):
            return Status.RED
        if self.handoff_required and not self.handoff_destination:
            return Status.YELLOW
        if all(
            leg in latest and latest[leg].state is LegState.SUCCEEDED
            for leg in _REQUIRED_LEGS
        ):
            return Status.GREEN
        return Status.YELLOW

    @property
    def failed_event(self) -> EvidenceEvent | None:
        latest = tuple(self.latest_by_leg().values())
        for event in reversed(latest):
            if event.state is LegState.FAILED:
                return event
        return None

    def summary(self) -> str:
        failed = self.failed_event
        if failed is not None:
            reason = failed.reason or failed.kind
            return (
                f"{self.status.value} command={self.command_id} "
                f"failed={failed.leg.value} reason={reason}"
            )
        latest = self.latest_by_leg()
        pending = [leg.value for leg in _REQUIRED_LEGS if leg not in latest]
        if self.handoff_required and not self.handoff_destination:
            pending.append("handoff_destination")
        if pending:
            return f"{self.status.value} command={self.command_id} pending={','.join(pending)}"
        return f"{self.status.value} command={self.command_id}"
