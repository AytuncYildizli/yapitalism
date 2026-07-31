# Changelog

All notable changes to Yapitalism will be documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow Semantic Versioning.

## [Unreleased]

### Added

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
