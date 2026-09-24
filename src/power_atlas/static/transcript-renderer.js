// Shared transcript rendering, loaded by both /acp (templates/acp.html) and
// the dashboard's transcript panel (templates/index.html) — the mechanism
// that keeps a live ACP session and a static/file-replayed one rendering
// identically instead of drifting apart as two separate implementations
// (dashboard/ACP-merge plan, Phase 2).
//
// IMPORTANT — acp.html's inline script is wrapped whole in `(function () {
// ... })();`, so a variable it declares is private to that closure, not a
// true global a separately-loaded <script> can read or write. The state
// this file owns below (transcriptEl, userMsgEls, agentBody, toolRows, …)
// therefore has to be DECLARED here, not merely referenced here — moving a
// `var`/`function` declaration's lexical position doesn't change how a bare
// identifier elsewhere resolves it (JS walks the scope chain outward, and
// finds whichever declaration is currently in scope), so acp.html's inline
// script keeps reading and writing these exact same names, unqualified,
// exactly as it always did, and now resolves to the single binding declared
// here instead of a local one. What changed is only WHERE the declaration
// lives — every read/write site elsewhere is untouched. A page-specific
// piece of state that only acp.html's own lifecycle code needs (e.g.
// `replaying`, `sessionId`) stays declared in its inline script, same as
// before.
//
// DOM references are the one category that can't just move: the dashboard's
// panel and acp.html don't share markup or element ids, so this file can't
// look its own elements up. Each loading page calls `initTranscriptDom()`
// once, early in its own inline script, with its own element references.
//
// Moved incrementally, one slice at a time, each verified against the full
// tests/acp_page.test.mjs suite before landing — not as one big-bang
// extraction, given how deep this coupling turned out to be.
//
// Depends on globals the loading page provides: `document`, `window.Prism`
// (optional — colouring degrades to plain text without it, never fails),
// `navigator.clipboard` (optional, code-block copy button), `setTimeout`,
// and `logLine` (an optional page-provided log/status sink — guarded, since
// the dashboard panel has no debug log panel of its own).
//
// No innerHTML anywhere in this file, matching the rest of /acp's XSS
// control: every node is built with createElement/createElementNS and filled
// with textContent, never with a string of markup.

// ---- shared DOM references and transcript state ---------------------------
//
// `null`/empty until `initTranscriptDom()` runs. Every function below that
// reads one of these assumes it has already been called — true for both
// pages, since each calls it synchronously near the top of its own inline
// script, before anything can respond to a WS frame or a fetched transcript.
var transcriptEl = null;
var promptNavEl = null;
var promptUpBtn = null;
var promptDownBtn = null;

/** Called once by each loading page's own inline script, with that page's
 *  own element references — see the file header for why this can't be done
 *  from in here. */
function initTranscriptDom(refs) {
  transcriptEl = refs.transcriptEl;
  promptNavEl = refs.promptNavEl;
  promptUpBtn = refs.promptUpBtn;
  promptDownBtn = refs.promptDownBtn;
}

// DOM elements of user message rows, in order — drives the prompt nav arrows.
var userMsgEls = [];

// The currently-open assistant bubble's body element, or null between turns.
var agentBody = null;

// toolCallId -> the elements a later `tool_update` rewrites in place, so a
// call that runs then completes is one row rather than three.
var toolRows = Object.create(null);
// toolCallId -> setInterval id for in-progress tool calls whose elapsed time
// is ticking live. Cleared by clearTranscript() and when stoppedAt arrives.
var _toolTimers = Object.create(null);
// Accumulates acp-msg-tool rows added during the current turn; flushed into
// collapsible group containers at meta turn:end by flushToolGroups().
// Null between turns (reset at turn:start and after each flush).
var toolGroup = null; // accumulates tool-call rows during a turn; flushed at turn:end
var _toolGroupSeq = 0; // monotonic counter for unique group-body ids (aria-controls linkage)

function stuckToBottom() {
  // Measured before the append, not after: once the node is in the DOM the
  // pane is by definition no longer scrolled to the bottom, so a check made
  // afterwards would never stick and a long answer would scroll away under
  // anyone reading the top of it.
  return transcriptEl.scrollHeight - transcriptEl.scrollTop
           - transcriptEl.clientHeight < 60;
}

function _updateNavArrows() {
  var atTop    = transcriptEl.scrollTop <= 0;
  var atBottom = transcriptEl.scrollTop + transcriptEl.clientHeight >= transcriptEl.scrollHeight - 1;
  promptUpBtn.classList.toggle('acp-prompt-nav-btn--dim', atTop);
  promptDownBtn.classList.toggle('acp-prompt-nav-btn--dim', atBottom);
}

// Every map is `Object.create(null)` and every one of them is load-bearing
// for the same reason `RAIL_AVAILABILITY` is: on an object literal every
// `Object.prototype` key is a hit, so a token typed `"constructor"` would
// find the Object constructor in `MD_TAG` and hand it to `createElement`.
// These are closed sets, and a prototype-less map is what closes them.

// Refused outright rather than escaped and shown. Showing raw HTML as text
// is safe here — textContent parses nothing — but it is not *useful*: what
// the reader would learn from a page of escaped markup is nothing, and the
// rule is easier to hold true when it has no "unless" in it. `image` goes
// with them because a remote URL in an <img> is a request this page would be
// making on the agent's say-so.
var MD_DROPPED = Object.create(null);
MD_DROPPED.block_html = true;
MD_DROPPED.inline_html = true;
MD_DROPPED.image = true;

// Tokens that are a wrapper in the tree and nothing on the page. `block_text`
// is what mistune puts inside a tight list item; rendering it as an element
// would put a block box inside every <li>.
var MD_TRANSPARENT = Object.create(null);
MD_TRANSPARENT.block_text = true;

// token type -> the element it becomes. Every value here is a literal, which
// is what keeps `createElement` off the wire.
var MD_TAG = Object.create(null);
MD_TAG.paragraph = 'p';
MD_TAG.text = 'span';
MD_TAG.strong = 'strong';
MD_TAG.emphasis = 'em';
MD_TAG.codespan = 'code';
MD_TAG.list_item = 'li';
MD_TAG.linebreak = 'br';
// Three of the table plugin's five token types are a tag and nothing more.
// The other two carry something that needs deciding and have their own arms
// in `mdNode`: `table_head` holds its cells with no row token between them,
// and `table_cell` is the only token here with wire data in its attrs.
MD_TAG.table = 'table';
MD_TAG.table_body = 'tbody';
MD_TAG.table_row = 'tr';

// The `:---:` delimiter's alignment, as a class name from a closed map. A
// map and not `cell.style.textAlign = attrs.align` for the same reason
// `MD_HEADING` is keyed by string: `align` is a wire value like every other
// field on this page, and the set of alignments a table can have is three.
var MD_ALIGN = Object.create(null);
MD_ALIGN.left = 'acp-md-left';
MD_ALIGN.center = 'acp-md-center';
MD_ALIGN.right = 'acp-md-right';

