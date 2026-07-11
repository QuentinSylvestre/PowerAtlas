# Live Session Status and Status Filter

> **Date**: 2026-07-11
> **Status**: Planned  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Last Updated**: 2026-07-11T07:03
> **Scope**: Show which discovered sessions are currently alive (🟢 Working / 🟡 Waiting) via live-only dots on session rows, plus a status filter dropdown that acts on both the Sessions and Workspaces panels
> **Estimated effort**: ~1-2 days

---

## Intent

### Problem statement & desired outcomes

PowerAtlas discovers sessions as files on disk and lets you resume/launch them, but once launched it has **no awareness of runtime state**. When you have many sessions in flight, the operator becomes the bottleneck: you can't tell at a glance which sessions are actively working, which have stopped and are waiting on you, and which are just historical files.

This delivers the "mission control" half of live status: **make the board reflect reality.** The existing global **Sessions panel** (added in the panel restructure) is already a recency-sorted list of every session across all workspaces — it is the natural triage surface. We decorate its rows (and rows inside expanded workspace cards) with a live-status dot, and add a status filter to isolate the few sessions that are actually alive.

Explicitly **out of scope** (cut during design): OS notifications, tray badge / ambient "pull me back" signal, and window focus / jump-to-terminal. This feature is purely the visual dashboard reflecting state.

### Success criteria

1. A session that is **currently running in a terminal** and actively producing output shows a 🟢 **Working** dot on its row.
2. A session that is **running in a terminal but stopped** (agent finished its turn / awaiting input) shows a 🟡 **Waiting** dot.
3. A session with **no live process** shows **no dot** (calm default — closed/historical).
4. Dots render both in the global Sessions panel (`#all-sessions`) and in expanded workspace-card session rows (same `session_row.html`).
5. A **status filter dropdown** (`All / Live / Working / Waiting / Closed`) lives in the Sessions panel and filters **both** the Sessions panel and the Workspaces panel (a workspace card is shown only if it contains a session matching the selected status).
6. Under an active status filter, filtered-in workspace cards show **no extra hint** — pure show/hide (dots remain on rows only).
7. Status updates ride the **existing 30s session refresh** (plus tab re-focus and manual refresh) — no new polling loop.
8. `All` (default) preserves today's behavior exactly; the filter and dots add nothing when nothing is live.

### Scope boundaries & non-goals

**In scope:**
- New `presence.py` module: process scan → set of live session keys, short-TTL cached.
- Server-side status annotation (`working` / `waiting` / `closed`) on session rows in `/partials/all-sessions`, `/partials/sessions`, and workspace-card rendering.
- Status filter dropdown UI + `_activeStatus` filter state wired into `_buildWorkspaceQs()` and the existing filter/refresh JS.
- Workspaces panel filtered by presence of a matching-status session.
- Live-only dot CSS in `session_row.html` / `style.css`.
- `psutil` dependency (cross-platform process + cmdline + cwd introspection).

**Non-goals:**
- Notifications, tray badge, sound, "needs attention" ambient signaling.
- Window focus / PID→window / jump-to-terminal (deferred future tier).
- Workspace-card status rollup / always-on live counts.
- A dedicated "Active sessions" view (the existing Sessions panel already serves this).
- Sub-30s / real-time push (SSE); status piggybacks the 30s refresh.
- Status for `kiro-ide` (an IDE, not a resumable CLI session — never gets a dot).
- Self-launch PID registry (optional future hardening; MVP relies on the process scan alone).

---

## Discovery

### Existing patterns & constraints

- **Sessions panel already exists**: `/partials/all-sessions` renders a global, recency-interleaved, paginated (20/page) list across all workspaces (`260709-0801_PANEL_RESTRUCTURE`). This is the mission-control surface — no new panel needed.
- **Unified filter system exists**: provider pills (`_activeProvider`) + tag/time dropdowns (`_activeTag`, `_activeTimeFilter`) by the Workspaces header, composed into requests via `_buildWorkspaceQs()` (`index.html`). Status becomes a fourth dimension `_activeStatus`.
- **Session row** (`partials/session_row.html`) carries `data-sid`, `data-cwd`, `data-provider` and already has a title row — the dot slots in before the title.
- **Session model** (`data.py`) is a frozen dataclass with `last_reply_tail`, `updated_at`, and the transcript file is already parsed; file **mtime** is available for the working/waiting split.
- **Refresh cadence**: 30s session refresh + `visibilitychange` + manual `doRefresh()` already drive re-fetches (`index.html`). Status annotation happens server-side on each fetch — piggybacks for free.
- **Correlation key**: launched commands carry the id — `claude --resume <id>`, `kiro-cli chat --resume-id <id>` (`launcher.py:_build_provider_args`). The window title convention `"{display} - {folder}"` is a weaker secondary signal (agents rewrite titles — see MEMORY.md) and is NOT used here since we don't touch windows.
- **htmx-mini caveat** (MEMORY.md): after any innerHTML swap, call `htmx.process(el)`. Filter changes re-fetch partials, so this applies.

### Liveness & status computation

