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
