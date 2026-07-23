# Live Session Status Redesign

> **Date**: 2026-07-23
> **Status**: Planned  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Scope**: Redesign live session detection and status indicators with cwd-based matching and simplified 4-state vocabulary

---

## Intent

### Problem statement & desired outcomes

The live session status feature in PowerAtlas is critically broken for the primary use case. Sessions started from a terminal (`kiro-cli chat -a`) lose their status indicator after 90 seconds because the detection system requires `--resume-id` on the process cmdline — a flag only present when resuming via PowerAtlas. The semantic JSONL classifier (which correctly identifies agent state) is gated behind this cmdline matching and never fires for most sessions.

Additionally, the 6-option status vocabulary (Live/Active/Needs input/Idle/Errored/Closed) adds confusion without signal — "Live" vs "Active" are indistinguishable to users, and the grey "Idle" dot is unactionable noise.

**Desired outcomes:**
- All running kiro-cli/claude sessions show a status dot regardless of how they were started
- Status vocabulary is simplified to 3 actionable states + absence (Working/Waiting/Errored/no-dot)
- Workspace cards show at-a-glance status (highest-priority session dot)
- Status filter works correctly (no "No matching sessions" when sessions are clearly running)
- The workspace panel filter-reset ordering bug is fixed

### Success criteria

1. A `kiro-cli chat -a` session started from a terminal shows a green "Working" dot while the agent executes tools, and transitions to yellow "Waiting" when the agent finishes its turn — indefinitely, not just for 90 seconds.
2. A session resumed via PowerAtlas (with `--resume-id`) shows correct status dots (same behavior, higher-confidence detection path).
3. Workspace cards display a status dot reflecting the highest-priority session status (Errored > Waiting > Working > none).
4. The status filter dropdown offers All / Working / Waiting / Errored and correctly narrows both panels.
5. Expanding a workspace card with the "Working" or "Waiting" filter active shows the matching session rows (no "No matching sessions" when a process is running).
6. Notifications fire on Working→Waiting and Working→Errored transitions.
7. v3 kiro-cli sessions (`messages.jsonl` format) are classified correctly.
8. Switching status filters and resetting to "All" preserves pinned-first ordering and time-group headings.

### Scope boundaries & non-goals

**In scope:**
- Rewrite `_session_status()` gate to use cwd-based association (remove the `is_explicitly_live OR is_fresh` gate)
- New 4-state vocabulary: Working (green pulsing), Waiting (yellow/orange), Errored (red), no dot (closed)
- Implement `classify_kiro_v3()` for v3 session JSONL format
- Add status dot to workspace card template (highest-priority aggregation)
- Simplify status filter dropdown (All / Working / Waiting / Errored)
- Fix `refreshCards()` workspace panel ordering bug on filter transitions
- Update notification transitions to Working→Waiting and Working→Errored

**Non-goals (deferred to roadmap):**
- Kiro IDE live session detection (different architecture — no CLI process, no JSONL)
- Read-tracking / "unread" concept (aspirational — would require per-session last-seen timestamp)
- Using `kiro-cli acp` as a background work API from PowerAtlas
- Subcommand filtering (both `chat` and `acp` are valid liveness signals for the same session)

## Resolved Decisions

