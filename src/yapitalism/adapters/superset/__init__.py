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
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from ...canary import strip_terminal_decoration
from ...prompt_state import EMPTY, HAS_TEXT, detect_prompt_state
from ...tokens import BoundedTokens
from ...model import EvidenceEvent, Leg, LegState, Provenance

_MANIFEST_MAX_BYTES = 64 * 1024
_RESPONSE_MAX_BYTES = 1024 * 1024
_DISPATCH_MAX_BYTES = 64 * 1024
_MAX_LINES = 1000
_KNOWN_MANIFEST_KEYS = {
    "endpoint",
    "bearer_token",
    "workspace_id",
    "terminal_id",
    "timeout_seconds",
}
# Phases a HOST response may carry. `staged_not_submitted` and
# `duplicate_after_ambiguous_write` are deliberately absent: no host reports them —
# they are produced only by the client-guarded path, which constructs its own
# result rather than parsing one.
_SEND_PHASES = {
    "injected",
    "duplicate_ignored",
    "duplicate_suspected",
    "rejected_revision_changed",
    "rejected_prompt_not_empty",
    "rejected_prompt_unreadable",
}
_PROMPT_STATUSES = {"empty", "has_text", "unknown"}
# The complete set of byte sequences this adapter will ever write to a terminal.
#
# `terminal.writeInput` takes `data: z.string()` — an arbitrary string, with no
# expectRevision, no clientToken and no requireEmptyPrompt. Every guard that
# makes `terminal.send` trustworthy is absent from it. So unlike `send`, where
# the host refuses a bad write, the only thing standing between this procedure
# and arbitrary typing is this table.
#
# Keys match tmux's CLEAR_ACTIONS deliberately: the operator says the same word
# regardless of which backend owns the pane.
#
# Enter is absent and must stay absent, for the same reason as in tmux — Escape
# cancels, Enter commits, and on a menu Enter picks whatever is highlighted.
_CLEAR_SEQUENCES: dict[str, str] = {
    "escape": "\x1b",
    "clear-line": "\x15",  # C-u, readline "kill line"
    "escape-twice": "\x1b\x1b",
}
_RUNTIMES = {"codex", "claude", "kimi", "shell", "unknown"}
_AGENT_RUNTIMES = {"codex", "claude", "kimi"}


class TrpcError(RuntimeError):
    """A redacted transport or tRPC protocol failure."""


class TrpcProcedureMissing(TrpcError):
    """The host does not route this procedure at all — tRPC answered 404.

    A subclass rather than a sibling, deliberately: every existing
    `except TrpcError` keeps catching it, so a 404 on any other procedure still
    fails closed exactly as before. Only the send path looks for the narrower
    type, because there a 404 says something about the host's BUILD rather than
    about this request — and nothing was written, which is what makes falling
    back to another path safe.
    """


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
    #: Whether `revision` is the HOST's counter or one derived from the screen.
    #: A derived value answers "did this change" and nothing else — it does not
    #: increase, so anything that treats revisions as ordered has to check this
    #: first. The shipped Superset sends no counter, so this is the normal case.
    revision_is_derived: bool = False

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
            state=LegState.PENDING,
            kind="terminal.snapshot",
            provenance=Provenance.API,
            reason="context_only",
            evidence_ref=f"terminal:{self.terminal_id}:revision:{self.revision}",
            source_id="adapter:superset",
            target_id=f"terminal:{self.terminal_id}",
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
            source_id="adapter:superset",
            target_id=f"terminal:{self.terminal_id}",
            delivery_id=self.delivery_id,
        )


