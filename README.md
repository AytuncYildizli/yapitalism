# Yapitalism

**Drive terminal coding agents by voice, and never let the answer claim more than it proved.**

You are away from your desk. You speak; an agent in a terminal does the work; you get a spoken
reply. That reply is the only thing you have — you cannot see the screen. So the worst failure is
not a crash, it is the voice saying *"done"* while your text sits unread in a prompt box.

Yapitalism is a local MCP server that lets a voice client reach your coding agents, plus a receipt
layer that decides what the voice is allowed to say.

> Public pre-alpha. Local-only. No production deployment, no external messaging, and no automation
> of closed-source clients.

## How it fits together

```
voice client (ChatGPT / Codex today)
    │  MCP over loopback HTTP — no public endpoint, no OAuth, no relay
    ▼
yapitalism MCP server          panes_list · pane_read · pane_send
    │
    ├── superset backend       local host-service over tRPC (127.0.0.1:48900)
    └── tmux backend           capture-pane / send-keys
```

The voice client never reaches your machine directly: it drives a local agent session, and that
session talks to this server over `127.0.0.1`. Nothing is exposed to the network.

## The part that matters: receipts

`pane_send` returns a verdict, not a shrug.

| | meaning |
| --- | --- |
| **GREEN** | the agent echoed a one-time marker. It demonstrably processed the text. |
| **YELLOW** | the write landed; processing was **not** proven. Never round this up. |
| **RED** | the backend refused the write. Nothing reached the terminal. |

`YELLOW` is the whole point. Text left unsubmitted in an agent's input box looks identical to work
in progress from outside — same spinner, same scrolling output, same HTTP 200. A voice that rounds
that up to "done" costs you hours before you notice.

Explicitly **not** acceptance: an HTTP 2xx, a PTY write returning, terminal output changing, a
revision advancing, or the prompt echoing your own words back.

### Waiting is not the same as failing

A fixed deadline reports on the clock, not on the agent. An agent that thinks for a minute and
then answers correctly was verified all along, and calling that YELLOW teaches an operator to
ignore YELLOW. So the wait is an **idle** timeout: it restarts whenever the pane changes, bounded
by a hard ceiling.

Pane movement decides only whether to keep waiting. It is never evidence of acceptance — that
stays the canary alone. A YELLOW therefore says which kind it is:

- `canary_timeout_pane_moving` — the pane's text was still changing. Named after what was
  measured: a spinner, a clock, a log tail or a second agent sharing the pane all produce this
  without the intended agent doing anything. It is a hint that looking again may be worth it,
  never a claim that the agent is working.
- `canary_timeout_pane_still` — nothing moved at all.

What remains irreducible: if an agent silently ignores the text and prints nothing, no mechanism
here can distinguish that from an agent that never received it. Verification needs the agent to
emit something.

### Backends do not prove the same things

Every receipt carries the guarantees its backend could not enforce:

```json
{ "status": "GREEN",
  "missing_guarantees": ["idempotent_dispatch", "optimistic_revision", "empty_prompt_check"] }
```

Superset's host refuses a write unless the revision still matches, the client token is unused, and
the prompt is empty. `tmux send-keys` enforces none of that. Both can reach GREEN; they are not the
same GREEN, and saying so is the difference between a receipt and a decoration.

## Install

Needs Python 3.11+ and `tmux`. Everything runs on your machine; nothing is exposed to the network.

```bash
# 1. install
pipx install git+https://github.com/AytuncYildizli/yapitalism        # or: uv tool install / pip install

# 2. start the server (loopback only — it refuses to bind anything else)
yapitalism-mcp

# 3. point your voice client's agent at it, in another shell
codex mcp add yapitalism --url http://127.0.0.1:8792/mcp
```

Then talk to the voice app: *"list my panes"*, then *"send this to the Codex pane"*.

`YAPITALISM_MCP_PORT` moves the port if 8792 is taken. `YAPITALISM_TMUX_SOCKET` targets a
non-default tmux server.

### Clients that launch the server themselves

Codex takes a URL. Claude Desktop, Cursor and most other MCP clients instead spawn the process
and speak over stdin/stdout, so point them at `--stdio` and do not run a separate server:

```json
{
  "mcpServers": {
    "yapitalism": {
      "command": "yapitalism-mcp",
      "args": ["--stdio"]
    }
  }
}
```

Use the absolute path from `command -v yapitalism-mcp` if the client does not inherit your
`PATH` — GUI apps on macOS usually do not. `YAPITALISM_MCP_TRANSPORT=stdio` does the same as the
flag, for clients that only let you set the environment.

