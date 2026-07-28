# Session Tooltip Rework

> **Date**: 2026-07-28
> **Status**: Exploring  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Scope**: Fix viewport crop, add session-id display, add user last message section, add markdown rendering to the session hover tooltip

---

## Intent

### Problem statement & desired outcomes

The session hover tooltip has four outstanding issues:

1. **Top-of-window crop**: when a session row is near the top of the viewport, the tooltip is shrunk to fit in the available space above, or hidden entirely when fewer than 100px are available. The tooltip should instead flip to open below the row when space above is insufficient.

2. **Session-id not visible**: the tooltip header shows workspace name and session title but not the session-id, which is often needed for debugging and resuming sessions. The session-id should appear right-aligned inline with the workspace name.

3. **Missing user last message**: the tooltip shows the user's first message and the agent's last message, but no view of the user's most recent message. A "User last message" section should appear between the two existing sections.

4. **No markdown rendering**: agent and user messages frequently contain markdown (code blocks, bold, lists). Currently the content is displayed as raw text with markdown syntax visible. Messages should be rendered as formatted HTML.

Desired outcomes: a tooltip that never crops off-screen, provides full session context at a glance (workspace, title, session-id, first user message, last user message, last agent message), and renders message content in readable formatted markdown.

### Success criteria

1. When a session row is near the top of the viewport, the tooltip opens below the row (not cropped or hidden) with `maxHeight` constrained to available space below
2. When both above and below space is insufficient (<100px), the tooltip opens on whichever side has more space; suppressed only when neither side reaches 100px
3. Session-id is displayed right-aligned on the same line as the workspace name in the tooltip header
4. "User last message" section appears between "User original message" and "Agent last message"; shows "—" when `last_prompt` is empty (cache miss or unpopulated)
5. All text sections (user first message, user last message, agent messages) are rendered as markdown HTML
6. `hideTail` resets all inline styles set by `loadTail` (`left`, `top`, `transform`, `maxHeight`) to prevent state leaks across row hovers
7. `mistune` is configured in safe mode (raw HTML stripped) so markdown rendering introduces no XSS surface
8. All three existing session-tail endpoint tests pass with updated assertions; no new test files introduced

### Invariants

- Tooltip trigger mechanism unchanged: 300ms debounce on `onmouseenter`/`onmouseleave`, same `loadTail`/`hideTail` entry points
- Workspace name and session title continue to appear in the tooltip header
- "User original message" (first prompt) and "Agent last message" sections remain present with their existing labels
- Empty-response short-circuit (`tail-empty` / "No recent output") unchanged
- `pointer-events: none` on `.session-tooltip-slot` retained (prevents hover flicker)
- `resetOverlays()` and `pollActiveSessions` tooltip-slot hide behavior unchanged

### Scope boundaries & non-goals

**In scope**: `session_tail.html` template, `loadTail`/`hideTail` JS in `index.html`, `style.css` tooltip CSS, `web.py` `/partials/session-tail` endpoint, `pyproject.toml` (add `mistune`), `tests/test_web.py` (update 3 existing tests).

**Non-goals**: changing the number of agent messages shown (`max_lines=15` stays); changing the `get_session_tail` or `get_first_prompt` data functions; adding interactivity to the tooltip (buttons, links); changing tooltip width (`max-width: 800px` stays); peek window behavior; session row card content changes.

---

## Exploration Discovery

<!-- Transient: /qplan folds these into the planning sections and removes this section. -->

### Existing patterns & constraints

