# ACP Needs Attention: Approvals, Agent Questions and Workflow Step Messages

> **Date**: 2026-10-12
> **Status**: Complete  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Last Updated**: 2026-10-13 11:22
> **Scope**: List pending approvals, agent questions and workflow step questions in the dashboard Overview's "Needs attention" with a `(N)` title count; declare kiro-cli's `userInput` capability and answer `_kiro/userInput`; handle `_kiro/session/notify` and paused workflow runs; fix `_note_subagent_action`'s crew-frame filter
> **Memory relied on**: none
> **Estimated effort**: 2-3 days

## Completion Summary

All seven phases are complete. The dashboard's "Needs attention" lists pending approvals, agent questions and workflow step questions with a `(N)` page-title count; agent questions arrive through kiro-cli's `userInput` and render as cards with options, descriptions and free text; step messages and paused runs render truthfully; the prompt silence watchdog is gone and Stop ends a turn locally when the agent ignores it; no automatic path cancels a request waiting on a person. Final suites: `tests/test_web.py` 3397 passed, 1 skipped; node 1112 passed. Live QA passed in a real browser, including a 33-minute held approval in Manual mode.

### Acknowledged at archival

- **Follow-up plan intended** (Follow-up Work items 1 and 2): the file-based transcript view's replayed step messages, and pending cards lost to history eviction. Both are in `plans/ROADMAP.md` ("Needs attention residuals").
- **Accepted** (doc ripple, D2): the kiro-cli delegation entry in `memory/MEMORY.md` still describes the removed silence watchdog; routed to the `/qdream lightweight` run at archival.
- Pass 4 fixed one stale reference: `plans/tests/260701_POWERATLAS.md` no longer lists `acp_prompt_silence_seconds` among the save-setting keys.
- Accepted (harness opportunity): Step 1.5's three code-tracing briefs overlapped heavily on this scope.
- Accepted (harness opportunity): the decisive findings came from probes and a vendor-bundle grep, not the trio.
- Accepted (harness opportunity): three plan reviewers independently found that the card belongs in `transcript-renderer.js`.
- Accepted (harness opportunity): with a 1-cycle cap, plan fixes went in without re-review.
- Promoted: `/qdev` Rules now require a unique commit-message file name per commit and a `git log -1 --format=%s` check (agent-playbook `db3af15`).

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
- [x] The new test fails on the unfixed code (run it before the fix and record the failure in §9)
- [x] Both tests pass after the fix; the full `tests/test_web.py` suite passes

Implementation (2026-10-12, code: 68b5f8e)
The fix is in `_note_subagent_action` in `src/power_atlas/acp.py`. It used to filter the crew frame through `self.crew_spawn_toolcallids`. It now passes `{meta["parent"]: entry.get("fan_out_id", _NO_ANCHOR_TOOLCALLID)}`, so the frame is filtered by the row's own wave tag (D16), with a short comment. Two tests were added to `TestAcpSubagentActivity` in `tests/test_web.py`: `test_action_frame_keeps_a_wave_tagged_row_under_its_own_wave` seeds rows from two waves and a different v2 anchor, and checks that an action on the active-wave row emits only that row with `toolCallId == "wave-new"`, and that an action on the older-wave row emits only that row with `toolCallId == "wave-old"`; `test_action_frame_keeps_a_v2_row_under_its_spawn_toolcallid` registers a v2 row through the real `SUBAGENT_LIST_METHOD` path with a stale `_active_fan_out_wave` entry and checks it is still emitted with `toolCallId == "v2-spawn-tc"`. Before the fix the wave test failed at its first payload check (`assert [] == ['sub-new']`); the v2 test passed before and after. Full suite after the fix: 3260 passed, 1 skipped.

Implementation (2026-10-12, code: 3ce782e)
Review auto-fixes. The wave test now drives the active-wave case through the real notification path: a `SUBAGENT_ACTIVITY_METHOD` `tool_call_chunk` from the child `sub-new` through `_on_notification`, which reaches `_note_subagent_action`, and it checks the payload holds only `sub-new`, with action "Reading file" and `toolCallId == "wave-new"`. The older-wave step still calls `_note_subagent_action` directly. With the old `self.crew_spawn_toolcallids` argument restored temporarily, the wave test failed with `assert [] == ['sub-new']` and the v2 test passed. A comment sentence in `_note_subagent_action` says that a row with no `fan_out_id`, which only old in-memory state can hold, falls back to the no-anchor id. Full suite: 3260 passed, 1 skipped.