// What a fenced block's info string may look like, which is the only place
// the wire says what language a snippet is. Absent on an indented block and
// on a bare fence, so the label is an upgrade for the blocks that carry one
// rather than something every block gets.
//
// A **shape** and not a set, which is the one place this file departs from
// the maps above. `MD_ALIGN` and `MD_HEADING` enumerate their values because
// a table has three alignments and a heading six levels; the languages an
// agent writes are open-ended and gain a new member every year, so an
// allowlist would answer `zig` with no label at all and go stale silently.
// The regex bounds instead what an info string may *look* like — one
// lowercase token, sixteen characters — and what it admits reaches
// `textContent` and a `data-` attribute and nothing else: never a tag name,
// never a class name, never a URL. The narrowing is real even so: no spaces
// means it cannot become a second class name, and the length cap means a
// fence labelled with a paragraph does not draw one in the corner.
var MD_LANG_RE = /^[a-z0-9][a-z0-9+#._-]{0,15}$/;

// The aliases worth spelling out, because `ps1` in the corner of a block
// reads as noise where `PowerShell` reads as a label. A language absent from
// here shows the token the agent wrote — this is a display table, and
// `MD_LANG_RE` above is the allowlist.
//
// `Object.create(null)` for the reason every map here is, and load-bearing
// in the same way: `constructor` is eleven lowercase letters, so it passes
// `MD_LANG_RE`, and on an object literal it would answer with the Object
// constructor for a label.
var MD_LANG_NAME = Object.create(null);
MD_LANG_NAME.bash = 'Bash';
MD_LANG_NAME.sh = 'Shell';
MD_LANG_NAME.zsh = 'Shell';
MD_LANG_NAME.shell = 'Shell';
MD_LANG_NAME.console = 'Console';
MD_LANG_NAME.ps1 = 'PowerShell';
MD_LANG_NAME.pwsh = 'PowerShell';
MD_LANG_NAME.powershell = 'PowerShell';
MD_LANG_NAME.bat = 'Batch';
MD_LANG_NAME.cmd = 'Batch';
MD_LANG_NAME.py = 'Python';
MD_LANG_NAME.python = 'Python';
MD_LANG_NAME.python3 = 'Python';
MD_LANG_NAME.js = 'JavaScript';
MD_LANG_NAME.mjs = 'JavaScript';
MD_LANG_NAME.cjs = 'JavaScript';
MD_LANG_NAME.javascript = 'JavaScript';
MD_LANG_NAME.jsx = 'JSX';
MD_LANG_NAME.ts = 'TypeScript';
MD_LANG_NAME.typescript = 'TypeScript';
MD_LANG_NAME.tsx = 'TSX';
MD_LANG_NAME.rs = 'Rust';
MD_LANG_NAME.rust = 'Rust';
MD_LANG_NAME.go = 'Go';
MD_LANG_NAME.golang = 'Go';
MD_LANG_NAME.rb = 'Ruby';
MD_LANG_NAME.ruby = 'Ruby';
MD_LANG_NAME.cs = 'C#';
MD_LANG_NAME.csharp = 'C#';
MD_LANG_NAME.c = 'C';
MD_LANG_NAME.h = 'C';
MD_LANG_NAME.cpp = 'C++';
MD_LANG_NAME.cxx = 'C++';
MD_LANG_NAME.java = 'Java';
MD_LANG_NAME.kt = 'Kotlin';
MD_LANG_NAME.kotlin = 'Kotlin';
MD_LANG_NAME.swift = 'Swift';
MD_LANG_NAME.php = 'PHP';
MD_LANG_NAME.lua = 'Lua';
MD_LANG_NAME.sql = 'SQL';
MD_LANG_NAME.json = 'JSON';
MD_LANG_NAME.jsonc = 'JSON';
MD_LANG_NAME.yml = 'YAML';
MD_LANG_NAME.yaml = 'YAML';
MD_LANG_NAME.toml = 'TOML';
MD_LANG_NAME.ini = 'INI';
MD_LANG_NAME.xml = 'XML';
MD_LANG_NAME.html = 'HTML';
MD_LANG_NAME.htm = 'HTML';
MD_LANG_NAME.css = 'CSS';
MD_LANG_NAME.scss = 'SCSS';
MD_LANG_NAME.md = 'Markdown';
MD_LANG_NAME.markdown = 'Markdown';
MD_LANG_NAME.diff = 'Diff';
MD_LANG_NAME.patch = 'Diff';
MD_LANG_NAME.dockerfile = 'Dockerfile';
MD_LANG_NAME.makefile = 'Makefile';
MD_LANG_NAME.txt = 'Text';
MD_LANG_NAME.text = 'Text';

// The aliases Prism does not answer to itself, mapped onto the grammar that
// fits. Prism already knows `js`, `py`, `sh`, `shell`, `rb`, `cs`, `kt`,
// `md`, `ts`, `yml`, `html`, `xml` and `dockerfile`; this is the rest of what
// `MD_LANG_NAME` accepts, and a word in neither place simply renders
// uncoloured.
//
// `Object.create(null)` for the usual reason, and doing real work here: the
// lookup below falls through to this map when `Prism.languages` has no own
// property by that name, so on an object literal `constructor` would come
// back a function and be passed to `tokenize` as a grammar.
var MD_LANG_GRAMMAR = Object.create(null);
MD_LANG_GRAMMAR.zsh = 'bash';
MD_LANG_GRAMMAR.console = 'bash';
MD_LANG_GRAMMAR.ps1 = 'powershell';
MD_LANG_GRAMMAR.pwsh = 'powershell';
MD_LANG_GRAMMAR.bat = 'batch';
MD_LANG_GRAMMAR.cmd = 'batch';
MD_LANG_GRAMMAR.python3 = 'python';
MD_LANG_GRAMMAR.mjs = 'javascript';
MD_LANG_GRAMMAR.cjs = 'javascript';
MD_LANG_GRAMMAR.rs = 'rust';
MD_LANG_GRAMMAR.golang = 'go';
MD_LANG_GRAMMAR.h = 'c';
MD_LANG_GRAMMAR.cxx = 'cpp';
MD_LANG_GRAMMAR.jsonc = 'json';
MD_LANG_GRAMMAR.htm = 'markup';
MD_LANG_GRAMMAR.patch = 'diff';

// What a Prism token may be called, as a shape. This is the one set on this
// page that is **not** agent-authored: token names come from the grammars in
// `static/prism.js`, which is a file this repo generates and vendors. The
// check is here anyway because a class name is being built by concatenation,
// and the property worth keeping is that no string reaches `className`
// without something having decided its shape — a grammar that named a token
// with a space in it would put a second class on the element, and this page
// has class names that mean things.
var MD_TOKEN_RE = /^[a-z][a-z0-9-]*$/;

// The largest snippet worth handing to Prism. Its grammars are regular
// expressions run on the main thread over text an agent wrote, and a bubble
// may legitimately carry `MAX_BUBBLE_CHARS` — 128 KiB — of it. Backtracking
// on a pathological snippet is a hung tab rather than a slow one, and this
// page's socket is in that tab. Past the cap the block renders uncoloured,
// which is the same outcome as a language Prism does not know.
var MD_HIGHLIGHT_MAX = 20000;

// Keyed by the heading level as a string, because `attrs.level` is a wire
// value like any other and `'h' + level` would be a tag name built by
// concatenation from it. A level this does not know falls back to a
// paragraph: the text still renders, at the wrong weight rather than not
// at all.
var MD_HEADING = Object.create(null);
MD_HEADING['1'] = 'h1';
MD_HEADING['2'] = 'h2';
MD_HEADING['3'] = 'h3';
MD_HEADING['4'] = 'h4';
MD_HEADING['5'] = 'h5';
MD_HEADING['6'] = 'h6';

// The only scheme an <a href> may carry. An allowlist and not a `javascript:`
// denylist: `data:`, `vbscript:`, and a leading-whitespace or
// mixed-case spelling of any of them all defeat a denylist, and the set of
// schemes worth linking to from a conversation is two.
var MD_LINK_SCHEME = /^https?:\/\//i;

/** Every character a token subtree would show, for the fall-through arm. */
function mdText(token) {
  if (!token || typeof token !== 'object') return '';
  var type = typeof token.type === 'string' ? token.type : '';
  if (MD_DROPPED[type]) return '';
  var kids = token.children;
  if (kids && typeof kids.length === 'number') {
    var out = '';
    for (var i = 0; i < kids.length; i++) out += mdText(kids[i]);
    return out;
  }
  return typeof token.raw === 'string' ? token.raw : '';
}

/** The language word a `block_code` token declares, normalised, or `''` for a
 *  block that declares none — an indented block, a bare fence, or an info
 *  string this page will not show. Both the label and the grammar are looked
 *  up from this, so there is one reading of the info string and not two.
 *
 *  Only the first word is read, because CommonMark's info string is not a
 *  language: everything after that word is free text, and mistune hands the
 *  whole of it over (` ```js {highlight} ` arrives as `"js {highlight}"`). */
function mdLangWord(token) {
  var info = token.attrs && token.attrs.info;
  if (typeof info !== 'string') return '';
  var word = info.trim().split(/\s+/)[0].toLowerCase();
  return MD_LANG_RE.test(word) ? word : '';
}

/** Prism's grammar for a language word, or `null`.
 *
 *  `Prism.languages` is an object literal — the one map this renderer reads
 *  that is not `Object.create(null)`, because Prism owns it and not this page
 *  — so `constructor`, `toString` and `__proto__` all answer it with
 *  something truthy. `hasOwnProperty` is what closes that, and the `typeof`
 *  check is the second door on the same hole. Both matter: `word` passed
 *  `MD_LANG_RE`, which admits every one of those names. */
function mdGrammar(word) {
  var prism = window.Prism;
  var langs = prism && prism.languages;
  if (!word || !langs || typeof prism.tokenize !== 'function') return null;
  var own = Object.prototype.hasOwnProperty;
  var key = own.call(langs, word) ? word : MD_LANG_GRAMMAR[word];
  if (typeof key !== 'string' || !own.call(langs, key)) return null;
  var grammar = langs[key];
  return grammar && typeof grammar === 'object' ? grammar : null;
}

/** One Prism token, as elements under `parent`.
 *
 *  This is the whole reason Prism is used through `tokenize()` and not
 *  through `highlightElement()`: the latter builds an HTML string and assigns
 *  it to `innerHTML`, and the property this page is built on is that no sink
 *  here parses markup. `tokenize()` returns data — a string, or a token whose
 *  `content` is a string, a token, or an array of either — and this walks it
 *  with `createElement` and `textContent` exactly as `mdNode` walks mistune's
 *  tree. The library's grammars do the hard part; only the rendering is
 *  ours. */
function mdToken(parent, token) {
  if (typeof token === 'string') {
    var plain = document.createElement('span');
    plain.textContent = token;
    parent.appendChild(plain);
    return;
  }
  if (!token || typeof token !== 'object') return;
  var el = document.createElement('span');
  var names = [];
  if (MD_TOKEN_RE.test(String(token.type))) names.push('acp-tok-' + token.type);
  // Some grammars carry the colour on an alias rather than on the type — a
  // pattern typed `attr-value` aliased `string`, say — so a walker that read
  // `type` alone would render those uncoloured for no visible reason.
  var alias = token.alias;
  if (typeof alias === 'string') alias = [alias];
  if (alias && typeof alias.length === 'number') {
    for (var a = 0; a < alias.length; a++) {
      if (MD_TOKEN_RE.test(String(alias[a]))) names.push('acp-tok-' + alias[a]);
    }
  }
  if (names.length) el.className = names.join(' ');
  var content = token.content;
  if (typeof content === 'string') {
    el.textContent = content;
  } else if (content && typeof content.length === 'number') {
    for (var i = 0; i < content.length; i++) mdToken(el, content[i]);
  } else if (content) {
    mdToken(el, content);
  }
  parent.appendChild(el);
}

/** Fill `code` with `text` coloured, and say whether that happened. `false`
 *  leaves `code` untouched for the caller to fill as plain text — Prism
 *  absent, a language it has no grammar for, a snippet over the cap, or a
 *  grammar that threw.
 *
 *  Nothing is built before `tokenize` has returned, so a throw cannot leave a
 *  half-coloured block behind. And no failure here costs the reader the code,
 *  which is the same trade `_close_bubble` makes on the server: the colouring
 *  is an upgrade to a block that already rendered correctly. */
function mdColour(code, text, word) {
  if (!text || text.length > MD_HIGHLIGHT_MAX) return false;
  var grammar = mdGrammar(word);
  if (!grammar) return false;
  var tokens;
  try {
    tokens = window.Prism.tokenize(text, grammar);
  } catch (err) {
    // `logLine` is optional: acp.html provides a debug-log panel, the
    // dashboard's transcript panel has none, and a highlighting failure must
    // not throw here either way (colouring is an upgrade to a block that
    // already rendered correctly, same as acp.py's own _close_bubble rule).
    if (typeof logLine === 'function') {
      logLine('error', 'highlighting a ' + word + ' block failed: ' +
                       ((err && err.message) || err));
    }
    return false;
  }
  if (!tokens || typeof tokens.length !== 'number') return false;
  for (var i = 0; i < tokens.length; i++) mdToken(code, tokens[i]);
  return true;
}

/** Fill one built element from its token: children if it has them, its own
 *  raw text otherwise (`text` and `codespan` carry no children). */
function mdFill(el, token) {
  var kids = token.children;
  if (kids && typeof kids.length === 'number') {
    mdAppend(el, kids);
    return;
  }
  el.textContent = typeof token.raw === 'string' ? token.raw : '';
}

function mdAppend(parent, tokens) {
  if (!tokens || typeof tokens.length !== 'number') return;
  for (var i = 0; i < tokens.length; i++) mdNode(parent, tokens[i]);
}

// Icon factories hoisted to module scope so they are created once rather
// than re-declared inside every mdNode call that renders a code block.
// Using createElementNS throughout because SVG elements must live in the
// SVG namespace — createElement produces HTML elements that browsers do
// not render as SVG shapes.
function makeCopyIcon() {
  var NS = 'http://www.w3.org/2000/svg';
  var svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', '0 0 16 16');
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '1.5');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  var rect = document.createElementNS(NS, 'rect');
  rect.setAttribute('x', '5');
  rect.setAttribute('y', '1');
  rect.setAttribute('width', '9');
  rect.setAttribute('height', '11');
  rect.setAttribute('rx', '1.5');
  var path = document.createElementNS(NS, 'path');
  path.setAttribute('d', 'M2 5v9a1.5 1.5 0 0 0 1.5 1.5H11');
  svg.appendChild(rect);
  svg.appendChild(path);
  return svg;
}
function makeCheckIcon() {
  var NS = 'http://www.w3.org/2000/svg';
  var svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', '0 0 16 16');
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '1.8');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  var poly = document.createElementNS(NS, 'polyline');
  poly.setAttribute('points', '2,8 6,13 14,4');
  svg.appendChild(poly);
  return svg;
}

