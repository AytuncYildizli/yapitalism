# Changelog

All notable changes to Yapitalism will be documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow Semantic Versioning.

## [Unreleased]

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
