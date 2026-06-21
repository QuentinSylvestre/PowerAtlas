# Kiro Orchestrator — Roadmap

> Non-executed ideas and future features, organized by theme.

---

-- **Rename to PowerAtlas** and use icon

## Automation & Workflows

- **Dispatch no-interactive tasks** — launch kiro-cli with `--no-interactive` and a prompt, fire-and-forget from the UI
- **Open session with specific prompt/skill** — generic prompt input, local/user skill execution with params
- **Template prompts** — save reusable prompt templates per workspace (e.g., "/qdev {latest plan file}" as one-click)
- **Scheduled tasks** — cron-like recurring kiro-cli launches (e.g., "/qdream every Sunday 10am in agent-playbook")
- **Chained launches** — "when session X exits, auto-launch session Y in folder Z" (pipeline mode)
- **Plan-file shortcuts** — detect plan files in `plans/` and offer one-click "/qdev on this plan" buttons

## Custom Launch Section

- **WSL support** — launch commands inside WSL from the orchestrator

## Workspace Intelligence

- **Session health indicators** — detect stale sessions (started /qdev but never completed, sessions with unanswered blockers), surface as "needs attention" badges
- **Plan progress overlay** — parse plan files to show phase completion status on workspace cards (e.g., "Phase 3/5")
- **kiro-cli usage stats** — dashboard with session counts, durations, tool usage patterns over time
- **Workspace tags/groups** — group workspaces by purpose (personal, work, playbook) with color coding

## Platform

- **Linux support** — run the orchestrator natively on Linux (tray icon, browser UI, terminal detection)
- **kiro-cli v3 session support** — scan `~/.kiro/sessions/<workspace-hash>/sess_*/` alongside v2 `cli/` directory; handle new message format, subagent detection via `sub-executions/` dir

## Quality of Life

- **Keyboard-driven navigation** — vim-style j/k movement, space to select, enter to launch
- **Multi-machine session sync** — see WSL kiro-cli sessions alongside Windows ones in the same UI
- **Peek window** — hotkey-held native window that shows the dashboard while pressed, disappears on release (like a HUD overlay)
