"""Machines as panes: the peer config's trust rules, and verbatim receipts.

A peer is another machine's yapitalism server mounted under its own namespace.
The two properties these tests exist to hold:

1. **Config fails closed.** A non-loopback peer without a token, a public
   address without an explicit `allow_public`, a group-readable file, a name
   that shadows a local backend — each is refused at load, not discovered later
   as a broken pane list.
2. **Receipts pass through verbatim.** The peer built its verdict next to its
   own terminal; this side re-namespaces target ids and changes NOTHING else.
   A restated remote claim would carry this process's name on evidence it
   never saw.
"""

from __future__ import annotations

import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

from yapitalism.mcp.backends.base import BackendError
from yapitalism.mcp.backends.remote_backend import RemotePeer
from yapitalism.peers import Peer, PeerConfigError, load_peers


def write_peers(directory: str, payload: dict, mode: int = 0o600) -> Path:
    path = Path(directory) / "peers.json"
    path.write_text(json.dumps(payload))
    os.chmod(path, mode)
    return path


class ConfigTrustTests(unittest.TestCase):
    def test_no_file_is_no_peers(self) -> None:
        self.assertEqual(load_peers(Path("/nonexistent/peers.json")), [])

    def test_a_tailscale_peer_with_a_token_loads(self) -> None:
        with TemporaryDirectory() as tmp:
            path = write_peers(tmp, {"peers": {"studio": {
                "url": "http://100.73.28.102:8792/mcp", "token": "t"}}})
            [peer] = load_peers(path)
        self.assertEqual(peer.name, "studio")

    def test_a_remote_peer_without_a_token_is_refused(self) -> None:
        """A 0.3.0 server will 401 it; recording it means recording an outage."""
        with TemporaryDirectory() as tmp:
            path = write_peers(tmp, {"peers": {"studio": {
                "url": "http://100.73.28.102:8792/mcp"}}})
            with self.assertRaisesRegex(PeerConfigError, "no token"):
                load_peers(path)

    def test_a_public_address_needs_the_explicit_flag(self) -> None:
        with TemporaryDirectory() as tmp:
            path = write_peers(tmp, {"peers": {"vps": {
                "url": "http://203.0.113.7:8792/mcp", "token": "t"}}})
            with self.assertRaisesRegex(PeerConfigError, "public address"):
                load_peers(path)
            path = write_peers(tmp, {"peers": {"vps": {
                "url": "http://203.0.113.7:8792/mcp", "token": "t",
                "allow_public": True}}})
            [peer] = load_peers(path)
            self.assertEqual(peer.name, "vps")

    def test_a_ts_net_name_counts_as_private(self) -> None:
        with TemporaryDirectory() as tmp:
            path = write_peers(tmp, {"peers": {"studio": {
                "url": "http://studio.tail1234.ts.net:8792/mcp", "token": "t"}}})
            [peer] = load_peers(path)
        self.assertIn("ts.net", peer.url)

    def test_an_arbitrary_dns_name_is_public_until_said_otherwise(self) -> None:
        """A hostname can resolve anywhere; only MagicDNS names are known-private."""
        with TemporaryDirectory() as tmp:
            path = write_peers(tmp, {"peers": {"box": {
                "url": "http://my-server.example.com:8792/mcp", "token": "t"}}})
            with self.assertRaisesRegex(PeerConfigError, "public address"):
                load_peers(path)

    def test_group_readable_file_is_refused(self) -> None:
        with TemporaryDirectory() as tmp:
            path = write_peers(tmp, {"peers": {}}, mode=0o644)
            with self.assertRaisesRegex(PeerConfigError, "chmod 600"):
                load_peers(path)

    def test_a_name_shadowing_a_local_backend_is_refused(self) -> None:
        """`tmux:%0` must always mean the LOCAL pane."""
        with TemporaryDirectory() as tmp:
            path = write_peers(tmp, {"peers": {"tmux": {
                "url": "http://100.73.28.102:8792/mcp", "token": "t"}}})
            with self.assertRaisesRegex(PeerConfigError, "shadows"):
                load_peers(path)

    def test_one_bad_peer_fails_the_whole_load(self) -> None:
        """A skipped peer is a machine the operator believes is watched."""
        with TemporaryDirectory() as tmp:
            path = write_peers(tmp, {"peers": {
                "good": {"url": "http://100.73.28.102:8792/mcp", "token": "t"},
                "bad": {"url": "http://100.73.28.103:8792/mcp"},
            }})
            with self.assertRaises(PeerConfigError):
                load_peers(path)

    def test_the_repr_never_shows_the_token(self) -> None:
        peer = Peer(name="studio", url="http://100.73.28.102:8792/mcp", token="hunter2")
        self.assertNotIn("hunter2", repr(peer))


