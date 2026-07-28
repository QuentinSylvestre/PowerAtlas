# Session Tooltip Rework

> **Date**: 2026-07-28
> **Status**: In Progress
> **Last Updated**: <set by /qclose at archival>
> **Estimated effort**: ~4-6 hours
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

## 1) Current State

The session hover tooltip pipeline: `loadTail` JS (`index.html:582`) → `GET /partials/session-tail` (`web.py:1821`) → `data.get_session_tail` + `data.get_first_prompt` → `session_tail.html` template.

Four deficiencies:

1. **Top-crop**: `loadTail` clamping logic (`index.html:582`) only opens upward. When `desiredTop - effectiveH < 0`, the tooltip is shrunk to `clampedH = desiredTop - 4` or hidden if `clampedH < 100`. No flip-below branch exists.
2. **No session-id in template**: `sid` is received by `partials_session_tail` (`web.py:1822`) but not added to the Jinja context dict (`web.py:1837–1843`). The template has no `{{ session_id }}`.
3. **No last-user-message**: `Session.last_prompt` (`data.py:34`) is populated by all three providers but never read by the endpoint or rendered in the template.
4. **No markdown rendering**: messages reach the template as raw text; `{{ msg }}` is HTML-escaped by Jinja2 autoescaping — markdown syntax renders verbatim. No markdown library in the stack.

## 2) Goal

Enrich the session tooltip with session-id, user last message, and markdown rendering, and replace the upward-only viewport clamping with a flip-below algorithm.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Viewport overflow handling | Flip tooltip below the row when space above is insufficient | Shrink-to-fit (current, crops) | User requested "moved so the full content can be displayed" |
| Session-id placement | Right-aligned on workspace-name row in header | Separate line below title | "Inline of the workspace name" — user's wording |
| `last_prompt` source | Read `s.last_prompt` from the existing session-cache loop in the endpoint | New `data.get_last_prompt()` function | No new data fetch; cache loop already runs; same degradation pattern as `session_title` |
| Empty `last_prompt` display | Show "—" (em dash) | Omit section | User explicitly requested visible placeholder |
| Markdown rendering | Server-side via `mistune>=3.0,<4` | Client-side JS library (marked.js) | Consistent with project's server-side rendering pattern; no new JS dep; sanitization at render time |
| Markdown sections | All three (first_prompt, last_prompt, messages) | Agent messages only | Users use markdown in prompts too; consistency |
| XSS safety | `mistune.create_markdown(escape=True)` (default safe mode) + `\| safe` filter | Trust Jinja2 autoescaping alone | Autoescaping would double-escape; must use `\| safe` after mistune sanitizes |

## 4) External Dependencies & Costs

### Required external changes

None — code-only change. No CI/CD, IAM, cloud resources, data migration, DNS, or third-party services.

One new Python dependency: `mistune>=3.2.1,<4` (BSD license). No cost impact. Requires `pip install -e ".[dev]"` rerun after `pyproject.toml` edit. Lower bound is `3.2.1` (not `3.0`) because CVE-2026-44708, CVE-2026-44896, CVE-2026-44897, CVE-2026-59923, and CVE-2026-59926 were fixed across 3.2.0–3.3.0; `3.2.1` is the earliest release with all these fixed.

## 5) Implementation Phases

### Phase 1: Endpoint enrichment, template, CSS, and tests [QA] [P:2]

**Goal**: Add `mistune` markdown rendering, expose `session_id` and `last_prompt` through the endpoint, update the template with session-id display and user-last-message section, add CSS for new elements, and update existing tests.

**File scope**: `pyproject.toml`, `src/power_atlas/web.py`, `src/power_atlas/templates/partials/session_tail.html`, `src/power_atlas/static/style.css`, `tests/test_web.py`

**`pyproject.toml`** — add to `dependencies` list:
```toml
"mistune>=3.2.1,<4",
```

**`web.py`** — at module level (near other imports):
```python
import mistune as _mistune
# escape=True causes mistune to HTML-entity-encode raw HTML tags (e.g. <script> → &lt;script&gt;)
# rather than passing them through. This prevents XSS when output is used with Jinja2's | safe filter.
_md = _mistune.create_markdown(escape=True)
```

