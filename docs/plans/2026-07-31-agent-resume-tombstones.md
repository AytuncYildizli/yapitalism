# Dead-Agent Resume with Prior Context — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let voice restart an agent that has already exited, resuming its prior conversation context, with a receipt that distinguishes "process launched" from "context actually restored."

**Architecture:** When an agent binding is deleted on exit, first copy its resume-critical fields into a new `terminal_agent_tombstones` table that holds **no foreign key** to `terminal_sessions` — the existing binding table cascade-deletes, so a tombstone cannot live there. A pure per-runtime mapper turns a tombstone into resume argv. A pure evidence detector classifies whether the restarted agent restored context, degrading to `unknown` rather than claiming success.

**Tech Stack:** TypeScript, Bun test runner, Drizzle ORM over SQLite, existing `TerminalAgentStore` / `TerminalAgentBindingPersistence`.

## Executed 2026-07-31 — branch `cor/agent-resume-tombstones` (5 commits, `7024abc`..`63d0c15`)

All five tasks are implemented and committed. 41 new tests; `bun test src/terminal-agents/ src/terminal/` = 150 pass / 0 fail; typecheck and Biome clean. The running desktop artifact was not rebuilt (`app.asar` mtime unchanged, host PID 19883 still up).

Where reality differed from this plan — the specifics below are corrections, not preferences:

1. **`claude -c/--continue` exists.** Task 4's premise that claude is unresumable without a session id was wrong. It now maps to `["claude", "--continue"]` with `fidelity: "last"`. Long flags used throughout (`--session` over `-S`).
2. **A malformed session id is refused, not downgraded.** Falling back to "resume the last session" when a specific one was requested would be a fidelity lie, so it returns `null`.
3. **Task 5's ANSI regex was broken.** It omitted the `\x1B` lead byte, so `[@-Z\\-_]` would have stripped every capital letter. Replaced with the CSI *and* OSC patterns from `sanitizePromptForPty`; OSC matters because agent TUIs set window titles with arbitrary payloads.
4. **`classifyResume` normalizes twice.** The anchored `^error:` pattern needs line structure; phrase matching needs whitespace flattened so a hard wrap cannot split a phrase.
5. **No shared `createTestDb` exists.** `persistence.test.ts` defines a local one; that pattern was copied, plus `PRAGMA foreign_keys = ON` — off by default in SQLite, so without it the survives-the-cascade test would have been vacuous. Sessions are seeded with `originWorkspaceId: null` because that column is itself a FK to `workspaces`.
6. **Naming/typing follow the house style:** `SqliteAgentTombstoneStore` (matching `SqliteTerminalAgentBindingPersistence`), and `HostDb` rather than `any` with a lint suppression.
7. **The store depends on a binding-shaped `AgentTombstoneSink`**, not on building a row itself. This keeps `store.ts` free of a clock, id generation, and the `operator-view` import, avoiding an import cycle. A throwing sink is logged and never blocks terminal teardown.
8. **`launchCommand` is already exposed.** `TerminalOperatorSession` carries it, so the out-of-scope item below overstated the gap: only `agentSessionId` still needs projecting.

## Global Constraints

- **Implementation worktree:** `~/hermes-workspace/research-intake/superset-prod-mcp-deploy-20260730`. Branch off as `cor/agent-resume-tombstones`. Never commit to `main`.
- **Never rebuild the running desktop app in place.** The live voice path runs `superset-voice-current-main/.../Superset.app`. This plan produces dormant host-service code plus one migration.
- **The load-bearing schema decision:** `terminal_agent_bindings.terminalId` is `.primaryKey().references(() => terminalSessions.id, { onDelete: "cascade" })`. Adding `exited_at` to that table **cannot work** — SQLite cascade-deletes the row when the terminal session goes away. Tombstones require a separate table with no `.references()`.
- **Never widen secret exposure.** `launchCommand` may contain flags with sensitive values. Every path that returns it to MCP must pass through `packages/shared/src/sensitive-text.ts`. Never log it raw.
- **Fail closed.** A tombstone with no usable resume handle must be reported unresumable, never launched as a fresh session pretending to be a resume. Silent downgrade from "resumed" to "new session" is the exact false-GREEN this project exists to prevent.
- **Bounded retention.** Tombstones are TTL-capped and per-workspace count-capped. This is a resume affordance, not an audit log.
- **No `SKILL.md` edits and no new MCP tools in this plan.** Voice wiring is a follow-up (see Out of Scope) so this plan lands inert.
- **Commit after every task.** Conventional commits, no `Co-Authored-By` trailer.

