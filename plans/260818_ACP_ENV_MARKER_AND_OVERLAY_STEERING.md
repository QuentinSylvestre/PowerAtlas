# ACP Env Marker and Overlay Steering

> **Date**: 2026-08-18
> **Status**: Complete
> **Last Updated**: <set by /qclose at archival>
> **Scope**: Inject PowerAtlas identity markers into all spawned kiro-cli processes, scrub inherited CLAUDE_CODE_* markers, and deliver per-session overlay steering via `_meta.kiro.steering`
> **Estimated effort**: 2–4 hours

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

- SC-1: `acp.py`'s `_Supervisor._spawn` passes an explicit `env=` dict to `subprocess.Popen` containing `POWER_ATLAS_SESSION=1` and `KIRO_CLI_ACP_CLIENT_NAME=poweratlas`, with `CLAUDE_CODE_*`, `CLAUDECODE`, and `CLAUDE_PID` keys absent.
- SC-2: `launcher.py`'s `launch_session` passes an explicit `env=` dict to both Popen calls (non-terminal path ~line 166; terminal path ~line 192) containing `POWER_ATLAS_SESSION=1`, with `CLAUDE_CODE_*`, `CLAUDECODE`, and `CLAUDE_PID` keys absent.
- SC-3: `acp.py`'s `new_session` and `load_session` include `_meta: {kiro: {steering: [...]}}` in their `session/new` and `session/load` request params, verified by exact-params assertions in tests for both call sites.
- SC-4: A `_build_child_env` helper is defined in both `acp.py` and `launcher.py` (identical scrub logic, intentionally different signatures — see § 3 Design Decisions).
- SC-5: The `session/load` exact-params assertion in `test_web.py` (~line 5186–5190) is updated to include the `_meta` field; a new `session/new` exact-params assertion is added.
- SC-6: New tests assert that `POWER_ATLAS_SESSION=1` and `KIRO_CLI_ACP_CLIENT_NAME=poweratlas` appear in the env passed to the ACP Popen, that `CLAUDECODE` and `PATH` (base-env passthrough) are correctly handled, and that CLAUDE markers are absent.

### Scope boundaries & non-goals

In scope:
- `src/power_atlas/acp.py`: `_spawn`, `new_session`, `load_session`
- `src/power_atlas/launcher.py`: `launch_session` (both Popen sites)
- `tests/test_web.py`: update exact-params assertions; add `_spawn` env test
- `tests/test_launcher.py`: add env= assertions for `launch_session`

Out of scope:
- Defining the steering document content (`poweratlas-context` body) — follow-on task
- `launch_custom` / `launch_terminal` (already have separate env= handling; not affected)
- Any kiro-cli agent definition changes
- Any changes to the ACP protocol beyond the `_meta.kiro.steering` field

---

## 1) Current State

**`acp.py` — `_Supervisor._spawn` (`acp.py:2498–2514`)**: The single `subprocess.Popen` call spawns `["kiro-cli", "acp", "-a"]` (constants `KIRO_BINARY` at `acp.py:600`, `ACP_ARGS` at `acp.py:601`) with CWD `_neutral_cwd()` = `CONFIG_DIR/"acp-cwd"` (`acp.py:1966`). No `env=` argument — child inherits full `os.environ`. `acp.py` does not currently `import os`; this plan adds the import.

**`launcher.py` — `launch_session` (`launcher.py:107–195`)**: Two Popen calls:
- Non-terminal path: `launcher.py:166` — `subprocess.Popen(cli_args, **kwargs)` where `kwargs` has only `creationflags`/`start_new_session`. No `env=`.
- Terminal path: `launcher.py:192` — same shape. No `env=`.

**Existing env-augmentation precedent (`launcher.py:402–405`)**: `launch_custom` already uses `proc_env = {**os.environ, **env} if env else None; kwargs["env"] = proc_env` — the established pattern for env= in this codebase. `launcher.py` already `import os` at line 3.

**`acp.py` protocol calls**:
- `new_session` at `acp.py:3936`: `_request("session/new", {"cwd": cwd, "mcpServers": []})` — no `_meta`.
- `load_session` at `acp.py:4066–4067`: `_request("session/load", {"sessionId": session_id, "cwd": cwd, "mcpServers": []})` — no `_meta`.

**KAS schema (confirmed from `acp-server.js`)**: `_meta.kiro.steering` is in `KiroSessionMetaSchema`. `ClientSteeringDescriptorSchema` accepts `name: string().min(1)`, `inclusion: enum(["always","fileMatch","manual"])`, optional `fileMatchPattern`, `content: string().max(1e6)`. Both `session/new` and `session/load` feed through `createSessionState(..., kiroMeta?.steering ? validateAndConvertClientSteering(...) : undefined, ...)` in `acp-server.js` — confirmed at both call sites (~byte offset 21257144 for newSession, ~21334885 for loadSession).

**Isolation boundary (`acp.py:15–28`)**: `acp.py` imports `config.CONFIG_DIR`, `launcher._SESSION_ID_RE`, and `data_kiro` from the package. The module header states the constraint as "imports exactly two names" from `config` and `launcher`; the exit criterion greps for those two module names. Adding a `_build_child_env` import from `launcher` would add a third name from `launcher` — violating the stated invariant. The function is duplicated instead.

**Test coverage**:
- `_spawn` is fully bypassed in all `test_web.py` tests via `patch.object(_Supervisor, "ensure_started", _no_spawn)` (`test_web.py:4950`). The actual `subprocess.Popen` in `_spawn` has no test coverage for its kwargs.
- One exact-params assertion exists for `session/load` at `test_web.py:5186–5190` — will need updating.
- No exact-params assertion for `session/new` — will be added (SC-5).
- `tests/test_launcher.py`: `test_env_passed` (~line 529) is the only test asserting env= kwargs — applies only to `launch_custom`. `launch_session` Popen kwargs are not asserted for env=.

**ROADMAP tracking**: The CLAUDE_CODE_* inheritance is tracked as a known open item at `plans/ROADMAP.md` under Platform: "Launched sessions inherit PowerAtlas's environment — sessions inherit `CLAUDE_CODE_CHILD_SESSION` and other markers from the tray process; three launch paths disagree." This plan closes it.