In `partials_session_tail` — extend the cache-lookup loop, early-return guard, and template context:
```python
session_title = ""
last_prompt = ""
cached_sessions = data.session_cache.get(cwd, provider)
if cached_sessions:
    for s in cached_sessions:
        if s.session_id == sid:
            session_title = s.title
            last_prompt = s.last_prompt or ""
            break
workspace_name = Path(cwd).name if cwd else ""
# Guard: show empty-state if ALL content fields are absent
if not messages and not first_prompt and not last_prompt:
    return HTMLResponse('<div class="tail-empty">No recent output</div>')
# Suppress last_prompt when it equals first_prompt (single-exchange session — avoid duplicate display)
if last_prompt == first_prompt:
    last_prompt = ""
# Render all text sections through mistune (escape=True entity-encodes raw HTML — safe for | safe filter)
first_prompt_html = _md(first_prompt) if first_prompt else ""
last_prompt_html = _md(last_prompt) if last_prompt else ""
messages_html = [_md(m) for m in messages]
return templates.TemplateResponse(request, "partials/session_tail.html", {
    "first_prompt": first_prompt_html,
    "last_prompt": last_prompt_html,
    "messages": messages_html,
    "session_title": session_title,
    "workspace_name": workspace_name,
    "session_id": sid,
})
```

Note: the early-return guard is extended from `not messages and not first_prompt` to also include `not last_prompt` — a session with only `last_prompt` populated should render content, not "No recent output."

**`session_tail.html`** — full replacement:
```html
<div class="session-tail-tooltip">
  {% if workspace_name or session_title %}
  <div class="tail-header">
    <div class="tail-header-row">
      {% if workspace_name %}<div class="tail-workspace">{{ workspace_name }}</div>{% endif %}
      {% if session_id %}<div class="tail-session-id" title="{{ session_id }}">{{ session_id[:8] }}</div>{% endif %}
    </div>
    {% if session_title %}<div class="tail-title" title="{{ session_title }}">{{ session_title }}</div>{% endif %}
  </div>
  {% endif %}
  <div class="tail-section">
    <span class="tail-label">User original message:</span>
    <div class="tail-line tail-first-prompt tail-md">{% if first_prompt %}{{ first_prompt | safe }}{% else %}—{% endif %}</div>
  </div>
  <div class="tail-section">
    <span class="tail-label">User last message:</span>
    <div class="tail-line tail-md">{% if last_prompt %}{{ last_prompt | safe }}{% else %}—{% endif %}</div>
  </div>
  {% if messages %}
  <div class="tail-section tail-agent-section">
    <span class="tail-label">Agent last message:</span>
    {% for msg in messages %}
    <div class="tail-line tail-md">{{ msg | safe }}</div>
    {% endfor %}
  </div>
  {% endif %}
</div>
```

Note: `first_prompt` and `last_prompt` sections are always rendered (not conditional) so the section labels and "—" fallback are always visible. The `messages` section remains conditional (some sessions have no agent reply yet).

**`style.css`** — add after the existing `.tail-label` rule:
```css
.tail-header-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.tail-session-id { font-size: 10px; color: var(--text-dim); font-family: monospace; opacity: 0.7; white-space: nowrap; flex-shrink: 0; }
/* Markdown content resets — keep tooltip compact; override .tail-line monospace/pre-wrap defaults */
.tail-md { font-family: inherit; white-space: normal; word-break: break-word; overflow-wrap: break-word; }
.tail-md p { margin: 0 0 4px; }
.tail-md p:last-child { margin-bottom: 0; }
.tail-md code { font-size: 10px; background: rgba(255,255,255,0.07); padding: 1px 3px; border-radius: 3px; font-family: monospace; }
.tail-md pre { font-size: 10px; background: rgba(255,255,255,0.05); padding: 4px 6px; border-radius: 4px; overflow-x: auto; margin: 4px 0; white-space: pre; }
.tail-md ul, .tail-md ol { margin: 2px 0; padding-left: 16px; }
.tail-md li { margin: 1px 0; }
.tail-md strong { font-weight: 600; }
.tail-md em { font-style: italic; }
```

**`tests/test_web.py`** — update the three session-tail tests:

