# tmux Provider Sidecar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the existing ChatGPT-Voice → Superset MCP route drive coding agents running in arbitrary tmux panes, without changing the MCP tool surface or the voice policy.

**Architecture:** A standalone sidecar process owns all tmux interaction and answers a small newline-delimited JSON protocol over a `0600` unix socket. The host-service gains prefix-based provider dispatch: target IDs beginning `superset:` keep the existing PTY path untouched, `tmux:` routes to the sidecar. The receipt ledger (`terminal-delivery.ts`) is reused unmodified because it is a pure function of observations. A crashing sidecar cannot take the voice path down.

**Tech Stack:** TypeScript, Bun (1.3.12) test runner, Node `node:child_process` + `node:net`, tmux 3.6a CLI.

## Global Constraints

- **Implementation worktree:** `~/hermes-workspace/research-intake/superset-prod-mcp-deploy-20260730` (branch `cor/prod-mcp-receipts-20260730`). Create a new branch off it: `cor/tmux-provider-sidecar`. Never commit to `main`.
- **Never rebuild the running desktop app in place.** The live voice path runs `superset-voice-current-main/apps/desktop/release/mac-arm64/Superset.app`. This plan produces a sidecar binary plus dormant, flag-gated host-service code. No step restarts or replaces that artifact.
- **Never touch the user's real tmux server.** Every test uses an isolated socket via `tmux -L yapitalism-tmux-test`. `kill-server` is only ever called with that explicit `-L`. The live session `termo-main` must survive every test run.
- **No secrets in code, tests, logs, or commits.** No bearer tokens, no manifest contents.
- **Fail closed.** Unknown pane runtime, unreadable pane, or changed pane identity must reject before any write. Never downgrade to an optimistic success.
- **Target ID namespaces:** `superset:<uuid>` and `tmux:<pane_id>` (tmux pane IDs already start with `%`, e.g. `tmux:%3`). No bare IDs.
- **No new MCP tools and no `SKILL.md` edits.** `terminalId`/`workspaceId`/`hostId` are `z.string().min(1)` in `packages/mcp-v2`, so namespaced IDs pass unchanged. Adding tools would force retuning the Turkish voice policy.
- **Commit after every task.** Conventional commits, no `Co-Authored-By` trailer.

## File Structure

New package `packages/tmux-provider/`:

| File | Responsibility |
| --- | --- |
| `src/tmux-cli.ts` | Thin, shell-free wrapper over the tmux binary. No business logic. |
| `src/tmux-cli.test.ts` | Real-tmux round-trip tests on the isolated socket, incl. Turkish. |
| `src/revision-tracker.ts` | Derives a monotonic integer `revision` tmux does not natively have. |
| `src/revision-tracker.test.ts` | Pure unit tests. |
| `src/runtime-detect.ts` | Pure `ps`-table parsing + process-tree classification → `TerminalRuntime`. |
| `src/runtime-detect.test.ts` | Fixture-driven unit tests (no agent processes spawned). |
| `src/server.ts` | Unix-socket JSON server; owner + mode checks; method dispatch. |
| `src/server.test.ts` | Protocol and rejection-path tests over a temp socket. |
| `src/types.ts` | Wire types shared by sidecar and host client. |

Modified in `packages/host-service/`:

| File | Change |
| --- | --- |
| `src/terminal/providers/target-id.ts` (create) | Parse/format namespaced target IDs. |
| `src/terminal/providers/target-id.test.ts` (create) | Unit tests. |
| `src/terminal/providers/tmux-client.ts` (create) | Socket client speaking the sidecar protocol. |
| `src/trpc/router/terminal/terminal.ts` | Prefix dispatch in `snapshot`/`send`/`awaitDelivery`, behind an env flag. |

---

### Task 1: tmux CLI wrapper with byte-exact Turkish round-trip

The riskiest unknown goes first. `send-keys -l` with multi-byte UTF-8 under a shell that may have bracketed-paste mode enabled is the most likely silent corruption path, and a corrupted prompt reaching a coding agent is worse than a rejected one.

**Files:**
- Create: `packages/tmux-provider/src/tmux-cli.ts`
- Create: `packages/tmux-provider/package.json`
- Test: `packages/tmux-provider/src/tmux-cli.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `tmuxRun(args: string[], timeoutMs?: number): Promise<string>`
  - `listPanes(): Promise<TmuxPane[]>` where
    `TmuxPane = { paneId: string; sessionName: string; windowIndex: number; paneIndex: number; panePid: number; currentCommand: string; width: number; height: number; dead: boolean }`
  - `capturePane(paneId: string, lines?: number): Promise<string>`
  - `sendLiteral(paneId: string, text: string): Promise<void>`
  - `sendEnter(paneId: string): Promise<void>`

- [ ] **Step 1: Create the package manifest**

```json
{
  "name": "@superset/tmux-provider",
  "private": true,
  "type": "module",
  "main": "./src/server.ts",
  "scripts": {
    "test": "bun test",
    "start": "bun run src/server.ts"
  }
}
```

- [ ] **Step 2: Write the failing test**

`TMUX_PROVIDER_SOCKET` is set to an isolated socket so the user's real `termo-main` server is never reachable from a test. The session runs `cat`, so typed text is echoed to the pane but nothing can execute even if Enter is sent.

```ts
import { afterAll, beforeAll, expect, test } from "bun:test";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const run = promisify(execFile);
const SOCK = "yapitalism-tmux-test";
const SESSION = "rp-cli-test";

