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

import argparse
import os
import sys
import time

import threading
from uuid import uuid4

from fastmcp import FastMCP

from .. import __version__
from .backends.base import AGENT_RUNTIMES, AcceptanceOutcome, BackendError
from .backends.superset_backend import SupersetBackend
from .backends.tmux_backend import TmuxBackend
from ..tokens import BoundedTokens
from .receipt import build_receipt, canary_instruction, new_canary
from .registry import BackendRegistry
from .resume import speak_fidelity


def _accepts_prompt_override(backend: object) -> bool:
    """Whether this backend's `send` takes the override at all."""
    import inspect

    try:
        return "override_host_prompt_check" in inspect.signature(backend.send).parameters
    except (TypeError, ValueError):
        return False

DEFAULT_HOST = "127.0.0.1"
# 8787 belongs to the launchd-managed mahmory-api; 8791 was also taken.
DEFAULT_PORT = 8792

#: `version` is not decoration: it is the answer to "which yapitalism am I talking
#: to", the first question anyone debugging a client asks. Left unset, FastMCP fills
#: serverInfo with its OWN version, so a 0.2.1 server introduced itself as 3.4.7 —
#: a number matching no release of this project, and one that changes when a
#: dependency updates.
mcp: FastMCP = FastMCP("yapitalism", version=__version__)
# Superset is registered unconditionally. Constructing it reads no files, and a
# missing or unusable manifest surfaces as a per-backend error in panes_list
# rather than preventing the server from starting or hiding tmux.
registry = BackendRegistry([TmuxBackend(), SupersetBackend()])
# Peers: other machines' yapitalism servers, from ~/.config/yapitalism/peers.json.
# Loaded once at startup like the backends; an empty or missing file is simply a
# machine with no fleet.
from .registry import PeerRegistry  # noqa: E402

peers = PeerRegistry()


def _forward_to_peer(target_id: str, tool: str, arguments: dict) -> dict | None:
    """If the id names a peer's pane, run the whole tool there.

    The receipt in the response is the PEER's receipt, untouched but for the
    target ids gaining the machine's name back. This side measured nothing and
    claims nothing - restating a remote verdict in local words would be the
    overclaim this project exists to refuse.
    """
    owner = peers.owner_of(target_id)
    if owner is None:
        return None
    forwarded = dict(arguments)
    forwarded["target_id"] = owner.strip(target_id)
    try:
        return owner.brand(owner.call_tool(tool, forwarded))
    except BackendError as error:
        return {"ok": False, "error": str(error), "target_id": target_id}


@mcp.tool
def panes_list() -> dict[str, object]:
    """List agent terminal panes on this machine, across every backend.

    Each pane carries:
      - `target_id`, namespaced (`tmux:%0`). Use it verbatim for any later call.
      - `runtime`: codex, claude, kimi, opencode, shell, or unknown. Anything that is not
        an agent runtime is NOT addressable — a pane that used to run an agent
        and now runs a plain shell would turn an instruction into a shell
        command.
      - `missing_guarantees`, when present: protections that backend cannot
        enforce. Never describe such a pane as being as safe as one without it.

    Naming a pane out loud: match on `project`, `folder` or `branch`, whichever
    the person actually said. These disagree more often than you would expect —
    project "yapitalism" sits in a folder called "relayproof", "Superset Watch
    Voice" in "superset-watchos-voice-spike" — so accept either word for the
    same pane. `label` carries a workspace name that is frequently meaningless
    ("dasendeha", "elo", "b"); prefer `project` and `folder` when speaking, and
    disambiguate with `runtime` when one project has several panes.

    `path` is the full directory. It is there to tell two identically named
    folders apart, not to be read aloud.

    `command` carries the pane's state. On Superset it is one of:
      - `idle`           accepts work now
      - `running`        busy, still accepts work
      - `waiting_input`  a prompt or menu is waiting on a human. A send WILL be
        refused. Do not send: read the pane, tell the operator what it is
        waiting for, and offer `pane_clear` — which now works on both backends.

    Checking this first is the difference between a refusal the operator has to
    decode and a sentence that tells them what to do.

    `errors` lists backends that could not be reached. A backend returning no
    panes and a backend that failed are different claims — do not report "no
    terminals" while `errors` is non-empty.

    `unconfigured` is a third and much duller thing: a backend this machine does
    not have. Almost nobody runs Superset, so its absence is the normal state and
    NOT a problem to report. Do not read it out, do not describe it as an error,
    and do not offer to fix it unless the operator asks what is missing. `ok`
    ignores it entirely.

    This is the local machine. Superset's `terminals_*` tools address
    Superset-managed PTYs instead.
    """
    panes, errors, unconfigured = registry.list_all()
    for peer in peers.all():
        # A configured peer that cannot answer is a FAILURE, not absence: the
        # operator wrote it into the peers file, so silence about it would hide
        # a machine they believe is watched.
        try:
            remote = peer.brand(peer.call_tool("panes_list", {}))
        except BackendError as error:
            errors.append({"backend": peer.namespace, "error": str(error)})
            continue
        for pane in remote.get("panes", []):
            pane["machine"] = peer.namespace
            panes.append(pane)
        for entry in remote.get("errors", []):
            errors.append(
                {"backend": f"{peer.namespace}:{entry.get('backend', '?')}",
                 "error": str(entry.get("error", ""))}
            )
    payload: dict[str, object] = {
        # Absence deliberately does not count. This was `not errors` with absence
        # folded into errors, so every machine without Superset got `ok: false`
        # while the tmux panes it asked for sat in the same response.
        "ok": not errors,
        "backends": list(registry.namespaces),
        "panes": panes,
        "errors": errors,
    }
    if unconfigured:
        payload["unconfigured"] = unconfigured
    return payload


