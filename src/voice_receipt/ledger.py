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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            self.path,
            os.O_APPEND | os.O_CREAT | os.O_WRONLY,
            0o600,
        )
        try:
            payload = json.dumps(event.as_dict(), sort_keys=True, separators=(",", ":"))
            os.write(descriptor, (payload + "\n").encode("utf-8"))
        finally:
            os.close(descriptor)
        os.chmod(self.path, 0o600)

    def read(self) -> tuple[dict[str, object], ...]:
        if not self.path.exists():
            return ()
        rows: list[dict[str, object]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(cast(dict[str, object], json.loads(line)))
        return tuple(rows)
