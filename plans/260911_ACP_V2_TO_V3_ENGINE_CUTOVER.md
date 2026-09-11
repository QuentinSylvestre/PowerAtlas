# ACP v2-to-v3 Engine Cutover

> **Date**: 2026-09-11
> **Status**: Exploring
> **Scope**: Retire the live v2 ACP protocol engine, make v3 the sole engine behind `/acp`, delete v2-only code and rename the surviving v3 code to drop its suffix.

---

## Intent

### Problem statement & desired outcomes

PowerAtlas currently runs two parallel ACP (Agent Client Protocol) engines: `/acp` (v2, mature, spawns kiro-cli with blanket auto-approve `-a`) and `/acp-v3` (v3, kiro-cli `--agent-engine v3`, just brought to production quality by `plans/done/260909-1127_ACP_V3_PRODUCTION_HARDENING.md`). The merge of the two was explicitly deferred at the end of that plan ("kept parallel... lower-risk, separately-scoped future project", `plans/done/260909-1127_ACP_V3_PRODUCTION_HARDENING.md:685`).

This plan reopens that deferral: v3 becomes the only engine, reachable at `/acp` (the URL v2 currently owns). `/acp-v3` as a separate path is retired. v2's live protocol/supervisor code is deleted outright, not just left unreached — including restructuring `_SupervisorV3`, which currently subclasses `_Supervisor` (`acp.py:4697`), so it no longer depends on v2's class body. The surviving v3 code is renamed to drop its `_v3`/`V3` suffix, since there is no longer a second engine to disambiguate from.

Desired outcome: one ACP engine, at one URL, with no dead v2 code left behind to maintain or reason about.

### Success criteria

- `/acp` serves v3 sessions; `/acp-v3` and its dedicated route registrations no longer exist.
- v2-only live-protocol code is deleted: `_Supervisor`'s v2-only methods, `serve_socket`, `_dispatch`, the unsuffixed `_handle_*` handlers, `ACP_ARGS`, `CLOSE_METHOD`. `_SupervisorV3` no longer inherits from `_Supervisor` — the methods it previously inherited unchanged are folded directly into the surviving class.
- The surviving engine's code is renamed repo-wide to drop the `_v3`/`V3` suffix (`acp.py`, `web.py`, and the ~180 ACP-related test names in `tests/test_web.py` that reference it).
- `data_kiro.py`'s v2 session-file reader (`get_first_prompt`, `_extract_prompts_cached`, the `.json`/`.jsonl`/`.history` parsing under `KIRO_SESSION_DIR`) is untouched — the dashboard can still browse/display the ~16,414 pre-existing v2 session files.
- The dashboard's "💬 Open in Agent orchestrator" button (`session_row.html:31`) is hidden or disabled specifically for old v2-provider rows, since there is no live v2 engine left to resume them into.
- `_REMOTE_ALLOWED_PATHS` (`web.py:1139-1171`) is reconciled to the new single-engine route set — no leftover entries for a retired path.
- `tests/test_web.py` and `tests/acp_page.test.mjs` pass, updated to match the new (unsuffixed) naming and the retired v2 route/behavior — including the three `acp_page.test.mjs` tests (`test_engine_v3_ws_path`, `test_engine_v3_session_api_url`, `test_engine_v3_workspaces_api_url`) that currently assert v2 URLs are *absent*, which need deliberate rewriting now that there is only one engine.

### Scope boundaries & non-goals

- **Out of scope**: the permission-UX consequence of this cutover (v3's interactive `session/request_permission` approval becomes the only approval flow — v2's blanket `-a` auto-approve has no equivalent) is accepted as a deliberate, permanent product decision in this plan, not something to fix here. A v3-side auto-mode remains a separate, already-tracked ROADMAP item under "Session Control & Integration."
- **Out of scope**: the loss of server-side session-close wire confirmation (previously scoped only to v3 sessions, now universal since v2's `close_session`'s `_kiro.dev/session/terminate` call goes away with the rest of v2) is accepted as a permanent kiro-cli v3 binary limitation, not a defect to chase.
- **Out of scope**: the mode-switcher UI, `_pending_permission`/orphan-lock/steering-palette follow-ups — tracked separately in `plans/260911_ACP_V3_FOLLOWUP_FEATURES.md`.
- **Non-goal**: this plan does not change kiro-cli itself or attempt to restore auto-approve-equivalent behavior for v3.

