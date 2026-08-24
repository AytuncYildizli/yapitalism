"""Names a person can say out loud, bound to panes they actually verified.

Voice cannot say `mbp3:tmux:%2`. A name maps a word to a target id — and to
what was MEASURED about that pane when it was named (runtime, folder), because
tmux reuses pane ids: `%2` today may be a different agent, or a shell, next
week. Resolution therefore re-verifies the binding against a live listing and
refuses a stale name rather than retargeting: sending "billing-codex" into
whatever now wears its old id would be exactly the wrong-agent write the
runtime gate exists to stop.

No fuzzy matching, ever. An exact unique name resolves; anything else is a
refusal that lists what exists. "Probably the right codex" is not a target.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
#: Words that already route somewhere: backend namespaces here, peer names at
#: validation time. A name shadowing either would make one spoken word mean
#: two different panes depending on which code path heard it.
_RESERVED = frozenset({"tmux", "superset"})

_MAX_BYTES = 64 * 1024


def names_path() -> Path:
    configured = os.environ.get("YAPITALISM_NAMES")
    if configured:
        return Path(configured)
    root = os.environ.get("XDG_CONFIG_HOME")
    base = Path(root) if root else Path.home() / ".config"
    return base / "yapitalism" / "names.json"


def validate_name(name: str, *, peer_names: tuple[str, ...] = ()) -> str:
    """The name, or a sentence saying why it cannot be one. Empty string = valid."""
    if not _NAME.fullmatch(name):
        return (
            "a name is 1-32 characters of lowercase letters, digits and "
            "hyphens, starting with a letter or digit"
        )
    if name in _RESERVED or name in peer_names:
        return f"'{name}' already routes somewhere (a backend or peer namespace)"
    return ""


def load_names() -> dict[str, dict[str, str]]:
    """The names table. Missing file is an empty table; a corrupt one raises.

    Corruption raises rather than returning {} because "your names all
    vanished" and "your names file is broken" are different problems, and only
    one of them should be repaired by re-naming everything.
    """
    path = names_path()
    try:
        raw = path.read_text()
    except OSError:
        return {}
    if len(raw.encode()) > _MAX_BYTES:
        raise ValueError(f"names file too large: {path}")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"names file must hold an object: {path}")
    table: dict[str, dict[str, str]] = {}
    for name, entry in data.items():
        if not isinstance(entry, dict) or "target_id" not in entry:
            raise ValueError(f"name '{name}' is missing its target_id: {path}")
        table[name] = {
            "target_id": str(entry["target_id"]),
            "runtime": str(entry.get("runtime", "")),
            "folder": str(entry.get("folder", "")),
        }
    return table


def save_name(name: str, entry: dict[str, str]) -> Path:
    table = load_names()
    table[name] = entry
    return _write(table)


def remove_name(name: str) -> bool:
    table = load_names()
    if name not in table:
        return False
    del table[name]
    _write(table)
    return True


def _write(table: dict[str, dict[str, str]]) -> Path:
    path = names_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(table, indent=2, sort_keys=True) + "\n")
    path.chmod(0o600)
    return path
