# ADR-0005: The event ledger is the sole authority

- Status: accepted
- Date: 2026-08-01

## Context

RelayProof can expose receipts through CLI summaries, future search indexes, or other read models. If any projection can also become writable authority, receipt state can diverge. Actor identity, source, target, session, command, and delivery identifiers are also different dimensions; collapsing them makes routing metadata look like proof authority.

## Decision

The verified append-only JSONL event ledger is RelayProof's sole local authority. Status, receipt summaries, manifests, search indexes, and future databases are rebuildable projections only.

The evidence envelope separates these optional identity dimensions:

- `actor_id`: who or what is attributed with the action;
- `source_id`: component that emitted the evidence;
- `target_id`: concrete intended or observed target;
- `session_id`: bounded execution/session correlation;
- `command_id`: logical receipt correlation;
- `delivery_id`: transport delivery correlation.

Identity fields provide attribution and correlation. They do not upgrade `PENDING` to `SUCCEEDED`, change provenance, or independently prove any receipt leg.

`relayproof ledger manifest` reports only bounded authority metadata: schema/projection versions, validity, sequence range, event and command counts, and chain head. It does not expose command IDs, targets, event payloads, or terminal content.

A source-of-truth cutover must use these states:

1. old ledger is sole authority;
2. candidate format is built as a read-only shadow;
3. manifests and deterministic projections are compared;
4. an operator explicitly selects the new ledger;
5. new ledger becomes sole authority;
6. old ledger remains a read-only rollback shadow;
7. retirement happens under a separate decision.

At no point may both ledgers accept authoritative writes.

## Consequences

- Projection databases can be deleted and rebuilt from verified ledger rows.
- `TerminalSnapshot` is context-only `PENDING` evidence; observing terminal state does not prove capture or work success.
- Adapter identity metadata remains inspectable without affecting receipt color.
- Migration and cutover are source-preserving and never silent in-place rewrites.
- RelayProof does not gain task routing, handoff buses, agent memory, dashboards, or multi-tenant control-plane behavior from this decision.
