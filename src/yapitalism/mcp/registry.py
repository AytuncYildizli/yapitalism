"""Routes a namespaced target id to the backend that owns it."""

from __future__ import annotations

from .backends.base import (
    BackendError,
    BackendPane,
    TerminalBackend,
    parse_target_id,
)


class BackendRegistry:
    def __init__(self, backends: list[TerminalBackend] | None = None) -> None:
        self._backends: dict[str, TerminalBackend] = {}
        for backend in backends or []:
            self.register(backend)

    def register(self, backend: TerminalBackend) -> None:
        namespace = backend.namespace
        if namespace in self._backends:
            raise BackendError(f"namespace already registered: {namespace}")
        self._backends[namespace] = backend

    @property
    def namespaces(self) -> tuple[str, ...]:
        return tuple(sorted(self._backends))

    def get(self, namespace: str) -> TerminalBackend | None:
        """Look a backend up by namespace, for calls that name no target yet.

        Creating a pane has no target id to route on, so it addresses a backend
        directly. Returns None rather than raising: "this machine cannot do
        that" is an answer, not a failure.
        """
        return self._backends.get(namespace)

    def resolve(self, target_id: str) -> TerminalBackend:
        namespace, _ = parse_target_id(target_id)
        backend = self._backends.get(namespace)
        if backend is None:
            known = ", ".join(self.namespaces) or "none"
            raise BackendError(f"no backend for '{namespace}'; registered: {known}")
        return backend

    def list_all(self) -> tuple[list[dict[str, object]], list[dict[str, str]]]:
        """Panes from every backend, plus per-backend errors.

        One failing backend must not hide the others, and it must not be
        silently omitted either — an empty list from a broken backend reads as
        "no terminals there", which is a different claim from "I could not look".
        """
        panes: list[dict[str, object]] = []
        errors: list[dict[str, str]] = []
        for namespace in self.namespaces:
            backend = self._backends[namespace]
            try:
                found = backend.list_panes()
            except BackendError as error:
                errors.append({"backend": namespace, "error": str(error)})
                continue
            capabilities = backend.capabilities()
            for pane in found:
                panes.append(_pane_payload(pane, namespace, capabilities.degraded))
        return panes, errors


def _pane_payload(
    pane: BackendPane, namespace: str, degraded: tuple[str, ...]
) -> dict[str, object]:
    payload: dict[str, object] = {
        "target_id": pane.target_id,
        "backend": namespace,
        "label": pane.label,
        "runtime": pane.runtime,
        "size": f"{pane.width}x{pane.height}",
        "dead": pane.dead,
    }
    if pane.detail:
        payload["command"] = pane.detail
    if pane.project:
        payload["project"] = pane.project
    if pane.branch:
        payload["branch"] = pane.branch
    if pane.folder:
        payload["folder"] = pane.folder
    if pane.path:
        payload["path"] = pane.path
    if degraded:
        # Surfaced per pane, not buried in a capabilities call the model may
        # never make.
        payload["missing_guarantees"] = list(degraded)
    return payload