## 2) Goal

Add an explicit `env=` dict to all three Popen call sites (scrubbing CLAUDE markers, injecting PowerAtlas identity vars), and extend `session/new` and `session/load` params to include a `_meta.kiro.steering` overlay doc — all verified by new and updated tests.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| `_build_child_env` helper location | Inline in both `acp.py` and `launcher.py` as a module-level function | Shared in `config.py`; shared in a new `_env.py` | `acp.py`'s isolation boundary restricts imports from `launcher` to the two named names; adding `_build_child_env` as a third would violate the stated invariant. The function is ~8 lines; duplication cost is minimal. See Follow-up #3 for extraction path. |
| `_build_child_env` signature intentional difference | `acp.py`: `(extra: dict[str, str]) -> dict[str, str]` (required). `launcher.py`: `(extra: dict[str, str] \| None = None) -> dict[str, str]` (optional, defaults to empty). | Identical signatures in both | `acp.py` always passes `{"KIRO_CLI_ACP_CLIENT_NAME": "poweratlas"}`; making `extra` required there makes the omission a type error, not a silent default. `launcher.py` call sites pass no extra; optional matches naturally. The difference is intentional — both helpers carry a docstring noting the intent. Each copy also carries a comment: `# NOTE: A copy of this function lives in acp.py / launcher.py (isolation boundary prevents shared import). Keep scrub lists in sync.` |
| CLAUDE_CODE_* scrub predicate | `k.startswith("CLAUDE_CODE_") or k in {"CLAUDECODE", "CLAUDE_PID"}` | Exact-name list; `k.startswith("CLAUDE")` (too broad) | Prefix match covers the measured set (`CLAUDE_CODE_CHILD_SESSION`, `CLAUDE_CODE_SESSION_ID`, `CLAUDE_CODE_BRIDGE_SESSION_ID`); exact names for `CLAUDECODE` and `CLAUDE_PID` which lack the prefix. |
| POWER_ATLAS_SESSION scope | All three spawn paths | ACP-only | Identifies PowerAtlas-launched sessions regardless of transport. |
| KIRO_CLI_ACP_CLIENT_NAME scope | ACP `_spawn` only | All three paths | Names the ACP client identity — meaningless for terminal launches. |
| `_OVERLAY_STEERING` immutability | Tuple of frozen dicts — `_OVERLAY_STEERING: tuple[dict, ...] = ({"name": ..., ...},)` | Plain list | `_build_session_meta()` returns a reference to `_OVERLAY_STEERING` items; a mutable list would be a shared-state trap if any code path appended to it. A tuple cannot be `.append()`'d. The inner dicts are still mutable, but one-line content strings are not modified in practice. |
| `_build_kas_session_params()` naming | Named `_build_kas_session_params()` | `_build_session_meta()` | The key `_meta` is a KAS protocol field from `KiroSessionMetaSchema`, not a PA internal. `_build_kas_session_params` makes the KAS origin visible at the call site. |
| Overlay steering placeholder content | `"PowerAtlas context — content TBD"` (no leading `#`) | `"# PowerAtlas context — content TBD"` | Content is plain text per `ClientSteeringDescriptorSchema` — no markdown format requirement. Leading `#` would suggest markdown convention where none applies. |
| `env=` insertion in `launch_session` | After the entire `if sys.platform == "win32": … else: …` block and before `subprocess.Popen` | Inside the platform block | Both branches build a `kwargs` dict; `env=` must be set after all `kwargs` mutations to cover both the `win32` and `else` paths with a single `kwargs["env"] = _build_child_env()` line. |

## 4) External Dependencies & Costs

No external dependencies, no cost impact, no infra changes.

## 5) Implementation Phases

### Phase 1: `acp.py` — env injection and overlay steering [QA] [P:2]

**Goal**: Add `import os`, `_build_child_env()` helper, and `env=` to `_spawn`; add `_OVERLAY_STEERING` constant and `_build_kas_session_params()` helper; extend `new_session` and `load_session` params; update and add tests.

**File scope**: `src/power_atlas/acp.py`, `tests/test_web.py`

**Covers**: SC-1, SC-3, SC-4 (acp.py helper), SC-5, SC-6

**Changes to `src/power_atlas/acp.py`**:

**Step 1 — add `import os`** at the stdlib imports block (before the `import` of `asyncio` or wherever other stdlib imports are; confirm the block, do not assume a position).

> Exit criterion: `grep "^import os" src/power_atlas/acp.py` returns a result.

**Step 2 — after the `ACP_ARGS` constant** (`acp.py:601`), add:

```python
# Keys stripped from the child env to prevent marker leakage from the PowerAtlas
# tray process into spawned kiro-cli ACP sessions.
# CLAUDE_CODE_* covers CLAUDE_CODE_CHILD_SESSION, CLAUDE_CODE_SESSION_ID, etc.
# CLAUDECODE and CLAUDE_PID are separate names without the CLAUDE_CODE_ prefix.
# NOTE: A copy of this function lives in launcher.py (isolation boundary prevents
# shared import). Keep _SCRUB_PREFIXES and _SCRUB_EXACT in sync with that copy.
_SCRUB_PREFIXES = ("CLAUDE_CODE_",)
_SCRUB_EXACT = frozenset({"CLAUDECODE", "CLAUDE_PID"})


def _build_child_env(extra: dict[str, str]) -> dict[str, str]:
    """Build the environment dict for the spawned kiro-cli ACP child process.

    Strips CLAUDE_CODE_* / CLAUDECODE / CLAUDE_PID markers inherited from the
    PowerAtlas tray process, and injects PowerAtlas identity vars. ``extra``
    is merged last so call-site additions override same-named keys from os.environ.
    ``extra`` is required (not optional) because every ACP spawn always passes
    at least KIRO_CLI_ACP_CLIENT_NAME.
    """
    base = {
        k: v for k, v in os.environ.items()
        if not any(k.startswith(p) for p in _SCRUB_PREFIXES)
        and k not in _SCRUB_EXACT
    }
    return {**base, "POWER_ATLAS_SESSION": "1", **extra}


# Overlay steering delivered to every ACP session via _meta.kiro.steering.
# Must conform to ClientSteeringDescriptorSchema in acp-server.js (kiro-cli 2.16.x+).
# Required keys: name (str, non-empty), inclusion (enum: always|fileMatch|manual),
# content (str, max 1 MB). Content is a placeholder; body defined as a follow-on task.
_OVERLAY_STEERING: tuple[dict, ...] = (
    {
        "name": "poweratlas-context",
        "inclusion": "always",
        "content": "PowerAtlas context — content TBD",
    },
)


def _build_kas_session_params() -> dict[str, Any]:
    """Build the _meta.kiro fragment for session/new and session/load requests.

    The _meta.kiro key is a KAS protocol field accepted by KiroSessionMetaSchema
    in acp-server.js. The steering list is delivered as clientSteeringDocs via
    createSessionState(..., kiroMeta?.steering ...).
    """
    return {"_meta": {"kiro": {"steering": list(_OVERLAY_STEERING)}}}
```

