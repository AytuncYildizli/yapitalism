# ADR-0004: Ledger sequence is authoritative for receipt ordering

- Status: accepted
- Date: 2026-08-01

## Context

Receipt events can arrive with equal, skewed, or replayed wall-clock timestamps. Append order is locally observable and deterministic; wall-clock order is not. A later successful retry must also be able to replace a stale failed projection without rewriting history.

## Decision

The owner-only JSONL ledger assigns every appended event a contiguous positive `sequence`. Receipt projection orders persisted events by this sequence, not by `occurred_at`.

An event may name one prior event in `supersedes`. The prior event remains in append-only history but is excluded from effective status and summary projection.

Every persisted row includes:

- `schema_version`;
- contiguous `sequence`;
- `prev_hash`;
- `event_hash`, calculated over canonical JSON for the row excluding `event_hash`.

Appending is process-locked, duplicate event IDs are idempotent only for an identical event payload, and every write is flushed with `fsync`. `relayproof ledger verify` checks schema, sequence continuity, previous-hash linkage, and row hashes.

## Consequences

- Status and summary use the same effective event set.
- A retry can supersede stale failure evidence without deleting it.
- Hash verification detects local corruption or rewriting but does not prove who wrote the ledger.
- Hash chaining is not a signature, external checkpoint, or tamper-proof store.
- Pre-chain ledger files require an explicit migration before new appends; RelayProof will not silently rewrite them.