@dataclass(frozen=True, slots=True)
class ClearResult:
    """What one unstick attempt actually did.

    `prompt_empty` is deliberately always None. The host's prompt detector runs
    inside `terminal.send`; `terminal.snapshot` returns only text, revision and
    dimensions, so nothing here can establish emptiness without reimplementing
    that detector against a screen dump — which would be guessing dressed as a
    guarantee. The proof is the next `send` with requireEmptyPrompt not being
    refused, and that response carries the host's own `promptStatus`.
    """

    terminal_id: str
    action: str
    revision_before: int
    revision_after: int
    text_changed: bool
    prompt_empty: None = None


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
    ) -> Any:
        wire = json.dumps({"json": payload}, separators=(",", ":"))
        url = f"{self.config.endpoint}/{procedure}?{urlencode({'input': wire})}"
        return self._request(Request(url, headers=self._headers(), method="GET"), timeout)

    def mutation(self, procedure: str, payload: dict[str, object]) -> dict[str, Any]:
        url = f"{self.config.endpoint}/{procedure}"
        body = json.dumps({"json": payload}, separators=(",", ":")).encode("utf-8")
        headers = self._headers()
        headers["Content-Type"] = "application/json"
        return self._request(Request(url, data=body, headers=headers, method="POST"), None)

    def required_input_fields(self, procedure: str) -> set[str]:
        """Which top-level input fields this procedure REQUIRES, per its own validator.

        Sends an empty input so the host's Zod schema reports every missing field
        at once, then reads the `path` of each issue. An empty set means either the
        procedure takes no required fields or the question could not be answered —
        both of which must read as "no guarantees", so callers test for the
        presence of what they need rather than the absence of what they do not.

        This exists because `procedure_exists` was being used to conclude that a
        host enforced three guarantees. It only ever proved a name was routed.
        """
        url = f"{self.config.endpoint}/{procedure}"
        body = json.dumps({"json": {}}, separators=(",", ":")).encode("utf-8")
        headers = self._headers()
        headers["Content-Type"] = "application/json"
        request = Request(url, data=body, headers=headers, method="POST")
        try:
            with self._opener.open(request, timeout=self.config.timeout_seconds):
                return set()  # accepted an empty input: it requires nothing
        except HTTPError as exc:
            if exc.code != 400:
                return set()
            try:
                payload = json.loads(exc.read(_RESPONSE_MAX_BYTES).decode("utf-8"))
                issues = json.loads(payload["error"]["json"]["message"])
            except (ValueError, KeyError, TypeError, UnicodeDecodeError):
                return set()
            fields: set[str] = set()
            for issue in issues if isinstance(issues, list) else []:
                path = issue.get("path") if isinstance(issue, dict) else None
                if isinstance(path, list) and path and isinstance(path[0], str):
                    fields.add(path[0])
            return fields
        except (TimeoutError, socket.timeout, URLError, OSError, http.client.HTTPException):
            return set()

    def procedure_exists(self, procedure: str) -> bool:
        """Whether the host routes this procedure at all.

        Deliberately sends a payload that cannot validate, so the question is
        answered by tRPC's router without the procedure ever running. tRPC routes
        before it authenticates and before it validates input, so 404 means
        absent while 400 and 401 both mean present — reading only "did it fail"
        would call every procedure missing.

        Any other failure returns False rather than guessing present: a host that
        cannot be reached must not have capabilities assumed for it.
        """
        url = f"{self.config.endpoint}/{procedure}"
        body = json.dumps({"json": {}}, separators=(",", ":")).encode("utf-8")
        headers = self._headers()
        headers["Content-Type"] = "application/json"
        request = Request(url, data=body, headers=headers, method="POST")
        try:
            with self._opener.open(request, timeout=self.config.timeout_seconds):
                return True
        except HTTPError as exc:
            return exc.code != 404
        except (TimeoutError, socket.timeout, URLError, OSError, http.client.HTTPException):
            return False

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
            if exc.code == 404:
                # tRPC routes before it authenticates and before it validates, so
                # a 404 means the procedure is absent from this build - not that
                # the call was malformed or unauthorized.
                raise TrpcProcedureMissing("Superset tRPC procedure not found") from None
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
        if not isinstance(data, (dict, list)):
            raise TrpcError("Superset tRPC result was not an object or array")
        return cast(Any, data)


