# Live Session Status Redesign

> **Date**: 2026-07-23
> **Status**: Exploring  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Scope**: Redesign live session detection and status indicators with cwd-based matching and simplified 4-state vocabulary

---

## Intent

### Problem statement & desired outcomes

The live session status feature in PowerAtlas is critically broken for the primary use case. Sessions started from a terminal (`kiro-cli chat -a`) lose their status indicator after 90 seconds because the detection system requires `--resume-id` on the process cmdline — a flag only present when resuming via PowerAtlas. The semantic JSONL classifier (which correctly identifies agent state) is gated behind this cmdline matching and never fires for most sessions.

Additionally, the 6-option status vocabulary (Live/Active/Needs input/Idle/Errored/Closed) adds confusion without signal — "Live" vs "Active" are indistinguishable to users, and the grey "Idle" dot is unactionable noise.

**Desired outcomes:**
- All running kiro-cli/claude sessions show a status dot regardless of how they were started
- Status vocabulary is simplified to 3 actionable states + absence (Working/Waiting/Errored/no-dot)
- Workspace cards show at-a-glance status (highest-priority session dot)
- Status filter works correctly (no "No matching sessions" when sessions are clearly running)
- The workspace panel filter-reset ordering bug is fixed

### Success criteria

1. A `kiro-cli chat -a` session started from a terminal shows a green "Working" dot while the agent executes tools, and transitions to yellow "Waiting" when the agent finishes its turn — indefinitely, not just for 90 seconds.
2. A session resumed via PowerAtlas (with `--resume-id`) shows correct status dots (same behavior, higher-confidence detection path).
3. Workspace cards display a status dot reflecting the highest-priority session status (Errored > Waiting > Working > none).
4. The status filter dropdown offers All / Working / Waiting / Errored and correctly narrows both panels.
5. Expanding a workspace card with the "Working" or "Waiting" filter active shows the matching session rows (no "No matching sessions" when a process is running).
6. Notifications fire on Working→Waiting and Working→Errored transitions.
7. v3 kiro-cli sessions (`messages.jsonl` format) are classified correctly.
8. Switching status filters and resetting to "All" preserves pinned-first ordering and time-group headings.

### Scope boundaries & non-goals

**In scope:**
- Rewrite `_session_status()` gate to use cwd-based association (remove the `is_explicitly_live OR is_fresh` gate)
- New 4-state vocabulary: Working (green pulsing), Waiting (yellow/orange), Errored (red), no dot (closed)
- Implement `classify_kiro_v3()` for v3 session JSONL format
- Add status dot to workspace card template (highest-priority aggregation)
- Simplify status filter dropdown (All / Working / Waiting / Errored)
- Fix `refreshCards()` workspace panel ordering bug on filter transitions
- Update notification transitions to Working→Waiting and Working→Errored

**Non-goals (deferred to roadmap):**
- Kiro IDE live session detection (different architecture — no CLI process, no JSONL)
- Read-tracking / "unread" concept (aspirational — would require per-session last-seen timestamp)
- Using `kiro-cli acp` as a background work API from PowerAtlas
- Subcommand filtering (both `chat` and `acp` are valid liveness signals for the same session)

## Resolved Decisions

- Q1: Detection strategy — A: Use cwd-based association for most-recently-updated session, with --resume-id as higher-confidence override — Decision: Remove the is_explicitly_live/is_fresh gate; when a process runs in a cwd, classify all sessions in that workspace from their JSONL tails
- Q2: Status vocabulary — A: 4-state: Working/Waiting/Errored/no-dot — Decision: Working (green pulsing) = agent executing; Waiting (yellow) = agent finished, your turn (ersatz unread); Errored (red) = error detected; no dot = no process
- Q3: "Unread" concept — A: Agent finished its turn, needs user input (detectable from JSONL tail) — Decision: No read-tracking needed; "Waiting" = last line is AssistantMessage and process is running
- Q4: Read tracking achievability — A: No read tracking (Option A) — Decision: Waiting state covers the useful case; read-tracking deferred as aspirational
- Q5: Idle/done/closed distinction — A: Map "process running but idle" to Waiting; no dot for no process — Decision: Single absent-dot state for all non-running sessions
- Q6: Subagent (acp) filtering — A: No filtering needed — Decision: Both chat and acp processes are valid liveness signals (same user session, same cwd)
- Q7: Filter options — A: All / Working / Waiting / Errored — Decision: 4 filter options replacing the current 7
- Q8: Workspace-level indicators — A: Dot on workspace card, priority Errored > Waiting > Working > none — Decision: Workspace cards show highest-priority dot
- Q9: refreshCards ordering bug — A: Include in this plan — Decision: Fix the differential DOM update to handle structural elements
- Q10: Kiro IDE sessions — A: Scope out, defer to roadmap — Decision: Not included in this plan
- Q11: v3 kiro-cli classifier — A: Include — Decision: Implement classify_kiro_v3 using documented messages.jsonl format
- Q12: Notification transitions — A: Working→Waiting and Working→Errored only — Decision: Same intent as today with new vocabulary
- Q13: Multiple sessions same workspace — A: Classify each independently from its own JSONL tail — Decision: Each session gets its own dot based on its JSONL state; mtime distinguishes actively-written (Working) from stale (Waiting)

## Harness Improvement Opportunities

- The OpenAI Codex Micro page (work-louder keyboard product page) was not a useful reference for status classification design — it only mentions "thinking, running, waiting, done" in marketing copy with no specification. The user's intent was to reference a simpler model, not that specific page. A future `/qexplore` could ask "what specifically from that reference applies?" earlier. — cost: one wasted web_fetch + time parsing a product page — suggested change: when user references an external URL for design inspiration, ask what specific aspect to extract before fetching.