## File Structure

| File | Responsibility |
| --- | --- |
| `src/db/schema.ts` (modify) | Add `terminalAgentTombstones` table, no FK. |
| `drizzle/00XX_agent_tombstones.sql` (generated) | Migration. |
| `src/terminal-agents/tombstone-retention.ts` (create) | Pure TTL + cap selection of rows to prune. |
| `src/terminal-agents/tombstone-retention.test.ts` (create) | Unit tests. |
| `src/terminal-agents/tombstone-store.ts` (create) | Drizzle read/write/prune for tombstones. |
| `src/terminal-agents/tombstone-store.test.ts` (create) | In-memory SQLite tests. |
| `src/terminal-agents/store.ts` (modify) | Capture a tombstone before `deleteTerminal`. |
| `src/terminal-agents/resume-argv.ts` (create) | Pure `(runtime, tombstone) → argv \| null`. |
| `src/terminal-agents/resume-argv.test.ts` (create) | Fixture tests per runtime. |
| `src/terminal-agents/resume-evidence.ts` (create) | Pure pane-text → resume phase classifier. |
| `src/terminal-agents/resume-evidence.test.ts` (create) | Fixture tests incl. the unknown path. |

---

### Task 1: Tombstone table with no cascade

**Files:**
- Modify: `packages/host-service/src/db/schema.ts` (after `terminalAgentBindings`, ~line 66)
- Generated: `packages/host-service/drizzle/`

**Interfaces:**
- Produces: `terminalAgentTombstones` Drizzle table; row type
  `AgentTombstoneRow = { id: string; workspaceId: string; terminalId: string; agentId: string; agentSessionId: string | null; definitionId: string | null; presetId: string | null; label: string | null; launchCommand: string | null; runtime: string; startedAt: number; exitedAt: number }`

- [ ] **Step 1: Add the table definition**

`terminalId` is stored as plain text history, deliberately **not** a reference — that is the entire point of this table.

```ts
export const terminalAgentTombstones = sqliteTable(
	"terminal_agent_tombstones",
	{
		// Own identity. No FK to terminal_sessions: this row must outlive the
		// terminal that produced it, so it cannot participate in that cascade.
		id: text().primaryKey(),
		workspaceId: text("workspace_id").notNull(),
		terminalId: text("terminal_id").notNull(),
		agentId: text("agent_id").notNull().$type<AgentIdentityId>(),
		agentSessionId: text("agent_session_id"),
		definitionId: text("definition_id").$type<AgentDefinitionId>(),
		presetId: text("preset_id"),
		label: text(),
		launchCommand: text("launch_command"),
		runtime: text().notNull(),
		startedAt: integer("started_at").notNull(),
		exitedAt: integer("exited_at").notNull(),
	},
	(table) => [
		index("terminal_agent_tombstones_workspace_idx").on(table.workspaceId),
		index("terminal_agent_tombstones_exited_at_idx").on(table.exitedAt),
	],
);
```

- [ ] **Step 2: Generate the migration**

Run: `cd packages/host-service && bun run drizzle-kit generate`
Expected: a new `drizzle/00XX_*.sql` plus a `meta/` snapshot and `_journal.json` entry.

- [ ] **Step 3: Verify the generated SQL has no foreign key**

Run: `grep -i 'foreign\|references' packages/host-service/drizzle/00XX_*.sql`
Expected: **no output.** If a FOREIGN KEY clause appears, the table definition is wrong — fix it before continuing, because the cascade will silently eat every tombstone.

- [ ] **Step 4: Commit**

```bash
git add packages/host-service/src/db/schema.ts packages/host-service/drizzle
git commit -m "feat(host-service): add cascade-free agent tombstone table"
```

---

### Task 2: Retention policy as a pure function

Retention is decided in a pure function so it can be tested without a database and reviewed without reading Drizzle.

**Files:**
- Create: `packages/host-service/src/terminal-agents/tombstone-retention.ts`
- Test: `packages/host-service/src/terminal-agents/tombstone-retention.test.ts`

