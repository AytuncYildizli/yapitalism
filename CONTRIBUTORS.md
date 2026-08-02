# Contributors

## Liri Ha — [@liri-ha](https://github.com/liri-ha)

Wrote the receipt-integrity core: durable single-use confirmation claims, deterministic and
verifiable ledger ordering, and the identity envelope and sole-authority projection contracts,
plus the fixes that closed several false-`GREEN` paths.

Attributed in the git history — `git log --author=liri-ha` — and the original commits are
carried on the current branch **unrewritten, with their original SHAs**, not reconstructed.

| Design | Where it lives |
| --- | --- |
| Durable single-use confirmation claims | [`docs/adr/0003`](docs/adr/0003-durable-single-use-confirmation-claims.md), `src/yapitalism/claims.py`, `src/yapitalism/ledger.py` |
| Sequence authority and the ledger hash chain | [`docs/adr/0004`](docs/adr/0004-sequence-authority-and-ledger-chain.md), `src/yapitalism/model.py` |
| The ledger as sole authority | [`docs/adr/0005`](docs/adr/0005-ledger-sole-authority.md) |

The single-use confirmation claim — that a send which mutates a terminal may be confirmed
exactly once, and that a replayed token is refused rather than silently accepted — is the
load-bearing idea behind every `GREEN` this project is willing to speak.

Because these commits are the attribution, **this branch must not be squash-merged**: squashing
would collapse twelve authored commits into one under the maintainer's name.

## Efe Büken — [@efe-arv](https://github.com/efe-arv)

Opened pull requests [#1](https://github.com/AytuncYildizli/yapitalism/pull/1),
[#2](https://github.com/AytuncYildizli/yapitalism/pull/2) and
[#3](https://github.com/AytuncYildizli/yapitalism/pull/3), which carried the work above.

### A correction

An earlier version of this file said Efe authored that design and that "no commit carries his
authorship". That was wrong, and it was wrong in a way worth recording rather than quietly
deleting: it came from reading the **pull request author** as the **commit author**. Every
commit inside those three PRs is authored by @liri-ha, and `git log --author=efe` returns
nothing anywhere in the repository because there was never a missing attribution to restore.

The same mistaken claim went into pull request #5's description and into comments posted on
#1–#3. All have been corrected.

## Maintainer

Aytunc Yildizli — [@AytuncYildizli](https://github.com/AytuncYildizli)
