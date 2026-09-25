# ACP Permission Modes: Yolo, Auto and Manual

> **Date**: 2026-09-24
> **Status**: Complete  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Last Updated**: 2026-09-25 09:00
> **Scope**: Replace the on/off ACP permission profile with three permission modes (Yolo, Auto, Manual), an always-on hard-deny floor, and a plain-language Manual-mode rule editor that compiles to the derived agent
> **Tier**: Major
> **Estimated effort**: 6-8 days

---

## Completion Summary

Delivered: Yolo / Auto (disabled) / Manual permission modes with an always-on Always blocked floor compiled into the
derived agent, a plain-language Manual rule editor, the prompt-card "Allow, and always in new sessions…" button, a
fail-closed Default-session gate with self-heal, persisted outside-change notices, and the migration from the on/off
setting. All SCs met; see the Post-Implementation Review. Final state: pytest 2239 passed, page tests 805/805.

### Acknowledged at archival

- Accepted — Implementation Divergences, Phases 0-1 (15 entries in § 9, reviewed in the phase and final reviews).
- Accepted — Implementation Divergences, Phases 2-3 (7 entries in § 9).
- Accepted — Implementation Divergences, Step 9 (SE7 leaf module and allow-rule route change; the new
  `tests/permission_pattern_cases.json` test data file; final-review behaviour changes).
- Promoted: qexplore/shared.md — a Probe-gate consent request may ride with the first question (agent-playbook ae7e76e).
- Accepted (harness opportunity): research report flagged as instruction-shaped for quoting vendor docs; no change unless it recurs.
- Promoted: qreview Verification phase — corroborated findings need no verifier; single-source findings may share one (ae7e76e).
- Promoted: shared/AGENTS.md — rate-limit sub-agent failures retry once after the reset, beyond /qcouncil (ae7e76e).
- Follow-up plan intended (harness opportunity): find a working fix for the Claude-in-Chrome MCP extension being disconnected, so Claude keeps using native browser tools rather than standalone Playwright (user, 2026-09-25).
- Promoted: qbrowser-test — Chrome MCP ref clicks right after navigation can be dropped (ae7e76e).
- Promoted: qreview spawn contract and shared/AGENTS.md — sub-agent findings return as text where report files are refused (ae7e76e).
- Promoted: PowerAtlas AGENTS.md — `[hidden] { display: none }` convention for classes that set `display` (archive commit).
- Promoted: qdev Step 4 — shape of a results-only phase (ae7e76e).
- Promoted: qexplore — write the Assumptions (unconfirmed) adjunct at any depth when defaults went unconfirmed (ae7e76e).
- Pass 4 documentation-ripple sweep: no stale references (remaining hits are intentional history or the migration comment).

---

## Intent

### Problem statement & desired outcomes

PowerAtlas's ACP permission setting is a boolean (`acp_permissions_enabled`). Off means sessions never ask
and nothing stops a destructive action. On means a fixed rule set asks about almost everything, and the only
way to reduce prompts is to edit package data. Neither state reduces approval fatigue without giving up all
protection, and neither lets the user shape the rules.

Desired outcome: three **permission modes**, chosen in the settings menu:

- **Yolo** — every action runs without asking, except a small **Always blocked** floor that applies silently in
  every mode.
- **Manual** — the Always blocked floor, plus user-editable rules that let many requests run without asking and
  escalate the rest to the user as a permission prompt. **Protected** items (writes to agent, steering and skill
  configuration) always prompt, even when their row says Allow, and each can be switched to block outright.
- **Auto** — deferred. An LLM decides Manual's prompts. The UI slot is built now, shown disabled with a
  "coming soon — behaves like Manual" note.

Rule editing must be intuitive: plain-language rows per kind of action, a default per row, exception lists,
and an "Always allow in new sessions" button on each permission prompt. PowerAtlas compiles the rules into
kiro-cli's own permission model inside the derived agent. The base agent is never modified.

### Success criteria

- SC-1: The settings menu offers Yolo, Auto (disabled, labelled "coming soon — behaves like Manual") and Manual
  in place of the on/off switch; only `yolo` and `manual` can be stored.
- SC-2: In Yolo, a new Default ACP session runs shell, write, web, MCP, sub-agent and skill actions without
  PowerAtlas-originated prompts, and every Always blocked item is refused silently with kiro-cli's denial text.
  kiro-cli's own non-overridable built-in asks (measured in Phase 0 P-0.10) are disclosed in the Yolo
  description.
- SC-3: PowerAtlas never creates a Default session without the Always blocked floor: when the derived agent is
  not in effect, Default creation is refused (D-34). Loaded sessions and vendor task modes are outside this
  guarantee, and the UI says so. The floor, identical in every mode, contains:
  - `fs_read` deny on credential stores: `~/.ssh/**`, `~/.aws/**`, `~/.azure/**`, `~/.config/gcloud/**`,
    kiro-cli's token files under `~/.kiro/`, and PowerAtlas's `local-secret` and `remote-secret` files;
  - `shell` deny patterns that mention those credential stores (for example `*.ssh*`, `*.aws*`), labelled as
    catching accidental reads, not deliberate rephrasing;
  - `fs_write` deny on PowerAtlas's own derived agent (`~/.kiro/agents/poweratlas-acp.md`);
  - kiro-cli's own built-in denies (`~/.kiro/settings/`, `~/.kiro/workspace-roots/`), listed so the UI shows
    the complete floor;
- SC-4: In Manual, a new Default session follows the user's rules: allowed patterns run silently, everything
  else in an Ask row raises a permission prompt, Block rows are refused, and Protected paths prompt even under an
  Allow row.
- SC-5: The Manual rule editor shows one row per kind of action (Read files, Write files, Run commands, Web
  fetch, Web search, MCP tools, Sub-agents, Skills, Powers) with an Allow / Ask / Block default and an exception
  list; the compiled `permissions:` block always carries the `exclude` entries kiro-cli needs, so no allow rule
  is silently defeated by a blanket ask.
- SC-6: Each permission prompt card on a loopback page offers "Allow, and always in new sessions…" when the
  prompt came from a Manual row rule, which adds the command prefix, path pattern or MCP tool to the matching
  row.
- SC-7: The mode picker and rule editor live only on the dashboard, which is loopback-only; the prompt-card
  button appears only on loopback pages; a POST to any permission route from a remote peer is refused.
- SC-8: Existing configs migrate: `acp_permissions_enabled = true` becomes Manual seeded from the current overlay
  (the agents-folder write deny kept, shell rules widened only where Phase 0 shows it is safe); `false` and fresh
  installs become Yolo; the old key is dropped on the next save.
- SC-9: "In effect" is true only when the derived agent on disk matches what the current settings compile to; an
  edited rule set is never classified as stale, and a genuinely stale or foreign file is still reported.
- SC-10: The UI states that a mode or rule change applies to sessions created afterwards.

### Scope boundaries & non-goals

In scope:
- Default ACP sessions that bind the derived agent, on /acp and the dashboard.
- Mode storage and migration in `config.toml`, rule compilation in `agent_profile.py`, the in-effect check,
  the settings UI, the prompt-card button, routes, tests and documentation.

Out of scope:
- **Auto mode's decider.** Deferred. Its intended design is recorded in § 3, decision D-5.
- Vendor task modes (Spec, Plan and so on), terminal sessions launched from PowerAtlas, and Kiro Crew. The floor
  does not reach them, and the settings copy says so (D-3).
- Editing the base agent or `~/.kiro/settings/permissions.yaml` (D-1, D-3).
- Hard-denying home or root deletion commands. Left to Auto mode (D-6).
- Per-workspace rules. Rules are global.
- Remote editing of rules or mode (D-9).
- kiro-cli capabilities never observed on ACP (`context`, `diagnostics`, `sandbox_network`) stay unnamed and
  inherit user scope, as today (Follow-up 8).

---

## 1) Current State

**Setting and storage.** `acp_permissions_enabled: bool = False` and `acp_permission_base_agent: str =
"kiro_default"` (`config.py`, block headed `# 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 1.`,
~109-110). `load_config` checks types only and drops a wrong-typed value (~604-616); a corrupt file falls back to
all defaults (~596-600). Value sanitisation follows the `remote_bind_address` precedent (~703-719). Unknown keys
survive in `_extra` unless named in `_LEGACY_KEYS` (~43, `{"trust_all_tools", "terminal_command"}`); the legacy
filter runs on the raw TOML before `_extra` (~618). Structured list settings with load-time normalisation already
exist: `launch_profiles` (~60, normalised ~625-650). `save_config` is last-writer-wins over the whole file
(~734); 22 call sites in `config.py`/`web.py` do load → mutate → save with no shared lock. The live config on this
machine has `acp_permissions_enabled = false`. Its launcher `env` entries hold `AUTH_TOKEN_*` values.

**Derived agent generation** (`agent_profile.py`). `sync_from_config()` holds `_generation_lock` (a plain,
non-reentrant `threading.Lock`) across `load_config()` and `_apply_locked(enabled=bool, base_agent=...)`. On:
`_generate` reads `~/.kiro/agents/<base>.md` (raising if missing), splices the package-data overlay
`src/power_atlas/agents/permissions.yaml` in textually, verifies the **staged** file by text comparison, then
`os.replace`s it over whatever is at the target — including a file PowerAtlas did not write. Off: `_remove`
deletes only a marker-carrying file. `overlay_text()` caches the overlay. `derived_block_state()` (no arguments)
returns `absent` / `on` / `stale` / `unknown` by byte-comparing the on-disk block with the cached overlay.
`GenerationStatus` carries `attempted, ok, error, enabled, base_agent`.

**Gate.** `web._acp_permission_state(config)` (`def _acp_permission_state`, ~4075) returns
`in_effect = enabled and derived_block_state() == "on"`. `web._derived_agent_in_effect` (~572-590) is wired as
`acp.mode_gate_hook` and takes no lock. `acp._derived_mode_in_effect` runs it via `asyncio.to_thread` with no
timeout. `acp._default_mode_binding(in_effect)` binds `poweratlas-acp` or `kiro_default`; `_handle_new` refuses an
explicit derived-agent request when not in effect and fails closed if the hook raises; `load_session` falls back
to `kiro_default` if the hook raises. User-visible refusal strings in `_handle_new` (~6478-6491) name the
"permission profile" and its "off" state. Vendor task modes never consult the hook. `_startup_sync_derived_agent`
(~512-528) stops waiting after 3 s but its thread keeps running and keeps the lock.

