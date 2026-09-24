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

// ---- slash command / skill autocomplete palette (SC1) ----------------------
//
// Ported out of acp.html's inline script (dashboard/ACP feature-parity plan,
// Phase 2), the same structural precedent Phase 1 set above: a plain
// top-level declaration here, reached as a bare global from each host page's
// own script (IIFE or not), with page-lifecycle state passed in as accessor
// FUNCTIONS through initCommandPaletteDom() rather than closed over free
// variables that would not exist once the code moved out of acp.html's IIFE.
//
// Two known-dead paths in acp.html's pre-Phase-2 implementation are
// deliberately NOT ported (Success Criteria SC1; Design Decisions "Slash-
// palette dead paths"; independently confirmed by
// plans/done/260909-1127_ACP_V3_PRODUCTION_HARDENING.md's Phase 7 review,
// finding #3): `_cmdOptionsTimer`/`applyCommandOptions()` and any handling of
// `commands_options`/`commands_options_result` — the client never sends a
// `commands_options` request (no send site for it ever existed in the code
// this was ported from), so a `commands_options_result` reply can never
// arrive in production; the code that merged one into the dropdown was
// unreachable. `MAX_CMD_PARTIAL_CHARS` existed solely to cap the string that
// dead send path would have put on the wire and is dropped for the same
// reason — a numeric constant with no remaining reader, per CLAUDE.md's
// "unused code is deleted, not kept as a shim" rule.
//
// `.acp-cmd-placeholder` (the dropdown's own dead path — a placeholder row
// styled in style.css but never created by any code) has two further JS-side
// remnants also dropped here: `renderCommandDropdown`'s `catalogueEmpty`
// parameter (computed by its one caller, never read inside the function
// body — the unused hook for a placeholder row that was never wired), and
// `moveCommandSelection`'s `aria-disabled` branch (written to skip that
// placeholder row when navigating past it — no `<li>` this module ever
// creates sets `aria-disabled`, so the branch can never trigger).

var sessionCommands = [];
var sessionSkills = [];
var sessionMcpServers = null; // null = no notification yet; array = known server list
var cmdDropdownEl = null;
var _cmdSelectedIndex = -1;
var cmdPromptInput = null;
// () => current session id, or null/'' when nothing is attached. REQUIRED —
// see the file header for why a raw value cannot substitute for this.
var cmdGetSessionId = null;
// The host page's own send(type, payload, sid) function. REQUIRED — this
// file has no WebSocket of its own, and (unlike `logLine`, ported as a true
// global in Phase 1) `send` stays page-private on both hosts, so it must be
// handed in rather than called bare.
var cmdSend = null;
// () => void, called after this module programmatically changes
// cmdPromptInput.value (a skill completion inserted, a command cleared on
// send) so the host page can re-run whatever textarea-height/composer-
// control refresh logic it owns. acp.html's autoGrowPrompt() +
// refreshComposerControls() are IIFE-private functions and cannot be reached
// as bare globals from this file — the same IIFE boundary this file's header
// comment documents for `sessionId`/`replaying`. The dashboard passes
// dashRefreshSendButton (it has no textarea auto-grow yet, Phase 2 of the
// dashboard/ACP feature-parity plan). REQUIRED, same as `getSessionId`/
// `send` above — see the file header for why a raw value/bare global cannot
// substitute for an accessor. This used to default to a silent no-op
// (`refs.onPromptChanged || function(){}`) when a caller omitted it, which
// would have degraded to "composer controls looked stale after a skill
// insertion" with no diagnostic; omitting the fallback here means a future
// caller that forgets this accessor gets a loud TypeError at the first
// prompt-changing call instead, the same failure mode every other REQUIRED
// accessor in this file already has (Phase 2 review fix).
var cmdOnPromptChanged = null;

/** Called once by each loading page's own inline script. `getSessionId`,
 *  `send`, and `onPromptChanged` are REQUIRED — see the file header for why
 *  a raw value/bare global cannot substitute for any of them. Also attaches
 *  the dropdown's mousedown delegate (mirrors initSidCopyDom()'s own
 *  click-listener attachment, Phase 1 — DOM listeners this module owns are
 *  wired here, once, rather than by each host page). */