**Interfaces:**
- Consumes: `AgentTombstoneRow` (Task 1).
- Produces:
  - `const TOMBSTONE_TTL_MS = 7 * 24 * 60 * 60 * 1000`
  - `const TOMBSTONE_MAX_PER_WORKSPACE = 20`
  - `selectExpiredTombstoneIds(rows: Pick<AgentTombstoneRow, "id" | "workspaceId" | "exitedAt">[], now: number): string[]`

- [ ] **Step 1: Write the failing test**

```ts
import { expect, test } from "bun:test";
import {
	selectExpiredTombstoneIds,
	TOMBSTONE_MAX_PER_WORKSPACE,
	TOMBSTONE_TTL_MS,
} from "./tombstone-retention";

const NOW = 1_800_000_000_000;

test("rows inside the ttl and under the cap are kept", () => {
	const rows = [{ id: "a", workspaceId: "w1", exitedAt: NOW - 1000 }];
	expect(selectExpiredTombstoneIds(rows, NOW)).toEqual([]);
});

test("rows older than the ttl are selected for pruning", () => {
	const rows = [
		{ id: "old", workspaceId: "w1", exitedAt: NOW - TOMBSTONE_TTL_MS - 1 },
		{ id: "new", workspaceId: "w1", exitedAt: NOW - 1000 },
	];
	expect(selectExpiredTombstoneIds(rows, NOW)).toEqual(["old"]);
});

test("beyond the per-workspace cap the oldest rows are pruned first", () => {
	const rows = Array.from({ length: TOMBSTONE_MAX_PER_WORKSPACE + 3 }, (_, i) => ({
		id: `r${i}`,
		workspaceId: "w1",
		exitedAt: NOW - (i + 1) * 1000,
	}));
	const pruned = selectExpiredTombstoneIds(rows, NOW);
	expect(pruned.length).toBe(3);
	// Highest index == oldest exitedAt.
	expect(pruned).toContain(`r${TOMBSTONE_MAX_PER_WORKSPACE + 2}`);
	expect(pruned).not.toContain("r0");
});

test("the cap is applied per workspace, not globally", () => {
	const rows = [
		...Array.from({ length: TOMBSTONE_MAX_PER_WORKSPACE }, (_, i) => ({
			id: `w1-${i}`, workspaceId: "w1", exitedAt: NOW - (i + 1) * 1000,
		})),
		...Array.from({ length: TOMBSTONE_MAX_PER_WORKSPACE }, (_, i) => ({
			id: `w2-${i}`, workspaceId: "w2", exitedAt: NOW - (i + 1) * 1000,
		})),
	];
	expect(selectExpiredTombstoneIds(rows, NOW)).toEqual([]);
});

test("a row is reported once even when both expired and over the cap", () => {
	const rows = Array.from({ length: TOMBSTONE_MAX_PER_WORKSPACE + 2 }, (_, i) => ({
		id: `r${i}`, workspaceId: "w1",
		exitedAt: NOW - TOMBSTONE_TTL_MS - (i + 1),
	}));
	const pruned = selectExpiredTombstoneIds(rows, NOW);
	expect(new Set(pruned).size).toBe(pruned.length);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/host-service && bun test src/terminal-agents/tombstone-retention.test.ts`
Expected: FAIL — cannot resolve `./tombstone-retention`.

- [ ] **Step 3: Implement retention**

```ts
export const TOMBSTONE_TTL_MS = 7 * 24 * 60 * 60 * 1000;
export const TOMBSTONE_MAX_PER_WORKSPACE = 20;

interface RetentionRow {
	id: string;
	workspaceId: string;
	exitedAt: number;
}

export function selectExpiredTombstoneIds(
	rows: RetentionRow[],
	now: number,
): string[] {
	const doomed = new Set<string>();
	const byWorkspace = new Map<string, RetentionRow[]>();

	for (const row of rows) {
		if (now - row.exitedAt > TOMBSTONE_TTL_MS) {
			doomed.add(row.id);
			continue;
		}
		const bucket = byWorkspace.get(row.workspaceId);
		if (bucket) bucket.push(row);
		else byWorkspace.set(row.workspaceId, [row]);
	}

	for (const bucket of byWorkspace.values()) {
		if (bucket.length <= TOMBSTONE_MAX_PER_WORKSPACE) continue;
		// Newest first, then drop everything past the cap.
		bucket.sort((a, b) => b.exitedAt - a.exitedAt);
		for (const row of bucket.slice(TOMBSTONE_MAX_PER_WORKSPACE)) {
			doomed.add(row.id);
		}
	}

	return [...doomed];
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `bun test src/terminal-agents/tombstone-retention.test.ts`
Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
git add packages/host-service/src/terminal-agents/tombstone-retention.ts packages/host-service/src/terminal-agents/tombstone-retention.test.ts
git commit -m "feat(host-service): add bounded tombstone retention policy"
```

