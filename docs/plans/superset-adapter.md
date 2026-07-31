# Superset Adapter Implementation Plan

## Goal

Build a real, local-first Superset host adapter that can read terminal snapshots, dispatch a uniquely identified prompt through the actual host tRPC contract, and prove acceptance only when the canary appears. No simulated GREEN and no undocumented Voice API.

## Scope

### Build

- `relayproof.adapters.superset` package.
- Explicit typed config for endpoint, bearer token, workspace id, and terminal id.
- Minimal tRPC GET/POST client using the Python standard library.
- `snapshot()` using real `terminal.snapshot`.
- `dispatch()` using real `terminal.send` with idempotent client token and expected revision.
- `await_canary()` using bounded snapshot polling and the existing line-wrap-safe matcher.
- Mapping from adapter observations to RelayProof evidence events.
- CLI read-only status command.
- CLI send/canary command that defaults dry-run and requires an explicit confirmation switch.
- Unit/contract tests with a local fake HTTP server and captured scrubbed envelopes.
- Optional live **read-only** smoke against an explicitly supplied local manifest; never commit the manifest/token.

### Do not build

- ChatGPT/iOS automation.
- Automatic third-party notification or messaging.
- Production deployment or auth mutation.
- Broad Superset fork refactor.
- Silent fallback from failed prompt verification to optimistic success.

## Contracts

- Host source of truth: the operator-reviewed Superset host-service terminal tRPC router.
- Live host manifests remain outside Git and are read only when an operator passes a path.
- `terminal.snapshot` is read-only.
- `terminal.send` is side-effecting and must receive: terminal/workspace ids, exact text, submit intent, unique client token, empty-prompt policy, repeat policy, and expected revision.
- A successful HTTP/tRPC send proves `dispatch`, not `accept`.
- Only `canary.observed` or explicit trusted agent acknowledgement proves `accept`.

## Verification

1. Existing 13 tests remain GREEN.
2. Fake-server tests prove request paths, envelopes, bearer redaction, timeout behavior, duplicate client-token behavior, send result parsing, snapshot polling, canary success, and canary timeout.
3. Ruff GREEN.
4. Wheel build GREEN.
5. Live read-only snapshot smoke proves the adapter can talk to the current local Superset host without printing secrets.
6. No live prompt send until explicitly authorized at the operator step.
