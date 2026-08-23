"""Routes a namespaced target id to the backend that owns it."""

from __future__ import annotations

from .backends.base import (
    BackendError,
    BackendPane,
    BackendUnavailable,
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

    def list_all(
        self,
    ) -> tuple[list[dict[str, object]], list[dict[str, str]], list[dict[str, str]]]:
        """Panes from every backend, plus what failed and what is simply absent.

        One failing backend must not hide the others, and it must not be
        silently omitted either — an empty list from a broken backend reads as
        "no terminals there", which is a different claim from "I could not look".

        Absence is a THIRD answer, and collapsing it into the second was measured
        to be the more common lie: on a machine with no Superset — nearly every
        machine — the Superset backend reported an error on every call, so a
        `panes_list` that had found the tmux panes perfectly still came back
        `ok: false`. "You do not have that" and "that is broken" are not the same
        sentence, and only one of them is anybody's problem.
        """
        panes: list[dict[str, object]] = []
        errors: list[dict[str, str]] = []
        unconfigured: list[dict[str, str]] = []
        for namespace in self.namespaces:
            backend = self._backends[namespace]
            try:
                found = backend.list_panes()
            except BackendUnavailable as absent:
                unconfigured.append({"backend": namespace, "detail": str(absent)})
                continue
            except BackendError as error:
                errors.append({"backend": namespace, "error": str(error)})
                continue
            capabilities = backend.capabilities()
            for pane in found:
                panes.append(_pane_payload(pane, namespace, capabilities.degraded))
        return panes, errors, unconfigured


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


class PeerRegistry:
    """Remote machines, resolvable by the namespace their panes carry.

    Kept apart from the backend registry on purpose: a backend answers the
    TerminalBackend protocol and this side builds its receipts; a peer answers
    whole TOOLS and its receipts pass through verbatim. Collapsing the two would
    invite exactly the restated-claim bug the split prevents.
    """

    def __init__(self) -> None:
        from ..peers import load_peers
        from .backends.remote_backend import RemotePeer

        self._peers = {peer.name: RemotePeer(peer) for peer in load_peers()}

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._peers))

    def owner_of(self, target_id: str):
        """The peer whose namespace prefixes this id, or None for local ids."""
        name = target_id.split(":", 1)[0]
        return self._peers.get(name)

    def all(self):
        return [self._peers[name] for name in self.names]