---

### Task 3: Capture a tombstone before the binding is deleted

`store.ts:206 deleteTerminal()` currently drops the binding from memory and persistence. The tombstone must be written from the in-memory binding **before** that happens — once the row is gone, `agentSessionId` and `launchCommand` are unrecoverable.

**Files:**
- Create: `packages/host-service/src/terminal-agents/tombstone-store.ts`
- Modify: `packages/host-service/src/terminal-agents/store.ts` (around `deleteTerminal`, ~line 206)
- Test: `packages/host-service/src/terminal-agents/tombstone-store.test.ts`

**Interfaces:**
- Consumes: `terminalAgentTombstones` (Task 1), `selectExpiredTombstoneIds` (Task 2), `TerminalAgentBinding`.
- Produces:
  - `interface AgentTombstonePersistence { record(row: AgentTombstoneRow): void; listByWorkspace(workspaceId: string): AgentTombstoneRow[]; get(id: string): AgentTombstoneRow | null; prune(now: number): number }`
  - `class DrizzleAgentTombstoneStore implements AgentTombstonePersistence`
  - `tombstoneFromBinding(binding: TerminalAgentBinding, runtime: string, exitedAt: number, id: string): AgentTombstoneRow`

- [ ] **Step 1: Write the failing test**

```ts
import { expect, test } from "bun:test";
import {
	DrizzleAgentTombstoneStore,
	tombstoneFromBinding,
} from "./tombstone-store";
import { createTestDb } from "../db/test-helpers"; // existing helper used by store.test.ts

const BINDING = {
	terminalId: "term-1",
	workspaceId: "w1",
	agentId: "agent-1" as never,
	agentSessionId: "sess-abc",
	presetId: "preset-1",
	label: "kimi ana",
	launchCommand: "kimi -m kimi-code/k3",
	startedAt: 1_700_000_000_000,
	lastEventAt: 1_700_000_100_000,
	lastEventType: "exit",
};

test("tombstoneFromBinding preserves the resume-critical fields", () => {
	const row = tombstoneFromBinding(BINDING as never, "kimi", 1_700_000_200_000, "tomb-1");
	expect(row.agentSessionId).toBe("sess-abc");
	expect(row.launchCommand).toBe("kimi -m kimi-code/k3");
	expect(row.runtime).toBe("kimi");
	expect(row.exitedAt).toBe(1_700_000_200_000);
});

test("a recorded tombstone is readable back by workspace", () => {
	const store = new DrizzleAgentTombstoneStore(createTestDb());
	store.record(tombstoneFromBinding(BINDING as never, "kimi", 1_700_000_200_000, "tomb-1"));
	const rows = store.listByWorkspace("w1");
	expect(rows).toHaveLength(1);
	expect(rows[0].agentSessionId).toBe("sess-abc");
});

test("a tombstone survives deletion of its terminal session row", () => {
	const db = createTestDb();
	const store = new DrizzleAgentTombstoneStore(db);
	store.record(tombstoneFromBinding(BINDING as never, "kimi", 1_700_000_200_000, "tomb-1"));
	db.run("DELETE FROM terminal_sessions WHERE id = 'term-1'");
	// This is the whole reason the table has no FK.
	expect(store.listByWorkspace("w1")).toHaveLength(1);
});

test("prune removes rows past the ttl and reports the count", () => {
	const store = new DrizzleAgentTombstoneStore(createTestDb());
	store.record(tombstoneFromBinding(BINDING as never, "kimi", 1_000, "old"));
	expect(store.prune(1_000 + 8 * 24 * 60 * 60 * 1000)).toBe(1);
	expect(store.listByWorkspace("w1")).toHaveLength(0);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bun test src/terminal-agents/tombstone-store.test.ts`
