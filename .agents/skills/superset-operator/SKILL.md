---
name: superset-operator
description: Operate terminal coding agents by voice. Reads and every send go through the local `yapitalism` MCP server, which returns receipts; the `superset` MCP server is used for workspaces, projects, and creating agents. Designed for ChatGPT Voice and Remote operation of the Mac Studio Superset instance. Trigger whenever the user says "Superset", "workspace", "Superset session", "Superset agent", or asks to inspect/continue/start work in the Superset cockpit.
metadata: {"version": "3.0.0"}
---

# Superset Operator

Two MCP servers cover this together. `yapitalism` runs locally and owns reading panes and **every send**, because it is the only one that returns a receipt. `superset` owns workspaces, projects, and agent lifecycle.

## Hard routing

- Do **not** use Browser, Chrome, Computer Use, shell scraping, or filesystem guessing to operate Superset.
- Do **not** reach for a third MCP server, or for a shell, to do what these two cover.
- Start by resolving the Mac Studio host through `hosts_list`, then resolve the target workspace/session from live Superset state.
- If the user's target is ambiguous, list the closest matching live workspaces/sessions and ask which one.
- **Two MCP servers are enabled and they are not interchangeable.** Use `superset` for workspaces, projects, and creating/closing agents. Use `yapitalism` for reading panes and for **every send**. See the section below.

## Yapitalism terminal contract (all sends go here)

- **Send only through `yapitalism.pane_send`.** Superset's `terminals_send` returns `{terminalId, submitted}` and nothing else — no phase, no delivery id, no await tool — so a send routed through it can never be proven. `submitted:true` means a key was pressed. It is not evidence an agent read anything, and it must never be spoken as success.
- Read with `panes_list` and `pane_read`. `panes_list` covers every backend at once, including Superset terminals, so prefer it over `terminals_list` when the user asks what is running.
- Target ids are namespaced — `tmux:%0`, `superset:<uuid>`. Pass them back verbatim. Never invent one, never strip the prefix.
- `panes_list` returns `errors` alongside `panes`. A backend that failed is **not** the same as a backend with nothing in it. Never say "no terminals" while `errors` is non-empty; say which backend could not be reached.
- **Report the whole list, compactly — never a subset presented as the answer.** With many panes, "compact" means totals and a breakdown, not one example. Say the count, then group by runtime, then offer to narrow: "23 pane var — 9 claude, 6 codex, 3 kimi, 5 shell. Hangisini istersin?" Listing only one backend, or only the first few, misleads the user into thinking that is everything. If the user then asks "what about Superset?", that means the first answer was wrong; do not defend it, just give the full picture.
- Check `runtime` before sending. Only `codex`, `claude`, and `kimi` are agents. Sending to a pane whose runtime is `shell` types the text into a shell prompt, which executes it — confirm explicitly with the user before doing that, and say plainly that it is a shell, not an agent.

### Speaking a `pane_send` receipt

`status` is the only field to speak from. Speak the returned `speak` value verbatim or more conservatively.

- **GREEN** — the agent echoed the one-time marker. It genuinely processed the text. "Ajan aldı ve işledi."
- **YELLOW** — the write landed, processing was **not** proven. Never round this up. Say it plainly: "SARI: Gönderdim, ajanın işlediğine dair kanıt gelmedi." Text sitting unread in a prompt box looks exactly like work in progress from outside, so an unproven send is the one case where sounding confident does real damage.
- **RED** — the backend refused; nothing reached the terminal. Do not retry blindly; say what was refused.

- `reason: acceptance_not_testable` means no marker was requested, so acceptance was never checkable. That is still YELLOW — "not tested" is not "passed".
- **`missing_guarantees`** lists protections that backend could not enforce before writing. A GREEN carrying missing guarantees is weaker than one without: the write itself was never checked against a stale terminal, a repeated command, or an occupied prompt. Do not describe the two as equivalent. If the user asks how sure you are, name what was not checked.
- `pane_send` is a write. Name the target and the action in one sentence, get an explicit yes, then send. Never synthesize a send from a fragment.

## Default safety

- Default mode is read-only.
- Read-only actions may use workspace, agent, and terminal listing/read tools.
- Never call `terminals_send`, create/start/close an agent, mutate a workspace, or run an automation unless the user explicitly asks for that exact action.
- Before a write action, say one short sentence naming the workspace, session/agent, and exact action. Then execute only that scope.
- Never infer success. Read back the resulting Superset state/output before reporting completion.

## Superset-native send contract (legacy — prefer yapitalism.pane_send)

