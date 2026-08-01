"""Local MCP server exposing terminal panes across backends.

Codex reaches this over plain loopback HTTP — the same shape as the `unityMCP`
entry already in the Codex config — so there is no public endpoint, no OAuth,
and no connector registry in the path. The phone drives a desktop Codex session
over Remote; only this machine ever talks to this process.

Tools are named `panes_*` rather than `terminals_*` on purpose: Superset's own
MCP may be enabled in the same Codex config and already owns `terminals_list`.
Two identically named tools would let a spoken "list my terminals" route to
either server.

Read-only by design. Proving the transport must not be able to mutate a
terminal, so nothing here writes. Sending, and the receipts that belong with
it, come after — and receipts are the reason sends must land here rather than
be split across two servers that disagree about what counts as proof.
"""

from __future__ import annotations

import os

from uuid import uuid4

from fastmcp import FastMCP

from .backends.base import AcceptanceOutcome, BackendError
from .backends.superset_backend import SupersetBackend
from .backends.tmux_backend import TmuxBackend
from .receipt import build_receipt, canary_instruction, new_canary
from .registry import BackendRegistry

DEFAULT_HOST = "127.0.0.1"
# 8787 belongs to the launchd-managed mahmory-api; 8791 was also taken.
DEFAULT_PORT = 8792

mcp: FastMCP = FastMCP("yapitalism")
# Superset is registered unconditionally. Constructing it reads no files, and a
# missing or unusable manifest surfaces as a per-backend error in panes_list
# rather than preventing the server from starting or hiding tmux.
registry = BackendRegistry([TmuxBackend(), SupersetBackend()])


@mcp.tool
def panes_list() -> dict[str, object]:
    """List agent terminal panes on this machine, across every backend.

    Each pane carries:
      - `target_id`, namespaced (`tmux:%0`). Use it verbatim for any later call.
      - `runtime`: codex, claude, kimi, shell, or unknown. Anything that is not
        an agent runtime is NOT addressable — a pane that used to run an agent
        and now runs a plain shell would turn an instruction into a shell
        command.
      - `missing_guarantees`, when present: protections that backend cannot
        enforce. Never describe such a pane as being as safe as one without it.

    `errors` lists backends that could not be reached. A backend returning no
    panes and a backend that failed are different claims — do not report "no
    terminals" while `errors` is non-empty.

    This is the local machine. Superset's `terminals_*` tools address
    Superset-managed PTYs instead.
    """
    panes, errors = registry.list_all()
    return {
        "ok": not errors,
        "backends": list(registry.namespaces),
        "panes": panes,
        "errors": errors,
    }


@mcp.tool
def pane_read(target_id: str, lines: int = 200) -> dict[str, object]:
    """Read recent visible output from one pane.

    `target_id` comes from panes_list and must stay namespaced, e.g. `tmux:%0`.
    Read-only: this never types into the pane. Output is the pane as rendered,
    so it may hold wrapped lines, prompts and ANSI leftovers — summarize it
    rather than reading it aloud verbatim.
    """
    try:
        backend = registry.resolve(target_id)
        text = backend.read_pane(target_id, lines)
    except BackendError as error:
        return {"ok": False, "error": str(error), "target_id": target_id}
    return {"ok": True, "target_id": target_id, "text": text}


def main() -> None:
    host = os.environ.get("YAPITALISM_MCP_HOST", DEFAULT_HOST)
    port = int(os.environ.get("YAPITALISM_MCP_PORT", DEFAULT_PORT))
    if host not in {"127.0.0.1", "::1", "localhost"}:
        # Loopback only. This process can read every terminal on the machine.
        raise SystemExit(f"refusing to bind a non-loopback host: {host}")
    mcp.run(transport="http", host=host, port=port)



@mcp.tool
def pane_send(
    target_id: str,
    text: str,
    prove_acceptance: bool = True,
    timeout_seconds: float = 8.0,
) -> dict[str, object]:
    """Send text to a pane and return a receipt for what was actually proven.

    This is a WRITE. Name the target and the action in one sentence and get an
    explicit confirmation before calling it.

    `status` is the only thing to speak from:
      - GREEN  the agent demonstrably processed the text.
      - YELLOW the write landed but processing was NOT proven. Say so. Never
        round a YELLOW up to "done" — text sitting unread in a prompt box looks
        identical to work in progress from the outside.
      - RED    the backend refused the write; nothing reached the terminal.

    `missing_guarantees`, when present, lists protections that backend could not
    enforce before writing. A GREEN from a backend with missing guarantees is
    weaker than one without; do not describe them as equivalent.

    With `prove_acceptance` the text is appended with a one-time marker the
    agent echoes back. Turn it off only when the marker itself would corrupt the
    command — and then the result can never be better than YELLOW.

    Speak the returned `speak` value verbatim or more conservatively.
    """
    try:
        backend = registry.resolve(target_id)
    except BackendError as error:
        return {"ok": False, "error": str(error), "target_id": target_id}

    canary = new_canary() if prove_acceptance else None
    payload = text if canary is None else f"{text}\n\n{canary_instruction(canary)}"
    try:
        outcome = backend.send(
            target_id, payload, canary=canary, client_token=str(uuid4())
        )
        acceptance = (
            backend.await_acceptance(target_id, canary, timeout=timeout_seconds)
            if outcome.dispatched
            else AcceptanceOutcome(False, 0, "not_dispatched")
        )
    except BackendError as error:
        return {"ok": False, "error": str(error), "target_id": target_id}

    receipt = build_receipt(outcome, acceptance, backend.capabilities())
    return {"ok": True, "target_id": target_id, "runtime": outcome.runtime, **receipt.as_dict()}

if __name__ == "__main__":
    main()
