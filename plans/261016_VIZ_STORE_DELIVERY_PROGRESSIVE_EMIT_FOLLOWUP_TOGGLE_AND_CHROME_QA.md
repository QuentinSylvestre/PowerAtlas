# Visualization store delivery, progressive emit, follow-up toggle and unattended Chrome QA

> **Date**: 2026-10-16
> **Status**: Exploring  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Scope**: qvisualize writes into the workspace store and emits a skeleton then the final card; PowerAtlas serves store files, opens cards by default, gets a follow-up toggle and an agent sign-in route for Chrome QA.

---

## Intent

### Problem statement & desired outcomes

The last QA of the visualization card left four open items, and one scope addition followed.

1. **No visible progress.** The agent scaffolds every time but usually delivers one card at the end. The person sees nothing for minutes. A new card also starts collapsed, so an early card is hidden.
2. **Truncated first turns.** At Max effort, an open-ended request can spend the whole output limit before its first tool call. Recovery (note plus one follow-up) works. The follow-up costs about 1 min, the dead turn about 5 min.
3. **No UI control for the follow-up.** `acp_auto_continue_truncated` is a `config.toml` key only, read once at startup.
4. **Chrome QA was not unattended.** The Chrome tools could reach PowerAtlas but not sign in, and the docs said the route was never exercised.
5. **Visualization files clutter the working folder** and are tied to it. They should live in the workspace store, so they belong to the workspace, work the same from the CLI and from PowerAtlas, and get cleaned up.

Desired outcome: the person sees a card within seconds of a visualization request, the card updates when the final file is ready, files land in the workspace store and expire after 30 days, the follow-up can be turned off from the dashboard, and an agent can run signed-in Chrome QA against PowerAtlas without the user.

### Success criteria

1. A new visualization card starts open. A later emit of the same file keeps the user's open or closed choice.
2. The qvisualize skill text, the `emit_visualization` tool description, its result text and the base agent line all tell the agent to emit right after the scaffold and again at the end. The silence rule covers chat text only, not tool calls. A live run shows a skeleton card before the final card.
3. The dashboard gear menu has a toggle for the truncation follow-up. It saves the setting and changes the running behaviour at once, without a restart.
4. `render.py` resolves the session folder's store through the shared resolver and writes to `<store>/visualizations/`. In a temp or scratch folder, or when the resolver returns none, it writes into the working folder as today.
5. `emit_visualization` and `/api/viz/` accept a file inside `visualizations/` of the session workspace's own store, and still accept a direct child of the session cwd. Any other path is refused (no `..`, no link escape).
6. A qvisualize prune script deletes files under every store's `visualizations/` that were not modified for 30 days. `/qconfig-sync` runs it as one step and reports the count.
7. A loopback-only route issues a login code to a caller that presents a valid `pa_local` cookie. An agent can use it to sign the connected Chrome back in through `/local-auth?code=…`. The route passes `/qreview`.
8. `AGENTS.md` Verification Setup states the working Chrome recipe (navigate to `/`, then open `/acp` from the page), the sign-in route, and corrects the "mint a login code in-process" line.
9. `plans/ROADMAP.md` carries three entries: phone access to cards, cards that survive a reload, and a visualization view with pin or star (pinned items skip the prune).

### Scope boundaries & non-goals

- **In scope:** items 1, 3, 4 and 5 above, across PowerAtlas and the agent-playbook (qvisualize skill, `render.py`, prune script, `qconfig-sync` step, base agent line).
- **Item 2 needs no code** (Q1). The note and the one follow-up stay.
- **Not in scope:** the store resolver and the store creation rule. The agent-playbook workspace-store centralization project owns them (agent-playbook `plans/ROADMAP.md`, the "centralize every workspace store" entry). This plan consumes them.
- **Not in scope, roadmap only:** phone access to cards, replay after reload, a visualization view with pin or star.
- **Not in scope:** a settings menu on `/acp`. `/acp` is a remote phone view.
- **Not in scope:** sharing, comments, versions or deploy of visualizations.
- **Sequencing:** this plan runs after the agent-playbook qdream plan and after the workspace-store centralization project. `/qplan` runs only after both close.