function mdNode(parent, token) {
  if (!token || typeof token !== 'object') return;
  var type = typeof token.type === 'string' ? token.type : '';
  if (MD_DROPPED[type]) return;

  if (type === 'link') {
    var url = token.attrs && token.attrs.url;
    if (typeof url === 'string' && MD_LINK_SCHEME.test(url)) {
      var a = document.createElement('a');
      a.setAttribute('href', url);
      // The two go together: without a target the link navigates /acp away
      // and takes the live socket with it, and with one, `noopener` is what
      // stops the opened page reaching back through `window.opener`.
      a.setAttribute('target', '_blank');
      a.setAttribute('rel', 'noopener noreferrer');
      mdAppend(a, token.children);
      parent.appendChild(a);
      return;
    }
    // Anything else — `javascript:`, `data:`, a bare word — renders as its
    // own text. The reader still sees what the agent wrote; it is just not
    // a thing that can be clicked.
    mdAppend(parent, token.children);
    return;
  }

  if (type === 'block_code') {
    var codeRaw = typeof token.raw === 'string' ? token.raw : '';
    var word = mdLangWord(token);
    var pre = document.createElement('pre');
    var code = document.createElement('code');
    // Coloured if Prism is loaded and has a grammar for the language, and
    // plain otherwise — the two are the same block either way, so a page
    // served without the highlighter is the transcript as it was.
    if (!mdColour(code, codeRaw, word)) code.textContent = codeRaw;
    pre.appendChild(code);
    // Language-less blocks are appended directly — no wrapper, no copy
    // button (there is no label and no wrapper to pin the button to).
    if (!word) {
      parent.appendChild(pre);
      return;
    }
    // Only labeled code blocks get the wrapper and copy-to-clipboard button.
    // The SVG is a minimal clipboard icon matching the rail settings button's
    // shape. Clipboard API is best-effort: the block is still usable for
    // selection+copy when it is unavailable.
    // All DOM construction uses createElement/setAttribute — no innerHTML.
    // makeCopyIcon / makeCheckIcon are hoisted to module scope above mdNode.
    var copyBtn = document.createElement('button');
    copyBtn.className = 'acp-md-copy';
    copyBtn.setAttribute('type', 'button');
    copyBtn.setAttribute('aria-label', 'Copy code');
    copyBtn.setAttribute('title', 'Copy code');
    copyBtn.appendChild(makeCopyIcon());
    (function (btn, text) {
      btn.addEventListener('click', function () {
        if (!navigator.clipboard) return;
        navigator.clipboard.writeText(text).then(function () {
          // Brief checkmark feedback — swap to a tick, then back.
          while (btn.firstChild) btn.removeChild(btn.firstChild);
          btn.appendChild(makeCheckIcon());
          setTimeout(function () {
            while (btn.firstChild) btn.removeChild(btn.firstChild);
            btn.appendChild(makeCopyIcon());
          }, 1500);
        });
      });
    }(copyBtn, codeRaw));
    var named = MD_LANG_NAME[word];
    var lang = typeof named === 'string' ? named : word;
    // Drawn by the stylesheet out of `data-lang` rather than appended as an
    // element, which buys two things. Generated content is not part of
    // `textContent`, so a reader who selects the block and copies it gets the
    // code and not the word "Python" above it — the check that the block
    // keeps its code would pass either way, and a snippet that pastes with a
    // language name welded to the top of it is the defect. And a wrapper is
    // something to pin the label to: pinned inside the <pre>, whose box is
    // the scroll container, it would scroll away with the code on any block
    // over the 220px cap.
    var codeWrap = document.createElement('div');
    codeWrap.className = 'acp-md-code';
    codeWrap.setAttribute('data-lang', lang);
    codeWrap.appendChild(pre);
    codeWrap.appendChild(copyBtn);
    parent.appendChild(codeWrap);
    return;
  }

  if (type === 'list') {
    var ordered = !!(token.attrs && token.attrs.ordered);
    var list = document.createElement(ordered ? 'ol' : 'ul');
    mdAppend(list, token.children);
    parent.appendChild(list);
    return;
  }

  if (type === 'table_head') {
    // The plugin's shape is not uniform: `table_head` holds `table_cell`
    // tokens directly, while `table_body` holds `table_row` tokens that hold
    // theirs. Mapping this to <thead> alone would put <th> straight inside
    // it, and the browser would hoist them into a row this page never built
    // — so the row is built here, explicitly.
    var thead = document.createElement('thead');
    var headRow = document.createElement('tr');
    mdAppend(headRow, token.children);
    thead.appendChild(headRow);
    parent.appendChild(thead);
    return;
  }

  if (type === 'table_cell') {
    var cellAttrs = token.attrs || {};
    // Both tag names are literals, as every tag name on this page is; the
    // wire only chooses between them.
    var cell = document.createElement(cellAttrs.head ? 'th' : 'td');
    var alignClass = MD_ALIGN[String(cellAttrs.align)];
    if (alignClass) cell.className = alignClass;
    mdFill(cell, token);
    parent.appendChild(cell);
    return;
  }

  if (type === 'heading') {
    var level = token.attrs && token.attrs.level;
    var heading = document.createElement(MD_HEADING[String(level)] || 'p');
    mdFill(heading, token);
    parent.appendChild(heading);
    return;
  }

  if (type === 'softbreak') {
    // A newline inside one paragraph, which every markdown renderer collapses
    // to a space. A span and not a text node because this harness — and the
    // rule this page keeps — allow createElement and textContent only.
    var gap = document.createElement('span');
    gap.textContent = ' ';
    parent.appendChild(gap);
    return;
  }

  if (MD_TRANSPARENT[type]) {
    mdAppend(parent, token.children);
    return;
  }

  var tag = MD_TAG[type];
  if (tag) {
    var el = document.createElement(tag);
    mdFill(el, token);
    parent.appendChild(el);
    return;
  }

  // Everything else — a blockquote, a token type a future mistune adds, a
  // table from a server whose mistune lacks the `table` plugin and which
  // therefore never emitted one. Its text, with none of its structure,
  // and nothing at all when it has no text (`blank_line`,
  // `thematic_break`). Never an element named after it.
  var text = mdText(token);
  if (!text) return;
  var span = document.createElement('span');
  span.textContent = text;
  parent.appendChild(span);
}

/** The tokens as a flat list of built elements, or an empty list. Built
 *  detached so a tree that renders to nothing — an answer that was only an
 *  <img>, say — leaves the bubble's plain text alone instead of blanking it.
 *  The sink is duck-typed rather than a container element because moving
 *  children out of one is the one DOM operation this page has no use for
 *  anywhere else. */
function mdBuild(tokens) {
  var built = [];
  mdAppend({ appendChild: function (node) { built.push(node); return node; } },
           tokens);
  return built;
}

// `role` is a literal at every call site, and `appendChunk` (still declared
// in each loading page's own inline script, for now — it also needs
// agentBody/toolGroup/flushToolGroups, not yet moved here) narrows the one
// value that comes off the wire to a fixed pair before it gets here. That is
// deliberate: this is the only place a class name is built by concatenation,
// and a payload-derived one would be an attribute sink for a string the
// agent wrote.
function addMessage(role, text) {
  var stick = stuckToBottom();
  var row = document.createElement('div');
  row.className = 'acp-msg acp-msg-' + role;
  var who = document.createElement('span');
  who.className = 'acp-msg-role';
  who.textContent = role === 'user' ? 'user'
    : (role === 'agent' ? 'agent' : (role === 'error' ? 'error' : (role === 'steer' ? 'user' : '')));
  var body = document.createElement('div');
  body.className = 'acp-msg-body';
  body.textContent = text;
  row.appendChild(who);
  row.appendChild(body);
  transcriptEl.appendChild(row);
  if (role === 'user') {
    userMsgEls.push(row);
    promptNavEl.hidden = userMsgEls.length < 2;
    _updateNavArrows();
  }
  if (stick) transcriptEl.scrollTop = transcriptEl.scrollHeight;
  return body;
}

/** Append a non-bubble, lightly styled system row to the transcript.
 *
 *  Used for compaction status indicators and other ephemeral notices.
 *  SECURITY: MUST use textContent / createTextNode — never innerHTML.
 *  The `text` parameter comes from the server (agent-controlled). */
function addSystemMessage(text) {
  var stick = stuckToBottom();
  var el = document.createElement('div');
  el.className = 'acp-system-msg';
  el.textContent = String(text);
  transcriptEl.appendChild(el);
  if (stick) transcriptEl.scrollTop = transcriptEl.scrollHeight;
}

// ---- tool-call rendering ---------------------------------------------------
//
// A status kiro-cli adds later renders as no badge rather than as a raw
// string, which is the same trade the tally has always made. Add the key
// here — and a colour beside `.acp-tool-status[data-status=…]` in style.css
// — to bring it back into view.
var TOOL_STATUS_LABEL = Object.create(null);
TOOL_STATUS_LABEL.pending     = 'pending';
TOOL_STATUS_LABEL.in_progress = 'in progress';
TOOL_STATUS_LABEL.completed   = 'completed';
TOOL_STATUS_LABEL.failed      = 'failed';

function elapsedText(startedAt, endAt) {
  if (typeof startedAt !== 'number' || !startedAt) return '';
  var now = (typeof endAt === 'number' && endAt) ? endAt : Date.now() / 1000;
  var secs = Math.round(now - startedAt);
  if (secs < 0) secs = 0;
  var m = Math.floor(secs / 60);
  var s = secs % 60;
  return m > 0 ? m + 'm ' + s + 's' : s + 's';
}

// Update a tool call row's elapsed-time span, and manage its live ticker.
//
// `row`  — the toolRows entry (`{timeSpan, startedAt, stoppedAt, ...}`).
// `id`   — the toolCallId string used to key `_toolTimers`, or null/empty
//          for rows with no id (no timer is started in that case).
//
// When `stoppedAt` is present the display is frozen and any running timer
// is cleared. When the call is still in progress (`!stoppedAt`) and an `id`
// is available, a 1-second ticker is started if one is not already running.
// Called for both the new-row path and the update path so one code path
// handles all cases.
function _updateToolTime(row, id) {
  var span = row.timeSpan;
  if (!span || !row.startedAt) {
    if (span) span.hidden = true;
    return;
  }
  var text = elapsedText(row.startedAt, row.stoppedAt || undefined);
  if (!text) { span.hidden = true; return; }
  span.textContent = text;
  span.hidden = false;
  if (row.stoppedAt) {
    // Call is done — clear any running ticker.
    if (id && _toolTimers[id]) {
      clearInterval(_toolTimers[id]);
      delete _toolTimers[id];
    }
  } else if (id && !_toolTimers[id]) {
    // Call is in progress — start a 1-second ticker.
    _toolTimers[id] = setInterval(function() {
      if (!row.timeSpan) {
        clearInterval(_toolTimers[id]);
        delete _toolTimers[id];
        return;
      }
      row.timeSpan.textContent = elapsedText(row.startedAt);
      row.timeSpan.hidden = false;
    }, 1000);
  }
}

// Creates a collapse toggle button for a tool call command wrapper.
// toolTitle is the human-readable tool name used in the accessible label.
function _makeToolToggle(cmdWrap, toolTitle, detail) {
  // Cap toolTitle at 80 chars to prevent an agent-authored string from producing
  // a thousands-character aria-label attribute (no XSS risk via setAttribute,
  // but unbounded wire data in an attribute is a hygiene violation).
  var safeTitle = (toolTitle || 'tool').slice(0, 80);
  var safeDetail = detail || 'command detail';
  var toggle = document.createElement('button');
  toggle.className = 'acp-tool-toggle';
  toggle.type = 'button';
  toggle.setAttribute('aria-expanded', 'false');
  toggle.setAttribute('aria-label', 'Show ' + safeDetail + ' — ' + safeTitle);
  toggle.addEventListener('click', function () {
    var open = toggle.getAttribute('aria-expanded') === 'true';
    toggle.setAttribute('aria-expanded', open ? 'false' : 'true');
    toggle.setAttribute('aria-label',
      (open ? 'Show' : 'Hide') + ' ' + safeDetail + ' — ' + safeTitle);
    // cmdWrap.hidden is an attribute mutation, not a DOM addition, so aria-live
    // re-announcement is not expected per spec (role="log" tracks additions only).
    cmdWrap.hidden = open;
  });
  return toggle;
}

