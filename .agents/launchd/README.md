# Running the Yapitalism MCP server under launchd

The server must outlive a shell and a reboot; Codex reads it at
`http://127.0.0.1:8792/mcp`, and a dead server means the voice silently loses
every pane tool.

## Install

    cp .agents/launchd/com.yapitalism.mcp.plist ~/Library/LaunchAgents/
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.yapitalism.mcp.plist

## Check

    launchctl print gui/$(id -u)/com.yapitalism.mcp | head -20
    curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8792/mcp \
      -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
      -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe","version":"1"}}}'

`200` means it is up. Logs are at `~/Library/Logs/yapitalism-mcp.log`.

## Stop / remove

    launchctl bootout gui/$(id -u)/com.yapitalism.mcp
    rm ~/Library/LaunchAgents/com.yapitalism.mcp.plist

## Before installing, kill any manually started server

A shell-started instance already holds port 8792 and launchd will crash-loop
against it. Find it with `lsof -nP -iTCP:8792 -sTCP:LISTEN` and stop that PID —
**only** that one. Port 8787 nearby belongs to the launchd-managed
`com.mahmut.mahmory-api`, which must not be touched.
