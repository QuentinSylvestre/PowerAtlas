# ACP Turn-End and Permission-Needed Notifications

> **Date**: 2026-09-19
> **Status**: In Progress — implemented and unit-verified; runtime verification pending a user-initiated restart  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Scope**: Tell the operator, outside the page, that an ACP-hosted turn finished or is blocked on their approval.

---

## Implementation

Implemented directly (user chose `/qexplore` → direct implementation, no `/qplan` stage).
Code: `3f47642`.

**Verified**: 1450 pytest, 450 `acp_page.test.mjs`, 439 other suites, `_check_test_names` clean.
The four new behavioural assertions were mutation-checked — each fails with its production line
disabled, so none is vacuous. The repaired Windows toast was verified against a live PowerShell
child: both text slots populate, zero errors, and a `$(...)` payload in either field renders as
literal text rather than executing.

**Not yet verified, and why**: the server half cannot be exercised without restarting PowerAtlas,
which project `AGENTS.md` forbids doing autonomously. Template changes are picked up by a hard
reload, but the `/api/notifications` route they call is Python and does not exist in the running
process. Outstanding runtime checks: toast fires on a real unwatched turn end; browser notification
fires on a real backgrounded tab; neither re-fires on reload.

### Deviations from the exploration's recommended approach

- `_fire_toast`'s signature became `(title, body)` rather than keeping the status-mapping shape.
  Its callers now pass an explicit body, since "turn ended" and "needs approval" are not statuses.
- Caller text moved out of the PowerShell script body into the child's environment. Not in the
  original plan: found while repairing the template that `html.escape` does not cover `$` or the
  backtick, and the body now carries agent-authored `toolCall.title`, so the text was executable.
