"""The stdio transport, spoken for real.

Most MCP clients — Claude Desktop, Cursor, and the directory listings — do not
take a URL. They spawn the server and talk over stdin/stdout. Without this
transport the server cannot be used from them at all, so it is worth a real
subprocess rather than a mock.

The specific regression guarded here is the startup banner. FastMCP prints one
by default; on stdio that lands in the protocol stream and the client fails to
parse the very first message.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest


class StdioTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.proc = subprocess.Popen(
            [sys.executable, "-m", "yapitalism.mcp.server", "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=dict(os.environ, PYTHONPATH="src"),
        )
        cls._send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            }
        )
        cls.first_line = cls.proc.stdout.readline()
        cls._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    @classmethod
    def tearDownClass(cls) -> None:
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()

    @classmethod
    def _send(cls, payload: dict) -> None:
        cls.proc.stdin.write(json.dumps(payload) + "\n")
        cls.proc.stdin.flush()

    def test_the_first_byte_of_stdout_is_protocol_not_a_banner(self) -> None:
        """The whole reason show_banner=False is passed."""
        self.assertTrue(
            self.first_line.lstrip().startswith("{"),
            f"stdout opened with non-protocol output: {self.first_line[:80]!r}",
        )

    def test_initialize_identifies_the_server(self) -> None:
        reply = json.loads(self.first_line)
        self.assertEqual(reply["result"]["serverInfo"]["name"], "yapitalism")

    def test_every_tool_is_reachable_over_stdio(self) -> None:
        self._send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = {
            tool["name"]
            for tool in json.loads(self.proc.stdout.readline())["result"]["tools"]
        }
        # A client that can list panes but not send would look like it works and
        # then quietly be useless for the thing the product is for.
        self.assertEqual(
            tools,
            {
                "panes_list",
                "pane_read",
                "pane_send",
                "pane_await",
                "pane_task",
                "panes_name",
                "panes_unname",
                "panes_create",
                "pane_clear",
                "panes_resume",
            },
        )


class TransportSelectionTests(unittest.TestCase):
    def test_http_stays_the_default(self) -> None:
        """Codex registers a URL; changing the default would break it."""
        import inspect

        from yapitalism.mcp import server

        source = inspect.getsource(server.main)
        self.assertIn('transport="http"', source)
        self.assertIn('transport="stdio"', source)

    def test_stdio_never_prints_a_banner(self) -> None:
        import inspect

        from yapitalism.mcp import server

        source = inspect.getsource(server.main)
        stdio_call = source.split('transport="stdio"')[1].split(")")[0]
        self.assertIn("show_banner=False", stdio_call)


if __name__ == "__main__":
    unittest.main()
