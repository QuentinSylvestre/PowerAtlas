// Shared composer chrome — context-window usage indicator, the workspace/
// session-id tap-to-copy widget, and the debug/transport log panel — loaded
// by both /acp (templates/acp.html) and the dashboard (templates/index.html).
// Ported out of acp.html's inline script (dashboard/ACP feature-parity plan,
// Phase 1), following the exact structural precedent transcript-renderer.js
// set: a plain top-level script, not wrapped in an IIFE, so every var/
// function declared here becomes a true global — acp.html's own
// `(function () { ... })();` wrapper still reads and writes these same
// names, unqualified, via the scope chain (JS resolves an unbound identifier
// by walking outward), exactly as that file's own header comment documents
// for transcriptEl et al. Moving a declaration's lexical position doesn't
// change how a bare reference elsewhere resolves it; what changed is only
// WHERE the declaration lives.
//
// Two pieces of page-lifecycle state the ported code used to read as free
// variables inside acp.html's IIFE — `sessionId` and `replaying` — do NOT
// move here: they are genuinely page-owned, and each host page's own value
// and lifecycle differ (index.html's equivalents are `_dashAttachedSid` /
// `_dashReplaying`, distinct names entirely). Each host page instead passes
// an accessor FUNCTION (`getSessionId` / `isReplaying`) into the matching
// initXxxDom() call below, and this file calls that function every time it
// needs the current value, rather than snapshotting it once at init — a raw
// value would go stale the moment a session switches, since init runs once,
// at page load. Without this, every ported call would throw a
// ReferenceError on both pages (the free variables it used to close over
// simply would not exist once the declaration moved out of acp.html's
// IIFE) — this bit an earlier draft of the plan this file implements, and is
// recorded here so a future edit does not reintroduce it.
//
// Collision check (Phase 1 exit criterion): every new global name this file
// introduces — contextEl, contextFill, contextLabel, sidEl, copyBtn,
// sidGetSessionId, logEl, logToggle, logIsReplaying, sidWorkspace,
// _sessionCwd, sidRevealed, _copyOkTimer, DEBUG_LOG_KEY, railStored,
// railStore, initContextDom, initSidCopyDom, initLogDom, setContext,
// shortName, renderSidLabel, logLine, setLogOpen — was grepped against both
// acp.html and index.html. Result: no collision. index.html's own rail-
// persistence helpers are named dashRailStored/dashRailStore (index.html,
// dashboard/ACP-merge Phase 4), a distinct pair of names from railStored/
// railStore below; the two now co-exist deliberately (dashRailStored/
// dashRailStore keep governing the dashboard's own rail-only preferences —
// left-pane width, sort mode — while this file's railStored/railStore are
// shared infrastructure the debug log panel and, from Phase 3 on, the
// send-mode picker use on both pages). Every other name above was absent
// from both templates before this file existed.

// ---- context-window usage indicator ----------------------------------------

var contextEl = null;
var contextFill = null;
var contextLabel = null;

/** Called once by each loading page's own inline script, with that page's
 *  own element references. No page-lifecycle accessor needed: setContext()
 *  only ever touches these three DOM refs, never a free variable owned by
 *  the host page. */
function initContextDom(refs) {
  contextEl = refs.contextEl;
  contextFill = refs.contextFill;
  contextLabel = refs.contextLabel;
}

function setContext(percent) {
  // Numeric or nothing. The server clamps this to 0-100 and rounds it, and
  // the check is repeated here because the value ends up as a CSS width:
  // that is an attribute sink, and the rule on both pages is that nothing
  // agent-derived reaches one. A number that survives both checks is
  // written by this line, not by the agent.
  if (typeof percent !== 'number' || !isFinite(percent) ||
      percent < 0 || percent > 100) {
    contextEl.hidden = true;
    return;
  }
  contextEl.hidden = false;
  contextFill.style.width = percent + '%';
  contextLabel.textContent = 'context ' + percent + '%';
}

// ---- workspace/session-id label + copy-to-clipboard ------------------------

var sidEl = null;
var copyBtn = null;
// () => the current session id, or null/'' when nothing is attached. REQUIRED
// — see the file header for why a raw value cannot substitute for this.
var sidGetSessionId = null;

// The open session's workspace, short form (`repo`, not `C:\work\repo`) —
// read off the `session` frame's `cwd` by each host page, same as acp.html
// always did. "" until a `session` frame lands, which is why renderSidLabel()
// falls back to the raw id rather than drawing an empty header.
var sidWorkspace = '';
// The full path of the current session's workspace — set from `payload.cwd`
// alongside sidWorkspace above. "" until the session frame lands. This is the
// value the copy button now copies (SC3 bug fix, below) instead of
// shortName(cwd).
var _sessionCwd = '';
// Whether the header is showing the session id instead of the workspace
// name. Each host page resets this to false on every session change: the id
// was one tap away for whichever conversation asked for it, not a standing
// preference.
var sidRevealed = false;
var _copyOkTimer = null;

/** Called once by each loading page's own inline script. `getSessionId` is
 *  REQUIRED — see the file header. */