class SupersetAdapter:
    """Superset terminal tRPC adapter with fail-closed send semantics."""

    def __init__(self, config: SupersetConfig) -> None:
        self.config = config
        self._transport = _TrpcTransport(config)
        self._token_claims: dict[str, tuple[str, int]] = {}
        #: None until the host has been asked whether it has the guarded send.
        self._host_guards: bool | None = None
        #: Tokens whose write reached a host that does not deduplicate for us.
        #: Bounded: a launchd-managed server runs for weeks, and an unbounded set
        #: would retain every token ever sent.
        self._landed_tokens = BoundedTokens()
        #: Tokens burned by a write that has not been confirmed to complete.
        self._ambiguous_tokens: set[str] = set()

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
        text = _required_string_allow_empty(data, "text")
        revision, derived = _revision_of(data, text)
        return TerminalSnapshot(
            terminal_id=terminal_id,
            text=text,
            revision=revision,
            cols=_required_positive_int(data, "cols"),
            rows=_required_positive_int(data, "rows"),
            revision_is_derived=derived,
        )

    def list_workspaces(self) -> list[dict[str, Any]]:
        """Every workspace on this host.

        Uses only the endpoint and token from the manifest; the terminal it
        binds is irrelevant here, which is what makes enumeration possible from
        a single-terminal manifest.
        """
        data = self._transport.query("workspace.list", {})
        if not isinstance(data, list):
            raise TrpcError("Superset workspace.list did not return an array")
        return [row for row in cast(list[Any], data) if isinstance(row, dict)]

    def list_terminals(self, workspace_id: str) -> list[dict[str, Any]]:
        """Terminal sessions in one workspace, each with its agent where there is one.

        Two procedures, because the host keeps two things:

          terminal.list                    -> the live PTYs
          terminalAgents.listByWorkspace   -> which agent is bound to which PTY

        This called `terminal.listSessions`, which the host answers 404 for. That
        name does exist, on the **daemon** router, and it lists daemon sessions
        rather than terminals. Nothing failed loudly: the 404 became a TrpcError,
        `list_panes` swallowed it per workspace, and a host we could not enumerate
        at all reported as a host with zero terminals — while `registry_runtime`
        returned "unknown" for everything, so the agent gate refused every send.

        Measured against the shipped host on 2026-08-16, both names read out of the
        app's own bundle and its Zod errors rather than guessed.
        """
        if not workspace_id.strip():
            raise ValueError("workspace_id must not be empty")
        data = self._transport.query("terminal.list", {"workspaceId": workspace_id})
        if not isinstance(data, dict):
            raise TrpcError("Superset terminal.list did not return an object")
        sessions = cast(dict[str, Any], data).get("sessions")
        if not isinstance(sessions, list):
            # The wrapper object is load-bearing: treating a missing `sessions`
            # as "no terminals" would report an empty workspace for a broken read.
            raise TrpcError("Superset terminal.list omitted sessions")
        rows = [row for row in cast(list[Any], sessions) if isinstance(row, dict)]

        # A workspace with no agent bindings is normal, and a terminal with no
        # binding is a plain shell — so a failure to read the bindings must NOT
        # look like that. It propagates, and the caller decides.
        bindings = self.list_agent_bindings(workspace_id)
        by_terminal = {
            binding.get("terminalId"): binding
            for binding in bindings
            if isinstance(binding.get("terminalId"), str)
        }
        for row in rows:
            binding = by_terminal.get(row.get("terminalId"))
            if binding is not None:
                row["agent"] = binding
        return rows

    def list_agent_bindings(self, workspace_id: str) -> list[dict[str, Any]]:
        """Which agent the host has bound to each terminal in one workspace.

        `agentId` is the host's own answer, from `terminalAgentStore` — not a
        process scan, so unlike tmux it can be trusted before a write.
        """
        if not workspace_id.strip():
            raise ValueError("workspace_id must not be empty")
        data = self._transport.query(
            "terminalAgents.listByWorkspace", {"workspaceId": workspace_id}
        )
        if not isinstance(data, list):
            raise TrpcError(
                "Superset terminalAgents.listByWorkspace did not return a list"
            )
        return [row for row in cast(list[Any], data) if isinstance(row, dict)]

    def clear_prompt(self, action: str = "escape") -> ClearResult:
        """Write one fixed control sequence to unstick a blocked prompt.

        Uses `terminal.writeInput`, which the host exposes unguarded: no
        expectRevision, no clientToken, no requireEmptyPrompt. That asymmetry
        with `dispatch` is the whole reason `action` indexes a closed table
        instead of naming bytes — a caller cannot reach `data` at all.

        The absent revision guard is a real limitation, not a rounding error: a
        change landing between the baseline read and the write is undetectable
        here, where `dispatch` would have been refused by the host. Both
        revisions are reported so the caller can at least see that something
        moved, and callers must treat this as a step rather than a result.
        """
        sequence = _CLEAR_SEQUENCES.get(action)
        if sequence is None:
            known = ", ".join(sorted(_CLEAR_SEQUENCES))
            raise ValueError(f"unknown clear action {action!r}; known: {known}")
        before = self.snapshot(max_lines=_MAX_LINES)
        self._transport.mutation(
            "terminal.writeInput",
            {
                "terminalId": self.config.terminal_id,
                "workspaceId": self.config.workspace_id,
                "data": sequence,
            },
        )
        # A TUI redraws asynchronously; reading immediately would compare against
        # a screen that has not repainted yet and report no change.
        time.sleep(0.4)
        after = self.snapshot(max_lines=_MAX_LINES)
        return ClearResult(
            terminal_id=self.config.terminal_id,
            action=action,
            revision_before=before.revision,
            revision_after=after.revision,
            text_changed=after.text != before.text,
        )

    def dispatch(
        self,
        text: str,
        *,
        expected_revision: int,
        client_token: str | None = None,
        confirm: bool = False,
        require_empty_prompt: bool = True,
    ) -> DispatchResult:
        """Send through the host's guarded path.

        `require_empty_prompt` defaults True and should stay True. It exists as a
        parameter because the host defaults it OFF and this client turns it on — so
        the one measured case where the host's prompt detector is wrong is a dial
        here, not a wall. Turning it down keeps `expectRevision` and `clientToken`,
        which is the whole reason it is preferable to routing around the host with
        `writeInput`: that would discard revision atomicity, token dedup, the host
        delivery id, bracketed-paste framing and multi-line capability to work
        around a detector.

        The caller is responsible for having earned the right to turn it down. See
        `SupersetBackend.send`.
        """
        if not isinstance(text, str) or not text:
            raise ValueError("text must not be empty")
        try:
            text_bytes = len(text.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise ValueError("text must be valid UTF-8") from exc
        if text_bytes > _DISPATCH_MAX_BYTES:
            raise ValueError("text must not exceed 64 KiB when encoded as UTF-8")
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 0
        ):
            raise ValueError("expected_revision must be a non-negative integer")
        if confirm and client_token is None:
            raise ValueError("confirmed dispatch requires an explicit stable client_token")
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
        # Known-unguarded hosts skip straight to the fallback. Unknown ones try the
        # guarded send first and learn from the answer, so a guarded host pays no
        # probe round trip at all - the detection is a byproduct of real work.
        if self._host_guards is False:
            return self._dispatch_client_guarded(
                text, expected_revision=expected_revision, token=token
            )
        payload: dict[str, object] = {
            "terminalId": self.config.terminal_id,
            "workspaceId": self.config.workspace_id,
            "text": text,
            "submit": True,
            "clientToken": token,
            "requireEmptyPrompt": require_empty_prompt,
            "allowRepeat": False,
            "expectRevision": expected_revision,
        }
        # Deliberately one POST only: ambiguous transport failure is surfaced, never retried.
        try:
            data = self._transport.mutation("terminal.send", payload)
        except TrpcProcedureMissing:
            # A stock host. Nothing was written - tRPC rejected the route before
            # reaching any handler - so continuing on the other path is safe, and
            # this is NOT the retry the comment above forbids.
            self._host_guards = False
            return self._dispatch_client_guarded(
                text, expected_revision=expected_revision, token=token
            )
        self._host_guards = True
        terminal_id = _required_response_string(data, "terminalId")
        if terminal_id != self.config.terminal_id:
            raise TrpcError("Superset send target mismatch")
        phase = _required_enum(data, "phase", _SEND_PHASES)
        prompt_status = _required_enum(data, "promptStatus", _PROMPT_STATUSES)
        target = data.get("target")
        if not isinstance(target, dict):
            raise TrpcError("Superset response field target was invalid")
        runtime = _required_enum(cast(dict[str, Any], target), "runtime", _RUNTIMES)
        delivery_id = _optional_string(data, "deliveryId")
        if delivery_id is not None and (not delivery_id.strip() or len(delivery_id) > 512):
            raise TrpcError("Superset response field deliveryId was not a bounded non-empty string")
        submit_sent = _required_bool(data, "submitSent")
        duplicate = _required_bool(data, "duplicate")
        revision_before = _required_nonnegative_int(data, "revisionBefore")
        revision_after = _optional_nonnegative_int(data, "revisionAfter")
        if phase == "injected":
            if (
                delivery_id is None
                or not _is_uuid(delivery_id)
                or not submit_sent
                or duplicate
                or revision_after is None
                or revision_after < revision_before
                or revision_before != expected_revision
            ):
                raise TrpcError("Superset injected response was contradictory")
        elif submit_sent:
            raise TrpcError("Superset rejected response was contradictory")
        elif phase.startswith("duplicate_") != duplicate:
            raise TrpcError("Superset duplicate response was contradictory")
        return DispatchResult(
            client_token=token,
            terminal_id=terminal_id,
            delivery_id=delivery_id,
            phase=phase,
            submit_sent=submit_sent,
            duplicate=duplicate,
            revision_before=revision_before,
            expected_revision=expected_revision,
            prompt_status=prompt_status,
            target_runtime=runtime,
            revision_after=revision_after,
        )

    def host_enforces_send_guards(self) -> bool:
        """Whether this host's `terminal.send` enforces the guards, asked once.

        This asked `procedure_exists("terminal.send")`, and a routing name is not
        a guarantee. The shipped Superset build HAS a `terminal.send` — it takes
        `{terminalId, workspaceId, text, submit}` and frames multi-line text as a
        bracketed paste. Nothing about it is guarded, and the words
        `expectRevision`, `clientToken` and `requireEmptyPrompt` do not occur
        anywhere in its bundle. So every install reported host/host/host on the
        strength of a same-named convenience procedure.

        Now the host's own validator answers. An empty input makes Zod enumerate
        the fields it requires; a guarded build demands the guards, and this one
        names only `terminalId`, `workspaceId` and `text`. Asking what a procedure
        REQUIRES is the difference between a name and a contract.

        Cached for the process: the answer is a property of the build, and
        re-probing per send would add a round trip to every dispatch.
        """
        if self._host_guards is None:
            required = self._transport.required_input_fields("terminal.send")
            self._host_guards = {"expectRevision", "clientToken"} <= required
        return self._host_guards

    def _dispatch_client_guarded(
        self, text: str, *, expected_revision: int, token: str
    ) -> DispatchResult:
        """Submit through `writeInput`, enforcing what this side can enforce.

        This is the path for a host without the guarded send. Two of the three
        guarantees survive here in a weaker but real form, and the third does
        not survive at all:

        - optimistic revision: the terminal is re-read and the write is refused
          if it moved. Not atomic — a change landing between this read and the
          write is invisible — but it does refuse a stale send.
        - idempotent dispatch: a token already used for a landed write is
          refused. Fully effective, because the repeats this protects against
          are this process's own.
        - empty prompt: not enforced. `snapshot` returns a screen, and deciding
          emptiness from it means reimplementing the host's detector by eye. The
          capability is reported NONE rather than approximated.

        Nothing is withheld for lacking the fork: the send happens, the canary
        is still checked, and the receipt says which guards were client-side.
        """
        if "\n" in text or "\r" in text:
            # The host's `send` takes text and decides when to submit. writeInput
            # is raw bytes, so an embedded newline IS a submit: multi-line text
            # would be delivered as several separate instructions, the first
            # arriving alone. Refusing is the only honest option here.
            raise ValueError(
                "text must be a single line on a host without terminal.send; "
                "an embedded newline would submit early and split the message"
            )
        runtime = self._runtime_from_registry()
        before = self.snapshot(max_lines=_MAX_LINES)
        # Writing blind into an occupied prompt does not replace the staged text,
        # it concatenates with it and submits the merge — a half-typed thought and
        # a voice instruction arriving as one corrupted message. That is the exact
        # case the guarded host refuses, and reporting empty_prompt_check as
        # unenforced while doing it anyway would be an honest label on a worse
        # behaviour. So this refuses too, on anything short of a confident EMPTY.
        prompt_state = detect_prompt_state(before.text, runtime)
        if prompt_state != EMPTY:
            return DispatchResult(
                token,
                self.config.terminal_id,
                None,
                # The same two phases the guarded host uses, so the receipt says
                # the same sentence and `pane_clear` is the same way out.
                "rejected_prompt_not_empty"
                if prompt_state == HAS_TEXT
                else "rejected_prompt_unreadable",
                False,
                False,
                before.revision,
                expected_revision,
                prompt_state,
                runtime,
                revision_after=before.revision,
            )
        # Token first, revision second, and the order carries meaning. A replay of
        # a token that already landed is a duplicate whether or not the terminal
        # moved since — and it usually HAS moved, because the agent started
        # working on the message. Checking the revision first would report
        # `rejected_revision_changed` for a message that was delivered, which an
        # operator reads as "it never arrived" and answers by sending again.
        if token in self._landed_tokens:
            return DispatchResult(
                token,
                self.config.terminal_id,
                None,
                # Two different situations reach here. A token whose write is known
                # to have landed is a genuine duplicate. A token burned by a write
                # that failed ambiguously is NOT — nothing may have arrived — and
                # reporting it as a duplicate would claim a delivery that never
                # happened, which is the exact overclaim this project exists to
                # prevent.
                "duplicate_ignored"
                if token not in self._ambiguous_tokens
                else "duplicate_after_ambiguous_write",
                False,
                True,
                before.revision,
                expected_revision,
                "unknown",
                runtime,
                revision_after=before.revision,
            )
        if before.revision != expected_revision:
            return DispatchResult(
                token,
                self.config.terminal_id,
                None,
                "rejected_revision_changed",
                False,
                False,
                before.revision,
                expected_revision,
                "unknown",
                runtime,
                revision_after=before.revision,
            )
        base = {
            "terminalId": self.config.terminal_id,
            "workspaceId": self.config.workspace_id,
        }
        # Burned before the write, not after. If the call fails ambiguously the
        # bytes may already be in the terminal, so a retry under the same token
        # must still be refused - but it is recorded as ambiguous until the write
        # is known to have completed, so the refusal can say which case it is.
        self._landed_tokens.add(token)
        self._ambiguous_tokens.add(token)
        self._transport.mutation("terminal.writeInput", {**base, "data": text})
        self._ambiguous_tokens.discard(token)
        after, submitted = self._submit_and_verify(base, runtime)
        return DispatchResult(
            client_token=token,
            terminal_id=self.config.terminal_id,
            # No host delivery id exists on this path. Inventing one would put a
            # fabricated evidence reference in a receipt.
            delivery_id=None,
            # The text is in the terminal either way; whether it was SENT is a
            # separate fact, and the first version of this asserted the second
            # from the first.
            phase="injected" if submitted else "staged_not_submitted",
            submit_sent=submitted,
            duplicate=False,
            revision_before=before.revision,
            expected_revision=expected_revision,
            # This side judged it EMPTY before writing — weaker than the host's
            # atomic check, but not nothing, so saying "unknown" would now
            # understate what was actually verified.
            prompt_status=EMPTY,
            target_runtime=runtime,
            revision_after=after.revision,
        )

    #: How long to let the composer settle before sending the submit, and how long
    #: to wait for the redraw afterwards. Measured, not guessed: writing the text
    #: and the carriage return back to back left the text STAGED in a live Codex
    #: pane, because a TUI composer reads immediately-following input as a
    #: multi-line paste and keeps the newline as a literal line break. A lone
    #: carriage return sent afterwards submitted the same text immediately.
    _SETTLE_SECONDS = 0.4
    _REDRAW_SECONDS = 0.7

    def _submit_and_verify(
        self, base: dict[str, object], runtime: str
    ) -> tuple[TerminalSnapshot, bool]:
        """Press Enter, then check whether the prompt actually emptied.

        Returns the snapshot taken after the attempt and whether the text left the
        prompt. Verified rather than assumed: `writeInput` reports success for
        having delivered bytes, which says nothing about the composer having
        submitted them, and reporting `submit_sent=True` on that basis is how a
        message ends up staged forever while the receipt says it was sent.

        Retried once, because the failure observed live was a timing artefact
        rather than a refusal — and a second Enter is safe here in a way it is not
        in `pane_clear`: this path has already established the prompt was EMPTY
        before writing, so there is no menu underneath for a stray Enter to pick.
        """
        for attempt in range(2):
            time.sleep(self._SETTLE_SECONDS * (attempt + 1))
            self._transport.mutation("terminal.writeInput", {**base, "data": "\r"})
            time.sleep(self._REDRAW_SECONDS)
            snapshot = self.snapshot(max_lines=_MAX_LINES)
            # `== EMPTY`, not `!= HAS_TEXT`. UNKNOWN means the screen could not be
            # read, and before the write UNKNOWN refuses — so treating it as proof
            # of submission afterwards would be optimistic in the one direction
            # that matters: a message still sitting in the prompt while the pane
            # draws something unrecognisable would be reported as delivered.
            if detect_prompt_state(snapshot.text, runtime) == EMPTY:
                return snapshot, True
        return snapshot, False

    def registry_runtime(self) -> str:
        """Public alias: the host's own runtime for the bound terminal.

        Callers outside this module need it to refuse a write BEFORE it happens,
        which is why it stopped being private.
        """
        return self._runtime_from_registry()

    def _runtime_from_registry(self) -> str:
        """The host's own agent for this terminal, or "unknown".

        The binding carries `agentId`, which is the host's vocabulary — `codex`,
        `claude`, `kimi`, and a dozen more this project has no launcher for. The
        ones we recognise pass; the rest read as "unknown" and the caller refuses,
        which over-refuses rather than writing into something unrecognised.

        This asked for `agent.runtime`, a field that does not exist, on a procedure
        that does not exist. It returned "unknown" for every terminal on every
        host, so the gate above it refused every Superset send for two days.
        """
        try:
            bindings = self.list_agent_bindings(self.config.workspace_id)
        except (TrpcError, ValueError):
            return "unknown"
        for binding in bindings:
            if binding.get("terminalId") != self.config.terminal_id:
                continue
            agent_id = binding.get("agentId")
            return agent_id if agent_id in _RUNTIMES else "unknown"
        return "unknown"

    def validate_canary(
        self,
        canary: str,
        *,
        baseline_text: str,
        submitted_text: str,
    ) -> None:
        _validate_canary(canary)
        # Deliberately the loose matcher: the guard must reject everything the
        # observation path could ever accept, under any wrapping.
        if _canary_could_appear(submitted_text, canary):
            raise ValueError("canary must not occur literally in submitted text")
        if _canary_could_appear(baseline_text, canary):
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
            # Only a HOST counter can be read as ordered. A derived revision is a
            # hash of the screen: it moves in both directions as output scrolls,
            # and treating a smaller number as a reset aborted every poll on the
            # shipped build — after the write had already landed.
            if not snapshot.revision_is_derived and snapshot.revision < baseline_revision:
                raise TrpcError("Superset terminal revision reset during canary polling")
            last_revision = snapshot.revision
            # "The screen changed" is the actual requirement. `>` expresses that
            # only for a host counter; for a derived hash it silently discards
            # every change that happens to hash lower than the baseline — a real
            # canary, on screen, thrown away because of a number's direction. The
            # live run that first proved this path GREEN did so by luck of the
            # ordering, and the test that caught it was written afterwards.
            changed = (
                snapshot.revision != baseline_revision
                if snapshot.revision_is_derived
                else snapshot.revision > baseline_revision
            )
            if changed and _structured_canary_observed(snapshot.text, canary):
                evidence = EvidenceEvent(
                    event_id=str(uuid.uuid4()),
                    command_id=command_id,
                    leg=Leg.ACCEPT,
                    state=LegState.SUCCEEDED,
                    kind="canary.observed",
                    provenance=Provenance.TERMINAL_DIFF,
                    evidence_ref=f"terminal:{snapshot.terminal_id}:revision:{snapshot.revision}",
                    source_id="adapter:superset",
                    target_id=f"terminal:{snapshot.terminal_id}",
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
            source_id="adapter:superset",
            target_id=f"terminal:{self.config.terminal_id}",
        )
        return CanaryResult(False, attempts, last_revision, evidence)


