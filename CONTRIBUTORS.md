# Contributors

## Why this file exists

Parts of this project were contributed as pull requests and then landed through
rework rather than merge — conflict resolution, a rebrand that moved every path, and review
passes that rewrote the commits. The result is that `git blame` and `git log` do not name
everyone whose design is in the code. That is an artifact of how the branches were integrated,
not a judgement about whose work mattered. This file records what the history lost.

## Efe Büken — [@efe-arv](https://github.com/efe-arv)

Authored the receipt-integrity design in three pull requests. **No commit in this repository
carries his authorship**, because the changes were reimplemented during integration and review.
The design and its contracts are his.

| PR | Contribution | Where it lives now |
| --- | --- | --- |
| [#1](https://github.com/AytuncYildizli/yapitalism/pull/1) | Persist working receipts and confirmation claims | [`docs/adr/0003-durable-single-use-confirmation-claims.md`](docs/adr/0003-durable-single-use-confirmation-claims.md), `src/yapitalism/claims.py`, `src/yapitalism/ledger.py` |
| [#2](https://github.com/AytuncYildizli/yapitalism/pull/2) | Make ledger ordering deterministic and verifiable | [`docs/adr/0004-sequence-authority-and-ledger-chain.md`](docs/adr/0004-sequence-authority-and-ledger-chain.md), `src/yapitalism/model.py` |
| [#3](https://github.com/AytuncYildizli/yapitalism/pull/3) | Identity and sole-authority projection contracts | [`docs/adr/0005-ledger-sole-authority.md`](docs/adr/0005-ledger-sole-authority.md) |

The single-use confirmation claim — that a send which mutates a terminal may be confirmed exactly
once, and that a replayed token is refused rather than silently accepted — is the load-bearing
idea behind every `GREEN` this project is willing to speak.

## Liri Ha — [@liri-ha](https://github.com/liri-ha)

Identity envelope and authority manifest, plus the ledger projection and supersession fixes that
closed several false-`GREEN` paths. Attributed in the git history (`git log --author=liri-ha`).

## Maintainer

Aytunc Yildizli — [@AytuncYildizli](https://github.com/AytuncYildizli)
