# Changelog

All notable changes to Yapitalism will be documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow Semantic Versioning.

## [Unreleased]

## [0.5.0] - 2026-08-24

The council release: three slices a four-model advisory panel and one operator
converged on, built the same day.

### Added

- **Turn receipts** — the second receipt. `pane_await` watches a pane until
  the agent's turn ends and says how: `ended` (idle prompt, stable screen —
  spoken with "not that the work is correct" attached, never as "done"),
  `waiting_input` (a blocking dialog, with the question in `tail`),
  `agent_error` (a named failure line — 401, rate limit — not a timeout),
  `exited`, `running`, `unreadable`. `pane_task` is send-then-await in one
  call: instruct, walk away, come back to the verdict. `ended` re-verifies the
  agent process is alive before it is claimed. Both forward to peers whole.
- **Write authority by transport.** The bearer token answers who is calling;
  it never answered what they may do. stdio clients write, always — the OS
  made that trust decision at spawn. Writes over HTTP are refused by default,
  with the fix named in the refusal: `yapitalism authority allow-http-writes`,
  once, on that machine (`yapitalism setup` asks the same question while
  printing HTTP registration lines). `yapitalism-mcp --read-only` refuses
  writes on every transport, so the watcher can run with zero write surface.
  Every write result is stamped with its `origin`. `doctor` shows the answer.
- **Agent identity bracket** on the tmux send path: the admitted agent process
  is fingerprinted (pid + start time) after the gate and re-verified
  immediately before Enter. An agent that exits into a shell mid-write gets
  the text typed but never submitted (`staged_agent_changed`), because typing
  is recoverable and Enter into a shell is command execution.
