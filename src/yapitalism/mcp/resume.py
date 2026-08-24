"""Bringing a dead agent back, and saying how faithfully.

Ported out of a Superset fork worktree that is frozen and is not the host this
tool talks to, so the capability existed on one machine in a checkout nobody
runs. The argv table and — more importantly — the fidelity distinction are the
parts worth keeping; the tombstone table that fed it is host-side state a client
cannot have.

`exact` means a recorded session id was used. `last` means the runtime was asked
for its most recent session, which is **not** a guarantee it is the one the
operator meant. Those are different claims and the receipt has to be able to say
which one it is out loud, or "resumed your session" becomes a sentence nobody can
check.

The argv is verified against the installed CLIs rather than remembered:

    codex resume [SESSION_ID] | codex resume --last
    claude --resume [value]   | claude --continue
    kimi --session [id]       | kimi --continue

Long flags deliberately: they survive a CLI reshuffling its short options, and
they are readable in a spoken confirmation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: A recorded session id reaches argv, logs and spoken confirmations. Constrained
#: to the shapes the agents actually emit — UUIDs and slug-ish names — so a value
#: from anywhere cannot become a flag or a path.
#:
#: The leading character is restricted separately, and that is not tidiness. The
#: ported pattern allowed `-` anywhere, which made `--last` a *valid* session id:
#: `resume_argv_for("codex", "--last")` returned `codex resume --last` labelled
#: `exact`. The argv was harmless, the label was a lie — the loosest possible
#: resume reported as the most precise one. Caught by the test written to prove
#: the opposite.
SAFE_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

EXACT = "exact"
LAST = "last"


@dataclass(frozen=True, slots=True)
class ResumePlan:
    argv: tuple[str, ...]
    #: EXACT or LAST. Never collapse these when speaking.
    fidelity: str


#: The complete set of resumes this server will start, closed for the same reason
#: AGENT_LAUNCHERS is closed: a resume tool that accepted a command string would
#: be remote code execution reachable by voice.
#:
#: The `last` forms are not equally strong, and the label hides that. `codex resume
#: --last` takes the most recent recorded session anywhere; `kimi --continue` and
#: `claude --continue` continue the previous session *for the working directory*.
#: Both are "not necessarily the one you meant", which is what the operator needs
#: to hear, so they share a label — but a caller reasoning about how wrong it
#: could be should know the codex case is the loosest.
_RESUMES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    #        runtime: (exact-form prefix,      last-form argv)
    "codex": (("codex", "resume"), ("codex", "resume", "--last")),
    "claude": (("claude", "--resume"), ("claude", "--continue")),
    "kimi": (("kimi", "--session"), ("kimi", "--continue")),
}

RESUMABLE_RUNTIMES = tuple(sorted(_RESUMES))


def resume_argv_for(runtime: str, session_id: str | None = None) -> ResumePlan | None:
    """Argv to resume one runtime, or None when it cannot be done.

    None is returned for a runtime with no resume form **and** for a session id
    that fails validation. The second is a refusal, not a fallback: quietly
    turning "resume this session" into "resume whatever ran last" is exactly the
    false success this project exists to prevent, and it would be invisible —
    the agent would come up, look right, and be the wrong conversation.
    """
    forms = _RESUMES.get(runtime)
    if forms is None:
        return None
    exact_prefix, last_argv = forms
    if session_id is None:
        return ResumePlan(argv=last_argv, fidelity=LAST)
    if not SAFE_SESSION_ID.fullmatch(session_id):
        return None
    return ResumePlan(argv=(*exact_prefix, session_id), fidelity=EXACT)


def speak_fidelity(fidelity: str) -> str:
    """What the operator has to hear about which conversation came back."""
    if fidelity == EXACT:
        return "restored by its saved session id"
    if fidelity == LAST:
        return (
            "the most recent session was requested; there is no guarantee it "
            "is the one you meant"
        )
    return "it is unclear which session came back"
