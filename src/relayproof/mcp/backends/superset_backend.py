"""Superset backend, over the local host-service.

Talks to `http://127.0.0.1:48900/trpc` directly. Superset's *hosted* MCP is not
in this path and cannot be: its `terminals_send` returns only
`{terminalId, submitted}`, with no phase and no await tool, so a voice turn
routed through it can never obtain proof. The host itself does carry the
receipt engine — this backend is how a verdict finally reaches it.

One manifest binds one terminal, so `list_panes` reports that terminal alone.
Enumerating a workspace would need tRPC procedures this adapter does not
implement; claiming to list everything while showing one would be worse than
being narrow.
"""

from __future__ import annotations

import os
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
_MANIFEST_LEGACY = _CACHE_DIR / "relayproof-manifest.json"

_CAPABILITIES = BackendCapabilities(
    idempotent_dispatch=True,
    optimistic_revision=True,
    empty_prompt_check=True,
    # The host knows the runtime, but only reports it in a send response. It is
    # therefore unavailable while reading, which is a real asymmetry against
    # tmux: there, the process tree can be checked BEFORE writing. Do not call
    # this "registry" — that would imply a pre-write check this backend cannot
    # make.
    runtime_detection="host_on_dispatch",
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
        adapter = self._connect()
        try:
            snapshot = adapter.snapshot(max_lines=1)
        except TrpcError as error:
            raise BackendError(str(error)) from None
        return [
            BackendPane(
                target_id=self._target_id(adapter),
                label=f"superset terminal {adapter.config.terminal_id[:8]}",
                # Honest: the host reports runtime only on dispatch, so a read
                # cannot say what is running here.
                runtime="unknown",
                width=snapshot.cols,
                height=snapshot.rows,
                dead=False,
                detail=f"revision {snapshot.revision}",
            )
        ]

    def read_pane(self, target_id: str, lines: int) -> str:
        adapter = self._connect()
        expected = self._target_id(adapter)
        if target_id != expected:
            # This manifest binds exactly one terminal. Reading a different id
            # would silently return the wrong terminal's output.
            raise BackendError(
                f"this manifest binds {expected}; it cannot read {target_id}"
            )
        try:
            return adapter.snapshot(max_lines=lines).text
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
        adapter = self._connect()
        expected = self._target_id(adapter)
        if target_id != expected:
            raise BackendError(f"this manifest binds {expected}; it cannot send to {target_id}")
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
        adapter = self._connect()
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
