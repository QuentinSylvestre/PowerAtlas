# Parse and Poll Performance

> **Date**: 2026-07-25
> **Status**: Complete
> **Last Updated**: 2026-07-25T15:42-05:00
> **Estimated effort**: ~1 day
> **Scope**: Eliminate redundant JSONL parsing and redundant polling work behind slow start, slow card expansion, and slow updates

## Completion Summary

All 5 phases implemented. All P0 and P1 items landed. Measured against the same 800-file / 1.31 GB corpus
used for the diagnosis, comparing pre-change `HEAD` against the optimized code:

| Path | Before | After | Speedup |
|---|---|---|---|
| Cold start — full sweep, page cache dropped | 3150 ms | 1956 ms | 1.6x |
| Card expansion — first, nothing cached | 39 ms | 15 ms | 2.6x |
| Card expansion — re-expand, parse cache warm | 39 ms | 0.2 ms | 178x |
| Refresh tick — one file changed in a 20-file workspace | 38 ms | 1 ms | 36x |
| Full sweep — warm page cache, caches cold | 1817 ms | 602 ms | 3.0x |
| Full sweep — warm page cache, caches warm | 1817 ms | 12 ms | 154x |

Cold start improves least (1.6x) because with the page cache dropped it is disk-bound, not CPU-bound —
the remaining time is reading 1.31 GB off disk, which no amount of parse optimization removes. Every
warm-path number is dominated by work that is now skipped entirely.

### Verification performed

- Differential test: new `_parse_session_file` produces byte-identical output to the pre-change
  implementation across all 800 corpus files plus 15 hand-built edge cases (empty file, malformed JSON,
  invalid UTF-8, CRLF, no trailing newline, title after first prompt, custom-title late in head, the word
  "title" inside message content, no user message within 500 lines, meta/command messages, content blocks,
  hook types, huge tail).
- Scheduler cadence simulated over 10 minutes: refresh/status/active fire at exactly the same timestamps
  as the four timers they replace.
- Full test suite: 501 passing, same 57 pre-existing platform failures as before the change (Windows path
  tests on Linux). 21 tests added.
- `ruff check`: no new findings versus baseline.

### Acknowledged at implementation

- Accepted: cold start remains disk-bound; a persistent index (P2, out of scope) is the next lever if that
  matters in the field.
- Accepted: `_read_meta` does not cache parse failures, so a malformed metadata file is re-read on each
  walk (deliberate — a file mid-write is transiently malformed and should not stick).
- Accepted: head cache reuses on equal-size + equal-mtime, so an in-place same-size rewrite that preserves
  mtime would be missed (not reachable for append-only JSONL; the parse cache has the same property).
- Accepted: negative path-resolution entries live for 5 s, so a brand-new session can take up to 5 s to
  resolve (bounded by the same 5 s status poll it feeds).
- Accepted: the surviving kiro-cli contribution (`_extract_prompts_cached`) is not covered by the benchmark
  above, which exercises the claude-code corpus only. It is unit-tested but its speedup is unmeasured here.

### Merged with main

`main` advanced 14 commits during implementation. Three overlapped:

- `c346982 perf(data_kiro)` — same idea, better executed. Resolved by taking `main`'s implementation whole
  and re-applying only `_extract_prompts_cached` on top (see P0-2 above).
- `a973715` / `ee998c2 fix(presence,status)` — reworked `_read_tail_lines` into a widening retry with a
  64 KB starting window. Auto-merged cleanly with the path memoization and LRU here; both are present and
  independent (one governs how much tail is read, the other how often the path and verdict are recomputed).

Post-merge verification: 523 passing against `main`'s own baseline of 502, with the same 57 pre-existing
platform failures and no new lint findings. The parse differential and benchmark were re-run on the merged
tree; `main` did not touch `data_claude.py` or `index.html`, so those results carry over unchanged.

## Intent

### Problem statement & desired outcomes

PowerAtlas feels slow on start, on card expansion, and on periodic updates. Profiling against a synthetic
heavy-user corpus (40 workspaces x 20 sessions = 800 session files, 1.31 GB) shows the cost is redundant
work, not slow work:

| Path | Measured |
|---|---|
| `discover_workspaces()` | 72 ms cold / 9 ms warm — not a bottleneck |
| `load_sessions()`, one workspace (card expansion) | 53 ms |
| `load_sessions()`, all 800 sessions (start / all-sessions sweep) | 5.28 s |
| `stat()` 20 files | ~0 ms — disk is not the bottleneck |

`cProfile` attributes the bulk of it to `json.loads`: 28,338 calls across 60 files, ~472 per file. That is
`_parse_session_file` (`data_claude.py:254`), which unconditionally reads and JSON-parses the **first 500
lines** of every session file to recover a title and the first user prompt — values that are almost always
within the first handful of lines.

Four secondary problems compound it, none of which are CPU-bound:

1. `refresh_stale_entries` (`data.py:229`) reloads an **entire workspace** when any single file changes.
   The live session file changes every few seconds while coding, so every 30 s tick re-parses everything.
2. `_resolve_jsonl_path` (`status_classifier.py:56`) iterates every kiro-cli v3 workspace directory when the
   v2 path misses. It is uncached and called per-session inside `/api/session-status`, which runs every 5 s —
   O(sessions x workspace_dirs) stat calls per poll.
3. `_status_cache` (`status_classifier.py:321`) is capped at 100 entries and evicts via `min()` over the whole
   dict on every insert: O(n) per write, and thrashing past 100 sessions.
4. First paint requests `/partials/workspaces?fresh=1` (`index.html:73`), which explicitly pops the discovery
   cache that `warmup_all()` just populated at startup.

Plus four independent polling timers (5 s status, 10 s active-session, 15 s burst x8, 30 s steady) that drift
into each other with no in-flight coordination.

**Desired outcomes:**

- Card expansion and the all-sessions sweep get materially faster without changing rendered output.
- A background refresh tick costs work proportional to what actually changed, not to workspace size.
- The 5 s status poll stops doing directory scans and O(n) cache evictions.
- First paint reuses the warmup cache instead of discarding it.

### Success criteria

- SC-1: `_parse_session_file` produces byte-identical results to the current implementation for all session
  shapes covered by the test suite.
- SC-2: `load_sessions()` over the 800-file benchmark corpus is at least 4x faster than baseline (warm cache).
- SC-3: A `refresh_stale_entries` tick where one file changed in a 20-file workspace re-parses only that file.
- SC-4: `_resolve_jsonl_path` performs at most one v3 directory scan per session id per TTL window.
- SC-5: The status cache performs O(1) eviction and holds enough entries not to thrash at realistic session counts.
- SC-6: First paint issues no `fresh=1` discovery.
- SC-7: Exactly one repeating timer drives background refresh, and a slow refresh cannot stack.
- SC-8: The full existing test suite passes unchanged, except where a test asserts on internals being changed.

### Scope boundaries & non-goals

**In scope:**
- `_parse_session_file` read strategy and parse-skipping (`data_claude.py`)
- Per-file parse caches in `data_claude.py` and `data_kiro.py`
- Path-resolution memoization and LRU status cache (`status_classifier.py`)
- First-paint query string and polling-timer consolidation (`index.html`)

**Non-goals:**
- Rewriting any component in Rust. The measured bottleneck is redundant work; a language change would make
  the wasted 500-line scan faster without making it smaller. Revisit only if profiling after this work still
  shows a JSON-parse-bound hot path, at which point `orjson` is a one-line dependency swap.
- Replacing polling with SSE/WebSocket. Consolidating the timers is in scope; changing the transport is not.
- A persistent (sqlite) session index. Deferred until this work is measured in the field.
- Changing rendered HTML, status semantics, or notification behavior.

---

## Design

### P0-1 — Byte-prefilter the head scan (subsumes binary tail reads)

The head loop needs three things: `ai-title`, `custom-title`, and the first `user` message. Once
`first_prompt` is known, the only lines that can still matter are title lines — and every title line
necessarily contains the ASCII bytes `title` (in `"type":"custom-title"` / `"type":"ai-title"`, and in the
`aiTitle` / `customTitle` keys). So after `first_prompt` is set, a line without `title` in it can be skipped
without parsing.

This is conservative in the safe direction: a false positive (the word "title" inside message content) costs
one redundant `json.loads` and stays correct; a false negative is impossible.

