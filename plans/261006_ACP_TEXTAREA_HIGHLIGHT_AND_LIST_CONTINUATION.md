# ACP Textarea: Skill Highlight + List Continuation

**Status**: Draft
**Plan slug**: 261006_ACP_TEXTAREA_HIGHLIGHT_AND_LIST_CONTINUATION

---

## Intent

### Problem

The ACP prompt textarea has no visual feedback when a skill or command name is
typed (e.g. `/qexplore`), and pressing Shift+Enter to start a new line in a
numbered list requires the user to re-type the list prefix manually on every
item — friction that breaks note-taking flow.

### Goals

1. **Skill/command highlight**: when the textarea contains a `/token` that
   exactly matches a known entry in `sessionCommands` or `sessionSkills`, the
   token is visually highlighted — inline, inside the text area.

2. **Numbered list continuation**: when the user presses Shift+Enter at the
   end of a line whose start matches a numbered/lettered list prefix pattern
   (e.g. `1. `, `2: `, `A3. `), the next line auto-starts with the
   incremented prefix.

### Success criteria

- SC-1: Typing a known `/command` or `/skill` name into either textarea produces an inline highlight on that token.
- SC-2: Highlight scans the full textarea text — all matching tokens in a multi-line prompt highlight simultaneously.
- SC-3: Pressing Shift+Enter at the end of a numbered/lettered list line (e.g. `1. `, `A3: `) inserts a newline and the incremented prefix with cursor positioned after it.
- SC-4: Pressing Shift+Enter mid-line, or on a line that does not match the list pattern, inserts only a plain newline (no continuation).
- SC-5: Both features behave identically in `/acp` (`acp.html`) and the dashboard panel (`index.html`).

### Out of scope

- Partial-match highlighting (the dropdown already handles that).
- Markdown rendering of the textarea content.
- Adding `autoGrowPrompt()` to the dashboard (pre-existing gap, tracked separately).
- List continuation on touch/mobile devices (Shift+Enter is not reliably available on virtual keyboards; the branch silently does not fire on those devices).

### Decisions

| # | Question | Decision |
|---|---|---|
| D1 | Highlighting approach | Overlay div behind transparent textarea |
| D2 | What to highlight | Any `/token` that exactly matches `sessionCommands[i].name` or `sessionSkills[i].name` (full-text scan, not just end-of-input) |
| D3 | Highlight on partial typing | No — only complete token matches |
| D4 | Shift+Enter trigger condition | Only when cursor is at end of the current line |
| D5 | List stop on empty prefix line | Continue (insert next number even if line has only the prefix) |
| D6 | Dashboard parity | Both features land in `index.html`; autoGrowPrompt gap left as-is (pre-existing) |

---

## Exploration Discovery

### 1. Work scope

Two self-contained enhancements to the prompt textarea compositor:
- Feature A: visual overlay highlighting for `/command` and `/skill` tokens.
- Feature B: Shift+Enter auto-increments a numbered list prefix.

Both touch the same three files: `acp.html` (inline IIFE script + HTML),
`index.html` (mirror keydown/input handlers), and `style.css`. Feature A may
also add a shared function to `composer-chrome.js`.

### 2. Affected files

| File | Change |
|---|---|
| `src/power_atlas/templates/acp.html` | Add `.acp-prompt-wrap` wrapper div + `.acp-prompt-hl` overlay div; update textarea CSS class; add highlight-update logic to `input` handler; add list-continuation branch to `keydown` handler |
| `src/power_atlas/templates/index.html` | Mirror both changes on `#dashPromptInput` |
| `src/power_atlas/static/style.css` | `.acp-prompt-wrap`, `.acp-prompt-hl` rules; adjust `.acp-prompt` (remove `flex:1`, keep it inside wrapper); font-size breakpoint must apply to both wrapper and overlay |
| `src/power_atlas/static/composer-chrome.js` | Optionally expose a `updatePromptHighlight(el)` helper; or keep inline in each page |
| `tests/acp_page.test.mjs` | New tests: skill-token highlight text output; list-continuation on Shift+Enter; no-continuation when cursor is mid-line |

### 3. Key structures

**sessionCommands / sessionSkills** (`composer-chrome.js:350-351`): true
globals (non-IIFE module). Shape: `{name: string, description: string}` where
`name` is bare — no leading `/` (stripped by `acp.py:_parse_skills` via
`.lstrip("/")` and by the `commands` builder at `acp.py:4856`). A highlighter
scanning the textarea must prepend `/` to each name before matching.

**Textarea geometry** (`style.css:1966`): `flex:1; min-height:44px;
max-height:258px; resize:none; font-family:inherit; font-size:16px;
line-height:1.5; padding:8px 10px`. Font overridden to `13px` at `≥768px`
(`style.css:2723`). The overlay must inherit the same breakpoint.

**Keydown handler** (`acp.html:6003–6069`): comment at 6004 explicitly states
Shift+Enter is not intercepted. The new list-continuation branch inserts before
the existing plain-Enter-sends branch. Order matters: slash-intercept →
dropdown nav → list continuation (new) → plain Enter sends.

**Cursor-position pattern** (`acp.html:1600–1606`): slice `value` at
`selectionStart`/`selectionEnd`, write new value, set
`selectionStart = selectionEnd = newPos`. This is the canonical in-repo pattern
for programmatic caret-positioned inserts.