function initCommandPaletteDom(refs) {
  cmdDropdownEl = refs.cmdDropdownEl;
  cmdPromptInput = refs.promptInput;
  cmdGetSessionId = refs.getSessionId;
  cmdSend = refs.send;
  cmdOnPromptChanged = refs.onPromptChanged;

  // Delegated mousedown on the dropdown container: fires before the blur
  // event on the textarea, so the dropdown is not hidden before the click
  // registers. On mousedown, check if the target (or a closest ancestor
  // within the dropdown) is an <li>; if so, prevent the textarea from losing
  // focus, set the selection index to match the clicked <li>, then confirm.
  cmdDropdownEl.addEventListener('mousedown', function (ev) {
    var target = ev.target;
    var ul = cmdDropdownEl.querySelector('ul');
    if (!ul) return;
    var items = ul.childNodes;
    var clickedIdx = -1;
    var node = target;
    while (node && node !== cmdDropdownEl) {
      for (var i = 0; i < items.length; i++) {
        if (items[i] === node) { clickedIdx = i; break; }
      }
      if (clickedIdx >= 0) break;
      node = node.parentNode;
    }
    if (clickedIdx < 0) return;
    ev.preventDefault(); // prevent blur on the textarea
    _cmdSelectedIndex = clickedIdx;
    updateCommandSelection();
    confirmCommandSelection();
  });
}

/** Returns true when the slash command dropdown is currently visible. */
function isCommandDropdownVisible() {
  return !cmdDropdownEl.hidden;
}

/** Populate and show the slash command dropdown.
 *
 *  `partial` is the text after the leading `/`; an empty string shows all.
 *  Renders from the local `sessionCommands` and `sessionSkills` lists — the
 *  WS frame for server-side suggestions is sent by the debounced input
 *  handler path, not here, to avoid a double send when the input handler
 *  also calls this. */
function showCommandDropdown(partial, filter) {
  // partial may be empty string (show all) or absent in programmatic invocations
  // filter: 'skills' = skills only (mid-text /); 'all' or undefined = both
  var lc = partial ? partial.toLowerCase() : '';
  // Merge commands and skills into one flat list; preserve server order
  // within each type (commands first, then skills).
  var allItems = (filter === 'skills' ? [] : sessionCommands.map(function(c) {
    return {name: c.name, description: c.description, isSkill: false};
  })).concat(sessionSkills.map(function(s) {
    // Skill descriptions are verbose multi-sentence paragraphs — show only
    // the first sentence so the row stays compact.
    var desc = s.description || '';
    var dot = desc.indexOf('. ');
    if (dot > 0) desc = desc.slice(0, dot + 1);
    return {name: s.name, description: desc, isSkill: true};
  }));
  var items = allItems.filter(function(c) {
    return !lc || (c.name || '').toLowerCase().indexOf(lc) !== -1;
  });
  // Cap at 5 items to keep the palette compact.
  if (items.length > 5) items = items.slice(0, 5);
  renderCommandDropdown(items);
}

