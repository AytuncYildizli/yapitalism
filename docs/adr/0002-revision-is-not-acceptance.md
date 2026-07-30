# ADR-0002: Terminal revision movement does not prove acceptance

- Status: accepted
- Date: 2026-07-30

## Context

Terminal revisions can change because of spinners, clocks, prompt redraws, or unrelated output. An unchanged revision is useful evidence of no visible progress, but a changed revision is asymmetric: it does not identify the cause.

## Decision

The `accept` leg can succeed only through:

- `canary.observed`; or
- `agent.acknowledged` from a trusted adapter.

`terminal.revision` may satisfy `work` only when paired with a material-output classifier; it cannot satisfy `accept`.

Canary matching removes ANSI sequences and whitespace so line wrapping does not create false negatives.

## Consequences

Adapters must carry explicit canaries or acknowledgements. The model rejects optimistic acceptance events based only on revision movement.
