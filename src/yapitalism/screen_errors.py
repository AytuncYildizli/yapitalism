"""Failure lines an agent prints to its own screen, shared by every reader.

Born the night the first cross-machine send returned a generic "terminalde
hareket var ama doğrulayamadım" while the pane plainly said `API Error: 401 —
OAuth access token has expired · Please run /login`. The cause was on screen
and readable; the receipt just never looked. The send path and the watcher now
judge the same list, so a login drop or an outage is NAMED wherever a screen
is already being read.

Matching a marker is a claim about the SCREEN, never about the agent: the
operator's own message could contain the words "rate limit". Every consumer
must speak it as "the screen shows X", which stays true either way.
"""

from __future__ import annotations

AGENT_ERROR_MARKERS: tuple[str, ...] = (
    # Authentication — the agent is up but cannot reach its model.
    "please run /login",
    "oauth access token has expired",
    "authentication_error",
    "api error: 401",
    "invalid api key",
    # Availability — the provider or the network is the problem.
    "service unavailable",
    "rate limit",
    "rate-limited",
    "overloaded",
    "quota exceeded",
    "connection refused",
    "internal server error",
)

#: A marker owns the verdict only while it owns the screen; deep scrollback is
#: history. Same window the send path's dialog check uses.
_TAIL_LINES = 30


def find_agent_error(text: str, tail_lines: int = _TAIL_LINES) -> str:
    """The first known failure marker in the last `tail_lines` of `text`.

    Returns the marker itself (lowercase, short, safe to speak) or "".
    """
    tail = "\n".join(text.splitlines()[-tail_lines:]).lower()
    for marker in AGENT_ERROR_MARKERS:
        if marker in tail:
            return marker
    return ""
