"""Derives a monotonic revision for backends that have none.

tmux exposes no revision counter, but an expected-revision guard needs a
monotonically increasing integer. Reverting to earlier content must still
advance, or a guard could pass against stale state.
"""

from __future__ import annotations

from hashlib import sha256


class RevisionTracker:
    def __init__(self) -> None:
        self._state: dict[str, tuple[str, int]] = {}

    def observe(self, target_id: str, text: str) -> int:
        digest = sha256(text.encode("utf-8")).hexdigest()[:32]
        previous = self._state.get(target_id)
        if previous is None:
            self._state[target_id] = (digest, 1)
            return 1
        previous_digest, revision = previous
        if previous_digest == digest:
            return revision
        revision += 1
        self._state[target_id] = (digest, revision)
        return revision

    def current(self, target_id: str) -> int | None:
        found = self._state.get(target_id)
        return found[1] if found else None

    def forget(self, target_id: str) -> None:
        self._state.pop(target_id, None)
