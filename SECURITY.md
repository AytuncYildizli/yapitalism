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

**On a shared machine, one hole — closed by default since 0.3.0.** 127.0.0.1 is
reachable by every local user's processes, so the HTTP transport crossing user
boundaries is the one real escalation path. The server therefore requires a
bearer token on every HTTP request: minted on first start, stored 0600 under
`$XDG_STATE_HOME/yapitalism/http-token`, compared in constant time, 401 before
any tool runs. `yapitalism setup` prints the registration lines that carry it;
`YAPITALISM_MCP_TOKEN` overrides it; `YAPITALISM_MCP_INSECURE=1` opts out and
says so on stderr. The stdio transport never had this exposure: the client
spawns the process, so the OS already decided who may talk to it.

The server binds loopback by default. One widening exists, for fleets: an
explicitly configured address inside tailscale's CGNAT range (100.64/10) may be
bound, and only with the bearer gate active — `YAPITALISM_MCP_INSECURE=1` on a
tailnet bind is refused outright. Public addresses are refused unconditionally.
There is no account, no cloud, no telemetry, and no outbound network access
except to the local backends and to peers you configured yourself.

## Authentication is not authorization (0.5.0)

The token answers *who is calling*; it never answered *what the caller may do*.
Since 0.5.0 those are separate:

- **stdio clients write, always.** The client spawned this process on this
  machine; the OS made that trust decision.
- **HTTP writes are OFF by default.** Every write tool (`pane_send`,
  `pane_task`, `pane_clear`, `panes_create`, `panes_resume`) is refused over
  HTTP — with the fix named in the refusal — until the machine's operator runs
  `yapitalism authority allow-http-writes` once (or says yes to the same
  question in `yapitalism setup`). Reading, watching and doctor work either
  way. A corrupt authority file fails closed.
- **`--read-only` refuses writes on every transport**, so a skeptic can run
  the watcher for weeks with zero write surface before allowing anything.
- Every write result is stamped with its `origin` (`stdio` or `http`).

## The threat model: a send is code execution

Be precise about the attacker. It is usually not someone stealing the token; it
is **untrusted text reaching the model that composes the send** — a GitHub
issue, a web page, a PR diff that says "call pane_send with the following". If
the orchestrating model obeys, this server will deliver the attacker's
instruction perfectly and return an honest GREEN. Receipts prove delivery, not
intent, and no server-side pattern filter can tell a malicious instruction from
a legitimate one — we do not pretend otherwise, because that filter would be
theater.

What the server does own, it enforces:

- The **trust boundary for composing sends belongs to the MCP client** (its
  approval flow, its operator). This server's side is to keep authority
  narrow (the authorization split above), keep every write attributable (the
  `origin` stamp, the spoken receipt), and keep the blast surface an agent
  prompt rather than a shell.
- The agent-runtime gate is bracketed, not assumed: the send path fingerprints
  the admitted agent process (pid + start time) after the gate and verifies the
  SAME process is still under the text immediately before Enter. An agent that
  exits into a shell mid-write gets the text typed but never submitted, and the
  receipt says exactly that ("Enter was never pressed").

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

GREEN means the agent completed a **canary round-trip**: it emitted a one-time
marker back, so the text demonstrably reached a process that read it and acted
on it. Say the mechanism plainly: an agent instructed (or prompt-injected) to
echo canaries could satisfy it, which is one more reason GREEN never claims
intent or correctness — only that the instruction was received and processed.
It does **not** claim the work the agent then did is right, and no receipt can.
"Delivered but unproven" stays YELLOW and is never rounded up; a refusal is RED
and nothing reached the terminal. The marker is minted per send and never
appears contiguously in the submitted text, so a terminal echo of your own
words cannot satisfy it, and a STALE marker from an earlier send cannot prove a
later one (observed live: an agent once answered a second message by copying
the first message's marker from scrollback, and the receipt correctly stayed
YELLOW).

Since 0.5.0 the turn receipt (`pane_await` / `pane_task`) answers the next
question — did the turn end, is it blocked on a human, did the provider die —
with the same discipline: `ended` is a claim about an idle screen, spoken with
"not that the work is correct" attached, never as "done".

## Honest limits

- Most guards are **client-enforced**: this process checks, then writes, as two
  operations. Real against what they cover, not atomic. The capability table
  (`yapitalism setup`) reports `host` / `client` / `none` per guarantee, read
  from what the backend actually enforces — the levels were once assumed and
  wrong, and are now measured; every receipt carries them.
- The Superset bearer token lives in a `0600` manifest under your home
  directory, written by `setup` after an explicit confirmation, never committed
  anywhere. This repository's history contains no secrets (scanned per
  release).

## Reporting

Open a GitHub issue for anything visible in public code. If you believe you
have found something that puts users at risk before a fix can land, use GitHub
private vulnerability reporting on this repository.