Expected: FAIL — cannot resolve `./tombstone-store`.

If `../db/test-helpers` does not exist, read `src/terminal-agents/store.test.ts` and reuse whatever in-memory database helper it already uses; do not invent a second pattern.

- [ ] **Step 3: Implement the store**

```ts
import { inArray, eq } from "drizzle-orm";
import { terminalAgentTombstones } from "../db/schema";
import { selectExpiredTombstoneIds } from "./tombstone-retention";
import type { TerminalAgentBinding } from "./types";

export interface AgentTombstoneRow {
	id: string;
	workspaceId: string;
	terminalId: string;
	agentId: string;
	agentSessionId: string | null;
	definitionId: string | null;
	presetId: string | null;
	label: string | null;
	launchCommand: string | null;
	runtime: string;
	startedAt: number;
	exitedAt: number;
}

export function tombstoneFromBinding(
	binding: TerminalAgentBinding,
	runtime: string,
	exitedAt: number,
	id: string,
): AgentTombstoneRow {
	return {
		id,
		workspaceId: binding.workspaceId,
		terminalId: binding.terminalId,
		agentId: binding.agentId,
		agentSessionId: binding.agentSessionId ?? null,
		definitionId: binding.definitionId ?? null,
		presetId: binding.presetId ?? null,
		label: binding.label ?? null,
		launchCommand: binding.launchCommand ?? null,
		runtime,
		startedAt: binding.startedAt,
		exitedAt,
	};
}

// biome-ignore lint/suspicious/noExplicitAny: matches the existing persistence layer's db type
type Db = any;

export class DrizzleAgentTombstoneStore {
	#db: Db;

	constructor(db: Db) {
		this.#db = db;
	}

	record(row: AgentTombstoneRow): void {
		this.#db.insert(terminalAgentTombstones).values(row).onConflictDoNothing().run();
	}

	listByWorkspace(workspaceId: string): AgentTombstoneRow[] {
		return this.#db
			.select()
			.from(terminalAgentTombstones)
			.where(eq(terminalAgentTombstones.workspaceId, workspaceId))
			.all() as AgentTombstoneRow[];
	}

	get(id: string): AgentTombstoneRow | null {
		const rows = this.#db
			.select()
			.from(terminalAgentTombstones)
			.where(eq(terminalAgentTombstones.id, id))
			.all() as AgentTombstoneRow[];
		return rows[0] ?? null;
	}

	prune(now: number): number {
		const rows = this.#db
			.select({
				id: terminalAgentTombstones.id,
				workspaceId: terminalAgentTombstones.workspaceId,
				exitedAt: terminalAgentTombstones.exitedAt,
			})
			.from(terminalAgentTombstones)
			.all() as Array<{ id: string; workspaceId: string; exitedAt: number }>;
		const doomed = selectExpiredTombstoneIds(rows, now);
		if (doomed.length === 0) return 0;
		this.#db
			.delete(terminalAgentTombstones)
			.where(inArray(terminalAgentTombstones.id, doomed))
			.run();
		return doomed.length;
	}
}
```

- [ ] **Step 4: Hook the capture into binding deletion**

In `src/terminal-agents/store.ts`, add an optional tombstone sink to the constructor options and write it at the top of `deleteTerminal`, before any removal:

```ts
private deleteTerminal(terminalId: string): void {
	const binding = this.byTerminal.get(terminalId);
	if (binding !== undefined && this.tombstones !== undefined) {
		// Capture BEFORE deletion: agentSessionId and launchCommand are
		// unrecoverable once the binding row is gone.
		this.tombstones.record(
			tombstoneFromBinding(
				binding,
				runtimeFromBinding(binding),
				Date.now(),
				randomUUID(),
			),
		);
	}
	// ...existing removal logic unchanged...
	this.byTerminal.delete(terminalId);
	this.persistence?.delete(terminalId);
}
```

`runtimeFromBinding` already exists in `src/terminal-agents/operator-view.ts:33` — import it rather than duplicating the mapping.

- [ ] **Step 5: Run the full terminal-agents suite**

Run: `cd packages/host-service && bun test src/terminal-agents/`
Expected: PASS — all four new tests plus every pre-existing `store.test.ts`, `persistence.test.ts`, and `operator-view.test.ts` case. Existing tests must not need edits; the sink is optional.

