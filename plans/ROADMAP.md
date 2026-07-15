# PowerAtlas — Roadmap

> Non-executed ideas and future features, organized by theme.

---

## Automation & Workflows

- **Dispatch no-interactive tasks** — launch kiro-cli with `--no-interactive` and a prompt, fire-and-forget from the UI
- **Open session with specific prompt/skill** — generic prompt input, local/user skill execution with params
- **Template prompts** — save reusable prompt templates per workspace (e.g., "/qdev {latest plan file}" as one-click)
- **Scheduled tasks** — cron-like recurring kiro-cli launches (e.g., "/qdream every Sunday 10am in agent-playbook")
- **Chained launches** — "when session X exits, auto-launch session Y in folder Z" (pipeline mode)
- **Plan-file shortcuts** — detect plan files in `plans/` and offer one-click "/qdev on this plan" buttons

## Workspace Intelligence

- ~~**Session health indicators** — live 🟢 Working / 🟡 Waiting status dots + status filter~~ — shipped (`260711_SESSION_LIVE_STATUS_AND_FILTER`); semantic status (Active/Needs-input/Idle/Errored) + toast notifications shipped (`260715_SEMANTIC_SESSION_STATUS`); future extension: distinguish blocked-on-approval vs asked-a-question, "stale /qdev never completed" heuristics, sound/chime notifications, and detecting fresh (non-resumed) in-terminal sessions
- **Plan progress overlay** — parse plan files to show phase completion status on workspace cards (e.g., "Phase 3/5")
- **kiro-cli usage stats** — dashboard with session counts, durations, tool usage patterns over time

## Platform

- **kiro-cli v3 session support** — scan `~/.kiro/sessions/<workspace-hash>/sess_*/` alongside v2 `cli/` directory; handle new message format, subagent detection via `sub-executions/` dir

##Misc
- Identify opened sessions
- Focus on opened sessions
- Visualize/interact with opened sessions from PowerAtlas
- Local network access to mimic claude code remote control in semi-remote fashion