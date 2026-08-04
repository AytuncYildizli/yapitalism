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

## Superset: the capability is already exposed

`pane_clear` does not support Superset panes, and the two panes that actually
blocked were both Superset. Two earlier versions of this section explained why
that was hard to fix. Both were wrong, in the same way, twice.

The first said the host "has no procedure" for emptying a prompt. That came from
probing `127.0.0.1:48900` for `clearPrompt`, `setPrompt`, `sendKeys`, `interrupt`
and `cancel` — five names invented by the person writing the probe.

The second said the host-service exposed "exactly four procedures" and that a new
guarded one would have to be added. That came from probing twelve more names,
including `write`. Also invented. `write` is what the PTY daemon's socket calls
it (`src/main/terminal-host/index.ts`) and what the app's internal tRPC router
calls it — so the name looked researched. It was still a guess about a third
surface.

The actual router is `packages/host-service/src/trpc/router/terminal/terminal.ts`,
a separate package rather than anything under `apps/desktop/src`, which is why
every source search for it came back empty. It has fifteen procedures. One of
them is:

```ts
writeInput: protectedProcedure
    .input(z.object({
        terminalId: z.string(),
        workspaceId: z.string(),
        data: z.string(),
    }))
    .mutation(({ input }) => { ... })
```

Probed live: `terminal.writeInput` returns **401**, not 404 — the same status as
`terminal.send`, which this project already calls successfully. The procedure
exists, it is reachable, and the credentials are in hand.

So Superset can clear a stuck prompt today. Sending `\x15` as `data` empties an
occupied prompt; `\x1b` dismisses a menu. No host change, no upstream request.

### But `writeInput` is unguarded, and that is the real design problem

Note what the input schema does not contain: no `expectRevision`, no
`clientToken`, no `requireEmptyPrompt`. `send` has all three (see its schema
directly below `writeInput` in the same file). `writeInput` takes an arbitrary
string and writes it to a PTY.

That is the correct shape for the host — something has to be able to type — but
it means the guarantees do not come free the way they do with `send`. A Superset
`pane_clear` built on `writeInput` inherits none of them.

The guard therefore has to live here, and it is the same closed-table discipline
`CLEAR_ACTIONS` already uses for tmux: `data` is never caller-supplied, only a
fixed control byte selected by name from a table in this repo.

### Verification is no better than tmux's, and one guess here was also wrong

An earlier draft of this section claimed a Superset `pane_clear` could return
`prompt_empty: true` honestly, because `snapshot` would report whether the prompt
was empty. It does not. `terminal.snapshot` returns `terminalId`, `text`,
`revision`, `cols`, `rows` — nothing else. The host's prompt detector
(`detectTerminalPromptStatus`) runs inside `send`, and speaks only through its
response.

So `prompt_empty` is `None` on both backends, and the proof of clearing remains
the next `send` with `requireEmptyPrompt` not being refused.

`pane_changed` is also weaker here than it looks. Measured live: Escape into an
idle Codex pane whose prompt was already empty still moved the revision
89209293 -> 89209557, because the status line ticks by itself. A running TUI is
never byte-still, so on Superset this field is close to always True. Its absence
would be informative; its presence is not, and it is documented that way in the
backend rather than left to read as signal.

## Consequence

tmux panes can now recover from a blocked prompt by voice. Superset panes still
require the machine, and the tool says so explicitly rather than failing
opaquely.

The honesty rule survives intact: nothing in this ADR lets any code path claim a
prompt is empty without evidence, and the only new mutation is a named
cancellation that cannot commit anything.