- [ ] **Step 6: Commit**

```bash
git add packages/host-service/src/terminal-agents/tombstone-store.ts packages/host-service/src/terminal-agents/tombstone-store.test.ts packages/host-service/src/terminal-agents/store.ts
git commit -m "feat(host-service): capture agent tombstones before binding deletion"
```

---

### Task 4: Per-runtime resume argv

Voice must never dictate CLI flags — Turkish ASR on `--resume` is hopeless. The host owns the mapping, as a pure function with an explicit unresumable case.

**Files:**
- Create: `packages/host-service/src/terminal-agents/resume-argv.ts`
- Test: `packages/host-service/src/terminal-agents/resume-argv.test.ts`

**Interfaces:**
- Consumes: `AgentTombstoneRow` (Task 3).
- Produces:
  - `type ResumePlan = { argv: string[]; fidelity: "exact" | "last" }`
  - `resumeArgvFor(row: Pick<AgentTombstoneRow, "runtime" | "agentSessionId">): ResumePlan | null`

- [ ] **Step 1: Verify the real flags before writing fixtures**

Do not trust remembered flags. Run each and record the actual resume syntax:

```bash
codex resume --help 2>&1 | head -20
claude --help 2>&1 | grep -i -A2 resume
kimi --help 2>&1 | grep -iE '^\s*-(c|S)|continue|session'
```

If an installed CLI's syntax differs from the fixtures below, **update the fixtures and the implementation to match the installed reality**, and note the deviation in the commit message.

- [ ] **Step 2: Write the failing test**

`fidelity` is load-bearing: `"last"` means "resumed whatever was most recent," which is *not* a guarantee it was this session. The receipt must be able to say so.

```ts
import { expect, test } from "bun:test";
import { resumeArgvFor } from "./resume-argv";

test("codex with a session id resumes that exact session", () => {
	const plan = resumeArgvFor({ runtime: "codex", agentSessionId: "abc123" });
	expect(plan?.argv).toEqual(["codex", "resume", "abc123"]);
	expect(plan?.fidelity).toBe("exact");
});

test("codex without a session id falls back to the last session", () => {
	const plan = resumeArgvFor({ runtime: "codex", agentSessionId: null });
	expect(plan?.argv).toEqual(["codex", "resume", "--last"]);
	expect(plan?.fidelity).toBe("last");
});

test("claude requires a session id and is unresumable without one", () => {
	expect(resumeArgvFor({ runtime: "claude", agentSessionId: "sid-9" })?.argv)
		.toEqual(["claude", "--resume", "sid-9"]);
	expect(resumeArgvFor({ runtime: "claude", agentSessionId: null })).toBe(null);
});

test("kimi resumes by id, or continues the last session", () => {
	expect(resumeArgvFor({ runtime: "kimi", agentSessionId: "k-1" })?.argv)
		.toEqual(["kimi", "-S", "k-1"]);
	expect(resumeArgvFor({ runtime: "kimi", agentSessionId: null })?.fidelity)
		.toBe("last");
});

test("a plain shell is never resumable", () => {
	expect(resumeArgvFor({ runtime: "shell", agentSessionId: "x" })).toBe(null);
});

test("an unknown runtime is never resumable", () => {
	expect(resumeArgvFor({ runtime: "unknown", agentSessionId: "x" })).toBe(null);
});

test("a session id with shell metacharacters is refused", () => {
	expect(resumeArgvFor({ runtime: "claude", agentSessionId: "a; rm -rf /" })).toBe(null);
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `bun test src/terminal-agents/resume-argv.test.ts`
Expected: FAIL — cannot resolve `./resume-argv`.

- [ ] **Step 4: Implement the mapper**

The metacharacter guard is defence in depth: argv is passed as an array so no shell is involved, but a session ID reaching a log or a tmux `send-keys` path later must already be clean.

```ts
export interface ResumePlan {
	argv: string[];
	fidelity: "exact" | "last";
}

const SAFE_SESSION_ID = /^[A-Za-z0-9._-]{1,128}$/;

