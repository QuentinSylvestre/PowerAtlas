# ACP Permission Modes: Yolo, Auto and Manual

> **Date**: 2026-09-24
> **Status**: Draft  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Last Updated**: <set by /qclose at archival>
> **Scope**: Replace the on/off ACP permission profile with three permission modes (Yolo, Auto, Manual), an always-on hard-deny floor, and a plain-language Manual-mode rule editor that compiles to the derived agent
> **Tier**: Major
> **Estimated effort**: 6-8 days

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

Only `&&` was measured; `;`, `|`, redirection, newline, backticks and PowerShell subexpressions were not.
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
| D-7 | Editor shape (Q7) | One row per action kind: default Allow/Ask/Block plus allow and block pattern lists; `*`-prefix shell patterns; seeded from the old overlay; prompt-card button in scope | Raw rule table; YAML editor | User asked for intuitive editing; the raw model has silent-failure traps |
| D-8 | Migration (Q8) | `true` → Manual, `false`/fresh → Yolo, old key in `_LEGACY_KEYS`, no Off state; derived agent always written | Keep a fourth "Off" mode | User accepted R-4, re-confirmed with its full scope (PD-4, 2026-09-24) |
| D-9 | Remote (Q9) | Posture changes only from this computer | Allow the button remotely | Posture-widening stays on the machine |
| D-10 | Terms (C2) | "permission mode", "Yolo/Auto/Manual", "Always blocked", "Protected" | "profile", "deny floor" in UI | AGENTS.md Terminology update proposed at the end of Phase 1 |
| D-11 | Rule data model | `acp_permission_rules` = `{<row>: {default, allow[], block[]}, protected_block[]}`, rows `fs_read, fs_write, shell, web_fetch, web_search, mcp, subagent, skill, power` | Free-form rule list | Uniform rows keep editor and compiler simple; "the session folder" is a seeded `./**` in `fs_read.allow` |
| D-12 | Powers row | A "Powers" row (default Ask) | Hard-code `power: ask` | Every capability must be named (capability-silence inheritance) |
| D-13 | Compilation per row | `block` → `deny match`; default `allow` → `allow` (allow list ignored); default `ask` → `allow match allow` + `ask exclude allow`; default `block` → `allow match allow` + `deny exclude allow`; empty `allow` → bare effect | User-ordered rules | Encodes most-restrictive-wins by construction; `deny`+`exclude` is probed in P-0.7 |
| D-14 | Pattern validation and emission | 1-200 characters from printable BMP only (reject C0/C1 controls, DEL, surrogates, U+2028/2029, U+FEFF), max 100 per list, not blank, not `*` or `**` alone; emitted with `json.dumps(s, ensure_ascii=False)` | Default `json.dumps` | Surrogate escapes and DEL can make kiro-cli reject the frontmatter, which fails open silently |
| D-15 | In-effect check | `derived_block_state(config)` compares the on-disk block with `compile_block(config)` and never raises (a compile error → `unknown` with the message in `generation_error`) | Byte-compare to one overlay | SC-9; otherwise every edit reads as stale |
| D-16 | Locking | `apply_settings(mutate)` holds `_generation_lock` across load, mutate, `save_config`, generate. The **only** other acquirer is `_derived_agent_in_effect`, with `acquire(timeout=2)`; timeout raises. `derived_block_state`, `compile_block`, `_apply_locked` never acquire it; routes compute the returned state after release | Lock-free gate; unbounded acquire | Closes the config-ahead-of-file window without letting a stalled generation hang session creation |
| D-17 | Overlay package data | Delete `src/power_atlas/agents/permissions.yaml` and the `agents/**` package-data entry; floor, Protected set and seed become constants in `agent_profile.py` carrying the overlay's measured-semantics comments | Template the YAML | The block is computed; one source of truth |
| D-18 | `_remove` path | Delete `_remove` and the off branch | Keep for rollback | No Off state; unused code is deleted per governance |
| D-19 | Prompt-card button | "Allow, and always in new sessions…" opens an inline editor; Save adds the rule and answers this prompt with the option whose `kind` is `allow_once`; if the prompt resolved meanwhile, the rule is still saved and the card says so | Save only adds the rule | One click for an action the user evidently approves |
| D-20 | Prefill | Shell: `triggeringResource` else `resource`; first token plus a second token matching `^[A-Za-z][A-Za-z0-9_-]*$`, then `*` — **except** interpreters, shells and destructive verbs (`python*`, `py`, `node`, `powershell`, `pwsh`, `cmd`, `bash`, `sh`, `rm`, `del`, `Remove-Item`, `rmdir`, `rd`, `curl`, `iwr`, `Invoke-WebRequest`, `iex`, `Invoke-Expression`), which prefill the exact command. File: parent folder + `/**` unless the parent is empty, `.`, a drive root or the home folder, then the exact resource. MCP/sub-agent/skill: `resource` exactly, if P-0.9 confirms it matches. Web fetch: hidden unless P-0.5 confirms a URL pattern. Always editable | Exact command always | Prefill comes from agent-authored text, so it must never propose the broadest rule |
| D-21 | Dashboard local flag | `var ACP_LOCAL = true` in `index.html` with a comment that `/` is loopback-only by construction | Derive per request | The dashboard cannot be served remotely; the shared renderer reads one global |
| D-22 | Manual in Phase 1 | Phase 1 ships the Manual compiler with `SEED_RULES` (no editor); Phase 2 adds the editor and custom rules | Refuse `manual` until Phase 2 | Every commit leaves migrated users on a defined, prompting rule set |
| D-23 | Migrated agents folder | `true → manual` seeds `protected_block = ["agents"]`, keeping today's `**/.kiro/agents/**` write deny | Protected ask | Honours SC-8 "seeded from the current overlay" |
| D-24 | Git seeds | Seed `git status*`, `git log*`, `git diff*` plus shell **block** patterns `git *--output*`, `git *--no-index*`, `git *--ext-diff*`; `git branch` stays exact. All prefixes conditional on P-0.8 | Widen `git branch*`; no block patterns | `git diff --output`, `--no-index` and `git branch -D` are write/read/destroy primitives |
| D-25 | Mode load | Case-insensitive; `auto` → `manual`; any other unreadable value → `manual`, with a `mode_warning` exposed in the state payload; logged once per value per process | Junk → `yolo` | Fail toward prompting, matching Auto's "behaves like Manual" |
| D-26 | Rule normalisation | In `load_config` and again inside `compile_block`: missing row → seed row; invalid default → `ask`, keeping the row's lists; invalid **allow** patterns dropped; an invalid **block** pattern refuses generation (named error) rather than being dropped; lists capped at 100 on load; `{}` is never persisted as "no rules" — GET returns normalised rows without writing | Replace invalid rows with seed | Never narrows protection silently; capability names always complete |
| D-27 | Migration precedence | If `acp_permission_mode` exists it wins over `acp_permissions_enabled`; rules are seeded only when the table is absent or empty | Legacy key wins | Newest schema wins after a rollback and roll-forward |
| D-28 | Missing base agent | If the base agent file is absent, generate from a built-in `MINIMAL_BASE` (`description`, `tools: ["*"]`) and report "base agent not found — using a minimal agent" | Fail generation | Keeps the floor on clean installs, where `kiro_default.md` exists only if agent-playbook deployed it |
| D-29 | Foreign file at the target | `_generate` refuses to overwrite a file classified `unknown` (not PowerAtlas-marked) and reports it | Overwrite | Keeps the prior "not ours: never touch" contract |
| D-30 | Stale self-heal | When the gate hook reads `stale` and the config loads cleanly, it regenerates once (inside its 2 s budget) and re-checks | Wait for restart | Heals hand edits and lost updates without a restart |
| D-31 | Bound mode per session | The session record stores the mode PowerAtlas bound (`session/new`) or sent (`session/load`); the frame gains `ruleEligible` computed server-side: `consent.source == "agent-profile"`, `matchedRule.effect == "ask"`, no `match` on the matched rule, bound mode `poweratlas-acp` | Eligibility by capability | The button must only appear where a row rule can silence the prompt (Protected, built-in and vendor-mode prompts cannot) |
| D-32 | `apply_settings` result | Returns `{saved, generation_ok, generation_error}`; a save failure → route `ok: false`; a generation failure → `ok: true` plus a warning shown by the panel and the card | Raise | Distinguishes "nothing changed" from "saved, not yet in effect" |
| D-33 | Base-agent save | `/api/save-setting` routes `acp_permission_base_agent` through `apply_settings` | Save then sync | D-16 covers every permission-relevant writer |