## Exploration Discovery

<!-- Transient: /qplan folds these into the planning sections and removes this section. -->

### Existing patterns & constraints

All anchors were read on 2026-10-16. Two projects land before this one, so `/qplan` re-reads every anchor before it plans phases.

- **Truncation follow-up.** `src/power_atlas/acp.py:9479-9515` (`AUTO_CONTINUE_TRUNCATED`, `_AUTO_CONTINUE_PROMPT`, `_truncated_turn`), the emit and follow-up at `acp.py:9700-9734`, bound once at startup in `apply_config` (`acp.py:10789-10791`). Config key at `config.py:135`.
- **Toggle precedent.** The "Auto-schedule resumes" gear-menu toggle: markup `templates/index.html:48-52`, script `index.html:6775-6803`, routes `web.py:4641-4655`. `/api/save-setting` rejects booleans (`_SETTING_TYPES`, `web.py:6442-6469`), so the toggle needs its own route.
- **Viz tool.** `_viz_session_for` `web.py:7673-7709`, `_handle_emit_visualization` `web.py:7712-7768` (result text `Visualization emitted: {filename}`), tool description `_VIZ_TOOLS` `web.py:7798-7827` ("Call it after render.py has produced the file"), file route `web.py:7978-8036` (direct child of the session cwd only).
- **Card.** `addVisualizationCard` `static/transcript-renderer.js:2988-3024`. The `<details>` is created without `open`. In-place update matches on `data-viz-url`. CSS at `static/style.css:3098-3100`.
- **qvisualize skill** (agent-playbook `shared/skills/qvisualize/`). `SKILL.md` silence rule at lines 12 and 140-142 contradicts "show the skeleton at once" at line 64. The direct-child rule is at line 35. `scripts/render.py --scaffold` prints only the path and refuses to overwrite.
- **Base agent.** agent-playbook `providers/kiro/agents/kiro_default.md:45-49` carries the viz guidance. PowerAtlas never edits the derived agent body (`agent_profile.py:1917-1993`).
- **Store lookup in PowerAtlas.** `overview.py:301-410` (`_get_store_dir`, `_build_store_map`). Exact match on `WORKSPACE` files when `playbook_root` is set; otherwise `<cwd>/.kiro/steering/__store__.md`. The live config has `playbook_root` empty.
- **Store contract.** agent-playbook `shared/skills/qdream/memory-rules.md § Workspace Store` and `§ Resolution contract`. Today stores are opt-in and created by hand. No script creates one. The centralization project replaces `WORKSPACE` with a `STORE` file and moves stores to a separate private repo.
- **qconfig-sync** checks report and never delete (`qconfig-sync/SKILL.md:56,122`). The prune step is a call to a qvisualize-owned script, not logic inside qconfig-sync.
- **Sign-in.** Login codes are process-local (`web.py:2150-2230`), so only code inside the server can mint one. `pa_local` is HttpOnly, SameSite=Strict, 90 days fixed, not renewed on use (`web.py:1990, 2131-2144`), and dropped on secret rotation (`web.py:6833`). `GET /acp` refuses a `Sec-Fetch-Site` other than same-origin or none (`web.py:1724-1794`).
- **Remote access.** `/api/viz/` is not in `_REMOTE_ALLOWED_PATHS` (`web.py:2464-2523`). Viz frames are broadcast only and not replayed (`acp.py:256-259`). Both are roadmap items.
- **Governance.** Python changes need a PowerAtlas restart, which the user does. Template and static changes need only a hard reload. Template scripts are covered by `node tests/acp_page.test.mjs`; it forbids `innerHTML`. This repository is public: no session ids in commits.

### Risks & mitigations