`test_session_tail_returns_messages`: set `last_prompt="fix the bug"` in the mock Session. Also use `"**hello** user"` as `first_prompt` return value to exercise markdown rendering. Add assertions:
```python
assert "tail-session-id" in resp.text
assert "sess-1" in resp.text          # session_id[:8] rendered
assert "fix the bug" in resp.text     # last_prompt rendered
assert "User last message" in resp.text
assert "<p>" in resp.text             # mistune rendered markdown (not raw text)
assert "<strong>" in resp.text        # **hello** → <strong>hello</strong>
```

`test_session_tail_graceful_no_cache`: `mock_cache.get.return_value = None`. Assert `"User last message" in resp.text` and `"—" in resp.text` (fallback shown). Also confirm `"tail-title" not in resp.text` still holds (the `{% if session_title %}` guard preserves this).

`test_session_tail_empty`: assert `"tail-empty" in resp.text` and `"No recent output" in resp.text` (unchanged). Note: this test patches `get_first_prompt` to return `""` and `get_session_tail` to return `[]`; `session_cache` is not patched (cache miss → `last_prompt = ""`). Since all three of `messages`, `first_prompt`, `last_prompt` are empty, the early-return guard fires correctly.

Add a new XSS test (within existing test file, no new file):
```python
@patch("power_atlas.web.data.session_cache")
@patch("power_atlas.web.data.get_first_prompt", return_value="<script>alert(1)</script>")
@patch("power_atlas.web.data.get_session_tail")
def test_session_tail_xss_stripped(mock_tail, mock_first, mock_cache, client):
    """mistune escape=True entity-encodes raw HTML and JS-URL hrefs; output is safe for | safe filter."""
    mock_tail.return_value = ["<script>evil()</script>", "[click](javascript:alert(1))"]
    mock_cache.get.return_value = None
    resp = client.get("/partials/session-tail?sid=xss-test&cwd=C%3A%5CTest")
    assert resp.status_code == 200
    assert "<script>" not in resp.text          # raw tags not present
    assert "&lt;script&gt;" in resp.text        # entity-encoded form IS present (confirms _md was invoked)
    assert "javascript:alert" not in resp.text  # JS URL not present in rendered output
```

**Covers**: SC-3, SC-4, SC-5, SC-7, SC-8

**Exit criteria**:
- [x] `mistune>=3.2.1,<4` present in `pyproject.toml` dependencies
- [x] `_md` module-level renderer created with `escape=True`; comment clarifies "entity-encodes" not "strips"
- [x] `session_id` and `last_prompt` present in template context dict
- [x] Early-return guard extended to `not messages and not first_prompt and not last_prompt`
- [x] `last_prompt` suppressed (set to `""`) when it equals `first_prompt` before rendering
- [x] `session_tail.html` renders `.tail-header-row` with `.tail-session-id` showing first 8 chars of session_id (full UUID in `title` attribute)
- [x] "User last message:" section always rendered; shows "—" when `last_prompt` is empty
- [x] All message content uses `| safe` filter (content pre-sanitized by mistune)
- [x] `.tail-header-row`, `.tail-session-id`, `.tail-md` CSS rules added to `style.css` (including `font-family: inherit; white-space: normal; word-break: break-word` reset on `.tail-md`)
- [x] `test_session_tail_returns_messages` updated and passes: `session_id`, `last_prompt`, `"<p>"`, `"<strong>"` assertions
- [x] `test_session_tail_graceful_no_cache` updated and passes with "—" assertion; `"tail-title" not in resp.text` still holds
- [x] `test_session_tail_xss_stripped` written and passes: no `<script>`, `&lt;script&gt;` present, `javascript:alert` absent
- [x] `pytest tests/test_web.py -v` passes (all session-tail tests green)
- [x] `plans/tests/260701_POWERATLAS.md` Section 1.8 oracle and probes updated

