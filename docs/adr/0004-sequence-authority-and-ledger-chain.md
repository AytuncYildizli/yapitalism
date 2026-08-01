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

Appending is process-locked, duplicate event IDs are idempotent only for an identical event payload, and every write is flushed with `fsync`. `yapitalism ledger verify` checks schema, sequence continuity, previous-hash linkage, and row hashes.

## Consequences

- Status and summary use the same effective event set.
- A retry can supersede stale failure evidence without deleting it.
- Hash verification detects local corruption or rewriting but does not prove who wrote the ledger.
- Hash chaining is not a signature, external checkpoint, or tamper-proof store.
- Pre-chain ledger files require `yapitalism ledger migrate --source OLD --output NEW` before new appends; Yapitalism will not silently rewrite them. Migration is non-destructive: it writes and verifies a new chained ledger and leaves the source unchanged.
- Send paths preflight ledger compatibility before constructing an adapter or making any terminal request.
- **Truncation of the tail is not detectable.** Verification checks that sequences are contiguous from 1 and that each `prev_hash` links, so deleting the last N rows leaves a prefix that still verifies. An older success then becomes the current event for its leg. Detecting this needs an anchor outside the file — a stored head hash and count — which this ADR does not introduce. Treat `verify()` as proof that the retained rows were not edited or reordered, never as proof that no row was removed.
- `receipt show` verifies the chain before projecting, so an edited row cannot produce a verdict. It is the projection surface, and a chain nothing reads is decorative.
- Supersession is scoped to a single leg. Cross-leg supersession would let any event delete another leg's failure and resurrect an older success.
