from __future__ import annotations

import hashlib
import json
import os
import stat
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
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            existing = self._read(path)
            if existing != payload:
                raise ValueError("client_token is already bound to a different claim") from None
            return
        try:
            os.write(fd, encoded)
            os.fsync(fd)
        finally:
            os.close(fd)

    def consume(
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
        expires_at = payload.get("expires_at")
        if not isinstance(expires_at, str) or datetime.fromisoformat(expires_at) <= datetime.now(timezone.utc):
            raise ValueError("confirmation claim has expired")
        consumed = path.with_suffix(".consumed")
        try:
            os.replace(path, consumed)
        except FileNotFoundError:
            raise ValueError("confirmation claim is not available") from None
        command_id = payload.get("command_id")
        if not isinstance(command_id, str) or not command_id:
            raise ValueError("confirmation claim is invalid")
        return command_id

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