def _wrap_tolerant_pattern(canary: str) -> str:
    """Match the canary even when a hard line wrap splits it mid-token."""
    return r"\s*".join(re.escape(character) for character in canary)


def _canary_could_appear(text: str, canary: str) -> bool:
    """Loosest possible match, used only to REJECT text before dispatch.

    This must stay strictly looser than `_structured_canary_observed`. The
    boundary assertions there are whitespace-sensitive, so a terminal wrap can
    manufacture a boundary that the submitted text did not have: text
    `X<canary>` carries no boundary before the canary, but the pane renders it
    as `X\\n<canary>`, which does. If the pre-dispatch guard were the stricter
    of the two, that echo would satisfy acceptance on its own — a false GREEN
    from prompt echo, which is the one outcome this project must never produce.

    Dropping the boundary assertions here means the guard rejects anything that
    could later be observed, in any wrapping.
    """
    return re.search(_wrap_tolerant_pattern(canary), strip_terminal_decoration(text)) is not None


def _structured_canary_observed(terminal_text: str, canary: str) -> bool:
    normalized = strip_terminal_decoration(terminal_text)
    pattern = rf"(?<![A-Z0-9_]){_wrap_tolerant_pattern(canary)}(?![A-Z0-9_])"
    return re.search(pattern, normalized) is not None


