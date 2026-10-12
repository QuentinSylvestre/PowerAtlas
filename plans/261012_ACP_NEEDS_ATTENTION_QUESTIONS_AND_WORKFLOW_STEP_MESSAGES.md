# ACP Needs Attention: Approvals, Agent Questions and Workflow Step Messages

> **Date**: 2026-10-12
> **Status**: Exploring  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Scope**: List pending approvals, agent questions and workflow step questions in the dashboard Overview's "Needs attention" with a `(N)` title count; declare kiro-cli's `userInput` capability and answer `_kiro/userInput`; handle `_kiro/session/notify` and paused workflow runs; fix `_note_subagent_action`'s crew-frame filter

---

## Intent

### Problem statement & desired outcomes

Three roadmap follow-ups (`plans/ROADMAP.md`, Session Control & Integration), explored together because two of them meet at one concept: a session is waiting on the user.

1. **Nothing tells you which session needs you.** A permission request or an agent's question in a session you are not looking at is invisible until you open that session. The desktop toast says *a* session needs you, not *which*. The Overview's "Needs attention" section (`#dashOvAttention` in `index.html`) already lists quota-stopped sessions and repositories with uncommitted work, but not approvals or questions.
2. **Agent questions are partly lost.** kiro-cli asks clarifying questions through its `user_input` tool (measured in quick-spec mode). PowerAtlas does not declare the `userInput` client capability, so kiro-cli falls back: a question with options arrives as an ordinary permission card without the options' descriptions, and a free-form question with no options is skipped to the next phase without the user ever seeing it.
3. **Workflow step messages are only logged.** A step's `send_message` arrives as `_kiro/session/notify` and reaches only the INFO log. A warning-severity message is how a step asks the user a question; the step's row shows nothing and the parent does not read as waiting. When kiro-cli does pause a run, `run_complete` arrives with `status: "paused"`, which PowerAtlas maps to `failed`.
4. **Small crew-frame bug.** `_note_subagent_action` emits the crew frame with the v2 fan-out map, so a row tagged with a fan-out wave is filtered out of that one frame.

Desired outcome: one place that shows everything waiting on the user, with each item opening the session where it is answered; questions answered with the agent's full options or free text; workflow step messages visible in the transcript and on the step's row; a paused workflow shown as waiting, not failed.

### Success criteria

- **SC-1 (attention list).** The Overview's "Needs attention" section shows, across every session PowerAtlas holds, one row per pending approval, pending agent question and open workflow step question. Each row shows the kind, the session title and workspace, the request or question text (cleaned, `textContent` only) and its age. Clicking a row opens that session. A row disappears within one poll after the item is answered, cancelled or cleared.
- **SC-2 (feed and title count).** A loopback-only endpoint (for example `GET /api/dashboard/attention`, `pa_local` cookie required, not in `_REMOTE_ALLOWED_PATHS`) returns the items, built from a loop-side snapshot of supervisor state. The dashboard polls it about every 5 s in every mode, including while a transcript is open and while the tab is hidden, and prefixes the page title with `(N)` when N > 0. `/acp` titles are unchanged.
- **SC-3 (`userInput` capability).** `initialize` declares `_meta.kiro.userInput: true`. A `_kiro/userInput` request is shown on `/acp` and in the dashboard panel as a question card: the question, each option's title and description, the recommended option marked, and a free-text answer box. Answering replies `{action: "answered", answer}`. Stop, close, turn end and detach reply with a non-answered action, so kiro-cli moves to its next phase rather than hanging. A request from a tracked workflow step is routed to the parent, as permission requests already are.
- **SC-4 (step messages).** `_kiro/session/notify` from a step (`sender: "step"`) sets the step's crew-row action text for every severity. For `warning` and `error` it also adds a recorded system row to the parent's transcript, which survives a reconnect. Replayed `system-notification` user-message chunks (on `session/load`) render as the same system row instead of a user bubble. Step text is cleaned like `_clean_step_label` (control and bidi characters dropped, clipped to a message-length limit).
- **SC-5 (step waiting state).** A warning notify from a step marks that step `waiting` immediately (row status and child state), so the parent reads Waiting through the existing overlay and the item appears in "Needs attention". The step returns to running on the first of: a notify from the parent to that step (`sender: "parent"`, `sessionId` = the step), a new `tool_call` from the step, or `node_complete`.
- **SC-6 (paused runs).** `node_paused` and `run_complete` with `status: "paused"` keep the step rows `waiting`, never `failed`; the run stays tracked under the existing 6 h waiting bound; the crew header reads "Paused" rather than "Done".
- **SC-7 (crew-frame fix).** `_note_subagent_action` emits its frame filtered by the changed entry's own `fan_out_id`. A unit test seeds a wave-tagged row, drives a `tool_call_chunk` for it, and asserts the row is in the emitted payload; the test fails on the unfixed code.
- **SC-8 (verification).** `pytest tests/test_web.py --timeout=300` and `node tests/acp_page.test.mjs` pass. Live QA in a real browser, after a PowerAtlas restart granted by the user, covers: a quick-spec question answered from a card, a free-form question answered with free text, a Manual-mode approval and a question appearing in "Needs attention" with the title count, a workflow step question showing on the row, in the transcript and in "Needs attention", and the row clearing after the answer.

