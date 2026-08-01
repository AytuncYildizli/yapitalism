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
from .base import BackendCapabilities, BackendError, BackendPane

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
