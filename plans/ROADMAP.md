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

## Session Control & Integration

> Three candidate paths to move PowerAtlas from read-only observation toward control. All three were spiked on 2026-07-24; raw findings, wire logs and captured payloads are archived outside the repo at `Downloads\260724_PowerAtlas-spikes\` (see `REPORT.md` there). Claims below are marked *verified* (observed on this machine, with logs) or *unverified* (inferred from shipped code or docs, not executed). Findings are pinned to Claude Code `2.1.219` and kiro-cli `2.14.0`/`2.14.1`.

- **Baseline — is the current mechanism actually inadequate?** Not yet established. `presence.py`'s psutil scan cost, its 3-second snapshot TTL, `status_classifier.py`'s 5-second cache, and the real-world frequency of the fresh-session miss have never been measured. Every effort estimate below is an improvement over an unquantified baseline. Measure this before committing to any path.

- **Push-based session status via hooks (Claude Code + kiro-cli)** — replace polling with agent-emitted events
  - *Problem it solves*: `status_classifier.py` derives Working/Waiting/Errored by tailing JSONL transcripts behind a 5-second cache, and `presence.py` detects liveness by matching the session id in a running process's command line (`--resume` for claude-code, `--resume-id` for kiro-cli, `presence.py:63-66`) — so a freshly started session with no id on argv is not matched **at row level**. Note `presence.py:15-17`: workspace-level liveness already works via cwd tracking, so the gap is narrower than "sessions are invisible".
  - *Verified — mechanism works end to end*: a project-level `.claude/settings.json` drove 28 real hook events into a FastAPI endpoint across 4 sessions; 7 hook types fired, zero delivery failures.
  - *Verified — `SessionStart` fires for fresh sessions*, carrying `source: "startup"` (vs `"resume"`), a `session_id` byte-identical to the JSONL name `status_classifier.py:66-70` keys on, plus `transcript_path` and `cwd` outright — which removes the need for `_get_project_folder()`'s cwd-mangling. Confirmed three independent ways.
  - *Verified — Working vs Waiting is derivable from `hook_event_name` alone*, no transcript read. `Stop.last_assistant_message` even carries the final text. **Trap**: `SubagentStop` fires ~5s before the parent `Stop`; a rule that does not check `hook_event_name` (and `agent_id`, present on `SubagentStop`, absent on `Stop`) will flip a session to Waiting mid-work.
  - *Verified — the catalogue is far larger than documented*: 31 hook event types have schemas in the 2.1.219 binary. Only 8 were wired. The unwired set includes `PermissionRequest`, `PermissionDenied`, `StopFailure`, `PostToolUseFailure`, `TeammateIdle`.
  - *Verified — kiro-cli HAS hooks*, resolving the previous unknown. Not in `kiro-cli settings` (no hook keys) and not in the empty `~/.kiro/hooks/`, but **per-agent in the agent JSON** — both `agent_config.json.example` and the live `kiro_default.json` carry a top-level `"hooks": {}`. Five triggers, extracted from the binary: `agentSpawn`, `userPromptSubmit`, `preToolUse`, `postToolUse`, `stop`. Per-hook fields: `command`, `matcher?`, `timeout_ms?` (default 10s), `max_output_size?` (default 10240), `cache_ttl_seconds?`.
  - *Unverified — the load-bearing kiro-cli question*: whether `agentSpawn`'s payload carries a **session id**. If it does, it closes the row-level gap for kiro-cli exactly as `SessionStart` does for Claude Code. If it does not, it adds nothing over existing workspace-level liveness. One field decides whether this half is worth 1-2 days or zero.
  - *Limitations, verified*: hooks instrument **only sessions started after installation** — a project settings file written mid-session is not picked up by the running session. No heartbeat, so a SIGKILLed session leaves stale state (`SessionEnd` on abnormal exit is untested); `presence.py`'s scan stays as the liveness backstop rather than being replaced. kiro-cli config is **per agent**, so install means editing every agent JSON and silently misses agents added later. kiro-cli has **no permission/notification trigger at all**, so "blocked, needs a human" is unavailable on that side.
  - *Gotchas for implementation*: `config.py:53` defaults the port to 0 (OS-assigned), so a hook command cannot hardcode one; `same_origin_guard` (`web.py:371-383`) 403s any POST lacking `Origin`/`Referer`. A file-spool transport sidesteps both.
  - *Effort*: ~4.5-6 days Claude-Code-only; +1-2 days for kiro-cli with high variance, and it should not start until `agentSpawn` is observed firing. Highest risk is the installer that merges into the user's live `settings.json`, not the plumbing.

- **ACP client (kiro-cli + Claude Code)** — structured agent control over JSON-RPC instead of terminal scraping
  - *What it buys, verified on kiro-cli*: streaming `agent_message_chunk`s, `tool_call`/`tool_call_update` with real command and output, a clean `session/request_permission` round trip, `stopReason` as an authoritative idle signal, and `_kiro.dev/metadata` carrying live **context-window usage percentage** and turn duration — data unavailable from transcript tailing.
  - *Verified — `kiro-cli acp` works today with no auth at all* (`"authMethods":[]`). Framing is **NDJSON**, not LSP `Content-Length`. `session/new` takes ~3.2s (MCP startup dominates). Options: `--agent`, `--model`, `--effort`, `--trust-all-tools`, `--trust-tools`, `--agent-engine v1|v2|v3` (default v2).
  - *Verified — explicit slash-command invocation is ordinary prompt text*, not a proprietary RPC. `/tools` sent as prompt text returned in **1 millisecond** with a canned count — impossible for a model round-trip, proving agent-side interception. **This inverts the previous assumption**: `_kiro.dev/commands/available` is an agent→client *push notification* carrying the catalogue (24 built-ins, all 25 user skills as `prompts` with `serverName: "skill:config"`, 15 tools, live MCP server status); calling it as a client→agent request returns `-32601`. A standards-only client sees skills fine.
  - *Verified — `_kiro.dev/commands/execute` kills the agent process with exit code 0, no stderr, no JSON-RPC error.* Reproduced 2/2 across separate processes and two param shapes. Combined with the process writing nothing to stderr all session, a supervisor can trust **neither exit code nor stderr** for health. Also: one process is a **tree** (MCP servers are grandchildren — a plain kill leaks them).
  - *Verified — Claude Code ACP gate is OPEN.* The `@zed-industries/claude-code-acp` bridge hardcodes `settingSources: ["user","project","local"]` (`acp-agent.js:777`), which is the documented source loading `~/.claude/skills/`; prompt text passes through unrewritten (`:992-1006`); no `/q*` skill is on its `UNSUPPORTED_COMMANDS` deny-list. Static analysis only — never executed.
  - *Verified — two Claude Code SDK-path gaps*: `allowed-tools` in SKILL.md is **silently ignored** through the SDK, and the bridge **drops hook lifecycle events** (`hook_started`/`hook_progress`/`hook_response` fall through to `break;` under a `// Todo`) — precisely the data PowerAtlas wants. Statusline is absent from both packages.
  - *Unverified — the scope-defining question*: whether `session/load` can attach to a session PowerAtlas already tracks, and whether a session can be attached while it is also live in a terminal. `loadSession: true` is advertised but was never exercised. If it cannot attach, ACP only covers sessions PowerAtlas itself launched — a much narrower product than this item implies.
  - *Unverified*: whether an invoked skill loads the SKILL.md **body** or the model improvises from the one-line description (~50% confidence either way — no wire evidence, expansion is agent-side). Whether one process can multiplex sessions, which drives process-count math. `reject_once` behaviour.
  - *Risks*: young protocol subject to breaking changes; the binary **self-updated mid-spike** (2.14.0→2.14.1), which a long-lived launcher will hit; Windows consumers must force UTF-8 and strip ANSI escapes.
  - *Product note*: a full ACP client turns PowerAtlas into an agent chat panel, a different product from the current launcher/dashboard, overlapping what Zed ships. Worth running for the state/control learning; committing the product to it is a separate decision.
  - *Effort*: ~3-5 days. JSON-RPC peer ~1d; process supervision ~1-1.5d (the larger, less obvious half); state-model mapping ~1d; permission UI ~0.5-1d.

