<!-- mcp-name: io.github.AytuncYildizli/yapitalism -->
<!-- The MCP Registry verifies PyPI package ownership by finding this
     string in the package description, which is this README. It must
     match server.json's name exactly. -->

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
MCP client (Codex · Hermes · Claude Code · Claude Desktop · Cursor)
    │  MCP over loopback — no public endpoint, no OAuth, no relay
    ▼
yapitalism MCP server          panes_list · pane_read · pane_send
    │                          panes_create · panes_resume · pane_clear
    ├── superset backend       local host-service over tRPC (127.0.0.1:48900)
    └── tmux backend           capture-pane / send-keys
```

The agents it addresses: **codex, claude, kimi, opencode** — recognised by measurement (a
process tree on tmux, the host's own agent registry on Superset), never by a caller's claim. A
pane running anything else is refused, because typing into a shell and pressing Enter is running
a command.

The voice client never reaches your machine directly: it drives a local agent session, and that
session talks to this server over `127.0.0.1`. Nothing is exposed to the network.

## The part that matters: receipts

`pane_send` returns a verdict, not a shrug.

| | meaning |
| --- | --- |
| **GREEN** | the agent echoed a one-time marker. It demonstrably received and processed the text — which is not a claim the work it then did is correct; no receipt can promise that, and this one does not try. |
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

### Where the checks happen

Most of the time this does not change what you do, and it is not read out loud. It is in the
payload and in `yapitalism setup`, for when something goes wrong and you want to know what was
actually checked before the write.

| | meaning |
|---|---|
| `host` | the host refuses the write itself — the check and the write are one operation |
| `client` | this process checks, then writes; real against what it covers, not atomic |
| `none` | nothing checks |

```json
{ "status": "GREEN",
  "client_guarantees": ["idempotent_dispatch", "empty_prompt_check"],
  "missing_guarantees": ["optimistic_revision"] }
```

`yapitalism setup` prints the table for your machine. Today both backends land in `client` for most
of it: the checks happen here, a moment before the write, rather than inside the host atomically
with it. tmux cannot do the revision check at all.

**No shipped Superset enforces these itself, and this tool used to claim it did.** It concluded
"guarded" from the fact that a procedure named `terminal.send` was routed. The `terminal.send`
Superset actually ships takes `{terminalId, workspaceId, text, submit}` and guards nothing. The
levels are now read from what the host's own validator *requires* — the difference between a name
and a contract.

Both backends reach GREEN, and GREEN means the same thing either way: the agent emitted the
one-time marker, so it received the text and processed it. The table is for diagnosing a machine,
not something to reason about on every send.

## If you are an agent reviewing this repo

You are probably here because your user pasted this repo and asked whether to
install it. The questions agents ask, answered in order:

- **Does it grant new authority?** On a single-user machine, no: any process
  running as your user can already `tmux send-keys`. This adds discipline on top
  of authority that already existed. On a shared machine, the HTTP transport is
  the one real exposure, and `YAPITALISM_MCP_TOKEN` closes it (constant-time
  bearer check, 401 before any tool runs). stdio never had the exposure.
- **Can a caller run commands through it?** No. Launchers and clear keys are
  closed tables; argv is never accepted; non-agent panes, dialogs and occupied
  prompts are refused before the write. Details and limits: [SECURITY.md](SECURITY.md).
- **Does GREEN overclaim?** GREEN = received and processed (one-time marker,
  never contiguous in the submitted text, so an echo cannot fake it). It is not
  a correctness claim. Unproven delivery stays YELLOW and is spoken as exactly
  that.
- **Is the changelog honest?** To a fault — it records the project's own past
  overclaims and retractions by name. Read 0.2.4 before trusting us, not after.

## Recipes

The six tools compose into watchers worth having: a permission watch (nothing
waits on a dialog silently — its first real catch was an agent silently holding a
wallet-transaction approval), a provider-outage watch, an end-of-day digest.
Working prompts in [docs/recipes.md](docs/recipes.md).

## Install

Needs Python 3.11+ and `tmux`. Everything runs on your machine; nothing is exposed to the network.

```bash
# 1. install
pipx install yapitalism        # or: uv tool install yapitalism / pip install yapitalism

