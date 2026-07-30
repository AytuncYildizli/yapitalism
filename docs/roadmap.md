# Roadmap

## Gate 0 — first two days

- Record voice-turn time, resolved target, terminal id, revision before/after, and unique canary.
- Reproduce foreground and background paths.
- Separate wrong-target, ended-turn, dispatch stall, acceptance stall, and speech-delivery failure.
- Kill gate: if no reliable post-prompt signal exists, stop at honest RED detection and operator discipline.

## Days 0–30

- Receipt/evidence core and deadman deadlines.
- Canary matcher and scrubbed `920118` replay.
- Append-only local ledger.
- Superset adapter contract.
- CLI `doctor`, `status`, and manual iOS probe protocol.

**Exit:** 20 controlled journeys, zero false GREEN, no raw-log leakage.

## Days 31–60

- Local notification fallback.
- Optional local TTS behind explicit approval.
- Handoff registry with destination verification.
- Read-only timeline/TUI.
- Drop/delay/duplicate/reorder fault injection.

**Kill:** if first-party speech fails and users reject fallbacks, re-scope to pure observability.

## Days 61–90

- Second adapter or complete contract-conformance harness.
- Redaction and threat-model audit.
- Collaborator dogfood: a new contributor gets a correct `doctor` verdict in under ten minutes.
- Prepare upstream MCP contributions only if write/merge access exists.

**Kill:** if collaborators do not use the status surface or the core only tracks one transient vendor bug, archive as an operator toolkit.