---

<!-- Transient: /qplan folds these into the planning sections and removes this section. -->
## Exploration Discovery

### Existing patterns & constraints

- `_SupervisorV3(_Supervisor)` (`acp.py:4697`) subclasses v2's `_Supervisor` (`acp.py:2618`, ~2065 lines) and overrides only 13 methods; the rest — `alive`, `agent_pid`, `ensure_started`, `shutdown`, `_request`, `_notify`, `_write`, `_reader_loop`, `_on_line`, `_post`, `_on_message`, `_on_response`, `_refuse`, `_stamp_activity`, `touch_used`, `_on_subagent_list`, `_evict_finished_subagents`, `_compaction_session`, `_on_compaction_status`, `_on_agent_death`, `at_capacity`, `_flush_pending_commands`, `record`, `prompt`, `cancel`, `steer`, `commands_options`, `commands_execute` — are inherited unchanged and must be folded directly into the surviving class rather than deleted.
- Transport dispatch is two fully parallel chains, not shared: `serve_socket` (`acp.py:5939`) → `_dispatch` (`acp.py:6142`) → 9 unsuffixed `_handle_*` functions, vs. `serve_socket_v3` (`acp.py:6021`) → `_dispatch_v3` (`acp.py:6084`) → 9 `_handle_*_v3` functions (confirmed pairs at `acp.py:6200/7484`, `6572/7591`, `6725/7674`, `6831/7758`, `7044/7890`, `7123/8159`, `7168/8201`, `7309/8044`, `7390/8099`) plus `_handle_permission_response_v3` (`acp.py:7958`), which has no v2 equivalent (`CLIENT_TYPES_V3 = CLIENT_TYPES | {"permission_response"}`, `acp.py:177`).
- Two independent, never-cross-checked engine-identity mechanisms exist today: `web.py`'s three dispatch sites (`_acp_availability` `web.py:1838`, `_acp_delete_session` `web.py:2845`, `_acp_delete_many` `web.py:2950`) use a syntactic `sid.startswith("sess_")` string check; `acp.py`'s `_Registry.attach`/`detach` (`acp.py:2066-2089`) use semantic `session_id in _supervisor_v3.sessions` dict-membership. Both become moot once only one engine exists, but any code that currently branches on this distinction needs to be found and simplified, not just left as dead branching.
- `_registry` (`acp.py:2097`, one `_Registry()` singleton) and its `MAX_CONNECTIONS = 8` cap are already shared, unpartitioned, process-wide state used identically by both engines today — no change needed here on cutover.
- `acp.html`'s engine-conditional surface is tiny: exactly 4 lines (`acp.html:660-666`, `ENGINE`-derived URL constants) out of 8034. `static/style.css` has zero engine-conditional rules. The template/CSS side of this cutover is a small, low-risk edit relative to the Python side.
- `apply_config()` runs synchronously at `__main__.py:754`, well before socket binding (`__main__.py:778`) — confirmed no race window where a request could reach `_supervisor_v3 is None`.
- AGENTS.md:5,7 — Python changes (`acp.py`, `web.py`) require a PowerAtlas restart to take effect; the agent must never restart autonomously, always defer timing to the user. Template/JS/CSS changes are hot-reloadable (hard browser reload only).

### Risks & mitigations