### Decisions from the plan review (user, 2026-09-24)

Escalated by the 2026-09-24 plan review; PD-1 and PD-2 went through a Full council first (§ Review Log).

| # | Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|---|
| D-34 | PD-1: Default when not in effect | **Fail closed.** `_handle_new` refuses a Default create while not in effect. The refusal names the specific cause (from the state and `generation_error`), the concrete fix (a Settings step, or a file to remove), says the fix needs this computer when the client is remote, and offers task modes as an explicit alternative that runs without the floor. Logged at WARNING. `session/load` keeps today's behaviour | Fall back to `kiro_default`; bind the stale file | User chose A; council 4-0. The old fallback was justified as "never wider than off", which no longer holds; a floorless fallback session could also make the file foreign and lock the fallback in |
| D-35 | PD-2: same-user self-widening | **Accepted, disclosed** (`User: accepted — 2026-09-24`). R-16 states that allowing an interpreter or a broad shell prefix in Manual equals allow-all, including widening future sessions via the API or `config.toml`. The editor and the prompt-card button show "allowing an interpreter is equivalent to allow-all" when a pattern's first token is an interpreter or shell (D-20 list). A posture change not made from the dashboard (detected when the D-30 self-heal regenerates from a changed mode or rules) raises a visible dashboard notice naming the new mode | Tray-granted elevation for widening changes | User chose A; council 4-0: `config.toml` sits beside the secret, so an HTTP-layer gate would guard one of two equal doors |
| D-36 | PD-3: floor additions | **None.** The floor stays as agreed in exploration (D-6). Within those items the patterns cover the files they name: `data.sqlite3*` (SQLite sidecars of the kiro token store) and `local-secret*` / `remote-secret*` (the rotation temp file) | Add the power-atlas folder, the base agent, extra shell tripwires | User chose to add nothing. Consequence recorded in R-16 and R-18: `config.toml` and the base agent stay writable by agents |
| D-37 | PD-4 and PD-5 | Both accepted (`User: accepted — 2026-09-24`): R-4 with its full scope, and R-11 rollback | Legacy key for one release; reopen D-8 | User accepted both |

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
- [ ] P-0.1 to P-0.12 recorded in § 9 with command, date, kiro-cli version and result
- [ ] Every gate branch that fired is applied to the affected D-row and phase text before Phase 1 starts
- [ ] `ls ~/.kiro/agents` shows no `pa-probe-*`; the probes' session folders are deleted

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
  `**/Kiro-Cli/data.sqlite3*`, `**/power-atlas/local-secret*`, `**/power-atlas/remote-secret*`;