> Ensure `from typing import Any` (or `Any` from wherever it is already imported in `acp.py`) is available for the return type annotation.

**Step 3 — `_Supervisor._spawn` (`acp.py:2498`)**, add `env=` to the `subprocess.Popen` call:

```python
proc = subprocess.Popen(
    [exe, *ACP_ARGS],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL,
    cwd=str(cwd),
    text=True,
    encoding="utf-8",
    errors="replace",
    bufsize=1,
    env=_build_child_env({"KIRO_CLI_ACP_CLIENT_NAME": "poweratlas"}),
    creationflags=_CREATE_NO_WINDOW,
)
```

**Step 4 — `new_session` (`acp.py:3936`)**, extend params:

```python
result = await self._request(
    "session/new",
    {"cwd": cwd, "mcpServers": [], **_build_kas_session_params()},
)
```

**Step 5 — `load_session` (`acp.py:4066–4067`)**, extend params:

```python
await self._request(
    "session/load",
    {"sessionId": session_id, "cwd": cwd, "mcpServers": [], **_build_kas_session_params()},
)
```

**Changes to `tests/test_web.py`**:

**Step 6 — Update `session/load` exact-params assertion** (`test_web.py:5186–5190`):

```python
assert calls == [(
    "session/load",
    {"sessionId": sid, "cwd": str(Path(store).resolve()),
     "mcpServers": [], **acp_mod._build_kas_session_params()},
)]
```

**Step 7 — Add `session/new` exact-params test** (in or near the existing new-session test class — find the class that calls `new_session` with a `fake_request` spy). Add an assertion that `session/new` params include `_meta`:

```python
# Inside a test that captures _request calls for new_session:
assert calls[0] == (
    "session/new",
    {"cwd": str(some_cwd), "mcpServers": [], **acp_mod._build_kas_session_params()},
)
```

Find the existing `TestAcpNewSession` class (or equivalent) and add the `_meta` assertion to its existing params capture test. If no params-capturing test exists, add one in the same style as the `session/load` assertion.

**Step 8 — Add `TestSpawnEnv` class** for `_spawn` env behavior. Use `@pytest.mark.skipif(sys.platform != "win32", reason="win32api/win32job spawn path")` — match the skip pattern established in `tests/test_autostart.py` or `tests/test_tray.py`. Patch at `power_atlas.acp` module level:

```python
@pytest.mark.skipif(sys.platform != "win32", reason="win32api/win32job spawn path")
class TestSpawnEnv:
    """Verify _spawn passes the correct environment to the child process."""

    def test_spawn_env_has_poweratlas_markers(self, tmp_path):
        """POWER_ATLAS_SESSION and KIRO_CLI_ACP_CLIENT_NAME are injected; PATH survives."""
        sup = acp_mod._Supervisor()
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        with patch("power_atlas.acp.shutil.which", return_value=str(tmp_path / "kiro-cli.exe")), \
             patch("power_atlas.acp.subprocess.Popen", return_value=mock_proc) as mock_popen, \
             patch.object(acp_mod._Supervisor, "_create_job", return_value=None), \
             patch("power_atlas.acp.win32api.OpenProcess", return_value=None), \
             patch("power_atlas.acp.win32job.AssignProcessToJobObject"), \
             patch("power_atlas.acp.win32api.CloseHandle"), \
             patch("power_atlas.acp.threading.Thread"):
            sup._spawn()
        env = mock_popen.call_args.kwargs["env"]
        assert env.get("POWER_ATLAS_SESSION") == "1"
        assert env.get("KIRO_CLI_ACP_CLIENT_NAME") == "poweratlas"
        assert "PATH" in env  # base os.environ keys survive the filter

    def test_spawn_env_scrubs_claude_markers(self, tmp_path, monkeypatch):
        """CLAUDECODE, CLAUDE_PID, and CLAUDE_CODE_* keys are absent."""
        monkeypatch.setenv("CLAUDECODE", "1")
        monkeypatch.setenv("CLAUDE_PID", "999")
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "abc")
        sup = acp_mod._Supervisor()
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        with patch("power_atlas.acp.shutil.which", return_value=str(tmp_path / "kiro-cli.exe")), \
             patch("power_atlas.acp.subprocess.Popen", return_value=mock_proc) as mock_popen, \
             patch.object(acp_mod._Supervisor, "_create_job", return_value=None), \
             patch("power_atlas.acp.win32api.OpenProcess", return_value=None), \
             patch("power_atlas.acp.win32job.AssignProcessToJobObject"), \
             patch("power_atlas.acp.win32api.CloseHandle"), \
             patch("power_atlas.acp.threading.Thread"):
            sup._spawn()
        env = mock_popen.call_args.kwargs["env"]
        assert "CLAUDECODE" not in env
        assert "CLAUDE_PID" not in env
        assert "CLAUDE_CODE_SESSION_ID" not in env
```