**Routes.** `GET/POST /api/acp-permissions` (`web.py` ~4114-4142): POST takes a literal boolean `enabled`, saves,
awaits `_sync_derived_agent()`, returns the state dict. The base agent saves through `/api/save-setting`
(~4707-4734) as save then sync, outside any lock. `/` (the dashboard) and both permission routes are absent from
`_REMOTE_ALLOWED_PATHS` (~1426-1453), whose comment says `/` never will be; remote peers get a 403. New POST
routes inherit the Origin/Referer check (~998-999) and the `pa_local` cookie gate. A same-user local process can
mint that cookie (AGENTS.md's QA recipe; prior plan R-19).

**Permission prompt path.** `_Supervisor._on_permission_request` (`acp.py`) validates, stores
`_pending_permission[opaque_id]`, and emits `permission_request {requestId, sessionId, toolCall:{title}, consent,
options}`; `consent` is the `_project_consent` allowlist (`_CONSENT_TEXT_FIELDS = ("capability", "resource",
"scope", "source")` plus `matchedRule.{capability, effect}`; the rule's `match`/`exclude` lists are dropped). The
session record (`_new_session_record`) does not store the bound mode. The shared renderer `addPermissionRequest`
(`static/transcript-renderer.js`, whose `PERMISSION_CONSENT_FIELDS` mirrors the server allowlist and whose
capability-words comment cites `agents/permissions.yaml`) draws one button per offered option; ACP offers only
`allow_once`, `reject_once`, `reject_always`. /acp exposes `ACP_LOCAL`; the dashboard template (`web.py` ~1943) does
not, and cannot be reached remotely.

**Settings UI.** Dashboard gear menu, "Agent permissions" (`index.html` ids `acpPermToggle`, `acpPermBadge`,
`acpPermDesc`, `acpPermBaseAgent`, `acpPermScopeNote`, `acpPermWarn`); JS `renderAcpPermissions`,
`loadAcpPermissions`, `toggleAcpPermissions`, `_acpPermSetToggle`, `acpPermToggleKey`, `_acpPermWarnText` (with an
off-branch "turn it on and off again"), and the strings `_ACP_PERM_NEXT_BASE` / `_ACP_PERM_NEXT_REGEN`. Modal
editors are static `<dialog>` partials included into `index.html` and driven by JS
(`partials/launch_profile_modal.html`). No rule editor exists.

**Tests that depend on removed names.** `tests/test_web.py`: `TestDerivedAgentInjectionIsTextual` (~23165-23430,
~30 calls to `build_derived_agent`/`overlay_text`), `TestDerivedAgentWrite` (~23476), `TestDerivedAgentRemovalOnOff`
(~23697), `TestRuleAssemblyInvariants` (~23874; three tests conflict with the new design: no trailing `*`, agents
folder denied, no meta-capability), `TestOverlayIsReachableAtRuntime` (~23987), `TestAcpPermissionRoutes`
(~24025), `TestGenerationRunsAtStartup` (~24165), `TestSettingsSurface` (class ~12558; the permission tests
~12783-12801 post `{"enabled": true}`), 29 `_regenerate(…, enabled=…)` helper calls; the `isolated_config` fixture
(~27-83) redirects `KIRO_AGENTS_DIR` to an empty folder with no base agent. `tests/acp_page.test.mjs`: ~60 lines
pin `acpPermToggle` and the on/off copy (~4773-4839, ~10989-11288, ~11513-11541). `tests/test_config.py` holds
`load_config` tests.

**On-disk state (read 2026-09-24).** `~/.kiro/settings/permissions.yaml` is `rules: [{capability: all, effect:
allow}]`, seeded by agent-playbook `setup.ps1`. `~/.kiro/agents/kiro_default.md` is also deployed by `setup.ps1`
(it lists `kiro_default.md` among its deployed files), so a clean kiro-cli install without it has no base agent.
`~/.kiro/steering/*.md` are symlinks into the agent-playbook repo. Credential files: `~/.kiro/secrets.json`,
`%LOCALAPPDATA%\Kiro-Cli\data.sqlite3` (plus SQLite `-wal`/`-shm` sidecars), and in `%LOCALAPPDATA%\power-atlas`:
`local-secret`, `remote-secret`, `acp-secrets.bin` (DPAPI-wrapped MCP OAuth tokens, `acp.py` ~711), `config.toml`.
Windows 8.3 short names exist for these folders (`SSH~1`, `AWS~1`, `LOCAL-~1`, …).

**Measured kiro-cli behaviour.** Probes run 2026-09-24 with `tools/acp_permission_probe.py` driven by a scratch
script against a separate `kiro-cli acp --agent-engine v3` (kiro-cli 2.24.0); probe agents and session folders
deleted afterwards.

| # | Measurement | Result |
|---|---|---|
| P-A1 | Agent block `all: allow` + `shell` deny `echo pa-floor-hit` + `fs_write` deny `**/denied-dir/**` | Allowed command ran, zero prompts; denied command refused, zero prompts, text `Tool call denied by user's permissions. Rule: deny shell matching …` |
| P-A2 | `echo pa-ok2 && echo pa-floor-hit` | Refused — `&&` chains are split per sub-command |
| P-A3 | `fs_write` to `denied-dir/a.txt`, `DENIED-DIR/c.txt`, and the absolute path | All refused; only the allowed file written (Windows, case-insensitive filesystem) |
| P-B | `shell` allow `git status*` + ask `exclude ["git status*"]`, command `git status && echo pa-chain` | Prompted; `consent.triggeringResource: "echo pa-chain"`, `askType: "explicit"`, `matchedRule` carries `exclude` |
| P-C | Rewrite the bound agent file mid-session to add a deny | Not picked up; the live session ran the newly denied command |

Only `&&` was measured here; the other separators, and every gap listed in Phase 0, were measured on
2026-09-24 in Phase 0 (results in its implementation notes; gate outcomes in D-38).
Carried forward from `plans/done/260924-0525_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL.md` § 9 Phase 0:
shell rules match literal, case-sensitive text (Step 6); an unnamed capability inherits user scope (Step 7); a
blanket `ask` silently defeats a narrower `allow` unless it carries `exclude`; malformed frontmatter fails open to
user scope silently (Step 7); `allow_always` is never offered over ACP (Step 8); deleting the derived agent moves
live sessions to `vibe`. The kiro-cli documentation also lists non-overridable built-in asks on writes to
`.git/**`, `.kiro/agents/**`, `.kiro/hooks/**` and `.kiroignore` — documented, never measured.

**Prior decision this plan revisits.** On 2026-09-22 the user dropped a shell-pattern deny floor because it was
bypassable (prior plan § 9 Gate resolution). This plan re-adds a floor on a different basis, agreed during
exploration: mostly path rules, with a shell tier labelled best-effort.

## 2) Goal

Replace the boolean with a `yolo` / `manual` permission mode (Auto shown disabled), compile the Always blocked
floor plus the user's Manual rules into the derived agent, and give the user a plain-language editor and a
one-click "Always allow in new sessions" path — with "in effect" meaning "the file matches what the current
settings compile to".

## 3) Design Decisions

Decisions D-1 to D-10 are the user's answers from exploration (Q1-Q9, C1-C2). D-11 onwards are planner
decisions; D-22 onwards were added by the 2026-09-24 plan review.

| # | Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|---|
| D-1 | What PowerAtlas edits (Q1) | Rules stored in `config.toml`, compiled into the derived agent's `permissions:` block | Edit the base agent; edit `~/.kiro/settings/permissions.yaml` | Base agent edits change terminal sessions; the compiler can generate `exclude` pairs and name every capability |
| D-2 | Auto before its decider exists (Q2) | Shown disabled, "coming soon — behaves like Manual"; only `yolo`/`manual` storable | Selectable, runs Manual with a label | A setting must not claim a mode that is not running |
| D-3 | Floor reach (Q3) | Default ACP sessions bound to the derived agent only; copy states the limit | Floor in `~/.kiro/settings/permissions.yaml` | Keeps terminal sessions independent; avoids co-owning a setup.ps1 seed file |
| D-4 | Floor content tiers (Q4) | Path rules plus a shell tier labelled best-effort | Paths only; patterns as a guarantee | Superseded in detail by D-5 and D-6 |
| D-5 | Floor effect (Q5) | Always blocked = silent kiro `deny`, every mode; Yolo adds no PowerAtlas asks; Manual adds **Protected** `ask` rules for agent/steering/skill/hook writes, each switchable to Block | Floor as `ask` everywhere | User: "yolo remains yolo, no ask". Auto's future decider: answer Deny, send the reason as a steer message, escalate on repeat — reachable for `ask` prompts only |
| D-6 | Always blocked list (Q6, C1) | Credential-store reads (`fs_read`), matching best-effort `shell` patterns, writes to PowerAtlas's own derived agent, kiro's built-in settings denies | Add home/root deletion patterns | User: deletion is Auto mode's job. No further additions (PD-3, 2026-09-24) |
| D-7 | Editor shape (Q7) | One row per action kind: default Allow/Ask/Block plus allow and block pattern lists; the user may type `*` shell patterns, with the D-38 redirection warning (seeds and prefill are exact per P-0.8); seeded from the old overlay; prompt-card button in scope | Raw rule table; YAML editor | User asked for intuitive editing; the raw model has silent-failure traps |
| D-8 | Migration (Q8) | `true` → Manual, `false`/fresh → Yolo, old key in `_LEGACY_KEYS`, no Off state; derived agent always written | Keep a fourth "Off" mode | User accepted R-4, re-confirmed with its full scope (PD-4, 2026-09-24) |
| D-9 | Remote (Q9) | Posture changes only from this computer | Allow the button remotely | Posture-widening stays on the machine |
| D-10 | Terms (C2) | "permission mode", "Yolo/Auto/Manual", "Always blocked", "Protected" | "profile", "deny floor" in UI | AGENTS.md Terminology update proposed at the end of Phase 1 |
| D-11 | Rule data model | `acp_permission_rules` = `{<row>: {default, allow[], block[]}, protected_block[]}`, rows `fs_read, fs_write, shell, web_fetch, web_search, mcp, subagent, skill, power` | Free-form rule list | Uniform rows keep editor and compiler simple; "the session folder" is a seeded `./**` in `fs_read.allow` |
| D-12 | Powers row | A "Powers" row (default Ask) | Hard-code `power: ask` | Every capability must be named (capability-silence inheritance) |
| D-13 | Compilation per row | `block` → `deny match`; default `allow` → `allow` (allow list ignored); default `ask` → `allow match allow` + `ask exclude allow`; default `block` → `allow match allow` + `deny exclude allow`; empty `allow` → bare effect | User-ordered rules | Encodes most-restrictive-wins by construction; `deny`+`exclude` is probed in P-0.7 |
| D-14 | Pattern validation and emission | 1-200 characters from printable BMP only (reject C0/C1 controls, DEL, surrogates, U+2028/2029, U+FEFF), max 100 per list, not blank, not `*` or `**` alone; emitted with `json.dumps(s, ensure_ascii=False)` | Default `json.dumps` | Surrogate escapes and DEL can make kiro-cli reject the frontmatter, which fails open silently |
| D-15 | In-effect check | `derived_block_state(config)` compares the on-disk block with `compile_block(config)` and never raises (a compile error → `unknown` with the message in `generation_error`) | Byte-compare to one overlay | SC-9; otherwise every edit reads as stale |
| D-16 | Locking | `apply_settings(mutate)` holds `_generation_lock` across load, mutate, `save_config`, generate. The **only** other acquirer is `_derived_agent_in_effect`, with `acquire(timeout=2)`; timeout raises. `derived_block_state`, `compile_block`, `_apply_locked` never acquire it; routes compute the returned state after release | Lock-free gate; unbounded acquire | Closes the config-ahead-of-file window without letting a stalled generation hang session creation |
| D-16a | Locking amendment (2026-09-25, Step 9 cycle 2, C-M3) | `acknowledge_notice` is a **third** acquirer of `_generation_lock` (bounded 5 s wait): it serialises writes of `permission-notice.json` against the gate's heal. The gate may hand its hold to a worker thread that runs the heal and releases it. The lock's declaration comment in `agent_profile.py` lists all three acquirers | Serialise notice writes another way | The notice file and the derived agent must not be written concurrently; a third bounded acquirer keeps D-16's invariant (config never ahead of the file) |
| D-16b | Locking amendment (2026-09-25, /qclose, M-10) | Every acquirer of `_generation_lock` now lives in `agent_profile`: `apply_settings` (and `sync_from_config`), `gate_check` (bounded acquire, worker hand-off, heal policy) and `acknowledge_notice`. `web._derived_agent_in_effect` calls `gate_check` and only formats the cause and fix; a test forbids `web.py` from touching private `agent_profile` names | Leave the gate's locked section in `web.py` | One module owns the whole lock protocol |
| D-17 | Overlay package data | Delete `src/power_atlas/agents/permissions.yaml` and the `agents/**` package-data entry; floor, Protected set and seed become constants in `agent_profile.py` carrying the overlay's measured-semantics comments | Template the YAML | The block is computed; one source of truth |
| D-18 | `_remove` path | Delete `_remove` and the off branch | Keep for rollback | No Off state; unused code is deleted per governance |
| D-19 | Prompt-card button | "Allow, and always in new sessions…" opens an inline editor; Save adds the rule and answers this prompt with the option whose `kind` is `allow_once`; if the prompt resolved meanwhile, the rule is still saved and the card says so | Save only adds the rule | One click for an action the user evidently approves |
| D-20 | Prefill | Shell: the exact command (`triggeringResource` else `resource`), never a `*` prefix (P-0.8 gate). The interpreter, shell and destructive-verb list (`python*`, `py`, `node`, `powershell`, `pwsh`, `cmd`, `bash`, `sh`, `rm`, `del`, `Remove-Item`, `rmdir`, `rd`, `curl`, `iwr`, `Invoke-WebRequest`, `iex`, `Invoke-Expression`) still drives the D-35 warning. File: parent folder + `/**` unless the parent is empty, `.`, a drive root or the home folder, then the exact resource (P-0.6 confirmed forward-slash absolute patterns match). MCP/sub-agent/skill: `resource` exactly (P-0.9 confirmed). Web fetch: button hidden (P-0.5 gate: `consent.resource` is the host, so a URL pattern never matches). Always editable | Exact command always | Prefill comes from agent-authored text, so it must never propose the broadest rule |
| D-21 | Dashboard local flag | `var ACP_LOCAL = true` in `index.html` with a comment that `/` is loopback-only by construction | Derive per request | The dashboard cannot be served remotely; the shared renderer reads one global |
| D-22 | Manual in Phase 1 | Phase 1 ships the Manual compiler with `SEED_RULES` (no editor); Phase 2 adds the editor and custom rules | Refuse `manual` until Phase 2 | Every commit leaves migrated users on a defined, prompting rule set |
| D-23 | Migrated agents folder | `true → manual` seeds `protected_block = ["agents"]`, keeping today's `**/.kiro/agents/**` write deny | Protected ask | Honours SC-8 "seeded from the current overlay" |
| D-24 | Git seeds | Seed exact literals `git status`, `git log`, `git diff`, `git branch` (P-0.8 gate fired: `git status > x.txt` ran silently under `git status*` and wrote the file with no `fs_write` check). Keep the shell **block** patterns `git *--output*`, `git *--no-index*`, `git *--ext-diff*` as guards for prefixes the user adds | `*` prefixes (the pre-Phase-0 plan) | Exact literals cannot carry a redirection or an extra flag; `git diff --output`, `--no-index` and `git branch -D` are write/read/destroy primitives |
| D-25 | Mode load | Case-insensitive; `auto` → `manual`; any other unreadable value → `manual`, with a `mode_warning` exposed in the state payload; logged once per value per process | Junk → `yolo` | Fail toward prompting, matching Auto's "behaves like Manual" |
| D-26 | Rule normalisation | In `load_config` and again inside `compile_block`: missing row → seed row; invalid default → `ask`, keeping the row's lists; invalid **allow** patterns dropped; an invalid **block** pattern refuses generation (named error) rather than being dropped; lists capped at 100 on load; `{}` is never persisted as "no rules" — GET returns normalised rows without writing | Replace invalid rows with seed | Never narrows protection silently; capability names always complete |
| D-27 | Migration precedence | If `acp_permission_mode` exists it wins over `acp_permissions_enabled`; rules are seeded only when the table is absent or empty | Legacy key wins | Newest schema wins after a rollback and roll-forward |
| D-28 | Missing base agent | If the base agent file is absent, generate from a built-in `MINIMAL_BASE` (`description`, `tools: ["*"]`) and report "base agent not found — using a minimal agent" | Fail generation | Keeps the floor on clean installs, where `kiro_default.md` exists only if agent-playbook deployed it |
| D-29 | Foreign file at the target | `_generate` refuses to overwrite a file classified `unknown` (not PowerAtlas-marked) and reports it | Overwrite | Keeps the prior "not ours: never touch" contract |
| D-30 | Stale self-heal | When the gate hook reads `stale` and the config loads cleanly, it regenerates once (inside its 2 s budget) and re-checks | Wait for restart | Heals hand edits and lost updates without a restart |
| D-31 | Bound mode per session | The session record stores the mode PowerAtlas bound (`session/new`) or sent (`session/load`); the frame gains `ruleEligible` computed server-side: `consent.source == "agent-profile"`, `matchedRule.effect == "ask"`, no `match` on the matched rule, bound mode `poweratlas-acp` | Eligibility by capability | The button must only appear where a row rule can silence the prompt (Protected, built-in and vendor-mode prompts cannot) |
| D-32 | `apply_settings` result | Returns `{saved, generation_ok, generation_error}`; a save failure → route `ok: false`; a generation failure → `ok: true` plus a warning shown by the panel and the card | Raise | Distinguishes "nothing changed" from "saved, not yet in effect" |
| D-33 | Base-agent save | `/api/save-setting` routes `acp_permission_base_agent` through `apply_settings` | Save then sync | D-16 covers every permission-relevant writer |
| D-38 | Phase 0 gate outcomes (2026-09-24, kiro-cli 2.24.0, P-0.x and F-x) | (a) 8.3 short names are not mapped by agent-profile rules for `fs_read` (P-0.11) or `fs_write` (F-5): every floor and Protected path ships short-name glob variants; fs patterns are case-insensitive (measured for `fs_read`); credential-store copy drops guarantee wording. Trailing-dot and trailing-space spellings name a different, missing folder and are not a bypass (F-2, F-5). (b) `\` is literal in shell patterns (P-0.3): shell floor entries with a separator ship both spellings. (c) kiro-cli's built-in `kiro-scope` rules apply in every mode: asks on writes to `.git`, `.vscode`, `*.code-workspace`, `.kiro/agents`, `.kiro/hooks` (workspace and home); denies on writes to `.kiroignore`, `.kiro/settings`, `~/.kiro/workspace-roots`, `~/.kiro/sandbox-state`, `~/.kiro/web-session`, `~/.kiro/powers/installed/*/mcp.json`, `~/.kiro/cloud-cache` (P-0.10, F-5). The Yolo description and the Always blocked view list them. The built-in `.kiro/settings` deny misses `KIRO~1\settings` (F-5); the floor's short-name variant closes it. (d) web-fetch patterns are host names (`example.com`), labelled so in the editor (P-0.5). (e) The editor warns on any shell allow pattern containing `*`: it also allows output redirection to any file (P-0.8). Exact literals match the whole command, so redirection and extra arguments prompt (F-3). (f) Shell patterns are case-sensitive (F-4): the shell floor ships lower- and upper-case spellings; mixed case stays uncovered (best-effort). (g) `fs_write` rules govern the file-writing tools only; a shell redirection writes any path unchecked (P-0.8, F-6). The fs_write floor and Protected are described as file-tool rules; a best-effort shell deny `*poweratlas-acp*` is added. (h) Rules match a symlink's resolved target: a link inside a Protected folder pointing outside escapes the ask (F-1), and a link to a denied folder escapes an `fs_read` deny (F-1b). Decided in D-39 (PD-6). Not fired: P-0.1 (search tools filtered by the `fs_read` deny), P-0.2, P-0.6, P-0.7, P-0.9, P-0.12 (a sub-agent runs under the parent's rules); F-7: kiro-cli sends UTF-8 and non-ASCII patterns match | Leave the gaps undisclosed | Plan Phase 0 gate list, applied mechanically; (h) is a trade-off |

### Decisions from the plan review (user, 2026-09-24)

Escalated by the 2026-09-24 plan review; PD-1 and PD-2 went through a Full council first (§ Review Log).

| # | Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|---|
| D-34 | PD-1: Default when not in effect | **Fail closed.** `_handle_new` refuses a Default create while not in effect. The refusal names the specific cause (from the state and `generation_error`), the concrete fix (a Settings step, or a file to remove), says the fix needs this computer when the client is remote, and offers task modes as an explicit alternative that runs without the floor. Logged at WARNING. `session/load` keeps today's behaviour | Fall back to `kiro_default`; bind the stale file | User chose A; council 4-0. The old fallback was justified as "never wider than off", which no longer holds; a floorless fallback session could also make the file foreign and lock the fallback in |
| D-35 | PD-2: same-user self-widening | **Accepted, disclosed** (`User: accepted — 2026-09-24`). R-16 states that allowing an interpreter or a broad shell prefix in Manual equals allow-all, including widening future sessions via the API or `config.toml`. The editor and the prompt-card button show "allowing an interpreter is equivalent to allow-all" when a pattern's first token is an interpreter or shell (D-20 list). A posture change not made from the dashboard (detected when the D-30 self-heal regenerates from a changed mode or rules) raises a visible dashboard notice naming the new mode | Tray-granted elevation for widening changes | User chose A; council 4-0: `config.toml` sits beside the secret, so an HTTP-layer gate would guard one of two equal doors |
| D-36 | PD-3: floor additions | **None.** The floor stays as agreed in exploration (D-6). Within those items the patterns cover the files they name: `data.sqlite3*` (SQLite sidecars of the kiro token store) and `local-secret*` / `remote-secret*` (the rotation temp file) | Add the power-atlas folder, the base agent, extra shell tripwires | User chose to add nothing. Consequence recorded in R-16 and R-18: `config.toml` and the base agent stay writable by agents |
| D-37 | PD-4 and PD-5 | Both accepted (`User: accepted — 2026-09-24`): R-4 with its full scope, and R-11 rollback | Legacy key for one release; reopen D-8 | User accepted both |
| D-39 | PD-6: symlinks escape Protected and the floor (D-38h) | **Detect and name, plus a probe for enforcement** (`User: accepted — 2026-09-24`, the user chose "C + probe B" after a Full council, 3-1 for C). A resolver `find_protected_links()` in `agent_profile.py` lists symlinks and junctions one level deep under `~/.kiro/{agents,steering,skills,hooks}` with their resolved targets (unresolvable entries listed as such). It is called only when the settings state is served (`GET /api/acp-permissions`), never by `compile_block`, `derived_block_state` or the session-creation hook. The Protected section shows a per-row marker ("N linked items not covered") and the list. Workspace links and credential aliases keep a generic disclosure. Phase 1 probes whether an `fs_write` ask on an absolute resolved target (a path with spaces) catches a write through the link; if it does, Follow-up 3 carries option B (emit target rules, shown in the UI, a resolver failure adds no rules and never refuses a session) | A: generic disclosure only; B now: resolve at compile time | Council: B couples the Default-session gate to a filesystem walk over OneDrive and rests on an unmeasured matching premise; C names the real gap without touching the in-effect check |

## 4) External Dependencies & Costs

### Required external changes

| Category | Change needed | Owner | Status |
|---|---|---|---|
| Rollout / cutover | PowerAtlas restart after each Python-changing phase; migration runs on first `load_config` | User | Pending |
| Data migration / backfill | `config.toml`: `acp_permissions_enabled` → `acp_permission_mode` (+ seeded rules for `true`) | Automatic, Phase 1 | Pending |

No CI/CD, IAM, cloud, DNS, secrets or third-party changes. kiro-cli is used as installed.

### Cost impact

None.

## 5) Implementation Phases

**Parallel annotation.** No pair is parallel-eligible: every phase edits `web.py`, `index.html` or
`tests/test_web.py`, and Phases 1-3 build on the compiler in order.

### Phase 0: Pre-flight probes and gate

**Goal**: Settle the measurements the floor, the seeds and the button depend on, before code commits to them.
**File scope**: none in the product; a scratch driver in the session scratchpad;
`plans/260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL.md` § 9.
**Covers**: SC-3

Run each with `tools/acp_permission_probe.py` (import its `Probe` class for multi-prompt sessions) against a
separate `kiro-cli acp --agent-engine v3`, never the running instance. Create `pa-probe-*` agents under
`~/.kiro/agents/` with **no bare `: ` in `description`**, delete them afterwards, and delete each probe's
`~/.kiro/sessions/<hash>/` folder after checking that `agentMode` and `workspacePaths` name the probe. Use canary
files in the scratchpad (a scratch `.ssh/dummy` in the probe cwd), never real credentials.

- P-0.1 `fs_read` deny `**/.ssh/**`: blocks the read-file tool **and** kiro's grep/glob/search tools on the canary.
- P-0.2 Does a `~`-prefixed glob match at all, or must floor paths use `**/`?
- P-0.3 `shell` deny `*.ssh*`: blocks `Get-Content .ssh/dummy` and `type .ssh\dummy`; does not block
  `ssh-keygen --help` or `git status`. Record commands it does block. Probe a backslash pattern
  (`*.config\gcloud*`) and record whether `\` is literal.
- P-0.4 Protected `ask` on `**/.kiro/steering/**` beside `fs_write: allow`: a canary write prompts; a write
  through a scratch symlink's real target — prompts or not.
- P-0.5 `web_fetch` allow `["https://example.com/*"]` suppresses the prompt for `https://example.com/a`.
- P-0.6 An absolute Windows path pattern with forward slashes matches `rawInput.path` (backslashes).
- P-0.7 A hand-compiled Manual block per D-13 with: seed rows; one row default Block + an allow entry
  (`deny`+`exclude`); one row default Allow + a block entry; a non-ASCII BMP pattern (`é`) emitted with
  `ensure_ascii=False`; a backslash pattern. Expect: `git status` silent, `echo x` prompts, a Protected write
  prompts, the Block row refuses except its allow entry, the Allow row refuses its block entry, the file loads
  (not fail-open: an `echo` under an Ask row still prompts).
- P-0.8 Separators against `git status*`: `git status; echo x`, `git status | echo x`, `git status > x.txt`,
  a newline, backticks, `git status $(echo x)`, PowerShell `git status; echo x`. Record which prompt.
- P-0.9 `mcp`, `subagent`, `skill`: an allow `match` equal to the prompt's `consent.resource` suppresses the
  prompt.
- P-0.10 Built-in asks under `all: allow`: writes to `.git/x`, `.kiro/agents/x` (workspace), `.kiro/hooks/x`,
  `.kiroignore` — prompt or not; record `consent.source`, `scope` and `matchedRule` for each.
- P-0.11 Windows alias forms against `fs_read` deny `**/.ssh/**`: the 8.3 short name of the canary folder,
  `\\?\C:\…`, `\\localhost\C$\…`.
- P-0.12 Sub-agent posture: a probe-agent session spawns a `kiro_default` sub-agent that reads the canary
  `.ssh/dummy` — blocked or not.

**Gate** (apply to the D-rows and phase text before Phase 1):
- P-0.1 search tools unblocked → UI copy says "file reads and common shell spellings".
- P-0.2 `~` fails → every floor path uses `**/` (already the default).
- P-0.3 backslash not literal → drop backslash variants.
- P-0.4 symlink write not prompted → Protected copy names the gap; Follow-up 3 stays.
- P-0.5 fails → web-fetch button hidden (D-20).
- P-0.6 fails → file prefill uses `**/<last two segments>/**`.
- P-0.7 any expectation fails → STOP and re-plan D-13/D-14 with the user.
- P-0.8 any separator runs silently → no `*` prefixes in `SEED_RULES` or prefill; exact literals only; D-24 and
  D-20 revised; README says so.
- P-0.9 fails for a capability → its button and pattern chips are hidden.
- P-0.10 built-in asks prompt → SC-2 and the Yolo description list them (already worded conditionally).
- P-0.11 any alias bypasses → add short-name globs (`**/SSH~*/**` etc.) where they close it, and the UI copy for
  credential stores drops any guarantee wording.
- P-0.12 sub-agent bypasses → the floor adds `subagent` deny for any agent other than `poweratlas-acp`, or, if
  kiro-cli cannot express that, `subagent` becomes Ask in every mode — escalate to the user before choosing.

**Exit criteria**:
- [x] P-0.1 to P-0.12 recorded in § 9 with command, date, kiro-cli version and result
- [x] Every gate branch that fired is applied to the affected D-row and phase text before Phase 1 starts
- [x] `ls ~/.kiro/agents` shows no `pa-probe-*`; the probes' session folders are deleted

Implementation (2026-09-24, code: none — probe-only phase; raw results in the session scratchpad `phase0/results.json`)
All twelve probes were measured on 2026-09-24 with kiro-cli 2.24.0 against separate `kiro-cli acp --agent-engine v3` processes driven by `tools/acp_permission_probe.Probe` (drivers, per-session `res_*.json`, full `frames_*.json` and the P-0.7 YAML `p07_manual_block.yaml` are in the scratch `phase0/` folder). Every step was classified only from tool frames: *denied-by-rule* = the `Tool call denied by user's permissions. Rule: …` text with no prompt; *prompted* = a `session/request_permission` arrived (answered `reject_once`); *ran* = tool status `completed`. No agent was coerced; every session bound its `pa-probe-*` agent. Four gates fired (P-0.5, P-0.8, P-0.10, P-0.11); P-0.7 met every expectation and P-0.12 showed no bypass, so neither user-decision stop applies. In the table, `<cwd>` is the probe's scratch working directory and "Floor" means the fs_read deny `**/.ssh/**`.

| # | Probe | Command/setup | Result | Gate branch |
|---|---|---|---|---|
| P-0.1 | `fs_read` deny `**/.ssh/**` covers read and search tools | Agent `all: allow` + that deny; canary `.ssh/dummy` = `SSHCANARY`, controls `.ctl/dummy` (hidden, not denied) and `ctl2/dummy` with the same text; tools named explicitly | `read_file` and `list_directory` on `.ssh`: **denied-by-rule**. `grep_search` and `file_search`: **ran**, but results silently leave out denied paths — a whole-workspace grep returned `.ctl/dummy` and `ctl2/dummy` only, and file_search likewise omitted `.ssh/dummy` and `.aws/dummy`; `grep_search` with `includePattern` `**/.ssh/**` or `.ssh/dummy` returned "No matches found". Attribution control (S1c, `all: allow` only, same folder): grep and file_search **do** return `.ssh/dummy` and `.aws/dummy`, so the fs_read deny rule filters the search tools | Not fired (search tools are filtered, not open) |
| P-0.2 | Does a `~`-prefixed glob match? | fs_read deny `~/AppData/Local/Temp/claude/…/phase0/s1/tilde-canary/**`, then `read_file tilde-canary/dummy` | **denied-by-rule**; the denial text names the `~/…` pattern. `~` expands to the home folder | Not fired (`**/` stays the default; `~` also works) |
| P-0.3 | `shell` deny `*.ssh*`; backslash pattern | Deny `*.ssh*` and `*.config\gcloud*` (JSON `"*.config\\gcloud*"`) via `execute_pwsh` (PowerShell) | `Get-Content .ssh/dummy`: denied. `type .ssh\dummy`: denied. `ssh-keygen --help`: ran (non-zero exit, usage printed). `git status`: ran. `type .config\gcloud\creds`: **denied** by `*.config\gcloud*`. `type .config/gcloud/creds`: **ran** (not matched) — `\` is literal and a backslash pattern does not match the forward-slash spelling. Over-match: `echo notes.sshx` was denied by `*.ssh*` | Not fired (backslash literal). Else-side action: the shell floor needs both `/` and `\` variants |
| P-0.4 | Protected `ask` on `**/.kiro/steering/**` beside `fs_write: allow` | Manual block from P-0.7 (fs_write default Allow); `os.symlink` succeeded: `steerlink` → `<cwd>/.kiro/steering` | Write `.kiro/steering/probe.md`: **prompted** (`matchedRule` ask on the steering/skills/hooks patterns, `source: agent-profile`). Write `steerlink/probe2.md`: **prompted**; `consent.resource` was `.kiro/steering/probe2.md`, so kiro-cli resolves the link before matching | Not fired |
| P-0.5 | `web_fetch` allow `["https://example.com/*"]` | Manual block: allow match that URL + ask exclude it; fetch `https://example.com/a`; control `https://example.org/` | Both **prompted**. `consent.resource` is the **host** (`example.com`), not the URL, so the URL pattern never matches. Follow-up (S1b): allow `example.com` + ask exclude `example.com` — the fetch then ran with no prompt (HTTP 404) | **Fired**: web-fetch button hidden (D-20). A host pattern does work |
| P-0.6 | Absolute Windows pattern with forward slashes | fs_read deny `C:/Users/QSylvestre.POLESTAR/…/s1/abs-canary/**`; read via an absolute backslash path and via a relative path | Both **denied-by-rule**; `rawInput.path` was `c:\Users\…\abs-canary\dummy`, so drive-letter case and slash direction are both tolerated | Not fired |
| P-0.7 | Hand-compiled Manual block (D-13/D-14): full Phase 1 floor first, Protected (agents = deny, the rest = ask), rows | YAML saved in `p07_manual_block.yaml`; patterns emitted with `json.dumps(ensure_ascii=False)` | Every expectation met: (1) `git status` **ran**, no prompt. (2) `echo x` **prompted**; `triggeringResource` `echo x`, `matchedRule` with `exclude` and no `match`. (3) Protected steering write under the Allow row **prompted**. (4) Block row: `inside.txt` **ran**; `../s2_outside.txt` **denied** (text reads `deny fs_read matching "*"` for a deny-with-exclude). (5) Allow row: `ok.txt` **ran**; `blocked-w/a.txt` **denied**. (6) `echo café` **ran** silently — the `echo café*` allow matched; the é survives as UTF-8 in the echoed `exclude`. (7) `type notes\a.txt` **ran** silently (backslash allow matched). (8) `git diff --output=out.txt` **denied** by the block pattern. (9) Floor `.ssh/dummy` read **denied** even though `./**` allows it. (10) `.kiro/agents/x.md` write **denied** by `protected_block`. (11) The file loaded: no fall-open, and `echo` prompts | Not fired |
| P-0.8 | Separators against `git status*` (allow + ask exclude, the P-0.7 block) | `execute_pwsh`, answering reject | **prompted**, each with `triggeringResource` = the second command: `git status; echo x`, `git status \| echo x`, the two-line newline form, `git status $(echo x)`, `git status & echo x`, `git status \|\| echo x`, `powershell -Command "git status; echo x"`. **Ran silently**: `git status > x.txt` — wrote `s3/x.txt` (199 bytes) with no `fs_write` check. Also ran silently: ``git status `echo x` `` — PowerShell treats the backtick as an escape, so no second command ran; in a shell where backticks mean command substitution this would be a bypass [inferred] | **Fired**: no `*` prefixes; exact literals only; D-24 and D-20 revised; README says so |
| P-0.9 | `mcp`, `subagent`, `skill`: an allow `match` equal to `consent.resource` | Local stdio MCP server `paecho` (tool `pa_echo`) in the agent's `mcpServers`; workspace skill `pa-probe-skill`. Phase a: those three capabilities ask. Phase b: allow match + ask exclude | Phase a: all **prompted**; resources `mcp` = `paecho/pa_echo` (title `@paecho/pa_echo`), `subagent` = `kiro_default` from both `invoke_sub_agent` and `subagent_kiro_default`, `skill` = `pa-probe-skill` (tool `disclose_context`). Phase b: all four **ran** with no prompt | Not fired |
| P-0.10 | Built-in asks under `all: allow` only | git-initialised cwd with `.kiro/agents/` and `.kiro/hooks/` | `.git/x`, `.kiro/agents/x`, `.kiro/hooks/x`: **prompted**, `source: kiro-scope`, `scope: kiro`, `askType: explicit`; `matchedRule` fs_write ask with a 38-entry `match` covering `.git`, `.vscode`, `.kiro/agents/`, `.kiro/hooks/`, `~/.kiro/agents/`, `~/.kiro/hooks/`, `**/*.code-workspace`, plus trailing-dot, trailing-space and 8.3 variants (`**/git~*/**`, `kiro~1/agents/`). `.kiroignore`: **denied-by-rule** — a built-in **deny**, not an ask (`Rule: deny fs_write matching '.kiroignore, .kiroignore., .kiroignore , kiroig~*' Source: kiro-scope:kiroignore`). `.kiro/steering/x.md`: **ran** (no built-in) | **Fired**: list the built-in asks (.git, .vscode, *.code-workspace, .kiro/agents, .kiro/hooks) and the `.kiroignore` built-in deny |
| P-0.11 | Windows alias forms against the Floor | `dir /x` shows `SSH~1`, `AWS~1`, `CONFIG~1`; `read_file` on each form | Fully short path `C:\Users\QSYLVE~1.POL\…\SSH~1\dummy`: **ran**, content returned. Long path with only `SSH~1`: **ran**, content returned — the 8.3 name bypasses the deny. `\\?\C:\…\.ssh\dummy`: denied. `\\localhost\C$\…\.ssh\dummy`: denied. Closing test (S1b): fs_read deny `**/ssh~*/**` (lowercase) **denied** `SSH~1\dummy` (patterns are case-insensitive); `**/AWS~*/**` **denied** `AWS~1\dummy`. The shell tier against `SSH~1` was not measured (that session had no shell rule); by construction `*.ssh*` cannot match `SSH~1` [inferred] | **Fired**: add short-name globs (`**/ssh~*/**` etc. close the read_file path); drop guarantee wording |
| P-0.12 | Sub-agent posture | Agent `all: allow` + Floor; the sub-agent `kiro_default` (no permissions block of its own) reads the canary `.ssh/dummy` via `invoke_sub_agent name=kiro_default` and via `subagent_kiro_default` | First attempt (S7): the model refused to delegate (tool-not-invoked). Retry (S7b): both tools fired (`Sub-agent: kiro_default`); the nested `Read File` came back **denied-by-rule** with `deny fs_read matching "**/.ssh/**" Source: agent-profile` — the sub-agent runs under the parent's agent-profile rules. No sub-agent session folder was created | Not fired (no bypass) |

Correction (orchestrator, 2026-09-25, Phase 4 review): in the P-0.8 row, the `powershell -Command "git status; echo x"` step was **not** split — its `resource` and `triggeringResource` were the whole command (raw `res_S3_separators.json` step `P-0.8i`); it prompted because the whole string did not match `git status*`. A command wrapped in `powershell -Command "…"` is matched as one string.

Cleanup verified: no `pa-probe-*` agent remains in `~/.kiro/agents`; no session.json under `~/.kiro/sessions` names a `phase0` workspace; all 11 deleted session folders passed both checks (`agentMode` = `pa-probe-*`, `workspacePaths` = a scratch `phase0` folder). The 9 hash folders listed under divergences remain. One `kiro-cli` process is still running and was not touched; it is probably the live PowerAtlas instance [inferred]. In the project file I ticked exit criteria 1 and 3; criterion 2 is left to you. The project file is not staged.

Orchestrator verification (2026-09-24): re-read the raw frames for P-0.8 (`git status > x.txt`: zero permission requests, status `completed`, `s3/x.txt` present) and P-0.11 (`read_file` on the `SSH~1` short path: zero permission requests, status `completed`). Gate edits applied to D-7, D-20, D-24, new D-38, Phase 1-3 text, R-3, R-7, R-14, R-15 and Follow-up 3. Per-phase review cycle cap set to 1 by the user's `/qdev` invocation ("1 qreview cycle per qreview"; default 2).

Implementation (2026-09-24, code: none — follow-up probes after the Security-auditor review; raw results in the session scratchpad `phase0b/results.json`)
Five throwaway `kiro-cli acp --agent-engine v3` sessions (SA, SB, SC, SC2, SD), 2026-09-24, kiro-cli 2.24.0; none touched the running PowerAtlas instance. Drivers in `phase0b/` (`common.py` with a `RawProbe` subclass capturing raw stdout bytes, `sA.py`…`sD.py`, `f7_analyze.py`, `cleanup.py`); evidence in `res_*.json`, `frames_*.json`, `raw_SD.bin`, `f7_bytes.txt`, merged into `results.json`. Symlinks were created with `os.symlink` without admin (file and directory). The model kept every unusual path and command verbatim (checked `rawInput` on every step). Unplanned finding: kiro-cli's built-in `source: kiro-scope` rule set applies even under `all: allow` — **ask** on fs_write to `.git/**`, `.vscode/**`, `.kiro/agents/`, `.kiro/hooks/` (each also `~/.kiro/`, with `.kiro.`, `.kiro `, `kiro~1`, `agents.`, `agents ` variants) and `**/*.code-workspace`; **deny** on fs_write to `~/.kiro/settings/`, `.kiro/settings/`, `~/.kiro/workspace-roots/`, `~/.kiro/sandbox-state/`, `~/.kiro/web-session/`, `~/.kiro/powers/installed/*/mcp.json`, `~/.kiro/cloud-cache/**`. Neither list covers `.kiro/steering` or `.kiro/skills` (full rule in `res_SC.json` step `control-write` and the SC2 denial text).

| # | Probe | Setup | Result (evidence class) | Implication for the floor/Protected |
|---|---|---|---|---|
| F-1 | Symlinks out of a Protected folder | P-0.7 Manual block. `.kiro/steering/linked.md` file symlink → `outside/linked.md`; `.kiro/steering/linkdir` dir symlink → `outside/dir` | **ran** for both, no prompt; `outside/linked.md` changed `ORIGINAL`→`F1A`; `outside/dir/x.md` created. rawInput kept the lexical `.kiro/steering/...` paths. Control `.kiro/steering/direct.md` **prompted** (steering ask). The linkdir write also produced `_kiro/steering/documents_changed` | A symlink inside a Protected folder pointing outside escapes the ask; the matcher tests the resolved target [inferred; consistent with P-0.4b]. The steering loader still sees the file under its lexical path |
| F-1b | Symlink to a folder named like a denied one | `.ssh2` dir symlink → `realssh/` holding `dummy`; fs_read deny `**/.ssh2/**` | **ran**, returned `SSH2CANARY`. Control (real `.ssh2` dir, same deny): **denied-by-rule** | An fs_read deny is escaped by a link whose target lies outside the pattern; the floor protects the real location, not aliases [inferred from the same resolution] |
| F-2 | Trailing dot / space | fs_read deny `**/.ssh/**`; `read_file` on `.ssh.\dummy`, `.ssh \dummy`, absolute forms | All four **ran-with-error** `ENOENT … stat '<cwd>\.ssh.\dummy'`; no denial, no canary | Not a bypass via `read_file`: kiro-cli's file tools do not strip trailing dots/spaces |
| F-3 | Exact shell literal vs redirection | Shell allow `["git status"]` + ask exclude `["git status"]`, git-init cwd | `git status` **ran** silently; `git status > x.txt` **prompted** (triggeringResource the full command; rejected, `x.txt` not created); `git status --short` **prompted**; control `echo x` **prompted** | A pattern without `*` matches the whole command exactly; redirection and extra arguments fall to the ask |
| F-4 | Shell pattern case | Shell deny `*.ssh*`, `*ssh~*` (short name `SSH~1`) | `Get-Content .SSH/dummy` **prompted** (deny did not match); `Get-Content SSH~1/dummy` **prompted**; `Get-Content ssh~1/dummy` **denied-by-rule** | Shell patterns are case-sensitive, unlike fs patterns; a case variant slips past a shell deny [under allow-all it would run, inferred] |
| F-5 | fs_write short name / trailing dot (SC2) | fs_write deny `**/.kiro/agents/target.md`, `**/.kiro/settings/target.md` + `all: allow`; short name `KIRO~1` | settings: direct **denied** (by kiro-scope, not agent-profile); `KIRO~1\settings\target.md` **ran**, file changed; absolute short form **ran**, file changed; `.kiro.\settings\target.md` **ran** but created a separate `.kiro.` folder; `target.md.` **denied** (kiro-scope). agents: direct **denied** (agent-profile); short, dirdot, filedot, absolute-short forms all **prompted** by the kiro-scope ask; rejected, file unchanged | Agent-profile fs_write denies do not map 8.3 names (same gap as P-0.11); only kiro-scope's own ask stopped the agents variants. A trailing dot names a distinct folder. Deny beat ask across sources [inferred from 2 steps] |
| F-6 | Shell redirection onto a denied path | fs_write deny on `.kiro/agents/target.md` + `all: allow`; `echo pwned > .kiro/agents/target.md` via `execute_pwsh` | **ran** (exit 0); file changed to `pwned`; kiro-cli then sent `_kiro/customAgent/config_error` (`No front matter found`) | fs_write rules do not cover shell writes; kiro-cli loads a workspace `.kiro/agents/` folder at run time |
| F-7 | Non-ASCII | Shell allow `echo café*` + ask exclude, `ensure_ascii=False`, UTF-8 file (bytes `63 61 66 c3 a9 2a`); raw stdout captured | `echo café` **ran** silently; rawInput bytes `63 61 66 c3 a9`. `echo naïve` **prompted**, triggeringResource bytes `6e 61 c3 af 76 65`, exclude bytes `63 61 66 c3 a9 2a`. 0 `\u00xx` escapes, 0 lone `e9`, 0 double-encoded sequences; capture is valid UTF-8. One U+FFFD in the command *output* | kiro-cli sends plain UTF-8; non-ASCII allow patterns match. Only shell output decoding loses characters [inferred: pwsh code page]. P-0.7's `caf?` was a display artefact [inferred] |

Orchestrator note: review findings applied to D-38 (a)-(h), Phase 1 floor, Protected, live criteria and README checkbox, Phase 2 editor, § 8, R-3, R-7, R-14, new R-19, Follow-up 3 and § 9. D-38(h) (symlinks) is escalated to the user as PD-6.

### Phase 1: Modes, compiler and the mode picker [QA]

**Goal**: The mode setting exists; Yolo and Manual (seed rules) compile with the Always blocked floor; migration
runs; locking, the in-effect check and the Default refusal follow D-15, D-16, D-30 and D-34; the settings menu
shows the three-option picker.
**File scope**: `src/power_atlas/config.py`, `src/power_atlas/agent_profile.py`, `src/power_atlas/web.py`,
`src/power_atlas/acp.py`, `src/power_atlas/templates/index.html`, `src/power_atlas/templates/acp.html` (comments
only), `src/power_atlas/static/transcript-renderer.js` (comment only), `src/power_atlas/static/style.css`,
`pyproject.toml`, `src/power_atlas/agents/permissions.yaml` (deleted), `tests/test_web.py`, `tests/test_config.py`,
`tests/acp_page.test.mjs`, `README.md`, `AGENTS.md` (Terminology, user-approved).
**Covers**: SC-1, SC-2, SC-3, SC-4, SC-8, SC-9, SC-10

**Config.** Replace `acp_permissions_enabled: bool` with:

```python
# sketch — the shape, not the deliverable
acp_permission_mode: str = "yolo"                          # "yolo" | "manual" (D-25)
acp_permission_rules: dict = field(default_factory=dict)   # normalised per D-26
```

`load_config` migration (on the raw TOML, before the `_extra` filter): D-27 precedence; `true` → `manual` with
`SEED_RULES` and `protected_block = ["agents"]` (D-23); `false`/absent → `yolo`. Add `acp_permissions_enabled` to
`_LEGACY_KEYS`. Mode per D-25; rules per D-26. Record a `_mode_warning` on the instance (like `_extra`) for the
state payload. Logging per D-25 (once per distinct value per process).
> **Rejected:** loading an unreadable mode as `yolo` — it fails toward the least restrictive mode. **Use instead:**
> `manual` plus `mode_warning` (D-25).

**Compiler.** In `agent_profile.py` replace `overlay_text()`, `_overlay_cache` and `build_derived_agent` with a
pure `compile_block(config) -> str` (reads `acp_permission_mode`, `acp_permission_rules`,
`acp_permission_base_agent`). Output: the provenance header (keeps `_PROVENANCE_MARKER`), `rules:` with
`FLOOR_RULES` first, then Yolo `{capability: all, effect: allow}` or Manual's Protected rules and rows (D-13,
D-26). Floor per D-6 and D-36, paths in `**/` form unless P-0.2 says otherwise:

- `fs_read` deny: `**/.ssh/**`, `**/.aws/**`, `**/.azure/**`, `**/.config/gcloud/**`, `**/.kiro/secrets.json`,
  `**/Kiro-Cli/data.sqlite3*`, `**/power-atlas/local-secret*`, `**/power-atlas/remote-secret*`; plus 8.3
  short-name variants (D-38a): `**/ssh~*/**`, `**/aws~*/**`, `**/azure~*/**`, `**/config~*/gcloud/**`,
  `**/kiro~*/secrets.json`, `**/.kiro/secret~*`, `**/kiro~*/secret~*`, `**/Kiro-Cli/data~*`,
  `**/power-~*/local-*`, `**/power-~*/remote*`, `**/power-atlas/local-~*`, `**/power-atlas/remote~*`. Each
  replaces one long segment with its 8.3 prefix glob; a test pins the list against the long forms. NTFS hashed
  short names (used after four prefix collisions) are not covered (R-3);
- `shell` deny (best-effort): `*.ssh*`, `*.aws*`, `*.azure*`, `*.config/gcloud*`, `*.config\gcloud*`,
  `*secrets.json*`, `*data.sqlite3*`, `*local-secret*`, `*remote-secret*`, `*poweratlas-acp*`, the short-name
  stems `*ssh~*`, `*aws~*`, `*azure~*`, `*config~*gcloud*`, `*secret~*`, `*local-~*`, `*remote~*`, `*data~*`,
  `*powera~*`, and an upper-case copy of every entry (D-38a, D-38b, D-38f, D-38g; P-0.3 measured the over-match
  `echo notes.sshx`, listed in the Always blocked view per R-12); a test pins the list;
- `fs_write` deny (file-writing tools only, D-38g): `**/.kiro/agents/poweratlas-acp.md`, `**/.kiro/settings/**`,
  `**/.kiro/workspace-roots/**`, plus short-name variants `**/kiro~*/agents/poweratlas-acp.md`,
  `**/.kiro/agents/powera~*`, `**/kiro~*/agents/powera~*`, `**/kiro~*/settings/**`, `**/.kiro/worksp~*/**`,
  `**/kiro~*/workspace-roots/**`, `**/kiro~*/worksp~*/**` (F-5).

`SEED_RULES` (the old overlay, per D-24): `fs_read` ask + allow `./**`; `fs_write` ask; `shell` ask + allow
`git status`, `git log`, `git diff`, `git branch`, `pwd`, `whoami`, `uname` + block `git *--output*`,
`git *--no-index*`, `git *--ext-diff*`; `web_fetch`, `web_search`, `mcp`, `subagent`, `skill`, `power` ask.
Protected: `{"agents": "**/.kiro/agents/**", "steering": "**/.kiro/steering/**", "skills": "**/.kiro/skills/**",
"hooks": "**/.kiro/hooks/**"}` → `fs_write` `ask`, or `deny` when in `protected_block` (kiro-cli's built-in
asks also cover `agents` and `hooks` in every mode, P-0.10; Protected still owns the Block outright switch).
Each Protected pattern also ships its `**/kiro~*/<folder>/**` short-name variant (F-5). Protected governs the
file-writing tools only (D-38g) and does not see writes through symlinks that point outside (D-38h); the
resolver and the per-row marker follow D-39.
Emission per D-14. Move
the overlay's measured-semantics comments beside the constants. Qualify every prior-plan ID the rewrite keeps
(`D-18`, `D-19`, `Phase 0`, `plan section 9`, …) with the slug `260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL`;
new comments cite `260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL D-n`.

`_generate` takes the compiled block; missing base → `MINIMAL_BASE` (D-28); `unknown` target → refuse (D-29);
the staged-file verification compares against the same compiled block. Delete `_remove`, the off branch and
`GenerationStatus.enabled` (replace with `mode`) (D-18). `derived_block_state(config)` per D-15. `apply_settings`
per D-16 and D-32. Rewrite the module docstring, the `_PROVENANCE_MARKER` comment (its purpose is now
recognising PowerAtlas's own file, not permitting deletion), the `GenerationStatus` and `derived_block_state`
docstrings.
> **Rejected:** keeping a static overlay file and templating the mode into it — two sources of truth for one block.
> **Use instead:** constants plus `compile_block`.

**Gate and fallback.** `_acp_permission_state(config)` returns `mode`, `mode_warning`, `in_effect =
derived_block_state(config) == "on"`, `floor` (display rows), `protected` (display rows), base-agent and
generation fields; drop `enabled`. `_derived_agent_in_effect`: `acquire(timeout=2)`, D-30 self-heal, release.
Default when not in effect: refuse per D-34 (`_handle_new`); `load_session` keeps sending `kiro_default` for a session with persisted metadata, as kiro-cli ignores the modeId there. D-30's self-heal records when it regenerated from a changed mode or rules, for D-35's dashboard notice.
Store the bound mode in the session record (D-31). Rewrite the `_handle_new` refusal strings and the `acp.py`
comments that describe "off" or "the profile" (~862-869, ~1128-1176, ~6432-6491, ~6528).

**Routes.** `POST /api/acp-permissions` accepts `{"mode": "yolo"|"manual"}` (anything else → `{ok: false,
error}`), runs `apply_settings` in a thread, returns state per D-32. `/api/save-setting` base agent per D-33.

**UI.** Replace `#acpPermToggle` with a radio group (`role="radiogroup"`: `acpPermModeYolo`, `acpPermModeAuto`
disabled with hint "coming soon — behaves like Manual", `acpPermModeManual`). Per-mode description (Yolo lists
kiro-cli's built-in asks and the `.kiroignore` built-in deny per D-38c). Scope note: "Changes apply to sessions created afterwards. Terminal sessions
and task modes such as Spec or Plan are not covered." An "Always blocked" disclosure listing the floor (shell tier
marked "catches common accidents, not a guarantee"; credential-store paths described without guarantee
wording, D-38a). Keep `#acpPermBadge`, `#acpPermWarn`, the gear dot, the
base-agent field (tooltip reworded). Rewrite `_acpPermWarnText` (delete the off branch; "turn it off and on again"
becomes "save the mode again or restart PowerAtlas"), `_ACP_PERM_NEXT_BASE`, `_ACP_PERM_NEXT_REGEN`, the gear-dot
title, and show `mode_warning`. Show the D-35 notice when the state reports an external posture change. JS guards check `typeof d.mode === 'string'`. `var ACP_LOCAL = true` (D-21).
Update the `transcript-renderer.js` capability-words comment that cites `agents/permissions.yaml`.

**Tests.**
- `tests/test_config.py`: migration table (`true`, `false`, absent, both keys present, rules already present);
  mode table (`Manual`, `AUTO`, `junk`, wrong type) with one WARNING per value; malformed `acp_permission_rules`
  shapes (string `allow`, non-dict row, unknown row, non-list `protected_block`, 10 000 entries, invalid block
  pattern) never raise and normalise per D-26.
- `isolated_config`: write a minimal base agent into the redirected agents folder; add a test for the
  `MINIMAL_BASE` path with none present.
- Port `TestDerivedAgentInjectionIsTextual` and `TestDerivedAgentWrite` to `inject_permissions(base,
  compile_block(cfg))`; keep every byte-identity assertion.
- Replace `TestDerivedAgentRemovalOnOff` and `TestOverlayIsReachableAtRuntime` with "generation always writes",
  "foreign file refused" and "missing base uses MINIMAL_BASE".
- `TestRuleAssemblyInvariants` → compiled-output invariants over a rule-set table (every default × empty and
  non-empty allow/block lists × `{}` and partial dicts): every capability in D-11 named in Manual; `exclude`
  equals the row's allow list wherever `ask`/`deny` lacks `match`; floor first and present in both modes; all
  strings JSON-quoted with `ensure_ascii=False`; D-14 rejects DEL, a surrogate, U+2028; output round-trips through
  `excise_permissions`. Keep "no trailing `*`" as a `SEED_RULES` invariant (D-24 after P-0.8). Retire, with a one-line reason each:
  "agents folder denied" (replaced by D-23 + Protected), "no meta-capability" (scoped to Manual; Yolo uses `all`).
  A mutation that drops an `exclude` must fail the suite.
- `TestAcpPermissionRoutes`: mode round trip; `auto`/junk refused; save failure → `ok: false`; generation failure
  → `ok: true` + warning. `TestSettingsSurface` remote tests move to the mode body.
- Lock: deterministic — patch `_generate` to wait on an Event, run `apply_settings` in a thread, assert the hook
  raises within its timeout and `_handle_new` sends the refusal; assert `apply_settings` never deadlocks when a
  hook call happens during it.
- D-34 refusal: a not-in-effect Default create is refused with the cause, the fix and the remote note; WARNING
  logged; a task-mode create still succeeds. D-30 self-heal after a hand edit; D-35 notice raised when the heal
  changed the mode or rules.
- Page tests: radio group semantics, Auto disabled, `mode` guard, scope-note and warning copy (replace the pins at
  ~4773-4839, ~10989-11288, ~11513-11541).

**Exit criteria**:
- [x] `pytest tests/test_web.py tests/test_config.py --timeout=300` and `node tests/acp_page.test.mjs` pass;
      `_check_test_names.py` clean
- [x] `git grep -n "acp_permissions_enabled" -- src/` returns only the migration code and its comment
- [x] `git grep -n -e overlay_text -e _overlay_cache -e build_derived_agent -e "agents/permissions.yaml" -e "_remove(" -- src/ tests/ pyproject.toml` returns no hits
- [x] Live, via a separate probe process bound to `compile_block` output (Yolo) copied into `pa-probe-yolo.md`:
      `echo ok` runs with no prompt; the read-file tool and `Get-Content` on the canary `.ssh/dummy` are refused, and
      so is the read-file tool on its 8.3 short-name path (`SSH~1\dummy`)
- [x] `find_protected_links()` tests (temp-directory fixture: file link, directory link, broken link, loop) and a
      test that `compile_block`, `derived_block_state` and the gate hook never call it (D-39); the settings section
      shows the per-row "linked items not covered" marker (page test)
- [x] Probe (D-39): under a Manual block with an `fs_write` ask on the absolute resolved target of a scratch link
      whose path contains spaces, a write through the link prompts or not; recorded in the implementation notes and
      Follow-up 3 updated
- [x] Live, same for Manual seed: `git status` silent, `echo x` and `git status > x.txt` prompt, a write under
      `.kiro/agents/` refused, and a write to the derived agent through its `KIRO~1` short-name path refused
- [x] Update README.md: config sample (`acp_permission_mode`), the feature bullet (~149, "off by default"), the
      task-mode paragraph (~380, "while the permission setting below is on"), and the permission section's modes,
      Always blocked, scope and built-in-ask disclosure (~385-416), including: seed commands are exact; a `*`
      in a command pattern also allows output redirection; command patterns are case-sensitive; write rules
      cover the file-writing tools, not shell redirection (D-38e-g)
- [x] AGENTS.md Terminology: rewrite **derived agent** (always written; "the overlay" becomes "the permission mode
      and rules"; fix its stale `plans/260921_…` path) and add **permission mode**, **Always blocked**,
      **Protected** — applied only after the user approves the shown diff

Implementation (2026-09-25, code: b4f579d)
Phase 1 replaces the on/off ACP permission switch with a stored permission mode, `acp_permission_mode` (`yolo` or `manual`), plus `acp_permission_rules`. `agent_profile.compile_block` compiles them into the derived agent, and the derived agent is now written in every mode: the Always blocked floor comes first, then either `all: allow` (Yolo) or the Protected items and the seed rows (Manual). The package-data overlay, `_remove`, the off branch and `build_derived_agent` are deleted. Old configs migrate on load: `true` becomes Manual with the agents write deny kept, and `false` or no key becomes Yolo. The session gate now takes the generation lock with a 2 s bounded wait. It regenerates a stale file once and raises a dashboard notice when that regeneration changed the mode or rules. While the derived agent is not in effect, a Default session is refused, naming the cause, the fix, a note for remote clients, and task modes as the alternative. The settings menu has a three-option radio group (Auto disabled), a description per mode that names kiro-cli's built-in rules, the Always blocked and Protected lists with a "N linked items not covered" marker, the mode warning and the outside-change notice. README is updated. The work is committed as `b4f579d` (no attribution trailers, not pushed).

| Probe (separate `kiro-cli acp --agent-engine v3`, kiro-cli 2.24.0, 2026-09-24/25) | Step | Result |
|---|---|---|
| Yolo | `echo ok` | ran, no prompt |
| Yolo | read_file `.ssh/dummy` / `Get-Content .ssh/dummy` / read_file `SSH~1\dummy` | denied-by-rule (floor), all three |
| Manual seed | `git status` | ran silently |
| Manual seed | `echo x`; `git status > x.txt` | both prompted (agent-profile ask); x.txt not created |
| Manual seed | write `.kiro/agents/probe.md` | prompted (kiro-scope ask), rejected |
| Manual seed / migrated | write `KIRO~1\agents\poweratlas-acp.md` | denied-by-rule (floor short-name variant) |
| Manual migrated | write `.kiro/agents/probe.md` | denied-by-rule (Protected block) |
| D-39 (target inside the workspace, path with a space) | write through a link to a target with an absolute-path ask rule | prompted |
| D-39 (same) | write through a link to a target with no rule | ran |
| D-39 (target outside the workspace) | writes through links | denied by kiro-cli's built-in `kiro-scope:workspace-escape` |

Orchestrator note on the Manual-seed criterion: its "a write under `.kiro/agents/` refused" clause predates D-23. In the plain seed the write is prompted by kiro-cli's own ask (not refused); it is refused as a rule only with `protected_block = ["agents"]`, the migrated case. Both match D-5/D-23; the box stands on the migrated run.

Implementation (2026-09-25, code: 682bbc7, 967267e, 687ee20 — review fixes and the user-requested test repair)
682bbc7 applied all 13 merged review findings: an unreadable `config.toml` no longer fails open to Yolo (`apply_settings` and the startup sync refuse to generate or save; gate and panel treat it as not in effect; `config_error` in the state payload); the D-30 heal runs in a worker inside the gate's 2 s budget; the permission routes use a 5 s bounded lock; a lock-timeout refusal names its cause; the D-35 notice is raised at startup too and cleared only by a mode/rules change or an explicit mode choice; `find_protected_links` works on Python 3.11 and reports a Protected folder that is itself a link; dropped or rewritten rules are logged once and shown (`rules_warning`); the D-28 note logs once; patterns made only of `*`, `/`, `\` are rejected; remote refusals redact paths; README wording on hand edits corrected; `_gate_verdict` fails closed on anything but the in-effect dict. 967267e repaired the six pre-existing dashboard `session_closed` page tests (from `8d1782d`): the test sandbox lacked a `clearTranscript` stand-in; the production page was correct. 687ee20 closed the targeted re-review's findings: `save_config` refuses to write a config produced by a failed load (`ConfigUnreadableError`, one handler: 409 for JSON routes, an error toast for toast routes; the folder-delete path checks before `rmtree`), the `.bak` wording matches whether a backup was written, remote redaction matches whole folders and the config folder, a fixed config clears the stale "could not be read" status, `heal_stale_locked` refuses a failed load, and only the gate's own budget reports "being applied". The 687ee20 commit body says "25 route call sites"; the correct count is 20 `save_config` calls in `web.py` plus one in `apply_settings` (not amended; amend is banned). Tests after 687ee20: pytest 2042 passed; `node tests/acp_page.test.mjs` 754/754; `_check_test_names.py` clean. The orchestrator re-ran the re-review's repro: a direct writer on a corrupt Manual config is refused, the file keeps its bytes, the gate reports not in effect and the derived agent stays Manual.

AGENTS.md Terminology applied after the user chose Save (2026-09-25): commit 5e719a5.

QA (Step 5b, 2026-09-25): **PASS**. The user authorised the restart ("You can restart yourself and test freely");
PowerAtlas was restarted through `/api/restart` at 23:01 after a copy of `config.toml` was saved as
`config.toml.pre-permission-modes`. Startup migrated `acp_permissions_enabled = false` to Yolo and wrote the derived
agent (Yolo, fingerprint header, floor first). Driven with standalone Playwright from the venv (AGENTS.md
§ Verification Setup) and direct HTTP; the Claude-in-Chrome extension was not connected after three attempts.
- API: GET state `mode=yolo, in_effect=true`, `protected_links` steering 9, skills 16, agents 0, hooks 0; POST
  `{"mode":"auto"}` and a list-valued mode refused with `ok:false`; POST without the cookie or without `Origin` → 403;
  derived agent unchanged by refused POSTs.
- UI: `role="radiogroup"`, Yolo checked, Auto disabled with "coming soon — behaves like Manual", Yolo description names
  kiro-cli's built-in asks and denies, scope note present, Always blocked list and Protected section render; the
  expanded Protected section shows "9 linked items not covered" with each steering link and its agent-playbook target.
  Clicking Manual switched the API state and the file to Manual; a reload kept Manual checked; clicking Yolo switched
  both back. No console errors.
- State probe: a hand edit of `poweratlas-acp.md` read as not in effect; creating a Default session on /acp healed
  it (file back to Yolo, edit gone, `in_effect=true`, no D-35 notice) and the session bound `poweratlas-acp`
  (log and `session.json` `agentMode`). The QA session was closed and its folder deleted after checking
  `createdAt`/`agentMode`/`workspacePaths`. Final mode left as Yolo.
- Observation (advisory): the "N linked items not covered" marker is visible only after expanding the Protected
  disclosure; the collapsed summary does not show the count. (Fixed in Phase 2, c7111fc: the summary shows the count.)
- Chrome MCP re-run (2026-09-25, after the user connected the Claude-in-Chrome extension and signed that Chrome in
  from the tray): the radio group, Auto hint, Protected list with "9 linked items not covered", Manual click (API
  `manual`, in effect) and ArrowUp from Manual skipping the disabled Auto to Yolo (API `yolo`, in effect) all
  confirmed; no console errors. Final mode Yolo.

### Phase 2: Custom Manual rules and the rule editor [QA]

**Goal**: The user edits Manual's rows in plain language; custom rules compile and validate.
**File scope**: `src/power_atlas/agent_profile.py`, `src/power_atlas/web.py`, `src/power_atlas/templates/index.html`,
`src/power_atlas/templates/partials/acp_permission_rules_modal.html` (new), `src/power_atlas/static/style.css`,
`tests/test_web.py`, `tests/acp_page.test.mjs`, `README.md`.
**Covers**: SC-4, SC-5, SC-7

**Routes.** `POST /api/acp-permissions` additionally accepts `{"rules": {...}}` (full replacement, validated
per D-11/D-14/D-26; errors name the row and pattern). `GET` returns normalised `rules` without persisting (D-26),
plus Protected labels and the floor.

**Editor.** `partials/acp_permission_rules_modal.html`, a `<dialog>` included into `index.html` like
`launch_profile_modal.html`, opened from "Edit rules…" in the settings section (every mode; rules apply when
Manual is selected). Rows with plain labels (Read files, Write files, Run commands, Web fetch, Web search, MCP
tools, Sub-agents, Skills, Powers), a default `<select>`, chip lists "Allow without asking" (hidden under default
Allow) and "Always block". `./**` renders as "the session folder". A breadth warning (not a refusal) on patterns whose first token is an interpreter or shell (D-35: "equivalent to allow-all") and on patterns
covering the session folder, a drive root or the home folder for Write files. Read-only Always blocked and
Protected sections (per-item "Block outright" toggles; the Protected section lists the D-39 linked items and their
targets). Footer: "Applies to sessions created afterwards." Save
posts JSON, shows the server error inline, closes on success, shows the D-32 warning when generation failed.
Web fetch chips are labelled as site host names (`example.com`, D-38d); no chip list is hidden (P-0.9
passed). A shell pattern containing `*` shows the D-38e redirection warning. The Run commands row notes that
patterns match literally and case-sensitively, and that `/` and `\` are different characters (D-38b, D-38f);
patterns are not mirrored automatically.
> **Rejected:** an HTMX-fetched partial per save — the modal needs client-side chip editing either way.
> **Use instead:** static include + fetch JSON, as the launch-profile modal does.
> **Rejected:** a remote read-only editor — `/` is loopback-only by construction, so the branch is unreachable.

**Exit criteria**:
- [x] Route tests: valid rules round-trip; each invalid shape (unknown row, bad default, control character, DEL,
      surrogate, 201-char pattern, 101 patterns, bare `*`) refused with a row-named error; remote POST refused
- [x] Page tests: editor renders normalised rows, hides the allow list under default Allow, shows the breadth
      warning and the D-38e redirection warning, posts the edited JSON, shows a generation warning
- [x] Live, via a probe agent from `compile_block` with a user-edited rule set (one custom allow, one custom
      block, one `Block outright` Protected item): each behaves as configured
- [x] Update README.md permission section: Manual rows, Protected items, the editor

Implementation (2026-09-25, code: 97c58c2)
Phase 2 lets you edit Manual's rules from the dashboard. It is committed as `97c58c2` plus the fix `192ba9f`. Neither commit carries attribution and nothing is pushed. **Route.** `POST /api/acp-permissions` now also accepts `{"rules": {...}}`, a full replacement of the rule set. It is checked by the new strict `agent_profile.validate_rules` before the lock is taken. The first fault refuses the whole save, and the error names the row by its label and key plus the pattern: an unknown or missing row, or an unknown key inside a row; a default other than allow, ask or block; a pattern with a control character, DEL, a surrogate or U+2028, a pattern over 200 characters, or a blank one; a pattern that matches everything (a bare `*` or `**`); a list of more than 100 patterns; an unknown Protected name. Valid rules are saved and the agent regenerated through `apply_settings`, so the lock, the D-32 result and the unreadable-config refusal all apply. The GET and the POST answers now carry the normalised `rules` and the row labels, and a GET never writes anything. The route is still loopback-only. **Editor.** An "Edit rules…" control under Agent permissions opens `partials/acp_permission_rules_modal.html`, a native modal dialog, included in `index.html` only. One row per kind of action, each with an Allow / Ask / Block default and two chip lists, "Allow without asking" (hidden under Allow) and "Always block"; `./**` shows as "the session folder". Run commands says patterns match literally and case-sensitively and that `/` and `\` differ; Web fetch says its patterns are host names. Warnings (advice only): an interpreter or shell as the first word of a command pattern, any `*` in a command pattern (it also allows redirection), and a Write files pattern that covers the session folder, a drive root or the home folder. Each Protected item has a Block outright switch and lists its linked items; the Always blocked list is read-only. The footer reads "Applies to sessions created afterwards." Save posts the rules as JSON, shows a refusal inside the dialog, closes on success, and shows the D-32 warning as a toast. Esc closes the dialog, focus stays inside it, every control has a label, and chip remove buttons work from the keyboard. README covers Manual's rows, the Protected items and the editor.

| Probe step (kiro-cli 2.24.0, separate `kiro-cli acp --agent-engine v3`, 2026-09-25) | Configured as | Result |
|---|---|---|
| `echo pa-custom` | custom shell allow | ran, no prompt |
| `echo pa-blocked` | custom shell block | denied by rule (agent profile), no prompt |
| write `.kiro/steering/probe.md` | Protected steering set to Block outright | denied by rule (agent profile); file not created |
| `echo x` (control) | Ask row | prompted; the ask rule's `exclude` includes `echo pa-custom` |
| write `.kiro/skills/probe.md` (control) | Protected skills left on ask | prompted, rejected; file not created |
| `git status` (control) | seed allow | ran with no prompt |

Implementation (2026-09-25, code: c7111fc, 1f824f1, 1a45415 — review and QA fixes)
c7111fc applied all 19 merged review findings (S1-S5, U1-U14): a save that does not choose the mode (rules-only, base-agent rename) raises the D-35 notice when the settings it starts from were changed outside the dashboard; the editor refuses to open on a stored Always block list that is not a list (the GET reports per-row load problems, `rule_problems`); `validate_rules` requires every key; patterns with no literal character besides `* ? / \ . :` and spaces are refused as match-everything (`./` and `../` patterns stay valid); the interpreter warning checks every word; Write files set to Allow gets an allow-all note; unsaved edits ask Discard / Keep editing inside the dialog; a refused save returns `{row, list, pattern, message}` and marks the chip in plain words; URL-style web-fetch patterns are flagged with the host to use; duplicates say "Already listed"; bad characters are refused on Add; Save stays focusable while busy; every `.profile-modal` is centred; chip remove buttons are 24×24 px; clearer session-folder and Protected wording; the Protected item "Skills" is renamed "Skill files"; one persistent status region per row; the settings-menu Protected summary shows the linked-item count; the intro explains that Always block wins and a pattern in both lists is flagged. 1f824f1 moved two imports (no behaviour change). 1a45415 (QA fix) returns focus to the settings gear after a successful save, makes only the rules body scroll (the dialog had two scrollbars because hidden `.sr-only` status lines were positioned against the dialog), and shows "Loading rules…" with `aria-busy` on the Edit rules row while the rules load (the menu now stays open until the dialog opens). Tests after 1a45415: pytest 2092 passed; `node tests/acp_page.test.mjs` 774/774; `_check_test_names.py` clean.

QA (Step 5b, 2026-09-25): **PASS** after one fix round. PowerAtlas was restarted through `/api/restart` at 23:56 (user
authorisation from Phase 1; `config.toml` copied to `config.toml.pre-phase2-restart` first); startup clean, derived
agent Yolo. Driven in real Chrome through the Claude-in-Chrome MCP (tab signed in by the user from the tray):
- Settings menu: Protected summary reads "Protected (Manual) (25 linked items not covered)"; "Edit rules…" opens the
  editor with the note that the rules are not used while the mode is Yolo; the row shows "Loading rules…" and
  `aria-busy="true"` while the rules load (after 1a45415).
- Editor: adding `npm test` and `python *` to Run commands keeps focus in the input; a duplicate shows "Already
  listed." and keeps the text; `python *` shows the allow-all and redirection warnings; `https://evil.example/x` in Web
  fetch's Always block shows "…blocks nothing as typed. Use evil.example instead."; Esc with unsaved edits shows
  Discard / Keep editing with focus on Keep editing; only the rules body scrolls (after 1a45415).
- Save: `npm test` stored (`shell.allow`), dialog closed, mode Yolo and in effect, no notice; switching to Manual
  compiled `npm test` into both the allow `match` and the ask `exclude`; a POST with a tab character in a pattern was
  refused with `detail {row: shell, list: allow, message: "…contains an invisible tab character…"}`. After a
  successful save focus returned to the settings gear (after 1a45415; before it, focus fell to `<body>` — the QA
  FAIL that 1a45415 fixed).
- State restored exactly (rules byte-equal to the pre-test copy), mode Yolo, in effect, no notice.
- Withdrawn observation: an apparent "first click after load does nothing" was the Chrome MCP dropping ref-based
  clicks right after a navigation (no pointer events reached the page); coordinate clicks worked.

### Phase 3: "Allow, and always in new sessions" on the prompt card [QA]

**Goal**: One click on an eligible loopback prompt card saves a rule and answers the current prompt.
**File scope**: `src/power_atlas/acp.py`, `src/power_atlas/web.py`, `src/power_atlas/static/transcript-renderer.js`,
`src/power_atlas/templates/acp.html`, `src/power_atlas/templates/index.html`, `src/power_atlas/static/style.css`,
`tests/test_web.py`, `tests/acp_page.test.mjs`, `README.md`.
**Covers**: SC-6, SC-7

**Frame.** Add `triggeringResource` to `_CONSENT_TEXT_FIELDS` and to the renderer's `PERMISSION_CONSENT_FIELDS`
(shown as "Triggered by" when it differs from `resource`). Add `ruleEligible` (D-31), computed from the raw
consent before projection.

**Route.** `POST /api/acp-permissions/allow-rule` with `{"capability": <row>, "pattern": <string>}`: loopback-only,
validated per D-14, appended to the row's `allow` list (deduplicated) through `apply_settings`. Refused for rows
whose default is Allow and rows outside D-11. Returns per D-32.

**Card.** In `addPermissionRequest`, when `window.ACP_LOCAL === true`, `ruleEligible` is true and an option with
`kind === "allow_once"` exists, add "Allow, and always in new sessions…". It opens an inline row: capability
label, an editable field prefilled per D-20, the D-35 interpreter warning when it applies, Save and Cancel. Save posts the rule; on `ok` it answers with the
`allow_once` option and shows "Rule added — new sessions will not ask", or the D-32 warning. If a
`permission_resolved` arrived meanwhile it skips the answer and says "Rule saved; this prompt was already
answered". On error it shows the message and leaves the prompt open.

**Exit criteria**:
- [x] Route tests: append, dedupe, invalid pattern refused, remote refused, default-Allow row refused
- [x] `_project_consent` and `ruleEligible` tests: `triggeringResource` forwarded; a non-string dropped;
      ineligible for a Protected rule (`match` present), a non-`agent-profile` source, a non-derived bound mode
- [x] Page tests on both pages: button present only with `ACP_LOCAL === true`, `ruleEligible` and an
      `allow_once` option; prefill per D-20 (`git status --short` → `git status --short`, `python -m pytest` → the
      exact command with the D-35 warning, a bare filename → the exact resource, an MCP tool → its resource, no button on a web-fetch prompt); Save posts then answers by
      `kind`; an error leaves the prompt open; a resolved-meanwhile prompt is not answered
- [x] Live on the running instance after the user restarts it (AGENTS.md § Verification Setup): in Manual, a
      prompted `echo pa-button` shows the button prefilled `echo pa-button`; save; the prompt is answered; a new session runs `echo pa-button` without a prompt
- [x] Update README.md: the button (new sessions only; when it does not appear) and the "Triggered by" field

Implementation (2026-09-25, code: fc336a1)
Phase 3 adds "Allow, and always in new sessions…" to permission prompt cards. It is committed as fc336a1, plus fc336a1's README wording fix 25bb9f5. Neither commit carries an attribution trailer, and nothing is pushed. In `acp.py`, the `permission_request` frame now forwards `consent.triggeringResource`. It also carries `ruleEligible`, `ruleRow` and `ruleRowLabel`, computed by `_rule_row` from the raw consent before projection: the source is `agent-profile`; the matched rule is an ask with no `match` list, so not Protected; the capability names the row whose rule matched; and the session record's mode is `poweratlas-acp`. In `web.py`, the new route is `POST /api/acp-permissions/allow-rule`. It validates the pattern with `pattern_error`, refuses `web_fetch`, rows outside D-11, a row set to Allow and a full list, checks again inside the lock, and appends without duplicates through `apply_settings(lock_timeout=5, sets_posture=False)`, returning the D-32 result. It is not in `_REMOTE_ALLOWED_PATHS`; tests confirm a 403 for a remote peer, a request without Origin and one without the `pa_local` cookie. In the shared renderer, the card offers the button only when `window.ACP_LOCAL === true` (`acp.html` now sets that global). The field is labelled and prefilled per revised D-20 and shows the D-35 and D-38e warnings. Save posts first and answers with the `allow_once` option by kind only after the server stored the rule; a prompt answered meanwhile is not answered again; an error leaves the prompt open with the reason. "Triggered by" shows only when it differs from the resource. README documents the button and the "Triggered by" field.

| Probe step (kiro-cli 2.24.0; fs_write Ask row with allow list + ask exclude) | Result |
|---|---|
| write `sub/a.txt` under allow `sub/**` (relative pattern, relative path) | ran, no prompt |
| write `<cwd>\sub\d.txt` (absolute backslash path) under relative `sub/**` | ran, no prompt: matched relative to the workspace |
| write `abs/e.txt` (relative path) under absolute `C:/…/sP3/abs/**` | ran, no prompt |
| write `C:/…/sP3b/abs/b.txt` and `C:\…\sP3b\abs\c.txt` under absolute forward-slash `C:/…/sP3b/abs/**` | both ran, no prompt |
| control: write outside the allowed folder | prompted; an absolute path inside the workspace arrives relativised (`resource` was `other.txt`) |
| shell bare ask, `echo pa-one && echo pa-two` | prompted; `triggeringResource` was the first sub-command, `echo pa-one` |
| frame shape of a bare row ask | `matchedRule` = `{capability, effect: ask}`, no `match`, no `exclude`: eligible |

Implementation (2026-09-25, code: 9bca1ae — review fixes)
9bca1ae applied all 15 merged review findings. A file prompt now fills in an absolute path under the session's folder (new frame key `ruleRoot`: the consent's `workspaceRoot`, else the session cwd), and never proposes a broad place: a drive root, the home folder or any ancestor of it, or the session folder or any ancestor of it give the exact file instead; a resource holding a wildcard, a `.`/`..` segment or a device/UNC prefix uses the exact resource. Broad folders warn for reads as well as writes, and a relative pattern warns that it applies in every session. Saving from a card no longer clears the D-35 notice (`apply_settings(keeps_notice=True)`); the route returns the notice and the card points at it after saving. `..` path segments are refused in Read/Write files allow patterns (route and `validate_rules`; loading drops a stored one with the rules warning). The route refuses any row outside `acp._RULE_ROWS` and accepts a pattern already in a full list as a no-op. A reloaded session's eligibility comes from the `agentMode` kiro-cli persisted in `session.json`; unreadable metadata makes it ineligible. Split commands get honest hint and outcome text, warnings run on the whole command, `?` and `[` trigger the redirection warning, focus falls back to the card when the trigger is disabled, a test pins the renderer's row set, a Save test puts `reject_once` first, dashboard-page tests were added, and README was corrected. Tests after 9bca1ae: pytest 2186 passed; `node tests/acp_page.test.mjs` 795/795; `_check_test_names.py` clean. The reviewer's prefill probe re-run shows no broad prefill for any adversarial case (`...\scratchpad\phase3\fix\probe2_out.txt`).

QA (Step 5b and the live exit criterion, 2026-09-25): **PASS**. PowerAtlas restarted through `/api/restart` at 01:04
(`config.toml` copied to `config.toml.pre-phase3-restart`); startup clean. Driven in real Chrome through the
Claude-in-Chrome MCP on /acp (reached with a same-origin `location.assign('/acp')`; a direct MCP navigation to /acp is
refused by the `Sec-Fetch-Site` guard, as designed):
- Mode set to Manual. A Default session in "The agent's own folder" was asked to run `echo pa-button`; the card showed
  Capability "Run shell commands", Source "The agent's own permissions (agent-profile)", Matched rule "Run shell
  commands → ask", and the button "Allow, and always in new sessions…".
- The button opened a labelled field ("Run commands — allow without asking in new sessions:") prefilled with the exact
  command `echo pa-button`, focus in the field, and the scope hint. Save stored the rule (`shell.allow` gained
  `echo pa-button`), answered the prompt with Allow, the command ran (agent replied `pa-button`), and the card read
  "Rule added — new sessions will not ask". No posture notice.
- A **new** session ran `echo pa-button` with no permission card (tool call completed).
- Cleanup: the session was closed, the rule removed through the rules route, the mode set back to Yolo (in effect, no
  notice, rules equal to the seed set), and both test session folders deleted after checking `createdAt`,
  `agentMode` (`poweratlas-acp`) and `workspacePaths`.

### Phase 4: Documentation and final live check

**Goal**: Docs describe the shipped behaviour.
**File scope**: `docs/KNOWLEDGE.md`, `plans/ROADMAP.md`, `plans/tests/260701_POWERATLAS.md`.
**Covers**: SC-10

- `docs/KNOWLEDGE.md`: probe results P-A1 to P-C and P-0.x, dated, with kiro-cli version; dated supersession
  notes at the opt-in/off-default entry (~86), the "settings copy says so" consequence (~269), "shipped overlay"
  (~277), and the "Not settled: whether `*` stops at `&&`" line (~287).
- `plans/ROADMAP.md`: cross-reference Auto mode from the existing item "Decide permission requests by rule for
  unattended sessions" (keep its name — other lines cite it), carrying D-5's deny-steer-escalate design and R-9;
  reword "as an opt-in setting" (~125).
- `plans/tests/260701_POWERATLAS.md`: "with the setting off, the default" (~154) → Yolo with the floor.

**Exit criteria**:
- [x] Every row of § 8 done or marked with its reason
- [x] `git grep -n -e "Permission profile" -e "acp_permissions_enabled" -e "turn it off and on" -- README.md docs/ AGENTS.md src/power_atlas/templates src/power_atlas/acp.py`
      returns only intentional historical mentions

Implementation (2026-09-25, code: 9254b1f)
Phase 4 is committed as 9254b1f, on top of 9b2137b. `docs/KNOWLEDGE.md` gains a new section, "ACP permission rules — measured 2026-09-24 and 2026-09-25, kiro-cli 2.24.0", recording the probe results in grouped bullets and citing the plan by slug for the full tables: P-A1..P-C, P-0.1..P-0.12, F-1..F-7 and the Phase 1-3 probes (separator splitting and redirection, exact literals, `triggeringResource` naming the part that hit the ask, case and slash behaviour of shell and file patterns, 8.3 names past both deny kinds, symlink resolution and kiro-cli's `workspace-escape`, search-tool filtering, sub-agents under the parent's rules, web_fetch host matching, resource matching for MCP/sub-agent/skill, kiro-cli's built-in `kiro-scope` asks and denies). Dated, italic supersession notes mark the four named entries (the opt-in/off-default entry, the "settings copy says so" `vibe` consequence, the "shipped overlay" line, and "Not settled: whether `*` stops at `&&`"); nothing was deleted. `plans/ROADMAP.md` keeps the item name "Decide permission requests by rule for unattended sessions" and gains a sub-bullet pointing to Auto mode with D-5's design, the unmeasured steer-timing probe and R-9; the "as an opt-in setting" sentence now describes the shipped Yolo and Manual modes. `plans/tests/260701_POWERATLAS.md`'s default posture now reads Yolo with the Always blocked list. The Phase 4 grep's only hit is `README.md:204`, the intentional migration comment. Review fixes in 6abfcbf: the powershell-wrapper note (not split) and the citation for the blanket-ask-without-exclude failure (prior plan § 9 Step 4).

## 6) Risk Assessment

| # | Risk | Impact | Mitigation |
|---|---|---|---|
| R-1 | Edited rules read as stale | Default creation refused until healed | D-15, D-30, D-34 |
| R-2 | Shell floor bypassed by rephrasing | Credential read through shell | Accepted (D-6): labelled best-effort; path rules carry the weight |
| R-3 | Path floor misses 8.3 short names (measured, P-0.11, F-5); search tools, `\\?\`, UNC and trailing-dot/space forms measured covered or harmless | Credential read, protected write | D-38a short-name globs; copy drops guarantee wording; NTFS hashed short names and unprobed device paths remain |
| R-4 | Always-present allow-all `poweratlas-acp` in kiro-cli's terminal picker | Widens posture for a default-configuration kiro user who picks it | User: accepted — 2026-09-24 (D-37) |
| R-5 | A compiled block kiro-cli rejects fails open silently | All protection lost | D-14, D-26, P-0.7, invariants test |
| R-6 | Missing `exclude` makes an allow rule dead | More prompts than configured | D-13; invariants test with a mutation check |
| R-7 | Rules match a symlink's resolved target; a link inside a Protected folder pointing outside, or to a denied folder, escapes (F-1, F-1b) | Self-config edit without a prompt (all 9 of the user's `~/.kiro/steering/*.md` and 16 of 27 `~/.kiro/skills` entries are such links); credential read via an alias | User: accepted — 2026-09-24, disclosed per item (D-39); kiro-cli's built-in `workspace-escape` denies writes through links whose target is outside the session workspace (Phase 1 probe), so the gap is limited to sessions whose workspace contains the target (e.g. agent-playbook itself); enforcement option in Follow-up 3 |
| R-8 | Allowed git commands run repository-controlled code (textconv, `diff.external`) | Code execution from a cloned repo | Accepted in the prior plan; carried forward |
| R-9 | An unanswered prompt is cancelled after 1800 s | Lost turn when away | Out of scope (Follow-up 6) |
| R-10 | Generation fails after a save | Default creation refused until fixed | D-32 warning; D-34 refusal names the fix; D-28 removes the commonest cause |
| R-11 | Rollback to the previous release | Manual users land on allow-all | User: accepted — 2026-09-24 (D-37) |
| R-12 | Shell floor blocks legitimate commands (`ssh -i ~/.ssh/key`) | Agent cannot run those | Accepted trade-off; listed in the Always blocked view |
| R-13 | Another settings route saves a stale config copy over a just-applied mode or rule change | The change is silently reverted | D-30 keeps file and config consistent; the revert itself is visible in the editor; single-user, millisecond window — accepted, Follow-up 9 |
| R-14 | Separators or redirection let a `*` shell pattern do more; shell writes bypass every fs_write rule | Silent file write via `> file` (measured P-0.8, F-6) | Seeds and prefill exact (D-24, D-20; F-3 measured); D-38e warning; D-38g file-tool wording; best-effort `*poweratlas-acp*` |
| R-15 | Sub-agents run under their own agent's posture | Floor bypassed by spawning `kiro_default` | Closed by P-0.12: the sub-agent was denied by the parent's rule |
| R-16 | An agent allowed to run an interpreter or broad shell changes the posture (API or `config.toml`) | Future sessions widened; in Manual, allowing an interpreter equals allow-all | User: accepted — 2026-09-24 (D-35); warnings and the dashboard notice detect, they do not prevent |
| R-18 | `config.toml` and the base agent file are writable by agents (no floor entry) | Rules or base agent tampered for future sessions | User: accepted — 2026-09-24 (D-36); D-35 notice surfaces mode and rule changes |
| R-19 | Shell patterns are case-sensitive (F-4) | A case variant of a credential path slips past the shell floor | Lower- and upper-case spellings (D-38f); mixed case accepted as best-effort (D-6) |
| R-17 | No runtime signal that kiro-cli applied a compiled block | Silent fail-open looks like Yolo | Live probe per phase; Follow-up 10 |

## 7) Verification

- `.venv-PowerAtlas/Scripts/python -m pytest tests/test_web.py tests/test_config.py --timeout=300`
- `node tests/acp_page.test.mjs`
- `.venv-PowerAtlas/Scripts/python _check_test_names.py`
- Protocol probes per Phase 0's procedure for compiled Yolo and Manual blocks (Phases 1-2).
- Live UI on the running instance after the user restarts it: AGENTS.md § Verification Setup.
- `qvalidate` on this file before each commit.

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `README.md` | Config sample; feature bullet (~149); task-mode paragraph (~380); permission section modes, Always blocked, scope, built-in asks, exact seeds, `*` and redirection, case-sensitive commands, file-tool-only writes (~385-416) | 1 |
| `README.md` | Manual rows, Protected items, the editor | 2 |
| `README.md` | The prompt-card button; "Triggered by" (~417-424) | 3 |
| `AGENTS.md` | Terminology: derived agent rewrite (incl. "overlay" wording and stale path), new terms — user-approved diff | 1 |
| `docs/KNOWLEDGE.md` | Probe results; supersession notes (~86, ~269, ~277, ~287) | 4 |
| `plans/ROADMAP.md` | Auto mode cross-referenced from the existing unattended-rules item; ~125 wording | 4 |
| `plans/tests/260701_POWERATLAS.md` | ~154 default posture | 4 |

## Progress Tracker

| # | Phase/Task | Status | Notes |
|---|---|---|---|
| 0 | Pre-flight probes and gate | Done | 4 gates fired, follow-up F-1..F-7, applied as D-38; PD-6 decided as D-39 |
| 1 | Modes, compiler and the mode picker | Done | b4f579d, 682bbc7, 967267e, 687ee20; Terminology 5e719a5 |
| 2 | Custom Manual rules and the rule editor | Done | 97c58c2, 192ba9f, c7111fc, 1f824f1, 1a45415 |
| 3 | "Allow, and always in new sessions" | Done | fc336a1, 25bb9f5, 9bca1ae |
| 4 | Documentation and final live check | Done | 9254b1f, 6abfcbf |

## Dependency Graph

```
Phase 0 (probes, gate)
   |
Phase 1 (config, compiler, Yolo + Manual seed, gate, picker)
   |
Phase 2 (custom rules, editor)
   |
Phase 3 (prompt-card button)
   |
Phase 4 (docs)
```

## Backwards Compatibility

| Item | Strategy | Safety effect |
|---|---|---|
| `acp_permissions_enabled` in `config.toml` | Migrated on load (D-8, D-27), dropped on save | `true` keeps prompting with the old rules, the agents-folder deny kept (D-23) and git prefixes guarded (D-24); `false` gains the floor |
| Derived agent file | Always written; a foreign file at the path is left alone and reported (D-29) | `poweratlas-acp` always in kiro-cli's agent picker (R-4, accepted) |
| Running sessions at upgrade | Keep their bound agent until closed (P-C) | No change mid-session |
| `/api/acp-permissions` payload | `enabled` → `mode`; POST body changes | Only in-repo pages consume it; updated in Phase 1 |
| Rollback to the previous release | Old code defaults `enabled = False` and deletes the derived agent | Manual users land on allow-all (R-11, accepted) |

## File Change Summary

### Created
- `src/power_atlas/templates/partials/acp_permission_rules_modal.html`

### Modified
- `src/power_atlas/config.py`, `agent_profile.py`, `web.py`, `acp.py`
- `src/power_atlas/templates/index.html`, `acp.html`; `src/power_atlas/static/transcript-renderer.js`, `style.css`
- `pyproject.toml` (package-data)
- `tests/test_web.py`, `tests/test_config.py`, `tests/acp_page.test.mjs`
- `README.md`, `AGENTS.md`, `docs/KNOWLEDGE.md`, `plans/ROADMAP.md`, `plans/tests/260701_POWERATLAS.md`

### Deleted
- `src/power_atlas/agents/permissions.yaml`

### Unchanged
- `tools/acp_permission_probe.py`, `launcher.py`, the base agent, `~/.kiro/settings/permissions.yaml`

## 9) Implementation Divergences from Plan

- **Phase 0 — results location.** Exit criterion 1 says "recorded in § 9"; the results table lives in Phase 0's
  implementation notes instead, because this section is for divergences. Rationale: § 9 carried Phase 0 results in
  the prior plan, whose layout this criterion copied.
- **Phase 0 — 11 kiro sessions (13 result entries, two of them non-kiro checks) instead of about 8-10.** S0 found the real tool names (`execute_pwsh`, `grep_search`,
  `file_search`, `invoke_sub_agent`, `subagent_kiro_default`, `disclose_context`); P-0.1 needed an attribution
  control; P-0.9 needed `consent.resource` from one session before allowing it in the next; P-0.12 was retried once.
- **Phase 0 — P-0.7 row choice.** fs_read default Block + allow `./**`; fs_write default Allow + block
  `**/blocked-w/**`; shell seed row plus `echo café*` and `type notes.txt`; web_fetch allow
  `https://example.com/*`; `protected_block = [agents]`. The plan named the row shapes, not which rows carry them.
- **Phase 0 — extra measurements.** Host-pattern web_fetch (P-0.5), lowercase and uppercase short-name globs
  (P-0.11), the P-0.1 search-filter attribution control, and the separators `&`, `||`, `powershell -Command`. Each
  settles how a fired gate is applied.
- **Phase 0 — cleanup residue.** Nine `~/.kiro/sessions/<hash>/` folders created by the probes, each holding only
  `.index`/`.lock`, were left by the first run and removed by the follow-up run after the same check.
- **Phase 0 — follow-up probes F-1 to F-7.** Added after the Security-auditor review to measure its open questions
  (symlinks, trailing dots, exact literals, shell case, write short names, shell redirection, non-ASCII). Results
  in Phase 0's second implementation note; applied in D-38.
- **Phase 1 — gate hook returns a dict** `{in_effect, state, mode, cause, fix}` (plus `remote_cause`/`remote_fix`)
  so the D-34 refusal can name cause and fix without `acp.py` importing `agent_profile`; `_gate_verdict` fails closed
  on anything else.
- **Phase 1 — D-35 detection by a `# Settings fingerprint:` header line** in the compiled block, so a settings change
  is told apart from a hand edit of the file and the check survives a restart.
- **Phase 1 — corrupt config is a first-class state.** `Config._load_error` stops generation, saving (`save_config`
  raises `ConfigUnreadableError`) and the heal; the gate reports not in effect. Not in the plan; added after review
  because a corrupt file loads as defaults, and the default mode is Yolo.
- **Phase 1 — rules constants live in `agent_profile.py`;** `config.py` imports them at call time, and the
  `acp_permission_rules` default factory returns the seed rows (D-26 "never `{}`").
- **Phase 1 — block lists over 100 are kept and refused by name at generation;** only allow lists are capped
  (D-26 said lists are capped on load, but capping a block list would drop protection silently).
- **Phase 1 — no gate hook installed (`mode_gate_hook is None`) refuses Default** (SC-3, D-34).
- **Phase 1 — `find_protected_links(root=None)`** returns `{folder: {count, links[:50]}}` and also reports a
  Protected folder that is itself a link.
- **Phase 1 — `.topbar-menu-keep` added to `topbarMenuTextEntryClick`** so clicking a radio label or a `<details>`
  summary does not close the settings menu.
- **Phase 1 — toast routes refuse a corrupt-config write with a 200 error toast,** JSON routes with 409; the
  htmx shim swaps nothing on non-2xx.
- **Phase 2 — `validate_rules` refuses a missing row, key or list on the save path** instead of seeding it (D-26's
  seeding is a load-time repair; a full-replacement save must not store rules the user never saw).
- **Phase 2 — delete and download verbs get their own warning wording** (`rm`/`del`/`Remove-Item`/`rmdir`/`rd`:
  delete without asking; `curl`/`iwr`/`Invoke-WebRequest`: download from and send data to any site); D-35's
  "equivalent to allow-all" is kept for interpreters and shells.
- **Phase 2 — match-everything widened** to any pattern with no literal character besides `* ? / \ . :` and spaces
  (`./` and `../` exempt). A stored block pattern of that kind now stops generation (fail-closed) and a stored allow
  pattern of that kind is dropped with a warning.
- **Phase 2 — additions beyond the plan text:** `rule_rows` and `rule_problems` in the state payload; an allow-all
  note for Run commands and Write files set to Allow; the Protected item "Skills" renamed "Skill files"; the
  Edit rules row keeps the settings menu open while the rules load; every `.profile-modal` now centred.
- **Phase 3 — the card is offered for Read files, Write files, Run commands, MCP tools, Sub-agents and Skills only**
  (not Web search or Powers, whose matching was never measured); the route refuses the same rows.
- **Phase 3 — file prefills are absolute** (forward slashes, under the session's workspace root from the consent's
  `workspaceRoot`, carried as frame key `ruleRoot`) rather than D-20's relative parent folder: a relative pattern
  is global across sessions and so broader than the prompt.
- **Phase 3 — `..` segments refused in file allow patterns;** loaded sessions eligible only when the persisted
  `agentMode` is `poweratlas-acp`; saving from a card keeps a pending D-35 notice (`keeps_notice`).
- **Phase 3 — shared helpers:** the rule editor's warning helpers moved into `transcript-renderer.js`;
  `markPermissionResolved` leaves an open rule row usable so a save can finish after the prompt resolved (D-19).
- **Step 9 — SE7 fix (2c0025d):** new leaf module `src/power_atlas/permission_rows.py` (no package imports) holds the
  row keys, labels and the six card rows; `web.py`'s allow-rule route now checks `permission_rows.CARD_ROWS` directly,
  so a failed guarded `acp` import no longer refuses every row. Config backups created during QA were deleted at the
  user's request (2026-09-25).
- **Step 9 — `tests/permission_pattern_cases.json` is a new test data file** (a case table shared by the Python and JS
  tests for pattern validation parity). AGENTS.md discourages new test files; the orchestrator's fix brief asked for
  a shared table, which needs a shared file.
- **Step 9 — behaviour changes from the final review:** the in-effect check compares the whole derived-agent file
  (base agent name and digest header lines); a base-agent change or edit reads stale until regenerated, and a failed
  regeneration refuses Default sessions; while the base cannot be read, a self-consistent file stays in effect. The
  gate also regenerates an absent file. The D-35 notice is persisted in `permission-notice.json` (with the last
  generated posture), says what changed (mode, rules, base agent, file), and offers Review rules and Acknowledge; a
  one-time upgrade notice follows migration. The settings panel gains Apply again. Unrelated saves keep the stored
  mode and rules as written (user decision 2026-09-25).
- **Phase 1 — scope addition at the user's request:** the six pre-existing dashboard page-test failures (from
  `8d1782d`) were repaired in this plan (967267e).

## Follow-up Work (Deferred)

1. **Auto mode decider.** Deny, steer with the reason, escalate on repeat (D-5); needs a probe of when a queued
   steer reaches the agent. Seam: the session record keeps `permission_mode` (Step 9 A7); `acp._rule_row` covers only
   row catch-all asks and must be generalised to classify every Manual ask (row, Protected, built-in, vendor) before
   Auto can reuse it (Step 9 A8). Source: D-2, D-5.
2. **Home/root deletion protection.** Left to Auto (D-6).
3. **Symlinked self-config enforcement (PD-6 option B).** The Phase 1 probe (2026-09-25) showed an `fs_write` ask on the absolute resolved target (a path with a space) prompts for a write through the link, so option B is viable: emit Protected rules for resolved targets, shown in the UI; a resolver failure adds no rules and never refuses a session. It matters only when the session workspace contains the target: kiro-cli's built-in `kiro-scope:workspace-escape` already denies writes through a link whose target lies outside the workspace (measured). Source: R-7, D-39.
4. **Shell floor bypass by rephrasing.** Accepted best-effort. Source: R-2, R-12.
5. **Terminal-picker exposure of the allow-all derived agent.** User-accepted. Source: R-4, D-37.
6. **Unanswered prompt cancelled at 1800 s.** Source: R-9.
7. **git textconv / `diff.external` via allowed git commands.** Source: R-8.
8. **Unnamed capabilities** (`context`, `diagnostics`, `sandbox_network`) inherit user scope; probe and name them.
   Source: Sec review #20.
9. **Config lost update between settings routes.** A single config-write primitive would remove it. `/api/save-setting`
   still saves non-permission keys outside the generation lock (Step 9 SEC7; user chose follow-up 2026-09-25).
   Source: R-13.
10. **Runtime confirmation that kiro-cli bound the compiled block.** Source: R-17.
11. **Stale plan paths.** `plans/260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL.md` is still cited at
    `docs/KNOWLEDGE.md` ~273, `acp.py` ~22 and ~896, `plans/ROADMAP.md` ~218 (AGENTS.md's is fixed in Phase 1).
    Pre-existing; reported, not in scope. Source: doc-impact scan.

## Review Log

### 2026-09-24 — Plan review (via /qplan Step 4)

Full effort (Major tier): four personas in parallel with fresh context — **Architect** (gap-critic lens),
**Senior engineer**, **Security auditor**, **Reliability engineer** — plus the mandatory doc-impact sub-agent.
One cycle, per the user's instruction ("1 qreview cycle"). 86 raw findings merged to 44 below (High 13, Medium
23, Low 8). 39 fixed in the plan; 5 escalated to the user as PD-1 to PD-5 and decided 2026-09-24 (D-34 to D-37).

Per-persona confidence before fixes: Architect 50%, Senior engineer 60%, Security auditor 35%, Reliability
engineer 45%.

Deviation from full effort, stated: the per-finding verifier phase and the completeness critic were **not**
dispatched. Instead each High finding was checked by the orchestrator against the code or against a second
persona that raised it independently (32 of 44 findings came from two or more personas). Single-source High
findings (#6, #8, #9, #11, #12, #13) were each confirmed by reading the cited code or the plan's own text.
The deviation trades refutation rigour for about 45 fewer sub-agent runs.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | Phase 1 migrates `true` to Manual and accepts `manual` before any Manual compiler exists (Arch 6, Rel 1, SE 1, Sec 14) | Fixed — D-22: Phase 1 ships the Manual compiler with `SEED_RULES` |
| 2 | High | Every not-in-effect path binds `kiro_default`, silently dropping the floor (Arch 3, Rel 7, Sec 10) | Fixed — D-34 fail closed (user decision after council 4-0) |
| 3 | High | `config.toml`, now the policy source, is unprotected and effective without restart (Arch 2, SE 8, Sec 2) | User: accepted — 2026-09-24, no floor additions (D-36); R-18 |
| 4 | High | `json.dumps` emits surrogate escapes; DEL passes; either can fail kiro-cli's parse open (Arch 8, Rel 3, SE 10, Sec 3) | Fixed — D-14 BMP-only validation, `ensure_ascii=False`, P-0.7 probe |
| 5 | High | Prefix widening rests on `&&` only; other separators and substitution unprobed (Arch 1, Sec 5) | Fixed — P-0.8 with a gate back to exact literals |
| 6 | High | `git diff*`/`git log*`/`git branch*` allow `--output`, `--no-index`, `branch -D` (Sec 4) | Fixed — D-24 block patterns; `git branch` stays exact; Follow-up #9 not closed |
| 7 | High | Migration drops the old `**/.kiro/agents/**` write deny (Arch 7, Rel 14, SE 5, Sec 8) | Fixed — D-23 seeds `protected_block = ["agents"]` for migrated users |
| 8 | High | Button prefill from agent-authored text yields `python*`, `/**`, `C:/**` in one click (Sec 7) | Fixed — D-20 interpreter/destructive exceptions, root/home fallbacks; D-14 rejects `*`/`**` |
| 9 | High | Base agent file is an unguarded upstream of every derived agent (Sec 9) | User: accepted — 2026-09-24, no floor additions (D-36); R-18 |
| 10 | High | Rule sets `{}`/partial may leave capabilities unnamed and fail open (Rel 2; Arch 16, SE 20, Sec 16) | Fixed — D-26 normalises in load and compile; invariants over `{}`/partial |
| 11 | High | Path floor untested against 8.3 short names, `\\?\` and UNC forms (Sec 11) | Fixed — P-0.11 probe with a gate |
| 12 | High | Sub-agent posture inheritance unprobed; `kiro_default` sub-agent may bypass the floor (Sec 6) | Fixed — P-0.12 probe; gate escalates to the user if it bypasses |
| 13 | High | Same-user agent can mint a cookie and POST a posture change (Sec 1) | User: accepted — 2026-09-24, disclosed with warnings and a dashboard notice (D-35) |
| 14 | Medium | Gate hook takes the lock unbounded; a stalled generation hangs session creation (Arch 4, Rel 4, SE 6) | Fixed — D-16 `acquire(timeout=2)`, raise, deterministic test |
| 15 | Medium | Lock placement unspecified; a lock inside `derived_block_state` deadlocks `apply_settings` (Rel 5, Sec 22) | Fixed — D-16 names the only acquirers; state computed after release |
| 16 | Medium | Base-agent save and other routes bypass `apply_settings` (Arch 5, Rel 6, SE 7, Sec 13) | Fixed — D-33 for the base agent; D-30 self-heal; residual R-13 → Follow-up 9 |
| 17 | Medium | SC-7's remote read-only settings view can never render (Arch 15, SE 2, Sec 23) | Fixed — SC-7 reworded; D-21 constant; editor branch dropped |
| 18 | Medium | Kiro built-in asks contradict "Yolo never prompts" (Arch 11, SE 3, Sec 18) | Fixed — P-0.10 probe; SC-2 reworded to disclose them |
| 19 | Medium | Button shown on prompts a row rule cannot silence (Protected, built-in, vendor mode) (Arch 10, SE 4, Sec 21) | Fixed — D-31 server-computed `ruleEligible` |
| 20 | Medium | MCP/sub-agent/skill match shape unmeasured (Arch 9, SE 9) | Fixed — P-0.9 with per-capability gate |
| 21 | Medium | Test inventory incomplete: injection tests, invariant conflicts, page pins (Arch 14, SE 11) | Fixed — Phase 1 Tests lists port/retire targets with reasons |
| 22 | Medium | `isolated_config` has no base agent; always-generate would error in every lifespan test (Rel 9) | Fixed — fixture seeds a base; `MINIMAL_BASE` test |
| 23 | Medium | Clean kiro installs lack `kiro_default.md` (deployed by agent-playbook) (Rel 8) | Fixed — D-28 `MINIMAL_BASE` fallback |
| 24 | Medium | `compile_block` raising inside the state read is unspecified (Rel 10) | Fixed — D-15 never raises; error surfaced |
| 25 | Medium | Hand-edited rules table can make `load_config` raise (Rel 11) | Fixed — D-26 plus a table-driven `test_config.py` test |
| 26 | Medium | Unreadable mode and `auto` load as Yolo, the least restrictive (Arch 17, Rel 12, Sec 15) | Fixed — D-25 `auto`/junk → Manual with `mode_warning` |
| 27 | Medium | `_generate` now clobbers a foreign file at the target (Rel 13) | Fixed — D-29 refuses `unknown` files |
| 28 | Medium | No runtime check that kiro-cli applied the block (Rel 15) | Fixed partially — live probe per phase; residual R-17 → Follow-up 10 |
| 29 | Medium | Hand edits never regenerate; D-15 then reads stale until restart (Rel 16) | Fixed — D-30 self-heal |
| 30 | Medium | `apply_settings` error contract unspecified; card claims success on generation failure (Rel 17) | Fixed — D-32 |
| 31 | Medium | `deny`+`exclude` and non-empty block lists never probed (Sec 17) | Fixed — P-0.7 extended |
| 32 | Medium | R-4 understates who the picker exposure affects (Arch 12) | User: accepted — 2026-09-24 (D-37) |
| 33 | Medium | Rollback lands Manual users on allow-all, accepted by the author only (Sec 19) | User: accepted — 2026-09-24 (D-37) |
| 34 | Medium | `acp.py` refusal strings, `index.html` "turn it off and on" copy, renderer comment omitted from scope (doc-impact; Arch 18, SE 17) | Fixed — named in Phase 1 scope and text |
| 35 | Medium | Floor misses `acp-secrets.bin`, SQLite sidecars, secret-file writes (Rel 20, SE 13, Sec 12) | Fixed for sidecars and secret temp files within agreed items; the rest User: accepted — 2026-09-24 (D-36) |
| 36 | Medium | ROADMAP target for Follow-up #9 wrong; Auto would duplicate an existing item (doc-impact) | Fixed — Phase 4 cross-references the existing item; #9 stays open |
| 37 | Low | Race test was stochastic (Rel 18) | Fixed — deterministic Event-based test |
| 38 | Low | Backslash shell pattern may be dead (Rel 19, SE 12) | Fixed — P-0.3 probe; variants dropped unless literal |
| 39 | Low | WARNINGs would fire on every `load_config` call (Rel 21) | Fixed — once per value per process |
| 40 | Low | Migration precedence with both keys unspecified (Rel 22) | Fixed — D-27 |
| 41 | Low | Card should pick the Allow option by `kind`, not id (SE 18) | Fixed — D-19 |
| 42 | Low | Phase 1 live check covered only the shell tier (SE 19) | Fixed — read-file tool added to the live criterion |
| 43 | Low | "Capability silence never applies" overstated (Sec 20) | Fixed — scope note and Follow-up 8 |
| 44 | Low | AGENTS.md Terminology stays false from Phase 1 to 4 (Arch 19); D-19 resolved-meanwhile race (Arch 20, Sec 24) | Fixed — Terminology diff at end of Phase 1; D-19 handles the race |

Doc-impact scan: every hit dispositioned in § 8, the phase text or as false-positive (historical dated notes,
the peek/restart "overlay", `kiro_default` mode-name mentions, other plans' unqualified IDs in `acp.py`). Four
stale `plans/260921_…` paths reported as Follow-up 11.

### 2026-09-24 — Council on PD-1 and PD-2 (via /qcouncil, pre-escalation)

Full tier (large blast radius). Per decision: assumption challenger, one advocate per option, one prosecutor per
option, four jurors. PD-1 jurors (Security auditor, Reliability engineer, End-user advocate, Senior engineer):
**A, fail closed, 4-0**; the End-user advocate was conditional on not-in-effect staying rare. PD-2 jurors (Security
auditor, End-user advocate, Senior engineer, Architect): **A, accept and disclose, 4-0**; the Architect was
conditional on an interpreter's own file writes not being bound by any kiro path rule (unmeasured; follows from how
kiro rules bind). Every juror asked for the refusal to name the cause and the fix (PD-1), and for a fresh acceptance
with the interpreter warning on the card (PD-2); both are in D-34 and D-35. The user chose A for both, "add nothing"
for PD-3, and accepted PD-4 and PD-5 on 2026-09-24.

### 2026-09-24 -- Implementation Review (after Phase 0, persona: Security auditor)

Implementation health: Green (finding 1 decided by the user as D-39 on 2026-09-24; the rest fixed).
13 findings (3 High, 6 Medium, 4 Low). Standard effort, one cycle (user cap, below).

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | P-0.4 tested a link into `.kiro/steering`; links pointing out (the user's own steering links) likely escape Protected | User: accepted — 2026-09-24, detect and name plus an enforcement probe (D-39, PD-6 after council 3-1) |
| 2 | High | Short-name globs added to fs_read only, not the fs_write floor or Protected | Fixed — F-5 measured the write bypass; short-name variants added to fs_write floor and Protected |
| 3 | High | Trailing-dot and trailing-space spellings neither probed nor covered | Fixed — F-2/F-5 measured: they name a different, missing folder, not an alias; recorded in D-38a and R-3 |
| 4 | Medium | "Exact literals cannot carry a redirection" was never measured | Fixed — F-3 measured redirection and extra arguments prompt; Phase 1 live criterion adds `git status > x.txt` |
| 5 | Medium | Shell redirection writes skip every fs_write rule, yet the floor reads as unconditional | Fixed — F-6 confirmed; D-38g file-tool wording, `*poweratlas-acp*` shell deny, R-14 |
| 6 | Medium | Redirection warning fired only on a trailing `*`, not a mid-pattern one | Fixed — warning on any `*` in a shell allow pattern (D-38e, Phase 2 editor) |
| 7 | Medium | P-0.8's "README says so" branch not applied | Fixed — Phase 1 README checkbox and § 8 row name exact seeds, redirection, case, file-tool scope |
| 8 | Medium | Shell case-insensitivity assumed from an fs_read measurement | Fixed — F-4 measured shell patterns case-sensitive; upper-case copies added (D-38f), R-19 |
| 9 | Medium | P-0.7 row claims UTF-8 survives, but frames showed mojibake | Fixed — F-7 raw bytes show clean UTF-8; the mojibake was a display artefact; P-0.7 row stands |
| 10 | Low | Shell short-name stems partial and undisclosed | Fixed — all stems added in lower and upper case; a test pins the list |
| 11 | Low | `~*` globs miss NTFS hashed short names; `power-~*` over-matches | Fixed — hashed names disclosed in the floor text and R-3; the over-match is a deny on reads only |
| 12 | Low | `/`-and-`\` mirroring covers only the floor, not user or prefilled patterns | Fixed — the editor states literal, case-sensitive matching; no automatic mirroring (D-38b, D-38f) |
| 13 | Low | Criterion 1 text edited at tick time; session count wrong | Fixed — wording restored; § 9 says 11 kiro sessions, 13 result entries |

Cycle 2 not run: the user's `/qdev` invocation capped review at one cycle per phase ("1 qreview cycle per
qreview"; default 2). Findings 2-4 and 8-9 were closed by the follow-up measurement F-1 to F-7 (second
implementation note), not by argument. The reviewer checked P-0.1, P-0.3-P-0.5, P-0.7-P-0.12 against the raw
frames and found no misclassified row; P-0.2, P-0.6 and S0/S4a/S7 were not opened. Review Log closures audited: none
reason-less.

### 2026-09-24 -- Council on PD-6 (via /qcouncil, pre-escalation from /qdev Phase 0)

Full tier (blast radius over three or more files). Options: A disclose; B resolve links at compile time; C detect
and name. The assumption challenger's first run failed on an API session limit (HTTP 429); the user resumed after
the reset and it was re-run. Challenger: all 9 global steering files and 16 of 27 skills entries are links into
agent-playbook; a resolve walk over them takes 56 ms; B's premise (an absolute-target rule catches a write through
a link) is strongly supported but unmeasured. Jurors: Reliability engineer C, End-user advocate C, Senior engineer
C, Security auditor B (conditional on that premise; falls back to C). **C, 3-1.** The user chose "C + probe B"
(D-39).

### 2026-09-25 -- Implementation Review (after Phase 1, persona: Security auditor, Reliability engineer, Senior engineer)

Implementation health: Green.
18 findings after merge (1 High, 4 Medium, 13 Low), plus a targeted Full-effort Security re-review of the High fix
(1 High, 5 Low). Standard effort, one cycle per the user's cap; the targeted re-review was the user's choice
(2026-09-25) because the High fix touches a security gate.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | An unreadable `config.toml` regenerated a Manual user's agent as Yolo at startup and saves wrote defaults over it | Fixed — 682bbc7: no generation or save on `_load_error`; gate not in effect; `config_error` shown |
| 2 | Medium | D-30 heal ran unbounded under the lock, outside the gate's 2 s budget | Fixed — 682bbc7: heal in a worker inside the remaining budget; deterministic test |
| 3 | Medium | D-35 notice missed startup and was cleared by unrelated saves | Fixed — 682bbc7: startup compares fingerprints; cleared only by mode/rules changes |
| 4 | Medium | `os.path.isjunction` needs Python 3.12; `pyproject` allows 3.11 | Fixed — 682bbc7: reparse-tag fallback; never raises per entry |
| 5 | Medium | D-39 probe box ticked but Follow-up 3 not updated | Fixed — Follow-up 3 and R-7 carry the probe result (this update) |
| 6 | Low | Permission routes took the lock unbounded | Fixed — 682bbc7: 5 s bounded acquire, "try again" answer |
| 7 | Low | Lock-timeout refusal was generic with a traceback | Fixed — 682bbc7: names the cause, WARNING without traceback |
| 8 | Low | Invalid rules, `protected_block` junk and bad defaults dropped silently | Fixed — 682bbc7: logged once, `rules_warning` shown |
| 9 | Low | D-28 note logged on every regeneration | Fixed — 682bbc7: once per process |
| 10 | Low | `***`, `**/*`, `*/**` passed D-14 | Fixed — 682bbc7: patterns of only `*`, `/`, `\` rejected |
| 11 | Low | A Protected folder that is itself a link went unreported | Fixed — 682bbc7: reported as a folder-level link |
| 12 | Low | D-34 refusal leaked absolute paths to remote clients | Fixed — 682bbc7, 687ee20: redacted at folder boundaries, incl. the config folder |
| 13 | Low | README said a hand edit is picked up by the next session for every key | Fixed — 682bbc7: wording per key and session kind |
| 14 | Low | `_gate_verdict` accepted a bare bool only for a test fixture | Fixed — 682bbc7: fixture returns the dict; bool fails closed |
| 15 | Low | Manual-seed criterion's agents-write clause met only in the migrated run | User: accepted — 2026-09-25, behaviour correct per D-5/D-23; the criterion predates D-23 |
| 16 | Low | Self-reported divergences not in § 9 | Fixed — recorded in § 9 |
| 17 | Low | Six dashboard page tests failing (pre-existing, `8d1782d`) | Fixed — 967267e (user chose to fix in this plan, 2026-09-25) |
| 18 | Low | `web.py` docstring cited `derived_block_state()` without its argument | Fixed — 682bbc7 |
| R1 | High | Direct `save_config` writers still wrote defaults over a corrupt config, yielding Yolo | Fixed — 687ee20: `save_config` refuses a failed-load config; one handler; repro re-run |
| R2 | Low | `.bak` named even when the backup failed | Fixed — 687ee20 |
| R3 | Low | Remote redaction matched bare prefixes and missed the config folder | Fixed — 687ee20 |
| R4 | Low | Stale "could not be read" status lingered after a hand fix | Fixed — 687ee20 |
| R5 | Low | `heal_stale_locked` relied on callers to check `_load_error` | Fixed — 687ee20 |
| R6 | Low | Any `TimeoutError` in the heal read as "being applied" | Fixed — 687ee20 |

Contributing personas: #1 raised by all three; #2 Reliability and Senior engineer; #3 Reliability and Security; #4
Security and Senior engineer; #10-12 Security; #6-9 Reliability; #13-16, #18 Senior engineer. The Security
re-review returned "HIGH FIX: CLOSED" for #1 and raised R1-R6. No cycle-2 re-review of 687ee20 (user cap); the
orchestrator re-ran the re-review's repro against it (Manual config preserved, gate not in effect). Step 5b QA: PASS
(see the Phase 1 QA note).

### 2026-09-25 -- Implementation Review (after Phase 2, persona: Security auditor, End-user advocate)

Implementation health: Green.
19 findings (1 High, 4 Medium, 14 Low), all fixed in c7111fc; one QA finding fixed in 1a45415. Standard effort, one
cycle per the user's cap.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| S1 | High | A rules-only save adopted an unhealed external config change and suppressed the D-35 notice | Fixed — c7111fc: mutation path compares the on-disk fingerprint; notice raised; probe A/B re-run |
| S2 | Medium | Editor turned a stored non-list block into `[]`, and Save deleted that protection | Fixed — c7111fc: `rule_problems` in the GET; editor refuses to open with a named toast |
| U1 | Medium | Esc, × and Cancel silently discarded unsaved edits | Fixed — c7111fc: in-dialog Discard / Keep editing |
| U2 | Medium | A refused save showed its error only in the footer, in internal terms | Fixed — c7111fc: structured detail; chip marked, plain-words message on the row |
| U3 | Medium | URL-style web-fetch patterns accepted without warning but never match | Fixed — c7111fc: warning names the host to use |
| S3 | Low | `validate_rules` filled missing `protected_block`/`allow`/`block` with `[]` | Fixed — c7111fc: every key required |
| S4 | Low | Effective match-alls (`?*`, `*.*`, `?:/**`) accepted | Fixed — c7111fc: widened match-everything check and breadth warning |
| S5 | Low | Interpreter warning missed `*python*`/`& python`; no allow-all note for Write files | Fixed — c7111fc |
| U4 | Low | Save disabled the focused button, dropping focus | Fixed — c7111fc: stays focusable while busy |
| U5 | Low | Focus return after close fell to body | Fixed — c7111fc (close paths) and 1a45415 (after a successful save) |
| U6 | Low | Duplicate pattern cleared the input silently | Fixed — c7111fc: "Already listed" |
| U7 | Low | Client accepted control characters the server refuses | Fixed — c7111fc |
| U8 | Low | Dialog pinned top-left, no gutter on phones (pre-existing `.profile-modal`) | Fixed — c7111fc: `margin: auto` |
| U9 | Low | Chip remove targets below 24×24 px | Fixed — c7111fc |
| U10 | Low | Redundant session-folder warning text | Fixed — c7111fc |
| U11 | Low | Protected toggle wording unclear; "Skills" clashed with the Skills row | Fixed — c7111fc: "Block outright: …"; "Skill files" |
| U12 | Low | Live regions recreated on redraw | Fixed — c7111fc: one persistent region per row |
| U13 | Low | Settings-menu Protected summary showed no linked-item count | Fixed — c7111fc |
| U14 | Low | Precedence between the lists unexplained | Fixed — c7111fc: intro sentence; both-lists warning |
| Q1 | Medium | QA: focus fell to `<body>` after a successful save (QA FAIL floor Medium) | Fixed — 1a45415; re-checked in Chrome |

No cycle-2 review of c7111fc/1a45415 (user cap); the orchestrator re-ran the security probe outcome (reported by the
fixer) and re-drove every changed surface in real Chrome (Phase 2 QA note).

### 2026-09-25 -- Implementation Review (after Phase 3, persona: Security auditor, Senior engineer)

Implementation health: Green.
15 findings after merge (1 High, 5 Medium, 9 Low), all fixed in 9bca1ae. Standard effort, one cycle per the user's cap.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | File prefill escaped the home/drive fallback (`C:\Users\desktop.ini` → `C:/Users/**`), no warning | Fixed — 9bca1ae: ancestor-of-home and workspace tests; exact resource for wildcards, `.`/`..`, device paths |
| 2 | Medium | `..` resources prefilled `../**`; the server accepted them | Fixed — 9bca1ae: prefill uses the exact path; `..` refused in file allow patterns |
| 3 | Medium | Relative file prefill became a global rule across sessions | Fixed — 9bca1ae: absolute prefill under `ruleRoot`; relative patterns warn |
| 4 | Medium | Saving from a card cleared the D-35 notice, which /acp never shows | Fixed — 9bca1ae: allow-rule keeps the notice; card points at it |
| 5 | Medium | Chained commands: the card promised "will not ask" for a rule covering one part | Fixed — 9bca1ae: split-command hint and outcome text; README corrected |
| 6 | Medium | Redirection warning only on `*`, not `?` or `[` | Fixed — 9bca1ae |
| 7 | Low | Loaded sessions' eligibility used the unconfirmed modeId sent | Fixed — 9bca1ae: persisted `agentMode`; unreadable → ineligible |
| 8 | Low | Warnings ignored the rest of a compound command | Fixed — 9bca1ae: warnings on the whole command |
| 9 | Low | Route accepted `web_search` and `power`, never offered by the card | Fixed — 9bca1ae: rows outside `_RULE_ROWS` refused |
| 10 | Low | Focus fell to body when a resolved card's row was cancelled | Fixed — 9bca1ae |
| 11 | Low | A duplicate on a full list was refused as "full" | Fixed — 9bca1ae: membership checked first |
| 12 | Low | Renderer row set not pinned to `_RULE_ROWS` | Fixed — 9bca1ae: pin test |
| 13 | Low | Answer-by-kind mutation survived the tests | Fixed — 9bca1ae: `reject_once`-first Save test |
| 14 | Low | Dashboard page coverage thinner than "on both pages" | Fixed — 9bca1ae: dashboard error, resolved-meanwhile and web-fetch tests |
| 15 | Low | README claimed task modes never show the button, untrue for reloaded sessions | Fixed — 9bca1ae with #7 |

No cycle-2 review of 9bca1ae (user cap); the orchestrator re-drove the button live in Chrome (Phase 3 QA note) and the
fixer re-ran the reviewer's prefill probe.

### 2026-09-25 -- Implementation Review (after Phase 4, persona: Senior engineer)

Implementation health: Green.
2 findings (0 High, 1 Medium, 1 Low), both fixed in 6abfcbf. Standard effort, one cycle. The reviewer checked ten
KNOWLEDGE facts against the raw probe files and re-ran the Phase 4 grep (one intentional hit).

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | Medium | KNOWLEDGE claimed kiro-cli splits inside `powershell -Command "…"`; raw P-0.8i shows the whole command matched | Fixed — 6abfcbf; plan's Phase 0 note carries a dated correction |
| 2 | Low | Blanket-ask-needs-exclude cited probes that only showed the working case | Fixed — 6abfcbf: cites the prior plan § 9 Step 4 |

### 2026-09-25 -- Post-Implementation Review

Overall implementation health: Green.
Personas: Senior engineer, Security auditor, Reliability engineer, End-user advocate, Architect (full effort).
Cycle 1: 2 High, 10 Medium (1 refuted by a verifier), about 30 Low — all fixed except SE7 (Follow-up 12) and the two
user decisions below. Cycle 2 (targeted re-review of the cycle-1 fix commits, Security auditor and Reliability
engineer): 2 High, 3 Medium, 9 Low, all introduced by the cycle-1 fixes and all fixed; no further review cycle at the
user's direction (2026-09-25: "Fix but no more review cycle"). QA verification: PASS after one fix round (6 surfaces
verified in real Chrome, 20+ probes executed).

#### Test execution summary

| Phase | Tests | QA | Notes |
|---|---|---|---|
| 0: Pre-flight probes and gate | not_run | SKIP | Probe-only phase; 12 probes plus follow-up F-1..F-7 against separate kiro-cli processes |
| 1: Modes, compiler and the mode picker | pass | PASS | Live Yolo/Manual probes; restart and Playwright + Chrome MCP QA |
| 2: Custom Manual rules and the rule editor | pass | PASS | Chrome MCP QA after one focus fix |
| 3: "Allow, and always in new sessions" | pass | PASS | Live button test end to end in Chrome |
| 4: Documentation and final live check | not_run | SKIP | Docs-only phase; facts spot-checked against raw probe files |

Final state: pytest `tests/test_web.py tests/test_config.py` 2236 passed; `node tests/acp_page.test.mjs` 805/805;
`_check_test_names.py` clean.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| H-A | High | [End-user, Reliability] "Save the mode again" impossible; an absent derived agent was never regenerated | Fixed — b95026a, 561bdf7: gate regenerates absent files; Apply again control; fix texts name it |
| H-B | High | [Security] In-effect check compared only the first `permissions:` block; tampering elsewhere read "on" | Fixed — b95026a: whole-file comparison with base name and digest; tamper heals with a "file" notice |
| M-1 | Medium | [Senior, Reliability] Heal raised a false outside-change notice after the dashboard's own failed save | Fixed — b95026a, 6c0f8b9 |
| M-2 | Medium | [Reliability, Security] Posture notice lost on restart | Fixed — b95026a: persisted in `permission-notice.json` |
| M-3 | Medium | [End-user] Notice text implied choosing the mode undoes outside rules changes | Fixed — 561bdf7: says what changed; Review rules and Acknowledge |
| M-4 | Medium | [End-user] Only the checked mode's description shown | Fixed — 561bdf7: both descriptions always shown |
| M-5 | Medium | [End-user, Senior] Manual description was fixed seed text | Fixed — 561bdf7: derived from the stored rules |
| M-6 | Medium | [End-user] No upgrade notice | Fixed — b95026a, 561bdf7: one-time persisted upgrade notice |
| M-7 | Medium | [End-user, Senior] SC-3's reopened-session disclosure missing | Fixed — 561bdf7: scope note, Always blocked note, README |
| M-8 | Medium | [End-user, Senior] Next-step advice always blamed the base agent | Fixed — 561bdf7: cause-specific next steps |
| M-9 | Medium | [Architect] `acp.py` docstring claimed `config` imports nothing from the package | Fixed — 24a5904: docstring corrected; import-isolation guard test |
| M-10 | Medium | [Architect] Lock protocol split across `web.py` and `agent_profile.py` | Fixed — 3ea9262 (user chose Fix now at /qclose, 2026-09-25): `agent_profile.gate_check`; `web.py` touches no private names |
| A10 | Low | [Architect] Unrelated saves persisted load-time-normalised rules and mode | Fixed — b95026a: raw stored values written back (user chose Fix, 2026-09-25) |
| SEC7 | Low | [Security] `/api/save-setting` saves outside the generation lock (pre-existing R-13) | User: accepted — 2026-09-25, left for Follow-up 9 |
| SE7 | Low | [Senior] Row-label map repeated four times | Fixed — 2c0025d (user chose Fix, 2026-09-25): leaf module `permission_rows.py`; card labels from the frame |
| L-* | Low | [all] About 28 further Lows (EU8-14, SE4/6/9, RE4-10, A3/4/5/6/7/9, SEC3/4/5/6/8) | Fixed — b95026a, 561bdf7, 24a5904 |
| C-H1 | High | [Security, cycle 2] Saved fingerprint never cleared, silencing outside reverts | Fixed — 6c0f8b9; reviewer probe re-run clean |
| C-H2 | High | [Security, cycle 2] Absent-file heal raised no notice for an outside change | Fixed — 6c0f8b9: last generated posture persisted; probe re-run clean |
| C-M1 | Medium | [Reliability, cycle 2] Transient base-read failure refused sessions on a correct file | Fixed — 6c0f8b9: self-consistency fallback and one retry |
| C-M2 | Medium | [Reliability, cycle 2] Base-caused refusal pointed at Apply again | Fixed — 6c0f8b9: names the base agent file |
| C-M3 | Medium | [Security, cycle 2] Third lock acquirer not in D-16 | Fixed — 6c0f8b9 comment; plan amended as D-16a |
| C-L* | Low | [cycle 2] Nine Lows (C-L1..C-L9) | Fixed — 6c0f8b9 |
| Q-1 | Medium | [QA] Apply again always visible: `.topbar-menu-row` `display:flex` overrode `hidden` | Fixed — 936794b; confirmed in Chrome |
| Q-2 | Medium | [QA] Focus fell to the page body after Apply again or Acknowledge | Fixed — db5b8e1; confirmed in Chrome |

Verification phase (full effort): one verifier per High/Medium, grouped into three verifier sub-agents to stay within the
API rate limit that had killed the first dispatch (deviation stated); Lows were not separately verified — the fixer was
told to reject any it could refute (none were). Completeness critic: not dispatched (deviation stated). The first Step 9
dispatch (five personas) failed on an API session limit (HTTP 429) and was retried at the user's request after the
reset.

QA (Step 9b, 2026-09-25, real Chrome via Claude-in-Chrome MCP after a restart at 08:12; standalone Playwright for phone
widths): settings panel (both descriptions, Manual summary, reopened-session scope note, Protected count); outside
change in `config.toml` → session gate heal → notice "Manual instead of Yolo" with Review rules / Acknowledge →
Acknowledge cleared it; foreign `poweratlas-acp.md` → Default refused with cause, fix and task-mode alternative, file
left untouched (D-29) → file removed → NOT IN EFFECT badge and Apply again → regenerated, in effect, focus on the checked
radio; prompt card in Manual shows row names and the PowerAtlas source label with the always-allow button; 375 px and
320 px widths without horizontal scroll, editor gutters 16-18 px. Two QA defects found and fixed (Q-1, Q-2). State
restored: Yolo, seed rules, no pending notice; QA sessions deleted after checking `createdAt`/`agentMode`/
`workspacePaths`. Backups: `config.toml.pre-permission-modes`, `.pre-phase2-restart`, `.pre-phase3-restart`,
`.pre-final-qa` in `%LOCALAPPDATA%\power-atlas`.

## Harness Improvement Opportunities

- `/qexplore`'s probe gate says to run side-effecting probes only with consent, while `shared.md` says one
  question at a time — cost: the first interview message had to carry both a probe-consent request and Q1,
  which blurs the one-question rule — suggested change: state that a probe-consent request may accompany the
  first question, or give it its own pre-interview turn.
- The harness flagged a research sub-agent's report as instruction-shaped because it quoted vendor
  documentation about `bypassPermissions` — cost: none beyond a warning banner, but a reader could mistake a
  quoted doc for an injected instruction — suggested change: none required unless it recurs.
- `/qreview` full effort prescribes one verifier sub-agent per finding plus a completeness critic; with four
  personas on a Major plan that is ~45 extra dispatches when most findings are already corroborated by two or
  more personas — cost: the orchestrator had to choose between the literal rule and a disclosed deviation —
  suggested change: let cross-persona corroboration substitute for a verifier, and dispatch verifiers only for
  single-source findings.
- An API session limit (HTTP 429) killed a council sub-agent mid-run; the council's partial-failure rule then halts the whole pipeline — cost: about an hour of wall-clock waiting on the reset and one re-dispatch — suggested change: none to the rule; a note in `/qcouncil` that a rate-limit failure is a Retry-after-reset case, with completed advocate briefs saved to scratch so they are reusable.
- The Claude-in-Chrome MCP extension was not connected even after starting Chrome, while `/qqa`'s browser gate names Playwright MCP `browser_*` calls and the project has no Playwright MCP — cost: two tool round trips and a gate the evidence cannot literally satisfy — suggested change: let `/qqa`'s browser gate accept a project-documented standalone Playwright recipe (`AGENTS.md § Verification Setup`) as equivalent evidence.
- Chrome MCP ref-based clicks issued right after a navigation were silently dropped (no pointer events reached the page), which first read as an app bug — cost: about eight tool calls of diagnosis — suggested change: `/qbrowser-test` guidance to wait for page settle, or prefer coordinate clicks after a navigation, and to log capture-phase pointer events before diagnosing "click does nothing".
- Sub-agents may not write report files ("Subagents should return findings as text"), while `/qdev` and the multi-agent rules say a sub-agent's deliverable is a file — cost: every reviewer spent a turn on a refused write and the orchestrator re-typed findings into scratch files — suggested change: state in `/qreview`'s spawn contract that findings return as text and the orchestrator persists them.
- Page tests run without a CSS engine, so a `display` rule overriding `hidden` (Q-1) passed 803 checks and was found only in a real browser — cost: one QA round — suggested change: a project convention (or lint) that any class setting `display` on a `hidden`-toggled element also declares `[hidden] { display: none }`.
- `/qdev` has no shape for a probe-only phase (no code commit): the `feat`/`docs` pairing and "sub-agent commits code" steps did not apply — cost: small; the orchestrator improvised a docs-only commit and confirmed `commit-pairing` passes — suggested change: state in `/qdev` Step 4 that a results-only phase produces one `docs(<slug>): phase N progress (code: none)` commit.
- The exploration's lower-stakes assumptions were shown at the checkpoint but not written as an
  `Assumptions (unconfirmed)` adjunct, so `/qplan` had no labelled list to route — cost: the planner folded them
  in as its own D-rows without a user confirmation step — suggested change: `/qexplore` Step 3 should always write
  the adjunct when the checkpoint listed defaults, even at medium depth.