Applies only when a send genuinely must go through Superset's own MCP. It cannot produce a receipt, so the outcome can never be better than YELLOW; prefer `yapitalism.pane_send` in every other case.

- Resolve noisy spoken targets with `targets_resolve_spoken`. Treat common ASR forms as hints only: `super set`→Superset, `kahve tabela`→Kahvetabela, `cloud/klaud`→Claude, `kodeks`→Codex, `kimmy`→Kimi, `glamma`→Glama, and `M C P`→MCP. Canonical identity must come from the live resolver result.
- If `ambiguous:true`, ask one short clarification and do not mutate. Otherwise speak the exact `canonicalConfirmation` before a write.
- Use one stable `clientToken` for all retries of a single voice turn. Keep `requireEmptyPrompt:true` and `allowRepeat:false` unless the user explicitly asks to repeat or replace known staged text.
- `terminals_send` phase is authoritative. `submitted:true` is deprecated and proves only a physical Enter write. `phase:injected` proves PTY injection but **not** agent processing.
- After `phase:injected`, call `terminals_await_delivery` exactly once with the receipt IDs. Do not build a Voice-side polling loop.
- Speak the host-generated `speak` value verbatim or more conservatively. Only `verified:true` with `phase:working|completed|needs_input` proves agent processing/state. `staged|unknown_timeout` is YELLOW.
- `rejected_prompt_not_empty`, `rejected_revision_changed`, and `duplicate_*` mean zero new PTY writes. Never retry them blindly.
- Preserve unrelated text already staged in the target prompt. Stop rather than clearing or overwriting it unless the user explicitly identifies that text as stale.
- Keep external side effects separate: terminal delivery proof does not authorize or prove a browser form, message, publish, payment, or other downstream action.

## Agent resume contract (NOT CURRENTLY AVAILABLE)

`agents_list_resumable`, `agents_resume` and `agents_await_resume` are built but not deployed to any MCP server this client can reach. Do not call them. If the user asks to restart a dead agent, say the capability is not connected yet rather than attempting it. The contract below applies once it is.

- "Devam et", "yeniden başlat", "kaldığı yerden devam" about an agent that has **exited** is a resume, not a `terminals_send`. A live session takes `terminals_send`; only a dead one takes `agents_resume`.
- Flow, in order: resolve the workspace with `targets_resolve_spoken` → `agents_list_resumable` for that workspace → one confirmation sentence → `agents_resume` → `agents_await_resume` exactly once. `targets_resolve_spoken` ranks live targets only, so dead agents are chosen from `agents_list_resumable`, never guessed.
- Resume is **delete-class**, stronger than `terminals_send`: it replaces a session and can discard in-memory context. Name the workspace, the agent, and the action in one sentence and get an explicit yes. Never synthesize it from a fragment such as "kimi" or "devam" alone.
- Skip any row with `resumable:false` — it cannot be restored and must not be offered.
- `fidelity` bounds what you may promise. `exact` = the recorded session. `last` = only the runtime's most recent session, which may not be the one the user meant; say so before mutating: "Kayıtlı oturum kimliği yok; en son oturumu açacağım."
- `agents_resume` returns `phase:"launched"`. That proves a process started and nothing else. Never speak it as success.
- Only `agents_await_resume` with `verified:true` and `phase:"restored"` proves the history loaded. Speak its `speak` value verbatim or more conservatively.
- `started_empty` is the trap: the process is healthy, the screen changed, output is flowing — and no context loaded. Report it as a failure, never as "devam ediyor".
- Resume phrases: restored → "Ajan geçmişiyle geri geldi."; restored_last → "SARI: Son oturum açıldı; istediğin oturum olduğunu doğrulayamadım."; started_empty → "SARI: Ajan açıldı ama geçmiş yüklenmedi; boş oturum başladı."; restore_failed → "SARI: Oturum geri yüklenemedi."; unknown_timeout → "SARI: Oturumun geri yüklendiğini doğrulayamadım."
- `NOT_FOUND` means the tombstone aged out of its retention window: say the old session is no longer restorable and offer a fresh `agents_create` as a separate, separately-confirmed action. Never silently start a new agent and call it a resume.

## MCP result-shape contract

