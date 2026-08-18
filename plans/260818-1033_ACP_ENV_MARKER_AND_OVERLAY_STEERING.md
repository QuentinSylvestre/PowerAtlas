# ACP Env Marker and Overlay Steering

> **Date**: 2026-08-18
> **Status**: Exploring
> **Scope**: Inject PowerAtlas identity markers into all spawned kiro-cli processes, scrub inherited CLAUDE_CODE_* markers, and deliver per-session overlay steering via `_meta.kiro.steering`

---

## Intent

### Problem statement & desired outcomes

PowerAtlas spawns kiro-cli processes (ACP supervisor child and terminal-launched provider sessions) without identifying itself: the spawned sessions carry no marker distinguishing them as PowerAtlas-owned, and they inherit `CLAUDE_CODE_*`/`CLAUDECODE`/`CLAUDE_PID` markers from the PowerAtlas tray process. The absence of a client name also means `KIRO_CLI_ACP_CLIENT_NAME` is absent for ACP sessions, where the TUI sets it to `kiro-tui` to identify itself.

Desired outcomes:
1. Every process PowerAtlas spawns carries `POWER_ATLAS_SESSION=1` (all three spawn paths: ACP supervisor, terminal-launched provider, non-terminal provider).
2. ACP supervisor sessions additionally carry `KIRO_CLI_ACP_CLIENT_NAME=poweratlas`.
3. Inherited `CLAUDE_CODE_*`, `CLAUDECODE`, and `CLAUDE_PID` markers are stripped from the child environment on all three paths.
4. Every ACP session (new and loaded) receives a per-session overlay steering document via `_meta.kiro.steering` on `session/new` and `session/load` (content defined separately as a follow-on).

### Success criteria

- SC1: `acp.py`'s `_Supervisor._spawn` passes an explicit `env=` dict to `subprocess.Popen` containing `POWER_ATLAS_SESSION=1` and `KIRO_CLI_ACP_CLIENT_NAME=poweratlas`, with `CLAUDE_CODE_*`, `CLAUDECODE`, and `CLAUDE_PID` keys absent.
- SC2: `launcher.py`'s `launch_session` passes an explicit `env=` dict to both Popen calls (non-terminal path, line ~166; terminal path, line ~192) containing `POWER_ATLAS_SESSION=1`, with `CLAUDE_CODE_*`, `CLAUDECODE`, and `CLAUDE_PID` keys absent.
- SC3: `acp.py`'s `new_session` and `load_session` include `_meta: {kiro: {steering: [...]}}` in their `session/new` and `session/load` request params, with a single `poweratlas-context` steering doc (`inclusion: "always"`, content TBD — empty string or minimal placeholder for now).
- SC4: A `_build_child_env` helper (or equivalent) is extracted in `acp.py` so the env construction logic is not duplicated.
- SC5: Existing tests that assert exact params for `session/load` (test_web.py) are updated to include the `_meta` field.
- SC6: New tests assert that `POWER_ATLAS_SESSION=1` and `KIRO_CLI_ACP_CLIENT_NAME=poweratlas` appear in the env passed to the ACP Popen, and that CLAUDE_CODE_* keys are absent.

### Scope boundaries & non-goals

In scope:
- `src/power_atlas/acp.py`: `_spawn`, `new_session`, `load_session`
- `src/power_atlas/launcher.py`: `launch_session` (both Popen call sites)
- `tests/test_web.py`: update exact-params assertions for `session/new` / `session/load`
- `tests/test_launcher.py`: add env= assertions for `launch_session`

Out of scope:
- Defining the steering document content (`poweratlas-context` body) — follow-on task
- `launch_custom` / `launch_terminal` (already have a separate env= pattern; not affected)
- Any kiro-cli agent definition changes
- Any changes to the ACP protocol beyond the `_meta.kiro.steering` field already supported

---

## Exploration Discovery

<!-- Transient: /qplan folds these into the planning sections and removes this section. -->

### Existing patterns & constraints

