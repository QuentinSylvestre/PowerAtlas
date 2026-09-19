# Test Harness
last_run: 2026-09-19

## Run notes (2026-09-19, plan mode for 260919_KIRO_CLI_V3_LIVENESS_ACP)
- **A live PowerAtlas instance was running (PID 41776) and idle** at plan time, supervising `kiro-cli.exe acp
  --agent-engine v3` (PID 37024) with one idle ACP session. Per user decision, this run uses the live instance
  directly for all probing, including a controlled kill of the supervised `kiro-cli.exe` child to test
  crash-recovery — but never restarts PowerAtlas itself (absolute rule, see `AGENTS.md`).
- **Never send `_kiro.dev/commands/options` with `command: ""` to a live/working instance** — project memory
  records this exits kiro-cli with code 0, no stderr. Useful as a deterministic crash probe on an already-idle
  instance; never send it to a session mid-turn.
- Config bounds for the three ACP tunables (`acp_max_sessions`, `acp_idle_ttl_seconds`,
  `acp_prompt_silence_seconds`) live in two places that must be kept in sync: `web.py::_SETTING_BOUNDS` (write
  path) and `acp.py::apply_config`'s `_clamped(...)` calls (load path, startup-only). Was drifted for
  `acp_prompt_silence_seconds` (60-86400 vs 60-7200) — fixed this run, see
  `plans/tests/260919_KIRO_CLI_V3_LIVENESS_ACP.md` §2.5.