**Exit criteria**:
- [x] `import os` present in `acp.py` stdlib imports block (`grep "^import os" src/power_atlas/acp.py` passes)
- [x] `_build_child_env` defined as a module-level function in `acp.py` with `_SCRUB_PREFIXES`, `_SCRUB_EXACT`, and the cross-copy comment
- [x] `_OVERLAY_STEERING` defined as a `tuple[dict, ...]` with the `ClientSteeringDescriptorSchema` comment
- [x] `_build_kas_session_params()` defined with the KAS schema citation in its docstring
- [x] `_Supervisor._spawn` Popen call includes `env=_build_child_env({"KIRO_CLI_ACP_CLIENT_NAME": "poweratlas"})`
- [x] `new_session` params include `**_build_kas_session_params()`
- [x] `load_session` params include `**_build_kas_session_params()`
- [x] `test_web.py` `session/load` assertion updated to include `_meta` via `_build_kas_session_params()`
- [x] `test_web.py` `session/new` assertion added for `_meta`
- [x] `TestSpawnEnv` decorated with `@pytest.mark.skipif(sys.platform != "win32", ...)` and tests pass on Windows: `pytest tests/test_web.py -k TestSpawnEnv -x`
- [x] Full `test_web.py` suite passes: `pytest tests/test_web.py -x`

Implementation (2026-08-18, code: e1460c4 / fix: 25e2b50)
Added `import os` to the stdlib imports block (alphabetically after `itertools`); updated `from typing import Final` to include `Any`. Inserted `_SCRUB_PREFIXES`, `_SCRUB_EXACT`, `_build_child_env`, `_OVERLAY_STEERING` (as `tuple[dict[str, str], ...]`), and `_build_kas_session_params` after `ACP_ARGS`. The `_spawn` Popen call now passes `env=_build_child_env({"KIRO_CLI_ACP_CLIENT_NAME": "poweratlas"})`. Both `new_session` and `load_session` `_request` calls now include `**_build_kas_session_params()`. In `tests/test_web.py`: the `session/load` exact-params assertion was updated; a new `TestAcpNewSessionParams` class with `test_new_session_params_include_meta` hard-codes the full expected `_meta` structure including `cwd` and steering content; `TestSpawnEnv` (Windows-only, `skipif` guarded) was added with two tests covering marker injection and CLAUDE scrub, including all five measured CLAUDE markers. Fix commit (25e2b50) resolved a tautological `session/new` assertion, removed a dead import, tightened the type annotation, improved cross-copy comment placement, and added `CLAUDE_CODE_CHILD_SESSION`/`CLAUDE_CODE_BRIDGE_SESSION_ID` to the scrub test. Cycle-2 fix (c98473c) removed a duplicate sync comment and added `CLAUDE_PID` assertion to `test_launch_session_kiro_ide_non_terminal`. All 1527 tests pass.

---

### Phase 2: `launcher.py` — env injection [QA] [P:1]

**Goal**: Add `_build_child_env()` helper in `launcher.py` and `env=` to both `launch_session` Popen calls; add env= assertions to tests; update ROADMAP.md.

**File scope**: `src/power_atlas/launcher.py`, `tests/test_launcher.py`, `plans/ROADMAP.md`, `plans/tests/260701_POWERATLAS.md`

**Covers**: SC-2, SC-4 (launcher.py helper)

**Changes to `src/power_atlas/launcher.py`**:

**Step 1 — after `_SESSION_ID_RE`** (`launcher.py:25`), add:

```python
# Keys stripped from the child env to prevent marker leakage from the PowerAtlas
# tray process into launched provider sessions.
# NOTE: A copy of this function lives in acp.py (isolation boundary prevents
# shared import). Keep _SCRUB_PREFIXES and _SCRUB_EXACT in sync with that copy.
_SCRUB_PREFIXES = ("CLAUDE_CODE_",)
_SCRUB_EXACT = frozenset({"CLAUDECODE", "CLAUDE_PID"})


def _build_child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Build the environment dict for a spawned provider child process.

    Strips CLAUDE_CODE_* / CLAUDECODE / CLAUDE_PID markers inherited from the
    PowerAtlas tray process, and injects POWER_ATLAS_SESSION=1. ``extra`` is
    optional (no per-launch extras needed for provider sessions). ``extra`` keys
    override same-named keys from os.environ (last-write-wins).
    """
    base = {
        k: v for k, v in os.environ.items()
        if not any(k.startswith(p) for p in _SCRUB_PREFIXES)
        and k not in _SCRUB_EXACT
    }
    return {**base, "POWER_ATLAS_SESSION": "1", **(extra or {})}
```

**Step 2 — `launch_session` non-terminal path** (`launcher.py:152–167`): Add `env=` to `kwargs` after the entire `if sys.platform == "win32": … else: …` block and before the `Popen` call. The correct insertion is:

```python
# After the if/else platform block that sets creationflags / start_new_session / shell:
kwargs["env"] = _build_child_env()
subprocess.Popen(cli_args, **kwargs)
```

**Step 3 — `launch_session` terminal path** (`launcher.py:190–195`): Same pattern — add `kwargs["env"] = _build_child_env()` after the `if sys.platform == "win32": … else: …` block and before `subprocess.Popen(cmd, **kwargs)`:

```python
kwargs: dict = {"creationflags": subprocess.CREATE_NEW_CONSOLE} if sys.platform == "win32" else {"start_new_session": True}
kwargs["env"] = _build_child_env()
subprocess.Popen(cmd, **kwargs)
```

> **Do not touch `launch_custom`** (`launcher.py:~380–435`). It already has its own `proc_env = {**os.environ, **env}` pattern. The CLAUDE marker scrub is deliberately omitted from `launch_custom` — custom launchers may need inherited environment for user-defined scripts. See Follow-up #2.

**Changes to `tests/test_launcher.py`**:

**Step 4 — Add env assertions to `test_launch_session_kiro_builds_correct_args`** (terminal path) and **`test_launch_session_kiro_ide_non_terminal`** (non-terminal path). Each already patches `subprocess.Popen` — add after the existing command-list assertion:

```python
kwargs = mock_popen.call_args.kwargs
assert kwargs["env"]["POWER_ATLAS_SESSION"] == "1"
assert "CLAUDECODE" not in kwargs["env"]
```

