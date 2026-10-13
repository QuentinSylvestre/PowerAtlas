# ACP Needs Attention: Approvals, Agent Questions and Workflow Step Messages

> **Date**: 2026-10-12
> **Status**: In Progress  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Last Updated**: <set by /qclose at archival>
> **Scope**: List pending approvals, agent questions and workflow step questions in the dashboard Overview's "Needs attention" with a `(N)` title count; declare kiro-cli's `userInput` capability and answer `_kiro/userInput`; handle `_kiro/session/notify` and paused workflow runs; fix `_note_subagent_action`'s crew-frame filter
> **Memory relied on**: none
> **Estimated effort**: 2-3 days

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
- **SC-9 (no cancel while waiting on a person).** A turn is never cancelled for silence: the prompt silence watchdog and its `acp_prompt_silence_seconds` setting are removed, and only Stop or the 24 h absolute ceiling end a turn. Stop ends the turn locally within a few seconds when the agent does not answer the cancel. The workflow reap no longer cancels a step's open approvals or questions. Added by the user's decisions of 2026-10-12, after the first review.
- **SC-8 (verification).** `pytest tests/test_web.py --timeout=300` and `node tests/acp_page.test.mjs` pass. Live QA in a real browser, after a PowerAtlas restart granted by the user, covers: a quick-spec question answered from a card, a free-form question answered with free text, a Manual-mode approval and a question appearing in "Needs attention" with the title count, a workflow step question showing on the row, in the transcript and in "Needs attention", and the row clearing after the answer.

### Scope boundaries & non-goals

- **In scope:** the three roadmap items above, plus the paused-run mapping found during exploration (it is part of the step waiting state); and, by the user's decisions of 2026-10-12, removing the prompt silence watchdog and the workflow reap's cancellation of step requests.
- **Out of scope:**
  - Unwatched turn ends in "Needs attention". The rail's static-green dot and the unwatched `turn_end` toast already mark them, and the server has no "seen" state to clear them. Reopen if a seen state is added.
  - Answering from the Overview without opening the session.
  - A title count on `/acp`.
  - Workflow Layers 3 and 4 (monitor UI, child-session subscriptions) and the other workflow follow-ups on the roadmap, except where this work touches them.
  - Codex and Claude Code sessions: the pending state comes from the kiro-cli supervisor only.

---

## 1) Current State

Code is cited by function or heading, with a line number as a hint only (lines drift). Runtime facts carry the command and date that established them.

### Pending permissions (`acp.py`)

- `_Supervisor._pending_permission` (`__init__`, ~3723) maps an opaque id to `{session_id, options, kiro_id}`, plus `workflow_child` for a request routed from a workflow step. It stores no text, kind or creation time; the title exists only in the recorded `permission_request` frame.
- `_on_agent_request` (~6636) routes `_kiro/auth/getAccessToken`, `session/request_permission`, the secret methods and `_kiro/openExternalUrl`. Anything else is logged at WARNING and refused through `_refuse`.
- `_on_permission_request` (~6745) is the pattern a question handler copies. It validates the id and options. It routes a tracked step's request to the parent through `_workflow_step_parent` (~5124) and prefixes the title with `[workflow step <name>]` through `_workflow_step_label`. It mints the opaque id and stores the entry. It then emits the recorded `permission_request` frame, raises the `_notify("permission_request", …)` toast and runs `_workflow_touch(step)`.
- Every path that removes an entry without the user's answer replies through `_Supervisor._answer_permission_cancelled(kiro_id, why)` (~4410), which writes the permission shape `{"outcome": {"outcome": "cancelled"}}`. The call sites:
  - detach: the `permission_resolved` broadcast loop, then `_pending_permission.clear()` (~3939). It writes no reply, because the agent is gone.
  - `_cancel_step_permissions` (~4830).
  - the malformed-request refusal in `_on_permission_request` (~6871).
  - close (~7307, through `_pop_pending_permissions`).
  - the turn-end sweep in `_handle_prompt`'s `finally` (~8661 and ~8736).
  - Stop, with no turn (~9066) and in turn (~9092, `notify_denial=False`).
- `_pop_pending_permissions` (module level, ~8740) pops the entries, announces each with `permission_resolved` and returns them for the caller to answer.
- `_handle_permission_response` (~8919) answers one entry. Its ordering is load-bearing: (a) unknown id, (b) ownership by `conn.session_id`, (c) `optionId` must be in `{opt["optionId"] for opt in entry["options"]}`, (d) pop, (e) reply. Step (c) would raise on a question entry (`KeyError` or `TypeError`, depending on what its options hold), so a `kind` check must come first.
- `sessions_with_pending_permission()` (~5069) returns the owning session ids. It feeds `web.py`'s `_workflow_states` overlay, so a parent with a pending request is not turned from waiting to working.

### Shared transcript renderer

- The permission card is built once in `static/transcript-renderer.js`: `addPermissionRequest` (~1917), `findPermissionRequestRow` (~2577), `markPermissionResolved` (~2596, disables `button` elements only) and `hasPendingStepPermissionCard` (~2622). `acp.html` and `index.html` call it from their `permission_request` and `permission_resolved` handlers. The page harness loads these functions by name (`rendererFunctionSource` list in `tests/acp_page.test.mjs`).

### Client capabilities and toasts

- `_kiro_client_capabilities()` (~1012) builds `clientCapabilities._meta.kiro` for `initialize`.
- `_notify` sends toasts through `web.py`'s `_notify_from_acp` (~1002). There, `if event == "permission_request":` (~1026) is the branch that always toasts while notifications are on.

### Frames

- `SERVER_TYPES` (~200) is checked by `envelope()`, and `TestAcpServerTypeGuard` checks every declared type. `CLIENT_TYPES` (~193) gates inbound frames; `permission_response` is dispatched around ~7847.
- `_emit` records a frame and replays it on reconnect, within `HISTORY_MAXLEN = 2000` and 2 MiB. `_registry.broadcast` does not record.
- `_without_rule_fields` (~1548) strips the `rule*` fields from `permission_request` frames for remote viewers. Every other type passes through unchanged.

### Workflow state (`acp.py`)

- `_workflow_children[parent][child] = (state, last_seen)` is written only by `_workflow_set` (~4775). `workflow_state` (~5061) returns `"waiting"` only when no fresh child is running. The waiting bound is `_WORKFLOW_WAITING_STALE_S` (6 h) and the running bound `_WORKFLOW_CHILD_STALE_S` (600 s).
- `_workflow_touch` (~5157) refreshes a tracked child and preserves its state by design. It never flips waiting to running.
- `_workflow_reopen_row(entry, state)` (~5207) sets `status` and `action` to waiting or running.
- `_on_workflow_notification` (~5414) handles `_kiro/workflow/*` frames:
  - `node_paused` already goes to `_on_workflow_node_done(parent, params, "waiting")`. The measured shape (`workflowId`, `nodeId`, `nodePath`, `reason`, `parentSessionId`; probe run1, 2026-10-12) resolves through the `(parent, workflowId, nodeId)` index, so this path works. Its comment still says "Shape not measured".
  - `_kiro/workflow/paused` is logged only.
  - `_on_workflow_run_done` (~5655) reads `_workflow_outcome(status)`, which maps anything other than `completed` to `failed` (~5398). For every child of the run it calls `_workflow_drop_child` and `_workflow_finish_row`, then pops `_workflow_runs` and calls `_workflow_mark_ended`.
  - So `run_complete` with `status: "paused"` marks the waiting step **failed** and forgets the run. This is the SC-6 defect.

### `_on_notification` order and `_kiro/session/notify` (`acp.py` ~5975 onward)

1. The workflow router.
2. `_stamp_activity(params.sessionId)`.
3. `_workflow_touch(sessionId)` when the id is a sub-agent session.
4. The early-frame buffer for an unregistered id while `_reserved > 0`.
5. The method branches.

`_kiro/session/notify` matches none of the branches and falls through to the INFO log at the end (~6385). A notify addressed to a step (`sessionId` = the step) would be caught by the early-frame buffer if a `session/new` happened to be in flight. The new handler must therefore sit after step 3 and before step 4.

### The `user_message_chunk` branch (~6044)

It leaves `_meta.kiro.notification` chunks as ordinary user chunks. On `session/load`, a step's `send_message` replays as a user chunk tagged `notification.kind == "system-notification"` with `status`, `agentName`, `nodeName`, `sender` and `replay: true` (`docs/KNOWLEDGE.md` § "`_kiro/workflow/*` notification family", Q7). It renders as a user bubble today.

### Crew frames

- `_emit_subagents_frame(parent, crews, fanout_map)` (~7532) filters the payload by `fanout_map.get(parent, _NO_ANCHOR_TOOLCALLID)` and uses the same value as `toolCallId`.
- v2 rows are tagged from `crew_spawn_toolcallids`; wave and workflow rows from `_active_fan_out_wave`.
- `_note_subagent_action` (~4716) passes `self.crew_spawn_toolcallids`, so a wave-tagged row is filtered out of that frame. Not reachable through workflows on kiro-cli 2.29.1: steps sent no `tool_call_chunk` in 4 probe runs (2026-10-12).
- On the clients, `renderCrewPanel` (`acp.html` ~1887) and its mirror (`index.html` ~4335) print "Done (n)" when every row is done and `workflowLive` is false, else "Orchestrating (n)".
- `subagentState` (`acp.html` ~1965) returns `'working'` for any row that is not done, so a waiting row shows the working dot. The rail's `.status-waiting` colour lives in `style.css` (~561).

### Overview (`web.py`, `index.html`)

- `api_dashboard_overview_live` (~4630) snapshots `held`, `_workflow_states` and `_inflight_ids` on the loop and hands them to `_overview_live` in a thread. That is the pattern to copy.
- Overview routes are loopback-only by their absence from `_REMOTE_ALLOWED_PATHS`.
- Titles come from `_acp_row_title(session)` (~3250) over `data.get_sessions(cwd, "kiro-cli-v3")`, which is cached.
- The supervisor's session record stores no title. The `focus_update` branch (~6309) emits a `title` frame and keeps nothing.
- "Needs attention" is `#dashOvAttention` (`index.html` ~370), with blocks `#dashOvInterruptedBlock` and `#dashOvReposBlock`. Both are rebuilt only when their JSON key changes (`dashOvRenderInterrupted` ~1713).
- `dashOvOpenTile` (~1920) opens a session through `openSessionTranscript({sid, provider, cwd}, tile, {cwd, name})`.
- The Overview pollers run only while the Overview is showing and the tab is visible (`dashOverviewPoll` ~1022).
- `index.html` never writes `document.title`.

### kiro-cli 2.29.1 facts (probe runs 1-6, a second `kiro-cli acp --agent-engine v3`, 2026-10-12)

