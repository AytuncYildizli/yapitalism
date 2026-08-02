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
from dataclasses import replace
from pathlib import Path

from ...adapters.superset import SupersetAdapter, SupersetConfig, TrpcError
from .base import (
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

_CAPABILITIES = BackendCapabilities(
    idempotent_dispatch=True,
    optimistic_revision=True,
    empty_prompt_check=True,
    # terminal.listSessions reports the runtime from the host's own agent
    # registry, so unlike tmux's process scan it can be trusted before a write.
    runtime_detection="registry",
)


def resolve_manifest_path() -> Path:
    """Prefer the yapitalism-era manifest, accept one provisioned before it."""
    override = os.environ.get("YAPITALISM_SUPERSET_MANIFEST")
    if override:
        return Path(override)
    if _MANIFEST_DEFAULT.exists():
        return _MANIFEST_DEFAULT
    return _MANIFEST_LEGACY


class SupersetBackend:
    def __init__(self, manifest_path: Path | str | None = None) -> None:
        self._manifest_path = Path(manifest_path) if manifest_path else resolve_manifest_path()
        self._adapter: SupersetAdapter | None = None
        # await_canary needs the exact baseline the send was guarded by.
        self._baseline: object | None = None
        self._last_text: str | None = None
        self._workspace_of: dict[str, str] = {}
        self._adapters: dict[str, SupersetAdapter] = {}

    @property
    def namespace(self) -> str:
        return "superset"

    def capabilities(self) -> BackendCapabilities:
        return _CAPABILITIES

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
        self._baseline = baseline
        self._last_text = text
        return SendOutcome(
            phase=result.phase,
            dispatched=result.dispatched,
            runtime=result.target_runtime,
            revision_before=result.revision_before,
            revision_after=result.revision_after,
            delivery_ref=result.delivery_id,
            reason="" if result.prompt_verified else "prompt_not_verified",
        )

    def await_acceptance(
        self, target_id: str, canary: str | None, *, timeout: float = 8.0
    ) -> AcceptanceOutcome:
        if canary is None:
            return AcceptanceOutcome(False, 0, "no_canary")
        adapter = self._adapter_for(target_id)
        baseline = self._baseline
        if baseline is None:
            raise BackendError("no baseline from a prior send in this process")
        try:
            result = adapter.await_canary(
                canary,
                command_id=f"mcp-{canary[-8:]}",
                baseline_revision=baseline.revision,
                baseline_text=baseline.text,
                submitted_text=self._last_text or "",
                timeout=timeout,
            )
        except (TrpcError, ValueError) as error:
            raise BackendError(str(error)) from None
        return AcceptanceOutcome(
            result.observed, result.attempts, "" if result.observed else "canary_timeout"
        )
