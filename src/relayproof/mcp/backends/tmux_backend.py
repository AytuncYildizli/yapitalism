"""tmux backend.

Honest about what it cannot do. `tmux send-keys` has no notion of an expected
revision, no client-token dedup, and no way to refuse a write when the agent's
prompt already holds text — so all three capability flags are False. Runtime is
derived from the pane's process tree, which is weaker than a host registry
because a pane can change what it runs between the check and the write.
"""

from __future__ import annotations

from ..tmux import TmuxError, capture_pane, list_panes
from .base import BackendCapabilities, BackendError, BackendPane

_CAPABILITIES = BackendCapabilities(
    idempotent_dispatch=False,
    optimistic_revision=False,
    empty_prompt_check=False,
    runtime_detection="process_tree",
)


class TmuxBackend:
    @property
    def namespace(self) -> str:
        return "tmux"

    def capabilities(self) -> BackendCapabilities:
        return _CAPABILITIES

    def list_panes(self) -> list[BackendPane]:
        try:
            panes = list_panes()
        except TmuxError as error:
            raise BackendError(str(error)) from None
        return [
            BackendPane(
                target_id=pane.target_id,
                label=f"{pane.session_name}:{pane.window_index}.{pane.pane_index}",
                runtime=pane.runtime,
                width=pane.width,
                height=pane.height,
                dead=pane.dead,
                detail=pane.current_command,
            )
            for pane in panes
        ]

    def read_pane(self, target_id: str, lines: int) -> str:
        try:
            return capture_pane(target_id, lines)
        except TmuxError as error:
            raise BackendError(str(error)) from None
