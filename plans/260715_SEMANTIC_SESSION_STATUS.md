# Semantic Session Status

> **Date**: 2026-07-15
> **Status**: Exploring  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Scope**: Replace mtime-based working/waiting heuristic with JSONL-derived semantic status (Active/Needs-input/Idle/Errored), add fresh-session detection, and layer toast notifications on status transitions

---

## Intent

### Problem statement & desired outcomes

PowerAtlas's live session status (shipped in `260711_SESSION_LIVE_STATUS_AND_FILTER`) uses a two-step heuristic: psutil process scan (is the process running?) + file mtime check (modified within 60s → "working", else → "waiting"). This produces inaccurate and uninformative results:

- Cannot distinguish "agent executing tools" from "waiting for user input" from "permission prompt pending" from "errored"
- 60-second mtime window causes false "waiting" when agent pauses to think >60s (e.g., complex reasoning)
- Kiro-CLI vs Claude Code asymmetry: kiro-cli `updated_at` comes from metadata `.json` (may lag behind actual activity), Claude Code's comes from `.jsonl` mtime (always fresh)
- Fresh sessions (started without `--resume-id` flag) are invisible to session-row-level detection
- No way to know "the agent finished and is waiting for you" without checking the terminal

The improvement replaces the mtime heuristic with **semantic classification from JSONL tail content** — reading the last few KB of the session transcript to determine the agent's actual state from message types. This is complemented by **toast notifications** that alert the user when an agent transitions from active to idle/needs-input.

Inspired by Omnigent's structured state machine approach, but adapted to PowerAtlas's read-only architecture (no runtime interposition — observe session files on disk only).

### Success criteria

1. Live sessions show one of 4 semantic status dots: **Active** (🟢 pulse — agent working), **Needs input** (🟡 — blocked on user, e.g. permission prompt), **Idle** (⚪ dim — turn complete, waiting for next prompt), **Errored** (🔴 — agent hit a problem). Closed sessions show no dot (unchanged).
2. Status classification is derived from the **last few JSONL lines** (message types), not from file mtime. Mtime heuristic serves only as fallback when JSONL parsing fails or format is unrecognized.
3. Both **kiro-cli v2** and **Claude Code** sessions are supported with equal accuracy. The classifier abstraction is designed so v3 kiro-cli (with richer `pending_interaction` signals) can be plugged in later without redesign.
4. **Fresh sessions** (started without `--resume-id`, invisible to current session-id-based detection) are detected via process-cwd + newest-session-file heuristic and shown with a status dot.
5. **Status filter dropdown** updated to new vocabulary: All / Live / Active / Needs input / Idle / Errored / Closed.
6. **Toast notifications** (opt-in, off by default) fire on Active→Idle, Active→Needs-input, and Active→Errored transitions, with a 1-minute cooldown per session.
7. No noticeable latency increase on the existing 15-30s refresh cycle. Status reads use a dedicated 4KB-read cache with mtime guard (separate from the 128KB tooltip tail cache).

### Scope boundaries & non-goals

**In scope:**
- Semantic status classifier (per-provider JSONL tail parser)
- Status cache with mtime-guarded invalidation (5s TTL)
- Fresh session detection via process-cwd matching
- Expanded status dot vocabulary + CSS
- Updated filter dropdown
- Toast notifications on status transitions (opt-in, 1-min cooldown)
- v3-ready abstraction (interface designed for `pending_interaction`, implementation deferred)

**Non-goals:**
- v3 kiro-cli session discovery/parsing (separate future plan — but the status classifier interface accommodates it)
- Token/cost visibility on session cards (separate feature)
- Sub-agent relationship visualization
- WebSocket/SSE push (stays poll-based)
- Sound/chime notifications
- Tray icon badge count
- Activity sparkline / timeline visualization
- kiro-ide session status (IDE sessions remain excluded — no live CLI process)