- **Deep retirement is an order-of-magnitude larger change than the ROADMAP item anticipated** ("engine parameter or subclass retained", `plans/ROADMAP.md`) — mitigation: `/qplan` should size this as Major tier given the ~2065-line base-class fold-in, the repo-wide rename, and the test-suite churn, not assume Standard tier from the original ROADMAP framing.
- **The rename is large mechanical churn** across `acp.py`, `web.py`, and ~180 test names — risk of an unintentional behavior change riding along with a rename that should be pure. Mitigation: treat the rename as its own reviewable step, verified by full test-suite pass before and after.
- **`_REMOTE_ALLOWED_PATHS` is a hand-maintained allowlist, not derived from the route table** (`web.py:1139-1171`) — must be deliberately reconciled, not assumed to update itself when routes are deleted. Getting this wrong risks either a broken route (over-pruned) or a stale, needlessly-open entry (under-pruned).
- **A pre-existing asymmetry**: `_handle_subscribe` cross-checks `_supervisor_v3.closing` before attaching a socket (`acp.py:6244-6245`), but `_handle_subscribe_v3` does not symmetrically check `_supervisor.closing`. This is now in scope since the merge directly touches this code — worth fixing as part of the consolidation rather than carrying the asymmetry into the merged class.
- **122 orphaned `sess_`-prefixed `.history` files already exist in `KIRO_SESSION_DIR` (`~/.kiro/sessions/cli/`, confirmed via live directory listing 2026-09-11)** — no `.json`/`.jsonl` pair, meaning kiro-cli v3 itself already writes readline-history files into the nominally-v2-only directory. Doesn't break anything today (v2's listing globs `.json`, never sees these) and this plan does not need to clean them up, but the "v2 and v3 storage is cleanly separated" assumption in existing code comments is weaker than stated — worth a one-line acknowledgment rather than silently building further on it.
- **Terminology/documentation fallout**: `plans/ROADMAP.md`, `docs/KNOWLEDGE.md`, and `AGENTS.md` itself reference "v2"/"v3"/"acp-v3" extensively. Once the rename ships, these read as stale. A documentation sweep should be a tracked exit criterion, not an afterthought.
- **Session-ID collision**: confirmed a non-issue. v2 ids are bare UUIDs, v3 ids are `sess_`-prefixed (`web.py:4920` regex), disjoint by construction and already exercised by tests (`test_deleting_a_v2_session_open_in_the_other_engine_is_refused`, `tests/test_web.py:17299`). Live directory listing (16,414 files, 2026-09-11) found zero v2-stored files starting with `sess_`. No new disjointness logic needed.

### Resolved decisions

- Q1: How should this request be organized into project file(s), given it spans ~6 largely-independent pieces of work? — A: Two plans: cutover + bundled follow-ups (Recommended). — Decision: split into this cutover plan and a separate `plans/260911_ACP_V3_FOLLOWUP_FEATURES.md`.
- Q2: Does "v3 fully replaces v2" mean routing-level takeover (v2 code stays, unreached) or deep retirement (v2 code deleted now, requiring `_SupervisorV3` to stop inheriting from `_Supervisor`)? — A: Deep retirement in this same plan. — Decision: delete v2-only live-protocol code now; fold `_SupervisorV3`'s inherited-unchanged methods directly into the surviving class.
- Q3: Should the dashboard still browse/read the ~16,414 pre-existing v2 session files after the live v2 engine is retired? — A: Keep old v2 history browsable (Recommended). — Decision: `data_kiro.py`'s v2 session-file reader stays untouched; only the live v2 supervisor/protocol code is retired.
- Q4: Should the surviving v3 code be renamed to drop the `_v3`/`V3` suffix? — A: Rename to drop the v3 suffix (Recommended). — Decision: repo-wide mechanical rename across `acp.py`, `web.py`, and `tests/test_web.py`'s ACP test names.
- Q5: How to handle the loss of v2's blanket auto-approve, given v3's `-a` is structurally incompatible (exits 2) and v2's own planned auto-mode alternative doesn't exist yet? Council-eligible; user explicitly opted to skip `/qcouncil` and be asked directly. — A: Accept it now, auto-mode stays a separate future item (Recommended). — Decision: ship v3's interactive `session/request_permission` approval as the permanent default; the existing "Auto-mode for `/acp` permissions" ROADMAP item (under "Session Control & Integration") remains a separately-scoped future plan, untouched by this one.
- Q6: What should happen to the dashboard's "Open in Agent orchestrator" button for old v2 session rows, given it currently implies a live resume that v2's retirement makes impossible? — A: Hide/disable the button for old v2 rows (Recommended). — Decision: gate `session_row.html:31`'s button to exclude v2-provider rows once this cutover ships.

