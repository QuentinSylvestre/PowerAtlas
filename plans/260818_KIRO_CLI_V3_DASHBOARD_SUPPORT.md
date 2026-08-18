# kiro-cli v3 Dashboard Support

> **Date**: 2026-08-18
> **Status**: Exploring  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Scope**: Add `kiro-cli-v3` as a built-in provider to the main dashboard — session discovery, display, new/resume launches, and live status dots.

---

## Intent

### Problem statement & desired outcomes

kiro-cli v3 stores sessions in a different layout (`~/.kiro/sessions/<workspace-hash>/sess_<uuid>/`) that PowerAtlas does not scan. The store has 85 sessions on this machine, 62 created in the last 7 days — it is actively growing and completely invisible in the dashboard. Users running `kiro-cli --v3` or `kiro-cli chat --agent-engine v3` accumulate sessions that cannot be resumed or inspected from PowerAtlas.

The desired outcome is a `kiro-cli v3` provider in the main dashboard — same workspace cards, same session rows, same status dots — such that a user's v3 sessions are as visible and launchable as their v2 sessions. The `/acp` surface stays v2-only; this change is confined to the main dashboard.

### Success criteria

1. v3 workspaces (derived from `workspacePaths[0]` in each session's `session.json`) appear in the Workspaces panel alongside v2 and other providers.
2. v3 sessions are listed per workspace with title, timestamps, first/last prompt extracted from `messages.jsonl`.
3. "New session" launches `kiro-cli chat --agent-engine v3 --trust-tools *` in a terminal at the selected workspace.
4. "Resume" launches `kiro-cli chat --agent-engine v3 --trust-tools * --resume-id sess_<uuid>` in a terminal.
5. Live status dots appear for v3 sessions resumed via PowerAtlas (argv-match detection; no lock-file sidecar — same limitation as v2 fresh sessions, documented).
6. v2 sessions, ACP, and all other providers are unaffected.
7. New `tests/test_data_kiro_v3.py` passes; existing test suite passes.
8. ROADMAP.md v3 entry removed (the feature is now shipped).

### Scope boundaries & non-goals

**In scope:**
- New `data_kiro_v3.py` adapter implementing the full 7-function provider interface
- `Session` dataclass gains `extra_fields: dict` field; all construction sites updated; v3 populates `agentMode` there
- `data.py` PROVIDERS dict gains `"kiro-cli-v3": data_kiro_v3`
- `launcher.py` gains `kiro-cli-v3` in `_PROVIDER_BINARY`, `_PROVIDER_DISPLAY`, `_PROVIDER_TERMINAL`, and a dedicated `_build_provider_args` branch (`--agent-engine v3 --trust-tools *`, `--resume-id` for resume)
- `web.py` gains `kiro-cli-v3` in `PROVIDER_COLORS` (`#7138cc`), `PROVIDER_DISPLAY_NAMES` (`"kiro-cli v3"`), `_PROVIDER_BINARY_DISPLAY`; hardcoded `{"kiro-cli","claude-code"}` sets at lines 459, 476, 3216 expanded to include `"kiro-cli-v3"`
- `presence.py` `_PROVIDER_SPECS` gains `"kiro-cli-v3"` entry (same binary/flag as `"kiro-cli"`)
- `status_classifier.py` `_resolve_jsonl_path_uncached` gains a dedicated `kiro-cli-v3` branch (goes directly to workspace-hash scan, skips `SESSION_DIR`)
- `tests/test_data_kiro_v3.py` — new test file for the adapter
- `test_data.py`, `test_launcher.py` — updated for `Session.extra_fields` and new provider args
- ROADMAP.md — remove the v3 parked item

**Not in scope:**
- `/acp` v3 support (ACP stays v2-only; `ACP_ARGS = ("acp", "-a")` unchanged)
- `--mode` selection UI (users can add `--mode vibe` via `default_args` in config)
- `agentMode` rendering in session rows (stored in `extra_fields`, not displayed)
- Liveness refinement beyond argv-matching (spike deferred post-implementation)
- Multi-workspace sessions (0/85 observed; `workspacePaths[0]` is the cwd)

---

## Exploration Discovery

<!-- Transient: /qplan folds these into the planning sections and removes this section. -->

### 4. Existing patterns & constraints

- **Provider adapter interface** (7 functions, all required): `is_available()`, `discover_workspaces() -> list[tuple[str,int,str]]`, `load_sessions(cwd) -> tuple[list[Session], dict[str,_FileInfo]]`, `refresh_stale_entries_for_cwd(norm_cwd, old_stats) -> bool`, `find_session_workspace(session_id) -> str|None`, `get_session_tail(session_id, cwd, max_lines) -> list[str]`, `get_first_prompt(session_id, cwd) -> str`. Source: `data.py:103-308`.
- **Session dataclass** (`data.py:31`): frozen, shared by all providers. Adding `extra_fields: dict = field(default_factory=dict)` — mutable field on frozen dataclass is valid Python; `Session` is never used as a dict key in the codebase (confirmed by search).
- **v3 session.json keys** are camelCase and do not map to Session fields without explicit translation: `id` → `session_id`, `workspacePaths[0]` → `cwd`, `createdAt` → `created_at`, `lastModifiedAt` → `updated_at`, `title` → `title` (same), `agentMode` → `extra_fields["agentMode"]`.
- **v3 messages.jsonl envelope**: `{"id","timestamp","payload":{"type":"user"|"assistant"|"tool_call"|...}}`. Content at `payload.content`. Entirely different from v2's `{"version":"v1","kind":"Prompt"|"AssistantMessage","data":{}}`.
- **`status_classifier.classify_kiro_v3()`** (`status_classifier.py:257`) already handles v3 JSONL classification — no changes to the classifier functions themselves.
- **`_is_v3_format()`** (`status_classifier.py:435`) already detects v3 format by checking `payload.type`.
- **`_build_provider_args()` else-branch** (`launcher.py:84`) currently handles any unknown provider as if it were kiro-cli — a `kiro-cli-v3` provider without an explicit branch would silently build wrong args (no `--agent-engine v3`, no `--trust-tools *`).
- **`PROVIDER_BADGES`** (`web.py:95`) is defined but never consumed anywhere — no entry needed for new provider.
- **`Session` construction sites**: `data_kiro.py`, `data_claude.py`, `data_kiro_ide.py`, all three test files — all need `extra_fields={}` added (can be keyword-defaulted on the dataclass so existing construction sites without the arg still work).
- **Cache patterns** from `data_kiro.py`: `_meta_cache` (path→(mtime,size,dict)), `BoundedCache` for prompts (2048 entries), `_tail_cache` TTL 5s, `_first_prompt_cache` TTL 60s. v3 adapter should follow same patterns.
- **Cache invalidation**: v3 uses root mtime (`~/.kiro/sessions/`) + per-hash-dir mtime. Root catches new workspace-hash dirs; hash-dir catches new sessions within existing workspaces. Stat cost is negligible.
- **`_cwd_to_files()` in `data_kiro.py`** is keyed on `SESSION_DIR` (v2 dir) mtime — entirely inapplicable to v3. v3 adapter needs its own index.
- **`_extract_content()` in `data_kiro.py`** matches `obj.get("kind")` — returns `""` for every v3 line. v3 adapter needs a separate extraction function matching `payload.type`.
- **No `.history` file in v3** — first prompt must come from `messages.jsonl` head scan only.
- **Sub-agent filtering**: v3 has no `parent_session_id`. Every `sess_*/session.json` is a user-initiated session — no filtering needed (sub-agents live in `sub-executions/*.jsonl` inside the parent dir, never as top-level `sess_*` dirs).
- **`publish.cursor` and `publish-sub.cursor`** appear alongside `session.json` + `messages.jsonl` in some sessions — telemetry cursors, should be ignored by the adapter.
- **Test patterns**: monkeypatch on module-level `SESSIONS_DIR` / `SESSION_DIR` constant, `_clear_cache` autouse fixture, `_bump_mtime()` helper for mtime-keyed cache tests, `_reset_kiro_caches()` function to clear all module-level caches. `test_data_kiro_v3.py` must follow the same discipline.
- **Mtime-race flaky family**: known in `test_data.py` — `_bump_mtime()` calls are mandatory when writing then immediately reading the same file. New tests must use this helper.

### 5. Risks & mitigations

- **`--trust-tools *` wildcard semantics**: probed — `--trust-tools "*"` launches KAS without a flag-rejection error. Risk of a future kiro-cli version changing wildcard behavior: low, mitigated by the config-driven `default_args` escape hatch (users can override in `config.toml`).
- **`_SESSION_ID_RE` and `sess_` prefix**: probed — `\w` in the regex matches underscore, so `sess_<uuid>` passes validation. Confirmed.
- **False-positive live dots from shared binary**: `kiro-cli --resume-id <bare-uuid>` (v2) won't match a `kiro-cli-v3` session because v2 session IDs are bare UUIDs and v3 IDs are `sess_<uuid>` — the format mismatch is the natural disambiguator.
- **`extra_fields` on frozen dataclass**: Python allows mutable fields on frozen dataclasses (only the field reference is frozen, not the contained object). `Session` is never hashed in production code — no runtime risk.
- **v3 store growth**: 23 workspace-hash dirs, 85 sessions today. Scan at `discover_workspaces()` must walk all hash dirs; caching with root + per-hash mtime prevents re-scanning on every call.
- **`web.py` hardcoded sets**: if any of the three `{"kiro-cli","claude-code"}` sets are missed, `kiro-cli-v3` status polling is silently absent. All three must be updated in one commit.

### 6. Resolved decisions

- Q1: What trust flag for v3 launches? — A: Hard-code `--trust-tools *` in launch args — Decision: hard-coded.
- Q2: `--agent-engine v3` vs `--v3`? — A: `--agent-engine v3` — Decision: use `--agent-engine v3`.
- Q3: Liveness detection approach? — A: Add `kiro-cli-v3` to `_PROVIDER_SPECS` (same binary/flag as `kiro-cli`) — Decision: add it, spike post-implementation to refine.
- Q4: Path resolution branch in `status_classifier.py`? — A: Separate `kiro-cli-v3` branch going directly to workspace-hash scan — Decision: dedicated branch.
- Q5: Display name and color? — A: `"kiro-cli v3"`, `#7138cc` — Decision: same purple as v2.
- Q6: Cache invalidation strategy? — A: Root mtime + per-hash-dir mtime — Decision: Option A (root + per-hash).
- Q7: Expand `poll_providers` hardcoded sets? — A: Yes, include in scope — Decision: expand all three sets.
- Q8: `agentMode` handling? — A: Add `extra_fields: dict` to `Session`, populate `agentMode` for v3 — Decision: store only, no UI rendering.
- Q9: `--mode` flag in launch args? — A: Omit — Decision: users can add via `default_args`.
- Q11: Remove ROADMAP.md v3 entry? — A: Yes — Decision: remove when this ships.
- Q12: New test file? — A: Yes, `tests/test_data_kiro_v3.py` — Decision: create it.
- Q14: Render `agentMode` in session row? — A: No, store only — Decision: A.

### 7. Open items

- **Liveness spike**: after implementation, run a live v3 session and verify the status dot appears and transitions correctly. No code changes expected; this is an observational check.
- **`empty_msgs` entry in `web.py`**: `web.py:3399` has a per-provider empty-state message dict. Adding `"kiro-cli-v3"` there is cosmetic (falls back to a generic message) but worth doing for completeness. Execution-contingent: confirm the dict still exists at that line when implementing.

### 8. Recommended approach

**Phase 1 — Data layer**: Create `data_kiro_v3.py` implementing all 7 provider interface functions. Key implementation notes:
- `V3_SESSIONS_ROOT = Path.home() / ".kiro" / "sessions"` (excludes `cli/`)
- `discover_workspaces()`: walk hash dirs, read `sess_*/session.json`, group by `workspacePaths[0]`, sort by `lastModifiedAt`
- `load_sessions(cwd)`: find matching hash dir via `workspacePaths[0]` normalization; read each session's `session.json` + head/tail of `messages.jsonl`; use root mtime + hash-dir mtime for index invalidation
- `_extract_v3_content(payload)`: check `payload["type"] == "user"|"assistant"`, extract from `payload["content"]` (string or `[{"type":"text","text":"..."}]` blocks)
- No `.history` file; first prompt from `messages.jsonl` head scan (first `user` payload)
- Sub-agent sessions (`sub-executions/`) are inside parent dirs, not top-level — no filtering needed
- Ignore `publish.cursor` and `publish-sub.cursor`
- Add `extra_fields={"agentMode": data.get("agentMode","")}`

**Phase 2 — Session dataclass**: Add `extra_fields: dict = field(default_factory=dict)` to `Session` in `data.py`. Update all construction sites in `data_kiro.py`, `data_claude.py`, `data_kiro_ide.py` to pass `extra_fields={}` explicitly (or rely on default).

**Phase 3 — Provider registration**: Add `from . import data_kiro_v3` and `"kiro-cli-v3": data_kiro_v3` to `data.py`.

**Phase 4 — Launcher**: Add `kiro-cli-v3` to `_PROVIDER_BINARY`, `_PROVIDER_DISPLAY`, `_PROVIDER_TERMINAL` in `launcher.py`. Add explicit `kiro-cli-v3` case to `_build_provider_args()`: `[binary, "chat", "--agent-engine", "v3", "--trust-tools", "*"]` + `["--resume-id", session_id]` if resuming.

**Phase 5 — Web layer**: Add `kiro-cli-v3` entries to `PROVIDER_COLORS`, `PROVIDER_DISPLAY_NAMES`, `_PROVIDER_BINARY_DISPLAY` in `web.py`. Expand the three hardcoded `{"kiro-cli","claude-code"}` sets to include `"kiro-cli-v3"`. Add `"kiro-cli-v3"` to `empty_msgs` dict.

**Phase 6 — Presence + status classifier**: Add `"kiro-cli-v3"` to `_PROVIDER_SPECS` in `presence.py`. Add `kiro-cli-v3` branch in `_resolve_jsonl_path_uncached()` in `status_classifier.py` (go directly to `_V3_SESSIONS_ROOT` walk, skip `SESSION_DIR`).

**Phase 7 — Tests**: Create `tests/test_data_kiro_v3.py` with `TestKiroV3IsAvailable`, `TestKiroV3DiscoverWorkspaces`, `TestKiroV3LoadSessions`, `TestKiroV3GetSessionTail`, `TestKiroV3GetFirstPrompt`, `TestKiroV3RefreshStale`, `TestKiroV3FindSessionWorkspace`. Update `test_data.py` for `Session.extra_fields`. Update `test_launcher.py` for `kiro-cli-v3` args. Update `test_web.py`/`test_data.py` for status classifier branch. Follow `_bump_mtime()` discipline.

**Phase 8 — ROADMAP cleanup**: Remove the v3 parked item from `plans/ROADMAP.md`.

### 9. QA environment

- **Runtime verification**: start PowerAtlas (`.venv-PowerAtlas\Scripts\power-atlas`), open dashboard at `http://127.0.0.1:<port>`. v3 workspaces should appear in the Workspaces panel. Click a workspace to see its v3 sessions. Click "Resume" on a v3 session to verify the terminal opens with `--agent-engine v3 --trust-tools * --resume-id sess_<uuid>`. Click "New session" on a v3 workspace to verify terminal opens with `--agent-engine v3 --trust-tools *` only.
- **Test suite**: `.venv-PowerAtlas\Scripts\python -m pytest tests/` (full suite). `node tests/acp_page.test.mjs` for template-inline-script coverage (run when `web.py` template changes are made).
- **Live status dot**: resume a v3 session via PowerAtlas, observe the status dot on the session row. Requires a running kiro-cli v3 process.
- **V3 session data on this machine**: `~/.kiro/sessions/` — 23 workspace-hash dirs, 85 sessions, 62 created in the last 7 days. Realistic test data without mocking.

## Harness Improvement Opportunities

- The probe gate instruction says "run every probe that is read-only and available" but the `kiro-cli` interactive probes hung (no `--no-interactive` equivalent for v3 resume). Cost: one tool-use cancellation. Suggested change: add a note to the probe gate that interactive CLIs cannot be probed non-interactively; skip those probes and record as open items.