`presence.py` exposes something like `live_keys() -> dict[key, float]` where `key = (provider, normalized_cwd, session_id)` and the value is the process's most-recent-activity hint. Implementation:

1. Enumerate processes via `psutil` whose executable/name matches a provider binary (`kiro-cli`, `claude`).
2. Read each process `cmdline` for the resume flag + id, and `cwd()` for the working directory.
3. Build the live set keyed by `(provider, cwd, sid)`. Cache with a short TTL (a few seconds) so multiple partials in one refresh reuse one scan.

Status per session row (computed server-side at render):
- **Closed** → no matching live process. Render no dot.
- **Live** → matching process exists:
  - **Working 🟢** if transcript mtime within the last ~60s (actively producing).
  - **Waiting 🟡** if transcript idle > ~60s (stopped, awaiting you).

The ~60s threshold is tuned to the 30s refresh so a session doesn't flicker Working↔Waiting between refreshes.

### Risks & mitigations

1. **Fresh in-terminal sessions lack the id in cmdline** → can't key by sid. Mitigation: fall back to `(provider, cwd)` match and attribute liveness to the most-recently-active session in that folder. Documented limitation; disambiguating multiple same-provider live sessions in one folder is out of MVP scope.
2. **`psutil` new dependency** → adds a wheel. Mitigation: it's mature, pure-ish, cross-platform, and the native alternative (Windows WMI + `/proc`) is materially more code. Guard the import so status degrades to "all closed" if unavailable rather than crashing.
3. **Process scan cost on each refresh** → mitigated by short-TTL caching the scan and only scanning for known provider binaries. One scan per 30s refresh cycle is negligible.
4. **Windows cmdline/cwd access** → `psutil` handles this, but `cwd()` can raise `AccessDenied` for some processes. Mitigation: wrap per-process reads in try/except and skip on error.
5. **Filter acting across panels** → status filter lives in the Sessions panel but must reach the Workspaces fetch. Mitigation: add `_activeStatus` to `_buildWorkspaceQs()` (already the shared query builder) so both `/partials/workspaces` and `/partials/all-sessions` receive it.
6. **Test coverage** → `test_web.py`, `test_data.py` cover panel endpoints and session parsing. New tests: presence keying/classification (with a faked process list), status annotation on partials, and filter behavior on both panels.

### Resolved decisions

- Q1: Hero use case? — **Mission control** (dashboard scan). Pull-me-back and jump-to-focus both cut.
- Q2: What counts as "needs attention"? — Originally all four states; moot after notifications were dropped.
- Q3: Notification trigger policy? — **None** — notifications dropped entirely; feature is dashboard-only.
- Q4: How many status buckets? — **3**: Working / Waiting / Closed.
- Q5: Scan surface? — **Dots on session rows only** + a status filter; leverage the existing global Sessions panel (no new view, no card rollup).
- Q6: Dot treatment given mostly-closed rows? — **Live-only dots** (Working/Waiting get a dot; Closed shows nothing).
- Q7: Status filter form? — **Dropdown** (`All / Live / Working / Waiting / Closed`), consistent with existing time/tag dropdowns.
- Q8: Filter scope? — **Both panels** (Sessions filtered by row status; Workspaces filtered to cards containing a matching session).
- Q9: Refresh cadence? — **Piggyback the existing 30s refresh** (+ tab-focus + manual). No new poll loop.
- Q10: Card appearance under filter? — **Pure filter, no card hint.**
- Q11: Working-vs-Waiting definition? — Live process + transcript mtime < ~60s → Working; live + idle > ~60s → Waiting. (Proposed; open to tuning.)
- Q12: Provider coverage? — `kiro-cli` and `claude-code` only; `kiro-ide` excluded (not a resumable CLI session).

---

## Implementation outline (phased)

**Phase 1 — Presence backend.** Add `psutil` to `pyproject.toml`. New `presence.py`: guarded `psutil` import, process scan → live-set keyed by `(provider, cwd, sid)` with resume-id parsing + cwd fallback, short-TTL cache. Unit tests with a faked process list (no real processes).

**Phase 2 — Status annotation.** In `web.py`, compute `status ∈ {working, waiting, closed}` for each session row when rendering `/partials/all-sessions`, `/partials/sessions`, and workspace cards, using `presence.live_keys()` + transcript mtime. Pass `status` into the template context.

**Phase 3 — Dot UI.** Add the live-only dot to `session_row.html` (before the title) + CSS in `style.css`, with a tooltip (e.g. "Working · active 12s ago" / "Waiting"). Closed → no element.

**Phase 4 — Status filter.** Add the `All / Live / Working / Waiting / Closed` dropdown to the Sessions panel; add `_activeStatus` state; wire into `_buildWorkspaceQs()` and the filter/clear/refresh JS. Server: filter session rows by status; filter workspace cards to those containing a matching-status session (no card hint). Handle `htmx.process()` after swaps.

**Phase 5 — Tests + docs.** Presence classification tests, partial-annotation tests, both-panel filter tests. Update README "Features" (live status + filter) and remove the corresponding "Session health indicators" bullet from ROADMAP.