- **Reachable read-only probe recipe for the live ACP instance**: fetch `GET /acp`, regex out
  `var ACP_TOKEN = "..."` from the page, then connect to `ws://127.0.0.1:<port>/ws/acp?t=<token>` with only an
  `Origin: http://127.0.0.1:<port>` header set (do NOT also set an explicit `Host` header — duplicate Host headers
  cause a 400 before the app's own auth checks run). Send `{"type":"load","sessionId":"<id>","payload":{}}` to
  resume a session read-mostly (no prompt), then `{"type":"close","sessionId":"<id>","payload":{}}` to release it
  again. Confirmed working against kiro-cli 2.22.0 / websockets 16.1.1 in the project venv.
- **Killing the supervised `kiro-cli.exe` directly does not trigger `_on_agent_death` promptly** — its child
  `node.exe` (the real `@kiro/agent` ACP server, spawned with inherited stdio) keeps the stdout pipe's write end
  open, so the reader thread's blocking read never sees EOF. Recovery only happens lazily, on the next
  request's `ensure_started()` check (which polls the exact PID directly, unaffected by the pipe). Confirmed via
  direct process-tree inspection (`Get-CimInstance Win32_Process`) — see
  `plans/tests/260919_KIRO_CLI_V3_LIVENESS_ACP.md` for the full writeup. Useful, repeatable crash-recovery test
  recipe for future runs; expect the orphaned `node.exe` to persist (growing RSS) until the next ACP request.
- **Bisecting a suspected regression**: a plain `git worktree add --detach --no-checkout <path> <rev>` followed by
  `git sparse-checkout init --cone` + `set <dirs>` + `git checkout <rev> -- .` avoids Windows "filename too long"
  errors from this repo's `_proto/` asset directories, which a full checkout of any historical commit hits.

## Run notes (2026-07-01, deep run of 260701_POWERATLAS)
- Confirmed live: both provider datasets on disk; web server starts via `python -m uvicorn power_atlas.web:app` on a fixed port (no tray/peek needed) — used :8899.
- **A live PowerAtlas instance was running (PID 15148)** during the run — so mutex/stop/restart/detach lifecycle tests were held to library-level (partially-verified) to avoid disrupting it. Verify with the PID file before lifecycle runs.
- Config snapshot+restore worked (real config was mutated by the H1/settings API tests, then restored from snapshot).
- Autostart enable/disable cycle regenerated the real `PowerAtlas.lnk` (state restored to original *enabled*; IconLocation now populated where the pre-existing shortcut had a blank icon path — target unchanged: venv pythonw.exe).
- Browser GUI pass (2026-07-02): Claude-in-Chrome extension NOT connectable in this background session (list_connected_browsers empty) and no Playwright MCP server configured. Fell back to **standalone Playwright** (`pip install playwright` + `playwright install chromium` into the venv) driven headless — reliable, no extension/user interaction needed. Recommend this as the default browser-test path here. (Playwright now persists in the venv; remove with `pip uninstall playwright` if unwanted.)
- Lifecycle pass (2026-07-02): completed under user authorization to stop/restart the app. Sequence that leaves the app healthy: `--stop` → verify down → bare launch (or `--restart`) to restore. The single-instance mutex is `PowerAtlasMutex`; a running instance makes `-f` exit(0) silently. Server binds a fresh dynamic port each start (seen: 64262, 61483). Cold discovery ~2.8s (corroborates the D1 thundering-herd finding).

## Resources
| Name | Type | Availability | Constraints | last_verified |
|---|---|---|---|---|
| local-dev-server | environment | always (uvicorn, dynamic port) | `power-atlas -f`; single instance via mutex `PowerAtlasMutex` | 2026-07-01 |
| pytest-suite | tool | always | 254 tests in tests/ (test_data, test_web, test_launcher, test_config, test_peek, test_tray, test_autostart) | 2026-07-01 |
| browser-mcp | tool | always | drives web UI at the dynamic server port; cannot interact with native tray/peek | 2026-07-01 |
| kiro-v3-session-data | data | always (read-only) | `~/.kiro/sessions/<workspace-hash>/sess_*/` (session.json + messages.jsonl) — do not modify/delete | 2026-08-18 |
| claude-session-data | data | always (read-only) | `~/.claude/projects/` (UUID *.jsonl) + `~/.claude/history.jsonl` — do not modify/delete | 2026-07-01 |
| config-toml | data | always | `%LOCALAPPDATA%\power-atlas\config.toml` — read/write; snapshot+restore | 2026-07-01 |
| icons-dir | data | always | `%LOCALAPPDATA%\power-atlas\icons\<launcher_id>.png` — snapshot/clean after | 2026-07-01 |
| autostart-folder | environment | always | Start Menu Startup `PowerAtlas.lnk` — enable/disable freely; no reboot (existence-only) | 2026-07-01 |
| venv-pythonw | tool | verify | `.venv-PowerAtlas\Scripts\pythonw.exe` — autostart shortcut target; confirm it exists | 2026-07-01 |
| system-tray | environment | user-assisted | native Windows tray; scoped out (user clicks and reports) | 2026-07-01 |
| peek-overlay | environment | user-assisted | native global hotkey + pywebview overlay; scoped out (X11-only on Linux) | 2026-07-01 |

## Execution Notes

- **Platform**: this machine is Windows 11. Linux code paths (5 Linux terminals, `.desktop` autostart,
  X11/Wayland display probe, `/proc` PID fallback, flock) are code-inspection-only.
- **Providers**: three — claude-code, kiro-ide, and kiro-cli-v3. All have real data on disk. Test cross-provider behavior
  (discovery merge, tab filtering, cache asymmetry) explicitly, not just one provider.
- **Parallelism**: Data, Config, Launcher, Icons, Autostart are independent (isolate + snapshot/restore).
  Web depends on Data + Config (shared server + browser). Lifecycle is process-level (isolate).
- **State hygiene**: snapshot `config.toml` and Startup-folder state before mutating; restore after.
  Clean `icons/` PNGs created during tests. Never touch real provider session data.
- **Scoped out**: native tray menu clicks and peek hotkey/overlay (user-assisted). Library-testable
  fragments of tray/peek remain unit-coverable but are deferred (existing unit tests cover them).
- **Browser tests**: start server on a dynamic port (`power-atlas -f`), then drive the web UI via browser MCP.

## Migration note (2026-07-01)
Renamed from kiro-orchestrator: config dir `%LOCALAPPDATA%\kiro-orchestrator` → `%LOCALAPPDATA%\power-atlas`;
autostart shortcut `Kiro Orchestrator.lnk` → `PowerAtlas.lnk`; added claude-code provider data as a resource.