- Q1: Detection strategy — A: Use cwd-based association for most-recently-updated session, with --resume-id as higher-confidence override — Decision: Remove the is_explicitly_live/is_fresh gate; when a process runs in a cwd, classify all sessions in that workspace from their JSONL tails
- Q2: Status vocabulary — A: 4-state: Working/Waiting/Errored/no-dot — Decision: Working (green pulsing) = agent executing; Waiting (yellow) = agent finished, your turn (ersatz unread); Errored (red) = error detected; no dot = no process
- Q3: "Unread" concept — A: Agent finished its turn, needs user input (detectable from JSONL tail) — Decision: No read-tracking needed; "Waiting" = last line is AssistantMessage and process is running
- Q4: Read tracking achievability — A: No read tracking (Option A) — Decision: Waiting state covers the useful case; read-tracking deferred as aspirational
- Q5: Idle/done/closed distinction — A: Map "process running but idle" to Waiting; no dot for no process — Decision: Single absent-dot state for all non-running sessions
- Q6: Subagent (acp) filtering — A: No filtering needed — Decision: Both chat and acp processes are valid liveness signals (same user session, same cwd)
- Q7: Filter options — A: All / Working / Waiting / Errored — Decision: 4 filter options replacing the current 7
- Q8: Workspace-level indicators — A: Dot on workspace card, priority Errored > Waiting > Working > none — Decision: Workspace cards show highest-priority dot
- Q9: refreshCards ordering bug — A: Include in this plan — Decision: Fix the differential DOM update to handle structural elements
- Q10: Kiro IDE sessions — A: Scope out, defer to roadmap — Decision: Not included in this plan
- Q11: v3 kiro-cli classifier — A: Include — Decision: Implement classify_kiro_v3 using documented messages.jsonl format
- Q12: Notification transitions — A: Working→Waiting and Working→Errored only — Decision: Same intent as today with new vocabulary
- Q13: Multiple sessions same workspace — A: Classify each independently from its own JSONL tail — Decision: Each session gets its own dot based on its JSONL state; mtime distinguishes actively-written (Working) from stale (Waiting)

## 1) Current State

The live session status subsystem spans 6 files:

- **`presence.py`** (218 lines): Scans `psutil.process_iter()` for kiro-cli/claude processes. Produces a `Snapshot` with `live_sids` (session IDs found on cmdline via `--resume-id`/`--resume`) and `live_cwds` (process working directories). 3s TTL cache.
- **`status_classifier.py`** (292 lines): Reads the last 4KB of a session's JSONL file, dispatches to per-provider classifiers (`classify_kiro_v2`, `classify_claude`, `classify_kiro_v3` stub). Returns `SemanticStatus` enum. 5s TTL + mtime guard cache.
- **`web.py`** `_session_status()` (lines 133-163): The critical gate — if session is NOT in `live_sids` AND NOT a "fresh" session (created <90s ago), returns `"closed"` immediately without calling the semantic classifier.
- **`web.py`** `_workspace_status()` (lines 168-174): Checks if cwd is in `live_cwds` + mtime heuristic. Only used for workspace card filtering, never for rendering a dot.
- **`templates/partials/session_row.html`**: Renders status dot span based on status string (active→green, needs_input→yellow, idle→grey, errored→red, closed→nothing).
- **`templates/index.html`** `refreshCards()`: Differential DOM update for workspaces panel; full innerHTML for sessions panel. 15s burst / 30s steady polling.

**Critical bugs confirmed by testing:**
1. `_session_status()` gate at web.py:148 makes the semantic classifier unreachable for 99% of sessions (no `--resume-id` on cmdline, and fresh heuristic expires after 90s)
2. `refreshCards()` differential update doesn't handle `.pinned-separator` or `.group-heading` elements — filter transitions break workspace ordering
3. `classify_kiro_v3()` is a stub returning None — v3 sessions never get classified
4. Workspace cards render no status dot — `_workspace_status()` is only used for filtering

## 2) Goal