// One 16x16 stroke path per value of the ACP `ToolKind` enum. A closed set:
// a kind absent from here draws no icon at all, which is the same trade
// TOOL_STATUS_LABEL makes and for the same reason — nothing agent-authored
// reaches the DOM as markup or as a path.
//
// SVG built with createElementNS rather than a glyph or a font character.
// A glyph would be cheaper, but the coverage is not there: the obvious
// choices for `search` and `move` (U+2315, U+21C4) are missing from enough
// desktop fonts that the row would show a tofu box on the machines that
// lack them, and a missing icon that looks like a rendering fault is worse
// than no icon. These are drawn.
var TOOL_KIND_ICON = Object.create(null);
TOOL_KIND_ICON.read        = 'M4 2h5l3 3v9H4zM9 2v3h3';
TOOL_KIND_ICON.edit        = 'M3 13l.7-2.7 6.6-6.6 2 2-6.6 6.6zM10 3l1.5-1.5 2 2L12 5';
TOOL_KIND_ICON['delete']   = 'M3.5 4.5h9M6.5 4.5V2.5h3v2M5 4.5l.7 9h4.6l.7-9';
TOOL_KIND_ICON.move        = 'M2.5 6h9l-2.5-2.5M13.5 10h-9l2.5 2.5';
TOOL_KIND_ICON.search      = 'M7 2.5a4.2 4.2 0 100 8.4 4.2 4.2 0 000-8.4zM10.2 10.2l3.3 3.3';
TOOL_KIND_ICON.execute     = 'M3 3.5L7 8l-4 4.5M8.5 12.5h5';
TOOL_KIND_ICON.think       = 'M8 1.8a6.2 6.2 0 100 12.4 6.2 6.2 0 000-12.4M5.5 8h.01M8 8h.01M10.5 8h.01';
TOOL_KIND_ICON.fetch       = 'M8 2.5v7M5 6.5l3 3 3-3M2.5 13h11';
TOOL_KIND_ICON.switch_mode = 'M2.5 5.5h9M9 3l2.5 2.5L9 8M13.5 10.5h-9M7 8l-2.5 2.5L7 13';
// `other` is the enum's catch-all and also marks planning steps kiro-cli
// filters from its own display, so it gets the quietest mark there is
// rather than a picture of anything.
TOOL_KIND_ICON.other       = 'M8 6.6a1.4 1.4 0 100 2.8 1.4 1.4 0 000-2.8';

var SVG_NS = 'http://www.w3.org/2000/svg';

/** The icon element for a wire `kind`, or null when this build has no path
 *  for it. `aria-hidden`: the kind is already on the row as text, so a
 *  screen reader announcing it twice would be noise, not access. */
function _toolKindIcon(wireKind) {
  var d = wireKind ? TOOL_KIND_ICON[wireKind] : null;
  if (!d) return null;
  var svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('class', 'acp-tool-icon');
  svg.setAttribute('viewBox', '0 0 16 16');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('focusable', 'false');
  var p = document.createElementNS(SVG_NS, 'path');
  p.setAttribute('d', d);   // from the closed map above, never from the wire
  svg.appendChild(p);
  return svg;
}

/** The trailing segment of a path, for either separator. Falls back to the
 *  whole string, so a path shape this does not expect still reads. */
function _basename(p) {
  var s = String(p || '');
  var cut = Math.max(s.lastIndexOf('/'), s.lastIndexOf('\\'));
  return (cut >= 0 && cut < s.length - 1) ? s.slice(cut + 1) : s;
}

/** The row's file subtitle, built from ACP's own `locations`.
 *
 *  The basename is what a reader scans for and the full path is what they
 *  occasionally need, so by default the first is rendered and the second is
 *  the `title`. A call touching several files names the first and counts
 *  the rest — listing twenty paths in a transcript row helps nobody.
 *
 *  `fullPath`: the edit panel (see `_renderEditPanel`) shows this line only
 *  once the reader has already asked to expand the row, so there is no
 *  reason left to hide the path behind a hover — the full string is shown
 *  inline instead, wrapping via `.acp-tool-loc-path`'s existing
 *  `overflow-wrap`. */
function _toolLocations(locations, fullPath) {
  if (!locations || !locations.length) return null;
  var first = locations[0];
  if (!first || !first.path) return null;
  var el = document.createElement('div');
  el.className = 'acp-tool-loc';
  var name = document.createElement('span');
  name.className = 'acp-tool-loc-path';
  name.textContent = (fullPath ? String(first.path) : _basename(first.path)) +
    (typeof first.line === 'number' ? ':' + first.line : '');
  if (!fullPath) {
    // Agent-authored, so `title` is set as an attribute value and never parsed.
    name.setAttribute('title', String(first.path));
  }
  el.appendChild(name);
  if (locations.length > 1) {
    var more = document.createElement('span');
    more.className = 'acp-tool-loc-more';
    more.textContent = '+' + (locations.length - 1) + ' more';
    el.appendChild(more);
  }
  return el;
}

// Write a wire status onto a row's status badge.
//
// One function for both the new-row and the update path, because the rule
// they have to share is the merge rule and it is easy to get wrong in one of
// two places: `tool_call_update` omits every field it is not changing, so an
// absent status means "no change" and must never overwrite a status the row
// already shows. This page used to write `'started'` on a new row and
// `'update'` on an update, so an update that carried only `content` — which
// is most of them — replaced a real status with a word the protocol never
// sends.
//
// The badge is hidden rather than blanked while there is nothing to say: an
// empty pill is a visible claim that the status is empty, which is a
// different (and false) statement from "not reported yet".
function _setToolStatus(el, wireStatus) {
  if (!el || !wireStatus) return;             // absent = no change
  var label = TOOL_STATUS_LABEL[wireStatus];  // closed set; see the map
  if (!label) return;                         // unknown value: leave as-is
  el.textContent = label;
  // `wireStatus` reaches an attribute only after the map has vouched for it,
  // so the value in the DOM is one of four literals rather than agent text.
  el.setAttribute('data-status', wireStatus);
  el.hidden = false;
}

// A `tool_output` body whose row has not been built yet, keyed by
// toolCallId. The server broadcasts the body immediately *before* the
// `tool_update` carrying the digest, and for a call whose first frame is a
// content-only update that update is also what creates the row — so the
// body can genuinely arrive first. Drained by `addToolCall`.
var pendingToolOutput = Object.create(null);

/** "3.4 KB" / "812 B" — output sizes only, so no need for anything above MB. */
function _humanBytes(n) {
  if (typeof n !== 'number' || !isFinite(n) || n < 0) return '';
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
  return (n / (1024 * 1024)).toFixed(1) + ' MB';
}

/** Whether a digest describes a call that should also have sent a body.
 *
 *  This is what separates "there was nothing to show" from "there was
 *  something and this page no longer has it": the body is broadcast and
 *  never recorded, so after a reload the digest replays alone. A `exit 0`
 *  with no output must not claim anything was lost. */
function _expectsBody(d) {
  return !!d && (d.form === 'diff' || (typeof d.bytes === 'number' && d.bytes > 0));
}

/** The one-line summary that survives a reload, rendered into the row.
 *
 *  Every branch here reads a number or a hard-capped string — this is the
 *  half the server records, and the reason it is a summary rather than the
 *  output is the replay-buffer budget noted above MAX_TOOL_OUTPUT_CHARS in
 *  acp.py. All text via textContent, as everywhere else on this page. */
function _renderToolDigest(d) {
  var wrap = document.createElement('div');
  wrap.className = 'acp-tool-digest';
  if (d.form === 'exec') {
    if (typeof d.exitStatus === 'number') {
      var code = document.createElement('span');
      // The one number an operator under -a most needs, so it gets the
      // status palette rather than the dim summary colour.
      code.className = 'acp-tool-exit';
      code.setAttribute('data-ok', d.exitStatus === 0 ? 'true' : 'false');
      code.textContent = 'exit ' + d.exitStatus;
      wrap.appendChild(code);
    }
    if (typeof d.bytes === 'number' && d.bytes > 0) {
      wrap.appendChild(_digestNote(
        d.lines + (d.lines === 1 ? ' line' : ' lines') + ', ' + _humanBytes(d.bytes)));
    }
    if (d.stderrHead) {
      var err = document.createElement('div');
      err.className = 'acp-tool-stderr';
      err.textContent = d.stderrHead + (d.stderrTruncated ? '…' : '');
      wrap.appendChild(err);
    }
  } else if (d.form === 'diff') {
    var stat = document.createElement('span');
    stat.className = 'acp-tool-diffstat';
    var plus = document.createElement('span');
    plus.className = 'acp-tool-diffstat-add';
    plus.textContent = '+' + (d.added || 0);
    stat.appendChild(plus);
    var minus = document.createElement('span');
    minus.className = 'acp-tool-diffstat-del';
    minus.textContent = '−' + (d.removed || 0);
    stat.appendChild(minus);
    wrap.appendChild(stat);
    if (d.isNew) wrap.appendChild(_digestNote('new file'));
  } else {
    wrap.appendChild(_digestNote(
      d.lines + (d.lines === 1 ? ' line' : ' lines') + ', ' + _humanBytes(d.bytes)));
  }
  return wrap.childNodes.length ? wrap : null;
}

function _digestNote(text) {
  var el = document.createElement('span');
  el.className = 'acp-tool-digest-note';
  el.textContent = text;
  return el;
}

/** The body: a collapsible beside the command's, never recorded server-side.
 *
 *  Reuses `_makeToolToggle` rather than inventing a second toggle idiom, so
 *  the two collapsibles on a tool row open the same way and read the same
 *  way to a screen reader. */
function _renderToolBody(payload) {
  var inner = (payload.form === 'diff')
    ? _renderDiff(payload) : _renderOutputText(payload);
  if (!inner) return null;
  var wrap = document.createElement('div');
  wrap.className = 'acp-tool-output';
  wrap.hidden = true;
  wrap.appendChild(inner);
  var row = document.createElement('div');
  row.className = 'acp-tool-output-row';
  var toggle = _makeToolToggle(
    wrap, payload.form === 'diff' ? 'diff' : 'output');
  // `className +=` rather than `classList.add`: the toggle keeps the shared
  // `.acp-tool-toggle` styling and gains a hook for addressing the output
  // one specifically.
  toggle.className += ' acp-tool-output-toggle';
  var label = document.createElement('span');
  label.className = 'acp-tool-output-label';
  label.textContent = payload.form === 'diff' ? 'Show diff' : 'Show output';
  label.style.cursor = 'pointer';
  label.addEventListener('click', function () { toggle.click(); });
  row.appendChild(toggle);
  row.appendChild(label);
  var holder = document.createElement('div');
  holder.appendChild(row);
  holder.appendChild(wrap);
  return holder;
}

function _renderOutputText(payload) {
  var text = String(payload.text || '');
  if (!text) return null;
  var box = document.createElement('div');
  // Same box as the command block: monospace, scrollable, pre-wrap. Output
  // is bytes a command printed, NOT prose the agent wrote, so it never goes
  // near mdBuild — the markdown path exists for the agent's own writing and
  // pointing it at tool output would put a parser on untrusted bytes.
  box.className = 'acp-tool-cmd';
  box.textContent = text;
  var out = document.createElement('div');
  out.appendChild(box);
  if (payload.truncated) {
    var more = document.createElement('div');
    more.className = 'acp-tool-more';
    more.textContent = 'showing the last ' + text.length +
                       ' of ' + payload.length + ' characters';
    out.appendChild(more);
  }
  return out;
}

/** A line-level diff of the two texts the wire supplied.
 *
 *  Common prefix and suffix are matched and everything between is shown as
 *  removed-then-added. Not an LCS: the inner diff would be a nicer read on a
 *  scattered edit, and it is also an O(n*m) table computed on the UI thread
 *  for a collapsible most readers never open. The cheap version is exact at
 *  the two ends, which is where an edit's context actually is. */
/** `startLine` seeds the number gutter: 1 for a whole-file create, or
 *  `locations[0].line` for a fragment (`strReplace` sends only the changed
 *  region, not the whole file, so numbering from 1 would be wrong there). */