- Both topbar toggles gained explicit ids and `refreshSettings` stopped using
  `querySelector('.topbar-toggle')`. Adding a second such row would otherwise have made a
  documented failure mode live (project memory: "ACP button label shows stale value on first load
  when refreshSettings() queries by class instead of id").

### Reported, not fixed

`web._status_matches` and its `_LIVE_STATUSES` constant are dead by the same measure as the
approved cluster (zero production callers; they filtered the removed server-rendered cards). They
were **not** in the approved deletion list, so they are left in place and reported rather than
silently swept up.

---

## Intent

### Problem statement & desired outcomes

PowerAtlas hosts kiro-cli sessions over ACP and renders everything about them inside `/acp`.
Nothing reaches the operator when that page is not in front of them. A turn that finishes,
and a `session/request_permission` that blocks a turn waiting for a human, are both invisible
until someone looks. The permission case is the worse half: the session stalls until
`acp_prompt_silence_seconds` cancels the turn, so the cost of not noticing is a wasted slot
and a silent non-result.

The infrastructure was believed to exist and merely lack a caller. Exploration established
that it is worse than that on both halves:

- **The OS toast has never worked.** `_fire_windows_toast` builds its payload from
  `GetTemplateContent(0)` (`ToastImageAndText01`), measured to have exactly **one** text slot,
  then writes two. The second append raises inside PowerShell, which leaves the exit code at
  `0`; `notifications.py:114` routes stderr to `DEVNULL`; and the `except Exception` guards
  only process *spawn*. So the body — the only thing distinguishing "done" from "hit an error"
  — is dropped, and every observable signal reports success.
- **The firing path is dead three times over.** `check_and_notify` is called only from
  `_session_status`, which has zero production callers; `mark_initialized()` has zero callers
  anywhere, so `_initialized` is permanently `False`; and `config.notifications` is read by
  nothing and writable by no HTTP route.

Desired outcome: a turn ending or blocking on approval reaches the operator on the machine
running PowerAtlas, through whichever surface can actually observe their absence, with the
toggle discoverable in the UI.

### Success criteria

1. A turn ending on a session with **no attached socket** fires an OS toast naming the
   workspace and whether the turn completed or errored.
2. A `session/request_permission` fires a notification **always**, watched or not — it blocks
   the turn either way.
3. A mid-turn `display_error` fires a notification on an unwatched session.
4. A turn ending or a permission request on a session whose tab is **attached but hidden**
   fires a browser `Notification` from `acp.html`; a **visible** tab fires nothing.
5. Neither surface fires twice for one event, including across a page reload or a WebSocket
   reconnect that replays history.
6. The OS toast renders **both** title and body.
7. `config.notifications.enabled` is readable and writable from the UI, takes effect without a
   restart, and defaults to off.
8. The provably-dead status/notification cluster is removed, not left as a decoy.

### Scope boundaries & non-goals

**In scope**: `notifications.py` (template repair + event-shaped API, dead-machinery removal),
`acp.py` (a `web.py`-injected notify hook at three fire points, permission-title clamp),
`web.py` (hook consumer + policy + a dedicated settings route, dead-cluster removal),
`acp.html` (browser `Notification` on the hidden-tab branch), `index.html` (the toggle),
`tests/test_web.py`.

**Explicit non-goals**:

- **Remote / phone notification.** Measured foreclosed, not merely degraded — see Discovery
  item 5, risk R1. Reopens only if TLS lands on the remote bind.
- **Cross-session notification from the client.** A tab's WebSocket carries frames only for the
  session it subscribes to, so the client half covers the open session only. The rail's
  existing poll-based `markUnread` (`acp.html:2903-2919`) stays the sole cross-session observer
  and is not unified with this work.
- **A "needs you" inbox**, Web Push, service workers, and any change to the remote bind's TLS
  posture — all separate roadmap items.
- Reviving the poll-shaped transition/cooldown model in any form.

---

## Exploration Discovery

<!-- Transient: /qplan folds these into the planning sections and removes this section. -->

### 4. Existing patterns & constraints

Step 1.5 dispatched the **code-tracing trio** (directed / subsystem / mutation-finder) on
Sonnet 5 — in-scope files are predominantly `.py` plus `acp.html`'s inline script.

- **`acp.py`'s isolation boundary is in force.** Its guarded intra-package imports are exactly
  `from .config import CONFIG_DIR` (`acp.py:82`) and `from .launcher import _SESSION_ID_RE`
  (`acp.py:83`). It may not import `notifications`. The sanctioned path is a `web.py`-injected
  hook, precedent `sessions_changed_hook` / `set_sessions_changed_hook` (`acp.py:861-879`),
  wired at `web.py:650` under an `if acp is not None:` guard. **No standing test enforces this
  invariant** — only a manual grep from a prior plan's close, so it must be re-verified by hand.
- **Hook call convention**: `_publish_live` (`acp.py:4771-4782`) guards `if hook is None`, then
  wraps the call in `try/except Exception: log.exception(...)` so a consumer fault never
  escapes into the producer. Test seam: `tests/test_web.py:3968-4030` saves the module
  attribute, replaces it, and restores in `finally` — the new hook must be a plain module
  attribute for the same pattern to work.
- **One turn-end fire point, covering every ending.** `_handle_prompt`'s `finally`
  (`acp.py:5728-5775`) is reached by normal completion, `AcpError`, generic exception,
  silence-timeout `AgentTimeout`, and user cancel alike. Its final statement is the
  `meta {turn:"end", stopReason}` emit at `5774`. The `finally` contains **no `await`**, so it
  cannot be preempted mid-execution.
- **Both fire points run on the event loop.** The reader thread hands messages over via
  `loop.call_soon_threadsafe` (`acp.py:3186-3198`), so `_on_permission_request` executes on the
  loop, synchronously, with no `await` — reads of `_registry.subscribers` there are a
  consistent snapshot, not a race.
- **`inflight` is already cleared** by the time the turn-end emit runs (added `5691`, discarded
  `5729`) — do not gate new turn-end logic on it.
- **Toast seam convention**: `tests/test_web.py::TestNotifications` patches `_fire_toast`, never
  the platform-specific `_fire_windows_toast` / `_fire_linux_notify`. New code routes through
  `_fire_toast` to stay patchable.
- **Settings**: `GET /api/settings` (`web.py:3525`) reads live per request; a named
  `_RESTART_TO_APPLY` set (`web.py:3845`) is snapshotted at startup and surfaced to the UI as
  "restart to apply". `notifications` belongs in neither — it should read live.
- **`/api/autostart`** (`web.py:3405-3411`) is the precedent for a bodyless boolean toggle:
  read, flip, return the new state. UI side is a one-line row in the topbar settings menu
  (`index.html:55`).
- `_new_session_record` (`acp.py:2168`) carries `cwd`/`created`/`last_used`/`last_activity` —
  **no title**. `focus_update` emits a `title` frame but never caches it, so the notification
  label must derive from `cwd`.
- `permission_request`'s `toolCall.title` passes through `_as_text` with **no length clamp**,
  unlike `MAX_TITLE_CHARS`, `MAX_STEER_CHARS`, `MAX_TOOL_INPUT_CHARS` and five siblings.
- `tests/acp_page.test.mjs` is the **only** coverage of `acp.html`'s inline script, is not in
  the pytest suite, and is not run by CI (project `AGENTS.md`). Its three `vm` sandboxes
  (lines 830, 4778, 10427) stub `WebSocket` but not `Notification`. It already ships a
  `setVisibility(state)` simulator at line 1082.

### 5. Risks & mitigations

- **R1 — the browser `Notification` API is unavailable over the remote bind.** Measured on the
  live deployment: `http://127.0.0.1:4915` gives `isSecureContext: true`,
  `Notification.permission: "default"`; `http://100.78.142.124:4915` (the configured, live
  `remote_bind_address`) gives `isSecureContext: false`, `Notification.permission: "denied"`,
  and no `serviceWorker`. The permission is *hard-denied*, so no user gesture can rescue it,
  and Web Push is independently foreclosed. **Mitigation**: accept as a non-goal; the client
  guard tests `Notification.permission === 'granted'` so the remote page degrades silently
  rather than throwing.
- **R2 — `_registry.subscribers` cannot see a backgrounded tab.** It answers "is a socket
  attached", not "is a human looking", and no wire frame carries tab visibility. Left alone,
  the walked-away-with-the-tab-open case — the main one — would be suppressed as "watched".
  **Mitigation**: the three-way split (Discovery item 6, Q2), where the client covers exactly
  what the server structurally cannot observe.
- **R3 — client-side replay double-fire.** Both target branches run during replay:
  `meta turn:end` (`acp.html:5100-5152`) gates only `setTurn`/`hideThinking` behind
  `!replaying`, and `permission_request` (`acp.html:5395-5412`) is **deliberately** unguarded so
  a mid-turn reload keeps the buttons live. A naive `new Notification(...)` in either re-fires
  on every reload. **Mitigation**: an explicit `!replaying` gate on the notification call only,
  leaving the existing UI behaviour untouched. Server side needs no such guard — replay ships
  one pre-built `history` envelope (`acp.py:5183/5445/5634`) and never re-enters `_emit`
  (verified).
- **R4 — half-open WebSocket blind spot.** A dropped connection with no close frame reads as
  subscribed server-side while delivering nothing client-side, so that one turn notifies
  nowhere. Bounded by `_Connection._retire` on send-queue overflow. Accepted: it degrades to
  silence, never to a wrong notification.
- **R5 — `config.notifications` defaults trap.** Measured: a bare `[notifications]` table in
  `config.toml` loads as `{}`, **not** the dataclass default `{"enabled": False}`, because
  `load_config` passes the empty dict explicitly and dataclass defaults apply only to omitted
  kwargs. **Mitigation**: every read uses `.get("enabled", False)`; never `["enabled"]`.
- **R6 — no automated guard on the isolation boundary.** Re-verify by hand after the hook
  lands: `acp.py`'s intra-package imports must still be exactly `config` and `launcher`.
- **R7 — client half will be untested.** The `Notification` stub was declined for the mjs
  sandboxes (Q5), so the `typeof Notification !== 'undefined'` guard is permanently false in
  the harness and every client test passes regardless of correctness. Recorded as Open Item 1.

### 6. Resolved decisions

- Q1: What renders when the current toast code fires? — A: (superseded by direct measurement)
  — Decision: measured rather than asked; template 0 has one text slot, so only the title
  renders and the body is silently dropped. `CreateToastNotifier("PowerAtlas").Setting` returns
  `Enabled`, so delivery permission is not the obstacle.
- Q2: Does the three-way firing split match the intent of "unwatched"? — A: Yes — Decision:
  server fires when `not _registry.subscribers.get(sid)`; client fires when subscribed **and**
  `document.visibilityState === 'hidden'`; nothing fires when the tab is visible. Mutually
  exclusive and jointly exhaustive, so no double-notify and no new wire message.
- Q3: How wide should the `notifications.py` repair go? — A: Fix template + delete the dead
  cluster — Decision: switch to template 5 (`ToastText02`, two slots, no empty image element),
  add event-shaped entry points over the `_fire_toast` seam, and delete `_session_status`,
  `_workspace_status`, `_raise_status`, `_waiting_detail`, `_row_origin`, `_session_origin`,
  `check_and_notify`, `mark_initialized` and their supporting state.
- Q4: How should the toggle persist, given `/api/save-setting` rejects booleans
  (`web.py:3913-3915`) and `notifications` is a nested dict? — A: Dedicated route,
  autostart-style — Decision: a `GET`/`POST /api/notifications` pair mirroring
  `/api/autostart`, touching no shared validation logic; read live, so no restart.
- Q5: Which adjacent defects fold in? — A: Clamp permission title length; notify on mid-turn
  agent errors (mjs `Notification` stub **not** selected) — Decision: add a permission-title
  clamp constant and a third fire point on `display_error` (`acp.py:4104-4110`); the mjs
  sandboxes stay unstubbed and the client half ships without harness coverage (Open Item 1).

**Terminology settled this session**: *watched* = a session with at least one attached
`_Connection` in `_registry.subscribers`. Deliberately distinct from *visible* (a client-side
`document.visibilityState` property) and from *unread* (the rail's existing localStorage
marker). Rejected synonyms: "focused", "active", "foreground".

### 7. Open items

1. **The client half has no automated coverage** (deterministic). The mjs `Notification` stub
   was declined, so `tests/acp_page.test.mjs` cannot exercise the hidden-tab branch. Resolvable
   later by adding a stub to the three sandboxes and driving it with the existing
   `setVisibility()` helper.
2. **Linux `notify-send` path is untested** (deterministic). No Linux host available this
   session; the template fix is Windows-only, so the Linux path is unchanged and unverified.

### Assumptions (unconfirmed)

- Notify on `stopReason` of `end_turn` and `error`; stay silent on `cancelled` and
  `interrupted`, since the operator caused those. *(Covers: functional scope.)*
- Multi-tab duplicates coalesced with `tag: sessionId` on the browser `Notification`.
  *(Covers: edge cases.)*
- Toggle defaults off; enabling it is what requests browser `Notification` permission, using
  the click as the required user gesture. *(Covers: UX flow.)*
- Sub-agent / child sessions never notify — they are read-only and never prompted directly.
  *(Covers: functional scope.)*
- Notification label is `basename(cwd)` plus a short session-id suffix, because
  `MAX_SESSIONS` is 8 and several sessions per workspace is the normal case, which bare
  `basename(cwd)` cannot disambiguate. *(Covers: UX flow — surfaced and not vetoed.)*

### 8. Recommended approach

1. **`notifications.py`** — repair `_fire_windows_toast` to template 5, materialising the node
   list with `@(...)` and guarding the second slot; re-shape `_fire_toast(title, body)` as the
   single patchable seam; add `notify_turn_end` / `notify_permission_needed` /
   `notify_agent_error`; delete the transition table, cooldown, LRU state, `check_and_notify`
   and `mark_initialized`.
2. **`acp.py`** — add `notify_hook = None` + `set_notify_hook(...)` mirroring
   `set_sessions_changed_hook` (minus the immediate-publish, which has no analogue for discrete
   events); add one private `_notify(event, session_id, detail)` helper that resolves `cwd` and
   the `watched` boolean and calls the hook inside `try/except Exception: log.exception`; call
   it from the three fire points; clamp `toolCall.title`. **`acp.py` decides no policy** — it
   reports `watched` as a fact and lets the consumer decide.
3. **`web.py`** — implement the consumer: read `config.notifications.get("enabled", False)`
   live, apply the policy (turn-end and agent-error only when unwatched; permission always),
   build the label; register it at lifespan beside `set_sessions_changed_hook`; add
   `GET`/`POST /api/notifications`; delete the dead cluster.
4. **`acp.html`** — fetch the flag at load, add a `maybeNotify` helper guarded on
   `typeof Notification !== 'undefined' && Notification.permission === 'granted'`, and call it
   from the two branches behind `!replaying && document.visibilityState === 'hidden'`.
5. **`index.html`** — a toggle row in the topbar settings menu following the autostart
   precedent, requesting `Notification` permission on enable.
6. **Tests** — rewrite `TestNotifications` against the new API, delete the dead cluster's
   tests (15 distinct test functions, 11 of them in `TestNotifications`), add coverage for the
   hook contract and the new route.

### 9. QA environment

- PowerAtlas runs from `.venv-PowerAtlas` and is **already running** (port `4915`); per project
  `AGENTS.md` it is never restarted autonomously — Python changes need the operator to restart.
- `/acp` reachable at `http://127.0.0.1:4915/acp`; the remote bind at
  `http://100.78.142.124:4915` is live and is the origin where `Notification` is hard-denied.
- Python suite: `.venv-PowerAtlas/Scripts/python -m pytest tests/test_web.py` (add
  `--timeout=300` after session-close/concurrency changes).
- Template script suite: `node tests/acp_page.test.mjs` — not in pytest, not in CI, must be run
  by hand after any `acp.html` change.
- Toast verification is a live desktop observation; `CreateToastNotifier("PowerAtlas").Setting`
  returning `Enabled` is the programmatic precondition check.
- Duplicate-test-name pre-commit hook: `.venv-PowerAtlas/Scripts/python _check_test_names.py`.
