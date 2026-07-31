from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from .model import EvidenceEvent

_MAX_LEDGER_BYTES = 64 * 1024 * 1024
_LEDGER_FIELDS = frozenset({"schema_version", "sequence", "prev_hash", "event_hash"})
_OPTIONAL_EVENT_FIELDS = frozenset(
    {"supersedes", "actor_id", "source_id", "target_id", "session_id", "delivery_id"}
)


@dataclass(frozen=True, slots=True)
class LedgerManifest:
    authority: str
    valid: bool
    schema_version: int
    projection_version: int
    event_count: int
    command_count: int
    first_sequence: int | None
    last_sequence: int | None
    chain_head: str
    verification_reason: str = ""


@dataclass(frozen=True, slots=True)
class LedgerVerification:
    valid: bool
    event_count: int
    chain_head: str
    reason: str = ""


def _canonical(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _event_payload(row: dict[str, object]) -> dict[str, object]:
    payload = {key: value for key, value in row.items() if key not in _LEDGER_FIELDS}
    for field_name in _OPTIONAL_EVENT_FIELDS:
        payload.setdefault(field_name, None)
    return payload


class JsonlLedger:
    """Append-only local evidence ledger with restrictive permissions and hash chaining."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(self, event: EvidenceEvent) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        descriptor = os.open(
            self.path,
            os.O_APPEND | os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
            0o600,
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            self._validate_descriptor(descriptor)
            rows = self._read_descriptor(descriptor)
            candidate = cast(dict[str, object], event.as_dict())
            candidate.pop("sequence", None)
            for existing in rows:
                if existing.get("event_id") != event.event_id:
                    continue
                if _event_payload(existing) == _event_payload(candidate):
                    return
                raise ValueError("event_id is already bound to a different payload")
            previous_hash = str(rows[-1].get("event_hash", "")) if rows else ""
            if rows:
                last_sequence = rows[-1].get("sequence")
                if isinstance(last_sequence, bool) or not isinstance(last_sequence, int):
                    raise ValueError("existing ledger requires migration before append")
                sequence = last_sequence + 1
            else:
                sequence = 1
            row: dict[str, object] = {
                **candidate,
                "schema_version": 1,
                "sequence": sequence,
                "prev_hash": previous_hash,
            }
            row["event_hash"] = hashlib.sha256(_canonical(row)).hexdigest()
            encoded = _canonical(row) + b"\n"
            written = 0
            while written < len(encoded):
                # A short write would append a truncated row. That row no longer
                # parses, so the whole ledger becomes unreadable and every later
                # verify reports ledger_unreadable.
                written += os.write(descriptor, encoded[written:])
            os.fsync(descriptor)
            # fchmod on the locked descriptor rather than chmod on the path
            # after release: the path could be swapped in between.
            os.fchmod(descriptor, 0o600)
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def read(self) -> tuple[dict[str, object], ...]:
        if not self.path.exists():
            return ()
        descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_SH)
            self._validate_descriptor(descriptor)
            return self._read_descriptor(descriptor)
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def manifest(self) -> LedgerManifest:
        try:
            rows = self.read()
        except (OSError, ValueError):
            rows = ()
            verification = LedgerVerification(False, 0, "", "ledger_unreadable")
        else:
            verification = self._verify_rows(rows)
        if not verification.valid:
            return LedgerManifest(
                authority="jsonl_ledger",
                valid=False,
                schema_version=1,
                projection_version=1,
                event_count=verification.event_count,
                command_count=0,
                first_sequence=None,
                last_sequence=None,
                chain_head=verification.chain_head,
                verification_reason=verification.reason,
            )
        commands = {str(row["command_id"]) for row in rows if row.get("command_id") is not None}
        return LedgerManifest(
            authority="jsonl_ledger",
            valid=True,
            schema_version=1,
            projection_version=1,
            event_count=len(rows),
            command_count=len(commands),
            first_sequence=(1 if rows else None),
            last_sequence=(len(rows) if rows else None),
            chain_head=verification.chain_head,
        )

    def require_appendable(self) -> None:
        result = self.verify()
        if not result.valid:
            raise ValueError(f"ledger is not appendable: {result.reason}")

    def verify(self) -> LedgerVerification:
        try:
            rows = self.read()
        except (OSError, ValueError):
            return LedgerVerification(False, 0, "", "ledger_unreadable")
        return self._verify_rows(rows)

    @staticmethod
    def _verify_rows(rows: tuple[dict[str, object], ...]) -> LedgerVerification:
        previous_hash = ""
        for expected_sequence, row in enumerate(rows, start=1):
            if row.get("schema_version") != 1:
                return LedgerVerification(False, len(rows), previous_hash, "schema_version_mismatch")
            if row.get("sequence") != expected_sequence:
                return LedgerVerification(False, len(rows), previous_hash, "sequence_mismatch")
            if row.get("prev_hash") != previous_hash:
                return LedgerVerification(False, len(rows), previous_hash, "previous_hash_mismatch")
            claimed_hash = row.get("event_hash")
            unhashed = {key: value for key, value in row.items() if key != "event_hash"}
            calculated_hash = hashlib.sha256(_canonical(unhashed)).hexdigest()
            if claimed_hash != calculated_hash:
                return LedgerVerification(False, len(rows), previous_hash, "event_hash_mismatch")
            previous_hash = calculated_hash
        return LedgerVerification(True, len(rows), previous_hash)

    @staticmethod
    def _validate_descriptor(descriptor: int) -> None:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or details.st_uid != os.geteuid():
            raise PermissionError("ledger must be an owner-controlled regular file")
        if stat.S_IMODE(details.st_mode) != 0o600:
            raise PermissionError("ledger must have mode 0600")
        if details.st_size > _MAX_LEDGER_BYTES:
            raise ValueError("ledger exceeded size limit")

    @staticmethod
    def _read_descriptor(descriptor: int) -> tuple[dict[str, object], ...]:
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        remaining = _MAX_LEDGER_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > _MAX_LEDGER_BYTES:
            raise ValueError("ledger exceeded size limit")
        rows: list[dict[str, object]] = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            payload: Any = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError("ledger row must be an object")
            rows.append(cast(dict[str, object], payload))
        return tuple(rows)
