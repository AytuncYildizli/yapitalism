# Architecture

## Boundary model

RelayProof observes five independent command legs:

```text
capture → dispatch → accept → work → deliver
```

The legs have separate lifetimes. Audio-session health is a parallel channel observation and never satisfies any command leg by itself.

## Components

- `model.py`: immutable evidence events and deterministic status projection.
- `canary.py`: ANSI/line-wrap tolerant canary matching.
- `ledger.py`: append-only local evidence ledger with restrictive permissions.
- `claims.py`: owner-only, expiring, single-use confirmation claims bound to exact dispatch content.
- `cli.py`: doctor output, persisted receipt projection, and explicit Superset operations.
- `adapters/superset/`: fail-closed local Superset tRPC integration.
- future adapters: replay and explicitly approved notification surfaces.

## Evidence provenance

Every event declares one of:

- `api`: provider/tool response;
- `terminal_diff`: terminal canary or material output;
- `ui_observation`: visible client/OS state;
- `user_report`: direct operator report;
- `inferred`: reasoned conclusion without direct proof.

Inference can explain a verdict but cannot independently satisfy a required leg.

## Status projection

- Any failed required leg → RED.
- Every required leg proven succeeded → GREEN.
- Everything else → YELLOW.
- An unknown handoff destination blocks GREEN when a handoff is claimed.
- Acceptance success requires `canary.observed` or `agent.acknowledged`; terminal revision movement is insufficient.

## Privacy boundary

The core event schema deliberately has no raw transcript field. Details are bounded classifications. Sensitive raw evidence belongs in an ignored, short-retention local store.
