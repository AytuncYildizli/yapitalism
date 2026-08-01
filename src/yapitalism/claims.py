from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

_MAX_CLAIM_BYTES = 16 * 1024
_CLAIM_TTL = timedelta(minutes=15)


def _payload_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _token_key(client_token: str) -> str:
    if not client_token.strip() or len(client_token) > 200:
        raise ValueError("client_token must contain 1 to 200 characters")
    return hashlib.sha256(client_token.encode("utf-8")).hexdigest()


def _claim_expired(payload: dict[str, Any]) -> bool:
    """True when a claim can no longer authorize a send.

    Anything unparseable counts as expired rather than raising: a naive
    timestamp compared against an aware `now()` would otherwise raise TypeError
    instead of the domain error, and an unusable claim must never read as live.
    """
    expires_at = payload.get("expires_at")
    if not isinstance(expires_at, str):
        return True
    try:
        deadline = datetime.fromisoformat(expires_at)
    except ValueError:
        return True
    if deadline.tzinfo is None:
        return True
    return deadline <= datetime.now(timezone.utc)


def _fsync_dir(directory: Path) -> None:
    """Persist a directory entry so a rename or create survives power loss."""
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@dataclass(frozen=True, slots=True)
class ConfirmationClaimStore:
    root: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root))

    def issue(
        self,
        *,
        client_token: str,
        command_id: str,
        text: str,
        expected_revision: int,
        terminal_id: str,
    ) -> None:
        with self._exclusive():
            self._issue_locked(
                client_token=client_token,
                command_id=command_id,
                text=text,
                expected_revision=expected_revision,
                terminal_id=terminal_id,
            )

    def consume(
        self,
        *,
        client_token: str,
        text: str,
        expected_revision: int,
        terminal_id: str,
    ) -> str:
        with self._exclusive():
            return self._consume_locked(
                client_token=client_token,
                text=text,
                expected_revision=expected_revision,
                terminal_id=terminal_id,
            )

    def _issue_locked(
        self,
        *,
        client_token: str,
        command_id: str,
        text: str,
        expected_revision: int,
        terminal_id: str,
    ) -> None:
        if not command_id.strip() or not terminal_id.strip():
            raise ValueError("command_id and terminal_id are required")
        if expected_revision < 0:
            raise ValueError("expected_revision must be non-negative")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        now = datetime.now(timezone.utc)
        payload = {
            "schema_version": 1,
            "command_id": command_id,
            "token_hash": _token_key(client_token),
            "payload_hash": _payload_hash(text),
            "expected_revision": expected_revision,
            "terminal_id": terminal_id,
            "created_at": now.isoformat(timespec="seconds"),
            "expires_at": (now + _CLAIM_TTL).isoformat(timespec="seconds"),
        }
        encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        path = self._path(client_token)
        consumed = path.with_suffix(".consumed")
        if consumed.exists():
            raise ValueError("client_token confirmation claim was already consumed")
        try:
            self._create(path, encoded)
        except FileExistsError:
            # An unreadable claim is NOT replaced. Replacing it would let a
            # truncation rebind a single-use token: the binding it asserted is
            # exactly what became unverifiable, so overwriting it silently
            # authorizes a different dispatch under the same token. Wedging the
            # token behind an actionable error is the safe direction.
            try:
                existing = self._read(path)
            except ValueError:
                raise ValueError(
                    "confirmation claim is unreadable; remove it to reissue this client_token"
                ) from None
            binding_keys = (
                "command_id",
                "token_hash",
                "payload_hash",
                "expected_revision",
                "terminal_id",
            )
            if any(existing.get(key) != payload[key] for key in binding_keys):
                raise ValueError("client_token is already bound to a different claim") from None
            if not _claim_expired(existing):
                return
            # Expired with a binding proven identical to the one being issued.
            # A stale claim is not a conflicting claim, and returning here would
            # report success while leaving a claim `consume` always rejects.
            os.unlink(path)
            self._create(path, encoded)

    @staticmethod
    def _create(path: Path, encoded: bytes) -> None:
        """Create the claim exclusively, or raise FileExistsError.

        A partially written claim is never published: any failure removes the
        file, so the next issue sees no claim rather than an unusable one.
        """
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            written = 0
            while written < len(encoded):
                # os.write may write fewer bytes than requested.
                written += os.write(fd, encoded[written:])
            os.fsync(fd)
        except BaseException:
            os.close(fd)
            os.unlink(path)
            raise
        os.close(fd)
        _fsync_dir(path.parent)

    def _consume_locked(
        self,
        *,
        client_token: str,
        text: str,
        expected_revision: int,
        terminal_id: str,
    ) -> str:
        path = self._path(client_token)
        try:
            payload = self._read(path)
        except FileNotFoundError:
            raise ValueError("confirmation claim is not available") from None
        expected = {
            "token_hash": _token_key(client_token),
            "payload_hash": _payload_hash(text),
            "expected_revision": expected_revision,
            "terminal_id": terminal_id,
        }
        if any(payload.get(key) != value for key, value in expected.items()):
            raise ValueError("confirmation claim does not match the requested dispatch")
        if _claim_expired(payload):
            raise ValueError("confirmation claim has expired")
        consumed = path.with_suffix(".consumed")
        try:
            os.replace(path, consumed)
        except FileNotFoundError:
            raise ValueError("confirmation claim is not available") from None
        # Persist the rename before the caller dispatches. Without this a power
        # loss can restore the live pathname, and the send becomes replayable —
        # the exact single-use failure this store exists to prevent.
        _fsync_dir(self.root)
        command_id = payload.get("command_id")
        if not isinstance(command_id, str) or not command_id:
            raise ValueError("confirmation claim is invalid")
        return command_id

    @contextmanager
    def _exclusive(self) -> Iterator[None]:
        """Serialize issue and consume against each other.

        Both are check-then-act over the same two pathnames, so without a lock
        an `issue` that passed the consumed-marker check can recreate a live
        claim that a concurrent `consume` already spent, and two consumers can
        each dispatch one token. The lock file is separate from the claims so
        it is never mistaken for one.
        """
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        descriptor = os.open(self.root / ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def _path(self, client_token: str) -> Path:
        return self.root / f"{_token_key(client_token)}.json"

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        flags = os.O_RDONLY | os.O_NOFOLLOW
        fd = os.open(path, flags)
        try:
            details = os.fstat(fd)
            if not stat.S_ISREG(details.st_mode) or details.st_uid != os.getuid():
                raise PermissionError("confirmation claim must be an owner-controlled regular file")
            if stat.S_IMODE(details.st_mode) != 0o600:
                raise PermissionError("confirmation claim must have mode 0600")
            raw = os.read(fd, _MAX_CLAIM_BYTES + 1)
        finally:
            os.close(fd)
        if len(raw) > _MAX_CLAIM_BYTES:
            raise ValueError("confirmation claim exceeded size limit")
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("confirmation claim is invalid") from None
        if not isinstance(payload, dict):
            raise ValueError("confirmation claim is invalid")
        typed = cast(dict[str, Any], payload)
        if typed.get("schema_version") != 1:
            raise ValueError("confirmation claim is invalid")
        return typed