function _renderDiff(payload, startLine) {
  // A trailing newline is how a well-formed text file ends, and splitting on
  // it yields a final empty element that is not a line of the file. Dropping
  // it here keeps the diff from ending on a blank row that looks like a
  // deletion nobody made.
  var newLines = _splitLines(payload.newText);
  var oldLines = payload.oldText === null || payload.oldText === undefined
    ? null : _splitLines(payload.oldText);
  var base = typeof startLine === 'number' ? startLine : 1;
  var box = document.createElement('div');
  box.className = 'acp-tool-diff';
  if (oldLines === null) {
    // A new file: every line is an addition and there is no old side.
    for (var n = 0; n < newLines.length; n++) _diffLine(box, '+', newLines[n], base + n);
    return box;
  }
  var head = 0;
  while (head < oldLines.length && head < newLines.length
         && oldLines[head] === newLines[head]) head++;
  var tail = 0;
  while (tail < oldLines.length - head && tail < newLines.length - head
         && oldLines[oldLines.length - 1 - tail] === newLines[newLines.length - 1 - tail]) tail++;
  // Up to three lines of leading context, so a change never appears
  // unanchored at the top of the box.
  var ctx = Math.max(0, head - 3);
  // Two counters: old and new agree through the common prefix/suffix, but
  // diverge across the changed region whenever the edit adds or removes
  // lines, so a single counter would mislabel one side of that region.
  var oldNum = base + ctx, newNum = base + ctx;
  if (ctx > 0) _diffLine(box, '@', '… ' + ctx + ' unchanged');
  for (var c = ctx; c < head; c++) {
    _diffLine(box, ' ', oldLines[c], newNum);
    oldNum++; newNum++;
  }
  for (var o = head; o < oldLines.length - tail; o++) _diffLine(box, '-', oldLines[o], oldNum++);
  for (var a = head; a < newLines.length - tail; a++) _diffLine(box, '+', newLines[a], newNum++);
  for (var t = 0; t < Math.min(tail, 3); t++) {
    _diffLine(box, ' ', newLines[newLines.length - tail + t], newNum);
    oldNum++; newNum++;
  }
  if (tail > 3) _diffLine(box, '@', '… ' + (tail - 3) + ' unchanged');
  if (payload.truncated) {
    var more = document.createElement('div');
    more.className = 'acp-tool-more';
    more.textContent = 'the diff was clipped before rendering';
    box.appendChild(more);
  }
  return box;
}

function _splitLines(text) {
  var lines = String(text || '').split('\n');
  if (lines.length > 1 && lines[lines.length - 1] === '') lines.pop();
  return lines;
}

// `marker` is one of four literals chosen by the caller, never wire data.
// `lineNum` is omitted for the '@' gap marker, which names no single line.
function _diffLine(box, marker, text, lineNum) {
  var line = document.createElement('div');
  line.className = 'acp-tool-diff-line';
  line.setAttribute('data-d', marker === ' ' ? 'ctx' :
                    marker === '@' ? 'gap' : marker === '+' ? 'add' : 'del');
  if (typeof lineNum === 'number') {
    var num = document.createElement('span');
    num.className = 'acp-tool-diff-num';
    num.textContent = String(lineNum);
    line.appendChild(num);
  }
  var g = document.createElement('span');
  g.className = 'acp-tool-diff-gutter';
  g.textContent = marker === '@' ? '⋯' : marker;
  line.appendChild(g);
  var t = document.createElement('span');
  t.className = 'acp-tool-diff-text';
  t.textContent = String(text);
  line.appendChild(t);
  box.appendChild(line);
}

/** The path an edit row's quick-info line and panel path line both use:
 *  the wire's own `locations`, falling back to the diff digest's `path`
 *  when `locations` is empty. Defensive layering — in practice the server
 *  attaches `locations` (live or backfilled) whenever a diff digest exists
 *  — kept as a fallback rather than an assumption. */
function _editRowPath(known) {
  var locations = known.locations;
  if (locations && locations[0] && locations[0].path) return locations;
  var digest = known.digest;
  if (digest && digest.form === 'diff' && digest.path) return [{ path: digest.path }];
  return null;
}

/** The one-liner beside an edit row's name: a short filename and the +/-
 *  stat, so a reader can tell what an edit touched without opening the
 *  row. Hidden while the panel is open — the panel repeats the same
 *  filename and stat, larger and with the full path, so showing both at
 *  once is the same fact twice rather than two facts. */
function _renderEditQuickInfo(known) {
  var el = known.quickInfo;
  el.textContent = '';
  if (known.panel && known.panel.hidden === false) { el.hidden = true; return; }
  var path = _editRowPath(known);
  if (!path) { el.hidden = true; return; }
  el.hidden = false;
  var name = document.createElement('span');
  name.className = 'acp-tool-quick-path';
  name.textContent = _basename(path[0].path);
  el.appendChild(name);
  var digest = known.digest;
  if (digest && digest.form === 'diff') {
    var stat = document.createElement('span');
    stat.className = 'acp-tool-diffstat';
    var plus = document.createElement('span');
    plus.className = 'acp-tool-diffstat-add';
    plus.textContent = '+' + (digest.added || 0);
    stat.appendChild(plus);
    var minus = document.createElement('span');
    minus.className = 'acp-tool-diffstat-del';
    minus.textContent = '−' + (digest.removed || 0);
    stat.appendChild(minus);
    el.appendChild(stat);
  }
}

/** The edit row's single collapsible: full path, then the diffstat, then
 *  whatever body arrived — a diff, a failure's text, or (after a reload
 *  with no backfill) the "not retained" notice. Rebuilt from scratch on
 *  every call from `known.locations`/`known.digest`/`known.outputPayload`,
 *  whichever of the three most recently changed — the three arrive on
 *  independent frames (locations and the digest on `tool_call`/
 *  `tool_call_update`, the body on a separate `tool_output` broadcast, in
 *  no guaranteed order — see `pendingToolOutput`), and rebuilding is cheap
 *  next to tracking three insertion points by hand. Also refreshes the
 *  row's quick-info one-liner, which is driven by the same three fields.
 *
 *  Line numbers seed from `locations[0].line` — the fragment offset for a
 *  `strReplace`, absent (so seeded at 1) for a whole-file `create`, which
 *  matches kiro-cli's own TUI numbering a new file from line 1. */
function _renderEditPanel(known) {
  _renderEditQuickInfo(known);
  var panel = known.panel;
  panel.textContent = '';
  var digest = known.digest;
  var locations = known.locations;
  var path = _editRowPath(known);
  var pathEl = _toolLocations(path, true);
  if (pathEl) panel.appendChild(pathEl);
  var digestEl = digest ? _renderToolDigest(digest) : null;
  if (digestEl) panel.appendChild(digestEl);
  var out = known.outputPayload;
  if (out && out.form === 'diff') {
    var startLine = locations && locations[0] && typeof locations[0].line === 'number'
      ? locations[0].line : 1;
    panel.appendChild(_renderDiff(out, startLine));
  } else if (out) {
    var text = _renderOutputText(out);
    if (text) panel.appendChild(text);
  } else if (_expectsBody(digest)) {
    var note = document.createElement('div');
    note.className = 'acp-tool-lost';
    note.textContent = 'output not retained after reload';
    panel.appendChild(note);
  }
}

/** Put a digest on a row, replacing any earlier one, and say so when the
 *  body it describes is not on this page.
 *
 *  The "not retained" line is the honest half of the record/broadcast split:
 *  the digest is recorded and replays after a reload, the body is not. A row
 *  that showed "142 lines" with no way to see them and no explanation would
 *  read as a bug. */
function _applyToolDigest(known, digest) {
  if (known.kind === 'edit') {
    known.digest = digest;
    _renderEditPanel(known);
    return;
  }
  var prev = known.body.querySelector('.acp-tool-digest');
  var built = _renderToolDigest(digest);
  if (prev && prev.parentNode) prev.parentNode.removeChild(prev);
  if (!built) return;
  // After the path subtitle when there is one, not merely after the head:
  // the path says *which file*, the digest says *what happened to it*, and
  // a "+3 −0" floating above the filename it describes reads backwards.
  var anchor = known.body.querySelector('.acp-tool-loc')
            || known.body.querySelector('.acp-tool-head');
  if (anchor && anchor.nextSibling) known.body.insertBefore(built, anchor.nextSibling);
  else known.body.appendChild(built);
  var hasBody = !!known.body.querySelector('.acp-tool-output-wrap');
  var lost = known.body.querySelector('.acp-tool-lost');
  if (_expectsBody(digest) && !hasBody) {
    if (!lost) {
      var note = document.createElement('div');
      note.className = 'acp-tool-lost';
      note.textContent = 'output not retained after reload';
      known.body.appendChild(note);
    }
  } else if (lost && lost.parentNode === known.body) {
    known.body.removeChild(lost);
  }
}

/** Attach an output body to its row, or hold it until the row exists. */
function addToolOutput(payload) {
  var id = payload && payload.toolCallId;
  if (!id) return;
  var known = toolRows['t:' + id];
  if (!known) { pendingToolOutput[id] = payload; return; }
  _attachToolOutput(known, payload);
}

function _attachToolOutput(known, payload) {
  if (known.kind === 'edit') {
    known.outputPayload = payload;
    _renderEditPanel(known);
    return;
  }
  // One body per row: a call that streams several content updates would
  // otherwise stack a collapsible per chunk. The later body wins, which is
  // the one carrying the most output.
  var existing = known.body.querySelector('.acp-tool-output-wrap');
  var built = _renderToolBody(payload);
  if (!built) return;
  built.className = 'acp-tool-output-wrap';
  if (existing && existing.parentNode === known.body) {
    known.body.removeChild(existing);
  }
  known.body.appendChild(built);
  // A body means nothing was lost, so retract any reload notice the digest
  // put there. Ordering normally makes this moot — the body precedes the
  // digest — but a late body must not leave the row contradicting itself.
  var lost = known.body.querySelector('.acp-tool-lost');
  if (lost && lost.parentNode === known.body) known.body.removeChild(lost);
}

