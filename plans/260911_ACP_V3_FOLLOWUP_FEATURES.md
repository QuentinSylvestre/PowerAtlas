# ACP v3 Follow-up Features

> **Date**: 2026-09-11
> **Status**: Exploring
> **Scope**: Mode-switcher UI, v3 self-orphan-lock suppression fix, and a research spike on the 3 unreachable steering-type slash commands.

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
- **Out of scope**: Linux cross-platform support (dropped from ROADMAP.md this session), `_pending_permission` expiry/timeout (dropped from ROADMAP.md this session, resolved as a config change).
- **Out of scope**: the v2-to-v3 engine cutover itself — tracked separately in `plans/260911_ACP_V2_TO_V3_ENGINE_CUTOVER.md`.
- **Non-goal**: the steering-palette item is scoped as a research spike with a documented outcome, not a guaranteed working feature — see Success criteria.

---

<!-- Transient: /qplan folds these into the planning sections and removes this section. -->
## Exploration Discovery

### Existing patterns & constraints

- Mode-switcher: `plans/ROADMAP.md`'s prior framing — "would unlock the modes SC-9's permission-handling defends against but doesn't itself build a path to" (from `260908_ACP_V3_PRODUCTION_HARDENING` Scope boundaries). No existing UI affordance for this was found anywhere in `templates/` during this session's research.
- Orphan-lock: the fix is already fully specified by `plans/ROADMAP.md`'s D32 entry (now folded into this plan's Intent above) — publish `_supervisor_v3`'s real agent pid instead of the sentinel. A corollary residual noted there: `_acp_live` is a single last-writer-wins global, so the v2 guard is also transiently defeated in the window after any v3 mutation (self-heals within one sweep interval) — this plan does not need to fix that corollary, only the primary v3 self-suppression gap.
- Steering palette: confirmed via code reading that `_meta.kiro.type in ("skill", "steering", "prompt", "custom-agent")` entries are filtered out of the palette's "commands" list (`acp.py:4194-4212`, `4951-4955`), and `_parse_skills` (`acp.py:1187`) only picks up `type == "skill"` — so the 3 steering-type entries are in neither bucket. `_handle_commands_execute_v3`'s `valid_names` check (`acp.py:8247-8253`) would refuse them even if typed manually — though a live check found the UI never actually reaches that refusal path today, since an unmatched palette entry is just sent as a plain chat prompt instead of calling `commands_execute`.
- **Live probe finding (2026-09-11, via an isolated disposable `kiro-cli acp --agent-engine v3` subprocess, no PowerAtlas process touched)**: the 3 entries' actual wire shape carries no inline content and no `resource_link` — only a lookup key:
  ```json
  {"name": "architecture-selection", "_meta": {"kiro": {"type": "steering",
    "contextQuery": "global:architecture-selection", "commandId": "architecture-selection",
    "resource": {"resourceType": "steering", "source": {"origin": "bundled"}}}}}
  ```
  This contradicts the original ROADMAP guess ("inline the steering document's content as prompt text") — there is no content on the wire to inline. The same probe's `initialize` response listed `_kiro/knowledge` among `agentCapabilities._meta.kiro.extensionMethods` — a plausible, untested method to resolve `contextQuery` into real content.

### Risks & mitigations

- Mode-switcher UI has no existing precedent in this codebase to follow — `/qplan` should treat the UI design as a real open design-space question, not a mechanical addition.
- The orphan-lock fix's corollary residual (transient v2-guard defeat after a v3 mutation) is a known, accepted, self-healing limitation — do not scope-creep this plan into fixing it.
- The steering-palette spike may fail (i.e., `_kiro/knowledge` may not resolve `contextQuery`, or may require capabilities/permissions this session's isolated probe didn't have) — Success criteria above already accounts for a documented-negative outcome, not just a working fix.

### Resolved decisions

- Q7: What behavior should govern an unanswered `session/request_permission` request? — A: Keep relying on the existing backstop, but 30min is too short. — Decision: no new dedicated timeout logic (rejected the auto-refuse and configurable-default-option alternatives); this surfaced that the backstop is a shared, already-configurable knob (see Q9/Q10 below), not something needing a plan phase at all.
- Q8: Is Linux cross-platform support in scope for this follow-ups plan? — A: Leave it out of scope for now (Recommended). — Decision: removed from `plans/ROADMAP.md` entirely (not merely deferred) at the user's explicit request.
- Q9: Given `PROMPT_SILENCE_SECONDS` is already configurable via `acp_prompt_silence_seconds` (60-7200s clamp, `acp.py:8609-8611`) with no code change needed, is a config change sufficient, or is dedicated permission-specific code wanted? — A: Just lower the existing global config value (Recommended). — Decision: no code change; config-only.
- Q10: The user's originally-requested value (4h / 14400s) exceeds the existing clamp's 7200s (2h) ceiling — widen the clamp in code, or accept the 2h ceiling? — A: ok for 2h. — Decision: no code change anywhere; the user will set `acp_prompt_silence_seconds` to `7200` directly in PowerAtlas's config. This item is fully resolved outside both plans and was removed from `plans/ROADMAP.md`.

### Open items

- **Steering palette trigger mechanism** (execution-contingent, not deterministic): whether `_kiro/knowledge` (or another extension method) can actually resolve a steering entry's `contextQuery` into content usable as prompt text is unverified beyond this session's live probe finding that it's a plausible candidate. `/qplan` or the implementation phase must test this directly against a live v3 session before committing to a specific fix design.
- Detailed mode-switcher UI shape — deliberately left open for `/qplan`'s design pass (see Success criteria).

**Assumptions (unconfirmed)**: none — every item in this plan's scope was either directly resolved with the user or is explicitly carried forward as a documented Open Item rather than assumed.

### Recommended approach

1. Orphan-lock fix first — small, well-specified, low-risk: change `_SupervisorV3._publish_live()` (or its post-cutover name) to publish the real agent pid instead of `pid=0`.
2. Steering-palette spike second — attempt `_kiro/knowledge` against a live v3 session to resolve one entry's `contextQuery`; if it returns usable content, design the client-side synthesis path ROADMAP originally envisioned (now grounded in real data instead of a guess); if not, document the negative finding and update `plans/ROADMAP.md`.
3. Mode-switcher UI last — the most open-ended design item; benefits from `/qplan` doing a proper design pass rather than this exploration guessing at a shape.

### QA environment

- Same live PowerAtlas instance and constraints as `plans/260911_ACP_V2_TO_V3_ENGINE_CUTOVER.md`: `acp.py`/`presence.py` changes need a restart (coordinate timing with the user); `acp.html` changes are hot-reloadable (hard browser reload only, per AGENTS.md:7).
- The steering-palette spike can reuse this session's proven approach: an isolated, disposable `kiro-cli acp --agent-engine v3` subprocess for wire-shape investigation that never touches the live PowerAtlas process, plus live browser checks against an existing idle session for UI-level behavior.
- `tests/test_web.py` (pytest) and `tests/acp_page.test.mjs` (`node tests/acp_page.test.mjs`, hand-run only) apply to any backend/template changes respectively, same as the cutover plan.
