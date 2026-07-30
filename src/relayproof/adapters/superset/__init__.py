from __future__ import annotations

import http.client
import ipaddress
import json
import os
import re
import socket
import stat
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from ...model import EvidenceEvent, Leg, LegState, Provenance

_MANIFEST_MAX_BYTES = 64 * 1024
_RESPONSE_MAX_BYTES = 1024 * 1024
_MAX_LINES = 1000
_KNOWN_MANIFEST_KEYS = {
    "endpoint",
    "bearer_token",
    "workspace_id",
    "terminal_id",
    "timeout_seconds",
}
_SEND_PHASES = {
    "injected",
    "duplicate_ignored",
    "duplicate_suspected",
    "rejected_revision_changed",
    "rejected_prompt_not_empty",
    "rejected_prompt_unreadable",
}
_PROMPT_STATUSES = {"empty", "has_text", "unknown"}
_RUNTIMES = {"codex", "claude", "kimi", "shell", "unknown"}
_AGENT_RUNTIMES = {"codex", "claude", "kimi"}


class TrpcError(RuntimeError):
    """A redacted transport or tRPC protocol failure."""


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _manifest_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Superset manifest contains a duplicate key")
        result[key] = value
    return result


def _load_manifest(path: str | Path) -> dict[str, Any]:
    manifest = Path(path)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(manifest, flags)
    except OSError as exc:
        raise ValueError("Superset manifest is not a safe readable regular file") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Superset manifest must be a regular file")
        if info.st_uid != os.geteuid():
            raise ValueError("Superset manifest must be owned by the current user")
        if info.st_size > _MANIFEST_MAX_BYTES:
            raise ValueError("Superset manifest exceeds 64 KiB")
        if info.st_mode & 0o077:
            raise ValueError("Superset manifest must have owner-only permissions (chmod 600)")
        chunks: list[bytes] = []
        remaining = _MANIFEST_MAX_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 8192))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > _MANIFEST_MAX_BYTES:
            raise ValueError("Superset manifest exceeds 64 KiB")
    finally:
        os.close(descriptor)
    try:
        decoded = raw.decode("utf-8")
        parsed = json.loads(decoded, object_pairs_hook=_manifest_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Superset manifest is not valid UTF-8 JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Superset manifest must contain a JSON object")
    payload = cast(dict[str, Any], parsed)
    unknown = set(payload) - _KNOWN_MANIFEST_KEYS
    if unknown:
        raise ValueError("Superset manifest contains unknown keys")
    return payload


def _validate_loopback_endpoint(endpoint: str) -> str:
    if any(ord(character) < 32 or character == "\\" for character in endpoint):
        raise ValueError("endpoint contains unsafe characters")
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("endpoint must be an absolute http(s) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("endpoint must not contain userinfo")
    if parsed.query or parsed.fragment:
        raise ValueError("endpoint must not contain query or fragment")
    if parsed.path.rstrip("/") != "/trpc":
        raise ValueError("endpoint path must be exactly /trpc")
    host = parsed.hostname
    if not host:
        raise ValueError("endpoint must include a host")
    try:
        parsed_port = parsed.port
    except ValueError as exc:
        raise ValueError("endpoint port is invalid") from exc
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if host.lower() != "localhost":
            raise ValueError("endpoint host must be a loopback address")
        try:
            resolutions = socket.getaddrinfo(host, parsed_port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise ValueError("localhost could not be resolved safely") from exc
        addresses = {ipaddress.ip_address(item[4][0]) for item in resolutions}
        if not addresses or any(not address.is_loopback for address in addresses):
            raise ValueError("localhost must resolve only to loopback addresses")
    else:
        if not address.is_loopback or getattr(address, "ipv4_mapped", None) is not None:
            raise ValueError("endpoint host must be a loopback address")
    host_text = f"[{host}]" if ":" in host else host
    port = parsed_port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return f"{parsed.scheme}://{host_text}:{port}/trpc"


@dataclass(frozen=True, slots=True, repr=False)
class SupersetConfig:
    """Connection details for one concrete loopback Superset terminal."""

    endpoint: str
    bearer_token: str
    workspace_id: str
    terminal_id: str
    timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        endpoint = _validate_loopback_endpoint(self.endpoint)
        for name in ("bearer_token", "workspace_id", "terminal_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value) > 4096:
                raise ValueError(f"{name} must be a bounded non-empty string")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or self.timeout_seconds <= 0
            or self.timeout_seconds > 60
        ):
            raise ValueError("timeout_seconds must be between 0 and 60 seconds")
        object.__setattr__(self, "endpoint", endpoint)
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))

    def __repr__(self) -> str:
        return (
            "SupersetConfig("
            f"endpoint={self.endpoint!r}, bearer_token='<redacted>', "
            f"workspace_id={self.workspace_id!r}, terminal_id={self.terminal_id!r}, "
            f"timeout_seconds={self.timeout_seconds!r})"
        )

    @classmethod
    def from_manifest(cls, path: str | Path) -> SupersetConfig:
        """Read one explicitly selected local manifest without exposing its contents."""
        payload = _load_manifest(path)
        timeout = payload.get("timeout_seconds", 5.0)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ValueError("timeout_seconds must be a number")
        return cls(
            endpoint=_required_string(payload, "endpoint"),
            bearer_token=_required_string(payload, "bearer_token"),
            workspace_id=_required_string(payload, "workspace_id"),
            terminal_id=_required_string(payload, "terminal_id"),
            timeout_seconds=float(timeout),
        )


@dataclass(frozen=True, slots=True)
class TerminalSnapshot:
    terminal_id: str
    text: str = field(repr=False)
    revision: int
    cols: int
    rows: int

    def __repr__(self) -> str:
        return (
            "TerminalSnapshot("
            f"terminal_id={self.terminal_id!r}, text='<redacted>', "
            f"revision={self.revision}, cols={self.cols}, rows={self.rows})"
        )

    def to_evidence(self, command_id: str) -> EvidenceEvent:
        return EvidenceEvent(
            event_id=str(uuid.uuid4()),
            command_id=command_id,
            leg=Leg.CAPTURE,
            state=LegState.SUCCEEDED,
            kind="terminal.snapshot",
            provenance=Provenance.API,
            evidence_ref=f"terminal:{self.terminal_id}:revision:{self.revision}",
        )


@dataclass(frozen=True, slots=True)
class DispatchResult:
    client_token: str
    terminal_id: str
    delivery_id: str | None
    phase: str
    submit_sent: bool
    duplicate: bool
    revision_before: int
    expected_revision: int
    prompt_status: str
    target_runtime: str
    revision_after: int | None = None
    dry_run: bool = False

    @property
    def prompt_verified(self) -> bool:
        return self.prompt_status == "empty" and self.target_runtime in _AGENT_RUNTIMES

    @property
    def dispatched(self) -> bool:
        return (
            not self.dry_run
            and self.phase == "injected"
            and self.submit_sent
            and not self.duplicate
            and self.revision_before == self.expected_revision
        )

    def to_evidence(self, command_id: str) -> EvidenceEvent:
        if self.dry_run:
            state = LegState.PENDING
            kind = "terminal.send.dry_run"
            reason = "confirmation_required"
        elif self.dispatched:
            state = LegState.SUCCEEDED
            kind = "terminal.send"
            reason = "" if self.prompt_verified else "prompt_not_verified"
        else:
            state = LegState.FAILED
            kind = "terminal.send.rejected"
            reason = self.phase
        evidence_ref = self.delivery_id or "delivery:unavailable"
        return EvidenceEvent(
            event_id=str(uuid.uuid4()),
            command_id=command_id,
            leg=Leg.DISPATCH,
            state=state,
            kind=kind,
            provenance=Provenance.API,
            reason=reason,
            evidence_ref=evidence_ref,
        )


@dataclass(frozen=True, slots=True)
class CanaryResult:
    observed: bool
    attempts: int
    last_revision: int
    evidence: EvidenceEvent


class _TrpcTransport:
    def __init__(self, config: SupersetConfig) -> None:
        self.config = config
        self._opener = build_opener(ProxyHandler({}), _RejectRedirects())

    def query(
        self,
        procedure: str,
        payload: dict[str, object],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        wire = json.dumps({"json": payload}, separators=(",", ":"))
        url = f"{self.config.endpoint}/{procedure}?{urlencode({'input': wire})}"
        return self._request(Request(url, headers=self._headers(), method="GET"), timeout)

    def mutation(self, procedure: str, payload: dict[str, object]) -> dict[str, Any]:
        url = f"{self.config.endpoint}/{procedure}"
        body = json.dumps({"json": payload}, separators=(",", ":")).encode("utf-8")
        headers = self._headers()
        headers["Content-Type"] = "application/json"
        return self._request(Request(url, data=body, headers=headers, method="POST"), None)

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.config.bearer_token}",
        }

    def _request(self, request: Request, timeout: float | None) -> dict[str, Any]:
        request_timeout = self.config.timeout_seconds if timeout is None else timeout
        try:
            with self._opener.open(request, timeout=request_timeout) as response:
                raw = response.read(_RESPONSE_MAX_BYTES + 1)
        except HTTPError as exc:
            if 300 <= exc.code < 400:
                raise TrpcError("Superset tRPC redirect rejected") from None
            raise TrpcError(f"Superset tRPC HTTP {exc.code}") from None
        except (TimeoutError, socket.timeout):
            raise TrpcError("Superset tRPC request timed out") from None
        except (URLError, OSError, http.client.HTTPException):
            raise TrpcError("Superset tRPC transport failed") from None
        if len(raw) > _RESPONSE_MAX_BYTES:
            raise TrpcError("Superset tRPC response exceeded size limit")
        try:
            wire = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise TrpcError("Superset tRPC returned invalid JSON") from None
        if not isinstance(wire, dict):
            raise TrpcError("Superset tRPC returned an invalid envelope")
        envelope = cast(dict[str, Any], wire)
        if "error" in envelope:
            raise TrpcError("Superset tRPC returned an error")
        result = envelope.get("result")
        if not isinstance(result, dict):
            raise TrpcError("Superset tRPC response omitted result")
        data = cast(dict[str, Any], result).get("data")
        if isinstance(data, dict) and "json" in data:
            data = cast(dict[str, Any], data)["json"]
        if not isinstance(data, dict):
            raise TrpcError("Superset tRPC result was not an object")
        return cast(dict[str, Any], data)