- **`kiro-cli serve`** — persistent V3 agent server over WebSocket
  - *Verified — it is genuine ACP over WebSocket*, not a Kiro-specific protocol. `serve` is a thin wrapper launching `@kiro/agent/dist/server/acp-server.js --transport=ws --auth=acp-callback` under Node; the whole 20 MB implementation is readable on disk under a content-hashed per-release path. Flag surface is only `--port` (default 8082), `-v`, `-h`. **v3-only structurally** — a different binary, not a mode switch.
  - *Verified — it is a multiplexer.* Multiple simultaneous clients attach with `role=observer`; only the first `initialize` reaches the agent and later clients get a cached result, so a second observer is cheap and does not restart the agent. Good shape for a dashboard. (It also leaks its connection count.)
  - *Verified — session-mutating calls are blocked.* `session/new` and `session/load` hang forever: `serve` hardcodes `--auth=acp-callback`, in which the agent requests its token *from the client* via `_kiro/auth/getAccessToken` — and observer-role clients never receive that frame. `session/new` reaches the agent (~100ms, id allocated and logged) but never becomes addressable.
  - **New, and the cheapest opportunity found: `session/list` works unauthenticated, today.** It returned 23 sessions spanning weeks, each with `_meta.kiro.status`, `title`, `cwd`, `updatedAt`, `agentMode` and sometimes a human-readable `description` — precisely the metadata `status_classifier.py` reverse-engineers from JSONL. This is a **read-only status source**, a different and far cheaper use than the control/hosting framing: no hooks to install, no per-agent config, no forwarder, no port coordination, and it covers sessions started **anywhere including a terminal**, which hooks structurally cannot do retroactively.
  - *Unverified — the make-or-break*: whether `_meta.kiro.status` is **live or last-persisted**. All 23 observed sessions read `"idle"`; a live one was never seen. If last-persisted, the read-only idea is dead; if live, it is the cheapest win available. ~30 minutes to settle.
  - *Unverified*: how a client becomes eligible to receive agent→client requests (settled by reading `MultiplexStream` in the bundle, ~30 min, or by capturing what the undocumented first-party `kiro-cli --remote <ws-url>` client sends). Whether `session/load` restores context. Whether skills work over it. `session/update` and `session/request_permission` shapes, never observed.
  - **Security, verified**: the server binds `0.0.0.0` with **no authentication on the WebSocket upgrade** — no subprotocol, no header, no token. Any local process or routable host can connect and enumerate every session on the machine; capabilities claim `sessionListScopes: ["workspace"]` but listing is machine-global in practice. There is no `--host`/`--bind` flag. Corroborated by an unrelated Firefox tab attaching to the probe server by accident. This is a property of the tooling and warrants a decision independent of whether PowerAtlas adopts the path.
  - *Scope note*: kiro-cli only. Provides no path for Claude Code, so it complements rather than replaces the items above.
  - *Effort*: ~4-7 days for the read-only status slice, with the auth/role blocker carrying nearly all the risk. Full control adds 1-2 weeks and cannot be honestly estimated until the `session/update` shape is known.

- **Open experiments, cheapest first** — roughly two days total, de-risking ~15 days of building
  1. Does `_meta.kiro.status` go non-idle during a live turn? (`serve` + `session/list`) — decides whether an entire cheap path exists.
  2. Does `agentSpawn` carry a session id? (kiro-cli hooks) — decides whether the kiro-cli hooks half has value over existing workspace liveness.
  3. Do `PermissionRequest`/`Notification` fire, and what do they carry? (Claude Code hooks) — settle **before** designing the state model, so it is built on the full event set.
  4. Can `session/load` attach to a session PowerAtlas already tracks, including one live in a terminal? (`acp`) — a product-scope question, not an implementation detail.
  5. What does the current `presence.py`/`status_classifier.py` baseline actually cost and miss? — the yardstick everything above is measured against.

##Misc
- Identify opened sessions
- Focus on opened sessions
- Visualize/interact with opened sessions from PowerAtlas
- Local network access to mimic claude code remote control in semi-remote fashion