#### Implementation (2026-07-28, code: e31a4a0)
Added `mistune>=3.3.0,<4` to `pyproject.toml` dependencies (lower bound raised from 3.2.1 during review — CVE-2026-59923 percent-encoded JS-URL bypass affects 3.2.x; 3.3.0 is the first fully patched release). In `web.py`, added module-level `_md = mistune.create_markdown(escape=True)` instance and rewrote `partials_session_tail` to: extend the cache-lookup loop to also read `last_prompt`; change the empty-state guard to require all three content fields absent; strip whitespace from `last_prompt`; suppress `last_prompt` when `first_prompt.startswith(last_prompt)` (handles 200-char cap mismatch in single-exchange sessions); add UUID validation (RFC 4122 regex, returns 400 for invalid sids); render all three text variables through `_md`; pass `session_id` and rendered HTML to template. `session_tail.html` fully replaced with `tail-header-row` (session_id[:8] + full UUID in title attribute), unconditional "User original message" and "User last message" sections with em-dash fallbacks, and `tail-md` class on all content divs with a Jinja comment explaining `| safe` safety. In `style.css`, 12 new rules added after `.tail-label` covering header row layout, session ID styling, and markdown content resets. Five session-tail tests written/updated (5/5 pass); `plans/tests/260701_POWERATLAS.md` §1.8 updated with new oracle fields and probes. Full test suite: 683 passed, 1 skipped.

### Phase 2: Viewport positioning rewrite [QA] [P:1]

**Goal**: Replace the shrink/hide clamping logic in `loadTail` with a flip-below algorithm; fix `hideTail` state leaks.

**File scope**: `src/power_atlas/templates/index.html`

**`index.html`** — replace the `loadTail` and `hideTail` functions (at the line containing `var _tailTimers`). New implementation:

```javascript
var _tailTimers={};
function loadTail(el){
  var row=el.closest('.session-row');
  var sid=row.dataset.sid;
  var provider=row.dataset.provider||'kiro-cli';
  var cwd=row.dataset.cwd||'';
  var slot=el.querySelector('.session-tooltip-slot');
  _tailTimers[sid]=setTimeout(function(){
    fetch('/partials/session-tail?sid='+encodeURIComponent(sid)+'&provider='+encodeURIComponent(provider)+'&cwd='+encodeURIComponent(cwd))
    .then(function(r){if(!r.ok)throw new Error(r.status);return r.text()})
    .then(function(html){
      slot.innerHTML=html;
      if(window.htmx)htmx.process(slot);
      var tooltip=slot.querySelector('.session-tail-tooltip');
      if(!tooltip)return;
      var rect=el.getBoundingClientRect();
      var spaceAbove=rect.top-8;
      var spaceBelow=window.innerHeight-rect.bottom-8;
      // Suppress when neither side has ≥100px usable space
      if(Math.max(spaceAbove,spaceBelow)<100){
        slot.style.left='';slot.style.top='';slot.style.transform='';
        slot.style.display='none';return;
      }
      // Determine max usable height and which side to open on
      var openBelow=spaceBelow>spaceAbove;
      var maxH=openBelow?Math.max(spaceBelow,100):Math.max(spaceAbove,100);
      tooltip.style.maxHeight=maxH+'px';
      var tooltipH=tooltip.scrollHeight;
      var effectiveH=Math.min(tooltipH,maxH);
      slot.style.left=rect.left+'px';
      if(!openBelow&&spaceAbove>=effectiveH){
        // Fits above — normal path
        slot.style.top=(rect.top-4)+'px';
        slot.style.transform='translateY(-100%)';
      } else if(openBelow){
        // Open below the row (pointer-events:none means tooltip overlapping lower rows is acceptable)
        slot.style.top=(rect.bottom+4)+'px';
        slot.style.transform='none';
      } else {
        // Above preferred but doesn't fully fit — clamp to viewport top
        slot.style.top='4px';
        slot.style.transform='none';
        tooltip.style.maxHeight=Math.max(spaceAbove,100)+'px';
      }
      slot.style.display='block';
    })
    .catch(function(){slot.style.display='none'});
  },300);
}
function hideTail(el){
  var row=el.closest('.session-row');
  var sid=row.dataset.sid;
  clearTimeout(_tailTimers[sid]);
  var slot=el.querySelector('.session-tooltip-slot');
  slot.style.display='none';
  slot.style.left='';
  slot.style.top='';
  slot.style.transform='';
  var tooltip=slot.querySelector('.session-tail-tooltip');
  if(tooltip)tooltip.style.maxHeight='';
}
```

