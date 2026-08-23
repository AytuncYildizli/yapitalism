"""A peer machine's yapitalism server, spoken to as one more backend.

This is deliberately NOT a TerminalBackend that re-implements send and
acceptance against remote primitives. The peer already runs the full receipt
engine next to its own terminals; rebuilding verdicts here from lower-level
calls would put this process's name on claims it did not measure. So the unit
of forwarding is the TOOL: `pane_send` on `studio:tmux:%0` becomes `pane_send`
on `tmux:%0` at studio, and the receipt comes back verbatim with only the
target ids re-namespaced so the caller can keep addressing what it sees.

Transport is MCP over HTTP with the peer's bearer token - the same surface any
client uses, so a peer needs nothing beyond what 0.3.0 already serves. The MCP
session is initialised lazily and once per process; a peer that restarts gets
one transparent re-initialise.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

from ...peers import Peer
from .base import BackendError


class RemotePeer:
    def __init__(self, peer: Peer, *, timeout: float = 20.0) -> None:
        self._peer = peer
        self._timeout = timeout
        self._lock = threading.Lock()
        self._session: str | None = None
        self._request_id = 0

    @property
    def namespace(self) -> str:
        return self._peer.name

    # -- MCP plumbing ---------------------------------------------------------

    def _post(self, payload: dict, *, session: str | None) -> tuple[dict, str | None]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._peer.token:
            headers["Authorization"] = f"Bearer {self._peer.token}"
        if session:
            headers["mcp-session-id"] = session
        request = urllib.request.Request(
            self._peer.url, data=json.dumps(payload).encode(), headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read(4 * 1024 * 1024).decode(errors="replace")
                new_session = response.headers.get("mcp-session-id")
        except urllib.error.HTTPError as error:
            if error.code == 401:
                raise BackendError(
                    f"peer {self._peer.name} refused the token (401); its token "
                    "rotated or the peers file is stale"
                ) from None
            if error.code == 404 and session:
                # The peer restarted and forgot the session; the caller retries.
                raise _SessionLost() from None
            raise BackendError(
                f"peer {self._peer.name} answered HTTP {error.code}"
            ) from None
        except OSError as error:
            raise BackendError(
                f"peer {self._peer.name} is unreachable ({error})"
            ) from None
        # Streamable HTTP frames JSON as SSE `data:` lines; plain JSON also occurs.
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                line = line[5:].strip()
            if line.startswith("{"):
                return json.loads(line), new_session
        raise BackendError(f"peer {self._peer.name} sent no JSON payload")

    def _initialise(self) -> str:
        payload = {
            "jsonrpc": "2.0", "id": self._next_id(), "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "yapitalism-peer", "version": "0"},
            },
        }
        message, session = self._post(payload, session=None)
        if "error" in message:
            raise BackendError(
                f"peer {self._peer.name} rejected initialize: {message['error'].get('message', '')[:120]}"
            )
        if not session:
            raise BackendError(f"peer {self._peer.name} issued no session id")
        notif = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        try:
            self._post(notif, session=session)
        except BackendError:
            pass  # some servers answer 202 with an empty body; the session stands
        except _SessionLost:
            raise BackendError(f"peer {self._peer.name} dropped its session mid-handshake") from None
        return session

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def call_tool(self, tool: str, arguments: dict) -> dict:
        """One tool call on the peer, with a single re-initialise on restart."""
        with self._lock:
            for attempt in range(2):
                if self._session is None:
                    self._session = self._initialise()
                payload = {
                    "jsonrpc": "2.0", "id": self._next_id(), "method": "tools/call",
                    "params": {"name": tool, "arguments": arguments},
                }
                try:
                    message, _ = self._post(payload, session=self._session)
                except _SessionLost:
                    self._session = None
                    continue
                if "error" in message:
                    raise BackendError(
                        f"peer {self._peer.name} {tool}: {message['error'].get('message', '')[:160]}"
                    )
                content = message.get("result", {}).get("content", [])
                text = content[0].get("text", "") if content else ""
                try:
                    return json.loads(text)
                except (ValueError, TypeError):
                    raise BackendError(
                        f"peer {self._peer.name} {tool} returned no JSON body"
                    ) from None
            raise BackendError(f"peer {self._peer.name} kept dropping its session")

    # -- namespacing ----------------------------------------------------------

    def strip(self, target_id: str) -> str:
        prefix = self._peer.name + ":"
        if not target_id.startswith(prefix):
            raise BackendError(f"{target_id} does not belong to peer {self._peer.name}")
        return target_id[len(prefix):]

    def brand(self, payload: dict) -> dict:
        """Re-namespace every target id in a peer response, in place.

        Only `target_id` fields are rewritten. Receipts, phases, spoken lines and
        guarantees pass through untouched: the peer proved them, and the machine
        name is already carried by the id.
        """
        def walk(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    if key == "target_id" and isinstance(value, str):
                        node[key] = f"{self._peer.name}:{value}"
                    else:
                        walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)
        walk(payload)
        return payload


class _SessionLost(Exception):
    pass