- **`acp.py:2498-2514`** — `_Supervisor._spawn`: single `subprocess.Popen([exe, *ACP_ARGS], ..., creationflags=_CREATE_NO_WINDOW)` with NO `env=`. `ACP_ARGS = ("acp", "-a")` at `acp.py:601`. CWD is always `_neutral_cwd()` = `CONFIG_DIR/"acp-cwd"` (`acp.py:1966`).
- **`launcher.py:166`** — non-terminal path Popen: `subprocess.Popen(cli_args, **kwargs)`, kwargs has only `creationflags`/`start_new_session`, no `env=`.
- **`launcher.py:192`** — terminal path Popen: same, kwargs has only `creationflags`/`start_new_session`.
- **`launcher.py:402-405`** — `launch_custom` has the existing env-augmentation pattern: `proc_env = {**os.environ, **env} if env else None`; `kwargs["env"] = proc_env`. This is the established precedent for env= in this codebase.
- **`acp.py:3936`** — `session/new` params: `{"cwd": cwd, "mcpServers": []}`. No `_meta`.
- **`acp.py:4066-4067`** — `session/load` params: `{"sessionId": session_id, "cwd": cwd, "mcpServers": []}`. No `_meta`.
- **`interpreter.py:94`** — `os.environ["POWER_ATLAS_VENV_REEXEC"] = "1"` — the only existing os.environ write in the codebase (other than Linux DISPLAY). All spawned children currently inherit this.
- **AGENTS.md constraint** — Python changes require a PowerAtlas restart; never restart autonomously.
- **`test_web.py` exact-params assertion** — `test_web.py:~248485` asserts `session/load` params as `{"sessionId": sid, "cwd": ..., "mcpServers": []}`. Will break and must be updated.
- **No `_spawn` test coverage** — `tests/test_web.py` patches `ensure_started` with `_no_spawn` for all acp tests; the actual Popen in `_spawn` is untested. New env= tests will need a different patch strategy (patch `subprocess.Popen` directly inside `_spawn`).
- **`test_launcher.py:~TestLaunchCustom.test_env_passed`** — only existing test asserting `env=` in kwargs; applies to `launch_custom`, not `launch_session`.

### Risks & mitigations

