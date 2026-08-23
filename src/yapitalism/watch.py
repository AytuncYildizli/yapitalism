"""The wallet-approval catch, as a command anyone can run.

The first production watcher was an LLM on a cron: read the panes, notice an
agent silently holding an approval, message the phone. Its first real day it
found a coding agent that had been sitting on a wallet-transaction confirmation
for six hours with nobody aware. That pattern should not require running an
agent gateway, so this is the same loop with no model in it: poll, classify
with the detectors this package already trusts for refusing writes, and notify
on TRANSITIONS only.

Transitions, not states, because a watcher that repeats "still blocked" every
tick trains its operator to mute it — the same failure as a YELLOW spoken as
routine. A pane is announced when it BECOMES noteworthy, announced once more
when it recovers, and silent in between.

What it looks for, in order of urgency:

- ``blocked``   a recognised dialog owns the input line (trust prompt, auth,
                permission menu). A human decision is waiting.
- ``outage``    provider-failure text in the tail (Service Unavailable, rate
                limit, quota). Looks exactly like a wedged agent from outside;
                measured live on 2026-08-18.
- ``exited``    a pane whose runtime was an agent and now is not. A dead agent
                is not a quiet agent.
- ``occupied``  the composer has held text across two consecutive polls. One
                poll is someone typing; two is a message parked unsent.

This watcher READS and never writes. It cannot approve, clear, or answer
anything — by construction, not by promise: nothing in this module imports a
send path.
"""

from __future__ import annotations

import time
import urllib.request
from dataclasses import dataclass

from .mcp.backends.base import AGENT_RUNTIMES, BackendError
from .mcp.backends.tmux_backend import detect_blocking_prompt
from .prompt_state import HAS_TEXT, detect_prompt_state

#: Provider-failure lines, matched case-insensitively against the tail. Each one
#: was seen on a real pane or in a real provider's error vocabulary; keep this
#: list boring and literal.
_OUTAGE_MARKERS = (
    "service unavailable",
    "rate limit",
    "rate-limited",
    "overloaded",
    "quota exceeded",
    "connection refused",
    "internal server error",
)

#: How many trailing lines to judge. Same reasoning as the dialog window in the
#: send path: a dialog owns the screen NOW; deep scrollback is history.
_TAIL_LINES = 30


@dataclass(frozen=True, slots=True)
class Finding:
    target_id: str
    runtime: str
    place: str
    kind: str  # blocked | outage | exited | occupied | recovered
    detail: str

    def spoken(self) -> str:
        name = f"{self.place} içindeki {self.runtime}".strip()
        if self.kind == "blocked":
            return f"{name} bir onay bekletiyor: {self.detail}"
        if self.kind == "outage":
            return f"{name} sağlayıcı hatasında takılı: {self.detail}"
        if self.kind == "exited":
            return f"{name} kapanmış; panel duruyor, ajan yok."
        if self.kind == "occupied":
            return f"{name} prompt'unda gönderilmemiş metin bekliyor."
        return f"{name} düzeldi."


def classify_pane(
    runtime: str, text: str, *, was_occupied: bool
) -> tuple[str, str] | None:
    """One pane's noteworthy state, or None. Pure, so the tests stay pure."""
    tail = "\n".join(text.rstrip().splitlines()[-_TAIL_LINES:])
    lowered = tail.lower()
    blocking = detect_blocking_prompt(tail)
    if blocking:
        return "blocked", blocking
    for marker in _OUTAGE_MARKERS:
        if marker in lowered:
            return "outage", marker
    if detect_prompt_state(text, runtime) == HAS_TEXT:
        # Only after two consecutive polls: one poll is a person typing.
        if was_occupied:
            return "occupied", "prompt holds text across two polls"
        return None
    return None


class Watcher:
    """Holds last-known states so only transitions are reported."""

    def __init__(self) -> None:
        self._states: dict[str, str] = {}
        self._occupied_once: set[str] = set()
        self._runtimes: dict[str, str] = {}

    def observe(
        self, panes: list[dict[str, object]], read_pane
    ) -> list[Finding]:
        findings: list[Finding] = []
        seen: set[str] = set()
        for pane in panes:
            target = str(pane.get("target_id"))
            runtime = str(pane.get("runtime") or "unknown")
            place = str(pane.get("folder") or pane.get("project") or pane.get("label") or "")
            seen.add(target)

            previous_runtime = self._runtimes.get(target)
            self._runtimes[target] = runtime
            if (
                previous_runtime in AGENT_RUNTIMES
                and runtime not in AGENT_RUNTIMES
            ):
                findings.append(Finding(target, previous_runtime, place, "exited", ""))
                self._states[target] = "exited"
                continue
            if runtime not in AGENT_RUNTIMES:
                continue

            # The host's own word beats a screen heuristic: a Superset binding
            # whose last event is PermissionRequest/Elicitation is announced
            # without reading a single line.
            if str(pane.get("command")) == "waiting_input":
                state = "blocked"
                previous = self._states.get(target, "ok")
                self._states[target] = state
                if state != previous:
                    findings.append(
                        Finding(target, runtime, place, "blocked", "waiting_input")
                    )
                continue

            try:
                text = read_pane(target)
            except BackendError:
                continue

            verdict = classify_pane(
                runtime, text, was_occupied=target in self._occupied_once
            )
            if verdict is None and detect_prompt_state(text, runtime) == HAS_TEXT:
                self._occupied_once.add(target)
            elif verdict is None:
                self._occupied_once.discard(target)

            state = verdict[0] if verdict else "ok"
            previous = self._states.get(target, "ok")
            self._states[target] = state
            if state == previous:
                continue
            if verdict is not None:
                findings.append(Finding(target, runtime, place, *verdict))
            elif previous != "ok":
                findings.append(Finding(target, runtime, place, "recovered", previous))
        return findings


def notify(url: str, findings: list[Finding], *, timeout: float = 10.0) -> None:
    """POST the findings somewhere a phone can hear.

    Plain text body, one line per finding: that is exactly what ntfy.sh expects,
    and every generic webhook receiver can read it too. Deliberately not JSON by
    default — the consumer of this message is a person's notification tray.
    """
    body = "\n".join(f.spoken() for f in findings).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "text/plain; charset=utf-8"},
    )
    with urllib.request.urlopen(request, timeout=timeout):
        pass


def run_watch(
    *,
    interval: float,
    notify_url: str,
    once: bool,
    registry=None,
    sleeper=time.sleep,
    emit=print,
) -> int:
    """The loop. Read-only against the same registry the MCP server uses."""
    if registry is None:
        from .mcp.server import registry as live_registry

        registry = live_registry
    watcher = Watcher()

    def read_pane(target_id: str) -> str:
        return registry.resolve(target_id).read_pane(target_id, 100)

    while True:
        panes, _errors, _absent = registry.list_all()
        findings = watcher.observe(panes, read_pane)
        for finding in findings:
            emit(finding.spoken())
        if findings and notify_url:
            try:
                notify(notify_url, findings)
            except OSError as error:
                emit(f"bildirim gönderilemedi: {error}")
        if once:
            return 0
        sleeper(interval)
