# Contributing

Yapitalism is a public pre-alpha project with a deliberately small scope: a local MCP server
for driving terminal coding agents by voice, and the receipt layer that decides what the voice
is allowed to claim.

## Development loop

1. Create a focused branch from current `main`.
2. Add or update a deterministic test before changing behavior.
3. Run:

   ```bash
   python3 -m unittest discover -s tests -v
   ```

4. Keep raw device/terminal evidence outside Git. Add scrubbed fixtures only.
5. Document architecture changes with an ADR under `docs/adr/`, including limits the change does
   NOT address. An overclaimed guarantee is worse than a missing one.
6. If you touch the voice policy, run `.agents/skills/superset-operator/sync.sh check` — the
   runtime copy and the versioned one drift silently otherwise.
7. Open a pull request with: problem, evidence boundary, tests, security impact, and rollback.

## Design rules

- No false GREEN. This is the only rule that outranks the others.
- Unknown evidence is YELLOW. "Not tested" is not "passed".
- A backend declares the guarantees it cannot enforce; a receipt never claims one that was not
  actually checked.
- Revision movement alone never proves acceptance.
- Closed-source client behavior remains observed/inferred unless documented by the provider.
- External sends and invitations require explicit approval.
