"""Hermes agents as task targets, through the interface Hermes itself supports.

Hermes ships a first-party peer surface — `hermes peer add/list/run/status` —
where the USER registers gateways they are authorized to reach and Hermes
stores the credentials. This module shells out to that surface and nothing
else: no gateway protocol is re-implemented here, no credential is read or
stored on this side, and whatever peers exist are the ones the user added.
Nothing is hardcoded to any machine or bot.

The task shape is `peer run` (an asynchronous turn with its own run ID), never
`peer dm` (which delivers into the agent's canonical Bot Chat): a task
submitted through here must not land in the middle of somebody's ongoing
WhatsApp conversation.

Two guards this side owns:

- **Duplicate suppression.** The caller's task key becomes Hermes's
  `--idempotency-key`, and a key this process has already dispatched is
  refused locally too — same rule as `pane_send`'s client token.
- **Loop damping.** Every submitted task is prefixed with a marker line, and a
  task whose text already CARRIES the marker is refused. A Hermes agent whose
  reply is piped back into this tool by another bot hits the marker on the
  second hop, which turns an infinite loop into one refused call. This is a
  depth-1 damper, not a distributed-cycle detector, and it is documented as
  exactly that.

Status words come from Hermes's own answer or they are not claimed. A run ID
means ACCEPTED — never "completed"; an HTTP round-trip or a spawned process is
not a result.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

_TIMEOUT_SECONDS = 60
_LOOP_MARKER = "[relayed-by-yapitalism]"

#: Hermes status words this side recognises, mapped to the four states a
#: client may act on. Anything not listed stays "unknown" and carries the raw
#: answer — guessing a terminal state from an unrecognised word is how
#: "completed" gets spoken about a job that is stuck.
_STATUS_MAP = {
    "pending": "accepted",
    "queued": "accepted",
    "accepted": "accepted",
    "running": "working",
    "in_progress": "working",
    "working": "working",
    "completed": "completed",
    "done": "completed",
    "succeeded": "completed",
    "failed": "blocked",
    "error": "blocked",
    "blocked": "blocked",
    "stopped": "blocked",
    "cancelled": "blocked",
}


class HermesUnavailable(RuntimeError):
    """The hermes CLI is not on this machine (or not findable)."""


def hermes_binary() -> str:
    """The hermes CLI, found the way tmux is found: PATH, then real homes.

    A launchd-hosted server gets a minimal PATH; the fallback list keeps a
    working install visible without a restart.
    """
    found = shutil.which("hermes")
    if found:
        return found
    for candidate in (
        os.path.expanduser("~/.local/bin/hermes"),
        "/opt/homebrew/bin/hermes",
        "/usr/local/bin/hermes",
    ):
        if os.access(candidate, os.X_OK):
            return candidate
    raise HermesUnavailable("the hermes CLI is not installed on this machine")


def _run(args: list[str], stdin_text: str | None = None) -> tuple[int, str, str]:
    completed = subprocess.run(
        [hermes_binary(), *args],
        input=stdin_text,
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_SECONDS,
        check=False,
    )
    return completed.returncode, completed.stdout, completed.stderr


def list_peers() -> list[dict[str, str]]:
    """The Hermes gateways the user has registered. Empty list means none.

    Parses `hermes peer list`'s tab-separated rows (`name\turl\t[key ...]`),
    observed live 2026-09-06. A row that does not parse is carried as raw
    text rather than dropped — an unparsed peer must not become an invisible
    one.
    """
    _, stdout, _ = _run(["peer", "list"])
    if "No peers registered" in stdout:
        return []
    peers: list[dict[str, str]] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) >= 2:
            peers.append({"name": parts[0].strip(), "url": parts[1].strip()})
        else:
            peers.append({"raw": line.strip()})
    return peers


def carries_loop_marker(text: str) -> bool:
    return _LOOP_MARKER in text


def mark_task(text: str) -> str:
    """Prefix the loop marker. The remote agent sees where the task came from."""
    return f"{_LOOP_MARKER}\n{text}"


def _parse_json_tail(stdout: str) -> dict[str, object] | None:
    """The JSON object in the CLI's stdout, tolerating warning lines above it."""
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                parsed = json.loads(line)
            except ValueError:
                continue
            if isinstance(parsed, dict):
                return parsed
    return None


def submit_task(target: str, text: str, idempotency_key: str) -> dict[str, object]:
    """`hermes peer run --json` — returns ACCEPTED with a run id, or why not.

    The message travels on stdin, never argv: argv is visible to every local
    process listing and has length limits a real task brief will exceed.
    """
    code, stdout, stderr = _run(
        ["peer", "run", "--json", "--idempotency-key", idempotency_key, target, "-"],
        stdin_text=mark_task(text),
    )
    parsed = _parse_json_tail(stdout)
    if parsed is None:
        detail = (stdout + "\n" + stderr).strip()[:400]
        return {
            "ok": False,
            "state": "not_submitted",
            "error": detail or f"hermes peer run said nothing (exit {code})",
        }
    run_id = str(parsed.get("run_id") or parsed.get("id") or "")
    if not run_id:
        return {
            "ok": False,
            "state": "not_submitted",
            "error": "hermes answered without a run id",
            "hermes": parsed,
        }
    # A run id is ACCEPTANCE, nothing more. The task may still fail, block, or
    # be doing the wrong thing entirely; hermes_task_status is where truth
    # accumulates.
    return {
        "ok": True,
        "state": "accepted",
        "run_id": run_id,
        "idempotency_key": idempotency_key,
        "hermes": parsed,
    }


def task_status(target: str, run_id: str) -> dict[str, object]:
    """`hermes peer status --json`, mapped conservatively.

    `state` is one of accepted / working / completed / blocked / unknown, and
    it is derived ONLY from Hermes's own status word. `completed` additionally
    requires Hermes to have produced final output — a terminal status with no
    output is spoken as blocked-shaped, because "it finished and said nothing"
    and "it died" are indistinguishable from here.
    """
    code, stdout, stderr = _run(["peer", "status", "--json", target, run_id])
    parsed = _parse_json_tail(stdout)
    if parsed is None:
        detail = (stdout + "\n" + stderr).strip()[:400]
        return {
            "ok": False,
            "state": "unknown",
            "error": detail or f"hermes peer status said nothing (exit {code})",
        }
    raw_status = str(parsed.get("status") or parsed.get("state") or "").lower()
    state = _STATUS_MAP.get(raw_status, "unknown")
    output = parsed.get("output") or parsed.get("result") or parsed.get("final") or ""
    if state == "completed" and not output:
        state = "blocked"
    payload: dict[str, object] = {
        "ok": True,
        "state": state,
        "run_id": run_id,
        "hermes_status": raw_status or "(absent)",
        "hermes": parsed,
    }
    if output:
        payload["output"] = output
    return payload
