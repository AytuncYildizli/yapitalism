"""Superset backend, over the local host-service.

Talks to `http://127.0.0.1:48900/trpc` directly. Superset's *hosted* MCP is not
in this path and cannot be: its `terminals_send` returns only
`{terminalId, submitted}`, with no phase and no await tool, so a voice turn
routed through it can never obtain proof. The host itself does carry the
receipt engine — this backend is how a verdict finally reaches it.

A manifest binds one terminal, but enumeration needs only its endpoint and
token — so a single-terminal manifest still sees every terminal on the host.
Each send and read is then bound to the requested (workspace, terminal) pair
rather than the manifest's own.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

from ...adapters.superset import SupersetAdapter, SupersetConfig, TrpcError
from ...prompt_state import EMPTY, detect_prompt_state
from .base import (
    AGENT_RUNTIMES,
    CLIENT,
    HOST,
    NONE,
    AcceptanceOutcome,
    BackendCapabilities,
    BackendError,
    BackendPane,
    SendOutcome,
)

_CACHE_DIR = Path.home() / ".cache" / "superset-watch-voice"
_MANIFEST_DEFAULT = _CACHE_DIR / "yapitalism-manifest.json"
# Deliberately NOT renamed: this is the filename an existing install actually
# has on disk. A blanket rebrand rewrote it once and silently broke the
# fallback, because both constants then pointed at a file that does not exist.
_MANIFEST_LEGACY = _CACHE_DIR / "relayproof-manifest.json"

# terminal.listSessions exists on every build, so the runtime comes from the
# host's own agent registry either way — unlike tmux's process scan, it can be
# trusted before a write.
_REGISTRY = "registry"

#: A host carrying the guarded `terminal.send`: it is given the expected
#: revision, a client token and an empty-prompt requirement, and it refuses the
#: write itself.
HOST_GUARDED = BackendCapabilities(
    idempotent_dispatch=HOST,
    optimistic_revision=HOST,
    empty_prompt_check=HOST,
    runtime_detection=_REGISTRY,
)

#: A stock host, which routes `terminal.writeInput` but not `terminal.send`.
#: These used to be declared HOST unconditionally, which promised every stock
#: user three guards their host had never heard of — the fake GREEN this project
#: exists to prevent, shipped by the project itself.
#:
#: The send still happens and the canary is still checked; all three guards move
#: to this process in a weaker but real form.
#:
#: empty_prompt_check was NONE here for one commit, on the reasoning that judging
#: emptiness from a screen dump is guessing. True, but it left the fallback
#: writing blind into an occupied prompt — which concatenates with the staged text
#: and submits the merge. An accurate label on a corrupting write is worse than a
#: heuristic that refuses, so it now judges and declines on anything short of a
#: confident EMPTY.
CLIENT_GUARDED = BackendCapabilities(
    idempotent_dispatch=CLIENT,
    optimistic_revision=CLIENT,
    empty_prompt_check=CLIENT,
    runtime_detection=_REGISTRY,
)

#: The host could not be asked. Claiming guards for a host that never answered
#: would be the same overclaim by a quieter route.
UNKNOWN_HOST = BackendCapabilities(
    idempotent_dispatch=NONE,
    optimistic_revision=NONE,
    empty_prompt_check=NONE,
    runtime_detection="unknown",
)


@dataclass(frozen=True, slots=True)
class _AcceptanceContext:
    """One send's proof context, kept only until that send's await consumes it.

    Bound to the operation rather than to the backend. The target is carried so an
    await can never be answered with a different pane's evidence, even if a caller
    passes a token that belongs elsewhere.
    """

    target_id: str
    baseline: object
    submitted_text: str


def resolve_manifest_path() -> Path:
    """Prefer the yapitalism-era manifest, accept one provisioned before it."""
    override = os.environ.get("YAPITALISM_SUPERSET_MANIFEST")
    if override:
        return Path(override)
    if _MANIFEST_DEFAULT.exists():
        return _MANIFEST_DEFAULT
    return _MANIFEST_LEGACY


def manifest_write_path() -> Path:
    """Where a newly provisioned manifest should go.

    Deliberately not `resolve_manifest_path()`. That function answers "where do I
    read from" and falls back to the pre-rebrand filename, which is right for
    reading and wrong for writing: on a machine with neither file — every new
    install — it would have `setup` create `relayproof-manifest.json`, naming a
    stranger's fresh install after a project name they have never seen.

    An existing legacy file is still written in place, so an upgrade refreshes the
    manifest it is already using instead of leaving a stale one beside a new one.
    """
    override = os.environ.get("YAPITALISM_SUPERSET_MANIFEST")
    if override:
        return Path(override)
    if not _MANIFEST_DEFAULT.exists() and _MANIFEST_LEGACY.exists():
        return _MANIFEST_LEGACY
    return _MANIFEST_DEFAULT


class SupersetBackend:
    def __init__(self, manifest_path: Path | str | None = None) -> None:
        self._manifest_path = Path(manifest_path) if manifest_path else resolve_manifest_path()
        self._adapter: SupersetAdapter | None = None
        # One entry per in-flight send, keyed by its client token, consumed by that
        # send's await. Replaces two singleton fields that any concurrent send
        # overwrote.
        self._contexts: dict[str, _AcceptanceContext] = {}
        self._workspace_of: dict[str, str] = {}
        self._adapters: dict[str, SupersetAdapter] = {}

    @property
    def namespace(self) -> str:
        return "superset"

    def capabilities(self) -> BackendCapabilities:
        """What THIS host enforces, asked once and cached by the adapter.

        Previously a module constant describing one machine's build. A receipt is
        only worth what the thing behind it checked, so the answer has to come
        from the host rather than from whoever wrote the constant.
        """
        try:
            adapter = self._connect()
            guarded = adapter.host_enforces_send_guards()
        except BackendError:
            return UNKNOWN_HOST
        return HOST_GUARDED if guarded else CLIENT_GUARDED

    def _connect(self) -> SupersetAdapter:
        # Built lazily and cached: constructing it reads a 0600 manifest, and a
        # missing manifest must surface as a backend error rather than stop the
        # whole server from starting.
        if self._adapter is None:
            try:
                self._adapter = SupersetAdapter(
                    SupersetConfig.from_manifest(self._manifest_path)
                )
            except (ValueError, OSError) as error:
                raise BackendError(f"superset manifest unusable: {error}") from None
        return self._adapter

    def _target_id(self, adapter: SupersetAdapter) -> str:
        return f"superset:{adapter.config.terminal_id}"

    def list_panes(self) -> list[BackendPane]:
        """Every terminal in every workspace on this host.

        The manifest binds one terminal, but only its endpoint and token are
        needed to enumerate — so a single-terminal manifest still sees the whole
        host. Workspaces with no terminals are skipped rather than listed empty.
        """
        adapter = self._connect()
        try:
            workspaces = adapter.list_workspaces()
        except (TrpcError, ValueError) as error:
            raise BackendError(str(error)) from None

        panes: list[BackendPane] = []
        self._workspace_of = {}
        for workspace in workspaces:
            workspace_id = workspace.get("id")
            if not isinstance(workspace_id, str) or not workspace_id:
                continue
            try:
                sessions = adapter.list_terminals(workspace_id)
            except (TrpcError, ValueError):
                # One unreadable workspace must not hide the rest of the host.
                continue
            name = str(workspace.get("name") or workspace_id[:8])
            project = str(workspace.get("projectName") or "")
            branch = str(workspace.get("branch") or "")
            worktree = str(workspace.get("worktreePath") or "")
            folder = worktree.rstrip("/").rsplit("/", 1)[-1] if worktree else ""
            for session in sessions:
                terminal_id = session.get("terminalId")
                if not isinstance(terminal_id, str) or not terminal_id:
                    continue
                agent = session.get("agent") if isinstance(session.get("agent"), dict) else {}
                state = str(session.get("state") or "unknown")
                self._workspace_of[terminal_id] = workspace_id
                panes.append(
                    BackendPane(
                        target_id=f"superset:{terminal_id}",
                        # Project first: it is the anchor a person names.
                        label=f"{project}/{name}" if project else name,
                        runtime=str(agent.get("runtime") or "unknown"),
                        width=0,
                        height=0,
                        dead=state == "exited",
                        detail=state,
                        project=project,
                        branch=branch,
                        folder=folder,
                        path=worktree,
                    )
                )
        return panes

    def _adapter_for(self, target_id: str) -> SupersetAdapter:
        """An adapter bound to one specific terminal.

        snapshot and send both address a (workspace, terminal) pair, so the
        manifest's own binding is replaced rather than assumed. An unknown
        terminal is refused instead of silently reading the manifest's one.
        """
        base = self._connect()
        terminal_id = target_id.removeprefix("superset:")
        workspace_id = self._workspace_of.get(terminal_id)
        if workspace_id is None:
            if terminal_id == base.config.terminal_id:
                return base
            raise BackendError(
                f"unknown superset terminal {terminal_id[:8]}; call panes_list first"
            )
        if (
            terminal_id == base.config.terminal_id
            and workspace_id == base.config.workspace_id
        ):
            return base
        cached = self._adapters.get(terminal_id)
        if cached is None:
            cached = SupersetAdapter(
                replace(base.config, terminal_id=terminal_id, workspace_id=workspace_id)
            )
            self._adapters[terminal_id] = cached
        return cached

    def read_pane(self, target_id: str, lines: int) -> str:
        try:
            return self._adapter_for(target_id).snapshot(max_lines=lines).text
        except (TrpcError, ValueError) as error:
            raise BackendError(str(error)) from None


    def clear_prompt(self, target_id: str, action: str = "escape") -> dict[str, object]:
        """Unstick a blocked prompt through the host's `terminal.writeInput`.

        Reports the same shape as the tmux backend so `pane_clear` stays one
        tool. Two fields differ in meaning and the difference is real:

        `pane_changed` here comes from the host's own revision counter rather
        than a screen diff — and that makes it weaker evidence, not stronger.
        Measured live: sending Escape to an idle Codex pane with an already-empty
        prompt still moved the revision 89209293 -> 89209557, because the status
        line ticks on its own. A running TUI is never byte-still, so on this
        backend `pane_changed` is close to always True and must not be read as
        "the clear did something". It is reported because its absence would be
        informative; its presence is not.

        `blocking_before` / `blocking_after` are empty strings rather than
        labels. tmux earns those by pattern-matching a screen dump, which this
        backend deliberately does not do — the host's prompt detector is the
        authority, and it only speaks through `send`. Claiming a recognised
        block here would be a weaker method wearing a stronger one's clothes.
        """
        adapter = self._adapter_for(target_id)
        try:
            result = adapter.clear_prompt(action)
        except (TrpcError, ValueError) as error:
            raise BackendError(str(error)) from None
        return {
            "action": result.action,
            "keys_sent": [result.action],
            "pane_changed": result.revision_after != result.revision_before
            or result.text_changed,
            "revision_before": result.revision_before,
            "revision_after": result.revision_after,
            "blocking_before": "",
            "blocking_after": "",
            "recognised_block_cleared": False,
            # Never a claim of emptiness — see ClearResult's docstring.
            "prompt_empty": None,
        }

    def send(
        self,
        target_id: str,
        text: str,
        *,
        canary: str | None,
        client_token: str,
    ) -> SendOutcome:
        """Confirmed send against the host's own guards.

        Unlike tmux, every guard here is real: the host is given the exact
        revision the baseline was read at, a stable client token, an
        empty-prompt requirement and a no-repeat flag, and it refuses the write
        itself if any of them no longer hold.
        """
        adapter = self._adapter_for(target_id)
        try:
            # BEFORE the write, not after. `dispatch` mutates the terminal, so a
            # runtime check on its RESPONSE would be too late - the shell would
            # already have run the text. tmux refuses up front; this path accepted
            # an injected `runtime="shell"` as a successful send, which turns a
            # misrouted voice turn into an executed command.
            #
            # listSessions is the host's own agent registry rather than a process
            # scan, so unlike tmux it can be trusted before a write.
            runtime = adapter.registry_runtime()
            if runtime not in AGENT_RUNTIMES:
                return SendOutcome(
                    phase="rejected_not_an_agent",
                    dispatched=False,
                    runtime=runtime,
                    reason=f"pane is running {runtime or 'something unrecognised'}",
                )
            baseline = adapter.snapshot(max_lines=1000)
            if canary is not None:
                adapter.validate_canary(
                    canary, baseline_text=baseline.text, submitted_text=text
                )
            # One POST. An ambiguous transport failure is surfaced, never retried.
            result = adapter.dispatch(
                text,
                expected_revision=baseline.revision,
                client_token=client_token,
                confirm=True,
            )
        except (TrpcError, ValueError) as error:
            raise BackendError(str(error)) from None
        if result.target_runtime not in AGENT_RUNTIMES:
            # Defence in depth: the pre-flight said agent and the host now says
            # otherwise, so the registry went stale in between. The write already
            # happened and cannot be recalled; refusing to REPORT it as delivered is
            # what is left, and it beats a receipt calling a shell write an
            # instruction the agent received.
            return SendOutcome(
                phase="rejected_not_an_agent",
                dispatched=False,
                runtime=result.target_runtime,
                reason="host reported a non-agent runtime after the write",
            )
        if result.dispatched:
            # Only a write that actually landed leaves acceptance context, and it is
            # keyed by the operation rather than stored on the backend.
            #
            # Both halves were bugs. The singleton fields meant a second send
            # overwrote the first's proof context before the first awaited, so a
            # concurrent A-send/B-send/A-await proved A's canary against B's
            # evidence - FastMCP runs sync tools on a threadpool, so that is a real
            # interleaving, not a theoretical one. And writing unconditionally meant
            # a REFUSED send clobbered the context of a successful one.
            self._contexts[client_token] = _AcceptanceContext(
                target_id=target_id, baseline=baseline, submitted_text=text
            )
        return SendOutcome(
            phase=result.phase,
            dispatched=result.dispatched,
            runtime=result.target_runtime,
            revision_before=result.revision_before,
            revision_after=result.revision_after,
            delivery_ref=result.delivery_id,
            # A refusal names what was wrong; only a dispatched send falls back to
            # the prompt caveat. Reporting "prompt_not_verified" for
            # rejected_prompt_not_empty read like a YELLOW footnote on a RED.
            reason=(
                self._refusal_reason(target_id, result.phase, baseline.text, result.target_runtime)
                if not result.dispatched
                else ("" if result.prompt_verified else "prompt_not_verified")
            ),
        )

    def _refusal_reason(
        self, target_id: str, phase: str, pane_text: str, runtime: str
    ) -> str:
        """Name the refusal, and flag the case where the host and the screen differ.

        Found by dogfooding through the running server. The host refused
        `rejected_prompt_not_empty` on an idle Codex pane whose prompt read
        `› Improve documentation in @filename`. That line is a placeholder, proved
        by `pane_clear` leaving it untouched, and the same client-guarded path
        judged it EMPTY and delivered a GREEN. So the host's detector counts
        Codex's own suggestion text as staged input.

        The consequence is not cosmetic. On the guarded path such a pane can never
        be written to, and the receipt was telling the operator to clear a prompt —
        advice that had already been shown not to work, because there is nothing
        there to clear. Distinguishing the two says the true thing instead.
        """
        if phase != "rejected_prompt_not_empty":
            return phase
        if detect_prompt_state(pane_text, runtime) == EMPTY:
            return "host_says_occupied_screen_says_empty"
        return phase

    def await_acceptance(
        self,
        target_id: str,
        canary: str | None,
        *,
        timeout: float = 8.0,
        client_token: str | None = None,
    ) -> AcceptanceOutcome:
        """Prove acceptance for ONE send, named by its client token.

        The token is how this await finds the baseline and submitted text of the
        send it belongs to. Without it there is nothing to prove against, and
        guessing "the most recent send" is what let a concurrent request answer
        with another operation's evidence.
        """
        if canary is None:
            return AcceptanceOutcome(False, 0, "no_canary")
        adapter = self._adapter_for(target_id)
        if client_token is None:
            raise BackendError("acceptance needs the client token of the send it proves")
        context = self._contexts.get(client_token)
        if context is None:
            raise BackendError("no acceptance context for this send in this process")
        if context.target_id != target_id:
            # A token belongs to one pane. Answering across panes would be the same
            # class of mix-up the shared singleton produced.
            raise BackendError("acceptance context belongs to a different pane")
        baseline = context.baseline
        try:
            result = adapter.await_canary(
                canary,
                # The full token, not the canary's last 8 characters. Two concurrent
                # sends whose canaries happened to share a suffix collided on the
                # host's own dedup - reintroducing the operation mix-up one layer
                # down, at the boundary meant to be authoritative.
                command_id=f"mcp-{client_token}",
                baseline_revision=baseline.revision,
                baseline_text=baseline.text,
                submitted_text=context.submitted_text,
                timeout=timeout,
            )
        except (TrpcError, ValueError) as error:
            raise BackendError(str(error)) from None
        return AcceptanceOutcome(
            result.observed, result.attempts, "" if result.observed else "canary_timeout"
        )