### Scope boundaries & non-goals

- **In scope:** the three roadmap items above, plus the paused-run mapping found during exploration (it is part of the step waiting state).
- **Out of scope:**
  - Unwatched turn ends in "Needs attention". The rail's static-green dot and the unwatched `turn_end` toast already mark them, and the server has no "seen" state to clear them. Reopen if a seen state is added.
  - Answering from the Overview without opening the session.
  - A title count on `/acp`.
  - Workflow Layers 3 and 4 (monitor UI, child-session subscriptions) and the other workflow follow-ups on the roadmap, except where this work touches them.
  - Codex and Claude Code sessions: the pending state comes from the kiro-cli supervisor only.

---

## Exploration Discovery
<!-- Transient: /qplan folds these into the planning sections and removes this section. -->

### Existing patterns & constraints

- **Pending permissions.** `_pending_permission` (`acp.py:3723`) maps an opaque id to `{session_id, options, kiro_id}` plus `workflow_child` for a routed step request (`acp.py:6884-6895`). It stores no title, consent or timestamp; those exist only in the recorded `permission_request` frame (`_emit`, `acp.py:6957`). `sessions_with_pending_permission()` (`acp.py:5069-5081`) returns session ids only and is read only by `_workflow_states` (`web.py:3348-3387`). Removal paths: answer (`_handle_permission_response`, `acp.py:8919-8993`), turn-end sweep keeping live step requests (`acp.py:8740`, `8661`), Stop with and without a turn (`acp.py:9066`, `9092`), close (`acp.py:7307`), step dropped (`_cancel_step_permissions`, `acp.py:4830-4868`), detach (`acp.py:3939-3947`).
- **Workflow-step routing.** `_workflow_step_parent` (`acp.py:5124-5142`) and the `[workflow step <name>]` prefix (`acp.py:6951-6956`) are the precedent for routing a step's request to the parent.
- **Unknown agent requests are refused.** `_on_agent_request` (`acp.py:6636-6653`) routes only `session/request_permission`; anything else gets a WARNING and a refusal. `_kiro/userInput` must be added there.
- **Client capabilities.** `_kiro_client_capabilities()` (`acp.py:1013-1027`) builds `_meta.kiro` for `initialize`; `userInput` goes there.
- **Overview data.** The summary route (`web.py:4448-4505`) runs in a worker thread and never reads the supervisor. The live route (`web.py:4627-4647`) snapshots `held`, `_workflow_states` and `_inflight_ids` on the loop and passes them to the thread: the pattern to copy. Live tiles are capped at 8 (`overview.py:417`), so a pending session may have no tile. Both routes are loopback-only (`web.py:4560-4564`).
- **Overview rendering.** "Needs attention" blocks (`index.html:370-376`) are keyed by a JSON string and rebuilt only on change (`dashOvRenderInterrupted`, `index.html:1713-1737`; `dashOvDrawRepos`, `index.html:1328-1366`). Opening a session: `dashOvOpenTile` → `openSessionTranscript({sid, provider, cwd}, …)` (`index.html:1920-1924`). Polling stops while a transcript is open or the tab is hidden (`index.html:1010-1030`, `1776-1848`). `index.html` never sets `document.title`; `acp.html` sets it from the `title` frame (`acp.html:1262`, `5098`, `5230`).
- **Crew frames.** `_emit_subagents_frame` (`acp.py:7532-7575`) uses one value as both payload filter and `toolCallId`; `_subagents_payload` keeps entries whose `fan_out_id` matches (`acp.py:7504-7529`). v2 rows are tagged from `crew_spawn_toolcallids` (`acp.py:4700-4702`), wave and workflow rows from `_active_fan_out_wave` (`acp.py:5583-5584`, `6437-6443`). `_note_subagent_action` (`acp.py:4716-4735`) is the only v3-path call site passing the v2 map. Clients: `setCrew` (`acp.html:2005-2074`) and `dashSetCrew` (`index.html:4454-4527`) are mirrors.
- **Workflow state.** `_workflow_children[parent][child] = (state, last_seen)` with `state` running or waiting, written only by `_workflow_set` (`acp.py:4775`). `_workflow_reopen_row(entry, "waiting")` (`acp.py:5207`) exists. `workflow_state` returns waiting only when no fresh child is running (`acp.py:5043-5067`). The overlay upgrades only a waiting or idle verdict and leaves pending-permission sessions alone (`web.py:3326-3345`). Waiting bound 6 h (`acp.py:689`), running bound 600 s (`acp.py:682`). `_workflow_outcome` (`acp.py:5398-5412`) maps any status but `completed` to `failed`. `_kiro/workflow/*` frames are routed by `parentSessionId` (`acp.py:5414+`).
- **`_kiro/session/notify` today.** Not matched by the workflow router (`acp.py:5983-5985`); `_stamp_activity(parent)` runs, `_workflow_touch` does not (the parent is not a step), and it falls through to the INFO log (`acp.py:6385-6396`). The `user_message_chunk` branch deliberately leaves `system-notification` chunks alone (`acp.py:6061-6066`).
- **Frames.** `envelope()` rejects any type not in `SERVER_TYPES` (`acp.py:200-250`, `1715-1727`); `TestAcpServerTypeGuard` (`tests/test_web.py:3093-3115`) checks every declared type. A recorded frame (`_emit`) replays on reconnect within `HISTORY_MAXLEN = 2000` / 2 MiB (`acp.py:374-375`); a broadcast does not. Remote sockets get permission frames minus the `rule*` fields (`acp.py:1542-1572`).
- **Toasts.** `_notify` (`acp.py:1615`) through `_notify_from_acp` (`web.py:1002-1037`): `permission_request` always toasts when notifications are on; `turn_end` and `agent_error` only when unwatched.
- **Text cleaning.** `_clean_step_label` (`docs/KNOWLEDGE.md` § ACP permission wire shapes, "Workflow step approvals") drops control and bidi characters, replaces brackets and clips to 60.
- **Governance (`AGENTS.md`).** Python changes need a restart, never without the user's grant; template and static changes need only a hard reload. `tests/acp_page.test.mjs` bans `innerHTML` and has no CSS engine (any `display` rule on a `hidden`-toggled element needs a `[hidden]{display:none}` companion). The harness pre-registers Overview element ids in `byId` (`tests/acp_page.test.mjs:15070-15090`); new block ids must be added there. `isolated_config` seams in `tests/test_web.py`. Terminology: "Stopped by a quota limit" for the interrupted list, "permission mode" not "posture".
- **Tests to extend.** `TestAcpSubagentActivity` (`tests/test_web.py:11797-11830`, v2 route only, both maps empty, so it does not pin the bug), `TestAcpFanOutIdFiltering` (`23967`), `TestAcpWorkflowNotifications` (`12331`, including workflow permission routing `13785-14096`), `TestWorkflowLivenessStatus` (`41544`), `TestOverviewLive` (`36502`), `TestNotificationPolicy` (`10838`), `TestAcpUnrecognizedNotificationLogLevel` (`3597`); page tests for crew panels (`tests/acp_page.test.mjs:6280+`), permission cards (`8948-9003`, `12427-12480`) and Attention blocks (`22451-22550`).

