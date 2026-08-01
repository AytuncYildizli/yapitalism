"""Local MCP server exposing tmux panes to Codex.

Transport spike. Codex reaches this over plain loopback HTTP — the same shape
as the `unityMCP` entry already in the Codex config — so there is no public
endpoint, no OAuth, and no connector registry in the path. The phone drives a
desktop Codex session over Remote; only the Mac ever talks to this process.

Read-only by design. Proving the transport must not be able to mutate a
terminal, so nothing here writes. Sending, and the receipts that go with it,
come after the round trip is confirmed.
"""

from __future__ import annotations

import os

from fastmcp import FastMCP

from .tmux import TmuxError, capture_pane, list_panes

DEFAULT_HOST = "127.0.0.1"
# 8787 belongs to the launchd-managed mahmory-api; 8791 was also taken.
DEFAULT_PORT = 8792

mcp: FastMCP = FastMCP("yapitalism")


@mcp.tool
def tmux_panes_list() -> dict[str, object]:
    """List tmux panes on this machine, with the agent runtime in each one.

    `runtime` is derived from the pane's live process tree, not from its title:
    codex, claude, kimi, shell, or unknown. Treat anything that is not an agent
    runtime as not addressable — a pane that used to run an agent and now runs a
    plain shell would otherwise turn an instruction into a shell command.

    Use `target_id` (for example `tmux:%0`) for any later call.

    This is the local tmux server on this Mac. It is unrelated to Superset's
    terminals_* tools, which address Superset-managed PTYs instead.
    """
    try:
        panes = list_panes()
    except TmuxError as error:
        return {"ok": False, "error": str(error), "sessions": []}
    return {
        "ok": True,
        "sessions": [
            {
                "target_id": pane.target_id,
                "session": f"{pane.session_name}:{pane.window_index}.{pane.pane_index}",
                "runtime": pane.runtime,
                "command": pane.current_command,
                "size": f"{pane.width}x{pane.height}",
                "dead": pane.dead,
            }
            for pane in panes
        ],
    }


@mcp.tool
def tmux_pane_read(target_id: str, lines: int = 200) -> dict[str, object]:
    """Read recent visible output from one tmux pane.

    `target_id` comes from tmux_panes_list, e.g. `tmux:%0`. Read-only: this never
    types into the pane. Output is the pane as rendered, so it may contain
    wrapped lines; do not read raw output aloud verbatim.
    """
    try:
        text = capture_pane(target_id, lines)
    except TmuxError as error:
        return {"ok": False, "error": str(error), "target_id": target_id}
    return {"ok": True, "target_id": target_id, "text": text}


def main() -> None:
    host = os.environ.get("YAPITALISM_MCP_HOST", DEFAULT_HOST)
    port = int(os.environ.get("YAPITALISM_MCP_PORT", DEFAULT_PORT))
    if host not in {"127.0.0.1", "::1", "localhost"}:
        # Loopback only. This process can read every terminal on the machine.
        raise SystemExit(f"refusing to bind a non-loopback host: {host}")
    mcp.run(transport="http", host=host, port=port)


if __name__ == "__main__":
    main()
