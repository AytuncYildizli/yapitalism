# ADR-0006: Unsticking a blocked prompt

## Context

Two voice sends were refused in live use:

- **Mahobrain / Codex** — `rejected_prompt_not_empty`. The Superset host refused
  because text was already sitting in the prompt.
- **Norget / Claude** — `rejected_prompt_unreadable`. An MCP management menu was
  open, so the host could not verify the prompt.

Both refusals were correct: nothing was written. But `RED` was a dead end. The
receipt named an obstruction and offered no route to delivery, so the operator's
only remedy was to walk to the machine — which defeats the point of speaking to
an agent from away from the desk.

Three separate defects sat behind that.

1. `rejected_prompt_unreadable` had no spoken case and fell through to the
   generic "the write was refused". The cause was computed, carried across a
   process boundary, and discarded in the last function. Mahobrain told the
   operator why; Norget did not.

2. The obstruction was **already visible before the send**. Superset reports a
   per-session state — `idle`, `running`, `waiting_input` — and `norget-clean`
   was `waiting_input` in the same `panes_list` the voice had just read.
   Nothing in the tool description said what that value meant, so the model
   spent a turn discovering a refusal that was advertised in advance.

3. There was no capability to clear a prompt at all. The tool surface was four
   read/write tools.

## Decision

**Name every refusal.** `rejected_prompt_unreadable` says a menu or overlay is
probably covering the prompt. A refusal the operator has to decode is a worse
product than one that tells them what to do.

**Document the state vocabulary** in `panes_list`, with `waiting_input` marked
as "a send WILL be refused; read the pane instead". Prevention beats refusal
when the data was already on hand.

**Add `pane_clear`, with a closed action set.** `escape`, `clear-line`,
`escape-twice`. Not a key-sending tool — an index into a fixed table, the same
shape as `AGENT_LAUNCHERS`, because a free-form key tool reachable by voice is
arbitrary input into a terminal.

**Enter is excluded and must stay excluded.** Escape cancels; Enter commits. A
misdirected Escape loses a half-typed thought. A misdirected Enter selects
whatever menu item is highlighted — on this machine that was
`1. Update now (runs npm install -g @openai/codex)`. The two are not the same
risk class, and a test asserts `Enter`, `C-m` and `KPEnter` appear in no action.

**`pane_clear` never claims the prompt is empty.** tmux declares
`empty_prompt_check = False`; a tool that announced "cleared" on the strength of
a keystroke would be inventing the guarantee the backend just admitted it lacks.
It reports the keys sent, whether the pane changed, and whether a *recognised*
blocking prompt is gone. `prompt_empty` is `None`, always.

**The proof that clearing worked is the next send returning GREEN.** Clearing is
a step, not a claim. Callers clear, then send, and speak the send's receipt.

## Not decided: Superset

`pane_clear` does not support Superset panes, and the two panes that actually
blocked were both Superset.

The first version of this section said the host "has no procedure" for emptying a
prompt. That was measured badly. The probe asked for `clearPrompt`, `setPrompt`,
`sendKeys`, `interrupt` and `cancel` — five names invented by the person writing
the probe — and concluded a capability gap from five misses.

What is actually true is narrower and more useful.

The host-service on `127.0.0.1:48900` is a local fork build
(`hermes-workspace/research-intake/superset-voice-current-main`, running
`dist/main/host-service.js`). Its HTTP surface exposes exactly four procedures:

    terminal.send  terminal.snapshot  terminal.listSessions  workspace.list

Twelve other names return NOT_FOUND, including `write`, `signal`, `resize`,
`clearScrollback`, `getSession`, `detach` and `kill`.

But the app's own tRPC router — `src/lib/trpc/routers/terminal/terminal.ts` —
**does** have `write` (raw PTY bytes) and `signal`. So the capability exists one
layer in. Sending `\x15` or `\x1b` through `terminal.write` would clear a prompt
today.

The host-service simply does not expose it, and that looks deliberate: four
procedures, each guarded. The gap is a boundary decision, not a missing feature.

### Which means exposing `terminal.write` would be the wrong fix

A raw PTY write on the network surface hands any caller arbitrary bytes into a
terminal, bypassing the revision check, the client-token dedup and the
empty-prompt check that are the entire reason a Superset `GREEN` is worth more
than a tmux one. It would trade the guarantee for the convenience.

The narrow addition keeps both:

    terminal.clearPrompt({ terminalId, expectedRevision, clientToken })
      -> { phase, revisionBefore, revisionAfter, promptEmptyAfter }

Guarded exactly like `terminal.send` — revision-checked, token-deduplicated —
writing only a fixed control sequence chosen by the host, never bytes supplied
by the caller. `promptEmptyAfter` is the one fact a client cannot establish for
itself, and the reason this belongs in the host rather than here.

Since the host-service is a local checkout rather than an upstream dependency,
this is a change within reach rather than a request to someone else.

## Consequence

tmux panes can now recover from a blocked prompt by voice. Superset panes still
require the machine, and the tool says so explicitly rather than failing
opaquely.

The honesty rule survives intact: nothing in this ADR lets any code path claim a
prompt is empty without evidence, and the only new mutation is a named
cancellation that cannot commit anything.