Key changes from current (`index.html:582`):
- `spaceAbove` vs `spaceBelow` computed; larger side preferred; suppress only when `Math.max < 100`
- Suppress branch now resets `left/top/transform` before returning (F-A3 fix)
- Opens below when preferred: `slot.style.top = rect.bottom + 4`; visual overlap with lower rows is accepted design (`pointer-events: none`)
- `hideTail` resets `slot.style.left`, `slot.style.top`, `slot.style.transform` (state leak fix)

**Covers**: SC-1, SC-2, SC-6

**Exit criteria**:
- [x] `loadTail` replaced with flip-below algorithm
- [x] Suppress branch resets `left/top/transform` before `return`
- [x] `hideTail` resets `left`, `top`, `transform` in addition to `maxHeight`
- [x] Hovering a session row near top of viewport: tooltip opens below the row
- [x] Hovering a session row in the middle: tooltip opens above the row (normal path)
- [x] Hovering a session row near bottom: tooltip opens above; if insufficient space above, flips below
- [x] `resetOverlays()` behavior unchanged (still hides all `.session-tooltip-slot` elements)
- [x] `node tests/acp_page.test.mjs` passes (template inline script harness — verifies no regressions in acp.html; loadTail/hideTail not covered by this harness, which is accepted as a known gap)

#### Implementation (2026-07-28, code: 457d0e5)
Replaced `loadTail` and `hideTail` in `index.html` with a flip-below algorithm. New `loadTail` computes `spaceAbove = rect.top - 8` and `spaceBelow = window.innerHeight - rect.bottom - 8`; suppresses when `Math.max(spaceAbove, spaceBelow) < 100` (with full `left/top/transform` reset before `display:none`); sets `openBelow = spaceBelow > spaceAbove` (strict — equal space defaults to above); positions below at `rect.bottom + 4px` when `openBelow`, above at `rect.top - 4px` with `translateY(-100%)` when above preferred and fits, or clamps to `top: 4px` in the theoretically-unreachable fallback (safety net). New `hideTail` resets `left`, `top`, `transform`, and `maxHeight` on the slot, preventing state leaks across hovers. Added comments: tie-break documentation, dead-code rationale in fallback branch. `plans/tests/260701_POWERATLAS.md` §2.3 updated to reflect above-or-below positioning. `node tests/acp_page.test.mjs`: 15 passed.

## Harness Improvement Opportunities

- Asking questions one-at-a-time was violated during this session (Q5/Q6/Q7 + assumptions checkpoint batched in one turn) — cost: user correction; suggested change: add an explicit anti-batch reminder in the kiro overlay for `/qexplore` Step 2


## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| XSS via `\| safe` on markdown output | High | `mistune.create_markdown(escape=True)` entity-encodes raw HTML (`<script>` → `&lt;script&gt;`); pin `>=3.2.1,<4` avoids all active CVEs; XSS test covers `<script>` and `javascript:` URL payloads |
| Cache miss degrades last_prompt to "—" | Low | Consistent with existing `session_title` degradation; warmup pre-loads pinned sessions |
| Viewport reflow timing (scrollHeight stale) | Low | Same pattern proven in prior plan (260708-1624); browser reflow happens synchronously before scrollHeight read |
| mistune minor-version output changes | Low | Pinned `<4`; only formatting details may change, not sanitization |
| `test_data.py` timing flakiness (pre-existing) | Low | Re-run standalone to distinguish from regressions; not introduced by this plan |

## 7) Verification

```bash
# Install mistune then run tests
.venv-PowerAtlas\Scripts\python -m pip install -e ".[dev]"
.venv-PowerAtlas\Scripts\pytest tests/test_web.py -v -k "session_tail"
# Full suite
.venv-PowerAtlas\Scripts\pytest tests/test_web.py -v
```

Runtime manual checks (start app with `.venv-PowerAtlas\Scripts\power-atlas`):
- Hover a session row in the middle of the panel → tooltip opens above
- Hover a session row near the top → tooltip opens below the row
- Hover a session row near the bottom → tooltip opens above; if cramped, flips below
- Inspect tooltip header: workspace name left, session-id right on same line
- Inspect "User last message:" section — shows content or "—"
- Inspect markdown rendering: `**bold**` renders as `<strong>bold</strong>`, backtick code renders styled

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `plans/tests/260701_POWERATLAS.md` | Section 1.8 oracle: add `session_id` + `last_prompt` as returned fields; add markdown-rendering note. Probes: add `last_prompt` populated/empty cases and `<script>`-stripped + `javascript:`-URL cases. | 1 |
| `plans/tests/260701_POWERATLAS.md` | Section 2.3 oracle: change "positioned above the row" to "positioned above or below the row depending on available viewport space." | 2 |

