# PowerAtlas — qtest Findings Fixes (hardening)

> **Date**: 2026-07-07
> **Status**: Complete  <!-- Exploring → Draft → In Progress → Complete -->
> **Last Updated**: 2026-07-08 12:57
> **Scope**: Fix the still-valid Medium/Low findings from the `260701_POWERATLAS` qtest run. The High data-loss cluster was already resolved by `260707_CONFIG_HOT_RELOAD_AND_PEEK_RESET`; this project covers the remaining ~22 correctness/robustness/perf findings + one dead-code cleanup.
> **Estimated effort**: ~1.5–3 days (6 phases; no High-severity work remaining)
> **Anchors RE-ANCHORED against HEAD**: `bb843f2` (code `c0ca17a`, 2026-07-07) — **after `260707_LAUNCH_PROFILES_FOR_EXPORTABLE_MCP_SAFE_TERMINALS` landed** (it rewrote config/launcher/web/index + tests). §1 Current State holds the current anchors; §5 phase snippets keep pre-LAUNCH_PROFILES line refs (use §1 + `git diff bb843f2 -- <file>`). Scope changes from the rework: CSRF (#2) is now RESOLVED by LAUNCH_PROFILES's `same_origin_guard` middleware; all other findings survived at new line numbers; data/icons/lifecycle files were untouched.

## Completion Summary

### Acknowledged at archival

- `Accepted (harness opportunity)`: Exploration output went stale across a multi-day gap because findings were anchored to `file:line` and the code was reworked in between — suggested change: when `/qexplore` output will be handed to a *deferred* `/qplan`, record the HEAD commit SHA the anchors were verified against, so `/qplan` can cheaply detect drift (git diff since that SHA) before trusting them.

---

## Intent

### Problem statement & desired outcomes
The full-app runtime test (`plans/tests/260701_POWERATLAS.md`) surfaced 32 findings. Re-validated against HEAD on 2026-07-07: the High data-loss cluster (settings-save wipes pins; provider-hardcoding migration; startup warmup `TypeError`) is **already fixed** by the `pinned_folders → list[str]` rework and `/settings` removal. What remains is a set of **Medium/Low correctness, robustness, and performance defects** with clear, isolated fixes: two reachable endpoint crashes (500s), a discovery-cache thundering herd, cross-provider cache/behaviour asymmetries, several unhardened input paths, and a stale-UI glitch. Desired outcome: eliminate the reachable crashes, make the config layer defensive (backup + preserve + validate), remove the perf cliff, and tidy the remaining rough edges — without regressing the green test suite or the behaviours the accepted findings deliberately keep.

### Success criteria
Each is concrete and verifiable (via a targeted test or a runtime probe). Grouped by subsystem.

**Crashes (highest priority — reachable 500s):**
- **SC1 (L2):** `launch_session` with malformed `default_args` (e.g. `'"'`) returns a `LaunchResult` error instead of raising `ValueError`; `/api/launch`, `/api/new-session`, `/api/launch-batch` return an error toast, not HTTP 500. Both `shlex.split` sites (`launcher.py:150` non-terminal, `:180` terminal) are covered. Honours the "never raises" contract (`launcher.py:130`).
- **SC2 (I3):** A whitespace-only launcher `command` no longer raises `IndexError` from `_resolve_binary` (`icons.py:129`); `/api/launcher/create` (`web.py:886`) and `/api/launcher/update` (`web.py:900`) succeed (with SVG fallback) instead of 500.

**Config robustness (decision: backup + preserve):**
- **SC3 (C5):** On a corrupt `config.toml`, `load_config` copies it to `config.toml.bak` and logs a warning before returning defaults (no silent, backup-less overwrite). (`config.py:45-49`)
- **SC4 (C7):** Unknown/future TOML keys survive a load→save round-trip (preserved, re-emitted) rather than being dropped. (`config.py:53-55`, `save_config` `:91`)
- **SC5 (C6):** `trust_all_tools=true` migrates to a `kiro-cli` entry even when *other* providers already exist in `provider_settings` (condition changes from "no provider_settings" to "no kiro-cli entry"). (`config.py:76`)
- **SC6 (C4-nested):** Malformed nested config values (`pinned_folders=[123]`, `provider_settings={'x':'notadict'}`, `workspace_icons={'k':5}`) are dropped/sanitised on load rather than stored verbatim; the existing top-level + bool guards (`config.py:56-61`) are preserved.

**Data layer (correctness + perf):**
- **SC7 (D1):** Concurrent cold-cache callers of `discover_workspaces_with_counts` (`data.py:145-179`) trigger a single scan (lock + single-flight), not N; the `GET /` cold-load pile-up is gone. The discover compute/store is `_discover_lock`-guarded and the fast-path read is `KeyError`-safe against concurrent `_cache.pop/clear` (the lock guards discovery, not every raw `_cache` access).
- **SC8 (D2):** The Claude adapter caches `get_session_tail`/`get_first_prompt` with an mtime guard, matching the kiro adapter (`data_kiro.py:254,310`); identical hover tooltips no longer re-parse per request.
- **SC9 (D3):** `SessionCache.get` (`data.py:84`) returns Session objects a caller cannot mutate into the shared cache (deep-copy or frozen dataclass).
- **SC10 (D4):** kiro `get_first_prompt` (`data_kiro.py:314-337`) does not pin an empty `''` for 60s without an mtime guard.
- **SC11 (D5):** `discover_workspaces_with_counts` with an unknown provider returns `[]` (fail-closed), consistent with `get_sessions` (`data.py:162-164` vs `:188-190`); no unbounded per-string cache keys.
- **SC12 (D7):** A Claude tooltip with an empty/unresolvable cwd is distinguishable from genuine "no output" (or the cwd is guaranteed non-empty at the call site `web.py:611`). *(Low.)*

**Web + GUI:**
- **SC13 (W1):** A disabled provider's workspace cards/badges are hidden from the "All" view, not just the tab bar (`partials_workspaces`/`_group_workspaces` consult `provider_settings[...].enabled`, matching `/api/available-providers` `web.py:543`).
- **SC14 (W4):** `GET /api/provider/{key}` for an unknown provider returns a 404 / empty rather than synthesised `enabled:true` defaults (`web.py:547-551`).
- **SC15 (G1):** After a provider switch (`switchProvider`) or any htmx swap that drops selected rows, the action bar reflects the real selection count (call `updateActionBar()` post-swap) — no phantom "1 selected".

**Launcher hardening (Low):**
- **SC16 (L1):** The cmd.exe fallback (`launcher.py:324-328`) guards the joined command args against shell metacharacters, not only `cwd`.
- **SC17 (L3):** Windows backslash paths in `default_args` are not mangled (`shlex.split(..., posix=False)` on Windows, `:150/:180`).
- **SC18 (L5):** Session-id validation has a length bound (`launcher.py:21,143`).
- **SC19 (L6):** Title sanitiser strips `;`, `$`, `` ` `` in addition to `"'&|` (`launcher.py:228`); existing tests stay green.

**Icons + Lifecycle (Low):**
- **SC20 (I1):** `default_icon_svg` (`icons.py:72`) validates/escapes the color before injecting it into SVG markup.
- **SC21 (I2, I4):** `_resolve_binary` resolves a space-containing binary path with trailing args (`icons.py:119-137`); `.cmd` shims using variable-indirection resolve or fall back cleanly (`icons.py:76-116`).
- **SC22 (Lc1):** `power-atlas --stop --restart -f` is rejected (argparse mutually-exclusive group, `__main__.py:321-334`) instead of silently honouring only `--stop`.
- **SC23 (H8):** `--restart` waits (bounded) for the old instance's mutex to free instead of a fixed `time.sleep(0.5)`, so a slow shutdown doesn't silently skip the relaunch.

**Cleanup:**
- **SC24:** Dead `settings.html` template and `.settings-*` / `.settings-page` CSS (`style.css`) are removed (the `/settings` route and `POST /api/settings` no longer exist).

### Scope boundaries & non-goals
**In scope:** SC1–SC24 above.

**Out of scope — already resolved at HEAD (do NOT re-implement):**
- C1 provider-hardcode migration, C2 settings-save wipes pins, the startup warmup `TypeError` crash, C4-bool, and W2 `'custom'`-sentinel divergence — all fixed by the `pinned_folders → list[str]` rework + `/settings` removal in `260707_CONFIG_HOT_RELOAD_AND_PEEK_RESET`. C3 mixed-list corruption survives only for transition-era on-disk configs and is covered incidentally by SC6.

**Out of scope — accepted (documented, deliberately not fixed):**
- **L4** — validating the terminal-override path (`launcher.py:37-38`); ~10 launch tests pass fake override paths, and a bad override already surfaces as a launch-time error. Fix cost ≫ bug impact.
- **D6** — lexical cross-provider sort (`data.py:177`); zero real inversions today (all timestamps fixed-width UTC).
- **A1** — autostart existence-only check (`autostart.py`); documented behaviour, and validating the shortcut target breaks `test_autostart.py:59` for an edge case.
- New untested `data_kiro_ide.py` provider — out of scope (new feature code, not a finding).

**Non-goals:** no new features; no re-architecting the provider system; no rework of the (already-fixed) pinned_folders/settings surface; no new test *files* (regression tests are added as *functions* to existing files per `AGENTS.md`).

---

## Discovery

*(Preserved in-file because `/qplan` runs in a later session without this conversation's context.)*

### 4. Existing patterns & constraints
- **Governance** (`AGENTS.md`): update existing tests when implementation changes; **no new test files** unless a regression fix requires one (regression tests go as new *functions* in existing files); update README only for user-visible changes; no new doc files.
- **Test infra**: `pip install -e ".[dev]"` then `pytest`. No `conftest.py`, no CI, no pytest config; isolation is per-file (autouse `isolated_config` monkeypatches `CONFIG_DIR`/`CONFIG_PATH` in `test_config.py:11`; `test_data.py` monkeypatches `SESSION_DIR`/`SQLITE_PATH`; `test_web.py` uses Starlette `TestClient`). ~220 `def test_` functions. Several `test_web.py` tests read the *real* config (don't patch `load_config`) — a pre-existing fragility to avoid worsening.
- **Fix precedents to reuse**: atomic write scaffold `save_config` (`config.py:85-100`) for the C5 `.bak`; lock precedents `SessionCache._lock` (`data.py:73`) + `data_claude._path_index_lock` for D1 (single-flight is new, ~20 lines); mtime-guarded cache `data_kiro._tail_cache` (`data_kiro.py:283`) as the model for SC8/SC10; `_SETTING_TYPES` allowlist + `isinstance` (`web.py:574-599`) for validation; regex-strip `_sanitize_title` (`launcher.py:231`) for L6. **No SVG/HTML-attr escaper exists** — SC20 introduces color validation (hex/allowlist), new.
- **Constraints**: `LaunchResult` "never raises" (`launcher.py:130`) — SC1 must bring `shlex.split` inside the try or pre-validate. Config atomic-save + legacy `trust_all_tools` drop (`config.py:92`) must be preserved. `save_config` *can* raise; `load_config` must not.

### 5. Risks & mitigations
- **Stale-anchor risk (materialised once already):** the code drifted materially between exploration (Jul 1-2) and now (Jul 7); the git diff base is unreliable. **Mitigation:** all SC anchors above are re-verified against HEAD on 2026-07-07. `/qplan`/`/qdev` should re-confirm before editing, as more drift may occur.
- **Tests encoding changed behaviour:** SC5 (C6) will fail `test_config.py:184-195` (asserts *no* migration when provider_settings exist) → update it. SC6/SC20/etc. are `[GAP]` (no test) → add regression functions. L4/D6/A1 accepted → their tests stay untouched.
- **SC13 (W1) behaviour change:** hiding a disabled provider's cards changes user-visible output; confirm this is the intended semantics of "disabled" (vs. tab-only). Flag to user if ambiguous during `/qdev`.
- **SC7 (D1) single-flight** is new concurrency code — the one non-trivial addition; keep it minimal and mirror existing lock idioms.

### 6. Resolved decisions
- Q-Triage: How to handle low-value/high-cost findings? — A: decide per-finding — Decision: **Accept** L4, D6, A1 (documented in Scope non-goals); fix L6; fix all other material findings.
- Q-Pins: How to fix the settings-save pin wipe (option a/b/c)? — A: **C** (remove `/settings` + drop custom-terminal-via-page) — Decision: **Moot / already done** at HEAD (route removed; custom terminal moved to topbar). Residual becomes SC24 (dead-file cleanup). Superseded by: HEAD-revalidation 2026-07-07.
- Q-Config: How defensive should the config layer be (C5/C7/C4)? — A: **Backup + preserve** — Decision: SC3 (`.bak`+log), SC4 (preserve unknown keys), SC6 (drop bad nested entries only).
- Q-Cache: D1 fix depth? — A: **Lock + single-flight** — Decision: SC7.
- Q-Scope: one combined plan or decomposed? — A: (implied) — Decision: **one combined, phased plan** (findings share files — `web.py`, `config.py`, `data.py` — and test files).

### 7. Open items
- **SC13 (W1)** semantics — *resolved 2026-07-07*: user confirmed **Option A** — hide cards + badges + expanded session rows for a disabled provider; keep explicitly-pinned sessions. No longer open.
- **SC12 (D7)** exact remedy (guarantee non-empty cwd at `web.py:611` vs. a distinct "unresolved" tooltip state) — resolvable by reading the call sites during planning.
- **C3 transition-era mixed lists** — *resolved during review*: SC6's post-migration sanitize does **not** neutralise this (the migration loop crashes first). Fixed directly in Phase 2 by guarding the migration loop with `isinstance(entry, dict)`. No longer open.
- **CSRF/Origin** — *resolved 2026-07-07, now IMPLEMENTED*: `260707_LAUNCH_PROFILES` shipped the `same_origin_guard` HTTP middleware (`web.py:139-155`) that rejects cross-origin/`null`/both-header-absent POSTs across all routes — exactly the follow-up the user accepted. No work remains; the earlier "tracked follow-up" is closed.

### 8. Recommended approach
One combined, phased `/qplan` plan, sequenced by risk:
1. **Crashes first** — SC1 (L2), SC2 (I3): smallest, highest-value (stop reachable 500s). Add regression test functions.
2. **Config robustness** — SC3–SC6 (all in `config.py` + `test_config.py`, one cohesive change; update the C6 test).
3. **Data layer** — SC7 (D1 lock+single-flight), SC8/SC10 (claude caches + mtime), SC9 (Session copy), SC11 (fail-closed), SC12 (D7).
4. **Web + GUI** — SC13 (W1), SC14 (W4), SC15 (G1 — pure `index.html` JS).
5. **Launcher/icons/lifecycle hardening** — SC16–SC23 (Low, mechanical).
6. **Cleanup** — SC24 (dead `settings.html` + CSS).
Verify per-phase with targeted pytest + a runtime probe (server for endpoint SCs, headless Playwright for SC15 which is already installed in the venv).

---

## 1) Current State
**RE-ANCHORED against HEAD `bb843f2` (code `c0ca17a`) after `260707_LAUNCH_PROFILES_FOR_EXPORTABLE_MCP_SAFE_TERMINALS` landed (2026-07-07).** That project rewrote `config.py` / `launcher.py` / `web.py` / `index.html` (+ the 3 test files), so those anchors moved; `data.py`, `data_kiro*.py`, `data_claude.py`, `icons.py`, `__main__.py`, `settings.html` were **untouched** so their anchors are current. `/qdev` still re-confirms with `git diff` before editing (the code may drift again). **The phase snippets in §5 keep their pre-LAUNCH_PROFILES line refs — this §1 is the authoritative current-anchor source; place edits from here + `git diff`.**
- **Launcher** (`launcher.py`) — *re-anchored, all still valid*: `shlex.split(default_args)` runs at `:383` (non-terminal `kiro-ide` path) and `:413` (terminal path), both **outside** the surrounding `try` (`:387` and the terminal-launch try below) — the "never raises" contract (`:362`) is broken on malformed args (L2). LAUNCH_PROFILES's new `default_args` API validation (`web.py:566-572`) checks only length + control chars, so it does **NOT** cover L2/L3 (an unbalanced-quote or backslash-path `default_args` still reaches `shlex.split`). The WT/pwsh builders were rewritten to take `wt_profile`, but the **injection sinks survived**: pwsh `& {' '.join(kiro_args)}` (`:581`) and the cmd `/k` join (`:595`; guard `_CMD_METACHAR_RE :494` applied to `cwd` only `:591`) (L1). `_TITLE_UNSAFE_RE = ["'&|]` (`:495`; `_sanitize_title :498-500`) misses `;$\`` (L6). `_SESSION_ID_RE` (`:26`) has no length bound (check `:376`) (L5). `_build_template_command` (`:503`) whitespace-splits literal segments (L7). NOTE: `LaunchResult` now carries `warning`/`used_fallback` (`:17`) — the launcher fixes here must not disturb those.
- **Icons** (`icons.py`): `_resolve_binary` (`:119-137`) `if not command` (`:121`) doesn't catch whitespace-only → `cmd.split()[0]` (`:129`) `IndexError`; `extract_icon` is called unwrapped by `web.py:886` (create) and `:900` (update) → 500 (I3). Space-in-path + args fails resolution → SVG fallback (I2). `default_icon_svg` (`:72`) injects `color` unescaped into SVG (I1). `_resolve_cmd_to_exe` (`:98-116`) mishandles `%~dp0\...` leading-backslash shims (npm.cmd) (I4). *(File untouched by LAUNCH_PROFILES — anchors current; but note `web.py:886/900` moved — re-confirm the icon endpoints.)*
- **Config** (`config.py`) — *re-anchored, all still valid*: corrupt TOML → silent defaults, no backup (`:156-157`) (C5). Unknown keys dropped on load (`:161-163`) and lost on save (`asdict` `:228`; save now also pops `terminal_command` `:230`) (C7). `trust_all_tools` migration gated on `not config.provider_settings` (`:213`) → suppressed by any provider entry (C6). The dict→str `pinned_folders` migration (`:201-210`) calls `.get()` on every element when `[0]` is a dict → a mixed/transition list crashes `load_config` (C3). Load validates only top-level type + bool (`:164-170`); the OTHER nested fields (pinned_folders/workspace_icons/custom_launchers/provider_settings) are unvalidated (C4-nested). NOTE: LAUNCH_PROFILES added a validation framework — `_normalize_launch_profile` (`:76-145`), `_strip_control_chars` (`:71`), `_SHELL_*`/`_HELPER_*` allow/deny lists (`:28-31`) — but only for `launch_profiles`; C4-nested should **mirror this existing pattern** for the other fields.
- **Data** (`data.py`): module `_cache` (`:15`) is a lockless dict; `discover_workspaces_with_counts` (`:145-179`) is check-then-act with no lock/single-flight (D1). `SessionCache.get` (`:84`) shallow-copies the list but shares mutable `Session` objects (`:19-28`) (D3). Unknown provider fails **open** → all providers (`:162-164`), caching a per-string key (`:154`) (D5); `get_sessions` fails closed (`:188-190`) — inconsistent. `data_kiro.py` has `_tail_cache`/`_first_prompt_cache` (`:254,310`); `data_claude.py` `get_session_tail`/`get_first_prompt` (`:378,426`) have **none** (D2) and return `[]`/`""` on unresolvable cwd (`:380-382,428-430`) (D7). kiro `get_first_prompt` negative-caches `""` for 60s with no mtime guard (`data_kiro.py:316-336`) (D4). *(Files untouched by LAUNCH_PROFILES — anchors current.)*
- **Web** (`web.py`) — *re-anchored, W1/W4 still valid; CSRF now RESOLVED*: `partials_workspaces` (`:333-373`), `partials_pinned_workspaces`, `search`, and `_group_workspaces` (`:48`) render cards for **all** providers; only `/api/available-providers` (`:544-547`) filters by `enabled` (→ tabs only) (W1). `get_provider_settings` (`:552-554`) returns synthesized `enabled:true` for unknown keys (W4). **RESOLVED by LAUNCH_PROFILES:** the CSRF concern (#2) — `web.py:139-155` now has an `@app.middleware("http") same_origin_guard` rejecting cross-origin/`null`-origin/both-header-absent POSTs. No longer a follow-up.
- **GUI** (`templates/index.html`): `switchProvider()` (`~:147`) swaps `#workspace-cards` innerHTML without calling `updateActionBar()`; the `htmx:afterSwap` handler (`:144`) calls only `loadExpandedCards()` → stale action bar after a provider switch drops selected rows (G1). *(LAUNCH_PROFILES added topbar profile controls but did not touch this gap.)*
- **Lifecycle** (`__main__.py`): three `store_true` flags with no mutually-exclusive group (`:321-334`) → `--stop --restart` silently stop-only (Lc1). `--restart` uses a fixed `time.sleep(0.5)` (`:342-343`) before relaunch → slow shutdown can leave the mutex held and skip the relaunch (H8). *(File untouched by LAUNCH_PROFILES — anchors current.)*
- **Dead code**: `templates/settings.html` and the `.settings-*` rules in `static/style.css` remain orphaned (the `/settings` page route + `POST /api/settings` are gone; the live `GET /api/settings` stays and must be kept). LAUNCH_PROFILES Phase 5's dead-code sweep grepped `terminal_command|terminal_override|PowerShell|MCP-safe|launch_profiles` — NOT `.settings-`, so it did not remove these (SC24). `/qdev` re-counts the exact `.settings-` rules (style.css grew with launch-profile-modal styles).

## 2) Goal
Eliminate the two reachable endpoint 500s, make the config layer defensive (backup + preserve + nested validation), remove the discovery-cache cold-start pile-up, close the cross-provider cache/behaviour gaps, harden the remaining input paths, and delete the orphaned settings UI — with no behaviour regressions beyond the intended W1 change and the green test suite kept green.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Config posture (C5/C7/C4) | Backup corrupt config to `.bak` + log; preserve unknown keys via a non-field `_extra` dict merged at save; drop only malformed nested entries | Silent defaults (status quo); reject/refuse-save on corrupt; schema `version` field | User-confirmed "backup + preserve" during `/qexplore`. `_extra` as an instance attr (not a dataclass field) keeps `asdict` clean and `test_unknown_keys_ignored` green. |
| Discovery cache (D1) | Lock + double-checked compute (single scan per key under `_discover_lock`) | Lock-only (pile-up remains); per-key `Event` single-flight | User-confirmed lock+single-flight. Double-check-under-lock is the minimal idiom; brief serialization of discovery is acceptable on a single-user desktop app. |
| Session isolation (D3) | `@dataclass(frozen=True)` on `Session` | Deep/`replace` copy on every `get` | Zero-cost, prevents poisoning at the source. **Gated**: Phase 3 must grep-confirm no post-construction `Session` field assignment exists; if any is found, fall back to `dataclasses.replace(s)` on `get`. |
| Unknown provider (D5) | Fail **closed** — unknown provider → `[]`, not cached | Keep fail-open | Consistency with `get_sessions`; prevents unbounded cache keys. |
| W1 semantics | Disabled provider hides its **workspace cards + badges + expanded session rows** (filter applied in `partials_workspaces`, `partials_pinned_workspaces`, `search`, AND the `partials_sessions` provider=all merge). **Explicitly-pinned individual sessions from a disabled provider still render** (a pin is a deliberate user choice) — documented residual. | Tab-only (status quo); hide absolutely everything incl. pins | Senior review: filtering only the card discovery left disabled-provider session rows visible on expand. Boundary drawn at "discovery hidden, explicit pins honored." **User-confirmed: Option A (2026-07-07).** |
| W4 unknown provider | Return HTTP 404 for a provider not in `data.PROVIDERS` | Empty defaults (status quo); 400 | A GET for a non-existent provider is a not-found, not a valid default. |
| L1 cmd injection | **Both** paths: cmd.exe fallback rejects args with a cmd metachar (as the `cwd` guard does); **pwsh path single-quote-escapes each arg** (`'` → `''`), matching the existing cwd escaping | Full cmd.exe quoting; reject-only | Security review: pwsh is the *preferred* Windows terminal — guarding only cmd left the common path injectable via `default_args`. Escaping (not rejecting) the pwsh args avoids over-rejecting legitimate args. wt (argv) + Linux paths are already injection-safe. `launch_custom`'s `shell=True` path is an intended arbitrary-command feature (out of scope). |
| H8 restart race | Capture `old_pid` **before** `_stop_running()` (which deletes the PID file), then bounded-poll `_pid_alive(old_pid)` (not `_read_pid()`) until the old process is dead or a ≤5s deadline; if still alive after the deadline, surface it (non-silent) | Keep fixed sleep; poll `_read_pid()` | Reliability/Architect/Senior all caught that polling `_read_pid()` is a no-op on Windows (the PID file is already gone); `_pid_alive(old_pid)` is the correct cross-platform liveness signal. |
| D7 (claude cwd) | Minimal: keep behaviour; the UI always passes `data-cwd`. Add a code comment; no functional change | Distinct "unresolved" tooltip state | Low severity; the reachable path already supplies cwd. Avoid over-engineering. |

## 4) External Dependencies & Costs
None — code-only, no infra/CI/IAM/third-party/data-layout changes. Playwright (already installed in `.venv-PowerAtlas`) is reused for the SC15 GUI check. **Cost impact**: none.

## 5) Implementation Phases

### Phase 1: Fix reachable endpoint crashes [QA]
**Goal**: Stop `/api/launch*` and `/api/launcher/create|update` from returning HTTP 500 on malformed input.
**File scope**: `src/power_atlas/launcher.py`, `src/power_atlas/icons.py`, `tests/test_launcher.py`.
**Covers**: SC1, SC2, SC17.

- **L2** (`launcher.py`): parse `default_args` once, after the session-id check (`:143`), before the provider branch, inside a guard:
  ```python
  try:
      extra_args = shlex.split(default_args, posix=(sys.platform != "win32")) if default_args else []
  except ValueError as e:
      return LaunchResult(False, session_id, cwd, error=f"Invalid launch arguments: {e}")
  ```
  Replace both `cli_args += shlex.split(default_args)` sites (`:150`, `:180`) with `cli_args += extra_args`. (This also delivers **SC17/L3** via `posix=` — Phase 5 no longer needs to touch this line; keep L3 here.)
- **I3** (`icons.py` `_resolve_binary`): after `cmd = command.strip()...` (`:123`) add `if not cmd: return None`; replace `token = cmd.split()[0]` (`:129`) with `parts = cmd.split(); if not parts: return None; token = parts[0]`.
- **Tests** (regression functions per `AGENTS.md` no-new-file rule): `test_launch_session_malformed_default_args_returns_error` — MUST `@patch("power_atlas.launcher.shutil.which")` to return a fake path AND pass an existing `tmp_path` cwd, else it short-circuits at the binary/folder guards (`launcher.py:134,140`) before reaching the `shlex` parse; assert `.success is False` and `"Invalid" in .error`, no raise. `test_resolve_binary_whitespace_command_returns_none` (import `_resolve_binary`, assert `is None`, no `IndexError`). `test_default_args_windows_quoting` — with `sys.platform` patched to win32, assert a backslash path (`C:\Users\me\proj`) round-trips intact and a quoted-spaces arg (`--foo "bar baz"`) is handled acceptably under `posix=False` (documents the quote-retention tradeoff of L3).

**Exit criteria**:
- [x] `launch_session` with `default_args='"'` returns a `LaunchResult` error (both provider paths); no `ValueError` escapes.
- [x] `_resolve_binary("   ")` returns `None`; `POST /api/launcher/create` with `command="   "` returns 200 (SVG fallback), not 500.
- [x] Backslash path in `default_args` is preserved on Windows (`posix=False`) (SC17/L3).
- [x] New regression tests pass; full `pytest` green.

**Implementation (2026-07-08, code: 0b9cae2)**
Fixed two crash paths in the launcher subsystem: (1) `launch_session` now parses `default_args` with `shlex.split(..., posix=False)` on Windows once before both the terminal and non-terminal branches, catching `ValueError` from malformed quotes and returning a structured `LaunchResult` error instead of letting it propagate as HTTP 500; (2) `_resolve_binary` in icons.py now guards against whitespace-only `command` strings that would cause `IndexError` on `cmd.split()[0]`. Three regression tests were added to the existing `tests/test_launcher.py`: one exercising the malformed-args path through both provider branches, one confirming whitespace-only commands return `None`, and one documenting the `posix=False` behavior that preserves Windows backslash paths.

### Phase 2: Config robustness — backup + preserve + validate [QA] [P:3,4,5]
**Goal**: Make `load_config`/`save_config` defensive without changing the happy path.
**File scope**: `src/power_atlas/config.py`, `tests/test_config.py`.
**Covers**: SC3, SC4, SC5, SC6.

- **C5** (`config.py:45-49`): on `except (OSError, tomllib.TOMLDecodeError)`, back up the corrupt file then default. The backup MUST NOT be able to make `load_config` raise (it is the one path that must never raise):
  ```python
  except (OSError, tomllib.TOMLDecodeError):
      try:
          shutil.copy2(CONFIG_PATH, CONFIG_PATH.with_name(CONFIG_PATH.name + ".bak"))  # config.toml.bak (not .with_suffix, which yields config.bak)
          log.warning("Corrupt config backed up to %s; using defaults", ...)
      except Exception:
          log.warning("Corrupt config; using defaults (backup failed)")
      return Config()
  ```
  Add a module logger + `import shutil`.
- **C3 (migration crash guard)** — **must land here, in Phase 2** (a reachable `load_config` `AttributeError` on a mixed/transition `pinned_folders`): the dict→str migration loop (`config.py:65-73`) calls `entry.get("folder","")` on every element once `[0]` is a dict; a mixed list like `[{"folder":"/a"}, "x"]` raises `AttributeError` on the str element **before** the C4 sanitize runs. Guard the loop: `folder = entry.get("folder","") if isinstance(entry, dict) else (entry if isinstance(entry, str) else "")`. (SC6's post-migration sanitize cannot fix this — ordering.)
- **C7**: at load, capture `extra = {k: v for k, v in data.items() if k not in fields and k != "trust_all_tools"}`; after building `config`, set `config._extra = extra` (instance attr, not a field). In `save_config`, after `data = asdict(config); data.pop("trust_all_tools", None)`, add `data.update(getattr(config, "_extra", {}) or {})` before `tomli_w.dump`. **Object-identity constraint**: `_extra` round-trips only while the *loaded* Config flows back into `save_config` (true for every current load→modify→save endpoint); a future fresh-`Config()` save path would drop unknowns — document this in a code comment.
- **C6** (`config.py:76`): change condition to `if data.get("trust_all_tools") is True and "kiro-cli" not in config.provider_settings:`.
- **C4-nested**: after migrations, sanitize element types:
  ```python
  config.pinned_folders = [x for x in config.pinned_folders if isinstance(x, str)]
  config.pinned_sessions = [x for x in config.pinned_sessions if isinstance(x, str)]
  config.workspace_icons = {k: v for k, v in config.workspace_icons.items() if isinstance(k, str) and isinstance(v, str)}
  config.custom_launchers = [x for x in config.custom_launchers if isinstance(x, dict)]
  config.provider_settings = {k: v for k, v in config.provider_settings.items() if isinstance(v, dict)}
  ```
- **Tests**: **update** `test_trust_all_tools_no_migration_when_provider_settings_exist` (actual location `test_config.py:203-214` — the plan's earlier `184-195` cite was a stale anchor for a *different* test; re-confirm at `/qdev`) — it now MUST migrate kiro-cli when only *other* providers exist (rename/repurpose to assert the kiro-cli entry appears; keep a case where an existing kiro-cli entry is NOT overwritten). Add: `test_corrupt_config_backs_up_and_defaults` (write junk, assert `config.toml.bak` created + defaults, no raise); `test_mixed_pinned_folders_no_crash` (`[{"folder":"/a"}, "x"]` → migrates without `AttributeError`); `test_unknown_keys_preserved_on_save` (load with `future_key`, save, reload raw TOML still has it); `test_nested_bad_types_dropped` (`pinned_folders=[123]` → `[]`).

**Exit criteria**:
- [x] Corrupt `config.toml` → `.bak` written + warning logged + defaults returned (no silent overwrite).
- [x] Unknown/future keys survive load→save→load.
- [x] `trust_all_tools=true` with only a `claude-code` entry migrates a `kiro-cli` entry; an existing `kiro-cli` entry is not clobbered.
- [x] `pinned_folders=[123]`, `provider_settings={'x':'s'}`, `workspace_icons={'k':5}` are dropped on load; top-level + bool guards preserved.
- [x] `test_config.py` updated + new tests pass; `save_config` still drops `trust_all_tools` and stays atomic + lock-safe.

**Implementation (2026-07-08, code: 7f20f9d)**
Made `load_config`/`save_config` defensive against real-world corruption and data evolution without changing the happy path. Added corrupt-config backup logic (C5) that catches parse failures including `UnicodeDecodeError` and copies the broken file to `.bak` before returning defaults. Guarded the pinned_folders migration loop (C3) against mixed-type lists that would cause `AttributeError`. Implemented unknown-key preservation (C7) via a `_extra` instance attribute so future config keys added by newer versions aren't silently dropped on re-save. Changed the `trust_all_tools` migration condition (C6) to check specifically for `"kiro-cli"` key presence rather than an empty dict, allowing migration even when other providers already exist. Added post-migration type sanitization (C4-nested) that drops non-str entries from pinned lists, non-dict entries from provider_settings, and non-dict entries from custom_launchers. Five new/updated tests cover all defensive behaviors.

### Phase 3: Data-layer correctness & performance [QA] [P:2,4,5]
**Goal**: Eliminate the discovery pile-up, the cache-poisoning vector, and the cross-provider cache/fail-open asymmetries.
**File scope**: `src/power_atlas/data.py`, `src/power_atlas/data_claude.py`, `src/power_atlas/data_kiro.py`, `tests/test_data.py`.
**Covers**: SC7, SC8, SC9, SC10, SC11, SC12.

- **D1** (`data.py`): add `_discover_lock = threading.Lock()` near `:16`. Wrap `discover_workspaces_with_counts` body (`:154-179`) so the cache check-compute-store runs under the lock with a double-check (return cached if fresh after acquiring). The lock-free fast-path read MUST be `KeyError`-safe against concurrent `web.py` `_cache.pop/clear` (`:246,273,336,509`) on the event-loop thread — use `entry = _cache.get(cache_key); if entry is not None: ts, result = entry; ...`, NOT `if cache_key in _cache: ... _cache[cache_key]` (check-then-index TOCTOU → spurious "could not load session data" toast). `web.py`'s `.pop/.clear` stay lock-free (dict ops are GIL-atomic; a store racing a clear self-corrects via TTL). **SC7 wording softened accordingly** — the lock guards the discover compute/store and single-flight, not every `_cache` access. Ensure the in-lock re-check is present (avoids double-scan).
- **D5** (`data.py:162-164`): fail closed — `if provider is not None and provider not in PROVIDERS: return []` (before the cache key is built), so unknown providers neither scan-all nor cache.
- **D9/D3** (`data.py`): `@dataclass(frozen=True)` on `Session` (`:19`). **Gate**: grep the repo for post-construction `Session` field assignment (`\.title\s*=`, `\.cwd\s*=`, etc.); if none, freeze; if any, instead return `[dataclasses.replace(s) for s in sessions]` from `SessionCache.get` (`:84`).
- **D2** (`data_claude.py`): add `_tail_cache`/`_first_prompt_cache` mirroring `data_kiro.py:254,310`, mtime-guarded (3-tuple `(time, mtime, value)`); do not cache empty results. **Key by the resolved `jsonl_path` string (or `(session_id, cwd)`), NOT `session_id` alone** — Claude session files live under a cwd-derived folder (`_get_project_folder(cwd)` `data_claude.py:380,428`), so a `session_id`-only key would collide/mis-serve across workspaces.
- **D10/D4** (`data_kiro.py:310-336`): change `_first_prompt_cache` to a 3-tuple `(time, mtime, prompt)` with an mtime guard (mirror `_tail_cache:283`); do not negative-cache `""` (return without storing when empty). **Record the mtime of the file actually read** — `get_first_prompt` prefers `.history` (`:321`) then falls back to `.jsonl`; guard whichever supplied the value (or track both mtimes), else a `.history` edit won't invalidate.
- **D7** (`data_claude.py`): add a one-line comment at `get_session_tail`/`get_first_prompt` noting the cwd-required contract; no functional change (UI always passes `data-cwd`).
- **Residual (noted, out of scope)**: the third provider `data_kiro_ide.py` retains the pre-fix cache shape (2-tuple, negative-caches `""`) — it stays the odd-one-out until it leaves "new/untested" status; fold into a follow-up. Neither adapter adds cache eviction (unbounded growth inherited; acceptable for a single-user desktop app).
- **Tests**: `test_discover_unknown_provider_returns_empty` (fail-closed, no new `_cache` key); `test_session_cache_get_isolated` (mutate a returned Session field → cache unaffected, or `FrozenInstanceError`); `test_claude_tail_cached` / `test_claude_first_prompt_cached` (second call within TTL doesn't re-read — patch/spy the file read); `test_kiro_first_prompt_refreshes_after_mtime_change`. **Test-setup note**: the autouse `_clear_cache` fixture (`test_data.py:124`) clears only `session_cache`, NOT the module-level `data._cache` — these tests must `data._cache.clear()` in their own setup, else stale discovery results leak between tests.

**Exit criteria**:
- [x] Concurrent cold callers of `discover_workspaces_with_counts` trigger one scan (assert via a call-count spy on `mod.discover_workspaces` under N threads).
- [x] Unknown provider → `[]`; no per-string cache key created.
- [x] Mutating a Session returned by `SessionCache.get` does not alter a subsequent `get` (or `Session` is frozen and mutation raises).
- [x] Claude tail + first-prompt are cached (mtime-guarded); kiro first-prompt refreshes after an mtime change and never pins an empty result.
- [x] `test_data.py` new tests pass; full `pytest` green.

**Implementation (2026-07-08, code: 5f4abf5, fix: 0416990)**
Added discovery pile-up prevention via `_discover_lock` with a double-check locking pattern in `discover_workspaces_with_counts`, using `.get()` for TOCTOU-safety. Unknown providers now fail closed (return `[]` immediately) preventing unbounded cache keys. Made the `Session` dataclass frozen to prevent post-construction mutation. Added mtime-guarded 3-tuple caches to both `data_claude.py` and upgraded `data_kiro.py`'s `_first_prompt_cache` from a 2-tuple to a 3-tuple with mtime guard, with no negative-caching of empty results. Added cwd-required contract docstring comments to Claude adapter functions. Six test functions verify: concurrency single-flight (SC7), unknown-provider rejection, frozen-session immutability, Claude tail caching, Claude first-prompt caching, and Kiro first-prompt mtime-based refresh.

### Phase 4: Web + GUI fixes [QA] [P:2,3,5]
**Goal**: Hide disabled providers' cards, 404 unknown providers, and fix the stale action bar.
**File scope**: `src/power_atlas/web.py`, `src/power_atlas/templates/index.html`, `tests/test_web.py`.
**Covers**: SC13, SC14, SC15.

- **W1** (`web.py`): add a helper `_enabled(config, prov) -> bool` = `config.provider_settings.get(prov, {}).get("enabled", True)`. Filter the flat `(cwd,count,updated,prov)` rows by `_enabled(config, p)` **before** `_group_workspaces` in `partials_workspaces` (`:363`), `partials_pinned_workspaces` (`:298`), and `search` (`:422`) — so a disabled provider's cards + badges disappear from the "All" view (a workspace whose only provider is disabled drops out entirely). **Also** filter the `partials_sessions` provider=all merge loop (`web.py:634` iterates `data.PROVIDERS`) so expanded cards don't show disabled-provider session rows. **Documented residual**: explicitly-pinned individual sessions (`_render_pinned_sessions` `:762-781`; search pinned rows `:429-471`) from a disabled provider still render — a pin is a deliberate user choice; leave them. (User-confirmed 2026-07-07: cards+badges+expand-rows hidden, explicit pins kept — Option A.)
- **W4** (`web.py:547-551`): if `key not in data.PROVIDERS`, `raise HTTPException(status_code=404, detail="Unknown provider")` (import `HTTPException`).
- **G1** (`index.html`): `switchProvider()` (`:155`) swaps `#workspace-cards` inside a `.then()` callback — call `updateActionBar()` **inside that `.then()`, after `el.innerHTML=html`** (alongside `loadExpandedCards()`), NOT synchronously at the end of the function (which would run before the swap resolves and read stale DOM). Apply the same inside `refreshCards()`'s workspace-cards `.then()`. Selection is intentionally cleared by the swap.
- **Tests**: `test_disabled_provider_hidden_from_cards` — MUST patch `power_atlas.web.load_config` (or the config) to force `provider_settings[p].enabled=False` (several `test_web.py` tests read the *real* config; mocking discovery alone won't set the flag); assert the disabled provider's card is absent. `test_get_provider_settings_unknown_404`.

**Exit criteria**:
- [x] A disabled provider's cards/badges are absent from `GET /partials/workspaces?provider=all` and survive `/api/refresh`.
- [x] `GET /api/provider/bogus` → 404.
- [x] After a provider-tab switch that drops selected rows (headless Playwright), the action bar shows the real count (0), not a stale "1" (SC15).
- [x] `test_web.py` updated + new tests pass.

**Implementation (2026-07-08, code: 0c5040e)**
Added the `_enabled()` helper function and `HTTPException` import to `web.py`. Filtered disabled providers from workspace card listings in `partials_workspaces`, `partials_pinned_workspaces`, `search`, and the `partials_sessions` provider=all merge loop — all filtering happens BEFORE `_group_workspaces` so cards for disabled providers are completely hidden. Added a 404 guard in `get_provider_settings` that raises HTTPException when the provider key is not in `data.PROVIDERS`. Fixed the stale action bar in `index.html` by calling `updateActionBar()` inside the `.then()` callbacks of `switchProvider()` and `refreshCards()` after `el.innerHTML = html`. Added two tests: `test_disabled_provider_hidden_from_cards` and `test_get_provider_settings_unknown_404`.

### Phase 5: Launcher / icons / lifecycle hardening [QA] [P:2,3,4]
**Goal**: Close the remaining Low-severity input-hardening and CLI gaps.
**File scope**: `src/power_atlas/launcher.py`, `src/power_atlas/icons.py`, `src/power_atlas/__main__.py`, `tests/test_launcher.py`.
**Covers**: SC16, SC18, SC19, SC20, SC21, SC22, SC23.
(SC17/L3 delivered in Phase 1 via `posix=`.)

- **L1 — BOTH Windows paths** (security review: pwsh is the *preferred* terminal and was left injectable):
  - **cmd fallback** (`launcher.py:326`): after `kiro_cmd = " ".join(kiro_args)`, add `if _CMD_METACHAR_RE.search(kiro_cmd): return None` (→ existing "unsafe for cmd.exe" error). Add a comment that `_CMD_METACHAR_RE` is cmd.exe-specific (omits `;$\``) and must NOT be reused to "guard" pwsh.
  - **pwsh path** (`launcher.py:308-315`): the injection is `& {' '.join(kiro_args)}` (`:314`) — args like `x; calc.exe`, `$(calc)`, `` `calc` `` survive `shlex.split` and execute. Fix by single-quote-escaping each arg (mirror the existing cwd escaping at `:309`): `invocation = " ".join("'" + a.replace("'", "''") + "'" for a in kiro_args)` then `script += f"...; & {invocation}"`. This makes args literal (no injection) without rejecting legitimate ones. wt (`:302-306`, argv) and Linux paths are already safe — no change.
- **L5** (`launcher.py:143`): `if session_id and (len(session_id) > 128 or not _SESSION_ID_RE.match(session_id)):`.
- **L6** (`launcher.py:228`): `_TITLE_UNSAFE_RE = re.compile(r'[\"\'&|;$`]')` (adds `;$\``; existing assertions unaffected).
- **I1** (`icons.py:68-73`): add `_SAFE_COLOR_RE = re.compile(r'^#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$|^[a-zA-Z]+$')`; in `default_icon_svg`, `if color and _SAFE_COLOR_RE.match(color): svg = svg.replace(...)` — invalid colors are ignored (default stroke).
- **I2** (`icons.py:119-137`): after the whole-command and first-token attempts fail, try progressively-shorter prefixes for a space-containing path with args:
  ```python
  parts = cmd.split()
  for i in range(len(parts), 0, -1):
      p = Path(" ".join(parts[:i]))
      if p.is_file():
          return p
  ```
- **I4** (`icons.py:99`): `rel = match.group(1).lstrip("\\/")` before `cmd_dir / rel`, so `%~dp0\node.exe` resolves relative to the shim dir instead of drive-anchoring.
- **Lc1** (`__main__.py:321-334`): wrap the three flags in `group = parser.add_mutually_exclusive_group()`. Note: this also makes `--stop -f` / `--restart -f` errors (combining any two is contradictory) — broader than SC22's literal `--stop --restart`, but the intended semantics; documented.
- **H8** (`__main__.py:340-347`): the naive poll on `_read_pid()` is a **no-op on Windows** — `_stop_running()` (`:93`) deletes the PID file itself, so `_read_pid()` returns `None` immediately and the loop never waits. Capture the pid *before* stopping and poll process liveness via `_pid_alive(old_pid)` (`:37`, cross-platform); if still alive after the deadline, do NOT silently fall through to `_single_instance_guard()` (which `os._exit(0)`s on `ERROR_ALREADY_EXISTS` — a silent no-relaunch):
  ```python
  import time
  old_pid = _read_pid()
  _stop_running()
  deadline = time.monotonic() + 5.0
  while old_pid and _pid_alive(old_pid) and time.monotonic() < deadline:
      time.sleep(0.05)
  if old_pid and _pid_alive(old_pid):
      print("Old instance still running after 5s; not restarting.", file=sys.stderr)
      return
  # ... then _single_instance_guard() + _relaunch_detached()
  ```
  (`time.monotonic()` is allowed here — app code, not a workflow script.)
- **Tests**: `test_cmd_rejects_metacharacters_in_args`; `test_session_id_length_bound`; `test_sanitize_title_strips_extended`; `test_default_icon_svg_rejects_invalid_color`; `test_resolve_binary_spaced_path_with_args`; `test_resolve_cmd_to_exe_dp0_leading_backslash`.

**Exit criteria**:
- [x] cmd fallback rejects args containing `&|<>^%"` (returns the unsafe-path error); wt/pwsh paths unaffected.
- [x] Over-length session id rejected; `_TITLE_UNSAFE_RE` strips `;$\`` while existing title tests stay green.
- [x] `default_icon_svg` ignores a markup-bearing color; valid hex/named colors still applied.
- [x] `_resolve_binary` resolves `"C:\Program Files\x\app.exe --flag"`; the npm-style `.cmd` shim resolves its `.exe`.
- [x] `power-atlas --stop --restart` errors (argparse); `--restart` polls for the old instance to exit instead of a fixed sleep.
- [x] New launcher/icon/lifecycle tests pass; full `pytest` green.

**Implementation (2026-07-08, code: 22c434a)**
Hardened input handling across the launcher, icons, and CLI modules. In `launcher.py`: the cmd.exe fallback now rejects metacharacters in assembled args (not just cwd), the pwsh path single-quote-escapes each arg to prevent injection via `& {args}`, session IDs exceeding 128 characters are rejected, and the title sanitizer now strips `;`, `$`, and backtick. In `icons.py`: a `_SAFE_COLOR_RE` regex validates colors before SVG injection, `_resolve_binary` tries progressively-shorter space-separated prefixes for paths containing spaces, and `_resolve_cmd_to_exe` strips leading backslash/forward-slash from `%~dp0`-relative paths. In `__main__.py`: `--stop` and `--restart` are now mutually exclusive via argparse, and the restart logic captures the old PID before stopping, then polls with a 5-second deadline before relaunching. Six new test functions cover all hardening paths.

### Phase 6: Dead-code cleanup
**Goal**: Remove the orphaned settings UI left by the completed `/settings` removal.
**File scope**: `src/power_atlas/templates/settings.html`, `src/power_atlas/static/style.css`, `tests/test_web.py`.
**Covers**: SC24.

- **DO NOT touch `GET /api/settings`** (`web.py:521-535`) — it is a **live** endpoint used by `refreshSettings()` (`index.html:150`, part of the 260707 hot-reload work). Only the settings *page* route (`/settings` GET) and `POST /api/settings` were already removed. This phase removes ONLY the orphaned template + CSS. Do NOT grep for `/settings\b` (it matches the live `/api/settings`).
- Delete `templates/settings.html`. Remove the 14 `.settings-page`/`.settings-form`/`.settings-group`/`.settings-row`/`.settings-select`/`.settings-input`/`.settings-btn` rules from `static/style.css`.
- Grep `tests/` for `settings.html` references only; delete any lingering dead test.

**Exit criteria**:
- [x] `settings.html` gone; no `.settings-` CSS classes remain in `style.css` (`grep -n "\.settings-" src/power_atlas/static/style.css` empty).
- [x] `GET /api/settings` still present + functional (regression guard — `grep -n "api/settings" src/power_atlas/web.py` still matches; `refreshSettings()` still works).
- [x] App loads (`GET /` 200) and `pytest` green after removal.

**Implementation (2026-07-08, code: 2979bcd)**
Removed all dead `.settings-*` CSS rules from `style.css`: the `/* Settings */` block (lines 178-190: `.settings-page`, `.settings-form`, `.settings-group`, `.settings-group-title`, `.settings-row`, `.settings-select`, `.settings-input`, `.settings-btn`, `.settings-btn.primary`) and the earlier topbar `.settings-btn` rules (lines 51-52). The `settings.html` template had already been removed by `260705_CONFIGURABLE_PORT` Phase 4. Confirmed: no `.settings-` references exist in any HTML template or Python source; `GET /api/settings` endpoint remains at web.py:537; `refreshSettings()` in index.html still works. 15 lines of dead CSS removed.

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| Anchors drift again before `/qdev` (already bit us once) | Wrong line edited | Header records verified HEAD `147325f`; `/qdev` re-confirms each anchor before editing; `git diff 147325f -- <file>` is cheap. |
| `@dataclass(frozen=True)` breaks a hidden post-construction `Session` mutation | Runtime `FrozenInstanceError` | Phase 3 grep-gate; fall back to `dataclasses.replace` on `get` if any assignment found. |
| D1 lock serializes discovery | Minor added latency under contention | Single-user desktop app; discovery is the only heavy op; double-check keeps the warm path lock-free-ish. |
| W1 card-hiding is a user-visible behaviour change | Disabled provider's workspaces vanish (intended, but confirm) | Flagged as an Open item + in the report; `/qdev` confirms with user if ambiguous. |
| C6 test (`test_config.py:203-214`, not the stale `184-195` cite) encodes old behaviour | Test fails on correct fix | Phase 2 explicitly updates it (documented [BREAKS] test); `/qdev` re-confirms the anchor. |
| Extending `_TITLE_UNSAFE_RE`/cmd guard over-rejects legitimate input | A valid launch blocked | Chars added are shell-active; cmd is the last-resort fallback; existing tests assert the safe cases stay green. |
| L1 fix hardens only cmd, leaving pwsh (the *preferred* terminal) injectable | RCE via `default_args` on the common path | Phase 5 now escapes the pwsh arg-join too (Design Decisions, L1 row); wt/Linux already safe. |
| H8 poll on `_read_pid()` is a Windows no-op (PID file already deleted) | `--restart` silently skips relaunch on the primary platform | Phase 5 captures `old_pid` first + polls `_pid_alive`; non-silent on stuck old process. |
| Phase 6 grep for `/settings` matches the live `GET /api/settings` | A naive delete breaks hot-reload | Phase 6 scopes removal to `settings.html` + `.settings-` CSS; adds a regression guard that `/api/settings` survives. |
| W1 hides disabled-provider workspaces + session rows (behaviour change) | Disabled provider's discovery vanishes; explicit pins kept | Scoped decision (cards+badges+expand-rows; pins honored); **user-confirmed Option A (2026-07-07)**. |
| Security (out of plan scope): CSRF/Origin check on command-executing POSTs | Localhost drive-by could set `default_args` then launch | **RESOLVED (2026-07-07) by `260707_LAUNCH_PROFILES`** — `web.py:139-155` `same_origin_guard` middleware now rejects cross-origin / `null`-origin / both-header-absent POSTs on all routes. The follow-up the user accepted is done; no work remains here. |

## 7) Verification
- Per-phase: targeted `pytest tests/test_<area>.py` + the phase's new tests; full `pytest` before marking a phase done.
- Endpoint SCs (SC1, SC2, SC13, SC14): start `python -m uvicorn power_atlas.web:app --port 8899` and probe with `httpx` (malformed launch → error toast not 500; disabled-provider cards absent; `/api/provider/bogus` → 404).
- SC15 (GUI): headless Playwright (installed in `.venv-PowerAtlas`) — select a row, `switchProvider`, assert action-bar count == 0.
- SC7 (D1): thread-pool call-count assertion on `mod.discover_workspaces`.
- Final: full `pytest` green; `GET /` 200 after Phase 6.

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `README.md` | Only if the config-example or usage surface changes — expected **none** (internal fixes; W1 is behavioural, not documented). Confirm via doc-impact grep. | doc-table-only |
| `AGENTS.md` | None (no policy change). | doc-table-only |

## 9) Implementation Divergences from Plan
- **Phase 2 (C5)**: Added `UnicodeDecodeError` to the except clause (in addition to `OSError` + `TOMLDecodeError`) because `tomllib` raises `UnicodeDecodeError` on binary-corrupt files before reaching the TOML parser.
- **Phase 6 (SC24)**: deleted no template — `settings.html` was already removed by `260705_CONFIGURABLE_PORT` Phase 4 before this plan's re-anchor (`bb843f2`). Phase 6 removed only the orphaned `.settings-*` CSS. §1/SC24/Phase-6 scope described deleting the template; the end state (file absent) is correct, so the template deletion was a no-op.
- **Phase 5 (Lc1/SC22)**: `-f/--foreground` was deliberately kept OUT of the argparse mutually-exclusive group (only `--stop`/`--restart` are mutual). The plan text implied `--stop -f`/`--restart -f` would also error; they do not — `-f` composes with neither action path (both `return` before consulting it), which is the intended behavior.

## Review Log

### 2026-07-07 — Plan review (via /qplan Step 4)

4 personas (Architect + gap-lens, Senior engineer, Security auditor, Reliability engineer), 1 cycle. 19 findings (2 High, 7 Medium, ~10 Low); 18 auto-resolved in the plan, 1 surfaced to user (out of scope).

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | H8 restart poll on `_read_pid()` is a no-op (PID file deleted by `_stop_running` first) → never waits on Windows | Resolved — capture `old_pid` first, poll `_pid_alive(old_pid)`, non-silent on stuck process (Phase 5, Design Decisions). |
| 2 | High | L1 fix guards only cmd.exe; the *preferred* pwsh path stays injectable via `default_args` | Resolved — Phase 5 now single-quote-escapes the pwsh arg-join too. |
| 3 | Med | C3 mixed `pinned_folders` still crashes `load_config` (`.get()` on a str element, pre-sanitize) | Resolved — Phase 2 guards the migration loop with `isinstance(entry, dict)`. |
| 4 | Med | Phase 6 grep `/settings\b` matches the LIVE `GET /api/settings` hot-reload endpoint | Resolved — Phase 6 scoped to `settings.html` + `.settings-` CSS; regression guard added. |
| 5 | Med | D1 fast-path `if key in _cache` + index → `KeyError` vs concurrent `_cache.pop/clear`; SC7 overstated | Resolved — use `_cache.get()`; SC7 wording softened (Phase 3, SC7). |
| 6 | Med | C5 `.bak` copy can itself raise, violating `load_config` never-raise | Resolved — Phase 2 wraps the copy in its own try/except; always returns defaults. |
| 7 | Med | H8 still silently skips relaunch if old process is stuck past the deadline | Resolved — Phase 5 prints + returns (non-silent) instead of falling into `os._exit(0)`. |
| 8 | Med | W1 filters only cards; disabled-provider session rows still show on expand | Resolved — Phase 4 also filters `partials_sessions`; explicit pins kept (documented residual). |
| 9 | Med | W1 test can't set `enabled=False` by mocking discovery (some web tests read real config) | Resolved — Phase 4 test patches `load_config`. |
| 10 | Low | SC3 `.with_suffix(".bak")` yields `config.bak`, not `config.toml.bak` | Resolved — use `.with_name(name + ".bak")`. |
| 11 | Low | Stale anchor: C6 test at `:203-214`, not `:184-195`; repo drifted past `147325f` again | Resolved — anchor corrected; header + risk warn `/qdev` to re-verify. |
| 12 | Low | Claude cache keyed by `session_id` alone collides across cwd-derived folders | Resolved — Phase 3 keys by resolved `jsonl_path`. |
| 13 | Low | D4 mtime guard must track `.history`-vs-`.jsonl` (whichever supplied the value) | Resolved — Phase 3 records the source file's mtime. |
| 14 | Low | G1 `updateActionBar()` placed synchronously runs before the async swap resolves | Resolved — Phase 4 places it inside the `.then()` after `innerHTML`. |
| 15 | Low | `posix=False` retains quotes in tokens (behaviour change) | Resolved — Phase 1 adds a Windows quoting/backslash round-trip test. |
| 16 | Low | New data-layer tests must clear module `data._cache` (autouse fixture clears only `session_cache`) | Resolved — Phase 3 test-setup note added. |
| 17 | Low | L2 test must patch `shutil.which` + pass an existing cwd or it hits earlier guards | Resolved — Phase 1 test spec updated. |
| 18 | Low | `data_kiro_ide` retains the D2/D4 defects; caches unbounded | Noted — out-of-scope residual recorded in Phase 3 (follow-up when it leaves new/untested). |
| 19 | Low (out of scope) | No CSRF/Origin check on command-executing POST endpoints | Surfaced to user — not fixed (localhost + random port mitigate); risk row + report. |

**Verified-safe by reviewers (no change needed):** `@dataclass(frozen=True)` on `Session` (grep of `src/` + `tests/` found zero post-construction mutations); the `_extra` unknown-key approach (survives `asdict` + atomic save); the `[P:2,3,4,5]` parallel grouping (non-overlapping file scopes; Phase 1↔5 overlap is sequential); no D1 deadlock; I1 SVG-color regex closes attribute-breakout.

**Not re-dispatched for a 2nd cycle**: every High/Medium was resolved by applying the reviewers' own prescribed fix verbatim, so ready-state is reached (no unresolved High, no auto-fixable Medium remaining). A fresh panel would re-verify its own prescriptions at material token cost for negligible value.

### 2026-07-07 — Re-anchor rework after `260707_LAUNCH_PROFILES` landed (via /qplan)

`260707_LAUNCH_PROFILES_FOR_EXPORTABLE_MCP_SAFE_TERMINALS` executed through Phase 5 (HEAD `bb843f2`), rewriting `config.py` (+142), `launcher.py` (+380), `web.py` (+327), `index.html` (+29), `style.css` (+38), and the 3 test files. Re-validated every finding against the new code (no code changes made — plan-only rework).

| Finding | Status after LAUNCH_PROFILES | Action |
|---|---|---|
| **#2 CSRF** (out-of-scope follow-up) | **RESOLVED** — `same_origin_guard` middleware at `web.py:139-155` | Removed from Risk/Discovery as done. |
| C3, C5, C6, C7, C4-nested | Still valid; LAUNCH_PROFILES added a `launch_profiles`-only validation framework, not these | Re-anchored (`config.py:156-230`); C4-nested to mirror the new `_normalize_launch_profile` pattern. |
| L1 (cmd+pwsh injection) | Still valid — builder rewrite kept the sinks (pwsh `:581`, cmd `:591-595`) | Re-anchored. |
| L2, L3 | Still valid; new `default_args` API validation (`web.py:566-572`) checks only length+control chars, does NOT cover unbalanced-quote/backslash | Re-anchored (`:383/:413`); noted the validation gap. |
| L5, L6, L7 | Still valid | Re-anchored (`:26/:376`, `:495`, `:503`). |
| W1, W4 | Still valid (discovery/provider endpoints untouched) | Re-anchored (`:333-373`, `:552-554`). |
| G1 | Still valid (switchProvider action-bar gap untouched) | Re-anchored (`~:147`, afterSwap `:144`). |
| I1–I4, D1–D7, Lc1, H8, SC24 | **Untouched files** — anchors unchanged | Kept as-is (note: `icons.py`'s `web.py:886/900` caller lines moved — re-confirm). |
| `LaunchResult` gained `warning`/`used_fallback` | New fields (LAUNCH_PROFILES) | Noted so launcher fixes don't disturb them. |

Net: 1 finding dropped (CSRF, done elsewhere), the rest re-anchored; no new findings; scope otherwise unchanged. §1 Current State is the authoritative anchor source going forward; §5 phase snippets retain their pre-LAUNCH_PROFILES line refs.

### 2026-07-08 -- Implementation Review (after Phase 1, persona: Senior engineer)

Implementation health: Green.
2 findings (0 High, 0 Medium, 2 Low).
QA verification: PASS (3 regression tests exercised crash paths).

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Low | Windows quoting test exercises `shlex.split` directly, not the full `launch_session` code path. | Accepted — documentation test; the L2 test exercises the integrated path. |
| 2 | Low | No web-level integration test for `POST /api/launcher/create` with whitespace-only command. | Accepted — pre-existing gap; unit test covers the fix at the `_resolve_binary` layer. |

### 2026-07-08 -- Implementation Review (after Phases 2-5, personas: Reliability engineer, Performance engineer, Senior engineer, Security auditor)

Implementation health: Green.
12 findings (0 High, 1 Medium, 11 Low).
Parallel group [2,3,4,5] reviewed per-phase; 374 tests pass, 1 pre-existing failure.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| P3-1 | Medium | Missing concurrency test for SC7 single-flight exit criterion. | Fixed — added `TestDiscoverSingleFlight` (commit 0416990). |
| P2-1 | Low | Repeated corruption overwrites previous `.bak` (no rotation). | Accepted — best-effort recovery for single-user desktop app. |
| P2-2 | Low | No test for backup-already-exists scenario. | Accepted — standard shutil.copy2 semantics; nice-to-have. |
| P2-3 | Low | `shutil.copy2` is not atomic mid-write; caught by outer `except`. | Accepted — defense-in-depth handles this correctly. |
| P3-2 | Low | Kiro first_prompt mtime-source TOCTOU (extremely unlikely desktop). | Accepted — self-healing within 60s TTL. |
| P3-3 | Low | `data_kiro.get_session_tail` still always-caches empty (inherited). | Accepted — mtime guard makes it harmless; out of plan scope. |
| P3-4 | Low | Claude `_TAIL_CACHE_TTL=5` could spike on network paths. | Accepted — OneDrive files locally synced on Windows. |
| P4-1 | Low | G1 race in parallel swaps (pinned-section swap after updateActionBar). | Accepted — edge within an edge; primary scenario fixed. |
| P4-2 | Low | No test for disabled-provider filtering in pinned/search/sessions paths. | Accepted — same `_enabled()` helper; single-path test adequate. |
| P4-3 | Low | htmx:afterSwap global handler doesn't call `updateActionBar()`. | Accepted — pre-existing gap not in SC15 spec. |
| P5-1 | Low | `_CMD_METACHAR_RE` doesn't cover `!` (delayed expansion, off by default). | Accepted — requires user-configured delayed expansion. |
| P5-2 | Low | `_SAFE_COLOR_RE` accepts unbounded-length alpha strings from own config. | Accepted — local trust boundary; no external attacker vector. |

Per-phase review deferred to Step 9: Phase 6 — dead CSS removal (≤30 LOC, no executable code).

### 2026-07-08 -- Post-Implementation Review

Overall implementation health: Green.
Personas: Senior engineer, Security auditor, Reliability engineer, Maintainability reviewer.
4 findings (0 High, 0 Medium, 4 Low).
QA verification: PASS (13 SC-specific tests + 361 full suite pass).

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Low | cmd-metachar error message says "path contains..." not "args contain..." — slightly misleading. | Accepted — cosmetic; error correctly identifies the failure context. |
| 2 | Low | `refreshCards()` pinned-sections fetch doesn't call `updateActionBar()` post-swap. | Accepted — edge within an edge; primary SC15 scenario fixed. |
| 3 | Low | `config._extra` as monkey-patched instance attr is fragile for fresh `Config()` callers. | Accepted — documented with comment + test; all current callers use load→mutate→save. |
| 4 | Low | Cache access style inconsistency between kiro tail_cache (index) and first_prompt_cache (destructured). | Accepted — pre-existing inherited style; not a regression from this plan. |

Invoked on fully-executed plan; performed standalone holistic review with high effort (4 personas).
Cycle 2 skipped — all findings Low + no auto-fixes needed.

### 2026-07-09 — Post-archival /qreview follow-up (4 personas, fixes applied)

A standalone `/qreview` of this archived plan (Architect, Senior engineer, Security auditor, Reliability engineer), grounded against the shipped code, surfaced 12 findings (1 Medium, 11 Low). Fixes applied directly on 2026-07-09:

| Finding (persona) | Fix |
|---|---|
| **Security (Medium)** — `same_origin_guard` reflected the `Host` header, so DNS rebinding bypassed the CSRF check and reached the `/api/launcher/run` `shell=True` sink | Added a loopback Host allowlist (`_ALLOWED_HOSTS`) check to the guard (`web.py`); regression test `test_post_rejected_for_non_loopback_host`. **Supersedes the earlier "CSRF fully resolved" claim (§7 open items, Risk row) — the same-origin guard now also hardens against DNS rebinding.** |
| **Security (Low)** — non-terminal kiro-ide `.cmd`/`.bat` launch passed unescaped `default_args` through `shell=True` (`cmd /c`) | Guard `extra_args` + `cwd` with `_CMD_METACHAR_RE` before setting `shell=True` (`launcher.py`); tests in `TestNonTerminalCmdShimMetacharGuard`. |
| Architect (Low) — `_extra` re-emitted legacy `terminal_command`, defeating the save-time pop | Excluded both legacy keys via a shared `_LEGACY_KEYS` constant used by load and save (`config.py`). |
| Architect (Low) — pwsh arg-escaping duplicated `_build_powershell_invocation` | Routed the pwsh terminal branch through the shared helper (`launcher.py`). |
| Architect (Low) — `_enabled()` bypassed by two inline copies | Routed `api_available_providers` and `partials_launchers` through `_enabled()` (`web.py`). |
| Reliability (Low) — `_single_instance_guard` `os._exit(0)`d silently (both the double-launch and restart-sibling paths) | Print "PowerAtlas is already running." before exit (`__main__.py`). |
| Reliability (Low) — `launch_session`/`launch_custom` "never raises" hole: a null-byte `cwd` made `Path.exists()` raise | Wrapped the existence checks; return a `LaunchResult` error instead (`launcher.py`). |
| Senior (Low) — two undocumented plan-vs-code divergences (`settings.html`, `-f`) | Recorded in §9 above. |
| Senior (Low) — SC6 `workspace_icons={'k':5}` drop case untested | Added the drop assertion to `test_nested_bad_types_dropped` (`test_config.py`). |
| Reliability (Low) — H8 PID-reuse-within-5s edge; kiro `.history`/`.jsonl` mtime-source mismatch | **Accepted (no change).** PID-reuse is a near-impossible desktop race now made non-silent by the `_single_instance_guard` print; the mtime-source mismatch self-heals within the 60s TTL (reviewer: no change needed). |

## Harness Improvement Opportunities
- Exploration output went stale across a multi-day gap because findings were anchored to `file:line` and the code was reworked in between — suggested change: when `/qexplore` output will be handed to a *deferred* `/qplan`, record the HEAD commit SHA the anchors were verified against, so `/qplan` can cheaply detect drift (git diff since that SHA) before trusting them. *(Adopted in this plan's header; propose promoting to the `/qexplore` Step 3 persist-intent rule.)*
