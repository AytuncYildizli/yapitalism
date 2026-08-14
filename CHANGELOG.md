# Changelog

All notable changes to Yapitalism will be documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow Semantic Versioning.

## [Unreleased]

## [0.2.0] - 2026-08-14

A security release. It closes a path by which a spoken instruction could reach a
terminal running a plain shell, where text followed by Enter is an executed command.
If you are on 0.1.x, upgrade.

Found by [@efe-arv](https://github.com/efe-arv), who filed six issues with file:line
evidence and working reproducers, and confirmed by an independent audit across five
models. Three further defects surfaced during that audit.

### Security

- **A send could reach a non-agent pane on the Superset backend.** The runtime was
  reported after the write rather than checked before it, so by the time the pane was
  known to be a shell, the shell had run the text. The check now happens before the
  write, against the host's own agent registry. A runtime that disagrees afterwards is
  refused as delivered — the write cannot be recalled, but the receipt can decline to
  call it an instruction the agent received. (#16)
- **`pane_clear` had no runtime check at all.** Clearing writes control bytes, which a
  shell interprets as keys. Fixing only the send path left the other voice-reachable
  mutation open.

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
