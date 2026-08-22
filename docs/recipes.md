# Recipes: things a scheduled agent can do with six pane tools

Yapitalism's MCP surface is deliberately small — `panes_list`, `pane_read`,
`pane_send`, `panes_create`, `panes_resume`, `pane_clear` — and everything here
composes from it. Each recipe is one scheduled agent turn: any agent runner with
MCP access and a scheduler works (these were built with a cron-capable gateway
driving the loopback HTTP server; `claude -p` or `codex exec` under plain cron
work the same way).

The first recipe is running in production and caught, on its first real day, a
coding agent silently holding a **MetaMask token-launch approval** — exactly the
kind of thing that must reach a human and had been reaching no one.

Two rules every recipe below obeys:

1. **Watchers read; they never write.** Say it in the prompt ("never type into a
   pane — read only"). A watcher that answers dialogs is an auto-approver with
   extra steps.
2. **Silence must be an instruction, not a hope.** Tell the agent exactly what
   to do when there is nothing to report ("send nothing, output only NOOP"), or
   every tick becomes a message.

## 1. Permission watch — nothing waits silently

The failure this exists for: a task dispatched in the morning sat on a
permission dialog until night, and nothing said so. Refusing to auto-approve is
correct; being silent about it is not.

Schedule: every 20 minutes. Prompt (works as-is):

> You are a watcher. Call yapitalism `panes_list`. For every pane whose runtime
> is codex, claude, kimi or opencode: if its state is waiting_input, or you are
> suspicious, call `pane_read` for the last 30 lines and look for a pending
> permission/approval dialog (permission, allow/deny, approve, trust, y/n, a
> numbered option menu). If any pane is waiting, send ONE short message naming
> the project, the agent, and what the screen is asking, one sentence per pane.
> If nothing is waiting, send nothing and output only NOOP. Never type into a
> pane — read only.

## 2. Provider-outage watch — "not writing" is usually "model is down"

A free-tier endpoint answering `Service Unavailable` mid-task looks exactly like
a wedged agent from the outside. Measured live: an OpenCode session stopped with
`Service Unavailable` rendered in its composer, and the operator's first theory
was that something had locked it up. Same watcher shape as recipe 1, different
needles: `Service Unavailable`, `rate limit`, `overloaded`, `quota`,
`connection refused` in the pane tail — plus the advice that a retry or a model
switch fixes it, so the alert carries its own way out.

## 3. Wrap-up digest — did the evening batch actually finish?

At a fixed hour, read every agent pane and send one message: per pane, one line —
finished (and what it says it did), still running, or waiting. The receipt rule
applies to the summary too: an idle pane whose last line *looks* done is
"idle, last output says X", never "done". Only a canary-proven send is proof,
and a digest has none.

## 4. Context-pressure alert — die before the cliff, not at it

Agents render their remaining context in the status line (OpenCode:
`202.3K (20%)`). A task that starts with plenty and burns to single digits will
fail mid-edit an hour later. Watch for the percentage dropping below a
threshold and name the pane while there is still room to wrap up cleanly.

## 5. Exit watch — an agent that died is not an agent that is quiet

`panes_list` reports a pane's runtime from measurement (process tree, or the
host's agent registry). A pane that was `kimi` an hour ago and is `unknown` now
is an exited agent. Alert with the pane name and offer `panes_resume` — but only
offer it: resume replaces a session and can discard in-memory context, so it is
the operator's call, spoken back through whatever channel they answer on.

## What not to build

An auto-approver. Every recipe above is a *notifier* precisely because the
dialogs they detect are the moments a human is supposed to decide — the
MetaMask catch is the argument. If a permission is routine enough to approve
automatically, the right fix is the agent's own permission config (allow it),
not a robot pressing "yes" from outside.
