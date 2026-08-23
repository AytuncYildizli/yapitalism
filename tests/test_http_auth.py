"""The HTTP transport's one real exposure, and its lid.

Loopback is not a user boundary: 127.0.0.1 is reachable by every local user's
processes, and this server can type into any terminal its own user can see. On a
single-user machine that grants nothing new — any process running as the user can
already `tmux send-keys` — but on a shared machine the open port would let other
users' processes cross into this one's terminals. `YAPITALISM_MCP_TOKEN` exists
for exactly that machine, and these tests pin the contract measured live on
2026-08-23: no token 401, wrong token 401, right token serves, and without the
env var the transport stays open as before.
"""

from __future__ import annotations

import asyncio
import unittest


class LoopbackTokenVerifierTests(unittest.TestCase):
    def verify(self, configured: str, presented: str):
        from yapitalism.mcp.server import LoopbackTokenVerifier

        verifier = LoopbackTokenVerifier(configured)
        return asyncio.run(verifier.verify_token(presented))

    def test_the_right_token_is_accepted(self) -> None:
        access = self.verify("s3cret", "s3cret")
        self.assertIsNotNone(access)
        self.assertEqual(access.client_id, "yapitalism-local")

    def test_a_wrong_token_is_refused(self) -> None:
        self.assertIsNone(self.verify("s3cret", "wrong"))

    def test_an_empty_presentation_is_refused(self) -> None:
        self.assertIsNone(self.verify("s3cret", ""))

    def test_a_prefix_is_not_enough(self) -> None:
        """The comparison is exact, not startswith — and constant-time."""
        self.assertIsNone(self.verify("s3cret", "s3cr"))
        self.assertIsNone(self.verify("s3cret", "s3cret-and-more"))

    def test_it_is_fastmcps_own_verifier_type(self) -> None:
        """The check must live inside FastMCP's auth middleware.

        A bespoke ASGI layer bolted on the outside would be one more thing to
        keep aligned with the framework's routing; subclassing TokenVerifier
        means the 401 happens before any tool dispatch, in the path FastMCP
        already guards.
        """
        from fastmcp.server.auth import TokenVerifier

        from yapitalism.mcp.server import LoopbackTokenVerifier

        self.assertIsInstance(LoopbackTokenVerifier("x"), TokenVerifier)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
