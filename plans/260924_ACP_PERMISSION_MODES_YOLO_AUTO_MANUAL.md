# ACP Permission Modes: Yolo, Auto and Manual

> **Date**: 2026-09-24
> **Status**: Exploring  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Scope**: Replace the on/off ACP permission profile with three permission modes (Yolo, Auto, Manual), an always-on hard-deny floor, and a plain-language Manual-mode rule editor that compiles to the derived agent

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

1. The settings menu offers Yolo, Auto (disabled, labelled "coming soon — behaves like Manual") and Manual in
   place of the on/off switch; only `yolo` and `manual` can be stored.
2. In Yolo, a new Default ACP session runs shell, write, web, MCP, sub-agent and skill actions with zero
   permission prompts, and every Always blocked item is refused silently with kiro-cli's denial text.
3. The Always blocked floor, identical in every mode, contains:
   - `fs_read` deny on credential stores: `~/.ssh/**`, `~/.aws/**`, `~/.azure/**`, `~/.config/gcloud/**`,
     kiro-cli's token files under `~/.kiro/`, and PowerAtlas's `local-secret` and `remote-secret` files;
   - `shell` deny patterns that mention those credential stores (for example `*.ssh*`, `*.aws*`), labelled as
     catching accidental reads, not deliberate rephrasing;
   - `fs_write` deny on PowerAtlas's own derived agent (`~/.kiro/agents/poweratlas-acp.md`);
   - kiro-cli's own built-in denies (`~/.kiro/settings/`, `~/.kiro/workspace-roots/`), listed so the UI shows
     the complete floor.
4. In Manual, a new Default session follows the user's rules: allowed patterns run silently, everything else in
   an Ask row raises a permission prompt, Block rows are refused, and Protected paths prompt even under an Allow
   row.
5. The Manual rule editor shows one row per kind of action (Read files, Write files, Run commands, Web fetch,
   Web search, MCP tools, Sub-agents, Skills) with an Allow / Ask / Block default and an exception list; the
   compiled `permissions:` block always carries the `exclude` entries kiro-cli needs, so no allow rule is
   silently defeated by a blanket ask.
6. Each permission prompt card on a loopback page offers "Always allow in new sessions", which adds the
   command prefix, path pattern or MCP tool to the matching Manual row.
7. The mode picker, the rule editor and the "Always allow in new sessions" button are loopback-only; a remote
   browser sees them read-only with a "Change rules from this computer" note, and a remote POST is refused.
8. Existing configs migrate: `acp_permissions_enabled = true` becomes Manual seeded from the current overlay
   (shell rules widened to prefix patterns such as `git status*`); `false` and fresh installs become Yolo; the
   old key is dropped on the next save.
9. "In effect" is true only when the derived agent on disk matches what the current settings compile to; an
   edited rule set is never classified as stale, and a genuinely stale or foreign file is still reported.
10. The UI states that a mode or rule change applies to sessions created afterwards.

### Scope boundaries & non-goals

In scope:
- Default ACP sessions that bind the derived agent, on /acp and the dashboard.
- Mode storage and migration in `config.toml`, rule compilation in `agent_profile.py`, the in-effect check,
  the settings UI, the prompt-card button, routes, tests and documentation.

Out of scope:
- **Auto mode's decider.** Deferred. Its intended design is recorded in Discovery, Resolved decisions Q5-note.
- Vendor task modes (Spec, Plan and so on), terminal sessions launched from PowerAtlas, and Kiro Crew. The floor
  does not reach them, and the settings copy says so (Q3).
- Editing the base agent or `~/.kiro/settings/permissions.yaml` (Q1, Q3).
- Hard-denying home or root deletion commands. Left to Auto mode (Q6).
- Per-workspace rules. Rules are global.
- Remote editing of rules or mode (Q9).

---

## Exploration Discovery

<!-- Transient: /qplan folds these into the planning sections and removes this section. -->

### Existing patterns & constraints