**Step 5 — Add `test_launch_session_scrubs_claude_markers`** to `TestLaunchSession`:

```python
def test_launch_session_scrubs_claude_markers(self, monkeypatch, tmp_path):
    """CLAUDECODE, CLAUDE_CODE_* absent from launched session env; POWER_ATLAS_SESSION present."""
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "abc")
    with patch("power_atlas.launcher.subprocess.Popen") as mock_popen, \
         patch("power_atlas.launcher.shutil.which", return_value=str(tmp_path / "wt.exe")):
        result = launch_session(str(tmp_path), provider="kiro-cli")
    assert result.success is True
    env = mock_popen.call_args.kwargs["env"]
    assert "CLAUDECODE" not in env
    assert "CLAUDE_CODE_SESSION_ID" not in env
    assert env["POWER_ATLAS_SESSION"] == "1"
```

> Use `power_atlas.launcher.subprocess.Popen` as the patch target (not the global `subprocess.Popen`) — this is the module-local binding and the idiomatic pattern in this codebase. Compare `patch("power_atlas.acp.subprocess.Popen")` used in Phase 1.

**Documentation updates**:

**Step 6 — `plans/ROADMAP.md`**: Remove the bullet "Launched sessions inherit PowerAtlas's environment — sessions inherit `CLAUDE_CODE_CHILD_SESSION` and other markers from the tray process; three launch paths disagree" from the Platform section. Add a note: "`launch_custom` env scrub deliberately excluded — see `plans/done/260818_ACP_ENV_MARKER_AND_OVERLAY_STEERING.md` Follow-up #2."

**Step 7 — `plans/tests/260701_POWERATLAS.md`**: Review Sections 1.6 and 1.12 for any description of `launch_session`'s Popen kwargs surface. Update any description of the kwargs dict to reflect the added `env=` key.

**Exit criteria**:
- [x] `_build_child_env` defined in `launcher.py` with `_SCRUB_PREFIXES`, `_SCRUB_EXACT`, cross-copy comment, and `extra: ... | None = None` optional signature
- [x] Non-terminal path Popen (`launcher.py:~166`) includes `env=_build_child_env()` added after the platform block
- [x] Terminal path Popen (`launcher.py:~192`) includes `env=_build_child_env()` added after the platform block
- [x] `launch_custom` unchanged — confirmed by `git diff src/power_atlas/launcher.py | grep -A5 "launch_custom"`
- [x] Patch target in `test_launch_session_scrubs_claude_markers` is `power_atlas.launcher.subprocess.Popen`
- [x] `test_launch_session_kiro_builds_correct_args` and `test_launch_session_kiro_ide_non_terminal` assert `env["POWER_ATLAS_SESSION"] == "1"` and `"CLAUDECODE" not in env`
- [x] `test_launch_session_scrubs_claude_markers` added and passes: `pytest tests/test_launcher.py -k "test_launch_session_scrubs_claude_markers" -x`
- [x] `plans/ROADMAP.md` CLAUDE_CODE bullet removed and `launch_custom` exclusion noted
- [x] `plans/tests/260701_POWERATLAS.md` Sections 1.6/1.12 reviewed; updated if they describe the Popen kwargs surface
- [x] Full `test_launcher.py` suite passes: `pytest tests/test_launcher.py -x`

Implementation (2026-08-18, code: 89049b7 / fix: fd2409f / c98473c)
Added `_SCRUB_PREFIXES`, `_SCRUB_EXACT`, and `_build_child_env` (optional `extra`, `**(extra or {})`) after `_SESSION_ID_RE` in `launcher.py`. Both `subprocess.Popen` call sites in `launch_session` — non-terminal (kiro-ide / detached) and terminal (kiro-cli / claude-code via wt/pwsh) — now pass `kwargs["env"] = _build_child_env()` inserted after the full `if sys.platform == "win32": … else: …` block and before each Popen call. `launch_custom` was left untouched per the plan. In `tests/test_launcher.py`: `test_launch_session_kiro_builds_correct_args` and `test_launch_session_kiro_ide_non_terminal` were updated with `monkeypatch.setenv("CLAUDECODE", "1")` and `monkeypatch.setenv("CLAUDE_PID", "999")` to make scrub assertions discriminating; both were also updated to use module-local patch targets (`power_atlas.launcher.subprocess.Popen`, `power_atlas.launcher.shutil.which`). `test_launch_session_scrubs_claude_markers` added to `TestLaunchSession`. ROADMAP.md: "Launched sessions inherit…" bullet removed from Platform section and priority table; `launch_custom` exclusion note added as a named bold item. `plans/tests/260701_POWERATLAS.md` Sections 1.6 and 1.12 were reviewed — Section 1.6 covers stale-entry cache refresh and Section 1.12 covers Claude session parse; neither describes `launch_session`'s Popen kwargs surface, so no text update was required. All 120 `test_launcher.py` tests pass.

---

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| R1: `os.environ` contamination | Low — dict comprehension snapshots `os.environ` into a new dict; `os.environ` is never mutated | Inline dict comprehension (not `os.environ.pop`); covered by both helpers' design |
| R2: CLAUDE_CODE_* scrub predicate too narrow | Low — a future `CLAUDE_*` var outside the prefix/exact set would pass through | Accepted; the measured set (session 361b50c5, 2026-08-03) covers all five known markers. If a new one appears, add it to both `_SCRUB_EXACT` copies. |
| R3: `TestSpawnEnv` unusable on non-Windows | Medium if unguarded — `win32api is None` on Linux, patching it raises AttributeError | `@pytest.mark.skipif(sys.platform != "win32", ...)` on the class; addressed in Phase 1 exit criteria |
| R4: `_OVERLAY_STEERING` mutable trap | Low — using `tuple[dict, ...]` prevents `.append()` mutations; `_build_kas_session_params` returns `list(_OVERLAY_STEERING)` (shallow copy) | Design decision (§3) mandates tuple; `list()` copy at call site |
| R5: Duplicate `_build_child_env` drift | Low — both copies are ~8 lines with no external state; cross-copy comment and test coverage on both enforce sync | Follow-up #3 tracks extraction if they diverge materially |
| R6: Terminal-path env= is best-effort | Low — `wt.exe` / `pwsh.exe` inherit `POWER_ATLAS_SESSION`; kiro-cli inherits from PowerShell via OS chain. A custom `{pscmd}` that resets the environment would break it. | Accepted; non-terminal path is guaranteed injection. SC-2 tests verify the env= reaches Popen, not that it reaches kiro-cli end-to-end. Noted here for future reference. |