- **`_kiro/userInput` request.** With `clientCapabilities._meta.kiro.userInput: true`, a question arrives as `{sessionId, toolCallId, question, options: [{title, description, recommended}]}`. Without the capability it arrives as a `session/request_permission` with `allow_once` options.
- **Replies, read from the bundle** (`acp-server.js`, the `_kiro/userInput` call site): `o.action==="answered"&&o.answer` answers. Any other action, an empty answer, or an error reply moves kiro-cli to the next phase. In run 5 the answer `"blue"` did not match any option, and the agent said so in its next question. A choice is therefore selected by sending its `title` verbatim.
- **Step questions.** A step's warning `send_message` reaches the parent as `_kiro/session/notify` `{sessionId: <parent>, callerSessionId: <step>, message, severity, sender: "step", workflowId, nodeId, agentName}`. The parent's relayed answer reaches the step as `{sessionId: <step>, callerSessionId: <parent>, message, severity: "info", sender: "parent"}`. In 1 of 4 runs kiro-cli also sent `node_paused`, `_kiro/workflow/paused` and `run_complete` `status: "paused"`; its resume was not measured.
- **Production log.** The full scan of `orchestrator.log` and 3 rotations found 58 notifies, all `sender: "step"` and `severity: "success"`.

## 2) Goal