Step 1.5 dispatched the code-tracing trio (in-scope files are predominantly `.py`, `.html`, `.js`), plus a
fourth, external research agent covering Kiro Crew, kiro-cli v3 permissions, and peer permission editors
(Claude Code, Codex, Cursor, VS Code).

- **Derived-agent generation assumes one constant overlay.** `overlay_text()` caches package data
  (`agent_profile.py:143,253-265`); `build_derived_agent` has no variant (`487-494`); `derived_block_state()`
  compares against exactly one block and classifies any other PowerAtlas-marked block as `stale`
  (`557-598`); `_apply_locked(enabled=bool)` has two branches (`660-690`). `stale` is never in effect
  (`web.py:4094`), so an edited rule set would silently bind `kiro_default` (`acp.py:6494`).
- **The splice is textual, verified before publish, and fails closed on odd base shapes** (D-18, D-19;
  `agent_profile.py:26-60,294-384,528-555`). The provenance marker "Written by PowerAtlas" is the only basis
  for deleting the file (`145-157`). `acp.py` must not import `agent_profile` (D-20); `DERIVED_AGENT_NAME` is in
  `config.py:119`.
- **The boolean is threaded through several consumers:** `config.py:109`; `web._acp_permission_state`
  (`web.py:4071-4098`); the bool `mode_gate_hook` and two-way `_default_mode_binding` (`acp.py:1126-1205`);
  `POST /api/acp-permissions` requires a literal bool (`web.py:4119-4138`); the page JS rejects any payload whose
  `enabled` is not a boolean (`index.html:7223,7273`).
- **Config supports structured lists with load-time normalisation** — `launch_profiles` (`config.py:60,625-650`).
  `load_config` checks types only (`config.py:604-616`); value sanitisation follows the `remote_bind_address`
  precedent (`config.py:703-719`). Unknown keys survive in `_extra` unless listed in `_LEGACY_KEYS`
  (`config.py:43,617-622,745`).
- **Modal editors use HTMX partials** — launch profiles (`partials/launch_profile_modal.html`,
  `web.py:4433-4522`) and launchers (`web.py:5296-5334`). No rule or pattern-list editor exists yet.
- **The prompt card** is `addPermissionRequest` in `static/transcript-renderer.js:1717-1771`, shared by /acp
  (`acp.html:5031-5070`) and the dashboard (`index.html:3552-3568`). Buttons come from the offered options;
  ACP offers only `allow_once`, `reject_once`, `reject_always` (never `allow_always`).
- **The frame drops fields a rule button needs.** `_on_permission_request` keeps only `toolCall.title`
  (`acp.py:5134,5156`) and `_project_consent` keeps five fields (`acp.py:1474-1528`). The measured request also
  carries `toolCall.toolCallId`, `consent.askType`, `consent.triggeringResource` and `matchedRule.exclude`.
- **Loopback-only posture routes** are pinned by `tests/test_web.py:12783-12801` and `25427-25430`.
- **Tests that pin the boolean:** `TestAcpPermissionRoutes` (`tests/test_web.py:24025`),
  `TestRuleAssemblyInvariants` (`23874`), `TestDerivedAgentRemovalOnOff` (`23697`), `TestOverlayIsReachableAtRuntime`
  (`23987`), the mode-gate tests (`4819-4846`, `7051-7291`); page tests at `tests/acp_page.test.mjs:11109-11541`
  pin the on/off copy and the switch semantics.
- **Governance:** base agent never modified; derived agent never hand-edited (AGENTS.md Terminology). Never
  restart PowerAtlas autonomously. Page JS changes need `node tests/acp_page.test.mjs`. Live protocol probes
  go through a separate `kiro-cli acp`, never the running instance.
- **Prior art:** `plans/done/260924-0525_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL.md` § 9 Phase 0 Steps
  5-6 and Gate resolution (lines ~1056-1290). A shell-pattern deny floor was measured bypassable on 2026-09-22
  (case, alias, `../` segment; POSIX idioms inert under PowerShell) and was dropped. This plan re-adds a floor
  on a different basis: mostly path rules, with the shell tier explicitly labelled best-effort. Follow-up #9
  there (widen exact shell literals) is now answerable (see probes below). ROADMAP `plans/ROADMAP.md:185-188`
  ("Decide permission requests by rule for unattended sessions") is the natural home of Auto mode.
