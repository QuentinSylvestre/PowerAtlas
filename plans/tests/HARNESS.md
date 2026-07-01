# Test Harness
last_run: null

## Resources
| Name | Type | Availability | Constraints | last_verified |
|---|---|---|---|---|
| local-dev-server | environment | always (uvicorn --foreground) | dynamic port; single instance via mutex | 2026-06-18 |
| pytest-suite | tool | always | existing tests in tests/ (test_data, test_config, test_launcher, test_autostart, test_web) | 2026-06-18 |
| playwright-mcp | tool | always | browser-based only; cannot interact with native tray | 2026-06-18 |
| real-session-data | data | always (read-only) | ~/.kiro/sessions/cli/ — do not modify or delete | 2026-06-18 |
| kiro-cli-sqlite | data | always (read-only) | %LOCALAPPDATA%/Kiro-Cli/data.sqlite3 — read-only access | 2026-06-18 |
| autostart-folder | environment | always | %APPDATA%/.../Startup/ — can enable/disable freely; no reboot | 2026-06-18 |
| system-tray | environment | user-assisted | native Windows tray; user clicks and reports | 2026-06-18 |
| config-toml | data | always | %LOCALAPPDATA%/kiro-orchestrator/config.toml — can read/write; snapshot+restore | 2026-06-18 |

## Execution Notes

- **Parallelism**: data, config, launcher, autostart are independent (no shared state). Web depends on data+config. Tray depends on all.
- **State hygiene**: Snapshot config.toml before mutating tests; restore after. Autostart .lnk: check initial state, restore after.
- **User-dependent tests** (deferred to end): tray menu interactions, autostart reboot verification.
- **Browser tests**: start server on dynamic port, use Playwright MCP to interact with web UI.