function addToolCall(payload) {
  // Every class name below is a literal. `status`, `kind` and `title` are
  // agent-authored and reach the page only through textContent — under -a
  // there is no permission gate, so this string can be anything, and it is
  // the one thing an operator has to read to know what ran.
  var id = payload.toolCallId;
  var known = id ? toolRows['t:' + id] : null;
  if (known) {
    _setToolStatus(known.status, payload.status);
    if (known.kind === 'edit') {
      // Everything an edit row shows lives in the one panel behind the
      // one toggle — see `_renderEditPanel`. `_applyToolDigest` already
      // branches on `known.kind` to store the digest and rebuild the
      // panel; `locations` has no equivalent setter, so it is handled here.
      if (payload.locations) {
        known.locations = payload.locations;
        _renderEditPanel(known);
      }
      if (payload.output) _applyToolDigest(known, payload.output);
      if (typeof payload.startedAt === 'number') known.startedAt = payload.startedAt;
      if (typeof payload.stoppedAt === 'number') known.stoppedAt = payload.stoppedAt;
      _updateToolTime(known, id);
      return;
    }
    // `locations` can arrive on the update rather than the opening call —
    // every field on a `tool_call_update` is optional, so which frame
    // carries it is the agent's choice. Added once; a second update naming
    // the same files must not stack a second subtitle onto the row.
    if (payload.locations && !known.body.querySelector('.acp-tool-loc')) {
      var lateLoc = _toolLocations(payload.locations);
      // After the head, before the command block, wherever that ended up.
      if (lateLoc) {
        var afterHead = known.body.querySelector('.acp-tool-head');
        if (afterHead && afterHead.nextSibling) {
          known.body.insertBefore(lateLoc, afterHead.nextSibling);
        } else {
          known.body.appendChild(lateLoc);
        }
      }
    }
    if (payload.output) _applyToolDigest(known, payload.output);
    if (payload.command && !known.body.querySelector('.acp-tool-cmd')) {
      var cmdWrap = commandBlock(payload);
      cmdWrap.hidden = true;
      var knownHead = known.body.querySelector('.acp-tool-head');
      if (knownHead && !knownHead.querySelector('.acp-tool-toggle')) {
        // head is always present (built in the new-row path above) but guard defensively
        var lateToggle = _makeToolToggle(cmdWrap, payload.title || payload.kind);
        knownHead.appendChild(lateToggle);
        var lateName = knownHead.querySelector('.acp-tool-name');
        if (lateName) {
          lateName.style.cursor = 'pointer';
          lateName.addEventListener('click', function () { lateToggle.click(); });
        }
      }
      known.body.appendChild(cmdWrap);
    }
    // Update elapsed time — picks up startedAt/stoppedAt from this update
    // frame (carried forward from the opening tool_call by the server).
    if (typeof payload.startedAt === 'number') known.startedAt = payload.startedAt;
    if (typeof payload.stoppedAt === 'number') known.stoppedAt = payload.stoppedAt;
    _updateToolTime(known, id);
    return;
  }

  var stick = stuckToBottom();
  // A tool call ends the open agent bubble: the prose after it is a new
  // paragraph, not a continuation of the sentence the call interrupted.
  agentBody = null;

  var row = document.createElement('div');
  row.className = 'acp-msg acp-msg-tool';
  var who = document.createElement('span');
  who.className = 'acp-msg-role';
  who.textContent = 'tool';
  var body = document.createElement('div');
  body.className = 'acp-msg-body';

  var head = document.createElement('div');
  head.className = 'acp-tool-head';
  // Icon first, so a column of rows can be read down the left edge without
  // parsing any of the text beside it.
  var icon = _toolKindIcon(payload.kind);
  if (icon) head.appendChild(icon);
  var name = document.createElement('span');
  name.className = 'acp-tool-name';
  name.textContent = payload.title || payload.kind || 'tool call';
  head.appendChild(name);
  // Only when a title is also present: without one the *name* is already
  // the kind, and a chip repeating it would say the same word twice.
  if (payload.kind && payload.title) {
    var kind = document.createElement('span');
    kind.className = 'acp-tool-kind';
    kind.textContent = payload.kind;
    head.appendChild(kind);
  }
  var status = document.createElement('span');
  status.className = 'acp-tool-status';
  // Built hidden and filled by `_setToolStatus`, which is also what a later
  // `tool_update` calls. A `tool_call` carrying no status leaves the badge
  // off the row until one arrives, rather than inventing a word for it.
  status.hidden = true;
  _setToolStatus(status, payload.status);
  head.appendChild(status);
  var timeSpan = document.createElement('span');
  timeSpan.className = 'acp-tool-time';
  timeSpan.hidden = true;
  head.appendChild(timeSpan);
  body.appendChild(head);

  var isEdit = payload.kind === 'edit';
  var panel = null;
  var quickInfo = null;
  if (isEdit) {
    // One toggle, one panel: collapsed shows the head (name, kind chip,
    // status) plus a short filename+stat one-liner (`_renderEditQuickInfo`,
    // outside the panel so it survives collapse); expanded reveals the
    // full path, the diffstat, and the diff or failure text — see
    // `_renderEditPanel`. Built unconditionally, unlike the command toggle
    // below — an edit row always has something worth expanding to once its
    // output lands, and there is exactly one control for it, not one per
    // field that happens to arrive.
    quickInfo = document.createElement('span');
    quickInfo.className = 'acp-tool-quick';
    quickInfo.hidden = true;
    head.appendChild(quickInfo);
    panel = document.createElement('div');
    panel.className = 'acp-tool-edit-panel';
    panel.hidden = true;
    var editToggle = _makeToolToggle(panel, payload.title || payload.kind, 'diff');
    head.appendChild(editToggle);
    // Runs after `_makeToolToggle`'s own listener above, so `panel.hidden`
    // already reflects the click when this reads it.
    editToggle.addEventListener('click', function () {
      var k = id ? toolRows['t:' + id] : null;
      if (k) _renderEditQuickInfo(k);
    });
    name.style.cursor = 'pointer';
    name.addEventListener('click', function () { editToggle.click(); });
    body.appendChild(panel);
  } else {
    var loc = _toolLocations(payload.locations);
    if (loc) body.appendChild(loc);

    if (payload.command) {
      var cmdWrap = commandBlock(payload);
      cmdWrap.hidden = true;
      var toggle = _makeToolToggle(cmdWrap, payload.title || payload.kind);
      head.appendChild(toggle);
      // Clicking the name (pink text) is equivalent to clicking the toggle arrow.
      name.style.cursor = 'pointer';
      name.addEventListener('click', function () { toggle.click(); });
      body.appendChild(cmdWrap);
    }
  }

  row.appendChild(who);
  row.appendChild(body);
  transcriptEl.appendChild(row);
  if (id) {
    toolRows['t:' + id] = { status: status, body: body, kind: payload.kind,
      panel: panel, quickInfo: quickInfo, locations: payload.locations || null,
      digest: null, outputPayload: null,
      timeSpan: timeSpan,
      startedAt: (typeof payload.startedAt === 'number') ? payload.startedAt : null,
      stoppedAt: (typeof payload.stoppedAt === 'number') ? payload.stoppedAt : null };
  }
  // Drain a body that arrived before this row existed, then the digest —
  // in that order, so `_applyToolDigest` sees the body and does not post a
  // "not retained" notice about output that is right there.
  var hadPendingOutput = id && !!pendingToolOutput[id];
  if (hadPendingOutput) {
    _attachToolOutput(toolRows['t:' + id], pendingToolOutput[id]);
    delete pendingToolOutput[id];
  }
  if (id && payload.output) _applyToolDigest(toolRows['t:' + id], payload.output);
  // An edit row's panel reflects `locations` too, and a call opening with
  // only a path (no output yet) would otherwise render nothing — the two
  // branches above only render on an output/digest arrival.
  if (id && isEdit && !payload.output && !hadPendingOutput) {
    _renderEditPanel(toolRows['t:' + id]);
  }
  // Render initial elapsed time (or start a live ticker for in-progress calls).
  if (id) _updateToolTime(toolRows['t:' + id], id);
  // Accumulate this row for turn-end grouping. The flush at meta turn:end
  // will wrap consecutive sub-runs of >=2 rows into collapsible group containers.
  if (!toolGroup) toolGroup = [];
  toolGroup.push(row);
  if (stick) transcriptEl.scrollTop = transcriptEl.scrollHeight;
}

function commandBlock(payload) {
  var wrap = document.createElement('div');
  var cmd = document.createElement('div');
  cmd.className = 'acp-tool-cmd';
  cmd.textContent = payload.command;
  wrap.appendChild(cmd);
  if (payload.commandTruncated) {
    // The clip is stated rather than silent: a command that ends mid-word
    // and looks complete is worse than no rendering at all.
    var more = document.createElement('div');
    more.className = 'acp-tool-more';
    more.textContent = 'showing the first ' + payload.command.length +
                       ' of ' + payload.commandLength + ' characters';
    wrap.appendChild(more);
  }
  return wrap;
}

// Wraps all consecutive sub-runs of tool-call rows (even single calls) into
// collapsible group containers. Called at meta turn:end. Sets toolGroup = null when done.
//
// Key invariants:
//   - toolRows[id].status and toolRows[id].body are DOM refs; reparenting
//     them into the group body does not invalidate those refs.
//   - The group header is a static snapshot of the turn's tools — it is
//     intentionally not updated by later tool_update mutations.
//   - toolGroup is reset in clearTranscript() and meta turn:start, so this
//     function always operates on a single turn's accumulation.
function flushToolGroups() {
  if (!toolGroup || toolGroup.length < 1) { toolGroup = null; return; }

  // Split toolGroup into consecutive sub-runs by DOM adjacency.
  // A prose bubble between two tool calls in the same turn makes them
  // non-adjacent siblings — they belong in separate groups (or none).
  var runs = [];
  var cur = [toolGroup[0]];
  for (var i = 1; i < toolGroup.length; i++) {
    if (toolGroup[i - 1].nextSibling === toolGroup[i]) {
      cur.push(toolGroup[i]);
    } else {
      runs.push(cur);
      cur = [toolGroup[i]];
    }
  }
  runs.push(cur);

  // Capture scroll position ONCE before any DOM mutation. stuckToBottom()
  // measures transcriptEl geometry, which changes after each insertion,
  // so a per-iteration call would give stale readings for all but the first.
  var stick = stuckToBottom();

  for (var r = 0; r < runs.length; r++) {
    var rows = runs[r];
    // Every sub-run, even a single tool call, is now wrapped in a group.

    // Capture insertion point BEFORE removing any row — once rows are moved
    // out, the last row has no nextSibling in the transcript.
    var insertionRef = rows[rows.length - 1].nextSibling;

    // Build name tally (preserves first-seen order).
    // Use the tool's semantic kind (execute, read, write…) rather than its
    // title, which is a command preview and far too verbose for a header.
    // Fall back to the name span only when kind is absent (e.g. legacy rows).
    var nameCounts = Object.create(null);
    var nameOrder = [];
    for (var n = 0; n < rows.length; n++) {
      var kindEl = rows[n].querySelector('.acp-tool-kind');
      var nameEl = rows[n].querySelector('.acp-tool-name');
      var nm = (kindEl ? kindEl.textContent : '') || (nameEl ? nameEl.textContent : 'tool');
      if (!nameCounts[nm]) { nameCounts[nm] = 0; nameOrder.push(nm); }
      nameCounts[nm]++;
    }

    // Build the status tally from each row's `data-status`, which
    // `_setToolStatus` sets only for a value TOOL_STATUS_LABEL vouched for.
    // Reading the attribute rather than the badge's text is what keeps this
    // honest now that the two differ — the badge renders `in_progress` as
    // "in progress", and re-deriving the key from the rendered label would
    // put display wording on the lookup path.
    //
    // A row with no attribute contributes nothing: either its status was
    // never reported, or it was a value this build does not know. Both are
    // "no claim", which is the right thing for a count to make.
    //
    // Snapshot at turn:end; tool_update mutations after this point are not
    // reflected in the header tally (static snapshot by design).
    var stCounts = Object.create(null);
    var stOrder = [];
    for (var s = 0; s < rows.length; s++) {
      var stEl = rows[s].querySelector('.acp-tool-status');
      var stRaw = stEl ? (stEl.getAttribute('data-status') || '') : '';
      var st = TOOL_STATUS_LABEL[stRaw]; // undefined for unknown/absent
      if (st) {
        if (!stCounts[st]) { stCounts[st] = 0; stOrder.push(st); }
        stCounts[st]++;
      }
    }

    var nameTally = nameOrder.map(function (nm) {
      return nameCounts[nm] > 1 ? nm + ' \xd7' + nameCounts[nm] : nm;
    }).join(', ');
    var stTally = stOrder.map(function (st) {
      return stCounts[st] > 1 ? st + ' \xd7' + stCounts[st] : st;
    }).join(', ');
    var toolWord = rows.length === 1 ? 'tool' : 'tools';
    var headerText = 'Called ' + rows.length + ' ' + toolWord + ': ' + nameTally + (stTally ? ' \xb7 ' + stTally : '');

    // Build group container.
    var group = document.createElement('div');
    group.className = 'acp-tool-group';
    var groupToggle = document.createElement('button');
    groupToggle.className = 'acp-tool-group-toggle';
    groupToggle.type = 'button';
    groupToggle.setAttribute('aria-expanded', 'false');
    groupToggle.textContent = headerText;
    group.appendChild(groupToggle);

    var groupBody = document.createElement('div');
    groupBody.id = 'acp-tg-' + (++_toolGroupSeq); // unique id for aria-controls
    groupBody.className = 'acp-tool-group-body';
    groupBody.hidden = true; // collapsed by default

    // Move rows into group body.
    for (var k = 0; k < rows.length; k++) {
      if (rows[k].parentNode === transcriptEl) transcriptEl.removeChild(rows[k]);
      groupBody.appendChild(rows[k]);
    }
    group.appendChild(groupBody);

    // Insert the group where the block's first row was. For consecutive rows,
    // last.nextSibling equals what immediately followed the entire block,
    // so insertBefore(group, last.nextSibling) places the group at first's
    // original DOM position.
    transcriptEl.insertBefore(group, insertionRef);

    // IIFE per iteration: `var` is function-scoped, so without this all
    // click handlers in a multi-run turn share the final iteration's
    // groupToggle + groupBody bindings — every toggle would control only
    // the last group's body. Same IIFE pattern as renderCrewPanel (acp.html).
    (function (gt, gb) {
      // aria-label gives AT a terse action verb (textContent carries the visible
      // count+tally for sighted users, prefixed with "Tool calls: " — the split
      // is intentional, not a gap: AT hears only the action verb, sighted users
      // see the full tally with the label).
      gt.setAttribute('aria-label', 'Expand tool call group');
      gt.setAttribute('aria-controls', gb.id);
      gt.addEventListener('click', function () {
        var open = gt.getAttribute('aria-expanded') === 'true';
        gt.setAttribute('aria-expanded', open ? 'false' : 'true');
        gt.setAttribute('aria-label', open ? 'Expand tool call group' : 'Collapse tool call group');
        gb.hidden = open;
        // When opening the group, expand all child tool-call detail panels
        // that are still collapsed (aria-expanded === 'false').
        if (!open) {
          var childToggles = gb.querySelectorAll('.acp-tool-toggle');
          for (var ci = 0; ci < childToggles.length; ci++) {
            if (childToggles[ci].getAttribute('aria-expanded') === 'false') {
              childToggles[ci].click();
            }
          }
        }
      });
    }(groupToggle, groupBody));

    if (stick) transcriptEl.scrollTop = transcriptEl.scrollHeight;
  }
  toolGroup = null; // also reset in clearTranscript() and at meta turn:start
}

