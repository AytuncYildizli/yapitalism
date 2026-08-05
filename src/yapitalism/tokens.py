"""A bounded record of client tokens whose write has already gone out.

Both backends deduplicate sends on their own side, because neither `tmux
send-keys` nor `terminal.writeInput` will do it for them. That was a plain `set`,
which is wrong for a service rather than a script: this server runs under launchd
for weeks, so the set only ever grows, and nothing ever becomes reusable.

Bounded by insertion order and small on purpose. The repeats this protects against
are a retried voice turn or a client resend — they arrive seconds apart, not days —
so a few thousand entries is far more history than the guarantee needs, and
forgetting the oldest is the correct trade rather than a leak with extra steps.
"""

from __future__ import annotations

from collections import OrderedDict

#: Chosen for the shape of the traffic, not for a memory target. A voice turn
#: produces one token; nothing legitimate replays a token from 2000 sends ago.
DEFAULT_CAPACITY = 2048


class BoundedTokens:
    """Membership with a ceiling. Oldest entries are dropped first."""

    __slots__ = ("_seen", "_capacity")

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity < 1:
            raise ValueError("capacity must be a positive integer")
        self._capacity = capacity
        self._seen: OrderedDict[str, None] = OrderedDict()

    def add(self, token: str) -> None:
        # Re-adding moves nothing: a token's age is when it was first used, so a
        # refused duplicate must not extend the lifetime of the entry that
        # refused it.
        if token not in self._seen:
            self._seen[token] = None
            while len(self._seen) > self._capacity:
                self._seen.popitem(last=False)

    def discard(self, token: str) -> None:
        self._seen.pop(token, None)

    def __contains__(self, token: object) -> bool:
        return token in self._seen

    def __len__(self) -> int:
        return len(self._seen)