export function resumeArgvFor(row: {
	runtime: string;
	agentSessionId: string | null;
}): ResumePlan | null {
	const id =
		row.agentSessionId !== null && SAFE_SESSION_ID.test(row.agentSessionId)
			? row.agentSessionId
			: null;

	switch (row.runtime) {
		case "codex":
			return id !== null
				? { argv: ["codex", "resume", id], fidelity: "exact" }
				: { argv: ["codex", "resume", "--last"], fidelity: "last" };
		case "claude":
			// No documented "resume last" form: without an id there is nothing to resume.
			return id !== null
				? { argv: ["claude", "--resume", id], fidelity: "exact" }
				: null;
		case "kimi":
			return id !== null
				? { argv: ["kimi", "-S", id], fidelity: "exact" }
				: { argv: ["kimi", "-c"], fidelity: "last" };
		default:
			return null;
	}
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `bun test src/terminal-agents/resume-argv.test.ts`
Expected: PASS, 7 tests.

- [ ] **Step 6: Commit**

```bash
git add packages/host-service/src/terminal-agents/resume-argv.ts packages/host-service/src/terminal-agents/resume-argv.test.ts
git commit -m "feat(host-service): map agent tombstones to per-runtime resume argv"
```

---

### Task 5: Resume-evidence detector

A launched process is not a restored context. This classifier is what stops voice from saying *"Ajan devam ediyor"* when resume silently started a blank session.

**Files:**
- Create: `packages/host-service/src/terminal-agents/resume-evidence.ts`
- Test: `packages/host-service/src/terminal-agents/resume-evidence.test.ts`

**Interfaces:**
- Consumes: `ResumePlan` (Task 4).
- Produces:
  - `type ResumePhase = "restored" | "restored_last" | "started_empty" | "restore_failed" | "unknown_timeout"`
  - `classifyResume(input: { runtime: string; paneText: string; fidelity: "exact" | "last"; timedOut: boolean }): ResumePhase`
  - `resumeSpeak(phase: ResumePhase): string`

- [ ] **Step 1: Write the failing test**

`started_empty` is the critical verdict: the process launched fine but resumed nothing. It must never be reported as success.

```ts
import { expect, test } from "bun:test";
import { classifyResume, resumeSpeak } from "./resume-evidence";

test("a codex resume banner with exact fidelity is restored", () => {
	expect(classifyResume({
		runtime: "codex",
		paneText: "Resuming session abc123\n> ",
		fidelity: "exact",
		timedOut: false,
	})).toBe("restored");
});

test("a resume banner with last fidelity is reported separately", () => {
	expect(classifyResume({
		runtime: "codex",
		paneText: "Resuming session zzz\n> ",
		fidelity: "last",
		timedOut: false,
	})).toBe("restored_last");
});

test("a fresh-session banner means resume silently started empty", () => {
	expect(classifyResume({
		runtime: "claude",
		paneText: "Welcome to Claude Code\nNo previous conversation\n> ",
		fidelity: "exact",
		timedOut: false,
	})).toBe("started_empty");
});

test("an explicit error is restore_failed", () => {
	expect(classifyResume({
		runtime: "claude",
		paneText: "Error: session sid-9 not found",
		fidelity: "exact",
		timedOut: false,
	})).toBe("restore_failed");
});

test("a timeout is unknown, never a success", () => {
	expect(classifyResume({
		runtime: "kimi",
		paneText: "",
		fidelity: "exact",
		timedOut: true,
	})).toBe("unknown_timeout");
});

test("unrecognised output is unknown rather than optimistic", () => {
	expect(classifyResume({
		runtime: "kimi",
		paneText: "[32m???[0m",
		fidelity: "exact",
		timedOut: false,
	})).toBe("unknown_timeout");
});

test("no phase except restored may speak a completion word", () => {
	for (const phase of ["started_empty", "restore_failed", "unknown_timeout"] as const) {
		expect(resumeSpeak(phase)).toContain("SARI");
	}
	expect(resumeSpeak("restored")).not.toContain("SARI");
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bun test src/terminal-agents/resume-evidence.test.ts`
Expected: FAIL — cannot resolve `./resume-evidence`.

- [ ] **Step 3: Implement the classifier**

ANSI is stripped first for the same reason `yapitalism/canary.py` does it: line wrapping and colour codes must not create false negatives.

```ts
import type { ResumePlan } from "./resume-argv";

export type ResumePhase =
	| "restored"
	| "restored_last"
	| "started_empty"
	| "restore_failed"
	| "unknown_timeout";

const ANSI = /(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])/g;

const RESTORED = [/resuming session/i, /resumed conversation/i, /continuing session/i];
const EMPTY = [/no previous conversation/i, /starting a new (session|conversation)/i, /new session started/i];
const FAILED = [/session .* not found/i, /could not resume/i, /invalid session/i, /^error:/im];

export function classifyResume(input: {
	runtime: string;
	paneText: string;
	fidelity: ResumePlan["fidelity"];
	timedOut: boolean;
}): ResumePhase {
	if (input.timedOut) return "unknown_timeout";
	const text = input.paneText.replace(ANSI, "");

	// Failure and empty-start checks run BEFORE the success check: a pane can
	// contain both a resume banner and a subsequent error.
	if (FAILED.some((pattern) => pattern.test(text))) return "restore_failed";
	if (EMPTY.some((pattern) => pattern.test(text))) return "started_empty";
	if (RESTORED.some((pattern) => pattern.test(text))) {
		return input.fidelity === "exact" ? "restored" : "restored_last";
	}
	return "unknown_timeout";
}

export function resumeSpeak(phase: ResumePhase): string {
	switch (phase) {
		case "restored":
			return "Oturum geçmişiyle birlikte geri geldi.";
		case "restored_last":
			return "SARI: Son oturum açıldı; tam olarak istediğin oturum olduğunu doğrulayamadım.";
		case "started_empty":
			return "SARI: Ajan açıldı ama geçmiş yüklenmedi; boş oturum başladı.";
		case "restore_failed":
			return "SARI: Oturum geri yüklenemedi.";
		case "unknown_timeout":
			return "SARI: Oturumun geri yüklendiğini doğrulayamadım.";
	}
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `bun test src/terminal-agents/resume-evidence.test.ts`
Expected: PASS, 7 tests.

- [ ] **Step 5: Run the whole host-service terminal suite**

Run: `cd packages/host-service && bun test src/terminal-agents/ src/terminal/`
Expected: PASS — 23 new tests plus every pre-existing case, including `terminal-delivery.test.ts`.

- [ ] **Step 6: Commit**

```bash
git add packages/host-service/src/terminal-agents/resume-evidence.ts packages/host-service/src/terminal-agents/resume-evidence.test.ts
git commit -m "feat(host-service): classify resume evidence without optimistic success"
```

---

## Out of scope for this plan

This plan lands inert: the mechanism exists and is tested, but voice cannot trigger it yet. Deliberate — each item below touches the deployed surface and deserves its own plan and its own canary.

- **`agents_create` resume parameter.** An optional `resumeFromTombstoneId` avoids inventing a new MCP tool, so the existing `SKILL.md` mutation gate keeps applying. Requires redeploying `apps/api`.
- **Dead-target resolution.** `targets_resolve_spoken` currently ranks live workspaces and sessions. Returning tombstones marked `dead: true, resumable: boolean` is what makes *"kimi oturumunu yeniden başlat"* addressable.
- **Exposing `agentSessionId` + redacted `launchCommand`** through `operator-view.ts`, routed via `packages/shared/src/sensitive-text.ts`.
- **`SKILL.md` phrases** for the five resume phases, and a stronger confirmation gate than `send` — resume replaces a session and can discard in-memory context, so it belongs in the delete class, with no fragment inference.
- **Skill invocation by voice.** Typing `/slug` is trivial; resolving a spoken Turkish name to one of 100+ skill slugs reuses `rankVoiceTargets`, and needs its own detector because an unknown slash command echoes, advances the revision, and would otherwise read as `working`.
- **Pruning schedule.** `prune()` exists but nothing calls it; wire it to host-service startup plus a daily timer.

## Verification gate before any voice exposure

1. `bun test src/terminal-agents/ src/terminal/` green in `packages/host-service`.
2. The generated migration contains **no** FOREIGN KEY clause.
3. A tombstone survives `DELETE FROM terminal_sessions` (Task 3, test 3).
4. Every non-`restored` phase speaks a `SARI` string — no phase can claim success without evidence.
5. Resume flags in `resume-argv.ts` were verified against the installed CLIs, not assumed.
6. The running desktop artifact was never rebuilt or restarted.