- **Dependency on the centralization project.** It has no project file yet. If it ships no resolver, or keeps creation opt-in, SC 4 to 6 reopen. Mitigation: the user sends that session a change request (resolver in Python, auto-create, reserved git-ignored `visualizations/`, PowerAtlas as a consumer). `/qplan` checks the delivered contract first.
- **PowerAtlas and `render.py` resolve different stores.** A file lands in one store while PowerAtlas looks in another. Mitigation: both use the same resolver; PowerAtlas read-only.
- **The PowerAtlas store lookup breaks when `WORKSPACE` files go away.** This also affects the store-backed workspace plans feature. Mitigation: raised in the change request to the centralization project.
- **Serving files outside cwd is a security boundary.** Mitigation: canonicalize, require the path inside the session workspace's own `visualizations/`, refuse `..` and link escape; `/qreview` on that phase.
- **The login-code route is a credential path.** Mitigation: loopback only, valid `pa_local` cookie required, same-user path already allowed by `AGENTS.md`; `/qreview` with a security persona.
- **Auto-create makes junk stores.** A read-only count on 2026-10-16 (full scan: 797 kiro sessions, 117 Claude Code sessions, first `cwd` line per transcript) found 24 kiro and 10 Claude Code workspaces without a store; about half had one session ever (Downloads, cloud-drive folders, scratch and temp folders). Mitigation: create on first write only; never in temp or scratch folders. Accepted cost for the rest.
- **The agent may still skip the skeleton emit.** Text changes are cheap but not enforced. Mitigation: verify with a live run; the measure is a skeleton card before the final card.
- **Report only, not in scope.** `_truncated_turn` can misread a stale `max_tokens` record within about 3 s of an earlier truncated turn. `/compact` goes through the same lookup. The user decides whether to fix it.

### Resolved decisions

- Q1: Which lever cuts the dead first turn, given Max stays (A steering rule, B change nothing, C tool text only)? — A: B ("Ok for B") — Decision: no change for truncation; keep the note and one follow-up. A live probe showed delegating to a sub-agent or workflow took about 11.8 min versus 6.6 min and hid the truncation from PowerAtlas.
- Q2: Should a new visualization card start open? — A: ok — Decision: new cards start open; a re-emit keeps the user's state.
- Q3: When should the agent emit? — A: ok — Decision: skeleton plus final, with text changes in `SKILL.md`, the tool description, the tool result and `kiro_default.md`. Exact wording is shown to the user before editing.
- Q4: What should the docs say about Chrome-tools sign-in? — A: "I'd rather agents use their native harness tools. in your case chrome tools are probably better" — Decision: native harness browser tools first; Playwright from the venv when the harness has none. Extended by Q9.
- Settings home on /acp — A: "/acp is only meant to be used remotely from another device, specifically a phone, so it's ok if settings can only be changed from the main dashboard" — Decision: the follow-up toggle lives in the dashboard gear menu, with its own route that saves and rebinds the running value.
- Scratchpad delivery — A: "I want it in the agent playbook workspace store … For cleanup … auto-cleanup of artifacts older than 30 days in qconfig-sync via a script" — Decision: build it; refined by Q5, Q6, Q8.
- Q5: Should the folder be tracked by git? — A: ok — Decision: git-ignored.
- Q6: Which direction (A user plan with fixes, B PowerAtlas-owned copy, C minimum)? — A: folder name `visualizations/`; stores are in the playbook store and all workspaces are moving there; qvisualize owns writing and cleanup, called by qconfig-sync; pin or star and phone access go to the PowerAtlas roadmap — Decision: `<store>/visualizations/`, git-ignored; `render.py` writes there; PowerAtlas only serves; a qvisualize prune script called by qconfig-sync; three roadmap entries. Confirmed at the assumptions checkpoint ("otherwise ok").
- Q7: Where does the file go when the folder has no store? — A: "Why not create the store?" — Decision: superseded by Q8.
- Q8: Route the resolver and the creation rule to the centralization project, and auto-create or ask? — A: "ok with reco on changes to other session plan", then "I changed my mind, let's auto-create, if we want to use the parent store we can always migrate and update the store file in .kiro/" — Decision: the centralization project owns a Python find-or-create resolver; a store is auto-created for the session folder's workspace on the first write; never in temp or scratch folders (confirmed at the checkpoint); a parent store is chosen by migrating and updating the store pointer. This plan only consumes the resolver.
- Q9: How does an agent sign Chrome back in without the user (A login-code route, B sliding cookie, C JavaScript-written cookie)? — A: A — Decision: a loopback route issues a login code to a caller with a valid `pa_local` cookie signed from the on-disk secret; the agent then navigates Chrome to `/local-auth?code=…`.
- Unattended Chrome QA — A: "unattended Chrome QA should be possible, chrome is logged in. don't defer this one" — Decision: in scope (SC 7, 8).
- Config sync after the agent-playbook edits — A: "qconfig-sync: no, ask me to run it at the very end of the plan, don't run it yourself." — Decision: the executing agent never runs `/qconfig-sync`. It commits playbook edits by pathspec as it goes, and at the very end of the plan it asks the user to run `/qconfig-sync`.