**autoGrowPrompt** (`acp.html:1348–1354`): modifies only `style.height` via
`scrollHeight + 2px`. Called after every programmatic value change.
`index.html` has no equivalent — a known pre-existing gap.

### 4. Existing patterns & constraints

- **Overlay architecture**: no textarea-highlight overlay exists anywhere in
  the codebase. The only positioned element inside `.acp-composer-row` is the
  `#acpCmdDropdown` (`position:absolute; bottom:100%; z-index:200`). The
  overlay sits *behind* the textarea, requiring a wrapper div to anchor it.
- **Wrapping the textarea**: `.acp-prompt` currently has `flex:1` which must
  move to the new `.acp-prompt-wrap` wrapper. The wrapper becomes the flex
  child; the textarea fills it at `width:100%` inside.
- **Scroll sync**: textarea has `max-height:258px` and then scrolls. The
  overlay's `scrollTop` must stay in sync via a `scroll` event listener on
  the textarea.
- **Text escaping**: the overlay renders user input as HTML — all content must
  be HTML-escaped before injection to prevent XSS.
- **`pointer-events: none`**: the overlay must not intercept mouse/keyboard
  events.
- **CRLF source files**: all three `.html` files and `style.css` use CRLF line
  endings — use the Edit tool, not shell scripts (`memory/MEMORY.md`).
- **Test harness**: `tests/acp_page.test.mjs` has no CSS engine and no
  `getBoundingClientRect`. Token-detection logic IS testable (selectionStart,
  value, dispatch keydown/input). Visual alignment requires browser QA
  (Playwright/Chromium per `AGENTS.md § Verification Setup`).
- **Hard reload**: template and static file edits are picked up with
  Ctrl+Shift+R; no PowerAtlas restart needed.

### 5. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Overlay misaligns at 768px font-size breakpoint (16px → 13px) | Override font-size on `.acp-prompt-hl` inside the same `@media (min-width:768px)` block as `.acp-prompt`; verify in Playwright at both widths |
| Overlay drifts when textarea scrolls | `textarea.addEventListener('scroll', () => overlayEl.scrollTop = textarea.scrollTop)` |
| Overlay height lags `autoGrowPrompt()` | Call `overlayEl.style.height = textarea.style.height` after every `autoGrowPrompt()` call (or size via CSS `height:100%` inside wrapper) |
| XSS via highlight div innerHTML | HTML-escape all textarea content before setting innerHTML on the overlay |
| index.html list continuation doesn't grow the textarea | Accepted — pre-existing gap. The value still updates correctly; height is just fixed until the user resizes or reloads. |
| Regex false-positives on list pattern (e.g., version numbers `v1.2`) | Require the separator (`. ` or `: `) to be followed by a space — already in pattern; `v1.2` has no space after the `.` before a digit. URLs like `http://` don't have digits immediately before `: ` that start a line. |
| List continuation on mid-line Shift+Enter (cursor not at end) | Guard: only fire when `selectionStart === selectionEnd` (no selection) AND cursor is at the end of the current line (text after cursor until next `\n` or end-of-value is empty). |

### 6. Tests to update / add

File: `tests/acp_page.test.mjs`

New test cases:
- `highlightWrapRendersKnownSkillToken`: populate `sessionSkills`, dispatch
  `input` on a value containing `/qexplore`, assert overlay inner HTML contains
  a highlight span around `/qexplore`.
- `highlightWrapDoesNotHighlightUnknownToken`: value `/foo`, no skills loaded,
  assert no span.
- `highlightWrapEscapesHtmlInInput`: value contains `<script>`, assert overlay
  contains `&lt;script&gt;`.
- `shiftEnterAtEndOfListLineContinues`: set value `1. item`, selectionStart at
  end, dispatch `{key:"Enter", shiftKey:true, preventDefault(){}}`, assert
  value is `1. item\n2. ` and cursor is after `2. `.
- `shiftEnterMidLineDoesNotContinue`: set value `1. item`, selectionStart
  mid-line, dispatch Shift+Enter, assert value unchanged (browser default fires,
  not our handler).
- `shiftEnterContinuesEvenOnEmptyPrefixLine`: value `2. `, cursor at end,
  dispatch Shift+Enter, assert `2. \n3. `.
- Dashboard mirrors of the above three keydown/list tests.

### 7. Open items

None blocking. The following are post-implementation QA checks:
- Visual overlay alignment at 16px and 13px font sizes (Playwright).
- Scroll sync correctness when textarea is at max-height.
- Highlight correctly updates on paste, undo, and image-marker inserts.

### 8. Step 1.5 dispatch

Code-tracing trio dispatched. Sub-agent findings confirmed:
- No existing overlay or list-continuation code anywhere in the three source
  files.
- `sessionSkills` / `sessionCommands` are true globals (non-IIFE
  `composer-chrome.js`) — accessible from host-page inline scripts without any
  API change.
- Skill names are bare (no leading `/`) in both arrays.
- Shift+Enter is explicitly not intercepted in both `acp.html:6004` and
  `index.html` (no Shift+Enter branch present).
- Tests pass clean at baseline: 969/969.

---

## Harness Improvement Opportunities