Replace the broken session-ID-gated detection with cwd-based association that classifies all sessions in a workspace when a provider process runs there. Simplify the status vocabulary to 3 actionable states (Working/Waiting/Errored) with no dot for closed sessions. Add workspace-card status dots and fix the filter-reset ordering bug.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Detection strategy | cwd-based: when process runs in workspace, classify all sessions from JSONL tails | Session-ID only (current, broken); PID-file tracking | cwd matching already works (confirmed by testing); JSONL writes are synchronous (verified: 0.9s lag) |
| Status vocabulary | Working / Waiting / Errored / no-dot | 6-state (current); 3-state without Errored | User decision: Errored is distinct from Waiting (needs different user action) |
| Workspace dot priority | Errored > Waiting > Working > none | Working first; no workspace dots | User decision: error needs attention most, then "your turn," then "happening" |
| Multiple sessions same cwd | Classify each independently from its JSONL | Only newest; all get same status | Each session has its own JSONL file; mtime distinguishes active from stale |
| Filter options | All / Working / Waiting / Errored | Keep all 7 current options | User decision: collapse redundant Live/Active/Idle into the new vocabulary |
| Notification transitions | Working→Waiting, Working→Errored | Include Waiting→Closed | User decision: closed transitions are noisy and unactionable |
| Subagent processes | Keep both chat+acp as liveness signals | Filter out acp | acp is the backend of the user's own chat session (confirmed via process tree) |
| refreshCards fix | Switch workspace panel to full innerHTML replacement | Fix differential to handle structural elements | Simpler, matches sessions panel approach, eliminates entire class of ordering bugs |

## 4) External Dependencies & Costs

### Required external changes

None — this is a code-only change to an existing local application.

### Cost impact

None.

## 5) Implementation Phases

### Phase 1: Core detection + classification rewrite [QA] [P:3]

**Goal**: Remove the `is_explicitly_live/is_fresh` gate. Implement cwd-based detection with the new 3+1 status vocabulary. Implement `classify_kiro_v3()`.

**Covers**: SC-1, SC-2, SC-7

**File scope**: `src/power_atlas/presence.py`, `src/power_atlas/status_classifier.py`, `src/power_atlas/web.py` (status functions only), `tests/test_data.py`, `tests/test_web.py`

**Changes:**

1. **`status_classifier.py`** — New vocabulary + v3 classifier:
   - Rename `SemanticStatus` values: `ACTIVE→WORKING`, `NEEDS_INPUT→WAITING` (merge with IDLE), `IDLE` removed, `ERRORED` stays, `CLOSED` stays
   - Implement `classify_kiro_v3()`: read `payload.type` field — `tool_call`/`user`→WORKING, `assistant`→WAITING. Check for error signals in `tool_result` with `success: false`.
   - Update `classify_kiro_v2()` and `classify_claude()` to return the new enum values

