# Feature Ledger — Superset Adapter

- `SA-001` — Config/secret boundary
  - User story: operator supplies endpoint/token/ids without committing or logging secrets.
  - Expected: typed config; redacted representation; explicit manifest opt-in.
  - Current: pending.
  - Retest receipt: tests + gitleaks.

- `SA-002` — Real snapshot transport
  - User story: read current terminal revision/text from actual host tRPC.
  - Expected: `terminal.snapshot` GET envelope parsed and bounded.
  - Current: pending.
  - Retest receipt: fake-server contract + live read-only smoke.

- `SA-003` — Receipt-aware dispatch
  - User story: send one idempotent command to a concrete target.
  - Expected: `terminal.send` POST with expected revision, client token, prompt-empty guard, no repeat.
  - Current: pending.
  - Retest receipt: fake-server contract; no live send by default.

- `SA-004` — Acceptance proof
  - User story: distinguish dispatched from accepted.
  - Expected: bounded polling; canary match → acceptance; timeout → RED; revision movement alone insufficient.
  - Current: pending.
  - Retest receipt: deterministic fake clock/server scenarios.

- `SA-005` — CLI safety
  - User story: inspect status safely and run canary only with explicit confirmation.
  - Expected: read-only command; send defaults dry-run; no token output.
  - Current: pending.
  - Retest receipt: CLI tests.

- `SA-006` — Packaging/CI
  - User story: collaborators clone and run tests/build.
  - Expected: full suite, Ruff, wheel, GitHub Actions.
  - Current: pending.
  - Retest receipt: local + Actions run URL.
