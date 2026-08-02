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
import time

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


#: How long a pane may sit unchanged before the wait is abandoned.
IDLE_TIMEOUT_SECONDS = 8.0
#: Absolute ceiling, however busy the agent looks.
MAX_WAIT_SECONDS = 180.0


def await_acceptance_patiently(
    backend: object,
    target_id: str,
    canary: str | None,
    *,
    idle_timeout: float = IDLE_TIMEOUT_SECONDS,
    max_wait: float = MAX_WAIT_SECONDS,
) -> AcceptanceOutcome:
    """Wait for proof for as long as the agent looks alive.

    A fixed deadline reports on the clock, not on the agent: an agent that
    thinks for thirty seconds and then answers correctly was verified all
    along, and calling that YELLOW is a false negative that trains an operator
    to ignore YELLOW. So the deadline resets whenever the pane changes, bounded
    by `max_wait` so a chatty pane cannot hold the turn open forever.

    Pane movement is used ONLY to decide whether to keep waiting. It is never
    evidence of acceptance — that remains the canary alone. This is the
    distinction ADR-0002 exists for, and widening the window must not widen
    what counts as proof.
    """
    if canary is None:
        return AcceptanceOutcome(False, 0, "no_canary")

    started = time.monotonic()
    attempts = 0
    previous: str | None = None
    changed_last_slice = False
    idle_deadline = started + idle_timeout
    hard_deadline = started + max_wait

    while True:
        now = time.monotonic()
        remaining = min(idle_deadline, hard_deadline) - now
        if remaining <= 0:
            break
        outcome = backend.await_acceptance(
            target_id, canary, timeout=min(remaining, 2.0)
        )
        attempts += outcome.attempts
        if outcome.observed:
            return AcceptanceOutcome(
                True, attempts, "", False, time.monotonic() - started
            )

        try:
            current = backend.read_pane(target_id, 1000)
        except BackendError:
            # Losing the ability to look is not proof of anything either way;
            # stop waiting and report honestly below.
            break
        changed_last_slice = previous is not None and current != previous
        if changed_last_slice:
            idle_deadline = time.monotonic() + idle_timeout
        previous = current
        # A backend whose await returns immediately would otherwise spin this
        # loop at full speed for the whole ceiling. Negligible against a real
        # two-second slice.
        time.sleep(0.05)

    waited = time.monotonic() - started
    reason = (
        "canary_timeout_agent_active" if changed_last_slice else "canary_timeout_idle"
    )
    return AcceptanceOutcome(False, attempts, reason, changed_last_slice, waited)


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

    `timeout_seconds` is an IDLE timeout, not a total one. The wait restarts
    whenever the pane changes, up to a hard ceiling, so an agent that thinks for
    a minute and then answers is still verified. Pane movement only decides
    whether to keep waiting; it never counts as acceptance.

    A YELLOW therefore carries which kind it is. `canary_timeout_agent_active`
    means the agent was still working when the wait ended — say that, and offer
    to check again. `canary_timeout_idle` means nothing moved at all, which is
    the one that usually means the text never landed anywhere useful.

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
            await_acceptance_patiently(
                backend, target_id, canary, idle_timeout=timeout_seconds
            )
            if outcome.dispatched
            else AcceptanceOutcome(False, 0, "not_dispatched")
        )
    except BackendError as error:
        return {"ok": False, "error": str(error), "target_id": target_id}

    receipt = build_receipt(outcome, acceptance, backend.capabilities())
    return {"ok": True, "target_id": target_id, "runtime": outcome.runtime, **receipt.as_dict()}

@mcp.tool
def panes_create(
    runtime: str,
    cwd: str,
    session_name: str = "",
) -> dict[str, object]:
    """Start a new agent in a fresh tmux session and report what actually runs.

    This STARTS A PROCESS. Name the runtime and the working directory in one
    sentence and get an explicit confirmation before calling it.

    `runtime` must be one of codex, claude, kimi. It selects a fixed launcher —
    there is no way to pass a command, arguments, or flags through this tool,
    and asking for one is a request to run arbitrary code by voice.

    `cwd` must already exist; it is the directory the agent will work in. Say it
    back to the operator before calling, because an agent started in the wrong
    repo will happily edit the wrong repo.

    Read the two flags separately and never merge them when speaking:
      - `created`           the session exists.
      - `runtime_confirmed` the agent is actually running in it.

    `created: true` with `runtime_confirmed: false` means an empty session is
    sitting there — usually a missing binary. Report it as "the session was
    created but <runtime> is not running in it", never as "started", and
    mention the pane so it can be cleaned up.

    Superset terminals cannot be created here; its host owns their lifecycle.
    """
    backend = registry.get("tmux")
    if backend is None or not hasattr(backend, "create_pane"):
        return {"ok": False, "error": "no backend on this machine can create panes"}

    name = session_name or f"yap-{runtime}-{uuid4().hex[:6]}"
    try:
        outcome = backend.create_pane(name, runtime, cwd)
    except BackendError as error:
        return {"ok": False, "error": str(error), "runtime": runtime, "cwd": cwd}

    speak = (
        f"{outcome.runtime_observed} calisiyor, pane {outcome.target_id}"
        if outcome.runtime_confirmed
        else (
            f"Oturum acildi ama {runtime} calistigi dogrulanamadi; "
            f"pane {outcome.target_id} bos olabilir"
        )
    )
    return {"ok": True, "speak": speak, **outcome.as_dict()}


if __name__ == "__main__":
    main()