## 7) Verification

```bash
# Phase 1 — acp.py and test_web.py
.venv-PowerAtlas\Scripts\pytest tests/test_web.py -x -q

# Phase 2 — launcher.py and test_launcher.py
.venv-PowerAtlas\Scripts\pytest tests/test_launcher.py -x -q

# Full suite (after both phases)
.venv-PowerAtlas\Scripts\pytest -x -q

# Manual verification after PowerAtlas restart:
# 1. Open PowerAtlas, navigate to /acp, create a new session
# 2. In the ACP session, run:
#    Get-ChildItem Env: | Where-Object { $_.Name -match 'POWER_ATLAS|KIRO_CLI_ACP|CLAUDE' }
# 3. Confirm: POWER_ATLAS_SESSION=1, KIRO_CLI_ACP_CLIENT_NAME=poweratlas,
#    no CLAUDECODE/CLAUDE_PID/CLAUDE_CODE_* keys
# 4. Check the session's messages.jsonl:
#    grep "steering_inclusion" ~/.kiro/sessions/*/<session-dir>/messages.jsonl
#    Confirm poweratlas-context appears in a steering_inclusion payload
```

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `plans/ROADMAP.md` | Remove "Launched sessions inherit PowerAtlas's environment" bullet; add `launch_custom` exclusion note | 2 |
| `plans/tests/260701_POWERATLAS.md` | Review Sections 1.6 and 1.12 for Popen kwargs surface descriptions; update to reflect `env=` addition | 2 |

## 9) Implementation Divergences from Plan

1. **`_OVERLAY_STEERING` inner dict copy upgraded to `{**d}`**: The plan specified `list(_OVERLAY_STEERING)` as the shallow copy strategy in `_build_kas_session_params`. During implementation review, it was found that `list()` copies the list but not the inner dicts, leaving shared mutable state. Fixed to `[{**d} for d in _OVERLAY_STEERING]` — each call now returns fresh dict objects. No behavior change observable at runtime (inner dicts are only read, never mutated in practice), but the protection is structural.

2. **`session/new` test uses hard-coded expected structure**: The plan described adding a `session/new` exact-params assertion using `acp_mod._build_kas_session_params()`. During review, this was flagged as tautological (comparing live function output against itself). The test was changed to hard-code the expected `_meta` structure including the literal `"PowerAtlas context — content TBD"` string. The `session/load` test retains the `**acp_mod._build_kas_session_params()` form — an intentional asymmetry: the `session/new` test pins content, the `session/load` test pins structure.

3. **`plans/tests/260701_POWERATLAS.md` — no update required**: Sections 1.6 and 1.12 cover stale-entry cache refresh and Claude session parse respectively. Neither describes `launch_session`'s Popen kwargs surface. Exit criterion 9 verified as complete (review done, no edit warranted).

## Follow-up Work (Deferred)

1. **PowerAtlas overlay steering content.** The `_OVERLAY_STEERING` constant ships with a placeholder. Define and populate the actual steering content (behavioral guidance for the agent in PowerAtlas context). Source: Q4 resolution during `/qexplore`.
2. **`launch_custom` CLAUDE marker scrub.** Custom launchers are not scrubbed — user-defined scripts may rely on inherited environment. If this becomes a problem, extend `_build_child_env` to `launch_custom`. Source: Phase 2 design decision (deliberately out of scope). Noted in ROADMAP.md.
3. **Shared `_build_child_env` if drift occurs.** If the two copies diverge meaningfully in a future session, extract to `config.py` (adding it as the second name `acp.py` imports from `config`, since `CONFIG_DIR` is already one). Source: R5.
4. **Scrub `POWER_ATLAS_VENV_REEXEC` from child env.** The re-exec sentinel set by `interpreter.py` leaks to kiro-cli children. kiro-cli ignores unknown env vars, so this is benign; add to `_SCRUB_EXACT` in both copies when cleaning up. Source: review finding F17 (2026-08-18).
5. **`launch_terminal` CLAUDE scrub.** `launch_terminal` (~line 595) opens a bare terminal shell without `env=` and thus inherits CLAUDE_CODE_* markers. This is out of scope for the current plan (user manually starts a process inside the terminal), but worth tracking. Source: review finding F19 (2026-08-18).

## Review Log

### 2026-08-18 — Post-Implementation Review

Overall implementation health: Green.
Personas: Senior engineer, Reliability engineer, Security auditor.
4 findings (0 High, 0 Medium, 4 Low).
QA verification: BLOCKED — PowerAtlas restart required to verify runtime env vars and KAS `_meta.kiro.steering` delivery; `AGENTS.md § Doc & Test Guidelines` prohibits autonomous restart. Manual verification steps documented in §7.

#### Test execution summary

| Phase | Tests | QA | Notes |
|---|---|---|---|
| 1: acp.py env injection + overlay steering | pass (1407+1 skip) | BLOCKED | Windows-only TestSpawnEnv passes on Windows; KAS protocol QA requires PowerAtlas restart |
| 2: launcher.py env injection | pass (120) | BLOCKED | Env= verified by test suite; terminal-chain propagation requires live launch |

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Low | `acp.py` module docstring (line 15) says "imports exactly two names from the rest of `power_atlas`" — predates this plan, refers to the guarded coupling constraint, not a literal count | User: accepted — pre-existing wording, intent is "exactly two *guarded* names"; no behavior impact |
| 2 | Low | `_build_child_env` called inside `try: except OSError:` — pure function; any future I/O addition would bypass job cleanup | User: accepted — function is demonstrably pure; document in Follow-up if modified |
| 3 | Low | `test_launch_session_claude_builds_correct_args` doesn't assert env= (claude-code uses same terminal-path code as kiro-cli) | User: accepted — `test_launch_session_scrubs_claude_markers` covers the shared code path |
| 4 | Low | `TestSpawnEnv` has inline `MagicMock` import vs module-level | User: accepted — consistent with file's established per-method inline import pattern |