### Measurements taken during exploration

All on kiro-cli 2.29.1, 2026-10-12, from a second `kiro-cli acp --agent-engine v3` process driven by a scratch script built on `tools/acp_permission_probe.Probe` (never the running PowerAtlas). Six runs; all 10 probe sessions deleted afterwards (each checked to be created inside the task window).

- **Agent questions (quick-spec, n=2 runs, 7 questions).** Without the capability: each question was a `tool_call` with `_meta.kiro.toolId: "user_input"`, then `session_info_update` kind `pending_interaction` (`interactionType: "user_input"`, `question`, `options`), then `session/request_permission` whose options were all `kind: "allow_once"` with the choice labels as names and the question as `toolCall.title`; after the answer, `interaction_resolved`. With `_meta.kiro.userInput: true`: the same `tool_call` and `pending_interaction`, then a `_kiro/userInput` request `{sessionId, toolCallId, question, options: [{title, description, recommended}]}`; reply `{action: "answered", answer: "<free text>"}` was accepted (`interaction_resolved` with `outcome: "answered"`). Bundle reading, not run: a free-form question with no options and no capability is skipped to the next phase (`freeFormQuestion.count` metric, disposition `advanced`); a non-answered reply to `_kiro/userInput` makes kiro-cli move to the next phase.
- **`userInput` scope (bundle).** `userInputCapability` is read in 6 places, all on the question paths, plus one check that allows spec modes in a sandbox environment (`kis()`); PowerAtlas sessions are not sandboxed.
- **Workflow step questions (n=4 runs, one step each).** Every run: the step's warning `send_message` reached the parent as `_kiro/session/notify` (`sender: "step"`, `severity: "warning"`, `callerSessionId` = the step), and kiro-cli ran a parent wake turn (`agentInitiated: true`) relaying the question. The user's answer, sent as a parent prompt, went to the step as `_kiro/session/notify` with `sessionId` = the step, `callerSessionId` = the parent, `sender: "parent"`, `severity: "info"`, followed by `steering_injected` on the step; the step then finished (`success` notify, `node_complete`, `run_complete` `completed`). In 3 of 4 runs the step stayed in its turn while waiting; the parent's `focus_update` stayed `in_progress` with `runningWorkflows: 1`, so kiro-cli gave no waiting signal besides the notify. In 1 of 4 runs the step ended its turn: kiro-cli sent `node_paused` (`workflowId`, `nodeId`, `nodePath`, `reason`, no `sessionId`), `_kiro/workflow/paused` (`pauseReason`), and `run_complete` with `status: "paused"`; the parent's `focus_update` read `waiting_on_user` with `activity.pausedWorkflows: 1`. That paused run was not answered, so its resume path is unmeasured.
- **`tool_call_chunk` from workflow steps: none** in 4 runs (steps sent `tool_call` and `tool_call_update` only). The `_note_subagent_action` bug is not reachable through workflows on 2.29.1. v3 sub-task rows are keyed by `agentSubtaskId`, not a session id, so a chunk cannot reach them either.
- **Production log** (`orchestrator.log` and 3 rotations, full scan): 58 `_kiro/session/notify`, all `sender: "step"`, all `severity: "success"`; 0 `node_paused` or `workflow/paused`; 0 refused agent requests; 24 `_kiro/system/notify` (`level: "warning"`, "The model response paused unexpectedly…"), a separate notification not in scope.

