# ACP v3 Follow-up Features

> **Date**: 2026-09-11
> **Status**: Draft
> **Last Updated**: <set by /qclose at archival>
> **Scope**: Mode-switcher UI, v3 self-orphan-lock suppression fix, and a research spike (with contingent implementation) on the 3 unreachable steering-type slash commands.
> **Estimated effort**: 2-4 days (higher if either live-probe spike needs iteration or turns up a negative finding requiring a design pivot)

---

## Intent

### Problem statement & desired outcomes

`plans/done/260909-1127_ACP_V3_PRODUCTION_HARDENING.md` left several smaller, independent follow-up items tracked in `plans/ROADMAP.md`'s "ACP v3 Follow-up" section. This plan bundles the subset that remains in scope after this session's exploration narrowed the original list:

1. A mode-switcher UI letting a user pick `spec`/`quick-spec`/`bug-fix`/`plan` modes for a v3 session.
2. A fix for v3's self-orphan-lock suppression gap (D32): `presence._scan()`'s orphan-lock guard only ever sees `_Supervisor._publish_live()`'s real pid; `_SupervisorV3._publish_live()` always publishes a sentinel `pid=0`, so the guard can never achieve genuine v3 self-orphan suppression.
3. A research spike on the 3 steering-type slash commands (`architecture-selection`, `quick-spec`, `bug-fix`) that are currently unreachable from the palette — this session's live probe found a concrete, untested lead (the `_kiro/knowledge` extension method) rather than resolving it, so this plan scopes the investigation, not a guaranteed fix.

Two items originally on the candidate list were explicitly dropped from scope during this session (not carried into this plan): Linux cross-platform support (removed from `plans/ROADMAP.md` entirely — no current Linux deployment target to build or test against) and `_pending_permission` expiry/timeout (removed from `plans/ROADMAP.md` — resolved as a pure config change, not code: the user will set `acp_prompt_silence_seconds` to `7200` (2h, the existing clamp ceiling) directly, no plan work needed).

### Success criteria

- A mode-switcher control exists in the ACP UI for v3 sessions, letting the user select among `spec`/`quick-spec`/`bug-fix`/`plan`. Detailed UI shape is intentionally left for `/qplan` to design, not fixed here.
- `_SupervisorV3._publish_live()` (or its post-cutover renamed equivalent, see Sequencing note below) publishes its own real agent pid to `presence._acp_live` instead of the `pid=0` sentinel, and `presence._scan()`'s orphan-lock guard correctly suppresses a genuine v3 self-orphan.
- The steering-palette investigation either produces a working trigger path for the 3 commands (if `_kiro/knowledge` or an equivalent resolves `contextQuery` into usable content) or documents concretely why it cannot, updating `plans/ROADMAP.md` accordingly either way — this is a spike with a documented outcome, not an open-ended commitment to ship a fix.

### Scope boundaries & non-goals

- **Sequencing note (important for `/qplan`)**: this plan's code touches `_SupervisorV3`/`presence.py` and possibly `acp.html`'s command-palette logic — the same surfaces `plans/260911_ACP_V2_TO_V3_ENGINE_CUTOVER.md` renames and restructures. The two plans are independent in scope but not in naming: if the cutover plan lands first, this plan should target the renamed (unsuffixed) engine; if this plan lands first, it should use the current `_v3`-suffixed names, and the cutover plan's rename sweep will pick up whatever this plan adds. Whoever runs `/qplan` on either file should check current code state rather than assume a fixed execution order — this plan does not mandate one.

  **Resolved during this `/qplan` pass**: the cutover plan (`plans/done/260912-2301_ACP_V2_TO_V3_ENGINE_CUTOVER.md`) landed first, on 2026-09-12 (commits `c89a8e7`, `a127813`, `817ebf2`). Every phase below targets the renamed, unsuffixed engine (`_Supervisor`, `_handle_commands_execute`, etc.) per this note's own contingency. See §1 Current State.
- **Out of scope**: Linux cross-platform support (dropped from ROADMAP.md this session), `_pending_permission` expiry/timeout (dropped from ROADMAP.md this session, resolved as a config change).
- **Out of scope**: the v2-to-v3 engine cutover itself — landed separately, see `plans/done/260912-2301_ACP_V2_TO_V3_ENGINE_CUTOVER.md`.
- **Out of scope**: changing an already-created session's mode after the fact (Phase 3) — no wire mechanism for this was found; mode selection is create-time only.
- **Non-goal**: the steering-palette item is scoped as a research spike with a documented outcome, not a guaranteed working feature — see Success criteria.

---

## 1) Current State

*(Content below re-verified against live source during this `/qplan` pass, 2026-09-16 — five days after the plan's original `Date` header, which records the earlier `/qexplore` intent-capture session and is preserved unchanged per plan-file convention. Where this section states an elapsed-time figure — e.g. the Declined-proposal window in §6 Risk Assessment — it is computed against 2026-09-16, not the header date.)*

### The v2-to-v3 cutover has landed — this plan targets the current, single-engine code

Confirmed directly (`git log --oneline -20 -- src/power_atlas/acp.py`) and by reading the code: `plans/done/260912-2301_ACP_V2_TO_V3_ENGINE_CUTOVER.md` landed 2026-09-12 across commits `c89a8e7` (`434 insertions(+), 2544 deletions(-)`), `a127813`, `817ebf2`. There is now exactly one supervisor class, `class _Supervisor` at `acp.py:2588`, speaking the v3 protocol directly (`ACP_ARGS = ("acp", "--agent-engine", "v3")`, `acp.py:620`). `_SupervisorV3`/`_supervisor_v3` no longer exist anywhere in the file (0 matches). Every `_v3`-suffixed survivor was renamed to its unsuffixed form (e.g. the module-level command handler is now `_handle_commands_execute`, not `_handle_commands_execute_v3`). All line numbers below are current, post-cutover line numbers, re-verified this session — the original exploration's citations (written 2026-09-11, pre-cutover) had drifted by hundreds to thousands of lines and are superseded here.

The cutover plan's own Phase 1 step 8 explicitly deferred the D32 sentinel fix to this plan (`acp.py:4871-4876`'s current docstring): *"Passes pid=0 rather than `agent_pid()` (F11) ... Left as-is post-cutover (Phase 1 step 8) — this is shared ground with `plans/260911_ACP_V3_FOLLOWUP_FEATURES.md`'s orphan-lock fix (D32) ... whichever plan lands second must re-read this method's then-current state."* This plan is that second plan.

### Item 2 — D32 orphan-lock fix: exact current mechanism

