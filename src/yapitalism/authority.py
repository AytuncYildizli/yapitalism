"""Who may WRITE through this server, decided by where the request came from.

Authentication answers "who holds the token"; it never answered "what may the
holder do". This module is the second half. The boundary is the transport,
because it is the one fact the server knows without trusting the caller:

    stdio   the client spawned this process on this machine — the OS already
            made the trust decision. Writes allowed, as they always were.
    http    anyone with the token, from anywhere the bind reaches. Writes are
            allowed only if the operator said so, once, on this machine —
            `yapitalism setup` says it while registering HTTP clients, or
            `yapitalism authority allow-http-writes` says it directly.

Absent file means DENY for HTTP. That is a breaking default for pre-0.5 HTTP
setups, chosen with eyes open: the refusal names the one command that fixes
it, and "auth exists but authorization is implicit" was the standing objection
of every serious reviewer. Read-only mode refuses writes on every transport
and exists so a skeptic can run the watcher for weeks with zero write surface.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

READ_ONLY_ENV = "YAPITALISM_READ_ONLY"


def authority_path() -> Path:
    configured = os.environ.get("YAPITALISM_AUTHORITY")
    if configured:
        return Path(configured)
    root = os.environ.get("XDG_CONFIG_HOME")
    base = Path(root) if root else Path.home() / ".config"
    return base / "yapitalism" / "authority.json"


def http_writes_allowed() -> bool:
    """Whether this machine's operator has allowed writes over HTTP."""
    try:
        raw = authority_path().read_text()
    except OSError:
        return False
    try:
        data = json.loads(raw)
    except ValueError:
        # A corrupt authority file fails CLOSED. Guessing "they probably meant
        # allow" would make a truncated write a privilege escalation.
        return False
    return isinstance(data, dict) and data.get("http_writes") == "allow"


def remote_create_allowed() -> bool:
    """Whether HTTP callers may START or RESUME agents here.

    Separate from `http_writes` on purpose: typing into an agent that already
    exists and materialising a new process are different amounts of authority,
    and the second must not ride in on the first. Default deny; fails closed
    on a corrupt file, same as everything else here.
    """
    try:
        data = json.loads(authority_path().read_text())
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("remote_create") == "allow"


def set_remote_create(allow: bool) -> Path:
    return _set_key("remote_create", "allow" if allow else "deny")


def set_http_writes(allow: bool) -> Path:
    return _set_key("http_writes", "allow" if allow else "deny")


def _set_key(key: str, value: str) -> Path:
    path = authority_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {}
    try:
        existing = json.loads(path.read_text())
        if isinstance(existing, dict):
            payload = existing
    except (OSError, ValueError):
        pass
    payload[key] = value
    path.write_text(json.dumps(payload, indent=2) + "\n")
    path.chmod(0o600)
    return path


def read_only_requested() -> bool:
    return os.environ.get(READ_ONLY_ENV) == "1"