- SECURITY.md gained the explicit threat model ("a send is code execution;
  the composer of the send is the attack surface") and a precise statement of
  what a GREEN canary round-trip does and does not prove.

### Changed

- **Breaking for HTTP setups upgrading from 0.4:** sends over HTTP return
  `http_writes_not_allowed` until the operator allows them (one command,
  above). The server says so at startup, the refusal says so in the receipt,
  and reading/watching are unaffected.

## [0.4.2] - 2026-08-24

The first cross-machine send taught two lessons in one night.

### Fixed

- A send that times out next to a failure the agent printed — `Please run
  /login`, a 401, a rate limit, an overloaded provider — now speaks that
  failure by name instead of a generic "could not verify". One marker list
  (`screen_errors.py`) is judged by both the send path and `yapitalism
  watch`, so a login drop also shows up as a watcher finding. The wording
  stays a claim about the screen, scoped to its last 30 lines; a proven
  canary always outranks a stale error in scrollback.

### Changed

- Every spoken line — receipts, refusals, watcher findings, create/resume,
  clear — now speaks English. The `speak` field is source material for a
  voice client that answers in the operator's own language; hardcoding one
  human language into it was a locale bug. The contract is unchanged:
  "Not sent:" is safe to retry, "Sent, but" is never resent — only looked at.

## [0.4.1] - 2026-08-24

First-contact fixes from the first owner-run peer setup — a bare MacBook Pro
added over the tailnet.

### Fixed

- tmux is resolved on every call — PATH first, then the places package
  managers actually install it — so `brew install tmux` after the server
  started (or a launchd-minimal PATH) no longer reports "tmux is not
  installed" until something is restarted.
- The HTTP transport prints its own one-line status (version, host, port)
  instead of the framework's startup banner, which carried a third party's
  deploy advertisement.

## [0.4.0] - 2026-08-24

Machines are panes too.

### Added

- **Peers**: another machine's yapitalism server, mounted under its own
  namespace — `studio:tmux:%0` sits in the same `panes_list` as your local
  panes and answers the same `pane_send`. The unit of forwarding is the whole
  TOOL, deliberately: the peer runs the full receipt engine next to its own
  terminals, and its verdicts pass through verbatim with only the target ids
  re-namespaced. This side measured nothing and claims nothing.
- **`yapitalism peers add|list|remove`** — `add` initialises a real MCP session
  against the peer and lists its tools before writing a byte of config, so a
  typoed URL or stale token is refused at registration. Tokens come from a file
  or a prompt, never argv. The peers file is owner-only JSON with fail-closed
  trust rules: a non-loopback peer requires a token; a public address requires
  `allow_public` said explicitly; a peer named `tmux` or `superset` is refused
  because `tmux:%0` must always mean the local pane; one invalid peer fails the
  whole load, because a skipped peer is a machine the operator believes is
  watched.
- **Tailnet bind**: `YAPITALISM_MCP_HOST` may name an address in tailscale's
  CGNAT range (100.64/10), and only with the bearer gate active —
  `YAPITALISM_MCP_INSECURE=1` plus a tailnet bind is refused outright, because
  an open port on the tailnet is every tailnet device's port.
- `yapitalism doctor` reports each configured peer with a live pane count.

### Verified

End to end over the tailscale interface with bearer auth: a second server bound
to 100.73.28.102, registered via `peers add` (which counted its 30 panes before
writing config), then through the full MCP surface — `panes_list` merged 30
local + 30 remote panes, and a `pane_send` to `studio2:superset:…` came back
GREEN, "codex aldı.", the peer's own canary-proven receipt, in 8.4s.

### Fixed

- Python's `is_private` was the wrong predicate twice over — it calls the
  documentation ranges private and tailscale's CGNAT space public, both
  measured. Peer trust now uses a closed list of networks that mean "my LAN or
  my tailnet".
- The startup line now says where the bearer token actually came from; it named
  the state file while serving a token from the environment.

340 tests.

## [0.3.0] - 2026-08-23

The breaking release, both halves promised in public: the strictest agent
reviewer's last objection closed, and the council's oldest unexecuted
recommendation executed.

### Changed — BREAKING

- **The HTTP transport requires a bearer token by default.** Minted on first
  start, stored 0600 under `$XDG_STATE_HOME/yapitalism/http-token`, compared in
  constant time, 401 before any tool runs. Restarts do not rotate it — a
  per-boot token would 401 every registered client after every restart, an
  outage shaped like a security feature. The server prints the token's PATH,
  never its value (launchd keeps stderr). `yapitalism setup` prints per-client
  registration lines that carry the token: Codex via `--bearer-token-env-var`,
  Claude Code via `--header`, Hermes via a headers block — all three verified to
  support bearer auth before this shipped. `YAPITALISM_MCP_TOKEN` overrides;
  `YAPITALISM_MCP_INSECURE=1` opts out loudly; stdio is untouched.

  **Upgrading an HTTP registration:** start the new server once, run
  `yapitalism setup`, and re-register your client with the printed line.

- **The hash-chained CLI ledger is gone.** `ledger.py`, `claims.py`, and the
  `doctor <fixture>` / `receipt show` / `ledger verify|manifest|migrate` /
  `superset send` commands were consumed by nothing but each other — the MCP
  path never imported them, and a verification chain nothing reads is
  decorative. `model.py` stays: the adapter's evidence objects are its API.
  ADR-0004 carries the superseded note.

### Added

- **`yapitalism doctor` is a live diagnosis**: the running server found and
  version-matched against the installed code, the token present, each backend
  answering with pane counts, and the who-enforces-what table read from live
  capabilities — moved here from the launch pitch, where it was a diagnostic
  cosplaying as a feature. Its first run caught this machine's own service
  serving 0.2.6 under an installed 0.2.9.

### Fixed

- A launcher that dies the instant its session is created reports
  `created, process_exited_immediately` instead of a raced BackendError from
  inside the settle loop.

326 tests — down from 361, because the removed surface took its suites with it.

## [0.2.9] - 2026-08-23

The product-loop release: install, see a proven GREEN, wire your phone to the
panes. First band of the post-launch program (docs/roadmap.md).

### Added

- **`yapitalism watch`** — the wallet-approval catch as a built-in, no LLM in the
  loop: poll the panes, classify with the same detectors the send path trusts for
  refusing writes, notify a webhook (ntfy.sh-compatible plain text) on
  TRANSITIONS only — blocked, provider outage, exited, text-parked-across-two-
  polls, and each recovery. Reads only, by construction. `--once` for cron.
- **`yapitalism demo`** — first canary-proven GREEN in about a minute: a real
  agent in a throwaway tmux session on an isolated socket, one message with
  proof, the receipt, cleanup. Answers exactly one known first-run screen
  (codex's update menu, matched by literal text, answered with Skip); everything
  else is reported, never typed into.
- **Real Superset pane states.** Every pane said `unknown`; the code read a
  session field the shipped rows do not carry. States now come from the
  binding's `lastEventType` (vocabulary read out of the shipped bundle):
  `PermissionRequest`/`Elicitation` → `waiting_input`, turn-ended events →
  `idle`, `SessionEnd` → `exited`, anything else — including unknown events —
  → `running`, never "ready". `watch` announces a `waiting_input` pane without
  reading a single line.
- **`docs/operator-prompt.md`** — the standing instructions we run in the bridge
  agent, linked from the README.
- **Linux, owned rather than assumed**: the full install → send → verify →
  canary → GREEN loop runs on a bare `python:3.11-slim` container, including the
  negative control (an echo of the submitted text must not satisfy acceptance).

### Fixed

- **tmux sends could drop the Enter.** The paste and the Enter went out
  back-to-back, and a booting codex was measured ingesting the text and losing
  the submit — message parked at "0 in · 0 out" while the receipt said
  `injected`. The same staged-not-submitted failure the Superset path was taught
  about in 0.2.x, found on tmux by running `yapitalism demo`. The write now
  settles, verifies the composer released the text, retries Enter once, and
  otherwise reports `staged_not_submitted`.
- Two newly observed codex composer placeholders (`{feature}`, `/review`) join
  the admissibility table, so an idle codex is not refused as occupied.

358 tests.

## [0.2.8] - 2026-08-23

The launch release. Everything here came from asking four independent coding
agents "should my user install this?" and fixing what they objected to — in the
code, not in the wording.

### Added

- **`YAPITALISM_MCP_TOKEN`**: optional bearer auth for the HTTP transport,
  enforced inside FastMCP's auth middleware with a constant-time compare.
  Loopback is not a user boundary — 127.0.0.1 is reachable by every local
  user's processes — so on a shared machine the open port was the one real
  exposure; this closes it. Measured live in all three directions: no token
  401, wrong token 401, right token serves. Unset, the transport behaves as
  before and prints a one-line stderr nudge. stdio needs no token: the OS
  already decided who may talk to a spawned process.
- **SECURITY.md**: the threat model, led by the fact reviewers kept missing —
  on a single-user machine this grants no authority a local process lacked,
  because `tmux send-keys` was always there. Plus what a caller can never do,
  what GREEN claims exactly, and the honest limits.
- **docs/recipes.md**: five watcher patterns over the same six tools, with
  working prompts. The permission watch caught, on its first real day, an agent
  silently holding a wallet-transaction approval for six hours.
- **README "Reviewing this repo"**: a verdict-free index of claims and where to
  verify each. An earlier draft told reviewing agents what to conclude and was
  rightly flagged as prompt-shaped persuasion; facts only now.

### Fixed

- A limits entry that had gone stale in the safe-sounding direction: "never
  tested against a real stock host" was false since 0.2.4 — the shipped
  Superset is the stock build, and canary-proven sends have landed against it
  live ever since.

343 tests.

## [0.2.7] - 2026-08-22

### Added

- **OpenCode is a relay runtime.** Recognised on both backends — the process tree
  on tmux, the host's agent registry on Superset — with its own measured prompt
  detector (OpenCode 1.18.16's composer and status line, read off a live pane).
  Send, read, clear and receipts work like the other three agents; `panes_create`
  and `panes_resume` deliberately do not start it — this relays to OpenCode panes,
  it does not launch them. Verified live: guarded relay smoke with an
  acceptance/readback receipt against a real OpenCode Superset pane.
- **Hermes is a client.** Registered against the same loopback HTTP service Codex
  uses and driven end to end from Hermes itself: tool discovery, `panes_list`, and
  a canary-proven `pane_send` that came back GREEN. `yapitalism setup` now detects
  `~/.hermes/config.yaml` and prints the registration as the config block Hermes
  actually reads, because its interactive `mcp add` cannot be scripted.

### Changed

- The README's architecture diagram names the five clients this has actually been
  registered with (Codex, Hermes, Claude Code, Claude Desktop, Cursor) and the four
  agent runtimes it addresses (codex, claude, kimi, opencode).

338 tests.

## [0.2.6] - 2026-08-18

Four defects, from a pre-announcement audit by four independent models plus one
found by driving 0.2.5 by hand. Each was confirmed against the running system
before being believed; two of the models' loudest findings did not survive that
check and are recorded below as not-defects.

### Fixed

- **A shell could classify as an agent, and a spoken instruction would run as a
  command.** `classify_tree` searched every token of every descendant process, so a
  plain shell with `python3 -c '...' codex` in the background reported
  `runtime="codex"`. Measured on an isolated tmux socket: the pane came back as
  codex while `pane_current_command` was `zsh`. It now matches argv[0] — the
  executable — which was chosen by measuring a live codex pane rather than
  reasoning about one: the pane process is `node /opt/homebrew/bin/codex` and its
  child execs the vendored `codex`, so the tree walk still finds real agents while
  a word in a child's arguments no longer counts. This is the boundary the function
  exists to be, and it had a way through it.
- **A refusal kept offering a remedy that had already failed.** A wedged pane
  produced: refusal offering `pane_clear` → clear reports nothing changed → the
  same refusal, offering the same clear. Every sentence true, the sequence a loop,
  and a voice operator has no other way out. Clear outcomes are now remembered per
  pane, bounded, and forgotten when a pane recovers; the second refusal says the
  pane is not responding to keys and names the machine as the place to look.
- **The receipt reported the backend's standing guarantees instead of this write's.**
  `SendOutcome.capabilities_override` exists for the send that overrules the host's
  prompt check and therefore enforces one guarantee fewer. It was being set and
  never read. Its own docstring calls that "a lie shaped exactly like the one the
  enforcement levels exist to prevent".
- **The Superset send had no per-terminal lock.** tmux has held one since the
  concurrency work; this path never did, so two sends could read the same pre-write
  screen, both judge the prompt empty on the same revision, and both write —
  reducing `optimistic_revision: client` and `empty_prompt_check: client` to
  decoration. The whole transaction is now serialised per terminal, with a
  barrier-driven test and a negative control that fails if the lock is removed.
- **The operator skill told the model to bypass the receipt layer.** Its rules say
  every send goes through `pane_send`; its command recipe said call
  `terminals_send`, which returns `{terminalId, submitted}` and can never be
  proven. A recipe beats a principle in practice, so the recipe was the thing that
  had to be right.

### Checked and found sound

Recorded because they were raised as blockers and the answer is now measured rather
than argued:

- **A screen echo cannot satisfy the canary.** `canary_instruction` splits the
  marker across real words, so the submitted text never contains it contiguously;
  the matcher rejects an echo of the instruction and accepts the agent printing the
  marker, including hard-wrapped across a line break.
- **YELLOW cannot lead to a duplicate send.** It never offers a resend, only to
  look, and a repeat under the same client token is refused.

331 tests, up from 316.

## [0.2.5] - 2026-08-16

Driving 0.2.4 through its own MCP surface — the sequence a voice client actually
causes — found two more, both invisible to the direct test because that test had
called `capabilities()` first and cached the answer. Order-dependent behaviour
hides in exactly that gap.

### Fixed

- **The first send of a process wrote through the guarded path before knowing the
  host was unguarded.** "Try the guarded send and learn from the answer" was safe
  only against a host that 404s the route. The shipped Superset *has* a
  `terminal.send`, requires `terminalId`, `workspaceId` and `text`, and Zod strips
  the guard fields it does not know — so the guarded attempt **succeeded and
  wrote**, and only then did parsing fail for want of a `phase` the response never
  had. A delivered message came back as a bare error, indistinguishable from
  nothing having happened. The host is now asked what `terminal.send` requires
  before the first write, at the cost of one round trip per process.
- **Every proven send on a shipped host was refused.** `prove_acceptance` appends
  the canary instruction, which adds a newline, and the fallback writes through
  raw `writeInput` where a newline *is* a submit — so multi-line text was refused
  outright. The host's own `terminal.send` frames it as a bracketed paste, and the
  write now goes through it where it routes. The guards stay on this side; it
  enforces none of them.

  Two different questions about one procedure, and conflating them is what
  produced `host/host/host` on every install: does it **route** (use it for the
  write) versus what does it **require** (does it guard).
- Submission is still verified rather than trusted, and `writeInput` still always
  presses Enter. Reading the prompt first on that path found it "already empty"
  and skipped the Enter, leaving text written and never sent — caught by the
  existing stock-host test within minutes of being written.

### Verified

The full MCP surface, against the running Superset, on an agent terminal in a
workspace the manifest does not bind:

```
panes_list  -> ok true, 33 superset panes (24 with agents), 2 tmux
pane_send   -> GREEN, "codex aldı.", 3.9s
```

316 tests, up from 311.

## [0.2.4] - 2026-08-16

**The Superset backend had been written against a build that does not exist.** Five
defects, found by pointing it at the running Superset and reading what came back
instead of what the code expected. The first send it has ever completed end to end,
proven by a canary, happened while fixing them.

Every test had passed throughout, because the tests invented the same shapes the
code did.

### Fixed

- **Every Superset send was refused.** A runtime gate added in 0.2.0 asks the host
  which agent owns a terminal, via `terminal.listSessions` — a procedure the host
  answers 404 for. That name exists on the *daemon* router and lists something
  else. The runtime came back `unknown` for every terminal, so the gate rejected
  every send as `rejected_not_an_agent`. Terminals are `terminal.list`; the agent
  is `terminalAgents.listByWorkspace`, and it is called `agentId`, not
  `agent.runtime`.
- **A host that could not be enumerated reported zero terminals.** `list_panes`
  skips a workspace it cannot read so one bad workspace cannot hide the rest — but
  a missing procedure fails *every* workspace, and the result read as "this host
  has no terminals". A missing procedure is now a fact about the host and fails the
  call. One genuinely bad workspace is still skipped.
- **`terminal.snapshot` was required to carry a `revision` it does not have.** The
  shipped snapshot is `{terminalId, cols, rows, text}`; the string `revision` does
  not occur anywhere in the host's bundle. Every snapshot raised, which is the other
  reason no send could complete. The revision is now derived from the screen when
  the host sends none — and carries `revision_is_derived`, because a hash answers
  "did this change" and cannot order two changes.
- **Two places read a derived revision as if it were a counter.** The acceptance
  poller aborted when it decreased, and accepted the canary only when it increased —
  so a marker that was on screen could be discarded because a number went the wrong
  way. The first live GREEN on this path passed by luck of the hash ordering; the
  test written afterwards caught it.
- **`host` was reported because a name was routed.** Capability detection asked
  `procedure_exists("terminal.send")`. Superset does ship a `terminal.send` — it
  takes `{terminalId, workspaceId, text, submit}`, frames multi-line text as a
  bracketed paste, and guards nothing. So every install has been told its host
  enforced idempotent dispatch, optimistic revision and an empty-prompt check.
  Detection now asks what the procedure *requires*, by sending an empty input and
  reading which fields the host's own validator names. An unanswerable probe claims
  nothing.

### Changed

- The README no longer leads with two kinds of GREEN. Where the checks happen is a
  diagnostic, not the product's message: GREEN means the agent emitted the marker,
  and that is the same claim on either backend.

311 tests, up from 297. The new ones are built from responses recorded off the
running host — its JSON, its Zod errors, and its own bundle — rather than from what
this project believed it would say.

## [0.2.3] - 2026-08-15

Everything here was found by running the tool on a machine that has nothing:
`python:3.11-slim` plus `tmux`, no agent CLIs, no Superset, none of this project's
own configuration. That is what a stranger installing it actually has, and until
now nobody had looked. It was also the first run on Python 3.11 at all — every
prior measurement was on 3.14.

Three defects, and each one hit the first thing a new user does.

### Fixed

- **`panes_list` reported `ok: false` on every machine without Superset** — which
  is nearly every machine. The Superset backend raised on each call because no
  manifest existed, `ok` was computed as "no backend errored", and so a call that
  had found the tmux panes perfectly, and returned them in the same response, still
  came back as a failure. Absence is now its own answer: a backend this machine
  does not have appears under `unconfigured` and does not touch `ok`. A backend
  that IS set up and then breaks is still an error, because that one is real.
- **A missing manifest was described as an unsafe file.** With no Superset
  installed the error read `superset manifest unusable: Superset manifest is not a
  safe readable regular file` — the wording for a file that exists and cannot be
  trusted, applied to one that was never created. It reads like a security problem.
  It now says Superset is not set up on this machine and names the command to fix
  it if you have the app. A *dangling symlink* still reports as a failure: something
  is there and it is wrong.
- **`panes_create` failed with a socket path when the agent was not installed.**
  Asking for `codex` on a box without codex made a session, the pane died at once,
  tmux exited for want of sessions, and the operator was handed `no server running
  on /tmp/tmux-0/default`. Nothing in that sentence points at the cause or the fix.
  PATH is now checked first — against the closed launcher table, never a
  caller-supplied name — so the answer is `codex is not installed — nothing was
  started`, and nothing is.
- **`tmux` with no server yet was an error.** A fresh machine has no tmux server
  until something starts one; that state surfaced as raw tmux stderr. An empty pane
  list is the honest answer. `tmux is not installed` still reports, as absence.

### Changed

- `panes_list` responses may now carry `unconfigured`. It is informational and
  deliberately dull — the tool's own docstring tells the model not to read it aloud
  or offer to fix it unless asked what is missing.

297 tests, up from 294. The one covering "a missing binary yields created but
unconfirmed" was split: it had conflated *never installed* with *started and died*,
which are different sentences for the operator, and only the second leaves a session
behind.

## [0.2.2] - 2026-08-15

Both of the places this project states its own version were wrong, and both were
found by installing 0.2.1 from PyPI and asking it.

### Fixed

- **The MCP handshake advertised FastMCP's version, not this project's.** `serverInfo`
  came back as `3.4.7` from a 0.2.1 server — a number matching no release of
  Yapitalism, and one that moves whenever a dependency updates. `FastMCP(...)` was
  constructed without `version`, so it filled the field with its own. It is the first
  question anyone debugging a client asks, and the answer was a different project's.
- **`yapitalism.__version__` reported `0.1.0.dev0`.** A hand-written literal, three
  releases stale. Nothing failed when it drifted and nothing read it back, which is
  why it drifted. Both surfaces now derive from the installed distribution, so the
  only version any of them can report is the wheel's own.

### Added

- Tests that fail if either surface grows a literal again, if `serverInfo` starts
  agreeing with FastMCP's version, or if `pyproject.toml`, `server.json` (which
  carries it twice) and this changelog stop agreeing.

## [0.2.1] - 2026-08-14

The tmux path was run end to end for the first time and did not work. Everything
below was found by using the tool, not by reading it — 279 tests passed throughout.

### Fixed

- **A dialog in scrollback blocked a pane forever.** `send` captures 1000 lines for
  revision tracking, and the blocking-prompt table matched a phrase anywhere in them.
  Every pane `panes_create` makes shows a trust prompt, so once it was *answered* the
  pane was still refused — permanently, with `pane_clear` unable to help because the
  text sat in scrollback rather than in the prompt. The whole create-then-send flow
  had never worked. Recognition is now scoped to the recent screen.
- **`TmuxBackend.await_acceptance` did not accept `client_token`.** The
  operation-scoping work added the parameter to the caller and to the Superset
  backend only, so every tmux send through `pane_send` had been crashing since. No
  test drove the live tmux path through the tool.
- **That crash lost a delivered message.** The write landed, the agent answered, and
  the watcher raised `TypeError` — not `BackendError` — so it fell through to the
  generic tool error, indistinguishable from "nothing happened". The acceptance phase
  now degrades **every** exception to YELLOW: by then the write is in the terminal,
  and whatever failed to watch it must not erase the evidence that it landed.
- **The not-an-agent gate could be shadowed by a keyword match.** A pane running a
  plain shell whose screen held a dialog phrase was reported as `rejected_trust_prompt`
  — the send was still refused, but the operator was sent to answer a dialog that does
  not exist and the refusal that means "writing here runs a command" never surfaced.
  The runtime gate now runs before the dialog table.
- **The runtime was observed three times per send**, each a `tmux list-panes` plus a
  `ps`, inside the held lock. The value that picked the prompt markers, the value the
  gate admitted and the value in the receipt were independent readings of a pane that
  can change between them. One observation now, reused.
- **`setup` reported healthy while every call returned 401** (from 0.2.0's follow-up):
  Superset rotates the token in its own manifest and our cached copy goes stale, but
  the check was reading the host's fresh token rather than the one this tool actually
  sends.

### Changed

- **The spoken surface collapses to two acts.** Six different sentences reached the
  operator's ear, each naming a different internal state, when the operator can only
  do two things — carry on, or act.
  - `RED` always leads with `Gönderilmedi`. On a voice channel the first word is often
    the only word heard, and a refusal is the one outcome that can safely be retried.
  - `YELLOW` always leads with `Gönderdim` and offers to **look**, never to resend:
    the text is already in the terminal, so a retry would deliver it twice.
  - `GREEN` is `codex aldı.` and nothing else.

  `status`, `phase`, `client_guarantees` and `missing_guarantees` are unchanged in the
  payload. Only the `speak` string changed shape — if you were matching on its text,
  match on `status` instead.
- **Panes are named the way a person names them.** `speak` was saying
  `pane tmux:%6`, read aloud as "pane tmux percent six". It now says
  `relayproof klasöründeki codex`; the id stays in the payload where the model needs it.
- **Enforcement attribution is no longer read aloud.** Whether the host or this process
  checked a guarantee is real and stays in the payload — there is no different action
  behind it for the person listening.
- **Turkish is spelled with Turkish letters** in every spoken line. ASCII-folded
  Turkish is a mispronunciation, not a spelling preference: a TTS engine reads `hazir`
  and `klasorundeki` with the wrong vowels.

289 tests, up from 279.

## [0.2.0] - 2026-08-14

Nine defects. Six reported by [@efe-arv](https://github.com/efe-arv) with file:line
evidence and working reproducers; three more found by an independent audit across five
models.

**Corrected after publication.** This release first went out described as a "security
release", with a draft advisory. That was an overstatement, and the advisory was closed
without being published. A misrouted send gives nobody a capability they did not already
have: the person speaking already has a shell on their own machine, the server binds to
loopback only, and no trust boundary is crossed. It is a routing defect with a bad
failure mode. Calling it a vulnerability is the same error this project exists to avoid,
pointed the other way — and the correction belongs in the record rather than in a quiet
edit.

### Fixed

- **A send could reach a pane that is not running an agent** (Superset backend). The
  runtime was read from the dispatch *response*, so the write had already happened by
  the time the pane was known to be a shell. tmux refused non-agent panes up front; this
  path had no equivalent check.

  What it costs: the text is typed and Enter is pressed. On an agent pane that is an
  instruction the agent interprets and can refuse; on a shell pane it is a command line —
  usually nonsense that fails with `command not found`, occasionally a real command when
  the dictated sentence happens to begin with one. Either way it is the wrong place for
  it, and `classify_tree` already documented that it must never happen.

  The check now runs before the write, against the host's own agent registry, and a
  runtime that disagrees afterwards is refused as delivered rather than reported as an
  instruction the agent received. (#16)
- **`pane_clear` had no runtime check at all**, on either backend. Clearing writes
  control bytes, which a shell reads as keys. Fixing only the send path would have left
  the other voice-reachable mutation open.

### Fixed

- **A receipt could prove the wrong send.** Acceptance context was two fields on the
  backend, and the registry hands every call the same instance while FastMCP runs
  sync tools on a threadpool — so a second send overwrote the first's proof before the
  first awaited, and a refused send clobbered a successful one's. Context is now keyed
  by client token, carries its target so a token cannot be answered across panes, and
  is written only when the write landed. (#17)
- **Concurrent tmux sends could merge.** Capture, guards, type, Enter and the closing
  capture are separate subprocesses with no lock, so two sends interleaved into one
  merged prompt and one submit, with both receipts claiming delivery and both canaries
  misattributed. Locked per pane. (#18)
- **`command_id` was derived from the canary's last 8 characters**, so two concurrent
  sends sharing a suffix collided on the host's own deduplication. It uses the full
  client token.
- **A proven write could be reported as if nothing happened.** The send and its
  observation shared one `try`, so an observation failure returned a bare error
  indistinguishable from "not sent" — inviting a retry that, on a backend which
  deduplicates nothing, writes twice. An observation failure is now an honest YELLOW
  carrying the dispatch evidence. (#19)
- **The MCP Registry listing could not start the server.** It resolves this package to
  its same-named console script with `--stdio`, and that script was the CLI. Every
  registry-driven client failed before `initialize`. (#20)
- **A confirmation claim was published before its evidence reached the ledger**, so a
  crash between the two left a live claim able to authorize a mutation whose dry run
  was never durably recorded. (#21)

### Added

- `pane_send` accepts a `client_token`, so a retry after an ambiguous failure stays
  one delivery instead of two. Without it each call minted a fresh token and a guarded
  host's deduplication never saw the repeat.
- `pane_send(override_host_prompt_check=True)` lets an operator overrule a host
  empty-prompt verdict this side can prove wrong — a Superset host counts an agent's
  own placeholder suggestion as staged input, which refuses those panes forever while
  `pane_clear` cannot help. Never automatic, `codex` only, judged on the same snapshot
  whose revision is sent, logged per occurrence, and spoken with the override leading.


## [0.1.2] - 2026-08-05

### Added

- `yapitalism setup` interviews the machine: usable backends, agent CLIs on `PATH`, Superset host
  liveness and build, detected MCP client configs, and the guarantee table filled in for that host.
- `yapitalism superset setup` provisions the Superset manifest from the app's own `0600` host
  manifest, proving the token against the live host first. Refuses a loose-permission source, a
  manifest naming a dead process, or two live organizations. The token is never printed.
- `pane_clear` now supports Superset panes, through `terminal.writeInput`.
- Sending works against a Superset host without the guarded `terminal.send`, with the revision,
  token and empty-prompt checks moved to this process and reported as such.
- `prompt_state` judges whether an agent's prompt holds text, so a send can decline instead of
  appending to it.

### Changed

- Capability guarantees are three-valued (`host` / `client` / `none`) instead of boolean, and are
  asked of the host rather than hard-coded. A boolean could not distinguish a client-side check from
  no check, and the constants described one machine's build — so a stock Superset user would have
  received a GREEN asserting three guards their host never applied.
- Receipts carry `client_guarantees` beside `missing_guarantees`, and GREEN has three wordings.
- tmux enforces client-token dedup and an empty-prompt check before writing; both were absent.
- The launchd plist carries the installing shell's `PATH`. Without it the service could not see
  `tmux`, `codex`, `claude` or `kimi`, while still holding its port and answering the protocol.

### Fixed

- **A send to a tmux pane never checked that an agent was running in it**, so a spoken instruction
  into a shell pane was arbitrary command execution. Now refused explicitly.
- A submit is verified rather than asserted: `writeInput` reporting success says nothing about the
  composer having accepted the text, which left a message staged while the receipt said it was sent.
- A client token burned by an ambiguous write is no longer reported as a duplicate — that claimed a
  delivery which may never have happened.
- `panes_create` polls for a blocking dialog instead of reading once, so it no longer announces a
  running agent that is sitting on an update menu or a trust prompt.
- A refusal caused by the host and the screen disagreeing about the prompt no longer advises clearing
  it, which had been measured not to work.
- The Superset host manifest is read through a single guarded descriptor rather than a `stat` followed
  by a separate open.
- Token dedup records are bounded; a plain set grew for the lifetime of the service.


### Added

- Separate actor, source, target, session, command, and delivery identity dimensions on evidence events.
- Privacy-bounded `ledger manifest` authority/projection receipts and a single-authority cutover contract.
- Monotonic ledger sequences, event supersession, duplicate-ID protection, and local hash-chain verification.
- `yapitalism ledger verify` with explicit schema, sequence, link, and event-hash receipts.
- Durable, expiring, single-use confirmation claims bound to exact command hashes, targets, and revisions.
- CLI persistence for dry-run, dispatch, ambiguous transport, and canary evidence with `receipt show` projection.
- Structured canary matching that preserves ordinary acknowledgement token boundaries.
- Real Superset host-service adapter for `terminal.snapshot` and `terminal.send`.
- Client-token idempotency, expected-revision guarding, and bounded canary polling.
- Adversarial fake-server coverage for false-GREEN, duplicate-send, redirect, proxy, manifest, timeout, and secret-containment boundaries.
- Reviewer-driven hardening for wrapped/ANSI-split canaries, explicit retry-stable tokens, contradictory response envelopes, and outbound size limits.
- Five-leg receipt model: capture, dispatch, accept, work, deliver.
- Typed five-leg evidence and receipt model.
- Deterministic GREEN/YELLOW/RED projection with no false GREEN.
- Acceptance-proof guard: canary or explicit acknowledgement required.
- ANSI and line-wrap tolerant canary matcher.
- Append-only `0600` local JSONL ledger.
- Scrubbed replay of the observed terminal revision stall.
- CLI `doctor` command.
- Unit, replay, security, and CI documentation.
