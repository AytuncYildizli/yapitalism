# Running the Yapitalism MCP server under launchd

The server must outlive a shell and a reboot; Codex reads it at
`http://127.0.0.1:8792/mcp`, and a dead server means the voice silently loses
every pane tool.

## Install

The plist is a template: launchd does not search `PATH` and does not expand
`~`, so the binary and log paths have to be absolute. What goes in the binary
slot depends on how the package is installed, and the two cases are genuinely
different — this file used to document only the first, which produced an empty
path on the machine it was written for.

### If the package is on your `PATH`

    sed -e "s|__YAPITALISM_MCP_BIN__|$(command -v yapitalism-mcp)|" \
        -e "s|__PATH__|$PATH|" \
        -e "s|__HOME__|$HOME|g" \
        .agents/launchd/com.yapitalism.mcp.plist \
        > ~/Library/LaunchAgents/com.yapitalism.mcp.plist

**Check that `command -v yapitalism-mcp` printed something first.** If it is
empty the `sed` still succeeds and writes a plist with an empty program path,
which launchd accepts and then fails to run.

### If it is not — Homebrew Python refuses a global install

Homebrew's Python is marked externally managed, so `pip install -e .` into it is
refused. A checkout-local virtualenv avoids both that and
`--break-system-packages`:

    python3 -m venv .venv
    .venv/bin/python -m pip install -e ".[dev]"

    sed -e "s|__YAPITALISM_MCP_BIN__|$PWD/.venv/bin/yapitalism-mcp|" \
        -e "s|__PATH__|$PATH|" \
        -e "s|__HOME__|$HOME|g" \
        .agents/launchd/com.yapitalism.mcp.plist \
        > ~/Library/LaunchAgents/com.yapitalism.mcp.plist

The install is editable, so the service picks up code changes on its next start —
no reinstall after a `git pull`. The trade is that the plist now points into the
checkout: **moving or deleting the directory breaks the service**, and launchd
reports a spawn failure rather than anything mentioning a missing venv.

Then, either way:

    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.yapitalism.mcp.plist

## Why `$PATH` is substituted

launchd hands a service a minimal `PATH` and never reads a login shell, so every
binary this server shells out to disappears. `panes_list` reported **"tmux is not
installed"** on a machine with tmux at `/opt/homebrew/bin/tmux`, and
`panes_create` would have failed identically on `codex`, `claude` and `kimi`.

This is invisible from a shell, where everything is on `PATH` already — which is
why the check below calls a tool instead of only pinging the port.

## Before installing, stop any manually started server

`KeepAlive` is on, so launchd will crash-loop against a port someone else holds.
Find it with `lsof -nP -iTCP:8792 -sTCP:LISTEN` and stop that PID — **only** that
one. Port 8787 nearby belongs to the launchd-managed `com.mahmut.mahmory-api`,
which must not be touched.

## Check

    launchctl print gui/$(id -u)/com.yapitalism.mcp | grep -E 'state|pid|program|last exit'

Expect `state = running` and `last exit code = (never exited)`. A climbing `runs`
count with a nonzero last exit is the crash-loop above.

Then confirm it is really serving MCP rather than merely holding the port:

    curl -sL -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8792/mcp \
      -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
      -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe","version":"1"}}}'

`200` means it is up. `-L` matters: the endpoint answers `/mcp` with a 307 to
`/mcp/`, and without it curl reports the redirect instead of the result.

Then call a tool, not just `initialize`. Holding the port and answering the
protocol both succeed while every backend is broken:

    yapitalism setup    # the tmux row must not say "not on PATH"

Logs are at `~/Library/Logs/yapitalism-mcp.log`.

## Stop / remove

    launchctl bootout gui/$(id -u)/com.yapitalism.mcp
    rm ~/Library/LaunchAgents/com.yapitalism.mcp.plist

## What the checks above do not establish

`RunAtLoad` and `KeepAlive` are set, and they are what make the service start at
login and come back after a crash. Neither is verified by anything here: the
commands prove it is running now, not that it returns. Testing them takes a
reboot and a deliberate kill.