### Risks & mitigations

- **A paused run's resume is unmeasured.** Mitigation: keep the run tracked as waiting under the 6 h bound and move the step back to running on any later step activity; probe a real pause and resume during `/qdev` (prompt a step to end its turn after asking) and record it in `docs/KNOWLEDGE.md`.
- **Abandoning a `_kiro/userInput` request.** The non-answered reply is read from the bundle, not run. Mitigation: measure it in the `/qdev` probe before relying on it; never reply with a JSON-RPC error until measured (a permission error was once read as approval, `tools/acp_permission_probe.py` K3 note).
- **`_kiro/userInput` from a workflow step is assumed.** No step asked a `user_input` question in the probes. Mitigation: route it like permission requests through `_workflow_step_parent`, and record it as unverified until observed.
- **Double rendering after `session/load`.** A live step-message row plus the replayed `system-notification` chunk. Mitigation: render the replayed chunk as the same system row (SC-4), and check a load in live QA.
- **History eviction.** A pending request whose recorded frame falls out of the 2000-event / 2 MiB history has no card after reconnect. The attention list reads the supervisor store, so it still shows the item. Pre-existing; not fixed here.
- **Background-tab throttling.** Browsers may slow a hidden tab's timers to about once a minute, so the title count can lag that long.
- **Agent-authored text** (questions, options, step messages) reaches the Overview and transcripts. Mitigation: clean and clip on the server, `textContent` on the client; the attention endpoint stays loopback-only.
- **Wrong fix for item 1.** Passing the wave map would break the v2 path. Mitigation: filter by the changed entry's own `fan_out_id` (SC-7).

