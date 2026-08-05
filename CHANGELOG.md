# Changelog

All notable changes to Yapitalism will be documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow Semantic Versioning.

## [Unreleased]

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