// ---- streaming assistant text + markdown reflow ----------------------------

function renderMarkdown(tokens) {
  // The bubble this applies to is whichever one is open, and the frame
  // always arrives immediately before the frame that closes it. Taken and
  // cleared first: a frame that arrives with no bubble open — a replay whose
  // chunks were evicted out from under it — must still not leave a stale one
  // behind for the next answer to be rendered into.
  var body = agentBody;
  agentBody = null;
  if (!body) return;
  var built = mdBuild(tokens);
  if (built.length === 0) return;
  var stick = stuckToBottom();
  // Emptied by textContent for the reason `clearTranscript` gives.
  body.textContent = '';
  // A literal, and the class the stylesheet hangs the block spacing off —
  // `.acp-msg-body`'s `white-space: pre-wrap` would double every margin.
  body.className = 'acp-msg-body acp-msg-md';
  for (var i = 0; i < built.length; i++) body.appendChild(built[i]);
  if (stick) transcriptEl.scrollTop = transcriptEl.scrollHeight;
}

function appendChunk(role, text) {
  if (role === 'agent' && agentBody) {
    var stick = stuckToBottom();
    agentBody.textContent += text;
    if (stick) transcriptEl.scrollTop = transcriptEl.scrollHeight;
    return;
  }
  // A user chunk marks the start of a new turn, which means the previous
  // agent turn has ended. During a loaded session replay kiro-cli never
  // sends meta turn:end frames, so flushToolGroups() would otherwise never
  // fire and consecutive tool calls stay as individual rows. Flushing here
  // mirrors the bubble-flush the server already performs at the same
  // boundary (_flush_bubble is called before every user_message_chunk).
  if (role === 'user' && toolGroup) flushToolGroups();
  var body = addMessage(role, text);
  agentBody = role === 'agent' ? body : null;
}

// ---- permission requests ----------------------------------------------

/** Render an inbound `session/request_permission` (SC-9) as an interactive
 *  choice in the transcript: the question, plus one button per option.
 *
 *  SECURITY: `title` and each option's `name` are agent-controlled text —
 *  set via textContent only, matching addMessage/addSystemMessage's rule.
 *  Never innerHTML.
 *
 *  Clicking a button sends `permission_response` with the chosen optionId
 *  and disables every button in the row. That is a UX nicety only, not the
 *  double-answer guard — the server's pop-before-write in
 *  _handle_permission_response is what actually prevents answering the
 *  same request twice; this only stops an obviously-confusing second click
 *  in the same tab before the server's reply (if any) comes back.
 *
 *  `send` is optional: acp.html provides its own WS-send function as a
 *  page global, but the dashboard's static/replayed transcript panel has
 *  no live connection to answer through (Phase 3 of this merge, once it
 *  exists, may change that only for a held kiro-cli-v3 session). Guarded
 *  the same way logLine is above — a missing capability degrades the
 *  button to a no-op click rather than throwing.
 *
 *  `consent` (260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 3)
 *  is the frame's allowlisted projection of kiro-cli's consent block — what
 *  capability is asked for, against which resource, and which rule matched.
 *  Optional, and every field in it is optional: a frame without one, or with
 *  `{}`, renders exactly as before. See permissionConsentBlock below. */
function addPermissionRequest(requestId, sid, title, options, consent) {
  var stick = stuckToBottom();
  var row = document.createElement('div');
  row.className = 'acp-msg acp-msg-permission';
  // Threaded onto the DOM so a later `permission_resolved` frame (SC-9
  // review fix) can find this exact row -- keyed by the same requestId the
  // server keys `_pending_permission` by. `dataset` always stores strings,
  // so the lookup side (findPermissionRequestRow) also stringifies before
  // comparing, whether requestId arrives as a number or a string.
  row.dataset.requestId = String(requestId);
  var who = document.createElement('span');
  who.className = 'acp-msg-role';
  who.textContent = 'question';
  var body = document.createElement('div');
  body.className = 'acp-msg-body';
  var question = document.createElement('div');
  question.className = 'acp-permission-question';
  question.textContent = title;
  body.appendChild(question);
  var consentBlock = permissionConsentBlock(consent);
  if (consentBlock) body.appendChild(consentBlock);
  var btnRow = document.createElement('div');
  btnRow.className = 'acp-permission-options';
  (options || []).forEach(function (opt) {
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'acp-btn acp-permission-option';
    btn.textContent = (opt && opt.name) ? opt.name : ((opt && opt.optionId) || '');
    // An "always" answer is session-scoped: PowerAtlas sends no scope with
    // its reply, so kiro-cli keeps the rule in memory for this session only
    // and saves nothing to disk. Say so on the button, or it reads as permanent.
    if (opt && typeof opt.kind === 'string' && /_always$/.test(opt.kind)) {
      btn.textContent += ' (this session)';
      btn.title = 'Applies until this session ends. Nothing is saved; ' +
                  'a new session asks again.';
    }
    btn.addEventListener('click', function () {
      if (btn.disabled) return;
      var buttons = btnRow.querySelectorAll('button');
      for (var i = 0; i < buttons.length; i++) buttons[i].disabled = true;
      btn.classList.add('acp-permission-chosen');
      if (typeof send === 'function') {
        send('permission_response',
             {requestId: requestId, optionId: opt.optionId}, sid);
      }
    });
    btnRow.appendChild(btn);
  });
  body.appendChild(btnRow);
  row.appendChild(who);
  row.appendChild(body);
  transcriptEl.appendChild(row);
  if (stick) transcriptEl.scrollTop = transcriptEl.scrollHeight;
  return row;
}

// 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 3: the consent
// fields shown under a permission question, in reading order. The server
// allowlists exactly these (acp.py `_project_consent`); anything else on the
// object is ignored here too rather than rendered, so the two allowlists agree.
var PERMISSION_CONSENT_FIELDS = [
  ['capability', 'Capability'],
  ['resource', 'Resource'],
  ['scope', 'Scope'],
  ['source', 'Source'],
];

// 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 3 review (G11):
// plain words for the identifiers a prompt most often carries, so a prompt
// reads "Write or delete files (fs_write)" rather than a bare `fs_write`. The
// capability names are the ones the shipped rule set uses
// (src/power_atlas/agents/permissions.yaml); `agent-profile` is the source
// measured in probe P4. Anything else falls back to the raw value. Looked up
// with hasOwnProperty, because the value is agent-authored and "constructor"
// or "__proto__" must not resolve to something on Object.prototype.
var PERMISSION_CAPABILITY_WORDS = {
  fs_read: 'Read files',
  fs_write: 'Write or delete files',
  shell: 'Run shell commands',
  mcp: 'Use an MCP tool',
  web_fetch: 'Fetch a web page',
  web_search: 'Search the web',
  subagent: 'Start a sub-agent',
  skill: 'Use a skill',
  power: 'Use a power',
};
var PERMISSION_SOURCE_WORDS = {
  'agent-profile': 'The agent’s own permissions',
};

function permissionWords(table, raw) {
  return Object.prototype.hasOwnProperty.call(table, raw) ? table[raw] : null;
}

/** "Plain words (raw)" for a known value, the raw value otherwise. */
function permissionLabelled(table, raw) {
  var words = permissionWords(table, raw);
  return words ? words + ' (' + raw + ')' : raw;
}

/** Build the consent block for a permission question, or return null when
 *  there is nothing to show.
 *
 *  SECURITY (260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 3):
 *  every value here is agent-authored and unbounded — the only cap on a
 *  `resource` is the 1 MiB inbound-line limit — so each goes in through
 *  `textContent` alone, never innerHTML, and the CSS on
 *  `.acp-permission-consent-value` is what contains its length (wrap anywhere,
 *  scroll past a max height). A field that is absent or not a string is
 *  skipped rather than stringified: `String({})` would print
 *  "[object Object]" as if the agent had said it. */
function permissionConsentBlock(consent) {
  if (!consent || typeof consent !== 'object' || Array.isArray(consent)) return null;
  var rows = [];
  PERMISSION_CONSENT_FIELDS.forEach(function (f) {
    var v = consent[f[0]];
    if (typeof v !== 'string' || v === '') return;
    if (f[0] === 'capability') v = permissionLabelled(PERMISSION_CAPABILITY_WORDS, v);
    else if (f[0] === 'source') v = permissionLabelled(PERMISSION_SOURCE_WORDS, v);
    rows.push([f[0], f[1], v]);
  });
  var rule = consent.matchedRule;
  if (rule && typeof rule === 'object' && !Array.isArray(rule)) {
    var parts = [];
    if (typeof rule.capability === 'string' && rule.capability !== '') {
      parts.push(permissionWords(PERMISSION_CAPABILITY_WORDS, rule.capability) || rule.capability);
    }
    if (typeof rule.effect === 'string' && rule.effect !== '') parts.push(rule.effect);
    if (parts.length) rows.push(['matchedRule', 'Matched rule', parts.join(' → ')]);
  }
  if (!rows.length) return null;
  var block = document.createElement('div');
  block.className = 'acp-permission-consent';
  rows.forEach(function (r) {
    var line = document.createElement('div');
    line.className = 'acp-permission-consent-row';
    line.dataset.field = r[0];
    var label = document.createElement('span');
    label.className = 'acp-permission-consent-label';
    label.textContent = r[1];
    var value = document.createElement('span');
    value.className = 'acp-permission-consent-value';
    value.textContent = r[2];
    line.appendChild(label);
    line.appendChild(value);
    block.appendChild(line);
  });
  return block;
}