**Default and override pairs** (`## Continuous Improvement`):
- The orchestrator proposed deferring unattended Chrome QA; the user overrode it into scope.
- The orchestrator recommended auto-create at Q8, then the ask rule after a data deep dive; the user chose auto-create.
- The orchestrator recommended the folder name `artifacts`; it collides with the store contract's plan byproducts folder, so the name became `visualizations/`.

**Terminology** for `/qplan`'s glossary step: **visualization card** (the transcript iframe card; not "viz widget"); **visualizations folder** (`<store>/visualizations/`; not "artifacts", which the store contract reserves for plan byproducts).

### Open items

- **File name collisions** (deterministic). Two sessions in one workspace writing the same output name share one folder; the second overwrites the first and an open card switches. Decide whether `render.py` adds a session or time suffix.
- **How PowerAtlas calls the resolver** (execution-contingent): import or subprocess, and caching so the request path stays fast.
- **The delivered resolver contract** (execution-contingent): read it when the centralization project closes, then fix the PowerAtlas serving rule and the prune script's store discovery.
- **Base agent wording** (deterministic): confirm the derived agent picks up the `kiro_default.md` change at the next generation.

### Recommended approach

Phases in dependency order; the first three do not depend on the centralization project.

1. Card opens by default, plus the emit text changes (skill, tool description, result text, base agent line). Live check: skeleton card before final card.
2. Follow-up toggle: route, live rebind, gear-menu row, tests.
3. Chrome QA: login-code route, `AGENTS.md` Verification Setup recipe and correction, proposal for the user's Chrome memory rule (a stale connection means start Chrome). `/qreview` with security.
4. Store delivery: `render.py` through the resolver with the working-folder fallback, PowerAtlas serving from the store with path checks, skill text update (drop the direct-child rule). `/qreview` with security.
5. Prune script, `qconfig-sync` step, roadmap entries.

### QA environment

- Live PowerAtlas at `http://127.0.0.1:4915`. Python changes need a restart that the user does.
- Chrome tools: the connected Chrome is signed in (`pa_local`). Navigate to `/`, then open `/acp` from the page (`location.href = '/acp'`); a direct tool navigation to `/acp` gets 403. Verified 2026-10-16.
- Playwright from `.venv-PowerAtlas` with a cookie signed from the on-disk secret, per `AGENTS.md`.
- Live viz check: a throwaway Default session, `render.py`, `emit_visualization`; clean up the session and files afterwards.
- Tests: `pytest`, `node tests/acp_page.test.mjs` for template scripts.

## Harness Improvement Opportunities

- `/qexplore` has no field for a prerequisite project that has no project file yet — cost: the dependency lives only in prose and a roadmap line, and anchors may drift before `/qplan` — suggested change: add an optional `> **Depends on**:` header line that `/qplan` checks first.
- A scope addition mid-interview (the store) needed context from another session that only the user knew about — cost: two rounds built on the wrong store contract before the user pointed at it — suggested change: on a scope expansion into a shared framework, ask whether another project is changing that framework before designing against it.
