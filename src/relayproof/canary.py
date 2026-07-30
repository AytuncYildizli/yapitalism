from __future__ import annotations

import re

_ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_WHITESPACE = re.compile(r"\s+")


def normalize_terminal_text(text: str) -> str:
    """Remove terminal decoration and wrapping whitespace for canary matching."""
    return _WHITESPACE.sub("", _ANSI_ESCAPE.sub("", text))


def canary_observed(terminal_text: str, canary: str) -> bool:
    """Return true only when the complete non-empty canary appears in visible text."""
    normalized_canary = normalize_terminal_text(canary)
    if not normalized_canary:
        raise ValueError("canary must not be empty")
    return normalized_canary in normalize_terminal_text(terminal_text)