- **Peer precedent:** Kiro Crew's Autopilot keeps deny rules ("never bypassed — even in Autopilot") over a
  137-pattern regex list and still collects spelling bypasses (KiroCrew #8387, #8240). Claude Code's
  bypassPermissions keeps deny rules and a critical-path circuit breaker. Every vendor calls pattern lists "not
  a security boundary".

**Live probes run 2026-09-24** (separate `kiro-cli acp --agent-engine v3`, kiro-cli 2.24.0,
`tools/acp_permission_probe.py` driven from a scratch script; probe agents and their three session folders
deleted afterwards):

| # | Measurement | Result |
|---|---|---|
| P-A1 | Agent block `all: allow` + `shell` deny `echo pa-floor-hit` + `fs_write` deny `**/denied-dir/**` | Allowed command ran, zero prompts. Denied command refused, zero prompts, "Tool call denied by user's permissions. Rule: deny shell matching …" |
| P-A2 | `echo pa-ok2 && echo pa-floor-hit` under the same agent | Refused: compound commands are split and the denied part is caught |
| P-A3 | `fs_write` to `denied-dir/a.txt`, `DENIED-DIR/c.txt`, and the absolute path | All three refused; only the allowed file was written |
| P-B | `shell` allow `git status*` + ask with `exclude`, command `git status && echo pa-chain` | Prompted; consent carried `triggeringResource: "echo pa-chain"`. `*` does not carry an allow across `&&`. **Falsifies** the overlay's "never established" caveat (`permissions.yaml:67-74`) |
| P-C | Rewrite the bound agent file mid-session to add a deny, then run the denied command | Ran. A live session does not pick up a changed agent file; changes apply to new sessions |

Measurement assumptions: the path-case result is on Windows (case-insensitive filesystem) and may reflect
kiro-cli's own path normalisation rather than glob case-folding; untested on another OS. The shell-case
bypass from 2026-09-22 was not re-measured and is assumed to still hold.

### Risks & mitigations

- **R1 — edited rules read as stale and silently fall back to `kiro_default`.** Mitigation: the in-effect check
  compares against the block compiled from current settings (SC-9).
- **R2 — the shell floor is bypassable by rephrasing.** Mitigation: path rules carry the weight; the shell
  tier is labelled "catches accidents" in the UI; home and root deletion are left to Auto (Q6).
- **R3 — the credential floor covers kiro's file tools, not shell reads.** Mitigation: the `*.ssh*` / `*.aws*`
  shell patterns (checkpoint decision C1), disclosed as best-effort. They may block benign commands that
  mention those paths.
- **R4 — Yolo writes an `all: allow` derived agent that appears in kiro-cli's terminal agent picker.** On this
  machine that widens nothing (user scope is `all: allow`); for a user with a stricter baseline, picking it in
  a terminal would widen posture. Accepted (Q8); the file carries the floor, so it is narrower than today's
  "off".
- **R5 — a malformed compiled block fails open to user scope silently** (`agent_profile.py:50-55`; prior plan
  § 9 Step 7). Mitigation: user patterns accepted from a restricted character set, only `*` as wildcard,
  always emitted quoted; the existing staged-file verification stays.
- **R6 — a blanket ask silently defeats a narrower allow unless `exclude` is generated**
  (`permissions.yaml:21-26`). Mitigation: the compiler always generates it; `TestRuleAssemblyInvariants`
  extends to compiled output.
- **R7 — Protected paths are matched on the path the agent reports.** Symlinked `~/.kiro/steering` or
  `~/.kiro/skills` written through their real target path would not match. Untested; record as a known gap
  unless a probe settles it.
- **R8 — git read commands can run repository-controlled code** (textconv, `diff.external`;
  `permissions.yaml:76-84`). Widening `git status` to `git status*` does not change that class; accepted in the
  prior plan and carried forward.
- **R9 — an unanswered prompt is cancelled by the 1800 s silence watchdog**, because `_stamp_activity` is called
  only from `_on_notification` (`acp.py:3766,4342`). Unchanged by this plan; relevant to Auto.

### Resolved decisions

- Q1: What should PowerAtlas edit when the user changes Manual rules? — A: agreed, we touch the derived agent — Decision: rules are stored in PowerAtlas settings and compiled into the derived agent's `permissions:` block using kiro-cli's rule model; the base agent and `~/.kiro/settings/permissions.yaml` are not edited.
- Q2: What should choosing Auto do before the LLM approver exists? — A: ok — Decision: Auto appears in the picker, disabled, labelled "coming soon — behaves like Manual"; only `yolo` and `manual` are storable.
- Q3: Which sessions must the deny floor cover? — A: agreed — Decision: only Default ACP sessions bound to the derived agent; vendor task modes and terminal sessions are out of reach and the copy says so.
- Q4: What goes in the floor? — A: agreed with two tiers, then refined by Q5 — Decision: pinned path rules plus a command-pattern tier labelled "catches common accidents, not a guarantee". Superseded by: Q5, Q6.
- Q5: What happens when an action hits the floor? — A: ok, except Yolo stays Yolo with no ask; only the (very limited) hard-deny floor applies — Decision: Always blocked items are silent kiro `deny` rules in every mode. Yolo never prompts. In Manual (and later Auto), Protected items (writes to agents, steering, skills) are `ask` rules that prompt even under an Allow row, each switchable to Block. Q5-note: Auto's future decider answers prompts with Deny, sends the reason through a steer message (`_handle_steer`, `acp.py:6767`), and escalates to the user on a repeat — reachable only for `ask` prompts, since PowerAtlas never sees a kiro `deny`.
- Q6: What exactly is in the hard-deny list? — A: ok for the three suggestions; home and root deletion patterns will be caught by the future Auto mode — Decision: credential-store reads, writes to PowerAtlas's own derived agent, and kiro-cli's built-in denies. No deletion patterns.
- Q7: How should the Manual rule editor look, and is "Always allow in new sessions" in scope? — A: ok for me; button: yes — Decision: one plain-language row per kind of action with an Allow/Ask/Block default and an exception list, compiled with generated `exclude` entries; prefix patterns with a trailing `*`; seeded from the current overlay; an "Always allow in new sessions" button on each loopback prompt card.
- Q8: Migration and the default for fresh installs? — A: ok — Decision: `true` becomes Manual, `false` and fresh installs become Yolo; the old key goes into `_LEGACY_KEYS`; there is no Off state, so the derived agent is always written.
- Q9: Can a remote browser change the rules? — A: agreed — Decision: mode picker, rule editor and "Always allow in new sessions" are loopback-only; remote browsers see them read-only.
- C1 (assumptions checkpoint): Should the floor add shell patterns covering credential stores, given the file-tool deny does not cover shell reads? — A: add the suggested shell deny patterns — Decision: `shell` deny patterns such as `*.ssh*` and `*.aws*` join the Always blocked floor, labelled best-effort.
- C2 (assumptions checkpoint): Terminology — A: ok with terms suggestions — Decision: the mode labels are "Yolo", "Auto", "Manual"; the floor is "Always blocked"; items that always prompt are "Protected"; "permission mode" replaces "permission profile" in the UI and in AGENTS.md's Terminology (for `/qplan` to propose — this skill does not edit governance). Rejected synonyms: "profile" (the old on/off feature), "deny floor" as a UI label (kept as an internal term only).

### Open items

- **Deterministic:** the exact kiro-cli token file names under `~/.kiro/` for the credential floor — read the
  directory. The exact list of shell credential patterns — author and check against false positives such as
  `ssh-keygen` or `git` over SSH.
- **Deterministic:** which consent fields the "Always allow in new sessions" button needs (`resource`,
  `triggeringResource`, `capability`, MCP tool id) and how a shell command is reduced to a prefix (first word
  or first two words) — read the measured payloads.
- **Execution-contingent:** whether Protected path rules catch writes through a symlink's real target path
  (R7) — probe with a symlinked directory.
- **Execution-contingent:** whether `fs_read` deny on credential paths also covers kiro's grep/glob/search
  tools, which are `fs_read`-class — probe once.
- **Deterministic:** how the dashboard and /acp settings surfaces (the settings menu lives in `index.html`) and
  the new modal share code; whether /acp needs the picker at all.

### Recommended approach

1. **Config:** add `acp_permission_mode: "yolo" | "manual"` (sanitised on load, invalid → `yolo`) and a
   structured `acp_permission_rules` table (per-row default plus allow and block pattern lists, per-Protected-item
   block flag), normalised on load like `launch_profiles`. Migrate the boolean; list it in `_LEGACY_KEYS`.
2. **Compiler (`agent_profile.py`):** replace the single cached overlay with a pure function
   `compile_block(mode, rules) -> str`. Floor rules come first and are identical in both modes. Yolo adds
   `all: allow`. Manual names every capability explicitly and generates the `exclude` pairs. Keep the provenance
   marker, the textual splice and staged verification. `derived_block_state()` compares against
   `compile_block(current settings)`; `stale` keeps meaning "PowerAtlas-marked, not current".
3. **Gate:** `in_effect` becomes "file matches compiled block"; the boolean `enabled` disappears from the state
   payload in favour of `mode`. `_default_mode_binding` still returns the derived agent when in effect.
4. **Routes:** `GET/POST /api/acp-permissions` carries `mode` and `rules`, loopback-only, validated
   server-side; the same route (or a sibling) accepts the "Always allow in new sessions" addition.
5. **Frame:** forward `toolCallId`, `triggeringResource` and the MCP tool id through the allowlist so the
   button can build a rule.
6. **UI:** a three-option mode control in the settings menu (Auto disabled), an "Edit rules…" HTMX modal with
   the row editor and the read-only Always blocked and Protected lists, and the prompt-card button, hidden on
   remote pages.
7. **Docs:** README permission section and config sample, `docs/KNOWLEDGE.md` (probe results P-A to P-C),
   AGENTS.md Terminology proposal (via `/qplan`), ROADMAP entry for Auto.

### QA environment

- **Unit tests:** `.venv-PowerAtlas/Scripts/python -m pytest tests/test_web.py --timeout=300`;
  `node tests/acp_page.test.mjs`; pre-commit `_check_test_names.py`.
- **Protocol probes:** `tools/acp_permission_probe.py` against a separate `kiro-cli acp --agent-engine v3` with
  disposable `pa-probe-*` agents under `~/.kiro/agents/`; delete the agents and their `~/.kiro/sessions/<hash>`
  folders afterwards (check `createdAt` and `agentMode` first).
- **Live UI:** the AGENTS.md § Verification Setup recipe (Playwright from the venv, `pa_local` cookie from the
  on-disk secret, instance on `127.0.0.1:4915`). Python changes need a PowerAtlas restart, which the user
  performs; template and static changes need only a hard reload.
- **Remote refusal:** a request with a non-loopback peer, as the existing loopback-only tests do.

## Harness Improvement Opportunities

- `/qexplore`'s probe gate says to run side-effecting probes only with consent, while `shared.md` says one
  question at a time — cost: the first interview message had to carry both a probe-consent request and Q1,
  which blurs the one-question rule — suggested change: state that a probe-consent request may accompany the
  first question, or give it its own pre-interview turn.
- The harness flagged a research sub-agent's report as instruction-shaped because it quoted vendor
  documentation about `bypassPermissions` — cost: none beyond a warning banner, but a reader could mistake a
  quoted doc for an injected instruction — suggested change: none required unless it recurs.