- `hosts_list` and `workspaces_list` return arrays.
- `terminals_list` returns an object shaped as `{ "sessions": [...] }`, **not** a top-level array. Prefer `structuredContent.sessions`; if parsing text content, read `JSON.parse(text).sessions`.
- A terminal/session is live when `exited` is `false`; `running`, `idle`, and `waiting_input` are all live states.
- Never report zero live terminals merely because the wrapper object is not an array. Before saying zero, sum `sessions.length` across the resolved target workspaces and require zero listing errors.
- For each live session, retain its workspace, runtime, state, title, and terminal ID internally; keep the spoken response compact.

## Voice handoff, progress, and read-aloud contract

- A ChatGPT Work/Codex handoff is a **conversation/task object**, not a repository file, unless a tool explicitly returns a filesystem path. Always tell the user where the handoff landed: desktop `Codex → Recents` for Codex-local work, mobile `Remote → <paired host> → <chat>` for a paired Codex session, or ChatGPT `Recents/Project` for cloud Work. Never say only "handoff created."
- After Voice starts or continues agent work, treat meaningful terminal/agent output as spoken progress. Read the newest relevant output, summarize it in one short sentence, and speak `working`, `needs_input`, `blocked`, or `completed` transitions. Do not make the user ask what happened after every state transition.
- Track the terminal revision/output boundary already read during the current Voice conversation. Do not repeat old output. Do not read raw logs, IDs, ANSI noise, diffs, secrets, or tool envelopes aloud.
- If the available surface can observe only on request and has no push/long-poll callback, say this limitation before claiming monitoring: "Bu bağlantı kendi kendine yeni tur başlatamıyor; kontrol et dediğinde son çıktıyı okuyacağım." Never promise autonomous spoken updates when the active tool path cannot deliver them.
- Treat the ChatGPT Voice microphone control as a client-owned input control, not as proof that assistant output/TTS remains available. **Observed behaviour (tested 2026-08-01): muting PAUSES the turn; unmuting resumes it. The turn is not cancelled and nothing is lost.** So do not describe a mute as a failure, do not say playback "was cut", and never report a Superset failure because of it — say "Duraklattım, mikrofonu açınca kaldığım yerden devam ederim." if it needs mentioning at all. A prompt or MCP tool still cannot control the client's audio session, so a paused turn is not something to work around; it resumes on its own. Keep the existing GPT/Codex Voice app as the frontend, and do not propose a separate paid Realtime API client unless the user explicitly changes direction.
- A delivery receipt proves the prompt reached the agent; it does not prove later agent messages will be pushed into Voice. Keep delivery and progress-monitoring claims separate.

## Voice behavior

- Keep spoken replies short: canonical target, action, receipt, blocker. No raw IDs unless asked.
- While resolving, say only: "Superset üzerinden bakıyorum."
- Before mutation say: "<workspace> içindeki <agent/session> hedefini seçtim; <exact action>."
- Receipt phrases: working → "Ajan aldı, çalışıyor."; completed → "Ajan tamamladı."; staged → "SARI: Metin prompt alanında kaldı; ajan işlemedi."; timeout → "SARI: Ajanın işlediğini doğrulayamadım."; occupied prompt → "Prompt alanında bekleyen metin var; hiçbir şey yazmadım."; duplicate → "Aynı mesajın tekrarını engelledim; ikinci kez yazmadım."
- Fragment hold: incomplete fragments such as "bir tanesi", "üç session", a bare target name, or a sentence ending in a connector are context, not authorization. Wait for an explicit verb/action before any write. Do not synthesize a missing verb.
- Turn stitching: when consecutive fragments refer to the same live target, combine them in working memory but send only once after the action is explicit. A later correction replaces the pending intent before send; it does not create a second send.
- Barge-in: stop speaking/proposing immediately. Before PTY injection, abandon the pending send. After injection, report the receipt state. Never send Ctrl-C, clear a prompt, or interrupt a running agent unless the user separately and explicitly asks for that exact interrupt.
- Return the selected canonical workspace/session, the live result, and whether anything changed.
- Do not dump raw terminal logs; summarize the last relevant output.

## Common commands

- "Superset durumunu söyle" → `hosts_list`, then live workspace/agent lists; read-only summary.
- "X workspace'ine geç" → resolve X from live workspace list and retain it as the active target for the conversation.
- "Claude/Codex session'ının son çıktısını oku" → resolve the target session and use terminal read tools only.
- "Şunu gönder" → resolve/confirm the active canonical workspace/session, call `terminals_send` once, then `terminals_await_delivery` once and speak its receipt.
- "Yeni agent başlat" → require explicit agent type and workspace, create only one agent, then verify it appears.

If the `superset` MCP server or OAuth is unavailable, say exactly that and stop. Do not fall back to UI automation.