def _is_uuid(value: str) -> bool:
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        return False
    return str(parsed) == value.lower()


def _validate_canary(canary: str) -> None:
    if not isinstance(canary, str) or re.fullmatch(r"YAPITALISM_ACK_[A-F0-9]{32}", canary) is None:
        raise ValueError("canary must be YAPITALISM_ACK_ followed by 32 uppercase hex characters")


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


def _revision_of(payload: dict[str, Any], text: str) -> tuple[int, bool]:
    """The host's revision if it sends one, otherwise one derived from the screen.

    The shipped host does not send one: `terminal.snapshot` returns
    `{terminalId, cols, rows, text}`, and the string "revision" does not occur
    anywhere in its bundle. Requiring the field made every snapshot raise, which
    is why no Superset send could complete.

    A derived value is NOT the host's monotonic counter and must never be reported
    as one — it cannot order two changes, and a screen that returns to an earlier
    state repeats its number. It answers only "did this change under me", which is
    the single question the optimistic check actually asks. Callers that want to
    claim more have to check `optimistic_revision` on the capabilities, which says
    `client` for exactly this reason.
    """
    value = payload.get("revision")
    if not isinstance(value, bool) and isinstance(value, int) and value >= 0:
        return value, False
    return zlib.crc32(text.encode("utf-8", "replace")), True


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
    "ClearResult",
    "DispatchResult",
    "SupersetAdapter",
    "SupersetConfig",
    "TerminalSnapshot",
    "TrpcError",
    "TrpcProcedureMissing",
]
