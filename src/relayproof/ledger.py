from __future__ import annotations

import json
import os
from pathlib import Path
from typing import cast

from .model import EvidenceEvent


class JsonlLedger:
    """Append-only local evidence ledger with restrictive file permissions."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(self, event: EvidenceEvent) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        # O_NOFOLLOW: the ledger is the evidence of record. Without it a symlink
        # planted at this path would redirect appends into another file and the
        # mode fix below would be applied to that target instead. The manifest
        # loader and the claim store already refuse to follow links; this is the
        # same rule applied to the one file that holds the receipts.
        descriptor = os.open(
            self.path,
            os.O_APPEND | os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW,
            0o600,
        )
        try:
            payload = json.dumps(event.as_dict(), sort_keys=True, separators=(",", ":"))
            encoded = (payload + "\n").encode("utf-8")
            written = 0
            while written < len(encoded):
                # A short write would leave a truncated row that no longer
                # parses as JSON, corrupting the ledger it was meant to extend.
                written += os.write(descriptor, encoded[written:])
            # fchmod, not chmod: operate on the descriptor already proven not to
            # be a symlink rather than re-resolving the path.
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)

    def read(self) -> tuple[dict[str, object], ...]:
        if not self.path.exists():
            return ()
        rows: list[dict[str, object]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(cast(dict[str, object], json.loads(line)))
        return tuple(rows)
