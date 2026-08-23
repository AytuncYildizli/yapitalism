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

## Post-launch program (agreed 2026-08-23)

Sequenced, one release per band. Earlier bands do not wait for later ones.

**0.2.9 — the product loop**
- `yapitalism watch`: the wallet-approval catch as a built-in, LLM-free watcher —
  poll panes, detect waiting/blocked/outage/exit transitions, notify a webhook
  (ntfy.sh-compatible) or stdout. Notify on transitions only.
- `yapitalism demo`: first canary-proven GREEN in two minutes, self-cleaning.
- `docs/operator-prompt.md`: the operator instructions we actually use, for any
  voice client.
- Real pane states for Superset agents from the binding's `lastEventType`
  (today: `unknown`).
- Own Linux officially: live smoke + README line (CI is already green there).

**0.3.0 — the breaking release**
- HTTP auth fail-closed by default: token generated on first run, stored 0600,
  `setup` prints registration lines carrying it. `YAPITALISM_MCP_INSECURE=1`
  opts out. This was the last standing objection of the strictest agent
  reviewer, and we said "0.3.0-shaped" in public.
- Delete the ledger/claims/model triad (725 lines the MCP path never imports);
  CLI shrinks to `setup` + `doctor`.

**0.4.0 — machines are panes too (tailscale)**
- `RemoteBackend`: a peer's yapitalism HTTP endpoint mounted into the local
  `BackendRegistry`, ids namespaced `machine:backend:id`, receipts passed
  through unchanged — the peer proved them, we do not restate them. Peers come
  from a 0600 config; any non-localhost peer REQUIRES its token. The tailnet is
  the transport, not the trust story: same bearer auth as local.