Read the file in **binary** mode. `json.loads` accepts `bytes`, and invalid UTF-8 raises `UnicodeDecodeError`,
which subclasses `ValueError` and is already caught by the existing handler — so the current
`errors="replace"` behavior of skipping unparseable lines is preserved. Binary mode also makes the 256 KB
tail seek honest rather than a text-mode cookie seek (measured 9 ms -> 2 ms across 20 files), which is P1-7.

### P0-2 — Per-file parse cache keyed by (mtime, size)

Rather than adding an incremental-reload hook to every provider adapter, cache the parse result per file keyed
by `(mtime, size)`. `load_sessions()` still globs and stats (measured ~0 ms for 20 files) but re-parses only
files whose stat changed.

This makes `refresh_stale_entries` incremental with no API change, and benefits every other caller of
`load_sessions()` for free. Correctness follows from the key: any content change moves mtime or size.

Bounded LRU (`data.BoundedCache`) so a large corpus cannot grow the cache without limit.

**Superseded for kiro-cli.** While this was in flight, `c346982` landed the same idea on `main` for
`data_kiro`, and went further: a `cwd -> files` index cached against the session directory's own mtime, so
`load_sessions()` no longer walks the whole flat store at all. That is strictly better than the parse cache
alone, so on merge the kiro-side work here was dropped in favour of it. What survives is the one gap that
commit left: `_extract_prompts` was still re-parsing each matched session's `.jsonl` (a 50-line head scan
plus a 100-line deque) on every call, so it now goes through `_extract_prompts_cached` with the same
`(mtime, size)` key. The claude-code parse cache is unaffected — `main` did not touch `data_claude`.

### P0-3 — Immutable head cache

`first_prompt` and the first-message timestamp are immutable for a session under append. Cache them per path
and reuse whenever the file has not **shrunk** (JSONL here is append-only; a smaller file means truncation or
rewrite, so invalidate).

Titles are deliberately *not* cached this way — `custom-title` is appended on rename, so titles are re-scanned
every time via the cheap prefilter from P0-1.

This matters for the case P0-1 does not cover: command-heavy sessions with no user message in the first 500
lines, where `first_prompt` never gets set and the prefilter never engages.

### P0-4 — Memoize `_resolve_jsonl_path`

Cache `(session_id, provider, cwd) -> Path | None`. Positive entries are revalidated with a cheap `is_file()`
before reuse, so a deleted file falls through to a full re-resolve. Negative entries get a short TTL so a
newly created session is picked up promptly without rescanning directories on every poll.

### P1-5 — LRU status cache

Replace the plain dict plus `min()`-scan eviction with `collections.OrderedDict`: `move_to_end` on read,
`popitem(last=False)` on overflow. Raise the cap from 100 to 512 — entries are small tuples, and 100 is below
a realistic active-session count.

### P1-6 — Drop `fresh=1` from first paint

`warmup_all()` populates the discovery cache at startup; `fresh=1` on the load-triggered `hx-get` throws it
away and pays full discovery on first paint. Remove it. Staleness is already covered by the periodic refresh,
the explicit refresh button, and the `warmup-status` short-poll that fires `refreshCards(true)`.

Note `/partials/all-sessions` does not accept a `fresh` parameter at all — it is silently ignored there, so
removing it is a no-op for that endpoint and is done only for consistency.

### P1-8 — One scheduler

Replace four independent `setInterval`s with a single 5 s tick dispatching by counter: status every tick,
active-session content every 2nd tick, workspace refresh every 3rd tick during the burst window and every 6th
after. Add an in-flight guard so a slow refresh cannot stack. Cadences are unchanged; only the timer topology
is.

---

## Phases

- **Phase 1** — P0-1, P0-3, P1-7 in `data_claude.py` (one coherent rewrite of `_parse_session_file`)
- **Phase 2** — P0-2 parse caches in `data_claude.py` and `data_kiro.py`
- **Phase 3** — P0-4 and P1-5 in `status_classifier.py`
- **Phase 4** — P1-6 and P1-8 in `index.html`
- **Phase 5** — Test updates, full suite, ruff, and before/after benchmark

## Verification

Benchmark harness generates the corpus under `/tmp/pa-bench` and times `discover_workspaces`,
`load_sessions` for one workspace and for all workspaces, plus the one-file-changed refresh path. Numbers are
taken warm (page cache primed) so they measure CPU rather than disk, and cold via `drop_caches` for the
start-up figure.
