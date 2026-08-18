# Test Harness
last_run: 2026-07-01

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
| kiro-session-data | data | always (read-only) | `~/.kiro/sessions/cli/` (*.json + *.jsonl + *.history) — do not modify/delete | 2026-07-01 |
| kiro-v3-session-data | data | always (read-only) | `~/.kiro/sessions/<workspace-hash>/sess_*/` (session.json + messages.jsonl) — do not modify/delete | 2026-08-18 |
| kiro-cli-sqlite | data | always (read-only) | `%LOCALAPPDATA%\Kiro-Cli\data.sqlite3` (conversations_v2) — read-only, mode=ro | 2026-07-01 |
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
- **Providers**: four — kiro-cli, claude-code, kiro-ide, and kiro-cli-v3. All have real data on disk. Test cross-provider behavior
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
