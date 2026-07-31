# ADR-0003: Confirmed sends require a durable single-use claim

- Status: accepted
- Date: 2026-08-01

## Context

RelayProof documents a two-command flow: an operator first reviews a dry run, then starts a separate CLI process to confirm the exact send. An in-memory token binding cannot survive that process boundary and therefore cannot prove that the confirmed text, target, and revision are the values the operator reviewed.

## Decision

A dry run issues an owner-only, expiring confirmation claim bound to:

- the transport client token;
- the logical command identifier;
- `sha256` of the exact UTF-8 command text;
- the reviewed terminal revision; and
- the target terminal identifier.

The confirmed process must load and validate that claim before any `terminal.send` mutation. A successful validation consumes the claim atomically. Consumed tokens cannot be re-issued. Claim files contain no raw command text or credentials.

Dispatch and acceptance evidence are appended to the local JSONL ledger. A transport failure after claim consumption is recorded as an ambiguous failed dispatch and is never automatically retried.

## Consequences

- Dry run remains zero-network but now has deliberate local persistence.
- Confirmed sends without a matching live claim fail before mutation.
- A rejected or ambiguous confirmed attempt requires a new reviewed dry run.
- Consumed tombstones are load-bearing replay protection and must not be deleted without a replacement retention design.
- The ledger remains operational evidence, not cryptographic proof.
