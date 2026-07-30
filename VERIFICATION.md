# Verification Receipt

Date: 2026-07-30

## Local execution

- Python compile: PASS
- Unit/replay suite: **40/40 PASS**
- Doctor fixture: `RED command=voice-canary-20260730 failed=accept reason=canary_timeout`
- Ruff: PASS
- Gitleaks: PASS, no leaks found across the repository after adapter implementation
- Wheel build: `relayproof-0.1.0.dev0-py3-none-any.whl`

## Real Superset adapter

- Live host read-only `terminal.snapshot`: PASS against `http://127.0.0.1:48900/trpc`
- Readback: terminal ID matched the selected active DB row; revision `130`; dimensions `55x32`
- Secret containment: bearer token and terminal text were not printed or persisted
- Live `terminal.send`: intentionally not executed; the mutation contract is covered by adversarial fake-server tests
- Default send path: zero-network dry-run; confirmed send requires the exact reviewed baseline revision
- Confirmed send also requires reuse of the explicit client token emitted by dry-run
- Wrapped/ANSI-split prompt echoes cannot satisfy the structured canary matcher
- Injected responses fail closed unless delivery ID, revision, submit, duplicate, and target invariants agree

## GitHub Actions status

The Actions budget blocker was cleared. Subsequent hosted runs `30527849193` and `30527899709` completed successfully. The adapter change extends CI with Ruff and wheel-build gates; its push run is verified separately through GitHub's check receipt.

## Claim boundary

- Local scaffold, core, and real adapter contracts: GREEN.
- Private GitHub create/push/readback: GREEN.
- Live Superset read-only snapshot: GREEN.
- Live Superset send/canary: YELLOW by policy because no user terminal was mutated.
- Hosted GitHub Actions execution: GREEN for the last pushed commit; the adapter commit requires its own post-push check receipt.
