# kiro-cli ACP Client Prototype

> **Date**: 2026-07-25
> **Status**: Exploring  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Scope**: Throwaway prototype of a WebSocket-backed kiro-cli ACP client on a new `/acp` page, validating transport, process supervision and the session model before a from-scratch rebuild

---

## Intent

### Problem statement & desired outcomes

PowerAtlas is a read-only observer. It infers what agent sessions are doing by scanning the
process table (`presence.py`) and tailing JSONL transcripts (`status_classifier.py`). Every
item under **Automation & Workflows** in `plans/ROADMAP.md` — dispatch no-interactive tasks,
open a session with a specific prompt or skill, template prompts, scheduled tasks, chained
launches — needs one capability PowerAtlas does not have: **a way to send a prompt to an agent
without a terminal.** ACP is that capability.

This project builds a deliberately throwaway prototype to learn what an ACP client costs and
what it yields, before committing to a design. The deliverable is knowledge and a validated
architecture, not reusable code — the intent is to rebuild from scratch afterwards.

Two constraints, both established by prior spikes, define the reachable shape:

- **ACP can resume any session that has exited, including ones the user's terminal created**,
  but it can **never** attach to a session currently running in a terminal. Concurrent attach
  is hard-refused in 0.73–0.84 s (`-32603 … "Session is active in another process (PID n)"`),
  because `kiro-cli chat` is itself a wrapper around `kiro-cli acp` and the grandchild holds
  the lock. A "live window onto my running sessions" product is not available.
- **Attaching or creating mutates the user's real session store.** Verified during this
  exploration: `session/new` writes `<sid>.json`, `<sid>.jsonl` (0 bytes) and `<sid>.lock`
  into `~/.kiro/sessions/cli/` at creation time, before any prompt. Sessions created by the
  prototype are permanent artifacts among the 13,227 already there.

Desired outcome: a working chat surface at `/acp` that proves the transport, the supervised
subprocess, and the server-side session registry, so the automation items become follow-on
work against a known-good substrate rather than an unexplored one.

### Success criteria

1. From `/acp`, create a new kiro-cli session against a chosen directory and see response text
   arrive incrementally as it is generated.
2. Resume an exited session — including one created in a terminal — and see its prior history
   rendered before the new turn begins.
3. A session survives a page reload: reopening `/acp` reconnects and replays the conversation
   from the server-side event log, without creating a second session.
4. An in-flight turn can be cancelled (`session/cancel`), and the UI reflects the cancellation.
5. Tool calls render as discrete, identifiable items rather than as undifferentiated text.
6. Live context-window usage percentage is displayed for the active session.
7. Quitting PowerAtlas terminates the ACP process **and its MCP grandchildren** — no orphaned
   process tree survives. (Measured: one session is 1 parent + 5 descendants.)
8. The WebSocket handler rejects connections whose `Origin` does not match the bound address,
   and this is demonstrated rather than assumed.
9. Typing in the dashboard search box with a status filter active no longer returns 500.

### Scope boundaries & non-goals

**In scope**

- A new `/acp` page route and a WebSocket route, both in `web.py`.
- A new ACP module: NDJSON codec, JSON-RPC correlation, one supervised `kiro-cli acp` process,
  a session registry with a bounded per-session event log, and tree-kill teardown.
- `websockets` added to `pyproject.toml` (uvicorn's preferred implementation; currently
  `AutoWebSocketsProtocol is None`, so WebSocket support does not exist in this environment).
- Entry from the existing dashboard: a session-row action opening `/acp`, plus a "new session"
  affordance on the page itself.
- The one-argument fix at `web.py:989`.

**Explicit non-goals**

- **Tests.** Prototype only, to be rebuilt. This waives `AGENTS.md:8`; see the proposed
  durable amendment below.
- **Linux.** Windows only for this prototype.
- **Claude Code.** Its ACP path requires the `@zed-industries/claude-code-acp` npm bridge — a
  separate bootstrap — despite the roadmap item naming both providers.
- **Permission prompts.** Sessions are created with `-a` (trust-all-tools), matching the
  existing `provider_settings.kiro-cli` default. Reopens when driven unattended or when this
  stops being a prototype.
- **`presence.py` changes and dashboard status integration.** ACP sessions will read as closed
  in the main dashboard after the ACP process's first two minutes (see Risks). `/acp` shows
  their true state; that is sufficient here.
- **README updates and new config keys.** Agent, model and effort are hardcoded.
- **A session browser on `/acp`.** Entry is from the dashboard, which already lists sessions.
- **The automation features themselves** — scheduled, templated and chained launches are
  what this unblocks, not what it delivers.

### Bug fix scope — `web.py:989`

Included as an independent phase so it lands separately from the ACP work.

`_workspace_status(snapshot, cwd, providers)` is declared with three parameters at
`web.py:244-245`; line 989 passes four, having retained a `g["latest_updated"]` argument that
commit `51fc500` removed from the other two call sites (`web.py:722`, `:754`). Reachable from
the UI by typing in search while a status filter is active, which 500s; `index.html:333` does
not check `r.ok`, so the error page body is injected into `#workspace-cards`.

**Invariants** — must hold after the fix:

- `/search` with `q` set and `status` in `("", "all")` continues to behave exactly as today.
- `/search` with `q` and a real status filter returns filtered workspace cards, not a 500.
- The status filter's meaning stays identical to `partials_workspaces` — same
  `_status_matches` / `_workspace_status` pairing, no divergent filtering logic.
- No change to `_workspace_status`'s signature or behaviour; this is a call-site fix only.

### Proposed durable governance amendment

Requested by the user during exploration; contradicts `AGENTS.md:8` as written, so recorded
here for an accept/drop decision at `/qclose` rather than applied silently.

> Add to `## Doc & Test Guidelines`:
> - Work explicitly scoped as a throwaway prototype (to be rebuilt rather than extended) is
>   exempt from the test and README requirements above. Record the exemption in the project
>   file. The exemption does not carry over to the rebuild.

---

## Harness Improvement Opportunities

- `/qexplore`'s Step 3 output contract defines 9 sections, but only items 1–3 (Intent) are
  persisted to the project file; items 4–9 (patterns, risks, resolved decisions, open items,
  recommended approach, QA environment) exist only in the chat transcript — cost: in a long
  exploration the Discovery half is the more expensive output, and it is lost unless `/qplan`
  runs in the same session; here it was preserved only by hand-copying into this file —
  suggested change: persist Discovery to the project file under a transient
  `## Exploration Discovery` heading that `/qplan` folds in and removes, mirroring the existing
  `## Resolved Decisions (re-plan handoff)` precedent.