### 2026-08-18 — Plan Creation (via /qplan)

16 findings (3 High, 9 Medium, 4 Low). All 16 auto-resolved.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| F1 | High | `import os` missing from `acp.py` — `_build_child_env` would NameError at spawn | Fixed — `import os` added to plan's acp.py Step 1; exit criterion adds grep check |
| F2 | High | `TestSpawnEnv` crashes (not skips) on Linux — `win32api.OpenProcess` AttributeError | Fixed — `@pytest.mark.skipif(sys.platform != "win32", ...)` added to class; exit criterion added |
| F3 | High | `session/new` exact-params assertion missing — SC-3 only half-verified | Fixed — SC-5 extended; Phase 1 Step 7 adds `session/new` params assertion; SC-3 updated to require both |
| F4 | Medium | `_OVERLAY_STEERING` is a mutable list — shared reference returned by helper | Fixed — changed to `tuple[dict, ...]`; `_build_kas_session_params` returns `list(_OVERLAY_STEERING)` (shallow copy) |
| F5 | Medium | Phase 2 Popen patch target was `subprocess.Popen` (global) — should be `power_atlas.launcher.subprocess.Popen` | Fixed — patch target corrected in Phase 2 test code and exit criteria |
| F6 | Medium | `env=` insertion point in `launch_session` ambiguous — could land inside the platform block | Fixed — explicitly stated "after the entire if/else platform block" in Phase 2 Steps 2 and 3 |
| F7 | Medium | `plans/tests/260701_POWERATLAS.md` missing from Documentation Updates | Fixed — added to §8 table with Phase 2 assignment; added to Phase 2 file scope and exit criteria |
| F8 | Medium | `_build_child_env` signature mismatch undocumented; no cross-copy sync comment | Fixed — Design Decisions table row added explaining intentional difference; cross-copy comment added to both code blocks |
| F9 | Medium | `_build_session_meta()` name misleads — looks like PA internal, is a KAS protocol key | Fixed — renamed to `_build_kas_session_params()` throughout; KAS origin documented in docstring |
| F10 | Medium | `_OVERLAY_STEERING` has no comment citing `ClientSteeringDescriptorSchema` | Fixed — schema comment added to constant definition |
| F11 | Medium | Phase 2 names only one test — non-terminal path test not cited; `call_args[1]` idiom outdated | Fixed — `test_launch_session_kiro_ide_non_terminal` and `test_launch_session_kiro_builds_correct_args` explicitly named; `call_args.kwargs` used throughout |
| F12 | Medium | `launch_custom` CLAUDE scrub omission not noted in ROADMAP.md | Fixed — Phase 2 Step 6 adds the `launch_custom` exclusion note to ROADMAP.md |
| F13 | Low | `call_args[1]` should be `call_args.kwargs` | Fixed — all test code blocks use `call_args.kwargs` |
| F14 | Low | `_build_session_meta` return type `-> dict` should be `-> dict[str, Any]` | Fixed — return type is `-> dict[str, Any]` in the renamed `_build_kas_session_params` |
| F15 | Low | Positive test should assert `PATH` in env to prove `os.environ` keys survive | Fixed — `assert "PATH" in env` added to `test_spawn_env_has_poweratlas_markers` |
| F16 | Low | Isolation boundary description overstated — `data_kiro` is a third import | Fixed — §1 Current State now correctly states three intra-package imports |

### 2026-08-18 — Implementation Review (after Phases 1+2, parallel, effort: high)