- **R1: `session/load` steering field not surfaced at replay.** When a session is loaded/resumed, KAS replays the transcript. Client-supplied steering arrives fresh on each `session/load` call — it is not persisted with the transcript. This is the desired behavior (overlay is always current), not a risk.
- **R2: os.environ mutation contamination.** The fix must construct a new dict (`{k: v for k, v in os.environ.items() if ... } | {...}`), not mutate `os.environ`. A mutation would affect all subsequent Popen calls in the tray process, including `launch_custom` and `launch_terminal`. The dict-construction approach (matching `launch_custom`'s precedent) avoids this entirely.
- **R3: CLAUDE_CODE_* key set is not fixed.** The scrub must match a prefix (`CLAUDE_CODE_`) plus two exact names (`CLAUDECODE`, `CLAUDE_PID`). New `CLAUDE_*` vars added in future Claude Code versions would not be scrubbed unless the filter is prefix-based. Use `k.startswith("CLAUDE_CODE_") or k in {"CLAUDECODE", "CLAUDE_PID"}` as the exclusion predicate.
- **R4: `_build_child_env` helper location.** Both `acp.py` and `launcher.py` need the same scrub-and-inject logic. Options: (a) a shared helper in `launcher.py` imported by `acp.py`, or (b) duplicate the short dict comprehension in both modules. `acp.py`'s isolation boundary (imports only `config` and `launcher._SESSION_ID_RE`) blocks (a) for the acp→launcher direction but allows launcher→acp if the helper is in `launcher.py`. Per the `acp.py`/`presence.py` mutual-import ban in project memory (D: `acp.py and presence.py may not import each other — wiring goes through web.py`), there is no ban on `acp.py` importing from `launcher.py` — but the module header's stated isolation (`acp.py imports exactly two names from the package`) is a documented invariant to check before adding a third import. Safest: inline the dict comprehension in both modules (it is short), or add the helper to a new `_env.py` utility module.
- **R5: `steering` content placeholder.** Empty string `""` may not pass `content: string().max(1e6)` if there is a `.min(1)` hidden in the schema. Check `ClientSteeringDescriptorSchema` — it requires `name: string().min(1)` but `content` is `string().max(MAX_TOTAL_CONTENT_SIZE)` with no visible minimum. A one-line comment placeholder (`# PowerAtlas context — content TBD`) is safer than an empty string.

### Resolved decisions

- Q1: Scrub CLAUDE_CODE_* from env — A: Yes, with scrub extended to all three spawn paths (acp._spawn, launcher non-terminal, launcher terminal) — Decision: all three Popen calls get explicit env= dict with CLAUDE_CODE_* stripped and POWER_ATLAS_SESSION=1 injected.
- Q2: Session ownership marker — A: POWER_ATLAS_SESSION=1 on all three paths; KIRO_CLI_ACP_CLIENT_NAME=poweratlas on ACP _spawn only — Decision: two separate markers with different scopes.
- Q3: Overlay steering delivery mechanism — A: Option B, `_meta.kiro.steering` on session/new and session/load — Decision: inject steering via the wire protocol, no filesystem writes to workspace directories.
- Q4: Overlay steering content — A: Defined as a follow-on; placeholder for now — Decision: SC3 ships with empty/placeholder content.

### Open items

- OI1: `_build_child_env` helper location — inline in both modules vs a shared `_env.py` utility. Deterministic (no user input needed): check `acp.py`'s module-level import comment for its exact stated invariant, then decide. If the comment reads "imports exactly two names", adding a third import is a tracked violation; use inline or `_env.py`. `/qplan` should resolve this.
- OI2: Whether `content: ""` passes KAS's `ClientSteeringDescriptorSchema` validation — deterministic from source. The schema at `acp-server.js` shows `content: external_exports2.string().max(MAX_TOTAL_CONTENT_SIZE)` with no `.min()`. An empty string should be accepted, but a one-line placeholder is safer and more discoverable.

### Recommended approach

Three focused changes, all additive:

1. **`acp.py` — `_spawn`**: Add `env=` to the `subprocess.Popen` call. Build the env dict as `{k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_CODE_") and k not in {"CLAUDECODE", "CLAUDE_PID"}} | {"KIRO_CLI_ACP_CLIENT_NAME": "poweratlas", "POWER_ATLAS_SESSION": "1"}`. Consider extracting to a `_build_child_env()` module-level function for testability.

2. **`launcher.py` — `launch_session`**: Add `env=` to both Popen calls (lines 166 and 192). Build via the same pattern but without `KIRO_CLI_ACP_CLIENT_NAME` (ACP-only). A helper `_build_child_env()` in `launcher.py` can serve both call sites.

3. **`acp.py` — `new_session` and `load_session`**: Extend the params dict with `"_meta": {"kiro": {"steering": [{"name": "poweratlas-context", "inclusion": "always", "content": "# PowerAtlas context — content TBD"}]}}`. A module-level `_OVERLAY_STEERING` constant holds the steering list so it can be updated without touching the call sites.

Test updates:
- `test_web.py`: update `session/load` and `session/new` exact-params assertions to include `_meta`.
- `test_launcher.py`: add assertions on `kwargs["env"]` for `launch_session`'s Popen calls — verify `POWER_ATLAS_SESSION=1` present and `CLAUDECODE` absent.
- `test_web.py` (new): patch `subprocess.Popen` inside `_spawn` directly and assert env= contains the expected keys.

### QA environment

- PowerAtlas runs from `.venv-PowerAtlas/Scripts/power-atlas` (or `python -m power_atlas`). Requires a restart to pick up Python changes.
- ACP surface: `http://127.0.0.1:<port>/acp` — open in browser after restart.
- Verification of env vars: after the change, inspect a running `kiro-cli acp` child process via `Get-Process kiro-cli | ForEach-Object { (Get-WmiObject Win32_Process -Filter "ProcessId=$($_.Id)").Environment }` or instrument `_spawn` to log the env keys.
- Verification of overlay steering: open a new ACP session in PowerAtlas, check that `poweratlas-context` appears in the session's `steering_inclusion` messages in its `messages.jsonl`.
- Test suite: `.venv-PowerAtlas\Scripts\pytest tests/test_web.py tests/test_launcher.py -x` (anchored to HEAD `eccfa1a`).

## Harness Improvement Opportunities

- The probe-before-interview flow (decidable-by-probe list from sub-agents) worked well; the only friction was that `acp-server.js` binary search required multiple round-trips to locate the exact method body. A pattern index of key KAS method names and their byte offsets (stored once, invalidated on version bump) would save 3-4 tool calls per steering-related exploration.
