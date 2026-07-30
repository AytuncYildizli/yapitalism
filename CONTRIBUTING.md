# Contributing

RelayProof is currently a private, small-group project.

## Development loop

1. Create a focused branch from current `main`.
2. Add or update a deterministic test before changing behavior.
3. Run:

   ```bash
   python3 -m unittest discover -s tests -v
   ```

4. Keep raw device/terminal evidence outside Git. Add scrubbed fixtures only.
5. Document architecture changes with an ADR under `docs/adr/`.
6. Open a pull request with: problem, evidence boundary, tests, security impact, and rollback.

## Design rules

- No false GREEN.
- Unknown evidence is YELLOW.
- Revision movement alone never proves acceptance.
- Closed-source client behavior remains observed/inferred unless documented by the provider.
- External sends and invitations require explicit approval.