process.env.TMUX_PROVIDER_SOCKET = SOCK;
const { capturePane, listPanes, sendLiteral } = await import("./tmux-cli");

let pane: string;

beforeAll(async () => {
  await run("tmux", ["-L", SOCK, "new-session", "-d", "-s", SESSION,
                     "-x", "80", "-y", "24", "cat"]);
  const panes = await listPanes();
  const found = panes.find((p) => p.sessionName === SESSION);
  if (!found) throw new Error("test session pane not found");
  pane = found.paneId;
});

afterAll(async () => {
  // Explicit -L: only ever kills the isolated test server.
  await run("tmux", ["-L", SOCK, "kill-server"]).catch(() => {});
});

test("listPanes reports the isolated session with real dimensions", async () => {
  const panes = await listPanes();
  const p = panes.find((x) => x.paneId === pane);
  expect(p?.sessionName).toBe(SESSION);
  expect(p?.width).toBe(80);
  expect(p?.height).toBe(24);
  expect(p?.dead).toBe(false);
  expect(p?.panePid).toBeGreaterThan(0);
});

test("sendLiteral round-trips Turkish characters byte-exact", async () => {
  const turkish = "ısşçöüğİŞÇÖÜĞ tamamlandı mı";
  await sendLiteral(pane, turkish);
  await Bun.sleep(150);
  const text = await capturePane(pane, 5);
  expect(text).toContain(turkish);
});