## 9) Implementation Divergences from Plan

_Reserved — filled during implementation._

## Review Log

### 2026-07-28 — Plan Creation Review (high effort, 4 personas: Architect, Senior engineer, Security auditor, End-user advocate)

16 findings across 4 personas. 12 auto-resolved, 4 escalated (2 Low — user decision).

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | High | `mistune>=3.0,<4` admits versions with active CVEs bypassing `escape=True`; XSS test passes on vulnerable versions. | Fixed — pin raised to `>=3.2.1,<4`; XSS test extended with `javascript:` URL probe and positive entity assertion. |
| 2 | Medium | Early-return guard `not messages and not first_prompt` misses `last_prompt`; session with only `last_prompt` shows "No recent output." | Fixed — guard extended to include `not last_prompt`. |
| 3 | Medium | `loadTail` suppress-branch does not reset `left/top/transform` before early return — stale positioning persists. | Fixed — resets added before `return` in suppress branch. |
| 4 | Medium | Phase 2 exit criteria omit `acp_page.test.mjs`; AGENTS.md mandates running it on template inline script changes. | Fixed — exit criterion added (acknowledged as known gap: `loadTail`/`hideTail` not covered by that harness). |
| 5 | Medium | XSS test assertion `"<script>" not in resp.text` does not verify `_md` was invoked and does not cover JS-URL bypass class. | Fixed — added `&lt;script&gt;` presence assertion and `javascript:alert` absence check. |
| 6 | Medium | Comment says `escape=True` "strips raw HTML" — factually wrong; it entity-encodes. | Fixed — corrected to "entity-encodes" in code comment and Risk Assessment. |
| 7 | Medium | `test_session_tail_returns_messages` doesn't assert markdown was rendered (no HTML tag check). | Fixed — added `"<p>" in resp.text` and `"<strong>"` assertions using `**hello**` in `first_prompt`. |
| 8 | Medium | `.tail-line` monospace/pre-wrap inherited into `.tail-md` — prose renders in monospace with extra blank lines. | Fixed — added `font-family: inherit; white-space: normal; word-break: break-word` reset to `.tail-md` CSS. |
| 9 | Low | `[P:N]` annotation semantics: Phase 1 = `[P:2]` and Phase 2 = `[P:1]` is unconventionally reversed but functionally correct (symmetric pairing identifies the partner, not execution order). | Accepted — the `[P:N]` scheme identifies parallel partners, not ordering; the annotation is correct per `TEMPLATES.md § Parallel Phase Annotation`. |
| 10 | Low | `plans/tests/260701_POWERATLAS.md` Section 2.3 oracle "above the row" becomes stale after Phase 2. | Fixed — added Section 2.3 update to Documentation Updates table assigned to Phase 2. |
| 11 | Low | UUID (36 chars) at 10px monospace is unreadable; no copy functionality in tooltip. | Fixed — template truncated to first 8 chars with full UUID in `title` attribute; existing row-level copy button covers full-ID need. |
| 12 | Low | Single-exchange sessions show identical content in both User sections (first_prompt == last_prompt). | User: accepted — suppress `last_prompt` in endpoint when it equals `first_prompt`; set `last_prompt = ""` before rendering so template shows "—". |
| 13 | Low | "User last message" and "User original message" labels visually identical — hard to distinguish at a glance. | User: accepted — keep labels as-is; label text is sufficient distinction. |
| 14 | Low | `_md()` calls run synchronously on the async event loop — should use `asyncio.to_thread`. | Accepted — tooltip content is bounded in size (200 chars per message, 15 messages max); synchronous processing time is negligible; `asyncio.to_thread` overhead would exceed processing time for this payload size. |
| 15 | Low | Flip-below tooltip overlaps lower rows — visual overlap is accepted design per `pointer-events: none`. | Fixed — comment added in Phase 2 code; exit criteria note acknowledges this as intentional. |
| 16 | Low | `test_session_tail_graceful_no_cache` existing assertion `"tail-title" not in resp.text` not explicitly confirmed to survive template change. | Fixed — plan now explicitly notes this assertion holds because `{% if session_title %}` guard is preserved. |