2. **`web.py`** — Rewrite `_session_status()`:
   ```python
   def _session_status(snapshot, session, provider: str, ...) -> str:
       # 1. Is a provider process running in this session's workspace?
       norm_cwd = _normalize_path(session.cwd)
       if norm_cwd not in snapshot.live_cwds({provider}):
           return "closed"

       # 2. Classify from JSONL tail (works for all sessions in this cwd)
       semantic = get_semantic_status(session.session_id, provider, session.cwd)
       if semantic is not None:
           return semantic.value

       # 3. Fallback: process is running but can't classify → "waiting"
       return "waiting"
   ```
   - Update `_LIVE_STATUSES` to `("working", "waiting", "errored")`
   - Update `_workspace_status()` to aggregate session statuses with priority (errored > waiting > working)
   - Keep `--resume-id` matching as a bonus signal (if session_id matches explicitly, skip the cwd check — it's definitely live)

3. **`presence.py`** — Simplify: `probable_fresh_session()` is no longer needed (remove or deprecate). The `_PROVIDER_SPECS` and `Snapshot` class remain unchanged (still scan processes, still report cwds).

4. **`status_classifier.py`** — v3 path resolution:
   ```python
   def _resolve_jsonl_path(session_id, provider, cwd):
       if provider == "kiro-cli":
           # v2 path
           path = SESSION_DIR / f"{session_id}.jsonl"
           if path.is_file():
               return path
           # v3 path: search workspace-hash dirs
           for ws_dir in V3_SESSION_ROOT.iterdir():
               if ws_dir.name == "cli" or not ws_dir.is_dir():
                   continue
               v3_path = ws_dir / f"sess_{session_id}" / "messages.jsonl"
               if v3_path.is_file():
                   return v3_path
           return None
   ```

5. **Tests**: Update existing test vocabulary (active→working, idle→waiting). Add tests for:
   - cwd-based detection without `--resume-id`
   - v3 classifier (assistant/user/tool_call/tool_result messages)
   - Multiple sessions same workspace getting independent status

**Exit criteria**:
- [ ] `_session_status()` returns "working" for a session whose JSONL tail shows tool activity AND a process runs in that cwd
- [ ] `_session_status()` returns "waiting" for a session whose JSONL tail ends with AssistantMessage AND a process runs in that cwd
- [ ] `_session_status()` returns "closed" when no process runs in the workspace
- [ ] v3 sessions in `sess_*/messages.jsonl` are classified correctly
- [ ] All existing status tests pass with updated vocabulary
- [ ] `--resume-id` sessions still detected (backward compat)

### Phase 2: UI vocabulary + workspace card dots + filter [QA] [P:3]

**Goal**: Update the frontend to the new status vocabulary — dot colors, filter dropdown, workspace card status indicator.

**Covers**: SC-3, SC-4, SC-5

**File scope**: `src/power_atlas/templates/partials/session_row.html`, `src/power_atlas/templates/partials/workspace_card.html`, `src/power_atlas/templates/index.html`, `src/power_atlas/static/style.css`, `src/power_atlas/web.py` (rendering functions)

**Changes:**

1. **`session_row.html`** — Update status dot rendering:
   ```html
   {% if status == 'working' %}<span class="session-status status-working" title="Working — agent is executing" aria-label="Working"></span>
   {% elif status == 'waiting' %}<span class="session-status status-waiting" title="Waiting — needs your input" aria-label="Waiting"></span>
   {% elif status == 'errored' %}<span class="session-status status-errored" title="Errored — something went wrong" aria-label="Errored"></span>
   {% endif %}
   ```

2. **`workspace_card.html`** — Add status dot to card header:
   ```html
   {% if workspace_status == 'errored' %}<span class="ws-status status-errored" title="Error in this workspace"></span>
   {% elif workspace_status == 'waiting' %}<span class="ws-status status-waiting" title="Session waiting for you"></span>
   {% elif workspace_status == 'working' %}<span class="ws-status status-working" title="Agent working"></span>
   {% endif %}
   ```

3. **`style.css`** — Update/rename status classes:
   ```css
   .session-status.status-working { background: #22c55e; box-shadow: 0 0 0 2px rgba(34, 197, 94, 0.22); animation: status-pulse 2s ease-in-out infinite; }
   .session-status.status-waiting { background: #f59e0b; box-shadow: 0 0 0 2px rgba(245, 158, 11, 0.22); }
   .session-status.status-errored { background: #ef4444; box-shadow: 0 0 0 2px rgba(239, 68, 68, 0.22); }
   .ws-status { /* same styles, slightly smaller for workspace cards */ }
   ```

4. **`index.html`** — Simplify filter dropdown:
   ```html
   <select id="statusFilter" onchange="setStatusFilter(this.value)" title="Filter by status">
     <option value="">All</option>
     <option value="working">🟢 Working</option>
     <option value="waiting">🟡 Waiting</option>
     <option value="errored">🔴 Errored</option>
   </select>
   ```

5. **`web.py`** — Pass `workspace_status` to workspace card template:
   - In `partials_workspaces()`: compute per-workspace status by aggregating session statuses (priority: errored > waiting > working)
   - Pass `workspace_status=` to the workspace_card.html template render call

**Exit criteria**:
- [ ] Session rows show green pulsing dot for "working", orange dot for "waiting", red for "errored", no dot for "closed"
- [ ] Workspace cards show the highest-priority status dot
- [ ] Filter dropdown shows All / Working / Waiting / Errored
- [ ] Selecting "Working" filter narrows both panels to matching items
- [ ] Expanding a workspace while filtered shows matching session rows (not "No matching sessions")

### Phase 3: refreshCards fix + notification update [QA] [P:1,2]

**Goal**: Fix the workspace panel ordering bug on filter transitions. Update notification transitions to the new vocabulary.

**Covers**: SC-6, SC-8

**File scope**: `src/power_atlas/templates/index.html` (JS only), `src/power_atlas/notifications.py`, `tests/test_web.py` (notification tests)

**Changes:**

1. **`index.html`** — Replace differential workspace update with full innerHTML:
   ```javascript
   // Replace the complex diff logic with:
   fetch('/partials/workspaces?' + provQs + qs).then(function(r) {
       return r.text();
   }).then(function(html) {
       var el = document.getElementById('workspace-cards');
       // Save expanded state before replacing
       var expandedCards = [];
       el.querySelectorAll('.workspace-card:not(.collapsed)').forEach(function(c) {
           expandedCards.push(c.dataset.cwd);
       });
       el.innerHTML = html;
       if (window.htmx) htmx.process(el);
       // Re-expand previously expanded cards
       expandedCards.forEach(function(cwd) {
           var card = el.querySelector('.workspace-card[data-cwd="' + CSS.escape(cwd) + '"]');
           if (card) {
               card.classList.remove('collapsed');
               var header = card.querySelector('.card-header');
               if (header) header.setAttribute('aria-expanded', 'true');
               loadCardSessions(card);  // re-fetch sessions for expanded cards
           }
       });
       el.removeAttribute('aria-busy');
       updateActionBar();
   });
   ```

2. **`notifications.py`** — Update transition set:
   ```python
   _NOTIFY_TRANSITIONS = frozenset({
       ("working", "waiting"),
       ("working", "errored"),
   })
   ```

3. **Tests**: Update notification test expectations for new vocabulary.

**Exit criteria**:
- [ ] Switching from "Working" filter to "All" preserves pinned-first ordering and time-group headings
- [ ] Switching between any filter combination preserves correct ordering
- [ ] Notifications fire on working→waiting transition
- [ ] Notifications fire on working→errored transition
- [ ] No notification fires on waiting→closed or working→closed

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| Old sessions in workspace get "Waiting" dot (false positive) | Low — user might see old sessions with yellow dot when any process runs in that cwd | Acceptable trade-off per user decision (ersatz "unread"). Future: only classify sessions updated within last N hours. |
| v3 path resolution perf (scanning workspace-hash dirs) | Low — iterdir() on a few dozen dirs is fast | Cache the session_id→path mapping in status_classifier; bounded to 100 entries already |
| Full innerHTML replacement loses expanded card state | Medium — user has to re-expand cards | Mitigated by saving/restoring expanded state in the JS code |
| Notifications fire more often (more sessions detected) | Low — cooldown (60s per session) already exists | Existing cooldown + `mark_initialized()` pattern handles baseline establishment |

## 7) Verification

- **Automated**: `pytest tests/test_data.py tests/test_web.py -k "presence or status or notification"` — all tests pass with new vocabulary
- **Manual/browser**: Start a `kiro-cli chat -a` session, verify green dot appears and persists beyond 90s. Wait for agent to finish, verify transition to orange "Waiting" dot.
- **Filter test**: Select "Working" filter, verify workspace cards narrow. Reset to "All", verify pinned ordering preserved.
- **Workspace dot**: Verify workspace card shows highest-priority dot.
- **Notification**: Enable notifications in config, trigger a working→waiting transition, verify toast fires.

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `README.md` | Update "Live session status" feature description to reflect new vocabulary (Working/Waiting/Errored) and workspace-card dots | 2 |

## 9) Implementation Divergences from Plan
<Reserved — filled during implementation>

## Review Log
<Reserved — filled by review cycles>

## Harness Improvement Opportunities

- The OpenAI Codex Micro page (work-louder keyboard product page) was not a useful reference for status classification design — it only mentions "thinking, running, waiting, done" in marketing copy with no specification. The user's intent was to reference a simpler model, not that specific page. A future `/qexplore` could ask "what specifically from that reference applies?" earlier. — cost: one wasted web_fetch + time parsing a product page — suggested change: when user references an external URL for design inspiration, ask what specific aspect to extract before fetching.
