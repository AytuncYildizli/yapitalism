# Verification Receipt

Date: 2026-07-30

## Local execution

- Python compile: PASS
- Unit/replay suite: **13/13 PASS**
- Doctor fixture: `RED command=voice-canary-20260730 failed=accept reason=canary_timeout`
- Ruff: PASS
- Gitleaks: PASS, no leaks found across the initial repository
- Wheel build: `voice_receipt-0.1.0.dev0-py3-none-any.whl`

## GitHub Actions status

The first push created Actions run `30527440813`, but GitHub did not allocate a runner. The check annotation was:

> The job was not started because an Actions budget is preventing further use.

No repository code or test step executed in that failed run. Automatic triggers are therefore gated off; the workflow remains available through `workflow_dispatch` once the account Actions budget permits execution.

## Claim boundary

- Local scaffold and core: GREEN.
- Private GitHub create/push/readback: GREEN.
- Hosted GitHub Actions execution: YELLOW, blocked by account budget rather than a code/test failure.
- Live Superset adapter and iOS end-to-end canary: not implemented; roadmap work.