# 2. start the server (loopback only — it refuses to bind anything else)
yapitalism-mcp

# 3. point your voice client's agent at it, in another shell
codex mcp add yapitalism --url http://127.0.0.1:8792/mcp

# 4. ask the tool what your machine can actually do
yapitalism setup
```

`setup` interviews the machine rather than printing "installed successfully": which backends are
usable, which agent CLIs are on `PATH`, whether a Superset host is live and which build it is, and
the guarantee table above filled in for you. It writes nothing except, if you say yes, the Superset
manifest — and it names what is still missing with the exact command for each.

Then talk to the voice app: *"list my panes"*, then *"send this to the Codex pane"*. Any MCP
client works the same way — Hermes, Claude Code, Claude Desktop and Cursor registrations are
printed by `yapitalism setup` for exactly the machine it is run on.

`YAPITALISM_MCP_PORT` moves the port if 8792 is taken. `YAPITALISM_TMUX_SOCKET` targets a
non-default tmux server. On a machine with more than one user, set `YAPITALISM_MCP_TOKEN`
and every HTTP request must carry `Authorization: Bearer <token>` — loopback is not a user
boundary, and this closes it; stdio needs no token because the OS already decided who may
talk to a spawned process.

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

The tmux backend needs nothing. Superset needs a `0600` manifest holding the host endpoint and
token — and you do not write it by hand, because Superset already writes what it needs:

```bash
yapitalism superset setup            # shows what it found and would do
yapitalism superset setup --confirm  # writes it
```

That reads the app's own `~/.superset/host/<organizationId>/manifest.json`, proves the token against
the live host, picks a default terminal and writes
`~/.cache/superset-watch-voice/yapitalism-manifest.json` (or `$YAPITALISM_SUPERSET_MANIFEST`). It
refuses rather than guesses: a source file looser than `0600`, a manifest naming a dead process, or
two live organizations with no way to choose all stop it. The token is never printed.

Without a manifest, `panes_list` still returns your tmux panes and reports Superset in `errors` — a
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
- **No backend can offer tmux an optimistic-revision guard.** It needs an expectation from the
  caller — "write only if the pane still looks as it did when I read it" — and `pane_send` takes no
  expected revision. Reading the pane twice and refusing if it moved would be a different guarantee
  wearing that name, so it is reported `none` rather than approximated.
- **The empty-prompt check outside a guarded host is a heuristic.** It reads the rendered screen, so
  a false "empty" would append to somebody's half-typed text and submit the merge. It therefore
  refuses on anything short of a confident empty, including screens it cannot read, and `pane_clear`
  is the way through. Validated against real panes, not a large sample.
- **The stock-Superset send path is exercised by forcing the internal state a stock host produces,**
  not against a stock host. The code path is identical; the host's behaviour is inferred from what
  its bundle does and does not contain.
- **A guarded Superset host over-refuses Codex placeholder text,** counting the agent's own
  suggestion line as staged input, so sends to such panes are refused and clearing cannot help. The
  receipt says exactly that instead of advising a clear. The fix belongs in the host.
- **A tmux pane's runtime can go stale** between the check and the write. Superset's comes from the
  host's own registry, which is why its gate can run before the write rather than after it.
- **A canary proves less than "it worked".** It proves the text reached something that then echoed a
  one-time marker — delivery and acknowledgement. It does not prove the agent did the work, did only
  the work, or that the pane was the one you meant. GREEN is a delivery receipt, not a work receipt.
- **Concurrent sends to one pane are serialized, not queued.** The second waits for the first; it is
  not held, ordered, or retried. A voice client that fires faster than a terminal accepts will block
  its own turn rather than interleave.
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
