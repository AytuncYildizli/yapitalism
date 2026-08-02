# Keeping the server running on Linux

The voice route dies when the MCP server does, so it should outlive a terminal.

`yapitalism-mcp.service` is a **user** unit, deliberately. The server can read every terminal
this user can see; running it as root or under a different account would hand it panes that are
not yours.

```bash
mkdir -p ~/.config/systemd/user
cp yapitalism-mcp.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now yapitalism-mcp
```

Check it, and confirm what it bound to:

```bash
systemctl --user status yapitalism-mcp
journalctl --user -u yapitalism-mcp -n 30 --no-pager
ss -ltnp 'sport = :8792'          # must be 127.0.0.1, never 0.0.0.0
```

Then point your agent at it:

```bash
codex mcp add yapitalism --url http://127.0.0.1:8792/mcp
```

## Before you enable it

**Kill any server you started by hand first.** Two instances race for port 8792; the loser exits
and which one that is comes down to timing.

`ExecStart` assumes `~/.local/bin/yapitalism-mcp`, which is where `pipx` and `pip install --user`
put it. If yours is elsewhere, point the unit at `$(command -v yapitalism-mcp)` — systemd does
not search `PATH`.

To have it survive logout, `loginctl enable-linger $USER`. Leave that off if you would rather
the server only exist while you are logged in; it is reading your terminals either way.

## Changing the port

Edit `Environment=YAPITALISM_MCP_PORT=` in the unit, `daemon-reload`, restart, and re-register
the URL with `codex mcp add`. The server refuses any non-loopback host outright, so there is no
way to widen its exposure through configuration alone.