### 2026-07-28 — Implementation Review (after Phase 1, personas: Security auditor, Senior engineer, Maintainability reviewer, End-user advocate)

Implementation health: Green.
9 findings (0 High, 5 Medium, 4 Low) — all resolved in 2 auto-fix cycles.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | Medium | CVE-2026-59923: `mistune>=3.2.1` admits percent-encoded JS-URL bypass; `>=3.3.0` is the first fully patched release. | Fixed — version pin raised to `>=3.3.0,<4` in pyproject.toml. |
| 2 | Medium | `plans/tests/260701_POWERATLAS.md` §1.8 not updated — exit criterion 15 explicitly requires oracle and probes updated. | Fixed — §1.8 updated with session_id, last_prompt, markdown rendering note, XSS/JS-URL probes. |
| 3 | Medium | `test_session_tail_empty` lacks `session_cache` patch — fragile if a prior test populates the real cache. | Fixed — added `@patch("power_atlas.web.data.session_cache")` with `mock_cache.get.return_value = None`. |
| 4 | Medium | XSS test docstring incorrectly attributes JS-URL sanitization to `escape=True`; actual mechanism is `HTMLRenderer.safe_url()`. | Fixed — docstring and comment corrected to name `HTMLRenderer.safe_url()`. |
| 5 | Medium | Dedup comparison `last_prompt == first_prompt` fails for sessions where `first_prompt` exceeds the 200-char `last_prompt` cap. | Fixed — changed to `first_prompt.startswith(last_prompt)`. |
| 6 | Medium | `sid` query param not validated — permissive pass-through enables path traversal in the data layer. | Fixed — RFC 4122 UUID regex validation added; returns 400 for invalid sids; test added. |
| 7 | Low | `import mistune as _mistune` placed after local imports, adding a new ruff I001 error. | Fixed — moved to third-party section; alias removed; `_md =` moved after all imports. |
| 8 | Low | Whitespace-only `last_prompt` bypasses empty guard and renders as "—" instead of triggering "No recent output". | Fixed — `last_prompt` now stripped with `.strip()` before all comparisons. |
| 9 | Low | UUID regex `[0-9a-f-]{36}` accepts 36-dash or 36-hex inputs — not RFC 4122 format. | Fixed — tightened to `[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}`. |

Persona: Security auditor, Senior engineer, Maintainability reviewer, End-user advocate. Identical findings merged; contributing personas noted in descriptions. Other low findings (import alias, `overflow-wrap` addition, `| safe` comment gap, `tail-line` CSS audit) were auto-fixed in the same pass (all mechanical/clarity improvements with no behavioral change).

### 2026-07-28 — Implementation Review (after Phase 2, personas: Senior engineer, End-user advocate)

Implementation health: Green.
5 findings (0 High, 1 Medium, 4 Low) — all resolved in 2 auto-fix cycles.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | Medium | `plans/tests/260701_POWERATLAS.md` §2.3 oracle still reads "positioned above the row" — not updated to reflect flip-below. | Fixed — §2.3 updated to tri-case description (above/below/suppressed). |
| 2 | Low | Dead code in else-clamp-to-top branch — provably unreachable given suppress handles `spaceAbove<100`. | Fixed — removed redundant `maxHeight` assignment; added accurate dead-code comment explaining unreachability. |
| 3 | Low | `openBelow = spaceBelow > spaceAbove` tie-break (equal space → above) undocumented. | Fixed — comment added: "strict comparison — equal space defaults to above (legacy direction)." |
| 4 | Low | `resetOverlays()` doesn't reset `left/top/transform` (pre-existing; Phase 2 adds three new inline properties). | User: accepted — plan invariant states `resetOverlays()` behavior unchanged; stale state is overwritten on next hover; no visible misposition. |
| 5 | Low | Dead-code comment said "suppress fires when `spaceAbove<100`" — imprecise (actual condition is `Math.max(spaceAbove,spaceBelow)<100`). | Fixed — comment updated to cite the exact suppress condition and trace the unreachability in the above-preferred context. |
