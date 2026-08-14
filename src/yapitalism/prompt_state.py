"""Is the agent's prompt holding text right now?

The guarded Superset host answers this itself, atomically, with
`detectTerminalPromptStatus`. Nothing else can. This module is the fallback for
hosts that do not, and it exists because the alternative was worse than a
heuristic: writing blind into an occupied prompt does not overwrite the staged
text, it *concatenates* with it and submits the merge. A half-typed thought and a
voice instruction arrive as one corrupted message.

So this is deliberately three-valued and deliberately pessimistic. `EMPTY` is the
only answer that permits a write; `HAS_TEXT` and `UNKNOWN` both refuse. Refusing
on UNKNOWN is not an inconvenience to be tuned away — an unrecognised screen is
exactly where the concatenation happens, and the caller has a route out:
`pane_clear` empties the prompt, after which the screen becomes recognisable and
the send goes through. The flow self-heals rather than requiring a person to
guess.

The placeholder strings are observed, not imagined. Every Codex entry below was
read off a live pane on 2026-08-05 while testing `pane_clear`; the pane that held
`once` reported HAS_TEXT and the same pane after clearing showed
"Use /skills to list available skills", which is how an empty Codex prompt
renders. Claude entries are limited to what its empty box actually looks like,
and anything else it draws falls to UNKNOWN rather than being guessed at.
"""

from __future__ import annotations

import re

EMPTY = "empty"
HAS_TEXT = "has_text"
UNKNOWN = "unknown"

#: The glyph each runtime draws at the start of its input line.
_MARKERS: dict[str, tuple[str, ...]] = {
    "codex": ("›", ">"),
    "claude": (">", "❯"),
    "kimi": (">", "❯"),
}

#: Text a runtime shows *inside* an empty prompt. Matched case-insensitively
#: against the whole remainder, so a partially typed line that merely starts with
#: one of these still reads as HAS_TEXT.
_PLACEHOLDERS: dict[str, tuple[str, ...]] = {
    # ADMISSIBILITY RULE: an entry may only be listed if it contains a literal
    # on-screen token a person would not type — `@filename`, `/skills`. Anything a
    # human could plausibly type is inadmissible no matter how often the runtime
    # shows it, because a false EMPTY appends to their sentence and submits it.
    #
    # `"explain this codebase"` was listed and is now removed: somebody typing
    # exactly that and pausing would have had their prompt judged empty. It was the
    # one entry in this table that a human could produce, which is precisely the
    # test that matters — not how confident anyone was that Codex shows it.
    "codex": (
        "use /skills to list available skills",
        # The @filename suggestions rotate; the token is literal on screen.
        "improve documentation in @filename",
        "find and fix a bug in @filename",
        "write tests for @filename",
    ),
    # Empty because nothing has been OBSERVED, not because Claude draws none. Its
    # empty composer really is blank after the marker, which is the common case and
    # judges EMPTY correctly — but when it renders a hint this returns HAS_TEXT and
    # the send is refused. That over-refuses rather than corrupting, and `pane_clear`
    # is the way through, so it stays empty until a real screen is read rather than
    # being filled in from memory of what the hint probably says.
    "claude": (),
    "kimi": (),
}

#: How far back to look for the input line. The prompt is the last thing drawn,
#: and scanning the whole scrollback would match a prompt from earlier in the
#: session.
_TAIL_LINES = 12


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", text)


def detect_prompt_state(pane_text: str, runtime: str) -> str:
    """Judge the prompt from a rendered screen. EMPTY only when sure.

    Returns EMPTY, HAS_TEXT or UNKNOWN. An unknown runtime is UNKNOWN rather than
    optimistically empty: guessing that an unrecognised agent has a clear prompt
    is the one error that corrupts someone's text.
    """
    markers = _MARKERS.get(runtime)
    if markers is None:
        return UNKNOWN
    placeholders = _PLACEHOLDERS.get(runtime, ())
    lines = _strip_ansi(pane_text).rstrip().splitlines()
    # The LAST marker line is the live input line; everything above it is
    # transcript. Codex echoes each submitted prompt back with the same marker, so
    # scanning bottom-up and stopping at the first match is what tracks the composer.
    #
    # This briefly judged EVERY marker line and let any one holding text win, to
    # close a case where a runtime might draw its hint BELOW the composer. That case
    # was reasoned about, never observed - and running the real path showed the
    # observed shape is the opposite: an instruction echoed ABOVE an empty prompt.
    # The "safer" rule refused every pane that had ever been sent to. Bottom-most it
    # is, and the placeholder table is what keeps a hint from reading as input.
    for raw in reversed(lines[-_TAIL_LINES:]):
        line = raw.strip()
        if not line or not line.startswith(markers):
            continue
        body = line[1:].strip()
        if not body:
            return EMPTY
        lowered = body.lower()
        if any(lowered == placeholder for placeholder in placeholders):
            return EMPTY
        # A box-drawing line or a status bar can begin with '>' too, so a body that
        # is only punctuation says nothing either way.
        if not any(character.isalnum() for character in body):
            return UNKNOWN
        return HAS_TEXT
    # No input line found at all - a menu, an overlay, a full-screen diff.
    return UNKNOWN