/** Find the transcript row `addPermissionRequest` built for `requestId`,
 *  or null. A plain child scan rather than a CSS attribute-selector
 *  lookup, so an unusual requestId value never needs escaping into a
 *  selector string. `dataset.requestId` is always a string; `requestId`
 *  here may arrive as either a string or a number (JSON-RPC ids are
 *  typically small integers), so both sides are stringified before
 *  comparing. */
function findPermissionRequestRow(requestId) {
  var want = String(requestId);
  var rows = transcriptEl.querySelectorAll('.acp-msg-permission');
  for (var i = 0; i < rows.length; i++) {
    if (rows[i].dataset.requestId === want) return rows[i];
  }
  return null;
}

/** Mark a permission-request row as no longer actionable (SC-9 review
 *  fix): a `permission_resolved` frame means the pending state behind it
 *  is gone, whether because it was answered or because the turn ended
 *  while it was still pending. Disables every option button and dims the
 *  row so it reads as settled, not fresh-and-clickable. Idempotent: a
 *  row already marked resolved (e.g. the click handler already disabled
 *  its own buttons before the server's echo arrives) is left alone. If no
 *  matching row exists (this frame replayed without its own
 *  `permission_request` still present, or the request was never rendered
 *  in this tab), this is a silent no-op -- there is nothing to update. */
function markPermissionResolved(requestId) {
  var row = findPermissionRequestRow(requestId);
  if (!row || row.classList.contains('acp-permission-resolved')) return;
  row.classList.add('acp-permission-resolved');
  var buttons = row.querySelectorAll('button');
  for (var i = 0; i < buttons.length; i++) buttons[i].disabled = true;
}

// ---- the "thinking…" placeholder ---------------------------------------

// The currently-shown "thinking…" row ({row, body}), or null. Torn down the
// moment something real arrives (a chunk, a tool call, turn end).
var thinkingRow = null;

/** Show the "thinking…" row, or do nothing if it is already showing.
 *
 *  Built like `addMessage` rather than through it: this row is not a
 *  message, it is process chrome that gets torn back out the moment
 *  something real arrives, and `addMessage` has no way to hand back the
 *  row `hideThinking` needs to remove — only the body inside it.
 *
 *  Called once, from `setTurn(true)`'s live arm (`meta turn:start`) — a
 *  turn is silent for certain in the instant it begins and there has been
 *  nothing to say yet. It does not reappear between a tool call and the
 *  agent's next words: nothing on the wire marks that gap as *renewed*
 *  silence rather than an answer still being composed, and guessing with a
 *  timer would show "thinking" over a bubble already mid-stream as often as
 *  over a genuinely quiet turn. */
function showThinking() {
  if (thinkingRow) return;
  var stick = stuckToBottom();
  var row = document.createElement('div');
  row.className = 'acp-msg acp-msg-thinking';
  var who = document.createElement('span');
  who.className = 'acp-msg-role';
  var body = document.createElement('div');
  body.className = 'acp-msg-body';
  body.textContent = 'thinking…';
  row.appendChild(who);
  row.appendChild(body);
  transcriptEl.appendChild(row);
  if (stick) transcriptEl.scrollTop = transcriptEl.scrollHeight;
  thinkingRow = { row: row, body: body };
}

/** Remove the "thinking…" row, or do nothing if none is showing. Called on
 *  every frame that means the turn stopped being silent — a message chunk,
 *  a tool call, the turn ending — so the row never survives past the thing
 *  it was standing in for. */
function hideThinking() {
  if (!thinkingRow) return;
  thinkingRow.row.remove();
  thinkingRow = null;
}

/** Render `agent_thought_chunk` content in place of the placeholder text,
 *  if the agent ever sends one. Unobserved in 1,200 measured runs across
 *  every Claude and Qwen model and thinking configuration tried
 *  (plans/ROADMAP.md) — this exists so a build that does send it is used
 *  rather than silently dropped, not because it is known to fire. Never
 *  called while replaying: a `thought` frame surviving in the ring buffer
 *  from a turn long over has nothing live left to indicate. */
function appendThought(text) {
  if (!text) return;
  showThinking();
  if (thinkingRow.body.textContent === 'thinking…') thinkingRow.body.textContent = '';
  thinkingRow.body.textContent += text;
}

// ---- resetting the panel for a new session -------------------------------

/** Wipe every rendered row and every piece of shared state tracked in this
 *  file, so the next session's frames start from a blank panel rather than
 *  drawing on top of (or being confused by) the previous one's leftovers.
 *  Called before loading a new session's history, and before re-subscribing
 *  to the same session id after a reconnect.
 *
 *  removeAllCrewPanels/closeSubagentView are acp.html-only: the crew/
 *  sub-agent fan-out panel is a live-WebSocket feature with no static/
 *  file-replayed equivalent (translate_transcript() emits no frame type for
 *  it — see transcript_translator.py), so a page without them (the
 *  dashboard's transcript panel) has nothing to tear down. Guarded and
 *  exposed the same way as send/logLine above: both are declared inside
 *  acp.html's IIFE, so a bare `typeof X === 'function'` here would not find
 *  them without that page explicitly assigning `window.X = X`. */
function clearTranscript() {
  // Emptying by textContent rather than innerHTML: same effect, and it keeps
  // the no-innerHTML rule true of every line on this page without exception.
  transcriptEl.textContent = '';
  // promptNavEl is now outside the transcript (sibling in .acp-transcript-wrap),
  // so textContent = '' above does not remove it. Just hide it and clear the
  // userMsgEls tracking array.
  userMsgEls = [];
  promptNavEl.hidden = true;
  _updateNavArrows();
  agentBody = null;
  toolRows = Object.create(null);
  // Clear any live elapsed-time timers — the rows they were ticking against
  // were just removed and the intervals would otherwise run forever.
  Object.keys(_toolTimers).forEach(function(k) {
    clearInterval(_toolTimers[k]);
  });
  _toolTimers = Object.create(null);
  // Held bodies belong to rows that no longer exist. Kept in step with
  // `toolRows`, or a body stashed under an id the next session happened to
  // reuse would attach itself to a stranger's tool call.
  pendingToolOutput = Object.create(null);
  toolGroup = null; // also reset at meta turn:start and at end of flushToolGroups()
  // The row emptying just detached, if one was showing — nothing left to
  // remove a second time, and `showThinking` would otherwise believe one
  // still stands.
  thinkingRow = null;
  // A new session (or a reload of this one) has no crew of its own yet —
  // whatever the previous session's fan-out looked like does not carry over,
  // and a sub-agent panel left open would be showing a conversation that no
  // longer has anything to do with what is on screen behind it.
  if (typeof removeAllCrewPanels === 'function') removeAllCrewPanels();
  if (typeof closeSubagentView === 'function') closeSubagentView();
  // Reset prompt navigation state: the nav arrows track DOM rows that were
  // just cleared by textContent = '' above, so stale refs are cleared here.
}

// ---- feeding frames from outside a live socket ---------------------------
//
// renderTranscriptFrame(frame) covers exactly the frame shapes that have no
// `replaying`-conditional behaviour in acp.html's live `handle()` dispatch
// (chunk, rendered, tool_call, tool_update) -- each always renders the same
// way whether the frame just arrived over the socket or is being replayed
// from the session's history buffer. That happens to be exactly the frame
// shape set transcript_translator.py's translate_transcript() produces for
// a file-derived, non-live transcript (see that file), which is what makes
// this function usable as the dashboard's whole rendering entry point: feed
// it GET /api/session-transcript's `events` and nothing further is needed.
//
// Left out on purpose, and still handled only inline in acp.html's
// `handle()`: thought (a live-only "still thinking" signal, explicitly
// suppressed during replay), tool_output (broadcast-only, reaches live
// viewers and nobody else, so meaningless off a live connection),
// permission_request/permission_resolved (answerable only over a live
// socket -- a later phase's concern once the dashboard gains live-attach),
// agent_error and history_truncated (both tied to the live ring buffer).
// None of these are ever produced by translate_transcript(), so the
// dashboard's static panel has no need to feed them through here, and
// acp.html's own dispatch for them is left untouched rather than routed
// through a shared function that would gain a `replaying` parameter for no
// caller that needs it yet.
//
// steer_sent (Step 9 review, Fix 2) belongs in this list by the same test
// the header above applies to everything else: acp.html's own `steer_sent`
// case carries an explicit "No !replaying guard" comment -- it has no
// replaying-conditional behaviour, so it always renders the same dimmed
// steer band whether live or replayed. It was simply missing here, which
// meant a dashboard `history` replay (the reconnect ring-buffer replay,
// dashHandle()'s `history` case -- not the static /api/session-transcript
// fetch, whose translate_transcript() never emits this frame shape) silently
// dropped any steer_sent event it carried: no band rendered at all. Fixed by
// adding the case below, via the same addMessage() (textContent-only, see
// its own header) both host pages already use for a live steer_sent.
// Deliberately does NOT touch any page-lifecycle state (no composer/textarea
// mutation) -- this module stays page-lifecycle-state-agnostic; the
// duplicate-send detection this same fix requires lives in index.html's own
// `history`-case code instead (dashHandle(), which already has payload.events
// in hand), not here.
function renderTranscriptFrame(frame) {
  var type = frame && frame.type;
  var payload = (frame && frame.payload) || {};
  if (type === 'chunk') {
    hideThinking();
    appendChunk(payload.role === 'user' ? 'user' : 'agent', payload.text || '');
    return;
  }
  if (type === 'steer_sent') {
    var steerText = payload && typeof payload.text === 'string' ? payload.text : '';
    if (steerText) addMessage('steer', steerText);
    return;
  }
  if (type === 'rendered') {
    // The bubble that just closed, reflowed from plain text into markup. It
    // arrives once per bubble at the end of it, never per chunk: 156 of 184
    // chunk boundaries in a measured turn fell *inside* an open code fence,
    // so parsing as it streamed would have been parsing an unterminated
    // document 85% of the time. Text streams exactly as it did before and
    // reflows once when there is a whole document to parse.
    renderMarkdown(payload.tokens);
    return;
  }
  if (type === 'tool_call' || type === 'tool_update') {
    hideThinking();
    addToolCall(payload);
    // logLine is page-specific (acp.html's own debug-log panel) and exposed
    // as a window global there for exactly this guard, same as elsewhere in
    // this file -- a page with no debug-log panel (the dashboard) just
    // skips the line.
    if (typeof logLine === 'function') {
      logLine('in', '← ' + type + ' ' + (payload.title || payload.kind || '') +
                    ' [' + (payload.status || '') + ']');
    }
    return;
  }
}

/** Replay a full ordered list of frames (e.g. the `events` array from
 *  GET /api/session-transcript) into a freshly-cleared panel. Mirrors the
 *  trailing steps of acp.html's own `history` frame handling: flush any
 *  tool group the last frame left open (a file-derived transcript has no
 *  closing `meta turn:end` to flush it, since the translator never
 *  produces meta frames at all), then land the scroll position at the
 *  bottom. This is the dashboard panel's whole entry point: initTranscriptDom(),
 *  fetch the events, call this once. */
function renderTranscriptHistory(frames) {
  clearTranscript();
  var events = frames || [];
  for (var i = 0; i < events.length; i++) renderTranscriptFrame(events[i]);
  if (toolGroup) flushToolGroups();
  transcriptEl.scrollTop = transcriptEl.scrollHeight;
}