function initSidCopyDom(refs) {
  sidEl = refs.sidEl;
  copyBtn = refs.copyBtn;
  sidGetSessionId = refs.getSessionId;

  sidEl.addEventListener('click', function () {
    if (!sidGetSessionId()) return;
    sidRevealed = !sidRevealed;
    renderSidLabel();
  });

  // Copies the value currently shown in the sid label (session id or
  // workspace path). Uses the Clipboard API where available and falls back
  // to a transient textarea execCommand for older/restricted contexts (e.g.
  // non-HTTPS).
  copyBtn.addEventListener('click', function () {
    var sessionId = sidGetSessionId();
    if (!sessionId) return;
    var showId = sidRevealed || !sidWorkspace;
    // Copy the raw value — the session id without the "session " display
    // prefix, or the full workspace path (SC3 bug fix: this used to copy
    // shortName(cwd) — sidWorkspace — despite acp.html's own comment
    // claiming full-path copy; now it copies _sessionCwd, the full path,
    // same as the comment always said) for easy paste. The DISPLAYED label
    // is unchanged — sidEl.textContent still shows sidWorkspace, the short
    // name; only the copied clipboard value changes.
    var value = showId ? sessionId : (_sessionCwd || sessionId);
    function markOk() {
      copyBtn.classList.add('acp-copy-btn--ok');
      copyBtn.title = 'Copied!';
      clearTimeout(_copyOkTimer);
      _copyOkTimer = setTimeout(function () {
        copyBtn.classList.remove('acp-copy-btn--ok');
        renderSidLabel(); // restore correct title
      }, 1200);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(value).then(markOk, function () {});
    } else {
      var ta = document.createElement('textarea');
      ta.value = value;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand('copy'); markOk(); } catch (e) {}
      document.body.removeChild(ta);
    }
  });
}

/** The last non-empty path segment, `Path(cwd).name`'s client-side mirror.
 *  Both separators are handled: the server sends whatever the OS the agent
 *  runs on writes into `cwd`, `\` on Windows, and this page runs on
 *  whichever OS the browser happens to be. */
function shortName(cwdPath) {
  var trimmed = String(cwdPath || '').replace(/[\\/]+$/, '');
  var cut = Math.max(trimmed.lastIndexOf('/'), trimmed.lastIndexOf('\\'));
  var name = cut >= 0 ? trimmed.slice(cut + 1) : trimmed;
  return name || trimmed;
}

/** Redraw the toolbar's session label from the current sidWorkspace /
 *  sidRevealed / sidGetSessionId() state. One function for every writer so
 *  the three cannot drift out of sync — see the click handler above and
 *  every place a host page assigns sidWorkspace. */
function renderSidLabel() {
  var sessionId = sidGetSessionId();
  if (!sessionId) {
    sidEl.textContent = '';
    sidEl.title = '';
    copyBtn.hidden = true;
    return;
  }
  // Falls back to the id when the workspace is not known yet — right after a
  // session is selected and before the `session` frame answers — rather than
  // drawing a blank header for that gap.
  var showId = sidRevealed || !sidWorkspace;
  sidEl.textContent = showId ? 'session ' + sessionId : sidWorkspace;
  sidEl.setAttribute('aria-pressed', showId ? 'true' : 'false');
  sidEl.title = sidWorkspace
    ? (showId ? 'tap to show the workspace' : 'tap to show the session id')
    : '';
  copyBtn.hidden = false;
  // Update tooltip to reflect what will be copied.
  copyBtn.title = showId ? 'Copy session id' : 'Copy workspace name';
  copyBtn.setAttribute('aria-label', showId ? 'Copy session id' : 'Copy workspace name');
}

// ---- debug / transport log panel -------------------------------------------

var logEl = null;
var logToggle = null;
// () => bool — REQUIRED. See the file header for why a raw value cannot
// substitute for this.
var logIsReplaying = null;

// Remembered per browser (railStored/railStore below) so a preference set
// once survives a reload rather than reopening closed by default every time.
// Shared between both pages deliberately — one open/closed preference, not
// one per page.
var DEBUG_LOG_KEY = 'pa_acp_debug_log';

/** Called once by each loading page's own inline script. `isReplaying` is
 *  REQUIRED — see the file header. */
function initLogDom(refs) {
  logEl = refs.logEl;
  logToggle = refs.logToggle;
  logIsReplaying = refs.isReplaying;

  setLogOpen(railStored(DEBUG_LOG_KEY) === 'open');
  logToggle.addEventListener('click', function () {
    var open = logEl.hidden;
    setLogOpen(open);
    railStore(DEBUG_LOG_KEY, open ? 'open' : 'closed');
  });
}

function logLine(kind, text) {
  if (logIsReplaying()) return;
  var row = document.createElement('div');
  row.className = 'acp-log-line acp-log-' + kind;
  var when = document.createElement('span');
  when.className = 'acp-log-time';
  when.textContent = new Date().toLocaleTimeString();
  var body = document.createElement('span');
  body.className = 'acp-log-body';
  body.textContent = text;
  row.appendChild(when);
  row.appendChild(body);
  logEl.appendChild(row);
  logEl.scrollTop = logEl.scrollHeight;
}

function setLogOpen(open) {
  logEl.hidden = !open;
  // The label text is static in the markup; only the attribute moves, and
  // the CSS-drawn chevron beside it (`.acp-log-toggle-chevron`) reads that
  // same attribute to rotate itself.
  logToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
  if (open) logEl.scrollTop = logEl.scrollHeight;
}

// ---- shared localStorage helpers -------------------------------------------
//
// Generic try/catch wrappers, not railStored/railStore's original name; kept
// here since DEBUG_LOG_KEY (above) and, from Phase 3 on, the send-mode picker
// depend on them, and they are now shared infrastructure rather than
// acp.html-private. index.html's own rail-only preferences keep using its
// existing, differently-named dashRailStored/dashRailStore — see the
// collision-check note at the top of this file.

function railStored(key) {
  try { return localStorage.getItem(key); } catch (e) { return null; }
}

function railStore(key, value) {
  try { localStorage.setItem(key, value); } catch (e) { /* not fatal */ }
}
