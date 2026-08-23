"""tmux backend.

Honest about who does the checking. `tmux send-keys` itself has no notion of an
expected revision, a client token or an occupied prompt — it types. Two of those
three guarantees are therefore enforced here, before the write, and declared
CLIENT rather than HOST because a check and a write in two steps are not the same
thing as a host refusing atomically.

The third stays NONE on purpose. An optimistic-revision guard needs an
expectation from the caller, and `pane_send` supplies none; reading the pane twice
and refusing if it moved would be a different guarantee wearing that name.

This was all-NONE until the prompt check went in, and the reason matters: without
it, `send` appended to whatever was staged in the prompt and the Enter submitted
the merge, so a half-typed thought and a voice instruction arrived as one
corrupted message. Declaring the gap honestly did not make that behaviour
acceptable.

Runtime is derived from the pane's process tree, which is weaker than a host
registry because a pane can change what it runs between the check and the write.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict

from ...adapters.superset import _canary_could_appear, _structured_canary_observed
from ...prompt_state import EMPTY, HAS_TEXT, detect_prompt_state
from ...tokens import BoundedTokens
from ..revision import RevisionTracker
from ..tmux import (
    TmuxError,
    capture_pane,
    send_clear_action,
    list_panes,
    new_agent_session,
    new_resumed_session,
    observe_runtime,
    send_enter,
    send_literal,
)
from .base import (
    AGENT_RUNTIMES,
    CLIENT,
    NONE,
    AcceptanceOutcome,
    BackendCapabilities,
    BackendError,
    BackendPane,
    BackendUnavailable,
    CreateOutcome,
    SendOutcome,
)

# Blocking states seen in real agent startups, matched against the pane after
# the runtime is confirmed. This is recognition, not proof: a state absent from
# this table reads as "nothing recognised", never as "ready". Named accordingly
# so nobody downstream can mistake one for the other.
_BLOCKING_PROMPTS: tuple[tuple[str, str], ...] = (
    # Observed live: claude parks here on a directory it has not seen before,
    # and swallows the first instruction sent to it.
    ("trust this folder", "trust_prompt"),
    ("do you trust", "trust_prompt"),
    ("yes, i trust", "trust_prompt"),
    ("sign in to", "auth_prompt"),
    ("log in to", "auth_prompt"),
    ("paste your api key", "auth_prompt"),
    ("press enter to continue", "confirm_prompt"),
)


#: How long to keep looking for a blocking dialog after the runtime is confirmed.
#: An agent execs, then draws its dialogs, so this window is the difference
#: between reporting a started agent and reporting a ready one.
_BLOCK_SETTLE_SECONDS = 3.0


#: Between pasting the text and pressing Enter, and between Enter and reading
#: the screen back. Measured, not tuned: a booting codex dropped a back-to-back
#: Enter entirely.
_SUBMIT_SETTLE_SECONDS = 0.4
_SUBMIT_VERIFY_SECONDS = 0.8


#: How far back a dialog may be recognised. A dialog occupies the screen NOW; text
#: further back is history.
_DIALOG_TAIL_LINES = 40


def detect_blocking_prompt(pane_text: str) -> str:
    """Label a known blocking prompt, or "" when none is recognised.

    Scoped to the recent screen. `send` captures 1000 lines for revision tracking,
    and matching a phrase anywhere in that made every dialog permanent: a pane that
    ever showed "Do you trust this folder" was refused forever, and `pane_clear`
    could not help because the text sat in scrollback rather than in the prompt.
    Since `panes_create` produces exactly such a pane, the create-then-send flow —
    the whole tmux path — was unusable.
    """
    # rstrip first: a capture is padded with the pane's empty rows, and in a pane
    # whose content sits at the top the tail window would land entirely in that
    # padding and see nothing. `detect_prompt_state` strips for the same reason.
    haystack = "\n".join(pane_text.rstrip().splitlines()[-_DIALOG_TAIL_LINES:]).lower()
    for needle, label in _BLOCKING_PROMPTS:
        if needle in haystack:
            return label
    return ""


TMUX_CAPABILITIES = BackendCapabilities(
    # Real, and the repeats it protects against are this process's own - a retried
    # voice turn, a client resend - so tracking them here is fully effective
    # rather than approximate.
    idempotent_dispatch=CLIENT,
    # Stays NONE, and not for lack of trying. An optimistic-revision guard needs an
    # expectation from the caller: "write only if the pane still looks as it did
    # when I read it". No caller supplies one - `pane_send` takes no expected
    # revision - so there is nothing to compare against. Reading the pane twice and
    # refusing if it moved would be a DIFFERENT guarantee (don't type into a busy
    # pane) wearing this one's name, which is the overclaim these levels exist to
    # stop. Making it real means threading an expectation through the MCP surface.
    optimistic_revision=NONE,
    # CLIENT: `send` judges the prompt before writing and declines on anything
    # short of a confident EMPTY. Weaker than the host's atomic check - the screen
    # can change between the read and the write - but it is a check, and the
    # alternative was appending to somebody's half-typed text and submitting the
    # merge.
    empty_prompt_check=CLIENT,
    runtime_detection="process_tree",
)


class TmuxBackend:
    def __init__(self) -> None:
        self._revisions = RevisionTracker()
        #: Tokens whose write reached the pane. tmux cannot deduplicate for us.
        #: Bounded — this server runs for weeks under launchd.
        self._landed_tokens = BoundedTokens()
        #: One lock per pane, held across the whole send transaction.
        #:
        #: A tmux write is capture -> guards -> type -> Enter -> capture, and every
        #: step is a separate subprocess. FastMCP runs sync tools on a threadpool, so
        #: two concurrent sends to one pane interleaved as type-A, type-B, Enter,
        #: Enter: both payloads merged into one prompt, one submit, and BOTH receipts
        #: claiming dispatched. The canaries merge too, so the proof is misattributed
        #: as well as the delivery.
        #:
        #: Keyed per pane rather than global so different panes still run in
        #: parallel - the race is within a pane, and a global lock would serialise a
        #: fleet for no safety gain.
        self._pane_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        #: Guards creation of the per-pane locks themselves.
        self._locks_guard = threading.Lock()
        #: Tokens burned by a write that has not been confirmed to complete.
        self._ambiguous_tokens: set[str] = set()

    @property
    def namespace(self) -> str:
        return "tmux"

    def capabilities(self) -> BackendCapabilities:
        return TMUX_CAPABILITIES

    def list_panes(self) -> list[BackendPane]:
        try:
            panes = list_panes()
        except TmuxError as error:
            # tmux missing is absence, like a Superset that was never set up: the
            # operator has not got that backend, and calling it a failure makes an
            # ordinary machine look broken. Anything else — a timeout, a refusal, an
            # unparseable answer — is a real failure and stays one.
            if "not installed" in str(error):
                raise BackendUnavailable(
                    "tmux is not installed on this machine"
                ) from None
            raise BackendError(str(error)) from None
        return [
            BackendPane(
                target_id=pane.target_id,
                label=f"{pane.session_name}:{pane.window_index}.{pane.pane_index}",
                runtime=pane.runtime,
                width=pane.width,
                height=pane.height,
                dead=pane.dead,
                detail=pane.current_command,
                folder=pane.current_path.rstrip("/").rsplit("/", 1)[-1],
                path=pane.current_path,
            )
            for pane in panes
        ]

    def clear_prompt(self, target_id: str, action: str = "escape") -> dict[str, object]:
        """Try to unstick a pane with one named key action.

        Deliberately does NOT claim the prompt is now empty. tmux cannot verify
        that — it is why empty_prompt_check is False — and a tool that announced
        "cleared" on the strength of a keystroke would be inventing the
        guarantee the backend just admitted it lacks.

        What it can say is narrower and true: what it sent, whether the pane
        changed, and whether a blocking prompt it could recognise before is gone
        now. The real proof that clearing worked is the next guarded send
        succeeding, so callers should treat this as a step and read the send's
        receipt as the verdict.
        """
        try:
            before = capture_pane(target_id, 200)
            blocking_before = detect_blocking_prompt(before)
            keys = send_clear_action(target_id, action)
            time.sleep(0.4)  # let the TUI redraw before looking
            after = capture_pane(target_id, 200)
        except TmuxError as error:
            raise BackendError(str(error)) from None

        blocking_after = detect_blocking_prompt(after)
        return {
            "action": action,
            "keys_sent": list(keys),
            "pane_changed": after != before,
            "blocking_before": blocking_before,
            "blocking_after": blocking_after,
            "recognised_block_cleared": bool(blocking_before) and not blocking_after,
            # Never a claim of emptiness — see the docstring.
            "prompt_empty": None,
        }

    def read_pane(self, target_id: str, lines: int) -> str:
        try:
            return capture_pane(target_id, lines)
        except TmuxError as error:
            raise BackendError(str(error)) from None


    def resume_pane(
        self,
        session_name: str,
        runtime: str,
        cwd: str,
        session_id: str | None = None,
        *,
        timeout: float = 10.0,
    ) -> CreateOutcome:
        """Bring a dead agent back in a fresh detached session.

        Reports `fidelity` alongside everything `create_pane` reports, because a
        caller told only "a pane exists" cannot say which conversation is in it.
        Without a session id the runtime is asked for its most recent one, and that
        is `last` — never spoken as "resumed your session".

        An unusable session id raises rather than falling back to `last`: turning
        "resume this session" into "resume whatever ran last" would come up looking
        correct and be the wrong conversation.
        """
        try:
            target_id, fidelity = new_resumed_session(
                session_name, runtime, cwd, session_id
            )
        except TmuxError as error:
            raise BackendError(str(error)) from None
        return self._settle(
            target_id, session_name, runtime, cwd, timeout=timeout, fidelity=fidelity
        )

    def create_pane(
        self,
        session_name: str,
        runtime: str,
        cwd: str,
        *,
        timeout: float = 10.0,
    ) -> CreateOutcome:
        """Start one known agent in a fresh detached session.

        The pane id comes from tmux itself rather than a list-panes scan, so a
        session someone else creates in the same moment can never be mistaken
        for this one.

        Creation and confirmation are reported separately. tmux returns a pane
        id the instant the session exists, which is before the agent has
        execed — and if the binary is missing the pane survives with the command
        already dead. So the process tree is polled until it agrees, and a
        timeout is reported as an unconfirmed runtime rather than a failure:
        the session really does exist and the caller must be told about it, or
        it becomes an orphan nobody knows to clean up.
        """
        try:
            target_id = new_agent_session(session_name, runtime, cwd)
        except TmuxError as error:
            raise BackendError(str(error)) from None
        return self._settle(target_id, session_name, runtime, cwd, timeout=timeout)

    def _settle(
        self,
        target_id: str,
        session_name: str,
        runtime: str,
        cwd: str,
        *,
        timeout: float,
        fidelity: str = "",
    ) -> CreateOutcome:
        """Confirm what is running, then look for a dialog in front of it.

        Shared by create and resume: a resumed agent draws the same trust prompt
        and the same update menu a fresh one does, and reporting readiness for one
        while not the other would be an accident of which path was written first.
        """
        deadline = time.monotonic() + timeout
        observed = "unknown"
        while time.monotonic() < deadline:
            try:
                observed = observe_runtime(target_id)
            except TmuxError as error:
                raise BackendError(str(error)) from None
            if observed == runtime:
                break
            time.sleep(min(0.3, max(0.0, deadline - time.monotonic())))

        # A confirmed runtime is not a ready agent. Look for a blocking prompt
        # before anyone sends work into what is actually a dialog box.
        #
        # Polled rather than read once, because the dialogs arrive AFTER the exec
        # this loop just confirmed. Measured live: a fresh codex pane reported
        # runtime_confirmed with blocked_on empty, and a second later was sitting
        # on "1. Update now (runs `npm install -g @openai/codex`)" — so the create
        # call said "codex is running" about an agent behind an install menu. One
        # early read is indistinguishable from no read at all here.
        blocked_on = ""
        settle_deadline = time.monotonic() + _BLOCK_SETTLE_SECONDS
        while time.monotonic() < settle_deadline:
            try:
                blocked_on = detect_blocking_prompt(capture_pane(target_id, 60))
            except TmuxError:
                blocked_on = ""
            if blocked_on:
                break
            time.sleep(min(0.3, max(0.0, settle_deadline - time.monotonic())))

        return CreateOutcome(
            target_id=target_id,
            created=True,
            runtime_requested=runtime,
            runtime_observed=observed,
            session_name=session_name,
            cwd=cwd,
            reason="" if observed == runtime else "runtime_not_observed",
            blocked_on=blocked_on,
            fidelity=fidelity,
        )

    def send(
        self,
        target_id: str,
        text: str,
        *,
        canary: str | None,
        client_token: str,
    ) -> SendOutcome:
        """Type text and submit it.

        tmux offers no expected-revision guard, no client-token dedup, and no
        way to refuse a write when the prompt already holds text. `client_token`
        is accepted for interface parity and deliberately unused: pretending to
        deduplicate would be worse than declaring the gap in capabilities.
        """
        # Held across the ENTIRE transaction - capture, guards, type, Enter, capture.
        # Anything less leaves the window the interleaving used: two sends could both
        # pass the guards, then type-A/type-B/Enter/Enter into one merged prompt with
        # both receipts claiming delivery.
        with self._pane_lock(target_id):
            try:
                before = capture_pane(target_id, 1000)

                # ONE observation of the runtime, reused by all three decisions
                # below. Each call is a `tmux list-panes -a` plus a `ps -A`, and
                # calling it per-decision inside the lock was not only three times
                # the subprocesses: it made the value that picks the prompt markers,
                # the value that passes the gate, and the value in the receipt three
                # independent readings of a pane that can change between them.
                runtime = self._runtime_of(target_id)

                # FIRST, because it is a boundary and not a heuristic. `classify_tree`
                # exists because "a pane that used to run an agent and now runs a plain
                # shell must never be treated as an agent target, or a spoken
                # instruction becomes an arbitrary shell command" — and the send path
                # never checked. Typing into a shell and pressing Enter IS running a
                # command, reachable by voice.
                #
                # It has to run before the dialog table, not after. A shell pane whose
                # screen happens to hold a dialog phrase — an agent that just exited, a
                # catted log — was being reported as `rejected_trust_prompt`, which
                # sends the operator to answer a dialog that is not there and hides the
                # refusal that actually matters. The send was refused either way; the
                # sentence was wrong, and the boundary was a side effect of a keyword
                # match, which is exactly what this check exists not to be.
                if runtime not in AGENT_RUNTIMES:
                    return SendOutcome(
                        phase="rejected_not_an_agent",
                        dispatched=False,
                        runtime=runtime,
                        reason=f"pane is running {runtime or 'something unrecognised'}",
                    )

                # Refuse rather than type into a dialog. This is not only a receipt
                # concern: a blocking prompt is usually a menu, so text followed by
                # Enter can SELECT one of its options. On the first live run the
                # pane was sitting on "1. Yes, I trust this folder / 2. No, exit"
                # and the send went straight into it.
                #
                # tmux cannot enforce an empty prompt, which is why
                # empty_prompt_check is declared False - but declining a state we
                # can positively recognise is strictly better than writing blind.
                # The prompt state is the stronger signal, so it is consulted
                # first. A dialog REPLACES the input line; if the input line is
                # present and EMPTY, the agent is accepting input and any dialog
                # phrase still on screen is a leftover. Keyword recognition then
                # only decides how to NAME a refusal, never whether one happens.
                prompt_state = detect_prompt_state(before, runtime)
                blocking = detect_blocking_prompt(before) if prompt_state != EMPTY else ""
                if blocking:
                    return SendOutcome(
                        phase=f"rejected_{blocking}",
                        dispatched=False,
                        runtime=runtime,
                        reason=blocking,
                    )

                # The dialog table above recognises seven known screens. This catches
                # the case it cannot: a prompt simply holding text. send-keys does not
                # replace that text, it appends to it, and the Enter submits the merge
                # — so a half-typed thought and a voice instruction arrive as one
                # corrupted message. Refusing on anything short of a confident EMPTY
                # is the same rule the guarded Superset host applies, and `pane_clear`
                # is the way out of both.
                if prompt_state != EMPTY:
                    return SendOutcome(
                        phase=(
                            "rejected_prompt_not_empty"
                            if prompt_state == HAS_TEXT
                            else "rejected_prompt_unreadable"
                        ),
                        dispatched=False,
                        runtime=runtime,
                        reason=prompt_state,
                    )

                # Real dedup, not parity. The repeats this protects against are this
                # process's own — a retried voice turn, a client resend — so tracking
                # them here is fully effective rather than approximate.
                if client_token in self._landed_tokens:
                    # A token burned by a write that failed ambiguously is not a
                    # duplicate: nothing may have arrived. Calling it one would claim a
                    # delivery that never happened.
                    ambiguous = client_token in self._ambiguous_tokens
                    return SendOutcome(
                        phase=(
                            "duplicate_after_ambiguous_write"
                            if ambiguous
                            else "duplicate_ignored"
                        ),
                        dispatched=False,
                        runtime=runtime,
                        reason=(
                            "client_token was burned by a write that failed ambiguously"
                            if ambiguous
                            else "client_token already used for a write that landed"
                        ),
                    )

                revision_before = self._revisions.observe(target_id, before)
                if canary is not None:
                    if _canary_could_appear(before, canary):
                        raise BackendError("canary was already present in the pane")
                    if _canary_could_appear(text, canary):
                        raise BackendError("canary must not occur in the submitted text")
                # Burned before the write: if send-keys half-succeeds the text is
                # already in the pane and a retry under the same token must still be
                # refused. Marked ambiguous until both calls return, so the refusal can
                # say whether anything is known to have landed.
                self._landed_tokens.add(client_token)
                self._ambiguous_tokens.add(client_token)
                send_literal(target_id, text)
                # A beat between paste and Enter, then VERIFY the composer let go
                # of the text. Back-to-back send-keys measured a real loss on
                # 2026-08-23: a booting codex ingested the paste, dropped the
                # Enter, and the message sat staged at "0 in · 0 out" while this
                # returned "injected" - the same staged-not-submitted failure the
                # Superset path was taught about in 0.2.x, rediscovered on tmux by
                # the demo command. A delayed Enter submitted it, so one retry is
                # made with a longer settle; after that the truth is "staged".
                time.sleep(_SUBMIT_SETTLE_SECONDS)
                send_enter(target_id)
                self._ambiguous_tokens.discard(client_token)
                submitted = False
                for attempt in range(2):
                    time.sleep(_SUBMIT_VERIFY_SECONDS * (attempt + 1))
                    after = capture_pane(target_id, 1000)
                    if detect_prompt_state(after, runtime) != HAS_TEXT:
                        # EMPTY is a proven submit. UNKNOWN means the screen is
                        # busy redrawing - the agent taking the message looks
                        # exactly like that - so only a composer STILL holding
                        # text counts as not submitted.
                        submitted = True
                        break
                    send_enter(target_id)
                if not submitted:
                    after = capture_pane(target_id, 1000)
            except TmuxError as error:
                raise BackendError(str(error)) from None
            if not submitted:
                return SendOutcome(
                    phase="staged_not_submitted",
                    dispatched=False,
                    runtime=runtime,
                    revision_before=revision_before,
                    revision_after=self._revisions.observe(target_id, after),
                    reason="text remains in the composer after two Enters",
                )
            return SendOutcome(
                phase="injected",
                dispatched=True,
                # The runtime the gate ADMITTED, not a fresh reading. Re-observing
                # here would let the receipt name something the guards never saw,
                # and there is nothing to gain: this value is reported, not checked.
                runtime=runtime,
                revision_before=revision_before,
                revision_after=self._revisions.observe(target_id, after),
            )

    def await_acceptance(
        self,
        target_id: str,
        canary: str | None,
        *,
        timeout: float = 8.0,
        client_token: str | None = None,
    ) -> AcceptanceOutcome:
        """Watch this pane for the canary.

        `client_token` names which send is being proven. tmux keeps no per-send
        context - the canary is unique and the pane is the target, so there is
        nothing here to look up - but the parameter is part of the backend contract
        and omitting it broke every tmux send the moment the caller started passing
        it. Accepting and ignoring it is the honest shape: the interface is uniform,
        and no proof is invented from it.
        """
        if canary is None:
            # Never testable, which is not the same as failed.
            return AcceptanceOutcome(False, 0, "no_canary")
        deadline = time.monotonic() + timeout
        attempts = 0
        while time.monotonic() < deadline:
            attempts += 1
            try:
                text = capture_pane(target_id, 1000)
            except TmuxError as error:
                raise BackendError(str(error)) from None
            if _structured_canary_observed(text, canary):
                return AcceptanceOutcome(True, attempts)
            time.sleep(min(0.3, max(0.0, deadline - time.monotonic())))
        return AcceptanceOutcome(False, attempts, "canary_timeout")

    def _pane_lock(self, target_id: str) -> threading.Lock:
        """The lock for one pane, created once.

        `defaultdict` is not itself atomic across threads under free-threading, so
        the creation is guarded. Cheap: taken once per pane, not per send.
        """
        with self._locks_guard:
            return self._pane_locks[target_id]

    def _runtime_of(self, target_id: str) -> str:
        for pane in self.list_panes():
            if pane.target_id == target_id:
                return pane.runtime
        return "unknown"