- `_Supervisor._publish_live()` (`acp.py:4868-4884`) is the sole publisher (the cutover deleted the old v2+v3 union — `memory/MEMORY.md`'s `_publish_live` entry, updated 2026-09-12, confirms: *"`_publish_live()` now publishes only `self.sessions`; there is only one engine's sessions left to union."*). It currently always calls `hook(frozenset(self.sessions), 0)` — the `0` sentinel, never `self.agent_pid()`.
- `agent_pid()` (`acp.py:2814-2824`) returns `self._proc.pid` (the real spawned kiro-cli process's pid) or `None` if `self._proc is None` (not yet started). Deliberately not gated on `poll()` — a recycled pid can only cost a more specific message elsewhere, never grant a load that should be refused.
- The wiring is a direct pass-through: `web.py:648`, `acp.set_sessions_changed_hook(presence.publish_acp_sessions)` — no adapter, so whatever `_publish_live()` passes as its second argument becomes `presence.publish_acp_sessions`'s `agent_pid` argument verbatim, stored in the module global `presence._acp_live = (ids, agent_pid)`.
- `presence._scan()`'s D32 guard (`presence.py:760-762`) currently reads:
  ```python
  if (provider == "kiro-cli" and acp_pid is not None
          and pid == acp_pid and sid not in acp_sids):
      continue
  ```
  Scoped to `provider == "kiro-cli"` only — not `_KIRO_PROVIDERS` (`presence.py:85`, `frozenset({"kiro-cli", "kiro-cli-v3"})`), which the other three sidecar-reconciliation checks in the same function already use. `presence.py`'s own `_sidecar_records()` (`presence.py:345-406`) labels a kiro lock file `"kiro-cli-v3"` whenever its session id is `sess_`-prefixed (`presence.py:367`) — which, post-cutover, is every session the single live `_Supervisor` creates or loads (v3's session id always comes from `result._meta.id`, confirmed `sess_`-prefixed in existing tests). So today, virtually every session our own agent could orphan is labeled `"kiro-cli-v3"`, and the guard's `provider == "kiro-cli"` condition never matches it — this is the D32 gap, precisely.
- **Why widening the guard is now safe** (it previously was not, and was already tried once and reverted for exactly that reason): the guard's own comment block (`presence.py:722-759`) explains the historical reason it was narrowly scoped — in the pre-cutover dual-supervisor world, `acp_pid` came from the *v2* supervisor's `_publish_live()` only, since v3's own `_publish_live()` always passed the `pid=0` sentinel; widening the provider check without also fixing the sentinel would have made the guard's condition read as though it protected v3 self-orphans while structurally being unable to (a v3 lock's `pid` field could never equal `acp_pid`, since `acp_pid` never named the v3 agent). This is not hypothetical: `plans/done/260909-1127_ACP_V3_PRODUCTION_HARDENING.md`'s Phase 1 cycle-2 review (finding #3, line 761) records that exactly this widening was tried once already and reverted — *"The D32 self-orphan-suppression guard's `_KIRO_PROVIDERS` widening was inert for v3 (`acp_pid` never comes from the v3 supervisor). Fixed (cycle 2) — narrowed that one guard back to `kiro-cli`-only."* The revert reason was inertness (dead, misleading code), not a correctness hazard from over-suppression — so this is not a repeat of a rejected approach, it is the same widening made effective by first fixing the upstream sentinel this plan's step 1 addresses. Existing test `test_presence_does_not_hide_a_v3_agent_orphaned_lock` (`tests/test_data.py:2741-2768`) documents the current, still-inert state as a known, accepted gap. Post-cutover there is exactly one supervisor and one hook — once `_publish_live()` stops passing the sentinel, `acp_pid` unambiguously names the current (single) agent's real pid, and widening the provider check becomes correct rather than misleading.
- Existing test coverage that pins today's behavior and must change: `tests/test_web.py:19756-19785` (`test_supervisor_publish_live_sentinel_pid`, asserts `pid == 0`) and `tests/test_data.py:2741-2768` (asserts the v3 orphan is NOT suppressed, i.e. documents the gap). Tests that are already gap-agnostic and need no change: `tests/test_data.py:2771-2787` (`test_presence_leaves_a_foreign_v3_kiro_lock_alone`) and `tests/test_data.py:2790-2814` (`test_presence_family_tolerance_does_not_defeat_recycled_pid_check`) — both explicitly documented as unaffected by whether the guard's provider check is narrow or widened.

### Item 1 — mode-switcher: exact current mechanism and precedent

- PowerAtlas already sends a `modeId` field on **every** `session/new` and `session/load` call, always hardcoded to `"kiro_default"` — this is the one concrete wire hook found, not a from-scratch feature:
  ```python
  # acp.py:675-689
  def _build_kas_session_params(mode_id: str = "kiro_default") -> dict[str, Any]:
      """Build the _meta.kiro fragment for session/new and session/load requests.

      The _meta.kiro key is a KAS protocol field accepted by KiroSessionMetaSchema
      in acp-server.js. ...
      """
      return {
          "_meta": {
              "kiro": {
                  "modeId": mode_id,
                  "steering": [{**d} for d in _OVERLAY_STEERING],
              }
          },
      }
  ```
  Call sites: `new_session()` at `acp.py:4700` (`params = _build_kas_session_params()`, no argument) and `load_session()` at `acp.py:4801-4804` (`**_build_kas_session_params()`, spread with no argument — **even a resumed/loaded session re-sends `modeId: "kiro_default"`**, which is itself informative: it suggests a session's mode is fixed at creation and this repeated default on load is either ignored by kiro-cli or redundantly re-asserts the session's own already-persisted mode; Phase 2 should observe which).
- Independent supporting evidence the concept is real: `data_kiro_v3.py:688` parses `data.get("agentMode", "")` out of a v3 session's `session.json` into `Session.extra_fields["agentMode"]` — captured but never read or displayed anywhere else in the codebase (0 other hits for `agentMode` or `extra_fields` consumption). This is a second, independent signal (on-disk, not wire) that kiro-cli v3 tracks a per-session mode.
- No CLI-level flag exists for this: `kiro-cli acp --help` (run locally, read-only) lists `--agent`, `--model`, `--effort`, `-a/--trust-all-tools`, `--trust-tools`, `--agent-engine {v1,v2,v3}`, `--auth-method`, `-v/--verbose` — no `--mode` flag, and no help text mentioning `spec`/`quick-spec`/`bug-fix`/`plan`. No vendored kiro-cli docs or schema exist in this repo. So `modeId` (or, possibly, `agentMode` set some other way) is the only locally-visible candidate activation mechanism — untested, not confirmed.
- `plans/ROADMAP.md`'s "ACP v3 Follow-up" section (lines ~28-43) confirms the framing verbatim: *"`/acp-v3` stays hardcoded to `kiro_default`, matching that `/acp` v2 has no mode-selection UI either."* and *"spec mode only adds a `\"spec\"` tag to the builtin-tools list and relies on ordinary `session/update` subtypes plus `session/request_permission`"* (the mechanism SC-9's interactive-permission UI, `plans/done/260909-1127_ACP_V3_PRODUCTION_HARDENING.md` Phase 6, was built to support without itself building a mode-selection path).
- **Naming collision risk (two, not one)**: `web.py` already uses `mode` as a query-string parameter name on `/api/acp/sessions` (`mode=recent` for flat-vs-grouped listing shape — `web.py:2471, 2521, 2540, 2586`), and `acp.html` already has an unrelated, fully-built "mode" picker: the **send mode** (Steer vs. Queue) toggle (`#acpModeToggle`/`#acpModeMenu`, `acp.html:289-311` HTML, `563-611` CSS, `693-723` JS state incl. `SEND_MODE_KEY = 'pa_acp_send_mode'` localStorage persistence, `5749-5776` event wiring) — entirely client-side, no backend round-trip, persisted only in `localStorage`. Any new mode-switcher UI must not collide with either existing meaning.
- **Reusable UI precedent**: the send-mode toggle is explicitly documented in-file as a deliberate choice: *"Custom mode picker — replaces the native `<select>` so the popover opens just above the button on mobile rather than centre-screen"* (`acp.html:297-298`). This interaction pattern (custom button + popover menu, not a native `<select>`) is the established precedent to reuse for the new control — not the send-mode toggle's own elements.
- **Where a new session is actually created client-side**: both "New session" buttons (`#acpRailNew` at `acp.html:85`, `#acpNew` at `acp.html:162`) open a shared modal, `#acpPicker` ("New session — where?", `acp.html:332-350`, comment at `acp.html:332-333`: *"Both `New session` buttons open this instead of creating [directly]"*), which round-trips through `/api/acp/workspaces` for workspace search. Confirming the choice, `#acpPicker` sends the actual creation message: `send('new', { cwd: cwd }, null)` (`acp.html:4773, 4796`) → server `_handle_new(conn, payload)` (`acp.py:5661-5690`, reads `payload.get("cwd")`, validates it's a string, calls `_supervisor.new_session(cwd)`) → `_Supervisor.new_session(cwd)` (`acp.py:4687-4720`, calls `_build_kas_session_params()` with no argument). **Note**: PowerAtlas's *other* "new session" mechanism, `/api/new-session` (`web.py:4306-4330`, used by the dashboard's per-workspace quick-launch buttons from earlier work this session) is unrelated — it calls `launcher.launch_session(...)`, spawning an independent OS-level terminal process outside the ACP wire protocol entirely, and has no `modeId` concept to thread. This plan's mode-switcher only applies to `/acp`'s in-page WebSocket session creation.

### Item 3 — steering-palette: exact current mechanism (post-cutover line numbers)

- The single surviving palette filter lives in `_on_notification`'s `available_commands_update` branch, `acp.py:4150-4165`:
  ```python
  if kind == "available_commands_update":
      available = update.get("availableCommands") or []
      commands = [
          {"name": _as_text(c.get("name")).lstrip("/"),
           "description": _as_text(c.get("description"))}
          for c in available
          if isinstance(c, dict)
          and _as_text(c.get("name"))
          and not (
              (isinstance(c.get("_meta"), dict)
               and isinstance(c["_meta"].get("kiro"), dict)
               and c["_meta"]["kiro"].get("type") in
                   ("skill", "steering", "prompt", "custom-agent"))
              or _as_text(c.get("serverName")).startswith("skill:")
          )
      ][:MAX_COMMANDS_COUNT]
      skills = _parse_skills(available)
  ```
  (The cutover deleted a duplicate v2-side copy of this filter that predated it, consolidating to this one site.) `_parse_skills` (`acp.py:1187-1206`) separately picks up `type == "skill"` entries only — steering-type entries land in neither `commands` nor `skills`.
- `contextQuery` (the lookup key the prior exploration's live probe found on the wire for these 3 entries, e.g. `"global:architecture-selection"`) has **zero references** anywhere in `acp.py` — completely inert, never read. `_kiro/knowledge` and `extensionMethods` likewise have **zero references** — no code has ever called this method. The only existing `_kiro/*` call site is `_kiro/auth/getAccessToken` (`acp.py:2605, 4531-4534, 4622`), and it is **inbound** (the agent calls PowerAtlas), not a template for an outbound call.
- The generic outbound-RPC helper every wire call in the file goes through: `async def _request(self, method, params, timeout=REQUEST_TIMEOUT_SECONDS)` (`acp.py:3091-3124`), builds a JSON-RPC 2.0 envelope, tracks by request id. The two closest existing outbound patterns to mirror for a hypothetical new method: `commands_options` (`acp.py:3939-3950`) and `commands_execute` (`acp.py:3952-3967`, calls `self._request("_kiro.dev/commands/execute", {"sessionId": session_id, "command": {"command": name, "args": {}}}, timeout=_INACTIVITY)`).
- The module-level handler `_handle_commands_execute` (renamed from `_handle_commands_execute_v3`; `acp.py:6166-6240`) — full behavior: six early guards (envelope/read-only/unknown-session/not-subscribed/close-in-progress/turn-in-progress, `6168-6196`), extracts and validates `name` (`6197-6208`), builds `valid_names` from the session's own cached `commands` + `skills` catalogue and refuses `bad_payload`/"Unknown command." if not found (`6209-6215`), then (on success) marks the session used/inflight, special-cases `"compact"`, and calls `await _supervisor.commands_execute(session_id, name)` (`6216-6227`) — **this function itself has zero steering-specific logic; it is a generic name-forwarding dispatcher.** This is independent evidence against assuming `_kiro/knowledge`-based content resolution is the only viable path: if kiro-cli's agent side already handles these 3 built-in command names natively when given directly to `commands/execute`, no content-resolution step is needed at all.
- Client-side trace, confirming exactly one path reaches the server: server broadcasts a `commands` WS frame → stored verbatim in `sessionCommands` (`acp.html:5332-5339`) → `showCommandDropdown`/`renderCommandDropdown` (`acp.html:1910-1935`, `1942-1984`) builds the palette `<li>` entries from `sessionCommands` + `sessionSkills` → `confirmCommandSelection()` (`acp.html:2066-2110`) is the only place a selection turns into network traffic: for a skill (`isSkill` badge present), it inserts `"/name "` as **plain prompt text** for the user to send as a normal chat turn — explicit doc comment at `acp.html:2058-2060`: *"kiro-cli dispatches skills via prompt text, not via the commands/execute wire method"* — **not** a call to `commands_execute`; for a plain command, it calls `send('commands_execute', { name: name }, sessionId)` (`acp.html:2109`), the only client call site reaching `_handle_commands_execute`. Because the 3 steering entries never enter `sessionCommands`/`sessionSkills` at all (filtered server-side), they generate no `<li>` and there is no click-time refusal to work around — they are invisible before any click happens.
- This confirms **three** testable hypotheses, not one, ordered by implementation cost — see Phase 4.

### Probe harness (Phase 2 and Phase 4) — no committed script exists; build from acp.py's own client code

**Review finding (Architect, High)**: the "disposable, isolated `kiro-cli acp --agent-engine v3` subprocess" technique this plan's Phase 2 and Phase 4 both depend on exists only as conversational memory from this plan's prior exploration session — no script, module, or documented protocol for it is committed anywhere in this repo (confirmed: no `*probe*` files repo-wide). Since 3 of this plan's 5 phases are gated on these two spikes, a fresh-context `/qdev` sub-agent needs a concrete starting point, not an inference exercise. Build the probe from `acp.py`'s own working client code, which already implements exactly this protocol against the real (non-disposable) agent:
- **Spawn**: mirror `ensure_started()`'s subprocess creation — the same executable and args PowerAtlas itself uses, `[exe, *ACP_ARGS]` where `ACP_ARGS = ("acp", "--agent-engine", "v3")` (`acp.py:620`) — but pointed at a scratch/disposable `cwd`, never the live PowerAtlas working directory or an existing session's.
- **Wire framing**: newline-delimited JSON-RPC 2.0 over the subprocess's stdin/stdout — confirmed via `proc.stdin.write(line + "\n")` (`acp.py:3241`) and the corresponding `readline()`-based reader (`acp.py:3250` and surrounding). One JSON object per line, no `Content-Length` framing.
- **Request/response shape**: mirror `_request()`'s envelope construction (`acp.py:3091-3124`) — `{"jsonrpc": "2.0", "id": <n>, "method": <method>, "params": <params>}`, matching responses by `id`.
- **Call sequence**: `initialize` first (`acp.py:2853`'s call site shows the params shape), then `session/new` with `_build_kas_session_params(mode_id=...)` (`acp.py:675-689`) merged in, then whatever probe-specific call the spike needs (a chat turn, `_kiro.dev/commands/execute`, or `_kiro/knowledge`).
- **Isolation**: a throwaway Python script (or an interactive `python -c`/REPL session) talking directly to the subprocess — never route through `_Supervisor`, `_registry`, or any other live-PowerAtlas-process state. See "Probe cleanup" below for what to do with the disposable session's on-disk artifacts afterward.

This is a template to build from, not a committed harness — the probe script itself is throwaway scratch work, not a plan deliverable, so it does not appear in any phase's File scope or exit criteria as a committed file.

### Probe cleanup — the disposable subprocess is isolated from the live *process*, not from the shared on-disk session store

**Review finding (Reliability engineer, Medium)**: "isolated, disposable, never touches the live PowerAtlas process" is true of the process but not of `~/.kiro/sessions` — kiro-cli's own on-disk persistence root (`V3_SESSIONS_ROOT`, `data_kiro_v3.py:32`), written by *any* `kiro-cli acp` invocation, probe or supervisor-managed alike. PowerAtlas's own cache-rebuild code (`data_kiro_v3.py:153-225`) walks every hash dir under this root unscoped — not filtered to sessions the live supervisor created — so a probe's leftover `session.json`/`messages.jsonl` becomes visible to PowerAtlas's own session-listing logic (and thus the live `/acp` UI) the moment its cache next rebuilds. Low severity (the probe process is dead by then, so it can never show as a false *live* dot — this is UI clutter, not a correctness or liveness bug), but real. Both Phase 2 and Phase 4's Method must end with an explicit cleanup step: delete each probe's `sess_<uuid>` directory under `~/.kiro/sessions/<hash>/` once the probe's findings are recorded.

---

## 2) Goal

Close the D32 self-orphan-lock gap for v3 (small, fully specified, low-risk), then empirically resolve — via disposable, isolated live probes, never touching the live PowerAtlas process — whether the existing `modeId` wire field actually activates kiro-cli's task modes and whether the steering-palette commands can be reached via the cheapest of three ordered hypotheses; ship UI/wiring for whichever mechanisms the probes confirm, and document (in `plans/ROADMAP.md`) whichever they don't.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| D32 fix scope | Publish the real agent pid from `_publish_live()` (`self.agent_pid()`) and widen `presence._scan()`'s guard to `provider in _KIRO_PROVIDERS` | Leave the `pid=0` sentinel and accept the v3 gap indefinitely | The original F11 concern ("false liveness in a unioned v2+v3 world") no longer applies post-cutover: there is exactly one supervisor, one hook, so the real pid unambiguously names the current agent |
| Mode-switcher activation mechanism | Test the already-wired `modeId` field first (Phase 2 spike) before writing any UI | Design a net-new activation mechanism from scratch | The wire slot already exists, is exercised on every `session/new`/`session/load`, and is explicitly documented as a real KAS protocol field — the cheapest, already-integrated hypothesis |
| Mode selection timing | Create-time only, inside the existing `#acpPicker` modal | A persistent per-turn toggle mirroring send-mode (steer/queue) | `modeId` is a `session/new` parameter; `session/load` always re-sends the hardcoded default even for a resumed session, consistent with mode being fixed at creation and not changeable afterward |
| New mode-switcher identifier naming | Distinct names avoiding the bare word "mode" (e.g. `taskMode`/`pa_acp_task_mode`) | Reuse `mode`/`#acpModeToggle` | Two unrelated existing meanings of "mode" already exist in this codebase (`web.py`'s `mode=recent` query param; acp.html's send-mode picker) — a third collision is a readability hazard, not a technical necessity |
| Steering-palette fix approach | Test cheapest-first: H1 (plain chat text, no PowerAtlas code change if it works) → H2 (direct `commands_execute` call, small filter-only fix if it works) → H3 (`_kiro/knowledge` resolution, most involved) | Commit directly to `_kiro/knowledge` per the original exploration's guess | `commands_execute`'s handler is confirmed to be a generic, steering-agnostic dispatcher (acp.py:6216-6227) — independent evidence a cheaper hypothesis may already work; `contextQuery`/`_kiro/knowledge` are both confirmed completely unused (0 references), so H3 is unvalidated territory, not a known-good fallback |
| Q7 (carried from exploration, re-verified unchanged) | No dedicated permission-timeout logic; rely on the existing configurable backstop | Auto-refuse; a configurable default option | Backstop (`acp_prompt_silence_seconds`) is already a shared, user-configurable knob — no code change needed |
| Q8 (carried from exploration, re-verified unchanged) | Linux support removed from `plans/ROADMAP.md` entirely, out of scope | Defer rather than remove | No current Linux deployment target to build or test against |
| Q9/Q10 (carried from exploration, re-verified unchanged) | No code change; user sets `acp_prompt_silence_seconds` to `7200` directly | Widen the 7200s clamp ceiling in code | User accepted the existing 2h ceiling as sufficient; this is a pure config change, already actioned outside this plan |

## 4) External Dependencies & Costs

None — pure code changes plus local, disposable process probes (the same `kiro-cli acp --agent-engine v3` subprocess technique already proven in this plan's own prior exploration session). No infra, CI/CD, IAM, cloud resources, or third-party services involved.

### Cost impact

Negligible, one-time, non-recurring — not literally "None" (review finding, Reliability engineer Low): Phase 2 and Phase 4's probe turns are sent to a live kiro-cli agent, which plausibly invokes the same LLM backend PowerAtlas depends on in production, incurring a small one-off API cost for the handful of probe turns each spike sends. No recurring or scaling cost.

## 5) Implementation Phases

### Phase 1: Fix v3 self-orphan-lock suppression (D32) [QA] [P:2]

**Goal**: Make `presence._scan()`'s orphan-lock guard correctly suppress a lock our own agent orphaned, for v3 sessions, closing the gap the engine cutover explicitly deferred here.

**File scope** (for parallel-eligibility with Phase 2): `src/power_atlas/acp.py` (~4868-4884), `src/power_atlas/presence.py` (~722-762), `tests/test_web.py` (~19756-19785), `tests/test_data.py` (~2741-2768), `memory/MEMORY.md` (`_publish_live` entry).

1. `acp.py`, `_Supervisor._publish_live()` (`acp.py:4868-4884`): change
   ```python
   hook(frozenset(self.sessions), 0)
   ```
   to
   ```python
   hook(frozenset(self.sessions), self.agent_pid())
   ```
   Rewrite the docstring: remove the now-stale F11 rationale ("passes pid=0 rather than agent_pid() ... could cause false liveness") and the "Left as-is post-cutover ... whichever plan lands second" hand-off note (this plan is that hand-off, now resolved). Replace with a short note that this closes D32 for v3: with exactly one supervisor and one hook, `self.agent_pid()` unambiguously names the current agent, so the historical false-liveness concern (scoped to the pre-cutover dual-supervisor union) no longer applies.

2. `presence.py`, `_scan()`'s D32 guard (`presence.py:760-762`): change
   ```python
   if (provider == "kiro-cli" and acp_pid is not None
           and pid == acp_pid and sid not in acp_sids):
       continue
   ```
   to
   ```python
   if (provider in _KIRO_PROVIDERS and acp_pid is not None
           and pid == acp_pid and sid not in acp_sids):
       continue
   ```
   Rewrite the long explanatory comment above it (`presence.py:722-759`) — it currently argues at length why widening to `_KIRO_PROVIDERS` "would read as though it protects v3 self-orphans ... but structurally cannot," because `acp_pid` used to never name v3's agent. That premise is now false after step 1 above; replace the comment with one describing the current, closed state and cross-reference this plan by name for the historical record (mirroring how the comment already cross-references D10/D32/plan history elsewhere in this file).

3. `tests/test_web.py`, `test_supervisor_publish_live_sentinel_pid` (`tests/test_web.py:19756-19785`): currently constructs `acp_mod._Supervisor.__new__(acp_mod._Supervisor)` (skips `__init__`, so `_proc` is never set) and asserts `pid == 0`. Rewrite: construct via `acp_mod._Supervisor()` (real `__init__`; `_proc` starts `None`), assert `_publish_live()` emits `pid=None` when unbound (`self.agent_pid()` returns `None` for `_proc is None`), then bind a lightweight stand-in (e.g. `types.SimpleNamespace(pid=4321)`) to `sup._proc` and assert a second `_publish_live()` call emits the real pid `4321`. Rename to `test_supervisor_publish_live_uses_the_real_agent_pid` and rewrite the docstring (it currently cites this exact plan by name as pending future work — update to say the fix landed here).

4. `tests/test_data.py`, `test_presence_does_not_hide_a_v3_agent_orphaned_lock` (`tests/test_data.py:2741-2768`): currently asserts `is_live("kiro-cli-v3", ..., v3_sid) is True` (documents the gap: NOT suppressed). Invert: rename to `test_presence_hides_a_v3_agent_orphaned_lock` (mirroring `test_presence_hides_a_lock_our_own_agent_orphaned`, `tests/test_data.py:2697-2720`, two tests above it in the same file — `test_presence_leaves_a_foreign_kiro_lock_alone` sits between them), change the final assertion to `is False`, and rewrite the docstring to describe D32 as now closed for v3 too (mirror the v2 test's docstring shape), removing "known, currently-unaddressed gap" language. Leave `test_presence_leaves_a_foreign_v3_kiro_lock_alone` (`tests/test_data.py:2771-2787`) and `test_presence_family_tolerance_does_not_defeat_recycled_pid_check` (`tests/test_data.py:2790-2814`) unchanged — both are already gap-agnostic per their own docstrings.

5. `memory/MEMORY.md`: append a new `**Update (<implementation date>, 260911_ACP_V3_FOLLOWUP_FEATURES)**:` paragraph to the `_publish_live` entry (heading `### acp.py and presence.py may not import each other — wiring goes through web.py`, `memory/MEMORY.md:245`) immediately after its current last line (`memory/MEMORY.md:251`, the `2026-09-12` cutover update) and before the blank line at `:252` that separates it from the next entry — confirmed via this session's own doc-impact grep as the exact current end of the entry.

**Exit criteria**:
- [ ] `_Supervisor._publish_live()` passes `self.agent_pid()`, not a hardcoded sentinel
- [ ] `presence._scan()`'s D32 guard checks `provider in _KIRO_PROVIDERS`
- [ ] `test_supervisor_publish_live_sentinel_pid` rewritten/renamed, covering both the unbound-pid-is-None and bound-pid-is-real cases
- [ ] `test_presence_does_not_hide_a_v3_agent_orphaned_lock` inverted/renamed to assert suppression now happens
- [ ] `memory/MEMORY.md`'s `_publish_live` entry gets a new dated Update paragraph
- [ ] `pytest tests/test_web.py tests/test_data.py --timeout=300` passes
- [ ] Manual live verification (after a user-approved restart — this touches `acp.py`/`presence.py`, never restart autonomously per `AGENTS.md:5`): trigger a failed `session/load` or an abrupt close on a v3 session, confirm the dashboard's live dot correctly reflects the orphaned lock now being suppressed

### Phase 2: Mode-activation live-probe spike [P:1]

**Goal**: Determine, empirically, whether the `modeId` field already sent on every `session/new`/`session/load` (`acp.py:675-689`, currently always `"kiro_default"`) is the real activation mechanism for `spec`/`quick-spec`/`bug-fix`/`plan`, and if so what's observably different — resolving this plan's Success Criteria requirement before any UI is built atop it.

**Method**: reuse the disposable, isolated `kiro-cli acp --agent-engine v3` subprocess technique already proven in this plan's own prior exploration session (never touches the live PowerAtlas process).

1. Baseline: start a disposable session with `modeId: "kiro_default"` (today's default), send one lightweight user turn designed to surface tool/capability/mode differences (e.g. asking what built-in tools or mode the agent currently has), capture the full raw JSON-RPC exchange — the `session/new` response and any `session/update`/notification frames received before the turn completes or a bounded idle timeout.
2. Repeat with `modeId: "spec"` — the one mode `plans/ROADMAP.md` already documents a mechanism for ("adds a `\"spec\"` tag to the builtin-tools list"). Diff against the baseline for any observable difference (tool list, capability list, response content, session metadata, or the `agentMode` field `data_kiro_v3.py:688` already parses from `session.json` on disk).
3. Also observe whether `session/load`'s own always-`"kiro_default"` `modeId` (`acp.py:4801-4804`) has any effect on an already-created `"spec"`-mode session when reloaded — this settles the "create-time only" design decision empirically rather than by inference alone.
4. If a difference is found for `"spec"`, repeat more briefly for `"quick-spec"`, `"bug-fix"`, `"plan"` to confirm the field accepts all four names without erroring — full behavioral characterization of each is not required, only: does the round-trip succeed, and does something observably change.
5. If no difference from baseline is found for `"spec"` even after a real turn, treat the hypothesis as unconfirmed rather than silently failed — a mode difference could plausibly only surface in longer/multi-turn interactions or system-prompt-level behavior a single-turn probe wouldn't catch. Record this explicitly as a residual unknown, not a negative result.
6. **Cleanup** (review finding, Reliability engineer Medium — see "Probe cleanup" above): once findings are recorded, delete each probe session's `sess_<uuid>` directory under `~/.kiro/sessions/<hash>/` — a leftover probe session is otherwise picked up by PowerAtlas's own unscoped session-store scan (`data_kiro_v3.py:153-225`) and surfaces as clutter in the live `/acp` UI.
7. **Partial-confirmation case** (review finding, Architect Low): if the four mode names don't uniformly confirm (e.g. `spec` shows an observable difference but `quick-spec` errors or shows none), do not treat this as all-or-nothing. Record a per-mode verdict, and Phase 3's gate (below) resolves per-mode: ship only the subset of modes individually confirmed working; a mode that errors or shows no difference is excluded from the picker and filed under Follow-up Work rather than blocking the modes that did confirm.

> **Rejected:** Building the mode-switcher UI directly against `modeId` without first confirming activation. — `modeId` is currently *always* hardcoded to `"kiro_default"` in every code path that exists today, so there is zero existing evidence PowerAtlas has ever exercised any other value; shipping a UI atop an unverified field risks a control that silently does nothing. **Use instead:** this spike, gating Phase 3.

**Exit criteria**:
- [ ] Probe findings recorded (in this plan's §9 Implementation Divergences) for at least `kiro_default` vs `spec`, with a clear verdict: confirmed-working / confirmed-no-observable-difference / inconclusive
- [ ] `session/load`'s effect (or lack thereof) on an already-created non-default-mode session is recorded
- [ ] If confirmed-working: the exact literal wire values for all four modes are settled (confirmed to be `spec`/`quick-spec`/`bug-fix`/`plan`, or corrected if the probe surfaces different literal values)
- [ ] If not confirmed-working: `plans/ROADMAP.md`'s mode-switcher bullet is updated with the negative/inconclusive finding, filed under this plan's Follow-up Work (Deferred) rather than closed as done, and Phase 3 is skipped per its own gate

### Phase 3: Mode-switcher UI [QA] (gated on Phase 2 confirming the mechanism works)

**Goal**: let a user pick a mode when creating a new `/acp` session, wiring the choice through to the `modeId` param Phase 2 confirmed is real.

**Gate**: implement this phase only if Phase 2's finding is confirmed-working for at least one mode. If Phase 2 found no observable difference or was inconclusive for **all four** modes: do not build speculative UI atop an unconfirmed mechanism — update `plans/ROADMAP.md`'s mode-switcher bullet to record the negative finding, close this plan's mode-switcher Success Criterion as "spiked, not shippable — see Phase 2 finding," and stop; skip the rest of this phase. **Partial confirmation** (some modes confirmed, others not — Phase 2 step 7): proceed with this phase, but the picker offers only the confirmed subset; a mode Phase 2 could not confirm is omitted from the picker's option list (not shipped as a guess) and filed under Follow-up Work (Deferred) rather than blocking the modes that did confirm.

**File scope** (confirmed-working branch): `src/power_atlas/acp.py` (~675-689 `_build_kas_session_params`, ~4687-4720 `new_session`, ~5661-5690 `_handle_new`), `src/power_atlas/templates/acp.html` (~332-350 `#acpPicker`, new CSS/JS mirroring the existing `#acpModeToggle` popover-button pattern at ~289-311/563-611/693-723/5749-5776), `tests/test_web.py`, `tests/acp_page.test.mjs`.

Design (confirmed-working branch):
- **Timing**: create-time only, inside the existing `#acpPicker` "New session — where?" modal — not a per-turn toggle. `modeId` is a `session/new` parameter; Phase 2 step 3 directly tests whether `session/load` even honors it for a resumed session, so this design decision is confirmed rather than assumed by the time this phase starts.
- **Naming**: new identifiers only, never the bare word "mode" — e.g. `taskMode`/`acpTaskMode`/`pa_acp_task_mode` — to avoid the two existing collisions (`web.py`'s `mode=recent` query param; `acp.html`'s `#acpModeToggle` send-mode picker).
- **UI pattern**: reuse the *interaction pattern* already established and justified in-file for `#acpModeToggle` (custom button + popover menu instead of a native `<select>`, "so the popover opens just above the button on mobile rather than centre-screen" — `acp.html:297-298`) for a new control inside `#acpPicker`. Do not reuse `#acpModeToggle`/`#acpModeMenu`'s own elements — different feature, different lifecycle (send-mode persists across the whole session in the conversation toolbar; task-mode is chosen once at creation and has no meaning afterward).
- **Backend wiring**: `_handle_new` (`acp.py:5661-5690`) reads an optional `payload.get("mode")`, validates it's `None` or one of the closed set `{"kiro_default", "spec", "quick-spec", "bug-fix", "plan"}` (rejecting anything else with `bad_payload`, mirroring the existing `raw_cwd` string-type check at `acp.py:5664-5666`), and passes it to `_supervisor.new_session(cwd, mode=raw_mode)`. `new_session()` (`acp.py:4687-4720`) gains a `mode: str | None = None` parameter, threading it to `_build_kas_session_params(mode_id=mode or "kiro_default")` at the current no-argument call site (`acp.py:4700`).
- **Client wiring**: the two `send('new', ...)` call sites are not symmetric and both need the picked mode threaded through correctly (review finding, Architect High / Senior engineer Medium — independently caught by both):
  - The immediate-create path (`acp.html:4773`) gains the mode directly: `send('new', { cwd: cwd, mode: pickedTaskMode }, null)`.
  - The deferred close-then-create path is different: `pendingCreate` (`acp.html:4780`, currently `{ cwd: cwd }` only, set before an async session-close completes) must also carry the mode — `pendingCreate = { cwd: cwd, mode: pickedTaskMode }` — and `pickerRunPending()`'s later `send('new', { cwd: want.cwd }, null)` (`acp.html:4796`) must read it back as `send('new', { cwd: want.cwd, mode: want.mode }, null)`. A literal `pickedTaskMode` reference inlined at line 4796 would be out of scope by the time `pickerRunPending()` actually runs — the picked value only survives via `pendingCreate`.
- **Backwards compatibility**: omitting `mode` (or picking the default option) must reproduce today's exact `"kiro_default"` behavior unchanged — every existing session-creation flow and its test coverage must continue to pass.

**Exit criteria**:
- [ ] (confirmed-working branch) New task-mode control added inside `#acpPicker`, reusing the existing custom-popover-button interaction pattern, under new non-colliding identifiers
- [ ] `mode` threaded end-to-end: client picker → `send('new', ...)` → `_handle_new` validation → `_supervisor.new_session(cwd, mode=...)` → `_build_kas_session_params(mode_id=...)` → `session/new` wire call
- [ ] Omitting/defaulting the picker reproduces today's exact `kiro_default` behavior (regression check)
- [ ] Invalid/unrecognized mode values rejected with `bad_payload`, never silently passed through to the agent
- [ ] `tests/test_web.py` gains coverage for `_handle_new`'s mode validation and `new_session`'s mode threading
- [ ] `node tests/acp_page.test.mjs` passes
- [ ] Manual live verification in `/acp`: create a session with a non-default mode picked, confirm (via whatever signal Phase 2's probe found observable) the mode actually took effect — requires a user-approved PowerAtlas restart first, since this phase touches `acp.py` (never restart autonomously, `AGENTS.md:5`)
- [ ] (negative-finding branch only) `plans/ROADMAP.md`'s mode-switcher bullet updated to record the finding; this success criterion closed as spiked-not-shippable

### Phase 4: Steering-palette live-probe spike

**Goal**: resolve, empirically, which (if any) of three ordered hypotheses makes the 3 unreachable steering-type slash commands (`architecture-selection`, `quick-spec`, `bug-fix`) reachable, testing cheapest-first:

- **H1 (cheapest — potentially zero PowerAtlas code change)**: send a plain chat turn whose full text is exactly `/architecture-selection`, mirroring the already-working skill-dispatch convention (PowerAtlas inserts `"/skill-name "` as plain prompt text and kiro-cli resolves it agent-side — `acp.html:2058-2060`). Observe whether the agent resolves and acts on the steering document. **Verification method matters here** (review finding, Senior engineer Medium): unlike H2/H3, which get an unambiguous JSON-RPC success/error signal, H1's only signal is the agent's freeform chat reply — a generically plausible-sounding answer about "architecture selection" is not evidence of genuine resolution (the LLM could produce one from training alone, with no steering document ever read). Do not treat a plausible-sounding reply alone as "H1 confirmed." Instead check the raw JSON-RPC trace for a `tool_call`/resource-fetch notification correlated with the turn, or compare the response's specific content against the actual steering document text (currently a placeholder at `_OVERLAY_STEERING`, `acp.py:666-672` — resolve what `architecture-selection`'s real steering content is before comparing), or check for a resolution marker in a `session/update` notification's `_meta`. Only a positive, source-traceable signal counts as H1 confirmed.
- **H2**: call `_kiro.dev/commands/execute` directly (bypassing PowerAtlas's own client-side `valid_names` catalogue gate entirely — `acp.py:6209-6215`) with `command: {command: "architecture-selection", args: {}}`. If this works, the fix is small: stop excluding these 3 entries from the palette filter (`acp.py:4150-4165`) so they reach `sessionCommands` and flow through the *already-wired* `commands_execute` click path (`acp.html:2109`) unchanged.
- **H3 (most expensive)**: call `_kiro/knowledge` with `{"contextQuery": "global:architecture-selection"}` (the extension method named in `agentCapabilities._meta.kiro.extensionMethods`, confirmed never called by any code in this repo — 0 hits). If this works, the fix requires a new `_Supervisor` outbound RPC method mirroring `commands_execute`/`commands_options` (`acp.py:3939-3967`, both built on the generic `_request()` helper at `acp.py:3091-3124`) plus a new `confirmCommandSelection()` branch (`acp.html:2066-2110`) that awaits content resolution before inserting text — architecturally different from the synchronous `commands_execute` case.

**Method**: same disposable, isolated `kiro-cli acp --agent-engine v3` subprocess technique as Phase 2 and this plan's own prior exploration session (never touches the live PowerAtlas process). Test H1, then H2, then H3, **stopping at the first one that demonstrably works** — do not test more expensive hypotheses once a cheaper one succeeds, and do not re-test an already-failed cheaper hypothesis.

> **Rejected:** Assuming `_kiro/knowledge` (H3) is the only viable path, per the original exploration session's framing. — This session's own research found `_kiro.dev/commands/execute`'s handler (`acp.py:6216-6227`) is a generic, steering-agnostic name-forwarding dispatcher with zero steering-specific logic — independent evidence a cheaper hypothesis (H1 or H2) may already work without any content-resolution step. **Use instead:** test cheapest-first rather than committing to H3's heavier design up front.

**Exit criteria**:
- [ ] H1 tested with a source-traceable verification signal (not a plausible-sounding reply alone); result recorded
- [ ] H2 tested (only if H1 failed); result recorded
- [ ] H3 tested (only if H1 and H2 both failed); result recorded
- [ ] Probe session(s) cleaned up under `~/.kiro/sessions/<hash>/` once findings are recorded (same rationale as Phase 2's cleanup step)
- [ ] If all three fail: `plans/ROADMAP.md`'s steering-palette bullet and this plan's Success Criteria are updated with the documented negative finding, filed under Follow-up Work (Deferred), and Phase 5 is skipped per its own gate

### Phase 5: Steering-palette fix [QA] (gated on Phase 4 confirming a working hypothesis)

**Goal**: implement whichever hypothesis Phase 4 confirmed, making the 3 steering commands reachable from the palette.

**Gate**: implement only if Phase 4 confirmed H1, H2, or H3. If all three failed: confirm `plans/ROADMAP.md` and this plan's Success Criteria already reflect Phase 4's negative finding (no code change) and stop.

File scope and design depend on which hypothesis won:
- **If H1 won**: no backend/client code change beyond the palette filter — the "unmatched entry sent as plain chat" path already produces the working behavior once typed. Fix: stop excluding these 3 entries from the palette filter (`acp.py:4150-4165`) so they're visible/clickable instead of requiring blind typing, and extend `confirmCommandSelection()` (`acp.html:2066-2110`) to insert `"/name "` as plain prompt text for a steering-type entry (generalize the existing `isSkill` branch, or add a parallel `isSteering` case).
- **If H2 won**: `acp.py:4150-4165` filter change only — the existing `commands_execute` click path (`acp.html:2109`, `_handle_commands_execute` at `acp.py:6166-6240`) already works unchanged once the entries are visible and in `valid_names`.
- **If H3 won**: `acp.py:4150-4165` filter change, a new `_Supervisor` outbound RPC method for `_kiro/knowledge` (mirroring `acp.py:3939-3967`), and a new `confirmCommandSelection()` branch (`acp.html:2066-2110`) that awaits the resolution RPC before inserting text, including a loading/pending state in the UI while resolution is in flight.

**Exit criteria** (adapt to the winning hypothesis):
- [ ] The 3 steering entries (`architecture-selection`, `quick-spec`, `bug-fix`) are visible and clickable in the `/acp` command palette
- [ ] Selecting one produces the correct agent-side behavior (per whichever mechanism Phase 4 confirmed)
- [ ] Existing palette behavior for all other entry types is unchanged — regression check that the filter change does not accidentally admit `"prompt"`/`"custom-agent"`-type entries too
- [ ] `tests/test_web.py` gains coverage for the filter change and (if H2/H3) the new dispatch path
- [ ] `node tests/acp_page.test.mjs` passes
- [ ] `plans/ROADMAP.md`'s steering-palette bullet updated to record the plan as closed, citing the winning mechanism
- [ ] Manual live verification in `/acp`: click all 3 entries from the palette and confirm each produces the intended steering behavior — requires a user-approved PowerAtlas restart first, since this phase touches `acp.py` (never restart autonomously, `AGENTS.md:5`)

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| Phase 2/4 spikes find no working mechanism | Mode-switcher UI / steering-palette fix cannot ship as originally envisioned | Phases 3/5 are explicitly gated on their spike's finding — a negative result closes the item via a `plans/ROADMAP.md` update instead of speculative code (addressed within this plan) |
| Running Phase 2's and Phase 4's live disposable-subprocess probes back-to-back or concurrently is an unexercised scenario (each spawns a real external `kiro-cli` process) | Possible flaky/misleading probe results if run concurrently; low risk of interference with the live PowerAtlas instance since both are isolated | Phases are deliberately not marked `[P:N]` parallel-eligible with each other despite having no file-scope conflict; each probe individually reuses the already-proven isolated/disposable technique from this plan's own prior exploration session (accepted — not a plan blocker) |
| A disposable probe's on-disk artifacts (`session.json`, `messages.jsonl` under `~/.kiro/sessions/<hash>/`) are isolated from the live PowerAtlas *process* but not from the shared kiro-cli session store PowerAtlas's own cache-rebuild unscoped-scans (`data_kiro_v3.py:153-225`) — found in review (Reliability engineer, Medium) | A leftover probe session could surface as stray clutter in the live `/acp` UI's session listing; no liveness/correctness impact (the probe process is already dead by the time anyone views the dashboard, so it can never show a false-live dot) | Phase 2 and Phase 4 both gained an explicit cleanup exit criterion — delete the probe's `sess_<uuid>` directory once findings are recorded (addressed within this plan) |
| D32 fix's widened guard (`_KIRO_PROVIDERS`) could theoretically over-suppress a legitimate `kiro-cli-v3` session if a real terminal-started v3 session's lock ever coincidentally shares a pid with our own agent | A live dashboard dot would wrongly disappear | The guard requires exact real-pid equality *and* `sid not in acp_sids` — a terminal-started session's lock names the terminal process's own (different) pid, never PowerAtlas's agent pid; `test_presence_leaves_a_foreign_v3_kiro_lock_alone` already covers this and is documented as unaffected by the widening (addressed within this plan — Phase 1 exit criteria keep this test green) |
| Steering-palette filter change (Phase 5, if H1/H2 wins) could accidentally admit `"prompt"`/`"custom-agent"`-type entries if the exclusion condition is loosened too broadly | Palette shows entries that don't work when clicked | Exit criteria explicitly require a regression check that other filtered types stay filtered (addressed within this plan) |
| `memory/MEMORY.md`'s Declined list (`memory/MEMORY.md:340`) records a prior agent proposal — *"`commands_execute` must whitelist every item type the palette can dispatch"* — declined by the user on 2026-08-14 (no reason recorded), still inside its 60-day re-proposal suppression window (33 days elapsed as of this plan's writing) | Adjacent to, though not identical to, Phase 5's H1/H2 design (which changes the upstream palette-catalogue filter, `acp.py:4150-4165`, not `_handle_commands_execute`'s own `valid_names` check) — worth the user's explicit awareness before approving Phase 5, since the declined framing and this phase's filter-widening direction are close enough that a silent proceed risks re-raising something already declined | Surfaced to the user directly at plan hand-off (this is not a memory re-proposal, so the suppression window does not block writing this plan — but the plan does not act on Phase 5 without the user seeing this citation first) |
| The corollary D32 residual noted during exploration — `_acp_live` is a single last-writer-wins global, so any consumer's guard is transiently defeated in the window right after a mutation from the other side — self-heals within one sweep interval | Momentary, self-correcting false-live dot | Explicitly out of scope for this plan (unchanged from exploration's original framing); tracked as accepted in `presence.py`'s own comments, not reopened here |

## 7) Verification

- `pytest tests/test_web.py tests/test_data.py --timeout=300` — full suite; `--timeout` per `AGENTS.md`'s guidance for session-lifecycle-adjacent changes
- `node tests/acp_page.test.mjs` after any `acp.html` inline-script change (Phase 3, Phase 5)
- `.venv-PowerAtlas/Scripts/python _check_test_names.py` before every commit (pre-commit hook also runs this; run by hand too per `AGENTS.md`)
- Manual live verification in `/acp` for Phase 1 (orphan-lock), Phase 3 (mode-switcher, confirmed-working branch), Phase 5 (steering-palette, if shipped) — each `acp.py`/`presence.py` change needs a user-approved PowerAtlas restart (never restart autonomously, `AGENTS.md:5`); `acp.html`-only changes are hot-reloadable via hard browser reload, no restart needed
- Disposable-subprocess live probes for Phase 2 and Phase 4 — isolated, never touch the live PowerAtlas process, same technique this plan's own prior exploration session already proved out

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `memory/MEMORY.md:245-251` | Append a dated Update paragraph to the `_publish_live` entry (insert after line 251, before the blank line at 252) recording D32 closed for v3 (real pid published, guard widened to `_KIRO_PROVIDERS`) | 1 |
| `plans/ROADMAP.md:45` | Close out the "ACP v3 Follow-up" section's D32/orphan-lock bullet ("Self-orphan-lock suppression (D32)") | 1 |
| `plans/ROADMAP.md:43` | Update the mode-switcher bullet — either close it out (confirmed-working branch) or record the negative finding (Phase 2 gate) | 3 |
| `plans/ROADMAP.md:47` | Update the steering-palette bullet ("3 steering-type slash-command palette entries have no working trigger in `/acp`") — either close it out citing the winning mechanism, or record the negative finding (Phase 4 gate) | 5 |
| `plans/ROADMAP.md:34-37` | Revise the section-intro sentence that currently reads "...three of them ... are now tracked directly in `plans/260911_ACP_V3_FOLLOWUP_FEATURES.md`" — stale once all three bullets are closed rather than tracked. Phase 5 runs last, so do this update there once Phase 1/3/5's own bullet edits have all landed (regardless of whether Phase 3/5 shipped code or only recorded a negative finding) | 5 |

Confirmed via this session's dedicated doc-impact grep sub-agent (all tracked `*.md` repo-wide excluding `plans/done/`, plus `docs/KNOWLEDGE.md` and `README.md`): no hits for any of the searched terms (`_publish_live`, `_build_kas_session_params`, `modeId`, `agentMode`, `commands_execute`, `contextQuery`, `_kiro/knowledge`, "D32", "mode-switcher", "steering-palette") in `README.md` or `docs/KNOWLEDGE.md` beyond the rows above — confirms all three items are internal-mechanism or in-page-UI changes with no install/usage-surface impact per `AGENTS.md`'s README update criterion. One incidental hit worth noting for implementer awareness (not a required update): `README.md:389` already describes `GET /api/acp/workspaces` as "the workspace list ... the create picker reads" — the same `#acpPicker` modal Phase 3 adds a control to; if Phase 3's UI change makes this description inaccurate (e.g. the picker's purpose broadens beyond "where"), revisit that line, but the endpoint itself is unchanged so no update is mandated up front.

## 9) Implementation Divergences from Plan
<Reserved -- filled during implementation. Phase 2 and Phase 4's probe findings belong here.>

## Follow-up Work (Deferred)

1. **D32's corollary residual (transient v2-guard-shaped defeat after any mutation).** `_acp_live` is a single last-writer-wins global; a consumer's guard is momentarily defeated in the window right after a mutation publishes, self-healing within one sweep interval. Accepted as a known limitation during the original exploration session; not reopened by this plan. Source: this plan's original Exploration Discovery, "Risks & mitigations."
2. **(Conditional) Mode-switcher UI, if Phase 2 finds no working mechanism.** See Phase 2/3's gate — if `modeId` turns out not to be a real activation mechanism (or the finding is inconclusive), the feature cannot ship as scoped here; `plans/ROADMAP.md` carries the negative finding forward for whoever investigates next.
3. **(Conditional) Steering-palette fix, if Phase 4 finds no working hypothesis among H1/H2/H3.** See Phase 4/5's gate — same disposition pattern as item 2.

## Review Log

### 2026-09-16 — Plan Review (via /qplan Step 4)

9 findings (1 High, 3 Medium, 5 Low) across 3 personas (Architect with gap-critic lens, Senior engineer, Reliability engineer). 9 auto-resolved. One review cycle run, per explicit user instruction to cap this plan at 1 cycle (default is up to 3).

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | Architect: Phase 2/4's disposable-probe technique has no committed script or documented protocol anywhere in the repo, leaving a fresh `/qdev` sub-agent nothing concrete to build from. | Fixed — added a "Probe harness" subsection in §1 templating the spawn/framing/request shape off `acp.py`'s own client code (`ACP_ARGS`, `_request()`, newline-delimited JSON-RPC over stdin/stdout). |
| 2 | Medium | Architect and Senior engineer independently caught the same gap: Phase 3's `pendingCreate` deferred close-then-create path (`acp.html:4780`) doesn't carry the picked mode, so the second `send('new', ...)` call site (`acp.html:4796`) would silently drop it. | Fixed — Phase 3's client-wiring design rewritten to capture `mode` into `pendingCreate` and read it back as `want.mode`. |
| 3 | Medium | Reliability engineer: a disposable probe's on-disk session artifacts are isolated from the live PowerAtlas process but not from the shared `~/.kiro/sessions` store PowerAtlas's own cache-rebuild scans unscoped, risking stray clutter in the live `/acp` UI. | Fixed — added an explicit cleanup exit criterion to both Phase 2 and Phase 4, plus a new Risk Assessment row. |
| 4 | Medium | Senior engineer: Phase 4's H1 hypothesis test has no way to distinguish a genuine steering-document resolution from a plausible-sounding hallucinated chat reply. | Fixed — added explicit verification-method guidance (raw JSON-RPC trace correlation, content comparison, or `_meta` resolution marker) to H1. |
| 5 | Low | Reliability engineer: §4's "Cost impact: None" is inaccurate — probe turns hit a live LLM backend. | Fixed — reworded to "negligible, one-time, non-recurring." |
| 6 | Low | Reliability engineer: Phase 3/5 exit criteria don't restate the user-approved-restart requirement inline the way Phase 1's does. | Fixed — added inline restart reminders to both phases' manual-verification exit criteria. |
| 7 | Low | Architect: Phase 2's exit criteria had no guidance for a partial-confirmation outcome (some modes confirmed, others not). | Fixed — added per-mode verdict guidance to Phase 2 and a matching partial-confirmation branch to Phase 3's gate. |
| 8 | Low | Architect: the header `Date` field (2026-09-11) predates internal content that re-verifies against 2026-09-12/2026-09-16 facts, risking reader confusion about currency. | Fixed — added a re-verification note at the top of §1 rather than altering the header's creation-date semantics. |
| 9 | Low | Senior engineer: two minor citation-precision nits — a test-adjacency claim off by one test, and a code-range citation off by one line. | Fixed — both corrected. |

All three personas independently verified the plan's file:line citations against live source (20+ spot-checks each, zero fabricated or stale citations found beyond the two Low nits above) and confirmed no reintroduction of either failure class from `plans/done/260909-1127_ACP_V3_PRODUCTION_HARDENING.md`'s Phase 1 incident history (the crashed-process-permanently-held bug, and the once-reverted `_KIRO_PROVIDERS` widening this plan's Phase 1 now makes effective rather than repeating). Pre-fix confidence scores: Architect 78%, Senior engineer 85%, Reliability engineer did not state a numeric score but raised no blocking objection. No unresolved High or Medium findings remain.

## Harness Improvement Opportunities
<Reserved -- appended during /qexplore, /qplan and /qdev when harness friction is felt.>