- The `/qexplore` skill asks for 1 question at a time but the orchestrator asked 4 at once on the first interview pass — cost: one correction round. Suggested change: add an explicit "1 question per turn" reminder to the pre-interview checklist in the skill.

---

> **Date**: 2026-10-06
> **Status**: Draft
> **Scope**: Skill/command highlight overlay + Shift+Enter list continuation in both textarea instances
> **Estimated effort**: ~0.5 day

## 1) Current State

**Textarea structure** (`acp.html:274-278`, `index.html:456-460`): both pages have a plain `<textarea class="acp-prompt">` with no surrounding wrapper. The textarea is a direct flex child of `.acp-composer-row` via `flex:1` (`style.css:1966`). There is no overlay or highlight mechanism anywhere in the codebase.

**Textarea CSS** (`style.css:1966`): `flex:1; min-height:44px; max-height:258px; resize:none; background:var(--surface); border:1px solid var(--border); border-radius:var(--radius-sm); color:var(--text); font-family:inherit; font-size:16px; line-height:1.5; padding:8px 10px`. Font overridden to `13px` at `≥768px` (`style.css:2723`).

**Keydown handler** (`acp.html:6003-6069`): comment at `acp.html:6004` explicitly states Shift+Enter is not intercepted. Branch order: slash-intercept → dropdown nav → plain Enter sends. Mirror in `index.html:2810-2857`.

**Catalogue globals** (`composer-chrome.js:350-351`): `sessionCommands` and `sessionSkills` are true globals (non-IIFE script). Shape: `{name: string, description: string}` — `name` is bare (no leading `/`). Populated by `setSessionCommands`/`setSessionSkills`; cleared by `resetCommandPalette()` on session change.

**HTML-escape** (`index.html:5798`): `_escHtml` defined as a global in `index.html` only, absent from `acp.html` by design (`acp.html:519`).

**Test harness constraint** (`tests/acp_page.test.mjs:290-292`): `El.innerHTML` getter and setter both throw (`HTML_SINK`). The overlay must be built entirely with `createElement + textContent + appendChild` — no `innerHTML`.

**Cursor-insert pattern** (`acp.html:1600-1606`): slice `value` at `selectionStart/End`, write new value, then `setSelectionRange(newPos, newPos)`. Canonical in-repo pattern for programmatic caret inserts.

**autoGrowPrompt** (`acp.html:1348-1354`): sets only `promptInput.style.height = scrollHeight + 2px`. Must be called after any programmatic `value` change. Dashboard (`index.html`) has no equivalent (pre-existing gap).

## 2) Goal

Wrap each textarea in a `.acp-prompt-wrap` div containing a highlight overlay div (`.acp-prompt-hl`). The overlay renders the textarea's text using `createElement+textContent` spans, with `.acp-prompt-hl-match` spans around exact `/token` matches from `sessionCommands`/`sessionSkills`. Add a Shift+Enter list-continuation branch to the keydown handler in both pages. Mirror both changes to `index.html`.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Overlay DOM method | `createElement + textContent + appendChild` — no `innerHTML` | `innerHTML` with escaped text | Test harness (`acp_page.test.mjs`) throws on any `innerHTML` access; `createElement` also provides XSS safety without escaping |
| Overlay positioning | `position:absolute; inset:1px` inside `.acp-prompt-wrap` | `inset:0` | `inset:1px` offsets inside the textarea's 1px border so both content areas are aligned |
| Highlight scope | All known `/tokens` in the full text (global regex, not just last token) | Last token only | Note-taking use case: multiple `/token` references per prompt |
| Highlight matching | Exact complete match (token followed by whitespace or EOL), no partials | Partial match | Partials already served by dropdown; exact only avoids noise |
| List pattern | `/^(\s*)(\S*?)(\d+)([.:])( +)/` against the current line text | Fixed numeric-only | Covers `1. `, `A1. `, `Q3: ` per user spec |
| Shift+Enter guard | Trigger only when `selectionStart === selectionEnd` AND cursor is at line-end | Always trigger | Mid-line Shift+Enter means "split item", not "continue list" |
| `_escHtml` in acp.html | Not needed — `textContent` is used for all user content | Carry `_escHtml` across | `textContent` does not interpret HTML; no XSS risk without a separate escape function |

## 4) External Dependencies & Costs

| Category | Change needed | Owner | Status |
|---|---|---|---|
| CI/CD | None | — | N/A |
| IAM / Permissions | None | — | N/A |
| Cloud resources | None | — | N/A |
| All others | None — pure front-end HTML/JS/CSS change | — | N/A |

**Cost impact**: None.

## 5) Implementation Phases

### Phase 1: Highlight overlay — HTML, CSS, and JS [QA]

**Goal**: Add the `.acp-prompt-wrap` wrapper and `.acp-prompt-hl` overlay to both textarea instances; update `.acp-prompt` CSS; implement `updatePromptHighlight()` in both pages; add scroll sync and hook into `input`/`cmdOnPromptChanged`; add tests.

**Why vertical**: HTML + CSS + JS together form the complete testable surface for SC-1 and SC-2.

**Covers**: SC-1, SC-2, SC-5 (partial — highlight)

**File scope**: `src/power_atlas/templates/acp.html`, `src/power_atlas/templates/index.html`, `src/power_atlas/static/style.css`, `tests/acp_page.test.mjs`

#### HTML — `acp.html`