test("sendLiteral does not mangle text beginning with a dash", async () => {
  await sendLiteral(pane, " --expect-revision 130");
  await Bun.sleep(150);
  const text = await capturePane(pane, 5);
  expect(text).toContain("--expect-revision 130");
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd packages/tmux-provider && bun test src/tmux-cli.test.ts`
Expected: FAIL — cannot resolve module `./tmux-cli`.

- [ ] **Step 4: Implement the wrapper**

`execFile` with an argument array means no shell, so no quoting or globbing can corrupt the payload. `--` stops tmux flag parsing so text starting with `-` is treated as data.

```ts
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const run = promisify(execFile);

const PANE_FIELDS = [
  "#{pane_id}",
  "#{session_name}",
  "#{window_index}",
  "#{pane_index}",
  "#{pane_pid}",
  "#{pane_current_command}",
  "#{pane_width}",
  "#{pane_height}",
  "#{pane_dead}",
] as const;

export interface TmuxPane {
  paneId: string;
  sessionName: string;
  windowIndex: number;
  paneIndex: number;
  panePid: number;
  currentCommand: string;
  width: number;
  height: number;
  dead: boolean;
}

function socketArgs(): string[] {
  const socket = process.env.TMUX_PROVIDER_SOCKET;
  return socket ? ["-L", socket] : [];
}

export async function tmuxRun(args: string[], timeoutMs = 3000): Promise<string> {
  const { stdout } = await run("tmux", [...socketArgs(), ...args], {
    timeout: timeoutMs,
    maxBuffer: 4 * 1024 * 1024,
    encoding: "utf8",
  });
  return stdout;
}

export async function listPanes(): Promise<TmuxPane[]> {
  const raw = await tmuxRun(["list-panes", "-a", "-F", PANE_FIELDS.join("\t")]);
  const panes: TmuxPane[] = [];
  for (const line of raw.split("\n")) {
    if (!line.trim()) continue;
    const f = line.split("\t");
    if (f.length !== PANE_FIELDS.length) continue;
    panes.push({
      paneId: f[0],
      sessionName: f[1],
      windowIndex: Number(f[2]),
      paneIndex: Number(f[3]),
      panePid: Number(f[4]),
      currentCommand: f[5],
      width: Number(f[6]),
      height: Number(f[7]),
      dead: f[8] === "1",
    });
  }
  return panes;
}

export async function capturePane(paneId: string, lines = 1000): Promise<string> {
  if (!Number.isInteger(lines) || lines < 1 || lines > 1000) {
    throw new Error("lines must be an integer between 1 and 1000");
  }
  return tmuxRun(["capture-pane", "-p", "-t", paneId, "-S", `-${lines}`]);
}

export async function sendLiteral(paneId: string, text: string): Promise<void> {
  if (!text) throw new Error("text must not be empty");
  await tmuxRun(["send-keys", "-t", paneId, "-l", "--", text]);
}

export async function sendEnter(paneId: string): Promise<void> {
  await tmuxRun(["send-keys", "-t", paneId, "Enter"]);
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd packages/tmux-provider && bun test src/tmux-cli.test.ts`
Expected: PASS, 3 tests.

- [ ] **Step 6: Verify the user's real tmux server was untouched**

Run: `tmux list-sessions`
Expected: `termo-main` still listed. If it is gone, stop and report — the isolation guard failed.

- [ ] **Step 7: Commit**

```bash
git add packages/tmux-provider/package.json packages/tmux-provider/src/tmux-cli.ts packages/tmux-provider/src/tmux-cli.test.ts
git commit -m "feat(tmux-provider): add shell-free tmux CLI wrapper with UTF-8 round-trip tests"
```

---

### Task 2: Monotonic revision derivation

tmux exposes no revision counter, but `terminal.send`'s `expectRevision` optimistic-concurrency guard requires a monotonically increasing integer. Derive it from content hashes.

**Files:**
- Create: `packages/tmux-provider/src/revision-tracker.ts`
- Test: `packages/tmux-provider/src/revision-tracker.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces: `class RevisionTracker { observe(paneId: string, text: string): number; forget(paneId: string): void }`

- [ ] **Step 1: Write the failing test**

```ts
import { expect, test } from "bun:test";
import { RevisionTracker } from "./revision-tracker";

test("first observation of a pane is revision 1", () => {
  const t = new RevisionTracker();
  expect(t.observe("%1", "hello")).toBe(1);
});

test("identical content does not advance the revision", () => {
  const t = new RevisionTracker();
  t.observe("%1", "hello");
  expect(t.observe("%1", "hello")).toBe(1);
});

test("changed content advances the revision by one", () => {
  const t = new RevisionTracker();
  t.observe("%1", "hello");
  expect(t.observe("%1", "hello world")).toBe(2);
});

test("revision never decreases when content reverts", () => {
  const t = new RevisionTracker();
  t.observe("%1", "a");
  t.observe("%1", "b");
  expect(t.observe("%1", "a")).toBe(3);
});

test("panes are tracked independently", () => {
  const t = new RevisionTracker();
  t.observe("%1", "a");
  t.observe("%1", "b");
  expect(t.observe("%2", "z")).toBe(1);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bun test src/revision-tracker.test.ts`
Expected: FAIL — cannot resolve `./revision-tracker`.

- [ ] **Step 3: Implement the tracker**

The revert case is why a bare hash is insufficient: reverting to earlier content must still advance, or `expectRevision` could pass against stale state.

```ts
import { createHash } from "node:crypto";

interface PaneState {
  hash: string;
  revision: number;
}

export class RevisionTracker {
  #state = new Map<string, PaneState>();

  observe(paneId: string, text: string): number {
    const hash = createHash("sha256").update(text, "utf8").digest("hex").slice(0, 32);
    const previous = this.#state.get(paneId);
    if (previous === undefined) {
      this.#state.set(paneId, { hash, revision: 1 });
      return 1;
    }
    if (previous.hash === hash) return previous.revision;
    const revision = previous.revision + 1;
    this.#state.set(paneId, { hash, revision });
    return revision;
  }

  forget(paneId: string): void {
    this.#state.delete(paneId);
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `bun test src/revision-tracker.test.ts`
Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
git add packages/tmux-provider/src/revision-tracker.ts packages/tmux-provider/src/revision-tracker.test.ts
git commit -m "feat(tmux-provider): derive monotonic pane revisions from content hashes"
```

---

### Task 3: Runtime detection and the pane-identity guard

This is the security boundary. A pane that used to run Claude Code and now runs `bash` would turn a spoken command into arbitrary shell execution. Superset reports `runtime` from its own agent registry; tmux has no registry, so it must be derived from the process tree and re-checked immediately before every write.

**Files:**
- Create: `packages/tmux-provider/src/runtime-detect.ts`
- Test: `packages/tmux-provider/src/runtime-detect.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `type TerminalRuntime = "codex" | "claude" | "kimi" | "shell" | "unknown"`
  - `parsePsTable(raw: string): PsRow[]` where `PsRow = { pid: number; ppid: number; args: string }`
  - `classifyTree(rows: PsRow[], panePid: number): TerminalRuntime`
  - `detectRuntime(panePid: number): Promise<TerminalRuntime>`

- [ ] **Step 1: Write the failing test**

Parsing and classification are pure functions over a `ps` table, so this is fully testable without launching any agent.

```ts
import { expect, test } from "bun:test";
import { classifyTree, parsePsTable } from "./runtime-detect";

const TABLE = `  501     1 /bin/launchd
  900   501 -zsh
  901   900 node /Users/x/.nvm/versions/node/v24.9.0/bin/codex -c model=gpt-5.6-sol
  950   501 -zsh
  980   501 -bash
  981   980 /Users/x/.local/bin/claude --permission-mode acceptEdits
  990   501 -zsh
  991   990 /opt/homebrew/bin/kimi -p hello
`;

test("parsePsTable extracts pid, ppid, and full args", () => {
  const rows = parsePsTable(TABLE);
  const row = rows.find((r) => r.pid === 901);
  expect(row?.ppid).toBe(900);
  expect(row?.args).toContain("codex");
});

test("classifyTree finds codex in a descendant of the pane pid", () => {
  expect(classifyTree(parsePsTable(TABLE), 900)).toBe("codex");
});

test("classifyTree finds claude under a bash pane", () => {
  expect(classifyTree(parsePsTable(TABLE), 980)).toBe("claude");
});

test("classifyTree finds kimi", () => {
  expect(classifyTree(parsePsTable(TABLE), 990)).toBe("kimi");
});

test("a bare shell with no agent child classifies as shell", () => {
  expect(classifyTree(parsePsTable(TABLE), 950)).toBe("shell");
});

test("an unknown pane pid classifies as unknown, never shell", () => {
  expect(classifyTree(parsePsTable(TABLE), 4242)).toBe("unknown");
});

test("a path containing the word codex does not match without a command boundary", () => {
  const rows = parsePsTable("  700   501 -zsh\n  701   700 /Users/x/codex-notes/bin/editor file.md\n");
  expect(classifyTree(rows, 700)).toBe("shell");
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bun test src/runtime-detect.test.ts`
Expected: FAIL — cannot resolve `./runtime-detect`.

- [ ] **Step 3: Implement detection**

The last test is the reason matching is on the executable basename rather than a substring: a directory named `codex-notes` must not be classified as the Codex agent.

```ts
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const run = promisify(execFile);

export type TerminalRuntime = "codex" | "claude" | "kimi" | "shell" | "unknown";

export interface PsRow {
  pid: number;
  ppid: number;
  args: string;
}

const SHELLS = new Set(["sh", "bash", "zsh", "fish", "dash", "ksh", "tcsh", "csh"]);
const AGENTS: ReadonlyArray<readonly [string, TerminalRuntime]> = [
  ["codex", "codex"],
  ["claude", "claude"],
  ["kimi", "kimi"],
];

export function parsePsTable(raw: string): PsRow[] {
  const rows: PsRow[] = [];
  for (const line of raw.split("\n")) {
    const match = /^\s*(\d+)\s+(\d+)\s+(.*)$/.exec(line);
    if (!match) continue;
    rows.push({ pid: Number(match[1]), ppid: Number(match[2]), args: match[3] });
  }
  return rows;
}

function basenames(args: string): string[] {
  return args
    .split(/\s+/)
    .filter(Boolean)
    .map((token) => token.replace(/^-/, "").split("/").pop() ?? "");
}

function agentOf(args: string): TerminalRuntime | null {
  const names = basenames(args);
  for (const [needle, runtime] of AGENTS) {
    if (names.includes(needle)) return runtime;
  }
  return null;
}

function isShell(args: string): boolean {
  const first = basenames(args)[0];
  return first !== undefined && SHELLS.has(first);
}

export function classifyTree(rows: PsRow[], panePid: number): TerminalRuntime {
  const byParent = new Map<number, PsRow[]>();
  const byPid = new Map<number, PsRow>();
  for (const row of rows) {
    byPid.set(row.pid, row);
    const siblings = byParent.get(row.ppid);
    if (siblings) siblings.push(row);
    else byParent.set(row.ppid, [row]);
  }

  const root = byPid.get(panePid);
  if (root === undefined) return "unknown";

  // Breadth-first over descendants; an agent anywhere in the tree wins.
  const queue: PsRow[] = [root];
  let sawShell = false;
  const seen = new Set<number>();
  while (queue.length > 0) {
    const row = queue.shift() as PsRow;
    if (seen.has(row.pid)) continue;
    seen.add(row.pid);
    const agent = agentOf(row.args);
    if (agent !== null) return agent;
    if (isShell(row.args)) sawShell = true;
    for (const child of byParent.get(row.pid) ?? []) queue.push(child);
  }
  return sawShell ? "shell" : "unknown";
}

export async function detectRuntime(panePid: number): Promise<TerminalRuntime> {
  if (!Number.isInteger(panePid) || panePid <= 0) return "unknown";
  try {
    const { stdout } = await run("ps", ["-ax", "-o", "pid=,ppid=,args="], {
      timeout: 3000,
      maxBuffer: 8 * 1024 * 1024,
      encoding: "utf8",
    });
    return classifyTree(parsePsTable(stdout), panePid);
  } catch {
    return "unknown";
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `bun test src/runtime-detect.test.ts`
Expected: PASS, 7 tests.

- [ ] **Step 5: Commit**

```bash
git add packages/tmux-provider/src/runtime-detect.ts packages/tmux-provider/src/runtime-detect.test.ts
git commit -m "feat(tmux-provider): classify pane runtime from the process tree"
```

---

### Task 4: Unix-socket sidecar server with fail-closed send

The sidecar is a separate process so a tmux fault cannot take down the voice path. `send` re-detects runtime and re-checks revision immediately before writing, and returns the same phase vocabulary the existing ledger already understands.

**Files:**
- Create: `packages/tmux-provider/src/types.ts`
- Create: `packages/tmux-provider/src/server.ts`
- Test: `packages/tmux-provider/src/server.test.ts`

**Interfaces:**
- Consumes: `listPanes`, `capturePane`, `sendLiteral`, `sendEnter` (Task 1); `RevisionTracker` (Task 2); `detectRuntime`, `TerminalRuntime` (Task 3).
- Produces:
  - `type TmuxRequest = { id: string; method: "list" } | { id: string; method: "snapshot"; paneId: string; maxLines?: number } | { id: string; method: "send"; paneId: string; text: string; expectRevision: number; expectRuntime: TerminalRuntime; clientToken: string }`
  - `type TmuxSendPhase = "injected" | "rejected_revision_changed" | "rejected_runtime_changed" | "rejected_prompt_unreadable" | "duplicate_ignored"`
  - `createServer(socketPath: string): Promise<{ close(): Promise<void> }>`

- [ ] **Step 1: Write the wire types**

```ts
import type { TerminalRuntime } from "./runtime-detect";

export type TmuxSendPhase =
  | "injected"
  | "rejected_revision_changed"
  | "rejected_runtime_changed"
  | "rejected_prompt_unreadable"
  | "duplicate_ignored";

export interface TmuxPaneSummary {
  targetId: string;
  sessionName: string;
  windowIndex: number;
  paneIndex: number;
  runtime: TerminalRuntime;
  width: number;
  height: number;
  dead: boolean;
}

export interface TmuxSnapshot {
  targetId: string;
  text: string;
  revision: number;
  cols: number;
  rows: number;
  runtime: TerminalRuntime;
}

export interface TmuxSendResult {
  targetId: string;
  phase: TmuxSendPhase;
  submitSent: boolean;
  duplicate: boolean;
  revisionBefore: number;
  revisionAfter: number | null;
  runtime: TerminalRuntime;
  deliveryId: string | null;
  clientToken: string;
}

export type TmuxRequest =
  | { id: string; method: "list" }
  | { id: string; method: "snapshot"; paneId: string; maxLines?: number }
  | {
      id: string;
      method: "send";
      paneId: string;
      text: string;
      expectRevision: number;
      expectRuntime: TerminalRuntime;
      clientToken: string;
    };

export type TmuxResponse =
  | { id: string; ok: true; result: unknown }
  | { id: string; ok: false; error: string };
```

- [ ] **Step 2: Write the failing test**

```ts
import { afterAll, beforeAll, expect, test } from "bun:test";
import { execFile } from "node:child_process";
import { connect } from "node:net";
import { mkdtempSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";

const run = promisify(execFile);
const SOCK = "yapitalism-tmux-test";
const SESSION = "rp-server-test";
process.env.TMUX_PROVIDER_SOCKET = SOCK;

const { createServer } = await import("./server");

let server: { close(): Promise<void> };
let socketPath: string;
let targetId: string;

function call(request: unknown): Promise<any> {
  return new Promise((resolve, reject) => {
    const client = connect(socketPath, () => {
      client.write(`${JSON.stringify(request)}\n`);
    });
    let buffer = "";
    client.on("data", (chunk) => {
      buffer += chunk.toString("utf8");
      const newline = buffer.indexOf("\n");
      if (newline >= 0) {
        client.end();
        resolve(JSON.parse(buffer.slice(0, newline)));
      }
    });
    client.on("error", reject);
  });
}

beforeAll(async () => {
  await run("tmux", ["-L", SOCK, "new-session", "-d", "-s", SESSION,
                     "-x", "80", "-y", "24", "cat"]);
  socketPath = join(mkdtempSync(join(tmpdir(), "rp-sock-")), "provider.sock");
  server = await createServer(socketPath);
  const listed = await call({ id: "1", method: "list" });
  const pane = listed.result.find((p: any) => p.sessionName === SESSION);
  targetId = pane.targetId;
});

afterAll(async () => {
  await server.close();
  await run("tmux", ["-L", SOCK, "kill-server"]).catch(() => {});
});

test("the socket is created with owner-only permissions", () => {
  expect(statSync(socketPath).mode & 0o077).toBe(0);
});

test("list returns namespaced target ids", () => {
  expect(targetId.startsWith("tmux:%")).toBe(true);
});

test("snapshot returns a revision and real dimensions", async () => {
  const res = await call({ id: "2", method: "snapshot", paneId: targetId });
  expect(res.ok).toBe(true);
  expect(res.result.cols).toBe(80);
  expect(res.result.rows).toBe(24);
  expect(res.result.revision).toBeGreaterThanOrEqual(1);
});

test("send rejects a stale expectRevision without writing", async () => {
  const res = await call({
    id: "3", method: "send", paneId: targetId, text: "merhaba",
    expectRevision: 999999, expectRuntime: "shell", clientToken: "tok-stale",
  });
  expect(res.result.phase).toBe("rejected_revision_changed");
  expect(res.result.submitSent).toBe(false);
  expect(res.result.deliveryId).toBe(null);
});

test("send rejects a changed runtime without writing", async () => {
  const snap = await call({ id: "4", method: "snapshot", paneId: targetId });
  const res = await call({
    id: "5", method: "send", paneId: targetId, text: "merhaba",
    expectRevision: snap.result.revision, expectRuntime: "codex",
    clientToken: "tok-runtime",
  });
  expect(res.result.phase).toBe("rejected_runtime_changed");
  expect(res.result.submitSent).toBe(false);
});

test("a repeated clientToken is ignored rather than written twice", async () => {
  const snap = await call({ id: "6", method: "snapshot", paneId: targetId });
  const first = await call({
    id: "7", method: "send", paneId: targetId, text: "ilk mesaj",
    expectRevision: snap.result.revision, expectRuntime: snap.result.runtime,
    clientToken: "tok-dup",
  });
  expect(first.result.phase).toBe("injected");
  const second = await call({
    id: "8", method: "send", paneId: targetId, text: "ilk mesaj",
    expectRevision: snap.result.revision, expectRuntime: snap.result.runtime,
    clientToken: "tok-dup",
  });
  expect(second.result.phase).toBe("duplicate_ignored");
  expect(second.result.submitSent).toBe(false);
});

test("an unknown method is rejected", async () => {
  const res = await call({ id: "9", method: "nope" });
  expect(res.ok).toBe(false);
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `bun test src/server.test.ts`
Expected: FAIL — cannot resolve `./server`.

- [ ] **Step 4: Implement the server**

```ts
import { randomUUID } from "node:crypto";
import { chmodSync, existsSync, unlinkSync } from "node:fs";
import { createServer as createNetServer, type Socket } from "node:net";
import { capturePane, listPanes, sendEnter, sendLiteral } from "./tmux-cli";
import { RevisionTracker } from "./revision-tracker";
import { detectRuntime, type TerminalRuntime } from "./runtime-detect";
import type {
  TmuxPaneSummary, TmuxRequest, TmuxResponse, TmuxSendResult, TmuxSnapshot,
} from "./types";

const AGENT_RUNTIMES: ReadonlySet<TerminalRuntime> = new Set(["codex", "claude", "kimi"]);
const MAX_LINE_BYTES = 128 * 1024;
const revisions = new RevisionTracker();
const tokens = new Map<string, string>();

function toPaneId(targetId: string): string {
  if (!targetId.startsWith("tmux:")) throw new Error("target id must be tmux-namespaced");
  const paneId = targetId.slice("tmux:".length);
  if (!/^%\d+$/.test(paneId)) throw new Error("invalid tmux pane id");
  return paneId;
}

async function handleList(): Promise<TmuxPaneSummary[]> {
  const panes = await listPanes();
  const out: TmuxPaneSummary[] = [];
  for (const pane of panes) {
    out.push({
      targetId: `tmux:${pane.paneId}`,
      sessionName: pane.sessionName,
      windowIndex: pane.windowIndex,
      paneIndex: pane.paneIndex,
      runtime: await detectRuntime(pane.panePid),
      width: pane.width,
      height: pane.height,
      dead: pane.dead,
    });
  }
  return out;
}

async function handleSnapshot(targetId: string, maxLines = 1000): Promise<TmuxSnapshot> {
  const paneId = toPaneId(targetId);
  const panes = await listPanes();
  const pane = panes.find((p) => p.paneId === paneId);
  if (pane === undefined) throw new Error("pane not found");
  const text = await capturePane(paneId, maxLines);
  return {
    targetId,
    text,
    revision: revisions.observe(paneId, text),
    cols: pane.width,
    rows: pane.height,
    runtime: await detectRuntime(pane.panePid),
  };
}

async function handleSend(request: Extract<TmuxRequest, { method: "send" }>): Promise<TmuxSendResult> {
  const { paneId: targetId, text, expectRevision, expectRuntime, clientToken } = request;
  const paneId = toPaneId(targetId);
  if (!text) throw new Error("text must not be empty");

  const claimed = tokens.get(clientToken);
  const base = {
    targetId, submitSent: false, duplicate: false,
    revisionAfter: null, deliveryId: null, clientToken,
  };

  // Snapshot immediately before the write: this is the guard, not a formality.
  const snapshot = await handleSnapshot(targetId, 1000).catch(() => null);
  if (snapshot === null) {
    return { ...base, phase: "rejected_prompt_unreadable", revisionBefore: -1, runtime: "unknown" };
  }

  if (claimed !== undefined) {
    return {
      ...base, phase: "duplicate_ignored", duplicate: true,
      revisionBefore: snapshot.revision, runtime: snapshot.runtime,
    };
  }
  if (snapshot.revision !== expectRevision) {
    return {
      ...base, phase: "rejected_revision_changed",
      revisionBefore: snapshot.revision, runtime: snapshot.runtime,
    };
  }
  if (snapshot.runtime !== expectRuntime || !AGENT_RUNTIMES.has(snapshot.runtime)) {
    return {
      ...base, phase: "rejected_runtime_changed",
      revisionBefore: snapshot.revision, runtime: snapshot.runtime,
    };
  }

  tokens.set(clientToken, targetId);
  await sendLiteral(paneId, text);
  await sendEnter(paneId);
  const after = await capturePane(paneId, 1000);
  return {
    targetId, phase: "injected", submitSent: true, duplicate: false,
    revisionBefore: snapshot.revision,
    revisionAfter: revisions.observe(paneId, after),
    runtime: snapshot.runtime,
    deliveryId: randomUUID(),
    clientToken,
  };
}

async function dispatch(request: TmuxRequest): Promise<unknown> {
  switch (request.method) {
    case "list": return handleList();
    case "snapshot": return handleSnapshot(request.paneId, request.maxLines);
    case "send": return handleSend(request);
    default: throw new Error("unknown method");
  }
}

export async function createServer(socketPath: string): Promise<{ close(): Promise<void> }> {
  if (existsSync(socketPath)) unlinkSync(socketPath);

  const server = createNetServer((socket: Socket) => {
    let buffer = "";
    socket.on("data", async (chunk) => {
      buffer += chunk.toString("utf8");
      if (buffer.length > MAX_LINE_BYTES) { socket.destroy(); return; }
      let newline = buffer.indexOf("\n");
      while (newline >= 0) {
        const line = buffer.slice(0, newline);
        buffer = buffer.slice(newline + 1);
        let response: TmuxResponse;
        let id = "unknown";
        try {
          const request = JSON.parse(line) as TmuxRequest;
          id = typeof request.id === "string" ? request.id : "unknown";
          response = { id, ok: true, result: await dispatch(request) };
        } catch (error) {
          response = { id, ok: false, error: error instanceof Error ? error.message : "bad request" };
        }
        socket.write(`${JSON.stringify(response)}\n`);
        newline = buffer.indexOf("\n");
      }
    });
    socket.on("error", () => socket.destroy());
  });

  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(socketPath, () => resolve());
  });
  chmodSync(socketPath, 0o600);

  return {
    close: () =>
      new Promise<void>((resolve) => {
        server.close(() => {
          if (existsSync(socketPath)) unlinkSync(socketPath);
          resolve();
        });
      }),
  };
}

if (import.meta.main) {
  const path = process.env.TMUX_PROVIDER_SOCKET_PATH;
  if (!path) throw new Error("TMUX_PROVIDER_SOCKET_PATH is required");
  await createServer(path);
  console.log(`[tmux-provider] listening on ${path}`);
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `bun test src/server.test.ts`
Expected: PASS, 7 tests.

- [ ] **Step 6: Confirm the real tmux server survived, then run the whole package**

Run: `tmux list-sessions && bun test`
Expected: `termo-main` listed; 22 tests pass across four files.

- [ ] **Step 7: Commit**

```bash
git add packages/tmux-provider/src/types.ts packages/tmux-provider/src/server.ts packages/tmux-provider/src/server.test.ts
git commit -m "feat(tmux-provider): add fail-closed unix-socket sidecar server"
```

---

### Task 5: Host-service target-ID dispatch behind a default-off flag

The existing PTY path must be byte-for-byte unchanged when the flag is unset. This task adds routing only; it does not advertise tmux targets to `targets_resolve_spoken`, so voice cannot reach a tmux pane yet.

**Files:**
- Create: `packages/host-service/src/terminal/providers/target-id.ts`
- Create: `packages/host-service/src/terminal/providers/target-id.test.ts`
- Create: `packages/host-service/src/terminal/providers/tmux-client.ts`
- Modify: `packages/host-service/src/trpc/router/terminal/terminal.ts`

**Interfaces:**
- Consumes: sidecar wire types from Task 4.
- Produces:
  - `type TargetKind = "superset" | "tmux"`
  - `parseTargetId(raw: string): { kind: TargetKind; id: string }`
  - `isTmuxProviderEnabled(): boolean`
  - `class TmuxProviderClient { snapshot(targetId, maxLines): Promise<TmuxSnapshot>; send(...): Promise<TmuxSendResult>; list(): Promise<TmuxPaneSummary[]> }`

- [ ] **Step 1: Write the failing test**

Legacy bare IDs must keep working, because every currently-stored Superset terminal ID is unprefixed.

```ts
import { expect, test } from "bun:test";
import { isTmuxProviderEnabled, parseTargetId } from "./target-id";

test("a bare uuid is treated as a legacy superset target", () => {
  const parsed = parseTargetId("3f8c1d20-1c2b-4a5e-9f00-9a1b2c3d4e5f");
  expect(parsed.kind).toBe("superset");
  expect(parsed.id).toBe("3f8c1d20-1c2b-4a5e-9f00-9a1b2c3d4e5f");
});

test("an explicit superset prefix is stripped", () => {
  expect(parseTargetId("superset:abc").id).toBe("abc");
});

test("a tmux prefix is recognised and preserved", () => {
  const parsed = parseTargetId("tmux:%3");
  expect(parsed.kind).toBe("tmux");
  expect(parsed.id).toBe("%3");
});

test("an unknown namespace is rejected", () => {
  expect(() => parseTargetId("ssh:box1")).toThrow();
});

test("the tmux provider is disabled unless explicitly enabled", () => {
  delete process.env.SUPERSET_TMUX_PROVIDER;
  expect(isTmuxProviderEnabled()).toBe(false);
  process.env.SUPERSET_TMUX_PROVIDER = "1";
  expect(isTmuxProviderEnabled()).toBe(true);
  delete process.env.SUPERSET_TMUX_PROVIDER;
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/host-service && bun test src/terminal/providers/target-id.test.ts`
Expected: FAIL — cannot resolve `./target-id`.

- [ ] **Step 3: Implement target-ID parsing**

```ts
export type TargetKind = "superset" | "tmux";

export interface ParsedTargetId {
  kind: TargetKind;
  id: string;
}

export function parseTargetId(raw: string): ParsedTargetId {
  if (typeof raw !== "string" || raw.length === 0) {
    throw new Error("target id must be a non-empty string");
  }
  const separator = raw.indexOf(":");
  if (separator < 0) return { kind: "superset", id: raw };

  const namespace = raw.slice(0, separator);
  const id = raw.slice(separator + 1);
  if (id.length === 0) throw new Error("target id must have a body");
  if (namespace === "superset") return { kind: "superset", id };
  if (namespace === "tmux") {
    if (!/^%\d+$/.test(id)) throw new Error("invalid tmux pane id");
    return { kind: "tmux", id };
  }
  throw new Error(`unknown target namespace: ${namespace}`);
}

export function isTmuxProviderEnabled(): boolean {
  return process.env.SUPERSET_TMUX_PROVIDER === "1";
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `bun test src/terminal/providers/target-id.test.ts`
Expected: PASS, 5 tests.

- [ ] **Step 5: Implement the socket client**

A short timeout is deliberate: a hung sidecar must surface as an error, never as a stall that starves the relay heartbeat.

```ts
import { connect } from "node:net";
import type { TmuxPaneSummary, TmuxSendResult, TmuxSnapshot } from "@superset/tmux-provider/types";

const REQUEST_TIMEOUT_MS = 5000;

export class TmuxProviderClient {
  #socketPath: string;
  #sequence = 0;

  constructor(socketPath: string) {
    this.#socketPath = socketPath;
  }

  #call<T>(payload: Record<string, unknown>): Promise<T> {
    const id = `${++this.#sequence}`;
    return new Promise<T>((resolve, reject) => {
      const socket = connect(this.#socketPath);
      const timer = setTimeout(() => {
        socket.destroy();
        reject(new Error("tmux provider request timed out"));
      }, REQUEST_TIMEOUT_MS);

      let buffer = "";
      socket.on("connect", () => socket.write(`${JSON.stringify({ id, ...payload })}\n`));
      socket.on("data", (chunk) => {
        buffer += chunk.toString("utf8");
        const newline = buffer.indexOf("\n");
        if (newline < 0) return;
        clearTimeout(timer);
        socket.end();
        try {
          const response = JSON.parse(buffer.slice(0, newline));
          if (response.ok === true) resolve(response.result as T);
          else reject(new Error(String(response.error ?? "tmux provider error")));
        } catch (error) {
          reject(error instanceof Error ? error : new Error("bad provider response"));
        }
      });
      socket.on("error", (error) => {
        clearTimeout(timer);
        reject(error);
      });
    });
  }

  list(): Promise<TmuxPaneSummary[]> {
    return this.#call<TmuxPaneSummary[]>({ method: "list" });
  }

  snapshot(targetId: string, maxLines = 1000): Promise<TmuxSnapshot> {
    return this.#call<TmuxSnapshot>({ method: "snapshot", paneId: targetId, maxLines });
  }

  send(input: {
    targetId: string;
    text: string;
    expectRevision: number;
    expectRuntime: string;
    clientToken: string;
  }): Promise<TmuxSendResult> {
    return this.#call<TmuxSendResult>({
      method: "send",
      paneId: input.targetId,
      text: input.text,
      expectRevision: input.expectRevision,
      expectRuntime: input.expectRuntime,
      clientToken: input.clientToken,
    });
  }
}
```

- [ ] **Step 6: Wire dispatch into the tRPC router**

In `packages/host-service/src/trpc/router/terminal/terminal.ts`, at the top of the `snapshot` resolver body, before any existing logic runs, insert:

```ts
const parsedTarget = parseTargetId(input.terminalId);
if (parsedTarget.kind === "tmux") {
  if (!isTmuxProviderEnabled()) {
    throw new Error("HOST_CAPABILITY_MISSING: tmux provider is not enabled");
  }
  const client = new TmuxProviderClient(requireTmuxSocketPath());
  const snapshot = await client.snapshot(input.terminalId, input.maxLines ?? 1000);
  return {
    terminalId: snapshot.targetId,
    text: snapshot.text,
    revision: snapshot.revision,
    cols: snapshot.cols,
    rows: snapshot.rows,
  };
}
// existing superset PTY path continues unchanged below
```

Add the same guard clause at the top of the `send` resolver, returning the sidecar's `TmuxSendResult` mapped onto the existing response shape, and add `requireTmuxSocketPath()` to `target-id.ts`:

```ts
export function requireTmuxSocketPath(): string {
  const path = process.env.SUPERSET_TMUX_PROVIDER_SOCKET;
  if (!path) throw new Error("SUPERSET_TMUX_PROVIDER_SOCKET is required");
  return path;
}
```

- [ ] **Step 7: Prove the existing path is unchanged with the flag off**

Run: `cd packages/host-service && SUPERSET_TMUX_PROVIDER= bun test src/terminal/`
Expected: every pre-existing terminal test still passes, including the staged-delivery cases in `terminal-delivery.test.ts`.

- [ ] **Step 8: Prove a tmux target is refused with the flag off**

Run: `cd packages/host-service && SUPERSET_TMUX_PROVIDER= bun test src/terminal/providers/`
Expected: PASS — a `tmux:%3` snapshot throws `HOST_CAPABILITY_MISSING`.

- [ ] **Step 9: Commit**

```bash
git add packages/host-service/src/terminal/providers packages/host-service/src/trpc/router/terminal/terminal.ts
git commit -m "feat(host-service): route namespaced tmux targets to the provider sidecar"
```

---

## Out of scope for this plan

Deliberately excluded; each needs its own plan after this one lands:

- **Synthetic workspaces.** `targets_resolve_spoken` ranks `workspace.list` first, so tmux panes remain unreachable by voice name until each session gets a workspace entry. Voice cannot reach tmux after this plan — by design.
- **Relay single-owner instance lock.** `flock` on a pidfile keyed by `machineId`, so two host processes can never contend for one `orgId:machineId` tunnel.
- **`awaitDelivery` phase detection for tmux.** Mapping `staged`/`working`/`completed` from tmux captures needs its own detector; PTY heuristics must not be reused.
- **Shadow-mode soak and the live canary** through phone → MCP → relay → tmux pane.
- **cmux/zellij backends.** Same provider interface, different CLI.

## Verification gate before any voice exposure

1. `bun test` green in `packages/tmux-provider` (22 tests) and `packages/host-service`.
2. `tmux list-sessions` still shows `termo-main` after the full suite.
3. Turkish round-trip fixture passes byte-exact.
4. With `SUPERSET_TMUX_PROVIDER` unset, the host-service diff is behaviourally inert.
5. The running desktop artifact was never rebuilt or restarted.
