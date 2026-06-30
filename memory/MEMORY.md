# Project Memory — PowerAtlas

## Pattern

### Cache getters must return copies, not references

**Why**: The workspace-count cache returned a raw list reference. A downstream consumer (`partials_workspaces`) appended pinned folders to it, corrupting the cache across requests. The same class of bug was pre-emptively prevented in `SessionCache.get()` by returning `list(sessions)`.
**How to apply**: Any cache `get()` method that returns a mutable collection must return a shallow copy. Callers should not be trusted to avoid mutation — enforce at the cache boundary.
**Source**: `plans/done/260618-1901_SESSION_PRELOAD_CACHE.md` — Post-Implementation Review finding #1 | **Verified**: 2026-06-18


### pywebview main-thread + pynput Ctrl-code quirks on Windows

**Why**: Two non-obvious platform behaviors caused runtime bugs despite passing unit tests: (1) pywebview enforces main-thread execution on Windows too (not just Linux/GTK) — `webview.start()` raises `WebViewException` from any non-main thread, and (2) pynput reports ASCII control codes (0x01–0x1a) instead of letter chars when Ctrl is held on Windows (e.g. Ctrl+Z → `\x1a`, not `'z'`).
**How to apply**: When working with pywebview, always use the main thread regardless of platform. When processing pynput key events with Ctrl held, normalize control codes back to letters via `chr(ord(ch) + ord('a') - 1)`.
**Source**: `plans/done/260630-1607_PEEK_WINDOW.md` — post-implementation empirical testing | **Verified**: 2026-06-30
