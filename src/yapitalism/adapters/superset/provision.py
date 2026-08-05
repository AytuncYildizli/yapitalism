"""Turning a Superset install into a manifest this tool can use.

Capability honesty told a stranger what their host enforces. It did not get them
connected: the Superset backend needs an endpoint and a bearer token, and nothing
here could produce them. Reading a token out of an app bundle or asking someone
to paste one are both worse than the truth, which is that Superset already writes
exactly what is needed.

The desktop app writes `~/.superset/host/<organizationId>/manifest.json` at mode
0600, holding `pid`, `endpoint`, `authToken`, `startedAt` and `organizationId`.
This module reads that file, proves the credentials work against the live host,
and writes the manifest this tool loads. No guessing, no pasting, and nothing
invented.

Everything here fails closed. A stale manifest, a file with loose permissions, a
host that does not answer, or two organizations with no way to choose between them
are all refusals rather than a best guess — a manifest written from a bad read
would fail later, further from the cause.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import SupersetAdapter, SupersetConfig, TrpcError, _validate_loopback_endpoint

#: The app reads this too, and honouring it is what makes a non-default install
#: discoverable instead of invisible.
SUPERSET_HOME_ENV = "SUPERSET_HOME_DIR"
_MAX_RECORD_BYTES = 64 * 1024
#: Placeholders for the two ids the discovery calls do not use. `workspace.list`
#: and `terminal.listSessions` need only the endpoint and token, but
#: SupersetConfig requires every field to be non-empty — so these satisfy
#: validation and are never sent anywhere.
_PENDING = "pending-discovery"


class ProvisionError(RuntimeError):
    """Provisioning could not proceed. Never raised to report partial success.

    Messages are written to be read by whoever is installing the tool, and never
    contain the token: an error is the easiest place for a credential to end up in
    a log or a screenshot.
    """


@dataclass(frozen=True, slots=True, repr=False)
class HostRecord:
    """One Superset host as the app itself describes it."""

    organization_id: str
    endpoint: str
    pid: int
    auth_token: str = field(repr=False)
    source: Path = Path()

    def __repr__(self) -> str:
        # Redacted like SupersetConfig's, and for the same reason: these objects
        # end up in tracebacks.
        return (
            "HostRecord("
            f"organization_id={self.organization_id!r}, endpoint={self.endpoint!r}, "
            f"pid={self.pid}, auth_token='<redacted>', source={str(self.source)!r})"
        )

    @property
    def alive(self) -> bool:
        """Whether the process the manifest names still exists.

        Superset leaves the file behind when it exits, so a manifest on disk is
        not a running host. Signal 0 checks for existence without delivering
        anything.
        """
        try:
            os.kill(self.pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            # Exists, owned by someone else. Still a live process.
            return True
        except OSError:
            return False
        return True


def superset_home() -> Path:
    override = os.environ.get(SUPERSET_HOME_ENV)
    return Path(override) if override else Path.home() / ".superset"


def _read_record(path: Path) -> HostRecord:
    try:
        info = path.stat()
    except OSError as error:
        raise ProvisionError(f"cannot read {path}: {error.strerror}") from None
    if not stat.S_ISREG(info.st_mode):
        raise ProvisionError(f"{path} is not a regular file")
    if info.st_uid != os.geteuid():
        raise ProvisionError(f"{path} is not owned by you")
    if info.st_mode & 0o077:
        # Superset writes 0600. Anything looser means something changed it, and a
        # bearer token readable by other accounts should stop the install rather
        # than be copied into a second file.
        raise ProvisionError(
            f"{path} is readable by others (mode {oct(info.st_mode & 0o777)}); "
            "Superset writes it 0600, so fix that before provisioning"
        )
    if info.st_size > _MAX_RECORD_BYTES:
        raise ProvisionError(f"{path} is larger than 64 KiB")
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ProvisionError(f"{path} is not valid UTF-8 JSON") from None
    if not isinstance(payload, dict):
        raise ProvisionError(f"{path} does not contain a JSON object")
    missing = [key for key in ("endpoint", "authToken", "organizationId", "pid") if key not in payload]
    if missing:
        raise ProvisionError(f"{path} is missing {', '.join(missing)}")
    pid = payload["pid"]
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise ProvisionError(f"{path} has an unusable pid")
    for key in ("endpoint", "authToken", "organizationId"):
        if not isinstance(payload[key], str) or not payload[key].strip():
            raise ProvisionError(f"{path} has an unusable {key}")
    endpoint = str(payload["endpoint"]).rstrip("/")
    try:
        # The app stores the bare origin; this tool addresses the tRPC surface,
        # and the same loopback restrictions apply either way.
        endpoint = _validate_loopback_endpoint(f"{endpoint}/trpc")
    except ValueError as error:
        raise ProvisionError(f"{path} endpoint is not usable: {error}") from None
    return HostRecord(
        organization_id=str(payload["organizationId"]),
        endpoint=endpoint,
        pid=pid,
        auth_token=str(payload["authToken"]),
        source=path,
    )


def discover_hosts(home: Path | None = None) -> list[HostRecord]:
    """Every Superset host manifest on this machine, readable ones only.

    An unreadable or malformed file is skipped rather than fatal: one broken
    organization directory must not hide a working one. What cannot be skipped is
    finding nothing, which the caller reports.
    """
    root = (home or superset_home()) / "host"
    records: list[HostRecord] = []
    for path in sorted(root.glob("*/manifest.json")):
        try:
            records.append(_read_record(path))
        except ProvisionError:
            continue
    return records


def select_host(records: list[HostRecord], organization_id: str | None = None) -> HostRecord:
    """Pick the one host to provision against, or refuse to guess."""
    if not records:
        raise ProvisionError(
            f"no Superset host manifest under {superset_home() / 'host'}. "
            "Start the Superset desktop app once so it writes one, then run this again"
        )
    if organization_id is not None:
        for record in records:
            if record.organization_id == organization_id:
                return record
        known = ", ".join(sorted(r.organization_id for r in records))
        raise ProvisionError(f"no host for organization {organization_id}; found: {known}")
    live = [record for record in records if record.alive]
    if not live:
        raise ProvisionError(
            "every Superset host manifest names a process that is gone. Superset "
            "leaves the file behind when it exits, so start the app and try again"
        )
    if len(live) > 1:
        known = ", ".join(sorted(r.organization_id for r in live))
        # Choosing silently would bind this tool to whichever sorted first, which
        # is not a decision anyone made.
        raise ProvisionError(
            f"{len(live)} live Superset hosts; pass --organization to choose: {known}"
        )
    return live[0]


@dataclass(frozen=True, slots=True)
class Binding:
    """The default (workspace, terminal) pair a manifest is written with.

    Only a default: every read and send rebinds to the pair it was asked for, so
    this decides which terminal a bare CLI call addresses and nothing more.
    """

    workspace_id: str
    workspace_name: str
    terminal_id: str
    runtime: str
    terminal_count: int


def probe_binding(record: HostRecord, timeout: float = 5.0) -> Binding:
    """Prove the credentials work and pick a default terminal.

    This is the step that turns "a file exists" into "the host answers us". A
    manifest written without it would look fine and fail on first use, which is
    the failure mode this whole module is trying to avoid.
    """
    config = SupersetConfig(
        endpoint=record.endpoint,
        bearer_token=record.auth_token,
        workspace_id=_PENDING,
        terminal_id=_PENDING,
        timeout_seconds=timeout,
    )
    adapter = SupersetAdapter(config)
    try:
        workspaces = adapter.list_workspaces()
    except TrpcError as error:
        raise ProvisionError(
            f"the Superset host at {record.endpoint} did not accept the token "
            f"from {record.source}: {error}"
        ) from None
    if not workspaces:
        raise ProvisionError(
            "the host answered but reports no workspaces; open a workspace in "
            "Superset first"
        )
    for workspace in workspaces:
        workspace_id = workspace.get("id")
        if not isinstance(workspace_id, str) or not workspace_id:
            continue
        try:
            sessions = adapter.list_terminals(workspace_id)
        except TrpcError:
            continue
        for session in sessions:
            terminal_id = session.get("terminalId")
            if not isinstance(terminal_id, str) or not terminal_id:
                continue
            # The runtime is nested under `agent`, not flat on the session. The
            # first version of this read `session["runtime"]` - a guessed name -
            # and reported "no agent terminal" against a host running 22 of them.
            # `SupersetBackend.list_panes` already knew the shape; the fix was to
            # read that rather than invent a key.
            agent = session.get("agent")
            runtime = agent.get("runtime") if isinstance(agent, dict) else None
            # Prefer a terminal running an agent: binding to a plain shell by
            # default would make the first send address a shell prompt.
            if runtime in ("codex", "claude", "kimi"):
                name = workspace.get("name")
                return Binding(
                    workspace_id=workspace_id,
                    workspace_name=name if isinstance(name, str) else workspace_id,
                    terminal_id=terminal_id,
                    runtime=str(runtime),
                    terminal_count=len(sessions),
                )
    raise ProvisionError(
        "the host answered but no terminal is running codex, claude or kimi. "
        "Start an agent in a Superset terminal, then run this again"
    )


def manifest_payload(record: HostRecord, binding: Binding, timeout: float = 8.0) -> dict[str, object]:
    return {
        "endpoint": record.endpoint,
        "bearer_token": record.auth_token,
        "workspace_id": binding.workspace_id,
        "terminal_id": binding.terminal_id,
        "timeout_seconds": timeout,
    }


def write_manifest(payload: dict[str, object], path: Path, *, force: bool = False) -> Path:
    """Write the manifest 0600, without clobbering one that already exists.

    Created with O_EXCL and the mode passed to open() rather than chmod'ed
    afterwards: a token must never be on disk group-readable, not even for the
    instant between the two calls.
    """
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if force else os.O_EXCL)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        raise ProvisionError(
            f"{path} already exists; pass --force to replace it"
        ) from None
    except OSError as error:
        raise ProvisionError(f"cannot write {path}: {error.strerror}") from None
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except OSError as error:
        raise ProvisionError(f"cannot write {path}: {error.strerror}") from None
    return path