- Step 1.5 dispatched the code-tracing trio — in-scope files were predominantly `.py`, `.html`, `.css`, `.js` source code.
- **Tooltip data pipeline**: `loadTail` JS (`index.html:582`) → `GET /partials/session-tail` (`web.py:1821`) → `data.get_session_tail` + `data.get_first_prompt` → `session_tail.html` template
- **`session_id` in endpoint scope**: `sid` query param is already in scope at `web.py:1822` but not forwarded to the template context (`web.py:1837–1843`). Adding `"session_id": sid` to the dict is the entire change needed.
- **`last_prompt` already on `Session`**: `data.py:34` — `Session.last_prompt: str` is populated by all three providers (kiro-cli `data_kiro.py:310`, claude-code `data_claude.py:407–411`, kiro-ide `data_kiro_ide.py:217`), capped at 200 chars. The endpoint already iterates the session cache to find `session_title`; `s.last_prompt` from that same matched object is the source — no new data fetch.
- **Jinja2 autoescaping**: Starlette's `Jinja2Templates` (`web.py:482`) enables autoescaping for `.html` files by default. Markdown-rendered HTML must use `{{ value | safe }}` after mistune sanitizes input. [unverified: Starlette constructor details]
- **`.tail-header` is block-layout** (`style.css:247`): currently `.tail-workspace` and `.tail-title` are stacked block children. Converting the workspace row to `display: flex; justify-content: space-between` achieves right-alignment of session-id without affecting `.tail-title` below.
- **`.session-title-row` precedent** (`style.css:162–164`): uses `display: flex; align-items: center; gap: 8px` with `margin-left: auto` on `.session-time` for right-alignment. Same pattern applies to the session-id in `.tail-header`.
- **Custom htmx-mini** (`static/htmx.min.js`): `htmx.process(slot)` must be called after `slot.innerHTML` assignment. Current `loadTail` already does this. `session_tail.html` has no htmx attributes so `process()` is a no-op, but the call must remain.
- **`package_data`** (`pyproject.toml:22`): `"power_atlas": ["static/**", "templates/**"]` — new static files are automatically bundled. No CI/CD pipeline.
- **Test convention** (`tests/test_web.py:539–590`): three tests use `@patch("power_atlas.web.data.session_cache")`, `@patch("power_atlas.web.data.get_first_prompt")`, `@patch("power_atlas.web.data.get_session_tail")`. `Session` is constructed directly with all 8 fields. No new mock patches needed for `last_prompt` (read from the already-mocked `mock_cache.get.return_value`).
- **Prior art** (`plans/done/260708-1624_SESSION_TOOLTIP_IMPROVEMENTS.md`): the previous tooltip plan added workspace name, session title, User:/Agent: labels, viewport clamping (upward only), and dynamic max-height. This plan extends that work — the clamping logic is the specific area being replaced.

### Risks & mitigations

- **XSS via markdown `| safe`** (High without mitigation): mistune must be configured with `HTMLRenderer(escape=True)` (mistune 3.x default) or equivalent safe mode. All message content passes through mistune before reaching `| safe`. Risk: if mistune version or config changes, raw HTML passthrough could enable XSS. Mitigation: pin `mistune>=3.0,<4` in `pyproject.toml`; add a test asserting `<script>` in a message is stripped in rendered output.
- **Cache miss for `last_prompt`** (Low): sessions not yet in the workspace cache (e.g., first hover before workspace is expanded) will have `last_prompt = ""` → displays "—". Consistent degradation with `session_title`. Mitigated by warmup which pre-loads pinned sessions.
- **Viewport measurement timing** (Low): `tooltip.scrollHeight` is measured after `slot.innerHTML = html` and `tooltip.style.maxHeight = maxH + 'px'`. If the browser hasn't reflowed yet, `scrollHeight` may be stale. Mitigated: this is the same pattern that worked in the previous plan (proven in QA).
- **mistune output changes on upgrade** (Low): markdown rendering of edge cases (e.g., bare URLs, line breaks) may change between minor versions. Mitigated by pinning `<4`.
- **`test_data.py` timing flakiness** (known, pre-existing): `(mtime, size)`-keyed caches make 8 tests intermittently flaky. Not introduced by this plan. Re-run standalone to distinguish from regressions.

### Resolved decisions

- Q1: How should the tooltip behave when near the top of the viewport? — A: flip below the row — Decision: when `desiredTop - effectiveH < 0`, position tooltip below `rect.bottom` with `maxHeight = window.innerHeight - rect.bottom - 8`; if neither side has ≥100px, open on the larger side; suppress only when max of both sides < 100px
- Q2: Where should session-id appear in the tooltip header? — A: right-aligned inline with workspace name — Decision: add `.tail-session-id` right-aligned via flexbox on the workspace name row; `.tail-title` remains below
- Q3: Where should the user last message section appear? — A: between first user message and agent last message — Decision: new `.tail-section` with label "User last message:" sourced from `s.last_prompt`; shows "—" when empty
- Q4: Markdown rendering approach — A: server-side Python library — Decision: add `mistune>=3.0,<4` to `pyproject.toml`; render all message sections (first_prompt, last_prompt, messages) via mistune in safe mode; use `{{ value | safe }}` in template
- Q5: Empty `last_prompt` display — A: show "—" or "N/A" — Decision: display "—" (dash), consistent with common UI convention for missing values
- Q6: Apply markdown to which sections? — A: all sections — Decision: markdown rendering applies to first_prompt, last_prompt, and each message in messages
- Q7: Which Python markdown library? — A: mistune — Decision: `mistune>=3.0,<4` (BSD license, pure Python, safe-mode HTML rendering)

