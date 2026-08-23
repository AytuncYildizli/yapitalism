# Security model

This document is written for the reviewer deciding whether to install — human or
agent. It states what authority the server has, where that authority comes from,
what is enforced versus merely checked, and what a GREEN receipt does and does
not claim. Nothing here is aspirational; every mechanism named is in the code
and tested.

## What this server can do, and why that is not an escalation

The server can read and type into agent terminals its own user can see, through
two backends: tmux (`capture-pane` / `send-keys`) and the Superset desktop app's
local host-service. That sounds broad, so be precise about what is new:

**On a single-user machine, nothing.** Any process running as you can already
run `tmux send-keys` or call Superset's loopback tRPC itself. This server adds
no authority a local process lacked; it adds *discipline* on top of authority
that already existed — closed launcher tables, agent-runtime gates, occupied-
prompt refusal, receipts.

**On a shared machine, one hole — and it has a lid.** 127.0.0.1 is reachable by
every local user's processes, so the HTTP transport crossing user boundaries is
the one real escalation path. Set `YAPITALISM_MCP_TOKEN` and every HTTP request
must carry `Authorization: Bearer <token>`, compared in constant time; without
the right token the server answers 401 before any tool runs. The stdio
transport never had this exposure: the client spawns the process, so the OS
already decided who may talk to it. On a multi-user machine, set the token or
use stdio.

The server refuses to bind any non-loopback host, unconditionally. There is no
account, no cloud, no telemetry, and no outbound network access except to the
two local backends.

## What a caller can never do

- **Run a command.** `panes_create` and `panes_resume` take a runtime *name*
  that indexes a closed table (`codex`, `claude`, `kimi`); argv, flags and
  arbitrary session ids are not accepted from callers. A resume session id must
  match a strict pattern or the call is refused rather than downgraded.
- **Type into a shell.** Every write path checks what is actually running in
  the target — the process tree on tmux (matched on argv[0], the executable,
  after a measured false positive on argument tokens), the host's own agent
  registry on Superset. A pane running anything but a known agent is refused,
  because typing into a shell and pressing Enter is running a command.
- **Type into a dialog or an occupied prompt.** A recognised permission screen
  is refused by name; a prompt already holding text is refused rather than
  appended to, because a write there concatenates and submits the merge.
- **Race another send.** Sends are serialised per pane/terminal for the whole
  read-check-write transaction, with a test that fails if the lock is removed.
- **Clear with arbitrary keys.** `pane_clear` writes from a closed table of
  three control sequences; Enter is deliberately absent — on a menu, Enter
  selects.

## What a GREEN receipt claims, exactly

GREEN means the agent emitted a one-time marker back, so it demonstrably
*received and processed the text*. It does **not** claim the work the agent then
did is correct — no receipt can promise that, and this one does not try.
"Delivered but unproven" stays YELLOW and is never rounded up; a refusal is RED
and nothing reached the terminal. The marker never appears contiguously in the
submitted text, so a terminal echo of your own words cannot satisfy it.

## Honest limits

- Most guards are **client-enforced**: this process checks, then writes, as two
  operations. Real against what they cover, not atomic. The capability table
  (`yapitalism setup`) reports `host` / `client` / `none` per guarantee, read
  from what the backend actually enforces — the levels were once assumed and
  wrong, and are now measured; every receipt carries them.
- The optional CLI evidence ledger is an append-only local file, not a
  tamper-proof log. It is for reconstructing what happened, not for proving it
  to an adversary.
- The Superset bearer token lives in a `0600` manifest under your home
  directory, written by `setup` after an explicit confirmation, never committed
  anywhere. This repository's history contains no secrets (scanned per
  release).

## Reporting

Open a GitHub issue for anything visible in public code. If you believe you
have found something that puts users at risk before a fix can land, use GitHub
private vulnerability reporting on this repository.