### Resolved decisions

- Q1: How should the three follow-ups be split? — A: ok (recommended: one combined project file, keep item 1's fix) — Decision: one combined project file; item 1's fix stays in scope as a small phase.
- Q2: Declare the `userInput` capability and answer `_kiro/userInput`, or keep the permission-card fallback? — A: ok (recommended: declare it) — Decision: declare `_meta.kiro.userInput: true`; add a question card with option descriptions, the recommended mark and a free-text answer on both pages.
- Q3: How does "Needs attention" get its items, and how far does the title count reach? — A: ok (recommended: a dedicated endpoint polled in every mode, title count on the dashboard only) — Decision: a loopback-only attention endpoint built from a loop-side supervisor snapshot, polled about every 5 s in every mode; `(N)` title prefix on the dashboard only.
- Q4: Which kinds of item go in the list? — A: ok (recommended: approvals, questions and step questions; no unwatched turn ends; rows open the session) — Decision: approvals, agent questions and workflow step questions; each row shows kind, session title and workspace, text and age, and opens the session; no answering from the Overview; unwatched turn ends excluded.
- Q5: Where does a step's `send_message` text appear? — A: ok (recommended: split) — Decision: every severity updates the step's crew-row action; `warning` and `error` also add a recorded system row to the parent transcript; replayed `system-notification` chunks render as that row; text cleaned like `_clean_step_label` with a message-length clip.
- Q6: What does a step question do to the step, and how is a paused run shown? — A: ok (recommended: mark waiting at once; back to running on parent reply, step tool call or `node_complete`; paused run kept waiting, header "Paused") — Decision: as recommended; the resume path for a run kiro-cli really paused stays unmeasured until probed.

Terminology settled in this exploration (for `/qplan`'s Terminology proposal): **approval** (a pending permission request for a tool call; not "permission prompt" in UI text), **question** (an agent's `user_input` question, whether it arrives as `_kiro/userInput` or as the permission-card fallback; not "clarifying question" in UI text, though the roadmap used that phrase), **step question** (a workflow step's warning-severity `send_message`; not "step notification"). "Needs attention" stays the section name.

### Open items

- *Execution-contingent:* the resume path for a run kiro-cli paused (`run_complete` `status: "paused"`); needs a probe that reproduces the pause and then answers.
- *Execution-contingent:* kiro-cli's handling of a non-answered `_kiro/userInput` reply, and which `action` value to send; measure before Stop, close and turn end rely on it.
- *Execution-contingent:* whether a workflow step can send `_kiro/userInput`, and with which `sessionId`.
- *Deterministic:* where the `(N)` title prefix composes with any future dashboard title write (none exists today).
- *Deterministic:* the message-length clip for step text and question text (reuse `MAX_ERROR_DETAIL_CHARS` or `MAX_PERMISSION_TITLE_CHARS`, `acp.py:649`).

### Assumptions (unconfirmed)

Disclosed at the assumptions checkpoint; the user replied "no let's go" without changes.

- A question raises a desktop toast like a permission request: always, when notifications are on (Quality attributes / UX).
- The question card offers no extra Skip button; kiro-cli supplies its own "Skip remaining questions" option (UX flow).
- `_kiro/userInput` cards reach remote viewers the way permission cards do; the attention endpoint stays loopback-only (Quality attributes: security).
- One supervisor store holds approvals and questions with text, options, kind and creation time; step questions live in a per-step record beside `_workflow_children` (Data model).
- `_note_subagent_action` filters by the changed entry's own `fan_out_id` (Design space, item 1).

### Recommended approach

Five phases, in dependency order:

1. **Crew-frame fix (SC-7).** Emit `_note_subagent_action`'s frame with the entry's own `fan_out_id`; add the failing-first unit test.
2. **Pending store and `userInput` (SC-3).** Extend the pending record with kind, text, options and created time; declare `userInput`; accept `_kiro/userInput` in `_on_agent_request` (routed to the parent for a tracked step); add a frame type for the question and a resolved companion (or reuse `permission_resolved`); answer route with free text; non-answered replies on Stop, close, turn end and detach after measuring them; question card on both pages (options with descriptions, recommended mark, free-text box); toast policy for questions. Measure the abandon reply first.
3. **Step messages and waiting state (SC-4, SC-5, SC-6).** Handle `_kiro/session/notify`: `sender: "step"` updates the row and, for warning and error, emits a recorded system row; warning marks the step waiting; `sender: "parent"` to a step moves it back to running; replayed `system-notification` chunks render as the system row. Map `node_paused` and `run_complete` `status: "paused"` to waiting and a "Paused" header. Probe a real pause and resume and record it in `docs/KNOWLEDGE.md`.
4. **Attention feed (SC-1, SC-2).** Loop-side snapshot of pending approvals, questions and open step questions with session title, workspace, provider and cwd; loopback-only endpoint; dashboard poller in every mode; new "Needs attention" blocks (approvals and questions, step questions) keyed and rebuilt like the existing ones; `(N)` title prefix.
5. **Docs and live QA (SC-8).** README row for the attention list and question cards; `docs/KNOWLEDGE.md` sections for `user_input`, `_kiro/session/notify` and paused runs; roadmap entries updated; live QA after a granted restart.

### QA environment

- **Unit tests:** `.venv-PowerAtlas/Scripts/python -m pytest tests/test_web.py --timeout=300`; page tests: `node tests/acp_page.test.mjs`.
- **Protocol probes:** a second `kiro-cli acp --agent-engine v3` driven by a script built on `tools/acp_permission_probe.Probe` (spawn, token and permission answering), with `_meta.kiro.settings.workflows/goal` enabled for workflow runs and `modeId: "quick-spec"` for questions. A one-step workflow whose step sends a warning `send_message` reproduces step questions; a quick-spec prompt asking for clarifying questions reproduces `user_input` questions. Delete probe sessions afterwards by id and `createdAt` (`~/.kiro/sessions/<hash>/sess_<id>/`; `session.json` has no `cwd`).
- **Live QA:** needs a PowerAtlas restart (Python changes), granted by the user for that task. Sign in through `/local-auth?code=<code>` per `AGENTS.md` § Verification Setup; drive the pages with the Chrome tools or standalone Playwright from the venv. Manual mode (`POST /api/acp-permissions {"mode":"manual"}`, restore after) produces approvals; a quick-spec session produces questions; a workflow-enabled session (`chat.enableWorkflows`) produces step questions.

---

## Harness Improvement Opportunities

- Step 1.5's three code-tracing briefs overlapped heavily on this scope: all three enumerated the same `_emit_subagents_frame` call sites and pending-permission removal paths — cost: about 630k sub-agent tokens for roughly one report's worth of distinct facts — suggested change: when Step 1 already names the call sites, tell the subsystem brief to skip enumerations the directed brief owns.
- The decisive findings (the `user_input` question path, the paused-run mapping) came from probes and a vendor-bundle grep, not from the trio — cost: the trio's "clarifying question" answer was "no mechanism found", which would have shaped the interview wrongly without the probes — suggested change: add "grep the vendor bundle for the concept" to the Probe gate's examples when the work integrates with a third-party protocol.
