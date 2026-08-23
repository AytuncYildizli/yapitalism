"""First canary-proven GREEN in two minutes, on your own machine.

The first thing a new user does today is hunt for an addressable pane and hope
its prompt is empty. This removes the hunt: start a real agent in a throwaway
tmux session on an isolated socket, send it one message with proof required,
show the receipt, clean up. Nothing here touches the user's own tmux server or
any existing pane — the socket name guarantees it.

It demonstrates the true thing, not a staged one: the send goes through the
same backend `pane_send` uses, the canary is a real canary, and a YELLOW or RED
is shown as itself. A demo that could not fail would be a decoration, and this
project has a word for decorations dressed as receipts.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid

from .mcp.backends.tmux_backend import TmuxBackend
from .mcp.receipt import build_receipt, canary_instruction, new_canary
from .mcp.tmux import AGENT_LAUNCHERS

_SOCKET = "yapitalism-demo"


def _tmux(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["tmux", "-L", _SOCKET, *args], capture_output=True, text=True, check=False
    )


def run_demo(runtime: str = "", *, keep: bool = False, emit=print) -> int:
    if shutil.which("tmux") is None:
        emit("tmux is not installed — the demo needs it. brew install tmux / apt install tmux")
        return 1
    if not runtime:
        runtime = next(
            (name for name in AGENT_LAUNCHERS if shutil.which(name)), ""
        )
    if not runtime or shutil.which(AGENT_LAUNCHERS.get(runtime, ("",))[0]) is None:
        known = ", ".join(sorted(AGENT_LAUNCHERS))
        emit(f"no agent CLI found on PATH (looked for: {known}). install one and rerun.")
        return 1

    emit(f"starting {runtime} in a throwaway tmux session (socket -L {_SOCKET})...")
    os.environ["YAPITALISM_TMUX_SOCKET"] = _SOCKET
    backend = TmuxBackend()
    session = f"demo-{uuid.uuid4().hex[:6]}"
    outcome = backend.create_pane(session, runtime, os.path.expanduser("~"), timeout=25.0)
    if not outcome.runtime_confirmed:
        emit(f"session created but {runtime} did not come up (reason: {outcome.reason}).")
        _cleanup(emit, keep)
        return 1
    if outcome.blocked_on:
        if _clear_known_first_gate(outcome.target_id, emit):
            outcome = outcome  # gate answered; fall through to the send below
        else:
            emit(
                f"{runtime} is up but parked on a {outcome.blocked_on} screen — a fresh "
                "agent asks for trust/login the first time. answer it once in a terminal:\n"
                f"  tmux -L {_SOCKET} attach -t {session}\n"
                "then rerun the demo. leaving the session up for that."
            )
            return 1

    if not _wait_for_composer(backend, outcome.target_id, runtime, emit):
        _cleanup(emit, keep)
        return 1
    emit(f"{runtime} is up. sending one message, proof required...")
    canary = new_canary()
    text = "Reply with one short line saying you are ready. " + canary_instruction(canary)
    token = f"demo-{int(time.time())}"
    send = backend.send(outcome.target_id, text, canary=canary, client_token=token)
    if send.dispatched:
        acceptance = backend.await_acceptance(
            outcome.target_id, canary, timeout=60.0, client_token=token
        )
    else:
        from .mcp.backends.base import AcceptanceOutcome

        acceptance = AcceptanceOutcome(False, 0, "not_dispatched")
    receipt = build_receipt(send, acceptance, backend.capabilities())

    emit("")
    emit(f"  status : {receipt.status}")
    emit(f"  phase  : {receipt.phase}")
    emit(f'  spoken : "{receipt.speak}"')
    if receipt.status == "GREEN":
        emit("")
        emit(f"that GREEN is not a status code: {runtime} echoed a one-time marker")
        emit("back, so it demonstrably received and processed the message.")
    _cleanup(emit, keep)
    return 0 if receipt.status == "GREEN" else 1


#: The one first-run screen the demo answers itself, recognised by exact text.
#: This is NOT the relay path growing a dialog-typing habit — pane_send still
#: refuses every dialog everywhere. It is the demo answering a menu in a session
#: the demo itself created seconds ago, choosing the option that changes nothing
#: ("Skip"), and only when the screen carries both of these literal lines.
#: Measured live: the "2" key alone selects; no Enter is sent, because Enter on a
#: menu picks whatever is highlighted.
_CODEX_UPDATE_MENU = ("Update now (runs `npm install -g", "2. Skip")


def _screen_tail(target_id: str) -> str:
    from .mcp.tmux import capture_pane

    # The CURRENT screen, not scrollback: after the menu is answered its text
    # scrolls up but survives in a deep capture, and the first version of this
    # judged that history as "still blocked".
    return "\n".join(capture_pane(target_id, 40).rstrip().splitlines()[-15:])


def _clear_known_first_gate(target_id: str, emit) -> bool:
    from .mcp.tmux import send_literal

    if not all(marker in _screen_tail(target_id) for marker in _CODEX_UPDATE_MENU):
        return False
    emit("codex is showing its update menu; choosing Skip (this changes nothing).")
    send_literal(target_id, "2")
    for _ in range(12):
        time.sleep(1.0)
        if not all(marker in _screen_tail(target_id) for marker in _CODEX_UPDATE_MENU):
            return True
    return False


def _wait_for_composer(backend: TmuxBackend, target_id: str, runtime: str, emit) -> bool:
    """Wait for the agent's empty composer, up to a bounded moment.

    A fresh agent spends its first seconds booting MCP servers and printing
    warnings; sending into that gets honestly refused. Waiting for the same
    EMPTY verdict the send path itself requires means the demo's first send is
    made at the same moment a careful operator would make it.
    """
    from .mcp.tmux import capture_pane
    from .prompt_state import EMPTY, detect_prompt_state

    for _ in range(20):
        if detect_prompt_state(capture_pane(target_id, 60), runtime) == EMPTY:
            return True
        time.sleep(1.0)
    emit(f"{runtime} never settled into an empty composer; sending anyway would be refused.")
    return False


def _cleanup(emit, keep: bool) -> None:
    if keep:
        emit(f"(kept: tmux -L {_SOCKET} attach)")
        return
    _tmux("kill-server")
    emit("(demo session cleaned up)")