Orchestrator notes: QA SKIP. The `[QA]` phase has no runtime path to drive: §1 records that steps sent no `tool_call_chunk` in 4 probe runs on kiro-cli 2.29.1, and the unit tests drive the real notification path instead. QA annotation mismatch: consider removing `[QA]` from a phase like this in future plan revisions. Review: one cycle (the user's per-phase cadence); cycle 2 skipped because both cycle-1 findings were Low and the fixes were a test re-route and a comment, re-verified by the implementer's mutation check.

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
- [x] `initialize` carries `_meta.kiro.userInput: true` (unit test)
- [x] `sessions_with_pending_permission` docstring says it covers questions too
- [x] Every Python and page test listed above passes; the full suites pass
- [x] `grep -n "_answer_permission_cancelled(" src/power_atlas/acp.py` shows only the definition, the malformed-request refusal in `_on_permission_request` and the call inside `_answer_pending_cancelled` (the two-line `coro = …` form in `_cancel_step_permissions` included)
- [x] Live (after a granted restart, else carried to Phase 7): a quick-spec question answered from a card option, and a free-form question answered with free text, on `/acp`

Implementation (2026-10-12, code: 82e0119)
In `src/power_atlas/acp.py`, `_kiro_client_capabilities()` now always declares `_meta.kiro.userInput: true`. `_on_agent_request` sends `_kiro/userInput` to a new `_on_user_input_request`. It validates and routes the request the same way `_on_permission_request` does, including workflow steps through `_workflow_step_parent`, and mints an opaque id. It keeps at most 20 options whose raw title is a non-empty string: the first 19 plus the last one, so kiro-cli's trailing "Skip questions" survives. It stores a `kind: "question"` entry in `_pending_permission` holding the raw `answer_titles`, the cleaned `text` and `created`. It then emits a recorded `question_request` frame with cleaned, clipped text, where `recommended` is true only for a real `true`. A step's question carries `workflowStep` and `workflowStepLabel` as separate fields, and its text is never prefixed. The handler raises a `question_request` toast and sets a step's question to waiting, reopening its row. A malformed request with a usable id gets `{"action":"cancelled"}`; one without a usable id goes to `_refuse`. The new `_clean_agent_text` collapses whitespace, drops every Unicode category C character and clips the result. Approval entries now also carry `kind: "approval"`, `text` and `created`. The new `_answer_pending_cancelled` writes `{"action":"cancelled"}` only for `kind == "question"`; any other value, missing or unknown, gets the permission outcome. Close, the turn-end sweep, both Stop paths and `_cancel_step_permissions` now use it, and a question that is dropped gets no denial notice. The new `_handle_question_response` keeps the (a)-(e) order and accepts either an `optionIndex` (an int, not a bool, in range), mapped to the stored raw title, or a stripped free-text `answer`; refusals are `empty_answer`, `invalid_option` and `bad_payload` (both keys, neither key, a non-string answer, or more than 4000 characters). `_handle_permission_response` refuses a question entry with `unknown_request`. For D25, the reap no longer calls `_cancel_step_permissions`, which lost its `notify` parameter, and the reap, the turn-end loop, `_evict_crew_children` and `_evict_finished_subagents` all skip a step for which the new `_workflow_step_awaits_person` is true. Outside `acp.py`: `notifications.notify_question(label, text)` and a `question_request` branch in `web.py`'s `_notify_from_acp`; `addQuestionRequest` in `static/transcript-renderer.js` (`createElement`/`textContent` only, the permission row class and `dataset.requestId`, step label, question, option buttons with a Recommended badge and description, a textarea whose Send stays disabled while blank; either answer disables the card; `markPermissionResolved` also disables textareas); both templates dispatch `question_request`, set `workflowStep` and refresh the composer controls, and `acp.html` fires its in-page notification behind `!replaying && _notifyEnabled`; `.acp-question-*` rules with `[hidden]` rules in `style.css`. Tests: the capability and exact-entry assertions updated, the two reap-cancels tests replaced by D25 tests, the combined reap-and-close test kept for its close half, and new tests for storage, frame, toast, free-form questions, raw-title mapping, each refusal and both boundaries, double answers, foreign sessions, the 20 cap, U+202E/U+0000, the abandon shape per kind, cross-route refusals, malformed requests, step routing and waiting, Stop/close/sweep/detach for both kinds, no denial notice, `node_complete`, the type guard, the `initialize` handshake and the toast policy; nine server mutations each failed a test. Eight page checks on both pages; three renderer mutations each failed one. pytest 3305 passed, 1 skipped; node 1087 passed.

Implementation (2026-10-12, code: 6223be4)
Review auto-fixes. `_handle_question_response` sends every refusal through a local `refuse()` that adds `requestId`. The `permission_request` frame title goes through `_clean_agent_text` without clipping (superseded by b0cabe5). Logging: `_log_held_step` logs at INFO once per child when the reap or the turn-end sweep skips a step waiting on a person; `_on_user_input_request` logs dropped options and a non-list `options`; `_evict_finished_subagents` logs one WARNING per parent when held rows keep the crew over `MAX_SUBAGENTS_PER_SESSION` (`_crew_overflow_warned`, cleared under the cap, with the parent and in `_detach`). Pages: the question box gets `maxLength = 4000`; `questionStrip()` decides blankness like Python's `str.strip()`; each card registers a reopen function that refuses a resolved card; `reopenQuestionAfterRefusal(payload)` reopens the card named by `requestId`, or the card answered last, for `bad_payload`, `empty_answer`, `invalid_option`, `not_subscribed` and `internal_error`; both pages' error handlers call it. Tests: `test_handle_question_response_reply_write_failure_calls_discard`, `test_crew_cap_eviction_keeps_finished_steps_waiting_on_a_person`, the log tests, the approval-title bidi assertion, `requestId` on every refusal; five page checks. Mutations of the eviction skip, `_discard`, the title cleaning and the refusal `requestId` each fail a test. pytest 3309 passed, 1 skipped; node 1092 passed.

Implementation (2026-10-12, code: b0cabe5)
User decision "A" (2026-10-12). `_sweepable` takes a new required keyword, `awaits_person`, and stays pure; `_sweep_once` computes it from the new `_Supervisor.sessions_awaiting_person()` (owners of every `_pending_permission` entry, routed step entries included, plus parents of `_workflow_step_questions` entries) and holds every session for a tick if that read raises. One INFO line per hold, through the pruned `_sweep_held_logged` set. `test_sweep_keeps_an_idle_parent_whose_step_awaits_a_person` covers an approval, a question and a step-question record (kept, nothing written, one log line) and a control that is swept; removing the guard fails all three held variants. The new `_clean_agent_lines`, used only for the `permission_request` frame title, normalises line endings and drops category-C characters except `\n` and `\t`, without collapsing, stripping or clipping, so the card's `white-space: pre-wrap` display is as before; the bidi test expects `"rm -rf /tmp/x\n\nnow"`. pytest 3313 passed, 1 skipped; node 1092 passed.

Implementation (2026-10-12, code: ad12a39)
Re-review fixes. When `sessions_awaiting_person()` raises, `_sweep_once` logs one traceback and then a counted WARNING per tick (`_sweep_awaiting_failures`, reset by the next good read) and writes no hold line; `test_sweep_holds_every_session_when_the_open_request_read_fails` covers it, and three mutations fail it. The `_on_permission_request` comment now describes the frame-title cleaning. `markPermissionResolved` clears `lastAwaitingQuestionId` when it matches (with a `typeof` guard for the picker sandbox); two page checks, of which the dashboard one fails without the fix. `_workflow_unhold(entry)` clears `held_logged` once a step has nothing left open, called at the four entry-removal sites; `test_held_step_is_logged_once_per_hold_by_the_reap` logs a second hold again. pytest 3314 passed, 1 skipped; node 1094 passed.

Orchestrator notes: the four change descriptions above are condensed from the sub-agent returns, not pasted verbatim. The live criterion is deferred to Phase 7 (restart batched there) and added to Phase 7's exit criteria. QA: per-phase `/qqa` BLOCKED by design and carried to Phase 7, since the running instance holds the old bytecode and the plan batches the single granted restart there; the unit and page suites drive the surfaces meanwhile.

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
- [x] Every Python and page test listed above passes; the full suites pass
- [x] `_workflow_outcome` docstring notes that `run_complete` `paused` is handled before it
- [x] The `_kiro/session/notify` INFO fall-through no longer fires for handled notifies (unit test on the log level, beside `TestAcpUnrecognizedNotificationLogLevel`)
- [x] Phase 1 probe 3's outcome applied (old-row handling added if the resume spawned a new child, or "unmeasured" noted in §9)
- [x] Live (after a granted restart, else carried to Phase 7): a workflow step question shows on the row, in the transcript and as Waiting; after the answer the row runs again and finishes; a reload or `session/load` shows one step-message row, not two

Implementation (2026-10-12, code: f6d4d71)
`_on_notification` handles `_kiro/session/notify` in a new `_on_session_notify`, after `_workflow_touch` and before the early-frame buffer, logging every notify at INFO; a `tool_call` from a tracked step calls `_workflow_step_resume` in the same place. A step's message (`sender: "step"`, step = `callerSessionId`, tracked under `sessionId`) is cleaned to 512 characters and set as the open row's `action` for every severity; `warning` and `error` also record `step_message {step, severity, message}` on the parent; `warning` writes `_workflow_step_questions[step]` and sets the step waiting, reopening a reaped row. A parent-to-step notify resumes the step. `_workflow_step_resume` removes the step question and sets a waiting step running unless a request is still open. Step questions are removed in `_workflow_drop_child` (node_complete, run_complete, Stop, eviction) and `_workflow_drop_parent` (close). A replayed `system-notification` chunk gives the same `step_message` for warning and error and nothing otherwise; a live one stays a user chunk with an INFO line. `run_complete` `status: "paused"` is handled before `_workflow_outcome`: tracked steps waiting, finished rows untouched, the run record refreshed and never popped or marked ended. A re-sent `run_start` and `node_start` reset nothing. Comments and the `_workflow_outcome` docstring cite the measured shapes; `step_message` is in `SERVER_TYPES`. Pages: `addStepMessage` in `static/transcript-renderer.js` (textContent only, severity class from a fixed list), dispatched by both templates; the D15 waiting state in both templates (state functions, `status-waiting` dot, `acp-crew-row-waiting`, sub-panel head, "Paused (n)" header); waiting colours and `.acp-step-message*` in `style.css`. About 25 workflow tests plus page checks; pytest 3345 passed, 1 skipped; node 1099 passed; 9 server and 4 page mutations each failed a test.

Implementation (2026-10-12, code: cdfa879)
Review fixes. A warning also stores its record in `_workflow_step_last_warning`; a `node_paused` whose reason mentions "user input" and "send_message" (`_is_send_message_pause`) reopens a step question that a step `tool_call` had removed (`_workflow_reopen_step_question`), cleared on the parent's answer, the step's end, close and detach. Both resume paths use `_workflow_step_awaits_person`. Only a step's own `tool_call` resume respects `_WORKFLOW_CHILD_MAX_AGE_S` (`_workflow_set(..., seen=)`, `_workflow_child_capped`). `_step_message_text` supplies "asked a question" / "sent a message" for an empty warning or error. `_workflow_step_label`'s docstring names all its uses. `renderTranscriptFrame` gained `step_message`, `permission_request`, `question_request` and `permission_resolved` cases for the dashboard's history replay. Tests for each, including the index-only guard and the dashboard sub-panel waiting head. pytest 3362 passed, 1 skipped; node 1101 passed.

Implementation (2026-10-12, code: 25fd7bc)
Re-review fixes. `_workflow_reopen_step_question` deletes the remembered warning once it reopens a question, so the reopen is one-shot (`test_question_reopen_is_one_shot`). A page check replays a resolved step approval card disabled and a pending one answerable on the dashboard. pytest 3363 passed, 1 skipped; node 1102 passed.

Orchestrator notes: the change descriptions above are condensed from the sub-agent returns, not pasted verbatim. The live criterion is deferred to Phase 7 and added to Phase 7's exit criteria. QA carried to Phase 7 with the batched restart. The changed page expectation (status-thinking to status-waiting) cites D15; the changed resume-rule test cites review finding 7 and is recorded in §9.

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
- [x] Every Python and page test listed above passes; the full suites pass
- [x] `_REMOTE_ALLOWED_PATHS` unchanged (`git diff` shows no edit to it)
- [x] Live (after a granted restart, else carried to Phase 7): a Manual-mode approval and a question appear in "Needs attention" with the title count, open their session on click, and clear within one poll of the answer
- [x] (deferred from Phase 4) Past the 6 h waiting bound, the attention list and the waiting override agree with the rail for a step question that outlives `workflow_state` (Phase 4 reliability review finding 4): either both still show it, or neither does, with a test

Implementation (2026-10-13, code: 71033de)
`acp.py`: the `focus_update` branch also stores the cleaned title on a held session (D18); the emitted `title` frame is unchanged. New `_Supervisor.attention_snapshot()`, a loop-only read returning one item per pending approval or question and per open step question, oldest first, `{kind, sessionId, text, created, step, cwd, title}`; a routed entry's `step` comes from `_workflow_step_label`; a step question needs a held parent and a child still in `_workflow_children[parent]`. `web.py`: new loopback-only `GET /api/dashboard/attention` (`no-store`, `acp is None` guard), snapshot on the loop and `_attention_payload` in a worker thread filling `workspace`, `provider`, `age` and the title (stored, else the cleaned `_acp_row_title`, else "Untitled session"). Waiting override: `_awaiting_person_ids` (from `sessions_awaiting_person()`, on the loop) and `_person_override_status`, threaded through `_acp_status_for_held`, `_acp_listing`, `_acp_flat_listing`, `_overview_live`, both listing routes, the live Overview route and `/api/session-availability`; `_workflow_states` excludes the same set. `index.html` and `style.css`: `#dashOvAskBlock` ("Waiting for you") first in Needs attention, `dashOvRenderAsk` keyed by JSON, button rows with kind label, title, workspace, "step · text" and age; a click opens through the rail row or `/acp?sid=` in a new tab; a 5 s poller started from `DOMContentLoaded` (`dashAttentionStart`), running in every mode; `dashSetAttentionTitle` writes the `(N) ` prefix on `_dashBaseTitle`. 16 tests in `TestDashboardAttention` and 7 page checks; the harness gained `document.title`, `querySelector`, `CSS.escape`, `window.open` and `dashOvAskBlock`. Fixture changes with no expected value changed: five `_overview_live` stubs gained `awaiting=None`; three fake supervisors renamed their method to `sessions_awaiting_person`. pytest 3379 passed, 1 skipped; node 1109 passed.

Implementation (2026-10-13, code: b13bcd0)
Review fixes. One shared validity rule: `_awaiting_entry_owner` and `_awaiting_step_parent` (a step question counts only while its child is tracked, by membership), read by both `sessions_awaiting_person` and `attention_snapshot`; the snapshot additionally leaves out owners that are not held. Entries with odd `text` or `created` are now listed (text "", created None sorted last) rather than skipped, so the list and the override agree without weakening the sweeper. The stale `_on_permission_request` comment names `sessions_awaiting_person()`. `_awaiting_person_ids` and `api_dashboard_attention` log failures through `_workflow_state_fault`. A row's age is the server's `age` plus the time since that poll arrived, so a wrong browser clock does not skew it. New tests: the override on `/api/dashboard/sessions` grouped and `mode=recent` and on `/api/acp/sessions?mode=recent` (each of the four listing call sites now fails it when removed), a malformed entry or untracked step question neither listed nor overriding, the throttled fault logging, and a page check for the clock-skew-free age. pytest 3384 passed, 1 skipped; node 1110 passed.

Orchestrator notes: the change descriptions above are condensed from the sub-agent returns. The live criterion is deferred to Phase 7 and added to its exit criteria. QA carried to Phase 7 with the batched restart. The changed malformed-entry test expectation cites review finding 5. No re-review of b13bcd0: the one-cycle cap applies and the fixes were tests, logging, a comment, a client age display and a validity helper whose tests pin both readers.

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
- [x] `grep -rn "PROMPT_SILENCE_SECONDS\|acp_prompt_silence_seconds\|_await_inactivity\|_INACTIVITY\b\|_stamp_activity\|last_activity" src tests README.md` returns only the `_LEGACY_KEYS` entry, the legacy-config test, the test module's incident docstring and `overview.py`'s unrelated `last_activity` output field
- [x] `grep -n -i "silence" src/power_atlas/acp.py` hits only text unrelated to a turn bound (each remaining hit named in §9)
- [x] The new tests pass (the no-cancel test failed before the change, and the D26 ignore-the-cancel test failed before the change); the full suites pass
- [x] `README.md` updated: the config example line removed, the "bounded by silence" bullet and the permission-section sentence rewritten
- [x] Live (batched into Phase 7's restart): the settings menu no longer lists the silence setting

Implementation (2026-10-13, code: d2fbc36)
The commit's subject and body wrongly carry the Phase 4 text (see §9); the change is Phase 6's. `acp.py`: `PROMPT_SILENCE_SECONDS`, the silence deadline in the wait (the `last_activity` and `queued_behind` extensions), `_stamp_activity` and both calls, the `last_activity` field and the `apply_config` rebinding are removed; `_await_inactivity` is `_await_prompt` and `_INACTIVITY` is `_PROMPT_WAIT`, with rewritten docstrings and comments; the `_V3_SESSION_STALE_SECONDS` comment rests on its own measurement; every comment describing the silence bound is reworded. D26: `cancel()` writes `session/cancel`, then records `meta["stop_requested_at"]` and wakes the wait through `_prompt_wakers`; `_await_prompt` races one shielded future against the wake event, re-reads the Stop each pass and caps each slice at the remaining grace; after `CANCEL_GRACE_SECONDS` with no answer it logs a WARNING ("did not answer Stop") and raises the D26 `AgentTimeout`, writing no second cancel; `clear_stop` runs at the start of a prompt and of a slash command, right after `inflight.add`. `config.py`: the field is gone and the name is in `_LEGACY_KEYS`. `web.py` and `index.html`: the key leaves the settings payload, type, bound and restart lists and the restart label. `README.md`: config entry removed, the turn-bound bullet and the permission-section sentence rewritten. Tests: silence-premise and `last_activity` tests removed or reduced to their `last_used` half; `TestAcpInactivityCeiling` renamed `TestAcpPromptWait`; new `test_a_quiet_turn_is_never_cancelled_for_silence`, the ceiling test asserting no early cancel, `TestAcpStopFallback` (ignored Stop, honoured Stop, next turn, slash command) and the legacy-key load-and-drop test. Before the change, the quiet-turn test failed ("The agent went silent for 0s…"), the ignored-Stop test ended at 3.21 s against a 1.2 s bound, and the slash-command test at 2.79 s. pytest 3376 passed, 1 skipped; `tests/test_config.py` 113 passed; node 1110 passed.

Implementation (2026-10-13, code: ca988d8)
Review fixes. `clear_stop` clears `stop_requested_at` and advances `meta["turn_seq"]`; `cancel()` reads it before the write and records the Stop only if unchanged after (else an INFO line). The Stop is recorded with `setdefault`, so a second Stop keeps the first deadline. The README bullet says the agent's own turn may carry on and its output may still arrive. New tests: a late cancel write does not end the next turn; a second Stop does not push the fallback back (both failed on d2fbc36); waker cleanup asserted after every Stop and prompt-wait scenario. pytest 3378 passed, 1 skipped.

Implementation (2026-10-13, code: fa49c87)
Re-review fixes. `cancel()` returns a bool; when the Stop is dropped (turn moved on, or record gone), `_handle_cancel` returns before its cascade, so a late Stop no longer cancels the next turn's approvals or stops its workflow steps. `turn_seq` comes from a supervisor-wide counter, so a rebuilt record never repeats a value. The late-write helper now opens an approval and a workflow step on turn 2 and asserts both survive; a new test replaces the record between turns. One test stub's return changed from None to True for the new contract. pytest 3379 passed, 1 skipped; node 1110 passed.

Orchestrator notes: the change descriptions above are condensed from the sub-agent returns. The live criterion is deferred to Phase 7 and added to its exit criteria. QA carried to Phase 7 with the batched restart.

### Phase 7: Documentation, roadmap and live QA
**Goal**: Record the protocol facts and the user-visible surface, then verify everything live.

**File scope**: `README.md`, `docs/KNOWLEDGE.md`, `plans/ROADMAP.md`, `plans/tests/260701_POWERATLAS.md`, `AGENTS.md` (only if the Terminology proposal is saved), this plan.

**Covers**: SC-8

1. Write the documentation updates in §8.
2. Restart PowerAtlas (granted for this task, 2026-10-12) via `POST /api/restart` and wait for a fresh "Server ready" line.
3. Run the live checks carried from Phases 3-6, including a session left with an open approval for longer than 30 minutes that is still waiting with its card answerable (start it first, run the other checks meanwhile), plus a Manual-mode approval (`POST /api/acp-permissions {"mode":"manual"}`, restored and verified afterwards). Follow `AGENTS.md` § Verification Setup: sign in through `/local-auth?code=<code>`, use one script per call, and close and delete the test sessions after the `createdAt` check.

**Exit criteria**:
- [x] `README.md` updated: the "Needs attention" row lists approvals, questions and step questions and the `(N)` title count; the `/acp` feature list mentions question cards with free-text answers
- [x] `docs/KNOWLEDGE.md` updated: a `user_input` / `_kiro/userInput` section (request shape, the capability, reply rules, Phase 1 results); `_kiro/session/notify` shapes for both senders; the paused run (`node_paused`, `paused`, `run_complete` `status: "paused"`) and the probe-3 resume result; the "`_workflow_outcome` … revise when a real failure is captured" note updated for `paused`
- [x] `plans/tests/260701_POWERATLAS.md` gains the `GET /api/dashboard/attention` section after the Overview summary section
- [x] `plans/ROADMAP.md` updated (Low item on resuming after `node_paused` and the Layer 3/4 handled-frames sentence included): the rows "`_note_subagent_action` filters its crew frame with the wrong map", "Approvals and questions in 'Needs attention'" and "`_kiro/session/notify` has no handler" removed from the ranking and body (anchor by those titles), and the ranking renumbered
- [x] `pytest tests/test_web.py --timeout=300` and `node tests/acp_page.test.mjs` pass
- [x] Every live check of SC-8 passes in a real browser, with the evidence (tool calls, screenshots) cited in §9; or a check is recorded BLOCKED with its reason
- [x] (deferred from Phase 3) Live: a quick-spec question answered from a card option, and a free-form question answered with free text, on `/acp` (file scope: no file; a live check)
- [x] (deferred from Phase 4) Live: a workflow step question shows on the row, in the transcript and as Waiting; after the answer the row runs again and finishes; a reload or `session/load` shows one step-message row, not two (file scope: no file; a live check)
- [x] (deferred from Phase 4) `docs/KNOWLEDGE.md` notes that a step past `_WORKFLOW_CHILD_MAX_AGE_S` that resumes through its own `tool_call` keeps its old staleness clock, so a step that waited more than 600 s reads "no longer reporting" at the next reap (user decision 2026-10-12: accept)
- [x] (deferred from Phase 5) Live: a Manual-mode approval and a question appear in "Needs attention" with the title count, open their session on click, and clear within one poll of the answer (file scope: no file; a live check)
- [x] (deferred from Phase 6) Live: the settings menu no longer lists the silence setting (file scope: no file; a live check)

Implementation (2026-10-13, code: 74bbbea)
Documentation half. `README.md`: the Overview *Needs attention* paragraph gains the *Waiting for you* block (approval, question and step question rows, click behaviour, about 5 s clearing, the `(N)` title count and its every-mode poll, no count on `/acp`); the clarifying-question item now covers option buttons with descriptions, the Recommended badge, free text, the trailing "Skip questions", a question waiting in every mode, the Needs attention row and Stop; a new paragraph explains workflow step questions. `docs/KNOWLEDGE.md`: the `_kiro/session/notify` shapes and handling, replayed step messages, `paused` and `node_paused` measured, a "Paused runs and their resume" block, the rewritten *Waiting.* and *Lifetime* bullets (resume rule, one-shot reopen, the skips for steps awaiting a person, the age-cap note deferred from Phase 4), the workflow-step approvals entry (`kind`, `text`, `created`; no reap or timeout cancels; the sweeper hold; the exits), and a new "Agent questions: `user_input` and `_kiro/userInput`" section with the probe results, Stop's local fallback and the refused `commands/execute`. `plans/ROADMAP.md`: the three items removed by title, the resume Low item removed (its residual moved into the Layers 3/4 limits), the handled-frames sentence updated, the ranking renumbered, a new item for slash commands on kiro-cli 2.29.1, and stale silence-ceiling text in the unattended-permissions item fixed. `plans/tests/260701_POWERATLAS.md`: new section 2.30 for `GET /api/dashboard/attention` and the range references widened.

Orchestrator notes: the description above is condensed from the sub-agent return. Order change: the final `/qdev` Step 9 review runs before this phase's restart, so a single granted restart serves both the live checks here and Step 9b's live QA (each restart ends every live ACP session). The doc changes are reviewed in Step 9 rather than per phase.

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
| R11 No silence bound on a turn or slash command (D24) | A turn parked on an unanswered approval or question holds its session slot until answered or stopped, and a remote device can do that on purpose (bounded by `MAX_SESSIONS`). A live agent that hangs is ended only by Stop or the 24 h ceiling | Accepted by the user, 2026-10-12. Stop ends the turn locally within `CANCEL_GRACE_SECONDS` + 1 s if kiro-cli does not answer the cancel (D26); any viewer's Stop releases a parked turn; the agent's own turn may keep running after a locally ended turn (it ignored the cancel), so its frames may still arrive, and its tool processes may outlive it until PowerAtlas exits, as with the ceiling (Phase 6 review). Phase 1 probe 5 measures the command case |
| R12 A paused run's resume moves the question to a new child | The old step question would be dropped with the old child | Phase 1 probe 3 decides; Phase 4 moves an open step question to the new child of the same `(workflowId, nodeId)` if probe 3 shows one |
| R13 A step that dies silently keeps its open approval or question (D25) | The item stays in "Needs attention", the parent reads Waiting, and the session holds a slot (bounded by `MAX_SESSIONS`) with no automatic exit | Accepted with D25. The exits are answer, Stop, close and a restart. The idle sweep is no longer an exit: by the user's decision of 2026-10-12 ("A", after a Light council, 3-0), `_sweepable` refuses a session awaiting a person (Phase 3 review). KNOWLEDGE states the exits (Phase 7) |
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
| 2 | Crew-frame filter fix | Complete | code: 68b5f8e, 3ce782e |
| 3 | Agent questions through `userInput` | Complete | code: 82e0119, 6223be4, b0cabe5, ad12a39; live check deferred to Phase 7 |
| 4 | Step messages, waiting state, paused runs | Complete | code: f6d4d71, cdfa879, 25fd7bc; live check deferred to Phase 7 |
| 5 | Attention feed and title count | Complete | code: 71033de, b13bcd0; live check deferred to Phase 7 |
| 6 | Remove the prompt silence watchdog | Complete | code: d2fbc36 (subject wrongly says phase 4), ca988d8, fa49c87; live check deferred to Phase 7 |
| 7 | Documentation, roadmap and live QA | Complete | docs: 74bbbea; live QA PASS after the Step 9 review (see §9 "Live QA") |

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
- **A step paused after `success` (D13, D14).** In the probe-3 resume, the step reported `success`, and kiro-cli then sent `node_paused` (`reason: "Awaiting next user message on step session."`) and `run_complete` `status: "paused"`, with no `node_complete`. User decision, 2026-10-12 ("A"): keep D14 as written. The step shows waiting, the crew header reads "Paused", and the parent reads Waiting, because kiro-cli itself holds the run unfinished. No step question is created, since that needs a warning notify, so it does not appear in "Needs attention".

### Live QA (Phase 7 and Step 9b, 2026-10-13)

PowerAtlas was restarted once under the task's grant (`POST /api/restart`, fresh "Server ready" 3.2 s later). Smoke: `GET /api/dashboard/attention` 200 with `no-store` and `{"items":[],"count":0}`, 403 without the cookie; the settings payload has no `acp_prompt_silence_seconds`; permission mode `yolo`. A QA sub-agent then drove the instance with standalone Playwright from the venv (one script per call, sessions in a scratch workspace), with the session ids redacted from every record. Final suites at 611a10d: `tests/test_web.py` 3397 passed, 1 skipped; node 1112 passed.

- **Quick-spec question, option answer: PASS.** A quick-spec session's question arrived as a card with the question, five option buttons with descriptions, a trailing "Skip questions" option and a free-text box whose Send stays disabled while blank. Clicking an option disabled the card; the log shows `interactionResolved` `outcome: "answered"` with the chosen title, and the next question followed from the choice. No question this run marked an option `recommended`, so the badge was not seen live.
- **Free-text answer: PASS.** Typed text enabled Send; sending disabled the card; the log shows the typed text as the answer and the agent went on.
- **Stop with a question open: PASS.** The turn ended 0.07 s after Stop (`stopReason=cancelled`), the card went inactive, and kiro-cli logged `interactionResolved` `outcome: "cancelled"` (PowerAtlas's `cancelled` reply). A question card also replayed active after detach and reopen.
- **Needs attention for a question: PASS.** A "Waiting for you" row (Question, session title, workspace, text, age) and the title `(1) PowerAtlas`; the rail dot read waiting; the prefix stayed with a transcript open across a poll; clicking the row opened the session; after the answer the row and the prefix went within 0.5 s.
- **Workflow step question: PASS.** A step's warning `send_message` showed a warning step-message row in the transcript, the crew row waiting with the message, the header "Paused (1 agent)", a "Step question" row with the step label in its own element, the title `(1) PowerAtlas` and a waiting rail dot. Prompting the parent with the answer logged the parent-to-step notify and "resumed: running" (with the measured re-sent `run_start` and `node_start`); the row cleared within 3.5 s and the header returned to "Orchestrating". A page reload showed exactly one step-message row.
- **Settings: PASS.** The settings menu and every settings tab hold no "silence" or "ceiling" text.
- **Manual-mode approvals: PASS.** With the user's permission ("yes go ahead, you can aslo restart PA", 2026-10-13), permission mode was switched to Manual. An approval card for a shell command appeared on /acp; "Waiting for you" listed an Approval row with the title `(1) PowerAtlas`, which stayed with a transcript open across a poll; clicking the row opened the session; Allow cleared the row and the prefix within 3.5 s and the turn completed.
- **An approval held for 33 minutes: PASS.** A second approval was left unanswered with no tab open from 10:41:09. At 33 minutes `orchestrator.log` held no `session/cancel`, "went silent", release or cancelled-reply line, and the attention feed still listed it (age 1981 s). A fresh browser showed the row ("33 min ago") and the prefix; `/acp?sid=` replayed the card active with Stop enabled; Allow ran the command and the turn ended `end_turn` 4 s later, and the row cleared within 1.5 s. Permission mode was then restored to `yolo` and read back as `yolo` before and after a final restart.
- **Not run.** A `session/load` replay after close was BLOCKED: kiro-cli keeps a closed session loaded, so it cannot be reopened until the idle sweeper or a restart unloads it; the reload check covers the one-row property. The `/acp?sid=` new-tab fallback was not exercised (every test session had a rail row); page tests cover it.
- **Cleanup.** Four parent sessions closed through `#acpClose`; six session folders (four parents, two step children) deleted after the `createdAt` check. kiro-cli re-created two folders with only `messages.jsonl` while it held the sessions loaded; after the final restart both were deleted (first record inside the window), with the two Manual-check sessions, and a re-check finds none left.

### Divergences from the final review (Step 9)

- **Held step keeps its wave.** `_workflow_holds_step` keeps `_active_fan_out_wave` at turn start and turn end while a tracked step awaits a person, so its row stays in crew frames (D25). Side effect, documented in KNOWLEDGE: a sub-agent started in a later turn joins that kept wave and shows in the earlier turn's panel.
- **`sessions_with_pending_permission` deleted.** It lost its last production reader in Phase 5; §1, D3 and the Phase 3 criteria above still name it as the plan was written. `sessions_awaiting_person()` replaces it everywhere.
- **Routed question resume.** A step that its own `_kiro/userInput` question set waiting (`question_wait`) runs again once its last open request is gone, unless kiro-cli paused it since (`node_paused`, `run_complete` `paused`), which forgets the flag. A capped step keeps its old clock on its own `tool_call`.
- **Step-question toast (user option A, 2026-10-13).** A warning notify that opens a step question raises "Question: [workflow step] <text>" through `notify_question`, whenever notifications are on. A reopened question and a replayed chunk raise none. D9 named only `question_request`.
- **Fault-log back-off.** `_workflow_state_fault` logs a traceback once, then at most one line per minute per key; `_workflow_state_recovered` clears both records on a good read. Two existing log expectations changed to match.
- **Attention poller on failure.** A 403 or three failed polls in a row clear the rows and the `(N)` count.
- **Step label element.** A "Waiting for you" row shows the step label in its own element and the full text as the tooltip.

### Divergences from Phase 6

- **Wrong subject on d2fbc36.** The Phase 6 code commit carries the Phase 4 message: a refused scratch-file Write left an earlier phase's message file in place for `git commit -F`. Amend is banned; user decision "B" (2026-10-13): leave it and record it here. ca988d8 and fa49c87 carry correct Phase 6 subjects.
- **Wait shape.** `_await_prompt` creates one shield before its loop and races it each pass against the Stop event with `asyncio.wait`, instead of `wait_for(shield(fut), tick)`. The shield's property holds: the future is never cancelled and callbacks do not pile up.
- **Stop pinned to its turn.** `cancel()` records the Stop after the `session/cancel` write and only if the supervisor-wide `turn_seq` is unchanged, so a late write never ends or touches the next turn, and no turn ends on a cancel the agent never received. A dropped Stop skips the whole Stop cascade. A second Stop keeps the first deadline. Wake events live in `_prompt_wakers`, so the session record stays plain data.
- **Tests reduced, not deleted.** Some `TestAcpActivityStamp` tests keep their `last_used` half, since only `last_activity` went away.
- **Remaining `silence` hits in `acp.py`, none about a turn bound.** The base64 decode comment ("would reproduce the same silence one layer earlier"); a Manual row pattern that can "silence" a permission card (two hits); a socket writer that fails "in silence"; the load-pending ceiling drifting "in silence".
- **Other first-grep hits.** `config.py`'s `_LEGACY_KEYS` comment, and `last_activity` in `index.html`, `tests/acp_page.test.mjs` and the overview tests, all reading `overview.py`'s unrelated output field.

### Divergences from Phase 5

- **One validity rule for the list and the override.** `sessions_awaiting_person` and `attention_snapshot` share one rule: an entry counts when it has a string owner; a step question counts while its child is tracked. Entries with odd text or time are listed rather than skipped, because `sessions_awaiting_person` also guards the idle sweeper and any entry with an owner is a request kiro-cli still waits on. A step-question record whose child is no longer tracked no longer holds its parent from the sweep; no request sits behind it, and dropping the child removes it (D11).
- **Past the 6 h bound (deferred from Phase 4).** A step question stays listed and keeps the parent Waiting, by membership in `_workflow_children[parent]`, so the list, the override and the rail agree.
- **Poller on 403.** The attention poller uses its own fetch and stops quietly on a 403, clearing the count; the Overview's own requests still report a sign-out.
- **Override and `errored`.** The waiting override lifts working and idle to waiting and leaves `errored` unchanged, matching `_workflow_overlay_status`.
- **Held owners only.** The snapshot leaves out entries whose owner the supervisor does not hold, matching the override.
- **Server `age`.** Each item carries `age` in whole seconds; the client shows `age` plus the time since the poll arrived, and keeps it out of the re-render key.

### Divergences from Phase 4

- **One resume rule (D13).** A waiting step goes running only when it has no open approval or question entry (`_workflow_step_awaits_person`), on the parent-answer, `tool_call` and re-sent `node_start` paths. D13 named only a pending question entry; review finding 7 asked for one rule. An open approval therefore keeps a waiting step waiting after the parent answers.
- **Re-sent `node_start`.** A resume re-sends `node_start` for the tracked step (probe 3); it sets a waiting step running only when nothing is open, and never drops an open step question.
- **Waiting row keeps its message.** `node_paused` and `run_complete` `paused` keep the action of an already-open waiting row (the step question's message) instead of writing "waiting".
- **Replayed step label.** Replayed `step_message` frames name the step "workflow step <name>", like the live path, where plan step 4 said the bare `_clean_step_label` name; D12 asks that a replay match the live frame.
- **Question reopen after a step `tool_call`.** A step `tool_call` after its warning removes the step question (D13). A following `node_paused` with the send_message reason reopens it once from the remembered warning, so SC-5's question text survives.
- **Age cap.** Only a step's own `tool_call` resume respects `_WORKFLOW_CHILD_MAX_AGE_S`; the parent-answer resume, entering waiting and the paused branch still refresh the clock, so the 6 h waiting bound holds. Consequence accepted by the user (2026-10-12, "OK for reco"): a step past the cap that waited more than 600 s and resumes through its own `tool_call` reads "no longer reporting" at the next reap. Phase 7 documents it.
- **Dashboard history replay.** `renderTranscriptFrame` now also renders `step_message`, `permission_request`, `question_request` and `permission_resolved`, so cards and step rows survive a dashboard reconnect; `/acp` replays through its own dispatch and never reaches these cases.
- **Foreign parent notifies.** A parent-to-step notify whose `callerSessionId` is not the step's parent is ignored; sender and severity are lower-cased before comparison.

### Divergences from Phase 3

- **Turn-end loop skips a waiting step.** `_handle_prompt`'s turn-end loop also skips a tracked step with an open approval or question, a site D25 does not name. Without it the row reads done while the entry survives, which breaks D25's truthful-row property. Both reviewers judged it sound.
- **`_workflow_step_questions` created early.** It is created empty and cleared in `_detach` in Phase 3 so the shared checks can read it; Phase 4 writes it and adds its other D11 removal paths (`_workflow_drop_child`, `_workflow_drop_parent`, `_workflow_step_resume`).
- **`sessions_awaiting_person()` created early.** Phase 5 step 5 plans it; it exists from Phase 3 because the idle-sweeper guard needs the same set. Phase 5 reuses it for the waiting override.
- **Over-long answers and non-list options.** A free-text answer over 4000 characters after stripping is refused `bad_payload` (D6 names no code). A non-list `options` value is treated as a free-form question and logged.
- **Idle sweeper guard (user decision "A", 2026-10-12).** The Phase 3 reliability review reproduced that the idle sweeper could still cancel a tracked step's open request once the step went stale. After a Light council (3-0 for A), the user chose to make `_sweepable` refuse a session awaiting a person. R13 is updated: the idle sweep is no longer an exit. A read failure holds every session for that tick.
- **Approval card title cleaned.** The plan's Phase 3 test bullet asks that U+202E and U+0000 in an approval title never reach the card, while step 4 said the frame title is unchanged. The frame title now drops category-C characters only, keeping line breaks and tabs, so the card looks as before.
- **Refusals carry `requestId`.** Every `question_response` refusal adds `requestId`, so a page reopens exactly the refused card; other error consumers ignore the extra key.
- **Crew over the cap.** `_evict_finished_subagents` can leave a crew over `MAX_SUBAGENTS_PER_SESSION` while steps wait on a person, bounded by the open requests; a WARNING is logged once per parent.

### Phase 2 pre-fix failure

`TestAcpSubagentActivity::test_action_frame_keeps_a_wave_tagged_row_under_its_own_wave`, run on the unfixed `_note_subagent_action` (2026-10-12), failed at its first payload check: `assert [r["sessionId"] for r in payload["subagents"]] == ["sub-new"]` gave `AssertionError: assert [] == ['sub-new']`. The frame was emitted but under the v2 anchor, so the wave-tagged row was filtered out of it. The companion v2 test passed before and after the fix. The wave test also checks D16's older-wave case (the row goes out under its own older wave id), which goes beyond the plan's step list; no divergence from D16.

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

### 2026-10-12 -- Implementation Review (after Phase 2, persona: Senior engineer)

Implementation health: Green.
2 findings (0 High, 0 Medium, 2 Low).

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | Low | SC-7 asks for a test that drives a `tool_call_chunk`, but the wave test called `_note_subagent_action` directly. | Fixed -- the wave test drives the chunk through `_on_notification` (3ce782e); a mutation check fails it. |
| 2 | Low | An untagged crew entry now gets the no-anchor `toolCallId` instead of the parent's v2 anchor; only old in-memory state has one. | Fixed -- a comment in `_note_subagent_action` states the fallback (3ce782e). |

The reviewer ran both mutations (the old `crew_spawn_toolcallids` argument and the rejected `_active_fan_out_wave`); each failed the tests, and the file was restored byte for byte. qvalidate `--phase 2 --expect-ticked 2` passed. Cycle 2 skipped under the user's one-cycle-per-phase cadence; both findings were Low. Cycle cap, recorded once for this plan: the default is 2 per phase (`/qdev` Step 6); 1 is applied per the user memory entry "/qdev review cadence: one review cycle per phase; only the Step 9 holistic review may run more".

### 2026-10-12 -- Implementation Review (after Phase 3, persona: Security auditor, Reliability engineer)

Implementation health: Green.
11 findings after merging (1 High, 3 Medium, 7 Low), plus 4 Low from a diff-only re-review of the fixes.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | [Reliability] The idle sweeper could cancel a tracked step's open request once the step went stale, breaking D25 and SC-9. | Fixed -- user chose A after a Light council; `_sweepable` refuses sessions awaiting a person; regression tests (b0cabe5). |
| 2 | Medium | A refused answer left the question card disabled while the question stayed pending; only a reload recovered it. | Fixed -- refusals carry `requestId`, pages reopen that card, `maxLength` 4000, strip-matched blank check (6223be4). |
| 3 | Medium | [Reliability] No test pinned the D25 skip in `_evict_finished_subagents`; its removal passed every test. | Fixed -- crew-cap eviction test; the mutation now fails it (6223be4). |
| 4 | Medium | [Reliability] No test pinned `_handle_question_response`'s `AcpError` to `_discard` path. | Fixed -- mirrored the permission-route test; the mutation now fails it (6223be4). |
| 5 | Low | [Security] The approval card showed the raw title with bidi and zero-width characters, against the Phase 3 test bullet. | Fixed -- frame title drops category-C characters, keeping line breaks (6223be4, b0cabe5). |
| 6 | Low | Eviction can leave a crew over `MAX_SUBAGENTS_PER_SESSION` while steps wait on a person. | Fixed -- one WARNING per parent logs it; the bound is the open requests (6223be4). |
| 7 | Low | [Reliability] The reap and turn-end skips, dropped options and non-list options were not logged. | Fixed -- INFO logs added (6223be4). |
| 8 | Low | [Reliability] Phase 4 must clear step questions in `_workflow_drop_parent` and `_workflow_step_resume`. | Fixed -- recorded in §9 "Divergences from Phase 3" for Phase 4. |
| 9 | Low | Fix 5's cleaning collapsed newlines, so a multi-line command showed on one line (orchestrator-observed side effect). | Fixed -- `_clean_agent_lines` keeps line breaks and tabs for the frame title (b0cabe5). |
| 10 | Low | [Re-review] The sweep's fail-closed branch was untested, logged a traceback every tick and mislabelled holds. | Fixed -- test, rate-limited log, no hold line on the fallback (ad12a39). |
| 11 | Low | [Re-review] A stale comment in `_on_permission_request` said the frame title is not cleaned. | Fixed -- comment reworded (ad12a39). |
| 12 | Low | [Re-review] `lastAwaitingQuestionId` was never cleared on resolution, so an id-less refusal could briefly reopen a card. | Fixed -- cleared in `markPermissionResolved`; page check (ad12a39). |
| 13 | Low | [Re-review] `held_logged` was never cleared, so a second hold of a step was not logged. | Fixed -- `_workflow_unhold` clears it at the four removal sites (ad12a39). |

Reviewers ran sequentially because both mutation-tested. Security: six mutations killed (D4 default, ownership, strict kind, boolean index, permission-route kind guard, cleaner). Reliability: eight mutations, two survived (findings 3 and 4, now fixed); full suites green. The four implementer divergences were judged sound by both. Because the fixes added substantial code after the one-cycle review (the sweeper guard, the card reopen), a diff-only re-review of 82e0119..b0cabe5 ran under `/qdev` Step 6 item 4a; it found no regressions and the four Lows in rows 10-13. Raw per-persona summaries are kept in the session scratchpad (`review_p3`).

### 2026-10-12 -- Implementation Review (after Phase 4, persona: Reliability engineer, Senior engineer)

Implementation health: Green.
12 findings (0 High, 2 Medium, 10 Low), including 3 Low from a diff-only re-review of the fixes.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | Medium | [Reliability] A step `tool_call` after its warning removed the step question before `node_paused`, losing the text (SC-5). | Fixed -- remembered warning reopens the question on a send_message pause, one-shot (cdfa879, 25fd7bc). |
| 2 | Medium | [Senior] The dashboard's history replay dropped `step_message` (and the cards), so they never survived a reconnect. | Fixed -- `renderTranscriptFrame` renders the four frame types; page checks (cdfa879). |
| 3 | Low | [Reliability] The paused branch's index-only guard was untested. | Fixed -- test seeds an index-only child (cdfa879). |
| 4 | Low | [Reliability] Resume and paused paths refresh a step's clock past `_WORKFLOW_CHILD_MAX_AGE_S`. | Fixed -- a step's own `tool_call` resume now respects the cap; other paths documented (cdfa879). |
| 5 | Low | [Reliability] Past the 6 h bound, a step question outlives `workflow_state`, so the rail and "Needs attention" disagree. | Fixed -- deferred to Phase 5's attention and override work as an exit criterion. |
| 6 | Low | [Reliability] An empty warning message created a blank step question. | Fixed -- fallback texts "asked a question" and "sent a message" (cdfa879). |
| 7 | Low | [Senior] The dashboard sub-panel head's waiting branch was untested. | Fixed -- page check mirrors the /acp one (cdfa879). |
| 8 | Low | [Senior] The two resume paths used different rules for "waiting on a person". | Fixed -- both use `_workflow_step_awaits_person`; recorded in §9 (cdfa879). |
| 9 | Low | [Senior] `_workflow_step_label`'s docstring named only the approval card. | Fixed -- docstring covers all uses (cdfa879). |
| 10 | Low | [Re-review] A remembered warning could reopen old text after a later send_message pause. | Fixed -- the reopen clears the record (25fd7bc). |
| 11 | Low | [Re-review] A step past the age cap that waited over 600 s reads "no longer reporting" right after its own `tool_call` resume. | User: accepted -- "OK for reco" (2026-10-12): the cap's intended semantics; Phase 7 documents it. |
| 12 | Low | [Re-review] No page check replayed a resolved approval card. | Fixed -- page check (25fd7bc). |

Rows 1-9 come from the two persona reviews (run sequentially for mutation testing; Reliability 6 of 7 mutations killed, Senior 4 of 5 page mutations killed). The re-review of cdfa879 ran under `/qdev` Step 6 item 4a because the fixes added the replay cases and the reopen. Row 11 is the user turn "OK for reco" answering the A/B question on 2026-10-12. Full pytest over `tests/`: 5382 passed, 3 skipped (Senior reviewer); final `tests/test_web.py` 3363 passed, 1 skipped; node 1102 passed.

### 2026-10-13 -- Implementation Review (after Phase 5, persona: Security auditor, Senior engineer)

Implementation health: Green.
5 findings (0 High, 1 Medium, 4 Low).

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | Medium | [Senior] No test pinned the waiting override on the flat listing or `/api/dashboard/sessions`; removing it stayed green. | Fixed -- parametrized override test over both modes and routes; each call-site mutation now fails (b13bcd0). |
| 2 | Low | [Security] Two new failure logs wrote a traceback on every request while the supervisor read failed. | Fixed -- both log through `_workflow_state_fault` (b13bcd0). |
| 3 | Low | [Senior] The server `age` field was never read by the client. | Fixed -- the client shows `age` plus time since receipt, removing clock skew (b13bcd0). |
| 4 | Low | [Senior] A comment in `_on_permission_request` named the old overlay read. | Fixed -- comment names `sessions_awaiting_person()` (b13bcd0). |
| 5 | Low | [Senior] The override and the list used different validity checks, so a session could read Waiting with nothing listed. | Fixed -- one shared rule for both readers; recorded in §9 (b13bcd0). |

Security ran five mutations (remote gate, cookie gate, stored and fallback title cleaning, override), all killed; full pytest over `tests/` 5416 passed. Senior confirmed every Server, page and test bullet, mutation-tested SC-2's every-mode polling two ways (both killed), and found the two uncovered call sites. Reviewers ran sequentially. No re-review of b13bcd0 (one-cycle cap; see the implementation notes).

### 2026-10-13 -- Implementation Review (after Phase 6, persona: Reliability engineer)

Implementation health: Green.
6 findings (1 High, 1 Medium, 4 Low), including 2 from a diff-only re-review of the first fix.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | A Stop for turn N could end turn N+1 when its cancel write landed late, breaking D26 (reproduced). | Fixed -- `turn_seq` pins the Stop to its turn; regression test (ca988d8). |
| 2 | Low | Waker cleanup in `_await_prompt` had no test. | Fixed -- every Stop and prompt-wait scenario asserts no waker remains (ca988d8). |
| 3 | Low | Each extra Stop press pushed the fallback deadline back. | Fixed -- the first Stop of a turn is kept (ca988d8). |
| 4 | Low | README and R11 named only tool processes as outliving a locally ended turn. | Fixed -- both say the agent's own turn may carry on (ca988d8; R11 in the plan). |
| 5 | Medium | [Re-review] A dropped late Stop still ran its cascade, cancelling the next turn's approvals and workflow steps. | Fixed -- `cancel()` returns a bool and `_handle_cancel` stops on False; test (fa49c87). |
| 6 | Low | [Re-review] A per-record `turn_seq` could repeat after a record was rebuilt (ABA). | Fixed -- supervisor-wide counter; test (fa49c87). |

The first review ran four mutations (wake-on-Stop, both `clear_stop` sites, waker cleanup); the cleanup one survived (row 2). The re-review of ca988d8 ran under `/qdev` Step 6 item 4a because the High fix changed concurrency code; its four mutations were all killed. Full `tests/test_web.py` plus `tests/test_config.py`: 3489 passed, 1 skipped (first review).

### 2026-10-13 -- Post-Implementation Review

Overall implementation health: Green.
Personas: Senior engineer, Security auditor, Reliability engineer (cycle 1); Reliability and Senior engineer lenses in one diff-only reviewer (cycle 2).
21 findings (1 High, 2 Medium, 18 Low).
QA verification: PASS (8 checks across /acp, the dashboard, workflow steps, settings and the attention endpoint, each with at least two probe categories; see §9 "Live QA").

#### Test execution summary

| Phase | Tests | QA | Notes |
|---|---|---|---|
| 1: Pre-flight probes | not_run | SKIP | Probe phase on a second kiro-cli; no unit tests apply |
| 2: Crew-frame filter fix | pass | SKIP | Path unreachable live on kiro-cli 2.29.1; unit tests drive the real notification path |
| 3: Agent questions through `userInput` | pass | PASS | Verified live in Phase 7 |
| 4: Step messages, waiting state, paused runs | pass | PASS | Verified live in Phase 7 |
| 5: Attention feed and title count | pass | PASS | Verified live in Phase 7, Manual mode included |
| 6: Remove the prompt silence watchdog | pass | PASS | Settings checked live; a 33-minute approval was not cancelled |
| 7: Documentation, roadmap and live QA | pass | PASS | Docs committed; two restarts under the task's grant |

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | [Reliability] A held, no-longer-fresh step dropped out of crew frames at the parent's next turn boundary (reproduced). | Fixed -- the wave is kept while a tracked step awaits a person; test over three kinds (9e5fef3, 23bf2fd). |
| 2 | Medium | [Senior] `sessions_with_pending_permission` was dead code after Phase 5, and KNOWLEDGE still named it. | Fixed -- deleted; asserts and KNOWLEDGE point at `sessions_awaiting_person()` (9e5fef3, 59f38a8). |
| 3 | Medium | [Cycle 2] A routed question answered after kiro-cli paused its step set the step running. | Fixed -- pause frames forget `question_wait`; frame-order test (23bf2fd). |
| 4 | Low | [Reliability] A routed question's removal left its step waiting until its next tool call. | Fixed -- `question_wait` resume once nothing is open (9e5fef3). |
| 5 | Low | [Senior] `_on_user_input_request` duplicated the set-waiting-and-reopen logic. | Fixed -- calls `_workflow_mark_step_waiting` (9e5fef3). |
| 6 | Low | [Reliability] Three hold-tracking calls had no pinning test. | Fixed -- three tests; each deletion fails one (9e5fef3). |
| 7 | Low | [Security] Replayed step-message label cleaning was untested. | Fixed -- replay test with U+202E, NUL and brackets (9e5fef3). |
| 8 | Low | [Reliability] `_workflow_state_fault` logged one warning per failed request forever. | Fixed -- one line per minute per key after the first traceback (9e5fef3). |
| 9 | Low | [Reliability] The attention poller kept stale rows and the count through failures and after a 403. | Fixed -- cleared after a 403 or three failures (9e5fef3). |
| 10 | Low | [Security] The step label and agent text shared one span, so a text could pose as a step item. | Fixed -- separate step element, full-text tooltip (9e5fef3). |
| 11 | Low | [Senior] The Needs attention markup comment omitted the new block. | Fixed -- comment updated (9e5fef3). |
| 12 | Low | [Senior] README said the agent carries on after Stop with a question open; probe 5 shows the turn ends. | Fixed -- README reworded (59f38a8). |
| 13 | Low | [Reliability] README said a step question leaves on Stop, which holds only during a turn. | Fixed -- README lists the step-question exits (59f38a8). |
| 14 | Low | [Security] The test plan said attention text is clipped to 200; step questions clip at 512. | Fixed -- test plan corrected (59f38a8). |
| 15 | Low | [Senior] The plan's deferred residuals had no ROADMAP entry. | Fixed -- one Low ROADMAP entry covers the three residuals (59f38a8). |
| 16 | Low | [Reliability] Step questions raised no desktop toast. | Fixed -- user chose option A (2026-10-13): step questions toast (23bf2fd, 611a10d). |
| 17 | Low | [Cycle 2] A capped step's own tool call refreshed its clock through the unhold path. | Fixed -- `step_frame` threaded through (23bf2fd). |
| 18 | Low | [Cycle 2] `_workflow_state_fault_logged` grew without bound. | Fixed -- `_workflow_state_recovered` clears both dicts (23bf2fd). |
| 19 | Low | [Cycle 2] A sub-agent in a later turn joins a kept wave's earlier panel. | Fixed -- documented in KNOWLEDGE as the user directed ("fill all six now", 2026-10-13) (611a10d). |
| 20 | Low | [Cycle 2] The test plan's risks line omitted "during a turn" for Stop on step questions. | Fixed -- reworded (611a10d). |
| 21 | Low | [Cycle 2] Missing tests: routed-question wave hold, question-pause-answer order, out-of-turn Stop or close resuming. | Fixed -- three tests (23bf2fd). |

Cycle 1 ran the three personas in parallel, each mutating only a private `git archive` copy: Senior killed 7 of 7 mutations, Security 10 of 11, Reliability 12 of 15 (survivors became rows 6 and 7). Cycle 2 (`a387bfd..59f38a8`) killed 9 of 9 and found rows 3 and 17-21. The cycle cap was then reached; the user directed "fill all six now + option A" (2026-10-13) for the cycle-2 findings and the toast. Final suites at 611a10d: `tests/test_web.py` 3397 passed, 1 skipped; `tests/test_config.py` 113 passed; node 1112 passed. Raw per-persona summaries are in the session scratchpad (`review_s9`).

### 2026-10-13 -- Divergence verification (at archival)

Every entry in §9 was judged by a reviewer or decided by the user. Phase 1: the skip-option title and the "Slash commands unmeasured" entry are records only; the re-sent `run_start`/`node_start` handling was verified by the Phase 4 Reliability review; the step paused after `success` is the user's decision "A". Phase 3: both Phase 3 reviewers judged the four implementer divergences sound; the sweeper guard is the user's decision "A"; the approval-title cleaning, the refusal `requestId` and the crew-over-cap warning were review fixes checked by the diff-only re-review. Phase 4: the Reliability reviewer judged all eight declared divergences sound; the reopen, the one resume rule and the dashboard replay were checked by the diff-only re-review; the age-cap consequence is the user's "OK for reco". Phase 5: the Security reviewer judged each declared divergence acceptable; the shared validity rule was checked by the Step 9 Senior engineer review. Phase 6: the wrong subject on d2fbc36 is the user's decision "B"; the Reliability reviewer judged the wait shape and the reduced tests sound, and the Stop pinning was checked by the re-review. Step 9: the cycle-2 review checked the kept wave, `question_wait` and the poller; the toast is the user's option A. The Phase 7 doc changes were checked by the Step 9 Senior engineer review.

## Harness Improvement Opportunities

- Step 1.5's three code-tracing briefs overlapped heavily on this scope: all three enumerated the same `_emit_subagents_frame` call sites and pending-permission removal paths — cost: about 630k sub-agent tokens for roughly one report's worth of distinct facts — suggested change: when Step 1 already names the call sites, tell the subsystem brief to skip enumerations the directed brief owns.
- The decisive findings (the `user_input` question path, the paused-run mapping) came from probes and a vendor-bundle grep, not from the trio — cost: the trio's "clarifying question" answer was "no mechanism found", which would have shaped the interview wrongly without the probes — suggested change: add "grep the vendor bundle for the concept" to the Probe gate's examples when the work integrates with a third-party protocol.
- The doc-impact sub-agent and the persona reviewers cover overlapping ground: three personas independently found that the card belongs in `transcript-renderer.js` — cost: three reviewers each spent about 15 tool calls on the same discovery — suggested change: have the planner list the shared modules a UI phase touches (grep the existing sibling feature's function names) before review, so reviewers check it rather than discover it.
- With a 1-cycle cap, 26 fixes went into the plan without any re-review — cost: unclear; regressions in the fixes go unchecked until `/qdev` — suggested change: when the user caps cycles at 1, offer a cheap diff-only check of the applied fixes that does not count as a cycle.
- A sub-agent's Write to a scratch commit-message file was refused because an earlier phase had left a file of the same name, and `git commit -F` then committed the stale Phase 4 message on the Phase 6 code (d2fbc36) — cost: a wrong commit subject that amend cannot fix under the ban, a user decision, and a commit-pairing gap — suggested change: `/qdev` briefs require a per-phase unique message file name, or `-m`, and a `git log -1 --format=%s` check right after the commit.