- `shell` deny (best-effort): `*.ssh*`, `*.aws*`, `*.azure*`, `*.config/gcloud*`, `*secrets.json*`,
  `*data.sqlite3*`, `*local-secret*`, `*remote-secret*` (backslash variants only if P-0.3 shows `\` is literal);
- `fs_write` deny: `**/.kiro/agents/poweratlas-acp.md`, `**/.kiro/settings/**`, `**/.kiro/workspace-roots/**`.

`SEED_RULES` (the old overlay, per D-24): `fs_read` ask + allow `./**`; `fs_write` ask; `shell` ask + allow
`git status*`, `git log*`, `git diff*`, `git branch`, `pwd`, `whoami`, `uname` + block `git *--output*`,
`git *--no-index*`, `git *--ext-diff*`; `web_fetch`, `web_search`, `mcp`, `subagent`, `skill`, `power` ask.
Protected: `{"agents": "**/.kiro/agents/**", "steering": "**/.kiro/steering/**", "skills": "**/.kiro/skills/**",
"hooks": "**/.kiro/hooks/**"}` → `fs_write` `ask`, or `deny` when in `protected_block`. Emission per D-14. Move
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
kiro-cli's built-in asks per P-0.10). Scope note: "Changes apply to sessions created afterwards. Terminal sessions
and task modes such as Spec or Plan are not covered." An "Always blocked" disclosure listing the floor (shell tier
marked "catches common accidents, not a guarantee"). Keep `#acpPermBadge`, `#acpPermWarn`, the gear dot, the
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
  `excise_permissions`. Retire, with a one-line reason each: "no trailing `*`" (replaced by D-24 + P-0.8),
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
- [ ] `pytest tests/test_web.py tests/test_config.py --timeout=300` and `node tests/acp_page.test.mjs` pass;
      `_check_test_names.py` clean
- [ ] `git grep -n "acp_permissions_enabled" -- src/` returns only the migration code and its comment
- [ ] `git grep -n -e overlay_text -e _overlay_cache -e build_derived_agent -e "agents/permissions.yaml" -e "_remove(" -- src/ tests/ pyproject.toml` returns no hits
- [ ] Live, via a separate probe process bound to `compile_block` output (Yolo) copied into `pa-probe-yolo.md`:
      `echo ok` runs with no prompt; the read-file tool and `Get-Content` on the canary `.ssh/dummy` are refused
- [ ] Live, same for Manual seed: `git status` silent, `echo x` prompts, a write under `.kiro/agents/` refused
- [ ] Update README.md: config sample (`acp_permission_mode`), the feature bullet (~149, "off by default"), the
      task-mode paragraph (~380, "while the permission setting below is on"), and the permission section's modes,
      Always blocked, scope and built-in-ask disclosure (~385-416)
- [ ] AGENTS.md Terminology: rewrite **derived agent** (always written; "the overlay" becomes "the permission mode
      and rules"; fix its stale `plans/260921_…` path) and add **permission mode**, **Always blocked**,
      **Protected** — applied only after the user approves the shown diff

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
Protected sections (per-item "Block outright" toggles). Footer: "Applies to sessions created afterwards." Save
posts JSON, shows the server error inline, closes on success, shows the D-32 warning when generation failed.
Chips for MCP/Sub-agents/Skills/Web fetch hidden where Phase 0 gated them.
> **Rejected:** an HTMX-fetched partial per save — the modal needs client-side chip editing either way.
> **Use instead:** static include + fetch JSON, as the launch-profile modal does.
> **Rejected:** a remote read-only editor — `/` is loopback-only by construction, so the branch is unreachable.

**Exit criteria**:
- [ ] Route tests: valid rules round-trip; each invalid shape (unknown row, bad default, control character, DEL,
      surrogate, 201-char pattern, 101 patterns, bare `*`) refused with a row-named error; remote POST refused
- [ ] Page tests: editor renders normalised rows, hides the allow list under default Allow, shows the breadth
      warning, posts the edited JSON, shows a generation warning
- [ ] Live, via a probe agent from `compile_block` with a user-edited rule set (one custom allow, one custom
      block, one `Block outright` Protected item): each behaves as configured
- [ ] Update README.md permission section: Manual rows, Protected items, the editor

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
- [ ] Route tests: append, dedupe, invalid pattern refused, remote refused, default-Allow row refused
- [ ] `_project_consent` and `ruleEligible` tests: `triggeringResource` forwarded; a non-string dropped;
      ineligible for a Protected rule (`match` present), a non-`agent-profile` source, a non-derived bound mode
- [ ] Page tests on both pages: button present only with `ACP_LOCAL === true`, `ruleEligible` and an
      `allow_once` option; prefill per D-20 (`git status --short` → `git status*`, `python -m pytest` → the exact
      command, a bare filename → the exact resource, an MCP tool → its resource); Save posts then answers by
      `kind`; an error leaves the prompt open; a resolved-meanwhile prompt is not answered
- [ ] Live on the running instance after the user restarts it (AGENTS.md § Verification Setup): in Manual, a
      prompted `echo pa-button` shows the button prefilled `echo*`; edit it to `echo pa-button*`, save; the prompt
      is answered; a new session runs `echo pa-button` without a prompt
- [ ] Update README.md: the button (new sessions only; when it does not appear) and the "Triggered by" field

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
- [ ] Every row of § 8 done or marked with its reason
- [ ] `git grep -n -e "Permission profile" -e "acp_permissions_enabled" -e "turn it off and on" -- README.md docs/ AGENTS.md src/power_atlas/templates src/power_atlas/acp.py`
      returns only intentional historical mentions

## 6) Risk Assessment

| # | Risk | Impact | Mitigation |
|---|---|---|---|
| R-1 | Edited rules read as stale | Default creation refused until healed | D-15, D-30, D-34 |
| R-2 | Shell floor bypassed by rephrasing | Credential read through shell | Accepted (D-6): labelled best-effort; path rules carry the weight |
| R-3 | Path floor misses search tools, 8.3 names, `\\?\`/UNC forms | Credential read | Phase 0 P-0.1, P-0.11 gates; copy drops guarantee wording if bypassed |
| R-4 | Always-present allow-all `poweratlas-acp` in kiro-cli's terminal picker | Widens posture for a default-configuration kiro user who picks it | User: accepted — 2026-09-24 (D-37) |
| R-5 | A compiled block kiro-cli rejects fails open silently | All protection lost | D-14, D-26, P-0.7, invariants test |
| R-6 | Missing `exclude` makes an allow rule dead | More prompts than configured | D-13; invariants test with a mutation check |
| R-7 | Protected paths miss writes through a symlink's real target | Self-config edit without a prompt | P-0.4; disclosed if confirmed (Follow-up 3) |
| R-8 | Allowed git commands run repository-controlled code (textconv, `diff.external`) | Code execution from a cloned repo | Accepted in the prior plan; carried forward |
| R-9 | An unanswered prompt is cancelled after 1800 s | Lost turn when away | Out of scope (Follow-up 6) |
| R-10 | Generation fails after a save | Default creation refused until fixed | D-32 warning; D-34 refusal names the fix; D-28 removes the commonest cause |
| R-11 | Rollback to the previous release | Manual users land on allow-all | User: accepted — 2026-09-24 (D-37) |
| R-12 | Shell floor blocks legitimate commands (`ssh -i ~/.ssh/key`) | Agent cannot run those | Accepted trade-off; listed in the Always blocked view |
| R-13 | Another settings route saves a stale config copy over a just-applied mode or rule change | The change is silently reverted | D-30 keeps file and config consistent; the revert itself is visible in the editor; single-user, millisecond window — accepted, Follow-up 9 |
| R-14 | Separators or substitution let `git status*` run another command | Silent command execution | P-0.8 gate; D-24 block patterns |
| R-15 | Sub-agents run under their own agent's posture | Floor bypassed by spawning `kiro_default` | P-0.12 gate |
| R-16 | An agent allowed to run an interpreter or broad shell changes the posture (API or `config.toml`) | Future sessions widened; in Manual, allowing an interpreter equals allow-all | User: accepted — 2026-09-24 (D-35); warnings and the dashboard notice detect, they do not prevent |
| R-18 | `config.toml` and the base agent file are writable by agents (no floor entry) | Rules or base agent tampered for future sessions | User: accepted — 2026-09-24 (D-36); D-35 notice surfaces mode and rule changes |
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
| `README.md` | Config sample; feature bullet (~149); task-mode paragraph (~380); permission section modes, Always blocked, scope, built-in asks (~385-416) | 1 |
| `README.md` | Manual rows, Protected items, the editor | 2 |
| `README.md` | The prompt-card button; "Triggered by" (~417-424) | 3 |
| `AGENTS.md` | Terminology: derived agent rewrite (incl. "overlay" wording and stale path), new terms — user-approved diff | 1 |
| `docs/KNOWLEDGE.md` | Probe results; supersession notes (~86, ~269, ~277, ~287) | 4 |
| `plans/ROADMAP.md` | Auto mode cross-referenced from the existing unattended-rules item; ~125 wording | 4 |
| `plans/tests/260701_POWERATLAS.md` | ~154 default posture | 4 |

## Progress Tracker

| # | Phase/Task | Status | Notes |
|---|---|---|---|
| 0 | Pre-flight probes and gate | Pending | |
| 1 | Modes, compiler and the mode picker | Pending | |
| 2 | Custom Manual rules and the rule editor | Pending | |
| 3 | "Allow, and always in new sessions" | Pending | |
| 4 | Documentation and final live check | Pending | |

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

<Reserved -- filled during implementation>

## Follow-up Work (Deferred)

1. **Auto mode decider.** Deny, steer with the reason, escalate on repeat (D-5); needs a probe of when a queued
   steer reaches the agent. Source: D-2, D-5.
2. **Home/root deletion protection.** Left to Auto (D-6).
3. **Symlinked self-config.** If P-0.4 confirms writes through a symlink target skip Protected rules. Source: R-7.
4. **Shell floor bypass by rephrasing.** Accepted best-effort. Source: R-2, R-12.
5. **Terminal-picker exposure of the allow-all derived agent.** User-accepted. Source: R-4, D-37.
6. **Unanswered prompt cancelled at 1800 s.** Source: R-9.
7. **git textconv / `diff.external` via allowed git commands.** Source: R-8.
8. **Unnamed capabilities** (`context`, `diagnostics`, `sandbox_network`) inherit user scope; probe and name them.
   Source: Sec review #20.
9. **Config lost update between settings routes.** A single config-write primitive would remove it. Source: R-13.
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
- The exploration's lower-stakes assumptions were shown at the checkpoint but not written as an
  `Assumptions (unconfirmed)` adjunct, so `/qplan` had no labelled list to route — cost: the planner folded them
  in as its own D-rows without a user confirmation step — suggested change: `/qexplore` Step 3 should always write
  the adjunct when the checkpoint listed defaults, even at medium depth.