### Open items

- Exact `_REMOTE_ALLOWED_PATHS` reconciliation shape (which of the 10 current entries collapse into which of the post-cutover set) — deterministic, resolvable by `/qplan` reading the current allowlist against the final route list.
- Full-breadth confirmation that no `web.py` helper function beyond the ones already traced (`_acp_availability`, `_acp_delete_session`, `_acp_delete_many`, and the route-level pairs) branches on v2-vs-v3 engine identity — the research passes' coverage of `web.py`'s many `_acp_*`/`api_acp_*` helpers was not exhaustive; `/qplan` or the implementation phase should do a full sweep before assuming the traced set is complete.

**Assumptions (unconfirmed)**: none beyond what's already recorded in Risks above — the exploration interview covered every consequence-significant decision point directly with the user rather than defaulting any of them.

### Recommended approach

1. Restructure `_SupervisorV3` into the sole supervisor class: fold in the ~15 inherited-unchanged methods from `_Supervisor`, delete v2-only methods (`_spawn`'s v2 branch, v2-only constants `ACP_ARGS`/`CLOSE_METHOD`), then rename the class to drop `V3`.
2. Delete `serve_socket`/`_dispatch` and the 9 unsuffixed `_handle_*` functions; rename the `_v3`-suffixed survivors to drop the suffix.
3. Repoint `web.py`'s `/acp` route (`acp_page`, `ws_acp`) to the renamed engine; delete `/acp-v3`'s route registrations (`acp_v3_page`, `ws_acp_v3`) and reconcile `_REMOTE_ALLOWED_PATHS`.
4. Rename `web.py`'s surviving `_v3`-suffixed endpoints/helpers to drop the suffix; delete their v2-only counterparts, keeping the cross-engine-aware logic (e.g., `data_kiro.py`'s v2 history reading) that must survive.
5. Update `session_row.html`'s gate to hide the "Open in Agent orchestrator" action for v2-provider rows.
6. Rename `tests/test_web.py`'s ~180 ACP test names and rewrite `tests/acp_page.test.mjs`'s three v2-URL-absence assertions to match the single-engine reality.
7. Documentation sweep: update `ROADMAP.md`, `docs/KNOWLEDGE.md`, `AGENTS.md` references to the old v2/v3 split.
8. Coordinate the restart with the user — this cannot be verified without one, per AGENTS.md.

`/qplan` should independently assess tier (this exploration's own read: Major, given the base-class fold-in and repo-wide rename) rather than deferring to the ROADMAP item's original "engine parameter or subclass retained" framing, which predates this session's decision to go with deep retirement.

### QA environment

- PowerAtlas is the user's live, daily-use instance. `acp.py`/`web.py` changes require a restart to take effect (AGENTS.md:5,7) — never restart autonomously; coordinate timing with the user before any phase that needs one to verify.
- `tests/test_web.py` (pytest) covers backend routing/dispatch — run via the project's existing test invocation.
- `tests/acp_page.test.mjs` (`node tests/acp_page.test.mjs`) covers `acp.html`'s inline JS, including the `ENGINE`-conditional lines — not part of CI/pytest, must be run by hand after any template change.
- Live browser verification against the running instance is available and was successfully used during this exploration (a live probe of the v3 steering-command wire shape via an isolated kiro-cli subprocess, and interactive palette/composer checks against a real idle `/acp-v3` session) — the same approach can verify post-cutover behavior once a restart has been coordinated.