@mcp.tool
def pane_read(target_id: str, lines: int = 200) -> dict[str, object]:
    """Read recent visible output from one pane.

    `target_id` comes from panes_list and must stay namespaced, e.g. `tmux:%0`.
    Read-only: this never types into the pane. Output is the pane as rendered,
    so it may hold wrapped lines, prompts and ANSI leftovers — summarize it
    rather than reading it aloud verbatim.
    """
    remote = _forward_to_peer(target_id, "pane_read", {"lines": lines})
    if remote is not None:
        return remote
    try:
        backend = registry.resolve(target_id)
        text = backend.read_pane(target_id, lines)
    except BackendError as error:
        return {"ok": False, "error": str(error), "target_id": target_id}
    return {"ok": True, "target_id": target_id, "text": text}


#: How long a pane may sit unchanged before the wait is abandoned.
IDLE_TIMEOUT_SECONDS = 8.0
#: Absolute ceiling. Deliberately short: this runs inside a synchronous voice
#: turn, and three minutes of dead air is a dead conversation. A pane that keeps
#: moving without ever emitting the canary should return an honest YELLOW
#: quickly so the operator can decide, not hold the turn open hoping.
MAX_WAIT_SECONDS = 30.0


def await_acceptance_patiently(
    backend: object,
    target_id: str,
    canary: str | None,
    *,
    idle_timeout: float = IDLE_TIMEOUT_SECONDS,
    max_wait: float = MAX_WAIT_SECONDS,
    client_token: str | None = None,
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
    last_change_at: float | None = None
    # Baseline BEFORE the first slice. Reading only afterwards meant the first
    # comparison needed a second slice, so any idle_timeout shorter than two
    # slices could never detect movement at all and always reported idle.
    try:
        previous: str | None = backend.read_pane(target_id, 1000)
    except BackendError:
        previous = None
    idle_deadline = started + idle_timeout
    hard_deadline = started + max_wait

    while True:
        now = time.monotonic()
        remaining = min(idle_deadline, hard_deadline) - now
        if remaining <= 0:
            break
        outcome = backend.await_acceptance(
            target_id,
            canary,
            timeout=min(remaining, 2.0),
            # Names WHICH send this proves. A backend that keyed proof on "the most
            # recent send" answered a concurrent request with another operation's
            # evidence.
            client_token=client_token,
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
        if previous is not None and current != previous:
            last_change_at = time.monotonic()
            idle_deadline = last_change_at + idle_timeout
        previous = current
        # A backend whose await returns immediately would otherwise spin this
        # loop at full speed for the whole ceiling. Negligible against a real
        # two-second slice.
        time.sleep(0.05)

    ended = time.monotonic()
    # "Recently" means inside the idle window, not merely "on the last poll".
    changed_recently = (
        last_change_at is not None and (ended - last_change_at) < idle_timeout
    )
    reason = (
        "canary_timeout_pane_moving" if changed_recently else "canary_timeout_pane_still"
    )
    return AcceptanceOutcome(False, attempts, reason, changed_recently, ended - started)


def _http_token_path() -> "os.PathLike[str]":
    from pathlib import Path

    configured = os.environ.get("XDG_STATE_HOME")
    root = Path(configured) if configured else Path.home() / ".local" / "state"
    return root / "yapitalism" / "http-token"


def load_or_create_http_token() -> str:
    """The HTTP transport's shared secret: read it, or mint it once.

    Generated on first HTTP start and persisted 0600, so every later start — and
    `yapitalism setup`, which prints the registration lines that carry it — sees
    the same value. Regenerating per boot would silently 401 every registered
    client after each restart, which is a outage shaped like a security feature.
    """
    import secrets
    from pathlib import Path

    path = Path(_http_token_path())
    try:
        existing = path.read_text().strip()
        if existing:
            return existing
    except OSError:
        pass
    token = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(token + "\n")
    return token


class LoopbackTokenVerifier:
    """Require one shared bearer token on the HTTP transport.

    Loopback is not a user boundary: 127.0.0.1 is reachable by every local
    process of every local user, and this server can type into any terminal its
    user can see. On a single-user machine that grants nothing new — any process
    running as you can already run `tmux send-keys` itself — but on a shared
    machine an open loopback port would let OTHER users' processes cross into
    yours. `YAPITALISM_MCP_TOKEN` closes exactly that hole.

    Deliberately not OAuth: the client and the server are the same person on the
    same machine, so a shared secret compared in constant time is the honest
    amount of ceremony. Subclassing FastMCP's TokenVerifier keeps the checking
    inside its auth middleware rather than in a bespoke ASGI layer.
    """

    def __new__(cls, token: str):  # pragma: no cover - thin composition shim
        import hmac

        from fastmcp.server.auth import AccessToken, TokenVerifier

        class _Verifier(TokenVerifier):
            async def verify_token(self, presented: str) -> AccessToken | None:
                if hmac.compare_digest(presented, token):
                    return AccessToken(
                        token=presented, client_id="yapitalism-local", scopes=[]
                    )
                return None

        return _Verifier()


def main(argv: list[str] | None = None) -> None:
    """Run the server over stdio or loopback HTTP.

    Two transports because MCP clients are split on how they start a server.
    Codex takes a URL (`codex mcp add --url`), so HTTP stays the default and an
    existing registration keeps working. Claude Desktop, Cursor and most of the
    directory listings instead spawn a process and speak over stdin/stdout, and
    without that this server simply cannot be used from them at all.
    """
    parser = argparse.ArgumentParser(prog="yapitalism-mcp", description=__doc__)
    parser.add_argument(
        "--stdio",
        action="store_true",
        help="speak MCP over stdin/stdout instead of binding a port "
        "(for clients that launch the server themselves)",
    )
    args = parser.parse_args(argv)

    transport = os.environ.get("YAPITALISM_MCP_TRANSPORT", "")
    if args.stdio or transport == "stdio":
        # Nothing but protocol may reach stdout here: FastMCP's startup banner
        # would be parsed as a message and break the session immediately.
        mcp.run(transport="stdio", show_banner=False)
        return

    host = os.environ.get("YAPITALISM_MCP_HOST", DEFAULT_HOST)
    port = int(os.environ.get("YAPITALISM_MCP_PORT", DEFAULT_PORT))
    if host not in {"127.0.0.1", "::1", "localhost"}:
        # Loopback by default — this process can read every terminal on the
        # machine. One widening, for fleets: an explicitly configured address in
        # tailscale's CGNAT range (100.64/10) may be bound, and ONLY with the
        # bearer gate active. The tailnet encrypts and authenticates the
        # transport; the token still decides who may call, because a tailnet can
        # contain machines that are not people you trust with your terminals.
        import ipaddress

        insecure = os.environ.get("YAPITALISM_MCP_INSECURE") == "1"
        try:
            bindable = ipaddress.ip_address(host) in ipaddress.ip_network("100.64.0.0/10")
        except ValueError:
            bindable = False
        if not bindable:
            raise SystemExit(f"refusing to bind a non-loopback host: {host}")
        if insecure:
            raise SystemExit(
                "refusing to bind a tailnet address with YAPITALISM_MCP_INSECURE=1: "
                "an open port on the tailnet is every tailnet device's port"
            )
    # Fail closed by default. Loopback is not a user boundary — 127.0.0.1 is
    # reachable by every local user's processes — and "auth exists but is off
    # unless you know the env var" was the last standing objection of the
    # strictest agent reviewer. The token is minted once and persisted, so
    # restarts do not rotate it; `yapitalism setup` prints the registration
    # lines that carry it. stdio needs none of this: the client spawns the
    # process, so the OS already decided who may talk to it.
    if os.environ.get("YAPITALISM_MCP_INSECURE") == "1":
        print(
            "yapitalism-mcp: YAPITALISM_MCP_INSECURE=1 — HTTP transport is open "
            "to all local users (see SECURITY.md)",
            file=sys.stderr,
        )
    else:
        from_env = os.environ.get("YAPITALISM_MCP_TOKEN", "")
        token = from_env or load_or_create_http_token()
        # The PATH is printed, never the token: launchd captures stderr to a
        # log file, and a secret in a log outlives every rotation policy. And the
        # message says where the token ACTUALLY came from — the first version
        # named the state file while serving one from the environment, which sent
        # a reader to a file that did not exist.
        source = (
            "from $YAPITALISM_MCP_TOKEN" if from_env else f"stored at {_http_token_path()}"
        )
        print(
            f"yapitalism-mcp: HTTP requests require a bearer token ({source}; "
            "`yapitalism setup` prints the client registration lines)",
            file=sys.stderr,
        )
        mcp.auth = LoopbackTokenVerifier(token)
    mcp.run(transport="http", host=host, port=port)



@mcp.tool
def pane_send(
    target_id: str,
    text: str,
    prove_acceptance: bool = True,
    timeout_seconds: float = 8.0,
    client_token: str = "",
    override_host_prompt_check: bool = False,
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
    whenever the pane changes. The default hard ceiling is 30 seconds; asking for
    a longer idle timeout also raises that ceiling to at least the requested value,
    so a slow runtime is not guaranteed to time out before its own idle window.
    Pane movement only decides whether to keep waiting; it never counts as acceptance.

    A YELLOW carries which kind it is, described by what was measured:
    `canary_timeout_pane_moving` means the pane's text was still changing —
    which a spinner, a clock or a second agent also produce, so say "there is
    movement", never "the agent is working", and offer to look again.
    `canary_timeout_pane_still` means nothing moved at all.

    Speak the returned `speak` value verbatim or more conservatively.
    `client_token` is how a RETRY stays one delivery instead of two. Leave it empty
    and each call mints a fresh one, which is right for a new instruction and wrong
    for re-sending after an ambiguous failure: a guarded host deduplicates on this
    token, and a fresh token per attempt means it never sees the repeat. If a
    previous `pane_send` came back with an error or an unproven YELLOW and the same
    message is being sent again, pass the SAME token as the first attempt.

    `override_host_prompt_check` exists for exactly one measured defect and must
    not be used for anything else.

    A Superset host counts an agent's own placeholder suggestion as staged input,
    so `pane_send` to such a pane returns RED `rejected_prompt_not_empty` forever
    and `pane_clear` cannot help — there is nothing in the prompt to clear. When
    that happens the receipt says so, with reason
    `host_says_occupied_screen_says_empty`.

    **Only set this after the operator has heard that sentence and said to send
    anyway, in words, for this pane.** Do not set it speculatively, do not set it
    because a previous pane needed it, and do not set it on any other refusal. The
    fact this turns on — whether that text is the agent's own suggestion or
    something a person typed and walked away from — is not in any screen; it is in
    the operator's head. Asking is the only way to read it.

    The write still goes through the guarded host path with the same expected
    revision and a client token, so a stale terminal is still refused by the host.
    Only the prompt verdict moves to this side, and the receipt reports
    `empty_prompt_check: client` for that send while the other two stay `host`.
    """
    remote = _forward_to_peer(
        target_id,
        "pane_send",
        {
            "text": text,
            "prove_acceptance": prove_acceptance,
            "timeout_seconds": timeout_seconds,
            "client_token": client_token,
            "override_host_prompt_check": override_host_prompt_check,
        },
    )
    if remote is not None:
        # The peer minted the canary next to its own terminal and built this
        # receipt itself; it comes back verbatim, ids re-namespaced.
        return remote
    try:
        backend = registry.resolve(target_id)
    except BackendError as error:
        return {"ok": False, "error": str(error), "target_id": target_id}

    canary = new_canary() if prove_acceptance else None
    payload = text if canary is None else f"{text}\n\n{canary_instruction(canary)}"
    token = client_token or str(uuid4())
    send_kwargs: dict[str, object] = {}
    if override_host_prompt_check:
        # Only offered where it means something. A backend without the
        # parameter would silently ignore it, and an ignored override that the
        # operator was asked to authorise is worse than an unavailable one.
        if not _accepts_prompt_override(backend):
            return {
                "ok": False,
                "target_id": target_id,
                "error": (
                    f"the {backend.namespace} backend has no host prompt check to "
                    "override; its refusal came from this side"
                ),
            }
        send_kwargs["override_host_prompt_check"] = True

    # Only the SEND is allowed to fail into a bare error, because only before the
    # send is "nothing happened" true.
    try:
        outcome = backend.send(
            target_id, payload, canary=canary, client_token=token, **send_kwargs
        )
    except BackendError as error:
        return {"ok": False, "error": str(error), "target_id": target_id}

    if not outcome.dispatched:
        acceptance = AcceptanceOutcome(False, 0, "not_dispatched")
    else:
        try:
            acceptance = await_acceptance_patiently(
                backend,
                target_id,
                canary,
                idle_timeout=timeout_seconds,
                # A caller asking for a longer idle window must not hit the hard
                # ceiling before that window can elapse. Defaults remain bounded
                # at 30 seconds; slow runtimes opt in by raising timeout_seconds.
                max_wait=max(MAX_WAIT_SECONDS, timeout_seconds),
                client_token=token,
            )
        except Exception as error:
            # Deliberately every exception, not just BackendError. The write is
            # already in the terminal; what failed is watching it. A TypeError from a
            # backend whose signature drifted lost a delivered message this way -
            # crashed after the send, returned a generic tool error, and the operator
            # could not tell it from "nothing happened". Whatever the cause, an
            # unobserved delivery is exactly what YELLOW is for, and the evidence
            # that the write landed must survive the thing that failed to watch it.
            acceptance = AcceptanceOutcome(
                False, 0, f"acceptance_observation_failed: {type(error).__name__}: {error}"
            )

    # THIS write's guarantees, not the backend's standing ones. `SendOutcome`
    # carries an override for exactly the case where they differ — a send that
    # overruled the host's prompt check enforces one guarantee fewer — and the
    # field was being set and never read, so the receipt reported the standing
    # answer anyway. Its own docstring calls that "a lie shaped exactly like the
    # one the enforcement levels exist to prevent".
    receipt = build_receipt(
        outcome,
        acceptance,
        outcome.capabilities_override or backend.capabilities(),
        clearing_known_useless=_clearing_is_known_useless(target_id),
    )
    return {"ok": True, "target_id": target_id, "runtime": outcome.runtime, **receipt.as_dict()}

def _spoken_pane_name(cwd: str, runtime: str) -> str:
    """How a person refers to a pane out loud.

    Never the target id. `tmux:%6` reads aloud as "tmux percent six", and nobody
    says "pane" either — they say "the codex in relayproof". `panes_list`'s docstring
    already tells the model to name panes by project or folder; these sentences were
    contradicting it in the one place the operator actually hears.

    The id stays in the payload, where the model needs it to make the next call.
    """
    folder = cwd.rstrip("/").rsplit("/", 1)[-1] if cwd else ""
    return f"{folder} klasöründeki {runtime}" if folder else runtime


#: Panes where a clear was attempted and the screen did not move. Bounded and
#: guarded, because FastMCP runs sync tools on a threadpool.
#:
#: This exists because the tool was offering a remedy that does not work, and then
#: offering it again. A wedged agent ignores Escape and C-u; `pane_clear` says so
#: honestly, and the NEXT refusal repeated "I can clear it and retry" as if nothing
#: had been learned. Honest sentences in sequence can still add up to a loop the
#: operator cannot leave — and a voice operator has no other way out.
_clears_that_changed_nothing = BoundedTokens(capacity=256)
_clear_memory_guard = threading.Lock()


def _remember_clear_outcome(target_id: str, changed: bool) -> None:
    with _clear_memory_guard:
        if changed:
            # It responded to keys this time, so the next refusal may offer the
            # clear again. Forgetting on success matters as much as remembering on
            # failure: a pane that recovers must not be described as stuck forever.
            _clears_that_changed_nothing.discard(target_id)
        else:
            _clears_that_changed_nothing.add(target_id)


def _clearing_is_known_useless(target_id: str) -> bool:
    with _clear_memory_guard:
        return target_id in _clears_that_changed_nothing


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

    if outcome.blocked_on:
        # Confirmed running and still unable to take work. Found on the first
        # live run: an agent parked on a trust dialog swallows the first
        # instruction, and the send that follows returns an honest YELLOW whose
        # cause is invisible unless this is said out loud.
        speak = (
            f"{_spoken_pane_name(cwd, runtime)} başladı ama bir onay ekranında "
            f"bekliyor ({outcome.blocked_on}); iş göndermeden önce orayı geçmek "
            "gerekiyor."
        )
    elif outcome.runtime_confirmed:
        speak = f"{_spoken_pane_name(cwd, outcome.runtime_observed)} hazır."
    else:
        speak = (
            f"Oturum açıldı ama {runtime} çalıştığı doğrulanamadı; "
            f"{_spoken_pane_name(cwd, runtime)} boş olabilir, temizlenmesi gerekebilir."
        )
    return {"ok": True, "speak": speak, **outcome.as_dict()}


@mcp.tool
def panes_resume(
    runtime: str,
    cwd: str,
    session_id: str = "",
    session_name: str = "",
) -> dict[str, object]:
    """Bring a dead agent back in a fresh tmux session, and say how faithfully.

    This is a WRITE that starts a process. Name the runtime and the directory in
    one sentence and get an explicit confirmation before calling it.

    `runtime` is one of codex, claude, kimi and selects a fixed resume form — as
    with `panes_create` there is no way to pass a command or flags, because asking
    for one is a request to run arbitrary code by voice.

    `session_id` is optional and it changes what can honestly be claimed:

      - given    `fidelity: "exact"` — that recorded session was asked for.
      - omitted  `fidelity: "last"`  — the runtime was asked for its most recent
        session. That is NOT a guarantee it is the one the operator meant.

    **Never speak `last` as "I resumed your session".** Say the most recent one was
    asked for and that it may not be the right conversation. An operator who
    believes the wrong thing came back will send follow-ups into a stranger's
    context.

    A `session_id` that fails validation is refused rather than downgraded to
    `last`. Silently resuming something else would come up looking correct.

    `blocked_on` and `runtime_confirmed` mean exactly what they mean for
    `panes_create`: a resumed agent draws the same trust prompt and update menu a
    fresh one does, so read them before sending work.
    """
    backend = registry.get("tmux")
    if backend is None or not hasattr(backend, "resume_pane"):
        return {"ok": False, "error": "no backend here can resume an agent"}

    name = session_name or f"yap-{runtime}-{uuid4().hex[:8]}"
    try:
        outcome = backend.resume_pane(name, runtime, cwd, session_id or None)
    except BackendError as error:
        return {"ok": False, "error": str(error), "runtime": runtime, "cwd": cwd}

    fidelity_line = speak_fidelity(outcome.fidelity)
    if not outcome.runtime_confirmed:
        speak = (
            f"Oturum oluşturuldu ama {runtime} içinde çalışmıyor: "
            f"{_spoken_pane_name(cwd, runtime)}. Temizlenmesi gerekiyor."
        )
    elif outcome.blocked_on:
        speak = (
            f"{_spoken_pane_name(cwd, runtime)} geri geldi ({fidelity_line}) ama bir "
            f"ekranda bekliyor ({outcome.blocked_on}); iş göndermeden önce orayı "
            "geçmek gerekiyor."
        )
    else:
        speak = f"{_spoken_pane_name(cwd, runtime)} geri geldi: {fidelity_line}."
    return {"ok": True, "speak": speak, **outcome.as_dict()}


def _pane_runtime(backend: object, target_id: str) -> str:
    """What the backend says is running in one pane, right now.

    Read from a fresh listing rather than a cache: the whole point of the check is
    that a pane can stop being an agent between one call and the next.
    """
    for pane in backend.list_panes():
        if pane.target_id == target_id:
            return pane.runtime
    raise BackendError(f"pane {target_id} is not listed by its backend")


@mcp.tool
def pane_clear(target_id: str, action: str = "escape") -> dict[str, object]:
    """Try to unstick a pane whose prompt is blocking a send.

    For when `pane_send` came back RED with `rejected_prompt_not_empty` (text is
    already sitting in the box) or `rejected_prompt_unreadable` (a menu or
    overlay is covering it).

    `action` is one of a fixed set — there is no way to send an arbitrary key:
      - `escape`        dismiss a dialog or cancel the current input
      - `clear-line`    empty an input that already holds text
      - `escape-twice`  for TUIs that need to leave an inner mode first

    Enter is not in that set and never will be. Escape cancels, Enter commits:
    on a menu, Enter picks whatever is highlighted, which is how a stray
    keystroke runs an install command.

    This does NOT report that the prompt is now empty, on either backend. tmux
    cannot verify it at all, and Superset's prompt detector runs inside `send`
    rather than `snapshot`, so neither can honestly answer the question here. It
    reports what was sent and whether the pane changed. **The proof that clearing
    worked is the next `pane_send` returning GREEN** — so clear, then send, and
    speak the send's receipt as the verdict. Never tell the operator the prompt
    is clear on the strength of this call alone.

    Works on both backends, with an asymmetry worth knowing. On Superset the
    write goes through `terminal.writeInput`, which — unlike `terminal.send` —
    the host exposes with no revision check and no token dedup. So the strongest
    backend has the weakest guarantee for exactly this one operation, and
    `pane_changed` there is read from the host's revision counter rather than a
    screen diff.
    """
    remote = _forward_to_peer(target_id, "pane_clear", {"action": action})
    if remote is not None:
        return remote

    try:
        backend = registry.resolve(target_id)
    except BackendError as error:
        return {"ok": False, "error": str(error), "target_id": target_id}

    if not hasattr(backend, "clear_prompt"):
        return {
            "ok": False,
            "error": (
                f"the {backend.namespace} backend cannot clear a prompt; its host "
                "exposes no procedure for it. Clear it at the machine."
            ),
            "target_id": target_id,
        }

    # Same boundary as `pane_send`, and it was missing here. Clearing writes control
    # bytes into a terminal; on a pane that is running a plain shell those are keys a
    # shell interprets. Fixing the send path alone left the other voice-reachable
    # mutation ungated, which is how a "fixed" boundary keeps leaking.
    try:
        runtime = _pane_runtime(backend, target_id)
    except BackendError as error:
        return {"ok": False, "error": str(error), "target_id": target_id}
    if runtime not in AGENT_RUNTIMES:
        return {
            "ok": False,
            "target_id": target_id,
            "runtime": runtime,
            "error": (
                f"that pane is running {runtime or 'something unrecognised'}, not an "
                "agent; clearing it would send control keys to a shell"
            ),
        }

    try:
        result = backend.clear_prompt(target_id, action)
    except BackendError as error:
        return {"ok": False, "error": str(error), "target_id": target_id}

    _remember_clear_outcome(
        target_id, bool(result["recognised_block_cleared"] or result["pane_changed"])
    )
    if result["recognised_block_cleared"]:
        speak = (
            f"{result['blocking_before']} ekranı kapandı. Şimdi gönderebiliriz."
        )
    elif result["pane_changed"]:
        speak = (
            "Tuşu gönderdim, terminalde bir şey değişti ama prompt'un boşaldığını "
            "doğrulayamam. Denemek için gönderebiliriz."
        )
    else:
        speak = (
            "Tuşu gönderdim ama terminalde hiçbir şey değişmedi; muhtemelen "
            "işe yaramadı."
        )
    return {"ok": True, "target_id": target_id, "speak": speak, **result}


if __name__ == "__main__":
    main()