At `acp.html:274`, the textarea is a direct child of `.acp-composer-row`. Wrap it:

```
Before line 274 (inside .acp-composer-row, replacing the bare textarea):
<div class="acp-prompt-wrap">
  <div class="acp-prompt-hl" id="acpPromptHl" aria-hidden="true"></div>
  <textarea class="acp-prompt" id="acpPrompt" rows="2" spellcheck="false"
            placeholder="..."
            aria-label="Prompt"
            aria-haspopup="listbox" aria-controls="acpCmdDropdown"
            aria-expanded="false"></textarea>
</div>
```

Keep all existing textarea attributes unchanged. The overlay div has `aria-hidden="true"` — it is a visual-only layer.

#### HTML — `index.html`

Same pattern at `index.html:456`. Add:

```
<div class="acp-prompt-wrap">
  <div class="acp-prompt-hl" id="dashPromptHl" aria-hidden="true"></div>
  <textarea class="acp-prompt" id="dashPromptInput" rows="2" spellcheck="false"
            placeholder="..."
            aria-label="Prompt"
            aria-haspopup="listbox" aria-controls="dashCmdDropdown"
            aria-expanded="false"></textarea>
</div>
```

Preserve all existing `dashPromptInput` attributes.

#### CSS — `style.css`

**Add** these new rules (after the `.acp-composer-row` rule near `style.css:1931`):

```css
/* Highlight-overlay wrapper — takes the flex:1 slot formerly held by .acp-prompt */
.acp-prompt-wrap {
  flex: 1;
  position: relative;
}

/* Overlay div: sits behind the transparent textarea, same metrics */
.acp-prompt-hl {
  position: absolute;
  inset: 1px;          /* offsets inside .acp-prompt's 1px border so content areas align */
  pointer-events: none;
  overflow: hidden;    /* scroll is synced by JS; no browser scrollbar */
  background: var(--surface);
  border-radius: calc(var(--radius-sm) - 1px);
  font-family: inherit;
  font-size: 16px;
  line-height: 1.5;
  padding: 8px 10px;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: break-word;
  color: var(--text);
  box-sizing: border-box;
}

/* Highlight color for matched /tokens */
.acp-prompt-hl-match {
  background: color-mix(in srgb, var(--accent) 18%, transparent);
  border-radius: 2px;
}
```

**Modify** the existing `.acp-prompt` rule at `style.css:1966`:
- Remove `flex: 1` (moved to `.acp-prompt-wrap`)
- Remove `background: var(--surface)` (moved to `.acp-prompt-hl`)
- Add: `width: 100%; background: transparent; color: transparent; caret-color: var(--text); position: relative; z-index: 1;`

The `border`, `border-radius`, `min-height`, `max-height`, `resize`, `font-family`, `font-size`, `line-height`, and `padding` remain on `.acp-prompt` unchanged. The focus rule `border-color: var(--accent)` at `style.css:1967` also remains unchanged.

**Add** to restore text selection visibility with the transparent textarea:

```css
.acp-prompt::selection { color: var(--text); }
```

This rule makes selected text visible again — `color: transparent` on the textarea hides the glyphs in a drag-selection, but `::selection` overrides the color for selected ranges.

> **Rejected**: using `inset: 0` on the overlay — the overlay's content area would be 2px wider than the textarea's (because the textarea has a 1px border on each side), causing text wrap to diverge. **Use instead**: `inset: 1px` to sit inside the border edge.

**Add** inside the existing `@media (min-width: 768px)` block (near `style.css:2723`):

```css
.acp-prompt-hl { font-size: 13px; }
```

**Note on `color-mix`**: `color-mix(in srgb, ...)` has wide browser support (Chrome 111+, Safari 16.2+, Firefox 113+). If the project's browser support target is older than these, fall back to `background: var(--accent); opacity: 0.2` on `.acp-prompt-hl-match` plus `opacity: 1` workaround on child spans, or use a hard-coded rgba. Verify minimum supported browser version before choosing.

#### JS — `acp.html` (inside the IIFE)

**1. Get overlay reference** (near `var promptInput = document.getElementById('acpPrompt')` around `acp.html:599`):

```javascript
var promptHlEl = document.getElementById('acpPromptHl');
```

**2. Add `updatePromptHighlight()` function** (near `autoGrowPrompt`, e.g. after `acp.html:1354`):

