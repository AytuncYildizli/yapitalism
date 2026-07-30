# ADR-0001: Unknown evidence is never GREEN

- Status: accepted
- Date: 2026-07-30

## Context

Opaque voice and remote-agent boundaries frequently expose partial state. A live audio session, an accepted HTTP request, and a moving terminal revision can each look healthy while the command is lost.

## Decision

The status projector uses three outcomes:

- GREEN only when every required command leg has proven success;
- RED when any required leg has failed or timed out;
- YELLOW for incomplete, unknown, or inference-only evidence.

Deadlines ensure YELLOW is not silent permanence: pending legs eventually receive explicit timeout evidence.

## Consequences

The system prefers honest uncertainty over optimistic success. GREEN rates may be lower, but false GREEN is treated as the highest-severity reliability defect.