### Open items

None — all decisions resolved.

### Recommended approach

**Phase 1 — Endpoint enrichment + template + CSS**

1. Add `mistune>=3.0,<4` to `pyproject.toml` dependencies.
2. In `web.py`, import mistune and create a module-level `_md = mistune.create_markdown(escape=True)` (or equivalent safe renderer). In `partials_session_tail`:
   - Add `"session_id": sid` to the template context.
   - Read `last_prompt` from the matched Session object in the cache loop (`s.last_prompt`); fall back to `""` on cache miss.
   - Pass `"last_prompt": last_prompt` to the template context.
   - Apply `_md()` to render `first_prompt`, `last_prompt`, and each item in `messages` before passing to template (or apply via a Jinja2 filter).
3. In `session_tail.html`:
   - Make the workspace name row a flex row: wrap `.tail-workspace` and a new `.tail-session-id` span in a flex container.
   - Add `{{ session_id }}` right-aligned in that row.
   - Add a new `.tail-section` between first-prompt and agent sections for "User last message:" with `{{ last_prompt | safe }}` (or "—" fallback).
   - Change all `{{ variable }}` to `{{ variable | safe }}` for markdown-rendered content (since content has already been sanitized by mistune).
4. In `style.css`:
   - Add `.tail-header-row { display: flex; align-items: center; justify-content: space-between; }` for the workspace/session-id line.
   - Add `.tail-session-id { font-size: 10px; color: var(--text-dim); font-family: monospace; opacity: 0.7; }`.
   - Add CSS for rendered markdown content inside `.tail-line` (e.g., `code`, `pre`, `strong`, `em` resets to fit the tooltip's compact style).
5. Update `tests/test_web.py` — the three existing session-tail tests:
   - Update `Session` mock to include a non-empty `last_prompt`.
   - Add assertions for `session_id`, `last_prompt` content, and `tail-session-id` class.
   - Add a test asserting `<script>` tags in message content are stripped.

**Phase 2 — Positioning rewrite (loadTail/hideTail)**

1. Replace the clamping branch in `loadTail` with flip-below logic:
   - `spaceAbove = rect.top - 8`
   - `spaceBelow = window.innerHeight - rect.bottom - 8`
   - If `spaceAbove >= effectiveH`: open above (current normal path).
   - Else if `spaceBelow >= 100`: open below at `rect.bottom + 4`, `maxHeight = spaceBelow`.
   - Else if `spaceAbove >= 100`: open above clamped (current clamp path, kept as last resort).
   - Else: suppress.
2. In `hideTail`: reset `slot.style.left`, `slot.style.top`, `slot.style.transform` in addition to the already-reset `tooltip.style.maxHeight`.

### QA environment

- Start the app: `.venv-PowerAtlas\Scripts\power-atlas` (or `python -m power_atlas` from checkout)
- Web UI: `http://localhost:<port>` (random port by default; printed on startup)
- Test runner: `.venv-PowerAtlas\Scripts\pytest tests/test_web.py -v` (after `pip install -e ".[dev]"`)
- Runtime verification surfaces: hover a session row → tooltip appears; hover near top → tooltip flips below; hover near bottom → tooltip opens above; inspect session-id displayed; inspect "User last message" section; inspect markdown rendering in message content
- Template script test: `node tests/acp_page.test.mjs` — not affected by this plan (`session_tail.html` has no inline scripts)

## Harness Improvement Opportunities

- Asking questions one-at-a-time was violated during this session (Q5/Q6/Q7 + assumptions checkpoint batched in one turn) — cost: user correction; suggested change: add an explicit anti-batch reminder in the kiro overlay for `/qexplore` Step 2