Keep one supervisor store for everything a person must answer: approvals, questions, and step questions tracked beside the workflow children. Answer questions in kiro-cli's native `userInput` shape. Render step messages and paused runs truthfully. Expose the store to a loopback attention endpoint that the dashboard polls into "Needs attention" and a `(N)` page-title count.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| D1 Plan split (Q1, user "ok" 2026-10-12) | One combined plan; the crew-frame fix is its own small phase | Three plans | Approvals, questions and step questions meet in one store and one list |
| D2 Question transport (Q2) | Declare `_meta.kiro.userInput: true` and answer `_kiro/userInput` | Keep the permission-card fallback | The fallback drops option descriptions and skips free-form questions unseen |
| D3 Store | One store, `_pending_permission`, with a new `kind` field (`"approval"` or `"question"`) and new `text` and `created` fields on every entry | A second dict for questions | `_pop_pending_permissions`, `_cancel_step_permissions`, detach and `sessions_with_pending_permission` (and so the waiting overlay) then cover questions unchanged. An entry without `kind` reads as `"approval"` |
| D4 Abandon reply | New `_Supervisor._answer_pending_cancelled(entry, why)` with a strict rule: `if entry.get("kind") == "question":` reply `{"action": "cancelled"}`; **every other value, missing or unknown, gets the permission `cancelled` outcome**. `kind` is set only by the handler of the JSON-RPC method (`_on_permission_request` or `_on_user_input_request`), never from the agent's payload. Every non-malformed call site in §1 that passes `entry["kiro_id"]` switches to it; the malformed-request refusals keep their own reply. A question entry gets **no** `_workflow_note_step_denied` notice on Stop or close: that notice says a tool call was denied, which is false for a question; the card's resolved state is the only signal. Detach writes nothing for either kind: the agent process is already gone, so SC-3's "detach" case is met by the `permission_resolved` broadcast alone | A JSON-RPC error for questions | The bundle treats any non-`answered` action as next-phase. A misclassified approval answered with the question shape could fail open on kiro-cli's turn-approval path (K3 history), so the default must be the permission shape. Confirmed by Phase 1 |
| D5 Question frames | New server type `question_request` `{requestId, sessionId, question, options: [{title, description, recommended}], workflowStep?}`, recorded with `_emit`. Resolved by the existing `permission_resolved {requestId}` | A separate `question_resolved` | Both pages already disable a card on `permission_resolved`, and every removal path already emits it |
| D6 Answer route | New client type `question_response`, either `{requestId, optionIndex}` (an option button) or `{requestId, answer}` (free text), handled by `_handle_question_response` with the same (a)-(e) ordering as `_handle_permission_response`. (c) checks `entry.get("kind") == "question"` strictly (anything else is `unknown_request`), then either `optionIndex` is an `int` (not `bool`) in range, and the reply carries the stored raw title `entry["answer_titles"][optionIndex]`, or `answer` is a string, stripped, 1 to `MAX_QUESTION_ANSWER_CHARS` (4000) characters. An empty answer is refused with `empty_answer`; both or neither key is refused with `bad_payload`. **No `await` and nothing that can raise between the lookup (a) and the pop (d)**, so a replayed `requestId` hits `unknown_request`. `_handle_permission_response` gains a `kind` check before its `optionId` check and refuses a question entry with `unknown_request`. Remote viewers can answer, gated exactly like `permission_response` (ownership only), deliberately. Step (e) copies the permission route's failure handling: on `AcpError` the reply write failed because the agent channel is broken, and `_supervisor._discard(...)` tears the agent down for every session, as it already does for a failed approval reply | Reuse `permission_response` with free text | The option-membership check cannot apply to free text, and a question entry would raise `KeyError` in it |
| D7 Option answers | The pending entry keeps each option's **raw** title, as kiro-cli sent it, under its own key `answer_titles` (never `options`, which approval readers iterate as dicts). An option is kept only when its raw title is a non-empty string: an empty answer would skip the question. The card shows cleaned, clipped copies. A button sends its index, and the server maps it to the raw title, so cleaning or clipping can never turn a choice into an unmatched free-text answer. Free text is the third path. The recommended option gets a "Recommended" badge. No extra Skip button: kiro-cli offers "Skip remaining questions" as an option (assumption confirmed at the /qexplore checkpoint, user "no let's go", 2026-10-12). At most `MAX_QUESTION_OPTIONS` (20) options are kept: the first 19 plus the last one, so kiro-cli's own "Skip remaining questions" survives if it sits last (Phase 1 probe 1 records where it sits); extras are dropped and logged at INFO | Send the title from the client | kiro-cli matches the answer against option titles (run 5); a clipped or cleaned title would not match |
| D8 Text cleaning and limits | One new cleaner, `_clean_agent_text(value, limit)`: whitespace (newlines included) collapses to single spaces, every Unicode category C character is dropped (controls including NUL, bidi overrides and isolates, zero-width, private-use, unassigned), and the result is clipped. Used for the stored `text` of approvals and questions (200, `MAX_PERMISSION_TITLE_CHARS`), the toast text, the question shown on the card (`MAX_QUESTION_TEXT_CHARS`, 2000), option titles and descriptions shown on the card (200 and 500), and step messages (`MAX_ERROR_DETAIL_CHARS`, 512). `_clean_step_label` stays as it is for step names. The stored `text` never includes a `[workflow step …]` prefix; the step label travels as its own field | Clip only | `textContent` does not neutralise bidi overrides or zero-width characters, and a NUL breaks the toast hand-off (`notifications.py`) |
| D9 Toasts | `question_request` toasts like `permission_request`: always, while notifications are on (assumption confirmed, 2026-10-12). New `notifications.notify_question(text)` ("Question: <text>", a fixed prefix `[workflow step]` for a step's question, as approval toasts do), dispatched from a `question_request` branch in `web.py`'s `_notify_from_acp`. `acp.html`'s `question_request` handler also fires its in-page browser notification with the same `!replaying && _notifyEnabled` gate as the `permission_request` handler (`maybeNotify`) | Unwatched only | A question stops the turn as an approval does |
| D10 Remote viewers | `question_request` reaches remote sockets unchanged and remote viewers may answer, exactly as with approvals today (`_dispatch` has no remote gate; `_without_rule_fields` touches only `permission_request`). The attention endpoint stays loopback-only (assumption confirmed, 2026-10-12). A remote dashboard's attention poll gets 403 and stops (D19) | Hide questions remotely | A question carries no rule fields and grants nothing an approval does not |
| D11 Step questions | A per-step record `_workflow_step_questions: dict[child_id, {parent, text, created, label}]` beside `_workflow_children` (assumption confirmed, 2026-10-12). Written by a warning notify from a tracked step. Removed by `_workflow_step_resume`, `_workflow_drop_child`, `_workflow_drop_parent` (close) and `_detach` (cleared with the other workflow dicts). The reap does **not** remove it (D25). `attention_snapshot` lists a step question only while its child is still in `_workflow_children[parent]`, as a second guard | Put step questions in `_pending_permission` | A step question has no JSON-RPC id to answer, and the removal paths would try to reply to it |
| D12 Step-message rendering (Q5) | Every `sender: "step"` notify sets the row's `action` to the cleaned message. `warning` and `error` also emit a recorded `step_message {step, severity, message}` to the parent. A `system-notification` user chunk **with `_meta.kiro.replay: true`** whose `status` is `warning` or `error` emits the same `step_message`; a replayed one with `success` or `info` is dropped from the transcript (it showed as a user bubble before). A live `system-notification` chunk (no `replay: true`, never observed) stays an ordinary user chunk and is logged at INFO, so it cannot double a live `step_message` | Render every severity in the transcript | Matches the live path, so a reload shows what the live session showed |
| D13 Waiting and resume (Q6) | A warning notify sets the step `waiting` (`_workflow_set` plus `_workflow_reopen_row(entry, "waiting")`, which also reopens a reaped row, as `node_paused` does), then `action` = message. A step's `_kiro/userInput` question sets the step waiting the same way when stored, so the 6 h waiting bound applies and the 600 s reap cannot cancel it. `_workflow_step_resume(parent, child)` pops the step question and, if the state is waiting, sets running (`status: "working"`, `action: "running"`) unless the step still has a pending question entry in `_pending_permission`. Triggers: a `sender: "parent"` notify whose `sessionId` is the step; a `session/update` with `sessionUpdate == "tool_call"` from a tracked workflow child, handled in `_on_notification` right after the `_workflow_touch(session_id)` call and **before** the early-frame buffer (a step's frames never reach the generic `tool_call` branch while `_reserved > 0`); `node_complete` (through `_workflow_drop_child`) | Resume on any step frame | `_workflow_touch` deliberately preserves state, and a step's housekeeping frames must not clear a question |
| D14 Paused runs (SC-6) | `_on_workflow_run_done` branches first on `status` (case-insensitive) `"paused"`: each child of the run that is **currently in `_workflow_children[parent]`** is set `waiting`, and its row is reopened as waiting only under the guard the other paths use (`not entry["done"] or entry.get("reaped")`), so a finished row is never reopened and an index-only child is never re-tracked. The run's `_workflow_runs` record is refreshed, not popped, and is **not** marked ended. Then it emits the frame and returns. `node_paused` keeps its route; its comment cites the measured shape | Map paused to done | The step is waiting on a person; marking the run ended would stop a resumed run's frames from re-recording it |
| D15 "Paused" header | Derived on the client: "Paused (n)" when at least one row has `status === 'waiting'` and no row is working (the wording the user approved in Q6, and confirmed for the open-step-question case on 2026-10-12). The state function returns `'waiting'` for a row that is not done and `status === 'waiting'`: `subagentState`, `renderCrewPanel` and `renderSubHead` in `acp.html`; `dashSubagentState`, `dashRenderCrewPanel` and the sub-panel head in `index.html`. Every consumer gets a `waiting` branch: the dot class `status-waiting`, the action text, and CSS for `acp-crew-row-waiting` and `acp-subpanel-status-waiting` in `style.css` (the crew-row rules near `.acp-crew-row-working`) | A `workflowPaused` wire field | A wire field would need setting at both `subagents` build sites (`_emit_subagents_frame` and the subscribe rebuild, ~8281), and they could drift |
| D16 Crew-frame fix (SC-7) | `_note_subagent_action` emits with `{parent: entry.get("fan_out_id", _NO_ANCHOR_TOOLCALLID)}` as the map. `_on_subagent_list` keeps `crew_spawn_toolcallids`: it builds only v2 rows, which are tagged from that map. A late action for a row of an older wave goes out under that wave's `toolCallId`, which repaints that wave's own panel; accepted, as the path is unreachable on 2.29.1 | Pass `_active_fan_out_wave` | The wave map would hide v2 rows; the entry's own tag is right for both paths |
| D17 Attention feed (Q3, Q4) | `GET /api/dashboard/attention`, loopback-only, `Cache-Control: no-store`. Built from a loop-side snapshot `_Supervisor.attention_snapshot()` (pure read: kind, session id, text, created, step label, plus each owner's `cwd` and stored title). The worker thread fills a missing title through `_acp_row_title` over `data.get_sessions(cwd, "kiro-cli-v3")`, else "Untitled session" | Fold it into the Overview summary | The summary route never reads the supervisor and polls every 60 s |
| D18 Title storage | The `focus_update` branch also stores `_clean_agent_text(title, MAX_TITLE_CHARS)` on `self.sessions[sid]["title"]` (the emitted `title` frame is unchanged). The worker cleans the `_acp_row_title` fallback the same way | Read disk every poll | A 5 s poll must not read session files for every item, and the title sits beside cleaned text |
| D19 Poller | Its own `setInterval` of 5000 ms, started by an init function called from the dashboard's existing load path (never a top-level statement, so the page harness controls it), independent of `_dashOverviewActive` and visibility. It renders the block (cheap, keyed) and always writes the title prefix. It stops on a 403 like the Overview requests | Reuse the Overview pollers | SC-2 requires polling while a transcript is open and while the tab is hidden |
| D20 Title prefix | `document.title = (n > 0 ? '(' + n + ') ' : '') + _dashBaseTitle`, where `_dashBaseTitle` is read once at load. A future writer of the dashboard title must set `_dashBaseTitle` and call the same function | Prefix on every write | `index.html` writes no title today |
| D21 Row action | A row is a button. If the rail shows the session (`document.querySelector('[data-sid="' + CSS.escape(id) + '"]')`), it opens with `openSessionTranscript({sid, provider, cwd}, item, {cwd, name})`, like `dashOvOpenTile`. Otherwise (a session created in "The agent's own folder" has no rail row) it opens `/acp?sid=` + `encodeURIComponent(id)` in a new tab, the parameter `/acp` already accepts (`ACP_SID`). It subscribes, and sends one `load` if the server no longer holds the session (existing behaviour); it never prompts or answers | Rows without action | Q4; every item must open where it can be answered |
| D23 Card location | The question card is built once in the shared `static/transcript-renderer.js` as `addQuestionRequest`, beside `addPermissionRequest`, with the row class `acp-msg-permission` and `dataset.requestId`, so `findPermissionRequestRow`, `markPermissionResolved` and `hasPendingStepPermissionCard` find it. `markPermissionResolved` also disables `textarea` elements. The templates only dispatch the frame to it | One copy per template | The permission card already lives there; two copies drift and would miss the shared resolved and Stop logic |
| D24 Prompt silence watchdog (user decision 2026-10-12: "I would remove the silence watchdog") | Remove the silence bound for prompts and slash commands (`commands_execute` shares the same wait):  `PROMPT_SILENCE_SECONDS`, the `last_activity` and `queued_behind` deadline extensions in `_await_inactivity`, the silence branch of its `AgentTimeout` message, the `acp_prompt_silence_seconds` setting (config field, settings route, settings menu label) and its `apply_config` rebinding. **Keep** the 24 h absolute ceiling (`PROMPT_ABSOLUTE_MAX_SECONDS`) and the tick, so a turn cannot run forever: the ceiling's own comment records why (an un-closable, un-sweepable session otherwise). Rename `_await_inactivity` to `_await_prompt`, the `_INACTIVITY` sentinel to `_PROMPT_WAIT`, and rewrite both docstrings. `_stamp_activity` and the `last_activity` field lose their only reader and are deleted with their tests (governance: unused code is deleted); the workflow router's and `_on_notification`'s `_stamp_activity` calls go too | Extend the deadline while a question or approval is open | An open question or approval can wait for hours; a turn that truly hangs now waits for Stop or the 24 h ceiling (R11) |
| D25 Workflow reap (user decision 2026-10-12: "no cancel, we also remove") | No automatic path cancels a step's request while the step is tracked. (1) `_workflow_reap_parent` no longer calls `_cancel_step_permissions`, and it **skips** (does not mark reaped) a child that has an open entry in `_pending_permission` (`workflow_child`) or an open step question: a step waiting on a person is not "no longer reporting", so its row, the crew header and the attention list stay truthful. (2) `_evict_crew_children` and `_evict_finished_subagents` skip rows whose step has an open entry or step question, so a turn boundary cannot drop the child and cancel through `_workflow_drop_child`. `_cancel_step_permissions` loses its `notify` parameter (its only caller was the reap) and its docstring is rewritten. Entries still go on answer, Stop, close, detach, the turn-end sweep of untracked steps, and `_workflow_drop_child` when the step really finished (`node_complete`, `run_complete`). Answers reach the step through the stored `kiro_id`, whatever the reap did | Apply the waiting rule to approvals only | The user chose to stop cancelling outright; skipping the reap for those rows is what keeps eviction from cancelling them a turn later |
| D26 Stop's local fallback (user decision 2026-10-12, "Ok with reco") | `_Supervisor.cancel` keeps writing `session/cancel` and also records the Stop on the session record (`meta["stop_requested_at"] = time.monotonic()`), cleared when a new prompt or command starts. `_await_prompt` (the renamed wait, shared by prompts and slash commands) ends the wait locally when the future has not resolved `CANCEL_GRACE_SECONDS` (3 s) after that Stop: it logs a WARNING and raises `AgentTimeout("The agent did not answer Stop; the turn was ended here. Its tool processes may still be running.")`, the same exit the 24 h ceiling takes. Property: the fallback fires within `CANCEL_GRACE_SECONDS` + 1 s of the Stop, never a whole `PROMPT_TICK_SECONDS` later, so the wait must wake on the Stop (for example a per-session event the wait races with the future) rather than on its next tick. `cancel()` itself still ends nothing: the raise inside the wait lets `_handle_prompt`'s `finally` emit the single turn boundary, which keeps the race its docstring warns about closed | Leave Stop as a request only | Without the watchdog, a live agent that ignores the cancel would hold the session for 24 h with no exit but a restart |
| D22 Plan of review | One `/qreview` cycle per `/qplan` run (user's invocations, 2026-10-12, twice), against the skill's default of up to 3 | 3 cycles | User override, recorded per `## Continuous Improvement` |

## 4) External Dependencies & Costs

### Required external changes

| Category | Change needed | Owner | Status |
|---|---|---|---|
| Rollout / cutover | A PowerAtlas restart to load the Python changes, which ends every live ACP session (`AGENTS.md`) | Agent | Granted by the user for this task, 2026-10-12 |
| Config migration | `acp_prompt_silence_seconds` leaves `config.toml`: listed in `config.py`'s `_LEGACY_KEYS`, so an existing file loads and the key is dropped at the next save (Phase 6) | Agent | Pending |

### Cost impact

None. kiro-cli usage is unlimited, and no service or dependency is added.

## 5) Implementation Phases

No phase is annotated `[P:N]`. Phases 2-5 all edit `acp.py` and `tests/test_web.py`, and Phase 1 decides Phases 3 and 4.

Unit tests run in every phase:
- `.venv-PowerAtlas/Scripts/python -m pytest tests/test_web.py --timeout=300`
- `node tests/acp_page.test.mjs` whenever a template changes.

Live checks of the `[QA]` phases need a PowerAtlas restart. The user granted restarts for this task on 2026-10-12, so `/qdev` may restart PowerAtlas (`POST /api/restart`, then wait for a fresh "Server ready" line in `orchestrator.log`) when a phase's live check needs the new code. Restarting ends every live ACP session, so batch the live checks of a phase into one restart.

### Phase 1: Pre-flight probes
**Goal**: Settle the three execution-contingent premises before the code that depends on them.

**File scope**: none in the repo (scratch only); results recorded in this plan's §9.

**Covers**: SC-3, SC-6

**Probe driver contract.**
- A scratch script, never committed, that subclasses `tools/acp_permission_probe.Probe`.
- It spawns a second `kiro-cli acp --agent-engine v3` and never touches the running PowerAtlas. The token is answered by `_fulfill_token` and never printed.
- `initialize` declares `_meta.kiro.userInput: true`.
- `session/new` sends `_meta.kiro.modeId` and `_meta.kiro.settings.workflows/goal.enabled` as the run needs.
- It answers `_kiro/userInput` with a configurable reply and logs every request and notification, with session ids replaced by roles.
- Afterwards it deletes the sessions it created, by recorded id, after checking that `createdAt` in `session.json` falls inside the task window (`~/.kiro/sessions/<hash>/sess_<id>/`).

**Probes.**
1. **Abandon reply.** Run a quick-spec prompt that asks clarifying questions, and answer the first `_kiro/userInput` with `{"action": "cancelled"}`. Expect kiro-cli to move to the next phase, with no hang and no error. Also run one with an empty `answer`. Record where kiro-cli places its "Skip remaining questions" option in the list.
2. **A step's `_kiro/userInput`.** Run a one-step workflow whose step is told to use its `user_input` tool. Record whether a `_kiro/userInput` arrives and which `sessionId` it carries.
3. **Paused-run resume.** Run a one-step workflow whose step sends a warning `send_message` and then ends its turn; repeat up to 4 times until `run_complete` `status: "paused"` appears. Then send the answer as a parent prompt. Record the frames that follow: whether the same step session resumes (its `sessionId` on `tool_call` frames, a parent-to-step notify, `node_complete`), or whether a new `node_start` and new child appear.

4. **Rail verdict for a question.** During probe 1, before answering, check whether kiro-cli wrote a `pending_interaction` record (or another record the transcript classifier reads as waiting) to the session's `messages.jsonl`. The rail's Waiting verdict for a session with an open question depends on it.

5. **Stop during a slash command and during a question.** Start a long `_kiro.dev/commands/execute` (for example `/compact` on a session with history) and send `session/cancel`; record whether the command's request answers. During probe 1, also send `session/cancel` while a `_kiro/userInput` is open and record what kiro-cli does.

**Gate.**
- If probe 1 hangs or errors, change D4 before Phase 3 (try `{"action": "skipped"}`, then a JSON-RPC error, which the bundle's catch also treats as next-phase) and record the change in §9.
- If probe 2 shows a step `sessionId`, Phase 3 routes it through `_workflow_step_parent`, as already planned. If no request arrives, keep the routing and mark it unverified.
- Probe 4 is recorded for the knowledge base only: Phase 5's waiting override (below) makes the rail read Waiting whatever is on disk.
- If probe 5 shows a cancel does not end a command, record it in R11.
- If probe 1 changes D4's reply, change the malformed `_kiro/userInput` reply in Phase 3 step 3 to match.
- If probe 3 shows a new child on resume, Phase 4 also ends the old waiting row when a new `node_start` arrives for the same `(workflowId, nodeId)`. If probe 3 never pauses in 4 tries, keep D14 and record the resume as unmeasured.

**Exit criteria**:
- [x] Probe 1 result (both replies) recorded in §9 with the kiro-cli version
- [x] Probe 2 result recorded in §9
- [x] Probe 3 result, or "no pause in 4 runs", recorded in §9
- [x] Probes 4 and 5 results recorded in §9
- [x] Every probe session deleted after its `createdAt` check; `git status` shows no change outside this plan file

Implementation (2026-10-12, code: none)
All five probes ran against a second `kiro-cli acp --agent-engine v3` process (kiro-cli-chat 2.29.1) started by a scratch driver, an extended copy of the exploration's `wfprobe.py`; the original was not edited and the running PowerAtlas was not touched. Run files are `runA.json` to `runE.json` in the session scratchpad's `phase1` folder; the `texts` fields in runD and runE contain real step session ids, so they are never copied into the repo. Probe 1: both the `{"action":"cancelled"}` reply and the empty answer moved kiro-cli on with `end_turn`, so D4 is unchanged. Probe 2: no `_kiro/userInput` arrived from a step in 2 runs; steps list no `user_input` tool, so the routing is kept and marked unverified. Probe 3: the run paused on the first try and resumed in the same child, with `run_start` and `node_start` re-sent for the tracked run and child; after a `success` notify the step ended paused again (`reason: "Awaiting next user message on step session."`). Probe 4: an open question leaves a `pending_interaction` record that the classifier reads as Waiting. Probe 5: `_kiro.dev/commands/execute` was refused with `-32603` (`PersistenceClassification`), so a cancel during a command was not measured; a cancel with a question open ended the turn in 0.05 s, and a late `cancelled` reply was harmless. Cleanup matched sessions by recorded id plus a `createdAt` inside the window: 10 found, 10 deleted, 0 on re-run.

Orchestrator notes: the git-status criterion was read as "no change caused by the probes". `plans/done/261009-2109_DASHBOARD_OVERVIEW_REHAUL_QUOTA_RESUME_AND_GIT_STATUS.md` was modified by another session at 20:04, before the probe window opened at 20:06, and is left untouched. Per-phase review deferred to Step 9: results-only phase, about 15 lines of prose in one file, no executable code. QA: SKIP (no `[QA]` annotation, no runtime surface).

### Phase 2: Crew-frame filter fix [QA]
**Goal**: `_note_subagent_action` keeps a wave-tagged row in its frame.

**File scope**: `src/power_atlas/acp.py`, `tests/test_web.py`.

**Covers**: SC-7

Change, in `_note_subagent_action` (a sketch, not the deliverable):

```python
entry["action"] = title
_emit_subagents_frame(meta["parent"], self.crews,
                      {meta["parent"]: entry.get("fan_out_id", _NO_ANCHOR_TOOLCALLID)})
```

> **Rejected:** passing `self._active_fan_out_wave` — it hides v2 rows tagged from `crew_spawn_toolcallids`. **Use instead:** the entry's own `fan_out_id`.

Test, in `TestAcpSubagentActivity`:
1. Seed `_active_fan_out_wave[parent] = "w1"` and a crew entry tagged `fan_out_id: "w1"`, registered in `subagent_sessions`.
2. Capture `_registry.broadcast`.
3. Call `_note_subagent_action(child, "Reading file")`.
4. Assert the child is in the payload's `subagents`, and that `toolCallId == "w1"`.
5. A second test keeps the v2 case: an entry tagged from `crew_spawn_toolcallids` is still emitted.

**Exit criteria**:
- [ ] The new test fails on the unfixed code (run it before the fix and record the failure in §9)
- [ ] Both tests pass after the fix; the full `tests/test_web.py` suite passes

### Phase 3: Agent questions through `userInput` [QA]
**Goal**: kiro-cli's questions reach a card with options, descriptions and free text, are answered in the native shape, and are abandoned safely.

**File scope**: `src/power_atlas/acp.py`, `src/power_atlas/web.py`, `src/power_atlas/notifications.py`, `src/power_atlas/static/transcript-renderer.js`, `src/power_atlas/templates/acp.html`, `src/power_atlas/templates/index.html`, `src/power_atlas/static/style.css`, `tests/test_web.py`, `tests/acp_page.test.mjs`.

**Covers**: SC-3, SC-9

Server (`acp.py`):
1. `_kiro_client_capabilities()` always sets `kiro["userInput"] = True`, so `_meta.kiro` is no longer empty off Windows. Update its docstring and the three tests that assert its exact value (search `tests/test_web.py` for `_kiro_client_capabilities()`; one is parametrized).
2. `_on_agent_request` routes `"_kiro/userInput"` to a new `_on_user_input_request(msg)`.
3. `_on_user_input_request` mirrors `_on_permission_request`:
   - The same id validation, and the same owner and step routing through `_workflow_step_parent`.
   - The question is required and options are optional. An empty option list is valid: that is a free-form question.
   - At most 20 options are kept (D7). Each keeps its raw `title` in the entry and is projected for the frame as `{title, description, recommended: bool}`, cleaned and clipped per D8.
   - A malformed request with a usable id is answered `{"action": "cancelled"}`; one without a usable id goes to `_refuse`.
   - It stores `{session_id, kind: "question", kiro_id, text: _clean_agent_text(question, 200), answer_titles: [<raw titles>], created: time.time()}`, plus `workflow_child` for a step.
   - It emits the recorded `question_request`. A step's question carries `workflowStep: true` and `workflowStepLabel` (the `_workflow_step_label` value) as separate fields; the question text is never prefixed.
   - It calls `_notify("question_request", …)`. For a step it sets the step waiting (D13) instead of only touching it.
4. `_on_permission_request` adds `kind: "approval"`, `text: _clean_agent_text(<unprefixed title>, 200)` and `created` to its entry. The frame's title is unchanged.
5. `_answer_pending_cancelled(entry, why)` per D4. Every non-malformed call site listed in §1 switches to it. The denial-notice loops in `_pop_pending_permissions` and `_cancel_step_permissions` skip question entries.
6. D25: delete the `_cancel_step_permissions` call in `_workflow_reap_parent`, make the reap skip children with an open entry or step question, make `_evict_crew_children` and `_evict_finished_subagents` skip their rows, and remove the `notify` parameter (with its notice branch) from `_cancel_step_permissions`.
7. Add `"question_request"` to `SERVER_TYPES` and `"question_response"` to `CLIENT_TYPES`, then add `_handle_question_response` per D6 and its dispatch beside `permission_response`. A step question's answer clears nothing in the workflow state; the step's next frames do that (Phase 4).

`notifications.py` and `web.py`: add `notify_question(label, text)` per D9 (the label names the session, as `notify_permission_needed(label, tool_title)` does) and a `question_request` branch in `_notify_from_acp` beside the `permission_request` one.

Pages. Build the card once in `static/transcript-renderer.js` (D23), with `createElement` and `textContent` only; `acp.html` and `index.html` dispatch `question_request` to it:
1. A `question_request` card shows:
   - the step label as its own element when `workflowStepLabel` is set;
   - the question;
   - one button per option (title, description under it, a "Recommended" badge);
   - a textarea with a Send button, which stays disabled while the text is empty.
2. Clicking an option sends `question_response {requestId, optionIndex}`; Send sends `{requestId, answer}`. Either disables the card at once.
3. The card is disabled (buttons and textarea) and marked answered on `permission_resolved` with the same `requestId`, through `markPermissionResolved`.
4. `acp.html` fires its in-page notification per D9.
5. New CSS goes in `style.css`. Any class that sets `display` on an element toggled with `hidden` also gets `[hidden] { display: none; }`.
6. `addQuestionRequest` returns the row, like `addPermissionRequest`. The template sets `row.dataset.workflowStep = '1'` when `payload.workflowStep === true` and then calls `refreshComposerControls()` (`acp.html`) or `dashRefreshComposerControls()` (`index.html`), exactly as its `permission_request` handler does, so Stop stays usable for a step question (`hasPendingStepPermissionCard` finds the row).
7. The question-card page tests run in the sandboxes that load the whole renderer. Add `addQuestionRequest` to the picker sandbox's `rendererFunctionSource` list only if that sandbox's Stop check needs it.

Tests:
- Python:
  - The capability is present.
  - A `_kiro/userInput` request stores a question entry and emits the frame.
  - A step's request is routed to the parent.
  - A malformed request gets `cancelled`.
  - Answering by `optionIndex` writes the stored raw title, even when the displayed title was cleaned or clipped; answering by free text writes it stripped; both shapes are `{"action": "answered", "answer": …}`.
  - An empty answer, an out-of-range or boolean `optionIndex`, and both or neither key are refused.
  - Two `question_response` frames for one id produce one reply and one `unknown_request`.
  - More than 20 options are cut to 20.
  - U+202E and U+0000 in an approval title and in a question never reach the stored `text`, the toast or the card.
  - An entry whose `kind` is missing or unknown is abandoned with the permission `cancelled` outcome.
  - `permission_response` on a question entry is refused with `unknown_request` and does not raise.
  - Stop, close and the turn-end sweep answer a question `{"action": "cancelled"}` and an approval with the permission outcome; a step's question gets no denial notice. Detach broadcasts `permission_resolved` for both kinds and writes nothing.
  - A step's question sets the step waiting.
  - A step question card keeps Stop enabled with no turn active, on both pages.
  - `node_complete` for a step with an open question abandons it with `{"action": "cancelled"}`.
  - A stale step with an open approval or question is not reaped; the parent's next turn start and turn end leave the entry pending; an answer afterwards still reaches the step.
  - Replace or split the existing reap-cancels tests: `…cancelled_when_its_step_is_reaped_not_before` and `test_workflow_reap_gives_one_denial_notice…` are replaced by the tests above; the test that covers both the reap and `close_session` keeps its close half; re-check the test asserting that a request refresh keeps the step from being reaped early (search `TestAcpWorkflowNotifications` for `reap`).
  - `sessions_with_pending_permission` includes a question's owner.
  - The type guard covers the new types.
  - The toast policy test covers `question_request`, with `notify_question` added to `TestNotificationPolicy._fire`'s stub list.
- Page (`tests/acp_page.test.mjs`), on both pages:
  - The card renders options, descriptions, the badge and free text.
  - Each answer path sends the right frame.
  - `permission_resolved` disables the card.
  - Agent text never reaches `innerHTML`.

**Exit criteria**:
- [ ] `initialize` carries `_meta.kiro.userInput: true` (unit test)
- [ ] `sessions_with_pending_permission` docstring says it covers questions too
- [ ] Every Python and page test listed above passes; the full suites pass
- [ ] `grep -n "_answer_permission_cancelled(" src/power_atlas/acp.py` shows only the definition, the malformed-request refusal in `_on_permission_request` and the call inside `_answer_pending_cancelled` (the two-line `coro = …` form in `_cancel_step_permissions` included)
- [ ] Live (after a granted restart, else carried to Phase 7): a quick-spec question answered from a card option, and a free-form question answered with free text, on `/acp`

### Phase 4: Step messages, step waiting state and paused runs [QA]
**Goal**: A step's messages show on its row and, for warnings and errors, in the transcript. A step question marks the step waiting until it resumes. A paused run reads Paused, never failed.

**File scope**: `src/power_atlas/acp.py`, `src/power_atlas/static/transcript-renderer.js` (if the step-message row is built there beside the other shared rows), `src/power_atlas/templates/acp.html`, `src/power_atlas/templates/index.html`, `src/power_atlas/static/style.css`, `tests/test_web.py`, `tests/acp_page.test.mjs`.

**Covers**: SC-4, SC-5, SC-6

Server (`acp.py`):
1. In `_on_notification`, after the `_workflow_touch` call and before the early-frame buffer, add `if method == "_kiro/session/notify": self._on_session_notify(params); return`. Log the notify at INFO (bounded, as the fall-through did), so the evidence path stays.
2. `_on_session_notify(params)`:
   - **`sender == "step"`.** The step is `callerSessionId`, and must be tracked: `_workflow_step_parent(step) == params.sessionId`.
     - The cleaned message (`_clean_agent_text(message, 512)`) becomes the row's `action` if the row is open, and a `subagents` frame is emitted with `_active_fan_out_wave`.
     - For `warning` and `error`, emit the recorded `step_message {step: _workflow_step_label(...), severity, message}` to the parent.
     - For `warning`, also write `_workflow_step_questions[step]`, call `_workflow_set(parent, step, "waiting")` and reopen the row as waiting, keeping `action` = the message.
   - **`sender == "parent"`, with `sessionId` a tracked step.** Call `_workflow_step_resume(parent, step)`.
   - A warning on a reaped row reopens it as waiting (D13).
   - **Anything else.** Log at INFO and change nothing (unknown senders, untracked steps).
3. `_workflow_step_resume(parent, child)` per D13, called in `_on_notification` right after `self._workflow_touch(session_id)` when `kind == "tool_call"` and the `sessionId` is a tracked workflow child (before the early-frame buffer). Remove step questions in every path D11 lists.
4. In the `user_message_chunk` branch, a chunk whose `_meta.kiro.notification.kind == "system-notification"` and `_meta.kiro.replay is True` is handled per D12: emit `step_message` for `warning` or `error` (step name from `agentName` or `nodeName` through `_clean_step_label`, message through `_clean_agent_text(text, MAX_ERROR_DETAIL_CHARS)`), and nothing else. It never touches workflow state. `workflow-progress` chunks stay as they are.
5. `_on_workflow_run_done` branches on paused per D14, before any drop, finish, pop or mark-ended.
6. Fix the "Shape not measured" comments on `node_paused` and `_kiro/workflow/paused`. Update the `_workflow_outcome` docstring to note that `paused` never reaches it from `run_complete`.
7. Add `"step_message"` to `SERVER_TYPES`.

Pages (both mirrors):
1. `step_message` renders as a system row (`.acp-step-message`, with a severity modifier class) labelled with the step name and the message. Text only.
2. The state functions, every consumer, the headers and the CSS per D15.

Tests:
- Python:
  - A step's warning notify sets the row action, the waiting state, the step question and one recorded `step_message`.
  - `success` and `info` set the action only.
  - `error` emits `step_message` without setting waiting.
  - A notify from an untracked step changes nothing.
  - A parent-to-step notify resumes the step.
  - A step `tool_call` resumes it, including while `_reserved > 0`.
  - A warning on a reaped row reopens it as waiting.
  - Close and detach remove the step question; a step with an open step question is not reaped.
  - `node_complete` drops the question.
  - Housekeeping frames (`agent_message_chunk`) do not resume it.
  - A step notify is not swallowed by the early-frame buffer while `_reserved > 0`.
  - A replayed `system-notification` warning chunk yields one `step_message` and no user chunk; a replayed success chunk yields neither; a live one (no `replay: true`) stays a user chunk.
  - `run_complete` `status: "paused"` keeps the children waiting and tracked, keeps the run record, does not mark it ended, and leaves no row failed.
  - A later `node_complete` `completed` still finishes the row.
  - `node_paused` with the measured shape sets waiting.
- Page (both pages):
  - A `step_message` row renders.
  - A waiting row gets `status-waiting`.
  - The header reads "Paused (n)" with one waiting row and none working, and "Orchestrating" with one working row.

**Exit criteria**:
- [ ] Every Python and page test listed above passes; the full suites pass
- [ ] `_workflow_outcome` docstring notes that `run_complete` `paused` is handled before it
- [ ] The `_kiro/session/notify` INFO fall-through no longer fires for handled notifies (unit test on the log level, beside `TestAcpUnrecognizedNotificationLogLevel`)
- [ ] Phase 1 probe 3's outcome applied (old-row handling added if the resume spawned a new child, or "unmeasured" noted in §9)
- [ ] Live (after a granted restart, else carried to Phase 7): a workflow step question shows on the row, in the transcript and as Waiting; after the answer the row runs again and finishes; a reload or `session/load` shows one step-message row, not two

### Phase 5: Attention feed, "Needs attention" rows and title count [QA]
**Goal**: The dashboard lists every pending approval, question and step question, and counts them in the page title.

**File scope**: `src/power_atlas/acp.py`, `src/power_atlas/web.py`, `src/power_atlas/templates/index.html`, `src/power_atlas/static/style.css`, `tests/test_web.py`, `tests/acp_page.test.mjs`.

**Covers**: SC-1, SC-2, SC-5

Server:
1. `acp.py`: the `focus_update` branch stores the title (D18).
2. `acp.py`: `_Supervisor.attention_snapshot()` is a pure, loop-only read. It returns a list of `{kind: "approval"|"question"|"step_question", sessionId, text, created, step, cwd, title}`:
   - from `_pending_permission`, skipping malformed entries;
   - from `_workflow_step_questions` whose parent is a registered session.
   It is sorted by `created`, oldest first. `step` is the step label for routed entries and step questions, else `""`.
3. `web.py`: `_DASHBOARD_ATTENTION_PATH = "/api/dashboard/attention"`, which is not added to `_REMOTE_ALLOWED_PATHS`.
   - The route takes the snapshot on the loop, with the `acp is None` guard, and calls `_attention_payload(items)` in a thread.
   - The worker fills the title (D17) and `workspace` (the cwd's base name), and sets `provider: "kiro-cli-v3"`.
   - It returns `{"items": [...], "count": n}`.

`index.html`:
1. A new block `<div class="dash-ov-block" id="dashOvAskBlock" hidden></div>`, first inside `#dashOvAttentionBody`.
2. `dashOvRenderAsk(items)` is keyed by JSON and rebuilt only on change, like `dashOvRenderInterrupted`. Its sub-heading is "Waiting for you". Each row is a button showing:
   - a kind label ("Approval", "Question", "Step question");
   - the session title and workspace;
   - the text, with "<step> · " in front when `step` is set;
   - the age, from `created`, refreshed with the block's existing countdown pattern or on each poll.
   Clicking a row runs D21.
3. The poller and title prefix per D19 and D20.
4. Harness: add `dashOvAskBlock` to the `byId` pre-registration in `tests/acp_page.test.mjs`. The dashboard harness slices named regions (`DASH_OVERVIEW_NAMES`), so put `dashOvRenderAsk`, the poller and the title function in the Overview region and add their names to that list. Check that the harness's `document` stand-in has a writable `title`; add one if not.
5. Waiting override (SC-5, parallel steps included). `acp.py`: `_Supervisor.sessions_awaiting_person()`, a loop-only read returning the owners of `_pending_permission` entries plus the parents of open step questions. `web.py`: snapshot it on the loop beside `_workflow_states`, and after `_resolved_session_status` at each status call site (the listing routes, the live Overview route and the dashboard sessions route; search `_resolved_session_status(`), a held session in that set reads `waiting`, ranked above `working`. `_workflow_states` excludes the same set it does today, now taken from `sessions_awaiting_person()`. Tests: a parent with an open step question and a running sibling step reads Waiting; a session with a pending question reads Waiting while mid-turn.

Tests:
- Python (`TestOverviewLive` neighbourhood):
  - The route is loopback-only (a remote peer gets 403; loopback without the `pa_local` cookie gets 403).
  - A step question whose child is no longer tracked is not listed.
  - It returns approval, question and step-question items with title, workspace and age fields.
  - A stored `focus_update` title is used; the `_acp_row_title` fallback is used when none is stored.
  - Malformed entries are skipped.
  - An answered item disappears on the next call.
  - The `isolated_config` seams stay in force.
- Page:
  - Rows render in order with `textContent`.
  - A click opens the session with `openSessionTranscript` when the rail has its row, and opens `/acp?sid=<encoded id>` in a new tab when it does not (stub `CSS.escape` and `window.open` in the dashboard sandbox if it lacks them).
  - The block hides when the list is empty.
  - The title gets `(N) ` and loses it at zero.
  - The poller keeps running while a transcript is open and while `visibilityState` is hidden.
  - `/acp`'s title code is untouched.

**Exit criteria**:
- [ ] Every Python and page test listed above passes; the full suites pass
- [ ] `_REMOTE_ALLOWED_PATHS` unchanged (`git diff` shows no edit to it)
- [ ] Live (after a granted restart, else carried to Phase 7): a Manual-mode approval and a question appear in "Needs attention" with the title count, open their session on click, and clear within one poll of the answer

### Phase 6: Remove the prompt silence watchdog [QA]
**Goal**: A turn is no longer cancelled for silence; only Stop (with its local fallback) or the 24 h ceiling ends it.

**File scope**: `src/power_atlas/acp.py`, `src/power_atlas/config.py`, `src/power_atlas/web.py`, `src/power_atlas/templates/index.html`, `README.md`, `tests/test_web.py`.

**Covers**: SC-9

Changes (D24, D26):
1. `acp.py`:
   - Delete `PROMPT_SILENCE_SECONDS` and its comment block. Rewrite the `PROMPT_TICK_SECONDS` comment: the tick now only re-checks the ceiling and the session record.
   - Rename `_await_inactivity` to `_await_prompt` and update its callers.
   - Keep the shielded wait, the `meta is None` fall-through, the hard stop and its `CANCEL_GRACE_SECONDS` grace. Remove the `deadline` seeding, the `last_activity` extension, the `queued_behind` extension (the `queued_behind` field and its `queue_status` frame stay) and the silence wording of the `AgentTimeout`.
   - Rename `_INACTIVITY` to `_PROMPT_WAIT` and reword its comment and `_request`'s docstring. Delete `_stamp_activity`, its calls and the `last_activity` field.
   - Sweep the comments that describe the silence timeout (search `silence` in `acp.py`: the workflow router's activity stamp, `_on_notification`'s stamp, the turn-end sweep comment, the `prompt_silence=` log format in `apply_config`, and the others) so none describes a bound that no longer exists.
   - Delete the `apply_config` rebinding and its `global` name. Reword the `_lock_holder_v3` staleness comment, which cites `PROMPT_SILENCE_SECONDS` for its 1800 s figure, so it stands on its own measured reasoning.
2. `config.py`: delete the `acp_prompt_silence_seconds` field and add the name to `_LEGACY_KEYS`.
3. `web.py`: delete the key from the settings payload, the type map, the range map and the restart-needed list (search `acp_prompt_silence_seconds`).
4. `index.html`: delete the settings label `'ACP: turn silence ceiling (seconds)'` and any control bound to the key.
5. D26 in `acp.py`: record the Stop in `cancel()`, clear it at the start of `_handle_prompt` and of a command's dispatch, and make `_await_prompt` end the wait per D26. Rewrite `cancel()`'s docstring to say what the fallback does and why the raise sits in the wait.
6. `README.md`: delete the whole `acp_prompt_silence_seconds` entry (its line and its continuation comment lines) from the config example. Rewrite the bullet "**A turn is bounded by silence, not by duration.**" so that a turn runs until it ends, Stop or the 24 h ceiling, and say that Stop ends the turn within a few seconds even if the agent does not answer (its tool processes may keep running until PowerAtlas exits). Reword the sentence ending "until the silence timeout above cancels the turn" in the permission section: such a turn now waits until answered or stopped.

Tests:
- **Keep the `acp_fast` fixture.** It also rebinds the TTL, sweep, watchdog, ceiling and grace constants for about 17 tests. Remove only `"PROMPT_SILENCE_SECONDS"` from its tuple and the `= 0.08` line, and reword its docstring's "tick << silence << absolute max" ratio.
- Delete only the tests whose premise is silence: `test_only_unhandled_update_kinds_still_count_as_life`, `test_silence_past_the_window_cancels_agent_side_and_raises` and `test_a_prompt_on_a_long_idle_session_gets_a_full_window`, plus the `last_activity` / `_stamp_activity` tests (search `last_activity` and `_stamp_activity` in `tests/test_web.py`).
- Re-point `test_a_cancel_honoured_inside_the_grace_returns_the_real_answer` at a small `PROMPT_ABSOLUTE_MAX_SECONDS`, so the grace keeps its coverage. In `test_a_turn_past_the_absolute_ceiling…`, delete the `PROMPT_SILENCE_SECONDS = 30.0` line.
- Remove the key from the settings key lists, the config default and clamping tests, and the settings-route range tests (search `acp_prompt_silence_seconds`). Rename the `_INACTIVITY` assertion to `_PROMPT_WAIT`. Leave the module docstring's incident record at the top of `tests/test_web.py`, which names the key historically.
- Add two tests:
  - A prompt with no notification for 0.3 s at a 0.01 s tick is not cancelled and returns its result when the future resolves. Run it before the change and record that it fails (the old `acp_fast` silence window is 0.08 s).
  - The absolute ceiling path is already covered by the ceiling test above; extend it to assert that no `session/cancel` is written before the ceiling.
- D26 tests: a fake agent that ignores `session/cancel` has its turn ended within `CANCEL_GRACE_SECONDS` + 1 s of a Stop, with one turn boundary and the `AgentTimeout` message; an agent that honours the cancel returns its real answer and the fallback never fires; a Stop recorded in one turn does not end the next turn early; the same fallback ends a slash command.
  - A `config.toml` holding `acp_prompt_silence_seconds = 7201` loads without error, and the key is gone after a save.

**Exit criteria**:
- [ ] `grep -rn "PROMPT_SILENCE_SECONDS\|acp_prompt_silence_seconds\|_await_inactivity\|_INACTIVITY\b\|_stamp_activity\|last_activity" src tests README.md` returns only the `_LEGACY_KEYS` entry, the legacy-config test, the test module's incident docstring and `overview.py`'s unrelated `last_activity` output field
- [ ] `grep -n -i "silence" src/power_atlas/acp.py` hits only text unrelated to a turn bound (each remaining hit named in §9)
- [ ] The new tests pass (the no-cancel test failed before the change, and the D26 ignore-the-cancel test failed before the change); the full suites pass
- [ ] `README.md` updated: the config example line removed, the "bounded by silence" bullet and the permission-section sentence rewritten
- [ ] Live (batched into Phase 7's restart): the settings menu no longer lists the silence setting

### Phase 7: Documentation, roadmap and live QA
**Goal**: Record the protocol facts and the user-visible surface, then verify everything live.

**File scope**: `README.md`, `docs/KNOWLEDGE.md`, `plans/ROADMAP.md`, `plans/tests/260701_POWERATLAS.md`, `AGENTS.md` (only if the Terminology proposal is saved), this plan.

**Covers**: SC-8

1. Write the documentation updates in §8.
2. Restart PowerAtlas (granted for this task, 2026-10-12) via `POST /api/restart` and wait for a fresh "Server ready" line.
3. Run the live checks carried from Phases 3-6, including a session left with an open approval for longer than 30 minutes that is still waiting with its card answerable (start it first, run the other checks meanwhile), plus a Manual-mode approval (`POST /api/acp-permissions {"mode":"manual"}`, restored and verified afterwards). Follow `AGENTS.md` § Verification Setup: sign in through `/local-auth?code=<code>`, use one script per call, and close and delete the test sessions after the `createdAt` check.

**Exit criteria**:
- [ ] `README.md` updated: the "Needs attention" row lists approvals, questions and step questions and the `(N)` title count; the `/acp` feature list mentions question cards with free-text answers
- [ ] `docs/KNOWLEDGE.md` updated: a `user_input` / `_kiro/userInput` section (request shape, the capability, reply rules, Phase 1 results); `_kiro/session/notify` shapes for both senders; the paused run (`node_paused`, `paused`, `run_complete` `status: "paused"`) and the probe-3 resume result; the "`_workflow_outcome` … revise when a real failure is captured" note updated for `paused`
- [ ] `plans/tests/260701_POWERATLAS.md` gains the `GET /api/dashboard/attention` section after the Overview summary section
- [ ] `plans/ROADMAP.md` updated (Low item on resuming after `node_paused` and the Layer 3/4 handled-frames sentence included): the rows "`_note_subagent_action` filters its crew frame with the wrong map", "Approvals and questions in 'Needs attention'" and "`_kiro/session/notify` has no handler" removed from the ranking and body (anchor by those titles), and the ranking renumbered
- [ ] `pytest tests/test_web.py --timeout=300` and `node tests/acp_page.test.mjs` pass
- [ ] Every live check of SC-8 passes in a real browser, with the evidence (tool calls, screenshots) cited in §9; or a check is recorded BLOCKED with its reason

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| R1 The abandon reply for `_kiro/userInput` behaves differently from the bundle reading | A stopped question hangs the turn | Phase 1 probe 1 before Phase 3; never a JSON-RPC error until measured (permission errors approve, `tools/acp_permission_probe.py` K3) |
| R2 A paused run resumes through a new child | The old row stays waiting for up to 6 h | Phase 1 probe 3; Phase 4 closes the old row on a new `node_start` for the same node if so |
| R3 A step's `_kiro/userInput` is unverified | A step question card could be refused | Route through `_workflow_step_parent` like approvals; Phase 1 probe 2 |
| R4 Double rendering after `session/load` | Two rows for one step message | Replayed chunks render through the same `step_message` path; Phase 4 live check |
| R5 A store change misses a removal path | A question is answered with the permission shape (read as next-phase anyway) or left pending | The `grep` exit criterion in Phase 3; tests for Stop, close, the sweep and detach on both kinds |
| R6 Agent-authored text reaches the Overview and transcripts | Markup or spoofed prefixes | Clean and clip on the server, `textContent` only, `innerHTML` banned by the page harness; the endpoint stays loopback-only |
| R7 History eviction | A pending card has no frame after a reconnect past 2000 events or 2 MiB | Pre-existing; the attention list reads the store, so the item still shows. Accepted |
| R8 Background-tab throttling | The title count can lag about a minute in a hidden tab | Accepted browser behaviour |
| R10 Declaring `userInput` changes behaviour in every permission mode | In Yolo, the old permission-card fallback for a question could be approved without asking; now every question waits for a person | Intended by D2 (Q2); README and KNOWLEDGE state it in Phase 7 |
| R11 No silence bound on a turn or slash command (D24) | A turn parked on an unanswered approval or question holds its session slot until answered or stopped, and a remote device can do that on purpose (bounded by `MAX_SESSIONS`). A live agent that hangs is ended only by Stop or the 24 h ceiling | Accepted by the user, 2026-10-12. Stop ends the turn locally within `CANCEL_GRACE_SECONDS` + 1 s if kiro-cli does not answer the cancel (D26); any viewer's Stop releases a parked turn; the agent's tool processes may outlive a locally ended turn until PowerAtlas exits, as with the ceiling. Phase 1 probe 5 measures the command case |
| R12 A paused run's resume moves the question to a new child | The old step question would be dropped with the old child | Phase 1 probe 3 decides; Phase 4 moves an open step question to the new child of the same `(workflowId, nodeId)` if probe 3 shows one |
| R13 A step that dies silently keeps its open approval or question (D25) | The item stays in "Needs attention" and the parent reads Waiting with no automatic exit while a tab is open | Accepted with D25; the exits are answer, Stop, close and the idle sweep with no tab attached. KNOWLEDGE states them (Phase 7) |
| R9 A Python change needs a restart that ends every live session | Live checks end any session in use | Restart granted for this task (2026-10-12); batch each phase's live checks into one restart |

## 7) Verification

- Automated: `.venv-PowerAtlas/Scripts/python -m pytest tests/test_web.py --timeout=300` and `node tests/acp_page.test.mjs`, after every phase.
- Protocol: the Phase 1 probes, on a second kiro-cli process only.
- Live (Phase 7, after a granted restart), in a real browser (Chrome tools or standalone Playwright):
  - a quick-spec option answer;
  - a free-text answer;
  - a Stop while a question is open (the turn moves on and the card disables);
  - a Manual-mode approval and a question in "Needs attention", with `(N)` in the title while a transcript is open;
  - a workflow step question on the row, in the transcript, in "Needs attention" and as Waiting, cleared after the answer;
  - a `session/load` showing one step-message row.
- Confirm `README.md`, `docs/KNOWLEDGE.md` and `plans/ROADMAP.md` are updated as listed in §8.

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `README.md` | Overview paragraph that begins "*Needs attention* lists what is waiting on you": add the approvals, questions and step questions block and the `(N)` title count. The `/acp` item "**The agent can ask a clarifying question mid-turn, and the page answers it inline.**": options with descriptions, the Recommended badge, free text, and the matching "Needs attention" row | 7 |
| `docs/KNOWLEDGE.md` | New `_kiro/userInput` section beside "ACP permission wire shapes" (capability, request shape, reply rules, Phase 1 results). The `_kiro/session/notify` shape entry: state that PowerAtlas handles it and with which frame. The "Not measured" list of workflow methods: move `paused` and `node_paused` out. The "Handlers" bullet: `_kiro/workflow/paused` behaviour. The "*`failed` is inferred.*" bullet: `run_complete` `status: "paused"` keeps steps waiting. The "*Waiting.*" bullet and the resume-signal bullet: the warning notify, the resume triggers, the "Paused" header. The replay chunk note ending "skips both": replayed `system-notification` warnings and errors render as a step-message row. The "Orchestrating (N agents)" paragraph: add "Paused (n)". The workflow-step approvals paragraph on `_pending_permission`: entries gain `kind`, `text`, `created`; questions are cancelled through the same paths with `{"action": "cancelled"}` | 7 |
| `plans/ROADMAP.md` | Remove the three items, anchored by their titles: "`_note_subagent_action` filters its crew frame with the wrong map", "Approvals and questions in "Needs attention"", "`_kiro/session/notify` has no handler". That covers the ranking rows, the body items and the clauses in the "Workflow follow-ups" summary line. Rewrite the Low item "A step resuming after `node_paused` stays `waiting`" to the shipped resume rule, or remove it if Phase 1 measured the resume. Update the Layer 3/4 sentence listing the handled `_kiro/workflow/*` frames. Renumber the ranking | 7 |
| `README.md` (watchdog) | Config example line for `acp_prompt_silence_seconds` removed; the "**A turn is bounded by silence, not by duration.**" bullet and the "until the silence timeout above cancels the turn" sentence rewritten | 6 |
| `docs/KNOWLEDGE.md` (reap, watchdog) | The "*Cancellation.*" bullet of the workflow-step approvals entry: the reap no longer cancels a step's requests, and skips steps waiting on a person. The "*Routing.*" bullet's note that the router stamps activity so the prompt watchdog does not cancel a workflow turn: the stamp and the watchdog are gone | 7 |
| `plans/tests/260701_POWERATLAS.md` | Add a section after "Overview summary (`GET /api/dashboard/overview/summary`)" for `GET /api/dashboard/attention` (loopback-only, item kinds, title count) | 7 |
| `src/power_atlas/acp.py` docstrings | `sessions_with_pending_permission` ("a permission request or question"); `_workflow_outcome` (paused never reaches it from `run_complete`); `_answer_permission_cancelled` keeps its plan identifier when wrapped by the dispatcher | 3, 4 |

## Progress Tracker

| # | Phase | Status | Notes |
|---|---|---|---|
| 1 | Pre-flight probes | Complete | code: none; results in §9 |
| 2 | Crew-frame filter fix | Not started | |
| 3 | Agent questions through `userInput` | Not started | |
| 4 | Step messages, waiting state, paused runs | Not started | |
| 5 | Attention feed and title count | Not started | |
| 6 | Remove the prompt silence watchdog | Not started | |
| 7 | Documentation, roadmap and live QA | Not started | Restart granted 2026-10-12 |

## 9) Implementation Divergences from Plan

### Phase 1 probe results (kiro-cli 2.29.1, 2026-10-12)

All runs used a second `kiro-cli acp --agent-engine v3` spawned by a scratch driver (a copy of the exploration's `wfprobe.py`, extended), with `_meta.kiro.userInput: true` declared. The running PowerAtlas was not touched. Session ids appear as roles (`parent`, `child-1`). Run files are scratch only: `runA.json`, `runB.json`, `runC.json`, `runC2.json`, `runD.json`, `runD2.json` and `runE.json` in the session scratchpad's `phase1` folder.

- **Probe 1, abandon reply (measured, runA and runB).** Quick-spec mode, the run-5 prompt. The question arrived as `_kiro/userInput` `{sessionId: parent, toolCallId, question, options}`, preceded by a `tool_call` (`user_input`, `pending`) and a `session_info_update` `pending_interaction` (`interactionType: "user_input"`). Reply `{"action": "cancelled"}` (runA): kiro-cli emitted `interaction_resolved` `outcome: "cancelled"`, asked no further question, and the turn ended `end_turn` 24 s later, with no error and no hang. Reply `{"action": "answered", "answer": ""}` (runB): `interaction_resolved` `outcome: "answered"`, no further question, `end_turn` 24 s later; the agent read it as "the user opted to skip". In both runs the parent agent then asked its remaining questions as plain text. **Skip option**: titled `"Skip questions"` (not "Skip remaining questions", which is its description: "Skip remaining questions and go straight to generating the task list"), and it was the **last** option in all four question runs (4 or 5 options, none `recommended`). D7's first-19-plus-last rule keeps it. **Gate: D4 unchanged**; the Phase 3 malformed-request reply needs no change.
- **Probe 2, a step's `_kiro/userInput` (measured, runD and runD2).** A one-step workflow told to call `user_input`. No `_kiro/userInput` arrived in either run. In runD (step agent `wf-coder`) the step asked through `send_message` `warning` instead. In runD2 the step was told to list its tools if it had no `user_input`: it reported `execute_pwsh, fs_write, web_fetch, disclose_context, send_message` (agent `kirocrew-worker`). Inferred: workflow step sessions have no `user_input` tool on 2.29.1, so step questions arrive only as warning notifies. **Gate: routing through `_workflow_step_parent` kept, marked unverified.**
- **Probe 3, paused-run resume (measured, runE; pauses also in runD and runD2).** The run-1 prompt paused on the first try: step `send_message` `warning`, step `turn_end`, then `node_paused` `{workflowId, nodeId, reason: "Step requested user input via send_message.", parentSessionId}`, `_kiro/workflow/paused` `{workflowId, pauseReason, parentSessionId}` and `run_complete` `status: "paused"` (3 of 3 workflow runs paused today). The parent prompt "Blue." produced, in order: a parent `tool_call` Send Message, a notify `{sessionId: child-1, callerSessionId: parent, sender: "parent", severity: "info"}`, then a **second `run_start` for the same `workflowId`** and a **`node_start` for the same `nodeId` carrying the same step `sessionId` (child-1)**. No new child. The step then sent a `success` notify and ended its turn, and kiro-cli sent **another** `node_paused` (`reason: "Awaiting next user message on step session."`), `paused` (`"Step 'ask-step' is waiting for the next user message."`) and `run_complete` `status: "paused"`. No `node_complete` arrived in the 90 s drain. Whether a further parent message would complete the node was not measured. **Gate: no new child, so Phase 4 needs no old-row handling for a new child (R2 and R12 do not occur).** Two shapes Phase 4 must handle and the orchestrator should decide on: (a) a resume re-sends `run_start` and the step's `node_start` for a run and child already tracked; (b) a step that finished its work and reported `success` can end paused, not completed, with a different `reason`, so D13/D14 as written would show it waiting for up to 6 h.
- **Probe 4, rail verdict (measured, runA, runB, runC, runC2).** While the question was open, before any reply, the session's `messages.jsonl` ended with a `pending_interaction` record (tail: `turn_start, steering_inclusion, assistant, assistant, session_metadata, sub_agent_start, pending_interaction`). After the reply an `interaction_resolved` record followed. `status_classifier` reads `pending_interaction` as WAITING, so the rail reads Waiting from disk alone for a quick-spec question. Recorded for the knowledge base; no gate.
- **Probe 5, Stop during a command and during a question (measured, runA, runB, runC, runC2).** `_kiro.dev/commands/execute` `{sessionId, command: {command: "compact", args: {}}}` was **refused at once** with JSON-RPC `-32603` "Internal error", `data.details`: `[PersistenceClassification] Ext method "_kiro.dev/commands/execute" has no persistence classification` (runA). So a cancel during a command could not be measured on 2.29.1, and the palette slash-command path appears broken on this version (inferred from one call; outside this plan). A typed `/compact` prompt cancelled after 0.3 s returned `stopReason: "cancelled"` in 0.33 s (runB). `session/cancel` sent 3 s after a `_kiro/userInput` arrived, with the request left unanswered: the prompt returned `stopReason: "cancelled"` within 0.05 s, the `user_input` tool call and the sub-agent went `failed`, and kiro-cli sent no `interaction_resolved` and no cancel for the open request (runC). A `{"action": "cancelled"}` reply sent 5 s after that turn ended was accepted without error: kiro-cli emitted `interaction_resolved` `outcome: "cancelled"`, and the next prompt on the session ran normally (runC2). **Gate: R11 gets the note that the cancel during a slash command is unmeasured because `commands/execute` errors on 2.29.1; Stop with an open question ends the turn, and D4's late `cancelled` reply on Stop is harmless.**

### Divergences from Phase 1

- **Skip option title (D7).** The measured title is `"Skip questions"`, last in 4 of 4 runs; "Skip remaining questions" is the start of its description. The first-19-plus-last rule still keeps it; only D7's wording was wrong.
- **Resume re-sends `run_start` and `node_start` (Phase 4).** A resumed paused run re-sends `run_start` for the tracked `workflowId` and `node_start` for the tracked `nodeId` with the same step `sessionId`. No new child appears, so the R2/R12 new-child handling is not needed. Phase 4 must accept these frames again without resetting the run or the row.
- **Slash commands unmeasured (R11).** `_kiro.dev/commands/execute` errors on 2.29.1, so the effect of Stop during a slash command is unmeasured. D26 still covers the command path. Outside this plan: PowerAtlas's `commands_execute` sends the refused shape. `orchestrator.log` and 3 rotations have 0 `PersistenceClassification` lines, so it is not confirmed in production.

## Follow-up Work (Deferred)

1. **File-based transcript view.** The dashboard's `/api/session-transcript` view of sessions it does not hold still shows replayed `system-notification` records as user bubbles. Out of scope: D12 covers the ACP live and replay path only (Architect review finding #14).
2. **Pending cards lost to history eviction.** A pending request whose frame falls out of the replay history has no card after a reconnect (R7). Pre-existing; the attention list still shows it.

## Review Log

### 2026-10-12 -- Plan Review (via /qplan, 1 cycle by user request)

Personas: Architect (gap-critic lens), Senior engineer, Security auditor. The doc-impact search ran alongside. 29 findings after merging duplicates (3 High, 10 Medium, 15 Low, 1 Info); 26 resolved in the plan, 3 escalated for the user. Merged findings keep the highest severity; raw per-persona summaries are kept in the session scratchpad.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | The question card was planned twice in the templates, while the permission card lives in `static/transcript-renderer.js` (Arch 1, Sr 1, Sec 11). | Fixed -- D23 builds `addQuestionRequest` there; Phase 3 scope, textarea disable and harness list updated. |
| 2 | High | A step's routed question kept the step running, so the 600 s reap cancelled it (Arch 2). | Fixed -- D13 sets the step waiting when its question is stored; test added. |
| 3 | High | Step questions leaked past close, detach and the 6 h reap (Arch 3, Sr 2). | Fixed -- D11 lists every removal path plus a tracking filter in the snapshot (the `_workflow_live` filter was superseded by D25); tests added. |
| 4 | Medium | Verbatim option titles could not survive cleaning or clipping, giving a silent wrong answer (Sec 1). | Fixed -- D6/D7 answer by `optionIndex`, mapped to the stored raw title. |
| 5 | Medium | Approval and question text was clipped but not cleaned of bidi, zero-width or NUL characters (Sec 2). | Fixed -- D8 adds `_clean_agent_text` for stored text, toast and card; test added. |
| 6 | Medium | The abandon dispatcher could fail open if an approval were misclassified (Sec 3). | Fixed -- D4 defaults every non-question kind to the permission shape; kind set by method only; test added. |
| 7 | Medium | The "step-frame branch" for the `tool_call` resume did not exist, and the buffer could swallow the frame (Arch 5, Sr 3). | Fixed -- D13 and Phase 4 name the spot after `_workflow_touch`, before the buffer; `_reserved > 0` test. |
| 8 | Medium | A question toast needed `notifications.notify_question`, the test stub and the in-page notification (Sr 4, Sec 8, Arch 13). | Fixed -- D9, Phase 3 scope and tests updated. |
| 9 | Medium | Abandoning a step question would post "a tool call was denied" (Arch 4, Sr 5, Sec 9). | Fixed -- D4 skips the denial notice for questions. |
| 10 | Medium | Whether the rail reads Waiting for an open question was unverified (Arch 6). | Fixed -- Phase 1 probe 4, plus a gated `_workflow_states` overlay in Phase 5. |
| 11 | Medium | `userInput` changes Yolo behaviour, and the 30-minute silence watchdog can cancel an open question (Arch 7). | Resolved by the user (2026-10-12) -- the silence watchdog is removed (D24, Phase 6); R10 records the Yolo change. |
| 12 | Medium | The waiting crew state had unlisted consumers, wrong dashboard function names and no CSS (Arch 8, Sr 7). | Fixed -- D15 lists every consumer, the names and the CSS. |
| 13 | Medium | D21 left the click mechanism open, although `/acp?sid=` exists (Arch 9, Sr 8, Sec 10). | Fixed -- D21 uses the rail when the row exists, else `/acp?sid=` in a new tab, select-only. |
| 14 | Low | Detach was said to reply to a question, but the agent is gone (Arch 10). | Fixed -- D4 and the Phase 3 test say detach writes nothing. |
| 15 | Low | D16 did not say why `_on_subagent_list` keeps the v2 map, or what an older-wave frame does (Arch 11, Sr 11). | Fixed -- D16 notes both. |
| 16 | Low | Phase 1's clean-`git status` criterion conflicted with recording results in this plan (Arch 12). | Fixed -- criterion now excludes this plan file. |
| 17 | Low | The file-based transcript view still shows replayed step messages as user bubbles (Arch 14). | Fixed -- recorded as Follow-up Work item 1. |
| 18 | Low | "Paused (n)" also shows for a step question in a run that was not paused (Arch 15, Sr 13). | Resolved by the user (2026-10-12, "ok") -- "Paused" kept. |
| 19 | Low | Three tests assert the exact `_kiro_client_capabilities` value (Sr 6). | Fixed -- Phase 3 step 1 names them. |
| 20 | Low | A live `system-notification` chunk could double a step message (Sr 9, Sec OQ2). | Fixed -- D12 gates on `replay: true`; live-chunk test added. |
| 21 | Low | The dashboard harness slices named regions; a top-level interval would leak into every harness instance (Sr 10). | Fixed -- D19 init function; Phase 5 names the region and the `title` stand-in. |
| 22 | Low | The Phase 3 grep criterion also matched the definition and a docstring (Sr 12). | Fixed -- criterion matches call sites only. |
| 23 | Low | The no-await rule between lookup and pop was not stated (Sec 4). | Fixed -- D6 states it; double-answer test added. |
| 24 | Low | Remote answering of questions needed its reasoning recorded (Sec 5). | Fixed -- D10 records it. |
| 25 | Low | The option count was unbounded (Sec 6). | Fixed -- D7 caps at 20; test added. |
| 26 | Low | A `[workflow step …]` prefix in question text could be spoofed (Sec 7). | Fixed -- `workflowStepLabel` travels as its own field. |
| 27 | Info | The attention endpoint's loopback gate is correct (Sec 12). | Fixed -- a no-cookie 403 test added. |
| 28 | Low | A routed approval also keeps its step running, so the 600 s reap cancels an unanswered step approval (Arch OQ4). | Resolved by the user (2026-10-12) -- the reap no longer cancels step requests (D25). |
| 29 | Low | A warning on a reaped row should reopen it, and a resumed run may move the question to a new child (Sr OQ2, OQ3). | Fixed -- D13 reopens reaped rows; R12 and Phase 4 handle the new child. |

Override recorded per `## Continuous Improvement`: review cycles, default up to 3 (`/qplan` auto-fix loop), user override 1 ("1 qreview cycle", 2026-10-12). No re-review ran after the fixes.

### 2026-10-12 -- Plan Revision (via /qplan review mode, 1 cycle by user request)

The user's decisions were folded in first: D24 removes the prompt silence watchdog (Phase 6, SC-9), D25 stops the reap cancelling step requests, D15 keeps "Paused", and restarts are granted. Then the same three personas reviewed the revised plan. 41 findings before merging; 31 rows after. 30 resolved in the plan; the 1 escalation was resolved by the user (D26).

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | Turn-boundary eviction of reaped rows would cancel step requests through `_workflow_drop_child`, undoing D25 (Arch 1, Sr 4). | Fixed -- D25: the reap skips steps waiting on a person, and both eviction paths skip their rows; tests added. |
| 2 | High | Phase 6 would delete the shared `acp_fast` fixture and about 17 unrelated tests (Arch 5, Sr 1). | Fixed -- Phase 6 keeps the fixture and names every test to delete, re-point or edit. |
| 3 | Medium | An approval-only step reaped at 600 s showed "Done" with its card open (Arch 2). | Fixed -- the D25 reap skip covers approvals too. |
| 4 | Medium | The probe-4 fallback could not override a working verdict (Arch 3). | Fixed -- Phase 5 adds an unconditional waiting override after `_resolved_session_status`. |
| 5 | Medium | SC-5 failed for parallel steps: a step question's parent read Working beside a running sibling (Arch 4). | Fixed -- `sessions_awaiting_person()` feeds the override; parallel-step test added. |
| 6 | Medium | D24 also removes the bound on slash commands, unstated and unmeasured (Arch 6). | Fixed -- D24 names commands; Phase 1 probe 5 measures cancel during a command. |
| 7 | Medium | `last_activity` and `_stamp_activity` lose their only reader; `_INACTIVITY` keeps a silence-era name (Arch 7, Sr 7). | Fixed -- D24 deletes them and renames the sentinel to `_PROMPT_WAIT`. |
| 8 | Medium | R11 overstated Stop: it writes a cancel but never ends a hung turn locally (Sec 1). | Resolved by the user (2026-10-12, "Ok with reco") -- D26: Stop ends the turn locally after the grace; Phase 6 tests. |
| 9 | Medium | The Phase 3 grep missed the two-line `coro =` call in `_cancel_step_permissions` (Arch 9, Sr 3, Sec 2). | Fixed -- grep widened with the expected hits; `node_complete` abandon test added. |
| 10 | Medium | D25 left a dead step's requests with no automatic exit, unstated (Sec 3). | Fixed -- R13 states the residual and its exits; D25 wording on answer routing corrected. |
| 11 | Medium | Phase 6 did not name the grace and ceiling tests that relied on the silence deadline (Sr 2). | Fixed -- both named with their edits. |
| 12 | Medium | The tests D25 replaces were not named (Sr 5). | Fixed -- named, with replace, split or re-check per test. |
| 13 | Medium | Nobody set `dataset.workflowStep` or refreshed the composer for a step question card (Sr 6). | Fixed -- Phase 3 step 6 assigns both to the template; page test added. |
| 14 | Low | Silence prose would survive the identifier-only grep; README entry spans several lines; KNOWLEDGE lines unlisted (Arch 8, Sr 8). | Fixed -- comment sweep, a `silence` grep criterion, whole README entry, KNOWLEDGE row. |
| 15 | Low | D4 still listed the reap among no-notice paths (Arch 10). | Fixed -- removed. |
| 16 | Low | A warning on a reaped row assumed the row still existed (Arch 11). | Fixed -- covered by the D25 eviction skip. |
| 17 | Low | The paused branch could re-track finished children (Arch 12, Sr 15). | Fixed -- D14 limits it to tracked children under the reopen guard. |
| 18 | Low | Phase 3 had two steps numbered 6 (Arch 13, Sr 11). | Fixed -- renumbered. |
| 19 | Low | The 30-minute live check sat outside the Phase 7 restart batch (Arch 14). | Fixed -- moved to Phase 7, started first. |
| 20 | Low | R11 omitted the deliberate pin by a remote device (Sec 4). | Fixed -- added to R11. |
| 21 | Low | D21 claimed `/acp?sid=` only selects; it may send one `load` (Sec 5). | Fixed -- D21 reworded. |
| 22 | Low | The D18 title was clipped, not cleaned (Sec 6). | Fixed -- cleaned on store and in the fallback. |
| 23 | Low | An empty raw option title would answer as a skip (Sec 7). | Fixed -- options need a non-empty raw title. |
| 24 | Low | Raw titles under `options` changed that key's element type (Sec 8). | Fixed -- stored under `answer_titles`; strict `kind == "question"` check. |
| 25 | Low | The question reply's write-failure handling was unstated (Sec 9). | Fixed -- D6 copies the permission route's `_discard` and names the blast radius. |
| 26 | Low | The cycle-1 log line contradicted D11's tracking filter (Sec 10). | Fixed -- log line annotated as superseded. |
| 27 | Low | The replayed step message was not named as cleaned (Sec 11). | Fixed -- Phase 4 step 4 names the cleaner. |
| 28 | Low | `notify_question` could not name the session (Sec 12). | Fixed -- `notify_question(label, text)`. |
| 29 | Low | `_cancel_step_permissions`' docstring, §1's Stop line numbers and the `KeyError` claim were off (Sr 9, Sr 10). | Fixed -- corrected. |
| 30 | Low | The renderer function list feeds only the picker sandbox (Sr 12). | Fixed -- Phase 3 names the sandboxes. |
| 31 | Low | The new no-cancel test could pass on unfixed code; the test docstring incident would be deleted (Sr 13, Sr 14). | Fixed -- 0.3 s wait, run before the change; the incident docstring is kept. |

No re-review ran after these fixes (1-cycle cap).

## Harness Improvement Opportunities

- Step 1.5's three code-tracing briefs overlapped heavily on this scope: all three enumerated the same `_emit_subagents_frame` call sites and pending-permission removal paths — cost: about 630k sub-agent tokens for roughly one report's worth of distinct facts — suggested change: when Step 1 already names the call sites, tell the subsystem brief to skip enumerations the directed brief owns.
- The decisive findings (the `user_input` question path, the paused-run mapping) came from probes and a vendor-bundle grep, not from the trio — cost: the trio's "clarifying question" answer was "no mechanism found", which would have shaped the interview wrongly without the probes — suggested change: add "grep the vendor bundle for the concept" to the Probe gate's examples when the work integrates with a third-party protocol.
- The doc-impact sub-agent and the persona reviewers cover overlapping ground: three personas independently found that the card belongs in `transcript-renderer.js` — cost: three reviewers each spent about 15 tool calls on the same discovery — suggested change: have the planner list the shared modules a UI phase touches (grep the existing sibling feature's function names) before review, so reviewers check it rather than discover it.
- With a 1-cycle cap, 26 fixes went into the plan without any re-review — cost: unclear; regressions in the fixes go unchecked until `/qdev` — suggested change: when the user caps cycles at 1, offer a cheap diff-only check of the applied fixes that does not count as a cycle.