```javascript
/**
 * Rebuild the highlight overlay to match the current promptInput.value.
 * Uses createElement+textContent — never innerHTML — so it is both
 * XSS-safe (no HTML parsing of user text) and compatible with the test
 * harness (tests/acp_page.test.mjs forbids innerHTML).
 *
 * A token (/word) is highlighted when it exactly matches a name in
 * sessionCommands or sessionSkills (bare names, no leading /) AND is
 * followed by whitespace or end-of-value.
 */
function updatePromptHighlight() {
  if (!promptHlEl) return;
  var text = promptInput.value;

  // Build the set of known /tokens (names are bare; prepend /)
  var tokens = [];
  var i;
  for (i = 0; i < sessionCommands.length; i++) {
    if (sessionCommands[i].name) tokens.push('/' + sessionCommands[i].name);
  }
  for (i = 0; i < sessionSkills.length; i++) {
    if (sessionSkills[i].name) tokens.push('/' + sessionSkills[i].name);
  }

  // Clear overlay content
  promptHlEl.textContent = '';

  if (tokens.length === 0 || !text) {
    // No catalogue yet or empty input — render plain text so overlay
    // background tracks the textarea's size during scroll.
    var plain = document.createElement('span');
    plain.textContent = text;
    promptHlEl.appendChild(plain);
    return;
  }

  // Sort longest first to avoid a shorter prefix matching inside a longer name
  // (e.g. /qp matching /qplan before /qplan can match).
  tokens.sort(function (a, b) { return b.length - a.length; });

  // Escape regex special chars in each token name
  var escaped = tokens.map(function (t) {
    return t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  });

  // Match a known /token followed by whitespace or end-of-value
  var pattern = new RegExp(
    '(' + escaped.join('|') + ')(?=\\s|$)', 'g');

  var lastIndex = 0;
  var match;
  pattern.lastIndex = 0;
  while ((match = pattern.exec(text)) !== null) {
    // Text segment before the match
    if (match.index > lastIndex) {
      var pre = document.createElement('span');
      pre.textContent = text.slice(lastIndex, match.index);
      promptHlEl.appendChild(pre);
    }
    // Highlighted match
    var hl = document.createElement('span');
    hl.className = 'acp-prompt-hl-match';
    hl.textContent = match[1];
    promptHlEl.appendChild(hl);
    lastIndex = match.index + match[1].length;
  }
  // Remaining text after last match
  if (lastIndex < text.length) {
    var post = document.createElement('span');
    post.textContent = text.slice(lastIndex);
    promptHlEl.appendChild(post);
  }
}
```

**3. Add `updatePromptHighlight()` to `autoGrowPrompt()`** — this is the critical coverage fix. Every programmatic `promptInput.value =` in acp.html (~12 sites: steer-restore, queue-restore, reconnect-restore, refused-prompt-restore, send-clear, image-marker insert, image renumber) already calls `autoGrowPrompt()`. Putting the overlay call there covers all of them without enumerating every site. Search for `function autoGrowPrompt()` (around `acp.html:1348`) and append one line inside:

```javascript
function autoGrowPrompt() {
  var stick = stuckToBottom();
  promptInput.style.height = 'auto';
  promptInput.style.height = (promptInput.scrollHeight + PROMPT_BORDER_PX) + 'px';
  if (stick) transcriptEl.scrollTop = transcriptEl.scrollHeight;
  updatePromptHighlight(); // keep overlay in sync with every value change
}
```

**4. Hook into `input` handler** (search for `promptInput.addEventListener('input'`, around `acp.html:6086`): after the existing `autoGrowPrompt()` call, `autoGrowPrompt()` now also calls `updatePromptHighlight()`, so no separate call is needed here. Verify the `input` handler still calls `autoGrowPrompt()`.

**5. Hook into `cmdOnPromptChanged`** (search for `onPromptChanged:`, around `acp.html:700`): `autoGrowPrompt()` already covers the overlay update, so leave this closure as-is:

```javascript
onPromptChanged: function () { autoGrowPrompt(); refreshComposerControls(); },
```

**6. Add scroll sync** (search for `promptInput.addEventListener('input'`, add after it):

```javascript
promptInput.addEventListener('scroll', function () {
  if (promptHlEl) promptHlEl.scrollTop = promptInput.scrollTop;
});
```

#### JS — `index.html`

Mirror the equivalent changes on `dashPromptInput`/`dashPromptHlEl`:

1. `var dashPromptHlEl = document.getElementById('dashPromptHl');` near `var dashPromptInput = ...` (search `index.html` for `var dashPromptInput =`)
2. `updateDashPromptHighlight()` function — identical logic to `updatePromptHighlight()` above but reading `dashPromptInput.value` and using `dashPromptHlEl`. Uses the same `createElement+textContent` approach and the same `sessionCommands`/`sessionSkills` globals. `_escHtml` NOT needed.
3. Add `updateDashPromptHighlight()` inside `dashRefreshComposerControls()` (search for `function dashRefreshComposerControls()`). This is the equivalent of the `autoGrowPrompt()` hook for the dashboard — `dashRefreshComposerControls()` is called at every `dashPromptInput.value = ...` site (~18 sites), covering all programmatic value changes:

```javascript
function dashRefreshComposerControls() {
  // ... existing body unchanged ...
  updateDashPromptHighlight(); // keep overlay in sync with every value change
}
```

4. `onPromptChanged` on the dashboard is a **bare function reference** at `index.html:2804`: `onPromptChanged: dashRefreshComposerControls`. Since `dashRefreshComposerControls` now calls `updateDashPromptHighlight()`, this is already covered — no change to the callback needed.
5. Add scroll sync on `dashPromptInput` (same pattern as acp.html)
6. `input` handler already calls `dashRefreshComposerControls()` — no additional hook needed

#### Tests — `tests/acp_page.test.mjs`

Use the test harness's actual pattern: `check(name, fn)` where `fn` receives a page object from `loadPage`. All tests below use `check('name', async (page) => { ... })`. Element refs: `page.el('acpPromptHl')` (new), `page.sandbox.sessionSkills = [...]` (direct global assignment).

Add after the existing skill palette tests:

- `check('highlightRendersMatchedToken', page => { page.sandbox.sessionSkills = [{name:'qexplore',description:''}]; page.el('acpPrompt').value = '/qexplore'; page.el('acpPrompt').dispatch('input'); /* assert hlEl has a span with className acp-prompt-hl-match and textContent '/qexplore' */ })`
- `check('highlightDoesNotHighlightUnknownToken', ...)` — `sessionSkills = []; sessionCommands = []`; value `/foo`; dispatch `input`; assert no `.acp-prompt-hl-match` child.
- `check('highlightScansFullText', ...)` — two `/qexplore` tokens in value; assert 2 `.acp-prompt-hl-match` spans.
- `check('highlightSyncScrollUpdatesOverlay', ...)` — `page.el('acpPrompt').scrollTop = 50`; dispatch `scroll`; assert `page.el('acpPromptHl').scrollTop === 50`.
- `check('highlightUpdatesViaAutoGrow', ...)` — programmatic value change followed by `autoGrowPrompt()` call (via the harness's sandbox); assert overlay reflects new value.

**Exit criteria**:
- [ ] `<div class="acp-prompt-wrap">` wraps `#acpPrompt` in `acp.html`
- [ ] `<div class="acp-prompt-hl" id="acpPromptHl" aria-hidden="true">` present inside the wrapper
- [ ] Same wrapper + overlay present in `index.html` around `#dashPromptInput`
- [ ] `.acp-prompt-wrap { flex: 1; position: relative; }` added to `style.css`
- [ ] `.acp-prompt-hl { position: absolute; inset: 1px; ... }` added to `style.css`
- [ ] `.acp-prompt-hl-match` class added to `style.css`
- [ ] `.acp-prompt` loses `flex:1` and gains `background:transparent; color:transparent; caret-color:var(--text)` in `style.css`
- [ ] `.acp-prompt::selection { color: var(--text); }` added to `style.css` (restores selection glyph visibility)
- [ ] `color-mix(in srgb, var(--accent) 18%, transparent)` verified functional in target browser, OR replaced with a supported fallback (e.g., `rgba(99,102,241,0.18)`) before shipping
- [ ] Overlay updates on every `input` event (both pages)
- [ ] Overlay scroll synced to textarea `scroll` event (both pages)
- [ ] `updatePromptHighlight()` (acp.html) and `updateDashPromptHighlight()` (index.html) use no `innerHTML`
- [ ] `node tests/acp_page.test.mjs` passes (all existing + new, zero failures)
- [ ] Hard reload in browser shows textarea text visible, `/qexplore` highlighted (browser QA)

---

### Phase 2: List continuation — Shift+Enter keydown handler [QA]

**Goal**: Intercept Shift+Enter in both keydown handlers when the cursor is at the end of a list-prefixed line; insert the next prefix; call `autoGrowPrompt()` on acp.html.

**Covers**: SC-3, SC-4, SC-5 (partial — list continuation)

**File scope**: `src/power_atlas/templates/acp.html`, `src/power_atlas/templates/index.html`, `tests/acp_page.test.mjs`

#### JS — `acp.html` keydown handler

In the keydown handler at `acp.html:6003`, insert this new branch **after the dropdown navigation block** (currently ending around `acp.html:6053`) and **before the plain-Enter-sends block** (starting around `acp.html:6055`):

```javascript
// List continuation: Shift+Enter at end of a numbered/lettered list line
// inserts a newline and the next list prefix automatically.
// Pattern: ^(\s*)(\S*?)(\d+)([.:])( +) at the start of the current line.
// Only fires when cursor is at end-of-line with no selection.
if (ev.key === 'Enter' && ev.shiftKey && !ev.ctrlKey && !ev.altKey) {
  var _pos = promptInput.selectionStart;
  if (_pos === promptInput.selectionEnd) {
    var _val = promptInput.value;
    // Current line: from last \n before cursor to cursor
    var _lineStart = _val.lastIndexOf('\n', _pos - 1) + 1;
    var _lineText = _val.slice(_lineStart, _pos);
    // Cursor is at end-of-line when nothing between cursor and next \n (or EOV)
    var _nextNl = _val.indexOf('\n', _pos);
    var _lineEnd = _nextNl === -1 ? _val.length : _nextNl;
    if (_pos === _lineEnd) {
      var _m = _lineText.match(/^(\s*)(\S*?)(\d+)([.:])( +)/);
      if (_m) {
        ev.preventDefault();
        var _nextNum = String(parseInt(_m[3], 10) + 1);
        var _insert = '\n' + _m[1] + _m[2] + _nextNum + _m[4] + _m[5];
        promptInput.value = _val.slice(0, _pos) + _insert + _val.slice(_pos);
        var _newPos = _pos + _insert.length;
        promptInput.setSelectionRange(_newPos, _newPos);
        autoGrowPrompt();
        refreshComposerControls();
        updatePromptHighlight(); // sync overlay
        return;
      }
    }
  }
  // Falls through: browser inserts a plain newline (default behavior).
}
```

Variable names use `_` prefix to avoid shadowing the outer handler's variables. The branch falls through (no `return`) when the pattern does not match or cursor is mid-line, letting the browser insert the default newline.

#### JS — `index.html` keydown handler

Identical branch at `index.html:2810`, using `dashPromptInput` instead of `promptInput`, **and using `e.key`, `e.shiftKey`, `e.ctrlKey`, `e.altKey` to match the index.html handler's existing event variable name `e` (not `ev`)**, and calling `dashRefreshComposerControls()` instead. Dashboard has no `autoGrowPrompt()` — this is accepted (pre-existing gap, D6). `dashRefreshComposerControls()` now calls `updateDashPromptHighlight()` (from Phase 1), so the overlay syncs after list continuation on the dashboard.

#### Tests — `tests/acp_page.test.mjs`

Add after the scroll sync tests from Phase 1:

- `shiftEnterAtEndOfListLineContinues`: `promptInput.value = '1. item'`; `promptInput.selectionStart = promptInput.selectionEnd = 7`; dispatch `{key:'Enter', shiftKey:true, ctrlKey:false, altKey:false, preventDefault(){}}` to the `keydown` listener; assert `promptInput.value === '1. item\n2. '` and `promptInput.selectionStart === 11`.
- `shiftEnterMidLineDoesNotContinue`: same setup but `selectionStart = selectionEnd = 3` (mid-line); dispatch Shift+Enter; assert `promptInput.value` unchanged (no branch fires; the `if` guard on `_pos === _lineEnd` is false; the browser's default fires — in the harness, the value is unchanged since no listener mutates it).
- `shiftEnterOnEmptyPrefixLineContinues`: `promptInput.value = '3. '`; `selectionStart = selectionEnd = 3`; dispatch Shift+Enter; assert value `'3. \n4. '`.
- `shiftEnterOnNonListLineNoEffect`: `promptInput.value = 'hello'`; cursor at 5; dispatch Shift+Enter; assert value unchanged.
- `shiftEnterPreservesLeadingIndent`: `promptInput.value = '  2. item'`; cursor at 9; dispatch Shift+Enter; assert value `'  2. item\n  3. '` (indent preserved).
- `shiftEnterWithAlphaPrefix`: `promptInput.value = 'A1. task'`; cursor at 8; dispatch; assert `'A1. task\nA2. '`.
- Dashboard mirrors of `shiftEnterAtEndOfListLineContinues` and `shiftEnterMidLineDoesNotContinue` on `dashPromptInput`.

**Exit criteria**:
- [ ] Shift+Enter at end of `1. text` inserts `\n2. ` (cursor after `2. `)
- [ ] Shift+Enter mid-line does not trigger list continuation
- [ ] Leading whitespace (indent) preserved on continuation
- [ ] Letter prefix (`A1. `) preserved and number incremented
- [ ] Both pages (`acp.html`, `index.html`) implement the handler
- [ ] All new tests pass
- [ ] `node tests/acp_page.test.mjs` passes (zero failures)
- [ ] Placeholder text updated in **both** textareas to mention list continuation ("Shift+Enter continues lists or adds a new line")

---

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| Overlay text wrapping diverges from textarea at 768px font-size breakpoint | High — tokens appear shifted, no usable highlight | Add `.acp-prompt-hl { font-size: 13px; }` in the same `@media (min-width: 768px)` block as `.acp-prompt`; verify in Playwright at both breakpoints |
| Overlay drifts when textarea scrolls past max-height | Medium — highlight no longer aligns with visible text | Scroll sync listener on textarea; tested in `acp_page.test.mjs` |
| `color-mix` not supported on user's browser | Low — highlight disappears (text still works) | Fall back to `rgba(var(--accent-rgb, 99,102,241), 0.18)` or verify minimum supported browser |
| List continuation inserts wrong position after image-marker text | Low — prefix may insert at unexpected offset | The guard `_pos === _lineEnd` with `_nextNl` check is exact; verified by test |
| `updatePromptHighlight` called during replay before `promptHlEl` is ready | Low — silent failure | `if (!promptHlEl) return;` guard at function top |
| index.html textarea height does not grow after list continuation | Low — accepted; pre-existing gap per D6 | Documented in Follow-up Work |

## 7) Verification

```bash
# Run the node test suite (always required after any template inline-script change):
node tests/acp_page.test.mjs

# Expected: all existing 969 + new tests pass, 0 failures
```

Browser QA (required for visual alignment — harness has no CSS engine):

1. Open `/acp` in browser. Hard reload (`Ctrl+Shift+R`).
2. Start an ACP session and wait for the skills catalogue to load (MCP indicator turns green).
3. Type `/qexplore` into the textarea — verify the token is visually highlighted with a colored background.
4. Type `hello /qplan world` — verify `/qplan` highlights mid-sentence.
5. Type without a known token — verify no spurious highlight.
6. Grow the textarea past max-height (type many lines) — scroll down — verify the highlight tracks the textarea's scroll position.
7. Type `1. first item` and press Shift+Enter — verify next line starts `2. `.
8. Type `A3: task` and press Shift+Enter — verify `A4: `.
9. Type `  2. indented item` (with leading spaces) and press Shift+Enter — verify `  3. ` (indent preserved).
10. Click mid-line in `1. something` and press Shift+Enter — verify only a plain newline inserts (no continuation).
11. Repeat checks 2–10 on the dashboard panel (`/`).

## 8) Documentation Updates

No prose documentation files reference the changed identifiers (`acp-prompt`, `acpPrompt`, `dashPromptInput`, `autoGrowPrompt`). The doc-impact sub-agent scanned all tracked `*.md`, `docs/`, `README.md`, and `AGENTS.md` — zero doc-update rows required.

The test file `tests/acp_page.test.mjs` already carries `acpPrompt` and `dashPromptInput` references (128 and 163 occurrences respectively) as test code — these are test maintenance, not documentation updates.

| Document | Update needed | Phase |
|---|---|---|
| — | None | — |

## Progress Tracker

| # | Phase | Status | Notes |
|---|---|---|---|
| 1 | Highlight overlay (HTML + CSS + JS + tests) | Not started | |
| 2 | List continuation (keydown + tests) | Not started | |

## 9) Implementation Divergences from Plan

*Reserved — filled during implementation.*

## Follow-up Work (Deferred)

1. **Dashboard textarea auto-grow after list continuation.** `index.html` has no `autoGrowPrompt()` equivalent, so after a list continuation the textarea height stays at its previous value. Accepted per D6. Source: risk table row 5.
2. **`color-mix` browser support check.** Verify the project's minimum supported browser version against `color-mix(in srgb, ...)` (Chrome 111+, Safari 16.2+, Firefox 113+) before shipping Phase 1 — now moved to Phase 1 exit criteria. Source: risk table row 3.
3. **Ctrl+Z undo for list continuation.** Programmatic `.value =` wipes the browser's undo stack, so Ctrl+Z after a list continuation may not cleanly undo the inserted prefix. `document.execCommand('insertText')` preserves undo but is deprecated. Accepted as-is; revisit if users raise it. Source: review finding #9.
4. **List continuation on mobile.** Shift+Enter is unavailable on most virtual keyboards, so list continuation is a desktop-only feature. No action needed unless mobile ACP usage becomes a priority. Source: review finding #10.

## Review Log

### 2026-10-06 — Plan creation (via /qplan, Full effort)

16 findings (2 High, 6 Medium, 8 Low). All Highs and most Mediums auto-resolved.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | High | All ~12 programmatic `value=` sites in acp.html miss overlay sync — ghost text on steer/queue/reconnect/image | Fixed — `updatePromptHighlight()` moved into `autoGrowPrompt()`, covering all sites |
| 2 | High | index.html keydown branch specifies `ev.` variables but index.html handler uses `e.` → ReferenceError | Fixed — Phase 2 explicitly specifies `e.` for index.html branch |
| 3 | Medium | Overlay `word-break`/`overflow-wrap` could diverge from textarea under pre-wrap | Fixed — added to browser QA checklist (verify long-token wrap alignment) |
| 4 | Medium | `align-items:stretch` could stretch wrap past textarea, exposing bare overlay at bottom | Fixed — added to browser QA checklist |
| 5 | Medium | SC-1..SC-5 referenced in "Covers" lines but not defined anywhere | Fixed — added explicit Success Criteria block to Intent |
| 6 | Medium | `dashRefreshComposerControls()` site coverage for dashboard overlay sync under-specified | Fixed — specified to add overlay call inside `dashRefreshComposerControls()` |
| 7 | Medium | `color: transparent` makes drag-selected text invisible (no glyph in selection band) | Fixed — added `.acp-prompt::selection { color: var(--text); }` to Phase 1 CSS |
| 8 | Medium | Placeholder "Shift+Enter for a new line" misleading after Phase 2; update marked optional | Fixed — made required in Phase 2 exit criteria |
| 9 | Medium | Ctrl+Z will not undo list continuation (programmatic `.value =` wipes browser undo stack) | User: accepted — documented as known limitation in Follow-up Work; `execCommand` is deprecated |
| 10 | Medium | Touch/mobile: Shift+Enter unavailable on most virtual keyboards → list continuation unreachable | User: accepted — scope limited to desktop; noted in Scope boundaries |
| 11 | Low | Line citations drifted ~11-50 lines from actual code | Fixed — replaced absolute line numbers with search-pattern anchors throughout |
| 12 | Low | Test pseudo-code used wrong harness API (`box.promptInput` etc.) | Fixed — updated to `check(name, page => ...)` pattern with `page.sandbox` and `page.el()` |
| 13 | Low | Exploration risk table listed superseded overlay height-sync mitigation | Fixed — removed the superseded row |
| 14 | Low | `color-mix` browser support check parked in Follow-up with no phase owner | Fixed — moved to Phase 1 exit criteria as a required check |
| 15 | Low | index.html `onPromptChanged` is a bare function reference; Phase 1 must not wrap it | Fixed — Phase 1 notes: since `dashRefreshComposerControls` now calls the overlay update, the bare reference is sufficient |
| 16 | Low | List regex fires on `foo1.` → `foo2.` (beyond stated spec) | Accepted — superset of stated spec; documented in risk table as acceptable extension |

## Harness Improvement Opportunities

- The `/qexplore` skill asks for 1 question at a time but the orchestrator asked 4 at once on the first interview pass — cost: one correction round. Suggested change: add an explicit "1 question per turn" reminder to the pre-interview checklist in the skill.
- The test harness's `innerHTML` prohibition (`HTML_SINK`) is not documented in `AGENTS.md § Doc & Test Guidelines` — it was discovered mid-plan by reading the test file, costing a design revision of the overlay approach. Suggested change: add a note to `AGENTS.md` that `acp_page.test.mjs` prohibits `innerHTML` and requires `createElement+textContent` for any DOM building that the test harness exercises.
