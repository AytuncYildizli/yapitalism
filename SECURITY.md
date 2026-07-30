# Security Policy

## Reporting

Do not open a public issue containing credentials, terminal transcripts, handoff locators, or private command content. Report privately to the repository owner.

## Data-handling rules

- Never commit raw terminal captures, voice transcripts, credentials, tokens, or signed URLs.
- Store evidence metadata and content hashes by default.
- Keep local ledger files mode `0600` and outside Git.
- External notification adapters must default off and require explicit per-channel approval.
- Treat handoff locators as sensitive metadata.
- UI observations and user reports are evidence classes, not proof of command delivery.

## Supported versions

The repository is pre-alpha and private. Only the latest `main` branch is supported.