/** Re-render the dropdown list from an array of `{name, description?, isSkill?}`. */
function renderCommandDropdown(items) {
  var ul = cmdDropdownEl.querySelector('ul');
  if (!ul) {
    ul = document.createElement('ul');
    ul.setAttribute('role', 'presentation');
    cmdDropdownEl.appendChild(ul);
  }
  ul.textContent = '';
  if (!items.length) {
    // No matches (or catalogue loading): hide the dropdown so Enter falls
    // through to sendPrompt() rather than being consumed here. The user can
    // see /text in the box already — no need for a visible "no match" signal
    // that blocks the send key.
    hideCommandDropdown();
    return;
  }
  for (var i = 0; i < items.length; i++) {
    var li = document.createElement('li');
    li.setAttribute('role', 'option');
    li.id = 'acp-cmd-opt-' + i;
    var nameSpan = document.createElement('span');
    nameSpan.className = 'acp-cmd-name';
    nameSpan.textContent = '/' + (items[i].name || '').replace(/^\//, '');
    li.appendChild(nameSpan);
    if (items[i].isSkill === true) {
      // Badge appended as a flex sibling after nameSpan inside <li>.
      var badge = document.createElement('span');
      badge.className = 'acp-cmd-skill-badge';
      badge.textContent = 'skill';
      badge.setAttribute('aria-hidden', 'true');
      li.appendChild(badge);
    }
    var descSpan = document.createElement('span');
    descSpan.className = 'acp-cmd-desc';
    descSpan.textContent = items[i].description || '';
    li.appendChild(descSpan);
    ul.appendChild(li);
  }
  _cmdSelectedIndex = 0;
  updateCommandSelection();
  cmdDropdownEl.hidden = false;
  cmdPromptInput.setAttribute('aria-expanded', 'true');
}

/** Update the visual selection indicator in the dropdown. */
function updateCommandSelection() {
  var ul = cmdDropdownEl.querySelector('ul');
  if (!ul) return;
  var items = ul.childNodes;
  for (var i = 0; i < items.length; i++) {
    items[i].setAttribute('aria-selected', String(i === _cmdSelectedIndex));
  }
  if (_cmdSelectedIndex >= 0 && items[_cmdSelectedIndex]) {
    var selectedId = items[_cmdSelectedIndex].id || ('acp-cmd-opt-' + _cmdSelectedIndex);
    cmdPromptInput.setAttribute('aria-activedescendant', selectedId);
    if (typeof items[_cmdSelectedIndex].scrollIntoView === 'function') {
      items[_cmdSelectedIndex].scrollIntoView({block: 'nearest'});
    }
  } else {
    cmdPromptInput.setAttribute('aria-activedescendant', '');
  }
}

/** Hide the slash command dropdown. */
function hideCommandDropdown() {
  cmdDropdownEl.hidden = true;
  _cmdSelectedIndex = -1;
  cmdPromptInput.setAttribute('aria-expanded', 'false');
  cmdPromptInput.setAttribute('aria-activedescendant', '');
}

/** Confirm the currently selected dropdown item.
 *
 *  Skills: insert "/<name> " into the prompt and focus it so the user can
 *  type arguments — kiro-cli dispatches skills via prompt text, not via the
 *  commands/execute wire method.
 *
 *  Commands: send `commands_execute` and clear the textarea, matching the
 *  TUI behaviour for built-in slash commands.
 *
 *  No-op when no item is selected or the dropdown is not visible. */
function confirmCommandSelection() {
  if (!isCommandDropdownVisible()) return;
  var ul = cmdDropdownEl.querySelector('ul');
  if (!ul) { hideCommandDropdown(); return; }
  var items = ul.childNodes;
  var idx = _cmdSelectedIndex >= 0 ? _cmdSelectedIndex : 0;
  if (!items[idx]) { hideCommandDropdown(); return; }
  var nameEl = items[idx].querySelector('.acp-cmd-name');
  if (!nameEl) { hideCommandDropdown(); return; }
  var name = nameEl.textContent.replace(/^\//, '');
  var isSkill = !!items[idx].querySelector('.acp-cmd-skill-badge');
  hideCommandDropdown();
  if (isSkill) {
    // Replace the /token the user is currently typing with "/<name> ",
    // preserving any text that precedes it in the prompt. This lets skills
    // be called from the middle of a sentence, e.g.:
    //   "look at this /qp" → "look at this /qplan "
    // The same regex the input handler uses to detect the command token:
    // match /word at start OR after whitespace at the end of the value.
    var current = cmdPromptInput.value;
    var tokenMatch = current.match(/(?:^|\s)(\/\S*)$/);
    var completed = '/' + name + ' ';
    if (tokenMatch) {
      // Keep everything up to (but not including) the /token, then append
      // the completed skill name. tokenMatch.index points at the start of
      // the full match (which may include a leading space); the /token
      // starts one character later when there is a space, or at index 0.
      var tokenStart = tokenMatch.index + (tokenMatch[0].charAt(0) === '/' ? 0 : 1);
      cmdPromptInput.value = current.slice(0, tokenStart) + completed;
    } else {
      cmdPromptInput.value = completed;
    }
    cmdOnPromptChanged();
    cmdPromptInput.focus();
    var len = cmdPromptInput.value.length;
    cmdPromptInput.setSelectionRange(len, len);
    return;
  }
  cmdPromptInput.value = '';
  cmdOnPromptChanged();
  var sid = cmdGetSessionId();
  if (!sid) return;
  // Step 9 review, Fix 10: cmdSend()'s own return value used to go
  // unchecked -- a failed send (while disconnected) produced no feedback
  // beyond whatever send() itself already logs on failure (`logLine('error',
  // 'not connected — nothing sent')`, both host pages' own send()). That
  // line is already clear that nothing was sent, but not what was attempted
  // -- the user chose to strengthen this logLine signal rather than add a
  // toast (acp.html has no toast infrastructure at all), so a user scanning
  // the debug log panel both pages already have can tell which command
  // silently failed to execute, not just that "nothing was sent" in the
  // abstract.
  var sent = cmdSend('commands_execute', { name: name }, sid);
  if (!sent) {
    logLine('error', 'command "/' + name + '" was not executed — see the previous line');
  }
}

/** Move the dropdown selection up or down. */
function moveCommandSelection(delta) {
  if (!isCommandDropdownVisible()) return;
  var ul = cmdDropdownEl.querySelector('ul');
  if (!ul) return;
  var count = ul.childNodes.length;
  if (!count) return;
  var newIdx = (_cmdSelectedIndex + delta + count) % count;
  _cmdSelectedIndex = newIdx;
  updateCommandSelection();
}

/** Merge a `commands` frame's catalogue in and, if the dropdown is currently
 *  open, refresh it against the new list — mirrors the `commands`
 *  frame-handling both host pages used to carry inline. */
function setSessionCommands(list) {
  sessionCommands = list || [];
  if (cmdDropdownEl && !cmdDropdownEl.hidden) {
    showCommandDropdown(cmdPromptInput.value.slice(1));
  }
}

/** Same as setSessionCommands(), for a `skills` frame. */
function setSessionSkills(list) {
  sessionSkills = list || [];
  if (cmdDropdownEl && !cmdDropdownEl.hidden) {
    showCommandDropdown(cmdPromptInput.value.slice(1));
  }
}

/** Clear the catalogue and close the dropdown. Called by each host page
 *  whenever the session it belonged to goes away — a new or resubscribed
 *  `session` frame, `session_closed`, `agent_died` — so stale slash-command
 *  suggestions from a previous session never show up in the next one. */
function resetCommandPalette() {
  sessionCommands = [];
  sessionSkills = [];
  sessionMcpServers = null; // hide MCP indicator on session change
  _renderMcpIndicator();
  hideCommandDropdown();
}

/** Store the MCP server list from a `mcp_servers` frame and update the indicator.
 *  Called by acp.html and the dashboard when a `mcp_servers` frame arrives.
 *  SC-4, plan 260923_ACP_V3_SESSION_DELETE_WATCHDOG_MCP_STATUS. */
function setSessionMcpServers(list) {
  sessionMcpServers = Array.isArray(list) ? list : null;
  _renderMcpIndicator();
}

/** Render or hide the MCP indicator from the current `sessionMcpServers` value.
 *  `#acpMcpPanel` visibility is driven exclusively by `aria-expanded` on
 *  `#acpMcpToggle` via the CSS sibling selector — never via `.hidden`. */
function _renderMcpIndicator() {
  var indicatorEl = document.getElementById('acpMcpIndicator');
  if (!indicatorEl) return;

  if (!sessionMcpServers) {
    indicatorEl.hidden = true;
    var t = document.getElementById('acpMcpToggle');
    if (t) t.setAttribute('aria-expanded', 'false'); // don't re-open on next session's frame
    return;
  }

  indicatorEl.hidden = false;
  var toggleEl = document.getElementById('acpMcpToggle');
  var compactEl = document.getElementById('acpMcpCompact');
  var listEl    = document.getElementById('acpMcpList');
  var servers = sessionMcpServers;

  // Count connected servers; flag failed / auth-needed state.
  var connected = 0;
  var needsAction = false;
  var nonDisabled = 0;
  servers.forEach(function (srv) {
    if (srv.status === 'connected') connected++;
    if (srv.status !== 'disabled') nonDisabled++;
    if (srv.status === 'failed' || srv.failedAuthorization) needsAction = true;
  });

  // Compact label + accessible name (F5-3 fix: title alone degrades to bare text).
  var label = connected + ' connected';
  if (compactEl) compactEl.textContent = label;
  if (toggleEl) {
    // Set aria-label so screen readers announce context, not just the raw count.
    var ariaLabel = needsAction
      ? 'MCP servers — action needed (' + label + ')'
      : 'MCP servers — ' + label;
    toggleEl.setAttribute('aria-label', ariaLabel);
    toggleEl.title = ariaLabel;
    toggleEl.classList.toggle('acp-mcp-warn', needsAction);
    toggleEl.classList.toggle('acp-mcp-caution',
      !needsAction && connected < nonDisabled);
  }

  // Expanded list.
  if (!listEl) return;
  listEl.textContent = ''; // clear children without innerHTML (no-innerHTML rule)
  var _VALID_STATUSES = {connected: 1, connecting: 1, failed: 1, disabled: 1};
  servers.forEach(function (srv) {
    var li = document.createElement('li');
    li.className = 'acp-mcp-server';

    var badge = document.createElement('span');
    var safeStatus = _VALID_STATUSES[srv.status] ? srv.status : 'disabled';
    badge.className = 'acp-mcp-badge acp-mcp-badge-' + safeStatus;
    badge.setAttribute('aria-hidden', 'true');
    li.appendChild(badge);

    var nameEl = document.createElement('span');
    nameEl.className = 'acp-mcp-server-name';
    nameEl.textContent = srv.name || '?';
    li.appendChild(nameEl);

    if (srv.failedAuthorization && srv.authorizationUrl) {
      var url = srv.authorizationUrl;
      // Security: only open https:// URLs; reject javascript:, file:, data:, etc.
      if (typeof url !== 'string' || url.indexOf('https://') !== 0) {
        url = null;
      }
      if (url) {
        var btn = document.createElement('button');
        btn.className = 'acp-mcp-connect-btn';
        btn.type = 'button';
        btn.textContent = 'Connect';
        btn.addEventListener('click', function () {
          window.open(url, '_blank', 'noopener,noreferrer');
        });
        li.appendChild(btn);
      }
    }
    listEl.appendChild(li);
  });
}

/** A `commands_execute_result` frame: render the command's ack (if any) as a
 *  system message and close the dropdown — mirrors acp.html's original
 *  handle() case exactly, including the `compact` exclusion: verified
 *  2026-08-14 against kiro-cli 2.18.0 that its ack's own res.message is
 *  "Compacting conversation..." — the *same* moment the dedicated
 *  `compaction` frame (handled by each host page directly, not by this
 *  module) already rendered "Compacting conversation context...". Rendering
 *  both gave a palette-triggered compaction two "Compacting..." rows instead
 *  of one; the typed `/compact` prompt does not go through this path at all,
 *  so it never had the duplicate.
 *
 *  Incoming dependency (Phase 2 review fix, the mirror image of this file's
 *  own header comment on outgoing globals): `addSystemMessage` below is not
 *  defined in this file — it is a bare global provided by
 *  transcript-renderer.js, which must therefore load before composer-chrome.js
 *  on any host page (both acp.html and index.html already order their
 *  `<script src>` tags this way). */
function handleCommandsExecuteResult(payload) {
  hideCommandDropdown();
  var res = payload && payload.result;
  if (res && res.message && payload.name !== 'compact') {
    addSystemMessage(res.message);
  } else if (res && !res.success) {
    addSystemMessage('Command failed.');
  }
}
