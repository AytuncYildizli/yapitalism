# Voice Receipt

**Evidence-backed reliability for voice-driven agent work.**

Voice Receipt is a local-first receipt and observability harness for commands that cross opaque voice, remote-agent, and terminal boundaries. It tells you what is proven, what is merely observed, and where a command stopped—without pretending to control closed-source voice clients.

> Private MVP. No production deployment, external messaging, or closed-client automation is included.

## Why

A healthy audio indicator does not prove that a command reached an agent. A terminal spinner does not prove acceptance. A handoff claim does not prove where the handoff landed. Voice Receipt separates these boundaries and requires receipts for each.

The initial incident behind this repository:

- iOS `Background conversations` was enabled;
- the ChatGPT audio session stayed alive in Dynamic Island;
- spoken progress stopped after backgrounding;
- two 180-second watchers saw the target terminal remain at revision `920118`;
- the canary never reached the terminal.

The product is not “fix ChatGPT.” The product is **never fake GREEN**.

## Core model

Each command has five independent legs:

1. `capture` — the user intent was captured;
2. `dispatch` — a concrete target received a dispatch attempt;
3. `accept` — the target proved acceptance by canary or explicit acknowledgement;
4. `work` — material agent progress was observed;
5. `deliver` — a final update reached the user through Voice or an approved fallback.

Evidence always carries provenance: `api`, `terminal_diff`, `ui_observation`, `user_report`, or `inferred`.

- **GREEN:** all required legs have proven success.
- **YELLOW:** incomplete or unknown evidence remains.
- **RED:** a leg failed, timed out, or violated policy.

## Quick start

```bash
python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m voice_receipt.cli doctor fixtures/stuck-revision.json
```

Expected doctor result for the reproduced incident:

```text
RED command=voice-canary-20260730 failed=accept reason=canary_timeout
```

## Repository map

```text
src/voice_receipt/      typed core, canary matching, ledger, CLI
tests/                  deterministic unit and replay tests
fixtures/               scrubbed incident replays
docs/architecture.md    component boundaries and evidence model
docs/roadmap.md         Gate 0 and 30/60/90 roadmap
docs/adr/               load-bearing architecture decisions
```

## Scope

### Build now

- deterministic receipt projection;
- deadman timeouts;
- canary normalization and matching;
- append-only redacted local ledger;
- Superset adapter contract and replay fixtures;
- CLI doctor/status output;
- manual iOS foreground/background test protocol.

### Explicit non-goals

- reverse-engineering or patching ChatGPT iOS/OpenAI internals;
- inventing background-turn or Voice-push APIs;
- an iOS companion app;
- multi-tenant SaaS;
- automatic email, DM, or messaging delivery;
- treating terminal revision movement as command acceptance.

## Security

Raw transcripts and credentials do not belong in this repository. Ledgers store bounded metadata, hashes, and classifications—not raw terminal output. See [SECURITY.md](SECURITY.md).

## Status

`0.1.0-dev` — professional scaffold with a tested receipt core. The Superset adapter and device probes remain roadmap work.
