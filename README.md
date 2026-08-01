# Yapitalism

**Evidence-backed reliability for voice-driven agent work.**

Yapitalism is a local-first receipt and observability harness for commands that cross opaque voice, remote-agent, and terminal boundaries. It tells you what is proven, what is merely observed, and where a command stopped—without pretending to control closed-source voice clients.

> Public pre-alpha. No production deployment, external messaging, or closed-client automation is included.

## Why

A healthy audio indicator does not prove that a command reached an agent. A terminal spinner does not prove acceptance. A handoff claim does not prove where the handoff landed. Yapitalism separates these boundaries and requires receipts for each.

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
PYTHONPATH=src python3 -m yapitalism.cli doctor fixtures/stuck-revision.json
```

Expected doctor result for the reproduced incident:

```text
RED command=voice-canary-20260730 failed=accept reason=canary_timeout
```

## Real Superset adapter

Yapitalism talks directly to the local Superset host-service tRPC surface. It does not use a fixture for these operations.

Create an explicit owner-only manifest outside the repository:

```json
{
  "endpoint": "http://127.0.0.1:48900/trpc",
  "bearer_token": "<host-service-secret>",
  "workspace_id": "<workspace-id>",
  "terminal_id": "<terminal-id>"
}
```

```bash
chmod 600 /path/to/yapitalism-superset.json

# Real, read-only terminal.snapshot. Raw terminal text is never printed.
PYTHONPATH=src python3 -m yapitalism.cli superset status \
  --manifest /path/to/yapitalism-superset.json

# Zero-network dry run. This is the default for send.
PYTHONPATH=src python3 -m yapitalism.cli superset send \
  --manifest /path/to/yapitalism-superset.json \
  --text '<prompt whose literal text does not contain the expected marker>' \
  --canary 'YAPITALISM_ACK_<32-uppercase-hex-characters>' \
  --expect-revision <reviewed-revision>

# A real terminal.send requires the dry-run command_id to be reused exactly.
# Add: --client-token <command_id-from-dry-run> --confirm-send
```

Dry runs persist a mode-`0600`, single-use confirmation claim bound to the exact
command hash, target terminal, and reviewed revision. Confirmed sends consume that
claim before network dispatch. Receipt events default to
`$XDG_STATE_HOME/yapitalism/events.jsonl` (or `~/.local/state/yapitalism/events.jsonl`)
and contain bounded metadata only. Project one receipt with:

```bash
PYTHONPATH=src python3 -m yapitalism.cli receipt show <command_id>
PYTHONPATH=src python3 -m yapitalism.cli ledger verify
PYTHONPATH=src python3 -m yapitalism.cli ledger manifest
PYTHONPATH=src python3 -m yapitalism.cli ledger migrate \
  --source /path/to/legacy.jsonl \
  --output /path/to/chained.jsonl
```

The confirmed path snapshots immediately before dispatch, rejects a changed revision, sends `requireEmptyPrompt=true`, `allowRepeat=false`, a stable `clientToken`, and the exact `expectRevision`, then polls snapshots against a monotonic deadline. HTTP 2xx, PTY revision movement, Superset `verified`, and prompt echo never prove acceptance. Only a command-correlated post-dispatch canary can do that.

Security boundaries: loopback-only `/trpc`, no redirects or ambient proxies, bounded responses, strict `0600` non-symlink manifests, redacted bearer/token handling, and no automatic POST retry after an ambiguous transport failure.

## Repository map

```text
src/yapitalism/      typed core, canary matching, ledger, CLI
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

`0.1.0-dev` — public pre-alpha with a tested receipt core and Superset adapter. Controlled device probes and live-journey evidence remain roadmap work.