class FakeMcpPeer:
    """A minimal streamable-HTTP MCP server: initialize, initialized, tools/call."""

    def __init__(self, tool_results: dict, *, token: str = "pt") -> None:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                if outer.token and self.headers.get("Authorization") != f"Bearer {outer.token}":
                    self.send_response(401)
                    self.end_headers()
                    return
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append(body)
                method = body.get("method")
                if method == "notifications/initialized":
                    self.send_response(202)
                    self.end_headers()
                    return
                if method == "initialize":
                    result = {"protocolVersion": "2025-06-18", "capabilities": {},
                              "serverInfo": {"name": "yapitalism", "version": "peer"}}
                else:
                    name = body["params"]["name"]
                    outcome = outer.tool_results[name]
                    result = {"content": [{"type": "text", "text": json.dumps(outcome)}]}
                payload = json.dumps({"jsonrpc": "2.0", "id": body.get("id"), "result": result})
                data = payload.encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("mcp-session-id", "sess-1")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args: object) -> None:
                return

        self.token = token
        self.tool_results = tool_results
        self.requests: list[dict] = []
        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_port}/mcp"

    def __enter__(self) -> "FakeMcpPeer":
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()


def remote(fake: FakeMcpPeer, name: str = "studio", token: str = "pt") -> RemotePeer:
    return RemotePeer(Peer(name=name, url=fake.url, token=token), timeout=5.0)


class ForwardingTests(unittest.TestCase):
    def test_the_peers_receipt_comes_back_verbatim_with_branded_ids(self) -> None:
        receipt = {"ok": True, "target_id": "tmux:%0", "runtime": "codex",
                   "status": "GREEN", "phase": "injected", "speak": "codex aldı.",
                   "client_guarantees": ["idempotent_dispatch"]}
        with FakeMcpPeer({"pane_send": receipt}) as fake:
            peer = remote(fake)
            out = peer.brand(peer.call_tool("pane_send", {"target_id": "tmux:%0", "text": "hi"}))
        self.assertEqual(out["target_id"], "studio:tmux:%0")
        self.assertEqual(out["status"], "GREEN")
        self.assertEqual(out["speak"], "codex aldı.", "the spoken line is the peer's, untouched")

    def test_strip_refuses_an_id_from_another_machine(self) -> None:
        with FakeMcpPeer({}) as fake:
            peer = remote(fake)
            with self.assertRaises(BackendError):
                peer.strip("otherbox:tmux:%0")
            self.assertEqual(peer.strip("studio:tmux:%0"), "tmux:%0")

    def test_a_wrong_token_is_a_named_error_not_a_hang(self) -> None:
        with FakeMcpPeer({"panes_list": {"ok": True}}) as fake:
            peer = remote(fake, token="wrong")
            with self.assertRaisesRegex(BackendError, "401"):
                peer.call_tool("panes_list", {})

    def test_nested_target_ids_are_branded_too(self) -> None:
        listing = {"ok": True, "panes": [
            {"target_id": "tmux:%0", "runtime": "codex"},
            {"target_id": "superset:abc", "runtime": "claude"},
        ], "errors": []}
        with FakeMcpPeer({"panes_list": listing}) as fake:
            peer = remote(fake)
            out = peer.brand(peer.call_tool("panes_list", {}))
        self.assertEqual([p["target_id"] for p in out["panes"]],
                         ["studio:tmux:%0", "studio:superset:abc"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