In stdio mode nothing but protocol may reach stdout, so the server suppresses its own startup
banner. If you wrap it in a shell script, keep that script silent too.

### Keeping it running

The voice route dies when the server does, so run it under your init system rather than a
terminal. Both units run as **your user**, never root: the server can read every terminal you
can see.

- **macOS** — `.agents/launchd/`. The plist is a template; its README has a `sed` line that
  fills in the real binary path, because launchd searches neither `PATH` nor `~`.
- **Linux** — `.agents/systemd/`, a `--user` unit. `systemctl --user enable --now yapitalism-mcp`.

Kill any shell instance first either way, or the two race for port 8792 and which one wins is
down to timing.

### Superset terminals (optional)

The tmux backend needs nothing. To also reach Superset-managed terminals, the backend reads a
`0600` manifest holding the host endpoint and token, from
`~/.cache/superset-watch-voice/yapitalism-manifest.json` or `$YAPITALISM_SUPERSET_MANIFEST`.
Without it `panes_list` still returns your tmux panes and reports Superset in `errors` — a
backend that could not be reached is never silently reported as "no terminals".

### Teaching the voice how to speak the receipts

`.agents/skills/superset-operator/` holds the policy that stops a model rounding YELLOW up to
"done", plus a drift check against the copy your agent actually loads.

## The receipt core, on its own

The five-leg model is usable without the MCP server:

1. `capture` — the intent was captured
2. `dispatch` — a concrete target received a write attempt
3. `accept` — the target proved acceptance by canary or explicit acknowledgement
4. `work` — material agent progress was observed
5. `deliver` — a final update reached the user

Evidence carries provenance — `api`, `terminal_diff`, `ui_observation`, `user_report`, `inferred` —
and `inferred` may never mark a leg succeeded. Events append to a `0600` JSONL ledger with a
per-row hash chain, contiguous sequence, and single-use confirmation claims for anything that
mutates a terminal.

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m yapitalism.cli doctor fixtures/stuck-revision.json
# RED command=voice-canary-20260730 failed=accept reason=canary_timeout
```

That fixture is the incident this project came from: the audio session stayed alive, spoken progress
stopped, two 180-second watchers saw the terminal frozen at revision `920118`, and the canary never
arrived. The product is not "fix the voice client". It is **never fake GREEN**.

CLI surface: `doctor`, `receipt show`, `ledger verify|manifest|migrate`, and `superset status|send`.
A confirmed `superset send` requires reusing the exact `client-token` a dry run emitted, snapshots
immediately before dispatch, rejects a changed revision, and never retries an ambiguous POST.

## Known limits

Stated plainly, because a receipt system that overclaims is worse than none:

- **Tail truncation is undetectable.** Deleting the last ledger rows leaves a prefix that still
  verifies. Catching it needs an anchor outside the file — see `docs/adr/0004`.
- **The ledger attests to itself.** Hash verification proves rows were not edited or reordered; it
  does not prove who wrote them, and a wholly fabricated ledger verifies fine.
- **tmux cannot refuse a duplicate or an occupied prompt.** Declared per receipt, never worked
  around.
- **A tmux pane's runtime can go stale** between the check and the write. Superset's comes from the
  host's own registry and cannot.
- Connector-in-voice behaviour in closed clients is undocumented and can change without notice.

## Repository map

```text
src/yapitalism/mcp/       MCP server, backend registry, receipts, tmux driver
src/yapitalism/adapters/  Superset host-service client
src/yapitalism/           receipt core: model, canary, claims, ledger, CLI
tests/                    deterministic unit, replay, and real-tmux tests
fixtures/                 scrubbed incident replays
.agents/skills/           voice policy + drift check
.agents/launchd/          run the server as a login agent
docs/adr/                 load-bearing decisions
docs/architecture.md      component boundaries and evidence model
```

## Explicit non-goals

- reverse-engineering or patching closed voice-client internals;
- inventing background-turn or push APIs that do not exist;
- multi-tenant SaaS;
- automatic email, DM, or messaging delivery;
- treating terminal revision movement as command acceptance.

## Credits

The receipt-integrity core was written by [@liri-ha](https://github.com/liri-ha), whose commits
are carried here unrewritten. See [CONTRIBUTORS.md](CONTRIBUTORS.md).

## Security

Raw transcripts, terminal text, and credentials do not belong in this repository. Ledgers store
bounded metadata, hashes, and classifications. A manifest holding a bearer token lives outside the
repo at mode `0600` and is read only when its path is passed explicitly; it is never printed. See
[SECURITY.md](SECURITY.md).