class SupersetAdapter:
    """Superset terminal tRPC adapter with fail-closed send semantics."""

    def __init__(self, config: SupersetConfig) -> None:
        self.config = config
        self._transport = _TrpcTransport(config)
        self._token_claims: dict[str, tuple[str, int]] = {}

    def snapshot(
        self,
        *,
        max_lines: int | None = None,
        request_timeout: float | None = None,
    ) -> TerminalSnapshot:
        payload: dict[str, object] = {
            "terminalId": self.config.terminal_id,
            "workspaceId": self.config.workspace_id,
        }
        if max_lines is not None:
            if isinstance(max_lines, bool) or not isinstance(max_lines, int) or not 0 < max_lines <= _MAX_LINES:
                raise ValueError("max_lines must be an integer between 1 and 1000")
            payload["maxLines"] = max_lines
        data = self._transport.query("terminal.snapshot", payload, timeout=request_timeout)
        terminal_id = _required_response_string(data, "terminalId")
        if terminal_id != self.config.terminal_id:
            raise TrpcError("Superset snapshot target mismatch")
        return TerminalSnapshot(
            terminal_id=terminal_id,
            text=_required_string_allow_empty(data, "text"),
            revision=_required_nonnegative_int(data, "revision"),
            cols=_required_positive_int(data, "cols"),
            rows=_required_positive_int(data, "rows"),
        )

    def dispatch(
        self,
        text: str,
        *,
        expected_revision: int,
        client_token: str | None = None,
        confirm: bool = False,
    ) -> DispatchResult:
        if not isinstance(text, str) or not text:
            raise ValueError("text must not be empty")
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 0
        ):
            raise ValueError("expected_revision must be a non-negative integer")
        token = client_token or str(uuid.uuid4())
        if not isinstance(token, str) or not token.strip() or len(token) > 200:
            raise ValueError("client_token must contain 1 to 200 characters")
        claim = (text, expected_revision)
        previous = self._token_claims.get(token)
        if previous is not None and previous != claim:
            raise ValueError("client_token is already bound to different dispatch content")
        self._token_claims[token] = claim
        if not confirm:
            return DispatchResult(
                token,
                self.config.terminal_id,
                None,
                "dry_run",
                False,
                False,
                expected_revision,
                expected_revision,
                "unknown",
                "unknown",
                dry_run=True,
            )
        payload: dict[str, object] = {
            "terminalId": self.config.terminal_id,
            "workspaceId": self.config.workspace_id,
            "text": text,
            "submit": True,
            "clientToken": token,
            "requireEmptyPrompt": True,
            "allowRepeat": False,
            "expectRevision": expected_revision,
        }
        # Deliberately one POST only: ambiguous transport failure is surfaced, never retried.
        data = self._transport.mutation("terminal.send", payload)
        terminal_id = _required_response_string(data, "terminalId")
        if terminal_id != self.config.terminal_id:
            raise TrpcError("Superset send target mismatch")
        phase = _required_enum(data, "phase", _SEND_PHASES)
        prompt_status = _required_enum(data, "promptStatus", _PROMPT_STATUSES)
        target = data.get("target")
        if not isinstance(target, dict):
            raise TrpcError("Superset response field target was invalid")
        runtime = _required_enum(cast(dict[str, Any], target), "runtime", _RUNTIMES)
        return DispatchResult(
            client_token=token,
            terminal_id=terminal_id,
            delivery_id=_optional_string(data, "deliveryId"),
            phase=phase,
            submit_sent=_required_bool(data, "submitSent"),
            duplicate=_required_bool(data, "duplicate"),
            revision_before=_required_nonnegative_int(data, "revisionBefore"),
            expected_revision=expected_revision,
            prompt_status=prompt_status,
            target_runtime=runtime,
            revision_after=_optional_nonnegative_int(data, "revisionAfter"),
        )

    def validate_canary(
        self,
        canary: str,
        *,
        baseline_text: str,
        submitted_text: str,
    ) -> None:
        _validate_canary(canary)
        if canary in submitted_text:
            raise ValueError("canary must not occur literally in submitted text")
        if _structured_canary_observed(baseline_text, canary):
            raise ValueError("canary was already present in baseline")

    def await_canary(
        self,
        canary: str,
        *,
        command_id: str,
        baseline_revision: int,
        baseline_text: str,
        submitted_text: str,
        timeout: float = 8.0,
        poll_interval: float = 0.3,
        max_attempts: int = 32,
        response_grace: float = 0.1,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> CanaryResult:
        if (
            isinstance(baseline_revision, bool)
            or not isinstance(baseline_revision, int)
            or baseline_revision < 0
        ):
            raise ValueError("baseline_revision must be a non-negative integer")
        if timeout <= 0 or poll_interval < 0 or response_grace < 0:
            raise ValueError("polling durations are invalid")
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        if not command_id.strip():
            raise ValueError("command_id must not be empty")
        self.validate_canary(
            canary,
            baseline_text=baseline_text,
            submitted_text=submitted_text,
        )
        started = clock()
        deadline = started + timeout
        attempts = 0
        last_revision = baseline_revision
        while attempts < max_attempts:
            now = clock()
            if now >= deadline:
                break
            remaining = deadline - now
            snapshot = self.snapshot(
                max_lines=_MAX_LINES,
                request_timeout=min(self.config.timeout_seconds, remaining + response_grace),
            )
            attempts += 1
            if snapshot.revision < baseline_revision:
                raise TrpcError("Superset terminal revision reset during canary polling")
            last_revision = snapshot.revision
            if snapshot.revision > baseline_revision and _structured_canary_observed(
                snapshot.text, canary
            ):
                evidence = EvidenceEvent(
                    event_id=str(uuid.uuid4()),
                    command_id=command_id,
                    leg=Leg.ACCEPT,
                    state=LegState.SUCCEEDED,
                    kind="canary.observed",
                    provenance=Provenance.TERMINAL_DIFF,
                    evidence_ref=f"terminal:{snapshot.terminal_id}:revision:{snapshot.revision}",
                )
                return CanaryResult(True, attempts, last_revision, evidence)
            now = clock()
            if now >= deadline:
                break
            sleeper(min(poll_interval, deadline - now))
        evidence = EvidenceEvent(
            event_id=str(uuid.uuid4()),
            command_id=command_id,
            leg=Leg.ACCEPT,
            state=LegState.FAILED,
            kind="canary.missed",
            provenance=Provenance.TERMINAL_DIFF,
            reason="canary_timeout",
            evidence_ref=f"terminal:{self.config.terminal_id}:revision:{last_revision}",
        )
        return CanaryResult(False, attempts, last_revision, evidence)


_ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _structured_canary_observed(terminal_text: str, canary: str) -> bool:
    scrubbed = _ANSI_ESCAPE.sub("", terminal_text)
    pattern = rf"(?<![A-Z0-9_]){re.escape(canary)}(?![A-Z0-9_])"
    return re.search(pattern, scrubbed) is not None


def _validate_canary(canary: str) -> None:
    if not isinstance(canary, str) or re.fullmatch(r"RELAYPROOF_ACK_[A-F0-9]{32}", canary) is None:
        raise ValueError("canary must be RELAYPROOF_ACK_ followed by 32 uppercase hex characters")


def _required_response_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise TrpcError(f"Superset response field {key} was not a non-empty string")
    return value


def _required_string_allow_empty(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise TrpcError(f"Superset response field {key} was not a string")
    return value


def _required_nonnegative_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TrpcError(f"Superset response field {key} was not a non-negative integer")
    return value


def _required_positive_int(payload: dict[str, Any], key: str) -> int:
    value = _required_nonnegative_int(payload, key)
    if value == 0:
        raise TrpcError(f"Superset response field {key} was not positive")
    return value


def _required_bool(payload: dict[str, Any], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise TrpcError(f"Superset response field {key} was not a boolean")
    return value


def _required_enum(payload: dict[str, Any], key: str, allowed: set[str]) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or value not in allowed:
        raise TrpcError(f"Superset response field {key} was invalid")
    return value


def _optional_string(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TrpcError(f"Superset response field {key} was not a string or null")
    return value


def _optional_nonnegative_int(payload: dict[str, Any], key: str) -> int | None:
    if key not in payload:
        return None
    return _required_nonnegative_int(payload, key)


__all__ = [
    "CanaryResult",
    "DispatchResult",
    "SupersetAdapter",
    "SupersetConfig",
    "TerminalSnapshot",
    "TrpcError",
]
