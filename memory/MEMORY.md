# Project Memory — kiro-orchestrator

## Pattern

### Cache getters must return copies, not references

**Why**: The workspace-count cache returned a raw list reference. A downstream consumer (`partials_workspaces`) appended pinned folders to it, corrupting the cache across requests. The same class of bug was pre-emptively prevented in `SessionCache.get()` by returning `list(sessions)`.
**How to apply**: Any cache `get()` method that returns a mutable collection must return a shallow copy. Callers should not be trusted to avoid mutation — enforce at the cache boundary.
**Source**: `plans/done/260618-1901_SESSION_PRELOAD_CACHE.md` — Post-Implementation Review finding #1 | **Verified**: 2026-06-18