Personas: Senior engineer, Reliability engineer, Security auditor, Maintainability reviewer (Phase 1); Senior engineer, Reliability engineer, Maintainability reviewer, Architect (Phase 2). Cycle 1: 2 High, 9 Medium, 8 Low. After auto-fix cycle 1 (commits 25e2b50, fd2409f) and cycle 2 (c98473c): 0 unresolved High, 0 unresolved Medium. Remaining Low findings: F11 (TestSpawnEnv placement — accepted as-is), F15 (extra overrides POWER_ATLAS_SESSION — by design), F17 (POWER_ATLAS_VENV_REEXEC leaks — deferred to Follow-up), F19 (launch_terminal not scrubbed — out of scope). Implementation health: **Green**.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | High | `test_new_session_params_include_meta` was tautological — `_meta` assertion compared live function output against itself | Fixed — 25e2b50 hard-codes expected `_meta` structure with steering content and `cwd`; fully discriminating |
| 2 | High | `test_launch_session_kiro_builds_correct_args` and `test_launch_session_kiro_ide_non_terminal` asserted `"CLAUDECODE" not in env` without setting it — vacuously true | Fixed — fd2409f adds `monkeypatch.setenv("CLAUDECODE", "1")` to both tests; c98473c adds `CLAUDE_PID` to `kiro_ide_non_terminal` |
| 3 | Medium | Exit criteria unchecked in plan file | Fixed — ticked by orchestrator in this plan update |
| 4 | Medium | `session/new` test missing `cwd` assertion | Fixed — 25e2b50 adds `cwd` to exact-match assertion |
| 5 | Medium | `_OVERLAY_STEERING` inner dicts mutable; `list()` provides only shallow copy | Fixed — 25e2b50 changes to `[{**d} for d in _OVERLAY_STEERING]`; fresh dict objects per call |
| 6 | Medium | `plans/tests/260701_POWERATLAS.md` review outcome not recorded | Fixed — §9 documents review; Sections 1.6/1.12 contain no Popen kwargs descriptions; no edit required |
| 7 | Medium | Patch targets inconsistent — amended tests used global `subprocess.Popen` | Fixed — fd2409f standardizes to `power_atlas.launcher.subprocess.Popen` on all three tests |
| 8 | Medium | Cross-copy sync comment in `acp.py` misattributed to constants block only | Fixed — 25e2b50 adds note to `_build_child_env` docstring; c98473c removes duplicate outer comment |
| 9 | Medium | `tmp_path` fixture unused in `test_new_session_params_include_meta` | Fixed — 25e2b50 removes parameter |
| 10 | Medium | `CLAUDE_PID` not tested in `test_launch_session_scrubs_claude_markers` | Fixed — fd2409f adds `monkeypatch.setenv("CLAUDE_PID", "999")` and assertion |
| 11 | Medium | `TestSpawnEnv` positioned at end of test_web.py (line ~19653), far from ACP session tests | Orchestrator: proposed-accept — Windows-only skip class naturally grouped at file end; moving would be high-churn splice across 20K-line file with no behavioral benefit |
| 12 | Medium | ROADMAP.md `launch_custom` note was bare bullet, not discoverable | Fixed — fd2409f reformats as named bold heading |
| 13 | Low | Dead `from power_atlas import acp as acp_mod` import in test | Fixed — 25e2b50 removes it |
| 14 | Low | `_OVERLAY_STEERING` type annotation imprecise | Fixed — 25e2b50 tightens to `tuple[dict[str, str], ...]` |
| 15 | Low | `extra` dict can override `POWER_ATLAS_SESSION` if caller passes it | Orchestrator: proposed-accept — design intent: `extra` takes precedence; no ACP call site passes that key; documented in `_build_child_env` docstring |
| 16 | Low | `test_spawn_env_scrubs_claude_markers` missing two of five measured CLAUDE markers | Fixed — 25e2b50 adds `CLAUDE_CODE_CHILD_SESSION` and `CLAUDE_CODE_BRIDGE_SESSION_ID` |
| 17 | Low | `POWER_ATLAS_VENV_REEXEC` leaks to kiro-cli children | Orchestrator: proposed-accept — kiro-cli ignores unknown env vars; added to Follow-up #4 |
| 18 | Low | `launcher.py` cross-copy comment didn't name the exact two-constant group | Fixed — fd2409f improves comment to mention both constants and function |
| 19 | Low | `launch_terminal` (line ~595) has no env= scrub | Orchestrator: proposed-accept — `launch_terminal` opens a shell where user manually starts a process; explicitly out of scope per plan. Added to Follow-up #5 |

16 findings (3 High, 9 Medium, 4 Low). All 16 auto-resolved.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| F1 | High | `import os` missing from `acp.py` — `_build_child_env` would NameError at spawn | Fixed — `import os` added to plan's acp.py Step 1; exit criterion adds grep check |
| F2 | High | `TestSpawnEnv` crashes (not skips) on Linux — `win32api.OpenProcess` AttributeError | Fixed — `@pytest.mark.skipif(sys.platform != "win32", ...)` added to class; exit criterion added |
| F3 | High | `session/new` exact-params assertion missing — SC-3 only half-verified | Fixed — SC-5 extended; Phase 1 Step 7 adds `session/new` params assertion; SC-3 updated to require both |
| F4 | Medium | `_OVERLAY_STEERING` is a mutable list — shared reference returned by helper | Fixed — changed to `tuple[dict, ...]`; `_build_kas_session_params` returns `list(_OVERLAY_STEERING)` (shallow copy) |
| F5 | Medium | Phase 2 Popen patch target was `subprocess.Popen` (global) — should be `power_atlas.launcher.subprocess.Popen` | Fixed — patch target corrected in Phase 2 test code and exit criteria |
| F6 | Medium | `env=` insertion point in `launch_session` ambiguous — could land inside the platform block | Fixed — explicitly stated "after the entire if/else platform block" in Phase 2 Steps 2 and 3 |
| F7 | Medium | `plans/tests/260701_POWERATLAS.md` missing from Documentation Updates | Fixed — added to §8 table with Phase 2 assignment; added to Phase 2 file scope and exit criteria |
| F8 | Medium | `_build_child_env` signature mismatch undocumented; no cross-copy sync comment | Fixed — Design Decisions table row added explaining intentional difference; cross-copy comment added to both code blocks |
| F9 | Medium | `_build_session_meta()` name misleads — looks like PA internal, is a KAS protocol key | Fixed — renamed to `_build_kas_session_params()` throughout; KAS origin documented in docstring |
| F10 | Medium | `_OVERLAY_STEERING` has no comment citing `ClientSteeringDescriptorSchema` | Fixed — schema comment added to constant definition |
| F11 | Medium | Phase 2 names only one test — non-terminal path test not cited; `call_args[1]` idiom outdated | Fixed — `test_launch_session_kiro_ide_non_terminal` and `test_launch_session_kiro_builds_correct_args` explicitly named; `call_args.kwargs` used throughout |
| F12 | Medium | `launch_custom` CLAUDE scrub omission not noted in ROADMAP.md | Fixed — Phase 2 Step 6 adds the `launch_custom` exclusion note to ROADMAP.md |
| F13 | Low | `call_args[1]` should be `call_args.kwargs` | Fixed — all test code blocks use `call_args.kwargs` |
| F14 | Low | `_build_session_meta` return type `-> dict` should be `-> dict[str, Any]` | Fixed — return type is `-> dict[str, Any]` in the renamed `_build_kas_session_params` |
| F15 | Low | Positive test should assert `PATH` in env to prove `os.environ` keys survive | Fixed — `assert "PATH" in env` added to `test_spawn_env_has_poweratlas_markers` |
| F16 | Low | Isolation boundary description overstated — `data_kiro` is a third import | Fixed — §1 Current State now correctly states three intra-package imports |

## Harness Improvement Opportunities

- The probe-before-interview flow (decidable-by-probe list from sub-agents) worked well; the only friction was that `acp-server.js` binary search required multiple round-trips to locate the exact method body. A pattern index of key KAS method names and their byte offsets (stored once, invalidated on version bump) would save 3-4 tool calls per steering-related exploration. — cost: ~4 extra tool calls across the exploration — suggested change: add a KAS method index to `docs/KNOWLEDGE.md`.
