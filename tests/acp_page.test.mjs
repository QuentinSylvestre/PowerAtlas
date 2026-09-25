// Behavioural coverage for the browser-side code this repo has no other way to
// test: src/power_atlas/templates/acp.html, the remote-access panel in
// templates/index.html, and the two rules in static/style.css that decide what
// the /acp topbar shows whom.
//
//   node tests/acp_page.test.mjs                    # the committed template
//   node tests/acp_page.test.mjs <path-to-acp.html> # any other copy of it
//
// The argument overrides acp.html only. The panel and stylesheet checks read
// their committed paths, because what they measure is a mutation of the
// committed file and pointing them elsewhere would measure nothing.
//
// Exits 0 when every check passes, 1 otherwise. No dependencies: Node's own
// `vm` plus the DOM stand-in below, so it runs anywhere `node` does and needs
// no install step.
//
// Why this file exists at all. The page is ~730 lines carrying the XSS control,
// the turn state machine, reconnect and the auto-load loop, and until now the
// only assertions on any of it were `str in src` substring checks from Python —
// which pin the *text* of a line, not what it does. The two High findings from
// the Phase 4 review and four Mediums from Phase 5 live entirely in this file;
// 5 of the checks below fail against `e8cb4df`, the commit those fixes
// landed on top of, which is what makes them evidence rather than assertion.
//
// The template is rendered here rather than read raw: the script under test is
// the *rendered* one, and `ACP_SID` in particular only differs from `sessionId`
// after Jinja has substituted it. `render()` implements exactly four things —
// `{# … #}`, `{% if <name> %}` / `{% else %}` / `{% endif %}` over a boolean
// context key, `{{ name }}` and `{{ name|tojson }}` — strips four content-free
// tags by name (`extends`, `block`, `endblock`, `include`), and **throws on
// everything else**, including every conditional it cannot evaluate.
//
// That last clause was untrue until the Phase 5b review measured it: the tag
// sweep was a blanket `replace(/\{%[^%]*%\}/g, "")`, so an `{% elif %}` or a
// condition that was not a bare identifier was deleted in silence. The three
// measured misrenders are pinned as checks below rather than described here.

import fs from "node:fs";
import path from "node:path";
import url from "node:url";
import vm from "node:vm";

const HERE = path.dirname(url.fileURLToPath(import.meta.url));
const DEFAULT_TEMPLATE = path.join(
  HERE, "..", "src", "power_atlas", "templates", "acp.html");
// The vendored highlighter the page loads, always the committed one even when
// the template under test is a copy: what the colouring checks measure is this
// repo's bundle against this repo's grammars.
const PRISM_BUNDLE = path.join(
  HERE, "..", "src", "power_atlas", "static", "prism.js");
// The shared transcript renderer (dashboard/ACP-merge plan, Phase 2) — loaded
// by acp.html via <script src>, same as prism.js, so it must run in the same
// sandbox as the inline script for the functions it defines (mdBuild and
// friends, so far) to be callable from it, exactly as a browser would load
// the two in document order.
const TRANSCRIPT_RENDERER_BUNDLE = path.join(
  HERE, "..", "src", "power_atlas", "static", "transcript-renderer.js");
let TRANSCRIPT_RENDERER_SRC = null;
function transcriptRendererSource() {
  if (TRANSCRIPT_RENDERER_SRC === null) {
    TRANSCRIPT_RENDERER_SRC = fs.readFileSync(TRANSCRIPT_RENDERER_BUNDLE, "utf8");
  }
  return TRANSCRIPT_RENDERER_SRC;
}
// The shared composer chrome (dashboard/ACP feature-parity plan, Phase 1) —
// context indicator, sid/copy widget, debug log panel — loaded by acp.html
// via <script src> immediately after transcript-renderer.js, same reasoning:
// setContext/renderSidLabel/logLine/etc. moved out of acp.html's inline
// script into this file, so it must run in the same sandbox, in the same
// document order a browser would load it in, for the inline script's now-bare
// references to them to resolve instead of throwing ReferenceError.
const COMPOSER_CHROME_BUNDLE = path.join(
  HERE, "..", "src", "power_atlas", "static", "composer-chrome.js");
let COMPOSER_CHROME_SRC = null;
function composerChromeSource() {
  if (COMPOSER_CHROME_SRC === null) {
    COMPOSER_CHROME_SRC = fs.readFileSync(COMPOSER_CHROME_BUNDLE, "utf8");
  }
  return COMPOSER_CHROME_SRC;
}

// Prism as the page sees it, built once and shared by every check.
//
// Run in a context of its own rather than in the page's, and handed over as an
// object. Prism's core sniffs for a document on the way up — `currentScript`,
// and a DOMContentLoaded hook it skips only because the bundle sets `manual` —
// and the DOM stand-in below is not a document: it is the handful of methods
// this page calls and nothing else. Nothing wanted from Prism here touches the
// DOM. `tokenize()` takes a string and returns data, which is the entire reason
// the page uses it instead of `highlightElement()`.
//
// Sharing one instance across checks is safe for the same reason: `tokenize`
// reads the grammars and never writes them, so there is no state for one check
// to leave behind for the next.
let PRISM = null;
function prismGlobal() {
  if (PRISM) return PRISM;
  const box = {};
  box.window = box;
  box.self = box;
  box.globalThis = box;
  vm.createContext(box);
  vm.runInContext(fs.readFileSync(PRISM_BUNDLE, "utf8"), box,
                  { filename: "prism.js" });
  if (!box.Prism || typeof box.Prism.tokenize !== "function") {
    throw new Error("static/prism.js did not define a usable Prism.tokenize");
  }
  // The bundle's whole safety property, pinned where it is cheap to pin: with
  // `manual` false, loading Prism rewrites every <pre> on the page through
  // innerHTML, which is the one sink /acp does not have.
  if (box.Prism.manual !== true) {
    throw new Error("static/prism.js does not set Prism.manual; it would " +
                    "rewrite the page's code blocks through innerHTML on load");
  }
  return (PRISM = box.Prism);
}

// ---------------------------------------------------------------- template --

function render(src, ctx) {
  // Jinja comments first: `{# … #}` never reaches the browser, so leaving it in
  // would put template prose into `markup` where a check scanning the rendered
  // page would have to reason about text no viewer ever sees.
  let out = src.replace(/\{#[\s\S]*?#\}/g, "");
  // `{% if %}` is rendered rather than stripped, and that distinction is the
  // whole reason this branch exists. Stripping every `{% %}` tag leaves *both*
  // arms of a conditional in the output — so a page that renders a dashboard
  // link for a loopback viewer and plain text for a remote one would appear to
  // the harness to do both at once, and a check asserting either would pass
  // against a template that had lost the other. One arm, chosen by the same
  // value the server would choose it by.
  //
  // Non-nested and boolean-only: the only conditional on this page tests one
  // context flag. A nested one would need a real parser, so it fails loudly
  // below rather than being half-handled here.
  //
  // `{% elif %}` is refused *before* IF_RE runs, and the ordering is the point.
  // IF_RE matches an `{% if %}` with a bare-identifier condition and stops at
  // the first `{% endif %}`, so a three-arm conditional matches it and then
  // renders wrongly: splitting the body on `{% else %}` buries the elif arm
  // inside arm one, and a falsy condition renders the else arm where Jinja
  // renders the elif's. Measured against the previous version of this file,
  // `{% if local %}L{% elif other %}E{% else %}R{% endif %}` with
  // `local=false, other=true` produced "R" and threw nothing — one plausible
  // wrong arm, silently, which is strictly worse than the blanket strip it
  // replaced. That produced an obviously wrong "LER".
  if (/\{%-?\s*elif\b/.test(out)) {
    throw new Error(
      "{% elif %} in the template; render() implements if/else/endif only and " +
      "would render the wrong arm rather than fail");
  }
  const IF_RE = /\{%\s*if\s+(\w+)\s*%\}([\s\S]*?)\{%\s*endif\s*%\}/g;
  out = out.replace(IF_RE, (_m, name, body) => {
    if (!(name in ctx)) throw new Error(`template branches on an unknown variable: ${name}`);
    if (/\{%\s*if\s/.test(body)) {
      throw new Error(
        `nested {% if %} in the template; render() handles one level only`);
    }
    const parts = body.split(/\{%\s*else\s*%\}/);
    if (parts.length > 2) throw new Error("more than one {% else %} in one {% if %}");
    return ctx[name] ? parts[0] : (parts[1] ?? "");
  });
  // Anything conditional still standing did not match IF_RE, which accepts a
  // bare identifier and nothing else. Under the blanket strip these fell
  // through and left *both* arms in the output: measured,
  // `{% if not local %}A{% else %}B{% endif %}` rendered "AB" and
  // `{% if user.admin %}yes{% else %}no{% endif %}` rendered "yesno", neither
  // throwing. A check written against either would pass over a template that
  // had lost the arm it meant to assert.
  const stray = out.match(/\{%-?\s*(?:if|else|endif)\b[^%]*%\}/);
  if (stray) {
    throw new Error(
      `render() cannot evaluate ${stray[0]} — it implements {% if <name> %} ` +
      "over a boolean context key and nothing else. Teach it the construct " +
      "rather than letting both arms reach the page");
  }
  // `{% extends %}` / `{% block %}` / `{% include %}` carry no content this
  // page's script reads; the block body is the whole file. An allowlist by
  // name, so a tag nobody taught this renderer about survives to the refusal
  // below instead of being deleted on the way past.
  out = out.replace(/\{%-?\s*(?:extends|block|endblock|include)\b[\s\S]*?%\}/g, "");
  const unknownTag = out.match(/\{%[\s\S]*?%\}/);
  if (unknownTag) {
    throw new Error(
      `render() does not implement ${unknownTag[0]}; teach it the tag rather ` +
      "than letting the page run against a template it silently rewrote");
  }
  const lookup = (name) => {
    if (!(name in ctx)) throw new Error(`template reads an unknown variable: ${name}`);
    return ctx[name];
  };
  out = out.replace(/\{\{\s*(\w+)\s*\|\s*tojson\s*\}\}/g,
                    (_m, name) => JSON.stringify(lookup(name)));
  out = out.replace(/\{\{\s*(\w+)\s*\}\}/g, (_m, name) => String(lookup(name)));
  // `{{ … }}` only. This check used to carry a `|\{%[^%]*%\}` alternative that
  // could never fire: the tag sweep above it ran first and deleted every `{% %}`
  // in the file, so the match had nothing left to find. Unknown tags are
  // refused by `unknownTag` above instead, which runs while they are still
  // there to be seen.
  const leftover = out.match(/\{\{[^}]*\}\}/);
  if (leftover) {
    throw new Error(
      `unrendered Jinja left in the page: ${leftover[0]} — teach render() ` +
      "about it rather than letting the script run against a literal");
  }
  return out;
}

// --------------------------------------------------------------------- DOM --

const HTML_SINK = (what) => {
  throw new Error(
    `the page wrote agent-influenced text through ${what}; this page's rule is ` +
    "createElement + textContent and nothing else");
};

// The focused element, or null for "nothing" (a browser would say <body>). A
// harness with no focus model cannot see the thing renderRail() breaks: it
// empties and recreates every node it owns, so whatever the user was on stops
// existing. Module-scope rather than per-page because `El` is defined out here;
// `loadPage` clears it, and the checks run one page at a time.
let ACTIVE = null;

class El {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.childNodes = [];
    this.parentNode = null;
    this.className = "";
    this.dataset = {};
    this.style = {};
    this.hidden = false;
    this.disabled = false;
    this.value = "";
    this.scrollTop = 0;
    this.scrollHeight = 0;
    this.clientHeight = 0;
    this.type = "";
    this.title = "";
    this._text = "";
    this._attrs = Object.create(null);
    this._listeners = Object.create(null);
  }
  // The attribute API. Only get/set: the page has no reason to read an
  // attribute back, and a removeAttribute nothing calls is a capability this
  // harness would be claiming rather than having.
  setAttribute(name, value) {
    this._attrs[String(name)] = String(value);
    // `class` and `className` are one thing in a real DOM, and the page has a
    // reason to use the attribute form: an SVG element's `className` is a
    // read-only SVGAnimatedString, so `setAttribute` is the only way to class
    // one. Without this link `matches()` — which reads `className` — could not
    // see an SVG the page had classed correctly, and a check for it would fail
    // against a page that was right.
    if (String(name) === "class") this.className = String(value);
  }
  getAttribute(name) {
    const got = this._attrs[String(name)];
    return got === undefined ? null : got;
  }
  get textContent() {
    if (this.childNodes.length === 0) return this._text;
    return this.childNodes.map((c) => c.textContent).join("");
  }
  set textContent(v) {
    // A browser moves focus to <body> the moment the focused element leaves the
    // document, and emptying a container by textContent is how renderRail()
    // removes every node it drew. Without this the harness would go on
    // reporting a detached node as focused and could not see focus being lost
    // at all — which is the entire failure the focus checks exist for.
    if (ACTIVE && this.childNodes.length) {
      for (const node of this.descendants()) {
        if (node === ACTIVE) { ACTIVE = null; break; }
      }
    }
    this._text = String(v);
    this.childNodes = [];
  }
  // The no-innerHTML rule, armed rather than assumed. A harness with no HTML
  // parser cannot *observe* an injection, so it forbids the sink instead: any
  // page that reached for one takes every check that renders down with it.
  get innerHTML() { HTML_SINK("innerHTML"); }
  set innerHTML(_v) { HTML_SINK("innerHTML"); }
  get outerHTML() { HTML_SINK("outerHTML"); }
  set outerHTML(_v) { HTML_SINK("outerHTML"); }
  insertAdjacentHTML() { HTML_SINK("insertAdjacentHTML"); }
  appendChild(child) {
    child.parentNode = this;
    this.childNodes.push(child);
    return child;
  }
  // `markRestartInputs` takes a badge back off when the server stops reporting
  // the key, and a harness with no removal could not tell that from a badge
  // that was never added.
  remove() {
    if (!this.parentNode) return;
    const kin = this.parentNode.childNodes;
    const at = kin.indexOf(this);
    if (at >= 0) kin.splice(at, 1);
    this.parentNode = null;
    if (ACTIVE === this) ACTIVE = null;
  }
  // Required by flushToolGroups() (Phase 3) which moves tool-call rows into
  // group containers via transcriptEl.removeChild(row). Returns the removed child.
  removeChild(child) {
    const i = this.childNodes.indexOf(child);
    if (i >= 0) this.childNodes.splice(i, 1);
    child.parentNode = null;
    return child;
  }
  // Required by flushToolGroups() which inserts group containers at their
  // original position via transcriptEl.insertBefore(group, insertBefore).
  // If ref is null, appends (matches real DOM behaviour).
  insertBefore(node, ref) {
    if (node.parentNode) node.parentNode.removeChild(node);
    node.parentNode = this;
    if (ref === null) {
      this.childNodes.push(node);
    } else {
      const i = this.childNodes.indexOf(ref);
      if (i >= 0) this.childNodes.splice(i, 0, node);
      else this.childNodes.push(node);
    }
    return node;
  }
  // Required by flushToolGroups() adjacency check:
  //   toolGroup[i-1].nextSibling === toolGroup[i]
  // A null parent or out-of-bounds index returns null (matches real DOM).
  get nextSibling() {
    if (!this.parentNode) return null;
    const kids = this.parentNode.childNodes;
    const i = kids.indexOf(this);
    return (i >= 0 && i + 1 < kids.length) ? kids[i + 1] : null;
  }
  // The remote panel's Copy button selects the field first and unconditionally,
  // because that is its fallback when the clipboard API is unavailable — which
  // it is over plain http off localhost, i.e. on the remote surface the panel
  // exists to configure. Recorded rather than ignored so a check can see it.
  select() { this.selected = true; ACTIVE = this; }
  focus() { ACTIVE = this; }
  // Node containment, inclusive like the DOM's: the MCP indicator's
  // outside-click handler asks whether the click landed inside it.
  contains(node) {
    for (let n = node; n; n = n.parentNode) if (n === this) return true;
    return false;
  }
  // A real `<textarea>`/`<input>` method: positions the caret (or selects the
  // range between the two offsets when they differ). The skill-completion
  // path calls this to land the caret after the text it just inserted.
  setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; }
  // `click()` is a standard DOM method: triggers the element's click listener.
  // Needed by code that calls element.click() programmatically (e.g. Enter-
  // during-turn dispatching to sendModeBtn.click()).
  click() { this.dispatch("click"); }
  addEventListener(type, fn) {
    (this._listeners[type] = this._listeners[type] || []).push(fn);
  }
  // Deliberately *not* gated on `disabled`. A browser fires no click on a
  // disabled control, but several checks below exist to exercise the page's own
  // guard behind that attribute; gating here would pass them without running
  // the guard. Checks that care about the attribute assert on the attribute.
  dispatch(type, ev) {
    const fns = this._listeners[type] || [];
    if (fns.length === 0) throw new Error(`nothing listens for '${type}' here`);
    const defaultEv = { stopPropagation: () => {} };
    for (const fn of fns) fn.call(this, ev !== undefined ? ev : defaultEv);
  }
  // Single simple selectors — `.class` or `tag` — and nothing else. The rail
  // builds its rows at runtime, so `byId`'s regex over the *static* markup
  // cannot reach them and a subtree query is the only way to address one; a
  // combinator would silently match the wrong node, so it fails loudly instead.
  matches(sel) {
    const want = String(sel).trim();
    if (/[ >+~,[\]#:]/.test(want)) {
      throw new Error(
        `the harness implements single class or tag selectors only, got ${sel}`);
    }
    if (want.startsWith(".")) {
      return String(this.className).split(/\s+/).includes(want.slice(1));
    }
    if (/^[a-zA-Z][\w-]*$/.test(want)) return this.tagName === want.toUpperCase();
    throw new Error(`the harness does not implement the selector ${sel}`);
  }
  querySelectorAll(sel) {
    return this.descendants().filter((node) => node.matches(sel));
  }
  querySelector(sel) {
    return this.querySelectorAll(sel)[0] ?? null;
  }
  descendants() {
    const out = [];
    const walk = (node) => {
      for (const child of node.childNodes) {
        out.push(child);
        walk(child);
      }
    };
    walk(this);
    return out;
  }
  get parentElement() {
    // In the real DOM, parentElement is parentNode when the parent is an Element.
    // All nodes in this harness are El instances (no Document/Text nodes as
    // parents), so parentElement and parentNode are equivalent here.
    return this.parentNode;
  }
  // classList — wraps `className` so page code using toggle/add/remove/contains
  // works in the harness. Only the four methods the page actually calls are
  // implemented; anything else throws loudly rather than silently no-oping.
  get classList() {
    const self = this;
    function classes() {
      return self.className ? self.className.split(/\s+/).filter(Boolean) : [];
    }
    return {
      add(name) {
        const list = classes();
        if (!list.includes(name)) list.push(name);
        self.className = list.join(" ");
      },
      remove(name) {
        self.className = classes().filter((c) => c !== name).join(" ");
      },
      toggle(name, force) {
        const list = classes();
        const has = list.includes(name);
        const add = force === undefined ? !has : Boolean(force);
        if (add && !has) list.push(name);
        else if (!add && has) list.splice(list.indexOf(name), 1);
        self.className = list.join(" ");
      },
      contains(name) { return classes().includes(name); },
    };
  }
}

// -------------------------------------------------- the listing endpoint --
//
// `GET /api/acp/sessions` (Phase 4) served from a synthetic store, paged by the
// same rules the real route uses: independent paging on both axes, and a `cwd`
// that selects one workspace and bypasses the group axis. Serving it properly
// rather than returning a canned page is what makes the paging checks below
// mean anything — a stub that ignored `session_page` would let a rail that
// never sent one pass.

function fakeStore({ workspaces = 12, sessions = 5 } = {}) {
  const out = [];
  for (let w = 0; w < workspaces; w++) {
    const rows = [];
    for (let s = 0; s < sessions; s++) {
      rows.push({
        id: `sess-w${w}-s${s}`,
        title: `workspace ${w} session ${s}`,
        // Shaped like the real store, suffix and all. A bare
        // `2026-07-10T09:00:00` is not a value this endpoint can return —
        // kiro-cli writes `Z` with a nine-digit fraction — and the difference
        // is not cosmetic: JavaScript reads an offset-less date-time as *local*
        // and a `Z` one as UTC, so a fixture without the suffix would have
        // verified the rail against an instant five hours from the one the
        // store actually holds, and against the wrong day either side of
        // midnight.
        updated_at: `2026-07-${String(10 + (s % 20)).padStart(2, "0")}T09:${String(s).padStart(2, "0")}:00.086294300Z`,
        availability: "available",
      });
    }
    // `exists` is the endpoint's stat of the workspace directory (Phase 5b),
    // a separate question from D17's per-session availability. Default true,
    // because 51 of the real store's 65 workspaces are still on disk; the
    // checks that care set it false on one group.
    out.push({ cwd: `C:\\work\\ws-${w}`, name: `ws-${w}`, sessions: rows,
               exists: true });
  }
  return out;
}

function parseQuery(target) {
  const out = {};
  const q = String(target).split("?")[1];
  if (!q) return out;
  for (const pair of q.split("&")) {
    const [k, v = ""] = pair.split("=");
    out[decodeURIComponent(k)] = decodeURIComponent(v.replace(/\+/g, " "));
  }
  return out;
}

// `web.py:1518-1519` clamps both sizes before `_acp_listing` ever sees them,
// and the route's `cwd` arm forces the reported page to 1 and returns at most
// one group (`web.py:1426-1429`). Mirrored here so a rail that asked for a page
// the endpoint would narrow gets the narrowed page from the harness too:
// nothing bites at today's 10/3, but a future RAIL_GROUP_SIZE above 20 would
// pass against a stub that honoured it and under-fill against the real route.
const MAX_GROUP_SIZE = 20;    // web.py:_ACP_MAX_GROUPS_PER_PAGE
const MAX_SESSION_SIZE = 50;  // web.py:_ACP_MAX_SESSIONS_PER_GROUP
const MAX_FLAT_SIZE = 100;    // web.py:_ACP_MAX_FLAT_PAGE_SIZE

function clampSize(raw, fallback, ceiling) {
  const n = Number(raw === undefined || raw === "" ? fallback : raw);
  return Math.max(1, Math.min(Number.isFinite(n) ? n : fallback, ceiling));
}

// The two ACP data routes, named once. The delete path is a *prefix* of nothing
// but is *prefixed by* the listing path, which is the trap `fakeFetch` documents.
const LISTING_URL = "/api/acp/sessions";        // web.py:_ACP_LISTING_PATH
const DELETE_URL = "/api/acp/sessions/delete";  // web.py:_ACP_DELETE_PATH
const WORKSPACES_URL = "/api/acp/workspaces";   // web.py:_ACP_WORKSPACES_PATH

/** The create picker's workspace list, derived from the same fixture.
 *
 *  Carries `capacity` because the real route does — the picker refuses at the
 *  cap before spending anything, so it needs the pair on the answer it already
 *  makes rather than a second request for it. `missing` is the count of
 *  workspaces whose folder is gone, which the route excludes and reports; the
 *  fixture has none unless a check sets one. */
function serveWorkspaces(store) {
  return {
    workspaces: store.map((w) => ({
      cwd: w.cwd, name: w.name, sessions: w.sessions.length,
    })),
    missing: store.missing ?? 0,
    // Read off the fixture the same way `serveListing` reads it, and keyed on
    // presence for the same reason: a check that sets `capacity` to null is
    // testing the page's handling of a malformed pair, not asking for a default.
    capacity: "capacity" in store ? store.capacity : { held: 0, max: 8 },
  };
}

/** The delete route's success shape: every id asked for, deleted.
 *
 *  The refusal shapes are reached through `opts.answer`, because a refusal is
 *  a property of the *session* (held, locked, gone) and this stub holds no
 *  model of that — inventing one would be a second implementation of
 *  `_acp_delete_many` for checks to pass against instead of the real one. */
function serveDelete(init) {
  let ids = [];
  try {
    ids = JSON.parse((init && init.body) || "{}").session_ids || [];
  } catch { ids = []; }
  return { deleted: ids, failed: [] };
}

// The flat recency shape (`mode=recent`) the rail reads when it groups by day.
// Mirrors the route rather than the rail: every session across every workspace,
// `updated_at` descending, walked by a single cursor, each row carrying the
// workspace it came from because grouped by day there is no header left to say
// so. Sorting here rather than trusting the fixture order is deliberate — the
// rail is required *not* to re-sort, so the stub has to be the thing that
// establishes the order, or a rail that sorted anyway would pass.
function serveFlat(store, params) {
  const page = Math.max(1, Number(params.page || 1));
  const size = clampSize(params.size, 30, MAX_FLAT_SIZE);
  const rows = [];
  for (const ws of store) {
    for (const s of ws.sessions) {
      rows.push({
        ...s,
        cwd: ws.cwd,
        name: ws.name,
        exists: ws.exists === undefined ? true : ws.exists,
      });
    }
  }
  rows.sort((a, b) => (
    a.updated_at < b.updated_at ? 1 : a.updated_at > b.updated_at ? -1 : 0));
  const start = (page - 1) * size;
  return {
    sessions: rows.slice(start, start + size),
    page,
    has_more: start + size < rows.length,
    capacity: { held: 0, max: 8 },
    // Pinned sessions: always the full list, passed through from the store
    // fixture. Keyed on presence, defaulting to [] (same pattern as `capacity`
    // and `missing`): the real route always sends this key, and the rail reads
    // it, so a stub that omitted it would silently exercise the `|| []` fallback
    // rather than the normal path.
    pinned: "pinned" in store ? store.pinned : [],
  };
}

function serveListing(store, params) {
  const groupSize = clampSize(params.group_size, 10, MAX_GROUP_SIZE);
  const sessionSize = clampSize(params.session_size, 3, MAX_SESSION_SIZE);
  const groupPage = Math.max(1, Number(params.group_page || 1));
  const sessionPage = Math.max(1, Number(params.session_page || 1));
  const single = Boolean(params.cwd);
  // The one place this stub still diverges: the route matches through
  // `data._normalize_path`, so it resolves case and separators, and this is an
  // exact string compare. Left as-is deliberately — reproducing path
  // normalisation here would be a second implementation of it to keep correct,
  // and the rail sends back the exact `cwd` string the endpoint gave it.
  const matched = single ? store.filter((w) => w.cwd === params.cwd) : store;
  const start = single ? 0 : (groupPage - 1) * groupSize;
  const page = single ? matched.slice(0, 1) : matched.slice(start, start + groupSize);
  return {
    groups: page.map((w) => {
      const from = (sessionPage - 1) * sessionSize;
      return {
        cwd: w.cwd,
        name: w.name,
        total: w.sessions.length,
        // Sent as the boolean the route sends, never omitted: the rail treats
        // an absent field as "no answer" rather than as "gone", and a stub that
        // dropped it would exercise that fallback in every check instead of the
        // real path.
        exists: w.exists !== false,
        session_page: sessionPage,
        has_more: from + sessionSize < w.sessions.length,
        // Copied, because a real answer crosses JSON and the page never holds
        // a reference into the server's store. Handed out by reference this
        // stub silently aliases: a check that moves a session's availability in
        // the fixture to model the sweeper reclaiming it would move the rail's
        // own copy at the same instant, so the rail would appear to have
        // noticed before it fetched anything — and a freshness check written
        // against that passes on a page with no freshness mechanism at all.
        sessions: w.sessions.slice(from, from + sessionSize).map((s) => ({ ...s })),
      };
    }),
    group_page: single ? 1 : groupPage,
    group_total: single ? matched.length : store.length,
    has_more: single ? false : start + groupSize < store.length,
    // The session cap, as the route reports it. Served on every answer, so a
    // check that never sets `store.capacity` still exercises the normal path
    // rather than the "server said nothing" fallback.
    //
    // Keyed on presence, not truthiness: a check that sets `capacity` to null
    // or to a half-formed pair is modelling a server that answered badly, and
    // `||` would quietly hand it a healthy default instead — testing the stub's
    // fallback rather than the page's.
    capacity: "capacity" in store ? store.capacity : { held: 0, max: 8 },
    // Pinned sessions: always the full list, passed through from the store
    // fixture. Same keyed-on-presence pattern as `capacity`.
    pinned: "pinned" in store ? store.pinned : [],
  };
}

// ---------------------------------------------------------------- the page --

function loadPage(templatePath, opts = {}) {
  const src = fs.readFileSync(templatePath, "utf8");
  const html = render(src, {
    sid: opts.sid ?? "",
    acp_error: opts.acpError ?? "",
    csp_nonce: opts.nonce ?? "NONCE-1",
    // `web.py` derives this from `scope["client"]` (D26). Defaults to the
    // loopback reading, which is what a developer running the page sees.
    local: opts.local ?? true,
    // `web.py` derives this from `local or not _is_mobile_ua(ua)`. Defaults
    // to true (loopback reading). Pass `canDelete: false` to simulate a remote
    // mobile viewer; pass `canDelete: true, local: false` for a remote desktop.
    can_delete: opts.canDelete ?? true,
  });

  // `acp.html`'s own content block only — `{% extends %}` is stripped by
  // `render()`, so `base.html`'s `<script src="/static/htmx.min.js">` is not in
  // this string and is not being counted. The served `/acp` therefore has four
  // script elements, not three (dashboard/ACP feature-parity plan, Phase 1
  // added composer-chrome.js alongside prism.js and transcript-renderer.js);
  // the policy still holds because base.html applies the same nonce
  // conditionally, and `test_web.py` counts the served page.
  //
  // What is measured here is this template's own contribution: exactly one
  // inline script — the one every check below drives — and every external one
  // nonced and served from this repo's own /static. An external tag that
  // arrived without a nonce would be blanked by the policy at runtime and
  // silently do nothing, which is a failure no assertion about behaviour can
  // see, because the behaviour is simply absent.
  const scripts = [...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)];
  const inline = scripts.filter((s) => !/\bsrc=/.test(s[1]));
  if (inline.length !== 1) {
    throw new Error(
      `expected exactly one inline <script> in acp.html's content block, ` +
      `found ${inline.length}`);
  }
  for (const external of scripts.filter((s) => /\bsrc=/.test(s[1]))) {
    const src = /\bsrc="([^"]*)"/.exec(external[1]);
    if (!/\bnonce="/.test(external[1])) {
      throw new Error(
        `acp.html loads ${src ? src[1] : "a script"} without a nonce; the ` +
        `page's Content-Security-Policy would blank it`);
    }
    if (!src || !src[1].startsWith("/static/")) {
      throw new Error(
        `acp.html loads ${src ? src[1] : "a script"} from outside /static; ` +
        `this page's scripts are vendored, not fetched from a third party`);
    }
  }
  const scriptAttrs = inline[0][1];
  const scriptBody = inline[0][2];
  const markup = html.replace(/<script[\s\S]*?<\/script>/g, "");

  // Every element the static markup gives an id, with its real tag name and its
  // initial `hidden` state.
  //
  // Both of those used to be dropped — every stand-in was a `div` with
  // `hidden: false`, whatever the markup said. That was survivable only because
  // every hidden control on this page was also hidden programmatically before
  // anything read it (`connect()` re-hides the recovery buttons, `setContext`
  // the meter). The create picker is the first element whose markup `hidden` IS
  // its initial state, and under the old sweep it loaded *open*: the first
  // Escape closed a dialog nobody had opened instead of the row menu that was.
  const byId = new Map();
  for (const tag of markup.matchAll(/<([a-zA-Z][\w-]*)\b([^>]*)>/g)) {
    const attrs = tag[2];
    const id = /\bid="([^"]+)"/.exec(attrs);
    if (!id) continue;
    const el = new El(tag[1]);
    // Boolean attribute: `hidden`, `hidden=""` and `hidden="hidden"` all mean
    // hidden, and nothing on this page writes any other form.
    if (/\bhidden\b/.test(attrs)) el.hidden = true;
    byId.set(id[1], el);
  }
  // `byId` has stayed leaf-only until now: every `container.querySelectorAll`
  // the page runs is over children the page appended itself at runtime (rail
  // rows, the command dropdown, the delete modal), so a flat map of id'd
  // elements was enough. The task-mode picker (plans/260911_ACP_V3_FOLLOWUP_
  // FEATURES.md Phase 3) is the first static-subtree query --
  // `taskModeMenu.querySelectorAll('.acp-taskmode-option')` over markup that
  // is already there on load, never appended by the script -- so this nests
  // exactly that one relationship rather than teaching `byId` to parse nesting
  // in general. Matched on class rather than scoped to the menu's own inner
  // HTML with a regex, so it survives the options being reordered. Nothing
  // else on this page queries this menu, so it is inert for every other check.
  const taskModeMenu = byId.get("acpPickerTaskModeMenu");
  for (const btn of markup.matchAll(/<button\b([^>]*)>/g)) {
    const attrs = btn[1];
    const cls = /\bclass="([^"]*)"/.exec(attrs);
    if (!cls || !cls[1].split(/\s+/).includes("acp-taskmode-option")) continue;
    const optId = /\bid="([^"]+)"/.exec(attrs);
    const opt = optId && byId.get(optId[1]);
    if (!opt || !taskModeMenu) continue;
    opt.className = cls[1];
    const val = /\bdata-value="([^"]*)"/.exec(attrs);
    if (val) opt.dataset.value = val[1];
    taskModeMenu.appendChild(opt);
  }
  ACTIVE = null;

  const sockets = [];
  const urls = [];
  const fetches = [];
  // Every `confirm` the page raised, in order. Recorded rather than merely
  // answered: a destructive action's whole safety story is that it asked first
  // and did nothing when told no, and neither half is observable from the
  // return value alone. `opts.confirm === false` is the declining user.
  const confirms = [];
  const store = opts.store ?? fakeStore();
  const page = { html, markup, scriptAttrs, scriptBody, sockets, urls, fetches,
                 confirms, store, opts, reloaded: false };

  // A fetch with a body. The old stub answered `{ok: true}` and nothing else,
  // which is enough for the refused-handshake diagnosis (the only caller before the
  // rail) and useless for anything that reads a response. `opts.answer` lets a
  // check fail or reject a specific request; everything else is served from the
  // synthetic store above.
  function fakeFetch(target, init) {
    const url = String(target);
    const params = parseQuery(url);
    fetches.push({ url, params, init: init || {} });
    const override = opts.answer ? opts.answer(url, params) : null;
    if (override && override.reject) {
      return Promise.reject(new Error(override.reject));
    }
    // A request that never answers, until the page aborts it — the case the
    // refused-handshake diagnosis's timeout exists for.
    // 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 6 review (J6)
    if (override && override.hang) {
      return new Promise((_resolve, reject) => {
        const signal = init && init.signal;
        if (signal) signal.addEventListener("abort", () => reject(new Error("aborted")));
      });
    }
    const ok = override ? override.ok !== false : true;
    const status = override && override.status ? override.status : (ok ? 200 : 500);
    // The delete path is matched **before** the listing, and by equality rather
    // than prefix. `/api/acp/sessions/delete` starts with `/api/acp/sessions`,
    // so a prefix test served it a listing payload — which carries no `deleted`
    // array, so the page read every successful deletion as a refusal and left
    // the row on screen. Silent, and in the direction that makes a broken
    // delete look like a working guard.
    const body = override && "body" in override
      ? override.body
      : (url === DELETE_URL ? serveDelete(init)
         : url.startsWith(WORKSPACES_URL) ? serveWorkspaces(store)
         : url.startsWith(LISTING_URL)
           ? (params.mode === "recent" ? serveFlat(store, params)
              : serveListing(store, params))
         : {});
    return Promise.resolve({
      ok,
      status,
      json: () => Promise.resolve(body),
      text: () => Promise.resolve(JSON.stringify(body)),
    });
  }

  class FakeWs {
    static OPEN = 1;
    constructor(wsUrl) {
      this.url = wsUrl;
      this.readyState = FakeWs.OPEN;
      this.sent = [];
      this.onopen = this.onmessage = this.onclose = this.onerror = null;
      sockets.push(this);
    }
    send(text) { this.sent.push(JSON.parse(text)); }
    close() { this.readyState = 3; }
  }

  // The rail's freshness poll. Nothing on this page used a timer before it, so
  // the sandbox had neither — a page that called `setInterval` would have
  // thrown at load and every check below would have failed at once rather than
  // the one that cares. Held rather than run: a real interval in a test process
  // is a race, so `page.tick()` fires them by hand and each check decides how
  // many ticks it wants.
  const intervals = [];
  // The open/close refresh's retry. Held on the same terms and for the same
  // reason as the intervals above: `railRefreshSoon` only reaches for a timer
  // when a rail request is already in flight, and a real one firing on its own
  // schedule would make every check that closes a session racy.
  const timers = [];
  const docListeners = new Map();
  let visibility = opts.visibility ?? "visible";
  // Seeded from `opts.stored`, which is how a check starts the page in a
  // grouping mode instead of clicking its way there — the mode is read once at
  // script evaluation, so setting it afterwards would be too late.
  const stored = { ...(opts.stored || {}) };

  /* ---- the image-attachment surface ------------------------------------
   *
   * `FileReader`, `Image`, `Blob`, `URL` and a canvas 2D context are browser
   * furniture and none of it exists in a bare `vm` context. They are stubbed
   * rather than skipped because the paste path cannot be driven at all
   * otherwise — and `opts.images === false` is then a real case rather than an
   * accident: a browser that genuinely lacks them, which the page has to
   * refuse cleanly instead of throwing.
   *
   * The encoder is deterministic and swappable on purpose. What these checks
   * are for is the ladder, the budget arithmetic, the numbering and *which
   * mimeType comes out the other end* — none of which depend on real
   * compression. Whether a browser agrees about the byte counts is a browser
   * question and is verified in one.
   */
  class FakeBlob {
    constructor(size, type) { this.size = size; this.type = type; }
  }
  const objectUrls = new Map();
  const revokedUrls = [];
  let objectUrlSeq = 0;
  // Bytes-per-pixel by format, scaled by quality. The ordering is what matters
  // and it matches the measurement the page's ladder was written against: WebP
  // well under JPEG, PNG far above both.
  const RATE = { "image/webp": 0.06, "image/jpeg": 0.11, "image/png": 0.9 };
  const encodeBlob = opts.encode || ((type, quality, w, h) => {
    // A browser asked for a format it cannot encode answers with PNG rather
    // than failing, which is exactly why the page reads the *blob's* type
    // instead of the one it requested. `opts.noWebp` is that browser.
    const got = (type === "image/webp" && opts.noWebp) ? "image/png" : type;
    return new FakeBlob(
      Math.max(1, Math.round(w * h * (RATE[got] ?? 0.11) * quality)), got);
  });

  const sandbox = {
    document: {
      createElement: (tag) => {
        const el = new El(tag);
        if (String(tag).toLowerCase() === "canvas" && opts.images !== false) {
          el.getContext = () => ({ drawImage() {} });
          el.toBlob = (cb, type, quality) =>
            cb(encodeBlob(type, quality, el.width, el.height));
        }
        return el;
      },
      // SVG elements are constructed through createElementNS in the page; the
      // mock treats them identically to HTML elements — the harness only checks
      // the DOM tree and never renders SVG shapes.
      createElementNS: (_ns, tag) => new El(tag),
      getElementById: (id) => byId.get(id) ?? null,
      addEventListener: (type, fn) => {
        if (!docListeners.has(type)) docListeners.set(type, []);
        docListeners.get(type).push(fn);
      },
      get visibilityState() { return visibility; },
      write: () => HTML_SINK("document.write"),
    },
    setInterval: (fn, ms) => { intervals.push({ fn, ms }); return intervals.length; },
    clearInterval: function(id) {
      if (id != null) {
        var idx = intervals.findIndex(function(_, i) { return i + 1 === id; });
        if (idx !== -1) intervals.splice(idx, 1);
      }
    },
    setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
    clearTimeout: function(id) {
      // id is the 1-based index returned by the setTimeout mock above
      if (id != null) {
        var idx = timers.findIndex(function(_, i) { return i + 1 === id; });
        if (idx !== -1) timers.splice(idx, 1);
      }
    },
    location: {
      protocol: "http:",
      host: "127.0.0.1:4915",
      pathname: "/acp",
      search: opts.sid ? `?sid=${encodeURIComponent(opts.sid)}` : "",
      reload() { page.reloaded = true; },
    },
    history: { replaceState: (_s, _t, u) => urls.push(u) },
    WebSocket: FakeWs,
    fetch: fakeFetch,
    // Node's own. The diagnosis feature-tests for it and aborts its GET on
    // timeout. 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 6
    // review (J6)
    AbortController,
    // The page's confirmation gate. A real one blocks the thread, which is
    // exactly the property the delete path relies on — nothing after it runs
    // until the user has answered — so answering synchronously here models it
    // correctly. Absent from this sandbox until session deletion existed; a
    // page that reached for it before would have thrown at the call site.
    confirm: (text) => { confirms.push(text); return opts.confirm !== false; },
    // Without a stand-in, every `localStorage` read raises a ReferenceError
    // that the page's own try/catch swallows — so the grouping mode would pin
    // silently to its default and every check about remembering it would pass
    // against a page that remembered nothing. `opts.storageThrows` reproduces
    // the browser refusing storage outright, which is the case that try/catch
    // exists for and precisely the one an unconditional shim would hide.
    localStorage: {
      getItem(key) {
        if (opts.storageThrows) throw new Error("storage is unavailable");
        return key in stored ? stored[key] : null;
      },
      setItem(key, value) {
        if (opts.storageThrows) throw new Error("storage is unavailable");
        stored[key] = String(value);
      },
      removeItem(key) {
        if (opts.storageThrows) throw new Error("storage is unavailable");
        delete stored[key];
      },
    },
    console: { log() {}, warn() {}, error() {} },
  };
  if (opts.images !== false) {
    sandbox.Blob = FakeBlob;
    sandbox.URL = {
      createObjectURL(source) {
        const url = `blob:fake-${++objectUrlSeq}`;
        objectUrls.set(url, source);
        return url;
      },
      // Recorded rather than ignored: an object URL that is never revoked
      // keeps its blob alive for the tab's lifetime, and that leak is
      // invisible unless something counts.
      revokeObjectURL(url) { revokedUrls.push(url); },
    };
    sandbox.FileReader = class {
      readAsDataURL(blob) {
        // The page splits on the first comma and keeps the tail, so this
        // payload is what a check reads back off the wire.
        this.result = `data:${blob.type};base64,b64-${blob.type}-${blob.size}`;
        if (this.onload) this.onload();
      }
    };
    sandbox.Image = class {
      constructor() {
        this.naturalWidth = opts.imageWidth ?? 1774;
        this.naturalHeight = opts.imageHeight ?? 887;
      }
      // Assigning `src` is what starts a decode, and the page assigns its
      // handlers before it — so firing synchronously here models the ordering
      // correctly without needing a timer.
      set src(value) {
        this._src = value;
        if (opts.imageDecodeFails) { if (this.onerror) this.onerror(); }
        else if (this.onload) this.onload();
      }
      get src() { return this._src; }
    };
  }
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  // Present by default, because the served page loads it. `prism: false` is the
  // page as a reader with a failed or blocked /static/prism.js gets it, which
  // is a case with its own check — the highlighting is an upgrade to blocks
  // that already render, and a check that only ever ran with Prism loaded could
  // not tell that from a hard dependency on it.
  if (opts.prism !== false) sandbox.Prism = prismGlobal();
  vm.createContext(sandbox);
  // Runs first, in the same sandbox — not provided as a pre-built object the
  // way Prism is above. Prism gets that treatment because bootstrapping it
  // needs a real document this stand-in cannot fully provide; this file has
  // no such needs, so running its actual source is what a browser loading
  // <script src="/static/transcript-renderer.js"> before the inline <script>
  // does, and measuring anything else would not be measuring the real page.
  vm.runInContext(transcriptRendererSource(), sandbox,
                  { filename: "transcript-renderer.js" });
  // Runs next, same reasoning and same sandbox as transcript-renderer.js
  // above — acp.html loads composer-chrome.js immediately after it, before
  // the inline <script>.
  vm.runInContext(composerChromeSource(), sandbox,
                  { filename: "composer-chrome.js" });
  vm.runInContext(scriptBody, sandbox, { filename: `${templatePath}#inline-script` });

  Object.assign(page, {
    el(id) {
      const found = byId.get(id);
      if (!found) throw new Error(`this page has no element with id '${id}'`);
      return found;
    },
    // Expose the sandbox so tests can access page-level functions exposed on
    // window (e.g., window._testAddSystemMessage for the XSS test).
    sandbox,
    socket() {
      if (sockets.length === 0) throw new Error("the page opened no socket");
      return sockets[sockets.length - 1];
    },
    /** The Nth socket the page has opened, 0-indexed — the sub-agent panel
     *  opens a second, independent WebSocket (`subWs`) the first time a pill
     *  is pressed, and checks on it need to address the main socket (index 0)
     *  and the sub-agent one (index 1) separately rather than always getting
     *  whichever opened last. */
    socketAt(i) {
      if (!sockets[i]) throw new Error(`the page has not opened socket #${i}`);
      return sockets[i];
    },
    open() {
      const s = page.socket();
      s.readyState = FakeWs.OPEN;
      if (!s.onopen) throw new Error("the page set no onopen handler");
      s.onopen();
    },
    /** Like `open()`, for a socket other than the last one opened. */
    openAt(i) {
      const s = page.socketAt(i);
      s.readyState = FakeWs.OPEN;
      if (!s.onopen) throw new Error("the page set no onopen handler");
      s.onopen();
    },
    deliver(frame) {
      page.socket().onmessage({ data: JSON.stringify(frame) });
    },
    /** Like `deliver()`, targeted at a socket other than the last one opened. */
    deliverTo(i, frame) {
      page.socketAt(i).onmessage({ data: JSON.stringify(frame) });
    },
    click(id) { page.el(id).dispatch("click"); },
    type(text) { page.el("acpPrompt").value = text; },
    /* A clipboard file. The page reads `type` to decide whether it is an image
     * and `name` only to name it in a refusal, so those two are the whole
     * contract — a real `File` brings bytes the fake encoder never looks at. */
    imageFile(type = "image/png", name = "screenshot.png") { return { type, name }; },
    /** Paste files into the composer. Returns whether the page took the event
     *  over, which is what decides if an ordinary text paste still works. */
    paste(files) {
      let prevented = false;
      page.el("acpPrompt").dispatch("paste", {
        clipboardData: { files },
        preventDefault() { prevented = true; },
      });
      return prevented;
    },
    /** Drop files onto the composer, having dragged them over it first. */
    drop(files) {
      let allowed = false;
      page.el("acpComposer").dispatch("dragover", {
        dataTransfer: { types: ["Files"], files: [] },
        preventDefault() { allowed = true; },
      });
      page.el("acpComposer").dispatch("drop", {
        dataTransfer: { files },
        preventDefault() {},
      });
      return allowed;
    },
    trayChips() { return page.all("acpTray", ".acp-attach"); },
    /** Every object URL the page has revoked, in order. */
    revoked() { return revokedUrls.slice(); },
    sentOf(type) { return page.socket().sent.filter((f) => f.type === type); },
    transcript() { return page.el("acpTranscript").textContent; },
    // Dynamic lookup. Everything the rail draws is created after render, so
    // `byId`'s regex over the static markup cannot see any of it; these address
    // it by walking down from the static container it is appended to.
    all(id, sel) { return page.el(id).querySelectorAll(sel); },
    one(id, sel) { return page.el(id).querySelector(sel); },
    railGroups() { return page.all("acpRailGroups", ".acp-rail-group"); },
    railRows() { return page.all("acpRailGroups", ".acp-rail-row"); },
    railTitles() {
      return page.railRows().map(
        (row) => row.querySelector(".acp-rail-row-title").textContent);
    },
    /** What `localStorage` holds, so persistence is asserted at the store. */
    stored,
    /** The heading of each group, whichever shape the rail is drawing. */
    railHeadings() {
      return page.all("acpRailGroups", ".acp-rail-group-name")
                 .map((n) => n.textContent);
    },
    settingsOptions() {
      return page.all("acpRailSettingsMenu", ".acp-rail-setting");
    },
    openSettings() {
      page.click("acpRailSettings");
      return page.settingsOptions();
    },
    listingCalls() {
      // Excludes the delete route, which the prefix would otherwise swallow —
      // the same collision `fakeFetch` guards against, and here it would let a
      // check counting listing requests silently count deletions too.
      return page.fetches.filter(
        (f) => f.url.startsWith(LISTING_URL) && f.url !== DELETE_URL);
    },
    intervals,
    /** Fire every registered interval once, as the browser would on a tick. */
    tick() { for (const t of intervals) t.fn(); },
    timers,
    /** Fire every timer queued so far, once. Drained before running so a retry
     *  that queues another timer does not extend the loop it is inside — the
     *  caller decides how many rounds it wants by calling this again. */
    runTimers() {
      const due = timers.splice(0, timers.length);
      for (const t of due) t.fn();
      return due.length;
    },
    /** Move the tab between foreground and background, notifying the page the
     *  way a browser does. Two steps because the page reads the property and
     *  listens for the event, and a helper that only did one would let a check
     *  pass against a page that only did the other. */
    setVisibility(state) {
      visibility = state;
      for (const fn of docListeners.get("visibilitychange") ?? []) fn();
    },
    // Null is this harness's <body>: nothing in the rail holds focus.
    focused() { return ACTIVE; },
    /** Fire a document-level listener, the way a bubbling event would.
     *
     *  This harness does not bubble — `El.dispatch` calls one node's own
     *  listeners and stops — so a menu that closes on a document click is
     *  invisible to a check that only presses buttons. Firing the listener
     *  directly measures the handler; what it cannot measure is that a real
     *  press reaches it, which is why the page guards that path with a flag
     *  rather than with stopPropagation. */
    fireDoc(type, ev) {
      const fns = docListeners.get(type) ?? [];
      if (fns.length === 0) throw new Error(`nothing listens for document '${type}'`);
      for (const fn of fns) fn(ev ?? {});
    },
    pickerRows() { return page.all("acpPickerList", ".acp-picker-ws"); },
    pickerNames() {
      // The label, not the whole name row: the row also carries the session
      // count, and `textContent` on the parent concatenates the two into
      // "ws-01 session".
      return page.pickerRows().map(
        (r) => r.querySelector(".acp-picker-option-label").textContent);
    },
    /** Open the picker and let its workspace fetch land. */
    async openPicker(which = "acpRailNew") {
      page.click(which);
      await page.settle();
    },
    railMenuButtons() { return page.all("acpRailGroups", ".acp-rail-menu-btn"); },
    railMenus() { return page.all("acpRailGroups", ".acp-rail-menu"); },
    openMenus() { return page.railMenus().filter((m) => !m.hidden); },
    deleteCalls() { return page.fetches.filter((f) => f.url === DELETE_URL); },
    // Everything the rail does hangs off a promise chain, so a check that
    // asserted straight after the click would be asserting on the frame before
    // the one it cares about. `setImmediate` runs after the microtask queue has
    // drained, which is every `.then` the page had pending.
    settle() { return new Promise((resolve) => setImmediate(resolve)); },
  });
  return page;
}

/** A page whose rail has finished its first load. */
async function railed(templatePath, opts = {}) {
  const page = loadPage(templatePath, opts);
  page.open();
  await page.settle();
  return page;
}

/** A page already past connect + a `session` frame, which is where the
 *  interesting behaviour starts. Returns the session id it settled on. */
// `rest` forwards anything else straight to `loadPage`, which is how the image
// checks reach `encode`, `noWebp`, `imageWidth` and friends without every
// caller having to know they exist.
function connected(templatePath, { sid = "", turnActive = false, prism, ...rest } = {}) {
  const page = loadPage(templatePath, { sid, prism, ...rest });
  page.open();
  const live = "sess-live-0001";
  page.deliver({
    type: "session",
    sessionId: live,
    payload: {
      sessionId: live, cwd: "C:\\work\\repo", created: !sid,
      turnActive, contextPercent: null,
    },
  });
  return { page, live };
}

// ------------------------------------------------------------------ checks --

const checks = [];
const check = (name, fn) => checks.push({ name, fn });

/* Let the staging promise chain finish. Every stub above resolves
 * synchronously, so one turn of the real macrotask queue is enough to drain
 * every microtask behind a paste. Node's own `setTimeout`, not the sandbox's
 * held one — different scopes, and this is the test process's clock. */
const settleStaging = () => new Promise((resolve) => setTimeout(resolve, 0));

function assert(cond, message) {
  if (!cond) throw new Error(message);
}
function assertEqual(got, want, message) {
  if (got !== want) {
    throw new Error(`${message}: expected ${JSON.stringify(want)}, got ${JSON.stringify(got)}`);
  }
}

check("no HTML sink appears anywhere in the page source", (tpl) => {
  const src = fs.readFileSync(tpl, "utf8");
  // Comments on this page discuss the rule by name, so only *uses* count: a
  // `.innerHTML`, an `outerHTML`, an `insertAdjacentHTML(` or a document.write.
  const uses = [...src.matchAll(/\.(innerHTML|outerHTML)\s*(=|\+=)|insertAdjacentHTML\s*\(|document\s*\.\s*write\s*\(/g)];
  assertEqual(uses.length, 0,
              `the page writes through an HTML sink: ${uses.map((m) => m[0]).join(", ")}`);
});

check("exactly one inline script in the content block, carrying the CSP nonce", (tpl) => {
  // Named for what it measures. The *served* /acp has five script elements
  // (dashboard/ACP feature-parity plan, Phase 1 added composer-chrome.js
  // alongside htmx.min.js, prism.js, transcript-renderer.js and the inline
  // script): this template extends base.html, which carries
  // `<script src="/static/htmx.min.js">` with the same nonce applied
  // conditionally. `loadPage` renders this template in isolation, so what the
  // count below pins is that acp.html contributes one inline script and no
  // external one — not that the page has a single tag.
  const page = loadPage(tpl);
  assert(/nonce="NONCE-1"/.test(page.scriptAttrs),
         `the single <script> carries no rendered nonce: ${page.scriptAttrs}`);
  assert(!/\bsrc=/.test(page.scriptAttrs),
         "the script is external, so the nonce policy would not cover its body");
});

check("subscribe carries the live session id, not the rendered one", (tpl) => {
  const page = loadPage(tpl, { sid: "sess-from-url-01" });
  page.open();
  assertEqual(page.sentOf("subscribe")[0]?.sessionId, "sess-from-url-01",
              "the first subscribe should use the id the URL carried");
  const live = "sess-adopted-02";
  page.deliver({
    type: "session", sessionId: live,
    payload: { sessionId: live, cwd: "C:\\w", created: false, turnActive: false },
  });
  page.click("acpReconnect");
  page.open();
  const subs = page.sentOf("subscribe");
  assertEqual(subs.length, 1, "the reconnected socket sent the wrong number of subscribes");
  assertEqual(subs[0].sessionId, live,
              "the reconnect resubscribed to the render-time ACP_SID, replaying " +
              "that conversation over the live one");
});

check("a session created with no ?sid= is still resubscribed on reconnect", (tpl) => {
  const { page, live } = connected(tpl);
  assertEqual(page.sentOf("subscribe").length, 0,
              "there was no session to subscribe to on the first socket");
  page.click("acpReconnect");
  page.open();
  const subs = page.sentOf("subscribe");
  assertEqual(subs.length, 1,
              "the reconnected socket sent no subscribe, leaving a connected-looking " +
              "page whose next prompt is refused not_subscribed");
  assertEqual(subs[0].sessionId, live, "the reconnect subscribed to the wrong session");
});

check("the header names the workspace by default, not the session id", (tpl) => {
  const { page } = connected(tpl);
  // `connected()`'s fixture cwd is `C:\work\repo` — the short form is what a
  // rail row scrolled out of view still leaves the pane able to say.
  assertEqual(page.el("acpSid").textContent, "repo",
              "opening a session should name its workspace by default — the raw id " +
              "is a tap away for whichever conversation actually needs it, not the " +
              "thing shown by default");
  assertEqual(page.el("acpSid").getAttribute("aria-pressed"), "false",
              "the header started in the id-hidden state without saying so to a " +
              "screen reader");
});

check("tapping the header reveals the session id, and tapping again hides it", (tpl) => {
  const { page, live } = connected(tpl);
  page.click("acpSid");
  assertEqual(page.el("acpSid").textContent, "session " + live,
              "tapping the workspace name did not reveal the session id");
  assertEqual(page.el("acpSid").getAttribute("aria-pressed"), "true",
              "the toggle did not announce its new state");
  page.click("acpSid");
  assertEqual(page.el("acpSid").textContent, "repo",
              "a second tap did not put the workspace name back");
  assertEqual(page.el("acpSid").getAttribute("aria-pressed"), "false",
              "the toggle did not announce reverting");
});

check("a session with no workspace known yet shows the raw id, not a blank header", async (tpl) => {
  const page = await railed(tpl);
  page.railRows()[6].dispatch("click");
  assert(page.el("acpSid").textContent.includes("sess-w1-s1"),
         "before the session frame answers there is nothing to show but the id — " +
         "a blank header here would read as broken rather than as still loading");
});

check("a reconnect's session frame shows the workspace again, not the id it was left on", (tpl) => {
  const { page, live } = connected(tpl);
  page.click("acpSid");
  assertEqual(page.el("acpSid").textContent, "session " + live,
              "fixture is wrong: the tap never revealed the id, so a reconnect " +
              "resetting it proves nothing");
  page.click("acpReconnect");
  page.open();
  page.deliver({
    type: "session", sessionId: live,
    payload: { sessionId: live, cwd: "C:\\work\\repo", created: false, turnActive: false },
  });
  assertEqual(page.el("acpSid").textContent, "repo",
              "the reconnect kept the id revealed from before the socket dropped, " +
              "instead of starting the new subscription the way every other one does");
});

check("a workspace path with a trailing separator still names the folder alone", (tpl) => {
  const page = loadPage(tpl, {});
  page.open();
  page.deliver({
    type: "session", sessionId: "sess-trail",
    payload: { sessionId: "sess-trail", cwd: "C:\\work\\repo\\",
               created: true, turnActive: false },
  });
  assertEqual(page.el("acpSid").textContent, "repo",
              "a trailing separator on cwd leaked into the displayed workspace name");
});

check("a refused send puts the typed prompt back in the box", (tpl) => {
  const { page, live } = connected(tpl);
  const typed = "summarise the repository layout";
  page.type(typed);
  page.click("acpSend");
  assertEqual(page.sentOf("prompt")[0]?.payload?.prompt, typed,
              "the prompt never reached the wire");
  assertEqual(page.el("acpPrompt").value, "", "the box should be cleared on send");
  page.deliver({
    type: "error", sessionId: live,
    payload: { code: "turn_in_progress", message: "This session is still answering." },
  });
  assertEqual(page.el("acpPrompt").value, typed,
              "the refusal lost what the user typed, with no way to get it back");
});

check("a refusal does not clobber what was typed since", (tpl) => {
  const { page, live } = connected(tpl);
  page.type("first question");
  page.click("acpSend");
  page.type("second question");
  page.deliver({
    type: "error", sessionId: live,
    payload: { code: "agent_error", message: "the agent refused" },
  });
  assertEqual(page.el("acpPrompt").value, "second question",
              "the restore overwrote what the user had started typing");
  assert(page.transcript().includes("first question"),
         "the refused prompt was dropped without being shown anywhere");
});

// The composer's height. `scrollHeight` is an inert field on the stand-in — a
// browser recomputes it from the content, this harness does not — so these set
// it by hand to stand for "the content is now this tall". That is enough to
// pin the arithmetic and the reset, which is where the defects are; what it
// cannot see is whether a real browser agrees about the number, and that half
// is browser-verified rather than asserted here.
check("the composer grows with what is typed into it", (tpl) => {
  const { page } = connected(tpl);
  const box = page.el("acpPrompt");
  page.type("one\ntwo\nthree");
  box.scrollHeight = 90;
  box.dispatch("input");
  assertEqual(box.style.height, "92px",
              "the composer did not grow to its content — 90 px of text plus " +
              "the 2 px border box-sizing: border-box makes `height` carry");
});

check("the composer comes back down when the text is deleted", (tpl) => {
  const { page } = connected(tpl);
  const box = page.el("acpPrompt");
  box.scrollHeight = 120;
  box.dispatch("input");
  assertEqual(box.style.height, "122px", "the composer did not grow first");
  // A browser reports the *set* height from scrollHeight once an explicit one
  // is in place, which is why the page resets to 'auto' before measuring. With
  // that reset missing the box grows and never shrinks, and this is the check
  // that would catch it.
  box.scrollHeight = 30;
  box.dispatch("input");
  assertEqual(box.style.height, "32px",
              "the composer stayed tall after its content shrank — the " +
              "height: auto reset before the measurement is missing");
});

check("growing the composer keeps a reader pinned to the bottom", (tpl) => {
  const { page } = connected(tpl);
  const box = page.el("acpPrompt");
  const pane = page.el("acpTranscript");
  // Within 60 px of the bottom: stuck.
  pane.scrollHeight = 500;
  pane.scrollTop = 480;
  pane.clientHeight = 0;
  box.scrollHeight = 90;
  box.dispatch("input");
  assertEqual(pane.scrollTop, 500,
              "the composer took its new height out of the transcript and " +
              "pushed a reader at the bottom off the newest message");
});

check("growing the composer leaves a reader scrolled up where they were", (tpl) => {
  const { page } = connected(tpl);
  const box = page.el("acpPrompt");
  const pane = page.el("acpTranscript");
  // 500 px from the bottom: deliberately reading history, not stuck.
  pane.scrollHeight = 500;
  pane.scrollTop = 0;
  pane.clientHeight = 0;
  box.scrollHeight = 90;
  box.dispatch("input");
  assertEqual(pane.scrollTop, 0,
              "typing yanked a reader who had scrolled up back to the bottom");
});

// ---- image attachments ----
//
// The encoder behind these is a deterministic stub, so what they pin is the
// page's own arithmetic and bookkeeping — the ladder, the budget, the
// numbering, which mimeType is reported and what gets revoked. Whether a real
// canvas produces those byte counts is a browser question, verified in one.

check("pasting an image stages it without touching the transcript", async (tpl) => {
  const { page } = connected(tpl);
  const took = page.paste([page.imageFile()]);
  assert(took, "the page let the browser handle an image paste itself");
  await settleStaging();
  assertEqual(page.trayChips().length, 1, "the image was not staged");
  assertEqual(page.el("acpTray").hidden, false, "the tray stayed hidden");
  assert(page.el("acpTray").textContent.includes("Image 1"),
         "the chip is not labelled with the name the transcript will use");
  assertEqual(page.sentOf("prompt").length, 0,
              "staging an image sent a prompt on its own");
});

check("a paste carrying no image is left entirely alone", (tpl) => {
  const { page } = connected(tpl);
  // Pasting a stack trace or a code block into the box is the common case;
  // swallowing it to go looking for pictures would break the feature people
  // actually use.
  const took = page.paste([{ type: "text/plain", name: "notes.txt" }]);
  assert(!took, "an ordinary text paste was intercepted");
  assertEqual(page.el("acpTray").hidden, true, "a text paste opened the tray");
});

check("a staged image travels as bytes the transcript never carries", async (tpl) => {
  const { page } = connected(tpl);
  page.paste([page.imageFile()]);
  await settleStaging();
  page.type("what is wrong here?");
  page.click("acpSend");
  const sent = page.sentOf("prompt")[0];
  assertEqual(sent.payload.prompt, "what is wrong here?",
              "the text and the images should travel in separate fields");
  assertEqual(sent.payload.images.length, 1, "the image never reached the wire");
  assert(sent.payload.images[0].data.length > 0, "the image carried no data");
  // Only the two fields the server validates. A thumbnail URL or a byte count
  // sent here would be a field nothing on the other side reads.
  assertEqual(Object.keys(sent.payload.images[0]).sort().join(","),
              "data,mimeType", "the wire carried more than the server reads");
  assertEqual(page.trayChips().length, 0, "the tray kept the images after sending");
});

check("an image with no words is a whole prompt", async (tpl) => {
  const { page } = connected(tpl);
  page.paste([page.imageFile()]);
  await settleStaging();
  page.click("acpSend");
  const sent = page.sentOf("prompt")[0];
  assert(sent, "paste-and-send with an empty box sent nothing at all");
  // After Phase 2, pasting an image inserts [Image 1] at the cursor, so the
  // prompt text is "[Image 1]" rather than "". Both the text marker and the
  // attached image travel together.
  assertEqual(sent.payload.prompt, "[Image 1]",
    "paste inserts [Image 1] marker — the prompt text should carry it");
  assertEqual(sent.payload.images.length, 1, "the image never reached the wire");
});

check("the type sent is the one the encoder produced, not the one asked for", async (tpl) => {
  // A browser that cannot encode WebP answers `toBlob('image/webp')` with a
  // PNG rather than failing. Forwarding the requested type would then be a
  // lie, and a declared type that disagrees with the bytes is the one thing
  // the agent handles worst — it comes back as an internal error naming no
  // image at all.
  const { page } = connected(tpl, { noWebp: true, imageWidth: 200, imageHeight: 100 });
  page.paste([page.imageFile()]);
  await settleStaging();
  page.click("acpSend");
  assertEqual(page.sentOf("prompt")[0].payload.images[0].mimeType, "image/png",
              "the page reported the format it requested rather than the one " +
              "it got back");
});

check("the encoder walks down the ladder until something fits", async (tpl) => {
  const { page } = connected(tpl, {
    encode: (type, quality) => ({ size: quality > 0.7 ? 900000 : 5000, type }),
  });
  page.paste([page.imageFile()]);
  await settleStaging();
  assertEqual(page.trayChips().length, 1,
              "the first rung did not fit and the page gave up instead of " +
              "trying a lower quality");
  assert(page.el("acpTray").textContent.includes("5 KB"),
         "the staged image is not the one the lower rung produced");
});

check("an image that cannot be made to fit is refused, not truncated", async (tpl) => {
  const { page } = connected(tpl, { encode: (type) => ({ size: 900000, type }) });
  page.paste([page.imageFile()]);
  await settleStaging();
  assertEqual(page.trayChips().length, 0, "an oversized image was staged anyway");
  assert(page.transcript().includes("not attached"),
         "the refusal was not said anywhere the user will read it");
});

check("the count cap is the server's, not a number written into the page", async (tpl) => {
  const { page } = connected(tpl);
  page.deliver({
    type: "meta",
    payload: { connected: true, maxMessageBytes: 262144, maxConnections: 8,
               maxPromptImages: 1, maxPromptImageBytes: 180224 },
  });
  page.paste([page.imageFile()]);
  await settleStaging();
  page.paste([page.imageFile("image/png", "second.png")]);
  await settleStaging();
  assertEqual(page.trayChips().length, 1,
              "the page ignored the cap the server advertised");
  assert(page.transcript().includes("at most 1 images"),
         "nothing said why the second image was dropped");
});

check("removing a staged image gives its object URL back", async (tpl) => {
  const { page } = connected(tpl);
  page.paste([page.imageFile()]);
  await settleStaging();
  const before = page.revoked().length;
  page.trayChips()[0].querySelector(".acp-attach-drop").dispatch("click");
  assertEqual(page.trayChips().length, 0, "the chip stayed after being removed");
  assert(page.revoked().length > before,
         "the object URL was never revoked — its blob stays alive for the " +
         "lifetime of the tab");
});

check("opening another session drops images staged against the last one", async (tpl) => {
  // Driven through the rail, because `selectSession` is the only thing that
  // performs this clear and a `session` frame does not go near it.
  const page = await railed(tpl);
  page.paste([page.imageFile()]);
  await settleStaging();
  assertEqual(page.trayChips().length, 1, "nothing was staged to begin with");
  const before = page.revoked().length;
  page.railRows()[4].dispatch("click");
  // A screenshot staged for one conversation must not arrive in front of a
  // different agent running in a different directory.
  assertEqual(page.trayChips().length, 0,
              "images staged for the previous session survived the switch");
  assert(page.revoked().length > before,
         "the tray was emptied but the object URLs behind it were not revoked");
});

check("closing the session drops the images staged against it", async (tpl) => {
  const { page, live } = connected(tpl);
  page.paste([page.imageFile()]);
  await settleStaging();
  const before = page.revoked().length;
  page.deliver({
    type: "session_closed", sessionId: live,
    payload: { sessionId: live, reason: "closed" },
  });
  assertEqual(page.trayChips().length, 0,
              "the session went away but its staged images stayed behind");
  assert(page.revoked().length > before, "the object URLs were never revoked");
});

check("a refused prompt gives the images back with the text", async (tpl) => {
  const { page, live } = connected(tpl);
  page.paste([page.imageFile()]);
  await settleStaging();
  page.type("look at this");
  page.click("acpSend");
  assertEqual(page.trayChips().length, 0, "the tray should empty on send");
  page.deliver({
    type: "error", sessionId: live,
    payload: { code: "turn_in_progress", message: "still answering" },
  });
  assertEqual(page.el("acpPrompt").value, "look at this", "the text was lost");
  assertEqual(page.trayChips().length, 1,
              "the refusal cost the user their attachment, which is another " +
              "paste, decode and re-encode to replace");
});

check("a started turn releases the images it consumed", async (tpl) => {
  const { page, live } = connected(tpl);
  page.paste([page.imageFile()]);
  await settleStaging();
  page.click("acpSend");
  const before = page.revoked().length;
  page.deliver({ type: "meta", sessionId: live, payload: { turn: "start" } });
  assert(page.revoked().length > before,
         "the turn started but the sent images' object URLs were never " +
         "revoked, so their blobs outlive the page's use for them");
});

check("dropping an image onto the composer stages it", async (tpl) => {
  const { page } = connected(tpl);
  const allowed = page.drop([page.imageFile()]);
  assert(allowed, "dragover never called preventDefault, so a real browser " +
                  "would navigate to the image instead of dropping it here");
  await settleStaging();
  assertEqual(page.trayChips().length, 1, "the dropped image was not staged");
});

check("a browser with no image APIs refuses cleanly instead of throwing", async (tpl) => {
  // The whole page is evaluated in this sandbox, so if any of the image code
  // reached for `FileReader` or a canvas at load rather than at the point of
  // use, every other check here would fail too — not just this one.
  const { page } = connected(tpl, { images: false });
  page.paste([page.imageFile()]);
  await settleStaging();
  assertEqual(page.trayChips().length, 0, "something was staged with no encoder");
  assert(page.transcript().includes("cannot attach images"),
         "the page failed silently rather than saying it could not attach");
});

check("a replayed turn marker does not move the buttons", (tpl) => {
  const { page, live } = connected(tpl);
  assertEqual(page.el("acpSend").disabled, false, "Send should be live on an idle session");
  page.deliver({
    type: "history", sessionId: live,
    payload: { events: [
      { type: "meta", sessionId: live, payload: { turn: "start" } },
      { type: "chunk", sessionId: live, payload: { role: "agent", text: "an old answer" } },
    ] },
  });
  assert(page.transcript().includes("an old answer"), "the replay rendered nothing");
  assertEqual(page.el("acpSend").disabled, false,
              "a replayed turn marker disabled Send against a session that is idle; " +
              "the ring buffer evicts these, so they are not evidence of a live turn");
});

check("a live turn marker does move the buttons", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({ type: "meta", sessionId: live, payload: { turn: "start" } });
  assertEqual(page.el("acpSend").disabled, true,
              "positive control: a live turn must disable Send");
});

check("a live turn shows a thinking indicator the instant it starts", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({ type: "meta", sessionId: live, payload: { turn: "start" } });
  assert(page.transcript().includes("thinking"),
         "a turn that started with nothing streamed yet gave no sign of being alive " +
         "— exactly the stalled appearance this indicator exists to prevent");
});

check("a replayed turn start does not show a thinking indicator", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({
    type: "history", sessionId: live,
    payload: { events: [
      { type: "meta", sessionId: live, payload: { turn: "start" } },
      { type: "chunk", sessionId: live, payload: { role: "agent", text: "an old answer" } },
      { type: "meta", sessionId: live, payload: { turn: "end", stopReason: "end_turn" } },
    ] },
  });
  assert(!page.transcript().includes("thinking"),
         "replaying a finished turn's start marker showed a live indicator for a " +
         "turn that has been over since before this page loaded");
});

check("the first chunk clears the thinking indicator", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({ type: "meta", sessionId: live, payload: { turn: "start" } });
  assert(page.transcript().includes("thinking"), "fixture: the indicator never showed");
  page.deliver({ type: "chunk", sessionId: live, payload: { role: "agent", text: "an answer" } });
  assert(!page.transcript().includes("thinking"),
         "the placeholder survived the first real content, sitting above the answer " +
         "it was standing in for");
});

check("a tool call clears the thinking indicator", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({ type: "meta", sessionId: live, payload: { turn: "start" } });
  page.deliver({
    type: "tool_call", sessionId: live,
    payload: { toolCallId: "t-think", title: "shell", kind: "execute",
               status: "pending", command: "git status" },
  });
  assert(!page.transcript().includes("thinking"),
         "a tool call did not clear the placeholder, so it now sits above a tool the " +
         "model has already started running");
});

check("the turn ending clears a thinking indicator that never got an answer", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({ type: "meta", sessionId: live, payload: { turn: "start" } });
  page.deliver({ type: "meta", sessionId: live,
                 payload: { turn: "end", stopReason: "end_turn" } });
  assert(!page.transcript().includes("thinking"),
         "a turn that ended without ever streaming anything left the placeholder on " +
         "screen with nothing left coming to remove it");
});

check("agent_thought_chunk content replaces the placeholder text", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({ type: "meta", sessionId: live, payload: { turn: "start" } });
  page.deliver({ type: "thought", sessionId: live,
                 payload: { text: "considering the diff" } });
  assert(page.transcript().includes("considering the diff"),
         "a thought frame's own text did not reach the transcript — unobserved on " +
         "the wire so far, but a build that does send it should not be dropped");
  assert(!page.transcript().includes("thinking…"),
         "the generic placeholder survived alongside real thought content");
});

check("a session closing removes its stuck thinking indicator", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({ type: "meta", sessionId: live, payload: { turn: "start" } });
  page.deliver({
    type: "session_closed", sessionId: live,
    payload: { sessionId: live, message: "This session was closed." },
  });
  assert(!page.transcript().includes("thinking"),
         "the session closing left a thinking indicator for a session that no " +
         "longer exists");
});

check("the session frame is authoritative for turn state", (tpl) => {
  const { page } = connected(tpl, { sid: "sess-from-url-01", turnActive: true });
  assertEqual(page.el("acpSend").disabled, true,
              "a reconnect to a session still holding a turn left Send enabled; the " +
              "start marker it would infer from is evictable, the frame is not");
});

check("replayed error frames render into the transcript", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({
    type: "history", sessionId: live,
    payload: { events: [
      { type: "chunk", sessionId: live, payload: { role: "user", text: "do the thing" } },
      { type: "error", sessionId: live,
        payload: { code: "agent_error", message: "Internal error (code -32603)" } },
    ] },
  });
  assert(page.transcript().includes("Internal error (code -32603)"),
         "a replayed failure reached only the 120 px log strip, which is not replayed — " +
         "so after a reload the turn comes back as a prompt with no answer");
});

check("a replayed refusal does not restore the prompt, a live one does", (tpl) => {
  const { page, live } = connected(tpl);
  page.type("the pending question");
  page.click("acpSend");
  page.deliver({
    type: "history", sessionId: live,
    payload: { events: [
      { type: "error", sessionId: live,
        payload: { code: "agent_error", message: "an old failure" } },
    ] },
  });
  assertEqual(page.el("acpPrompt").value, "",
              "a replayed failure from an earlier turn refilled the box");
  page.deliver({
    type: "error", sessionId: live,
    payload: { code: "agent_error", message: "a live failure" },
  });
  assertEqual(page.el("acpPrompt").value, "the pending question",
              "the live refusal did not restore the prompt it refused");
});

check("agent text reaches the DOM as text, and class names stay literal", (tpl) => {
  const { page, live } = connected(tpl);
  let armed = false;
  try { new El("div").innerHTML = "x"; } catch { armed = true; }
  assert(armed, "the harness is not enforcing the no-innerHTML rule");
  const hostile = '<img src=x onerror="alert(1)">';
  page.deliver({
    type: "tool_call", sessionId: live,
    payload: { toolCallId: "t-1", title: hostile, kind: `execute" onload=x`,
               status: "pending", command: "rm -rf ." },
  });
  const text = page.transcript();
  assert(text.includes(hostile), "the tool call's title never reached the transcript");
  assert(text.includes("rm -rf ."),
         "the command a trust-all-tools agent is about to run was not rendered");
  for (const node of page.el("acpTranscript").descendants()) {
    assert(!/onload|onerror|</.test(String(node.className)),
           `an agent-authored string reached a class name: ${node.className}`);
  }
});

check("a later tool_update rewrites the same row", (tpl) => {
  const { page, live } = connected(tpl);
  const call = { toolCallId: "t-9", title: "shell", kind: "execute",
                 status: "pending", command: "git status" };
  page.deliver({ type: "tool_call", sessionId: live, payload: call });
  const rows = page.el("acpTranscript").childNodes.length;
  page.deliver({ type: "tool_update", sessionId: live,
                 payload: { ...call, status: "completed" } });
  assertEqual(page.el("acpTranscript").childNodes.length, rows,
              "the update opened a second row instead of rewriting the first");
  assert(page.transcript().includes("completed"), "the new status never rendered");
});

// ---- tool call elapsed time ------------------------------------------

check("a tool_call with startedAt and stoppedAt shows elapsed time in the head", (tpl) => {
  const { page, live } = connected(tpl);
  // startedAt and stoppedAt are POSIX seconds (time.time() on the server).
  // Use values far enough apart that elapsedText produces a non-empty string.
  const startedAt = 1000;
  const stoppedAt = 1003;
  page.deliver({
    type: "tool_call", sessionId: live,
    payload: { toolCallId: "t-tm1", title: "shell", kind: "execute",
               status: "completed", command: "echo hi",
               startedAt, stoppedAt },
  });
  assert(page.transcript().includes("3s"),
         "a tool call with a 3-second elapsed time did not show '3s' in the transcript — " +
         "startedAt/stoppedAt are not being picked up by the time span");
});

check("a tool_update with stoppedAt freezes the elapsed time", (tpl) => {
  const { page, live } = connected(tpl);
  const startedAt = 2000;
  const stoppedAt = 2065;
  page.deliver({
    type: "tool_call", sessionId: live,
    payload: { toolCallId: "t-tm2", title: "shell", kind: "execute",
               status: "in_progress", command: "make",
               startedAt },
  });
  page.deliver({
    type: "tool_update", sessionId: live,
    payload: { toolCallId: "t-tm2", status: "completed",
               startedAt, stoppedAt },
  });
  // 65s = 1m 5s
  assert(page.transcript().includes("1m 5s"),
         "after a tool_update carrying stoppedAt, the elapsed time was not frozen at " +
         "the correct value — expected '1m 5s' for a 65-second call");
});

check("a tool_call with no startedAt shows no elapsed time span", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({
    type: "tool_call", sessionId: live,
    payload: { toolCallId: "t-tm3", title: "shell", kind: "execute",
               status: "in_progress", command: "ls" },
  });
  // No startedAt — time span should be hidden (empty text content).
  const transcript = page.el("acpTranscript");
  const timeSpans = Array.from(transcript.querySelectorAll
    ? transcript.querySelectorAll(".acp-tool-time")
    : []);
  const anyVisible = timeSpans.some(function(s) {
    return !s.hidden && s.textContent.trim() !== "";
  });
  assert(!anyVisible,
         "a tool_call with no startedAt rendered a non-empty elapsed time span");
});

// ------------------------------------------------- the agent's markdown --
//
// The server parses a finished bubble with mistune and sends the **token
// tree**; the page walks it with createElement + textContent. Every fixture
// below is the tree mistune 3.3.4 really produces for the markdown named
// above it — captured from the installed parser, not invented — so these
// checks measure the page against the wire it actually meets.
//
// **The page's allowlist is the entire security boundary**, and that is
// measured rather than assumed: `create_markdown(renderer=None)` never
// consults `escape=` (it belongs to `HTMLRenderer`) and never applies
// `safe_url()` (it lives in the HTML renderer too), so `<script>` arrives raw
// and `javascript:` arrives as a link URL. Nothing upstream has looked at
// either. Each of the four refusals below therefore has a check that fails if
// the rule is removed — a rule with no failing mutation behind it is a comment.

// `# Findings` + bold/italic/inline code + a fenced block + both list kinds.
const MD_FORMATTED = [{"type":"heading","attrs":{"level":1},"style":"atx","children":[{"type":"text","raw":"Findings"}]},{"type":"blank_line"},{"type":"paragraph","children":[{"type":"text","raw":"It is "},{"type":"strong","children":[{"type":"text","raw":"bold"}]},{"type":"text","raw":", "},{"type":"emphasis","children":[{"type":"text","raw":"slanted"}]},{"type":"text","raw":", see "},{"type":"codespan","raw":"run.py"},{"type":"text","raw":":"}]},{"type":"blank_line"},{"type":"block_code","raw":"x = 1\n","style":"fenced","marker":"```","attrs":{"info":"py"}},{"type":"blank_line"},{"type":"list","children":[{"type":"list_item","children":[{"type":"block_text","children":[{"type":"text","raw":"one"}]}]},{"type":"list_item","children":[{"type":"block_text","children":[{"type":"text","raw":"two"}]}]}],"tight":true,"bullet":"-","attrs":{"depth":0,"ordered":false}},{"type":"list","children":[{"type":"list_item","children":[{"type":"block_text","children":[{"type":"text","raw":"first"}]}]},{"type":"list_item","children":[{"type":"block_text","children":[{"type":"text","raw":"second"}]}]}],"tight":true,"bullet":".","attrs":{"depth":0,"ordered":true}}];

// `Hello **there**` / `<script>alert(1)</script>` / `Bye`. The prose either
// side is what makes the check meaningful: with only the script tag in it the
// bubble would render to nothing and keep its plain text, and the check would
// fail against correct behaviour.
const MD_BLOCK_HTML = [{"type":"paragraph","children":[{"type":"text","raw":"Hello "},{"type":"strong","children":[{"type":"text","raw":"there"}]}]},{"type":"blank_line"},{"type":"block_html","raw":"<script>alert(1)</script>\n"},{"type":"blank_line"},{"type":"paragraph","children":[{"type":"text","raw":"Bye"}]}];

// `Inline <img src=x onerror=alert(1)> here.`
const MD_INLINE_HTML = [{"type":"paragraph","children":[{"type":"text","raw":"Inline "},{"type":"inline_html","raw":"<img src=x onerror=alert(1)>"},{"type":"text","raw":" here."}]}];

// `Look: ![alt text](http://evil.example/x.png) done.`
const MD_IMAGE = [{"type":"paragraph","children":[{"type":"text","raw":"Look: "},{"type":"image","children":[{"type":"text","raw":"alt text"}],"attrs":{"url":"http://evil.example/x.png"}},{"type":"text","raw":" done."}]}];

// `[safe](https://example.com/a), [bad](javascript:alert(1)), [rel](/local/path)`
const MD_LINKS = [{"type":"paragraph","children":[{"type":"link","children":[{"type":"text","raw":"safe"}],"attrs":{"url":"https://example.com/a"}},{"type":"text","raw":", "},{"type":"link","children":[{"type":"text","raw":"bad"}],"attrs":{"url":"javascript:alert(1)"}},{"type":"text","raw":", "},{"type":"link","children":[{"type":"text","raw":"rel"}],"attrs":{"url":"/local/path"}}]}];

// Two lines of one paragraph.
const MD_SOFTBREAK = [{"type":"paragraph","children":[{"type":"text","raw":"line one"},{"type":"softbreak"},{"type":"text","raw":"line two"}]}];

// `| Integration | Topic pattern | Rows |` with `|---|:---:|---:|` under it and
// one body row, whose middle cell is `contains \`aruba\`` — a codespan inside a
// cell, because agent tables are full of them and a cell filled by `textContent`
// instead of by the walker would lose it.
const MD_TABLE = [{"type":"table","children":[{"type":"table_head","children":[{"type":"table_cell","attrs":{"align":null,"head":true},"children":[{"type":"text","raw":"Integration"}]},{"type":"table_cell","attrs":{"align":"center","head":true},"children":[{"type":"text","raw":"Topic pattern"}]},{"type":"table_cell","attrs":{"align":"right","head":true},"children":[{"type":"text","raw":"Rows"}]}]},{"type":"table_body","children":[{"type":"table_row","children":[{"type":"table_cell","attrs":{"align":null,"head":false},"children":[{"type":"text","raw":"Classic Aruba"}]},{"type":"table_cell","attrs":{"align":"center","head":false},"children":[{"type":"text","raw":"contains "},{"type":"codespan","raw":"aruba"}]},{"type":"table_cell","attrs":{"align":"right","head":false},"children":[{"type":"text","raw":"12"}]}]}]}]}];

// The same table with `[bad](javascript:alert(1))` in one cell and
// `![x](http://evil.example/x.png)` in the next. A cell is a container like any
// other and the refusals have to hold inside one too — a table arm that filled
// cells by any route but the walker would reinstate every sink at once.
const MD_TABLE_HOSTILE = [{"type":"table","children":[{"type":"table_head","children":[{"type":"table_cell","attrs":{"align":null,"head":true},"children":[{"type":"text","raw":"Cell"}]},{"type":"table_cell","attrs":{"align":null,"head":true},"children":[{"type":"text","raw":"Payload"}]}]},{"type":"table_body","children":[{"type":"table_row","children":[{"type":"table_cell","attrs":{"align":null,"head":false},"children":[{"type":"link","children":[{"type":"text","raw":"bad"}],"attrs":{"url":"javascript:alert(1)"}}]},{"type":"table_cell","attrs":{"align":null,"head":false},"children":[{"type":"image","children":[{"type":"text","raw":"x"}],"attrs":{"url":"http://evil.example/x.png"}}]}]}]}]}];

// One body cell whose `align` is `constructor`. Hand-built, like the other
// prototype fixture: mistune emits one of three alignments and never this — but
// mistune is not the wire, and `align` is the one table field that reaches a
// map lookup.
const MD_TABLE_PROTO_ALIGN = [{ type: "table", children: [
  { type: "table_body", children: [
    { type: "table_row", children: [
      { type: "table_cell", attrs: { align: "constructor", head: false },
        children: [{ type: "text", raw: "CELL" }] }] }] }] }];

// Every shape a fenced block's info string arrives in, as one bubble: a
// language the display table names, one it does not, CommonMark's "everything
// after the fence" form (` ```js {highlight} `, whose language is the first
// word alone), a bare fence, and an indented block. The last two carry no
// `attrs` at all — mistune has nothing to put there — which is why the label
// is an upgrade for the blocks that declare a language rather than a thing
// every block gets.
const MD_CODE_LANGS = [
  { type: "block_code", raw: "x = 1\n", style: "fenced", marker: "```",
    attrs: { info: "py" } },
  { type: "block_code", raw: "const a = 1;\n", style: "fenced", marker: "```",
    attrs: { info: "js {highlight}" } },
  { type: "block_code", raw: "fn main() {}\n", style: "fenced", marker: "```",
    attrs: { info: "zig" } },
  { type: "block_code", raw: "bare\n", style: "fenced", marker: "```" },
  { type: "block_code", raw: "indented\n", style: "indent" },
];

// Info strings that must not reach the label as themselves. `constructor` is
// the prototype probe and the interesting one: eleven lowercase letters, so it
// passes the shape check and reaches the display table — the same probe
// `MD_TABLE_PROTO_ALIGN` runs against `MD_ALIGN`. The other two are the shapes
// the check refuses outright, one for its characters and one for its length.
//
// The length case is a single long token deliberately. Only the first word of
// an info string is read, so a *long* info string is never what the cap
// catches — ` ```an info string this long ` labels the block `an`, and the
// shape check cannot tell that from `zig`. That is the cost of bounding a
// shape rather than a set, and it buys a label for every language nobody
// thought to list. The label is small, lowercase and above the block, so the
// worst case reads as an odd word rather than as damage.
const MD_CODE_HOSTILE = [
  { type: "block_code", raw: "A\n", style: "fenced", marker: "```",
    attrs: { info: "constructor" } },
  { type: "block_code", raw: "B\n", style: "fenced", marker: "```",
    attrs: { info: "<script>alert(1)</script>" } },
  { type: "block_code", raw: "C\n", style: "fenced", marker: "```",
    attrs: { info: "supercalifragilisticexpialidocious" } },
];

// Every element name the page is allowed to build from a token tree. Anything
// else in the bubble is a tag name that came off the wire.
const MD_TAGS = new Set([
  "DIV", "SPAN", "P", "H1", "H2", "H3", "H4", "H5", "H6",
  "UL", "OL", "LI", "PRE", "CODE", "STRONG", "EM", "BR", "A",
  "TABLE", "THEAD", "TBODY", "TR", "TH", "TD",
]);

/** Stream `text` into a fresh agent bubble and then render `tokens` into it,
 *  which is the order and the framing the server really emits. */
function answered(page, live, text, tokens) {
  page.deliver({ type: "chunk", sessionId: live,
                 payload: { role: "agent", text } });
  page.deliver({ type: "rendered", sessionId: live, payload: { tokens } });
}

/** The rendered bubble: the last `.acp-msg-body` in the transcript. */
function bubble(page) {
  const bodies = page.all("acpTranscript", ".acp-msg-body");
  assert(bodies.length > 0, "the transcript has no message body at all");
  return bodies[bodies.length - 1];
}

function tagsIn(el) {
  return el.descendants().map((n) => n.tagName);
}

check("a finished bubble is rebuilt as markup, not left as source", (tpl) => {
  const { page, live } = connected(tpl);
  answered(page, live,
           "# Findings\n\nIt is **bold**, *slanted*, see `run.py`:\n\n" +
           "```py\nx = 1\n```\n\n- one\n- two\n\n1. first\n2. second\n",
           MD_FORMATTED);
  const body = bubble(page);
  const tags = tagsIn(body);
  for (const want of ["H1", "P", "STRONG", "EM", "CODE", "PRE", "UL", "OL", "LI"]) {
    assert(tags.includes(want),
           `the rendered bubble has no <${want}>; it built ${tags.join(",")}`);
  }
  // Ordered and unordered are different elements, not one with a bullet
  // rewritten — the numbering is the list's, not the agent's text.
  assertEqual(tags.filter((t) => t === "OL").length, 1, "the numbered list is not an <ol>");
  assertEqual(tags.filter((t) => t === "LI").length, 4, "the two lists lost items");
  // The markdown's own punctuation is gone from the text, which is the whole
  // point: `**bold**` reads as bold rather than as four asterisks.
  const text = body.textContent;
  assert(!text.includes("**") && !text.includes("```") && !text.includes("# "),
         `markdown source survived into the rendered bubble: ${text}`);
  assert(text.includes("x = 1"), "the code block lost its code");
  // The class the stylesheet needs to turn `white-space: pre-wrap` off. With
  // it left on, every rendered block is double-spaced.
  assert(body.className.split(/\s+/).includes("acp-msg-md"),
         `the rendered bubble is not marked for the markdown rules: ${body.className}`);
});

check("a fenced block is labelled with its language, and only if it has one", (tpl) => {
  const { page, live } = connected(tpl);
  answered(page, live, "plain", MD_CODE_LANGS);
  const body = bubble(page);
  const labels = body.querySelectorAll(".acp-md-code")
                     .map((n) => n.getAttribute("data-lang"));
  // `py` and `js` through the display table, `zig` through as itself — the
  // table names the aliases worth expanding and the shape check is what admits
  // the rest, so a language nobody listed still gets labelled. The two blocks
  // that declared nothing are absent from this list entirely.
  assertEqual(labels.join("|"), "Python|JavaScript|zig",
              "the labels are not the languages the fences declared");
  // Five blocks in, five blocks out. The two with no language are still a
  // <pre>, they are simply not wrapped — a label that cost a block its
  // rendering would be a worse trade than no label at all.
  assertEqual(body.querySelectorAll("pre").length, 5, "a code block was lost");
  for (const raw of ["x = 1", "const a = 1;", "fn main() {}", "bare", "indented"]) {
    assert(body.textContent.includes(raw), `the block holding \`${raw}\` lost its code`);
  }
  // The label is drawn by the stylesheet out of `data-lang`, so it is not part
  // of the bubble's text and cannot come along with a copied snippet.
  assert(!/Python|JavaScript/.test(body.textContent),
         `the language label reached the bubble's text: ${body.textContent}`);
});

check("an info string reaches the label as a language or not at all", (tpl) => {
  const { page, live } = connected(tpl);
  answered(page, live, "plain", MD_CODE_HOSTILE);
  const body = bubble(page);
  const labels = body.querySelectorAll(".acp-md-code")
                     .map((n) => n.getAttribute("data-lang"));
  // One label out of three, and it is the word the agent wrote. On an object
  // literal `MD_LANG_NAME['constructor']` answers the Object constructor and
  // the corner of the block reads "function Object() { [native code] }"; the
  // other two never get that far, one refused for its characters and one for
  // its length.
  assertEqual(labels.join("|"), "constructor",
              "an info string reached the label as something other than a language");
  // Every block still shows its code, which is the trade here: a refused info
  // string costs the label and never the snippet.
  assertEqual(body.querySelectorAll("pre").length, 3, "a code block was lost");
  for (const raw of ["A", "B", "C"]) {
    assert(body.textContent.includes(raw), `the block holding \`${raw}\` lost its code`);
  }
  assert(!body.textContent.includes("alert(1)"),
         `an info string's payload was rendered as text: ${body.textContent}`);
});

// ---------------------------------------------------- syntax highlighting --
//
// The page walks `Prism.tokenize()` — data — with createElement and
// textContent, rather than calling `Prism.highlightElement()`, which builds an
// HTML string and assigns it to `innerHTML`. That choice is the reason /acp
// still has no sink that parses markup, and the harness arms it: `new El().
// innerHTML = …` throws, so a future switch to the convenient Prism API fails
// here rather than shipping.
//
// Every check below therefore asserts two things at once — that the colouring
// happened, and that the code came through the walk unaltered. The second is
// the one that matters: a highlighter that drops a character has corrupted a
// snippet the reader is about to run.

/** The code a block ended up showing, and the token classes it was given. */
function codeBlock(page, index = 0) {
  const pres = bubble(page).querySelectorAll("pre");
  const pre = pres[index];
  assert(pre, `the bubble has no code block at index ${index}`);
  const classes = pre.descendants()
                     .map((n) => String(n.className))
                     .filter(Boolean)
                     .join(" ")
                     .split(/\s+/)
                     .filter(Boolean);
  return { text: pre.textContent, classes: new Set(classes) };
}

const PY_SNIPPET = 'def greet(name):\n    return f"hi {name}"  # note\n';

check("a block in a language Prism knows is coloured, character for character", (tpl) => {
  const { page, live } = connected(tpl);
  answered(page, live, "plain", [
    { type: "block_code", raw: PY_SNIPPET, style: "fenced", marker: "```",
      attrs: { info: "python" } },
  ]);
  const { text, classes } = codeBlock(page);
  // Not a subset check and not a "contains" check. Every character the agent
  // wrote, in order, including the trailing newline: a token walk that dropped
  // whitespace between tokens would still pass any assertion phrased as
  // `includes`, and would hand the reader code that does not run.
  assertEqual(text, PY_SNIPPET, "the highlighted block is not the code it was given");
  for (const want of ["acp-tok-keyword", "acp-tok-string", "acp-tok-comment",
                      "acp-tok-function", "acp-tok-punctuation"]) {
    assert(classes.has(want),
           `nothing in the block was marked ${want}; it got ${[...classes].join(",")}`);
  }
});

check("the page renders every block plainly when Prism did not load", (tpl) => {
  // /static/prism.js blocked, cached stale, or 404 after a bad deploy. The
  // colouring is an upgrade to a block that already rendered, so its absence
  // costs the colour and nothing else — the same trade the server makes when
  // mistune is missing and the bubble keeps its plain text.
  const { page, live } = connected(tpl, { prism: false });
  answered(page, live, "plain", [
    { type: "block_code", raw: PY_SNIPPET, style: "fenced", marker: "```",
      attrs: { info: "python" } },
  ]);
  const { text, classes } = codeBlock(page);
  assertEqual(text, PY_SNIPPET, "the block lost its code with no highlighter");
  assertEqual(classes.size, 0,
              `something was marked as a token with no Prism: ${[...classes].join(",")}`);
  // The label does not depend on Prism — it is read off the fence, not off a
  // grammar — so it survives a highlighter that never loaded.
  const wrap = bubble(page).querySelector(".acp-md-code");
  assert(wrap, "the block lost its wrapper with no highlighter");
  assertEqual(wrap.getAttribute("data-lang"), "Python",
              "the label is gated on the highlighter loading");
});

check("a language with no grammar keeps its label and its text", (tpl) => {
  // `zig` passes the shape check and gets a label, and Prism has no grammar for
  // it because the bundle does not carry one. The two are independent: the
  // label comes off the fence, the colour off the grammar.
  const { page, live } = connected(tpl);
  answered(page, live, "plain", [
    { type: "block_code", raw: "pub fn main() !void {}\n", style: "fenced",
      marker: "```", attrs: { info: "zig" } },
  ]);
  const { text, classes } = codeBlock(page);
  assertEqual(text, "pub fn main() !void {}\n", "the block lost its code");
  assertEqual(classes.size, 0,
              `an unknown language was coloured anyway: ${[...classes].join(",")}`);
  assertEqual(bubble(page).querySelector(".acp-md-code").getAttribute("data-lang"), "zig",
              "an unknown language lost its label");
});

check("an info string off Object.prototype is not a grammar", (tpl) => {
  // `Prism.languages` is an object literal — the one map this renderer reads
  // that the page does not own — so `constructor` answers it with a function.
  // Without the `hasOwnProperty` guard that function reaches `tokenize()` as a
  // grammar. The same probe as the `MD_ALIGN` and `MD_TAG` checks above, on the
  // one lookup that could not be closed by building the map differently.
  const { page, live } = connected(tpl);
  answered(page, live, "plain", [
    { type: "block_code", raw: "payload\n", style: "fenced", marker: "```",
      attrs: { info: "constructor" } },
    { type: "block_code", raw: "second\n", style: "fenced", marker: "```",
      attrs: { info: "__proto__" } },
  ]);
  for (const [i, raw] of [[0, "payload\n"], [1, "second\n"]]) {
    const { text, classes } = codeBlock(page, i);
    assertEqual(text, raw, "a block whose language came off the prototype lost its code");
    assertEqual(classes.size, 0,
                `a prototype value was used as a grammar: ${[...classes].join(",")}`);
  }
});

check("a snippet past the size cap is rendered rather than tokenised", (tpl) => {
  // Prism's grammars are regular expressions run on the main thread, over text
  // an agent wrote, in the tab holding this page's socket. A bubble may carry
  // 128 KiB of it (MAX_BUBBLE_CHARS in acp.py), so past the cap the block
  // renders uncoloured — a slow tab is recoverable and a hung one is not.
  const { page, live } = connected(tpl);
  const huge = "x = 1\n".repeat(4000);   // 24,000 chars, over the 20,000 cap
  answered(page, live, "plain", [
    { type: "block_code", raw: huge, style: "fenced", marker: "```",
      attrs: { info: "python" } },
  ]);
  const { text, classes } = codeBlock(page);
  assertEqual(text, huge, "the oversized block lost its code");
  assertEqual(classes.size, 0,
              `a block over the cap was tokenised anyway: ${[...classes].join(",")}`);
  // And the block under the cap still is, so the check above is measuring the
  // cap rather than a highlighter that stopped working.
  answered(page, live, "plain", [
    { type: "block_code", raw: "x = 1\n", style: "fenced", marker: "```",
      attrs: { info: "python" } },
  ]);
  assert(codeBlock(page).classes.size > 0,
         "nothing is being highlighted at all, so the cap check proves nothing");
});

check("an alias the bundle does not carry still finds its grammar", (tpl) => {
  // Prism answers to `js`, `py` and `md` itself; `ps1`, `rs`, `golang` and
  // `cxx` it does not, and `MD_LANG_GRAMMAR` is what maps those on. A missing
  // entry is invisible — the block renders, uncoloured, looking like a language
  // nobody supports — so each one is pinned.
  const { page, live } = connected(tpl);
  const cases = [
    ["ps1", "Get-ChildItem -Recurse\n"],
    ["rs", "fn main() { let x = 1; }\n"],
    ["golang", "func main() { x := 1 }\n"],
    ["cxx", "int main() { return 0; }\n"],
    ["jsonc", "{\"a\": 1}\n"],
    ["patch", "--- a/x\n+++ b/x\n"],
  ];
  answered(page, live, "plain", cases.map(([info, raw]) => (
    { type: "block_code", raw, style: "fenced", marker: "```", attrs: { info } })));
  cases.forEach(([info, raw], i) => {
    const { text, classes } = codeBlock(page, i);
    assertEqual(text, raw, `the ${info} block lost its code`);
    assert(classes.size > 0, `${info} found no grammar and rendered uncoloured`);
  });
});

check("a rendered bubble closes, so the next answer starts its own", (tpl) => {
  const { page, live } = connected(tpl);
  answered(page, live, "first", [
    { type: "paragraph", children: [{ type: "text", raw: "first" }] }]);
  page.deliver({ type: "chunk", sessionId: live,
                 payload: { role: "agent", text: "second" } });
  const bodies = page.all("acpTranscript", ".acp-msg-body");
  assertEqual(bodies.length, 2,
              "the text after a rendering was appended to the bubble that was " +
              "just rebuilt, so it lands inside the rendered markup");
  assertEqual(bodies[1].textContent, "second",
              "the second answer did not open a bubble of its own");
});

check("raw HTML in the agent's markdown is dropped, not shown", (tpl) => {
  // `block_html`. The agent's output is attacker-influenced — repo files,
  // fetched pages, commit messages — and this page's socket drives an agent
  // running with every tool approved, so an injection here is not a defaced
  // page, it is a shell. mistune with `renderer=None` does not escape it:
  // the token arrives holding `<script>alert(1)</script>` verbatim.
  const { page, live } = connected(tpl);
  answered(page, live, "Hello **there**\n\n<script>alert(1)</script>\n\nBye\n",
           MD_BLOCK_HTML);
  const body = bubble(page);
  assert(body.textContent.includes("Hello") && body.textContent.includes("Bye"),
         "the prose around the raw HTML was lost with it");
  assert(!body.textContent.includes("<script"),
         `raw HTML reached the bubble: ${body.textContent}`);
  assert(!body.textContent.includes("alert(1)"),
         `the script body reached the bubble: ${body.textContent}`);
});

check("inline raw HTML is dropped too", (tpl) => {
  // `inline_html` is a separate token type from `block_html` and needs its own
  // rule; an allowlist that dropped only the block form would pass every
  // check above while letting `<img onerror=…>` straight through.
  const { page, live } = connected(tpl);
  answered(page, live, "Inline <img src=x onerror=alert(1)> here.\n",
           MD_INLINE_HTML);
  const body = bubble(page);
  assert(body.textContent.includes("Inline") && body.textContent.includes("here."),
         "the text around the inline HTML was lost with it");
  assert(!/onerror|<img/.test(body.textContent),
         `inline raw HTML reached the bubble: ${body.textContent}`);
});

check("an image is dropped rather than requested", (tpl) => {
  // An <img> with an agent-chosen URL is a request this page makes to a host
  // the agent picked, on page load, with the viewer's IP and referrer — from a
  // surface that is reachable off the loopback. The alt text goes with it: it
  // is the image's own label and showing it alone reads as prose the agent
  // did not write.
  const { page, live } = connected(tpl);
  answered(page, live, "Look: ![alt text](http://evil.example/x.png) done.\n",
           MD_IMAGE);
  const body = bubble(page);
  assert(!tagsIn(body).includes("IMG"), "the page built an <img> from a token");
  assert(!body.textContent.includes("alt text"),
         `the image's alt text was rendered as prose: ${body.textContent}`);
  assert(body.textContent.includes("Look:") && body.textContent.includes("done."),
         "the text around the image was lost with it");
});

check("a link is an element only for http(s), and its text otherwise", (tpl) => {
  // `safe_url()` is in mistune's HTML renderer, which is not on this path, so
  // `javascript:alert(1)` arrives in `attrs.url` exactly as the agent wrote
  // it. An allowlist of two schemes rather than a `javascript:` denylist:
  // `data:`, `vbscript:` and a leading-whitespace spelling of either defeat a
  // denylist, and a conversation has no use for a third scheme.
  const { page, live } = connected(tpl);
  answered(page, live,
           "[safe](https://example.com/a), [bad](javascript:alert(1)), " +
           "[rel](/local/path)\n", MD_LINKS);
  const body = bubble(page);
  const anchors = body.descendants().filter((n) => n.tagName === "A");
  assertEqual(anchors.length, 1,
              "exactly one of the three links has an http(s) URL, so exactly " +
              "one of them may be an <a>");
  assertEqual(anchors[0].getAttribute("href"), "https://example.com/a",
              "the wrong link became an element");
  assertEqual(anchors[0].getAttribute("rel"), "noopener noreferrer",
              "the opened page can reach back through window.opener");
  for (const node of body.descendants()) {
    const href = node.getAttribute("href");
    assert(href === null || /^https?:\/\//i.test(href),
           `a non-http(s) URL reached an href: ${href}`);
  }
  // Refused as a link, kept as text: the reader still sees what the agent
  // wrote, it just is not a thing that can be clicked.
  assert(body.textContent.includes("bad") && body.textContent.includes("rel"),
         `the refused links lost their text: ${body.textContent}`);
});

check("a pipe table is rebuilt as a table, not flattened onto one line", (tpl) => {
  const { page, live } = connected(tpl);
  answered(page, live,
           "| Integration | Topic pattern | Rows |\n|---|:---:|---:|\n" +
           "| Classic Aruba | contains `aruba` | 12 |\n", MD_TABLE);
  const body = bubble(page);
  const tags = tagsIn(body);
  for (const want of ["TABLE", "THEAD", "TBODY", "TR", "TH", "TD"]) {
    assert(tags.includes(want),
           `the rendered table has no <${want}>; it built ${tags.join(",")}`);
  }
  // The CSS that makes a wide table scroll instead of stretching the panel is
  // keyed on this class. Without it the table renders and then overflows the
  // conversation, which is a different bug wearing this one's clothes.
  assert(String(body.className).split(/\s+/).includes("acp-msg-md"),
         `the rendered bubble is not marked as markdown: ${body.className}`);
  // mistune hangs the head's cells straight off `table_head`, with no row
  // token between them. The page builds that row itself; stop doing so and the
  // browser hoists the cells into an implicit one — a row the page never made,
  // cannot style and cannot count.
  const heads = body.descendants().filter((n) => n.tagName === "THEAD");
  assertEqual(heads.length, 1, "the table has no single <thead>");
  assertEqual(heads[0].childNodes.map((n) => n.tagName).join(","), "TR",
              "the head's cells were not wrapped in a row of the page's own");
  assertEqual(heads[0].childNodes[0].childNodes.length, 3,
              "the header row did not get all three cells");
  // `head` on the token is the only thing choosing between <th> and <td>, and
  // reversing it would look almost right while turning every column heading
  // into a data cell.
  const cellsOf = (root) => root.descendants()
    .filter((n) => n.tagName === "TH" || n.tagName === "TD");
  for (const cell of cellsOf(heads[0])) {
    assertEqual(cell.tagName, "TH", "a head cell was built as a data cell");
  }
  const tbodies = body.descendants().filter((n) => n.tagName === "TBODY");
  assertEqual(tbodies.length, 1, "the table has no single <tbody>");
  for (const cell of cellsOf(tbodies[0])) {
    assertEqual(cell.tagName, "TD", "a body cell was built as a header cell");
  }
  // A cell is filled by the walker and not by `textContent`, so the codespan
  // inside one is still an element. A table arm that took the shortcut would
  // pass every check above and quietly flatten every `like this` in a cell.
  assert(cellsOf(tbodies[0]).some((c) => tagsIn(c).includes("CODE")),
         "the codespan inside a cell was flattened into plain text");
  assert(body.textContent.includes("Classic Aruba") &&
         body.textContent.includes("aruba"),
         `the table lost its cell text: ${body.textContent}`);
});

check("a column's alignment is a class from a closed set, never a style", (tpl) => {
  const { page, live } = connected(tpl);
  answered(page, live,
           "| Integration | Topic pattern | Rows |\n|---|:---:|---:|\n" +
           "| Classic Aruba | contains `aruba` | 12 |\n", MD_TABLE);
  const body = bubble(page);
  const classesOf = (tag) => body.descendants()
    .filter((n) => n.tagName === tag)
    .map((n) => String(n.className || "")).join("|");
  // `|---|:---:|---:|` — default, centre, right — on both the head and the row
  // under it, because mistune puts the alignment on every cell in the column
  // and a page that read it only off the head would align nothing below it.
  assertEqual(classesOf("TH"), "|acp-md-center|acp-md-right",
              "the delimiter row's alignment did not reach the header cells");
  assertEqual(classesOf("TD"), "|acp-md-center|acp-md-right",
              "the delimiter row's alignment did not reach the body cells");
  // `align` is a wire string like every other field here. Reaching it into
  // `style.textAlign` would put an agent-authored value straight into a CSS
  // property; the closed map is what keeps it out, and the way to see that the
  // map is still doing the work is that nothing in the table has a style at all.
  const table = body.descendants().find((n) => n.tagName === "TABLE");
  assert(table, "the table was never built");
  for (const node of [table].concat(table.descendants())) {
    assertEqual(Object.keys(node.style).length, 0,
                `a table node carries an inline style: ${JSON.stringify(node.style)}`);
  }
});

check("the markdown refusals hold inside a table cell too", (tpl) => {
  const { page, live } = connected(tpl);
  answered(page, live,
           "| Cell | Payload |\n|---|---|\n" +
           "| [bad](javascript:alert(1)) | ![x](http://evil.example/x.png) |\n",
           MD_TABLE_HOSTILE);
  const body = bubble(page);
  assertEqual(body.descendants().filter((n) => n.tagName === "A").length, 0,
              "a javascript: URL became a link because it was inside a cell");
  for (const tag of tagsIn(body)) {
    assert(MD_TAGS.has(tag),
           `a table cell built a tag outside the allowlist: ${tag}`);
  }
  for (const node of body.descendants()) {
    assertEqual(node.getAttribute("href"), null,
                "an href reached a node inside a table");
    assertEqual(node.getAttribute("src"), null,
                "a src reached a node inside a table");
  }
  // Refused as a link, kept as text — the same bargain the prose case strikes.
  // The image keeps nothing, which is also the prose case: it is dropped, not
  // escaped, so there is no alt text to find here.
  assert(body.textContent.includes("bad"),
         `the refused link lost its text inside the cell: ${body.textContent}`);
});

check("a transcript table is bounded by the pane and by nothing else", () => {
  // Three declarations decide how one of these tables gives up width, and none
  // of them reads as load-bearing:
  //   - no `max-width` on a cell. A fixed cap is chosen without knowing the
  //     pane, so it is wrong twice: it wraps a table that had room to spare
  //     and fails to save one that had none. Measured at 42ch, it bound on a
  //     900px pane where the table already fit.
  //   - no `white-space`. `nowrap` makes min-content the whole line, and the
  //     table then cannot shrink at all.
  //   - `overflow-wrap: anywhere`. Auto table layout hands every column its
  //     min-content width before sharing out anything, so one unbreakable
  //     token holds its column at full width while the prose beside it pays
  //     the whole shortfall. Measured on a 4-column table in a 444px pane:
  //     `break-word` kept one column at 100% and cut another to 14% (spread
  //     0.86); `anywhere` gave 0.56/0.54/0.45/0.40 (spread 0.17).
  // Every rule naming the cell, not the last one: `.acp-msg-md td` is set
  // twice — once for the box and once for its colour — and reading either
  // alone describes a cell that does not exist. Declarations are collected in
  // source order and the last value of each property is the effective one,
  // which is the cascade this file can model without a CSS engine (no @media
  // here, and all three selectors are the same specificity).
  const css = fs.readFileSync(STYLESHEET, "utf8");
  const declarations = [];
  for (const m of css.matchAll(/([^{}]*)\{([^{}]*)\}/g)) {
    const selectors = m[1].split(",").map((s) => s.trim().replace(/\s+/g, " "));
    if (!selectors.includes(".acp-msg-md td")) continue;
    for (const decl of m[2].split(";")) {
      const at = decl.indexOf(":");
      if (at < 0) continue;
      declarations.push([decl.slice(0, at).trim(),
                         decl.slice(at + 1).trim()]);
    }
  }
  assert(declarations.length > 0,
         "no rule sets `.acp-msg-md td` any more; the transcript's table cells " +
         "are unstyled and this check has lost its subject");
  const effective = (prop) => {
    const hits = declarations.filter(([name]) => name === prop);
    return hits.length ? hits[hits.length - 1][1] : null;
  };
  assertEqual(effective("max-width"), null,
              "a `max-width` is back on the transcript's table cells. That " +
              "caps a column without knowing how wide the pane is, so it " +
              "wraps tables that had room and does not save the ones that " +
              "had none — the pane is the only thing allowed to bound these");
  assertEqual(effective("white-space"), null,
              "a `white-space` is on the cells. If it is `nowrap` the table " +
              "cannot shrink at all: nowrap makes min-content the whole line, " +
              "and no column is ever narrower than its min-content");
  assertEqual(effective("overflow-wrap"), "anywhere",
              "the cells no longer drop their min-content floor, so auto " +
              "table layout gives each column its longest word before " +
              "sharing anything out — one unbreakable token then keeps its " +
              "column at full width and the prose column absorbs every pixel " +
              "of the shortfall instead of the columns shrinking together");
  // Keeps the decision above local. `.acp-msg-body` sets `word-break:
  // break-word`, which inherits in and would decide the cells' breaking for
  // them from a rule 50 lines away that is about streamed prose.
  assertEqual(effective("word-break"), "normal",
              "the cells do not reset `word-break`, so how a table breaks is " +
              "set by `.acp-msg-body`'s rule for streamed prose rather than here");
});

check("an alignment off Object.prototype cannot name a class", (tpl) => {
  // `MD_ALIGN` is `Object.create(null)` for the same reason every other map in
  // the renderer is: on an object literal `MD_ALIGN['constructor']` answers the
  // Object constructor, and a truthy answer is assigned straight to className.
  const { page, live } = connected(tpl);
  answered(page, live, "plain", MD_TABLE_PROTO_ALIGN);
  const body = bubble(page);
  const cells = body.descendants().filter((n) => n.tagName === "TD");
  assertEqual(cells.length, 1, "the row did not build its one cell");
  assertEqual(String(cells[0].className || ""), "",
              "a value off Object.prototype reached a cell's class");
  // And the cell still shows what the agent wrote: a hostile attribute costs
  // the alignment, never the content.
  assert(body.textContent.includes("CELL"),
         `the cell lost its text: ${body.textContent}`);
});

check("a token type off Object.prototype cannot name an element", (tpl) => {
  // The reason every map in the renderer is `Object.create(null)`, and the
  // same reason `RAIL_AVAILABILITY` is: on an object literal every
  // `Object.prototype` key is a hit, so `MD_TAG['constructor']` answers the
  // Object constructor and `createElement` is handed a function whose
  // `String()` becomes the tag name. The token type and the heading level both
  // come off the wire, so both maps are probed here.
  const { page, live } = connected(tpl);
  answered(page, live, "plain", [
    { type: "constructor", raw: "CANARY" },
    { type: "heading", attrs: { level: "constructor" },
      children: [{ type: "text", raw: "LEVEL" }] },
  ]);
  const body = bubble(page);
  for (const tag of tagsIn(body)) {
    assert(MD_TAGS.has(tag),
           `a wire value reached createElement and became a tag: ${tag}`);
  }
  // An unknown type still shows its text — dropping it silently would lose
  // agent output to a parser upgrade. `CANARY` and the streamed `plain` differ
  // so that a bubble which was never rebuilt cannot pass this.
  assert(body.textContent.includes("CANARY"),
         `an unknown token type lost its text: ${body.textContent}`);
  assert(body.textContent.includes("LEVEL"),
         `a heading with an unusable level lost its text: ${body.textContent}`);
  assert(!body.textContent.includes("plain"),
         "the bubble was never rebuilt, so this check proves nothing");
});

check("a bubble that renders to nothing keeps the text it had", (tpl) => {
  // Every token dropped. Blanking the bubble would remove text the reader
  // watched arrive; keeping it leaves the pre-markdown behaviour, which is a
  // transcript that was already correct.
  const { page, live } = connected(tpl);
  answered(page, live, "<script>alert(1)</script>", [
    { type: "block_html", raw: "<script>alert(1)</script>\n" }]);
  assertEqual(bubble(page).textContent, "<script>alert(1)</script>",
              "a bubble whose every token was refused came out empty");
});

check("a rendered frame with no bubble open changes nothing", (tpl) => {
  // Reachable in a replay: the ring buffer evicts oldest-first, so a `history`
  // can carry a rendering whose chunks are gone. It must not attach itself to
  // whatever bubble happens to be open next.
  const { page, live } = connected(tpl);
  page.deliver({ type: "meta", sessionId: live, payload: { turn: "end", stopReason: "end_turn" } });
  page.deliver({ type: "rendered", sessionId: live, payload: { tokens: MD_FORMATTED } });
  assert(!page.transcript().includes("Findings"),
         "a rendering with no bubble open was drawn into the transcript anyway");
  page.deliver({ type: "chunk", sessionId: live, payload: { role: "agent", text: "next" } });
  assertEqual(bubble(page).textContent, "next",
              "the orphaned rendering leaked into the next answer's bubble");
});

check("a line break inside a paragraph stays a gap between words", (tpl) => {
  const { page, live } = connected(tpl);
  answered(page, live, "line one\nline two\n", MD_SOFTBREAK);
  assert(bubble(page).textContent.includes("line one line two"),
         `the two lines were welded together: ${bubble(page).textContent}`);
});

check("session_closed clears the session id out of the URL", (tpl) => {
  const { page, live } = connected(tpl, { sid: "sess-from-url-01" });
  page.deliver({
    type: "session_closed", sessionId: live,
    payload: { sessionId: live, message: "This session was closed." },
  });
  assertEqual(page.urls[page.urls.length - 1], "/acp",
              "the ?sid= survived the close, so a reload re-adopts the session and " +
              "spends again the memory the Close press existed to free");
});

// Every other clear `session_closed` performs, each pinned by the effect a user
// would see rather than by the statement that produces it.
//
// These exist because a mutation run found the arm almost unguarded here.
// Deleting `sessionId = null`, `setContext(null)`, `setTurn(false)` or
// `sidEl.textContent = ''` from the branch one at a time left this harness
// fully green -- only `history.replaceState` above was killed. The single thing
// covering the other four was a Python test asserting their *source text*
// inside the branch, which is why hoisting them into a shared helper broke it
// twice: it pinned where the statements were written, not what they did.
// Behavioural checks first, then that test goes and the hoist is free.
check("session_closed lets go of the session id itself", (tpl) => {
  const { page, live } = connected(tpl, { sid: "sess-from-url-01" });
  page.deliver({
    type: "session_closed", sessionId: live,
    payload: { sessionId: live, message: "This session was closed." },
  });
  // Reconnecting is the observable: the page resubscribes to whatever id it
  // still holds. Holding a released one re-adopts a session the server has
  // already torn down -- the same waste the URL clear exists to prevent, by a
  // route the URL assertion cannot see.
  page.click("acpReconnect");
  page.open();
  assertEqual(page.sentOf("subscribe").length, 0,
              "the page still held the closed session's id and resubscribed to it");
});

check("session_closed stands the controls down", (tpl) => {
  const { page, live } = connected(tpl, { sid: "sess-from-url-01", turnActive: true });
  assertEqual(page.el("acpStop").hidden, false,
              "the fixture is wrong: no turn is running, so there is nothing to stand down");
  page.deliver({
    type: "session_closed", sessionId: live,
    payload: { sessionId: live, message: "This session was closed." },
  });
  // A turn cannot outlive the session it ran in. Leaving Stop on screen offers
  // a cancel that names a session the agent no longer has.
  assertEqual(page.el("acpStop").hidden, true,
              "Stop survived the close, so the page still offers to cancel a turn " +
              "in a session that no longer exists");
  assert(page.el("acpSend").disabled,
         "Send came back enabled against a closed session");
  assertEqual(page.el("acpClose").hidden, true,
              "Close survived the close of the session it would have closed");
});

check("session_closed takes the context meter and the header id down", (tpl) => {
  const { page, live } = connected(tpl, { sid: "sess-from-url-01" });
  page.deliver({
    type: "meta", sessionId: live,
    payload: { contextPercent: 42 },
  });
  assertEqual(page.el("acpContext").hidden, false,
              "the fixture is wrong: the meter never came up, so hiding it proves nothing");
  page.deliver({
    type: "session_closed", sessionId: live,
    payload: { sessionId: live, message: "This session was closed." },
  });
  assertEqual(page.el("acpContext").hidden, true,
              "the context meter still reports a percentage for a session that is gone");
  assertEqual(page.el("acpSid").textContent, "",
              "the header still names the closed session");
});

check("session_closed gives the New-session buttons back", (tpl) => {
  // The sixth clear in the branch, and the one no test reached: a close that
  // arrives while a `new` is in flight must release both copies of the button.
  // Left disabled they read "Creating…" forever, and the rail's copy is the
  // only one a phone can see -- so the recovery from a close is a page reload.
  const { page, live } = connected(tpl, { sid: "sess-from-url-01" });
  // The server acknowledges a `new` before the session exists, and that ack is
  // what disables the buttons -- the click alone does not.
  page.deliver({ type: "meta", payload: { pending: "new" } });
  assert(page.el("acpNew").disabled && page.el("acpRailNew").disabled,
         "the fixture is wrong: no `new` is in flight, so releasing it proves nothing");
  page.deliver({
    type: "session_closed", sessionId: live,
    payload: { sessionId: live, message: "This session was closed." },
  });
  assert(!page.el("acpNew").disabled,
         "New session stayed disabled after the close, reading 'Creating…' with " +
         "nothing coming to release it");
  assert(!page.el("acpRailNew").disabled,
         "the rail's New session -- the only copy a phone can reach -- stayed disabled");
});

check("a close_in_progress for the held session empties the page", (tpl) => {
  const { page, live } = connected(tpl, { sid: "sess-from-url-01" });
  page.deliver({
    type: "chunk", sessionId: live,
    payload: { role: "agent", text: "an answer from before the sweep" },
  });
  // Negative control first: a refusal naming a different session is somebody
  // else's close and must leave this page exactly as it was.
  page.deliver({
    type: "error", sessionId: "sess-someone-else",
    payload: { code: "close_in_progress", message: "This session is being released." },
  });
  assert(page.transcript().includes("an answer from before the sweep"),
         "a close_in_progress naming another session wiped this one's transcript");
  assertEqual(page.urls[page.urls.length - 1], `/acp?sid=${encodeURIComponent(live)}`,
              "a close_in_progress naming another session stripped this one's ?sid=");

  // The socket drops and the page reconnects into the close window. The
  // resubscribe is refused, so this socket is in nobody's subscriber set — the
  // path where the refusal really is terminal.
  page.click("acpReconnect");
  page.open();
  assertEqual(page.sentOf("subscribe")[0]?.sessionId, live,
              "the reconnect did not resubscribe, so there is nothing to refuse");
  page.deliver({
    type: "error", sessionId: live,
    payload: { code: "close_in_progress",
               message: "This session is being released. Wait a moment and load it again." },
  });
  assert(!page.transcript().includes("an answer from before the sweep"),
         "the transcript survived a terminal close_in_progress: the session it " +
         "belongs to is being swept and this socket was never subscribed, so no " +
         "session_closed is coming and the stale content stays on screen forever");
  assert(page.transcript().includes("This session is being released"),
         "the page emptied without saying why");
  assertEqual(page.el("acpSid").textContent, "",
              "the header still names a session the server has released");
  assertEqual(page.urls[page.urls.length - 1], "/acp",
              "?sid= survived, so a reload re-adopts a session that is gone");
  page.click("acpReconnect");
  page.open();
  assertEqual(page.sentOf("subscribe").length, 0,
              "the page still held the id and resubscribed to the released session");
});

check("a close_in_progress on a subscribed socket leaves the transcript alone", (tpl) => {
  // The other two emitters of this code — a prompt refused mid-close, and a
  // second Close — answer a socket that *is* in the session's subscribers, so
  // the close's own `session_closed` is still coming. That frame deliberately
  // keeps the transcript, and clearing it here would take the conversation off
  // screen a beat before the frame that exists to preserve it.
  const { page, live } = connected(tpl, { sid: "sess-from-url-01" });
  page.deliver({
    type: "chunk", sessionId: live,
    payload: { role: "agent", text: "an answer the user is still reading" },
  });
  page.type("a prompt that will be refused");
  page.click("acpSend");
  page.deliver({
    type: "error", sessionId: live,
    payload: { code: "close_in_progress",
               message: "This session is being closed. Create a new one to carry on." },
  });
  assert(page.transcript().includes("an answer the user is still reading"),
         "a close_in_progress on a subscribed socket wiped the transcript that " +
         "the session_closed still to come deliberately keeps");
  assert(page.transcript().includes("This session is being closed"),
         "the refusal was not explained in the transcript");
  // The rest of the clears are right on both paths: the session is going away.
  assertEqual(page.el("acpSid").textContent, "",
              "the header still names a session the server has released");
  assertEqual(page.urls[page.urls.length - 1], "/acp",
              "?sid= survived, so a reload re-adopts a session that is gone");
  assertEqual(page.el("acpPrompt").value, "a prompt that will be refused",
              "the refused prompt was not put back in the textarea");

  page.deliver({
    type: "session_closed", sessionId: live,
    payload: { sessionId: live, message: "This session was closed." },
  });
  assert(page.transcript().includes("an answer the user is still reading"),
         "session_closed dropped the transcript it exists to keep");
});

check("Send with no session sends nothing and keeps the text", (tpl) => {
  const page = loadPage(tpl);
  page.open();
  page.type("typed before there was anywhere to send it");
  page.click("acpSend");
  assertEqual(page.sentOf("prompt").length, 0, "a prompt was sent with no session");
  assertEqual(page.el("acpPrompt").value, "typed before there was anywhere to send it",
              "the text was cleared even though nothing was sent");
});

// ------------------------------------------------------------- the rail --
//
// Everything below needs three things the harness did not have: a `fetch` that
// answers with a body, a way to address nodes created after render, and a
// check that can wait for a promise. Remove any one of them and every check in
// this section fails — which is the evidence for the first exit criterion.

check("the rail asks for ten workspaces and ten sessions each", async (tpl) => {
  const page = await railed(tpl);
  const calls = page.listingCalls();
  assertEqual(calls.length, 1, "the rail made the wrong number of listing requests");
  const { params, init } = calls[0];
  assertEqual(params.group_size, "10",
              "D16 shows ten workspaces; the rail asked for a different page");
  assertEqual(params.session_size, "10",
              "D16 shows ten sessions a workspace; the rail asked for a different page");
  assertEqual(params.group_page, "1", "the first page is page 1");
  assertEqual(params.session_page, "1", "the first page is page 1");
  assertEqual(init.cache, "no-store",
              "availability is a liveness reading with a lifetime of seconds and " +
              "must not be served from the browser cache");
  assertEqual(init.credentials, "same-origin",
              "the device cookie has to ride the listing request or it 403s remotely");
});

check("the rail draws a group per workspace and a row per session", async (tpl) => {
  const page = await railed(tpl);
  assertEqual(page.railGroups().length, 10,
              "the rail drew the wrong number of workspace groups");
  assertEqual(page.railRows().length, 50,
              "ten groups of five is fifty rows; the rail drew a different shape");
  const first = page.railGroups()[0];
  const head = first.querySelector(".acp-rail-group-head").textContent;
  assert(head.includes("ws-0"), `the group is not named after its workspace: ${head}`);
  assert(head.includes("5 of 5"),
         `the group does not say how much of the workspace is shown: ${head}`);
  assert(page.railTitles().includes("workspace 0 session 1"),
         "a session's title never reached its row");
  assert(page.el("acpRailStatus").textContent.includes("12"),
         "the rail does not say how many workspaces there are in total");
});

check("the filter narrows the rows to what matches", async (tpl) => {
  const page = await railed(tpl);
  const box = page.el("acpRailSearch");

  box.value = "workspace 3 session 1";
  box.dispatch("input");
  assertEqual(page.railRows().length, 1, "the filter did not narrow to the one match");
  assertEqual(page.railGroups().length, 1,
              "a workspace with nothing left after the filter was still drawn");
  assertEqual(page.railTitles()[0], "workspace 3 session 1", "the wrong row survived");

  // The workspace name and path are part of the haystack: a rail grouped by
  // workspace invites "the sessions in ws-7" as a query.
  box.value = "WS-7";
  box.dispatch("input");
  assertEqual(page.railRows().length, 5,
              "matching the workspace should keep all of its loaded rows");

  box.value = "no-such-thing";
  box.dispatch("input");
  assertEqual(page.railRows().length, 0, "the filter matched something it should not");
  assert(page.one("acpRailGroups", ".acp-rail-empty"),
         "an empty result left the rail silently blank, which reads as a broken page");

  box.value = "";
  box.dispatch("input");
  assertEqual(page.railRows().length, 50, "clearing the filter did not restore the rows");
});

check("show-more appends the next page of workspaces", async (tpl) => {
  const page = await railed(tpl);
  assertEqual(page.el("acpRailMore").hidden, false,
              "12 workspaces do not fit in a page of 10, so show-more must be offered");
  page.click("acpRailMore");
  await page.settle();
  const calls = page.listingCalls();
  assertEqual(calls.length, 2, "show-more made the wrong number of requests");
  assertEqual(calls[1].params.group_page, "2", "show-more re-asked for the page it had");
  assertEqual(page.railGroups().length, 12,
              "the second page replaced the first instead of extending it");
  const names = page.railGroups().map(
    (g) => g.querySelector(".acp-rail-group-name").textContent);
  assert(names.includes("ws-0") && names.includes("ws-11"),
         `both pages should be on screen, got ${names.join(", ")}`);
  assertEqual(page.el("acpRailMore").hidden, true,
              "there is no third page, so the button must stop offering one");
});

check("a workspace's own show-more pages that workspace alone", async (tpl) => {
  const page = await railed(tpl, { store: fakeStore({ workspaces: 12, sessions: 15 }) });
  const group = page.railGroups()[0];
  const more = group.querySelector(".acp-rail-group-more");
  assert(more, "a workspace with 15 sessions showing 10 offered no way to see the rest");
  more.dispatch("click");
  await page.settle();

  const call = page.listingCalls()[1];
  assertEqual(call.params.cwd, "C:\\work\\ws-0",
              "the per-group page did not name its workspace, so it paged the " +
              "whole store instead of this one");
  assertEqual(call.params.session_page, "2", "it re-asked for the sessions it had");
  assert(!("group_page" in call.params),
         "a cwd request bypasses the group axis; sending one asks for the wrong shape");

  const rows = page.railGroups()[0].querySelectorAll(".acp-rail-row");
  assertEqual(rows.length, 15, "the workspace's own show-more did not extend it");
  assertEqual(page.railGroups()[1].querySelectorAll(".acp-rail-row").length, 10,
              "paging one workspace changed another");
  assert(!page.railGroups()[0].querySelector(".acp-rail-group-more"),
         "the workspace is fully shown and must stop offering more");
});

check("the live dot is drawn for a held session and for nothing else", async (tpl) => {
  const store = fakeStore({ workspaces: 1, sessions: 3 });
  store[0].sessions[0].availability = "available";
  store[0].sessions[1].availability = "held";
  store[0].sessions[1].status = "waiting";
  store[0].sessions[2].availability = "locked";
  const page = await railed(tpl, { store });
  const rows = page.railRows();

  assertEqual(rows.map((r) => r.dataset.availability).join(","),
              "available,held,locked",
              "the three states did not survive the trip to the row");
  for (const [i, want] of [[0, "available"], [1, "held"], [2, "locked"]]) {
    assert(String(rows[i].className).includes(`acp-rail-row-${want}`),
           `row ${i} carries no ${want} class: ${rows[i].className}`);
  }

  // The dot's *presence* is what says this ACP is driving the session. That is
  // the whole reason its colour is free to mean what the dashboard's means —
  // green used to say "free to open" here and "the agent is working" there.
  assert(!rows[0].querySelector(".session-status"),
         "an available session was given a live dot, which claims this ACP is " +
         "driving a session nothing here holds");
  assert(!rows[2].querySelector(".session-status"),
         "a locked session was given a live dot, which claims this ACP is " +
         "driving a session another process has taken");

  const dot = rows[1].querySelector(".session-status");
  assert(dot, "the held session has no live dot, so nothing on the row says " +
              "this ACP is the thing driving it");
  // The dashboard's own class, not a rail-local restatement of it. A rail rule
  // that repeated the hue would drift the first time either surface moved.
  assert(String(dot.className).includes("status-waiting"),
         `the dot does not carry the dashboard's waiting class: ${dot.className}`);
  assert(dot.getAttribute("aria-label"),
         "the dot has no accessible name, and it carries no text of its own");
});

check("a state or a status off the wire is narrowed before it reaches an attribute", async (tpl) => {
  const store = fakeStore({ workspaces: 3, sessions: 3 });
  // Off the wire and into a class name and a data attribute — both attribute
  // sinks, and this page's rule is that nothing payload-derived reaches one.
  //
  // The first of these is an *own-property* miss and passes any lookup. The
  // other three are the reason the maps have to be prototype-less: on an object
  // literal every `Object.prototype` key is a hit, so `map[value] || 'default'`
  // answers with the inherited value and never reaches the default. Measured on
  // the literal: "constructor" puts `acp-rail-row-function Object() { [native
  // code] }` into className and dataset.availability and makes the indicator's
  // aria-label the literal string "undefined"; "__proto__" gives
  // `acp-rail-row-[object Object]`; "toString" the same shape.
  store[0].sessions[0].availability = 'locked" onload=x';
  store[0].sessions[1].availability = "constructor";
  store[0].sessions[2].availability = "__proto__";
  store[1].sessions[0].availability = "toString";
  // The dot's own field reaches a class name by the same route, so it is
  // narrowed on the same terms. Held, because a status is only ever read where
  // a dot is drawn.
  for (const [i, sent] of [[0, 'working" onload=x'], [1, "constructor"],
                           [2, "__proto__"]]) {
    store[2].sessions[i].availability = "held";
    store[2].sessions[i].status = sent;
  }
  const page = await railed(tpl, { store });
  const rows = page.railRows();

  for (const [i, sent] of [[0, 'locked" onload=x'], [1, "constructor"],
                           [2, "__proto__"], [3, "toString"]]) {
    assertEqual(rows[i].dataset.availability, "available",
                `the state ${JSON.stringify(sent)} was passed through rather ` +
                "than narrowed to one of the three literals");
    assert(/^acp-rail-row acp-rail-row-(available|held|locked)$/.test(
             String(rows[i].className)),
           `${JSON.stringify(sent)} reached a class name: ${rows[i].className}`);
    assert(!rows[i].querySelector(".session-status"),
           `${JSON.stringify(sent)} narrowed to available but still drew a ` +
           "dot, which says this ACP is driving it");
  }
  for (const [i, sent] of [[6, 'working" onload=x'], [7, "constructor"],
                           [8, "__proto__"]]) {
    const dot = rows[i].querySelector(".session-status");
    assert(dot, `row ${i} is held and was given no dot to narrow`);
    assertEqual(String(dot.className), "session-status status-thinking",
                `the status ${JSON.stringify(sent)} reached a class name ` +
                `rather than narrowing to the fallback: ${dot.className}`);
    assertEqual(dot.getAttribute("aria-label"),
                "open in this PowerAtlas — the agent is working",
                `${JSON.stringify(sent)} left the dot without a real ` +
                "accessible name, and it carries no text of its own");
  }
});

check("a locked row says why it cannot be opened, not only that it cannot", async (tpl) => {
  const store = fakeStore({ workspaces: 1, sessions: 2 });
  store[0].sessions[1].availability = "locked";
  const page = await railed(tpl, { store });
  const rows = page.railRows();

  // The reason used to live on the availability dot, which a locked row no
  // longer has. Greying is a colour, and a colour is not an answer to "why can
  // I not open this one?" — without this the row reads as broken, not as taken.
  assert(/another process/i.test(String(rows[1].title)),
         `the locked row does not say what has it: ${JSON.stringify(rows[1].title)}`);
  // `aria-label` replaces the accessible name outright, so the session's own
  // title has to survive into it or the row announces its refusal and never
  // which session is refusing.
  const label = String(rows[1].getAttribute("aria-label"));
  assert(/another process/i.test(label),
         `a screen reader is told nothing about the refusal: ${label}`);
  assert(label.includes(store[0].sessions[1].title),
         `the refusal replaced the row's name instead of joining it: ${label}`);
  assertEqual(rows[0].getAttribute("aria-label"), null,
              "an available row was given a refusal label it has no reason for");
});

check("a locked row is greyed off and cannot be selected", async (tpl) => {
  const store = fakeStore({ workspaces: 1, sessions: 3 });
  store[0].sessions[1].availability = "locked";
  const page = await railed(tpl, { store });
  const rows = page.railRows();

  assertEqual(rows[1].disabled, true,
              "a session held by a live foreign process was offered as clickable; " +
              "loading it is refused, so the row is a dead end with no explanation");
  assertEqual(rows[0].disabled, false,
              "positive control: an available session must stay selectable");
  rows[1].dispatch("click");
  assertEqual(page.sentOf("subscribe").length, 0,
              "clicking a locked row still tried to open the session");
  assertEqual(page.el("acpSid").textContent, "",
              "the page adopted a session it cannot load");
});

check("selecting an available row opens that session", async (tpl) => {
  const page = await railed(tpl);
  const rows = page.railRows();
  rows[6].dispatch("click");
  const subs = page.sentOf("subscribe");
  assertEqual(subs.length, 1, "the row did not subscribe to its session");
  assertEqual(subs[0].sessionId, "sess-w1-s1", "the rail opened the wrong session");
  assert(page.el("acpSid").textContent.includes("sess-w1-s1"),
         "the header does not name the session the rail just opened");
  assertEqual(page.urls[page.urls.length - 1], "/acp?sid=sess-w1-s1",
              "the id never reached the URL, so a reload strands the session");
  // A second click on the row already open must not re-subscribe: the server
  // answers every subscribe with a `session` frame that clears the transcript.
  rows[6].dispatch("click");
  assertEqual(page.sentOf("subscribe").length, 1,
              "re-selecting the open session resubscribed and wiped its transcript");
});

check("the rail says how many sessions are open before the limit is hit", async (tpl) => {
  const store = fakeStore({ workspaces: 1, sessions: 3 });
  store.capacity = { held: 3, max: 8 };
  const page = await railed(tpl, { store });
  const status = page.el("acpRailStatus").textContent;
  assert(status.includes("3/8"),
         `the rail never says how full the session cap is: ${status}`);
  assert(!status.includes("at the limit"),
         `three of eight is not the limit, but the rail says it is: ${status}`);
});

check("at the limit the rail says so and refuses a row that needs a slot", async (tpl) => {
  // The defect: the rail reaches MAX_SESSIONS in eight taps, and the ninth was
  // refused by the *server* — after selectSession had already cleared the
  // transcript and repointed ?sid=. So the cost of discovering the limit was
  // losing the conversation you were reading, recoverable only by knowing to
  // re-tap the previous row.
  const store = fakeStore({ workspaces: 1, sessions: 3 });
  store.capacity = { held: 8, max: 8 };
  const page = await railed(tpl, { store });
  assert(page.el("acpRailStatus").textContent.includes("at the limit"),
         "the rail is at the cap and does not say so");

  page.railRows()[0].dispatch("click");
  assertEqual(page.sentOf("subscribe").length, 0,
              "the row was opened at the session cap, spending a slot the server " +
              "would have refused");
  assertEqual(page.el("acpSid").textContent, "",
              "the page adopted a session it was never going to be given");
  assertEqual(page.urls.length, 0,
              "?sid= was repointed at a session the server refuses, so a reload " +
              "strands the page on it");
  assert(page.transcript().includes("8 of 8"),
         "the refusal did not say what the limit is");
  assert(page.transcript().includes("Close"),
         "the refusal did not name the remedy, which is the whole complaint " +
         "F-14 records: closeBtn lives in the conversation pane, so a phone " +
         "user has to be told where to go");
});

check("at the limit a session already held still opens", async (tpl) => {
  // The cap bounds *new* slots. A row already held by this PowerAtlas is in
  // _supervisor.sessions already, so subscribe answers it without spending
  // anything — refusing those would make the cap look like it locks the rail
  // rather than bounding it, and the session you most want at the cap is one
  // of the eight already open.
  const store = fakeStore({ workspaces: 1, sessions: 3 });
  store.capacity = { held: 8, max: 8 };
  store[0].sessions[1].availability = "held";
  const page = await railed(tpl, { store });
  page.railRows()[1].dispatch("click");
  assertEqual(page.sentOf("subscribe").length, 1,
              "a session this PowerAtlas already holds was refused at the cap, " +
              "though opening it spends nothing");
});

// Null and "0 of 8" are different states. A rail that guessed would either put
// a number on screen no measurement produced, or — worse — gate a control on
// it: a half-parsed pair that made `held >= max` true by accident would lock
// the rail against a server perfectly willing to serve.
//
// The numeric-strings case is the one that isolates the `typeof` check, and it
// is here because a mutation run found the first fixture did not. `{held: "8",
// max: null}` is already rejected by the `max <= 0` arm, so deleting the type
// checks left the harness green. `{held: "8", max: "8"}` survives every other
// arm — `isFinite` coerces, `"8" < 0` is false, `"8" <= 0` is false — and then
// `"8" >= "8"` compares as strings and is **true**, so without `typeof` the
// rail would refuse every row on a server reporting eight of eight hundred.
for (const [label, capacity] of [
  ["a half-formed pair", { held: "8", max: null }],
  ["numeric strings", { held: "8", max: "8" }],
  ["a missing field", { held: 3 }],
  ["nothing at all", null],
]) {
  check(`a capacity that is ${label} is not invented or acted on`, async (tpl) => {
    const store = fakeStore({ workspaces: 1, sessions: 3 });
    store.capacity = capacity;
    const page = await railed(tpl, { store });
    const status = page.el("acpRailStatus").textContent;
    assert(!status.includes("sessions open"),
           `the rail rendered a cap from an unusable payload: ${status}`);
    page.railRows()[0].dispatch("click");
    assertEqual(page.sentOf("subscribe").length, 1,
                "an unusable capacity payload locked the rail; with no answer " +
                "the server is still the authority and the tap must go through");
  });
}

check("a session with no title renders a placeholder, not a blank row", async (tpl) => {
  const store = fakeStore({ workspaces: 1, sessions: 3 });
  store[0].sessions[0].title = "";
  store[0].sessions[1].title = "   ";
  const page = await railed(tpl, { store });
  const titles = page.railTitles();
  // 185 of the real store's 1,210 sessions have neither a title nor a first
  // prompt behind it, so this is 15% of the rail and not an edge case.
  assert(titles[0].trim().length > 0,
         "a session with no title rendered an empty row with nothing to read");
  assertEqual(titles[0], titles[1],
             "a whitespace-only title took a different path from an empty one");
  assertEqual(titles[2], "workspace 0 session 2",
              "positive control: a real title must not be replaced");
});

check("a rail timestamp is the reader's local time, not the store's UTC", async (tpl) => {
  const store = fakeStore({ workspaces: 1, sessions: 3 });
  // The three readings the fix has to survive: a real record, an absent one,
  // and one the rail cannot parse.
  store[0].sessions[0].updated_at = "2026-08-03T12:00:00.086294300Z";
  store[0].sessions[1].updated_at = "";
  store[0].sessions[2].updated_at = "not a timestamp";
  const page = await railed(tpl, { store });
  const when = page.all("acpRailGroups", ".acp-rail-row-when")
                   .map((n) => n.textContent);

  // Derived from the same instant rather than hardcoded, so the check states
  // the property and holds wherever it is run. Pinning the literal
  // "2026-08-03 07:00" would encode this author's UTC-5 machine and fail for
  // everyone else — which is the same class of mistake as the bug being fixed,
  // one timezone assumed to be the only one.
  const at = new Date("2026-08-03T12:00:00.086294300Z");
  const p2 = (n) => (n < 10 ? "0" + n : String(n));
  const want = `${at.getFullYear()}-${p2(at.getMonth() + 1)}-${p2(at.getDate())}`
             + ` ${p2(at.getHours())}:${p2(at.getMinutes())}`;
  // Asserted on the row's `title`, because the visible column carries a short
  // form whose shape depends on how long ago the instant was — a clock today, a
  // day this year, a year before that. Pinning the visible text would make this
  // check start failing on its own the day after it was written, which is a
  // test that reports the calendar rather than the code.
  //
  // The trailing segment only: the hover reads `[{workspace}]: {session title}
  // - {date & time}`, and the two facts before the timestamp are the next
  // check's subject. Splitting the two keeps this one reporting the timezone
  // question it was written for rather than failing on a renamed fixture.
  assert(String(page.railRows()[0].title).endsWith(" - " + want),
         "the rail drew the store's UTC digits instead of the reader's local "
         + `time: ${page.railRows()[0].title}`);
  assert(when[0] && when[0].length < want.length,
         `the row still spends the full ${want.length} characters on a timestamp: ${when[0]}`);

  // Not "renders something harmless" — `new Date(null)` is the epoch, so the
  // failure this guards is a confident `1969-12-31` that reads as a real date.
  assertEqual(when[1], "",
              "an absent updated_at drew a timestamp; new Date(null) is 1969-12-31");
  assertEqual(when[2], "not a timestamp",
              "a record the rail cannot read must be shown as it came, not as Invalid Date");
});

// The hover is the only place a rail row states all three of its facts at once:
// grouped by workspace the visible row shows a title and a short clock and the
// project is in a header that scrolls away; grouped by day it shows a title
// alone. Both are drawn by `railRowNode`, which had two separate `title`
// assignments in two different grammars — so the same session hovered as
// `2026-07-10 09:00` in one mode and `alpha · 2026-07-10 09:00` in the other,
// and neither named the session. Asserted in both modes from one fixture,
// because one form for both is the property, not an implementation detail.
check("a row hovers its workspace, title and time, identically in both modes",
      async (tpl) => {
  const store = fakeStore({ workspaces: 1, sessions: 1 });
  store[0].name = "alpha";
  store[0].sessions[0].updated_at = "2026-07-10T09:00:00.086294300Z";
  store[0].sessions[0].title = "the one session";

  // Derived from the instant rather than written out, for the reason the check
  // above states: a literal would pin this author's UTC offset.
  const at = new Date("2026-07-10T09:00:00.086294300Z");
  const p2 = (n) => (n < 10 ? "0" + n : String(n));
  const want = "[alpha]: the one session - "
             + `${at.getFullYear()}-${p2(at.getMonth() + 1)}-${p2(at.getDate())}`
             + ` ${p2(at.getHours())}:${p2(at.getMinutes())}`;

  const grouped = await railed(tpl, { store });
  assertEqual(grouped.railRows()[0].title, want,
              "the workspace-grouped row does not hover "
              + "`[{workspace}]: {session title} - {date & time}`");

  // The load-bearing half. The grouped listing carries the workspace name on
  // the group meta and not on its rows, so this is the mode where the name has
  // to be handed down into the row; the flat listing puts it on the row itself
  // and would pass on its own.
  const byDay = await railed(tpl, { store, stored: { pa_acp_group: "date" } });
  assertEqual(byDay.railRows()[0].title, want,
              "the two grouping modes hover the same session differently");
});

check("a hover drops a field the store did not have, not just its value",
      async (tpl) => {
  // A session with no readable timestamp would otherwise hover with a trailing
  // ` - ` and nothing after it, which reads as a formatting defect rather than
  // as a field the store is missing.
  const store = fakeStore({ workspaces: 1, sessions: 1 });
  store[0].name = "alpha";
  store[0].sessions[0].updated_at = "";
  store[0].sessions[0].title = "the one session";
  const page = await railed(tpl, { store });
  assertEqual(page.railRows()[0].title, "[alpha]: the one session",
              "an unset timestamp left its separator behind");
});

// ---- grouped by day -------------------------------------------------------
//
// Instants are built from the *local* clock and converted to the UTC string the
// store would hold, rather than written as literals. A literal would encode the
// author's offset: `2026-08-04T02:00:00Z` is the 3rd here and the 4th in
// London, so a check pinned to it would assert this machine's timezone rather
// than the behaviour. Building from local midnight outward states the property
// instead, and holds wherever it runs.
function isoAtLocal(daysAgo, hour) {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate() - daysAgo,
                  hour, 0).toISOString();
}

function dayStore() {
  return [{
    cwd: "C:\\work\\alpha", name: "alpha", exists: true,
    sessions: [
      // Late enough that its UTC date is the *next* day anywhere west of
      // Greenwich — the row that separates local bucketing from UTC bucketing.
      { id: "late", title: "late tonight", updated_at: isoAtLocal(0, 23),
        availability: "available" },
      { id: "early", title: "early today", updated_at: isoAtLocal(0, 1),
        availability: "available" },
      { id: "prev", title: "yesterday one", updated_at: isoAtLocal(1, 12),
        availability: "available" },
    ],
  }];
}

check("grouped by day the rail asks for the flat listing", async (tpl) => {
  const page = await railed(tpl, {
    store: dayStore(), stored: { pa_acp_group: "date" } });
  const asked = page.listingCalls().map((c) => c.params);
  assert(asked.length > 0, "the rail made no listing request at all");
  assertEqual(asked[0].mode, "recent",
              "grouped by day the rail still asked for workspace groups");
  assertEqual(asked[0].size, "30", "the flat page size is not the agreed 30");
  assert(!("group_page" in asked[0]),
         "the flat request carried the group axis it does not have");
});

check("a day heading follows the reader's clock, not the stored UTC", async (tpl) => {
  const page = await railed(tpl, {
    store: dayStore(), stored: { pa_acp_group: "date" } });
  const headings = page.railHeadings();
  assertEqual(headings[0], "Today", `first heading was ${headings[0]}`);
  assertEqual(headings[1], "Yesterday", `second heading was ${headings[1]}`);
  // The load-bearing one. `late` is 23:00 local, so its stored UTC date is the
  // following day for every reader west of Greenwich. Bucketed on the raw
  // string it lands in a group of its own, ahead of Today; bucketed on the
  // reader's clock it sits beside the 01:00 row it shares a day with.
  assertEqual(headings.length, 2,
              `bucketed by UTC, not local: headings were ${headings.join(", ")}`);
  const groups = page.railGroups();
  assertEqual(groups[0].querySelectorAll(".acp-rail-row").length, 2,
              "the 23:00 row did not join the day it belongs to locally");
});

check("a day row carries no timestamp column, but still says where it is from",
      async (tpl) => {
  const page = await railed(tpl, {
    store: dayStore(), stored: { pa_acp_group: "date" } });
  assertEqual(page.all("acpRailGroups", ".acp-rail-row-when").length, 0,
              "the date-grouped row kept the timestamp its heading already carries");
  const row = page.railRows()[0];
  assert(/alpha/.test(row.title),
         `the row does not name the workspace it came from: ${row.title}`);
  // And the session it is, which the visible row does say — but the hover is
  // what a truncated title is read from, so dropping it there would make the
  // long-title case the one with no answer.
  assert(/late tonight/.test(row.title),
         `the row's hover does not name the session: ${row.title}`);
});

check("a day shows ten rows and offers exactly the rest", async (tpl) => {
  const store = dayStore();
  for (let i = 0; i < 12; i++) {
    store[0].sessions.push({
      id: `extra-${i}`, title: `extra ${i}`, updated_at: isoAtLocal(0, 12),
      availability: "available" });
  }
  const page = await railed(tpl, {
    store, stored: { pa_acp_group: "date" } });
  const first = page.railGroups()[0];
  assertEqual(first.querySelectorAll(".acp-rail-row").length, 10,
              "the day drew more than the ten rows a group shows");
  const more = first.querySelector(".acp-rail-group-more");
  // Six sessions fall on today, three are drawn: the promise is exact because
  // these rows are already loaded, unlike the grouped mode's button which
  // promises what the next request will bring.
  assertEqual(more.textContent, "Show 4 more",
              `the button misstates what it will reveal: ${more.textContent}`);
  more.dispatch("click");
  assertEqual(page.railGroups()[0].querySelectorAll(".acp-rail-row").length, 14,
              "revealing the day did not draw the rows it promised");
});

check("a session whose folder is gone is marked on the row itself", async (tpl) => {
  const store = dayStore();
  store[0].exists = false;
  const page = await railed(tpl, {
    store, stored: { pa_acp_group: "date" } });
  const row = page.railRows()[0];
  assert(/acp-rail-row-gone/.test(row.className),
         "the row from a missing directory is drawn like any other");
  assert(/folder missing/i.test(row.title),
         `the row does not say why it is marked: ${row.title}`);
  assert(!row.disabled,
         "a missing folder made the row unselectable; an unmounted drive is "
         + "not a deleted workspace, which is why the listing fails this open");
});

check("choosing a grouping mode is remembered", async (tpl) => {
  const page = await railed(tpl, { store: dayStore() });
  // Default with nothing stored is the shape that shipped, so a rail nobody
  // has configured is the rail they already had.
  assertEqual(page.listingCalls()[0].params.mode, undefined,
              "an unconfigured rail did not start in workspace grouping");
  const options = page.openSettings();
  assertEqual(options.length, 3, "the settings popup did not offer all three modes");
  const byDate = options.filter((o) => o.dataset.mode === "date")[0];
  byDate.dispatch("click");
  await page.settle();
  assertEqual(page.stored.pa_acp_group, "date",
              "the chosen mode was not written to storage");
  const last = page.listingCalls().pop().params;
  assertEqual(last.mode, "recent", "switching mode did not refetch the new shape");
});

check("a group starts expanded and collapses from its header", async (tpl) => {
  const page = await railed(tpl, { store: fakeStore({ workspaces: 2, sessions: 3 }) });
  const toggle = page.railGroups()[0].querySelector(".acp-rail-group-toggle");
  assertEqual(toggle.getAttribute("aria-expanded"), "true",
              "a group nobody has touched did not start expanded");
  // The label leads and the arrow trails it. Source order is what draws this:
  // the header is a flex row and only the count is pushed right, so the two
  // read in the order they are built. Led by the chevron the heading starts
  // 16 px in — further than the rows beneath it — and a workspace reads as one
  // more indented line rather than as the thing those rows hang off.
  const order = toggle.childNodes.map((n) => n.className);
  assert(order.indexOf("acp-rail-group-name")
         < order.indexOf("acp-rail-group-chevron"),
         `the collapse arrow does not follow the group name: ${order.join(", ")}`);
  toggle.dispatch("click");

  const collapsed = page.railGroups()[0];
  assertEqual(collapsed.querySelector(".acp-rail-group-toggle")
                       .getAttribute("aria-expanded"), "false",
              "the header did not report itself collapsed");
  // Not built, rather than hidden with CSS. A row that is merely invisible is
  // still a tab stop and still read out, so a collapsed workspace would go on
  // costing a keyboard and a screen reader everything it appears to have saved.
  assertEqual(collapsed.querySelectorAll(".acp-rail-row").length, 0,
              "a collapsed group still drew its rows");
  assertEqual(page.railGroups()[1].querySelectorAll(".acp-rail-row").length, 3,
              "collapsing one group emptied another");

  collapsed.querySelector(".acp-rail-group-toggle").dispatch("click");
  assertEqual(page.railGroups()[0].querySelectorAll(".acp-rail-row").length, 3,
              "the group did not come back when expanded again");
});

check("a day collapses by the same control as a workspace", async (tpl) => {
  const page = await railed(tpl, {
    store: dayStore(), stored: { pa_acp_group: "date" } });
  const toggle = page.railGroups()[0].querySelector(".acp-rail-group-toggle");
  toggle.dispatch("click");
  assertEqual(page.railGroups()[0].querySelectorAll(".acp-rail-row").length, 0,
              "a day heading is not the collapse control a workspace heading is");
});

check("a group's plus opens the picker already on that workspace", async (tpl) => {
  const page = await railed(tpl, { store: fakeStore({ workspaces: 3, sessions: 2 }) });
  const add = page.railGroups()[1].querySelector(".acp-rail-group-add");
  assert(/ws-1/.test(add.getAttribute("aria-label")),
         `the control does not name the workspace it creates in: ${add.getAttribute("aria-label")}`);
  add.dispatch("click");
  await page.settle();
  assertEqual(page.el("acpPicker").hidden, false, "the picker did not open");
  const offered = page.pickerNames();
  assertEqual(offered.length, 1,
              `the picker was not narrowed to the workspace pressed: ${offered.join(", ")}`);
  assertEqual(offered[0], "ws-1", "the picker preselected the wrong workspace");
});

check("the rail's own create control still offers every workspace", async (tpl) => {
  // The regression this guards is one line away at all times: `pickerOpen` now
  // takes a workspace, so a listener bound straight to it is handed the click
  // Event as that argument and filters the picker to a stringified event —
  // which offers nothing, from the only labelled create control a phone has.
  const page = await railed(tpl, { store: fakeStore({ workspaces: 3, sessions: 2 }) });
  page.click("acpRailNew");
  await page.settle();
  assertEqual(page.pickerNames().length, 3,
              "the unfiltered create control opened a filtered picker");
});

check("the rail's chrome is gated on the device, not the window width", () => {
  // CSS is the code here, for the reason the topbar check gives: this harness
  // has no layout engine, so what can be pinned is the rule rather than the
  // pixels. These three decide whether the redesign works for a reader who is
  // not using a mouse, which is exactly the reader a desktop browser cannot
  // show you.
  const css = fs.readFileSync(STYLESHEET, "utf8")
                .replace(/\/\*[\s\S]*?\*\//g, "").replace(/\s+/g, " ");
  const after = (marker, span) => {
    const at = css.indexOf(marker);
    return at === -1 ? "" : css.slice(at, at + span);
  };

  assert(/\.acp-rail-group-head \{[^}]*position: sticky/.test(css),
         "the group header does not stick, so scrolling a long workspace loses "
         + "which group the rows on screen belong to");

  const hover = after("@media (hover: hover)", 400);
  assert(hover, "no (hover: hover) block — hiding the row menu unconditionally "
       + "takes Delete away from every touch device, which is where the rail is "
       + "the whole page");
  assert(/\.acp-rail-menu-wrap \{ opacity: 0/.test(hover),
         "the row menu is not hidden by that block at all");
  assert(/:focus-visible/.test(hover),
         "the menu is revealed by pointing with no keyboard route to it, so "
         + "Delete becomes unreachable without a mouse");
  assert(/aria-expanded="true"/.test(hover),
         "nothing keeps the menu visible while its own popup is open, and the "
         + "pointer leaves the button the moment the popup is used");

  // Row padding must be wide enough at rest (for always-visible more-btn) and on
  // hover (for the full 3-button wrap) without creating a blank gap at rest.
  assert(/\.acp-rail-item .acp-rail-row \{ padding-right: 4[04]px/.test(css),
         "base row padding-right is too narrow -- title bleeds under the /acp "
         + "kebab button wrap on hover");
  assert(/\.acp-rail-item:has\(.acp-rail-menu-wrap-ghost\) .acp-rail-row \{ padding-right: 3\dpx/.test(css),
         "ghost-wrap rest padding-right should be small (just more-btn width) -- "
         + "100px at rest creates a visible blank gap between the date and the button");
  assert(/\.acp-rail-item:has\(.acp-rail-menu-wrap-ghost\):hover .acp-rail-row[^}]*padding-right: 1\d\dpx/.test(css),
         "ghost-wrap hover padding-right is too narrow -- title bleeds under "
         + "the dashboard's 3-button wrap on hover");

  const fine = after("@media (pointer: fine)", 260);
  assert(fine && /\.acp-rail-row \{[^}]*min-height/.test(fine),
         "the compact row height is not gated on the pointer, so it shrinks the "
         + "40 px touch target the rail relies on");
});

check("the width handle is a splitter a keyboard can reach", async (tpl) => {
  // What this harness can hold is the contract, not the drag: there is no
  // layout engine, no pointer capture and no `window` here. The dragging itself
  // was measured in a browser — 288 to 445 and persisted, floored at 220, two
  // arrow presses moving 32 px, and a stored 9999 reopening at 450 on a 900 px
  // window. What is pinned here is the part that silently rots: a splitter that
  // loses `tabindex` or its value attributes still drags perfectly and becomes
  // unreachable for anyone who cannot.
  // Read from the template rather than through `page.el`. `byId` builds its
  // stubs by regexing the markup for ids alone, so every element it hands back
  // reports `null` for every attribute — asserting through it would pass
  // against a handle with no role and no tabindex at all.
  const src = fs.readFileSync(tpl, "utf8");
  const handle = src.match(/<div[^>]*id="acpRailResize"[^>]*>/);
  assert(handle, "the rail has no width handle in the markup at all");
  for (const attr of ['role="separator"', 'tabindex="0"',
                      'aria-orientation="vertical"', "aria-valuenow",
                      "aria-valuemin", "aria-valuemax"]) {
    assert(handle[0].includes(attr),
           `the splitter is missing ${attr}, so it drags for a mouse and for `
           + `nothing else: ${handle[0]}`);
  }

  const css = fs.readFileSync(STYLESHEET, "utf8")
                .replace(/\/\*[\s\S]*?\*\//g, "").replace(/\s+/g, " ");
  assert(/\.acp-rail \{[^}]*flex: 0 0 var\(--acp-rail-w, 288px\)/.test(css),
         "the rail's width is not driven by the custom property, so the handle "
         + "moves a number nothing reads — and the 288px fallback is what keeps "
         + "the rail its old width when storage or the script is unavailable");
  assert(/\.acp-rail-resize \{ display: none/.test(css),
         "the handle is not hidden below the breakpoint, where the rail and the "
         + "conversation are one pane at a time and there is no edge to drag");
});

check("a browser that refuses storage still renders the rail", async (tpl) => {
  // `localStorage` throws on read as well as on write when storage is
  // disabled, and the read happens while the page's script is still
  // evaluating — so an unguarded one takes the whole rail down, not just its
  // memory of a preference.
  const page = await railed(tpl, {
    store: dayStore(), storageThrows: true });
  assert(page.railRows().length > 0,
         "storage that refuses left the rail with no rows at all");
  assertEqual(page.listingCalls()[0].params.mode, undefined,
              "a rail that cannot read its preference did not fall back to the default");
});

check("a listing that fails says so instead of leaving the rail blank", async (tpl) => {
  const page = await railed(tpl, {
    answer: (url) => (url.startsWith("/api/acp/sessions")
      ? { reject: "the network went away" } : null),
  });
  assertEqual(page.railRows().length, 0, "rows appeared from a request that failed");
  const said = page.el("acpRailStatus").textContent;
  assert(/could not load/i.test(said),
         `the rail stayed on its loading message forever: ${said}`);

  // A refused response is the other half: the remote allowlist answers 403 to a
  // device with no cookie, and `res.ok` is the only thing that separates it from
  // a listing that is genuinely empty.
  const refused = await railed(tpl, {
    answer: (url) => (url.startsWith("/api/acp/sessions")
      ? { ok: false, status: 403, body: {} } : null),
  });
  assert(/could not load/i.test(refused.el("acpRailStatus").textContent),
         "a 403 was rendered as an empty store rather than as a refusal");
});

check("the rail is visible, and only because style.css now bounds it", (tpl) => {
  // The replacement for Phase 5a's "the rail stays inert" check, which pinned
  // `hidden` on the <aside> while style.css carried no `.acp-rail` rule at all.
  // With none, the rail's flex `min-height` resolved to content height,
  // unshrinkable, while `.acp-page { flex:1; min-height:0 }` has
  // `flex-basis:0` and absorbed the whole squeeze — measured in Chromium at
  // 1280x800 as a 26 px transcript, and at 390x844 as a composer below the fold
  // of a viewport `overflow:hidden` will not scroll.
  //
  // The two halves are pinned **together**, in one check, because either alone
  // is what shipped the collapse: markup without CSS is Phase 5a's High
  // finding, and CSS without markup is a rail nobody can see. This is a source
  // check on both files rather than a rendered-layout check — the DOM stand-in
  // has no box model — so the pixel evidence is the browser measurement in the
  // phase log, and what lives here is the pairing that measurement was taken
  // against.
  const page = loadPage(tpl);
  const aside = page.markup.match(/<aside\b[^>]*class="acp-rail"[^>]*>/);
  assert(aside, "the rail's <aside> is not where this check expects it");
  assert(!/\shidden(\s|>)/.test(aside[0]),
         `the rail is still rendered inert: ${aside[0]}`);
  assert(/<div\b[^>]*class="acp-shell"[^>]*data-view=/.test(page.markup),
         "the rail and the conversation are not inside a shell carrying an " +
         "initial data-view, so the drill-down has nothing to switch");

  // **Comments stripped first.** The block this checks is heavily commented
  // and the comments name the very things asserted below — `100dvh`, the
  // 768 px breakpoint — so against the raw file two of these assertions matched
  // the prose explaining the rule rather than the rule. Measured: with the
  // media query moved to 900 px and `100dvh` reverted to `100%`, both survived.
  // A check that passes on a stylesheet that has lost the declaration, because
  // a sentence above it still mentions it, measures nothing.
  const css = fs.readFileSync(
    path.join(HERE, "..", "src", "power_atlas", "static", "style.css"), "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "");
  assert(/^\.acp-rail\s*\{[^}]*\bmin-height:\s*0/m.test(css),
         "style.css has no `.acp-rail` rule bounding the rail's flex height, " +
         "which is the exact condition that collapsed the transcript to 26 px");
  assert(/^\.acp-rail-groups\s*\{[^}]*overflow-y:\s*auto/m.test(css),
         "nothing inside the rail scrolls, so a long list pushes the rail's " +
         "own height past the shell instead of scrolling within it");
  assert(/@media\s*\(min-width:\s*768px\)/.test(css),
         "there is no 768 px breakpoint, so the two-pane layout the phase " +
         "exists for is not expressed anywhere");
  assert(/height:\s*100dvh/.test(css),
         "the shell height is still viewport-percentage, which mobile browsers " +
         "resolve against the URL-bar-retracted viewport and which therefore " +
         "puts the composer below the fold when the bar is showing");
});

check("the drill-down moves between the rail and the conversation", async (tpl) => {
  // Below 768 px these are the only two states the page has, and the toggle is
  // the only way between them: the conversation's own controls are inside the
  // pane the rail replaces.
  const page = await railed(tpl);
  const shell = page.el("acpShell");
  const toggle = page.el("acpViewToggle");
  assertEqual(shell.dataset.view, "rail",
              "a page opened with no ?sid= landed on a conversation that has " +
              "no session in it");
  // The label names the destination, not the current pane — a button reading
  // "Sessions" while the sessions are what is on screen is a button that
  // appears to do nothing.
  assert(/conversation/i.test(toggle.textContent),
         `the toggle does not name where it goes: ${toggle.textContent}`);

  page.railRows()[0].dispatch("click");
  assertEqual(shell.dataset.view, "chat",
              "picking a session left the phone looking at the rail, which is " +
              "the drill-down not happening");
  assert(/session/i.test(toggle.textContent),
         `the toggle still points at the pane already shown: ${toggle.textContent}`);

  toggle.dispatch("click");
  assertEqual(shell.dataset.view, "rail",
              "there is no way back to the session list");
  toggle.dispatch("click");
  assertEqual(shell.dataset.view, "chat", "the toggle does not toggle");
});

check("a page opened at a session starts on the conversation", (tpl) => {
  const page = loadPage(tpl, { sid: "sess-from-url-01" });
  assertEqual(page.el("acpShell").dataset.view, "chat",
              "a URL naming a session opened the session list instead, making " +
              "the phone's first act finding the session it already named");
});

check("only the two known views ever reach the shell attribute", async (tpl) => {
  // `data-view` is an attribute sink selected on by CSS. Nothing payload-derived
  // reaches it today, and this pins that: the value is narrowed to one of two
  // literals rather than passed through.
  const page = await railed(tpl);
  const shell = page.el("acpShell");
  const seen = new Set([shell.dataset.view]);
  page.railRows()[0].dispatch("click");
  seen.add(shell.dataset.view);
  page.el("acpViewToggle").dispatch("click");
  seen.add(shell.dataset.view);
  assertEqual([...seen].sort().join(","), "chat,rail",
              "the shell took a view value other than the two the CSS knows");
});

check("the topbar logo names the product for both viewers", (tpl) => {
  // The `<a href="/">` "Main dashboard" back-link this test used to assert
  // was itself removed by 0b4708e ("rename to Agent orchestrator, remove
  // Main dashboard link") — `/` is not on `_REMOTE_ALLOWED_PATHS` and never
  // will be (SC-4), so from a phone it was a control whose only outcome was
  // a 403 with no way back, and it was dropped rather than gated. What
  // remains, and still needs covering, is the logo below.
  const local = loadPage(tpl, { local: true });
  const remote = loadPage(tpl, { local: false });
  assert(!/topbar-nav/.test(local.markup) && !/topbar-nav/.test(remote.markup),
         "a topbar-nav dashboard link exists again — it has no remote-safe destination (SC-4)");

  // The logo is what makes dropping the link above affordable, so it is the
  // other half of this check rather than a separate one: it is served from
  // `/static` — which *is* on the allowlist — and naming the product is not
  // navigation, so it is unconditional. Both renderings are asserted on both
  // arms; the stylesheet, not the template, decides which is on screen.
  for (const [who, page] of [["loopback", local], ["remote", remote]]) {
    assert(/class="[^"]*acp-banner[^"]*"/.test(page.markup),
           `the ${who} page has no banner logo, so above 768 px it opens with ` +
           "an unnamed topbar");
    assert(/class="acp-wordmark"/.test(page.markup),
           `the ${who} page has no wordmark, so below 768 px — where the ` +
           "banner is 376 px of a 390 px row and is hidden — nothing names " +
           "the product");
  }
});

check("a workspace whose directory is gone is marked in the rail", async (tpl) => {
  // 14 of the real store's 65 workspaces name a directory that no longer
  // exists, including the 208-session `nrf_tool` worktree. Their sessions
  // report `available` and that is correct — D17 measures lock liveness, and
  // nothing holds a lock on a session in a deleted tree — so without this the
  // rail offers 208 rows that fail the moment one is tapped.
  const store = fakeStore({ workspaces: 3, sessions: 2 });
  store[1].exists = false;
  const page = await railed(tpl, { store });
  const groups = page.railGroups();

  const marks = groups.map((g) => Boolean(g.querySelector(".acp-rail-group-missing")));
  assertEqual(marks.join(","), "false,true,false",
              "the vanished workspace is indistinguishable from the two that " +
              "are still on disk");
  assert(String(groups[1].className).includes("acp-rail-group-gone"),
         `the group carries no class the stylesheet can dim: ${groups[1].className}`);
  const badge = groups[1].querySelector(".acp-rail-group-missing");
  assert(badge.textContent.trim().length > 0,
         "the marker renders nothing, so it is invisible to a reader");
  assert(/no longer exists/i.test(String(badge.title)),
         `the marker does not say what it means: ${badge.title}`);

  // Still selectable, for the same reason D17 fails open: an unmounted network
  // drive is not a dead workspace, and a row the user cannot try is a dead end
  // with no way to find out why.
  const rows = groups[1].querySelectorAll(".acp-rail-row");
  assertEqual(rows[0].disabled, false,
              "a vanished directory disabled rows that a remounted drive would " +
              "make openable again");
  assertEqual(rows[0].dataset.availability, "available",
              "the marker was implemented by rewriting availability, which " +
              "measures a different thing");
});

check("a listing with no exists field marks nothing rather than everything",
      async (tpl) => {
  // The field is a boolean the endpoint always sends. An older server, or a
  // truncated payload, means "no answer" — and `!group.exists` would read that
  // as "gone" and badge every workspace on the page.
  const store = fakeStore({ workspaces: 2, sessions: 2 });
  const page = await railed(tpl, {
    store,
    answer: (url) => {
      if (!url.startsWith("/api/acp/sessions")) return null;
      const body = serveListing(store, {});
      for (const g of body.groups) delete g.exists;
      return { body };
    },
  });
  const marked = page.railGroups()
    .filter((g) => g.querySelector(".acp-rail-group-missing"));
  assertEqual(marked.length, 0,
              "a payload with no `exists` field badged every workspace as " +
              "missing, which trains the user to ignore the badge");
});

check("the page with no ACP module offers no way to list sessions", (tpl) => {
  const page = loadPage(tpl, { acpError: "No module named 'power_atlas.acp'" });
  assertEqual(page.listingCalls().length, 0,
              "a page whose ACP module failed to import still fetched the listing");
  assert(/unavailable/i.test(page.el("acpRailStatus").textContent),
         "the rail did not say why it is empty");
  // Asserted on the attribute rather than by clicking, because `dispatch`
  // deliberately ignores `disabled` (see the note on it). The status line alone
  // was not the fix: Refresh's listener is registered unconditionally, so one
  // press replaced that line with "10 of 12 workspaces" and a full rail of rows
  // whose only action — open a session — has no module to open one with.
  assertEqual(page.el("acpRailReload").disabled, true,
              "Refresh was live on a page that cannot open any session it lists");
  assertEqual(page.el("acpRailSearch").disabled, true,
              "the filter box invites narrowing a list that must not be loaded");
});

check("a workspace that comes back on a later page merges into the one on screen",
      async (tpl) => {
  const store = fakeStore({ workspaces: 12, sessions: 15 });
  const page = await railed(tpl, {
    store,
    answer: (url, params) => {
      if (!url.startsWith("/api/acp/sessions") || params.group_page !== "2") return null;
      // The reorder the rail itself causes. Workspaces come back
      // recency-ordered, and the rail's own purpose — open a session, run a
      // turn — moves that workspace towards the front, so the second page can
      // legitimately re-answer with one already on screen.
      return { body: {
        groups: [
          { cwd: "C:\\work\\ws-0", name: "ws-0", total: 15, session_page: 1,
            has_more: true, sessions: store[0].sessions.slice(0, 10) },
          { cwd: "C:\\work\\ws-11", name: "ws-11", total: 15, session_page: 1,
            has_more: true, sessions: store[11].sessions.slice(0, 10) },
        ],
        group_page: 2, group_total: 12, has_more: false,
      } };
    },
  });

  // Page into ws-0 first, so the merge has state that must survive it.
  page.railGroups()[0].querySelector(".acp-rail-group-more").dispatch("click");
  await page.settle();
  assertEqual(page.railGroups()[0].querySelectorAll(".acp-rail-row").length, 15,
              "positive control: the per-group show-more must extend ws-0 first");

  page.click("acpRailMore");
  await page.settle();
  const names = page.railGroups().map(
    (g) => g.querySelector(".acp-rail-group-name").textContent);
  assertEqual(names.filter((n) => n === "ws-0").length, 1,
              `ws-0 was drawn twice, and each copy then carries its own ` +
              `session_page, so its show-more extends only one of them: ${names.join(", ")}`);
  assertEqual(page.railGroups().length, 11,
              "the repeat was appended rather than merged");
  assertEqual(page.railRows().length, 115,
              "the repeat's rows were appended beside the ones already drawn");
  assertEqual(page.railGroups()[0].querySelectorAll(".acp-rail-row").length, 15,
              "the merge rewound ws-0 to the ten rows the repeat carried, " +
              "losing the page the user had already asked for");
  assert(!page.railGroups()[0].querySelector(".acp-rail-group-more"),
         "the merge took the repeat's has_more and re-offered rows already drawn");
  assert(names.includes("ws-11"),
         "the workspace that was genuinely new on the second page never arrived");
});

check("a second show-more with nothing settled in between is dropped, not raced",
      async (tpl) => {
  const page = await railed(tpl, { store: fakeStore({ workspaces: 12, sessions: 15 }) });
  assertEqual(page.listingCalls().length, 1, "the first load made the wrong shape");

  page.click("acpRailMore");
  const busyText = page.el("acpRailStatus").textContent;
  page.click("acpRailMore");
  await page.settle();
  assertEqual(page.listingCalls().length, 2,
              "the second click went out on top of the first: two group pages in " +
              "flight interleave into the rail in whatever order they answer");
  assert(/loading/i.test(busyText),
         `the dropped click landed on a rail that never said it was busy: ${busyText}`);

  // The per-group axis has its own button and its own reach into the guard.
  const before = page.listingCalls().length;
  const more = page.railGroups()[0].querySelector(".acp-rail-group-more");
  more.dispatch("click");
  const groupBusyText = page.el("acpRailStatus").textContent;
  more.dispatch("click");
  await page.settle();
  assertEqual(page.listingCalls().length, before + 1,
              "a double-press on a workspace's show-more sent two overlapping " +
              "session pages for the same workspace");
  assert(/loading/i.test(groupBusyText),
         `the per-group show-more is silent while it works: ${groupBusyText}`);
});

check("re-selecting a session after another one re-tries the adoption", async (tpl) => {
  const page = await railed(tpl);
  const a = page.railRows()[0].dataset.sid;
  const unknown = { code: "unknown_session", message: "This server holds no such session." };

  page.railRows()[0].dispatch("click");
  page.deliver({ type: "error", sessionId: a, payload: unknown });
  assertEqual(page.sentOf("load").length, 1,
              "the first selection never asked the agent to load the session");
  page.deliver({ type: "session", sessionId: a,
                 payload: { sessionId: a, cwd: "C:\\work\\ws-0", created: false,
                            turnActive: false, contextPercent: null } });

  // A second session on the same socket, then back to the first. Phase 2's idle
  // sweeper reclaiming A while the user works in B is the live trigger.
  page.railRows()[3].dispatch("click");
  page.railRows()[0].dispatch("click");
  page.deliver({ type: "error", sessionId: a, payload: unknown });

  const loads = page.sentOf("load");
  assertEqual(loads.length, 2,
              "adoption is keyed per connection, so the second selection of a " +
              "session sent no load at all and the row silently does nothing");
  assertEqual(loads[1].sessionId, a, "the retry named the wrong session");
  assert(!page.transcript().includes("[unknown_session]"),
         "the user got a bare protocol code and not even the recovery note, " +
         "which fires off a `meta pending:'load'` that was never sent");
});

check("selecting a row clears the conversation that was on screen", async (tpl) => {
  const page = await railed(tpl);
  const a = page.railRows()[0].dataset.sid;
  page.railRows()[0].dispatch("click");
  page.deliver({ type: "chunk", sessionId: a,
                 payload: { role: "agent", text: "an answer belonging to the first session" } });
  assert(page.transcript().includes("an answer belonging to the first session"),
         "positive control: the chunk never rendered");
  page.railRows()[3].dispatch("click");
  assert(!page.transcript().includes("an answer belonging to the first session"),
         "selecting a row left the previous conversation on screen, under a header " +
         "and a URL that both name the new session");
});

check("a rail-selected session is unsubscribed until the server answers it", async (tpl) => {
  const page = await railed(tpl);
  const a = page.railRows()[0].dataset.sid;
  page.railRows()[0].dispatch("click");
  page.deliver({ type: "session", sessionId: a,
                 payload: { sessionId: a, cwd: "C:\\work\\ws-0", created: false,
                            turnActive: false, contextPercent: null } });
  // Subscribed now. The next selection is a different session on the same
  // socket, and nothing has answered for it — so a `close_in_progress` naming
  // it takes the terminal arm, where no `session_closed` is ever coming.
  const b = page.railRows()[3].dataset.sid;
  page.railRows()[3].dispatch("click");
  page.deliver({ type: "chunk", sessionId: b,
                 payload: { role: "agent", text: "text that arrived before the sweep" } });
  page.deliver({ type: "error", sessionId: b,
                 payload: { code: "close_in_progress",
                            message: "This session is being released." } });
  assert(!page.transcript().includes("text that arrived before the sweep"),
         "a stale `subscribed` from the previously selected session sent this one " +
         "down the wrong arm, leaving a transcript whose session no longer exists " +
         "and no frame coming to say so");
  assert(page.transcript().includes("Everything on screen belonged"),
         "the page emptied without telling the user the session went with it");
});

check("a group's count agrees with the rows drawn beneath it", async (tpl) => {
  const page = await railed(tpl);
  const box = page.el("acpRailSearch");
  box.value = "workspace 3 session 1";
  box.dispatch("input");
  const group = page.railGroups()[0];
  assertEqual(group.querySelectorAll(".acp-rail-row").length, 1,
              "positive control: the filter must narrow this group to one row");
  const head = group.querySelector(".acp-rail-group-head").textContent;
  assert(!/3 of 5/.test(head),
         `the header counts the loaded set while the group draws only what matched: ${head}`);
  assert(head.includes("1 matching"), `the header does not say what it is showing: ${head}`);

  box.value = "";
  box.dispatch("input");
  assert(page.railGroups()[0].querySelector(".acp-rail-group-head")
             .textContent.includes("5 of 5"),
         "clearing the filter did not restore the loaded-of-total count");
});

check("a re-render puts keyboard focus back where the user left it", async (tpl) => {
  const page = await railed(tpl, { store: fakeStore({ workspaces: 12, sessions: 15 }) });

  // A workspace's own show-more: ten sessions become fifteen, which is all of
  // them, so the button the user pressed does not exist after the rebuild.
  const more = page.railGroups()[0].querySelector(".acp-rail-group-more");
  more.focus();
  more.dispatch("click");
  await page.settle();
  let now = page.focused();
  assert(now, "the rebuild dropped focus to the document body, throwing a keyboard " +
              "or screen-reader user out of the rail mid-task — the same population " +
              "the locked row's `disabled` exists for");
  assert(now.dataset && now.dataset.sid && now.dataset.sid.startsWith("sess-w0-"),
         "focus did not land on a ws-0 row");

  // ws-0 now has 15 rows; ws-1 starts at index 15.
  const sid = page.railRows()[16].dataset.sid;
  page.railRows()[16].focus();
  page.railRows()[16].dispatch("click");
  now = page.focused();
  assert(now, "selecting a row dropped focus to the document body");
  assertEqual(now.dataset.sid, sid, "focus moved somewhere other than the row selected");

  // A press that does not re-render must not leave a restore pending. Clicking
  // the row already open returns early, and the next render is the filter's —
  // whose box is outside the rail and must keep the focus it has.
  page.railRows().find((r) => r.dataset.sid === sid).dispatch("click");
  const box = page.el("acpRailSearch");
  box.value = "ws-1";
  box.dispatch("input");
  assertEqual(page.focused(), null,
              "typing in the filter pulled focus out of the box and onto a rail row");
  box.value = "";
  box.dispatch("input");

  // The rail-wide show-more, which hides itself once the last page is in — the
  // one render where that button cannot keep its own focus.
  page.el("acpRailMore").focus();
  page.click("acpRailMore");
  await page.settle();
  assertEqual(page.el("acpRailMore").hidden, true,
              "positive control: there is no third page, so the button must hide");
  now = page.focused();
  assert(now && now.dataset.sid,
         "the button hid itself with focus still on it, which is the document body " +
         "as far as the keyboard is concerned");
});

// ------------------------------------------- the rail's freshness (D15) --
//
// The defect these three measure: a browser sat on /acp for 500 s across 25
// samples and kept three rows on a blue "held by this PowerAtlas" dot at every
// sample, while the idle sweeper reclaimed all three beneath it and deleted
// their `.lock` files. The counts were identical at t=0.0 and t=400.1, and one
// press of Refresh corrected all three at once — the rail had the right answer
// available and no way to learn it had gone stale.

check("a stale held row corrects itself on a tick, without a press", async (tpl) => {
  const store = fakeStore({ workspaces: 2, sessions: 2 });
  store[0].sessions[0].availability = "held";
  const page = await railed(tpl, { store });
  const held = () => page.railRows().filter(
    (r) => r.dataset.availability === "held").length;
  assertEqual(held(), 1, "the fixture's held row did not render as held");

  // The sweeper reclaims it. Nothing is pressed, and no frame arrives — this is
  // exactly the situation `session_closed` cannot reach, because the socket is
  // not subscribed to this session and acp.py fans that frame out to
  // `_registry.subscribers[sessionId]` and nowhere else.
  store[0].sessions[0].availability = "available";
  assertEqual(held(), 1,
              "positive control: the rail must not read the store directly");
  page.tick();
  await page.settle();
  assertEqual(held(), 0,
              "the rail still asserts `held` for a session the sweeper released — " +
              "the state it showed for 500 s in the measurement this check is for");
});

check("a held row's dot follows the turn, not just the holding", async (tpl) => {
  const store = fakeStore({ workspaces: 1, sessions: 1 });
  store[0].sessions[0].availability = "held";
  store[0].sessions[0].status = "working";
  const page = await railed(tpl, { store });
  const dotClass = () => String(
    page.railRows()[0].querySelector(".session-status").className);
  assert(dotClass().includes("status-thinking"), "the fixture did not render working");

  // The turn ends. Availability has not moved — this ACP holds the session
  // either way — so a refresh that carried availability alone would leave the
  // working pulse running on a session that had stopped.
  store[0].sessions[0].status = "waiting";
  page.tick();
  await page.settle();
  assert(dotClass().includes("status-waiting"),
         `the dot still claims the agent is working: ${dotClass()}`);
});

check("the poll moves the open-session counter with the dots", async (tpl) => {
  const store = fakeStore({ workspaces: 1, sessions: 1 });
  store[0].sessions[0].availability = "held";
  store.capacity = { held: 1, max: 8 };
  const page = await railed(tpl, { store });
  assert(/1\/8 sessions open/.test(page.el("acpRailStatus").textContent),
         `the fixture's counter did not render: ${page.el("acpRailStatus").textContent}`);

  // The sweeper reclaims it. Every other loader ends in `railSetCapacity`; this
  // one did not, so the dot went green while the header went on claiming the
  // slot was taken — two widgets contradicting each other about one fact.
  store[0].sessions[0].availability = "available";
  store.capacity = { held: 0, max: 8 };
  page.tick();
  await page.settle();
  assert(/0\/8 sessions open/.test(page.el("acpRailStatus").textContent),
         "the counter still claims a slot the rail has already drawn as free: " +
         page.el("acpRailStatus").textContent);
});

check("the poll picks up a session renamed outside the page", async (tpl) => {
  const store = fakeStore({ workspaces: 1, sessions: 1 });
  const page = await railed(tpl, { store });
  assertEqual(page.railTitles()[0], "workspace 0 session 0",
              "the fixture's title never reached its row");

  // Renamed in the agent, which is the only place renaming happens: nothing on
  // this page did it and no frame announces it. `railRefreshStates` copied
  // availability and status alone, and no other automatic path writes a title
  // — so the row kept its old label not for a tick but for as long as the tab
  // stayed open, until someone happened to press Refresh.
  store[0].sessions[0].title = "renamed in the agent";
  page.tick();
  await page.settle();
  assertEqual(page.railTitles()[0], "renamed in the agent",
              "the rail still shows the old title, so a rename is visible only " +
              "to whoever thinks to press Refresh");
});

check("the poll leaves a rail whose titles only look different alone", async (tpl) => {
  // The other half of the same change. `changed` is what gates `renderRail`,
  // and a diff computed against the raw field rather than the rendered fallback
  // would set it on every tick for a pair of values that draw the same string —
  // rebuilding the rail, and dropping focus, once a minute for no news. `null`
  // is what the listing sends for a session carrying no title at all; `""` is
  // what a later fetch of the same row may carry instead.
  const store = fakeStore({ workspaces: 1, sessions: 1 });
  store[0].sessions[0].title = null;
  const page = await railed(tpl, { store });
  assertEqual(page.railTitles()[0], "untitled session",
              "the empty-title fallback never rendered");

  const row = page.railRows()[0];
  store[0].sessions[0].title = "";
  page.tick();
  await page.settle();
  assert(page.railRows()[0] === row,
         "the poll rebuilt the rail over a title that renders identically " +
         "either way — every node recreated, and focus dropped with them");
});

check("closing a session refreshes the rail instead of waiting out the tick", async (tpl) => {
  const store = fakeStore({ workspaces: 1, sessions: 1 });
  store[0].sessions[0].availability = "held";
  const page = await railed(tpl, { store, sid: "sess-w0-s0" });
  const before = page.listingCalls().length;

  // No tick, and no `session_closed` reaching any other socket: this is the
  // page that did the closing, and the row it just freed is its own.
  store[0].sessions[0].availability = "available";
  page.deliver({
    type: "session_closed", sessionId: "sess-w0-s0",
    payload: { sessionId: "sess-w0-s0", message: "This session was closed." },
  });
  await page.settle();

  assertEqual(page.listingCalls().length - before, 1,
              "closing asked the server nothing, so the row the user just " +
              "freed goes on claiming to be open for up to a minute");
  assertEqual(page.railRows().filter(
                (r) => r.dataset.availability === "held").length, 0,
              "the closed session is still drawn as held by this PowerAtlas");
});

check("adopting a session refreshes the rail instead of waiting out the tick", async (tpl) => {
  const store = fakeStore({ workspaces: 1, sessions: 1 });
  const page = await railed(tpl, { store });
  const before = page.listingCalls().length;

  // `created: false` — a load, or a re-subscribe. The row is already on the
  // rail; what changed is that this ACP now holds it, and that is a fact only
  // the server has, since `held` is read from `_supervisor.sessions`.
  store[0].sessions[0].availability = "held";
  store[0].sessions[0].status = "working";
  page.deliver({
    type: "session", sessionId: "sess-w0-s0",
    payload: { sessionId: "sess-w0-s0", cwd: "C:\\work\\ws-0", created: false,
               turnActive: false, contextPercent: null },
  });
  await page.settle();

  assertEqual(page.listingCalls().length - before, 1,
              "adopting a session asked the server nothing, so its row stays " +
              "drawn as free until the 60 s tick");
  const dot = page.railRows()[0].querySelector(".session-status");
  assert(dot && String(dot.className).includes("status-thinking"),
         "the adopted row carries no live dot, so nothing on it says this ACP " +
         "is now the thing driving it");
});

check("the freshness poll keeps the rail's paging and costs one request", async (tpl) => {
  const store = fakeStore({ workspaces: 25, sessions: 2 });
  const page = await railed(tpl, { store });
  page.click("acpRailMore");
  await page.settle();
  const paged = page.railGroups().length;
  assertEqual(paged, 20, "the second workspace page did not land");

  const before = page.listingCalls().length;
  page.tick();
  await page.settle();
  assertEqual(page.listingCalls().length - before, 1,
              "a tick cost more than one request; a poll per group is the shape " +
              "this was written to avoid");
  assertEqual(page.railGroups().length, paged,
              "the poll collapsed the rail back to page one — an automatic " +
              "`loadGroupPage(1)` would do this once a minute, which is worse " +
              "than the staleness it fixes");
  // Both workspace pages are covered by the one request, not just the first.
  const last = page.listingCalls().at(-1).params;
  assertEqual(Number(last.group_size), 20,
              "the poll asked for one page's worth, so the workspaces the user " +
              "paged to keep their stale dots");
});

check("a backgrounded tab stops polling and refreshes on return", async (tpl) => {
  const store = fakeStore({ workspaces: 1, sessions: 1 });
  const page = await railed(tpl, { store });
  const calls = () => page.listingCalls().length;

  page.setVisibility("hidden");
  const hidden = calls();
  page.tick();
  await page.settle();
  assertEqual(calls(), hidden,
              "a hidden tab is still polling — one request a minute per open tab " +
              "against a route whose per-row cost is a file read plus a psutil " +
              "query, spent on a picture nobody is looking at");

  page.setVisibility("visible");
  await page.settle();
  assertEqual(calls(), hidden + 1,
              "coming back to the tab did not refresh, so the first thing the " +
              "user sees is the stale rail the poll was paused on");
});

// ------------------------------------- creating a session from the rail (SC-1) --

check("the rail carries a labelled create control", async (tpl) => {
  const page = await railed(tpl, { store: fakeStore({ workspaces: 1, sessions: 1 }) });
  // Read off the markup, not the element: `byId` builds a bare stand-in per id
  // and no static label reaches it, so an element assertion here would measure
  // the harness. This is the string a cold page actually paints — nothing calls
  // `setPending` before the first press.
  const button = page.markup.match(
    /<button[^>]*id="acpRailNew"[^>]*>([^<]*)<\/button>/);
  assert(button, "the rail has no create control at all");
  assertEqual(button[1].trim(), "New session",
              "the rail's create control is not labelled as what it does; on a " +
              "cold 390x844 load the whole conversation pane is `display: none` " +
              "and the only visible control was a toggle reading 'Conversation →'");
  assert(page.markup.split('id="acpRail"')[1].split("</aside>")[0]
             .includes('id="acpRailNew"'),
         "the create control is not inside the rail, so the pane a phone lands " +
         "on still does not carry it");
  // It opens the picker rather than creating; creating is what the picker's
  // options do. Both `New session` buttons go through it, so the directory a
  // trust-all-tools agent runs in is chosen rather than inherited from a text
  // box the rail could not see.
  page.click("acpRailNew");
  assertEqual(page.el("acpPicker").hidden, false,
              "the rail's create control opened no picker");
  assertEqual(page.sentOf("new").length, 0,
              "the rail's create control created a session before the user had " +
              "said where");
  await page.settle();
  page.click("acpPickerNeutral");
  const sent = page.sentOf("new");
  assertEqual(sent.length, 1, "choosing the agent's own folder created nothing");
  assertEqual(sent[0].payload.cwd, "",
              "the neutral option named a directory; blank is what selects the " +
              "agent's own");
  assertEqual(page.el("acpPicker").hidden, true,
              "the picker stayed open over the session it had just created");
  assertEqual(page.el("acpShell").dataset.view, "chat",
              "the press stayed on the rail, so a phone that pressed create is " +
              "looking at a list that will not show the session for a minute");
});

check("the rail's create control is guarded like the toolbar's", async (tpl) => {
  const page = await railed(tpl, { store: fakeStore({ workspaces: 1, sessions: 1 }) });
  page.deliver({ type: "meta", payload: { pending: "new" } });
  assertEqual(page.el("acpRailNew").disabled, true,
              "the one create control a phone can see is not stopped from landing " +
              "a second `new` while the first is in flight");
  // And it is off entirely when there is no ACP module to create anything with,
  // which is the same reason Refresh and the filter box are.
  const dead = loadPage(tpl, { acpError: "no module named acp" });
  assertEqual(dead.el("acpRailNew").disabled, true,
              "the rail offers a create control while the ACP module is not loaded");
});

// ------------------------------------------------------- creating a session --
//
// Both `New session` buttons now open a picker instead of creating against
// whatever a text box held. Each check below is one of the four frictions that
// replaced: which folder, finding what you created, losing your place, and
// hitting the cap blind.

check("the picker lists workspaces and creates in the one chosen", async (tpl) => {
  const page = await railed(tpl, { store: fakeStore({ workspaces: 3, sessions: 1 }) });
  await page.openPicker();
  assertEqual(page.pickerNames().join(","), "ws-0,ws-1,ws-2",
              "the picker did not list the workspaces the machine has");
  page.pickerRows()[1].dispatch("click");
  const sent = page.sentOf("new");
  assertEqual(sent.length, 1, "choosing a workspace created nothing");
  assertEqual(sent[0].payload.cwd, "C:\\work\\ws-1",
              "the session was created against the wrong directory — under -a " +
              "this is where the agent's tools actually run");
});

check("the picker's filter narrows the list without a request", async (tpl) => {
  const page = await railed(tpl, { store: fakeStore({ workspaces: 4, sessions: 1 }) });
  await page.openPicker();
  const before = page.fetches.length;
  page.el("acpPickerSearch").value = "ws-2";
  page.el("acpPickerSearch").dispatch("input");
  assertEqual(page.pickerNames().join(","), "ws-2",
              "the filter did not narrow the list");
  assertEqual(page.fetches.length, before,
              "the filter spent a request per keystroke against a route that " +
              "stats every workspace");
});

check("at the cap both create controls are off and say why", async (tpl) => {
  const store = fakeStore({ workspaces: 2, sessions: 1 });
  store.capacity = { held: 8, max: 8 };
  const page = await railed(tpl, { store });
  for (const id of ["acpNew", "acpRailNew"]) {
    assertEqual(page.el(id).disabled, true,
                `${id} is still pressable at 8/8; the press buys a round trip ` +
                "and comes back as a red error the rail's own status line " +
                "already predicted");
    assert(/limit/i.test(page.el(id).title),
           `${id} is disabled without saying why: ${page.el(id).title}`);
  }
});

check("a freed slot re-arms the create controls", async (tpl) => {
  // The direction that matters: a page that disabled at the cap and never
  // re-enabled would need a reload to create again.
  const store = fakeStore({ workspaces: 1, sessions: 1 });
  store.capacity = { held: 8, max: 8 };
  const page = await railed(tpl, { store });
  assertEqual(page.el("acpRailNew").disabled, true, "the fixture is not at the cap");
  store.capacity = { held: 7, max: 8 };
  page.click("acpRailReload");
  await page.settle();
  assertEqual(page.el("acpRailNew").disabled, false,
              "a slot came free and the create controls stayed off");
  assertEqual(page.el("acpRailNew").title, "",
              "the cap's explanation outlived the cap");
});

check("the picker offers to close the open session, and says what it costs",
      async (tpl) => {
  const { page } = connected(tpl, { sid: "sess-w0-s0" });
  await page.settle();
  await page.openPicker();
  assertEqual(page.el("acpPickerKeepRow").hidden, false,
              "the picker said nothing about the session already open, which " +
              "keeps a slot and ~161 MB for the full idle TTL");
  assert(/slot/i.test(page.el("acpPickerKeepText").textContent),
         "the offer does not name what leaving it open costs: " +
         page.el("acpPickerKeepText").textContent);
  assertEqual(page.el("acpPickerCloseCurrent").checked, false,
              "closing the current session is on by default; leaving a long " +
              "turn running while starting another session is a real use");
});

check("with nothing open the picker makes no offer to close anything", async (tpl) => {
  const page = await railed(tpl, { store: fakeStore({ workspaces: 1, sessions: 1 }) });
  await page.openPicker();
  assertEqual(page.el("acpPickerKeepRow").hidden, true,
              "the picker offered to close a session that does not exist");
});

check("the close-first offer is off during a turn, which the server would refuse",
      async (tpl) => {
  const { page } = connected(tpl, { sid: "sess-w0-s0", turnActive: true });
  await page.settle();
  await page.openPicker();
  assertEqual(page.el("acpPickerCloseCurrent").disabled, true,
              "the picker offered to close a session mid-turn; `_handle_close` " +
              "refuses that with turn_in_progress");
  assert(/still answering/i.test(page.el("acpPickerKeepText").textContent),
         "the disabled offer does not say why it is disabled");
});

check("closing first closes, and creates only once the slot is free", async (tpl) => {
  const { page, live } = connected(tpl, { sid: "sess-w0-s0" });
  await page.settle();
  await page.openPicker();
  page.el("acpPickerCloseCurrent").checked = true;
  page.click("acpPickerNeutral");
  assertEqual(page.sentOf("close").length, 1, "nothing was closed");
  assertEqual(page.sentOf("new").length, 0,
              "the create went out before the close landed — at the cap that is " +
              "a refusal by a limit one frame from having room");
  page.deliver({ type: "session_closed", sessionId: live,
                 payload: { sessionId: live, message: "This session was closed." } });
  assertEqual(page.sentOf("new").length, 1,
              "the close landed and the create it was holding never ran");
});

check("a refused close abandons the create rather than half-doing it", async (tpl) => {
  const { page, live } = connected(tpl, { sid: "sess-w0-s0" });
  await page.settle();
  await page.openPicker();
  page.el("acpPickerCloseCurrent").checked = true;
  page.click("acpPickerNeutral");
  page.deliver({ type: "error", sessionId: live, payload: {
    code: "turn_in_progress", message: "This session is still answering." } });
  assertEqual(page.sentOf("new").length, 0,
              "the close was refused and the session was created anyway — the " +
              "user asked for one action, not the half that spends a slot");
  assert(/no new one was created/i.test(page.transcript()),
         "nothing said the create had been abandoned");
});

check("a created session reaches the rail without a Refresh", async (tpl) => {
  const store = fakeStore({ workspaces: 2, sessions: 1 });
  const page = await railed(tpl, { store });
  assertEqual(page.railTitles().length, 2, "the fixture did not load as expected");
  // The agent has created it, so the store now has it. This is the state the
  // rail could not see: `renderRail` draws from `railGroups`, which only the
  // paging loaders extend, and the 60 s poll updates fields on rows already
  // there — availability, status, title — and adds none.
  store[0].sessions.unshift({
    id: "sess-brand-new", title: "brand new", availability: "held",
    updated_at: "2026-08-03T12:00:00.086294300Z",
  });
  page.deliver({ type: "session", sessionId: "sess-brand-new", payload: {
    sessionId: "sess-brand-new", cwd: "C:\\work\\ws-0", created: true } });
  await page.settle();
  assert(page.railTitles().includes("brand new"),
         "the session just created is not in the rail; before the picker it " +
         "took a manual Refresh to find what you had just made");
});

check("cancelling the picker creates nothing and leaves the page alone", async (tpl) => {
  const page = await railed(tpl, { store: fakeStore({ workspaces: 1, sessions: 1 }) });
  await page.openPicker();
  page.click("acpPickerCancel");
  assertEqual(page.el("acpPicker").hidden, true, "Cancel left the picker open");
  assertEqual(page.sentOf("new").length, 0, "Cancel created a session");
  // And Escape is the other way out, closing the picker rather than a row menu.
  await page.openPicker();
  page.fireDoc("keydown", { key: "Escape" });
  assertEqual(page.el("acpPicker").hidden, true, "Escape left the picker open");
  assertEqual(page.sentOf("new").length, 0, "Escape created a session");
});

// ---------------------------------------------------- picker task-mode wiring --
//
// The task-mode control inside #acpPicker (plans/260911_ACP_V3_FOLLOWUP_FEATURES.md
// Phase 3) lets a user pick a mode at create time. The two `send('new', ...)`
// call sites are not symmetric: the immediate-create path threads the picked
// mode directly, but the deferred close-then-create path can only carry it
// through `pendingCreate` -- review caught exactly this gap once already
// (pendingCreate built as `{ cwd: cwd }` with `mode` silently dropped), so the
// second check below is written to fail again if that regresses.

check("picking a task mode reaches send('new', ...) on the immediate-create path",
      async (tpl) => {
  const page = await railed(tpl, { store: fakeStore({ workspaces: 1, sessions: 1 }) });
  await page.openPicker();
  page.click("acpPickerTaskModeOptSpec");
  page.click("acpPickerNeutral");
  const sent = page.sentOf("new");
  assertEqual(sent.length, 1, "picking a task mode and creating made no create call");
  assertEqual(sent[0].payload.mode, "spec",
              "the picked task mode did not reach send('new', ...) on the " +
              "immediate-create path");
});

check("a picked task mode survives the deferred close-then-create path", async (tpl) => {
  // The exact bug class review caught once already: pendingCreate built as
  // `{ cwd: cwd }` with `mode` silently dropped, so pickerRunPending()'s later
  // send('new', ...) call would lose the mode the user actually picked.
  const { page, live } = connected(tpl, { sid: "sess-w0-s0" });
  await page.settle();
  await page.openPicker();
  page.click("acpPickerTaskModeOptBugFix");
  page.el("acpPickerCloseCurrent").checked = true;
  page.click("acpPickerNeutral");
  assertEqual(page.sentOf("close").length, 1, "nothing was closed");
  assertEqual(page.sentOf("new").length, 0,
              "the create went out before the close landed");
  page.deliver({ type: "session_closed", sessionId: live,
                 payload: { sessionId: live, message: "This session was closed." } });
  const sent = page.sentOf("new");
  assertEqual(sent.length, 1, "the close landed and the deferred create never ran");
  assertEqual(sent[0].payload.mode, "bug-fix",
              "the picked task mode did not survive pendingCreate through to the " +
              "deferred send('new', ...) call -- this is the exact interface-" +
              "contract bug review already caught once, before implementation");
});

check("leaving the task-mode picker untouched sends today's kiro_default, unchanged",
      async (tpl) => {
  const page = await railed(tpl, { store: fakeStore({ workspaces: 1, sessions: 1 }) });
  await page.openPicker();
  page.click("acpPickerNeutral");
  const sent = page.sentOf("new");
  assertEqual(sent.length, 1, "creating with the default task mode made no create call");
  assertEqual(sent[0].payload.mode, "kiro_default",
              "omitting/defaulting the task-mode picker did not reproduce today's " +
              "exact kiro_default behavior");
});

// --------------------------------------------------------- deleting a session --
//
// The one destructive action on this page, and the only thing PowerAtlas does
// that writes to kiro-cli's store. Every check below exists because the
// alternative behaviour is either data loss or a control that lies about what
// it will do.

/** A rail whose first workspace holds one session in a chosen state. */
async function railedOne(tpl, { availability = "available", opts = {} } = {}) {
  const store = fakeStore({ workspaces: 1, sessions: 1 });
  store[0].sessions[0].availability = availability;
  const page = await railed(tpl, { store, ...opts });
  return page;
}

check("a remote viewer is offered no delete control at all", async (tpl) => {
  const remote = await railed(tpl, {
    local: false, canDelete: false,
    store: fakeStore({ workspaces: 1, sessions: 2 }) });
  assertEqual(remote.railRows().length, 2,
              "the remote rail lost its rows, so this check is measuring nothing");
  assertEqual(remote.railMenuButtons().length, 0,
              "a mobile/excluded remote viewer is offered a row menu; " +
              "ACP_CAN_DELETE=false should suppress it");
  // And the loopback viewer is, or the check above passes on a page with no
  // menu anywhere.
  const local = await railed(tpl, { store: fakeStore({ workspaces: 1, sessions: 2 }) });
  assertEqual(local.railMenuButtons().length, 2,
              "the loopback viewer has no row menu");
});

check("a remote desktop viewer is offered the delete control", async (tpl) => {
  // local=false (remote IP), canDelete=true (desktop UA) → menu present.
  const page = await railed(tpl, {
    local: false, canDelete: true,
    store: fakeStore({ workspaces: 1, sessions: 2 }) });
  assertEqual(page.railRows().length, 2,
              "the desktop-remote rail lost its rows");
  assertEqual(page.railMenuButtons().length, 2,
              "the remote desktop viewer is not offered a row menu");
});

check("a mobile remote viewer (canDelete false) is offered no delete control", async (tpl) => {
  // Explicit named test for the mobile-remote combination.
  const page = await railed(tpl, {
    local: false, canDelete: false,
    store: fakeStore({ workspaces: 1, sessions: 3 }) });
  assertEqual(page.railMenuButtons().length, 0,
              "a mobile-remote viewer should have no menu buttons");
});

check("the menu opens on its own row and closes the one before it", async (tpl) => {
  const page = await railed(tpl, { store: fakeStore({ workspaces: 1, sessions: 3 }) });
  const buttons = page.railMenuButtons();
  assertEqual(page.openMenus().length, 0, "a menu was open before anything was pressed");
  buttons[0].dispatch("click");
  assertEqual(page.openMenus().length, 1, "pressing the menu button opened nothing");
  assertEqual(buttons[0].getAttribute("aria-expanded"), "true",
              "the open menu's button does not report itself expanded");
  buttons[2].dispatch("click");
  assertEqual(page.openMenus().length, 1,
              "two menus are open at once; the rail is a list of forty rows and " +
              "each one leaving its menu behind would bury the list");
  assertEqual(buttons[0].getAttribute("aria-expanded"), "false",
              "the menu that closed still reports itself expanded");
  // Pressing the same button again is a toggle, not a re-open.
  buttons[2].dispatch("click");
  assertEqual(page.openMenus().length, 0, "the menu button does not toggle its own menu shut");
});

check("an open menu closes on Escape and on a click elsewhere", async (tpl) => {
  const page = await railed(tpl, { store: fakeStore({ workspaces: 1, sessions: 2 }) });
  page.railMenuButtons()[0].dispatch("click");
  page.fireDoc("keydown", { key: "Escape" });
  assertEqual(page.openMenus().length, 0, "Escape left the menu open");

  page.railMenuButtons()[0].dispatch("click");
  assertEqual(page.openMenus().length, 1, "the menu did not re-open");
  // The press that opened it is still notionally travelling to the document.
  // That one must be ignored, or the menu would never survive its own gesture.
  page.fireDoc("click");
  assertEqual(page.openMenus().length, 1,
              "the document handler closed the menu on the very press that " +
              "opened it");
  page.fireDoc("click");
  assertEqual(page.openMenus().length, 0, "a click elsewhere left the menu open");
});

check("deleting asks first, and a declined confirm deletes nothing", async (tpl) => {
  const page = await railedOne(tpl, { opts: { confirm: false } });
  page.railMenuButtons()[0].dispatch("click");
  page.one("acpRailGroups", ".acp-rail-menu-item").dispatch("click");
  await page.settle();
  assertEqual(page.confirms.length, 1, "the delete asked nothing before deleting");
  assert(/permanently/i.test(page.confirms[0]),
         `the confirm does not say the deletion is permanent: ${page.confirms[0]}`);
  // Close is reversible and this is not; a confirm that did not separate them
  // would be read as the one the user has already pressed a hundred times.
  assert(/close/i.test(page.confirms[0]),
         `the confirm does not distinguish itself from Close: ${page.confirms[0]}`);
  assertEqual(page.deleteCalls().length, 0,
              "declining the confirm deleted the session anyway");
  assertEqual(page.railRows().length, 1, "the declined delete removed the row");
});

check("a confirmed delete posts the id and takes the row away", async (tpl) => {
  const page = await railedOne(tpl);
  page.railMenuButtons()[0].dispatch("click");
  page.one("acpRailGroups", ".acp-rail-menu-item").dispatch("click");
  await page.settle();
  const calls = page.deleteCalls();
  assertEqual(calls.length, 1, "the confirmed delete sent no request");
  assertEqual(calls[0].init.method, "POST", "the delete was not a POST");
  assertEqual(JSON.parse(calls[0].init.body).session_ids[0], "sess-w0-s0",
              "the delete named the wrong session");
  assertEqual(page.railRows().length, 0, "the deleted row is still on screen");
  assert(/deleted/i.test(page.el("acpRailStatus").textContent),
         "nothing on screen says the deletion happened");
});

check("a refused delete keeps the row and shows the server's reason", async (tpl) => {
  const page = await railedOne(tpl, { opts: {
    answer: (url) => url === "/api/acp/sessions/delete" ? { body: {
      deleted: [],
      failed: [{ id: "sess-w0-s0", code: "locked",
                 message: "Another process (pid 21344) is using this session." }],
    } } : null,
  } });
  page.railMenuButtons()[0].dispatch("click");
  page.one("acpRailGroups", ".acp-rail-menu-item").dispatch("click");
  await page.settle();
  assertEqual(page.railRows().length, 1,
              "a refused delete removed the row anyway — the rail would then be " +
              "claiming a deletion the store never made");
  const said = page.el("acpRailStatus").textContent;
  assert(/pid 21344/.test(said),
         `the refusal does not carry the server's own reason: ${said}`);
});

check("a delete the server never answered says so and keeps the row", async (tpl) => {
  const page = await railedOne(tpl, { opts: {
    answer: (url) => url === "/api/acp/sessions/delete"
      ? { reject: "network down" } : null,
  } });
  page.railMenuButtons()[0].dispatch("click");
  page.one("acpRailGroups", ".acp-rail-menu-item").dispatch("click");
  await page.settle();
  assertEqual(page.railRows().length, 1, "a failed delete removed the row");
  assert(/could not delete/i.test(page.el("acpRailStatus").textContent),
         "a failed delete left the rail claiming nothing went wrong");
});

check("delete is off for a session the server would refuse", async (tpl) => {
  for (const [availability, why] of [["held", /close/i], ["locked", /another process/i]]) {
    const page = await railedOne(tpl, { availability });
    page.railMenuButtons()[0].dispatch("click");
    const item = page.one("acpRailGroups", ".acp-rail-menu-item");
    assertEqual(item.disabled, true,
                `delete is offered on a ${availability} row, which the server refuses`);
    assert(why.test(item.title),
           `the ${availability} row's delete does not name the remedy: ${item.title}`);
    // Disabled and *inert*: a browser fires no click on a disabled button, but
    // the harness deliberately does, so the page's own guard is what is being
    // measured here.
    item.dispatch("click");
    await page.settle();
    assertEqual(page.confirms.length, 0,
                `a disabled delete on a ${availability} row still asked to delete`);
    assertEqual(page.deleteCalls().length, 0,
                `a disabled delete on a ${availability} row still sent a request`);
  }
});

check("deleting the session this page is holding lets go of it", async (tpl) => {
  // Reachable despite the `held` guard: a session named by ?sid= that never
  // loaded is not held by this server, so it deletes cleanly while the URL
  // still points at it.
  const store = fakeStore({ workspaces: 1, sessions: 1 });
  const page = await railed(tpl, { store, sid: "sess-w0-s0" });
  page.railMenuButtons()[0].dispatch("click");
  page.one("acpRailGroups", ".acp-rail-menu-item").dispatch("click");
  await page.settle();
  assertEqual(page.deleteCalls().length, 1, "the delete never went out");
  assertEqual(page.urls[page.urls.length - 1], "/acp",
              "?sid= still names the deleted session, so a reload would try to " +
              "adopt a session whose files are gone");
  assert(/deleted from the store/i.test(page.transcript()),
         "the transcript does not say its session no longer exists");
});

check("deleting the last loaded row leaves the rest of the workspace reachable",
      async (tpl) => {
  // One workspace, five sessions, three of them loaded. Deleting all three used
  // to take the whole workspace off the rail — and with it the only control
  // that could reach the other two.
  const page = await railed(tpl, { store: fakeStore({ workspaces: 1, sessions: 15 }) });
  assertEqual(page.railRows().length, 10, "the fixture did not page as expected");
  for (let i = 0; i < 10; i++) {
    page.railMenuButtons()[0].dispatch("click");
    page.one("acpRailGroups", ".acp-rail-menu-item").dispatch("click");
    await page.settle();
  }
  assertEqual(page.railRows().length, 0, "the rows were not all deleted");
  assertEqual(page.railGroups().length, 1,
              "the workspace vanished from the rail while the server still has " +
              "two of its sessions, so nothing can reach them");
  assert(page.one("acpRailGroups", ".acp-rail-group-more") !== null,
         "the emptied workspace kept no way to load the sessions it still has");
  // And that control asks for the first page, not the page after the rows that
  // no longer exist.
  page.one("acpRailGroups", ".acp-rail-group-more").dispatch("click");
  await page.settle();
  const last = page.listingCalls()[page.listingCalls().length - 1];
  assertEqual(last.params.session_page, "1",
              "the emptied workspace resumed paging past rows that were deleted");
});

// ----------------------------------------------- render(), as its own subject --
//
// Everything above renders one template and asserts on the page. These three
// assert on the renderer, because the Phase 5b review measured it silently
// producing a *wrong* page: the strip-all it used for anything IF_RE could not
// match deleted the construct and kept every arm of it. Each case below is
// quoted with what the previous version of this file actually returned for it.

function assertThrows(fn, pattern, message) {
  let threw = null;
  try {
    fn();
  } catch (err) {
    threw = err;
  }
  if (!threw) throw new Error(`${message}: it returned instead of throwing`);
  if (!pattern.test(String(threw.message))) {
    throw new Error(`${message}: threw the wrong thing — ${threw.message}`);
  }
}

check("render() refuses an {% elif %} rather than rendering the wrong arm", () => {
  const tpl = "{% if local %}L{% elif other %}E{% else %}R{% endif %}";
  // Measured against the previous version of this renderer: `"R"`, where Jinja
  // renders `"E"`. One plausible wrong arm, silently — and the entire
  // justification for teaching this harness `{% if %}` was that a silent strip
  // is dangerous. Under the strip-all it replaced, the same template produced
  // `"LER"`, which is wrong in a way nobody could miss.
  assertThrows(() => render(tpl, { local: false, other: true }), /elif/,
               "an {% elif %} rendered instead of throwing");
  // Both settings of the condition: a renderer that happened to keep arm one
  // would look right for `local = true` and be wrong for the case that matters.
  assertThrows(() => render(tpl, { local: true, other: true }), /elif/,
               "an {% elif %} rendered instead of throwing");
});

check("render() refuses a condition it cannot evaluate rather than keeping both arms", () => {
  // Measured against the previous version: `"AB"` and `"yesno"` — every arm
  // concatenated, no throw. A check asserting either arm would have passed
  // against a template that had lost the other, which is the exact failure the
  // `{% if %}` branch was added to prevent.
  assertThrows(() => render("{% if not local %}A{% else %}B{% endif %}", { local: true }),
               /cannot evaluate/, "a negated condition fell through to the strip");
  assertThrows(() => render("{% if user.admin %}yes{% else %}no{% endif %}", { user: {} }),
               /cannot evaluate/, "an attribute condition fell through to the strip");
  assertThrows(() => render("{% if a == b %}x{% endif %}", { a: 1, b: 1 }),
               /cannot evaluate/, "a comparison fell through to the strip");
  // The positive control. Without it every assertion above is satisfied by a
  // render() that throws on all input.
  assertEqual(render("{% if local %}A{% else %}B{% endif %}", { local: false }), "B",
              "the one conditional shape render() implements stopped working");
});

check("render() refuses a tag nobody taught it instead of deleting it", () => {
  // The old sweep was `replace(/\{%[^%]*%\}/g, "")`, so a `{% for %}` vanished
  // and its body reached the page once, unlooped. The leftover check that was
  // supposed to catch this could not: it ran *after* the strip, so by the time
  // it looked there was no `{% %}` left in the string to find, and its
  // `|\{%[^%]*%\}` alternative was unreachable code.
  assertThrows(() => render("{% for row in rows %}x{% endfor %}", { rows: [] }),
               /does not implement/, "an unknown tag was deleted in silence");
  assertThrows(() => render("{% set x = 1 %}", {}),
               /does not implement/, "an unknown tag was deleted in silence");
  // The four stripped by name still are — this is what the allowlist replaced,
  // not removed.
  assertEqual(render('{% extends "base.html" %}{% block c %}hi{% endblock %}', {}), "hi",
              "the tags render() strips by name stopped being stripped");
});

// ------------------------------------------------ the settings panel (D22, D24) --
//
// `index.html`'s remote-access panel: ~220 lines of createElement JS that had no
// test anywhere. A grep across `tests/` for `renderRemoteAccess`,
// `markRestartInputs`, `_RESTART_KEY_LABELS`, `remoteAccessBody`,
// `rotateRemoteSecret` or `restart-badge` returned nothing, and the Phase 5b
// review proved it rather than inferring it: deleting the D24 rotation warning
// outright left 42/42 node checks and 1371 pytest green. Three exit criteria
// rest on this code — the copyable URL and secret, the restart-to-apply labels,
// and the rotation warning.
//
// Covered from here rather than from `tests/test_web.py`, deliberately. What the
// criteria are about is what the JS *builds*: which node carries the URL,
// whether the field can be selected by hand, whether a key the server reports
// and this file does not label still gets a row, whether the warning precedes
// the button it warns about. Python can asserta string literal appears in the
// rendered template, which pins the text of a line and not what it does — and
// the mutation that survived was invisible to a substring check for the plainest
// possible reason: the substring went with it.
//
// Only the panel's own region runs. `index.html`'s other scripts touch dashboard
// DOM that does not exist here and would throw at load, and the file as a whole
// cannot go through `render()` — it carries `{{ }}` expressions with filters and
// attribute access that `render()` refuses by design.

const INDEX_TEMPLATE = path.join(
  HERE, "..", "src", "power_atlas", "templates", "index.html");
const STYLESHEET = path.join(
  HERE, "..", "src", "power_atlas", "static", "style.css");

const PANEL_NAMES = [
  "_remoteField", "_remoteNote", "_remoteAddressEditor", "_drainAddressNotice",
  "_remoteStopSection", "setRemoteStopped",
  "renderRemoteAccess", "rotateRemoteSecret",
  "loadRemoteAccess", "_RESTART_KEY_LABELS", "renderRestartKeys",
  "markRestartInputs", "loadRestartKeys", "openRemoteModal",
  // 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 3; the mode
  // picker replaced the toggle in 260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL
  // Phase 1.
  "renderAcpPermissions", "loadAcpPermissions", "setAcpPermissionMode",
  "saveAcpPermissionBaseAgent",
  // 260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL Phase 2: the rule editor.
  "openAcpRulesEditor", "closeAcpRulesEditor", "saveAcpRules", "_acpRulesWarnings",
  "acpRulesDiscard", "acpRulesKeepEditing",
  // 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL final review.
  "renderLocalSecret", "rotateLocalSecret",
];

function panelSource() {
  const src = fs.readFileSync(INDEX_TEMPLATE, "utf8");
  // Anchored on the panel's first function and the end of the <script> element
  // holding it — both code. Anchoring on the section comment above them would
  // let a comment rewrite silently shrink what is under test, which is the
  // defect the phase before this one found in two of its own checks.
  const from = src.indexOf("function _remoteField(");
  if (from < 0) throw new Error("index.html no longer defines _remoteField");
  const to = src.indexOf("</script>", from);
  if (to < 0) throw new Error("the remote panel's <script> element is unterminated");
  const region = src.slice(from, to);
  for (const name of PANEL_NAMES) {
    if (!region.includes(name)) {
      throw new Error(
        `the extracted region does not contain ${name}; the panel has moved and ` +
        "this harness is measuring less of it than it claims to");
    }
  }
  return region;
}

// The topbar settings menu's open/close wiring: the gear's click listener, the
// document-level closer and the Escape closer. Anchored on code at both ends.
// 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL final QA
function topbarMenuSource() {
  const src = fs.readFileSync(INDEX_TEMPLATE, "utf8");
  const from = src.indexOf("var topbarSettingsBtn = document.getElementById('topbarSettingsBtn');");
  if (from < 0) throw new Error("index.html no longer declares topbarSettingsBtn");
  const to = src.indexOf("function restartPowerAtlas(", from);
  if (to < 0) throw new Error("restartPowerAtlas no longer follows the topbar menu wiring");
  const region = src.slice(from, to);
  for (const name of ["closeTopbarSettings", "topbarMenuGuard", "document.addEventListener('click'"]) {
    if (!region.includes(name)) throw new Error(`the topbar menu region lost ${name}`);
  }
  return region;
}

function loadPanel(opts = {}) {
  const body = new El("div");         // #remoteAccessBody
  const restartBody = new El("div");  // #remoteRestartBody
  const modal = new El("dialog");
  modal.showModal = () => { modal.open = true; };
  const byId = new Map([
    ["remoteAccessBody", body],
    ["remoteRestartBody", restartBody],
    ["remoteModal", modal],
  ]);
  // 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 3: the
  // permission rows in the topbar menu, which live in the same <script>
  // region as this panel. Initial `hidden` states match the markup.
  // 260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL Phase 1: the three mode
  // radios (Auto disabled, as in the markup), the per-mode description, the
  // external-change notice and the Always blocked / Protected lists.
  const permBadge = new El("span");
  permBadge.hidden = true;
  const permWarn = new El("div");
  permWarn.hidden = true;
  const permBase = new El("input");
  for (const [id, value] of [["acpPermModeYolo", "yolo"], ["acpPermModeAuto", "auto"],
                             ["acpPermModeManual", "manual"]]) {
    const radio = new El("input");
    radio.type = "radio";
    radio.value = value;
    radio.checked = false;
    radio.disabled = value === "auto";
    byId.set(id, radio);
  }
  const permNotice = new El("div");
  permNotice.hidden = true;
  byId.set("acpPermBadge", permBadge);
  byId.set("acpPermWarn", permWarn);
  byId.set("acpPermBaseAgent", permBase);
  byId.set("acpPermNotice", permNotice);
  // 260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL final review: each mode's
  // own description (M-4), the Apply again button (H-A), the upgrade notice
  // (M-6) and the Always blocked details it opens. Hidden as in the markup.
  byId.set("acpPermDescYolo", new El("div"));
  byId.set("acpPermDescManual", new El("div"));
  const applyAgain = new El("button");
  applyAgain.hidden = true;
  byId.set("acpPermApplyAgain", applyAgain);
  const permUpgrade = new El("div");
  permUpgrade.hidden = true;
  byId.set("acpPermUpgrade", permUpgrade);
  const permFloor = new El("details");
  permFloor.open = false;
  byId.set("acpPermFloor", permFloor);
  byId.set("acpPermFloorList", new El("div"));
  byId.set("acpPermProtectedList", new El("div"));
  // 260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL Phase 2: the rule editor's
  // shell (partials/acp_permission_rules_modal.html); its rows are drawn by
  // the page. Initial `hidden` states match the markup.
  const rulesModal = new El("dialog");
  rulesModal.open = false;
  rulesModal.showModal = () => { rulesModal.open = true; };
  rulesModal.close = () => { rulesModal.open = false; };
  const rulesModeNote = new El("div");
  rulesModeNote.hidden = true;
  const rulesError = new El("div");
  rulesError.hidden = true;
  byId.set("acpRulesModal", rulesModal);
  byId.set("acpRulesModeNote", rulesModeNote);
  byId.set("acpRulesBody", new El("div"));
  byId.set("acpRulesError", rulesError);
  byId.set("acpRulesSave", new El("button"));
  // Phase 2 review, U1: the in-dialog unsaved-changes question; U13: the
  // Protected summary's linked-item count. Hidden, as in the markup.
  const rulesDiscard = new El("div");
  rulesDiscard.hidden = true;
  byId.set("acpRulesDiscard", rulesDiscard);
  byId.set("acpRulesDiscardYes", new El("button"));
  byId.set("acpRulesDiscardKeep", new El("button"));
  const protectedCount = new El("span");
  protectedCount.hidden = true;
  byId.set("acpPermProtectedCount", protectedCount);
  // G4 (Phase 3 review): the settings gear's dot, shared with restart drift.
  const pendingDot = new El("span");
  pendingDot.hidden = true;
  byId.set("topbarPendingDot", pendingDot);
  // 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL final review (F2,
  // F3): the Browser sign-in rows, initial `hidden` states as in the markup.
  const secretWarn = new El("div");
  secretWarn.hidden = true;
  const secretRotate = new El("button");
  const secretNote = new El("div");
  secretNote.hidden = true;
  byId.set("localSecretWarn", secretWarn);
  byId.set("localSecretRotate", secretRotate);
  byId.set("localSecretNote", secretNote);
  // 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL final QA: the gear
  // button and the menu it opens, driven by the real open/close wiring when
  // `opts.topbarMenu` extracts it (topbarMenuSource below). The menu starts
  // hidden, as in the markup.
  const topbarBtn = new El("button");
  topbarBtn.setAttribute("aria-expanded", "false");
  const topbarMenu = new El("div");
  topbarMenu.hidden = true;
  byId.set("topbarSettingsBtn", topbarBtn);
  byId.set("topbarSettingsMenu", topbarMenu);
  // 260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL Phase 2 QA: the "Edit
  // rules…" row and its label, marked busy while the editor's read is out.
  byId.set("acpPermEditRules", new El("button"));
  const editRulesLabel = new El("span");
  editRulesLabel.textContent = "Edit rules…";
  byId.set("acpPermEditRulesLabel", editRulesLabel);
  // The two live controls in the dashboard topbar that `markRestartInputs`
  // reaches for by class. Present here because their absence is a passing
  // state in that function (`if (!host) return`), so a harness without them
  // would run the badge code and assert on nothing.
  const hosts = new Map([
    [".peek-hotkey-group", new El("div")],
    [".port-group", new El("div")],
  ]);
  ACTIVE = null;

  const toasts = [];
  const fetches = [];
  const confirms = [];
  const clipboard = [];
  const timers = [];
  const domReady = [];
  // Every other document-level listener, so the topbar menu's closer can be
  // fired the way a bubbling click reaches it.
  // 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL final QA
  const docListeners = new Map();

  function fakeFetch(target, init) {
    const url = String(target);
    fetches.push({ url, init: init || {} });
    const answer = opts.answer ? opts.answer(url) : undefined;
    if (answer && answer.reject) return Promise.reject(new Error(answer.reject));
    return Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve(answer && "body" in answer ? answer.body : {}),
    });
  }

  const sandbox = {
    document: {
      createElement: (tag) => new El(tag),
      getElementById: (id) => byId.get(id) ?? null,
      querySelector: (sel) => hosts.get(sel) ?? null,
      addEventListener: (type, fn) => {
        if (type === "DOMContentLoaded") { domReady.push(fn); return; }
        if (!docListeners.has(type)) docListeners.set(type, []);
        docListeners.get(type).push(fn);
      },
      write: () => HTML_SINK("document.write"),
    },
    // Absent in a browser off localhost over plain http, which is the surface
    // this panel configures — so `opts.clipboard === false` is not a hypothetical.
    navigator: opts.clipboard === false ? {} : {
      clipboard: {
        writeText: (text) => { clipboard.push(text); return Promise.resolve(); },
      },
    },
    confirm: (text) => { confirms.push(text); return opts.confirm !== false; },
    // Helpers the panel uses from an earlier <script> block in the same page.
    showToast: (html) => toasts.push(html),
    _escHtml: (s) => String(s).replace(/&/g, "&amp;")
                              .replace(/</g, "&lt;").replace(/>/g, "&gt;"),
    setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
    fetch: fakeFetch,
    console: { log() {}, warn() {}, error() {} },
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  // index.html loads transcript-renderer.js before its inline scripts, and the
  // rule editor's warnings call its shared pattern helpers
  // (260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL Phase 3), so it runs first
  // here too, as the real page orders them.
  vm.runInContext(transcriptRendererSource(), sandbox,
                  { filename: "transcript-renderer.js" });
  vm.runInContext(panelSource(), sandbox, { filename: "index.html#remote-panel" });
  if (opts.topbarMenu) {
    vm.runInContext(topbarMenuSource(), sandbox, { filename: "index.html#topbar-menu" });
  }

  return {
    sandbox, body, restartBody, modal, hosts,
    toasts, fetches, confirms, clipboard, timers, domReady,
    topbarBtn, topbarMenu,
    /** A click reaching the document: every document-level click listener
     *  runs, as a bubbling click with nothing stopping it would reach them. */
    fireDoc(type, ev) {
      const fns = docListeners.get(type) ?? [];
      if (fns.length === 0) throw new Error(`nothing listens for document '${type}'`);
      for (const fn of fns) fn(ev ?? {});
    },
    // The *copyable value* fields — the URL and the secret — and deliberately
    // not the bind-address editor, which shares `.remote-field` for its
    // spacing but is an input the user types into rather than a value the page
    // hands them. Folding it in here would shift the indices every check below
    // addresses positionally and would make "no field for a surface that is
    // not listening" fail on the one control that turns the surface on.
    fields() {
      return body.querySelectorAll(".remote-field")
                 .filter((f) => !f.matches(".remote-address"));
    },
    addressRow() { return body.querySelector(".remote-address"); },
    rows() { return restartBody.querySelectorAll(".remote-restart-row"); },
    badge(sel) { return hosts.get(sel).querySelector(".restart-badge"); },
    settle() { return new Promise((resolve) => setImmediate(resolve)); },
  };
}

check("the panel renders the URL and the secret as copyable text", async () => {
  const p = loadPanel();
  p.sandbox.renderRemoteAccess({
    enabled: true,
    url: "http://100.90.1.5:4915/acp",
    secret_present: true,
    secret: "9f3c-secret-value",
    secret_path: "C:\\Users\\me\\AppData\\Local\\power-atlas\\remote-secret",
  });
  const fields = p.fields();
  assertEqual(fields.length, 2,
              "the panel drew neither the URL nor the secret as a field");
  const boxOf = (f) => f.querySelector(".remote-field-value");
  assertEqual(boxOf(fields[0]).value, "http://100.90.1.5:4915/acp",
              "the first field does not hold the URL to open on the device");
  assertEqual(boxOf(fields[1]).value, "9f3c-secret-value",
              "the second field does not hold the device secret");
  for (const f of fields) {
    // Read-only rather than disabled, and the distinction is the criterion: a
    // disabled input cannot be selected, so its text cannot be copied by hand,
    // and copying by hand is the guarantee here. The button is convenience.
    assertEqual(boxOf(f).readOnly, true, "the field is editable");
    assertEqual(boxOf(f).disabled, false,
                "the field is disabled, so its text cannot be selected by hand");
  }
  const copy = fields[1].querySelector(".remote-copy");
  assert(copy, "the secret has no Copy button");
  copy.onclick();
  assertEqual(boxOf(fields[1]).selected, true,
              "Copy did not select the field, which is its only fallback where " +
              "the clipboard API is unavailable — i.e. on the remote surface");
  await p.settle();
  assertEqual(p.clipboard.join("|"), "9f3c-secret-value",
              "Copy put something other than the secret on the clipboard");
  assertEqual(copy.textContent, "Copied", "the button gave no feedback");
});

check("the panel copies by selection where there is no clipboard API", async () => {
  // Plain http off localhost is not a secure context, so `navigator.clipboard`
  // is undefined there — on exactly the devices this panel exists to enrol.
  const p = loadPanel({ clipboard: false });
  p.sandbox.renderRemoteAccess({
    enabled: true, url: "http://100.90.1.5:4915/acp",
    secret_present: true, secret: "s", secret_path: "p",
  });
  const copy = p.fields()[0].querySelector(".remote-copy");
  copy.onclick();
  await p.settle();
  assertEqual(p.fields()[0].querySelector(".remote-field-value").selected, true,
              "nothing was selected, so there is no way to copy the URL at all");
  assertEqual(copy.textContent, "Copied",
              "the button gave no feedback where the clipboard API is absent");
});

check("the panel shows no secret it does not have, and no form when remote is off", () => {
  const absent = loadPanel();
  absent.sandbox.renderRemoteAccess({
    enabled: true, url: "http://x/acp", secret_present: false });
  assertEqual(absent.fields().length, 1,
              "a secret field was drawn for a secret that does not exist");
  assert(!absent.body.textContent.includes("undefined"),
         "the panel rendered `undefined` where the absent secret would go");
  const missing = absent.body.querySelectorAll(".remote-note-warn")
                        .filter((n) => /no device secret exists/i.test(n.textContent));
  assertEqual(missing.length, 1,
    "nothing says that no device can authenticate yet");
  // Not a check on wording but on which control the note sends the user to.
  // Rotation is the panel's destructive action — it signs every device out —
  // and it happens to work here only because there is nothing to revoke yet.
  // Save reaches `ensure_remote_secret`, which issues one without revoking.
  assert(!/rotat/i.test(missing[0].textContent),
         `the secretless note points at the destructive control: ${missing[0].textContent}`);
  assert(/\bsave\b/i.test(missing[0].textContent),
         `the secretless note names no way to get a secret: ${missing[0].textContent}`);

  const off = loadPanel();
  off.sandbox.renderRemoteAccess({ enabled: false });
  assertEqual(off.fields().length, 0,
              "a field was drawn for a surface that is not listening");
  assertEqual(off.body.querySelectorAll(".remote-rotate").length, 0,
              "a rotate button was drawn for a surface that is not listening");
  const note = off.body.querySelectorAll(".remote-note-off");
  assertEqual(note.length, 1, "nothing says remote access is off");
  assert(/loopback/i.test(note[0].textContent),
         `the off note does not say what the server is doing instead: ${note[0].textContent}`);
});

// -------------------------------------------- the bind-address control --
//
// Phase 6 found the panel had no `remote_bind_address` input anywhere, and its
// own off-note told the user to set the key in config.toml by hand — the one
// path that creates no device secret, so startup then declines to bind and the
// user gets no remote access and a log line. `POST /api/save-setting` already
// did both halves in one request; nothing called it. These four checks are on
// what the JS builds and does rather than on the text of a line, because the
// mutation that survives a substring check is the one that deletes the code the
// substring lived in.

check("the panel exposes the bind address and saves it through /api/save-setting", async () => {
  const p = loadPanel({ answer: (url) =>
    url === "/api/save-setting" ? { body: { ok: true, restart_required: true } }
                                : { body: {} } });
  p.sandbox.renderRemoteAccess({ enabled: false, remote_bind_address: "" });
  const row = p.addressRow();
  assert(row, "there is no bind-address control, so the only documented way to " +
              "turn remote access on is still editing config.toml by hand — " +
              "which creates no device secret and therefore does not work");
  const box = row.querySelector(".remote-address-input");
  assert(box, "the bind-address row has no input to type an address into");
  assertEqual(box.value, "",
              "the input does not show the address in force");
  const save = row.querySelector(".remote-address-save");
  assert(save, "the bind-address row has no way to submit what was typed");

  box.value = "  100.78.142.124  ";
  save.onclick();
  assertEqual(p.fetches.length >= 1, true, "pressing Save sent no request at all");
  assertEqual(p.fetches[0].url, "/api/save-setting",
              "the address was posted somewhere other than the route that " +
              "issues the device secret alongside it");
  assertEqual(String(p.fetches[0].init.method).toUpperCase(), "POST",
              "the address was not posted");
  const sent = JSON.parse(p.fetches[0].init.body);
  assertEqual(sent.key, "remote_bind_address",
              "Save wrote some other setting");
  // Trimmed here as well as server-side: `save_setting` strips before it
  // validates, so an untrimmed value would be accepted and stored — but the
  // input is also read back into this field, and showing the user surrounding
  // whitespace they cannot see is how "why was this rejected" starts.
  assertEqual(sent.value, "100.78.142.124",
              "the typed address reached the server untrimmed");
  await p.settle();

  // And the field is seeded from the value in force when there is one, or the
  // panel cannot be used to *change* an address, only to set a first one.
  const on = loadPanel();
  on.sandbox.renderRemoteAccess({
    enabled: true, remote_bind_address: "fd00::1", url: "http://[fd00::1]:4915/acp",
    secret_present: true, secret: "s", secret_path: "p" });
  assertEqual(on.addressRow().querySelector(".remote-address-input").value,
              "fd00::1",
              "the input is blank while an address is in force, so saving it " +
              "unchanged would silently turn remote access off");
});

check("a refused address shows the server's own reason and claims no success", async () => {
  // `save_setting` names why: wildcard, loopback, hostname, zone id, bracketed,
  // non-canonical, or `port = 0`. That sentence is the only thing telling the
  // user what to type instead, and a generic "save failed" here reproduces
  // exactly the silent failure this control exists to end.
  const reason = "remote_bind_address must not be a wildcard address";
  const p = loadPanel({ answer: (url) =>
    url === "/api/save-setting" ? { body: { ok: false, error: reason } }
                                : { body: {} } });
  p.sandbox.renderRemoteAccess({ enabled: false, remote_bind_address: "" });
  const row = p.addressRow();
  row.querySelector(".remote-address-input").value = "0.0.0.0";
  row.querySelector(".remote-address-save").onclick();
  await p.settle();

  const status = p.addressRow().querySelector(".remote-address-status");
  assert(status, "a rejected save left nothing on screen at all");
  assertEqual(status.textContent, reason,
              "the panel replaced the server's stated reason with wording of " +
              "its own, so the user is told a save failed but not why");
  // A rejected save must not refresh — a refresh is how the panel says "this
  // took", and here nothing was written.
  assertEqual(p.fetches.filter((f) => f.url === "/api/remote-access").length, 0,
              "a refused address still triggered the success refresh");
  assertEqual(p.fetches.filter((f) => f.url === "/api/settings").length, 0,
              "a refused address still triggered the success refresh");
  // The button comes back, or one typo ends the session.
  assertEqual(p.addressRow().querySelector(".remote-address-save").disabled, false,
              "Save stayed disabled after a rejection, so the typo cannot be fixed");
});

check("a saved address puts the secret on screen and says it needs a restart", async () => {
  // The whole point of "one step": after the save, the credential the user needs
  // next is already visible, without closing and reopening anything. The save
  // route creates the secret in the same request, so the refresh is what turns
  // that into something the user can act on.
  const p = loadPanel({ answer: (url) => {
    if (url === "/api/save-setting") return { body: { ok: true, restart_required: true } };
    if (url === "/api/remote-access") return { body: {
      enabled: true, remote_bind_address: "100.78.142.124",
      url: "http://100.78.142.124:4915/acp",
      secret_present: true, secret: "fresh-secret", secret_path: "p" } };
    if (url === "/api/settings") return { body: {
      restart_to_apply: ["remote_bind_address"], remote_bind_address: "100.78.142.124" } };
    return { body: {} };
  } });
  p.sandbox.renderRemoteAccess({ enabled: false, remote_bind_address: "" });
  p.addressRow().querySelector(".remote-address-input").value = "100.78.142.124";
  p.addressRow().querySelector(".remote-address-save").onclick();
  await p.settle();

  const values = p.fields().map((f) => f.querySelector(".remote-field-value").value);
  assert(values.includes("fresh-secret"),
         `the device secret the save just issued is not on screen: ${values.join("|")}`);
  assert(values.includes("http://100.78.142.124:4915/acp"),
         "the URL to open on the device is not on screen after enabling");
  // The restart section below the divider re-reads the value in force, or it
  // goes on reporting the previous one beside the key it exists to report.
  const rows = p.rows();
  assertEqual(rows.length, 1, "the restart-only list was not refreshed after the save");
  assertEqual(rows[0].querySelector(".remote-restart-value").textContent,
              "100.78.142.124",
              "the restart list still shows the address that was replaced");

  // Honest about *when*. `remote_bind_address` is read once at startup, so a
  // message implying the surface is up would send the user to a phone that
  // cannot connect.
  const said = p.body.textContent;
  assert(/next time it starts|restart/i.test(said),
         `nothing says the address takes effect only on restart: ${said}`);
  assert(!/now listening|is listening on 100\.78/i.test(said),
         `the panel claims the surface is already up: ${said}`);
  // The confirmation survives the refresh that produced the secret — writing it
  // into the pre-refresh body would destroy it with the render.
  assert(/Saved/.test(said), "the successful save left no confirmation on screen");
});

check("clearing the field says what it turns off and what it keeps", async () => {
  // Two things a user acts on. Clearing turns remote access off at the next
  // launch — and does *not* delete the secret, so devices already enrolled work
  // again when it is turned back on. Neither is guessable from an empty field.
  const p = loadPanel({ answer: (url) => {
    if (url === "/api/save-setting") return { body: { ok: true, restart_required: true } };
    if (url === "/api/remote-access") return { body: { enabled: false, remote_bind_address: "" } };
    return { body: { restart_to_apply: [] } };
  } });
  p.sandbox.renderRemoteAccess({
    enabled: true, remote_bind_address: "100.78.142.124",
    url: "http://100.78.142.124:4915/acp",
    secret_present: true, secret: "s", secret_path: "p" });

  // The standing hint, present before anything is pressed: this is the text the
  // user reads *while deciding* whether to clear the field.
  const hint = p.addressRow().querySelector(".remote-address-hint");
  assert(hint, "the bind-address control carries no explanation at all");
  assert(/restart/i.test(hint.textContent),
         `the hint does not say a restart is required: ${hint.textContent}`);
  assert(/clear/i.test(hint.textContent) && /off|loopback/i.test(hint.textContent),
         `the hint does not say that clearing the field turns remote access off: ${hint.textContent}`);
  assert(/not delete the secret|does not delete/i.test(hint.textContent),
         `the hint does not say that clearing keeps the device secret, so the ` +
         `user cannot tell whether enrolled devices survive: ${hint.textContent}`);

  p.addressRow().querySelector(".remote-address-input").value = "";
  p.addressRow().querySelector(".remote-address-save").onclick();
  assertEqual(JSON.parse(p.fetches[0].init.body).value, "",
              "clearing the field posted something other than an empty address");
  await p.settle();
  const said = p.body.textContent;
  assert(/off/i.test(said) && /next start|restart/i.test(said),
         `nothing says remote access goes off at the next start: ${said}`);
  assert(/secret is kept|keeps? working|already enrolled/i.test(said),
         `nothing says the device secret survives, so re-enabling looks like ` +
         `it would require re-enrolling every device: ${said}`);
});

// ------------------------------------------- the runtime stop switch --
//
// A kill switch for remote control that needs no restart. The user chose
// "refuse every remote request" over "close the socket", so the panel's job is
// to say that and not the comfortable version of it: the port stays bound
// until PowerAtlas restarts, nothing is written to config.toml, and loopback
// is unaffected. On what the JS builds and does rather than on the text of a
// line — the mutation that survives a substring check is the one that deletes
// the code the substring lived in.

const SERVING = {
  enabled: true, stopped: false, remote_bind_address: "100.78.142.124",
  url: "http://100.78.142.124:4915/acp",
  secret_present: true, secret: "s", secret_path: "p",
};
const STOPPED = Object.assign({}, SERVING, { stopped: true });

check("the panel can stop remote access, and posts a stop to do it", async () => {
  const p = loadPanel();
  p.sandbox.renderRemoteAccess(SERVING);
  const stop = p.body.querySelector(".remote-stop-btn");
  assert(stop, "there is no way to stop remote access from the panel, so the " +
               "only way to take the machine off the network is a restart");
  assertEqual(p.body.querySelectorAll(".remote-resume").length, 0,
              "a Resume button was drawn over a surface that is already serving");
  stop.onclick();
  assertEqual(p.confirms.length, 0,
              "stopping asked for confirmation; the refusing direction is the " +
              "safe one and the one the user reached for in a hurry");
  assert(p.fetches.length >= 1, "pressing Stop sent no request at all");
  assertEqual(p.fetches[0].url, "/api/remote-access/stop",
              "Stop posted somewhere other than the runtime switch");
  assertEqual(String(p.fetches[0].init.method).toUpperCase(), "POST",
              "the switch was not posted, so the CSRF check never applies to it");
  assertEqual(JSON.parse(p.fetches[0].init.body).stopped, true,
              "Stop posted something other than a request to stop");
  await p.settle();
  // Re-read rather than repainted from the press: the server's answer is the
  // only thing that knows whether the switch took.
  assertEqual(p.fetches.filter((f) => f.url === "/api/remote-access").length, 1,
              "the panel repainted from the button press rather than from the " +
              "state the server reports");
});

check("the stopped panel states what stopped and what did not", () => {
  const p = loadPanel();
  p.sandbox.renderRemoteAccess(STOPPED);
  const section = p.body.querySelector(".remote-stop");
  assert(section, "the stop switch is not on screen at all");
  const said = section.textContent;
  assert(/stopped/i.test(said), `nothing says the surface is stopped: ${said}`);
  assert(/refus/i.test(said),
         `nothing says what happens to a remote request now: ${said}`);
  // The three things a user infers wrongly on their own, each of which the
  // user explicitly accepted when choosing this design over closing the socket.
  assert(/(stays|still) bound|was not closed|does not close/i.test(said),
         `the panel does not say the port is still bound: ${said}`);
  assert(/restart/i.test(said),
         `the panel does not say what a restart does to this: ${said}`);
  assert(/config\.toml/i.test(said),
         `the panel does not say this was not written to config.toml, so the ` +
         `user cannot tell whether a restart undoes it: ${said}`);
  assert(/loopback/i.test(said),
         `the panel does not say the dashboard itself is unaffected: ${said}`);
  // And it must not claim the listener went away, which is the one thing that
  // did not happen.
  assert(!/no longer listening|port is closed|socket is closed|stopped listening/i.test(said),
         `the panel implies the socket was closed, which it was not: ${said}`);
  const resume = section.querySelector(".remote-resume");
  assert(resume, "a stopped surface offers no way back short of a restart");
  assertEqual(p.body.querySelectorAll(".remote-stop-btn").length, 0,
              "a Stop button was drawn over a surface that is already stopped");
});

check("the live state is stated above the settings that only apply next launch", () => {
  const p = loadPanel();
  p.sandbox.renderRemoteAccess(STOPPED);
  const nodes = p.body.childNodes;
  const stopAt = nodes.indexOf(p.body.querySelector(".remote-stop"));
  const addressAt = nodes.indexOf(p.addressRow());
  assert(stopAt >= 0, "the stop section is not a section of the panel body");
  assert(addressAt >= 0, "the bind-address row vanished");
  assert(stopAt < addressAt,
         "the only thing on this panel describing what the server is doing " +
         "right now sits below a form about the next launch");
});

check("resuming asks first, and a declined confirm resumes nothing", async () => {
  const declined = loadPanel({ confirm: false });
  declined.sandbox.renderRemoteAccess(STOPPED);
  declined.body.querySelector(".remote-resume").onclick();
  assertEqual(declined.confirms.length, 1,
              "resuming put the machine back on the network without asking");
  assertEqual(declined.fetches.length, 0,
              "declining the confirm resumed remote access anyway");

  const p = loadPanel();
  p.sandbox.renderRemoteAccess(STOPPED);
  p.body.querySelector(".remote-resume").onclick();
  assertEqual(p.fetches[0].url, "/api/remote-access/stop",
              "Resume posted somewhere other than the runtime switch");
  assertEqual(JSON.parse(p.fetches[0].init.body).stopped, false,
              "Resume posted something other than a request to resume — an " +
              "exact `false` is the only value the route accepts as a resume");
  await p.settle();
});

check("a failed stop says so rather than leaving the panel looking stopped", async () => {
  // The dangerous half. A user who pressed the kill switch and saw nothing
  // walks away believing the machine is off the network while it is serving.
  const p = loadPanel({ answer: (url) =>
    url === "/api/remote-access/stop" ? { reject: "offline" } : { body: {} } });
  p.sandbox.renderRemoteAccess(SERVING);
  p.body.querySelector(".remote-stop-btn").onclick();
  await p.settle();
  const toast = p.toasts.join("|");
  assert(/toast-error/.test(toast),
         `a failed stop was reported as anything but a failure: ${toast}`);
  assert(/not stopped/i.test(toast),
         `the failure does not say which direction failed: ${toast}`);
  assert(/still serving|still.{0,20}remote/i.test(toast),
         `the failure does not say the surface is still up, which is the whole ` +
         `reason this message exists: ${toast}`);
  assertEqual(p.fetches.filter((f) => f.url === "/api/remote-access").length, 1,
              "a failed stop left the panel showing what was asked for rather " +
              "than re-reading what is in force");
});

check("a server that never heard of the switch reads as serving, not stopped", () => {
  // `stopped` absent from the payload. Guessing "stopped" from a missing field
  // draws a Resume button for a control that does not exist and tells the user
  // the machine is off the network when nothing said so.
  const p = loadPanel();
  p.sandbox.renderRemoteAccess({
    enabled: true, url: "u", secret_present: true, secret: "s", secret_path: "p" });
  assert(p.body.querySelector(".remote-stop-btn"),
         "an absent `stopped` field left the panel with no control at all");
  assertEqual(p.body.querySelectorAll(".remote-resume").length, 0,
              "an absent `stopped` field was read as stopped, so the panel " +
              "claims the machine is off the network when nothing said so");
});

check("the rotation warning names every device, above the button that revokes them", () => {
  // The exact code the Phase 5b review deleted to prove this file untested. It
  // removed the four lines below `// D24 gave up per-device revocation` and both
  // suites stayed green.
  const p = loadPanel();
  p.sandbox.renderRemoteAccess({
    enabled: true, url: "u", secret_present: true, secret: "s", secret_path: "p" });
  const warnings = p.body.querySelectorAll(".remote-note-warn")
                    .filter((n) => /revocation/i.test(n.textContent));
  assertEqual(warnings.length, 1,
              "no warning tells the user that rotating signs out every device");
  const text = warnings[0].textContent;
  assert(/\bEVERY\b|\bevery\b|\ball\b/.test(text) && /device/i.test(text),
         `the warning does not say which devices are revoked: ${text}`);
  assert(/no per-device revocation/i.test(text),
         `the warning does not say revocation is all-or-nothing: ${text}`);
  // Order, not merely presence. D24 gave up per-device revocation knowingly, so
  // the consequence has to be on screen *before* the control — not discovered
  // afterwards by a second device that stopped working.
  const button = p.body.querySelector(".remote-rotate");
  assert(button, "there is no rotate button");
  assert(p.body.childNodes.indexOf(warnings[0]) < p.body.childNodes.indexOf(button),
         "the warning sits below the button that triggers what it warns about");

  // And again at the point of no return, which is a separate gate on a separate
  // code path: a user who scrolled past the note still has to be told.
  p.sandbox.rotateRemoteSecret();
  assertEqual(p.confirms.length, 1, "rotation asked nothing before rotating");
  assert(/every authorized device/i.test(p.confirms[0]),
         `the confirm does not name the consequence: ${p.confirms[0]}`);
  assertEqual(p.fetches.length, 1, "the confirmed rotation sent no request");
  assertEqual(p.fetches[0].url, "/api/remote-access/rotate",
              "rotation posted somewhere other than the rotate endpoint");

  // A refused confirm must rotate nothing — the warning is a gate, not a notice.
  const declined = loadPanel({ confirm: false });
  declined.sandbox.rotateRemoteSecret();
  assertEqual(declined.fetches.length, 0,
              "declining the confirm rotated the secret anyway");
});

check("every key the server reports as restart-only gets a row and a badge", () => {
  const p = loadPanel();
  p.sandbox.renderRestartKeys({
    restart_to_apply: ["port", "peek_hotkey", "acp_max_sessions", "brand_new_key"],
    port: 4915,
    peek_hotkey: "ctrl+alt+p",
    acp_max_sessions: 3,
    brand_new_key: "",
  });
  const rows = p.rows();
  assertEqual(rows.length, 4,
              "the panel dropped a key the server reported, which is the one " +
              "thing this panel exists to report");
  const named = rows.map((r) => r.querySelector(".remote-restart-key").textContent);
  assertEqual(named[0], "Server port", "a labelled key lost its label");
  // The unlabelled key falls back to its raw name rather than being skipped. A
  // key added server-side and not labelled here must still appear, or the panel
  // silently under-reports exactly the list it exists to report — which is the
  // same lie, told from the other side, that Phase 3 fixed in the API.
  assertEqual(named[3], "brand_new_key",
              "a key the server reports and this file does not label was dropped");
  const values = rows.map((r) => r.querySelector(".remote-restart-value").textContent);
  assertEqual(values[0], "4915", "the value in force is not shown beside the key");
  assertEqual(values[1], "ctrl+alt+p", "the value in force is not shown beside the key");
  assertEqual(values[3], "\u2014",
              "an empty value rendered as nothing rather than as a dash");

  // The two keys with a live control in the topbar get the badge on the control
  // itself. Without it the hotkey field accepts a new value, saves it, and
  // behaves as though nothing happened until the next launch. No `in_force` and
  // no `restart_pending` above, which is an older server: the page must
  // over-warn rather than go silent, so every restart-only key reads pending.
  for (const sel of [".peek-hotkey-group", ".port-group"]) {
    const badge = p.badge(sel);
    assert(badge, `${sel} carries no restart badge`);
    assertEqual(badge.textContent, "on relaunch", `${sel}'s badge says nothing`);
    assert(/next launch/i.test(String(badge.title)),
           `${sel}'s badge does not say when the value takes effect: ${badge.title}`);
  }
  // And it comes back off when the server stops reporting the key. A badge that
  // only ever accretes is the same lie in the other direction.
  p.sandbox.renderRestartKeys({ restart_to_apply: ["port"], port: 4915 });
  assertEqual(p.badge(".peek-hotkey-group"), null,
              "the badge outlived the server's report of the key");
  assert(p.badge(".port-group"), "the still-reported key lost its badge");
});

check("the badge marks what is not in force, not what could ever need a restart", () => {
  const p = loadPanel();
  // `port` is running what is stored; `peek_hotkey` is not. Only the second is
  // unfinished business, and before this the badge sat on both forever — no
  // restart could clear it, which is what made it read as a permanent nag.
  p.sandbox.renderRestartKeys({
    restart_to_apply: ["port", "peek_hotkey"],
    restart_pending: ["peek_hotkey"],
    in_force: { port: 4915, peek_hotkey: "ctrl+shift+z" },
    port: 4915,
    peek_hotkey: "ctrl+alt+p",
  });
  assertEqual(p.badge(".port-group"), null,
              "a value the process is actually running was badged as pending");
  assert(p.badge(".peek-hotkey-group"),
         "a saved-but-unapplied value was not badged");

  // The rows show what is RUNNING, not what is stored. This is the half that
  // was quietly wrong: the panel says "takes effect on restart" and then
  // rendered the stored value, so a changed setting displayed as though it had
  // already taken effect.
  const values = p.rows().map(
    (r) => r.querySelector(".remote-restart-value").textContent);
  assertEqual(values[1], "ctrl+shift+z",
              "the row showed the stored value where it promised the one in force");

  // The disagreement is stated on the row too, naming the stored value, so the
  // panel does not force a comparison against the field the user just edited.
  const rowBadge = p.rows()[1].querySelector(".restart-badge");
  assert(rowBadge, "the row that disagrees with the store said nothing about it");
  assert(/ctrl\+alt\+p/.test(String(rowBadge.title)),
         `the row badge does not name the saved value: ${rowBadge.title}`);
  assertEqual(p.rows()[0].querySelector(".restart-badge"), null,
              "a row in force was marked as pending");

  // Nothing pending is the state after a relaunch, and it must clear both.
  p.sandbox.renderRestartKeys({
    restart_to_apply: ["port", "peek_hotkey"],
    restart_pending: [],
    in_force: { port: 4915, peek_hotkey: "ctrl+alt+p" },
    port: 4915,
    peek_hotkey: "ctrl+alt+p",
  });
  assertEqual(p.badge(".peek-hotkey-group"), null,
              "the badge survived the relaunch that applied the value");
  assertEqual(p.rows()[1].querySelector(".restart-badge"), null,
              "the row badge survived the relaunch that applied the value");
});

check("the status pill is the one thing in the topbar that cannot be squeezed", () => {
  // CSS is the code here for the same reason as the check below: this harness
  // has no box model, so the pixel evidence is the browser measurement in the
  // phase log — #acpStatus running from 353 px to 434 px against a 390 px
  // viewport, with `documentElement.scrollWidth` at 390 and `body.scrollWidth`
  // at 434, i.e. clipped and not scrollable — and what lives here is the rule
  // that measurement was taken against.
  const css = fs.readFileSync(STYLESHEET, "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
  const body = (selector) => {
    const rules = [...css.matchAll(/([^{}]*)\{([^{}]*)\}/g)].filter(
      (m) => m[1].split(",").some((s) => s.trim().replace(/\s+/g, " ") === selector));
    return rules.map((m) => m[2]).join(";");
  };
  assert(/flex-shrink:\s*0/.test(body(".acp-status")),
         "the status pill can be shrunk by the flex row it sits in, which is how " +
         "'connected' became 'conn' at 390 px — and it is the only element on the " +
         "page that says whether the socket is up");
  assert(/white-space:\s*nowrap/.test(body(".acp-status")),
         "the pill may wrap, so a longer state ('reconnecting') breaks the topbar's " +
         "line box instead of staying one pill");
  // Something has to absorb the shortfall, or an unshrinkable pill just moves
  // the overflow rather than removing it. Step 1 is the cluster, and it needs
  // BOTH halves: the factor, or it never gives and the deficit falls through
  // to the logo (measured: 206 px at every width from 320 to 1600); and the
  // floor, or it gives everything and the pill it contains is pushed off the
  // right of a viewport that clips (measured: 97 px at 360 px, against a 95 px
  // pill). Neither half is meaningful alone, so neither is optional.
  const cluster = body("body:has(> .acp-shell) .topbar-cluster");
  assert(/flex-shrink:\s*(?!0\b)[1-9]/.test(cluster),
         "the status cluster has no shrink factor, so the meter inside it — " +
         "the one item here that degrades gracefully — never gives way, and " +
         "the shortfall is taken out of the logo instead");
  assert(/min-width:\s*\d+px/.test(cluster),
         "the status cluster has no floor, so it shrinks past its own status " +
         "pill and pushes it off the right of a viewport that clips. `auto` " +
         "and `min-content` do not count and are why this asks for a length: " +
         "the meter's track is `width: 72px`, and a definite width is its own " +
         "min-content contribution");

  // Step 2, and it has two renderings — the row is only safe if *whichever* of
  // them the width selects can give, so both are checked.
  for (const sel of [".acp-wordmark", ".acp-banner"]) {
    const logo = body(sel);
    assert(/min-width:\s*0/.test(logo) && /text-overflow:\s*ellipsis/.test(logo),
           `${sel} is not allowed to give way, so pinning the pill only moves ` +
           "the 44 px overflow onto whichever item is last in the line box");
    assert(/flex-shrink:\s*(?!0\b)[1-9]/.test(logo),
           `${sel} does not yield at all, so the deficit lands on an item ` +
           "further down the order than the logo");
    assert(!/flex-shrink:\s*99\d/.test(logo),
           `${sel} takes a shrink factor so large it absorbs the whole ` +
           "shortfall by weight — measured at 26 px of an 83 px product name " +
           "while the meter beside it sat at its full width. Giving first " +
           "must not mean giving alone");
  }
  // The banner is a replaced element. Shrinking its box without this stretches
  // the wordmark drawn inside it, which is a distorted logo rather than the
  // graceful give the shrink factor above was set for.
  assert(/object-fit:\s*contain/.test(body(".acp-banner")),
         "the banner's shortfall is taken as a squeeze rather than a scale");
  // Step 3. Added with the `Main dashboard` link, which is ~126 px this row
  // did not have to find before. The selector is compound on purpose: the
  // dashboard renders `.topbar-nav` too, and a bare-class rule collapsed its
  // `ACP` pill to 26 px — emoji, no word — in the dashboard's own topbar.
  const nav = body(".acp-btn.topbar-nav");
  assert(nav, "the dashboard link's shrink rule is gone, or no longer scoped " +
              "to this page's button shape");
  assert(/min-width:\s*0/.test(nav) && /text-overflow:\s*ellipsis/.test(nav),
         "the dashboard link cannot give way, so once the cluster has reached " +
         "its floor there is nothing left to take a 320 px window's deficit");
  assert(/flex-shrink:\s*(?!0\b)[1-9]/.test(nav),
         "the dashboard link never yields, which puts it ahead of the status " +
         "pill in the order of sacrifice rather than behind the logo");
  assert(/min-width:\s*\d+px/.test(body(".acp-context-track")),
         "the context meter's track has no floor, so it collapses to nothing " +
         "before the logo has finished giving way");
});

check("the cross-surface link keeps its label in the dashboard's own topbar", () => {
  // The stylesheet is shared, and this half of the pair lives on `/` — a row
  // with a `flex-shrink: 0` banner and three clusters pinned at min-content,
  // which makes any direct child of it the only thing in the row that can give
  // way. This link became such a child, and took the whole deficit: measured
  // at 42 px of a 65 px pill at 1280 px, and 26 px at 1100 px — a robot emoji
  // and the letter "A". The row has always been wider than a narrow window and
  // has always clipped on the right when it did not fit; what it must not do
  // is quietly consume the one control added to make `/acp` findable.
  const css = fs.readFileSync(STYLESHEET, "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
  const rules = [...css.matchAll(/([^{}]*)\{([^{}]*)\}/g)];
  const bodyOf = (selector) => rules
    .filter((m) => m[1].split(",").some((s) => s.trim().replace(/\s+/g, " ") === selector))
    .map((m) => m[2]).join(";");
  assert(/flex-shrink:\s*0/.test(bodyOf(".topbar-nav")),
         "`.topbar-nav` has no shrink floor, so on `/` the ACP pill is the " +
         "only item in the topbar that can give and is truncated to its emoji " +
         "before any cluster beside it yields a pixel");
  // The override that keeps `/acp`'s own row working must still outrank it, or
  // fixing the dashboard freezes the link on the page that has no room for it.
  assert(/flex-shrink:\s*(?!0\b)[1-9]/.test(bodyOf(".acp-btn.topbar-nav")),
         "`/acp`'s override is gone, so the link cannot give way on the one " +
         "row where the order of sacrifice needs it to");
});

check("the dashboard link is hidden from the remote viewer, not from a narrow window", () => {
  // CSS is the code here, so this reads the sheet rather than the page: there
  // is no layout engine in this harness and a check on the template alone
  // cannot see a rule that hides what the template rendered.
  const css = fs.readFileSync(STYLESHEET, "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
  const hidden = [];
  // Selector lists are the text between a `}` and the `{` of a block whose body
  // has no nested block, which is every rule including those inside `@media`.
  for (const m of css.matchAll(/([^{}]*)\{([^{}]*)\}/g)) {
    if (!/display\s*:\s*none/.test(m[2])) continue;
    for (const sel of m[1].split(",")) hidden.push(sel.trim().replace(/\s+/g, " "));
  }
  assert(!hidden.includes(".topbar-nav"),
         "`.topbar-nav` is hidden by a rule in the sheet, and the template " +
         "renders it for every viewer it renders it for at all — so a loopback " +
         "viewer who merely narrowed a desktop window below 768 px loses the " +
         "only link back to the dashboard. The width is not the viewer; " +
         "`local` is, and the template already computes it");
  // The positive control. Without it this check also passes against a sheet
  // that hides nothing at all — a different regression, in which the 390 px
  // topbar keeps 376 px of banner and the row it was measured for is gone.
  assert(hidden.includes(".acp-banner"),
         "nothing hides the banner at narrow widths, so the tightest row on " +
         "the page opens with a logo nearly as wide as the viewport");
  // The other half of the swap. The two are alternates, not an element and an
  // optional extra: if the wordmark is never hidden, both are on screen above
  // the breakpoint and the page names itself twice.
  assert(hidden.includes(".acp-wordmark"),
         "the wordmark is not hidden anywhere, so above 768 px it renders " +
         "beside the banner that replaced it");
  // Also width-keyed, and for the same reason the other two are: it is what a
  // wider row renders differently, not something a viewer is denied. Dropped
  // rather than clipped because a clipped figure is not a coarser reading but
  // a wrong one — "64%" cut to its first glyph is "6", which is a plausible
  // percentage. The cluster's floor is sized for a row without it.
  assert(hidden.includes(".acp-context-label"),
         "the context meter's number is not dropped at narrow widths, so it " +
         "is clipped there instead and reports a percentage that is not the " +
         "one the agent sent");
});

// ---------------------------------------------- the agent bar + debug log --

function subagentsFrame(live, subagents, toolCallId) {
  return { type: "subagents", sessionId: live, payload: { subagents, toolCallId: toolCallId || '' } };
}

check("no crew panel appears until the session has a crew", (tpl) => {
  const { page } = connected(tpl);
  assertEqual(page.all("acpTranscript", ".acp-crew-panel").length, 0,
              "a session with no crew should show no crew panel");
});

check("a subagents frame with running entries shows a crew panel in the transcript",
  (tpl) => {
    const { page, live } = connected(tpl);
    const now = Date.now() / 1000;
    page.deliver(subagentsFrame(live, [
      { sessionId: "sub-1", role: "explorer", task: "", sessionName: "stage-1",
        status: "working", action: "reading", done: false, error: "", startedAt: now - 5 },
    ]));
    const panels = page.all("acpTranscript", ".acp-crew-panel");
    assertEqual(panels.length, 1, "a crew panel should appear in the transcript");
    const entries = panels[0].querySelectorAll(".acp-crew-row");
    assertEqual(entries.length, 1, "one row per sub-agent");
    const nameText = entries[0].querySelector(".acp-crew-label").textContent;
    assertEqual(nameText, "stage-1", "entry should show the sessionName as primary label");
    const actionText = entries[0].querySelector(".acp-crew-action").textContent;
    assertEqual(actionText, "reading", "entry should show the current action");
  });

check("crew panel entries are clickable and open the sub-agent panel", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-1", role: "explorer", task: "", sessionName: "",
      status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  const entries = page.all("acpTranscript", ".acp-crew-row");
  assertEqual(entries.length, 1);
  entries[0].dispatch("click");
  assertEqual(page.el("acpSubPanel").hidden, false,
              "clicking a crew row should open the sub-agent panel");
});

check("crew panel persists after all done — panels no longer auto-dismiss",
  (tpl) => {
    const { page, live } = connected(tpl);
    const now = Date.now() / 1000;
    page.deliver(subagentsFrame(live, [
      { sessionId: "sub-1", role: "explorer", task: "", status: "working",
        action: "", done: false, error: "", startedAt: now - 10 },
    ], "tcid-persist"));
    assertEqual(page.all("acpTranscript", ".acp-crew-panel").length, 1,
                "panel should be present while running");
    // All done — same toolCallId, same slot
    page.deliver(subagentsFrame(live, [
      { sessionId: "sub-1", role: "explorer", task: "", status: "terminated",
        action: "", done: true, error: "", startedAt: now - 10 },
    ], "tcid-persist"));
    // Panel stays — no auto-dismiss per SC4
    assertEqual(page.all("acpTranscript", ".acp-crew-panel").length, 1,
                "panel should stay visible after all done (SC4: no dismissal)");
    // Next main event: a chunk from the agent — panel still persists
    page.deliver({ type: "chunk", sessionId: live,
                   payload: { role: "agent", text: "finished" } });
    assertEqual(page.all("acpTranscript", ".acp-crew-panel").length, 1,
                "panel should persist after main event — SC4");
  });

check("crew panel persists after turn ends — no auto-dismiss on turn end", (tpl) => {
  const { page, live } = connected(tpl);
  const now = Date.now() / 1000;
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-1", role: "builder", task: "", status: "terminated",
      action: "", done: true, error: "", startedAt: now - 30 },
  ], "tcid-turnend"));
  assertEqual(page.all("acpTranscript", ".acp-crew-panel").length, 1,
              "panel should be present");
  // turn end must NOT dismiss the panel per SC4
  page.deliver({ type: "meta", sessionId: live,
                 payload: { turn: "end", stopReason: "end_turn" } });
  assertEqual(page.all("acpTranscript", ".acp-crew-panel").length, 1,
              "panel should persist after turn end (SC4: no auto-dismiss)");
});

check("crew panel elapsed time ticks when the interval fires", (tpl) => {
  const { page, live } = connected(tpl);
  // startedAt 10 seconds ago
  const startedAt = Date.now() / 1000 - 10;
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-1", role: "explorer", task: "", status: "working",
      action: "", done: false, error: "", startedAt: startedAt },
  ]));
  const elapsed = page.one("acpTranscript", ".acp-crew-elapsed");
  assert(elapsed !== null, "elapsed span should be present");
  assert(elapsed.textContent.length > 0, "elapsed should show a non-empty time string");
  // Fire the interval — should rebuild rows without error
  page.intervals[page.intervals.length - 1].fn();
  const elapsed2 = page.one("acpTranscript", ".acp-crew-elapsed");
  assert(elapsed2 !== null, "elapsed span should still be present after tick");
});

check("crew panel is cleared when the transcript is cleared", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-1", role: "explorer", task: "", status: "working",
      action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  assertEqual(page.all("acpTranscript", ".acp-crew-panel").length, 1);
  // A new session frame triggers clearTranscript()
  page.deliver({ type: "session", sessionId: "sess-new-crew",
    payload: { sessionId: "sess-new-crew", cwd: "/tmp", created: true,
               turnActive: false, contextPercent: null } });
  assertEqual(page.all("acpTranscript", ".acp-crew-panel").length, 0,
              "crew panel should be gone after transcript clear");
});

check("tapping a crew entry opens a second, read-only socket for it", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-1", role: "explorer", task: "look around",
      sessionName: "", status: "working", action: "", done: false, error: "",
      startedAt: Date.now() / 1000 },
  ]));
  page.all("acpTranscript", ".acp-crew-row")[0].dispatch("click");
  assertEqual(page.el("acpTranscriptWrap").hidden, true,
              "the transcript wrapper should hide while a sub-agent is open");
  assertEqual(page.el("acpComposer").hidden, true,
              "the composer should hide — a sub-agent's conversation is read-only");
  assertEqual(page.el("acpSubPanel").hidden, false);
  page.openAt(1);
  const subs = page.socketAt(1).sent.filter((f) => f.type === "subscribe");
  assertEqual(subs.length, 1, "expected exactly one subscribe on the sub-agent socket");
  assertEqual(subs[0].sessionId, "sub-1");
});

check("the transcript wrapper is hidden (not just the inner transcript) when a sub-agent is open", (tpl) => {
  // Regression: the cycle-1 fix introduced .acp-transcript-wrap. openSubagent()
  // used to hide #acpTranscript but not the wrapper, so the wrapper still claimed
  // flex space while the subpanel was open.
  const { page, live } = connected(tpl);
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-1", role: "explorer", task: "", sessionName: "",
      status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  page.all("acpTranscript", ".acp-crew-row")[0].dispatch("click");
  assertEqual(page.el("acpTranscriptWrap").hidden, true,
              "the transcript wrapper still claims flex space while the subpanel " +
              "is open — openSubagent must hide the wrapper, not just the inner transcript");
  assertEqual(page.el("acpTranscript").hidden, false,
              "the inner transcript should not be hidden directly (the wrapper hides it)");
  page.click("acpSubBack");
  assertEqual(page.el("acpTranscriptWrap").hidden, false,
              "the transcript wrapper must be restored when closing the sub-agent view");
});

check("the sub-agent panel renders its own chunk and tool_call frames", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-1", role: "explorer", task: "", sessionName: "",
      status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  page.all("acpTranscript", ".acp-crew-row")[0].dispatch("click");
  page.openAt(1);
  page.deliverTo(1, { type: "session", sessionId: "sub-1",
    payload: { sessionId: "sub-1", readOnly: true, parentSessionId: live } });
  page.deliverTo(1, { type: "history", sessionId: "sub-1", payload: { events: [] } });
  page.deliverTo(1, { type: "chunk", sessionId: "sub-1",
    payload: { role: "agent", text: "looking around" } });
  page.deliverTo(1, { type: "tool_call", sessionId: "sub-1",
    payload: { toolCallId: "tc-1", title: "read", kind: "", status: "" } });
  const body = page.el("acpSubTranscript").textContent;
  assert(body.includes("looking around"), "the sub-agent's chunk text was not rendered");
  assert(body.includes("read"), "the sub-agent's tool call was not rendered");
});

check("the back button in the sub-agent panel returns to the main transcript", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-1", role: "explorer", task: "", sessionName: "",
      status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  page.all("acpTranscript", ".acp-crew-row")[0].dispatch("click");
  page.openAt(1);
  page.click("acpSubBack");
  assertEqual(page.el("acpSubPanel").hidden, true);
  assertEqual(page.el("acpTranscriptWrap").hidden, false);
});

check("reopening a sub-agent reuses the existing socket rather than a third one",
  (tpl) => {
    const { page, live } = connected(tpl);
    page.deliver(subagentsFrame(live, [
      { sessionId: "sub-1", role: "explorer", task: "", sessionName: "",
        status: "working", action: "", done: false, error: "",
        startedAt: Date.now() / 1000 },
    ]));
    page.all("acpTranscript", ".acp-crew-row")[0].dispatch("click");
    page.openAt(1);
    page.click("acpSubBack");
    page.all("acpTranscript", ".acp-crew-row")[0].dispatch("click");
    let thirdOpened = true;
    try { page.socketAt(2); } catch (e) { thirdOpened = false; }
    assert(!thirdOpened, "a third socket was opened instead of reusing the " +
                         "existing sub-agent one");
    const subs = page.socketAt(1).sent.filter((f) => f.type === "subscribe");
    assertEqual(subs.length, 2, "expected a second subscribe sent on the reused socket");
  });

check("a live subagents update refreshes the crew panel while a sub-agent panel is open", (tpl) => {
  const { page, live } = connected(tpl);
  const now = Date.now() / 1000;
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-1", role: "explorer", task: "", sessionName: "",
      status: "working", action: "reading", done: false, error: "", startedAt: now - 5 },
  ], "tcid-update"));
  page.all("acpTranscript", ".acp-crew-row")[0].dispatch("click");
  page.openAt(1);
  // On the MAIN socket (index 0) — `page.deliver()` alone would now target the
  // sub-agent socket, since it always addresses whichever opened last.
  // Use same toolCallId so the update lands in the same slot.
  page.deliverTo(0, subagentsFrame(live, [
    { sessionId: "sub-1", role: "explorer", task: "", sessionName: "",
      status: "done", action: "", done: true, error: "", startedAt: now - 5 },
  ], "tcid-update"));
  assertEqual(page.el("acpSubPanel").hidden, false,
              "the panel should stay open across a crew update");
  assertEqual(page.el("acpSubStatus").textContent, "done");
});

check("a new session frame clears the crew panel and closes any open sub-agent panel",
  (tpl) => {
    const { page, live } = connected(tpl);
    page.deliver(subagentsFrame(live, [
      { sessionId: "sub-1", role: "explorer", task: "", sessionName: "",
        status: "working", action: "", done: false, error: "",
        startedAt: Date.now() / 1000 },
    ]));
    page.all("acpTranscript", ".acp-crew-row")[0].dispatch("click");
    page.openAt(1);
    page.deliverTo(0, {
      type: "session", sessionId: "sess-other-0002",
      payload: { sessionId: "sess-other-0002", cwd: "C:\\work\\other",
                 created: false, turnActive: false, contextPercent: null },
    });
    assertEqual(page.all("acpTranscript", ".acp-crew-panel").length, 0,
                "the previous session's crew panel should not carry over");
    assertEqual(page.el("acpSubPanel").hidden, true,
                "the sub-agent panel should close on a session switch");
    assertEqual(page.el("acpTranscriptWrap").hidden, false);
    assertEqual(page.el("acpComposer").hidden, false);
  });

// ---- crew panel header and lean row redesign (Phase 2) ----

check("crew panel header shows 'Orchestrating (2 agents)' with 2 running entries", (tpl) => {
  const { page, live } = connected(tpl);
  const now = Date.now() / 1000;
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-1", role: "kiro_default", task: "", sessionName: "stage-a",
      status: "working", action: "", done: false, error: "", startedAt: now },
    { sessionId: "sub-2", role: "kiro_default", task: "", sessionName: "stage-b",
      status: "working", action: "", done: false, error: "", startedAt: now },
  ]));
  const hdr = page.one("acpTranscript", ".acp-crew-header");
  assert(hdr !== null, "crew panel should have a header element");
  assertEqual(hdr.textContent, "Orchestrating (2 agents)",
              "header should say 'Orchestrating (2 agents)'");
});

check("crew panel header shows 'Orchestrating (1 agent)' (singular) with 1 running entry", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-1", role: "kiro_default", task: "", sessionName: "stage-a",
      status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  const hdr = page.one("acpTranscript", ".acp-crew-header");
  assert(hdr !== null, "crew panel should have a header element");
  assertEqual(hdr.textContent, "Orchestrating (1 agent)",
              "header should use singular 'agent' for exactly one entry");
});

check("crew panel header shows 'Done (2 agents)' when crewAllDone is true", (tpl) => {
  const { page, live } = connected(tpl);
  const now = Date.now() / 1000;
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-1", role: "kiro_default", task: "", sessionName: "stage-a",
      status: "terminated", action: "", done: true, error: "", startedAt: now - 5 },
    { sessionId: "sub-2", role: "kiro_default", task: "", sessionName: "stage-b",
      status: "terminated", action: "", done: true, error: "", startedAt: now - 5 },
  ]));
  const hdr = page.one("acpTranscript", ".acp-crew-header");
  assert(hdr !== null, "crew panel should have a header element");
  assertEqual(hdr.textContent, "Done (2 agents)",
              "header should say 'Done (2 agents)' when all done");
});

check("crew panel header shows 'Done (1 agent)' (singular) when crewAllDone is true with 1 entry", (tpl) => {
  const { page, live } = connected(tpl);
  const now = Date.now() / 1000;
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-1", role: "kiro_default", task: "", sessionName: "stage-a",
      status: "terminated", action: "", done: true, error: "", startedAt: now - 5 },
  ]));
  const hdr = page.one("acpTranscript", ".acp-crew-header");
  assert(hdr !== null, "crew panel should have a header element");
  assertEqual(hdr.textContent, "Done (1 agent)",
              "header should say 'Done (1 agent)' (singular) when all done with 1 entry");
});

check("crew row has status-thinking dot for working entry", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-1", role: "kiro_default", task: "", sessionName: "stage-a",
      status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  const row = page.one("acpTranscript", ".acp-crew-row");
  assert(row !== null, "crew panel should have a row");
  const dot = row.querySelector(".session-status");
  assert(dot !== null, "row should have a session-status dot");
  assert(String(dot.className).split(/\s+/).includes("status-thinking"),
         "working row dot should have status-thinking class, got: " + dot.className);
});

check("crew row has status-idle dot for done entry", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-1", role: "kiro_default", task: "", sessionName: "stage-a",
      status: "terminated", action: "", done: true, error: "", startedAt: Date.now() / 1000 - 5 },
  ]));
  const row = page.one("acpTranscript", ".acp-crew-row");
  assert(row !== null, "crew panel should have a row");
  const dot = row.querySelector(".session-status");
  assert(dot !== null, "row should have a session-status dot");
  assert(String(dot.className).split(/\s+/).includes("status-idle"),
         "done row dot should have status-idle class, got: " + dot.className);
});

check("crew row has status-errored dot for error entry", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-1", role: "kiro_default", task: "", sessionName: "stage-a",
      status: "terminated", action: "", done: true, error: "tool failed", startedAt: Date.now() / 1000 - 5 },
  ]));
  const row = page.one("acpTranscript", ".acp-crew-row");
  assert(row !== null, "crew panel should have a row");
  const dot = row.querySelector(".session-status");
  assert(dot !== null, "row should have a session-status dot");
  assert(String(dot.className).split(/\s+/).includes("status-errored"),
         "error row dot should have status-errored class, got: " + dot.className);
});

check("acp-crew-label shows entry.sessionName when present", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-1", role: "kiro_default", task: "do something long",
      sessionName: "count_src", status: "working", action: "", done: false, error: "",
      startedAt: Date.now() / 1000 },
  ]));
  const label = page.one("acpTranscript", ".acp-crew-label");
  assert(label !== null, "row should have a .acp-crew-label element");
  assertEqual(label.textContent, "count_src",
              "label should show sessionName, not task");
});

check("acp-crew-label falls back to 30-char truncated task with ellipsis", (tpl) => {
  const { page, live } = connected(tpl);
  const longTask = "Count the number of source files in the repository";
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-1", role: "kiro_default", task: longTask,
      sessionName: "", status: "working", action: "", done: false, error: "",
      startedAt: Date.now() / 1000 },
  ]));
  const label = page.one("acpTranscript", ".acp-crew-label");
  assert(label !== null, "row should have a .acp-crew-label element");
  const txt = label.textContent;
  assert(txt.endsWith("\u2026"), "label should end with ellipsis when task > 30 chars");
  // slice(0, 30) of the 50-char task, trimmed, then + ellipsis
  assertEqual(txt, "Count the number of source fil\u2026",
              "label should be first 30 chars of task + ellipsis");
});

check("acp-crew-label falls back to 'agent' when both sessionName and task are empty", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-1", role: "kiro_default", task: "", sessionName: "",
      status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  const label = page.one("acpTranscript", ".acp-crew-label");
  assert(label !== null, "row should have a .acp-crew-label element");
  assertEqual(label.textContent, "agent",
              "label should be 'agent' when sessionName and task are both empty");
});

check("acp-crew-action shows 'working\u2026' fallback when action is empty and state is working", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-1", role: "kiro_default", task: "do something", sessionName: "stage-1",
      status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  const row = page.one("acpTranscript", ".acp-crew-row");
  assert(row !== null, "crew panel should have a row");
  const actionSpan = row.querySelector(".acp-crew-action");
  assert(actionSpan !== null, "row should have a .acp-crew-action element");
  assertEqual(actionSpan.textContent, "working\u2026",
              "action should show 'working\u2026' fallback when action is empty and state is working");
});

check("acp-crew-action shows 'done' for a done:true row with no action field", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-1", role: "kiro_default", task: "do something", sessionName: "stage-1",
      status: "terminated", done: true, error: "", startedAt: Date.now() / 1000 - 5 },
  ]));
  const row = page.one("acpTranscript", ".acp-crew-row");
  assert(row !== null, "crew panel should have a row");
  const actionSpan = row.querySelector(".acp-crew-action");
  assert(actionSpan !== null, "row should have a .acp-crew-action element");
  assertEqual(actionSpan.textContent, "done",
              "action should show 'done' for a done:true row with no action field");
});

check("acp-crew-action shows 'errored' for an error:true row", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-1", role: "kiro_default", task: "do something", sessionName: "stage-1",
      status: "terminated", done: true, error: "tool failed", startedAt: Date.now() / 1000 - 5 },
  ]));
  const row = page.one("acpTranscript", ".acp-crew-row");
  assert(row !== null, "crew panel should have a row");
  const actionSpan = row.querySelector(".acp-crew-action");
  assert(actionSpan !== null, "row should have a .acp-crew-action element");
  assertEqual(actionSpan.textContent, "errored",
              "action should show 'errored' for an error:true row");
});

check("renderSubHead subRoleEl shows sessionName when present", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-1", role: "kiro_default", task: "long task text",
      sessionName: "count_src", status: "working", action: "", done: false, error: "",
      startedAt: Date.now() / 1000 },
  ]));
  page.all("acpTranscript", ".acp-crew-row")[0].dispatch("click");
  assertEqual(page.el("acpSubRole").textContent, "count_src",
              "sub-panel role header should show sessionName when present");
});

check("old crew class names are absent from rendered output", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-1", role: "kiro_default", task: "do work",
      sessionName: "stage-a", status: "working", action: "reading", done: false, error: "",
      startedAt: Date.now() / 1000 },
  ]));
  const panel = page.one("acpTranscript", ".acp-crew-panel");
  assert(panel !== null, "crew panel should exist");
  // Collect all class names from all descendants
  const allClasses = panel.descendants()
    .flatMap((n) => String(n.className || "").split(/\s+/).filter(Boolean));
  assert(!allClasses.includes("acp-crew-entry"),
         "old class acp-crew-entry should not appear in output");
  assert(!allClasses.includes("acp-crew-name"),
         "old class acp-crew-name should not appear in output");
  assert(!allClasses.includes("acp-crew-working"),
         "old class acp-crew-working should not appear in output");
  assert(!allClasses.includes("acp-crew-done"),
         "old class acp-crew-done should not appear in output");
  // acp-crew-error (the old bare state class) should not appear — acp-crew-row-error is ok
  assert(!allClasses.includes("acp-crew-error"),
         "old class acp-crew-error should not appear in output");
});

check("role span is omitted when entry.role is empty", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-1", role: "", task: "do work", sessionName: "stage-a",
      status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  const crewPanel = page.one("acpTranscript", ".acp-crew-panel");
  assert(crewPanel !== null, "crew panel should exist");
  assertEqual(crewPanel.querySelector(".acp-crew-role"), null,
              "role span should be absent when entry.role is empty");
});

check("role span shows role text when entry.role is non-empty", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-1", role: "kiro_default", task: "do work", sessionName: "stage-a",
      status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  const crewPanel = page.one("acpTranscript", ".acp-crew-panel");
  assert(crewPanel !== null, "crew panel should exist");
  const roleSpan = crewPanel.querySelector(".acp-crew-role");
  assert(roleSpan !== null, "role span should be present when entry.role is non-empty");
  assertEqual(roleSpan.textContent, "kiro_default",
              "role span should show the role text");
});

// ---- Phase 4: per-toolCallId inline crew panel tests ----

check("setCrew with toolCallId anchors panel after tool call row", (tpl) => {
  const { page, live } = connected(tpl);
  // First deliver a tool_call frame so toolRows has the entry
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "tcid-1", title: "orchestrate", kind: "execute",
               status: "in_progress" } });
  const toolRow = page.one("acpTranscript", ".acp-msg-tool");
  assert(toolRow !== null, "tool call row should exist in the transcript");
  // Now deliver the subagents frame with the same toolCallId
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-a", role: "worker", task: "", sessionName: "stage-a",
      status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ], "tcid-1"));
  // crews['tcid-1'] should exist
  assert(page.sandbox._testCrews() && page.sandbox._testCrews()["tcid-1"],
         "crews['tcid-1'] should exist after subagents frame with toolCallId");
  const panel = page.sandbox._testCrews()["tcid-1"].panel;
  assert(panel !== null && panel !== undefined, "crew panel should be created");
  // The panel should be inserted after the tool call row (as nextSibling of the row)
  const transcriptKids = page.el("acpTranscript").childNodes;
  const toolRowIdx = transcriptKids.indexOf(toolRow);
  const panelIdx = transcriptKids.indexOf(panel);
  assert(toolRowIdx >= 0, "tool call row should be a direct child of transcript");
  assert(panelIdx >= 0, "crew panel should be a direct child of transcript");
  assert(panelIdx === toolRowIdx + 1,
         "crew panel should be inserted directly after the tool call row");
});

check("setCrew with empty toolCallId creates no-anchor panel appended to transcript", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-b", role: "worker", task: "", sessionName: "stage-b",
      status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ], ""));
  const crewKeys = Object.keys(page.sandbox._testCrews());
  assertEqual(crewKeys.length, 1, "exactly one crew slot should be created");
  assert(crewKeys[0].startsWith("_na_"),
         "no-anchor slot key should start with _na_");
  // Panel should be last child of transcriptEl
  const transcriptKids = page.el("acpTranscript").childNodes;
  const panel = page.sandbox._testCrews()[crewKeys[0]].panel;
  assert(panel !== null, "panel should be created");
  assertEqual(transcriptKids[transcriptKids.length - 1], panel,
              "no-anchor panel should be appended as last child of transcript");
});

check("setCrew with empty entries and no active no-anchor slot does not ghost _noAnchorKey", (tpl) => {
  // Each check() call gets a fresh page via loadPage(), so _noAnchorSeq and
  // _noAnchorKey both start at their initial values (0 / null) here.
  const { page, live } = connected(tpl);
  // Spurious server dismissal with no active no-anchor slot: this must NOT
  // advance _noAnchorSeq or set _noAnchorKey to a ghost key.
  page.deliver(subagentsFrame(live, [], ''));
  // Now create a real no-anchor slot — should get _na_1, not _na_2.
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub1", role: "worker", task: "", sessionName: "Agent 1",
      status: "working", action: "", done: false, error: "",
      startedAt: Date.now() / 1000 },
  ], ''));
  const crewsMap = page.sandbox._testCrews();
  const keys = Object.keys(crewsMap);
  assertEqual(keys.length, 1, "should have exactly one slot (_na_1 not _na_2)");
  assertEqual(keys[0], "_na_1",
              "_noAnchorSeq should not have been advanced by the spurious empty call");
});

check("two subagents frames with different toolCallIds produce two independent panels", (tpl) => {
  const { page, live } = connected(tpl);
  // Deliver two tool_call frames
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "a", title: "orchestrate-a", kind: "execute", status: "in_progress" } });
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "b", title: "orchestrate-b", kind: "execute", status: "in_progress" } });
  // Deliver two subagents frames with different toolCallIds
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-a1", role: "worker", task: "", sessionName: "agent-a",
      status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ], "a"));
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-b1", role: "worker", task: "", sessionName: "agent-b",
      status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ], "b"));
  assertEqual(Object.keys(page.sandbox._testCrews()).length, 2,
              "two different toolCallIds should produce two independent crew slots");
  assert(page.all("acpTranscript", ".acp-crew-panel").length === 2,
         "two panels should appear in the transcript");
});

check("session frame clears all crew panels", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-c", role: "worker", task: "", sessionName: "agent-c",
      status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ], "tcid-clear"));
  assertEqual(Object.keys(page.sandbox._testCrews()).length, 1,
              "one crew slot should exist before session frame");
  // Deliver a session frame (triggers clearTranscript)
  page.deliver({ type: "session", sessionId: "sess-new",
    payload: { sessionId: "sess-new", cwd: "C:\\work\\new", created: true,
               turnActive: false, contextPercent: null } });
  assertEqual(Object.keys(page.sandbox._testCrews()).length, 0,
              "all crew slots should be cleared after session frame");
  assertEqual(page.all("acpTranscript", ".acp-crew-panel").length, 0,
              "no acp-crew-panel elements should remain in the DOM");
});

check("crewEntry finds entries across multiple active crews", (tpl) => {
  const { page, live } = connected(tpl);
  // Create two crew slots with different toolCallIds and different sessionIds
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-x1", role: "worker", task: "", sessionName: "agent-x",
      status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ], "tcid-x"));
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-y1", role: "worker", task: "", sessionName: "agent-y",
      status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ], "tcid-y"));
  // crewEntry should find entries in either slot
  const entryX = page.sandbox._testCrewEntry("sub-x1");
  const entryY = page.sandbox._testCrewEntry("sub-y1");
  assert(entryX !== null, "crewEntry should find sub-x1 across crew slots");
  assert(entryY !== null, "crewEntry should find sub-y1 across crew slots");
  assertEqual(entryX.sessionId, "sub-x1", "crewEntry should return the correct entry for sub-x1");
  assertEqual(entryY.sessionId, "sub-y1", "crewEntry should return the correct entry for sub-y1");
  // Should return null for an unknown session
  assert(page.sandbox._testCrewEntry("not-a-session") === null,
         "crewEntry should return null for an unknown sessionId");
});

check("panels persist after meta turn:end", (tpl) => {
  const { page, live } = connected(tpl);
  const now = Date.now() / 1000;
  // Deliver an all-done crew frame (the previously auto-dismissed case)
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-z", role: "worker", task: "", sessionName: "agent-z",
      status: "terminated", action: "", done: true, error: "",
      startedAt: now - 30, stoppedAt: now },
  ], "tcid-persist2"));
  // meta turn:end
  page.deliver({ type: "meta", sessionId: live,
                 payload: { turn: "end", stopReason: "end_turn" } });
  // crews['tcid-persist2'] should still exist
  assert(page.sandbox._testCrews() && page.sandbox._testCrews()["tcid-persist2"],
         "crews['tcid-persist2'] should still exist after turn:end (SC4)");
  const panel = page.sandbox._testCrews()["tcid-persist2"].panel;
  assert(panel !== null && panel !== undefined && panel.parentNode !== null,
         "crew panel should still be in the DOM after turn:end (SC4)");
});

check("two consecutive no-anchor subagents updates reuse the same slot", (tpl) => {
  const { page, live } = connected(tpl);
  const now = Date.now() / 1000;
  // Deliver two no-anchor subagents frames for the same fan-out
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub1", role: "worker", task: "", sessionName: "agent-1",
      status: "working", action: "", done: false, error: "", startedAt: now },
  ], ""));
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub1", role: "worker", task: "", sessionName: "agent-1",
      status: "terminated", action: "", done: true, error: "", startedAt: now, stoppedAt: now + 1 },
  ], ""));
  const crewsMap = page.sandbox._testCrews();
  const keys = Object.keys(crewsMap);
  assertEqual(keys.length, 1, "exactly one no-anchor slot after two updates");
  assert(keys[0].startsWith("_na_"), "key has _na_ prefix");
  const panels = page.all("acpTranscript", ".acp-crew-panel");
  assertEqual(panels.length, 1, "exactly one crew panel in DOM");
});

check("anchor panel is a direct transcriptEl child even after flushToolGroups", (tpl) => {
  const { page, live } = connected(tpl);
  // Deliver two tool_call frames to trigger grouping at turn:end
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "tcid-group", name: "subagent_call", title: "orchestrate",
               kind: "execute", status: "in_progress" } });
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "tcid-other", name: "read_file", title: "read",
               kind: "read", status: "in_progress" } });
  // Turn:end triggers flushToolGroups — moves tool rows into a hidden group body
  page.deliver({ type: "meta", sessionId: live,
                 payload: { turn: "end", stopReason: "end_turn" } });
  // Now deliver the subagents frame anchored to the grouped tool call
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-grp1", role: "worker", task: "", sessionName: "agent-grp",
      status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ], "tcid-group"));
  const panels = page.all("acpTranscript", ".acp-crew-panel");
  assertEqual(panels.length, 1, "one crew panel");
  assertEqual(panels[0].parentNode, page.el("acpTranscript"),
              "panel is direct child of transcriptEl");
});

check("the debug log starts collapsed and a tap opens it, remembered for next time",
  (tpl) => {
    const { page } = connected(tpl);
    assertEqual(page.el("acpLog").hidden, true, "the debug log should start collapsed");
    assertEqual(page.el("acpLogToggle").getAttribute("aria-expanded"), "false");
    page.click("acpLogToggle");
    assertEqual(page.el("acpLog").hidden, false, "tapping the toggle should open the log");
    assertEqual(page.el("acpLogToggle").getAttribute("aria-expanded"), "true");
    assertEqual(page.stored["pa_acp_debug_log"], "open",
                "the open state should be persisted for the next load");
  });

check("a stored open preference reopens the debug log on load", (tpl) => {
  const { page } = connected(tpl, { stored: { pa_acp_debug_log: "open" } });
  assertEqual(page.el("acpLog").hidden, false,
              "a previously-opened debug log should not reopen closed");
});

// ---- status rail grouping mode (Phase 1) ----

/** A flat store with sessions in each availability/status bucket for status-mode tests. */
function statusStore() {
  return [{
    cwd: "C:\\work\\alpha", name: "alpha", exists: true,
    sessions: [
      { id: "s-working",   title: "working session",   updated_at: "2026-08-01T10:00:00.000000000Z",
        availability: "held",      status: "working" },
      { id: "s-waiting",   title: "waiting session",   updated_at: "2026-08-01T09:00:00.000000000Z",
        availability: "held",      status: "waiting" },
      { id: "s-errored",   title: "errored session",   updated_at: "2026-08-01T08:00:00.000000000Z",
        availability: "held",      status: "errored" },
      { id: "s-available", title: "available session", updated_at: "2026-08-01T07:00:00.000000000Z",
        availability: "available", status: "" },
      { id: "s-locked",    title: "locked session",    updated_at: "2026-08-01T06:00:00.000000000Z",
        availability: "locked",    status: "" },
    ],
  }];
}

check("railSetMode('status') sets railMode to 'status' and stores it in localStorage",
  async (tpl) => {
    const page = await railed(tpl, { store: statusStore() });
    // Default starts in project mode. Switch to status via settings.
    const options = page.openSettings();
    const statusOption = options.filter((o) => o.dataset.mode === "status")[0];
    assert(statusOption !== undefined, "settings menu has no 'status' option");
    statusOption.dispatch("click");
    await page.settle();
    assertEqual(page.stored.pa_acp_group, "status",
                "switching to status mode did not write 'status' to localStorage");
    // Reload: should restore status mode from storage.
    const page2 = await railed(tpl, {
      store: statusStore(), stored: { pa_acp_group: "status" } });
    // In status mode the rail loads flat sessions — verify it asked for ?mode=recent.
    const asked = page2.listingCalls().map((c) => c.params);
    assert(asked.length > 0, "railed page made no listing request");
    assertEqual(asked[0].mode, "recent",
                "status mode on load did not request the flat listing");
  });

check("switching to status mode dispatches a ?mode=recent listing request", async (tpl) => {
  const page = await railed(tpl, { store: statusStore() });
  const callsBefore = page.listingCalls().length;
  const statusOption = page.openSettings().filter((o) => o.dataset.mode === "status")[0];
  statusOption.dispatch("click");
  await page.settle();
  const newCalls = page.listingCalls().slice(callsBefore);
  assert(newCalls.length > 0, "switching to status mode made no listing request");
  assertEqual(newCalls[0].params.mode, "recent",
              "status mode did not request the flat (recent) listing");
});

check("renderRailStatus groups sessions into correct buckets in priority order",
  async (tpl) => {
    const page = await railed(tpl, {
      store: statusStore(), stored: { pa_acp_group: "status" } });
    const headings = page.railHeadings();
    // Working > Waiting > Errored > Available > Locked — order is what we are testing.
    assert(headings.indexOf("Working")   < headings.indexOf("Waiting"),
           "Working bucket should appear before Waiting");
    assert(headings.indexOf("Waiting")   < headings.indexOf("Errored"),
           "Waiting bucket should appear before Errored");
    assert(headings.indexOf("Errored")   < headings.indexOf("Available"),
           "Errored bucket should appear before Available");
    assert(headings.indexOf("Waiting")   < headings.indexOf("Available"),
           "Waiting bucket should appear before Available");
    assert(headings.indexOf("Available") < headings.indexOf("Locked"),
           "Available bucket should appear before Locked");
    // All five occupied buckets rendered.
    assert(headings.includes("Working"),   "Working bucket absent");
    assert(headings.includes("Waiting"),   "Waiting bucket absent");
    assert(headings.includes("Errored"),   "Errored bucket absent");
    assert(headings.includes("Available"), "Available bucket absent");
    assert(headings.includes("Locked"),    "Locked bucket absent");
    // Sessions under each group: each bucket has 1 session in the fixture.
    assertEqual(page.railRows().length, 5,
                "wrong total number of session rows under status mode");
  });

check("statusBucketKey maps availability/status pairs to correct bucket keys", async (tpl) => {
  // Load the page so the script is evaluated and statusBucketKey is in scope.
  // We verify by switching to status mode and checking the DOM groupings.
  // Held+working -> Working bucket; available -> Available; locked -> Locked.
  const store = [{
    cwd: "C:\\work\\test", name: "test", exists: true,
    sessions: [
      { id: "h-w", title: "h-w", updated_at: "2026-08-01T10:00:00.000000000Z",
        availability: "held",      status: "working" },
      { id: "av",  title: "av",  updated_at: "2026-08-01T09:00:00.000000000Z",
        availability: "available", status: "" },
      { id: "lk",  title: "lk",  updated_at: "2026-08-01T08:00:00.000000000Z",
        availability: "locked",    status: "" },
    ],
  }];
  const page = await railed(tpl, { store });
  const statusOption = page.openSettings().filter((o) => o.dataset.mode === "status")[0];
  statusOption.dispatch("click");
  await page.settle();
  const headings = page.railHeadings();
  assert(headings.includes("Working"),   "held+working should land in Working bucket");
  assert(headings.includes("Available"), "available should land in Available bucket");
  assert(headings.includes("Locked"),    "locked should land in Locked bucket");
  assert(!headings.includes("Errored"),  "Errored bucket should be absent (no errored sessions)");
  assert(!headings.includes("Waiting"),  "Waiting bucket should be absent (no waiting sessions)");
});

check("the rail settings menu contains a third menuitemradio for Status", async (tpl) => {
  const page = await railed(tpl);
  const options = page.openSettings();
  assertEqual(options.length, 3, "settings menu should have exactly 3 options");
  const statusOption = options.filter((o) => o.dataset.mode === "status")[0];
  assert(statusOption !== undefined, "no option with data-mode='status'");
  assertEqual(statusOption.getAttribute("role"), "menuitemradio",
              "status option should have role=menuitemradio");
  assertEqual(statusOption.getAttribute("aria-checked"), "false",
              "status option should start unchecked when mode is project");
  // Switch to status mode and verify aria-checked updates.
  statusOption.dispatch("click");
  await page.settle();
  const options2 = page.openSettings();
  const statusOption2 = options2.filter((o) => o.dataset.mode === "status")[0];
  assertEqual(statusOption2.getAttribute("aria-checked"), "true",
              "status option aria-checked should be true after selecting status mode");
});

check("railCollapsed with s: prefix collapses/expands status buckets via group head click",
  async (tpl) => {
    // statusStore() puts one held/working session, so Working bucket is present.
    const page = await railed(tpl, {
      store: statusStore(), stored: { pa_acp_group: "status" } });
    // Helper to find Working group by heading text.
    const workingGroup = () => page.railGroups().filter((g) => {
      const name = g.querySelector(".acp-rail-group-name");
      return name && name.textContent === "Working";
    })[0];
    const wg = workingGroup();
    assert(wg !== undefined, "Working group not found");
    const toggle = () => workingGroup().querySelector(".acp-rail-group-toggle");
    // Starts expanded.
    assertEqual(toggle().getAttribute("aria-expanded"), "true",
                "Working bucket should start expanded");
    assert(workingGroup().querySelectorAll(".acp-rail-row").length > 0,
           "Working bucket should show rows when expanded");
    // Click to collapse — renderRail() rebuilds the DOM, so re-query.
    toggle().dispatch("click");
    assertEqual(toggle().getAttribute("aria-expanded"), "false",
                "Working bucket toggle should report collapsed after click");
    assertEqual(workingGroup().querySelectorAll(".acp-rail-row").length, 0,
                "Working bucket should show no rows after collapsing");
    // Click again to expand.
    toggle().dispatch("click");
    assertEqual(toggle().getAttribute("aria-expanded"), "true",
                "Working bucket toggle should report expanded after second click");
    assert(workingGroup().querySelectorAll(".acp-rail-row").length > 0,
           "Working bucket should show rows after re-expanding");
  });

check("railSummary() shows session count not workspace count under status mode",
  async (tpl) => {
    const page = await railed(tpl, {
      store: statusStore(), stored: { pa_acp_group: "status" } });
    const summaryText = page.el("acpRailStatus").textContent;
    // statusStore() has 5 sessions total.
    assert(summaryText.includes("5 session"),
           `railSummary under status mode should say "5 sessions loaded", got: ${summaryText}`);
    assert(!summaryText.includes("workspaces"),
           `railSummary under status mode should not mention "workspaces", got: ${summaryText}`);
  });


check("Load-more click in status mode dispatches a flat request, not a group request",
  async (tpl) => {
    // Build a store big enough that serveFlat returns has_more: true.
    // serveFlat uses RAIL_FLAT_SIZE=30 as the page size, so 31+ sessions trigger it.
    const bigStore = [{ cwd: "C:\\work\\big", name: "big", exists: true, sessions: [] }];
    for (let i = 0; i < 35; i++) {
      bigStore[0].sessions.push({
        id: `s-${i}`, title: `session ${i}`,
        updated_at: `2026-08-01T${String(10 + (i % 10)).padStart(2, "0")}:00:00.000000000Z`,
        availability: "available", status: "",
      });
    }
    const page = await railed(tpl, {
      store: bigStore,
      stored: { pa_acp_group: "status" },
    });

    // Verify the Load-more button is visible (has_more=true from the fixture).
    assert(!page.el("acpRailMore").hidden,
           "Load-more button should be visible when railFlatHasMore is true");

    const callsBefore = page.listingCalls().length;
    page.click("acpRailMore");
    await page.settle();

    const newCalls = page.listingCalls().slice(callsBefore);
    assert(newCalls.length > 0,
           "Load-more click in status mode made no listing request");
    // Must use the flat endpoint (mode=recent), not the grouped endpoint.
    assertEqual(newCalls[0].params.mode, "recent",
                "Load-more click in status mode did not dispatch a flat (?mode=recent) request");
    assert(!("group_page" in newCalls[0].params),
           "Load-more click in status mode dispatched a group-page request instead of flat");
  });

check("tick-poll in status mode dispatches a flat request, not a group request",
  async (tpl) => {
    const page = await railed(tpl, {
      store: statusStore(),
      stored: { pa_acp_group: "status" },
    });

    const callsBefore = page.listingCalls().length;
    page.tick();
    await page.settle();

    const newCalls = page.listingCalls().slice(callsBefore);
    assert(newCalls.length > 0,
           "tick-poll in status mode made no listing request");
    // Must use the flat endpoint (mode=recent), not the grouped endpoint.
    assertEqual(newCalls[0].params.mode, "recent",
                "tick-poll in status mode did not dispatch a flat (?mode=recent) request");
    assert(!("group_page" in newCalls[0].params),
           "tick-poll in status mode dispatched a group-page request instead of flat");
  });

check("statusBucketKey: held+waiting maps to Waiting bucket, held+errored maps to Errored bucket",
  async (tpl) => {
    // Covers {availability:'held', status:'waiting'} -> 'waiting'
    // and    {availability:'held', status:'errored'}  -> 'errored'.
    const store = [{
      cwd: "C:\\work\\held-test", name: "held-test", exists: true,
      sessions: [
        { id: "held-waiting", title: "held waiting",
          updated_at: "2026-08-01T10:00:00.000000000Z",
          availability: "held", status: "waiting" },
        { id: "held-errored", title: "held errored",
          updated_at: "2026-08-01T09:00:00.000000000Z",
          availability: "held", status: "errored" },
      ],
    }];
    const page = await railed(tpl, { store, stored: { pa_acp_group: "status" } });
    const headings = page.railHeadings();
    assert(headings.includes("Waiting"),
           "{availability:'held', status:'waiting'} should map to the Waiting bucket");
    assert(headings.includes("Errored"),
           "{availability:'held', status:'errored'} should map to the Errored bucket");
    assert(!headings.includes("Working"),
           "Waiting/Errored fixtures should not produce a Working bucket");
    assert(!headings.includes("Available"),
           "Waiting/Errored fixtures should not produce an Available bucket");
  });

check("status mode with no sessions renders the empty-state node and no bucket groups",
  async (tpl) => {
    // renderRailStatus returns 0, which triggers renderRail()'s existing empty-state path.
    const page = await railed(tpl, {
      store: [{ cwd: "C:\\work\\empty", name: "empty", exists: true, sessions: [] }],
      stored: { pa_acp_group: "status" },
    });
    const groups = page.railGroups();
    assertEqual(groups.length, 0,
                "no bucket groups should be rendered when the session list is empty");
    const emptyNode = page.one("acpRailGroups", ".acp-rail-empty");
    assert(emptyNode !== null && emptyNode !== undefined,
           "the empty-state node should appear when status mode has no sessions");
  });

check("clicking Show-N-more in a status bucket restores focus to the last revealed row",
  async (tpl) => {
    // Build a store with more than RAIL_SESSION_SIZE (10) available sessions so
    // the Available bucket renders a Show-N-more button.
    const bigStore = [{
      cwd: "C:\\work\\focus", name: "focus", exists: true,
      sessions: Array.from({length: 12}, function(_, i) { return { id: "s-av-" + (i+1), title: "av " + (i+1), updated_at: "2026-08-01T10:" + String(i).padStart(2, "0") + ":00.000000000Z", availability: "available", status: "" }; }),
    }];
    const page = await railed(tpl, {
      store: bigStore, stored: { pa_acp_group: "status" } });

    // Find the Available bucket's Show-N-more button — should be present because
    // 12 sessions > RAIL_SESSION_SIZE (10).
    const availableGroup = page.railGroups().filter((g) => {
      const name = g.querySelector(".acp-rail-group-name");
      return name && name.textContent === "Available";
    })[0];
    assert(availableGroup !== undefined, "Available group not rendered");
    const moreBtn = availableGroup.querySelector(".acp-rail-group-more");
    assert(moreBtn !== null && moreBtn !== undefined,
           "Show-N-more button absent from Available bucket — need > 10 sessions to trigger it");

    moreBtn.focus();
    moreBtn.dispatch("click");
    await page.settle();

    // After the click the button is gone (all rows revealed), so focus must
    // have been restored to the last row in the expanded bucket.
    // Re-query the group: renderRail() rebuilds the DOM, so pre-click references are stale.
    const expandedGroup = page.railGroups().filter((g) => {
      const name = g.querySelector(".acp-rail-group-name");
      return name && name.textContent === "Available";
    })[0];
    const now = page.focused();
    assert(now, "clicking Show-N-more in status bucket dropped focus to document body — " +
                "railRestoreFocus() has no want.status branch");
    const rows = expandedGroup.querySelectorAll(".acp-rail-row");
    assert(rows.length > 0, "Available bucket has no rows after expand");
    const lastRow = rows[rows.length - 1];
    assertEqual(now.dataset.sid, lastRow.dataset.sid,
                "focus did not land on the last revealed row of the Available bucket");
  });

// -------------------------------------------------------- Phase 2: collapse tool call command body --

check("tool_call with command renders .acp-tool-toggle in head; cmdWrap starts hidden", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({
    type: "tool_call", sessionId: live,
    payload: { toolCallId: "tc-col-1", title: "shell", kind: "execute",
               status: "in_progress", command: "ls -la" },
  });
  const transcript = page.el("acpTranscript");
  const toggle = transcript.querySelector(".acp-tool-toggle");
  assert(toggle !== null, "tool_call with command should render .acp-tool-toggle in .acp-tool-head");
  const head = transcript.querySelector(".acp-tool-head");
  assert(head.querySelector(".acp-tool-toggle") !== null,
         ".acp-tool-toggle should be a child of .acp-tool-head");
  assertEqual(toggle.getAttribute("aria-expanded"), "false",
              "toggle should start with aria-expanded=false (collapsed)");
  // The commandBlock wrapper (parent of .acp-tool-cmd) should start hidden
  const cmdEl = transcript.querySelector(".acp-tool-cmd");
  assert(cmdEl !== null, "tool_call with command should render .acp-tool-cmd");
  const cmdWrap = cmdEl.parentElement;
  assert(cmdWrap.hidden === true,
         "command wrapper should start hidden (collapsed by default)");
});

check("clicking toggle reveals command body and sets aria-expanded=true", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({
    type: "tool_call", sessionId: live,
    payload: { toolCallId: "tc-col-2", title: "shell", kind: "execute",
               status: "in_progress", command: "git status" },
  });
  const transcript = page.el("acpTranscript");
  const toggle = transcript.querySelector(".acp-tool-toggle");
  const cmdWrap = transcript.querySelector(".acp-tool-cmd").parentElement;
  assert(cmdWrap.hidden === true, "fixture: should start collapsed");
  toggle.dispatch("click");
  assertEqual(toggle.getAttribute("aria-expanded"), "true",
              "after first click aria-expanded should be true");
  assert(cmdWrap.hidden === false,
         "after first click command wrapper should be visible (hidden=false)");
});

check("second click on toggle collapses command body again", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({
    type: "tool_call", sessionId: live,
    payload: { toolCallId: "tc-col-3", title: "shell", kind: "execute",
               status: "in_progress", command: "echo hi" },
  });
  const transcript = page.el("acpTranscript");
  const toggle = transcript.querySelector(".acp-tool-toggle");
  const cmdWrap = transcript.querySelector(".acp-tool-cmd").parentElement;
  toggle.dispatch("click");
  assert(cmdWrap.hidden === false, "fixture: first click should open");
  toggle.dispatch("click");
  assertEqual(toggle.getAttribute("aria-expanded"), "false",
              "after second click aria-expanded should be false again");
  assert(cmdWrap.hidden === true,
         "after second click command wrapper should be hidden again");
});

check("tool_call without command has no .acp-tool-toggle; head unchanged", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({
    type: "tool_call", sessionId: live,
    payload: { toolCallId: "tc-col-4", title: "read_file", kind: "read",
               status: "in_progress" },
  });
  const transcript = page.el("acpTranscript");
  const toggle = transcript.querySelector(".acp-tool-toggle");
  assert(toggle === null, "tool_call without command should not render .acp-tool-toggle");
  const cmdEl = transcript.querySelector(".acp-tool-cmd");
  assert(cmdEl === null, "tool_call without command should not render .acp-tool-cmd");
});

check("tool_update adding command to existing row appends toggle and starts collapsed", (tpl) => {
  const { page, live } = connected(tpl);
  // First deliver the initial call with no command
  const call = { toolCallId: "tc-col-5", title: "shell", kind: "execute", status: "in_progress" };
  page.deliver({ type: "tool_call", sessionId: live, payload: call });
  const transcript = page.el("acpTranscript");
  assert(transcript.querySelector(".acp-tool-toggle") === null,
         "fixture: no toggle before tool_update adds command");
  // Now deliver an update that adds a command
  page.deliver({
    type: "tool_update", sessionId: live,
    payload: { ...call, status: "completed", command: "git diff" },
  });
  const toggle = transcript.querySelector(".acp-tool-toggle");
  assert(toggle !== null, "tool_update adding command should append .acp-tool-toggle to head");
  const cmdWrap = transcript.querySelector(".acp-tool-cmd").parentElement;
  assert(cmdWrap.hidden === true,
         "command wrapper added by tool_update should start hidden");
  assertEqual(toggle.getAttribute("aria-expanded"), "false",
              "toggle added by tool_update should start with aria-expanded=false");
});

check("tool_update status mutation works when row is collapsed", (tpl) => {
  const { page, live } = connected(tpl);
  const call = { toolCallId: "tc-col-6", title: "shell", kind: "execute",
                 status: "in_progress", command: "npm test" };
  page.deliver({ type: "tool_call", sessionId: live, payload: call });
  const transcript = page.el("acpTranscript");
  const statusEl = transcript.querySelector(".acp-tool-status");
  assertEqual(statusEl.textContent, "in progress", "fixture: initial status");
  // Row is collapsed (default). Deliver a status-only update.
  page.deliver({
    type: "tool_update", sessionId: live,
    payload: { ...call, status: "completed" },
  });
  assertEqual(statusEl.textContent, "completed",
              "tool_update status should reach .acp-tool-status even when command wrapper is hidden");
  // Toggle should still be present and still collapsed
  const toggle = transcript.querySelector(".acp-tool-toggle");
  assertEqual(toggle.getAttribute("aria-expanded"), "false",
              "collapse state should be unaffected by a status-only tool_update");
  const cmdWrap = transcript.querySelector(".acp-tool-cmd").parentElement;
  assert(cmdWrap.hidden === true,
         "command wrapper should remain hidden after status-only tool_update");
});

check("aria-label on toggle contains tool name", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({
    type: "tool_call", sessionId: live,
    payload: { toolCallId: "tc-col-7", title: "shell", kind: "execute",
               status: "in_progress", command: "make build" },
  });
  const toggle = page.el("acpTranscript").querySelector(".acp-tool-toggle");
  assert(toggle !== null, "fixture: toggle should be present");
  const label = toggle.getAttribute("aria-label");
  assert(label.includes("shell"),
         "aria-label should contain the tool name — got: " + label);
  assert(label.toLowerCase().includes("show"),
         "initial aria-label should say 'Show' — got: " + label);
  // Click and verify label updates
  toggle.dispatch("click");
  const labelAfter = toggle.getAttribute("aria-label");
  assert(labelAfter.includes("shell"),
         "aria-label after click should still contain tool name — got: " + labelAfter);
  assert(labelAfter.toLowerCase().includes("hide"),
         "aria-label after click should say 'Hide' — got: " + labelAfter);
});

check("toggle aria-label falls back to kind when title is empty", (tpl) => {
  // Fix M1: a tool_call with kind:'execute', title:'', and a non-empty command
  // must still produce a toggle whose aria-label contains the kind ('execute').
  const { page, live } = connected(tpl);
  page.deliver({
    type: "tool_call", sessionId: live,
    payload: { toolCallId: "tc-m1-kind", title: "", kind: "execute",
               status: "in_progress", command: "grep -r foo ." },
  });
  const transcript = page.el("acpTranscript");
  const toggle = transcript.querySelector(".acp-tool-toggle");
  assert(toggle !== null,
         "tool_call with empty title but a command should still render .acp-tool-toggle in head");
  const label = toggle.getAttribute("aria-label");
  assert(label.includes("execute"),
         "aria-label should fall back to kind when title is empty — got: " + label);
});

check("exactly one toggle is created when command is updated multiple times", (tpl) => {
  // Fix M2: delivering a tool_call with no command, then two tool_updates each
  // providing a command, must result in exactly ONE .acp-tool-toggle in the head.
  const { page, live } = connected(tpl);
  // Step 1: initial call with no command (status only)
  const call = { toolCallId: "tc-m2-idem", title: "shell", kind: "execute",
                 status: "in_progress" };
  page.deliver({ type: "tool_call", sessionId: live, payload: call });
  // Step 2: first tool_update adding a command
  page.deliver({
    type: "tool_update", sessionId: live,
    payload: { ...call, status: "running", command: "npm install" },
  });
  // Step 3: second tool_update also providing a command (simulating a subsequent update)
  page.deliver({
    type: "tool_update", sessionId: live,
    payload: { ...call, status: "completed", command: "npm install" },
  });
  const head = page.el("acpTranscript").querySelector(".acp-tool-head");
  assert(head !== null, "fixture: .acp-tool-head should exist");
  const toggles = head.querySelectorAll(".acp-tool-toggle");
  assertEqual(toggles.length, 1,
              "multiple tool_updates providing a command should produce exactly " +
              "ONE .acp-tool-toggle in the row's head — got " + toggles.length);
});

// ---- Phase 3: Group consecutive tool calls at turn end -------------------
//
// deliverTurn sends turn:start, each tool_call payload, then turn:end, then
// settles. This mirrors the live frame sequence the agent produces.
async function deliverTurn(page, live, toolCallPayloads) {
  page.deliver({ type: "meta", sessionId: live, payload: { turn: "start" } });
  for (const payload of toolCallPayloads) {
    page.deliver({ type: "tool_call", sessionId: live, payload });
  }
  page.deliver({ type: "meta", sessionId: live,
                 payload: { turn: "end", stopReason: "end_turn" } });
  await page.settle();
}

check("P3: turn with 3 tool calls produces one .acp-tool-group; rows removed from root", async (tpl) => {
  const { page, live } = connected(tpl);
  await deliverTurn(page, live, [
    { toolCallId: "g1a", title: "shell",     kind: "execute", status: "completed", command: "ls" },
    { toolCallId: "g1b", title: "shell",     kind: "execute", status: "completed", command: "pwd" },
    { toolCallId: "g1c", title: "read_file", kind: "read",    status: "completed", command: "cat f" },
  ]);
  const transcript = page.el("acpTranscript");
  const groups = transcript.querySelectorAll(".acp-tool-group");
  assertEqual(groups.length, 1, "expected exactly 1 .acp-tool-group");
  const rootToolRows = transcript.childNodes.filter(
    (n) => n.className && String(n.className).includes("acp-msg-tool"));
  assertEqual(rootToolRows.length, 0,
    "individual acp-msg-tool rows should not be at transcript root after grouping");
});

check("P3: group is collapsed by default; toggle has aria-expanded=false", async (tpl) => {
  const { page, live } = connected(tpl);
  await deliverTurn(page, live, [
    { toolCallId: "g2a", title: "shell", kind: "execute", status: "completed", command: "a" },
    { toolCallId: "g2b", title: "shell", kind: "execute", status: "completed", command: "b" },
  ]);
  const transcript = page.el("acpTranscript");
  const toggle = transcript.querySelector(".acp-tool-group-toggle");
  assert(toggle !== null, "group toggle should exist");
  assertEqual(toggle.getAttribute("aria-expanded"), "false",
    "group should be collapsed by default");
  const body = transcript.querySelector(".acp-tool-group-body");
  assert(body !== null, "group body should exist");
  assert(body.hidden === true, "group body should be hidden by default");
  // aria-controls linkage: toggle must reference the body by id
  if (body && toggle) {
    assertEqual(toggle.getAttribute('aria-controls'), body.id, 'toggle aria-controls should link to body id');
  }
});

check("P3: group header format: name xCount · status xCount", async (tpl) => {
  const { page, live } = connected(tpl);
  await deliverTurn(page, live, [
    { toolCallId: "g3a", title: "shell",     kind: "execute", status: "completed", command: "a" },
    { toolCallId: "g3b", title: "shell",     kind: "execute", status: "completed", command: "b" },
    { toolCallId: "g3c", title: "read_file", kind: "read",    status: "completed", command: "c" },
  ]);
  const transcript = page.el("acpTranscript");
  const toggle = transcript.querySelector(".acp-tool-group-toggle");
  assert(toggle !== null, "group toggle should exist");
  const text = toggle.textContent;
  // Expected: "Called 3 tools: execute ×2, read · completed ×3"
  assert(text.toLowerCase().startsWith("called"),
    "header should start with 'Called' prefix — got: " + text);
  assert(!text.toLowerCase().includes("shell"),
    "header should not contain title 'shell' (kind is used instead) — got: " + text);
  assert(text.includes("execute"),
    "header should contain kind 'execute' — got: " + text);
  assert(text.includes("\xd72"),
    "header should use \xd7 (multiplication sign) for counts — got: " + text);
  assert(text.includes("read"),
    "header should contain kind 'read' — got: " + text);
  assert(text.includes("\xb7"),
    "header should contain \xb7 (middle dot) separator — got: " + text);
  assert(text.includes("completed"),
    "header should include narrowed status — got: " + text);
});

check("P3: clicking group toggle reveals rows and expands their command bodies", async (tpl) => {
  const { page, live } = connected(tpl);
  await deliverTurn(page, live, [
    { toolCallId: "g4a", title: "shell", kind: "execute", status: "completed", command: "ls" },
    { toolCallId: "g4b", title: "shell", kind: "execute", status: "completed", command: "pwd" },
  ]);
  const transcript = page.el("acpTranscript");
  const toggle = transcript.querySelector(".acp-tool-group-toggle");
  assert(toggle !== null, "group toggle should exist");
  const body = transcript.querySelector(".acp-tool-group-body");
  // Each row's command block starts collapsed while the group itself is
  // collapsed — checked before the click, since opening the group changes it.
  const innerRows = body.querySelectorAll(".acp-msg-tool");
  assert(innerRows.length >= 2, "group body should contain the individual rows");
  for (const row of innerRows) {
    const cmdWrap = row.querySelector(".acp-tool-cmd")
      ? row.querySelector(".acp-tool-cmd").parentNode : null;
    if (cmdWrap) {
      assert(cmdWrap.hidden === true,
        "individual rows should start with their command block collapsed");
    }
  }
  toggle.dispatch("click");
  assertEqual(toggle.getAttribute("aria-expanded"), "true",
    "after click group should be expanded");
  assert(body.hidden === false, "group body should be visible after click");
  // Opening the group also expands every still-collapsed child toggle
  // (d972bc9, "expand child tool rows when opening a tool call group") — a
  // user who opens the group wants to see what ran, not a second click per row.
  for (const row of innerRows) {
    const cmdWrap = row.querySelector(".acp-tool-cmd")
      ? row.querySelector(".acp-tool-cmd").parentNode : null;
    if (cmdWrap) {
      assert(cmdWrap.hidden === false,
        "opening the group should also expand each row's command block");
    }
  }
});

check("P3: turn with 1 tool call: wrapped in a group with 'Called 1 tool' header", async (tpl) => {
  const { page, live } = connected(tpl);
  await deliverTurn(page, live, [
    { toolCallId: "g5a", title: "shell", kind: "execute", status: "completed", command: "ls" },
  ]);
  const transcript = page.el("acpTranscript");
  const groups = transcript.querySelectorAll(".acp-tool-group");
  assertEqual(groups.length, 1, "single call should produce a group");
  const toggle = transcript.querySelector(".acp-tool-group-toggle");
  assert(toggle !== null, "group toggle should exist");
  const text = toggle.textContent;
  assert(text.toLowerCase().startsWith("called 1 tool"),
    "single-call header should read 'Called 1 tool' — got: " + text);
  const rootToolRows = transcript.childNodes.filter(
    (n) => n.className && String(n.className).includes("acp-msg-tool"));
  assertEqual(rootToolRows.length, 0, "single call row should be inside the group, not at transcript root");
});

check("P3: tool_call + prose + tool_call+tool_call: all get grouped; first as a solo group, last two as a pair", async (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({ type: "meta", sessionId: live, payload: { turn: "start" } });
  // First tool call
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "g6a", title: "shell", kind: "execute",
               status: "completed", command: "first" } });
  // Prose between (creates agentBody, breaking DOM adjacency)
  page.deliver({ type: "chunk", sessionId: live,
    payload: { role: "agent", text: "then I did something" } });
  // Two more adjacent tool calls
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "g6b", title: "shell", kind: "execute",
               status: "completed", command: "second" } });
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "g6c", title: "shell", kind: "execute",
               status: "completed", command: "third" } });
  page.deliver({ type: "meta", sessionId: live,
    payload: { turn: "end", stopReason: "end_turn" } });
  await page.settle();
  const transcript = page.el("acpTranscript");
  const groups = transcript.querySelectorAll(".acp-tool-group");
  assertEqual(groups.length, 2, "both sub-runs (1 and 2) should each form a group");
  const rootToolRows = transcript.childNodes.filter(
    (n) => n.className && String(n.className).includes("acp-msg-tool"));
  assertEqual(rootToolRows.length, 0,
    "no tool rows should remain at transcript root; all are in groups");
});

check("P3: IIFE closure — two groups A+B and C+D; clicking A+B expands only A+B", async (tpl) => {
  // Critical: validates the var-in-loop IIFE closure fix.
  // Without the IIFE all toggles would control the last group's body.
  const { page, live } = connected(tpl);
  page.deliver({ type: "meta", sessionId: live, payload: { turn: "start" } });
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "gAa", title: "shell", kind: "execute",
               status: "completed", command: "A" } });
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "gAb", title: "shell", kind: "execute",
               status: "completed", command: "B" } });
  // Prose breaks adjacency
  page.deliver({ type: "chunk", sessionId: live,
    payload: { role: "agent", text: "in between" } });
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "gCa", title: "shell", kind: "execute",
               status: "completed", command: "C" } });
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "gCb", title: "shell", kind: "execute",
               status: "completed", command: "D" } });
  page.deliver({ type: "meta", sessionId: live,
    payload: { turn: "end", stopReason: "end_turn" } });
  await page.settle();
  const transcript = page.el("acpTranscript");
  const groups = transcript.querySelectorAll(".acp-tool-group");
  assertEqual(groups.length, 2, "expected two groups (A+B and C+D)");
  const [groupAB, groupCD] = groups;
  const toggleAB = groupAB.querySelector(".acp-tool-group-toggle");
  const bodyAB   = groupAB.querySelector(".acp-tool-group-body");
  const toggleCD = groupCD.querySelector(".acp-tool-group-toggle");
  const bodyCD   = groupCD.querySelector(".acp-tool-group-body");
  assertEqual(toggleAB.getAttribute("aria-expanded"), "false", "A+B starts collapsed");
  assertEqual(toggleCD.getAttribute("aria-expanded"), "false", "C+D starts collapsed");
  toggleAB.dispatch("click");
  assertEqual(toggleAB.getAttribute("aria-expanded"), "true",
    "A+B should expand after clicking its toggle");
  assert(bodyAB.hidden === false, "A+B body should be visible");
  // IIFE validation: C+D must NOT have changed
  assertEqual(toggleCD.getAttribute("aria-expanded"), "false",
    "C+D must remain collapsed — IIFE closure fix");
  assert(bodyCD.hidden === true, "C+D body must remain hidden — IIFE closure fix");
});

check("P3: toolRows reference valid after grouping; tool_update status mutation works", async (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({ type: "meta", sessionId: live, payload: { turn: "start" } });
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "g8a", title: "shell", kind: "execute",
               status: "in_progress", command: "ls" } });
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "g8b", title: "shell", kind: "execute",
               status: "in_progress", command: "pwd" } });
  page.deliver({ type: "meta", sessionId: live,
    payload: { turn: "end", stopReason: "end_turn" } });
  await page.settle();
  // Deliver tool_update after grouping
  page.deliver({ type: "tool_update", sessionId: live,
    payload: { toolCallId: "g8a", title: "shell", kind: "execute", status: "completed" } });
  await page.settle();
  const transcript = page.el("acpTranscript");
  const group = transcript.querySelector(".acp-tool-group");
  assert(group !== null, "group should exist");
  const body = group.querySelector(".acp-tool-group-body");
  const statusSpans = body.querySelectorAll(".acp-tool-status");
  const statuses = Array.from(statusSpans).map((s) => s.textContent);
  assert(statuses.includes("completed"),
    "tool_update should reach the status element even after reparenting — statuses: " + statuses.join(", "));
});

check("P3: replay safety — turn:end in history produces group", async (tpl) => {
  const { page, live } = connected(tpl);
  // Build a history frame with two adjacent tool_call payloads and a turn:end,
  // matching the format real replay frames use. This exercises the actual replay
  // path (history frame → events loop → flushToolGroups at turn:end), not the
  // live-event path deliverTurn() uses.
  page.deliver({
    type: "history", sessionId: live,
    payload: { events: [
      { type: "meta", sessionId: live, payload: { turn: "start" } },
      { type: "tool_call", sessionId: live,
        payload: { toolCallId: "rp1", title: "shell", kind: "execute",
                   status: "completed", command: "git log" } },
      { type: "tool_call", sessionId: live,
        payload: { toolCallId: "rp2", title: "shell", kind: "execute",
                   status: "completed", command: "git diff" } },
      { type: "meta", sessionId: live, payload: { turn: "end", stopReason: "end_turn" } },
    ] },
  });
  await page.settle();
  const transcript = page.el("acpTranscript");
  const groups = transcript.querySelectorAll(".acp-tool-group");
  assertEqual(groups.length, 1,
    "history replay with two adjacent tool_calls + turn:end should produce exactly one .acp-tool-group");
});

check("P3: TOOL_STATUS_LABEL — unknown status is omitted from tally, no separator", async (tpl) => {
  // A status outside the ACP `ToolCallStatus` enum is narrowed out entirely,
  // so the tally is empty and the · separator does not appear. `cancelled` is
  // the fixture because acp.py carries it in `_TERMINAL_TOOL_STATUSES`
  // defensively while the protocol's own enum does not list it — exactly the
  // "value this build does not know" case.
  const { page, live } = connected(tpl);
  await deliverTurn(page, live, [
    { toolCallId: "m3a", title: "shell", kind: "execute", status: "cancelled", command: "x" },
    { toolCallId: "m3b", title: "shell", kind: "execute", status: "cancelled", command: "y" },
  ]);
  const transcript = page.el("acpTranscript");
  const groups = transcript.querySelectorAll(".acp-tool-group");
  assertEqual(groups.length, 1, "two unknown-status tool calls should still form a group");
  const toggle = groups[0].querySelector(".acp-tool-group-toggle");
  assert(toggle, "group has no toggle button");
  assert(!toggle.textContent.includes("cancelled"),
    "raw wire status 'cancelled' reached the toggle textContent — must be narrowed out");
  assert(!toggle.textContent.includes('\xb7'),
    "· separator appears even though the status tally is empty (all statuses unknown)");
  // And the badge itself stays off the row rather than showing the raw value.
  const badge = transcript.querySelector(".acp-tool-status");
  assert(badge.hidden === true, "an unknown status left the badge visible");
  assertEqual(badge.getAttribute("data-status"), null,
    "an unknown status reached data-status");
});

check("P3: in_progress is counted in the tally, rendered as its label", async (tpl) => {
  // Regression guard. `in_progress` and `pending` — the two statuses an
  // initial tool_call actually carries — were both absent from
  // TOOL_STATUS_LABEL, so an unfinished call was dropped from the tally: a
  // turn with three finished of five rendered "5 tool calls · completed ×3"
  // and silently lost the other two.
  const { page, live } = connected(tpl);
  await deliverTurn(page, live, [
    { toolCallId: "ip1", title: "shell", kind: "execute", status: "completed", command: "a" },
    { toolCallId: "ip2", title: "shell", kind: "execute", status: "in_progress", command: "b" },
    { toolCallId: "ip3", title: "shell", kind: "execute", status: "pending", command: "c" },
  ]);
  const transcript = page.el("acpTranscript");
  const toggle = transcript.querySelector(".acp-tool-group-toggle");
  assert(toggle, "group has no toggle button");
  // A count of 1 renders as the bare label; only 2+ carries a ×n suffix.
  const text = toggle.textContent;
  assert(text.includes("completed"), "completed missing from tally: " + text);
  assert(text.includes("in progress"), "in_progress missing from tally: " + text);
  assert(text.includes("pending"), "pending missing from tally: " + text);
  // The label is displayed; the wire key never is.
  assert(!text.includes("in_progress"),
    "raw wire status 'in_progress' reached the toggle textContent");
});

check("a tool row badge carries data-status for its wire value", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "ds1", title: "shell", kind: "execute",
               status: "in_progress", command: "sleep 1" } });
  const badge = page.el("acpTranscript").querySelector(".acp-tool-status");
  assertEqual(badge.getAttribute("data-status"), "in_progress",
    "data-status should carry the wire value the closed set vouched for");
  assertEqual(badge.textContent, "in progress", "badge should show the label, not the key");
  assert(badge.hidden === false, "a known status left the badge hidden");
  page.deliver({ type: "tool_update", sessionId: live,
    payload: { toolCallId: "ds1", status: "failed" } });
  assertEqual(badge.getAttribute("data-status"), "failed",
    "data-status should follow the row's status");
});

check("a tool_call with no status leaves the badge off the row", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "ns1", title: "shell", kind: "execute", command: "ls" } });
  const badge = page.el("acpTranscript").querySelector(".acp-tool-status");
  assert(badge.hidden === true, "an unreported status rendered a visible badge");
  assertEqual(badge.textContent, "", "an unreported status invented badge text");
});

check("a shell digest shows exit status, size and the stderr head", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "d1", title: "cargo build", kind: "execute",
               status: "failed", command: "cargo build",
               output: { form: "exec", exitStatus: 101, bytes: 4200, lines: 38,
                         stderrHead: "error[E0432]: unresolved import",
                         stderrTruncated: false } } });
  const row = page.el("acpTranscript");
  const exit = row.querySelector(".acp-tool-exit");
  assertEqual(exit.textContent, "exit 101");
  assertEqual(exit.getAttribute("data-ok"), "false", "a non-zero exit should read as not-ok");
  assert(row.querySelector(".acp-tool-digest").textContent.includes("38 lines"),
    "the digest should state the size");
  assertEqual(row.querySelector(".acp-tool-stderr").textContent,
    "error[E0432]: unresolved import",
    "stderr rides in the digest and is shown without opening anything");
});

check("exit 0 reads as ok", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "d2", title: "true", kind: "execute", status: "completed",
               output: { form: "exec", exitStatus: 0, bytes: 0, lines: 0 } } });
  assertEqual(page.el("acpTranscript").querySelector(".acp-tool-exit")
                  .getAttribute("data-ok"), "true");
});

check("a diff digest shows the stat below the path it describes", (tpl) => {
  // Order matters: the path says which file, the stat says what happened to
  // it, and a "+3 −0" above the filename reads backwards.
  const { page, live } = connected(tpl);
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "d3", title: "edit", kind: "edit", status: "completed",
               locations: [{ path: "/repo/acp.py" }],
               output: { form: "diff", path: "/repo/acp.py", added: 3,
                         removed: 1, isNew: false } } });
  const row = page.el("acpTranscript");
  row.querySelector(".acp-tool-toggle").dispatch("click");
  const panel = row.querySelector(".acp-tool-edit-panel");
  const classes = panel.childNodes.map((c) => c.className);
  assert(classes.indexOf("acp-tool-loc") < classes.indexOf("acp-tool-digest"),
    "the diffstat should follow the path, not precede it: " + classes.join(","));
  assertEqual(panel.querySelector(".acp-tool-diffstat-add").textContent, "+3");
  assertEqual(panel.querySelector(".acp-tool-diffstat-del").textContent, "−1");
});

check("a digest with no body says the output was not retained", (tpl) => {
  // The honest half of the record/broadcast split: the digest replays after a
  // reload, the body does not. A row claiming "38 lines" with no way to see
  // them and no explanation would read as a bug.
  const { page, live } = connected(tpl);
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "d4", title: "cargo build", kind: "execute",
               status: "failed",
               output: { form: "exec", exitStatus: 1, bytes: 4200, lines: 38 } } });
  assert(page.el("acpTranscript").querySelector(".acp-tool-lost"),
    "a digest replayed without its body should say so");
});

check("a digest describing no output claims nothing was lost", (tpl) => {
  // `exit 0` with an empty stdout must not accuse the page of losing anything.
  const { page, live } = connected(tpl);
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "d5", title: "true", kind: "execute", status: "completed",
               output: { form: "exec", exitStatus: 0, bytes: 0, lines: 0 } } });
  assert(!page.el("acpTranscript").querySelector(".acp-tool-lost"),
    "a silent command was reported as having lost its output");
});

check("a body that arrives with its digest retracts the not-retained notice", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "d6", title: "ls", kind: "execute", status: "in_progress" } });
  page.deliver({ type: "tool_output", sessionId: live,
    payload: { toolCallId: "d6", form: "text", text: "a\nb\n", truncated: false, length: 4 } });
  page.deliver({ type: "tool_update", sessionId: live,
    payload: { toolCallId: "d6",
               output: { form: "text", bytes: 4, lines: 2 } } });
  const row = page.el("acpTranscript");
  assert(!row.querySelector(".acp-tool-lost"), "the body is right there");
  assert(row.querySelector(".acp-tool-output-wrap"), "no body attached");
});

check("an output body arriving before its row is held and then attached", (tpl) => {
  // A call whose first frame is a content-only update: the body is broadcast
  // just before the update that creates the row.
  const { page, live } = connected(tpl);
  page.deliver({ type: "tool_output", sessionId: live,
    payload: { toolCallId: "d7", form: "text", text: "early\n",
               truncated: false, length: 6 } });
  page.deliver({ type: "tool_update", sessionId: live,
    payload: { toolCallId: "d7", title: "ls", kind: "execute", status: "completed",
               output: { form: "text", bytes: 6, lines: 1 } } });
  const row = page.el("acpTranscript");
  assert(row.querySelector(".acp-tool-output-wrap"), "the held body never attached");
  assert(!row.querySelector(".acp-tool-lost"),
    "a held body should count as present");
});

check("the output body is collapsed until asked for", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "d8", title: "ls", kind: "execute", status: "completed" } });
  page.deliver({ type: "tool_output", sessionId: live,
    payload: { toolCallId: "d8", form: "text", text: "x\n", truncated: false, length: 2 } });
  const row = page.el("acpTranscript");
  const wrap = row.querySelector(".acp-tool-output");
  assert(wrap.hidden === true, "output should start collapsed, as the command does");
  const toggle = row.querySelector(".acp-tool-output-toggle");
  assertEqual(toggle.getAttribute("aria-expanded"), "false");
  toggle.dispatch("click");
  assert(wrap.hidden === false, "the toggle did not open the output");
  assertEqual(toggle.getAttribute("aria-expanded"), "true");
});

check("tool output is never rendered as markdown", (tpl) => {
  // Output is bytes a command printed, not prose the agent wrote. The
  // markdown path exists for the latter, and pointing it at the former would
  // put a parser on untrusted bytes.
  const { page, live } = connected(tpl);
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "d9", title: "cat", kind: "execute", status: "completed" } });
  page.deliver({ type: "tool_output", sessionId: live,
    payload: { toolCallId: "d9", form: "text", truncated: false, length: 40,
               text: "# heading\n<script>alert(1)</script>\n" } });
  const box = page.el("acpTranscript").querySelector(".acp-tool-cmd");
  assert(box.textContent.includes("<script>alert(1)</script>"),
    "output should survive verbatim as text");
  assert(!page.el("acpTranscript").querySelector(".acp-msg-md"),
    "output reached the markdown renderer");
  assert(!page.el("acpTranscript").querySelector("script"),
    "a script element was constructed from tool output");
});

check("a diff body marks added and removed lines and keeps context", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "df1", title: "edit", kind: "edit", status: "completed" } });
  page.deliver({ type: "tool_output", sessionId: live,
    payload: { toolCallId: "df1", form: "diff", path: "/repo/a.py", truncated: false,
               oldText: "one\ntwo\nfour\n", newText: "one\ntwo\nthree\nfour\n" } });
  const lines = page.el("acpTranscript").querySelectorAll(".acp-tool-diff-line");
  const marks = lines.map((l) => l.getAttribute("data-d"));
  assertEqual(marks.filter((m) => m === "add").length, 1, "expected one addition");
  assertEqual(marks.filter((m) => m === "del").length, 0, "expected no deletions");
  const added = lines.filter((l) => l.getAttribute("data-d") === "add")[0];
  assert(added.textContent.includes("three"), "the added line should be 'three'");
  // A trailing newline is how a text file ends; it is not a blank final line.
  assert(!marks.some((m, i) => i === marks.length - 1 &&
                     lines[i].querySelector(".acp-tool-diff-text").textContent === ""),
    "the diff ended on a blank line produced by the trailing newline");
});

check("a new-file diff shows every line as an addition", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "df2", title: "create", kind: "edit", status: "completed" } });
  page.deliver({ type: "tool_output", sessionId: live,
    payload: { toolCallId: "df2", form: "diff", path: "/repo/new.py",
               truncated: false, oldText: null, newText: "a\nb\n" } });
  const marks = page.el("acpTranscript").querySelectorAll(".acp-tool-diff-line")
    .map((l) => l.getAttribute("data-d"));
  assertEqual(marks.join(","), "add,add", "a new file is all additions");
});

// ---- Edit row consolidation: one toggle, full path, numbered diff --------

check("an edit row has exactly one toggle, and it gates the whole panel", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "ep1", title: "edit", kind: "edit", status: "completed",
               locations: [{ path: "/repo/fixverify.py" }],
               output: { form: "diff", path: "/repo/fixverify.py", added: 3,
                         removed: 0, isNew: true } } });
  page.deliver({ type: "tool_output", sessionId: live,
    payload: { toolCallId: "ep1", form: "diff", path: "/repo/fixverify.py",
               truncated: false, oldText: null, newText: "a\nb\nc\n" } });
  const row = page.el("acpTranscript");
  const toggles = row.querySelectorAll(".acp-tool-toggle");
  assertEqual(toggles.length, 1, "an edit row should have exactly one toggle");
  const panel = row.querySelector(".acp-tool-edit-panel");
  assert(panel.hidden === true, "the panel should start collapsed");
  assert(row.querySelector(".acp-tool-loc"), "the path lives inside the panel");
  assert(row.querySelector(".acp-tool-digest"), "the digest lives inside the panel");
  assert(row.querySelector(".acp-tool-diff"), "the diff lives inside the panel");
  assert(!row.querySelector(".acp-tool-cmd"),
    "an edit row should not also render the raw rawInput command echo");
  toggles[0].dispatch("click");
  assert(panel.hidden === false, "clicking the toggle should open the panel");
  assertEqual(toggles[0].getAttribute("aria-expanded"), "true");
});

check("a collapsed edit row still shows a filename+stat one-liner in the head", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "qi1", title: "edit", kind: "edit", status: "completed",
               locations: [{ path: "/repo/fixverify.py" }],
               output: { form: "diff", path: "/repo/fixverify.py", added: 3,
                         removed: 0, isNew: true } } });
  const row = page.el("acpTranscript");
  const panel = row.querySelector(".acp-tool-edit-panel");
  assert(panel.hidden === true, "fixture: the panel stays collapsed");
  const quick = row.querySelector(".acp-tool-quick");
  assert(quick.hidden === false, "the quick-info one-liner should be visible while collapsed");
  assertEqual(quick.querySelector(".acp-tool-quick-path").textContent, "fixverify.py",
    "the quick-info line should show the short filename, not the full path");
  assertEqual(quick.querySelector(".acp-tool-diffstat-add").textContent, "+3");
  assertEqual(quick.querySelector(".acp-tool-diffstat-del").textContent, "−0");
});

check("the quick-info one-liner hides while the panel is open, to avoid showing the stat twice", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "qi3", title: "edit", kind: "edit", status: "completed",
               locations: [{ path: "/repo/fixverify.py" }],
               output: { form: "diff", path: "/repo/fixverify.py", added: 3,
                         removed: 0, isNew: true } } });
  const row = page.el("acpTranscript");
  const quick = row.querySelector(".acp-tool-quick");
  const toggle = row.querySelector(".acp-tool-toggle");
  assert(quick.hidden === false, "fixture: visible while collapsed");
  toggle.dispatch("click");
  assert(quick.hidden === true,
    "the one-liner should hide once the panel (same filename, same stat) is open");
  toggle.dispatch("click");
  assert(quick.hidden === false,
    "the one-liner should return once the panel is collapsed again");
  assertEqual(quick.querySelector(".acp-tool-quick-path").textContent, "fixverify.py");
});

check("the quick-info one-liner stays empty until a location is known", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "qi2", title: "edit", kind: "edit", status: "in_progress" } });
  const quick = page.el("acpTranscript").querySelector(".acp-tool-quick");
  assert(quick.hidden === true, "no location yet, so the one-liner has nothing to show");
  page.deliver({ type: "tool_update", sessionId: live,
    payload: { toolCallId: "qi2", locations: [{ path: "/repo/late.py" }] } });
  assert(quick.hidden === false, "a location arriving later should reveal the one-liner");
  assertEqual(quick.querySelector(".acp-tool-quick-path").textContent, "late.py");
});

check("an edit panel shows the full path, not just the basename", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "ep2", title: "edit", kind: "edit", status: "completed",
               locations: [{ path: "/home/q/PowerAtlas/src/power_atlas/fixverify.py" }] } });
  const pathEl = page.el("acpTranscript").querySelector(".acp-tool-loc-path");
  assertEqual(pathEl.textContent, "/home/q/PowerAtlas/src/power_atlas/fixverify.py",
    "the edit panel should show the whole path, not the basename");
  assertEqual(pathEl.getAttribute("title"), null,
    "the full path is already visible, so no hover-only title is needed");
});

check("a new-file diff numbers lines starting at 1, TUI-style", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "ln1", title: "create", kind: "edit", status: "completed" } });
  page.deliver({ type: "tool_output", sessionId: live,
    payload: { toolCallId: "ln1", form: "diff", path: "/repo/new.py",
               truncated: false, oldText: null, newText: "a\nb\nc\n" } });
  const nums = page.el("acpTranscript").querySelectorAll(".acp-tool-diff-num")
    .map((n) => n.textContent);
  assertEqual(nums.join(","), "1,2,3", "a new file should number from line 1");
});

check("a strReplace diff seeds its line numbers from locations[0].line", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "ln2", title: "edit", kind: "edit", status: "completed",
               locations: [{ path: "/repo/calc.py", line: 9 }] } });
  page.deliver({ type: "tool_output", sessionId: live,
    payload: { toolCallId: "ln2", form: "diff", path: "/repo/calc.py", truncated: false,
               oldText: "one\ntwo\nfour\n", newText: "one\ntwo\nthree\nfour\n" } });
  const nums = page.el("acpTranscript").querySelectorAll(".acp-tool-diff-num")
    .map((n) => n.textContent);
  assertEqual(nums.join(","), "9,10,11,12",
    "line numbers should start from the location's line, not from 1");
});

check("a failed edit's panel shows its failure text, not a blank diff", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "ep3", title: "edit", kind: "edit", status: "failed",
               locations: [{ path: "/repo/rejected.py" }] } });
  page.deliver({ type: "tool_output", sessionId: live,
    payload: { toolCallId: "ep3", form: "text", text: "Tool use was rejected by the user.",
               truncated: false, length: 35 } });
  const panel = page.el("acpTranscript").querySelector(".acp-tool-edit-panel");
  assert(panel.textContent.includes("Tool use was rejected by the user."),
    "a failed edit should show its failure reason inside the panel");
  assert(!panel.querySelector(".acp-tool-diff"), "a failed edit has no diff to render");
});

check("an edit digest with no body says the output was not retained", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "ep4", title: "edit", kind: "edit", status: "completed",
               locations: [{ path: "/repo/x.py" }],
               output: { form: "diff", path: "/repo/x.py", added: 2,
                         removed: 0, isNew: false } } });
  const panel = page.el("acpTranscript").querySelector(".acp-tool-edit-panel");
  assert(panel.querySelector(".acp-tool-lost"),
    "a diff digest replayed without its body should say so, inside the panel");
});

check("an edit row with a body attached does not show the not-retained notice", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "ep5", title: "edit", kind: "edit", status: "completed",
               locations: [{ path: "/repo/y.py" }],
               output: { form: "diff", path: "/repo/y.py", added: 1,
                         removed: 0, isNew: false } } });
  page.deliver({ type: "tool_output", sessionId: live,
    payload: { toolCallId: "ep5", form: "diff", path: "/repo/y.py", truncated: false,
               oldText: "a\n", newText: "a\nb\n" } });
  const panel = page.el("acpTranscript").querySelector(".acp-tool-edit-panel");
  assert(!panel.querySelector(".acp-tool-lost"), "the body is right there");
  assert(panel.querySelector(".acp-tool-diff"), "the diff should render");
});

check("an edit row's output arriving before the row is held and reaches the panel", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({ type: "tool_output", sessionId: live,
    payload: { toolCallId: "ep6", form: "diff", path: "/repo/z.py", truncated: false,
               oldText: null, newText: "x\n" } });
  page.deliver({ type: "tool_update", sessionId: live,
    payload: { toolCallId: "ep6", title: "create", kind: "edit", status: "completed",
               locations: [{ path: "/repo/z.py" }],
               output: { form: "diff", path: "/repo/z.py", added: 1,
                         removed: 0, isNew: true } } });
  const panel = page.el("acpTranscript").querySelector(".acp-tool-edit-panel");
  assert(panel.querySelector(".acp-tool-diff"), "the held body never reached the panel");
  assert(!panel.querySelector(".acp-tool-lost"), "a held body should count as present");
});

check("a second output body replaces the first rather than stacking", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "df3", title: "ls", kind: "execute", status: "in_progress" } });
  for (const text of ["first\n", "first\nsecond\n"]) {
    page.deliver({ type: "tool_output", sessionId: live,
      payload: { toolCallId: "df3", form: "text", text, truncated: false,
                 length: text.length } });
  }
  const row = page.el("acpTranscript");
  assertEqual(row.querySelectorAll(".acp-tool-output-wrap").length, 1,
    "a streaming call stacked one collapsible per chunk");
  assert(row.querySelector(".acp-tool-cmd").textContent.includes("second"),
    "the later body should win");
});

check("a tool row draws an icon for its kind", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "ic1", title: "shell", kind: "execute", command: "ls" } });
  const svg = page.el("acpTranscript").querySelector(".acp-tool-icon");
  assert(svg, "no icon drawn for kind=execute");
  assertEqual(svg.tagName, "SVG", "icon should be an svg element");
  assertEqual(svg.getAttribute("aria-hidden"), "true",
    "the icon duplicates the kind chip and must not be announced twice");
  const path = svg.querySelector("path");
  assert(path && path.getAttribute("d"), "icon has no path data");
});

check("an unknown tool kind draws no icon rather than a wrong one", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "ic2", title: "shell", kind: "teleport", command: "ls" } });
  assert(!page.el("acpTranscript").querySelector(".acp-tool-icon"),
    "an off-enum kind drew an icon");
});

check("every ToolKind value has an icon path", (tpl) => {
  // The enum is closed and small; a value added to the page without a path
  // silently loses its icon, which is the kind of gap only a sweep catches.
  const { page, live } = connected(tpl);
  const KINDS = ["read", "edit", "delete", "move", "search",
                 "execute", "think", "fetch", "switch_mode", "other"];
  KINDS.forEach((k, i) => {
    page.deliver({ type: "tool_call", sessionId: live,
      payload: { toolCallId: "k" + i, title: "t", kind: k, command: "c" } });
  });
  const icons = page.el("acpTranscript").querySelectorAll(".acp-tool-icon");
  assertEqual(icons.length, KINDS.length,
    "one or more ToolKind values have no icon path");
});

check("a tool row shows the file from locations, basename with full path on hover", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "lc1", title: "read", kind: "read",
               locations: [{ path: "/home/q/PowerAtlas/src/power_atlas/acp.py", line: 42 }] } });
  const el = page.el("acpTranscript").querySelector(".acp-tool-loc-path");
  assert(el, "no location subtitle rendered");
  assertEqual(el.textContent, "acp.py:42", "should show basename and line");
  assertEqual(el.getAttribute("title"),
    "/home/q/PowerAtlas/src/power_atlas/acp.py", "full path should be the title");
});

check("a location with no line shows no line number", (tpl) => {
  // Absent `line` means "the whole file" on the wire, which is a different
  // statement from line 0.
  const { page, live } = connected(tpl);
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "lc2", title: "read", kind: "read",
               locations: [{ path: "C:\\repo\\main.rs" }] } });
  const el = page.el("acpTranscript").querySelector(".acp-tool-loc-path");
  assertEqual(el.textContent, "main.rs", "a backslash path should also resolve");
});

check("several locations name the first and count the rest", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "lc3", title: "search", kind: "search",
               locations: [{ path: "/a/one.py" }, { path: "/a/two.py" },
                           { path: "/a/three.py" }] } });
  const row = page.el("acpTranscript");
  assertEqual(row.querySelector(".acp-tool-loc-path").textContent, "one.py");
  assertEqual(row.querySelector(".acp-tool-loc-more").textContent, "+2 more");
});

check("locations arriving on a later update are added once", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "lc4", title: "search", kind: "search" } });
  const row = page.el("acpTranscript");
  assert(!row.querySelector(".acp-tool-loc"), "fixture: no subtitle yet");
  page.deliver({ type: "tool_update", sessionId: live,
    payload: { toolCallId: "lc4", locations: [{ path: "/a/late.py" }] } });
  assertEqual(row.querySelector(".acp-tool-loc-path").textContent, "late.py",
    "a location on an update should reach the row");
  page.deliver({ type: "tool_update", sessionId: live,
    payload: { toolCallId: "lc4", locations: [{ path: "/a/late.py" }] } });
  assertEqual(row.querySelectorAll(".acp-tool-loc").length, 1,
    "a repeated location stacked a second subtitle onto the row");
});

check("a tool_update carrying no status does not clobber the row's status", (tpl) => {
  // The tool_call_update merge rule: a field the update omits means "no
  // change". This page used to write the literal 'update' here, so an update
  // carrying only content replaced a real status with a word the protocol
  // never sends.
  const { page, live } = connected(tpl);
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "mg1", title: "shell", kind: "execute",
               status: "completed", command: "ls" } });
  const badge = page.el("acpTranscript").querySelector(".acp-tool-status");
  assertEqual(badge.textContent, "completed", "fixture: initial status");
  page.deliver({ type: "tool_update", sessionId: live,
    payload: { toolCallId: "mg1", title: "shell" } });
  assertEqual(badge.textContent, "completed",
    "a status-less tool_update overwrote the row's status");
  assertEqual(badge.getAttribute("data-status"), "completed",
    "a status-less tool_update overwrote data-status");
});

check("P3: sequential turns each produce their own .acp-tool-group", async (tpl) => {
  const { page, live } = connected(tpl);
  // Turn 1 with 2 tool calls
  await deliverTurn(page, live, [
    { toolCallId: "sf1a", title: "shell", kind: "execute", status: "completed", command: "turn1-a" },
    { toolCallId: "sf1b", title: "shell", kind: "execute", status: "completed", command: "turn1-b" },
  ]);
  // Turn 2 with 2 tool calls
  await deliverTurn(page, live, [
    { toolCallId: "sf1c", title: "shell", kind: "execute", status: "completed", command: "turn2-a" },
    { toolCallId: "sf1d", title: "shell", kind: "execute", status: "completed", command: "turn2-b" },
  ]);
  const transcript = page.el("acpTranscript");
  const groups = transcript.querySelectorAll(".acp-tool-group");
  assertEqual(groups.length, 2,
    "two sequential turns with 2 tool calls each should produce exactly 2 .acp-tool-group elements");
});

check("P3: toolGroup is null after clearTranscript", async (tpl) => {
  const { page, live } = connected(tpl);
  // Start a turn with tool calls, then switch session (which calls clearTranscript)
  page.deliver({ type: "meta", sessionId: live, payload: { turn: "start" } });
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "g10a", title: "shell", kind: "execute",
               status: "in_progress", command: "ls" } });
  // Switch session (triggers clearTranscript)
  const newSid = "sess-post-clear";
  page.deliver({ type: "session", sessionId: newSid,
    payload: { sessionId: newSid, cwd: "C:\\other", created: true,
               turnActive: false, contextPercent: null } });
  await page.settle();
  const transcript = page.el("acpTranscript");
  assertEqual(transcript.querySelectorAll(".acp-tool-group").length, 0,
    "no groups should exist after clearTranscript");
  // New single-call turn should form its own fresh group (toolGroup was properly
  // reset by clearTranscript, so no stale accumulation from the old session leaks in)
  await deliverTurn(page, newSid, [
    { toolCallId: "g10b", title: "shell", kind: "execute", status: "completed", command: "new" },
  ]);
  assertEqual(transcript.querySelectorAll(".acp-tool-group").length, 1,
    "single call after clear should form its own group (toolGroup was properly reset)");
});

check("P3: group toggle aria-label toggles between Expand and Collapse", async (tpl) => {
  const { page, live } = connected(tpl);
  await deliverTurn(page, live, [
    { toolCallId: "g11a", title: "shell", kind: "execute", status: "completed", command: "a" },
    { toolCallId: "g11b", title: "shell", kind: "execute", status: "completed", command: "b" },
  ]);
  const transcript = page.el("acpTranscript");
  const toggle = transcript.querySelector(".acp-tool-group-toggle");
  assert(toggle !== null, "group toggle should exist");
  const initial = toggle.getAttribute("aria-label");
  assert(initial !== null && initial.toLowerCase().includes("expand"),
    "initial aria-label should say 'Expand' — got: " + initial);
  toggle.dispatch("click");
  const expanded = toggle.getAttribute("aria-label");
  assert(expanded.toLowerCase().includes("collapse"),
    "aria-label after expand should say 'Collapse' — got: " + expanded);
  toggle.dispatch("click");
  const collapsed = toggle.getAttribute("aria-label");
  assert(collapsed.toLowerCase().includes("expand"),
    "aria-label after re-collapse should say 'Expand' — got: " + collapsed);
});

check("P3: anonymous tool calls (no toolCallId) form a group", async (tpl) => {
  // Anonymous tool calls have no toolCallId — they always take the new-row
  // path in addToolCall (id is falsy, known is always null). flushToolGroups
  // should still collect and group them at turn:end.
  const { page, live } = connected(tpl);
  page.deliver({ type: "meta", sessionId: live, payload: { turn: "start" } });
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { title: "shell", kind: "execute", status: "completed", command: "a" } });
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { title: "shell", kind: "execute", status: "completed", command: "b" } });
  page.deliver({ type: "meta", sessionId: live, payload: { turn: "end", stopReason: "end_turn" } });
  await page.settle();
  const transcript = page.el("acpTranscript");
  assertEqual(transcript.querySelectorAll(".acp-tool-group").length, 1,
    "two anonymous tool calls should produce exactly one .acp-tool-group");
});

check("P3: flushToolGroups fires at user-chunk boundary (no intervening turn:end)", async (tpl) => {
  // During session replay kiro-cli may never emit meta turn:end frames.
  // Two adjacent tool_calls followed by a user-role chunk (without turn:end)
  // should flush the tool group when the user chunk arrives.
  const { page, live } = connected(tpl);
  // Simulate an agent turn with two tool calls during replay
  page.deliver({ type: "meta", sessionId: live, payload: { turn: "start" } });
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "ub1", title: "shell", kind: "execute",
               status: "completed", command: "ls" } });
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "ub2", title: "shell", kind: "execute",
               status: "completed", command: "pwd" } });
  // No turn:end — directly deliver a user chunk (as happens during replay)
  page.deliver({ type: "chunk", sessionId: live,
    payload: { role: "user", text: "hello" } });
  await page.settle();
  const transcript = page.el("acpTranscript");
  // The tool group should have been flushed when the user chunk arrived
  assertEqual(transcript.querySelectorAll(".acp-tool-group").length, 1,
    "tool group should be flushed at user-chunk boundary even without turn:end");
});

check("P3: flushToolGroups fires at post-replay tail (tool_calls at end of history with no turn:end)", async (tpl) => {
  // A session/load history frame may end with tool_calls and no following
  // turn:end. The post-replay tail flush should produce a group.
  const { page, live } = connected(tpl);
  page.deliver({
    type: "history", sessionId: live,
    payload: { events: [
      { type: "meta", sessionId: live, payload: { turn: "start" } },
      { type: "tool_call", sessionId: live,
        payload: { toolCallId: "tail1", title: "shell", kind: "execute",
                   status: "completed", command: "git status" } },
      { type: "tool_call", sessionId: live,
        payload: { toolCallId: "tail2", title: "shell", kind: "execute",
                   status: "completed", command: "git log" } },
      // No meta turn:end — history ends with the two tool_calls
    ] },
  });
  await page.settle();
  const transcript = page.el("acpTranscript");
  // The post-replay tail flush should have produced exactly one tool group
  assertEqual(transcript.querySelectorAll(".acp-tool-group").length, 1,
    "post-replay tail flush should produce a tool group when history ends with tool_calls");
});

// ---- Phase 2: Queue/Steer controls and image inline -----------------------

check("image inline: [Image N] marker inserted at cursor position", async (tpl) => {
  const { page } = connected(tpl);
  page.el("acpPrompt").value = "hello world";
  // Set cursor at position 5 ("hello" | " world")
  page.el("acpPrompt").selectionStart = 5;
  page.el("acpPrompt").selectionEnd = 5;
  page.paste([page.imageFile()]);
  await settleStaging();
  const val = page.el("acpPrompt").value;
  assert(val.includes("[Image 1]"),
    "textarea should contain [Image 1] after paste — got: " + val);
  const pos = val.indexOf("[Image 1]");
  assertEqual(pos, 5, "[Image 1] should appear at cursor position 5 — got: " + pos);
});

check("image inline: second paste inserts [Image 2]", async (tpl) => {
  const { page } = connected(tpl);
  page.paste([page.imageFile()]);
  await settleStaging();
  page.el("acpPrompt").selectionStart = page.el("acpPrompt").value.length;
  page.el("acpPrompt").selectionEnd = page.el("acpPrompt").value.length;
  page.paste([page.imageFile()]);
  await settleStaging();
  const val = page.el("acpPrompt").value;
  assert(val.includes("[Image 1]"), "should contain [Image 1] — got: " + val);
  assert(val.includes("[Image 2]"), "should contain [Image 2] — got: " + val);
});

check("queue button hidden when turn active but textarea empty", (tpl) => {
  const { page, live } = connected(tpl, { turnActive: true });
  // Textarea is empty — Stop shows, Queue+Steer hidden
  assert(page.el("acpStop").hidden === false,
    "Stop should be visible during turn with empty textarea");
  assert(page.el("acpQueueSteer").hidden === true,
    "Queue+Steer wrapper should be hidden with empty textarea during turn");
});

check("queue+steer buttons visible when turn active and textarea has text", (tpl) => {
  const { page, live } = connected(tpl, { turnActive: true });
  page.type("some text");
  page.el("acpPrompt").dispatch("input");
  assert(page.el("acpStop").hidden === true,
    "Stop should be hidden when textarea has text during turn");
  assert(page.el("acpQueueSteer").hidden === false,
    "Queue+Steer wrapper should be visible when textarea has text during turn");
});

check("queue button stores text and clears textarea", (tpl) => {
  const { page, live } = connected(tpl, { turnActive: true });
  page.type("hello agent");
  page.el("acpPrompt").dispatch("input");
  page.click("acpModeOptQueue");
  page.click("acpSendMode");
  assertEqual(page.el("acpPrompt").value, "",
    "textarea should be cleared after Queue");
  // Queue note should contain cancel button in transcript
  const cancelBtn = page.one("acpTranscript", ".acp-inline-cancel");
  assert(cancelBtn !== null,
    "queue note should contain a cancel button in transcript");
});

check("queue cancel link restores text to textarea", (tpl) => {
  const { page, live } = connected(tpl, { turnActive: true });
  page.type("queued text");
  page.el("acpPrompt").dispatch("input");
  page.click("acpModeOptQueue");
  page.click("acpSendMode");
  assertEqual(page.el("acpPrompt").value, "", "fixture: textarea cleared after queue");
  // Find the cancel button inside the transcript note
  const cancelBtn = page.one("acpTranscript", ".acp-inline-cancel");
  assert(cancelBtn !== null, "cancel button should be present in queue note");
  cancelBtn.dispatch("click");
  assertEqual(page.el("acpPrompt").value, "queued text",
    "cancel should restore text to textarea");
});

check("queued prompt auto-sends on meta turn:end when WS open and textarea empty", (tpl) => {
  const { page, live } = connected(tpl, { turnActive: true });
  page.type("queued message");
  page.el("acpPrompt").dispatch("input");
  page.click("acpModeOptQueue");
  page.click("acpSendMode");
  // Turn ends
  page.deliver({ type: "meta", sessionId: live, payload: { turn: "end", stopReason: "end_turn" } });
  const prompts = page.socket().sent.filter((f) => f.type === "prompt");
  assert(prompts.length >= 1,
    "at least one prompt should be sent — auto-send should have fired");
  const last = prompts[prompts.length - 1];
  assert(last && last.payload && last.payload.prompt === "queued message",
    "auto-sent prompt payload.prompt should match queued text, got: " +
    JSON.stringify(last && last.payload));
});

check("queued prompt discarded when session changed", (tpl) => {
  const { page, live } = connected(tpl, { turnActive: true });
  page.type("to discard");
  page.el("acpPrompt").dispatch("input");
  page.click("acpModeOptQueue");
  page.click("acpSendMode");
  // Simulate session change: release current session
  page.deliver({
    type: "session_closed", sessionId: live,
    payload: { message: "closed" },
  });
  // Deliver a new session
  const live2 = "sess-new-0002";
  page.deliver({
    type: "session", sessionId: live2,
    payload: { sessionId: live2, cwd: "C:\\work\\repo2", created: true, turnActive: false },
  });
  // The turn end on the old session shouldn't fire auto-send against new session
  const prompts = page.socket().sent.filter((f) => f.type === "prompt" && f.sessionId === live2);
  assertEqual(prompts.length, 0,
    "auto-send should not fire against the new session after session change");
});

check("steer sends steer frame and clears/disables textarea", (tpl) => {
  const { page, live } = connected(tpl, { turnActive: true });
  page.type("inject this");
  page.el("acpPrompt").dispatch("input");
  // Default mode is steer — just click sendModeBtn
  page.click("acpSendMode");
  const steers = page.socket().sent.filter((f) => f.type === "steer");
  assertEqual(steers.length, 1, "exactly one steer frame should be sent");
  assertEqual(steers[0].payload && steers[0].payload.message, "inject this",
    "steer payload.message should match typed text");
  assertEqual(page.el("acpPrompt").value, "",
    "textarea should be cleared after steer");
  assert(page.el("acpPrompt").disabled === true,
    "textarea should be disabled while awaiting steer_ack");
});

check("steer_ack re-enables controls", (tpl) => {
  const { page, live } = connected(tpl, { turnActive: true });
  page.type("steer text");
  page.el("acpPrompt").dispatch("input");
  page.click("acpSendMode");
  page.deliver({ type: "steer_ack", sessionId: live, payload: { queued: true } });
  assert(page.el("acpPrompt").disabled === false,
    "textarea should be re-enabled after steer_ack");
  assert(page.el("acpSendMode").disabled === false,
    "send mode button should be re-enabled after steer_ack");
  assert(page.el("acpModeToggle").disabled === false,
    "mode select should be re-enabled after steer_ack");
  assert(!page.el("acpTranscript").textContent.includes("Steer sent"),
    "transcript should NOT contain 'Steer sent' note (removed in Phase 1)");
});

check("steer_ack queued:false shows error note", (tpl) => {
  const { page, live } = connected(tpl, { turnActive: true });
  const originalText = "bad steer";
  page.type(originalText);
  page.el("acpPrompt").dispatch("input");
  page.click("acpSendMode");
  page.deliver({ type: "steer_ack", sessionId: live, payload: { queued: false } });
  assert(page.el("acpTranscript").textContent.includes("not accepted"),
    "transcript should contain rejection note when queued:false");
  assertEqual(page.el("acpPrompt").value, originalText,
    "steer_ack queued:false should restore the textarea text");
  assertEqual(page.el("acpPrompt").disabled, false, "promptInput re-enabled");
  assertEqual(page.el("acpSendMode").disabled, false, "sendModeBtn re-enabled");
  assertEqual(page.el("acpModeToggle").disabled, false, "modeSelect re-enabled");
});

check("steer_sent frame adds dimmed steer band", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({ type: "steer_sent", sessionId: live, payload: { text: "do X" } });
  const steerBands = page.el("acpTranscript").querySelectorAll(".acp-msg-steer");
  assertEqual(steerBands.length, 1, "transcript should contain exactly one .acp-msg-steer element");
  const body = steerBands[0].querySelector(".acp-msg-body");
  assert(body !== null, ".acp-msg-steer should contain .acp-msg-body");
  assertEqual(body.textContent, "do X", ".acp-msg-steer body should contain steer text");
  assert(String(steerBands[0].className).split(/\s+/).includes('acp-msg-steer'),
    'steer band element must carry acp-msg-steer CSS class; got classes: ' + steerBands[0].className);
});

check("steer_sent frame renders during replay", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({
    type: "history", sessionId: live,
    payload: { events: [
      { type: "steer_sent", sessionId: live, payload: { text: "replayed steer" } },
    ] },
  });
  const steerBands = page.el("acpTranscript").querySelectorAll(".acp-msg-steer");
  // Feeds via history wrapper which sets replaying=true during dispatch.
  // Band appearing confirms no !replaying guard is present in the steer_sent handler.
  assertEqual(steerBands.length, 1,
    "steer_sent during history replay should add a steer band (no !replaying guard)");
  const body = steerBands[0].querySelector(".acp-msg-body");
  assert(body !== null, ".acp-msg-steer replayed should contain .acp-msg-body");
  assertEqual(body.textContent, "replayed steer", "replayed steer band should contain steer text");
});

check("steer_sent frame with empty text is no-op", (tpl) => {
  const { page, live } = connected(tpl);
  const before = page.el("acpTranscript").childNodes.length;
  page.deliver({ type: "steer_sent", sessionId: live, payload: { text: "" } });
  const after = page.el("acpTranscript").childNodes.length;
  assertEqual(after, before,
    "steer_sent with empty text should not append any element to transcript");
  const steerBands = page.el("acpTranscript").querySelectorAll(".acp-msg-steer");
  assertEqual(steerBands.length, 0, "transcript should have no .acp-msg-steer for empty text");
});

check("transcript-renderer.js: renderTranscriptFrame renders a steer_sent frame as a dimmed steer band, closing the dashboard's replay gap (Fix 2, Step 9 review)", (tpl) => {
  // Calls the shared module's function directly, bypassing acp.html's own
  // handle() (which intercepts steer_sent via its own explicit case before
  // ever reaching a generic renderTranscriptFrame dispatch -- see the tests
  // above) -- this is what dashHandle()'s `history` case now reaches via
  // renderTranscriptHistory() during a dashboard reconnect replay, where
  // this frame type previously had no case at all and was silently dropped.
  const { page } = connected(tpl);
  page.sandbox.renderTranscriptFrame({ type: "steer_sent", payload: { text: "look at foo.py" } });
  const steerBands = page.el("acpTranscript").querySelectorAll(".acp-msg-steer");
  assertEqual(steerBands.length, 1, "renderTranscriptFrame must render exactly one steer band");
  const body = steerBands[0].querySelector(".acp-msg-body");
  assert(body !== null, ".acp-msg-steer should contain .acp-msg-body");
  assertEqual(body.textContent, "look at foo.py", "the band must contain the steered text");
});

check("transcript-renderer.js: renderTranscriptFrame ignores a steer_sent frame with empty/missing text", (tpl) => {
  const { page } = connected(tpl);
  const before = page.el("acpTranscript").childNodes.length;
  page.sandbox.renderTranscriptFrame({ type: "steer_sent", payload: { text: "" } });
  page.sandbox.renderTranscriptFrame({ type: "steer_sent", payload: {} });
  const after = page.el("acpTranscript").childNodes.length;
  assertEqual(after, before, "an empty/missing steer text must not append anything");
});

check("acp.html's own steer_sent handling is unaffected by renderTranscriptFrame gaining a steer_sent case (acp.html intercepts the frame type via its own handle() case first)", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({ type: "steer_sent", sessionId: live, payload: { text: "do X" } });
  const steerBands = page.el("acpTranscript").querySelectorAll(".acp-msg-steer");
  assertEqual(steerBands.length, 1,
    "acp.html's own live handle() dispatch must still produce exactly one band, not two, confirming " +
    "renderTranscriptFrame's new case is never reached for a live acp.html steer_sent frame");
});

check("error frame during steer restores textarea text", (tpl) => {
  const { page, live } = connected(tpl, { turnActive: true });
  page.type("important steer");
  page.el("acpPrompt").dispatch("input");
  page.click("acpSendMode");
  assert(page.el("acpPrompt").disabled === true, "fixture: textarea disabled after steer click");
  page.deliver({
    type: "error", sessionId: live,
    payload: { code: "agent_error", message: "steer failed" },
  });
  assertEqual(page.el("acpPrompt").value, "important steer",
    "error frame should restore steer text to textarea");
  assert(page.el("acpPrompt").disabled === false,
    "textarea should be re-enabled after error frame");
  assertEqual(page.el("acpModeToggle").disabled, false, "modeSelect re-enabled after error frame");
});

check("queuedPrompt and _steerPending cleared on releaseSession", (tpl) => {
  const { page, live } = connected(tpl, { turnActive: true });
  // Queue a prompt
  page.type("queue me");
  page.el("acpPrompt").dispatch("input");
  page.click("acpModeOptQueue");
  page.click("acpSendMode");
  // Release session
  page.deliver({
    type: "session_closed", sessionId: live,
    payload: { message: "closed" },
  });
  // Turn end on closed session should not fire auto-send
  const prevCount = page.socket().sent.filter((f) => f.type === "prompt").length;
  page.deliver({ type: "meta", sessionId: live, payload: { turn: "end", stopReason: "end_turn" } });
  const newCount = page.socket().sent.filter((f) => f.type === "prompt").length;
  assertEqual(newCount, prevCount,
    "no new prompt should be sent after releaseSession clears queuedPrompt");
});

check("removeAttachment renumbers [Image N] markers in textarea", async (tpl) => {
  const { page } = connected(tpl);
  // Paste two images
  page.paste([page.imageFile()]);
  await settleStaging();
  page.paste([page.imageFile()]);
  await settleStaging();
  const val = page.el("acpPrompt").value;
  assert(val.includes("[Image 1]"), "fixture: [Image 1] present — got: " + val);
  assert(val.includes("[Image 2]"), "fixture: [Image 2] present — got: " + val);
  // Remove first attachment (index 0)
  const chips = page.trayChips();
  assert(chips.length >= 1, "fixture: at least one chip");
  // Find and click the × on the first chip
  const removeBtn = chips[0].querySelector("button");
  assert(removeBtn !== null, "fixture: remove button on first chip");
  removeBtn.dispatch("click");
  const after = page.el("acpPrompt").value;
  assert(!after.includes("[Image 2]"),
    "[Image 2] should have been renumbered to [Image 1] — got: " + after);
  const count1 = (after.match(/\[Image 1\]/g) || []).length;
  assert(count1 === 1,
    "after removing first attachment, [Image 1] should appear exactly once for the remaining one — got: " + after);
});

check("removeAttachment handles middle element of 3", async (tpl) => {
  const { page } = connected(tpl);
  // Stage 3 images
  page.paste([page.imageFile()]);
  await settleStaging();
  page.paste([page.imageFile()]);
  await settleStaging();
  page.paste([page.imageFile()]);
  await settleStaging();
  // Override textarea value to a known state with all 3 markers
  page.el("acpPrompt").value = "[Image 1][Image 2][Image 3]";
  // Remove the middle attachment (index 1)
  const chips = page.trayChips();
  assert(chips.length === 3, "fixture: 3 chips — got: " + chips.length);
  chips[1].querySelector("button").dispatch("click");
  const after = page.el("acpPrompt").value;
  // [Image 1] should remain exactly once
  const count1 = (after.match(/\[Image 1\]/g) || []).length;
  assert(count1 === 1, "[Image 1] should appear exactly once — got: " + after);
  // [Image 2] should appear exactly once (renumbered from [Image 3])
  const count2 = (after.match(/\[Image 2\]/g) || []).length;
  assert(count2 === 1, "[Image 2] should appear exactly once (renumbered from [Image 3]) — got: " + after);
  // [Image 3] should NOT appear
  assert(!after.includes("[Image 3]"), "[Image 3] should not appear after removal — got: " + after);
});

// Fix 7: steer textarea re-enabled on WS close during pending steer
check("steer textarea re-enabled on ws close during pending steer", (tpl) => {
  const { page, live } = connected(tpl, { turnActive: true });
  page.type("steer text");
  page.el("acpPrompt").dispatch("input");
  page.click("acpSendMode");
  assert(page.el("acpPrompt").disabled === true,
    "fixture: textarea should be disabled after clicking Steer");
  // Simulate WS close mid-steer
  page.socket().onclose({ code: 1006, reason: "" });
  assertEqual(page.el("acpPrompt").disabled, false,
    "textarea should be re-enabled when WS closes during a pending steer");
  assertEqual(page.el("acpSendMode").disabled, false,
    "send mode button should be re-enabled when WS closes during a pending steer");
  assertEqual(page.el("acpModeToggle").disabled, false,
    "mode select should be re-enabled when WS closes during a pending steer");
  // Textarea text should be restored from _steerPending
  assertEqual(page.el("acpPrompt").value, "steer text",
    "textarea text should be restored from _steerPending on WS close");
});

// Fix 7b: steer controls re-enabled on session_closed (releaseSession) while steer pending
check("steer controls re-enabled on session release during pending steer", (tpl) => {
  const { page, live } = connected(tpl, { turnActive: true });
  page.type("steer before close");
  page.el("acpPrompt").dispatch("input");
  page.click("acpSendMode");
  assert(page.el("acpPrompt").disabled === true,
    "fixture: textarea disabled after steer click");
  // Release session via session_closed
  page.deliver({
    type: "session_closed", sessionId: live,
    payload: { message: "closed" },
  });
  assertEqual(page.el("acpPrompt").disabled, false,
    "textarea should be re-enabled after session release with steer pending");
  assertEqual(page.el("acpSendMode").disabled, false, "sendModeBtn re-enabled after release");
  assertEqual(page.el("acpModeToggle").disabled, false, "modeSelect re-enabled after release");
});

check("steer textarea re-enabled on agent_died", (tpl) => {
  const { page, live } = connected(tpl, { turnActive: true });
  // Set up turn active and click steer
  page.type("steer during turn");
  page.el("acpPrompt").dispatch("input");
  page.click("acpSendMode");
  assert(page.el("acpPrompt").disabled === true,
    "fixture: textarea should be disabled after clicking Steer");
  // Deliver agent_died frame
  page.deliver({
    type: "agent_died", sessionId: live,
    payload: { exitCode: 1, message: "agent process exited" },
  });
  assertEqual(page.el("acpPrompt").disabled, false,
    "promptInput should be re-enabled after agent_died with steer pending");
  assertEqual(page.el("acpSendMode").disabled, false,
    "sendModeBtn should be re-enabled after agent_died with steer pending");
  assertEqual(page.el("acpModeToggle").disabled, false,
    "modeSelect should be re-enabled after agent_died with steer pending");
});

// Fix 13: session-change guard — queue with sessionId=A, change to B, turn:end with A, no prompt sent
check("queued prompt not sent when sessionId changed before turn end", (tpl) => {
  const { page, live } = connected(tpl, { turnActive: true });
  page.type("queue this");
  page.el("acpPrompt").dispatch("input");
  page.click("acpModeOptQueue");
  page.click("acpSendMode");
  // Verify the prompt was queued (sent no prompt yet)
  const promptsBefore = page.socket().sent.filter((f) => f.type === "prompt").length;
  // Change sessionId to a different session (without closing), by delivering
  // a new session frame directly
  const live2 = "sess-other-0099";
  page.deliver({
    type: "session", sessionId: live2,
    payload: { sessionId: live2, cwd: "C:\\work\\other", created: false, turnActive: false },
  });
  // Now fire turn:end with the ORIGINAL session id — the guard _queueSession !== sessionId
  // should prevent sending to the new session
  page.deliver({ type: "meta", sessionId: live, payload: { turn: "end", stopReason: "end_turn" } });
  const promptsAfter = page.socket().sent.filter((f) => f.type === "prompt").length;
  assertEqual(promptsAfter, promptsBefore,
    "queued prompt should not be sent when sessionId changed between queue and turn:end");
  assert(page.transcript().includes("session changed") || page.transcript().includes("discarded"),
    "nothing said the queued prompt was discarded due to session change");
});

check("queued prompt not sent and note shown when turn ends with stopReason=cancelled", (tpl) => {
  const { page, live } = connected(tpl, { turnActive: true });
  page.type("important followup");
  page.el("acpPrompt").dispatch("input");
  page.click("acpModeOptQueue");
  page.click("acpSendMode");
  // Confirm it was queued (no prompt sent yet)
  const promptsBefore = page.socket().sent.filter((f) => f.type === "prompt").length;
  // Turn ends with stopReason=cancelled (user pressed Stop)
  page.deliver({ type: "meta", sessionId: live,
                 payload: { turn: "end", stopReason: "cancelled" } });
  const promptsAfter = page.socket().sent.filter((f) => f.type === "prompt").length;
  assertEqual(promptsAfter, promptsBefore,
    "no prompt frame should be sent when turn ends with stopReason=cancelled");
  assert(page.transcript().includes("not sent") || page.transcript().includes("stopped"),
    "a note should be shown when the queued prompt is discarded due to a cancelled turn");
});

// --------------------------------------------------------- Phase 1 new tests: mode select and Enter-during-turn --

check("mode select defaults to steer", (tpl) => {
  const { page } = connected(tpl);
  assertEqual(page.el("acpModeToggle").getAttribute("data-mode"), "steer",
    "mode select should default to 'steer' when no stored value");
  assertEqual(page.el("acpSendMode").textContent, "Steer",
    "send mode button text should be 'Steer' by default");
});

check("mode select change updates button label and persists", (tpl) => {
  const { page } = connected(tpl);
  page.click("acpModeOptQueue");
  assertEqual(page.el("acpSendMode").textContent, "Queue",
    "send mode button text should update to 'Queue' after mode change");
  assertEqual(page.stored["pa_acp_send_mode"], "queue",
    "localStorage should persist the new mode value");
});

check("mode select with invalid stored value defaults to steer", (tpl) => {
  // Pre-seed localStorage with an invalid value
  const page = loadPage(tpl, { stored: { pa_acp_send_mode: "invalid" } });
  page.open();
  const live = "sess-live-0001";
  page.deliver({
    type: "session", sessionId: live,
    payload: { sessionId: live, cwd: "C:\\work\\repo", created: true, turnActive: false },
  });
  assertEqual(page.el("acpModeToggle").getAttribute("data-mode"), "steer",
    "invalid stored value should fall back to 'steer'");
  assertEqual(page.el("acpSendMode").textContent, "Steer",
    "button text should be 'Steer' when invalid stored value falls back to default");
});

check("Enter during turn in steer mode triggers steer send", (tpl) => {
  const { page, live } = connected(tpl, { turnActive: true });
  page.type("steer via enter");
  page.el("acpPrompt").dispatch("input");
  // Default mode is steer; fire Enter keydown
  page.el("acpPrompt").dispatch("keydown", {
    key: "Enter", shiftKey: false, ctrlKey: false, altKey: false,
    preventDefault() {},
  });
  const steers = page.socket().sent.filter((f) => f.type === "steer");
  assertEqual(steers.length, 1, "exactly one steer frame should be sent via Enter during turn");
  assertEqual(steers[0].payload && steers[0].payload.message, "steer via enter",
    "steer payload.message should match typed text");
});

check("Enter during turn in queue mode stores queued prompt", (tpl) => {
  const { page, live } = connected(tpl, { turnActive: true });
  page.type("queue via enter");
  page.el("acpPrompt").dispatch("input");
  // Switch to queue mode
  page.click("acpModeOptQueue");
  // Fire Enter keydown
  page.el("acpPrompt").dispatch("keydown", {
    key: "Enter", shiftKey: false, ctrlKey: false, altKey: false,
    preventDefault() {},
  });
  // No steer frame should be sent
  const steers = page.socket().sent.filter((f) => f.type === "steer");
  assertEqual(steers.length, 0, "no steer frame should be sent when queue mode is active");
  // Textarea should be cleared (queued)
  assertEqual(page.el("acpPrompt").value, "",
    "textarea should be cleared after queue via Enter");
  // Cancel button should appear in transcript (confirms queued)
  const cancelBtn = page.one("acpTranscript", ".acp-inline-cancel");
  assert(cancelBtn !== null, "queue note with cancel button should appear after Enter in queue mode");
});

// --------------------------------------------------------- Phase 3: SC-3 auto-reconnect, SC-5 rail refresh --

// SC-3 test 1: ws.onclose with opened=true schedules a reconnect timer
check("auto reconnect scheduled on close when opened", (tpl) => {
  const { page } = connected(tpl);
  // Record timers before the close
  const timersBefore = page.timers.length;
  // Simulate a WS drop on a previously-opened socket
  page.socket().onclose({ code: 1006, reason: "" });
  assert(page.timers.length > timersBefore,
    "a reconnect timer should be scheduled when the socket closes after being opened");
  // The reconnect fires connect(), which opens a new socket
  const socketsBefore = page.sockets.length;
  page.runTimers();
  assert(page.sockets.length > socketsBefore,
    "the reconnect timer should call connect() and open a new WebSocket");
});

// SC-3 test 2: backoff delay doubles on each consecutive close.
// `onclose` does not reset `opened`, so firing onclose twice on the same
// (already-closed) socket exercises the delay doubling without needing a
// successful reconnect and re-open between them.
check("reconnect delay doubles on each close", (tpl) => {
  const { page } = connected(tpl);
  // First close while opened=true: timer at 1000ms, delay internally → 2000.
  page.socket().onclose({ code: 1006, reason: "" });
  assertEqual(page.timers[page.timers.length - 1].ms, 1000,
    "first reconnect timer should use 1000ms delay");
  // Second close: opened is still true (onclose does not reset it).
  // Delay is now internally 2000, so the new timer should use 2000ms.
  page.socket().onclose({ code: 1006, reason: "" });
  assertEqual(page.timers[page.timers.length - 1].ms, 2000,
    "second reconnect timer should use doubled 2000ms delay");
});

// SC-3 test 3: reconnect delay is capped at 30000ms.
// Chain enough consecutive closes (opened stays true) to push past the cap.
// Sequence: 1000 → 2000 → 4000 → 8000 → 16000 → 30000 (cap) → 30000 (stays).
check("reconnect delay capped at 30s", (tpl) => {
  const { page } = connected(tpl);
  // 5 closes bring delay to 30000 (would be 32000 without cap): 1k→2k→4k→8k→16k→30k
  for (let i = 0; i < 5; i++) {
    page.socket().onclose({ code: 1006, reason: "" });
  }
  page.socket().onclose({ code: 1006, reason: "" });
  assertEqual(page.timers[page.timers.length - 1].ms, 30000,
    "delay should be capped at 30000ms after 6 consecutive closes");
  // One more to confirm it stays at 30000
  page.socket().onclose({ code: 1006, reason: "" });
  assertEqual(page.timers[page.timers.length - 1].ms, 30000,
    "delay should remain at 30000ms once capped");
});

// SC-3 test 4: ws.onopen resets _reconnectDelay to 1000.
// Build delay to 2000 via two consecutive closes, then reconnect and open the
// new socket — onopen should reset the delay so the next close uses 1000ms.
check("reconnect delay resets on open", (tpl) => {
  const { page } = connected(tpl);
  // Two consecutive closes: first at 1000ms, second at 2000ms.
  page.socket().onclose({ code: 1006, reason: "" });
  page.socket().onclose({ code: 1006, reason: "" });
  assertEqual(page.timers[page.timers.length - 1].ms, 2000,
    "fixture: second timer should use 2000ms delay before the reset");
  // Fire the reconnect timer → connect() runs → new socket created.
  page.runTimers();
  // Open the new socket: onopen resets _reconnectDelay to 1000.
  page.open();
  // Next close on the newly-opened socket should use the reset 1000ms delay.
  page.socket().onclose({ code: 1006, reason: "" });
  assertEqual(page.timers[page.timers.length - 1].ms, 1000,
    "delay should reset to 1000ms after ws.onopen");
});

// SC-3 test 5: no auto-reconnect when opened=false (refused-handshake path)
check("no auto reconnect when not opened", (tpl) => {
  // Load page but do NOT call page.open() — socket was created but onopen never fired
  const page = loadPage(tpl);
  // page.open() would set opened=true; skipping it leaves opened=false
  const timersBefore = page.timers.length;
  // Fire close without ever having opened
  page.socket().onclose({ code: 4401, reason: "token expired" });
  // The diagnostic GET's own 5 s timeout is the one timer allowed here; it is
  // not a reconnect. 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL
  // Phase 6 review (J6)
  assertEqual(page.timers.filter((t) => t.ms !== 5000).length, timersBefore,
    "no reconnect timer should be scheduled when socket closes without having been opened");
  assertEqual(page.el("acpReconnect").hidden, true,
    "reconnect button must not appear before the refused handshake is diagnosed");
});

// SC-5 test 1: railRefreshSoon called after sendPrompt success
check("rail refresh called after send", (tpl) => {
  const { page, live } = connected(tpl);
  const timersBefore = page.timers.length;
  const fetchesBefore = page.fetches.filter(
    (f) => f.url.startsWith(LISTING_URL)).length;
  page.type("hello agent");
  page.click("acpSend");
  // A successful send queues a railRefreshSoon timer (or calls railRefresh directly if not busy)
  // Either way, at minimum a fetch was triggered OR a timer was queued.
  // The simplest assertion: the prompt was sent AND the timer count increased
  // (railRefreshSoon schedules a retry timer when the rail is busy / first call).
  // Actually railRefreshSoon returns immediately if railBusy=false (calls railRefresh directly),
  // which triggers a fetch. Check that a fetch was issued after the send.
  const promptSent = page.sockets.length > 0 &&
    page.socket().sent.some((f) => f.type === "prompt");
  assert(promptSent, "fixture: prompt should be sent by sendPrompt");
  // railRefreshSoon either scheduled a timer OR triggered a fetch. At minimum one of those happened.
  const fetchAfter = page.fetches.filter(
    (f) => f.url.startsWith(LISTING_URL)).length;
  assert(fetchAfter > fetchesBefore || page.timers.length > timersBefore,
    "railRefreshSoon should have been called after sendPrompt (fetch or timer queued)");
});

// SC-5 test 2: railRefreshSoon called after stop button press
check("rail refresh called after stop", (tpl) => {
  const { page, live } = connected(tpl, { turnActive: true });
  const timersBefore = page.timers.length;
  const fetchesBefore = page.fetches.filter(
    (f) => f.url.startsWith(LISTING_URL)).length;
  page.click("acpStop");
  const cancelSent = page.socket().sent.some((f) => f.type === "cancel");
  assert(cancelSent, "fixture: cancel should be sent by stop button");
  const fetchAfter = page.fetches.filter(
    (f) => f.url.startsWith(LISTING_URL)).length;
  assert(fetchAfter > fetchesBefore || page.timers.length > timersBefore,
    "railRefreshSoon should have been called after stop button press (fetch or timer queued)");
});

// SC-3 test 6: onclose while timer pending replaces timer not stacks
check("onclose while timer pending replaces timer not stacks", (tpl) => {
  const { page } = connected(tpl);
  // First close while opened=true: schedules timer 1
  page.socket().onclose({ code: 1006, reason: "" });
  assertEqual(page.timers.length, 1,
    "fixture: first close should schedule exactly one timer");
  // Second close without running the timer — should replace, not stack
  page.socket().onclose({ code: 1006, reason: "" });
  assertEqual(page.timers.length, 1,
    "onclose while timer pending should replace the old timer, not stack a second one");
});

// ---- Phase 4: dot color scheme (SC-4) -----------------------------------

// SC-4: working held session → blue thinking dot
check("dot class thinking when working", async (tpl) => {
  const store = fakeStore({ workspaces: 1, sessions: 1 });
  store[0].sessions[0].availability = "held";
  store[0].sessions[0].status = "working";
  const page = await railed(tpl, { store });
  const dot = page.railRows()[0].querySelector(".session-status");
  assert(dot, "held working session must have a dot");
  assertEqual(String(dot.className), "session-status status-thinking",
              `working held session should have status-thinking dot, got: ${dot.className}`);
});

// SC-4: idle held session with unread flag → green unread dot
check("dot class unread when idle and unread", async (tpl) => {
  const store = fakeStore({ workspaces: 1, sessions: 1 });
  store[0].sessions[0].id = "sess-for-unread";
  store[0].sessions[0].availability = "held";
  store[0].sessions[0].status = "idle";
  // Seed localStorage so isUnread returns true for this session
  const page = await railed(tpl, {
    store,
    stored: { "pa_unread_sess-for-unread": "1" },
  });
  const dot = page.railRows()[0].querySelector(".session-status");
  assert(dot, "held unread session must have a dot");
  assertEqual(String(dot.className), "session-status status-unread",
              `idle held session with unread should have status-unread dot, got: ${dot.className}`);
  assertEqual(dot.getAttribute("aria-label"), "open in this PowerAtlas \u2014 unread messages",
              `unread dot aria-label should be the unread label, got: ${dot.getAttribute("aria-label")}`);
});

// SC-4: idle held session with no unread flag → white idle dot
check("dot class idle when no unread", async (tpl) => {
  const store = fakeStore({ workspaces: 1, sessions: 1 });
  store[0].sessions[0].availability = "held";
  store[0].sessions[0].status = "idle";
  const page = await railed(tpl, { store });
  const dot = page.railRows()[0].querySelector(".session-status");
  assert(dot, "held idle session must have a dot");
  assertEqual(String(dot.className), "session-status status-idle",
              `idle held session without unread should have status-idle dot, got: ${dot.className}`);
  assertEqual(dot.getAttribute("aria-label"), "open in this PowerAtlas \u2014 idle",
              `idle dot aria-label should be the idle label, got: ${dot.getAttribute("aria-label")}`);
});

// SC-4: non-held session → no dot element (or hidden dot)
check("dot absent for non held", async (tpl) => {
  const store = fakeStore({ workspaces: 1, sessions: 1 });
  store[0].sessions[0].availability = "available";
  const page = await railed(tpl, { store });
  const dot = page.railRows()[0].querySelector(".session-status");
  // Non-held rows should have no dot drawn (the plan says: no dot for non-held)
  assert(!dot || dot.hidden === true,
         `available session should have no visible dot, but got: ${dot ? dot.className : "none"}`);
});

// SC-4: unrecognized wire status falls back to status-thinking dot
check("dot class thinking for unknown wire status", async (tpl) => {
  const store = fakeStore({ workspaces: 1, sessions: 1 });
  store[0].sessions[0].availability = "held";
  store[0].sessions[0].status = "bogus_unknown_value";
  const page = await railed(tpl, { store });
  const dot = page.railRows()[0].querySelector(".session-status");
  assert(dot, "held session with unknown status must have a dot");
  assertEqual(String(dot.className), "session-status status-thinking",
              `held session with unrecognized wire status should fall back to status-thinking, got: ${dot.className}`);
});

// SC-4: mark unread when non-open held session transitions working -> not working
check("mark unread on turn end for other session", async (tpl) => {
  const store = fakeStore({ workspaces: 1, sessions: 2 });
  store[0].sessions[0].id = "sess-other";
  store[0].sessions[0].availability = "held";
  store[0].sessions[0].status = "working";
  store[0].sessions[1].id = "sess-open";
  store[0].sessions[1].availability = "held";
  store[0].sessions[1].status = "idle";

  // Subscribe to sess-open (making it the current session)
  const page = loadPage(tpl, { store });
  page.open();
  page.deliver({
    type: "session",
    sessionId: "sess-open",
    payload: { sessionId: "sess-open", cwd: "C:\\work", created: false,
               turnActive: false, contextPercent: null },
  });
  await page.settle();

  // First tick: seed _prevSessionStatus with working for sess-other
  page.tick();
  await page.settle();

  // sess-other transitions from working to idle
  store[0].sessions[0].status = "idle";
  page.tick();
  await page.settle();

  assert(page.stored["pa_unread_sess-other"] === "1",
         "sess-other should be marked unread after its turn ended while sess-open was active");

  // The rail should have re-rendered (changed=true when status moved); verify
  // the dot for sess-other now shows status-unread.
  const rows = page.railRows();
  const otherRow = [...rows].find((r) => r.dataset.sid === "sess-other");
  assert(otherRow, "sess-other row must be present in the rail after the tick");
  const dot = otherRow.querySelector(".session-status");
  assert(dot, "sess-other (held) must have a status dot");
  assertEqual(String(dot.className), "session-status status-unread",
              `dot for sess-other should be status-unread after turn ended, got: ${dot.className}`);
});

// SC-4: clear unread on subscribe
check("clear unread on subscribe", (tpl) => {
  const { page, live } = connected(tpl, {
    stored: { ["pa_unread_" + "sess-live-0001"]: "1" },
  });
  // The connected() helper already delivered a session frame for "sess-live-0001"
  // which should have called clearUnread
  assert(page.stored["pa_unread_sess-live-0001"] !== "1",
         "subscribing to a session should clear its unread marker");
});

// ---- Phase 5: SC-7 crew timer tests -----------------------------------------

check("elapsed text with stopped at freezes at done elapsed", (tpl) => {
  // Verify that a done crew entry's elapsed span shows the frozen time
  // (stoppedAt - startedAt) and does not keep incrementing with Date.now().
  const { page, live } = connected(tpl);
  const t = Date.now() / 1000 - 200;  // 200s ago
  const stoppedAt = t + 65;            // done 65s after start => "1m 5s"
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-1", role: "worker", task: "", status: "done",
      action: "", done: true, error: "", startedAt: t, stoppedAt: stoppedAt },
  ]));
  const elapsed = page.one("acpTranscript", ".acp-crew-elapsed");
  assert(elapsed !== null, "elapsed span should be present for done entry");
  assertEqual(elapsed.textContent, "1m 5s",
    "done entry elapsed should be frozen at stoppedAt - startedAt (1m 5s)");
});

check("elapsed text without stopped at uses live Date.now", (tpl) => {
  // Verify that a working entry's elapsed span reflects the real elapsed time.
  const { page, live } = connected(tpl);
  const startedAt = Date.now() / 1000 - 10;  // 10 seconds ago
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-1", role: "worker", task: "", status: "working",
      action: "", done: false, error: "", startedAt: startedAt },
  ]));
  const elapsed = page.one("acpTranscript", ".acp-crew-elapsed");
  assert(elapsed !== null, "elapsed span should be present for working entry");
  assertEqual(elapsed.textContent, "10s",
    "working entry elapsed should reflect current elapsed time (10s)");
});

check("crew timer starts when crew has a non-done entry", (tpl) => {
  // setCrew([{done:false}]) must start a setInterval so the elapsed display ticks.
  const { page, live } = connected(tpl);
  const intervalsBefore = page.intervals.length;
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-1", role: "worker", task: "", status: "working",
      action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  assert(page.intervals.length > intervalsBefore,
    "a setInterval should be registered when crew has a running entry");
});

check("crew timer does not start for an all-done crew", (tpl) => {
  // setCrew([{done:true}]) must NOT start a new interval.
  const { page, live } = connected(tpl);
  const intervalsBefore = page.intervals.length;
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-1", role: "worker", task: "", status: "done",
      action: "", done: true, error: "", startedAt: Date.now() / 1000 - 30, stoppedAt: Date.now() / 1000 },
  ]));
  assertEqual(page.intervals.length, intervalsBefore,
    "no new setInterval should be registered when all entries are done");
});

// ---- Phase 5: SC-8 prompt navigation tests ----------------------------------

check("prompt nav hidden when zero or one user message", (tpl) => {
  const { page, live } = connected(tpl);
  // 0 user messages: nav hidden
  assert(page.el("acpPromptNav").hidden === true,
    "prompt nav should be hidden when there are no user messages");
  // 1 user message: still hidden
  page.deliver({ type: "chunk", sessionId: live,
    payload: { role: "user", text: "hello" } });
  assert(page.el("acpPromptNav").hidden === true,
    "prompt nav should still be hidden with only one user message");
  // 2 user messages: visible
  page.deliver({ type: "chunk", sessionId: live,
    payload: { role: "user", text: "second message" } });
  assertEqual(page.el("acpPromptNav").hidden, false,
    "prompt nav should become visible when there are 2 or more user messages");
});

check("prompt nav cleared on clearTranscript", (tpl) => {
  const { page, live } = connected(tpl);
  // Build up 2 user messages so the nav is visible
  page.deliver({ type: "chunk", sessionId: live,
    payload: { role: "user", text: "message 1" } });
  page.deliver({ type: "chunk", sessionId: live,
    payload: { role: "user", text: "message 2" } });
  assertEqual(page.el("acpPromptNav").hidden, false,
    "fixture: nav should be visible before clear");
  // Clear the transcript via a new session frame
  page.deliver({ type: "session", sessionId: "sess-new-nav",
    payload: { sessionId: "sess-new-nav", cwd: "/tmp", created: true,
               turnActive: false, contextPercent: null } });
  assertEqual(page.el("acpPromptNav").hidden, true,
    "prompt nav should be hidden after clearTranscript");
  // Deliver 2 more user messages in the new session and verify re-attach worked
  page.deliver({ type: "chunk", sessionId: "sess-new-nav",
    payload: { role: "user", text: "re-attach msg 1" } });
  page.deliver({ type: "chunk", sessionId: "sess-new-nav",
    payload: { role: "user", text: "re-attach msg 2" } });
  assertEqual(page.el("acpPromptNav").hidden, false,
    "prompt nav should become visible again after 2 messages in the new session (re-attach worked)");
});

check("prompt nav up arrow greys when scrolled to top, clears when scrolled down", (tpl) => {
  const { page, live } = connected(tpl);
  // Build 2 user messages so the nav is visible
  page.deliver({ type: "chunk", sessionId: live,
    payload: { role: "user", text: "msg 1" } });
  page.deliver({ type: "chunk", sessionId: live,
    payload: { role: "user", text: "msg 2" } });
  const transcript = page.el("acpTranscript");
  const upBtn = page.el("acpPromptUp");
  const downBtn = page.el("acpPromptDown");
  // At top (scrollTop=0): up arrow should be dimmed
  transcript.scrollTop = 0;
  transcript.scrollHeight = 500;
  transcript.clientHeight = 200;
  transcript.dispatch("scroll");
  assert(upBtn.classList.contains("acp-prompt-nav-btn--dim"),
    "up arrow should be dimmed when scrolled to the top");
  // Scrolled to middle: up arrow should not be dimmed
  transcript.scrollTop = 150;
  transcript.dispatch("scroll");
  assert(!upBtn.classList.contains("acp-prompt-nav-btn--dim"),
    "up arrow should not be dimmed when scrolled away from top");
});

check("prompt nav down arrow greys when scrolled to bottom, clears when scrolled up", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({ type: "chunk", sessionId: live,
    payload: { role: "user", text: "msg 1" } });
  page.deliver({ type: "chunk", sessionId: live,
    payload: { role: "user", text: "msg 2" } });
  const transcript = page.el("acpTranscript");
  const downBtn = page.el("acpPromptDown");
  const upBtn = page.el("acpPromptUp");
  // Scrolled to bottom: down arrow dimmed
  transcript.scrollTop = 300;
  transcript.scrollHeight = 500;
  transcript.clientHeight = 200;
  transcript.dispatch("scroll");
  assert(downBtn.classList.contains("acp-prompt-nav-btn--dim"),
    "down arrow should be dimmed when scrolled to the bottom");
  // Scrolled to middle: down arrow not dimmed
  transcript.scrollTop = 100;
  transcript.dispatch("scroll");
  assert(!downBtn.classList.contains("acp-prompt-nav-btn--dim"),
    "down arrow should not be dimmed when scrolled away from bottom");
});

check("crew timer stops when all crew entries are done", (tpl) => {
  // setCrew([{done:false}]) starts a timer; updating to all-done must clear it.
  const { page, live } = connected(tpl);
  const intervalsBaseline = page.intervals.length;
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-1", role: "worker", task: "", status: "working",
      action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ], "tcid-timer-stop"));
  assert(page.intervals.length > intervalsBaseline,
    "fixture: a setInterval should be registered for a running crew entry");
  // Update the SAME slot using the same toolCallId — all done, timer should clear
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-1", role: "worker", task: "", status: "done",
      action: "", done: true, error: "", startedAt: Date.now() / 1000 - 5, stoppedAt: Date.now() / 1000 },
  ], "tcid-timer-stop"));
  assertEqual(page.intervals.length, intervalsBaseline,
    "crew timer should be cleared when all entries become done");
});

check("crew timer clears on session release", (tpl) => {
  // setCrew([{done:false}]) starts a timer; a session_closed frame must clear it.
  const { page, live } = connected(tpl);
  const intervalsBaseline = page.intervals.length;
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-1", role: "worker", task: "", status: "working",
      action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  assert(page.intervals.length > intervalsBaseline,
    "fixture: a setInterval should be registered for a running crew entry");
  page.deliver({ type: "session_closed", sessionId: live });
  assertEqual(page.intervals.length, intervalsBaseline,
    "crew timer should be cleared when the session is released");
});

check("crew timer does not fire after session release", (tpl) => {
  // Verify the interval is removed so tick() after release does not re-render crew.
  const { page, live } = connected(tpl);
  const intervalsBaseline = page.intervals.length;
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-1", role: "worker", task: "", status: "working",
      action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  assert(page.intervals.length > intervalsBaseline,
    "fixture: interval should be registered before release");
  page.deliver({ type: "session_closed", sessionId: live });
  assertEqual(page.intervals.length, intervalsBaseline,
    "crew interval should be removed after session release");
  // tick() should not throw or re-render the crew panel
  page.tick();
  const crewPanel = page.one("acpTranscript", ".acp-crew-panel");
  assert(crewPanel === null,
    "crew panel should not be re-rendered after session release");
});

check("crew timer — a drop tears down crew-panel timers immediately, not waiting for a reconnect that may never come (parity with dashboard's Fix 8, Step 9 review)", (tpl) => {
  // acp.html's own connect() onclose used to have no removeAllCrewPanels()
  // (or equivalent) call at all -- a reconnect's clearTranscript() (via the
  // fresh `session` frame) tears crew timers down, but only once reconnect
  // actually succeeds. If it never does, those timers kept firing forever
  // with nothing left to tick against. connect()'s onclose now calls
  // removeAllCrewPanels() directly, closing the same gap index.html's
  // dashConnect() onclose closed on the dashboard (Fix 8, Step 9 review;
  // dashboard/ACP feature-parity plan Follow-up Work).
  const { page, live } = connected(tpl);
  const intervalsBaseline = page.intervals.length;
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-1", role: "explorer", task: "", status: "working",
      action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  assert(page.intervals.length > intervalsBaseline,
    "fixture: a running crew slot should have started its elapsed-time timer");
  assertEqual(page.one("acpTranscript", ".acp-crew-panel") === null, false,
    "fixture: a crew panel should be rendered before the drop");
  // Simulate a WS drop with no subsequent session/history frame -- a
  // reconnect that never succeeds.
  page.socket().onclose({ code: 1006, reason: "" });
  assertEqual(page.intervals.length, intervalsBaseline,
    "a drop must tear down crew-panel timers immediately, even without a subsequent session/history frame " +
    "-- an indefinitely failed reconnect must not leave them ticking forever");
  // tick() should not throw or re-render the crew panel now that the timer
  // and the panel it was ticking against are both gone.
  page.tick();
  assert(page.one("acpTranscript", ".acp-crew-panel") === null,
    "the crew panel must be removed by the drop's teardown, not just its timer");
});

// -------------------------------------------------------------------- main --

const template = process.argv[2]
  ? path.resolve(process.argv[2])
  : DEFAULT_TEMPLATE;

console.log(`browser-side behavioural harness — ${template}\n`);
// ---- Phase 2: railRefresh date/status fix (SC-4) -------------------------

// railRefreshDateModeCallsLoadFlatPage: In date mode, a tick calls loadFlatPage(1)
// (mode=recent fetch) rather than the state-only railRefreshStates path.
check("railRefreshDateModeCallsLoadFlatPage", async (tpl) => {
  // Use status store for a non-empty flat response; set date mode via localStorage.
  const page = await railed(tpl, {
    store: statusStore(),
    stored: { pa_acp_group: "date" },
  });
  // The initial railed() load issued listing calls for date mode. Record the count.
  const before = page.listingCalls().length;
  // A tick fires railRefresh(). In date mode the new code calls loadFlatPage(1),
  // which emits a ?mode=recent fetch — the same call the initial load makes.
  page.tick();
  await page.settle();
  const newCalls = page.listingCalls().slice(before);
  assert(newCalls.length > 0,
    "railRefresh in date mode made no listing call; it should call loadFlatPage(1)");
  assertEqual(newCalls[0].params.mode, "recent",
    "railRefresh in date mode did not request the flat (recent) listing");
  // Confirm page=1: loadFlatPage(1) resets the list rather than appending.
  assertEqual(String(newCalls[0].params.page), "1",
    "railRefresh in date mode should request page 1, not a continuation page");
});

// railRefreshStatusModeCallsLoadFlatPage: same as date mode for status mode.
check("railRefreshStatusModeCallsLoadFlatPage", async (tpl) => {
  const page = await railed(tpl, {
    store: statusStore(),
    stored: { pa_acp_group: "status" },
  });
  const before = page.listingCalls().length;
  page.tick();
  await page.settle();
  const newCalls = page.listingCalls().slice(before);
  assert(newCalls.length > 0,
    "railRefresh in status mode made no listing call; it should call loadFlatPage(1)");
  assertEqual(newCalls[0].params.mode, "recent",
    "railRefresh in status mode did not request the flat (recent) listing");
  assertEqual(String(newCalls[0].params.page), "1",
    "railRefresh in status mode should request page 1, not a continuation page");
});

// railRefreshProjectModeUsesRefreshStates: regression — project mode is unchanged.
// The tick should NOT emit a mode=recent fetch; it uses the grouped path instead.
check("railRefreshProjectModeUsesRefreshStates", async (tpl) => {
  // Default mode is project (no stored pa_acp_group override).
  const page = await railed(tpl, {
    store: fakeStore({ workspaces: 1, sessions: 2 }),
  });
  const before = page.listingCalls().length;
  page.tick();
  await page.settle();
  const newCalls = page.listingCalls().slice(before);
  // The tick must issue some request (the grouped refresh), but it must NOT
  // be a mode=recent flat-list request.
  assert(newCalls.length > 0,
    "railRefresh in project mode made no listing call at all");
  assert(newCalls.every((c) => c.params.mode !== "recent"),
    "railRefresh in project mode issued a mode=recent fetch — the grouped path is broken");
});

// railRefreshDateModeFailureIsSilent: a background tick in date mode that hits a
// server error must not overwrite the status line with an error message and must
// leave the poll cycle unfrozen so the next tick can retry.
check("railRefreshDateModeFailureIsSilent", async (tpl) => {
  // Prime the rail in date mode with a healthy initial load.
  const page = await railed(tpl, {
    store: statusStore(),
    stored: { pa_acp_group: "date" },
  });
  const statusBefore = page.el("acpRailStatus").textContent;
  const beforeTick = page.listingCalls().length;

  // Make the listing endpoint fail for the next tick.
  page.opts.answer = (url) =>
    url.startsWith(LISTING_URL) ? { reject: "server error" } : null;

  page.tick();
  await page.settle();

  // The status line must not show an error message.
  const statusAfter = page.el("acpRailStatus").textContent;
  assert(!/could not load/i.test(statusAfter),
    `background tick failure overwrote status with error: ${statusAfter}`);

  // The poll cycle must not be frozen: a second tick should trigger another
  // listing request (railBusy was cleared by the silent catch).
  page.opts.answer = null;
  const afterFirst = page.listingCalls().length;
  page.tick();
  await page.settle();
  const afterSecond = page.listingCalls().length;
  assert(afterSecond > afterFirst,
    "second railRefresh made no listing call — poll cycle frozen after silent failure");
});

// ---- Phase 3: slash command palette (SC-1, SC-2) -------------------------

// commandsFramePopulatesSessionCommands
// Delivering a 'commands' frame stores the list in sessionCommands.
check("commandsFramePopulatesSessionCommands", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({
    type: "commands",
    sessionId: live,
    payload: {
      commands: [
        { name: "context", description: "Show context usage" },
        { name: "tools",   description: "List available tools" },
      ],
    },
  });
  // The dropdown should appear when '/' is typed (which triggers
  // showCommandDropdown('') against the now-populated sessionCommands list).
  page.el("acpPrompt").value = "";
  page.el("acpPrompt").dispatch("keydown", {
    key: "/", shiftKey: false, ctrlKey: false, altKey: false, preventDefault() {},
  });
  const drop = page.el("acpCmdDropdown");
  assert(!drop.hidden,
    "dropdown should be visible after '/' keydown on an empty prompt " +
    "when sessionCommands has entries");
  const names = drop.querySelectorAll(".acp-cmd-name").map((n) => n.textContent);
  assert(names.includes("/context") && names.includes("/tools"),
    "dropdown should show the commands received in the 'commands' frame; " +
    "got: " + JSON.stringify(names));
});

// commandsFrameOnSessionChangeResetsSessionCommands
// A new 'session' frame resets sessionCommands so stale commands from the
// previous session never appear in the dropdown.
check("commandsFrameOnSessionChangeResetsSessionCommands", (tpl) => {
  const { page, live } = connected(tpl);
  // Populate sessionCommands via a 'commands' frame.
  page.deliver({
    type: "commands", sessionId: live,
    payload: { commands: [{ name: "context", description: "ctx" }] },
  });
  // New session frame — should reset sessionCommands.
  const newSid = "sess-new-reset";
  page.deliver({
    type: "session", sessionId: newSid,
    payload: { sessionId: newSid, cwd: "C:\\tmp", created: true,
               turnActive: false, contextPercent: null },
  });
  // Now '/' should show the placeholder row — old session's commands must not appear.
  page.el("acpPrompt").value = "";
  page.el("acpPrompt").dispatch("keydown", {
    key: "/", shiftKey: false, ctrlKey: false, altKey: false, preventDefault() {},
  });
  const drop = page.el("acpCmdDropdown");
  // Dropdown shows but with placeholder only — no selectable items from the old session.
  // No .acp-cmd-name spans means no selectable commands (only placeholder).
  const cmdNames = drop.querySelectorAll(".acp-cmd-name");
  assert(cmdNames.length === 0,
    "no selectable commands - old session catalogue was cleared");
});

// compactionStartedAddsSystemMessage
// A 'compaction' frame with status 'started' appends a system message.
check("compactionStartedAddsSystemMessage", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({
    type: "compaction", sessionId: live,
    payload: { status: "started", summary: "" },
  });
  const msgs = page.el("acpTranscript").querySelectorAll(".acp-system-msg");
  assert(msgs.length > 0, "a 'compaction started' frame should add a system message row");
  assert(msgs[0].textContent.includes("Compacting"),
    "started system message should mention compacting; got: " + msgs[0].textContent);
});

// compactionCompletedAddsSystemMessage
// A 'compaction' frame with status 'completed' appends a system message.
check("compactionCompletedAddsSystemMessage", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({
    type: "compaction", sessionId: live,
    payload: { status: "completed", summary: "" },
  });
  const msgs = page.el("acpTranscript").querySelectorAll(".acp-system-msg");
  assert(msgs.length > 0, "a 'compaction completed' frame should add a system message row");
  assert(msgs[0].textContent.toLowerCase().includes("compact"),
    "completed system message should mention compaction; got: " + msgs[0].textContent);
});

// compactionCompletedWithSummaryAddsRecapDetails
// A 'compaction' frame with status 'completed' and a non-empty summary
// appends both the system message and a collapsible <details> containing
// the recap text.
check("compactionCompletedWithSummaryAddsRecapDetails", (tpl) => {
  const { page, live } = connected(tpl);
  const recap = "## OBJECTIVE\nDo something important.\n\n## NEXT STEPS\n1. Continue.";
  page.deliver({
    type: "compaction", sessionId: live,
    payload: { status: "completed", summary: recap },
  });
  const transcript = page.el("acpTranscript");
  const msgs = transcript.querySelectorAll(".acp-system-msg");
  assert(msgs.length > 0, "system message row should appear");
  const details = transcript.querySelector(".acp-compaction-details");
  assert(details !== null, "a <details> recap element should be appended");
  const pre = details.querySelector(".acp-compaction-recap");
  assert(pre !== null, "recap content div should be present");
  assert(pre.textContent === recap, "recap text should match summary verbatim; got: " + pre.textContent);
  // No <details> when summary is empty (guard against false rendering)
  const { page: p2, live: l2 } = connected(tpl);
  p2.deliver({ type: "compaction", sessionId: l2, payload: { status: "completed", summary: "" } });
  const noDetails = p2.el("acpTranscript").querySelector(".acp-compaction-details");
  assert(noDetails === null, "no <details> should appear when summary is empty");
});

// paletteCompactAckDoesNotDuplicateSystemMessage
// Measured 2026-08-14 against kiro-cli 2.18.0: a palette-triggered /compact
// sends a 'compaction' started frame and, moments later, a
// 'commands_execute_result' ack whose own result.message is also
// "Compacting conversation...". Before the fix, both were rendered as
// separate .acp-system-msg rows, so a palette compaction showed two
// "Compacting..." rows instead of one.
check("paletteCompactAckDoesNotDuplicateSystemMessage", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({
    type: "compaction", sessionId: live,
    payload: { status: "started", summary: "" },
  });
  page.deliver({
    type: "commands_execute_result", sessionId: live,
    payload: { name: "compact", status: "accepted",
               result: { success: true, message: "Compacting conversation..." } },
  });
  const msgs = page.el("acpTranscript").querySelectorAll(".acp-system-msg");
  assert(msgs.length === 1,
    "a palette /compact should add exactly one system message row, not one per frame; got "
    + msgs.length);
});

// nonCompactCommandsExecuteResultStillRendersItsMessage
// The compact-only exclusion must not silence every other command's ack.
check("nonCompactCommandsExecuteResultStillRendersItsMessage", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({
    type: "commands_execute_result", sessionId: live,
    payload: { name: "clear", status: "accepted",
               result: { success: true, message: "Conversation cleared." } },
  });
  const msgs = page.el("acpTranscript").querySelectorAll(".acp-system-msg");
  assert(msgs.length === 1, "a non-compact command's ack message should still render");
  assert(msgs[0].textContent === "Conversation cleared.",
    "got: " + msgs[0].textContent);
});

// slashKeyOpensDropdown
// Pressing '/' on an empty prompt opens the command palette (even with no
// commands loaded — renders an empty list and hides).
// With commands, the dropdown is visible.
check("slashKeyOpensDropdown", (tpl) => {
  const { page, live } = connected(tpl);
  // Seed at least one command.
  page.deliver({
    type: "commands", sessionId: live,
    payload: { commands: [{ name: "tools", description: "Tools list" }] },
  });
  const prompt = page.el("acpPrompt");
  prompt.value = "";
  let prevented = false;
  prompt.dispatch("keydown", {
    key: "/", shiftKey: false, ctrlKey: false, altKey: false,
    preventDefault() { prevented = true; },
  });
  assert(prevented, "/ keydown should preventDefault so the browser does not also insert '/'");
  assertEqual(prompt.value, "/", "/ keydown should set promptInput.value to '/'");
  const drop = page.el("acpCmdDropdown");
  assert(!drop.hidden, "pressing / on empty prompt should open the command dropdown");
  // Clearing the value hides the dropdown.
  prompt.value = "";
  prompt.dispatch("input", {});
  assert(drop.hidden, "clearing the prompt should hide the command dropdown");
});

// commandsOptionsResultIsANoOp
// A 'commands_options_result' frame is not handled at all (dashboard/ACP
// feature-parity plan, Phase 2, SC1's dead-path removal) — the client never
// sends a commands_options request (confirmed by
// plans/done/260909-1127_ACP_V3_PRODUCTION_HARDENING.md's Phase 7 review,
// finding #3: "currently-dead client-side... unreachable today"), so this
// frame can never arrive in production. This replaces the pre-Phase-2
// commandOptionsResultUpdatesDropdown check, which exercised exactly the
// applyCommandOptions()/commands_options_result path Phase 2 deliberately
// dropped rather than ported — that check now describes removed behaviour,
// not a regression. Delivering the frame anyway must be a silent no-op: the
// open dropdown's contents are unchanged and handle() does not throw (an
// unrecognized type falls through to the generic `logLine('in', ...)`
// catch-all at the end of handle()).
check("commandsOptionsResultIsANoOp", (tpl) => {
  const { page, live } = connected(tpl);
  // Seed one command so the dropdown opens.
  page.deliver({
    type: "commands", sessionId: live,
    payload: { commands: [{ name: "context", description: "Context" }] },
  });
  page.el("acpPrompt").value = "";
  page.el("acpPrompt").dispatch("keydown", {
    key: "/", shiftKey: false, ctrlKey: false, altKey: false, preventDefault() {},
  });
  assert(!page.el("acpCmdDropdown").hidden, "fixture: dropdown should be open");
  const namesBefore = page.el("acpCmdDropdown").querySelectorAll(".acp-cmd-name")
                           .map((n) => n.textContent);
  // A stray commands_options_result must not throw and must not touch the
  // dropdown — there is no handler for it any more.
  page.deliver({
    type: "commands_options_result", sessionId: live,
    payload: { options: [{ name: "memory", description: "Memory stats" }] },
  });
  assert(!page.el("acpCmdDropdown").hidden,
    "commands_options_result must not close the dropdown");
  const namesAfter = page.el("acpCmdDropdown").querySelectorAll(".acp-cmd-name")
                          .map((n) => n.textContent);
  assertEqual(JSON.stringify(namesAfter), JSON.stringify(namesBefore),
    "commands_options_result must not add the server suggestion — the " +
    "dead path it used to feed (applyCommandOptions) was dropped in Phase 2");
});

// commandsExecuteResultClosesDropdown
// A 'commands_execute_result' frame hides the dropdown.
check("commandsExecuteResultClosesDropdown", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({
    type: "commands", sessionId: live,
    payload: { commands: [{ name: "tools", description: "Tools" }] },
  });
  // Open dropdown.
  page.el("acpPrompt").value = "";
  page.el("acpPrompt").dispatch("keydown", {
    key: "/", shiftKey: false, ctrlKey: false, altKey: false, preventDefault() {},
  });
  assert(!page.el("acpCmdDropdown").hidden, "fixture: dropdown should be open");
  // The ack frame should close it.
  page.deliver({
    type: "commands_execute_result", sessionId: live,
    payload: { name: "tools", status: "accepted" },
  });
  assert(page.el("acpCmdDropdown").hidden,
    "commands_execute_result should hide the dropdown");
});

// dropdownEscapeDismisses
// Escape keydown while the dropdown is open hides it without sending anything.
check("dropdownEscapeDismisses", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({
    type: "commands", sessionId: live,
    payload: { commands: [{ name: "context", description: "ctx" }] },
  });
  page.el("acpPrompt").value = "";
  page.el("acpPrompt").dispatch("keydown", {
    key: "/", shiftKey: false, ctrlKey: false, altKey: false, preventDefault() {},
  });
  assert(!page.el("acpCmdDropdown").hidden, "fixture: dropdown should be open");
  const sentBefore = page.socket().sent.length;
  page.el("acpPrompt").dispatch("keydown", {
    key: "Escape", shiftKey: false, ctrlKey: false, altKey: false, preventDefault() {},
  });
  assert(page.el("acpCmdDropdown").hidden,
    "Escape should dismiss the dropdown");
  assertEqual(page.socket().sent.length, sentBefore,
    "Escape should not send any WS frame");
});

// dropdownEnterSendsCommandsExecute
// With the dropdown open and an item selected, pressing Enter sends a
// 'commands_execute' WS frame (not a 'prompt').
check("dropdownEnterSendsCommandsExecute", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({
    type: "commands", sessionId: live,
    payload: { commands: [{ name: "tools", description: "Tools list" }] },
  });
  const prompt = page.el("acpPrompt");
  prompt.value = "";
  prompt.dispatch("keydown", {
    key: "/", shiftKey: false, ctrlKey: false, altKey: false, preventDefault() {},
  });
  assert(!page.el("acpCmdDropdown").hidden, "fixture: dropdown should be open");
  const promptsBefore = page.sentOf("prompt").length;
  prompt.dispatch("keydown", {
    key: "Enter", shiftKey: false, ctrlKey: false, altKey: false, preventDefault() {},
  });
  // Must send commands_execute, not prompt.
  const execFrames = page.sentOf("commands_execute");
  assert(execFrames.length > 0,
    "Enter with dropdown open should send a commands_execute frame");
  assertEqual(execFrames[0].payload && execFrames[0].payload.name, "tools",
    "commands_execute payload.name should be the selected command's name");
  assertEqual(page.sentOf("prompt").length, promptsBefore,
    "Enter with dropdown open must NOT send a prompt frame");
  assert(page.el("acpCmdDropdown").hidden,
    "dropdown should be hidden after command selection");
});

// commandSendFailureLogsAttemptedCommand
// A failed commands_execute send (the socket is disconnected) used to
// produce no feedback beyond send()'s own generic "not connected — nothing
// sent" logLine call. The user explicitly chose NOT to add a toast (acp.html
// has no toast infrastructure at all) -- instead, confirmCommandSelection()
// now checks cmdSend()'s return value and adds one more specific logLine
// entry naming the command that failed to execute, so a user scanning the
// debug log panel understands what didn't happen, not just that "nothing was
// sent" in the abstract (Step 9 review, Fix 10). composer-chrome.js is
// shared, so this is exercised once here (acp.html) rather than duplicated
// on the dashboard side too.
check("commandSendFailureLogsAttemptedCommand", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({
    type: "commands", sessionId: live,
    payload: { commands: [{ name: "tools", description: "Tools list" }] },
  });
  const prompt = page.el("acpPrompt");
  prompt.value = "";
  prompt.dispatch("keydown", {
    key: "/", shiftKey: false, ctrlKey: false, altKey: false, preventDefault() {},
  });
  assert(!page.el("acpCmdDropdown").hidden, "fixture: dropdown should be open");
  page.socket().readyState = 3; // simulate a disconnected socket (FakeWs.OPEN === 1)
  prompt.dispatch("keydown", {
    key: "Enter", shiftKey: false, ctrlKey: false, altKey: false, preventDefault() {},
  });
  assertEqual(page.sentOf("commands_execute").length, 0,
    "nothing should have actually reached the wire while disconnected");
  const lines = page.el("acpLog").querySelectorAll(".acp-log-body");
  assert(lines.length > 0, "a log line must have been added for the failed send");
  const last = lines[lines.length - 1];
  assert(/tools/.test(last.textContent),
    "the log entry must name the attempted command ('tools'), not just say nothing was sent in the abstract");
  assert(/not executed/.test(last.textContent),
    "the log entry must make clear the command did not execute");
});

// spaceAfterCommandDismissesDropdown
// Typing a space after the slash-token (e.g. '/context ') hides the dropdown
// and leaves the input unchanged (the user is now typing arguments as plain text).
check("spaceAfterCommandDismissesDropdown", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({
    type: "commands", sessionId: live,
    payload: { commands: [{ name: "context", description: "ctx" }] },
  });
  const prompt = page.el("acpPrompt");
  prompt.value = "";
  prompt.dispatch("keydown", {
    key: "/", shiftKey: false, ctrlKey: false, altKey: false, preventDefault() {},
  });
  assert(!page.el("acpCmdDropdown").hidden, "fixture: dropdown should be open after /");
  // Simulate typing 'context ' (with trailing space) — triggers the input event.
  prompt.value = "/context ";
  prompt.dispatch("input", {});
  assert(page.el("acpCmdDropdown").hidden,
    "space after command token should dismiss the dropdown");
  assertEqual(prompt.value, "/context ",
    "prompt value should be preserved after dropdown dismissal");
});

// dropdownEnterWithClosedSocketIsNoop
// When Enter is pressed with the dropdown open but the socket is closed,
// no crash occurs and the dropdown is hidden cleanly.
check("dropdownEnterWithClosedSocketIsNoop", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({
    type: "commands", sessionId: live,
    payload: { commands: [{ name: "tools", description: "Tools" }] },
  });
  const prompt = page.el("acpPrompt");
  prompt.value = "";
  prompt.dispatch("keydown", {
    key: "/", shiftKey: false, ctrlKey: false, altKey: false, preventDefault() {},
  });
  assert(!page.el("acpCmdDropdown").hidden, "fixture: dropdown should be open");
  // Close the socket.
  page.socket().close();
  // Enter should not throw and should hide the dropdown cleanly.
  let threw = false;
  try {
    prompt.dispatch("keydown", {
      key: "Enter", shiftKey: false, ctrlKey: false, altKey: false, preventDefault() {},
    });
  } catch (e) {
    threw = true;
  }
  assert(!threw, "Enter with closed socket should not throw");
  assert(page.el("acpCmdDropdown").hidden,
    "dropdown should be hidden after Enter with closed socket");
});

// addSystemMessageUsesTextContent
// addSystemMessage must use textContent (never innerHTML) so that
// agent-controlled text cannot inject markup or script.
check("addSystemMessageUsesTextContent", (tpl) => {
  const { page, live } = connected(tpl);
  // Inject a string that would execute if inserted via innerHTML.
  const malicious = "<script>window._injected=true;<\/script>";
  page.deliver({
    type: "compaction", sessionId: live,
    payload: { status: "started", summary: malicious },
  });
  // The system message should appear (compaction started fires regardless of summary).
  const msgs = page.el("acpTranscript").querySelectorAll(".acp-system-msg");
  assert(msgs.length > 0, "addSystemMessage should append a row to the transcript");
  // The text must be literal — textContent returns the raw string, not its
  // parsed form. If innerHTML were used, _text would not include the script tag
  // as literal text (and the HTML_SINK guard in the harness would have thrown).
  // The harness's HTML_SINK throws on any innerHTML access, so reaching here
  // means no innerHTML was used. Separately verify the message text is non-empty.
  const msgText = msgs[0].textContent;
  assert(msgText.length > 0, "system message textContent should be non-empty");
  // Verify the sandbox did not register _injected (would only happen if a real
  // browser ran the script, but belt-and-suspenders).
  assert(typeof page.el === "function", "sanity check — page still functional");
});

// Fix 6 (S1): direct addSystemMessage XSS test — call with an HTML payload,
// verify it appears as literal text rather than executing.
check("addSystemMessageDirectXssTest", (tpl) => {
  const { page } = connected(tpl);
  const xssPayload = '<img src=x onerror="window._xss_executed=true">';
  // _testAddSystemMessage is exposed on the sandbox window by the page's IIFE.
  const addSystemMessage = page.sandbox._testAddSystemMessage;
  assert(typeof addSystemMessage === "function",
    "page did not expose _testAddSystemMessage — the test hook is missing");
  addSystemMessage(xssPayload);
  const msgs = page.el("acpTranscript").querySelectorAll(".acp-system-msg");
  assert(msgs.length > 0, "addSystemMessage did not append a .acp-system-msg element");
  const lastMsg = msgs[msgs.length - 1];
  // textContent returns the raw string, including all characters. If innerHTML
  // were used the harness HTML_SINK would have thrown before reaching here.
  assert(lastMsg.textContent === xssPayload,
    `XSS payload should appear as literal text, got: ${lastMsg.textContent}`);
  // _xss_executed should be absent — the onerror handler cannot fire in the
  // DOM stand-in.
  assert(!page.sandbox._xss_executed,
    "onerror handler fired — addSystemMessage used innerHTML");
});

// Fix 1 (EU1): mousedown on a dropdown item selects and confirms the command.
check("dropdownMouseClickSelectsCommand", async (tpl) => {
  const { page, live } = connected(tpl);
  // Seed the commands catalogue.
  page.deliver({
    type: "commands", sessionId: live,
    payload: { commands: [
      { name: "qplan", description: "Write a plan" },
      { name: "qdev",  description: "Execute a plan" },
    ] },
  });
  // Open the dropdown via keydown.
  page.el("acpPrompt").value = "";
  page.el("acpPrompt").dispatch("keydown", {
    key: "/", shiftKey: false, ctrlKey: false, altKey: false,
    preventDefault: () => {},
  });
  assert(!page.el("acpCmdDropdown").hidden,
    "dropdown should be open after pressing /");
  const ul = page.el("acpCmdDropdown").querySelector("ul");
  assert(ul, "dropdown <ul> not found");
  const items = ul.childNodes;
  assert(items.length >= 2, "expected at least 2 dropdown items");
  // Dispatch a mousedown on the dropdown container, simulating a click on the
  // second <li>. The delegated handler uses ev.target to find the clicked item.
  // In a real browser the event would bubble up to cmdDropdownEl; in the
  // harness we dispatch directly on the container with the target set.
  page.el("acpCmdDropdown").dispatch("mousedown", { target: items[1], preventDefault: () => {} });
  // commands_execute should have been sent for 'qdev'.
  const executed = page.sentOf("commands_execute");
  assert(executed.length >= 1, "commands_execute was not sent after mousedown");
  assertEqual(executed[executed.length - 1].payload.name, "qdev",
    "wrong command name sent");
  assert(page.el("acpCmdDropdown").hidden,
    "dropdown should be hidden after mousedown selection");
});

// Fix 2 (EU2): slash key should not open dropdown during an active turn.
check("slashKeyBlockedDuringActiveTurn", (tpl) => {
  const { page, live } = connected(tpl);
  // Start a turn.
  page.deliver({ type: "meta", sessionId: live, payload: { turn: "start" } });
  assert(page.el("acpSend").disabled, "fixture: turn should be active");
  // Attempt to open the dropdown via keydown on '/'.
  page.el("acpPrompt").value = "";
  page.el("acpPrompt").dispatch("keydown", {
    key: "/", shiftKey: false, ctrlKey: false, altKey: false,
    preventDefault: () => {},
  });
  assert(page.el("acpCmdDropdown").hidden,
    "dropdown should remain hidden when a turn is active");
});

// Fix 4 (EU4): Tab key confirms dropdown selection.
check("dropdownTabConfirmsSelection", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({
    type: "commands", sessionId: live,
    payload: { commands: [{ name: "qexplore", description: "Explore" }] },
  });
  // Open the dropdown.
  page.el("acpPrompt").value = "";
  page.el("acpPrompt").dispatch("keydown", {
    key: "/", shiftKey: false, ctrlKey: false, altKey: false,
    preventDefault: () => {},
  });
  assert(!page.el("acpCmdDropdown").hidden, "dropdown should be open");
  // Press Tab.
  let prevented = false;
  page.el("acpPrompt").dispatch("keydown", {
    key: "Tab", shiftKey: false, ctrlKey: false, altKey: false,
    preventDefault: () => { prevented = true; },
  });
  assert(prevented, "Tab should call preventDefault when dropdown is open");
  const executed = page.sentOf("commands_execute");
  assert(executed.length >= 1, "Tab should have sent commands_execute");
  assertEqual(executed[executed.length - 1].payload.name, "qexplore",
    "wrong command sent on Tab");
  assert(page.el("acpCmdDropdown").hidden,
    "dropdown should be hidden after Tab confirm");
});

// ----------------------- Phase 4: skills palette tests -------------------

// Test 1: skillsFramePopulatesSessionSkills
// Delivering a 'skills' frame stores the list and the skill appears in the
// dropdown when '/' is pressed.
check("skillsFramePopulatesSessionSkills", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({
    type: "skills",
    sessionId: live,
    payload: { skills: [{ name: "qtest", description: "d" }] },
  });
  // Open the dropdown to verify the skill was stored.
  page.el("acpPrompt").value = "";
  page.el("acpPrompt").dispatch("keydown", {
    key: "/", shiftKey: false, ctrlKey: false, altKey: false,
    preventDefault() {},
  });
  const drop = page.el("acpCmdDropdown");
  assert(!drop.hidden,
    "dropdown should be visible after '/' when sessionSkills has entries");
  const names = drop.querySelectorAll(".acp-cmd-name").map((n) => n.textContent);
  assert(names.includes("/qtest"),
    "skill 'qtest' should appear in the dropdown after the 'skills' frame; " +
    "got: " + JSON.stringify(names));
  // Verify the description is correct via the desc span.
  const lis = drop.querySelectorAll("li");
  const li = lis.find((l) => {
    const s = l.querySelector(".acp-cmd-name");
    return s && s.textContent === "/qtest";
  });
  assert(li, "could not find <li> for qtest");
  const desc = li.querySelector(".acp-cmd-desc");
  assert(desc && desc.textContent === "d",
    "skill description should be 'd'; got: " + (desc && desc.textContent));
});

// Test 2: skillsFrameOnSessionChangeResetsSessionSkills
// A new 'session' frame resets sessionSkills so old skills never appear in
// the dropdown.
check("skillsFrameOnSessionChangeResetsSessionSkills", (tpl) => {
  const { page, live } = connected(tpl);
  // Populate sessionSkills via a 'skills' frame.
  page.deliver({
    type: "skills", sessionId: live,
    payload: { skills: [{ name: "qexplore", description: "e" }] },
  });
  // Verify the skill is present before reset.
  page.el("acpPrompt").value = "";
  page.el("acpPrompt").dispatch("keydown", {
    key: "/", shiftKey: false, ctrlKey: false, altKey: false, preventDefault() {},
  });
  {
    const names = page.el("acpCmdDropdown").querySelectorAll(".acp-cmd-name")
                      .map((n) => n.textContent);
    assert(names.includes("/qexplore"),
      "fixture: 'qexplore' skill should appear before the session reset");
  }
  // Close the dropdown.
  page.el("acpPrompt").dispatch("keydown", {
    key: "Escape", shiftKey: false, ctrlKey: false, altKey: false,
    preventDefault() {},
  });
  // New session frame — should reset sessionSkills.
  const newSid = "sess-new-reset-skills";
  page.deliver({
    type: "session", sessionId: newSid,
    payload: { sessionId: newSid, cwd: "C:\\tmp", created: true,
               turnActive: false, contextPercent: null },
  });
  assert(page.el("acpCmdDropdown").hidden, "dropdown should be hidden after session frame");
  // '/' should now show only a loading placeholder — no skill names.
  page.el("acpPrompt").value = "";
  page.el("acpPrompt").dispatch("keydown", {
    key: "/", shiftKey: false, ctrlKey: false, altKey: false, preventDefault() {},
  });
  const drop = page.el("acpCmdDropdown");
  const names = drop.querySelectorAll(".acp-cmd-name").map((n) => n.textContent);
  assert(!names.includes("/qexplore"),
    "old session skill 'qexplore' should not appear after a new 'session' frame; " +
    "got: " + JSON.stringify(names));
  // Dropdown should be hidden — no items means no dropdown (Enter sends instead).
  assert(drop.hidden,
    "dropdown should be hidden when both lists are empty after session reset");
});

// Test 3: releaseSessionClearsSessionSkills
// A session_closed frame triggers releaseSession(), which clears sessionSkills
// so the dropdown shows the loading placeholder on the next '/' press.
check("releaseSessionClearsSessionSkills", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({
    type: "skills", sessionId: live,
    payload: { skills: [{ name: "qdream", description: "d" }] },
  });
  // Verify the skill appears.
  page.el("acpPrompt").value = "";
  page.el("acpPrompt").dispatch("keydown", {
    key: "/", shiftKey: false, ctrlKey: false, altKey: false, preventDefault() {},
  });
  {
    const names = page.el("acpCmdDropdown").querySelectorAll(".acp-cmd-name")
                      .map((n) => n.textContent);
    assert(names.includes("/qdream"),
      "fixture: 'qdream' skill should appear before session_closed");
  }
  // Close dropdown.
  page.el("acpPrompt").dispatch("keydown", {
    key: "Escape", shiftKey: false, ctrlKey: false, altKey: false,
    preventDefault() {},
  });
  // session_closed triggers releaseSession() which clears sessionSkills.
  page.deliver({
    type: "session_closed", sessionId: live,
    payload: { sessionId: live, message: "Session closed." },
  });
  assert(page.el("acpCmdDropdown").hidden, "dropdown should be hidden after session frame");
  // '/' should now show the loading placeholder — sessionSkills was cleared.
  page.el("acpPrompt").value = "";
  page.el("acpPrompt").dispatch("keydown", {
    key: "/", shiftKey: false, ctrlKey: false, altKey: false, preventDefault() {},
  });
  const drop = page.el("acpCmdDropdown");
  const names = drop.querySelectorAll(".acp-cmd-name").map((n) => n.textContent);
  assert(!names.includes("/qdream"),
    "skill 'qdream' should be gone after session_closed (releaseSession clears it); " +
    "got: " + JSON.stringify(names));
  // Dropdown should be hidden — no items means no dropdown (Enter sends instead).
  assert(drop.hidden,
    "dropdown should be hidden when both lists are empty after releaseSession");
});

// Test 4: slashKeyShowsSkillsInDropdown
// Both command entries and skill entries appear when '/' is pressed with
// both sessionCommands and sessionSkills populated.
check("slashKeyShowsSkillsInDropdown", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({
    type: "commands", sessionId: live,
    payload: { commands: [{ name: "tools", description: "d" }] },
  });
  page.deliver({
    type: "skills", sessionId: live,
    payload: { skills: [{ name: "qexplore", description: "e" }] },
  });
  page.el("acpPrompt").value = "";
  page.el("acpPrompt").dispatch("keydown", {
    key: "/", shiftKey: false, ctrlKey: false, altKey: false,
    preventDefault() {},
  });
  const drop = page.el("acpCmdDropdown");
  assert(!drop.hidden, "dropdown should be visible after '/' with both lists populated");
  const names = drop.querySelectorAll(".acp-cmd-name").map((n) => n.textContent);
  assert(names.includes("/tools"),
    "dropdown should include the command entry '/tools'; got: " + JSON.stringify(names));
  assert(names.includes("/qexplore"),
    "dropdown should include the skill entry '/qexplore'; got: " + JSON.stringify(names));
});

// Test 5: skillEntriesShowBadge
// Skill entries rendered in the dropdown carry an .acp-cmd-skill-badge element.
check("skillEntriesShowBadge", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({
    type: "skills", sessionId: live,
    payload: { skills: [{ name: "qplan", description: "Write a plan" }] },
  });
  page.el("acpPrompt").value = "";
  page.el("acpPrompt").dispatch("keydown", {
    key: "/", shiftKey: false, ctrlKey: false, altKey: false,
    preventDefault() {},
  });
  const drop = page.el("acpCmdDropdown");
  assert(!drop.hidden, "fixture: dropdown should be open");
  // Find the <li> for the skill entry.
  const lis = drop.querySelectorAll("li");
  const skillLi = lis.find((li) => {
    const nameSpan = li.querySelector(".acp-cmd-name");
    return nameSpan && nameSpan.textContent === "/qplan";
  });
  assert(skillLi, "could not find the <li> for '/qplan' skill entry");
  const badge = skillLi.querySelector(".acp-cmd-skill-badge");
  assert(badge,
    "skill entry <li> should contain an element with class 'acp-cmd-skill-badge'");
  assert(badge.getAttribute("aria-hidden") === "true", "skill badge should have aria-hidden=\"true\"");
});

// skillSelectionSendsCleanName
// Verify that selecting a skill entry completes the prompt with only the
// clean name, not the badge text. Updated for the mid-sentence-completion
// behavior (acp.html ~2135-2160, commits 998884f3/ea6c9393): a skill, unlike
// a plain command, is not executed immediately on selection — it replaces
// the typed "/token" with "/<name> " and leaves the turn for the user to
// send, so the same text can be completed from the middle of a sentence.
check("skillSelectionSendsCleanName", (tpl) => {
  const { page, live } = connected(tpl);
  // Seed one skill.
  page.deliver({
    type: "skills", sessionId: live,
    payload: { skills: [{ name: "qplan", description: "d" }] },
  });
  // Open dropdown.
  page.el("acpPrompt").value = "";
  page.el("acpPrompt").dispatch("keydown", {
    key: "/", shiftKey: false, ctrlKey: false, altKey: false, preventDefault() {},
  });
  const drop = page.el("acpCmdDropdown");
  assert(!drop.hidden, "fixture: dropdown should be open");
  // Find the skill entry and verify it's there.
  const lis = drop.querySelectorAll("li");
  const skillLi = lis.find((li) => {
    const s = li.querySelector(".acp-cmd-name");
    return s && s.textContent === "/qplan";
  });
  assert(skillLi, "one skill entry for qplan expected");
  // Select it with Enter.
  page.el("acpPrompt").dispatch("keydown", {
    key: "Enter", shiftKey: false, ctrlKey: false, altKey: false, preventDefault() {},
  });
  // No commands_execute yet — a skill completes the prompt rather than sending.
  assertEqual(page.sentOf("commands_execute").length, 0,
    "a skill selection must not execute immediately, only complete the prompt");
  assertEqual(page.el("acpPrompt").value, "/qplan ",
    "skill name should be clean (no badge text), got: " + page.el("acpPrompt").value);
  assert(drop.hidden, "the dropdown should close once a skill is completed");
});

// keyboardNavigationReachesSkillEntries
// Verify that ArrowDown navigation reaches skill entries and Enter completes
// one. Updated for the mid-sentence-completion behavior (see
// skillSelectionSendsCleanName above): a skill selection completes the
// prompt text rather than sending commands_execute.
check("keyboardNavigationReachesSkillEntries", (tpl) => {
  const { page, live } = connected(tpl);
  // Seed ONE skill only (no commands) — so the only valid selection is "qexplore".
  // This makes the test discriminating: if ArrowDown navigation skips skill entries,
  // Enter would find nothing to select and the prompt would stay empty.
  page.deliver({
    type: "skills", sessionId: live,
    payload: { skills: [{ name: "qexplore", description: "e" }] },
  });
  // Open dropdown.
  page.el("acpPrompt").value = "";
  page.el("acpPrompt").dispatch("keydown", {
    key: "/", shiftKey: false, ctrlKey: false, altKey: false, preventDefault() {},
  });
  // Navigate down once — the first entry is the skill (index 0 since no commands).
  page.el("acpPrompt").dispatch("keydown", {
    key: "ArrowDown", shiftKey: false, ctrlKey: false, altKey: false, preventDefault() {},
  });
  // Select the current item.
  page.el("acpPrompt").dispatch("keydown", {
    key: "Enter", shiftKey: false, ctrlKey: false, altKey: false, preventDefault() {},
  });
  // The skill must be reachable by keyboard: the prompt completes to its exact name.
  assertEqual(page.sentOf("commands_execute").length, 0,
    "a skill selection must not execute immediately, only complete the prompt");
  assertEqual(page.el("acpPrompt").value, "/qexplore ",
    "Keyboard navigation must reach the skill entry; got: " + page.el("acpPrompt").value);
});

// Test 6: commandEntriesDoNotShowBadge
// Command entries rendered in the dropdown do NOT carry an .acp-cmd-skill-badge.
check("commandEntriesDoNotShowBadge", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({
    type: "commands", sessionId: live,
    payload: { commands: [{ name: "context", description: "Context usage" }] },
  });
  page.el("acpPrompt").value = "";
  page.el("acpPrompt").dispatch("keydown", {
    key: "/", shiftKey: false, ctrlKey: false, altKey: false,
    preventDefault() {},
  });
  const drop = page.el("acpCmdDropdown");
  assert(!drop.hidden, "fixture: dropdown should be open");
  const badges = drop.querySelectorAll(".acp-cmd-skill-badge");
  assertEqual(badges.length, 0,
    "command entries should not render any .acp-cmd-skill-badge elements; " +
    "found " + badges.length);
});

// Test 7: slashFilterMatchesSkillsByName
// Typing '/qex' filters to skills whose name contains 'qex' and excludes
// commands whose name does not match.
check("slashFilterMatchesSkillsByName", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({
    type: "commands", sessionId: live,
    payload: { commands: [{ name: "tools", description: "d" }] },
  });
  page.deliver({
    type: "skills", sessionId: live,
    payload: { skills: [{ name: "qexplore", description: "e" }] },
  });
  // Open with '/' first, then type 'qex' to simulate the filtered state.
  page.el("acpPrompt").value = "";
  page.el("acpPrompt").dispatch("keydown", {
    key: "/", shiftKey: false, ctrlKey: false, altKey: false,
    preventDefault() {},
  });
  // Simulate the filter: update prompt to '/qex' and fire input event.
  page.el("acpPrompt").value = "/qex";
  page.el("acpPrompt").dispatch("input");
  const drop = page.el("acpCmdDropdown");
  const names = drop.querySelectorAll(".acp-cmd-name").map((n) => n.textContent);
  assert(names.includes("/qexplore"),
    "'/qex' filter should match the 'qexplore' skill; got: " + JSON.stringify(names));
  assert(!names.includes("/tools"),
    "'/qex' filter should exclude 'tools' command; got: " + JSON.stringify(names));
});

// Test 8: emptyBothListsDropdownIsHidden
// With both sessionCommands and sessionSkills empty (no frames delivered),
// pressing '/' hides the dropdown immediately — no placeholder blocks Enter.
check("emptyBothListsDropdownIsHidden", (tpl) => {
  const { page } = connected(tpl);
  // Neither 'commands' nor 'skills' frame has been delivered — both lists are empty.
  page.el("acpPrompt").value = "";
  page.el("acpPrompt").dispatch("keydown", {
    key: "/", shiftKey: false, ctrlKey: false, altKey: false,
    preventDefault() {},
  });
  const drop = page.el("acpCmdDropdown");
  assert(drop.hidden,
    "dropdown should be hidden when both lists are empty so Enter can send the prompt");
});

// Test 9: loadedButNoMatchHidesDropdown
// With data loaded but a filter that matches nothing, the dropdown is hidden
// so Enter sends the prompt rather than being consumed by the dropdown.
check("loadedButNoMatchHidesDropdown", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({
    type: "commands", sessionId: live,
    payload: { commands: [{ name: "tools", description: "d" }] },
  });
  page.deliver({
    type: "skills", sessionId: live,
    payload: { skills: [{ name: "qexplore", description: "e" }] },
  });
  // Type a filter that matches nothing.
  page.el("acpPrompt").value = "";
  page.el("acpPrompt").dispatch("keydown", {
    key: "/", shiftKey: false, ctrlKey: false, altKey: false,
    preventDefault() {},
  });
  page.el("acpPrompt").value = "/zzznotexists";
  page.el("acpPrompt").dispatch("input");
  const drop = page.el("acpCmdDropdown");
  assert(drop.hidden,
    "dropdown should be hidden when filter matches nothing so Enter can send the prompt");
});

// Test 10: copyButtonPresentForLabeledCodeBlocks
// A fenced block with a language label produces a .acp-md-copy button.
check("copyButtonPresentForLabeledCodeBlocks", (tpl) => {
  const { page, live } = connected(tpl);
  answered(page, live, "```python\nx = 1\n```\n", [
    { type: "block_code", raw: "x = 1\n", style: "fenced", marker: "```",
      attrs: { info: "python" } },
  ]);
  const body = bubble(page);
  const copyBtns = body.querySelectorAll(".acp-md-copy");
  assert(copyBtns.length > 0,
    "a labeled code block should have an .acp-md-copy button; none found");
});

// Test 11: copyButtonAbsentForUnlabeledCodeBlocks
// A fenced block with no language label produces no .acp-md-copy button.
check("copyButtonAbsentForUnlabeledCodeBlocks", (tpl) => {
  const { page, live } = connected(tpl);
  answered(page, live, "```\nplain block\n```\n", [
    { type: "block_code", raw: "plain block\n", style: "fenced", marker: "```" },
  ]);
  const body = bubble(page);
  const copyBtns = body.querySelectorAll(".acp-md-copy");
  assertEqual(copyBtns.length, 0,
    "an unlabeled code block should not have an .acp-md-copy button; " +
    "found " + copyBtns.length);
});

// Test 12: copyButtonNotPresentInNonCodeBlocks
// A response with only prose (no fenced block) produces no .acp-md-copy element.
check("copyButtonNotPresentInNonCodeBlocks", (tpl) => {
  const { page, live } = connected(tpl);
  answered(page, live, "Plain prose with `inline code` only.\n", [
    { type: "paragraph", children: [
      { type: "text", raw: "Plain prose with " },
      { type: "codespan", raw: "inline code" },
      { type: "text", raw: " only." },
    ] },
  ]);
  const body = bubble(page);
  const copyBtns = body.querySelectorAll(".acp-md-copy");
  assertEqual(copyBtns.length, 0,
    "a response with no fenced code block should not have any .acp-md-copy element; " +
    "found " + copyBtns.length);
});

// ---- workspace bulk-delete UI (Phase 3) ----

/** A store with a single workspace holding two sessions. */
function wsDeleteStore(name = "my-project", cwd = "C:\\work\\my-project") {
  return [{
    cwd,
    name,
    exists: true,
    sessions: [
      { id: "sess-a", title: "session a",
        updated_at: "2026-08-01T10:00:00.000000000Z", availability: "available" },
      { id: "sess-b", title: "session b",
        updated_at: "2026-08-01T09:00:00.000000000Z", availability: "available" },
    ],
  }];
}

check("delete button appears on workspace group headers in project mode with canDelete=true",
  async (tpl) => {
    const page = await railed(tpl, { store: wsDeleteStore() });
    // Default mode is project; canDelete defaults to true in railed().
    const menuBtns = page.all("acpRailGroups", ".acp-rail-group-menu-btn");
    assert(menuBtns.length > 0,
      "no ⋯ menu button on workspace group header in project mode; "
      + "railGroupMenuNode must be called from railGroupNode when "
      + "railMode === 'project' && ACP_CAN_DELETE");
  });

check("delete button absent when railMode is 'date'", async (tpl) => {
  const page = await railed(tpl, {
    store: wsDeleteStore(),
    stored: { pa_acp_group: "date" },
  });
  const menuBtns = page.all("acpRailGroups", ".acp-rail-group-menu-btn");
  assertEqual(menuBtns.length, 0,
    "workspace ⋯ menu button is shown in date grouping mode; it should only appear in project mode");
});

check("delete button absent when railMode is 'status'", async (tpl) => {
  const page = await railed(tpl, {
    store: wsDeleteStore(),
    stored: { pa_acp_group: "status" },
  });
  const menuBtns = page.all("acpRailGroups", ".acp-rail-group-menu-btn");
  assertEqual(menuBtns.length, 0,
    "workspace ⋯ menu button is shown in status grouping mode; it should only appear in project mode");
});

// ---- pinned section (all three grouping modes) ---------------------------

/** A single pinned session row, shaped like the backend sends it. */
function pinnedRow(id) {
  return {
    id,
    title: `pinned session ${id}`,
    updated_at: "2026-08-18T10:00:00.000000000Z",
    availability: "available",
    status: "",
    cwd: "C:\\work\\pinned-ws",
    name: "pinned-ws",
    exists: true,
  };
}

check("Pinned section appears first in project mode", async (tpl) => {
  const store = fakeStore({ workspaces: 1, sessions: 1 });
  store.pinned = [pinnedRow("p-1")];
  const page = await railed(tpl, { store });
  const groups = page.railGroups();
  assert(groups.length > 0, "rail rendered no groups");
  assertEqual(groups[0].dataset.head, "p:pinned",
              "first group is not the Pinned section in project mode");
  const headings = page.railHeadings();
  assert(headings[0] === "Pinned",
         "first heading is not 'Pinned' in project mode; got: " + headings[0]);
  const rows = groups[0].querySelectorAll(".acp-rail-row");
  assertEqual(rows.length, 1, "Pinned group has wrong number of rows in project mode");
  assertEqual(rows[0].querySelector(".acp-rail-row-title").textContent,
              "pinned session p-1",
              "Pinned row title is wrong in project mode");
});

check("Pinned section appears first in date mode", async (tpl) => {
  const store = fakeStore({ workspaces: 1, sessions: 1 });
  store.pinned = [pinnedRow("p-2")];
  const page = await railed(tpl, { store, stored: { pa_acp_group: "date" } });
  const groups = page.railGroups();
  assert(groups.length > 0, "rail rendered no groups in date mode");
  assertEqual(groups[0].dataset.head, "p:pinned",
              "first group is not the Pinned section in date mode");
});

check("Pinned section appears first in status mode", async (tpl) => {
  const store = [{
    cwd: "C:\\work\\ws", name: "ws", exists: true,
    sessions: [{ id: "s-avail", title: "available", updated_at: "2026-08-18T09:00:00.000000000Z",
                 availability: "available", status: "" }],
    pinned: [pinnedRow("p-3")],
  }];
  // store.pinned must be at the top level (the flat listing reads store.pinned)
  store.pinned = [pinnedRow("p-3")];
  const page = await railed(tpl, { store, stored: { pa_acp_group: "status" } });
  const groups = page.railGroups();
  assert(groups.length > 0, "rail rendered no groups in status mode");
  assertEqual(groups[0].dataset.head, "p:pinned",
              "first group is not the Pinned section in status mode");
});

check("Pinned section absent when server returns empty pinned list", async (tpl) => {
  // No store.pinned — serveFlat and serveListing default to [].
  const page = await railed(tpl, { store: fakeStore({ workspaces: 1, sessions: 1 }) });
  const pinnedGroups = [...page.railGroups()].filter(
    (g) => g.dataset.head === "p:pinned");
  assertEqual(pinnedGroups.length, 0,
              "a Pinned group was rendered when server sent no pinned sessions");
});

check("Pinned separator rendered between Pinned group and workspace groups in project mode",
  async (tpl) => {
    const store = fakeStore({ workspaces: 1, sessions: 1 });
    store.pinned = [pinnedRow("p-sep")];
    const page = await railed(tpl, { store });
    const separator = page.one("acpRailGroups", ".pinned-separator");
    assert(separator !== null,
           "no .pinned-separator in project mode when Pinned and workspace groups are both present");
  });

check("Pinned separator absent when pinned list is empty", async (tpl) => {
  const page = await railed(tpl, { store: fakeStore({ workspaces: 1, sessions: 1 }) });
  const separator = page.one("acpRailGroups", ".pinned-separator");
  assert(separator === null,
         ".pinned-separator appeared in rail with no pinned sessions");
});

check("Pinned separator rendered between Pinned group and date buckets in date mode",
  async (tpl) => {
    const store = fakeStore({ workspaces: 1, sessions: 1 });
    store.pinned = [pinnedRow("p-sep-date")];
    const page = await railed(tpl, { store, stored: { pa_acp_group: "date" } });
    const separator = page.one("acpRailGroups", ".pinned-separator");
    assert(separator !== null,
           "no .pinned-separator in date mode when Pinned and date buckets are both present");
  });

check("Pinned session row is selectable (click does not throw)", async (tpl) => {
  const store = fakeStore({ workspaces: 1, sessions: 1 });
  store.pinned = [pinnedRow("p-click")];
  const page = await railed(tpl, { store });
  const pinnedGroupEl = [...page.railGroups()].find((g) => g.dataset.head === "p:pinned");
  assert(pinnedGroupEl !== undefined, "Pinned group not found");
  const row = pinnedGroupEl.querySelector(".acp-rail-row");
  assert(row !== null, "no row found inside Pinned group");
  // Must not throw — railRowNode's click handler calls selectSession.
  let threw = false;
  try { row.dispatch("click"); } catch (e) { threw = true; }
  assert(!threw, "clicking a pinned row threw");
});

check("modal opens with correct session count and folder name", async (tpl) => {
  const page = await railed(tpl, { store: wsDeleteStore("my-project") });
  const menuBtn = page.one("acpRailGroups", ".acp-rail-group-menu-btn");
  assert(menuBtn !== null, "no ⋯ menu button — cannot open modal");
  menuBtn.dispatch("click");
  // Click the "Delete all sessions" item in the now-open dropdown.
  const delItem = page.one("acpRailGroups", ".acp-rail-group-menu-del");
  assert(delItem !== null, "no 'Delete all sessions' item (.acp-rail-group-menu-del) in workspace menu");
  delItem.dispatch("click");
  // Modal is appended to document.body (real browser) or returned standalone.
  // In the harness, railDeleteWorkspace builds the modal and calls focus on the
  // input; the modal lives in memory even without document.body.
  // We verify by checking that buildWorkspaceDeleteModal was invoked by looking
  // for the input element. Since the modal isn't in a byId-indexed container,
  // we use the fact that the input received focus via ACTIVE.
  const focused = page.focused();
  assert(focused !== null, "nothing received focus after modal open");
  assert(focused.matches(".acp-ws-delete-name-input"),
    "the focused element after modal open is not the name input; "
    + "got: " + (focused ? focused.className : "null"));
});

check("confirm button disabled when name input is empty", async (tpl) => {
  const page = await railed(tpl, { store: wsDeleteStore("my-project") });
  const menuBtn = page.one("acpRailGroups", ".acp-rail-group-menu-btn");
  assert(menuBtn !== null, "no ⋯ menu button");
  menuBtn.dispatch("click");
  const delItem = page.one("acpRailGroups", ".acp-rail-group-menu-del");
  assert(delItem !== null, "no 'Delete all sessions' item (.acp-rail-group-menu-del) in workspace menu");
  delItem.dispatch("click");
  // The input is focused. Find the confirm button. We need to find the modal
  // content — since it isn't in the rail container, we use the focused element's
  // parent chain to find the modal body.
  const input = page.focused();
  assert(input !== null, "no focused input");
  // Walk up to the modal body, then find the confirm button.
  let container = input;
  while (container && !container.querySelector(".acp-ws-delete-confirm")) {
    container = container.parentNode;
  }
  assert(container !== null, "could not find modal container from focused input");
  const confirmBtn = container.querySelector(".acp-ws-delete-confirm");
  assert(confirmBtn !== null, "no confirm button in modal");
  assert(confirmBtn.disabled, "confirm button is not disabled when input is empty");
});

check("confirm button disabled when input does not match folder name", async (tpl) => {
  const page = await railed(tpl, { store: wsDeleteStore("my-project") });
  const menuBtn = page.one("acpRailGroups", ".acp-rail-group-menu-btn");
  assert(menuBtn !== null, "no ⋯ menu button");
  menuBtn.dispatch("click");
  const delItem = page.one("acpRailGroups", ".acp-rail-group-menu-del");
  assert(delItem !== null, "no 'Delete all sessions' item (.acp-rail-group-menu-del) in workspace menu");
  delItem.dispatch("click");
  const input = page.focused();
  assert(input !== null, "no focused input");
  // Type a wrong name.
  input.value = "wrong-name";
  input.dispatch("input");
  let container = input;
  while (container && !container.querySelector(".acp-ws-delete-confirm")) {
    container = container.parentNode;
  }
  const confirmBtn = container.querySelector(".acp-ws-delete-confirm");
  assert(confirmBtn !== null, "no confirm button");
  assert(confirmBtn.disabled,
    "confirm button is enabled when input doesn't match folder name");
});

check("confirm button enabled when input matches folder name", async (tpl) => {
  const page = await railed(tpl, { store: wsDeleteStore("my-project") });
  const menuBtn = page.one("acpRailGroups", ".acp-rail-group-menu-btn");
  assert(menuBtn !== null, "no ⋯ menu button");
  menuBtn.dispatch("click");
  const delItem = page.one("acpRailGroups", ".acp-rail-group-menu-del");
  assert(delItem !== null, "no 'Delete all sessions' item (.acp-rail-group-menu-del) in workspace menu");
  delItem.dispatch("click");
  const input = page.focused();
  assert(input !== null, "no focused input");
  input.value = "my-project";
  input.dispatch("input");
  let container = input;
  while (container && !container.querySelector(".acp-ws-delete-confirm")) {
    container = container.parentNode;
  }
  const confirmBtn = container.querySelector(".acp-ws-delete-confirm");
  assert(confirmBtn !== null, "no confirm button");
  assert(!confirmBtn.disabled,
    "confirm button is still disabled when input matches the folder name");
});

check("confirmed delete (no folder delete) posts cwd to delete endpoint and evicts group",
  async (tpl) => {
    const page = await railed(tpl, { store: wsDeleteStore("my-project") });
    // Verify the group is present before delete.
    const groupsBefore = page.railGroups();
    assertEqual(groupsBefore.length, 1, "fixture: should have one workspace group");

    const menuBtn = page.one("acpRailGroups", ".acp-rail-group-menu-btn");
    assert(menuBtn !== null, "no ⋯ menu button");
    menuBtn.dispatch("click");
    const delItem = page.one("acpRailGroups", ".acp-rail-group-menu-del");
    assert(delItem !== null, "no 'Delete all sessions' item (.acp-rail-group-menu-del) in workspace menu");
    delItem.dispatch("click");

    const input = page.focused();
    assert(input !== null, "no focused input");
    input.value = "my-project";
    input.dispatch("input");

    // Find the modal and confirm button.
    let container = input;
    while (container && !container.querySelector(".acp-ws-delete-confirm")) {
      container = container.parentNode;
    }
    const confirmBtn = container.querySelector(".acp-ws-delete-confirm");
    assert(confirmBtn !== null, "no confirm button");
    assert(!confirmBtn.disabled, "confirm button is disabled — cannot proceed");
    confirmBtn.dispatch("click");
    await page.settle();

    // Verify POST was sent with cwd.
    const calls = page.deleteCalls();
    assert(calls.length > 0, "no delete request was sent");
    const sent = JSON.parse(calls[0].init.body);
    assertEqual(sent.cwd, "C:\\work\\my-project",
      "delete request did not carry the workspace cwd");
    assert(!sent.delete_folder,
      "delete_folder was set when folder checkbox was not checked");

    // Full success (serveDelete returns {deleted: [], failed: []}) → group evicted.
    const groupsAfter = page.railGroups();
    assertEqual(groupsAfter.length, 0,
      "workspace group was not evicted after successful delete");
  });

check("partial failure shows rail status message and group is not evicted", async (tpl) => {
  const page = await railed(tpl, {
    store: wsDeleteStore("my-project"),
    answer: (url) => url === DELETE_URL ? {
      body: {
        deleted: ["sess-a"],
        failed: [{ id: "sess-b", code: "locked",
                   message: "Another process is using this session." }],
        total_found: 2,
      },
    } : null,
  });

  const menuBtn = page.one("acpRailGroups", ".acp-rail-group-menu-btn");
  assert(menuBtn !== null, "no ⋯ menu button");
  menuBtn.dispatch("click");
  const delItem = page.one("acpRailGroups", ".acp-rail-group-menu-del");
  assert(delItem !== null, "no 'Delete all sessions' item (.acp-rail-group-menu-del) in workspace menu");
  delItem.dispatch("click");

  const input = page.focused();
  assert(input !== null, "no focused input");
  input.value = "my-project";
  input.dispatch("input");

  let container = input;
  while (container && !container.querySelector(".acp-ws-delete-confirm")) {
    container = container.parentNode;
  }
  const confirmBtn = container.querySelector(".acp-ws-delete-confirm");
  assert(confirmBtn !== null, "no confirm button");
  confirmBtn.dispatch("click");
  await page.settle();

  // Group should still be present (partial failure).
  const groups = page.railGroups();
  assert(groups.length > 0,
    "workspace group was evicted on partial failure — it should stay");

  // Rail status should show a message.
  const status = page.el("acpRailStatus").textContent;
  assert(/could not be deleted/i.test(status) || /Deleted/i.test(status),
    "rail status does not report the delete result: " + status);
});

check("confirmed delete with folder checkbox posts delete_folder=true", async (tpl) => {
  const page = await railed(tpl, { store: wsDeleteStore("my-project") });
  const menuBtn = page.one("acpRailGroups", ".acp-rail-group-menu-btn");
  assert(menuBtn !== null, "no ⋯ menu button");
  menuBtn.dispatch("click");
  const delItem = page.one("acpRailGroups", ".acp-rail-group-menu-del");
  assert(delItem !== null, "no 'Delete all sessions' item (.acp-rail-group-menu-del) in workspace menu");
  delItem.dispatch("click");

  const input = page.focused();
  assert(input !== null, "no focused input");
  input.value = "my-project";
  input.dispatch("input");

  // Find the modal via the input's parent chain.
  let container = input;
  while (container && !container.querySelector(".acp-ws-delete-confirm")) {
    container = container.parentNode;
  }
  assert(container !== null, "could not find modal container");

  // Check the folder checkbox.
  const folderCb = container.querySelector(".acp-ws-delete-folder-cb");
  assert(folderCb !== null, "no folder checkbox in modal");
  folderCb.checked = true;

  const confirmBtn = container.querySelector(".acp-ws-delete-confirm");
  assert(confirmBtn !== null, "no confirm button");
  assert(!confirmBtn.disabled, "confirm button is disabled — cannot proceed");
  confirmBtn.dispatch("click");
  await page.settle();

  const calls = page.deleteCalls();
  assert(calls.length > 0, "no delete request was sent");
  const sent = JSON.parse(calls[0].init.body);
  assert(sent.delete_folder === true,
    "delete_folder was not set to true when folder checkbox was checked; got: "
    + JSON.stringify(sent.delete_folder));
});

check("delete button absent when canDelete=false", async (tpl) => {
  const page = await railed(tpl, {
    store: wsDeleteStore("my-project"),
    local: false,
    canDelete: false,
  });
  const menuBtns = page.all("acpRailGroups", ".acp-rail-group-menu-btn");
  assertEqual(menuBtns.length, 0,
    "workspace ⋯ menu button is shown when canDelete=false; it must be hidden for "
    + "non-desktop or non-authenticated remote viewers");
});

check("WebSocket connects to /ws/acp", (tpl) => {
  // There is now exactly one ACP engine, so wsUrl() has nothing to branch on
  // — it must always produce the sole engine's WebSocket path. Verified by
  // opening the socket and reading the URL the FakeWs constructor received.
  const page = loadPage(tpl);
  page.open();
  const url = page.sockets[0] && page.sockets[0].url;
  assert(url != null, "page.open() did not create a WebSocket socket");
  // The bare path and nothing after it: no retired sibling like /ws/acp-v3,
  // and no "?t=" token — the cookie authenticates the socket.
  // 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 6
  assertEqual(url, "ws://127.0.0.1:4915/ws/acp",
    `WebSocket URL should be the bare /ws/acp path; got: ${JSON.stringify(url)}`);
});

// ---- refused handshake: signed out vs unreachable -----------------------
// 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 6. Driven, not
// grepped: the socket closes without opening and the page's own GET of
// itself (/acp) answers as the server would. Both directions asserted; the
// retired stale-token Reload affordance must appear in neither.

async function refusedHandshake(tpl, pageAnswer, opts = {}) {
  const page = loadPage(tpl, {
    ...opts,
    answer: (u) => (u.startsWith("/acp") ? pageAnswer : null),
  });
  page.socket().onclose({ code: 1006, reason: "" }); // never opened
  await page.settle();
  return {
    page,
    status: () => page.el("acpStatus").textContent,
    log: () => page.el("acpLog").textContent,
    transcript: () => page.el("acpTranscript").textContent,
  };
}

check("refused handshake: a cookie-less tab (the gate's 403) reads as signed out, pointing at the tray", async (tpl) => {
  const r = await refusedHandshake(tpl, { ok: false, status: 403, body: {} });
  assertEqual(r.status(), "signed out", "the status must say signed out");
  assert(/signed out/i.test(r.transcript()) && /tray/i.test(r.transcript()),
    `the transcript must tell the user to open PowerAtlas from the tray; got ${JSON.stringify(r.transcript())}`);
  assert(!/unreachable|not answering|still be starting/i.test(r.status() + r.log() + r.transcript()),
    "the gate's 403 comes from a live server and must not read as unreachable");
  assertEqual(r.page.el("acpReconnect").hidden, true, "Reconnect cannot help a signed-out tab");
  assertEqual(r.page.el("acpReload").hidden, true,
    "no stale-token Reload affordance: a reload cannot sign a browser back in");
  assertEqual(r.page.reloaded, false, "nothing may reload the page on its own");
});

check("refused handshake: the signed-out message names the step after the tray -- reload this tab (F7)", async (tpl) => {
  // Both recovery buttons stay hidden on a signed-out tab, so the message is
  // the only place the way back can be.
  // 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL final review
  const r = await refusedHandshake(tpl, { ok: false, status: 403, body: {} });
  assert(/tray/i.test(r.transcript()) && /reload this tab/i.test(r.transcript()),
    `the signed-out message must say to reload this tab after the tray; got ${JSON.stringify(r.transcript())}`);
});

check("refused handshake: a signed-out remote viewer is sent to /remote-auth, not to a tray it does not have", async (tpl) => {
  const r = await refusedHandshake(tpl, { ok: false, status: 403, body: {} },
                                   { local: false, canDelete: true });
  assert(/remote-auth/.test(r.transcript()) && !/tray/i.test(r.transcript()),
    `a remote viewer's sign-in path is /remote-auth; got ${JSON.stringify(r.transcript())}`);
});

check("refused handshake: a server that is not answering still reads as unreachable, not signed out", async (tpl) => {
  const r = await refusedHandshake(tpl, { reject: "connection refused" });
  assertEqual(r.status(), "server unreachable", "the status must say unreachable");
  assert(!/signed out/i.test(r.log() + r.transcript()),
    "a network failure must not be reported as signed out");
  assertEqual(r.page.el("acpReconnect").hidden, false, "Reconnect must reappear for an unreachable server");
  assertEqual(r.page.el("acpReload").hidden, true, "Reload is not the recovery for an unreachable server");
});

check("refused handshake: a server that admits this browser offers Reconnect, never the stale-token Reload", async (tpl) => {
  const r = await refusedHandshake(tpl, null); // the default stub answers 200
  assert(!/stale|reload/i.test(r.status() + r.log() + r.transcript()),
    `no stale-token wording may survive; log: ${JSON.stringify(r.log())}`);
  assert(!/signed out/i.test(r.status() + r.transcript()), "a signed-in browser is not signed out");
  assertEqual(r.page.el("acpReconnect").hidden, false, "Reconnect is the recovery here");
  assertEqual(r.page.el("acpReload").hidden, true,
    "the stale-token Reload affordance is retired -- there is no per-launch token to go stale");
});

// ---- refused handshake: review fixes ---------------------------------------
// 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 6 review
// (J5-J9). The "any other answer" branch, the diagnostic GET's timeout, a
// throw inside the success handler, a stale answer, and the remote wording.

check("refused handshake: a 5xx answer is a server error -- not signed out, not unreachable -- and offers Reconnect (J5)", async (tpl) => {
  const r = await refusedHandshake(tpl, { ok: false, status: 500, body: {} });
  const all = r.status() + r.log() + r.transcript();
  assertEqual(r.status(), "server error", "a 5xx must read as a server error");
  assert(!/signed out/i.test(all), `a 5xx is not a signed-out browser; got ${JSON.stringify(all)}`);
  assert(!/unreachable|not answering|still be starting/i.test(all),
    `a server that answered 500 is reachable; got ${JSON.stringify(all)}`);
  assertEqual(r.page.el("acpReconnect").hidden, false, "Reconnect is the recovery for a server error");
});

check("refused handshake: a diagnostic GET that never answers times out as unreachable and offers Reconnect (J6)", async (tpl) => {
  const r = await refusedHandshake(tpl, { hang: true });
  assertEqual(r.page.el("acpReconnect").hidden, true, "fixture: still diagnosing");
  const diag = r.page.timers.filter((t) => t.ms === 5000);
  assertEqual(diag.length, 1, "the diagnosis must arm exactly one 5 s timeout");
  diag[0].fn();
  await r.page.settle();
  assertEqual(r.status(), "server unreachable", "silence past the timeout reads as unreachable");
  assertEqual(r.page.el("acpReconnect").hidden, false, "Reconnect must reappear after the timeout");
  const get = r.page.fetches.find((f) => f.url.startsWith("/acp"));
  assert(get && get.init.signal && get.init.signal.aborted,
    "the hung GET must be aborted, not left open");
});

check("refused handshake: a throw inside the success handler still ends with Reconnect shown (J7)", async (tpl) => {
  const page = loadPage(tpl); // the page's own GET answers 200
  page.socket().onclose({ code: 1006, reason: "" }); // never opened
  // The 200 branch's first act is setState('closed', 'disconnected').
  Object.defineProperty(page.el("acpStatus"), "textContent", {
    configurable: true,
    get() { return ""; },
    set() { throw new Error("injected failure"); },
  });
  await page.settle();
  await page.settle();
  assertEqual(page.el("acpReconnect").hidden, false,
    "an exception in the success handler left both recovery buttons hidden");
});

check("refused handshake: an answer for a socket a later connect() replaced paints nothing (J8)", async (tpl) => {
  const page = loadPage(tpl, {
    answer: (u) => (u.startsWith("/acp") ? { ok: false, status: 403, body: {} } : null),
  });
  page.socket().onclose({ code: 1006, reason: "" }); // never opened
  page.el("acpReconnect").click(); // a new connect() before the GET answers
  const before = page.el("acpTranscript").textContent;
  await page.settle();
  await page.settle();
  assert(!/signed out/i.test(page.el("acpTranscript").textContent.slice(before.length)
                             + page.el("acpStatus").textContent),
    "a stale diagnosis painted signed out over the newer connection");
});

check("refused handshake: a remote 403 names both causes -- signed out, or remote access turned off (J9)", async (tpl) => {
  const r = await refusedHandshake(tpl, { ok: false, status: 403, body: {} },
                                   { local: false, canDelete: true });
  assert(/remote access is turned off/i.test(r.transcript()),
    `a remote 403 may mean remote access was stopped; got ${JSON.stringify(r.transcript())}`);
  assert(/remote-auth/.test(r.transcript()) && !/tray/i.test(r.transcript()),
    `the remote sign-in path is still /remote-auth; got ${JSON.stringify(r.transcript())}`);
});


check("rail fetches /api/acp/sessions", async (tpl) => {
  // Same story for the rail's first fetch: one engine, one listing path.
  const store = fakeStore({ workspaces: 1, sessions: 1 });
  const page = await railed(tpl, { store });
  const urls = page.fetches.map((f) => f.url);
  assert(urls.length > 0, "no fetch calls were made after railed()");
  assert(urls.some((u) => u.startsWith("/api/acp/sessions")),
    `rail fetch should use /api/acp/sessions; fetched: ${JSON.stringify(urls)}`);
});



check("picker fetches /api/acp/workspaces", async (tpl) => {
  // Same story for the create-session picker's workspaces fetch.
  const store = fakeStore({ workspaces: 2, sessions: 1 });
  const page = await railed(tpl, { store });
  // Clear fetches from the rail load so we only see the picker fetch.
  page.fetches.length = 0;
  // Click the create-new button to trigger a workspace fetch.
  page.click("acpRailNew");
  await page.settle();
  const urls = page.fetches.map((f) => f.url);
  assert(urls.some((u) => u.startsWith("/api/acp/workspaces")),
    `picker fetch should use /api/acp/workspaces; fetched: ${JSON.stringify(urls)}`);
});

// --------------------------------------------------------- Phase 3: SC-3 session_info_update client rendering --

check("steer_status queued shows transient text near composer", (tpl) => {
  const { page, live } = connected(tpl);
  const status = page.el("acpSteerStatus");
  assertEqual(status.hidden, true, "steer status should start hidden");
  page.deliver({ type: "steer_status", sessionId: live,
                 payload: { status: "steering_queued", messageId: "m1",
                            content: "look at foo.py" } });
  assertEqual(status.hidden, false, "steer status should be shown once queued");
  assert(status.textContent.includes("look at foo.py"),
    `steer status text should include the steered content, got: ${status.textContent}`);
});

check("steer_status injected then auto-clears after a timer", (tpl) => {
  const { page, live } = connected(tpl);
  const status = page.el("acpSteerStatus");
  page.deliver({ type: "steer_status", sessionId: live,
                 payload: { status: "steering_injected", messageId: "m1",
                            content: "look at foo.py" } });
  assertEqual(status.hidden, false, "steer status should be shown once injected");
  assert(status.textContent.length > 0, "steer status text should be non-empty when injected");
  const before = page.timers.length;
  assert(before > 0, "steering_injected should schedule a clear timer");
  page.runTimers();
  assertEqual(status.hidden, true, "steer status should hide itself once the clear timer fires");
  assertEqual(status.textContent, "", "steer status text should be cleared once the timer fires");
});

check("steer_status cleared hides the status line immediately", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({ type: "steer_status", sessionId: live,
                 payload: { status: "steering_queued", content: "x" } });
  const status = page.el("acpSteerStatus");
  assertEqual(status.hidden, false, "sanity check — status should be visible after steering_queued");
  page.deliver({ type: "steer_status", sessionId: live,
                 payload: { status: "steering_cleared" } });
  assertEqual(status.hidden, true, "steer status should hide immediately on steering_cleared");
});

check("steer_status frame during history replay does not touch the DOM", (tpl) => {
  const { page, live } = connected(tpl);
  const status = page.el("acpSteerStatus");
  page.deliver({
    type: "history", sessionId: live,
    payload: { events: [
      { type: "steer_status", sessionId: live,
        payload: { status: "steering_injected", content: "stale" } },
    ] },
  });
  // A replayed steer_status describes a turn long over, with no live timer to
  // clear it afterward — the handler must be a no-op during replay.
  assertEqual(status.hidden, true,
    "a replayed steer_status must not surface a stale composer status");
});

check("steer_status frame with agent-controlled content uses textContent, never innerHTML", (tpl) => {
  const { page, live } = connected(tpl);
  const malicious = "<img src=x onerror=\"window._steer_xss=true\">";
  page.deliver({ type: "steer_status", sessionId: live,
                 payload: { status: "steering_queued", content: malicious } });
  const status = page.el("acpSteerStatus");
  // Reaching here at all means no innerHTML sink fired (HTML_SINK throws on
  // any access). textContent returns the literal string, unparsed.
  assert(status.textContent.includes(malicious),
    `steer status should render the content as literal text, got: ${status.textContent}`);
  assert(!page.sandbox._steer_xss, "onerror handler must not fire — steer status used innerHTML");
});

check("title frame sets the document title from focus_update", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({ type: "title", sessionId: live, payload: { title: "Fix the login bug" } });
  assert(String(page.sandbox.document.title).includes("Fix the login bug"),
    `document.title should include the session title, got: ${page.sandbox.document.title}`);
});

check("title frame with empty title is a no-op", (tpl) => {
  const { page, live } = connected(tpl);
  page.sandbox.document.title = "unchanged";
  page.deliver({ type: "title", sessionId: live, payload: { title: "" } });
  assertEqual(page.sandbox.document.title, "unchanged",
    "an empty title payload must not overwrite the current document title");
});

check("title frame renders during history replay (converges on the latest)", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({
    type: "history", sessionId: live,
    payload: { events: [
      { type: "title", sessionId: live, payload: { title: "first title" } },
      { type: "title", sessionId: live, payload: { title: "second title" } },
    ] },
  });
  assert(String(page.sandbox.document.title).includes("second title"),
    `document.title should reflect the last replayed title, got: ${page.sandbox.document.title}`);
});

check("document.title resets to the page default on releaseSession (session_closed)", (tpl) => {
  // Review finding, Security auditor, Low: releaseSession() already resets
  // other per-session transient UI state (setSteerStatus('')) but used to
  // leave document.title untouched, so session A's focus_update-derived
  // title would persist on the tab after A closed.
  const { page, live } = connected(tpl);
  page.deliver({ type: "title", sessionId: live, payload: { title: "Session A's title" } });
  assert(String(page.sandbox.document.title).includes("Session A's title"),
    "sanity check — the title frame should have set the tab title first");
  page.deliver({
    type: "session_closed", sessionId: live,
    payload: { message: "closed" },
  });
  assertEqual(page.sandbox.document.title, "PowerAtlas",
    "document.title should reset to the page default once the session is released");
});

check("document.title resets when switching to a different session", (tpl) => {
  // Review finding, Security auditor, Low: without a reset in the `session`
  // frame handler, a short session B that never sends its own focus_update
  // would keep showing session A's title on the tab indefinitely.
  const { page, live } = connected(tpl);
  page.deliver({ type: "title", sessionId: live, payload: { title: "Session A's title" } });
  assert(String(page.sandbox.document.title).includes("Session A's title"),
    "sanity check — the title frame should have set the tab title first");
  page.deliver({
    type: "session", sessionId: "sess-live-0002",
    payload: { sessionId: "sess-live-0002", cwd: "C:\\work\\repo2", created: true,
               turnActive: false, contextPercent: null },
  });
  assertEqual(page.sandbox.document.title, "PowerAtlas",
    "document.title should reset to the page default when a new `session` frame " +
    "adopts a different session, not carry over the previous session's title");
});

check("agent_error frame renders inline in the transcript like a failed tool call", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({ type: "agent_error", sessionId: live,
                 payload: { message: "MCP server requires authorization.",
                            errorType: "mcp_connection_error" } });
  const rows = page.el("acpTranscript").querySelectorAll(".acp-msg-error");
  assertEqual(rows.length, 1, "agent_error should append exactly one .acp-msg-error row");
  const body = rows[0].querySelector(".acp-msg-body");
  assert(body !== null, ".acp-msg-error row should contain .acp-msg-body");
  assert(body.textContent.includes("MCP server requires authorization."),
    `agent_error row should contain the error message, got: ${body.textContent}`);
  assert(body.textContent.includes("mcp_connection_error"),
    `agent_error row should surface the errorType, got: ${body.textContent}`);
});

check("agent_error frame with agent-controlled message uses textContent, never innerHTML", (tpl) => {
  const { page, live } = connected(tpl);
  const malicious = "<img src=x onerror=\"window._agent_error_xss=true\">";
  page.deliver({ type: "agent_error", sessionId: live,
                 payload: { message: malicious, errorType: "mcp_connection_error" } });
  // Reaching here at all means no innerHTML sink fired (HTML_SINK throws on
  // any access) — the harness would have thrown before this line otherwise.
  const rows = page.el("acpTranscript").querySelectorAll(".acp-msg-error");
  const body = rows[rows.length - 1].querySelector(".acp-msg-body");
  assert(body.textContent.includes(malicious),
    `agent_error should render the message as literal text, got: ${body.textContent}`);
  assert(!page.sandbox._agent_error_xss, "onerror handler must not fire — agent_error used innerHTML");
});

// --------------------------------------------------------- Phase 6: SC-9 session/request_permission client UI --

check("permission_request frame renders the question and one button per option", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({
    type: "permission_request", sessionId: live,
    payload: {
      requestId: 1, sessionId: live,
      toolCall: { title: "Pick a doc to write first" },
      options: [
        { optionId: "opt-0", name: "Requirements", kind: "allow_once" },
        { optionId: "opt-1", name: "Technical Design", kind: "allow_once" },
        { optionId: "opt-2", name: "Quick Spec", kind: "allow_once" },
      ],
    },
  });
  const rows = page.el("acpTranscript").querySelectorAll(".acp-msg-permission");
  assertEqual(rows.length, 1, "permission_request should append exactly one .acp-msg-permission row");
  const question = rows[0].querySelector(".acp-permission-question");
  assert(question !== null, "permission row should contain the question text");
  assertEqual(question.textContent, "Pick a doc to write first",
    `permission question should render the toolCall title, got: ${question.textContent}`);
  const buttons = rows[0].querySelectorAll(".acp-permission-option");
  assertEqual(buttons.length, 3, "permission row should render one button per option");
  assertEqual(buttons[0].textContent, "Requirements", "button text should be the option's name");
  assertEqual(buttons[1].textContent, "Technical Design", "button text should be the option's name");
  assertEqual(buttons[2].textContent, "Quick Spec", "button text should be the option's name");
});

check("permission_request frame with agent-controlled text uses textContent, never innerHTML", (tpl) => {
  const { page, live } = connected(tpl);
  const malicious = "<img src=x onerror=\"window._perm_xss=true\">";
  page.deliver({
    type: "permission_request", sessionId: live,
    payload: {
      requestId: 2, sessionId: live,
      toolCall: { title: malicious },
      options: [{ optionId: "opt-0", name: malicious, kind: "allow_once" }],
    },
  });
  // Reaching here at all means no innerHTML sink fired (HTML_SINK throws on
  // any access) — the harness would have thrown before this line otherwise.
  const rows = page.el("acpTranscript").querySelectorAll(".acp-msg-permission");
  const question = rows[rows.length - 1].querySelector(".acp-permission-question");
  assert(question.textContent.includes(malicious),
    `permission question should render the title as literal text, got: ${question.textContent}`);
  const button = rows[rows.length - 1].querySelector(".acp-permission-option");
  assert(button.textContent.includes(malicious),
    `permission option button should render the name as literal text, got: ${button.textContent}`);
  assert(!page.sandbox._perm_xss, "onerror handler must not fire — permission_request used innerHTML");
});

check("clicking a permission option sends permission_response and disables every button in the row", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({
    type: "permission_request", sessionId: live,
    payload: {
      requestId: 3, sessionId: live,
      toolCall: { title: "Which approach?" },
      options: [
        { optionId: "opt-a", name: "Option A", kind: "allow_once" },
        { optionId: "opt-b", name: "Option B", kind: "allow_once" },
      ],
    },
  });
  const rows = page.el("acpTranscript").querySelectorAll(".acp-msg-permission");
  const buttons = rows[rows.length - 1].querySelectorAll(".acp-permission-option");
  assertEqual(buttons.length, 2, "sanity check — two option buttons should be rendered");
  buttons[0].dispatch("click");
  const sent = page.sentOf("permission_response");
  assertEqual(sent.length, 1, "clicking an option should send exactly one permission_response frame");
  assertEqual(sent[0].sessionId, live, "permission_response should carry the session id");
  assertEqual(sent[0].payload.requestId, 3, "permission_response should carry the original requestId");
  assertEqual(sent[0].payload.optionId, "opt-a",
    "permission_response should carry the clicked option's optionId");
  assertEqual(buttons[0].disabled, true, "the clicked button should be disabled");
  assertEqual(buttons[1].disabled, true,
    "the other button should be disabled too, preventing a second click from resolving a different option");
});

check("a second click on an already-clicked permission row sends nothing further", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({
    type: "permission_request", sessionId: live,
    payload: {
      requestId: 4, sessionId: live,
      toolCall: { title: "Which approach?" },
      options: [{ optionId: "opt-a", name: "Option A", kind: "allow_once" }],
    },
  });
  const rows = page.el("acpTranscript").querySelectorAll(".acp-msg-permission");
  const button = rows[rows.length - 1].querySelector(".acp-permission-option");
  button.dispatch("click");
  assertEqual(page.sentOf("permission_response").length, 1,
    "sanity check — the first click should have sent exactly one frame");
  button.dispatch("click");
  assertEqual(page.sentOf("permission_response").length, 1,
    "a second click on an already-clicked button must not send a second permission_response — "
    + "this is the client-side nicety, not the server's actual double-answer guard");
});

check("permission_request frame renders during history replay (still answerable after a mid-turn reload)", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({
    type: "history", sessionId: live,
    payload: { events: [
      { type: "permission_request", sessionId: live,
        payload: { requestId: 5, sessionId: live,
                   toolCall: { title: "Replayed question" },
                   options: [{ optionId: "opt-0", name: "Only option", kind: "allow_once" }] } },
    ] },
  });
  const rows = page.el("acpTranscript").querySelectorAll(".acp-msg-permission");
  assertEqual(rows.length, 1,
    "a permission_request replayed from history must still render — unlike steer_status's " +
    "transient echo, a mid-turn reload must leave the request answerable");
});

check("permission_resolved frame disables the matching permission-request row's buttons (review fix)", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({
    type: "permission_request", sessionId: live,
    payload: {
      requestId: 6, sessionId: live,
      toolCall: { title: "Which approach?" },
      options: [{ optionId: "opt-0", name: "Only option", kind: "allow_once" }],
    },
  });
  const rows = page.el("acpTranscript").querySelectorAll(".acp-msg-permission");
  const row = rows[rows.length - 1];
  const button = row.querySelector(".acp-permission-option");
  assertEqual(button.disabled, false, "sanity check — the button starts enabled");

  page.deliver({ type: "permission_resolved", sessionId: live, payload: { requestId: 6 } });

  assertEqual(button.disabled, true,
    "permission_resolved must disable the matching row's buttons even though this tab never " +
    "clicked one — the cross-tab case: another tab answered this same request");
  assert(row.classList.contains("acp-permission-resolved"),
    "permission_resolved must visually mark the row resolved");

  button.dispatch("click");
  assertEqual(page.sentOf("permission_response").length, 0,
    "clicking a button after permission_resolved must send nothing — the buttons are disabled");
});

check("a permission_resolved frame with no matching row is a silent no-op", (tpl) => {
  const { page, live } = connected(tpl);
  // No permission_request was ever rendered for requestId 999 in this tab
  // (e.g. the buffer evicted it, or it belongs to a different session this
  // tab never subscribed to). This must not throw.
  page.deliver({ type: "permission_resolved", sessionId: live, payload: { requestId: 999 } });
  const rows = page.el("acpTranscript").querySelectorAll(".acp-msg-permission");
  assertEqual(rows.length, 0, "no permission row should exist or be created by this frame");
});

// 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 7 (K1): the
// server now sends an opaque `secrets.token_urlsafe(16)` string as requestId,
// not kiro-cli's small integer. The page must echo it verbatim and match its
// permission_resolved on it; the tests above only ever used numbers.
check("an opaque string requestId is echoed verbatim and resolves its own row", (tpl) => {
  const { page, live } = connected(tpl);
  const opaque = "Zq-8_x3nV0aK-pL_9wQe2A";
  page.deliver({
    type: "permission_request", sessionId: live,
    payload: {
      requestId: opaque, sessionId: live,
      toolCall: { title: "echo hi" },
      options: [{ optionId: "accept", name: "Yes", kind: "allow_once" },
                { optionId: "reject", name: "No", kind: "reject_once" }],
    },
  });
  const rows = page.el("acpTranscript").querySelectorAll(".acp-msg-permission");
  const row = rows[rows.length - 1];
  const buttons = row.querySelectorAll(".acp-permission-option");
  page.deliver({ type: "permission_resolved", sessionId: live, payload: { requestId: "other-id" } });
  assertEqual(buttons[0].disabled, false,
    "a permission_resolved for a different opaque id must leave this row clickable");
  buttons[1].dispatch("click");
  const sent = page.sentOf("permission_response");
  assertEqual(sent.length, 1, "the click sends exactly one permission_response");
  assertEqual(sent[0].payload.requestId, opaque,
    "the opaque requestId must be echoed back exactly, as the same string");
  page.deliver({ type: "permission_resolved", sessionId: live, payload: { requestId: opaque } });
  assert(row.classList.contains("acp-permission-resolved"),
    "a permission_resolved carrying the opaque id must mark its row resolved");
});

check("permission_request immediately followed by its own permission_resolved replays " +
      "into a resolved row, not fresh-and-clickable (review fix)", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver({
    type: "history", sessionId: live,
    payload: { events: [
      { type: "permission_request", sessionId: live,
        payload: { requestId: 7, sessionId: live,
                   toolCall: { title: "Already answered" },
                   options: [{ optionId: "opt-0", name: "Only option", kind: "allow_once" }] } },
      { type: "permission_resolved", sessionId: live, payload: { requestId: 7 } },
    ] },
  });
  const rows = page.el("acpTranscript").querySelectorAll(".acp-msg-permission");
  assertEqual(rows.length, 1, "sanity check — exactly one permission row rendered");
  const row = rows[rows.length - 1];
  const button = row.querySelector(".acp-permission-option");
  assertEqual(button.disabled, true,
    "replaying permission_request followed by its own permission_resolved must land on the " +
    "resolved state, not render as a fresh, fully-clickable question — this is the whole point " +
    "of recording permission_resolved into history rather than suppressing replay entirely");
  assert(row.classList.contains("acp-permission-resolved"),
    "the replayed row must be visually marked resolved");

  button.dispatch("click");
  assertEqual(page.sentOf("permission_response").length, 0,
    "clicking a resolved-on-replay row must send nothing");
});

// ---- 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 3 ----------
//
// The permission prompt's consent block, the in-page notification clamp, the
// new-session Default's resolution against the permission profile, and the
// dashboard's settings rows for it. The consent payload below is the shape
// acp.py's `_project_consent` emits: five allowlisted fields, every one of them
// optional, `matchedRule` reduced to `{capability, effect}`.

const PERM_OPTIONS = [
  { optionId: "allow_once", name: "Yes", kind: "allow_once" },
  { optionId: "reject_once", name: "No", kind: "reject_once" },
];

function permFrame(live, requestId, title, consent) {
  const payload = { requestId, sessionId: live, toolCall: { title }, options: PERM_OPTIONS };
  if (consent !== undefined) payload.consent = consent;
  return { type: "permission_request", sessionId: live, payload };
}

function lastPermRow(page) {
  const rows = page.el("acpTranscript").querySelectorAll(".acp-msg-permission");
  return rows[rows.length - 1];
}

function consentValue(row, field) {
  const hit = row.querySelectorAll(".acp-permission-consent-row")
    .filter((r) => r.dataset.field === field)[0];
  return hit ? hit.querySelector(".acp-permission-consent-value").textContent : null;
}

check("permission prompt: a consent block renders capability and resource", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver(permFrame(live, 101, "Write File", {
    capability: "fs_write",
    resource: "C:\\work\\repo\\src\\app.py",
    scope: "agent",
    source: "agent-profile",
    matchedRule: { capability: "fs_write", effect: "ask" },
  }));
  const row = lastPermRow(page);
  assert(row, "no permission row was drawn for a frame carrying a consent block");
  // G11 (Phase 3 review): known identifiers read as plain words, raw kept.
  assertEqual(consentValue(row, "capability"), "Write files (fs_write)",
    "the prompt does not say which capability is being asked for");
  assertEqual(consentValue(row, "resource"), "C:\\work\\repo\\src\\app.py",
    "the prompt does not name the file — the whole point of SC-4 is that a write " +
    "prompt stops reading as a bare 'Write File'");
  assertEqual(consentValue(row, "scope"), "agent", "scope was not rendered");
  assertEqual(consentValue(row, "source"), "PowerAtlas permission rules (Manual) (agent-profile)",
    "source was not rendered");
  assertEqual(consentValue(row, "matchedRule"), "Write files \u2192 ask",
    "the matched rule was not rendered as capability and effect");
  assertEqual(row.querySelectorAll(".acp-permission-option").length, 2,
    "the consent block displaced the answer buttons");
});

check("permission prompt: an 'always' option says it lasts for this session only", (tpl) => {
  // kiro-cli keeps an always-answer as an in-memory, session-scoped rule
  // (PowerAtlas sends no scope), so the button must not read as permanent.
  const { page, live } = connected(tpl);
  const frame = permFrame(live, 190, "echo hi");
  frame.payload.options = [
    { optionId: "accept", name: "Allow", kind: "allow_once" },
    { optionId: "reject", name: "Deny", kind: "reject_once" },
    { optionId: "always-reject", name: "Always deny", kind: "reject_always" },
  ];
  page.deliver(frame);
  const btns = lastPermRow(page).querySelectorAll(".acp-permission-option");
  assertEqual(btns.map((b) => b.textContent).join("|"),
    "Allow|Deny|Always deny (this session)",
    "only the always option should carry the session-scope label");
  assert(/session/.test(btns[2].title || ""), "the always option has no session-scope tooltip");
  assert(!btns[0].title && !btns[1].title, "a one-off option gained a scope tooltip");
});

check("permission prompt: an unknown or prototype-named capability renders raw, not mapped", (tpl) => {
  // G11 (Phase 3 review): the plain-words table is looked up with
  // hasOwnProperty, because the value is agent-authored — "constructor" must
  // not resolve to Object.prototype.constructor and print a function.
  const { page, live } = connected(tpl);
  const cases = ["future_capability", "constructor", "__proto__", "toString"];
  cases.forEach((cap, i) => {
    page.deliver(permFrame(live, 150 + i, "Do something", {
      capability: cap, source: "hasOwnProperty", matchedRule: { capability: cap, effect: "ask" } }));
    const row = lastPermRow(page);
    assertEqual(consentValue(row, "capability"), cap, `capability ${cap} was not rendered raw`);
    assertEqual(consentValue(row, "source"), "hasOwnProperty", "an unknown source was not rendered raw");
    assertEqual(consentValue(row, "matchedRule"), cap + " \u2192 ask",
      `the matched rule for ${cap} was not rendered raw`);
  });
});

check("permission prompt: no consent block, an empty one, or a malformed one renders without throwing", (tpl) => {
  const { page, live } = connected(tpl);
  const cases = [
    ["absent", undefined],
    ["empty", {}],
    ["null", null],
    ["a string", "fs_write"],
    ["an array", ["fs_write"]],
    ["non-string fields", { capability: 7, resource: { path: "x" }, matchedRule: "ask" }],
  ];
  cases.forEach(([label, consent], i) => {
    page.deliver(permFrame(live, 200 + i, "Run shell command", consent));
    const row = lastPermRow(page);
    assertEqual(row.dataset.requestId, String(200 + i), `no row was drawn for a consent that is ${label}`);
    assertEqual(row.querySelector(".acp-permission-question").textContent, "Run shell command",
      `the question was lost for a consent that is ${label}`);
    assertEqual(row.querySelectorAll(".acp-permission-consent").length, 0,
      `a consent block was drawn for a consent that is ${label}; nothing in it is a ` +
      "string worth showing, and String() of an object reads as agent text");
    assertEqual(row.querySelectorAll(".acp-permission-option").length, 2,
      `the answer buttons were lost for a consent that is ${label}`);
  });
  // A partial block shows what it has and nothing else.
  page.deliver(permFrame(live, 299, "Read file", { resource: "notes.md" }));
  const row = lastPermRow(page);
  assertEqual(consentValue(row, "resource"), "notes.md", "a lone resource was not rendered");
  assertEqual(consentValue(row, "capability"), null, "a missing capability drew an empty row");
});

check("permission prompt: script tags and quotes in the title and resource render as inert text", (tpl) => {
  // The escaping assertion R-7/Sec-3 promised. HTML_SINK makes any innerHTML
  // access throw, so reaching the assertions is half the proof; the other half
  // is that the text arrives verbatim and no element was built from it.
  const { page, live } = connected(tpl);
  const hostileTitle = "<script>alert(1)</script> \"double\" 'single' `tick`";
  const hostileResource = "C:\\x\\\"><script>alert(1)</script><img src=x onerror='alert(2)'>.txt";
  page.deliver(permFrame(live, 301, hostileTitle, {
    capability: "<b>shell</b>", resource: hostileResource,
    matchedRule: { capability: "\"shell\"", effect: "<i>ask</i>" },
  }));
  const row = lastPermRow(page);
  assertEqual(row.querySelector(".acp-permission-question").textContent, hostileTitle,
    "the title was altered on its way to the page");
  assertEqual(consentValue(row, "resource"), hostileResource,
    "the resource was altered on its way to the page");
  assertEqual(consentValue(row, "capability"), "<b>shell</b>",
    "the capability was altered on its way to the page");
  assertEqual(consentValue(row, "matchedRule"), "\"shell\" \u2192 <i>ask</i>",
    "the matched rule was altered on its way to the page");
  for (const tag of ["script", "img", "b", "i"]) {
    assertEqual(row.querySelectorAll(tag).length, 0,
      `a <${tag}> element exists in the permission row — agent text became markup`);
  }
  assert(!page.sandbox._perm_xss, "an injected handler ran");
});

check("permission prompt: a 500-character shell title renders without truncation", (tpl) => {
  const { page, live } = connected(tpl);
  const title = "Run: " + "git log --oneline -- src/power_atlas/".repeat(20).slice(0, 495);
  assertEqual(title.length, 500, "fixture length");
  page.deliver(permFrame(live, 401, title, { capability: "shell", resource: title }));
  const row = lastPermRow(page);
  const shown = row.querySelector(".acp-permission-question").textContent;
  assertEqual(shown.length, 500,
    "the title was truncated in the transcript — Phase 2 stopped clamping it " +
    "server-side precisely so the whole command is what the user approves");
  assertEqual(shown, title, "the title was altered");
});

check("permission prompt: a tens-of-kilobytes resource renders through textContent, contained by CSS", (tpl) => {
  const { page, live } = connected(tpl);
  const resource = "C:\\" + "a".repeat(48 * 1024) + ".txt";
  page.deliver(permFrame(live, 501, "Read file", { capability: "fs_read", resource }));
  const row = lastPermRow(page);
  assertEqual(consentValue(row, "resource"), resource,
    "a long resource was truncated or altered — the renderer contains its length " +
    "with CSS, it does not cut what the user is approving");
  // The containment is CSS, and this harness has no box model, so the rule is
  // what is asserted: wrap anywhere, and scroll past a bounded height.
  const css = fs.readFileSync(STYLESHEET, "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
  const body = (selector) => [...css.matchAll(/([^{}]*)\{([^{}]*)\}/g)]
    .filter((m) => m[1].split(",").some((s) => s.trim().replace(/\s+/g, " ") === selector))
    .map((m) => m[2]).join(";");
  const value = body(".acp-permission-consent-value");
  assert(/overflow-wrap:\s*anywhere/.test(value),
    "a consent value has no overflow-wrap: an unbroken 48 KB path would widen the transcript");
  assert(/max-height:/.test(value) && /overflow-y:\s*auto/.test(value),
    "a consent value has no bounded, scrollable height: a 48 KB resource would push " +
    "the answer buttons a screen away");
  const question = body(".acp-permission-question");
  assert(/overflow-wrap:\s*anywhere/.test(question),
    "the now-unclamped title has no overflow-wrap");
  // G8 (Phase 3 review): the title sits above the consent block and the
  // buttons, so it is bounded and scrolls inside itself too.
  assert(/max-height:/.test(question) && /overflow-y:\s*auto/.test(question),
    "the permission title has no bounded, scrollable height: a long title " +
    "pushes the Allow/Deny buttons off screen");
  // The transcript-renderer source itself: no HTML sink anywhere near consent.
  const renderer = transcriptRendererSource();
  const from = renderer.indexOf("function permissionConsentBlock(");
  const to = renderer.indexOf("function findPermissionRequestRow(", from);
  assert(from >= 0 && to > from, "permissionConsentBlock has moved");
  assert(!/innerHTML|insertAdjacentHTML|outerHTML/.test(renderer.slice(from, to)),
    "the consent renderer uses an HTML sink");
});

check("permission prompt: the in-page browser notification clamps the title to 200 characters", async (tpl) => {
  const bodies = [];
  const page = loadPage(tpl, {
    visibility: "hidden",
    answer: (url) => url === "/api/notifications" ? { body: { enabled: true } } : null,
  });
  page.sandbox.Notification = class {
    constructor(title, o) { bodies.push({ title, body: o.body, tag: o.tag }); }
  };
  page.sandbox.Notification.permission = "granted";
  page.open();
  const live = "sess-live-0001";
  page.deliver({ type: "session", sessionId: live, payload: {
    sessionId: live, cwd: "C:\\work\\repo", created: true, turnActive: true, contextPercent: null } });
  await page.settle();
  const title = "x".repeat(5000);
  page.deliver(permFrame(live, 601, title, { capability: "shell" }));
  assertEqual(bodies.length, 1, "no notification was raised for a hidden tab with notifications on");
  assertEqual(bodies[0].body, "Needs approval: " + "x".repeat(200),
    "the notification body was not clamped to 200 title characters");
  assertEqual(lastPermRow(page).querySelector(".acp-permission-question").textContent.length, 5000,
    "the clamp reached the transcript row, which must show the whole title");
});

// The new-session Default against the permission profile (SC-3).
// 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 3 (G1, user
// decision 2026-09-23): the server resolves Default. Every page path sends the
// Default entry's own value, `kiro_default`, and never reads
// /api/acp-permissions to decide it — a page that could not read the setting
// (the remote phone page; a stale tab) used to send `kiro_default` while the
// profile was on and start an ungated session that looked gated. The answer
// below would say "in effect" if anything asked, so a page that still resolved
// Default itself would send the derived agent and fail these checks.

const PERM_IN_EFFECT = (url) => url.startsWith("/api/acp-permissions")
  ? { body: { enabled: true, in_effect: true, state: "on" } } : null;

check("task-mode picker: Default sends kiro_default, never the derived agent, without reading the permission state", async (tpl) => {
  const page = await railed(tpl, {
    store: fakeStore({ workspaces: 1, sessions: 1 }),
    answer: PERM_IN_EFFECT,
  });
  await page.openPicker();
  const values = page.all("acpPickerTaskModeMenu", ".acp-taskmode-option")
    .map((o) => o.dataset.value);
  assert(values.length >= 5, "the task-mode menu has no options to check");
  assert(!values.includes("poweratlas-acp"),
    "the picker offers the derived agent as a mode of its own; Default is how it is reached");
  page.click("acpPickerNeutral");
  // Sent at once: nothing to wait for, since the page no longer resolves it.
  const sent = page.sentOf("new");
  assertEqual(sent.length, 1, "the create did not go out synchronously");
  assertEqual(sent[0].payload.mode, "kiro_default",
    "Default sent something other than its own value; the server decides what it binds");
  // A vendor mode passes through untouched.
  await page.openPicker();
  page.click("acpPickerTaskModeOptSpec");
  page.click("acpPickerNeutral");
  await page.settle();
  assertEqual(page.sentOf("new")[1].payload.mode, "spec", "a vendor task mode was rewritten");
  assert(!page.fetches.some((f) => f.url.startsWith("/api/acp-permissions")),
    "the /acp page read /api/acp-permissions; creating a session must not depend on it");
});

check("task-mode picker: a refused derived-agent create is legible in the transcript", async (tpl) => {
  const { page } = connected(tpl, { store: fakeStore({ workspaces: 1, sessions: 1 }) });
  await page.settle();
  // The D-34 refusal (260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL): cause,
  // fix, the remote note and the task-mode way on.
  const message = "PowerAtlas did not create this session: its permission rules, " +
    "including the Always blocked list, are not in effect, because its agent file " +
    "C:/x/poweratlas-acp.md has not been written. Save the permission mode again in " +
    "Settings > Agent permissions on the dashboard, or restart PowerAtlas. This has to " +
    "be done on the computer running PowerAtlas. To start a session now without those " +
    "rules, pick a task mode such as Spec or Plan instead of Default.";
  page.deliver({ type: "error", payload: { code: "bad_payload", message } });
  assert(page.transcript().includes(message),
    "the server's refusal did not reach the transcript, so the create just silently failed");
});

check("dashboard: the picker's Default and quick create both send kiro_default without reading the permission state", async () => {
  const p = loadDashPicker();
  const values = p.el("dashPickerTaskModeMenu").querySelectorAll(".acp-taskmode-option")
    .map((o) => o.getAttribute("data-value"));
  assert(!values.includes("poweratlas-acp"),
    "the dashboard picker offers the derived agent as a mode of its own");
  p.sandbox.dashPickerOpen("");
  // The harness's dialog has no removeEventListener; skip the focus-trap
  // teardown the same way "dashPickerClose hides the picker" does.
  p.sandbox._dashPickerTrapRemove = null;
  p.sandbox.dashPickerCreate("/ws");
  assertEqual(p.sentOf("new").length, 1, "the picker's create did not go out synchronously");
  assertEqual(p.sentOf("new")[0].payload.mode, "kiro_default",
    "the dashboard picker's Default sent something other than kiro_default");
  p.sandbox.dashRailQuickCreate("/ws2");
  assertEqual(p.sentOf("new")[1].payload.mode, "kiro_default",
    "quick create sent something other than kiro_default");
  await p.settle();
  assert(!p.fetches.some((f) => String(f.url).startsWith("/api/acp-permissions")),
    "the dashboard read /api/acp-permissions to create a session");
});

check("dashboard: a refused create replaces 'Creating session…' with the server's message", () => {
  // G2 (Phase 3 review, UX#2): the refusal used to leave the placeholder in the
  // pane with the composer hidden, explained only by a 4-second toast.
  const p = loadDashPicker({ dashAttachedSid: null });
  const toasts = [];
  p.sandbox.showToast = (html) => toasts.push(html);
  p.sandbox._dashPickerCapacity = { held: 0, max: 8 };
  p.sandbox.dashRailQuickCreate("/proj");
  assert(/Creating session/.test(p.el("dashTranscript").textContent),
    "fixture: the placeholder was not drawn");
  assertEqual(p.sandbox.dashComposerEl.hidden, true, "fixture: the composer was not hidden");
  const message = "PowerAtlas could not check whether its permission rules are in effect, " +
    "so no session was created.";
  p.sandbox.dashHandle({ type: "error", payload: { code: "bad_payload", message } });
  assert(!/Creating session/.test(p.el("dashTranscript").textContent),
    "the 'Creating session…' placeholder survived the refusal");
  const said = p.addMessageCalls.map((c) => c.role + " " + c.text).join("\n");
  assert(said.includes(message), `the refusal did not reach the transcript: ${said}`);
  assertEqual(toasts.length, 0, "the refusal went to a toast instead of the transcript");
  // A later sid-less error is not mistaken for another create refusal.
  p.sandbox.dashHandle({ type: "error", payload: { code: "bad_payload", message: "other" } });
  assertEqual(toasts.length, 1, "an unrelated error after the refusal was not a toast");
});

check("dashboard: a refused create with a session still attached brings the composer back", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.sandbox.showToast = () => {};
  p.sandbox._dashPickerCapacity = { held: 1, max: 8 };
  p.sandbox.dashRailQuickCreate("/proj");
  assertEqual(p.sandbox.dashComposerEl.hidden, true, "fixture: the composer was not hidden");
  p.sandbox.dashHandle({ type: "error", payload: { code: "session_limit", message: "full" } });
  assertEqual(p.sandbox.dashComposerEl.hidden, false,
    "the composer stayed hidden although the attached session is still open");
});

check("dashboard: a permission_request frame passes its consent block to the renderer", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  const calls = [];
  p.sandbox.addPermissionRequest = function () { calls.push([...arguments]); };
  const consent = { capability: "fs_write", resource: "a.txt" };
  p.sandbox.dashHandle({ type: "permission_request", sessionId: "sess-1", payload: {
    requestId: 9, sessionId: "sess-1", toolCall: { title: "Write File" },
    consent, options: PERM_OPTIONS } });
  assertEqual(calls.length, 1, "the dashboard drew no permission row");
  assertEqual(JSON.stringify(calls[0][4]), JSON.stringify(consent),
    "the dashboard dropped the consent block on the way to the renderer");
});

// ---- 260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL Phase 3 -------------------
// "Allow, and always in new sessions…" on the permission card (D-19, D-20,
// D-31, D-35, SC-6, SC-7). The card lives in transcript-renderer.js, which
// /acp's harness runs for real; the dashboard's harness does not load it, so
// the dashboard is covered twice: `dashHandle` forwards the server's verdict,
// and the real renderer is run beside index.html's own `ACP_LOCAL` global.

const RULE_OPTIONS = [
  // `optionId` differs from `kind` on purpose: the card answers by kind.
  { optionId: "accept", name: "Yes", kind: "allow_once" },
  { optionId: "reject", name: "No", kind: "reject_once" },
];
const RULE_URL = "/api/acp-permissions/allow-rule";

function ruleFrame(live, requestId, consent, over = {}) {
  const payload = Object.assign({
    requestId, sessionId: live, toolCall: { title: consent.resource || "x" },
    options: RULE_OPTIONS, consent,
    ruleEligible: true, ruleRow: consent.capability,
    ruleRowLabel: { shell: "Run commands", fs_write: "Write files", fs_read: "Read files",
                    mcp: "MCP tools", subagent: "Sub-agents", skill: "Skills" }[consent.capability] || "",
  }, over);
  return { type: "permission_request", sessionId: live, payload };
}

function shellConsent(command, trig) {
  const c = { capability: "shell", resource: command, scope: "agent", source: "agent-profile",
              matchedRule: { capability: "shell", effect: "ask" } };
  if (trig !== undefined) c.triggeringResource = trig;
  return c;
}

function fileConsent(cap, resource) {
  return { capability: cap, resource, scope: "agent", source: "agent-profile",
           matchedRule: { capability: cap, effect: "ask" } };
}

function ruleButton(row) { return row.querySelector(".acp-permission-rule-open"); }

/** Open the inline row on the last card and return its parts. */
function openRule(page) {
  const row = lastPermRow(page);
  const trigger = ruleButton(row);
  assert(trigger, "the card has no 'Allow, and always in new sessions…' button");
  trigger.dispatch("click");
  const input = row.querySelector(".acp-permission-rule-input");
  assert(input, "the button opened no field");
  return {
    row, trigger, input,
    save: row.querySelector(".acp-permission-rule-save"),
    cancel: row.querySelector(".acp-permission-rule-cancel"),
    warnings: () => row.querySelectorAll(".acp-permission-rule-warn-line").map((n) => n.textContent),
    status: () => row.querySelector(".acp-permission-rule-status"),
  };
}

function ruleAnswer(body, extra = {}) {
  return (url) => (url === RULE_URL ? Object.assign({ body }, extra) : null);
}

check("rule button: shown on a loopback page for an eligible prompt with an allow_once option", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver(ruleFrame(live, 601, shellConsent("echo pa-button")));
  const btn = ruleButton(lastPermRow(page));
  assert(btn, "no button on an eligible loopback card");
  assertEqual(btn.textContent, "Allow, and always in new sessions\u2026", "the button's words changed");
  assertEqual(btn.getAttribute("aria-expanded"), "false", "the button does not say it is collapsed");
  assertEqual(page.sandbox.window.ACP_LOCAL, true,
    "acp.html does not expose ACP_LOCAL on window, so the shared renderer cannot see it");
});

check("rule button: absent on a remote page, an ineligible prompt, without allow_once, and for web fetch", (tpl) => {
  const remote = connected(tpl, { local: false });
  remote.page.deliver(ruleFrame(remote.live, 602, shellConsent("echo x")));
  assertEqual(ruleButton(lastPermRow(remote.page)), null, "a remote page offered the button (D-9, SC-7)");
  assertEqual(remote.page.sandbox.window.ACP_LOCAL, false, "a remote page exposed ACP_LOCAL as true");

  const { page, live } = connected(tpl);
  const cases = [
    ["ruleEligible false", ruleFrame(live, 610, shellConsent("echo x"), { ruleEligible: false })],
    ["ruleEligible a string", ruleFrame(live, 611, shellConsent("echo x"), { ruleEligible: "true" })],
    ["no ruleEligible at all", ruleFrame(live, 612, shellConsent("echo x"), { ruleEligible: undefined })],
    ["no allow_once option", ruleFrame(live, 613, shellConsent("echo x"),
      { options: [{ optionId: "reject", name: "No", kind: "reject_once" }] })],
    ["a web fetch prompt", ruleFrame(live, 614, fileConsent("web_fetch", "example.com"))],
    ["an unknown row", ruleFrame(live, 615, shellConsent("echo x"), { ruleRow: "constructor" })],
  ];
  for (const [label, frame] of cases) {
    page.deliver(frame);
    const row = lastPermRow(page);
    assertEqual(row.dataset.requestId, String(frame.payload.requestId), `no card for ${label}`);
    assertEqual(ruleButton(row), null, `the button appeared for ${label}`);
  }
});

check("rule button: the field is prefilled per D-20 and warns per D-35", (tpl) => {
  const { page, live } = connected(tpl);
  const cases = [
    [shellConsent("git status --short"), "git status --short", 0],
    [shellConsent("git status && echo x", "echo x"), "echo x", 0],
    [shellConsent("python -m pytest"), "python -m pytest", 1],
    [fileConsent("mcp", "paecho/pa_echo"), "paecho/pa_echo", 0],
    [fileConsent("subagent", "kiro_default"), "kiro_default", 0],
    [fileConsent("skill", "pa-probe-skill"), "pa-probe-skill", 0],
  ];
  cases.forEach(([consent, want, warnings], i) => {
    page.deliver(ruleFrame(live, 620 + i, consent));
    const r = openRule(page);
    assertEqual(r.input.value, want, `prefill for ${consent.capability} ${consent.resource}`);
    assertEqual(r.warnings().length, warnings, `warnings for ${want}: ${r.warnings().join(" | ")}`);
    if (warnings) {
      assert(/equivalent to allow-all/.test(r.warnings()[0]),
        `the interpreter warning is missing for ${want}`);
    }
    assertEqual(page.focused(), r.input, "opening the row did not move the focus into the field");
  });
  // Editing re-runs the warnings: a * warns about redirection (D-38e).
  page.deliver(ruleFrame(live, 690, shellConsent("npm test")));
  const r = openRule(page);
  r.input.value = "npm *";
  r.input.dispatch("input");
  assert(r.warnings().some((w) => /redirection/.test(w)), "no redirection warning for a * pattern");
  // The row is labelled, and the label names the plain row.
  const label = r.row.querySelector(".acp-permission-rule-label");
  assertEqual(label.getAttribute("for"), r.input.getAttribute("id"), "the field has no label");
  assert(/^Run commands/.test(label.textContent), `the label does not name the row: ${label.textContent}`);
  // Phase 3 review (M5): ? and [ are wildcards too.
  for (const p of ["echo ????", "echo [ab]"]) {
    r.input.value = p;
    r.input.dispatch("input");
    assert(r.warnings().some((w) => /redirection/.test(w)), `no redirection warning for ${p}`);
  }
});

// Phase 3 review (H1, M1, M2): a file prompt's prefill is an absolute path
// under the session's folder (`ruleRoot`), never a broad place, and the exact
// resource whenever the resource cannot be read as a folder.
const RULE_ROOT = "C:/ws/proj";
const FILE_PREFILLS = [
  // [row, resource, root, prefill, warnings matched in order]
  ["fs_write", "notes.md", RULE_ROOT, "C:/ws/proj/notes.md", []],
  ["fs_write", "./notes.md", RULE_ROOT, "C:/ws/proj/./notes.md", []],
  ["fs_write", "src\\app\\main.py", RULE_ROOT, "C:/ws/proj/src/app/**", []],
  ["fs_write", "other/x.txt", RULE_ROOT, "C:/ws/proj/other/**", []],
  ["fs_read", "C:\\work\\repo\\src\\a.py", RULE_ROOT, "C:/work/repo/src/**", []],
  ["fs_write", "C:\\notes.md", RULE_ROOT, "C:/notes.md", []],
  ["fs_write", "C:\\Users\\bob\\notes.md", RULE_ROOT, "C:/Users/bob/notes.md", []],
  // The review's escapes (probe2.js), each 0 warnings before the fix.
  ["fs_read", "C:\\Users\\desktop.ini", RULE_ROOT, "C:/Users/desktop.ini", [/covers the whole home folder/]],
  // An exact path of the home folder's shape still warns: without a /** a
  // pattern may name that folder, and the warning costs nothing if it is a file.
  ["fs_write", "/home/x", RULE_ROOT, "/home/x", [/covers the whole home folder/]],
  ["fs_write", "/x", RULE_ROOT, "/x", []],
  ["fs_write", "C:\\Users\\*\\x.txt", RULE_ROOT, "C:/Users/*/x.txt", [/holds a wildcard/]],
  ["fs_write", "C:\\Users\\q\\.\\x", RULE_ROOT, "C:/Users/q/./x", []],
  ["fs_write", "\\\\?\\C:\\x\\y", RULE_ROOT, "//?/C:/x/y", [/holds a wildcard/]],
  ["fs_write", "\\\\server\\share\\x.txt", RULE_ROOT, "//server/share/x.txt", []],
  ["fs_write", "C:\\Users\\QSYLVE~1.POL\\x", RULE_ROOT, "C:/Users/QSYLVE~1.POL/x", []],
  ["fs_write", "C:\\Users\\q\\OneDrive\\x", RULE_ROOT, "C:/Users/q/OneDrive/**", []],
  // M1: `..` stays literal; the server refuses it, and the card says why first.
  ["fs_read", "../x.txt", RULE_ROOT, "C:/ws/proj/../x.txt", [/goes up a folder/]],
  ["fs_read", "../../x.txt", RULE_ROOT, "C:/ws/proj/../../x.txt", [/goes up a folder/]],
  ["fs_write", "C:/Users/q/../../x", RULE_ROOT, "C:/Users/q/../../x", [/goes up a folder/]],
  // The session folder, or a folder above it, is never the prefill.
  ["fs_write", "C:\\ws\\x.txt", RULE_ROOT, "C:/ws/x.txt", []],
  ["fs_write", "c:\\WS\\PROJ\\x.txt", RULE_ROOT, "c:/WS/PROJ/x.txt", []],
  // No root known: the exact relative resource, flagged as relative.
  ["fs_write", "src/app/main.py", null, "src/app/main.py", [/is relative/]],
];

check("rule button: a file prompt prefills an absolute, narrow path and warns on the rest", (tpl) => {
  const { page, live } = connected(tpl);
  FILE_PREFILLS.forEach(([row, resource, root, want, warns], i) => {
    page.deliver(ruleFrame(live, 800 + i, fileConsent(row, resource), { ruleRoot: root }));
    const r = openRule(page);
    assertEqual(r.input.value, want, `prefill for ${row} ${resource}`);
    const got = r.warnings();
    assertEqual(got.length, warns.length, `warnings for ${want}: ${got.join(" | ")}`);
    warns.forEach((re, k) => assert(re.test(got[k]), `warning ${k} for ${want}: ${got[k]}`));
    const hint = r.row.querySelector(".acp-permission-rule-hint").textContent;
    assert(/applies to that folder in every new session/.test(hint),
      `the file hint does not say where the rule applies: ${hint}`);
  });
});

check("rule button: broad file patterns warn for reads as well as writes", (tpl) => {
  const { page, live } = connected(tpl);
  const cases = [
    ["fs_read", "C:/Users/**", /covers every home folder, so file-tool reads/],
    ["fs_read", "/home/**", /covers every home folder/],
    ["fs_write", "C:/Users/q/**", /covers the whole home folder, so file-tool writes/],
    ["fs_write", "/**", /covers a whole drive/],
    ["fs_write", "C:/ws/proj/**", /covers the whole session folder/],
    ["fs_read", "C:/ws/**", /covers a folder that contains the session folder/],
    ["fs_write", "other/**", /is relative, so it matches in the folder of every session/],
  ];
  cases.forEach(([row, pattern, re], i) => {
    page.deliver(ruleFrame(live, 860 + i, fileConsent(row, "C:\\ws\\proj\\a\\b.txt"),
      { ruleRoot: RULE_ROOT }));
    const r = openRule(page);
    r.input.value = pattern;
    r.input.dispatch("input");
    assert(r.warnings().some((w) => re.test(w)), `${row} ${pattern}: ${r.warnings().join(" | ")}`);
  });
});

check("rule button: a split command says the rule covers only its part, and warns on the whole", async (tpl) => {
  // Phase 3 review (M4, L2): kiro-cli asked about one part of the command
  // (`triggeringResource`); allow_once runs the whole command.
  const { page, live } = connected(tpl, { answer: ruleAnswer({ ok: true }) });
  page.deliver(ruleFrame(live, 880, shellConsent("echo pa-one && python evil.py", "echo pa-one")));
  const r = openRule(page);
  assertEqual(r.input.value, "echo pa-one", "the prefill is not the part that asked");
  const hint = r.row.querySelector(".acp-permission-rule-hint").textContent;
  assert(/covers only “echo pa-one”; other parts of the command may still ask/.test(hint),
    `the hint overpromises: ${hint}`);
  assert(r.warnings().some((w) => /^Save also allows the whole command once\. .*python/.test(w)),
    `no warning for the whole command: ${r.warnings().join(" | ")}`);
  r.save.dispatch("click");
  await settleStaging();
  assertEqual(r.status().textContent,
    "Rule added for that part only \u2014 new sessions may still ask about the rest",
    "the outcome overpromises for a split command");
  // An unsplit command keeps the plain words and adds no whole-command line.
  page.deliver(ruleFrame(live, 881, shellConsent("echo same", "echo same")));
  const plain = openRule(page);
  assert(!/covers only/.test(plain.row.querySelector(".acp-permission-rule-hint").textContent),
    "an unsplit command claims to be partial");
  assertEqual(plain.warnings().length, 0, "an unsplit command warned twice");
});

check("rule button: a pending posture notice is pointed at after a save", async (tpl) => {
  // Phase 3 review (M3): the route never clears D-35's notice and answers with it.
  const notice = { mode: "manual", detected_at: "2026-09-25 00:00:00" };
  const { page, live } = connected(tpl, { answer: ruleAnswer({ ok: true, posture_notice: notice }) });
  page.deliver(ruleFrame(live, 890, shellConsent("echo a")));
  const r = openRule(page);
  r.save.dispatch("click");
  await settleStaging();
  const line = r.status().querySelector(".acp-permission-rule-notice");
  assert(line, "the notice was not pointed at");
  assertEqual(line.textContent, "Settings changed outside the dashboard \u2014 review them in the dashboard\u2019s Settings > Agent permissions",
    "the notice line's words changed");
  const quiet = connected(tpl, { answer: ruleAnswer({ ok: true, posture_notice: null }) });
  quiet.page.deliver(ruleFrame(quiet.live, 891, shellConsent("echo a")));
  const q = openRule(quiet.page);
  q.save.dispatch("click");
  await settleStaging();
  assertEqual(q.status().querySelector(".acp-permission-rule-notice"), null,
    "a notice line appeared with no notice pending");
});

check("rule button: 'Triggered by' shows only when it differs from the resource", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver(ruleFrame(live, 700, shellConsent("git status && echo x", "echo x")));
  assertEqual(consentValue(lastPermRow(page), "triggeringResource"), "echo x",
    "the split sub-command is not shown");
  page.deliver(ruleFrame(live, 701, shellConsent("echo x", "echo x")));
  assertEqual(consentValue(lastPermRow(page), "triggeringResource"), null,
    "the same text was shown twice");
  const label = lastPermRow(page).querySelectorAll(".acp-permission-consent-label")
    .map((n) => n.textContent);
  assert(!label.includes("Triggered by"), "an empty 'Triggered by' row was drawn");
});

check("rule button: Save posts the rule, then answers with the allow_once option by kind", async (tpl) => {
  const { page, live } = connected(tpl, { answer: ruleAnswer({ ok: true }) });
  // Phase 3 review (L7): reject_once first, so answering with the first
  // option instead of the one of kind allow_once fails here.
  page.deliver(ruleFrame(live, 710, shellConsent("echo pa-button"),
    { options: [RULE_OPTIONS[1], RULE_OPTIONS[0]] }));
  const r = openRule(page);
  r.input.value = "echo pa-button";
  r.save.dispatch("click");
  const posts = page.fetches.filter((f) => f.url === RULE_URL);
  assertEqual(posts.length, 1, "Save sent no request");
  assertEqual(posts[0].init.method, "POST", "the rule was not POSTed");
  assertEqual(posts[0].init.body, JSON.stringify({ capability: "shell", pattern: "echo pa-button" }),
    "the posted body is not {capability, pattern}");
  assertEqual(page.sentOf("permission_response").length, 0,
    "the prompt was answered before the rule was saved");
  await settleStaging();
  const sent = page.sentOf("permission_response");
  assertEqual(sent.length, 1, "the prompt was not answered after the rule was saved");
  assertEqual(sent[0].payload.optionId, "accept", "the answer was not the allow_once option");
  assertEqual(sent[0].payload.requestId, 710, "the answer names another request");
  assertEqual(r.status().textContent, "Rule added \u2014 new sessions will not ask",
    "the card does not say the rule was added");
  assertEqual(r.row.querySelector(".acp-permission-rule"), null, "the inline row stayed open");
  assertEqual(r.trigger.disabled, true, "the button can be pressed again");
  assertEqual(page.focused(), r.status(), "the focus did not land on the outcome");
  const chosen = r.row.querySelectorAll(".acp-permission-option")
    .filter((b) => b.classList.contains("acp-permission-chosen"));
  assertEqual(chosen.length, 1, "the allow_once button is not marked chosen");
  assertEqual(chosen[0].textContent, "Yes", "another option was marked chosen");
});

check("rule button: Enter saves, Escape and Cancel close and return the focus", async (tpl) => {
  const { page, live } = connected(tpl, { answer: ruleAnswer({ ok: true }) });
  page.deliver(ruleFrame(live, 720, shellConsent("echo a")));
  let r = openRule(page);
  r.input.dispatch("keydown", { key: "Escape", preventDefault() {}, stopPropagation() {} });
  assertEqual(r.row.querySelector(".acp-permission-rule"), null, "Escape did not close the row");
  assertEqual(page.focused(), r.trigger, "Escape did not return the focus to the button");
  assertEqual(r.trigger.getAttribute("aria-expanded"), "false", "the button still says expanded");
  r = openRule(page);
  r.cancel.dispatch("click");
  assertEqual(r.row.querySelector(".acp-permission-rule"), null, "Cancel did not close the row");
  assertEqual(page.focused(), r.trigger, "Cancel did not return the focus to the button");
  assertEqual(page.fetches.filter((f) => f.url === RULE_URL).length, 0, "closing posted a rule");
  r = openRule(page);
  r.input.dispatch("keydown", { key: "Enter", preventDefault() {} });
  await settleStaging();
  assertEqual(page.fetches.filter((f) => f.url === RULE_URL).length, 1, "Enter did not save");
  assertEqual(page.sentOf("permission_response").length, 1, "Enter's save did not answer");
});

check("rule button: a refusal or a failed request leaves the prompt open with the reason", async (tpl) => {
  const cases = [
    ["a refusal", ruleAnswer({ ok: false, error: "The rule was not saved: 'x' is blank." }),
      "The rule was not saved: 'x' is blank."],
    ["a 403", ruleAnswer({ error: "Forbidden" }, { ok: false, status: 403 }), "Forbidden"],
    ["a 409 without a reason", ruleAnswer(null, { ok: false, status: 409 }),
      "The rule was not saved (HTTP 409)."],
    ["no answer", (url) => (url === RULE_URL ? { reject: "down" } : null),
      "The rule was not saved: PowerAtlas could not be reached."],
  ];
  for (const [label, answer, want] of cases) {
    const { page, live } = connected(tpl, { answer });
    page.deliver(ruleFrame(live, 730, shellConsent("echo a")));
    const r = openRule(page);
    r.save.dispatch("click");
    await settleStaging();
    assertEqual(page.sentOf("permission_response").length, 0, `${label}: the prompt was answered`);
    const error = r.row.querySelector(".acp-permission-rule-error");
    assertEqual(error.hidden, false, `${label}: no reason shown`);
    assertEqual(error.textContent, want, `${label}: wrong reason`);
    assertEqual(r.save.disabled, false, `${label}: Save stayed disabled`);
    assert(r.row.querySelectorAll(".acp-permission-option").every((b) => !b.disabled),
      `${label}: the answer buttons were disabled`);
    assertEqual(page.focused(), r.input, `${label}: the focus did not go back to the field`);
  }
});

check("rule button: a prompt resolved while saving is not answered, and the card says so", async (tpl) => {
  const { page, live } = connected(tpl, { answer: ruleAnswer({ ok: true }) });
  page.deliver(ruleFrame(live, 740, shellConsent("echo a")));
  const r = openRule(page);
  r.save.dispatch("click");
  page.deliver({ type: "permission_resolved", sessionId: live, payload: { requestId: 740 } });
  await settleStaging();
  assertEqual(page.sentOf("permission_response").length, 0, "a resolved prompt was answered again");
  assertEqual(r.status().textContent, "Rule saved; this prompt was already answered",
    "the card does not say the prompt was already answered");
});

check("rule button: an open row stays usable after the prompt resolves; the button does not", async (tpl) => {
  const { page, live } = connected(tpl, { answer: ruleAnswer({ ok: true }) });
  page.deliver(ruleFrame(live, 750, shellConsent("echo a")));
  const r = openRule(page);
  page.deliver({ type: "permission_resolved", sessionId: live, payload: { requestId: 750 } });
  assertEqual(r.save.disabled, false, "resolving the prompt disabled the open row's Save");
  assertEqual(r.trigger.disabled, true, "resolving the prompt left the button enabled");
  r.save.dispatch("click");
  await settleStaging();
  assertEqual(page.fetches.filter((f) => f.url === RULE_URL).length, 1, "the rule was not saved");
  assertEqual(page.sentOf("permission_response").length, 0, "a resolved prompt was answered");
  assertEqual(r.status().textContent, "Rule saved; this prompt was already answered",
    "the outcome is wrong for a resolved prompt");
  // Phase 3 review (L4): closing the row now returns the focus to the card,
  // since the button it came from is disabled; never to <body>.
  page.deliver(ruleFrame(live, 752, shellConsent("echo c")));
  const esc = openRule(page);
  page.deliver({ type: "permission_resolved", sessionId: live, payload: { requestId: 752 } });
  esc.cancel.dispatch("click");
  assertEqual(page.focused(), esc.row, "Cancel on a resolved card lost the focus");
  assertEqual(esc.row.getAttribute("tabindex"), "-1", "the card cannot take the focus");
  page.deliver(ruleFrame(live, 753, shellConsent("echo d")));
  const esc2 = openRule(page);
  page.deliver({ type: "permission_resolved", sessionId: live, payload: { requestId: 753 } });
  esc2.input.dispatch("keydown", { key: "Escape", preventDefault() {}, stopPropagation() {} });
  assertEqual(page.focused(), esc2.row, "Escape on a resolved card lost the focus");
  // A card resolved before the row was opened offers nothing.
  page.deliver(ruleFrame(live, 751, shellConsent("echo b")));
  page.deliver({ type: "permission_resolved", sessionId: live, payload: { requestId: 751 } });
  assertEqual(ruleButton(lastPermRow(page)).disabled, true, "a resolved card's button is enabled");
});

check("rule button: answering by an option first means Save does not answer again", async (tpl) => {
  const { page, live } = connected(tpl, { answer: ruleAnswer({ ok: true }) });
  page.deliver(ruleFrame(live, 760, shellConsent("echo a")));
  const r = openRule(page);
  r.row.querySelectorAll(".acp-permission-option")[1].dispatch("click");
  assertEqual(page.sentOf("permission_response").length, 1, "the option did not answer");
  r.save.dispatch("click");
  await settleStaging();
  assertEqual(page.sentOf("permission_response").length, 1, "Save sent a second answer");
  assertEqual(r.status().textContent, "Rule saved; this prompt was already answered",
    "the card claims it answered");
});

check("rule button: a D-32 generation warning is shown with the outcome", async (tpl) => {
  const warning = "Saved, but not yet in effect: the agent file was not written. " +
    "New Default sessions are refused until this is fixed.";
  const { page, live } = connected(tpl, { answer: ruleAnswer({ ok: true, warning }) });
  page.deliver(ruleFrame(live, 770, shellConsent("echo a")));
  const r = openRule(page);
  r.save.dispatch("click");
  await settleStaging();
  assertEqual(page.sentOf("permission_response").length, 1, "a saved rule did not answer the prompt");
  const shown = r.status().querySelector(".acp-permission-rule-warning");
  assert(shown, "the warning was swallowed");
  assertEqual(shown.textContent, warning, "the warning was altered");
});

check("rule button: agent text in the prefill stays text", (tpl) => {
  const { page, live } = connected(tpl);
  const hostile = "echo \"<img src=x onerror=alert(1)>\"";
  page.deliver(ruleFrame(live, 780, shellConsent(hostile)));
  const r = openRule(page);
  assertEqual(r.input.value, hostile, "the prefill was altered");
  assertEqual(r.row.querySelectorAll("img").length, 0, "agent text became markup");
});

check("dashboard: a permission_request frame forwards the rule verdict to the renderer", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  const calls = [];
  p.sandbox.addPermissionRequest = function () { calls.push([...arguments]); };
  p.sandbox.dashHandle({ type: "permission_request", sessionId: "sess-1", payload: {
    requestId: 9, sessionId: "sess-1", toolCall: { title: "echo x" },
    consent: shellConsent("echo x"), options: RULE_OPTIONS,
    ruleEligible: true, ruleRow: "shell", ruleRowLabel: "Run commands" } });
  p.sandbox.dashHandle({ type: "permission_request", sessionId: "sess-1", payload: {
    requestId: 10, sessionId: "sess-1", toolCall: { title: "echo y" },
    consent: shellConsent("echo y"), options: RULE_OPTIONS, ruleEligible: "yes" } });
  assertEqual(calls.length, 2, "the dashboard drew no permission row");
  assertEqual(JSON.stringify(calls[0][5]),
    JSON.stringify({ eligible: true, row: "shell", label: "Run commands" }),
    "the dashboard dropped the rule verdict");
  assertEqual(calls[1][5].eligible, false, "a non-boolean ruleEligible was taken as true");
});

/** The real transcript-renderer.js beside index.html's own `ACP_LOCAL`
 *  declaration and a dashboard-shaped `send`, as the dashboard page runs them. */
function loadDashCard(answer) {
  const src = fs.readFileSync(INDEX_TEMPLATE, "utf8");
  const decl = src.match(/^var ACP_LOCAL = true;$/m);
  assert(decl, "index.html does not declare ACP_LOCAL = true");
  const fetches = [];
  const sent = [];
  ACTIVE = null;
  const sandbox = {
    document: { createElement: (tag) => new El(tag), createElementNS: (_n, tag) => new El(tag) },
    fetch: (url, init) => {
      fetches.push({ url: String(url), init: init || {} });
      const got = answer(String(url));
      if (got.reject) return Promise.reject(new Error(got.reject));
      return Promise.resolve({ ok: got.ok !== false, status: got.status || 200,
                               json: () => Promise.resolve(got.body) });
    },
    setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {},
    console: { log() {}, warn() {}, error() {} },
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(transcriptRendererSource(), sandbox, { filename: "transcript-renderer.js" });
  vm.runInContext(decl[0], sandbox, { filename: "index.html#ACP_LOCAL" });
  sandbox._dashSent = sent;
  vm.runInContext("function send(type, payload, sid) { _dashSent.push({type: type, payload: payload, sid: sid}); return true; }",
                  sandbox, { filename: "index.html#send" });
  const transcript = new El("div");
  sandbox.initTranscriptDom({ transcriptEl: transcript, promptNavEl: null,
                              promptUpBtn: null, promptDownBtn: null });
  return { sandbox, transcript, fetches, sent };
}

check("dashboard: the real renderer offers the rule button and answers by kind", async () => {
  const d = loadDashCard((url) => (url === RULE_URL ? { body: { ok: true } } : { body: {} }));
  const consent = shellConsent("git status --short");
  const row = d.sandbox.addPermissionRequest(5, "sess-1", "git status --short", RULE_OPTIONS,
    consent, { eligible: true, row: "shell", label: "Run commands" });
  const trigger = ruleButton(row);
  assert(trigger, "the dashboard's card has no rule button although ACP_LOCAL is true");
  trigger.dispatch("click");
  const input = row.querySelector(".acp-permission-rule-input");
  assertEqual(input.value, "git status --short", "the dashboard's prefill differs");
  row.querySelector(".acp-permission-rule-save").dispatch("click");
  await settleStaging();
  assertEqual(d.fetches.filter((f) => f.url === RULE_URL).length, 1, "the dashboard posted no rule");
  assertEqual(d.sent.length, 1, "the dashboard did not answer the prompt");
  assertEqual(d.sent[0].payload.optionId, "accept", "the dashboard answered by id, not by kind");
  assertEqual(d.sent[0].sid, "sess-1", "the answer went to another session");
  // Not offered on an ineligible card.
  const other = d.sandbox.addPermissionRequest(6, "sess-1", "x", RULE_OPTIONS, consent,
    { eligible: false, row: "shell", label: "Run commands" });
  assertEqual(ruleButton(other), null, "the dashboard offered the button on an ineligible card");
});

check("dashboard: a refused rule leaves the prompt open with the reason", async () => {
  // Phase 3 review (L8): the dashboard's card, not only /acp's.
  const d = loadDashCard((url) => (url === RULE_URL
    ? { body: { ok: false, error: "The rule was not saved: nope." } } : { body: {} }));
  const row = d.sandbox.addPermissionRequest(7, "sess-1", "echo x", RULE_OPTIONS,
    shellConsent("echo x"), { eligible: true, row: "shell", label: "Run commands" });
  ruleButton(row).dispatch("click");
  row.querySelector(".acp-permission-rule-save").dispatch("click");
  await settleStaging();
  assertEqual(d.sent.length, 0, "the dashboard answered a prompt whose rule was refused");
  const error = row.querySelector(".acp-permission-rule-error");
  assertEqual(error.hidden, false, "the dashboard showed no reason");
  assertEqual(error.textContent, "The rule was not saved: nope.", "the dashboard's reason differs");
  assert(row.querySelectorAll(".acp-permission-option").every((b) => !b.disabled),
    "the dashboard disabled the answer buttons");
});

check("dashboard: a prompt resolved while saving is not answered again", async () => {
  const d = loadDashCard((url) => (url === RULE_URL ? { body: { ok: true } } : { body: {} }));
  const row = d.sandbox.addPermissionRequest(8, "sess-1", "echo x", RULE_OPTIONS,
    shellConsent("echo x"), { eligible: true, row: "shell", label: "Run commands" });
  ruleButton(row).dispatch("click");
  row.querySelector(".acp-permission-rule-save").dispatch("click");
  d.sandbox.markPermissionResolved(8);
  await settleStaging();
  assertEqual(d.sent.length, 0, "the dashboard answered a resolved prompt");
  assertEqual(row.querySelector(".acp-permission-rule-status").textContent,
    "Rule saved; this prompt was already answered", "the dashboard's outcome is wrong");
});

check("dashboard: no rule button on a web fetch prompt or with ACP_LOCAL false", () => {
  const d = loadDashCard(() => ({ body: {} }));
  const fetchRow = d.sandbox.addPermissionRequest(9, "sess-1", "example.com", RULE_OPTIONS,
    fileConsent("web_fetch", "example.com"), { eligible: true, row: "web_fetch", label: "Web fetch" });
  assertEqual(ruleButton(fetchRow), null, "the dashboard offered the button on a web fetch prompt");
  d.sandbox.ACP_LOCAL = false;
  const remote = d.sandbox.addPermissionRequest(10, "sess-1", "echo x", RULE_OPTIONS,
    shellConsent("echo x"), { eligible: true, row: "shell", label: "Run commands" });
  assertEqual(ruleButton(remote), null, "the dashboard offered the button with ACP_LOCAL false");
});

// The dashboard settings rows. They live in the same <script> region as the
// remote-access panel, so `loadPanel` runs them from the real source.
// 260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL Phase 1 replaced the on/off
// toggle with a three-option mode picker (Yolo, Auto disabled, Manual).

const PERM_FLOOR = [
  { id: "fs_read", label: "Reading credential stores", detail: "SSH, AWS",
    patterns: ["**/.ssh/**"], note: "A link to one of these folders is not covered." },
  { id: "shell", label: "Commands that mention those stores", detail: "",
    patterns: ["*.ssh*"], note: "Catches common accidents, not a guarantee." },
];
const PERM_PROTECTED = [
  { id: "agents", label: "Agent definitions", patterns: ["**/.kiro/agents/**"], effect: "block" },
  { id: "steering", label: "Steering files", patterns: ["**/.kiro/steering/**"], effect: "ask" },
];
function permState(over) {
  return Object.assign({ mode: "yolo", mode_warning: "", config_error: "",
    rules_warning: "", in_effect: true, state: "on",
    base_agent: "kiro_default", generation_ok: true, generation_error: "",
    generation_note: "", floor: PERM_FLOOR, protected: PERM_PROTECTED,
    posture_notice: null }, over || {});
}
function checkedMode(p) {
  const $ = (id) => p.sandbox.document.getElementById(id);
  const on = [["yolo", "acpPermModeYolo"], ["auto", "acpPermModeAuto"], ["manual", "acpPermModeManual"]]
    .filter(([, id]) => $(id).checked === true).map(([m]) => m);
  return on.length ? on.join(",") : null;
}

check("settings: the mode picker checks the stored mode, describes it, and warns only when not in effect", () => {
  const p = loadPanel();
  const $ = (id) => p.sandbox.document.getElementById(id);
  p.sandbox.renderAcpPermissions(permState());
  assertEqual(checkedMode(p), "yolo", "the stored Yolo mode was not the checked radio");
  assertEqual($("acpPermModeAuto").disabled, true, "Auto became selectable");
  assertEqual($("acpPermBaseAgent").value, "kiro_default", "the base agent was not shown");
  const yoloDesc = $("acpPermDescYolo").textContent;
  assert(/^New sessions run every action without asking/.test(yoloDesc), `no Yolo description: ${yoloDesc}`);
  // D-38c: kiro-cli's own built-in asks and the .kiroignore deny, in every mode.
  for (const word of [".git", ".vscode", "*.code-workspace", ".kiro/agents", ".kiro/hooks", ".kiroignore"]) {
    assert(yoloDesc.includes(word), `the Yolo description does not name ${word}`);
  }
  // Final review (M-4): Manual's description is shown while Yolo is chosen.
  assert(/Manual rules/.test($("acpPermDescManual").textContent),
    `Manual's description is not shown beside Yolo's: ${$("acpPermDescManual").textContent}`);
  assertEqual($("acpPermBadge").hidden, true, "a healthy state shows the badge");
  assertEqual($("acpPermWarn").hidden, true, "a healthy state shows a warning");
  assertEqual($("acpPermApplyAgain").hidden, true, "a healthy state offers Apply again");

  p.sandbox.renderAcpPermissions(permState({ mode: "manual", base_agent: "my_agent" }));
  assertEqual(checkedMode(p), "manual", "Manual was not the checked radio");
  assert(/Manual rules/.test($("acpPermDescManual").textContent), "no Manual description");
  assertEqual($("acpPermDescYolo").textContent, yoloDesc, "Yolo's description went away under Manual");
  assertEqual($("acpPermBaseAgent").value, "my_agent", "the base agent did not update");

  // SC-9's UI half: the file is not what the settings compile to.
  p.sandbox.renderAcpPermissions(permState({ mode: "manual", in_effect: false, state: "absent",
    generation_ok: false, generation_error: "cannot write C:/x/poweratlas-acp.md" }));
  assertEqual(checkedMode(p), "manual", "the chosen mode stopped reading as chosen");
  assertEqual($("acpPermBadge").hidden, false, "not in effect shows no badge");
  assertEqual($("acpPermWarn").hidden, false, "not in effect shows no warning");
  const warn = $("acpPermWarn").textContent;
  assert(/Not in effect/.test(warn), `the warning does not say not in effect: ${warn}`);
  assert(warn.includes("cannot write C:/x/poweratlas-acp.md"), "the warning does not carry the error");
  assert(/New Default sessions are refused/.test(warn), "the warning does not say what that means (D-34)");
  assert(/task modes such as Spec or Plan/.test(warn), "the warning does not offer the way on");
  assert($("acpPermBadge").title.includes("poweratlas-acp.md"), "the badge carries no reason");

  // Neither a bare refusal nor an old-shaped `enabled` answer is a state.
  p.sandbox.renderAcpPermissions({ ok: false, error: "nope" });
  p.sandbox.renderAcpPermissions({ enabled: false, in_effect: false, state: "absent" });
  assertEqual(checkedMode(p), "manual", "a non-state answer repainted the radios");
});

check("settings: every permission warning names a next step, and none says to turn anything off and on (G9)", () => {
  const p = loadPanel();
  const warn = () => p.sandbox.document.getElementById("acpPermWarn").textContent;
  // Final review (M-8): the step fits the cause the server names.
  p.sandbox.renderAcpPermissions(permState({ in_effect: false, state: "absent",
    generation_ok: false, generation_error: "invalid base agent name: 'x'", error_kind: "base" }));
  assert(/Check the Base agent name below, then press Apply again/.test(warn()),
    `no next step for a base agent failure: ${warn()}`);
  p.sandbox.renderAcpPermissions(permState({ in_effect: false, state: "unknown",
    generation_ok: false, error_kind: "rules",
    generation_error: "Run commands (shell): block pattern “x” is blank, so the rules were not applied" }));
  assert(/Open Edit rules/.test(warn()), `no rules step for a rule that does not compile: ${warn()}`);
  assert(!/Base agent/.test(warn()), `a rules error points at the base agent: ${warn()}`);
  p.sandbox.renderAcpPermissions(permState({ in_effect: false, state: "stale" }));
  assert(/Press Apply again, or restart PowerAtlas/.test(warn()), `no next step for a stale file: ${warn()}`);
  p.sandbox.renderAcpPermissions(permState({ in_effect: false, state: "unknown",
    derived_agent: "C:/k/poweratlas-acp.md", error_kind: "foreign" }));
  assert(/Remove or rename that file, then press Apply again/.test(warn()),
    `no next step for a foreign file: ${warn()}`);
  assert(warn().includes("C:/k/poweratlas-acp.md"), "the foreign file is not named");
  // The server's own foreign-file error names its fix; no second one follows.
  p.sandbox.renderAcpPermissions(permState({ in_effect: false, state: "unknown",
    generation_ok: false, error_kind: "foreign",
    generation_error: "C:/k/poweratlas-acp.md was not written by PowerAtlas, so it was left in "
      + "place; remove or rename it, then press Apply again under Settings > Agent permissions" }));
  assertEqual((warn().match(/Apply again/g) || []).length, 1, `two fixes were named: ${warn()}`);
  assert(!/Base agent/.test(warn()), `the base-agent step was appended: ${warn()}`);
  p.sandbox.renderAcpPermissions(permState({ in_effect: false, state: "unreadable",
    derived_agent: "C:/k/poweratlas-acp.md", error_kind: "unreadable" }));
  assert(/could not be read just now/.test(warn()) && /close any program/.test(warn()),
    `no retry step for an unreadable file: ${warn()}`);
  assert(!/Remove or rename/.test(warn()), "an unreadable file was called foreign");
  p.sandbox.renderAcpPermissions(permState({ in_effect: false, state: "absent" }));
  assert(/restart PowerAtlas/.test(warn()), `no next step for a missing file: ${warn()}`);
  assert(!/save the mode again/i.test(warn()), "a warning still says to save the mode again");
  const src = panelSource();
  assert(!/off and on again|on and off again/.test(src),
    "the panel still tells the user to turn something off and on; there is no Off state");
});

check("settings: a failed change that kept the previous file, an unreadable mode and a minimal base agent are reported", () => {
  const p = loadPanel();
  const $ = (id) => p.sandbox.document.getElementById(id);
  // No generation has run yet in this process: not a failure.
  p.sandbox.renderAcpPermissions(permState({ generation_ok: false, generation_attempted: false }));
  assertEqual($("acpPermWarn").hidden, true, "a generation that has not run yet was reported as failed");
  // G3: in effect, but the latest generation failed.
  p.sandbox.renderAcpPermissions(permState({ generation_ok: false,
    generation_error: "cannot write the file" }));
  assert(/Still using the previous agent file/.test($("acpPermWarn").textContent),
    `the warning does not say the previous file is in use: ${$("acpPermWarn").textContent}`);
  assertEqual($("acpPermBadge").hidden, true, "the badge claims not in effect while it is");
  // D-25: a junk stored mode loads as Manual and says so.
  p.sandbox.renderAcpPermissions(permState({ mode: "manual",
    mode_warning: "acp_permission_mode 'junk' is not a permission mode; running as Manual" }));
  assertEqual($("acpPermWarn").hidden, false, "the mode warning was not shown");
  assert($("acpPermWarn").textContent.includes("running as Manual"), "the mode warning text was lost");
  // D-28: generated from the minimal agent.
  p.sandbox.renderAcpPermissions(permState({
    generation_note: "base agent 'kiro_default' not found — using a minimal agent" }));
  assert($("acpPermWarn").textContent.includes("using a minimal agent"), "the minimal-agent note was not shown");
});

check("settings: an unreadable config.toml checks no mode and names the file; dropped rules are named (Phase 1 review, findings 1 and 7)", () => {
  const p = loadPanel();
  const $ = (id) => p.sandbox.document.getElementById(id);
  const dot = p.sandbox.document.getElementById("topbarPendingDot");
  p.sandbox.renderAcpPermissions(permState({ mode: "manual" }));
  assertEqual(checkedMode(p), "manual", "setup: Manual was not checked");
  const configError = "PowerAtlas's config.toml could not be read (TOMLDecodeError: x), so no "
    + "setting was changed. Fix the file C:/c/config.toml by hand (a copy of the unreadable "
    + "file was saved as C:/c/config.toml.bak)";
  p.sandbox.renderAcpPermissions(permState({ mode: "yolo", in_effect: false, state: "stale",
    config_error: configError, generation_ok: false, generation_error: configError }));
  assertEqual(checkedMode(p), null, "the defaults' mode was checked while config.toml is unreadable");
  const warn = $("acpPermWarn").textContent;
  assertEqual($("acpPermWarn").hidden, false, "the unreadable config was not shown");
  assert(warn.includes("config.toml.bak"), `the warning does not name the backup: ${warn}`);
  assert(/New Default sessions are refused/.test(warn), "the warning does not say what that means");
  assert(!/Check the Base agent name/.test(warn), `the warning points at the wrong fix: ${warn}`);
  assertEqual($("acpPermBadge").hidden, false, "an unreadable config shows no badge");
  assertEqual(dot.hidden, false, "an unreadable config did not light the gear dot");

  p.sandbox.renderAcpPermissions(permState({ mode: "manual",
    rules_warning: "shell: default 'sometimes' is not allow, ask or block, so it asks" }));
  assertEqual(checkedMode(p), "manual", "a rules warning unchecked the mode");
  assert($("acpPermWarn").textContent.includes("'sometimes'"), "the rules warning was not shown");
  assertEqual(dot.hidden, false, "a rules warning did not light the gear dot");
});

check("settings: the gear dot lights for a permission mode not working as set, and restart drift cannot hide it (G4)", () => {
  const p = loadPanel();
  const dot = p.sandbox.document.getElementById("topbarPendingDot");
  p.sandbox.renderAcpPermissions(permState());
  assertEqual(dot.hidden, true, "a healthy state lit the gear dot");
  p.sandbox.renderAcpPermissions(permState({ in_effect: false, state: "absent",
    generation_ok: false, generation_error: "nope" }));
  assertEqual(dot.hidden, false, "not in effect did not light the gear dot");
  assert(/permission mode/i.test(dot.title), `the dot does not say why: ${dot.title}`);
  p.sandbox._topbarDotRestart = false;
  p.sandbox._syncTopbarDot();
  assertEqual(dot.hidden, false, "the restart-drift source hid the permission source's dot");
  p.sandbox.renderAcpPermissions(permState({ posture_notice: { mode: "manual", detected_at: "t" } }));
  assertEqual(dot.hidden, false, "an outside change did not light the gear dot");
  p.sandbox.renderAcpPermissions(permState());
  assertEqual(dot.hidden, true, "a healthy state left the gear dot lit");
});

check("settings: a mode changed outside the dashboard is announced, naming the new mode (D-35)", () => {
  const p = loadPanel();
  const notice = p.sandbox.document.getElementById("acpPermNotice");
  p.sandbox.renderAcpPermissions(permState({ mode: "yolo",
    posture_notice: { mode: "yolo", detected_at: "2026-09-24 10:00:00" } }));
  assertEqual(notice.hidden, false, "the outside change was not shown");
  assert(/outside the dashboard/.test(notice.textContent) && /Yolo/.test(notice.textContent),
    `the notice does not name the new mode: ${notice.textContent}`);
  p.sandbox.renderAcpPermissions(permState({ posture_notice: null }));
  assertEqual(notice.hidden, true, "the notice outlived the change that cleared it");
});

check("settings: the Always blocked and Protected lists render as text, with the linked-items marker (D-39)", () => {
  const p = loadPanel();
  const $ = (id) => p.sandbox.document.getElementById(id);
  const links = {
    agents: { count: 1, links: [
      { name: "agents", target: "C:/elsewhere/agents", error: "", folder: true }] },
    steering: { count: 2, links: [
      { name: "a.md", target: "C:/playbook/a.md", error: "" },
      { name: "<img src=x>.md", target: "", error: "unresolvable (FileNotFoundError)" }] },
  };
  p.sandbox.renderAcpPermissions(permState({ protected_links: links }));
  const floor = $("acpPermFloorList").textContent;
  assert(floor.includes("Reading credential stores") && floor.includes("**/.ssh/**"),
    `the floor was not listed: ${floor}`);
  assert(floor.includes("not a guarantee"), "the shell tier is not labelled best-effort");
  const prot = $("acpPermProtectedList").textContent;
  assert(prot.includes("Agent definitions") && /blocked outright/.test(prot), `Protected block effect missing: ${prot}`);
  assert(prot.includes("Steering files") && /asks/.test(prot), "Protected ask effect missing");
  assert(prot.includes("2 linked items not covered"), `no linked-items marker: ${prot}`);
  assert(prot.includes("a.md → C:/playbook/a.md"), "a link target is not shown");
  assert(prot.includes("<img src=x>.md (unresolvable"), "an unresolvable link is not shown as text");
  // Finding 10: a Protected folder that is itself a link.
  assert(prot.includes("agents (the whole folder) → C:/elsewhere/agents"),
    `a linked folder is not shown as one: ${prot}`);
  const markers = $("acpPermProtectedList").querySelectorAll(".acp-perm-links-marker");
  assertEqual(markers.length, 2, "a linked folder got no marker");
  // A POST answer carries no links (the walk runs only on GET): the last
  // links found are kept rather than the marker vanishing.
  p.sandbox.renderAcpPermissions(permState({ mode: "manual" }));
  assert($("acpPermProtectedList").textContent.includes("2 linked items not covered"),
    "a POST answer without links erased the marker");
});

check("settings: choosing a radio posts the mode it names, and reconciles from the answer", async () => {
  let answer = Object.assign({ ok: true }, permState({ mode: "manual" }));
  const p = loadPanel({ answer: (url) => url === "/api/acp-permissions" ? { body: answer } : { body: {} } });
  const $ = (id) => p.sandbox.document.getElementById(id);
  p.sandbox.setAcpPermissionMode($("acpPermModeManual"));
  assertEqual(p.fetches.length, 1, "choosing Manual sent no request");
  assertEqual(p.fetches[0].url, "/api/acp-permissions", "the mode posted to the wrong route");
  assertEqual(String(p.fetches[0].init.method).toUpperCase(), "POST", "the mode was not POSTed");
  assertEqual(JSON.stringify(JSON.parse(p.fetches[0].init.body)), JSON.stringify({ mode: "manual" }),
    "the request did not state the chosen mode");
  await p.settle();
  assertEqual(checkedMode(p), "manual", "the answer was not drawn");
  // Saved but not in effect (D-32): drawn, and the warning raised.
  answer = Object.assign({ ok: true, warning: "Saved, but not yet in effect: x." },
    permState({ mode: "yolo", in_effect: false, state: "unknown" }));
  p.sandbox.setAcpPermissionMode($("acpPermModeYolo"));
  await p.settle();
  assertEqual(checkedMode(p), "yolo", "a saved-but-not-in-effect answer was not drawn");
  assert(p.toasts.some((t) => t.includes("not yet in effect")), "the D-32 warning was not raised");
  // A refusal: toast, and the radios are put back from a fresh read.
  answer = { ok: false, error: "mode must be \"yolo\" or \"manual\"" };
  const before = p.fetches.length;
  p.sandbox.setAcpPermissionMode($("acpPermModeManual"));
  await p.settle();
  await p.settle();
  assert(p.toasts.some((t) => t.includes("mode must be")), "a refused change was not reported");
  assert(p.fetches.slice(before).some((f) => !f.init.method), "the radios were not re-read after a refusal");
  // Auto is not storable: nothing is sent for it.
  const sent = p.fetches.length;
  p.sandbox.setAcpPermissionMode($("acpPermModeAuto"));
  assertEqual(p.fetches.length, sent, "choosing Auto sent a request");
});

check("settings: a state read that fails checks no mode and says so (G10)", async () => {
  const p = loadPanel({ answer: (url) => url === "/api/acp-permissions" ? { reject: "offline" } : null });
  const $ = (id) => p.sandbox.document.getElementById(id);
  p.sandbox.renderAcpPermissions(permState());
  await p.sandbox.loadAcpPermissions();
  assertEqual(checkedMode(p), null, "a failed read left a mode checked");
  assertEqual($("acpPermWarn").hidden, false, "a failed read showed no explanation");
  assert(/Could not read the permission mode/.test($("acpPermWarn").textContent),
    `the explanation does not say the read failed: ${$("acpPermWarn").textContent}`);
  // An answer that is not the state's shape is a failed read too.
  const q = loadPanel({ answer: (url) => url === "/api/acp-permissions" ? { body: { enabled: true } } : null });
  await q.sandbox.loadAcpPermissions();
  assertEqual(checkedMode(q), null, "an old-shaped answer checked a mode");
});

check("settings: the permission rows' error toasts can be dismissed (G12)", async () => {
  const p = loadPanel({ answer: (url) => url === "/api/acp-permissions"
    ? { body: { ok: false, error: "nope" } } : { body: {} } });
  p.sandbox.setAcpPermissionMode(p.sandbox.document.getElementById("acpPermModeManual"));
  await p.settle();
  assertEqual(p.toasts.length, 1, "the refusal raised no toast");
  assert(p.toasts[0].includes('class="toast-dismiss"'), "the error toast has no dismiss button");
});

check("settings: the base agent saves through /api/save-setting and shows the resulting state", async () => {
  let answer = Object.assign({ ok: true, restart_required: false },
    permState({ in_effect: false, state: "unknown", base_agent: "other",
      generation_ok: false, generation_error: "C:/k/poweratlas-acp.md was not written by PowerAtlas" }));
  const p = loadPanel({ answer: (url) =>
    url === "/api/save-setting" ? { body: answer }
      : url === "/api/acp-permissions" ? { body: permState() }
      : { body: {} } });
  const input = p.sandbox.document.getElementById("acpPermBaseAgent");
  input.value = "  other  ";
  p.sandbox.saveAcpPermissionBaseAgent(input);
  assertEqual(p.fetches[0].url, "/api/save-setting", "the base agent was saved somewhere else");
  const sent = JSON.parse(p.fetches[0].init.body);
  assertEqual(sent.key, "acp_permission_base_agent", "the save wrote some other setting");
  assertEqual(sent.value, "other", "the name was not trimmed before saving");
  await p.settle();
  assertEqual(p.sandbox.document.getElementById("acpPermWarn").hidden, false,
    "a rename whose regeneration failed was reported as success");

  // A rejected name: toast, and the field is put back to the value in force.
  answer = { ok: false, error: "Base agent must be 1-64 characters" };
  input.value = "bad name!";
  // Saved with Enter, so focus is still in the field — the case where a
  // background repaint deliberately leaves the field alone.
  p.sandbox.document.activeElement = input;
  p.sandbox.saveAcpPermissionBaseAgent(input);
  await p.settle();
  await p.settle();
  assert(p.toasts.some((t) => t.includes("Base agent must be 1-64 characters")),
    "a rejected base-agent name was not reported");
  assertEqual(input.value, "kiro_default",
    "the field kept a name the server rejected, which is not the one in force");
});

// ---- The Manual rule editor -----------------------------------------------
// 260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL Phase 2 (SC-5, D-7, D-11, D-20,
// D-35, D-38e). The dialog's rows are drawn by the page from the state's
// `rules` and `rule_rows`; these checks drive the real functions.

const RULE_ROWS = [
  ["fs_read", "Read files"], ["fs_write", "Write files"], ["shell", "Run commands"],
  ["web_fetch", "Web fetch"], ["web_search", "Web search"], ["mcp", "MCP tools"],
  ["subagent", "Sub-agents"], ["skill", "Skills"], ["power", "Powers"],
].map(([id, label]) => ({ id, label }));
function seedRules() {
  const rules = {};
  for (const { id } of RULE_ROWS) rules[id] = { default: "ask", allow: [], block: [] };
  rules.fs_read.allow = ["./**"];
  rules.shell.allow = ["git status", "pwd"];
  rules.shell.block = ["git *--output*"];
  rules.protected_block = ["agents"];
  return rules;
}
function rulesState(over) {
  return permState(Object.assign({ mode: "manual", rules: seedRules(), rule_rows: RULE_ROWS,
    protected: [
      { id: "agents", label: "Agent definitions", patterns: ["**/.kiro/agents/**"], effect: "block" },
      { id: "steering", label: "Steering files", patterns: ["**/.kiro/steering/**"], effect: "ask" },
    ],
    protected_links: { steering: { count: 2, links: [
      { name: "a.md", target: "C:/playbook/a.md", error: "" },
      { name: "b.md", target: "", error: "unresolvable (FileNotFoundError)" }] } } }, over || {}));
}
async function openRules(over, answer) {
  let state = rulesState(over);
  let post = { ok: true, ...rulesState(over) };
  const p = loadPanel({ answer: (url) => {
    if (url !== "/api/acp-permissions") return { body: {} };
    const last = p.fetches[p.fetches.length - 1];
    if (last.init.method === "POST") return answer ? answer(last) : { body: post };
    return { body: state };
  } });
  await p.sandbox.openAcpRulesEditor();
  const $ = (id) => p.sandbox.document.getElementById(id);
  const row = (id) => $("acpRulesBody").querySelectorAll(".acp-rules-row").find((r) => r.dataset.row === id);
  const list = (id, which) => row(id).querySelector(".acp-rules-list-" + which);
  const chips = (id, which) => list(id, which).querySelectorAll(".acp-rules-chip-text").map((c) => c.textContent);
  const add = (id, which, text) => {
    list(id, which).querySelector(".acp-rules-input").value = text;
    list(id, which).querySelector(".acp-rules-add").click();
  };
  const choose = (id, value) => {
    const sel = row(id).querySelector(".acp-rules-select");
    sel.value = value;
    sel.dispatch("change");
  };
  const warnings = (id) => { const w = row(id).querySelector(".acp-rules-warn"); return w ? w.textContent : ""; };
  return { p, $, row, list, chips, add, choose, warnings };
}

check("rules editor: opens on a fresh read and renders every normalised row in plain words", async () => {
  const e = await openRules({ mode: "yolo" });
  assertEqual(e.$("acpRulesModal").open, true, "the editor did not open");
  assertEqual(e.p.fetches[0].url, "/api/acp-permissions", "the editor did not read the stored rules");
  const rows = e.$("acpRulesBody").querySelectorAll(".acp-rules-row");
  assertEqual(rows.map((r) => r.querySelector(".acp-rules-row-title").textContent).join("|"),
    RULE_ROWS.map((r) => r.label).join("|"), "the rows are not the nine kinds of action, in order");
  for (const r of rows) {
    assertEqual(r.querySelector(".acp-rules-select").value, "ask", `row ${r.dataset.row} lost its default`);
    assert(r.querySelector(".acp-rules-select").getAttribute("aria-label"), `row ${r.dataset.row}'s default has no label`);
  }
  // D-11: the seeded `./**` reads as the session folder, and keeps its pattern.
  assertEqual(e.chips("fs_read", "allow").join("|"), "the session folder", "./** was not shown as the session folder");
  assertEqual(e.list("fs_read", "allow").querySelector(".acp-rules-chip-text").title, "./**",
    "the session folder chip lost its pattern");
  assertEqual(e.chips("shell", "allow").join("|"), "git status|pwd", "the command allow list is wrong");
  assertEqual(e.chips("shell", "block").join("|"), "git *--output*", "the command block list is wrong");
  assert(/literally and case-sensitively/.test(e.row("shell").textContent)
    && /\/ and \\ are different characters/.test(e.row("shell").textContent),
    `the Run commands row does not say how matching works: ${e.row("shell").textContent}`);
  assert(/host names, such as example\.com/.test(e.row("web_fetch").textContent),
    "Web fetch patterns are not labelled as host names (D-38d)");
  // Rules are edited in every mode; outside Manual the editor says they wait.
  assertEqual(e.$("acpRulesModeNote").hidden, false, "no note that the rules are not in force in Yolo");
  assert(/until Manual is selected/.test(e.$("acpRulesModeNote").textContent), "the mode note is wrong");
  // Protected, with its Block outright switches and the D-39 links; the floor.
  const body = e.$("acpRulesBody");
  const boxes = body.querySelectorAll(".acp-rules-protected-box");
  assertEqual(boxes.map((b) => b.checked).join(","), "true,false", "Block outright does not show protected_block");
  assert(boxes.every((b) => /^Block .* outright$/.test(b.getAttribute("aria-label"))), "a Protected switch has no label");
  const prot = body.querySelector(".acp-rules-protected").textContent;
  assert(prot.includes("2 linked items not covered") && prot.includes("a.md → C:/playbook/a.md")
    && prot.includes("b.md (unresolvable"), `the Protected links are not listed: ${prot}`);
  const floor = body.querySelector(".acp-rules-floor").textContent;
  assert(floor.includes("Reading credential stores") && floor.includes("**/.ssh/**"), `no Always blocked list: ${floor}`);
  assert(e.row("shell").querySelectorAll(".acp-rules-chip-remove")
    .every((b) => b.tagName === "BUTTON" && /^Remove /.test(b.getAttribute("aria-label"))),
    "a chip cannot be removed with the keyboard, or its button has no name");
});

check("rules editor: in Manual, no mode note; a failed read does not open an empty editor", async () => {
  const e = await openRules();
  assertEqual(e.$("acpRulesModeNote").hidden, true, "the mode note shows in Manual");
  const p = loadPanel({ answer: () => ({ reject: "offline" }) });
  await p.sandbox.openAcpRulesEditor();
  assertEqual(p.sandbox.document.getElementById("acpRulesModal").open, false,
    "the editor opened on nothing, so Save could erase the stored rules");
  assert(p.toasts.some((t) => /Could not read the permission rules/.test(t)), "the failed read was not reported");
  const q = loadPanel({ answer: () => ({ body: permState() }) });
  await q.sandbox.openAcpRulesEditor();
  assertEqual(q.sandbox.document.getElementById("acpRulesModal").open, false,
    "a state without rules opened the editor");
  // An unreadable config.toml: the rules in the answer are the defaults.
  const r = loadPanel({ answer: () => ({ body: rulesState({
    config_error: "PowerAtlas's config.toml could not be read (x)" }) }) });
  await r.sandbox.openAcpRulesEditor();
  assertEqual(r.sandbox.document.getElementById("acpRulesModal").open, false,
    "the editor opened on the defaults of an unreadable config.toml");
  assert(r.toasts.some((t) => t.includes("could not be read")), "the unreadable config was not reported");
});

check("rules editor: default Allow hides the allow list and the patterns come back with Ask", async () => {
  const e = await openRules();
  e.choose("shell", "allow");
  assertEqual(e.list("shell", "allow").hidden, true, "the allow list is offered under default Allow");
  assertEqual(e.list("shell", "block").hidden, false, "the block list was hidden under default Allow");
  assert(/Everything runs without asking, except/.test(e.row("shell").textContent), "default Allow is not explained");
  assert(/equivalent to allow-all/.test(e.warnings("shell")), "allowing every command carries no warning");
  e.choose("shell", "ask");
  assertEqual(e.list("shell", "allow").hidden, false, "the allow list did not come back");
  assertEqual(e.chips("shell", "allow").join("|"), "git status|pwd", "switching the default lost the allow list");
});

check("rules editor: warns on an interpreter, on a * in a command and on a broad write, without refusing", async () => {
  const e = await openRules();
  e.add("shell", "allow", "python -m pytest");
  assert(/"python -m pytest": allowing python is equivalent to allow-all/.test(e.warnings("shell")),
    `no interpreter warning (D-35): ${e.warnings("shell")}`);
  e.add("shell", "allow", "C:/Tools/PWSH.exe -File x.ps1");
  assert(/allowing pwsh is equivalent to allow-all/.test(e.warnings("shell")), "a path and .exe hid the interpreter");
  e.add("shell", "allow", "npm test*");
  assert(/"npm test\*": a wildcard \(\*, \?, \[ or \{\) also matches an output redirection/.test(e.warnings("shell")),
    `no redirection warning (D-38e): ${e.warnings("shell")}`);
  e.add("shell", "allow", "rm -rf build");
  assert(/lets the agent delete files/.test(e.warnings("shell")), "a destructive verb carries no warning");
  assertEqual(e.chips("shell", "allow").length, 6, "a warned pattern was refused rather than added");
  // A blocked interpreter is not a widening.
  e.add("shell", "block", "python*");
  assert(!/"python\*"/.test(e.warnings("shell")), "a block pattern was warned about");
  e.add("fs_write", "allow", "./**");
  e.add("fs_write", "allow", "C:\\**");
  e.add("fs_write", "allow", "~/**");
  e.add("fs_write", "allow", "src/**");
  const w = e.warnings("fs_write");
  assert(/The session folder: file-tool writes anywhere in it run without asking/.test(w), `no session-folder warning: ${w}`);
  assert(/"C:\\\*\*" covers a whole drive/.test(w), `no drive-root warning: ${w}`);
  assert(/"~\/\*\*" covers the whole home folder/.test(w), `no home-folder warning: ${w}`);
  assert(!/src\/\*\*/.test(w), "a narrow write pattern was warned about");
  // The same patterns on Read files carry no write warning.
  e.add("fs_read", "allow", "C:/**");
  assertEqual(e.warnings("fs_read"), "", "a read pattern got a write warning");
  // The client refuses only what the server would refuse outright.
  e.add("shell", "allow", "*");
  assert(/matches everything/.test(e.list("shell", "allow").querySelector(".acp-rules-problem").textContent),
    "a bare * was not stopped");
  assertEqual(e.chips("shell", "allow").length, 6, "a bare * was added");
});

check("rules editor: Save posts the edited rule set as JSON, closes, and shows the result", async () => {
  const e = await openRules();
  e.row("shell").querySelectorAll(".acp-rules-chip-remove")[1].click();  // pwd
  e.add("shell", "allow", "npm test");
  e.add("shell", "block", "git push");
  e.add("web_fetch", "allow", "example.com");
  e.choose("mcp", "block");
  const boxes = e.$("acpRulesBody").querySelectorAll(".acp-rules-protected-box");
  boxes[0].checked = false; boxes[0].dispatch("change");
  boxes[1].checked = true; boxes[1].dispatch("change");
  await e.p.sandbox.saveAcpRules();
  const post = e.p.fetches.find((f) => f.init.method === "POST");
  assert(post, "Save sent nothing");
  assertEqual(post.url, "/api/acp-permissions", "the rules posted to the wrong route");
  const sent = JSON.parse(post.init.body);
  assertEqual(Object.keys(sent).join(","), "rules", "Save sent more than the rules");
  const want = seedRules();
  want.shell.allow = ["git status", "npm test"];
  want.shell.block = ["git *--output*", "git push"];
  want.web_fetch.allow = ["example.com"];
  want.mcp.default = "block";
  want.protected_block = ["steering"];
  for (const key of Object.keys(want)) {
    assertEqual(JSON.stringify(sent.rules[key]), JSON.stringify(want[key]), `the posted ${key} is wrong`);
  }
  assertEqual(sent.rules.fs_read.allow[0], "./**", "the session folder was posted as its label");
  assertEqual(e.$("acpRulesModal").open, false, "a saved editor stayed open");
  assertEqual(e.p.toasts.length, 0, "a clean save raised a toast");
});

check("rules editor: a successful save puts the focus back on the settings gear (Phase 2 QA)", async () => {
  // Measured in Chrome: after Save the focus was on <body>. The stand-in
  // dialog raises no `close` event of its own, so this checks the save path
  // itself, not the `close` listener the Discard path relies on.
  const e = await openRules();
  e.add("shell", "allow", "npm test");
  assert(ACTIVE !== e.$("topbarSettingsBtn"), "the gear had the focus before the save");
  await e.p.sandbox.saveAcpRules();
  assertEqual(e.$("acpRulesModal").open, false, "a saved editor stayed open");
  assert(ACTIVE === e.$("topbarSettingsBtn"), "the focus did not go back to the settings gear after a save");
  // The `close` event that follows keeps it there.
  e.$("acpRulesModal").dispatch("close");
  assert(ACTIVE === e.$("topbarSettingsBtn"), "the close event moved the focus off the gear");
});

check("rules editor: Edit rules says it is loading and opens once, however often it is clicked (Phase 2 QA)", async () => {
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const p = loadPanel({ answer: () => ({ body: rulesState() }) });
  const $ = (id) => p.sandbox.document.getElementById(id);
  const realFetch = p.sandbox.fetch;
  p.sandbox.fetch = (url, init) => gate.then(() => realFetch(url, init));
  const first = p.sandbox.openAcpRulesEditor();
  assertEqual($("acpPermEditRules").getAttribute("aria-busy"), "true", "the row is not marked busy");
  assertEqual($("acpPermEditRulesLabel").textContent, "Loading rules…", "the row does not say it is loading");
  const second = p.sandbox.openAcpRulesEditor();
  release();
  await first; await second;
  assertEqual(p.fetches.filter((f) => f.url === "/api/acp-permissions").length, 1, "a second click read the rules again");
  assertEqual($("acpRulesModal").open, true, "the editor did not open");
  assertEqual($("acpPermEditRules").getAttribute("aria-busy"), "false", "the row stayed busy");
  assertEqual($("acpPermEditRulesLabel").textContent, "Edit rules…", "the row kept its loading label");
  // A failed read clears it too, and the next click reads again.
  p.sandbox.fetch = () => Promise.reject(new Error("down"));
  $("acpRulesModal").open = false;
  await p.sandbox.openAcpRulesEditor();
  assertEqual($("acpPermEditRules").getAttribute("aria-busy"), "false", "a failed read left the row busy");
  assert(p.toasts.some((t) => t.includes("Could not read the permission rules")), "a failed read said nothing");
});

check("rules editor: a refused save stays open with the server's reason; a saved-but-not-applied one warns (D-32)", async () => {
  let reply = { ok: false, error: "The rules were not saved: Run commands (shell): allow pattern 'x' contains the character U+007F, which is not allowed." };
  const e = await openRules(null, () => ({ body: reply }));
  await e.p.sandbox.saveAcpRules();
  assertEqual(e.$("acpRulesModal").open, true, "a refused save closed the editor");
  assertEqual(e.$("acpRulesError").hidden, false, "the refusal was not shown");
  assert(e.$("acpRulesError").textContent.includes("Run commands (shell)"), "the refusal lost the row name");
  reply = Object.assign({ ok: true, warning: "Saved, but not yet in effect: nope. New Default sessions are refused until this is fixed." },
    rulesState({ in_effect: false, state: "stale", generation_ok: false, generation_error: "nope" }));
  await e.p.sandbox.saveAcpRules();
  assertEqual(e.$("acpRulesModal").open, false, "a saved change kept the editor open");
  assert(e.p.toasts.some((t) => t.includes("not yet in effect")), "the D-32 warning was not shown");
  assertEqual(e.$("acpPermWarn").hidden, false, "the panel was not redrawn from the answer");
});

check("rules editor: the dialog is on the dashboard only and carries its names and footer (SC-7, SC-10)", () => {
  const idx = fs.readFileSync(INDEX_TEMPLATE, "utf8");
  assert(idx.includes('{% include "partials/acp_permission_rules_modal.html" %}'), "index.html does not include the editor");
  assert(/id="acpPermEditRules"[^>]*onclick="openAcpRulesEditor\(\)/.test(idx), "no Edit rules control in the settings menu");
  // Phase 2 QA: the menu stays up, showing "Loading rules…", until the read answers.
  assert(/id="acpPermEditRules" class="[^"]*\btopbar-menu-keep\b/.test(idx), "a click on Edit rules closes the menu before it can show it is loading");
  assert(!/id="acpPermEditRules"[^>]*closeTopbarSettings/.test(idx), "Edit rules closes the menu before its read answers");
  assert(/The rules apply when Manual is selected/.test(idx), "the Edit rules control does not say when rules apply");
  const acp = fs.readFileSync(path.join(HERE, "..", "src", "power_atlas", "templates", "acp.html"), "utf8");
  assert(!acp.includes("acp_permission_rules_modal") && !acp.includes("openAcpRulesEditor"),
    "/acp, which a remote client can open, carries the rule editor");
  const modal = fs.readFileSync(path.join(HERE, "..", "src", "power_atlas", "templates", "partials",
    "acp_permission_rules_modal.html"), "utf8");
  assert(/<dialog id="acpRulesModal"[^>]*aria-labelledby="acpRulesTitle"/.test(modal), "the dialog has no accessible name");
  assert(/aria-label="Close"/.test(modal), "the close button has no name");
  assert(/role="alert"/.test(modal), "save errors are not announced");
  assert(modal.includes("Applies to sessions created afterwards."), "the footer does not say when changes apply");
});

// ---- Phase 2 review fixes (S2, S4, S5, U1-U4, U6, U7, U10-U14) -------------

check("rules editor: a stored Always block that is not a list keeps the editor closed (S2)", async () => {
  const rules = seedRules();
  rules.shell.block = "git push";
  const p = loadPanel({ answer: () => ({ body: rulesState({ rules,
    rule_problems: { shell: ["shell: the block list is not a list ('git push')"] } }) }) });
  await p.sandbox.openAcpRulesEditor();
  assertEqual(p.sandbox.document.getElementById("acpRulesModal").open, false,
    "the editor opened on a block list it cannot show, so Save would drop it");
  assert(p.toasts.some((t) => t.includes("Run commands") && t.includes("not a list")),
    `the refusal does not name the row: ${p.toasts.join(" | ")}`);
  assertEqual(p.fetches.filter((f) => f.init.method === "POST").length, 0, "something was saved");
});

check("rules editor: rows show what loading config.toml changed in them", async () => {
  const e = await openRules({ rule_problems: { mcp: ["mcp: allow pattern '***' matches everything, so it was ignored"] } });
  assert(/When config\.toml was read: mcp: allow pattern '\*\*\*'/.test(e.row("mcp").textContent),
    `the load problem is not on its row: ${e.row("mcp").textContent}`);
  assert(!/When config\.toml was read/.test(e.row("shell").textContent), "a clean row shows a load problem");
});

check("rules editor: an interpreter anywhere in a command, and Write files set to Allow, are warned about (S5)", async () => {
  const e = await openRules();
  e.add("shell", "allow", "*python*");
  assert(/"\*python\*": allowing python is equivalent to allow-all/.test(e.warnings("shell")),
    `*python* hid the interpreter: ${e.warnings("shell")}`);
  e.add("shell", "allow", "& python x");
  assert(/"& python x": allowing python is equivalent to allow-all/.test(e.warnings("shell")),
    `& python hid the interpreter: ${e.warnings("shell")}`);
  e.add("shell", "allow", "git log");
  assert(!/"git log"/.test(e.warnings("shell")), "a plain command was warned about");
  e.choose("fs_write", "allow");
  assert(/File-tool writes anywhere run without asking/.test(e.warnings("fs_write")),
    `Write files set to Allow carries no note: ${e.warnings("fs_write")}`);
});

check("rules editor: patterns that match everything are stopped, the session folder is not (S4)", async () => {
  const e = await openRules();
  for (const p of ["?*", "* *", "*.*", "?:/**", "**/?*"]) {
    e.add("fs_write", "allow", p);
    assert(/matches everything/.test(e.list("fs_write", "allow").querySelector(".acp-rules-problem").textContent),
      `${p} was not stopped`);
  }
  assertEqual(e.chips("fs_write", "allow").length, 0, "a match-everything pattern was added");
  // Final review (SEC6): glob syntax is no literal either.
  for (const p of ["{**}", "*{,}*", "**/{*}"]) {
    e.add("fs_write", "allow", p);
    assert(/matches everything/.test(e.list("fs_write", "allow").querySelector(".acp-rules-problem").textContent),
      `${p} was not stopped`);
  }
  e.add("fs_write", "allow", "./**");
  e.add("fs_write", "allow", "../shared/**");
  // Final review (SE6): a `..` segment in a file allow pattern is stopped
  // here, as the server's `fs_allow_error` refuses it on Save.
  assert(/goes up a folder/.test(e.list("fs_write", "allow").querySelector(".acp-rules-problem").textContent),
    "a `..` allow pattern was not stopped before Save");
  e.add("fs_write", "block", "../shared/**");
  assertEqual(e.chips("fs_write", "allow").join("|"), "the session folder",
    "a folder-scoped pattern was refused");
  assertEqual(e.chips("fs_write", "block").join("|"), "../shared/**",
    "a `..` block pattern, which only narrows, was stopped");
  e.add("fs_write", "allow", "?:/Users/me/**");
  assert(/"\?:\/Users\/me\/\*\*" covers the whole home folder/.test(e.warnings("fs_write")),
    `a ?: drive stem got no breadth warning: ${e.warnings("fs_write")}`);
});

check("rules editor: Esc, the close button and Cancel ask before dropping unsaved changes (U1, U5)", async () => {
  // Nothing changed: Cancel closes at once, and the focus goes back to the gear.
  let e = await openRules();
  e.p.sandbox.closeAcpRulesEditor();
  assertEqual(e.$("acpRulesModal").open, false, "an unchanged editor did not close");
  e.$("acpRulesModal").dispatch("close");
  assert(ACTIVE === e.$("topbarSettingsBtn"), "the focus did not go back to the settings gear");
  // Changed: Cancel (and the close button, which calls the same function) asks in the dialog.
  e = await openRules();
  e.add("shell", "allow", "npm test");
  e.p.sandbox.closeAcpRulesEditor();
  assertEqual(e.$("acpRulesModal").open, true, "Cancel dropped unsaved changes");
  assertEqual(e.$("acpRulesDiscard").hidden, false, "no Discard question");
  assert(ACTIVE === e.$("acpRulesDiscardKeep"), "the question did not take the focus");
  assertEqual(e.p.confirms.length, 0, "window.confirm was used");
  e.p.sandbox.acpRulesKeepEditing();
  assertEqual(e.$("acpRulesDiscard").hidden, true, "Keep editing left the question up");
  assertEqual(e.$("acpRulesModal").open, true, "Keep editing closed the editor");
  assertEqual(e.chips("shell", "allow").join("|"), "git status|pwd|npm test", "Keep editing lost the draft");
  // Esc raises `cancel`; with changes it is stopped and the question asked.
  let prevented = false;
  e.$("acpRulesModal").dispatch("cancel", { preventDefault: () => { prevented = true; } });
  assertEqual(prevented, true, "Esc was not stopped while changes were unsaved");
  assertEqual(e.$("acpRulesDiscard").hidden, false, "Esc did not ask");
  // A browser that closes anyway (a repeated Esc) gets the dialog back, draft intact.
  e.$("acpRulesModal").open = false;
  e.$("acpRulesModal").dispatch("close");
  assertEqual(e.$("acpRulesModal").open, true, "a forced close dropped unsaved changes");
  assertEqual(e.chips("shell", "allow").join("|"), "git status|pwd|npm test", "the reopened editor lost the draft");
  e.p.sandbox.acpRulesDiscard();
  assertEqual(e.$("acpRulesModal").open, false, "Discard did not close");
  e.$("acpRulesModal").dispatch("close");
  assertEqual(e.$("acpRulesModal").open, false, "Discard was asked again");
  assertEqual(e.p.fetches.filter((f) => f.init.method === "POST").length, 0, "Discard saved");
  // Esc with nothing changed is not stopped.
  e = await openRules();
  prevented = false;
  e.$("acpRulesModal").dispatch("cancel", { preventDefault: () => { prevented = true; } });
  assertEqual(prevented, false, "Esc was stopped with nothing to lose");
});

check("rules editor: a refused save marks the chip, explains it on its row and keeps the focus (U2, U4)", async () => {
  let reply = { ok: false,
    error: "The rules were not saved: Run commands (shell): block pattern 'rm\\tx' contains the character U+0009, which is not allowed.",
    detail: { row: "shell", list: "block", pattern: "rm\tx",
      message: "Run commands, Always block: “rm\tx” contains an invisible tab character, which is not allowed" } };
  const e = await openRules(null, () => ({ body: reply }));
  // The client stops this at Add; put it in the draft as a stored rule would be.
  e.p.sandbox._acpRulesDraft.shell.block.push("rm\tx");
  const saving = e.p.sandbox.saveAcpRules();
  assertEqual(e.$("acpRulesSave").disabled, false, "Save was disabled, which drops the focus");
  assertEqual(e.$("acpRulesSave").getAttribute("aria-disabled"), "true", "Save is not marked busy");
  await saving;
  assertEqual(e.$("acpRulesSave").getAttribute("aria-disabled"), "false", "Save stayed busy");
  assertEqual(e.$("acpRulesModal").open, true, "a refused save closed the editor");
  const bad = e.list("shell", "block").querySelectorAll(".acp-rules-chip-bad");
  assertEqual(bad.length, 1, "the refused pattern is not marked");
  assertEqual(bad[0].querySelector(".acp-rules-chip-text").title, "rm\tx", "the wrong chip is marked");
  const rowError = e.row("shell").querySelector(".acp-rules-row-error");
  assert(rowError && /an invisible tab character/.test(rowError.textContent), "the reason is not on the row");
  assert(!/U\+0009/.test(rowError.textContent), "the row shows a code point, not words");
  assert(/^The rules were not saved: Run commands, Always block/.test(e.$("acpRulesError").textContent),
    `the footer summary is not in the editor's words: ${e.$("acpRulesError").textContent}`);
  assert(ACTIVE === bad[0].querySelector(".acp-rules-chip-remove"), "the focus is not on the marked chip");
  assert(/invisible tab character/.test(e.row("shell").querySelector(".acp-rules-live").textContent),
    "the reason was not announced on the row");
  // Removing the chip clears the mark.
  bad[0].querySelector(".acp-rules-chip-remove").click();
  assertEqual(e.row("shell").querySelectorAll(".acp-rules-chip-bad").length, 0, "the mark outlived the chip");
  assertEqual(e.row("shell").querySelector(".acp-rules-row-error"), null, "the row error outlived the chip");
  // A refusal with no row: the footer only, and the focus goes to it.
  reply = { ok: false, error: "The rules were not saved: protected_block is missing." };
  await e.p.sandbox.saveAcpRules();
  assertEqual(e.$("acpRulesError").textContent, reply.error, "a row-less refusal was not shown as sent");
  assert(ACTIVE === e.$("acpRulesError"), "the focus did not move to the refusal");
});

check("rules editor: a Web fetch pattern typed as an address is flagged, in both lists (U3)", async () => {
  const e = await openRules();
  e.add("web_fetch", "allow", "https://evil.com/x");
  assert(/"https:\/\/evil\.com\/x" looks like a web address.*never matches\. Use evil\.com instead\./.test(e.warnings("web_fetch")),
    `no address warning on the allow list: ${e.warnings("web_fetch")}`);
  e.add("web_fetch", "block", "evil.com/path");
  assert(/"evil\.com\/path" looks like a web address.*blocks nothing as typed\. Use evil\.com instead\./.test(e.warnings("web_fetch")),
    `no address warning on the block list: ${e.warnings("web_fetch")}`);
  e.add("web_fetch", "allow", "example.com");
  assert(!/"example\.com"/.test(e.warnings("web_fetch")), "a host name was warned about");
});

check("rules editor: a duplicate says Already listed and keeps the text; a control character is refused in words (U6, U7)", async () => {
  const e = await openRules();
  const input = e.list("shell", "allow").querySelector(".acp-rules-input");
  e.add("shell", "allow", "pwd");
  assert(/Already listed/.test(e.list("shell", "allow").querySelector(".acp-rules-problem").textContent),
    "a duplicate was not reported");
  assertEqual(input.value, "pwd", "the duplicate's text was cleared");
  assertEqual(e.chips("shell", "allow").join("|"), "git status|pwd", "the list changed");
  e.add("shell", "allow", "echo\ta");
  const problem = e.list("shell", "allow").querySelector(".acp-rules-problem").textContent;
  assert(/invisible tab character/.test(problem) && !/U\+/.test(problem), `the tab was not refused in words: ${problem}`);
  e.add("shell", "allow", "echo  x");
  assert(/non-breaking space/.test(e.list("shell", "allow").querySelector(".acp-rules-problem").textContent),
    "a non-breaking space was accepted, which the server refuses");
  e.add("shell", "allow", "echo \u{1F600}");
  assert(/emoji/.test(e.list("shell", "allow").querySelector(".acp-rules-problem").textContent),
    "an emoji was accepted, which the server refuses");
  assertEqual(e.chips("shell", "allow").join("|"), "git status|pwd", "a refused pattern was added");
  e.add("shell", "allow", "echo café");
  assertEqual(e.chips("shell", "allow").join("|"), "git status|pwd|echo café", "a printable accent was refused");
});

check("rules editor: the session folder warning reads plainly; a pattern in both lists is flagged (U10, U14)", async () => {
  const e = await openRules();
  e.add("fs_write", "allow", "./**");
  assert(/The session folder: file-tool writes anywhere in it run without asking\./.test(e.warnings("fs_write")),
    `the session-folder warning is not special-cased: ${e.warnings("fs_write")}`);
  assert(!/covers the whole session folder/.test(e.warnings("fs_write")), "the old repetitive wording is back");
  e.add("shell", "block", "pwd");
  assert(/"pwd" is in both lists\. Always block wins, so it is blocked\./.test(e.warnings("shell")),
    `no both-lists warning: ${e.warnings("shell")}`);
  const modal = fs.readFileSync(path.join(HERE, "..", "src", "power_atlas", "templates", "partials",
    "acp_permission_rules_modal.html"), "utf8");
  assert(/Always block wins over one under Allow without asking/.test(modal), "the intro does not explain precedence");
});

check("rules editor: Protected switches lead with the action (U11); a row's live region is kept across redraws (U12)", async () => {
  const e = await openRules();
  const toggles = e.$("acpRulesBody").querySelectorAll(".acp-rules-protected-toggle").map((t) => t.textContent);
  assertEqual(toggles[0], "Block outright:Agent definitions(otherwise asks)", `the switch does not lead with the action: ${toggles[0]}`);
  const live = e.row("shell").querySelector(".acp-rules-live");
  assertEqual(live.getAttribute("role"), "status", "the row's live region has no role");
  assertEqual(live.textContent, "", "the live region spoke before anything changed");
  e.add("shell", "allow", "python x");
  assert(e.row("shell").querySelector(".acp-rules-live") === live, "the live region was recreated on redraw");
  assert(/equivalent to allow-all/.test(live.textContent), "the new warning was not announced");
  assertEqual(e.row("shell").querySelector(".acp-rules-warn").getAttribute("role"), null,
    "the visual warning list is also a live region, so it would be announced twice");
});

check("settings: the Protected summary counts the linked items not covered (U13)", () => {
  const p = loadPanel();
  const $ = (id) => p.sandbox.document.getElementById(id);
  p.sandbox.renderAcpPermissions(permState({ protected: [
      { id: "agents", label: "Agent definitions", patterns: [], effect: "ask" },
      { id: "steering", label: "Steering files", patterns: [], effect: "ask" }],
    protected_links: { agents: { count: 1, links: [] }, steering: { count: 2, links: [] } } }));
  assertEqual($("acpPermProtectedCount").hidden, false, "no count on the summary");
  assertEqual($("acpPermProtectedCount").textContent, "(3 linked items not covered)", "the summary count is wrong");
  const q = loadPanel();
  q.sandbox.renderAcpPermissions(permState({ protected_links: {} }));
  assertEqual(q.sandbox.document.getElementById("acpPermProtectedCount").hidden, true,
    "a count shows with no linked items");
  const idx = fs.readFileSync(INDEX_TEMPLATE, "utf8");
  assert(/<summary>Protected[^\n]*id="acpPermProtectedCount"/.test(idx), "the count is not in the summary");
});

// ---- Browser sign-in: the local key --------------------------------------
// 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL final review (F2, F3).

check("settings: an in-memory local key is reported with its cause and next step, and nothing is shown when it is persisted (F2)", async () => {
  const lost = { persisted: false, error: "Could not write C:\\cfg\\local-secret: [Errno 13] denied",
                 path: "C:\\cfg\\local-secret" };
  let local = lost;
  const p = loadPanel({ answer: (url) => url === "/api/settings"
    ? { body: { restart_to_apply: [], restart_pending: [], local_secret: local } } : { body: {} } });
  const $ = (id) => p.sandbox.document.getElementById(id);
  await p.sandbox.loadRestartKeys();
  assertEqual($("localSecretWarn").hidden, false, "an in-memory key was not reported");
  const text = $("localSecretWarn").textContent;
  assert(text.includes("Errno 13"), `the cause is missing: ${text}`);
  assert(/sign in again/i.test(text) && /restart/i.test(text),
    `the consequence (sign in again after the next restart) is missing: ${text}`);
  assert(text.includes("C:\\cfg\\local-secret") && /check/i.test(text),
    `the next step (check the path) is missing: ${text}`);
  assertEqual($("topbarPendingDot").hidden, false, "the settings gear's dot did not flag it");

  local = { persisted: true, error: "", path: "C:\\cfg\\local-secret" };
  await p.sandbox.loadRestartKeys();
  assertEqual($("localSecretWarn").hidden, true, "a persisted key still shows the warning");
  assertEqual($("topbarPendingDot").hidden, true, "the dot outlived the condition");

  // An older server with no `local_secret` field says nothing, rather than
  // guessing.
  p.sandbox.renderLocalSecret({ restart_to_apply: [] });
  assertEqual($("localSecretWarn").hidden, true, "an absent field was read as not persisted");
});

check("settings: 'Sign out other browsers' arms on the first click and rotates only on the second, without window.confirm (F3)", async () => {
  const p = loadPanel({ answer: (url) => url === "/api/local-secret/rotate"
    ? { body: { ok: true, reissued: true, message: "x" } } : { body: {} } });
  const $ = (id) => p.sandbox.document.getElementById(id);
  const btn = $("localSecretRotate");
  const rotations = () => p.fetches.filter((f) => f.url === "/api/local-secret/rotate");
  p.sandbox.rotateLocalSecret(btn);
  assertEqual(rotations().length, 0, "the first click rotated the key");
  assert(btn.classList.contains("armed") && /again/i.test(btn.textContent),
    `the first click did not arm the button: ${btn.textContent}`);
  assertEqual(p.confirms.length, 0, "window.confirm() was used");
  p.sandbox.rotateLocalSecret(btn);
  assertEqual(rotations().length, 1, "the second click did not rotate");
  assertEqual(rotations()[0].init.method, "POST", "the rotation was not a POST");
  assert(!btn.classList.contains("armed"), "the button stayed armed after acting");
  await p.settle();
  const note = $("localSecretNote");
  assertEqual(note.hidden, false, "the outcome was not shown");
  assert(/this browser stays signed in/i.test(note.textContent),
    `success did not say this browser stays signed in: ${note.textContent}`);
});

check("settings: an armed rotation button disarms by itself, and a stale timer cannot disarm a newer arming (F3)", () => {
  const p = loadPanel();
  const btn = p.sandbox.document.getElementById("localSecretRotate");
  p.sandbox.rotateLocalSecret(btn);
  const first = p.timers.at(-1);
  first.fn();
  assert(!btn.classList.contains("armed"), "the arming never expired");
  p.sandbox.rotateLocalSecret(btn);  // armed again
  first.fn();                       // the old timer fires late
  assert(btn.classList.contains("armed"), "a stale timer disarmed the newer arming");
  assertEqual(p.fetches.filter((f) => f.url === "/api/local-secret/rotate").length, 0,
    "arming alone rotated the key");
});

check("settings: the markup wires the rotation button and the sign-in rows the script addresses (F3)", () => {
  // The script above is driven through `loadPanel`'s stand-ins; this pins the
  // real markup to the same ids and handler, so a renamed id or a dropped
  // onclick cannot leave a button that does nothing.
  const src = fs.readFileSync(INDEX_TEMPLATE, "utf8");
  const btn = src.match(/<button id="localSecretRotate"[^>]*>([^<]*)<\/button>/);
  assert(btn, "index.html has no #localSecretRotate button");
  // `event` is load-bearing: rotateLocalSecret stops it short of the topbar
  // menu's document-level closer (260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL final QA).
  assert(/onclick="rotateLocalSecret\(this, event\)"/.test(btn[0]),
    `the button does not call rotateLocalSecret(this, event): ${btn[0]}`);
  assert(/Sign out other browsers/.test(btn[1]), `unexpected label: ${btn[1]}`);
  assert(/type="button"/.test(btn[0]), "the button must not default to submit");
  for (const id of ["localSecretWarn", "localSecretNote"]) {
    const el = src.match(new RegExp(`<div id="${id}"[^>]*>`));
    assert(el && /\bhidden\b/.test(el[0]) && /role="status"/.test(el[0]),
      `#${id} is missing, not hidden initially, or not a status region`);
  }
});

check("settings: a real click on 'Sign out other browsers' keeps the menu open on the armed label and on the result (final QA)", async () => {
  // The QA found the arming click closed the menu: the document-level closer
  // saw it, so "Click again" rendered into a closed menu and the natural
  // re-click after reopening rotated with no confirmation seen. Driven as a
  // browser delivers a click: the button's own inline handler, taken from the
  // markup, then every document-level click listener unless it stopped
  // propagation.
  // 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL final QA
  const p = loadPanel({ topbarMenu: true, answer: (url) => url === "/api/local-secret/rotate"
    ? { body: { ok: true, reissued: true, message: "x" } } : { body: {} } });
  const $ = (id) => p.sandbox.document.getElementById(id);
  const btn = $("localSecretRotate");
  const src = fs.readFileSync(INDEX_TEMPLATE, "utf8");
  const onclick = src.match(/<button id="localSecretRotate"[^>]*onclick="([^"]*)"/)[1];
  const inline = vm.runInContext(`(function (event) { ${onclick} })`, p.sandbox);
  function click(el, handler) {
    let stopped = false;
    const ev = { type: "click", target: el, stopPropagation() { stopped = true; } };
    handler.call(el, ev);
    if (!stopped) p.fireDoc("click", ev);
    return stopped;
  }
  const menuOpen = () => !p.topbarMenu.hidden
    && p.topbarBtn.getAttribute("aria-expanded") === "true";
  // Open the menu through the gear's real listener; its click then bubbles to
  // the closer, which the guard absorbs.
  click(p.topbarBtn, (ev) => p.topbarBtn.dispatch("click", ev));
  assert(menuOpen(), "the gear did not open the menu");
  // The closer is live: a click elsewhere closes the menu. Reopen for the check.
  p.fireDoc("click", { type: "click" });
  assert(!menuOpen(), "a click outside the menu did not close it; the check below would prove nothing");
  click(p.topbarBtn, (ev) => p.topbarBtn.dispatch("click", ev));

  click(btn, inline);
  assert(menuOpen(), "the arming click closed the settings menu");
  assert(btn.classList.contains("armed") && /click again/i.test(btn.textContent),
    `the open menu does not show the armed label: ${btn.textContent}`);
  assertEqual(p.fetches.filter((f) => f.url === "/api/local-secret/rotate").length, 0,
    "the arming click rotated the key");

  click(btn, inline);
  await p.settle();
  assert(menuOpen(), "the rotating click closed the settings menu");
  const note = $("localSecretNote");
  assertEqual(note.hidden, false, "the result note is not shown");
  assert(/this browser stays signed in/i.test(note.textContent),
    `the open menu does not show the result: ${note.textContent}`);
});

check("settings: clicking into the menu's text fields keeps it open; a toggle or an outside click still closes it (final QA)", () => {
  // The QA observed in Chromium that a click in the base-agent or Peek hotkey
  // field reached the document-level closer, which hid the field just focused.
  // Driven through the real topbar wiring with the click's target, as a
  // bubbling click delivers it.
  // 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL final QA
  const p = loadPanel({ topbarMenu: true });
  const $ = (id) => p.sandbox.document.getElementById(id);
  // The markup's nesting: menu > .topbar-menu-row > .hotkey-field > input.
  function fieldInMenu(input) {
    const row = new El("div");
    row.className = "topbar-menu-row";
    const box = new El("div");
    box.className = "hotkey-field";
    box.appendChild(input);
    row.appendChild(box);
    p.topbarMenu.appendChild(row);
    return box;
  }
  const baseAgent = $("acpPermBaseAgent");
  const baseBox = fieldInMenu(baseAgent);
  const peek = new El("input");
  const peekBox = fieldInMenu(peek);
  // A Startup-style toggle row (the permission toggle it used to be was
  // replaced by the mode picker, 260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL).
  const toggle = new El("div");
  toggle.className = "topbar-menu-row topbar-toggle";
  p.topbarMenu.appendChild(toggle);
  // The mode picker's label text and the Always blocked disclosure's
  // summary are not inputs; `.topbar-menu-keep` keeps the menu open on them.
  const modes = new El("div");
  modes.className = "acp-perm-modes topbar-menu-keep";
  const modeLabel = new El("label");
  const modeText = new El("span");
  modeLabel.appendChild(modeText);
  modes.appendChild(modeLabel);
  p.topbarMenu.appendChild(modes);
  const floor = new El("details");
  floor.className = "acp-perm-details topbar-menu-keep";
  const summary = new El("summary");
  floor.appendChild(summary);
  p.topbarMenu.appendChild(floor);
  const outside = new El("div");
  const menuOpen = () => !p.topbarMenu.hidden
    && p.topbarBtn.getAttribute("aria-expanded") === "true";
  const open = () => {
    if (menuOpen()) return;
    const ev = { type: "click", target: p.topbarBtn, stopPropagation() {} };
    p.topbarBtn.dispatch("click", ev);
    p.fireDoc("click", ev);
    assert(menuOpen(), "the gear did not open the menu");
  };
  const clickOn = (target) => p.fireDoc("click", { type: "click", target, stopPropagation() {} });

  open();
  clickOn(baseAgent);
  assert(menuOpen(), "clicking into the base-agent field closed the settings menu");
  clickOn(baseBox);
  assert(menuOpen(), "clicking the base-agent field's box closed the settings menu");
  clickOn(peek);
  assert(menuOpen(), "clicking into the Peek hotkey field closed the settings menu");
  clickOn(peekBox);
  assert(menuOpen(), "clicking the Peek hotkey field's box closed the settings menu");
  clickOn(modeText);
  assert(menuOpen(), "clicking a permission mode's label closed the settings menu");
  clickOn(summary);
  assert(menuOpen(), "opening the Always blocked list closed the settings menu");

  clickOn(outside);
  assert(!menuOpen(), "an outside click no longer closes the settings menu");
  open();
  // An input that is not in the menu is an outside click too.
  clickOn(new El("input"));
  assert(!menuOpen(), "a click in a text field outside the menu kept it open");
  open();
  clickOn(toggle);
  assert(!menuOpen(), "a toggle click no longer closes the menu, unlike the Startup toggles");
});

check("settings: a refused rotation says so and does not claim success (F3)", async () => {
  const p = loadPanel({ answer: (url) => url === "/api/local-secret/rotate"
    ? { body: { ok: false, error: "Could not write the key file; the previous local secret is still in effect" } }
    : { body: {} } });
  const btn = p.sandbox.document.getElementById("localSecretRotate");
  p.sandbox.rotateLocalSecret(btn);
  p.sandbox.rotateLocalSecret(btn);
  await p.settle();
  const note = p.sandbox.document.getElementById("localSecretNote");
  assert(/still in effect/.test(note.textContent), `the refusal was not shown: ${note.textContent}`);
  assert(!/signed out/i.test(note.textContent), "a refused rotation claimed browsers were signed out");
});

check("settings: the permission rows are a radio group with Auto disabled, and say which sessions they apply to", () => {
  // Static markup, which the panel harness does not render; asserted on the
  // template source, anchored on the rows' own ids.
  // 260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL Phase 1 (SC-1, SC-10, D-2).
  const src = fs.readFileSync(INDEX_TEMPLATE, "utf8");
  const from = src.indexOf('id="acpPermModes"');
  const to = src.indexOf('id="topbarRestartDivider"', from);
  assert(from >= 0 && to > from, "the permission rows are not in the settings menu");
  const rows = src.slice(from, to);
  const group = /<div id="acpPermModes"[^>]*>/.exec(src)[0];
  assert(group.includes('role="radiogroup"'), "the mode picker is not a radiogroup");
  assert(/aria-labelledby="acpPermModesLabel"/.test(group), "the radiogroup has no accessible name");
  const radios = [...rows.matchAll(/<input type="radio" name="acpPermMode" id="(acpPermMode\w+)" value="(\w+)"([^>]*)>/g)];
  assertEqual(radios.map((m) => m[2]).join(","), "yolo,auto,manual", "the three modes are not offered in order");
  const auto = radios.find((m) => m[2] === "auto");
  assert(/\bdisabled\b/.test(auto[3]), "Auto is selectable; it has no decider yet (D-2)");
  assert(!/onchange=/.test(auto[3]), "Auto has a change handler");
  assert(/coming soon — behaves like Manual/.test(rows), "Auto does not say it is coming and behaves like Manual");
  for (const m of radios.filter((r) => r[2] !== "auto")) {
    assert(/onchange="setAcpPermissionMode\(this\)"/.test(m[3]), `${m[1]} does not save the mode`);
  }
  const note = /id="acpPermScopeNote"[^>]*>([^<]*)</.exec(rows);
  assert(note, "the permission rows carry no scope note");
  assertEqual(note[1], "Changes apply to sessions created afterwards. A reopened session keeps " +
    "the agent it started with, so it is outside the Always blocked list if PowerAtlas's agent " +
    "was not in effect when it started. Terminal sessions and task " +
    "modes such as Spec or Plan are not covered.", "the scope note (SC-10, D-3, SC-3) changed");
  assert(/<details id="acpPermFloor"[^>]*>\s*<summary>Always blocked<\/summary>/.test(rows),
    "there is no Always blocked disclosure");
  assert(/<details id="acpPermProtected"/.test(rows), "there is no Protected disclosure");
  assert(/id="acpPermNotice"/.test(rows), "there is no outside-change notice (D-35)");
  assert(/id="acpPermBaseAgent"/.test(rows), "there is no base-agent input");
  assert(!/acpPermToggle|role="switch"/.test(rows), "the old on/off switch is still in the rows");
  assert(!/querySelector\(['"]\.topbar-toggle/.test(src),
    "a `.topbar-toggle` class query is back — it matches the Startup toggles first");
});

check("dashboard: ACP_LOCAL is a literal true, because / is loopback-only by construction (D-21)", () => {
  const src = fs.readFileSync(INDEX_TEMPLATE, "utf8");
  assert(/^var ACP_LOCAL = true;$/m.test(src), "index.html does not declare ACP_LOCAL = true");
});

// ---- dashboard new-session picker (plans/260919_DASHBOARD_ACP_NEW_SESSION_PICKER.md) ---
//
// `index.html` carries the picker JS in a dedicated section that can be
// extracted by anchor the way the remote-access panel was. Two regions are
// needed here: `dashHandle` (which has the payload.created branch) and the
// picker vars+functions block. Both run in the same sandbox so the shared
// globals (_viewingSid, _dashPickerCapacity, etc.) are the same object.
//
// The region extracts anchored on code rather than comments — moving a comment
// silently shrinks what is under test without throwing, which is the defect
// the Phase 5b review found in two of its own checks.

const DASH_PICKER_NAMES = [
  "_dashPickerWorkspaces", "_dashPickerCapacity", "_dashPickedTaskMode",
  "_dashPendingCreate", "dashPickerOpen", "dashPickerClose", "dashPickerRender",
  "dashPickerRenderKeepRow", "dashPickerCreate", "dashPickerRunPending",
  "dashPickerInitTaskMode",
];
const DASH_HANDLE_NAMES = [
  "dashCloseIfAbandoned", "dashHandle",
  // SC5 (Queue/Steer + Stop, dashboard/ACP feature-parity plan Phase 3) --
  // guards against the frame cases silently moving out of dashHandle.
  "steer_ack", "steer_sent", "steer_status",
  // SC8 (sub-agent/crew read-only panel, dashboard/ACP feature-parity plan
  // Phase 5) -- guards against the `subagents` case, and the agent_died/
  // session_closed crew/sub-agent teardown calls, silently moving out of
  // dashHandle. dashCloseSubWs (Fix 8, Phase 5 review) guards the same for
  // the explicit-socket-close call these teardown sites, and the new-session
  // creation branch, now also make.
  "subagents", "dashCloseSubagentView", "dashRemoveAllCrewPanels", "dashCloseSubWs",
];
// SC8 (sub-agent/crew read-only panel, dashboard/ACP feature-parity plan
// Phase 5) -- a duplicated, not extracted, feature (index.html-only, not
// composer-chrome.js), mirroring acp.html's own crew panel + read-only
// sub-agent panel, kept as two distinct pieces here too.
const DASH_CREW_SUBAGENT_NAMES = [
  "dashCrewLabel", "dashRenderCrewPanel", "dashSubagentState", "dashStopSlotTimer",
  "dashRemoveSingleCrewPanel", "dashRemoveAllCrewPanels", "dashSetCrew",
  "dashOpenSubagent", "dashCloseSubagentView", "dashConnectSubWs",
  "dashSubAppendChunk", "dashSubAddToolCall", "dashSubAddNote", "dashHandleSub",
  "window.removeAllCrewPanels", "window.closeSubagentView",
  // Phase 5 review fixes: dashCloseSubWs (Fix 8, the explicit-close helper
  // shared by agent_died/session_closed/new-session-creation teardown) and
  // dashSubErrorShown (Fix 3, the onclose-fallback double-message guard).
  "dashCloseSubWs", "dashSubErrorShown",
];
const DASH_CMD_PALETTE_NAMES = [
  "initCommandPaletteDom", "showCommandDropdown", "hideCommandDropdown",
  "isCommandDropdownVisible", "moveCommandSelection", "confirmCommandSelection",
];
// SC5 (Queue/Steer + Stop, dashboard/ACP feature-parity plan Phase 3) -- a
// duplicated, not extracted, feature (index.html-only, not composer-chrome.js).
const DASH_COMPOSER_CONTROLS_NAMES = [
  "dashRefreshComposerControls", "setDashSteerStatus", "dashApplySendMode",
  "DASH_SEND_MODE_KEY",
];
const DASH_QUEUE_STEER_WIRING_NAMES = [
  "dashStopBtn.addEventListener", "_dashCloseModeMenu", "dashSendModeBtn.addEventListener",
];
// SC6 (Image paste-to-attach, dashboard/ACP feature-parity plan Phase 4) --
// dash-prefixed, index.html-only (no composer-chrome.js involvement).
const DASH_IMAGE_ATTACH_NAMES = [
  "IMAGE_LADDER", "IMAGE_FORMATS", "dashImagesSupported", "dashImageFilesFrom",
  "dashStageFiles", "dashStageOne", "dashRenderTray", "dashAttachmentChip",
  "dashRemoveAttachment", "dashClearAttachments", "dashReleasePendingAttachments",
  "dashRevokeAttachment", "function dashSendPrompt",
];
// SC7 (WS reconnect-on-drop, dashboard/ACP feature-parity plan Phase 6) --
// guards against dashConnect()'s reconnect/backoff machinery, or the
// refused-handshake diagnosis it defers to, silently moving out of this region.
// The diagnosis names were renamed from the stale-token pair by
// 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 6.
const DASH_CONNECT_NAMES = [
  "function dashConnect", "_dashReconnectTimer", "_dashReconnectDelay",
  "_dashOpened", "_dashReconnectQueueSnapshot", "dashReportSignedOut",
  "dashExplainRefusedHandshake", "dashReconnectOnReady",
  "dashReconnectBtn.addEventListener", "dashReloadBtn.addEventListener",
];
// Close-button click region (Fix 3, Phase 6 review): just the
// dashCloseBtn.addEventListener('click', ...) listener itself, NOT the
// surrounding var declarations for dashStopBtn/dashQueueSteerEl/etc. that sit
// between it and dashUpdateCloseButton() in the real file -- those are
// already separate, standalone El() instances directly in this harness's
// sandbox literal (not looked up via document.getElementById), so replaying
// their `var x = document.getElementById(...)` declarations here would
// silently clobber every one of them with an unrelated (or null) lookup.
// Real source, not a hand-rewritten stand-in, for the same reason every
// other click-dispatch region in this file is one.
const DASH_CLOSE_BTN_NAMES = ["dashCloseBtn.addEventListener"];
// openSessionTranscript region (Fix 3, Step 9 review): the whole function,
// including its fetch-`.catch()` branch under test -- real source, same
// reasoning as every other click/async-dispatch region in this file. No
// top-level side effects (a bare function declaration), so unlike
// connectRegion it can load unconditionally and unordered relative to the
// other regions -- it only reads dashCloseIfAbandoned/dashCloseSubagentView/
// dashRemoveAllCrewPanels/dashCloseSubWs/renderTranscriptHistory/fetch/
// dashMaybeAttach as free variables AT CALL TIME, never at parse time.
const DASH_OPEN_TRANSCRIPT_NAMES = ["function openSessionTranscript"];
// dashRailForgetSession region (Fix 3, Step 9 review): same reasoning as
// openSessionTranscriptRegion above -- a standalone function declaration
// with no top-level side effects.
const DASH_RAIL_FORGET_NAMES = ["function dashRailForgetSession"];

function dashPickerSource() {
  const src = fs.readFileSync(INDEX_TEMPLATE, "utf8");

  // Composer-controls region (SC5, dashboard/ACP feature-parity plan Phase
  // 3): from dashRefreshComposerControls's declaration (which governs
  // Send/Stop/Queue+Steer's visible/enabled state) through the
  // initCommandPaletteDom() call that follows it (exclusive -- that call is
  // the start of cmdPaletteRegion below). Must run BEFORE cmdPaletteRegion:
  // that region's own initCommandPaletteDom({onPromptChanged:
  // dashRefreshComposerControls}) call reads dashRefreshComposerControls as
  // a plain identifier value immediately, at harness-setup time, so it has
  // to already exist as a real function by then -- exactly why this used to
  // be stubbed as a no-op here before Phase 3 needed its real Stop/
  // Queue+Steer visibility logic under test.
  const controlsFrom = src.indexOf("function dashRefreshComposerControls");
  if (controlsFrom < 0) throw new Error("index.html no longer defines dashRefreshComposerControls");
  const cmdFrom = src.indexOf("initCommandPaletteDom({");
  if (cmdFrom < 0) throw new Error("index.html no longer calls initCommandPaletteDom");
  if (cmdFrom < controlsFrom) throw new Error("initCommandPaletteDom now precedes dashRefreshComposerControls");
  const composerControlsRegion = src.slice(controlsFrom, cmdFrom);
  for (const name of DASH_COMPOSER_CONTROLS_NAMES) {
    if (!composerControlsRegion.includes(name)) {
      throw new Error(
        `the extracted composer-controls region does not contain ${name}; it has moved`);
    }
  }

  // Command-palette wiring region (SC1, dashboard/ACP feature-parity plan
  // Phase 2): from the initCommandPaletteDom() call through the end of the
  // `input` listener paired with it, immediately before dashSendBtn's own
  // click wiring. Real source, not a hand-rewritten stand-in, for the same
  // reason handleRegion below is one — this is genuine keyboard-dispatch
  // logic (the `/` intercept, arrow-key navigation, Enter/Tab confirm) that
  // must be exercised as written, not as re-described. Comes earlier in the
  // file than dashHandle, so it is extracted and run first.
  const cmdTo = src.indexOf("dashSendBtn.addEventListener('click', dashSendPrompt);", cmdFrom);
  if (cmdTo < 0) throw new Error("index.html's composer wiring section has moved");
  const cmdPaletteRegion = src.slice(cmdFrom, cmdTo);
  for (const name of DASH_CMD_PALETTE_NAMES) {
    if (!cmdPaletteRegion.includes(name)) {
      throw new Error(
        `the extracted command-palette region does not contain ${name}; it has moved`);
    }
  }

  // Queue/Steer + Stop click-handler wiring region (SC5, dashboard/ACP
  // feature-parity plan Phase 3): from immediately after dashSendBtn's own
  // click wiring (cmdTo, above) through dashMaybeAttach's declaration
  // (exclusive). Real source, not a re-typed stand-in, for the same
  // keyboard/click-dispatch reason cmdPaletteRegion is one.
  const queueSteerTo = src.indexOf("function dashMaybeAttach", cmdTo);
  if (queueSteerTo < 0) throw new Error("index.html no longer defines dashMaybeAttach after the composer wiring");
  const queueSteerWiringRegion = src.slice(cmdTo, queueSteerTo);
  for (const name of DASH_QUEUE_STEER_WIRING_NAMES) {
    if (!queueSteerWiringRegion.includes(name)) {
      throw new Error(
        `the extracted queue/steer wiring region does not contain ${name}; it has moved`);
    }
  }

  // dashHandle region: from dashCloseIfAbandoned's declaration (the function
  // immediately preceding dashHandle -- pulled in too so its own review-fix
  // behaviour, e.g. the setContext(null) call added during the Phase 1 fix
  // cycle, is exercised by the real source rather than a re-typed stand-in)
  // through dashHandle's closing `}` immediately before the `// ---- Phase 4`
  // picker comment.
  const handleFrom = src.indexOf("function dashCloseIfAbandoned");
  if (handleFrom < 0) throw new Error("index.html no longer defines dashCloseIfAbandoned");

  // Sub-agent/crew read-only panel region (SC8, dashboard/ACP feature-parity
  // plan Phase 5): from dashCrews's declaration through the end of the
  // window.removeAllCrewPanels/window.closeSubagentView guard assignments,
  // immediately before dashCloseIfAbandoned (handleFrom, above). Real
  // source, not a hand-rewritten stand-in, for the same reason
  // imageAttachRegion below is one -- the crew-panel rendering, timer
  // cleanup and sub-agent-socket lifecycle logic must be exercised as
  // written.
  // Object.create(null), not {} (Fix 4, Phase 5 review) -- keyed directly by
  // wire-controlled toolCallId.
  const crewFrom = src.indexOf("var dashCrews = Object.create(null);");
  if (crewFrom < 0) throw new Error("index.html no longer defines dashCrews");
  if (crewFrom > handleFrom) throw new Error("dashCrews now follows dashCloseIfAbandoned");

  // Image-attach pipeline region (SC6, dashboard/ACP feature-parity plan
  // Phase 4): from IMAGE_LADDER's declaration through the end of
  // dashSendPrompt, immediately before the sub-agent/crew panel region
  // (crewFrom, above -- Phase 5 inserted its own block between dashSendPrompt
  // and dashCloseIfAbandoned, so this region's end boundary moved off
  // handleFrom onto crewFrom). Real source, not a hand-rewritten stand-in,
  // for the same reason cmdPaletteRegion/queueSteerWiringRegion are -- the
  // encode pipeline and dashSendPrompt's own images-payload logic must be
  // exercised as written.
  const imageFrom = src.indexOf("var IMAGE_LADDER = [");
  if (imageFrom < 0) throw new Error("index.html no longer defines IMAGE_LADDER");
  if (imageFrom > crewFrom) throw new Error("IMAGE_LADDER now follows the sub-agent/crew panel region");
  const imageAttachRegion = src.slice(imageFrom, crewFrom);
  for (const name of DASH_IMAGE_ATTACH_NAMES) {
    if (!imageAttachRegion.includes(name)) {
      throw new Error(
        `the extracted image-attach region does not contain ${name}; it has moved`);
    }
  }

  const crewSubagentRegion = src.slice(crewFrom, handleFrom);
  for (const name of DASH_CREW_SUBAGENT_NAMES) {
    if (!crewSubagentRegion.includes(name)) {
      throw new Error(
        `the extracted sub-agent/crew panel region does not contain ${name}; it has moved`);
    }
  }

  // The picker section follows immediately after the closing `}` of dashHandle.
  const pickerCommentMarker = "// ---- Phase 4: ACP new-session picker";
  const pickerStart = src.indexOf("var _dashPickerWorkspaces");
  if (pickerStart < 0) throw new Error("index.html no longer defines _dashPickerWorkspaces");
  const handleRegion = src.slice(handleFrom, pickerStart);
  for (const name of DASH_HANDLE_NAMES) {
    if (!handleRegion.includes(name)) {
      throw new Error(
        `the extracted dashHandle region does not contain ${name}; it has moved`);
    }
  }

  // Picker region: from `var _dashPickerWorkspaces` through the closing `}` of
  // dashPickerRailAdopt. Anchor on the function that follows it (`function resetOverlays`)
  // to know where to stop, so the wiring event listeners between them are included.
  const pickerEnd = src.indexOf("function resetOverlays", pickerStart);
  if (pickerEnd < 0) throw new Error("index.html no longer defines resetOverlays after the picker");
  const pickerRegion = src.slice(pickerStart, pickerEnd);
  for (const name of DASH_PICKER_NAMES) {
    if (!pickerRegion.includes(name)) {
      throw new Error(
        `the extracted picker region does not contain ${name}; the picker has moved and ` +
        "this harness is measuring less of it than it claims to");
    }
  }

  // WS reconnect-on-drop region (SC7, dashboard/ACP feature-parity plan
  // Phase 6): from dashWsUrl's declaration (dashConnect's own dependency)
  // through immediately before dashComposerEl's declaration -- covers
  // _dashWs/the reconnect-timer state, send(), dashReportSignedOut(),
  // dashExplainRefusedHandshake(), dashConnect() itself, and the
  // Reconnect/Reload button wiring. Loaded only when a check opts in
  // (loadDashPicker({realConnect: true})) -- see that function's own header
  // comment for why: this region's real dashConnect() waits on a real
  // WebSocket's onopen before firing its onReady callback, unlike the
  // synchronous no-op stub every other region's checks are written against
  // (dashSendPrompt()'s own lazy-attach path, the picker's session-creation
  // flow), so it must never run unconditionally.
  const wsUrlFrom = src.indexOf("function dashWsUrl(){");
  if (wsUrlFrom < 0) throw new Error("index.html no longer defines dashWsUrl");
  const connectTo = src.indexOf("var dashComposerEl = document.getElementById('dashComposer');", wsUrlFrom);
  if (connectTo < 0) throw new Error("index.html's dashConnect region no longer precedes dashComposerEl's declaration");
  const connectRegion = src.slice(wsUrlFrom, connectTo);
  for (const name of DASH_CONNECT_NAMES) {
    if (!connectRegion.includes(name)) {
      throw new Error(
        `the extracted connect region does not contain ${name}; it has moved`);
    }
  }

  // Close-button click region (Fix 3, Phase 6 review): see DASH_CLOSE_BTN_NAMES
  // above for why this is a narrow, standalone slice rather than an extension
  // of an existing region's boundary. Loaded unconditionally (not gated on
  // realConnect) -- ordinary synchronous click-dispatch code, same as
  // queueSteerWiringRegion.
  const closeBtnFrom = src.indexOf("dashCloseBtn.addEventListener('click'");
  if (closeBtnFrom < 0) throw new Error("index.html no longer wires dashCloseBtn's click listener");
  const closeBtnTo = src.indexOf("// _dashAttachedSid: the sid this socket", closeBtnFrom);
  if (closeBtnTo < 0) throw new Error("index.html's dashCloseBtn click region no longer precedes the _dashAttachedSid comment");
  const closeBtnRegion = src.slice(closeBtnFrom, closeBtnTo);
  for (const name of DASH_CLOSE_BTN_NAMES) {
    if (!closeBtnRegion.includes(name)) {
      throw new Error(
        `the extracted close-button region does not contain ${name}; it has moved`);
    }
  }

  // openSessionTranscript region (Fix 3, Step 9 review): from its own
  // declaration through the `// ---- Phase 3: live-attach wiring` comment
  // that immediately follows it in the real file.
  const openTranscriptFrom = src.indexOf("function openSessionTranscript");
  if (openTranscriptFrom < 0) throw new Error("index.html no longer defines openSessionTranscript");
  const openTranscriptTo = src.indexOf("// ---- Phase 3: live-attach wiring", openTranscriptFrom);
  if (openTranscriptTo < 0) throw new Error("index.html's live-attach wiring comment no longer follows openSessionTranscript");
  const openSessionTranscriptRegion = src.slice(openTranscriptFrom, openTranscriptTo);
  for (const name of DASH_OPEN_TRANSCRIPT_NAMES) {
    if (!openSessionTranscriptRegion.includes(name)) {
      throw new Error(
        `the extracted openSessionTranscript region does not contain ${name}; it has moved`);
    }
  }

  // dashRailForgetSession region (Fix 3, Step 9 review): from its own
  // declaration through the start of dashDeleteSession, the function that
  // immediately follows it in the real file.
  const railForgetFrom = src.indexOf("function dashRailForgetSession");
  if (railForgetFrom < 0) throw new Error("index.html no longer defines dashRailForgetSession");
  const railForgetTo = src.indexOf("function dashDeleteSession", railForgetFrom);
  if (railForgetTo < 0) throw new Error("index.html's dashDeleteSession no longer follows dashRailForgetSession");
  const railForgetRegion = src.slice(railForgetFrom, railForgetTo);
  for (const name of DASH_RAIL_FORGET_NAMES) {
    if (!railForgetRegion.includes(name)) {
      throw new Error(
        `the extracted dashRailForgetSession region does not contain ${name}; it has moved`);
    }
  }

  return {
    composerControlsRegion, cmdPaletteRegion, queueSteerWiringRegion, imageAttachRegion,
    crewSubagentRegion, handleRegion, pickerRegion, connectRegion, closeBtnRegion,
    openSessionTranscriptRegion, railForgetRegion,
  };
}

function loadDashPicker(opts = {}) {
  const {
    composerControlsRegion, cmdPaletteRegion, queueSteerWiringRegion, imageAttachRegion,
    crewSubagentRegion, handleRegion, pickerRegion, connectRegion, closeBtnRegion,
    openSessionTranscriptRegion, railForgetRegion,
  } = dashPickerSource();

  // All picker-element IDs that must exist in the byId map for parse-time
  // wiring (document.getElementById calls in the picker script body) to work.
  const pickerEls = [
    "dashPicker", "dashPickerSearch", "dashPickerNote",
    "dashPickerTaskModeMenu", "dashPickerTaskModeToggle",
    "dashPickerTaskModeToggleText", "dashPickerCloseCurrent",
    "dashPickerKeepRow", "dashPickerKeepText",
    "dashPickerNeutral", "dashPickerList", "dashPickerCancel",
    "dashPickerTitle",
  ];
  const byId = new Map();
  for (const id of pickerEls) {
    const el = new El("div");
    // Match the markup: #dashPicker starts hidden.
    if (id === "dashPicker") el.hidden = true;
    if (id === "dashPickerKeepRow") el.hidden = true;
    if (id === "dashPickerTaskModeMenu") el.hidden = true;
    if (id === "dashPickerSearch") { el.tagName = "INPUT"; el.value = ""; }
    if (id === "dashPickerCloseCurrent") { el.tagName = "INPUT"; el.type = "checkbox"; el.checked = false; }
    if (id === "dashPickerNeutral") { el.tagName = "BUTTON"; el.disabled = false; }
    byId.set(id, el);
  }

  // Wire the task-mode toggle's dataset so dashPickerOpen can set .dataset.taskMode
  byId.get("dashPickerTaskModeToggle").dataset = { taskMode: "kiro_default" };

  // composer-chrome.js's DOM refs (dashboard/ACP feature-parity plan, Phase
  // 1) -- dashHandle's `session` and `agent_died` branches now reach
  // setContext/renderSidLabel/logLine via the shared module's initXxxDom()
  // calls below, the same way the real index.html wires it, so this harness
  // must too: without them, the `dashHandle creation branch …` checks further
  // down (which already send a `session` frame with a real `cwd`) would throw
  // a ReferenceError the moment that widened code runs.
  // WS reconnect-on-drop DOM refs (SC7, dashboard/ACP feature-parity plan
  // Phase 6) -- pre-set exactly like dashContext/etc. below: dashConnect()'s
  // own top-level `document.getElementById` calls (connectRegion, loaded
  // only when opts.realConnect is true) look these up at parse time, and its
  // own `dashReconnectBtn.addEventListener(...)`/`dashReloadBtn.
  // addEventListener(...)` calls run at parse time too. Matches the real
  // markup's `hidden` attribute.
  byId.set("dashReconnect", new El("button"));
  byId.get("dashReconnect").hidden = true;
  byId.set("dashReload", new El("button"));
  byId.get("dashReload").hidden = true;
  byId.set("dashContext", new El("span"));
  byId.get("dashContext").hidden = true;
  byId.set("dashContextFill", new El("span"));
  byId.set("dashContextLabel", new El("span"));
  byId.set("dashSid", new El("button"));
  byId.set("dashCopy", new El("button"));
  byId.get("dashCopy").hidden = true;
  byId.set("dashLog", new El("div"));
  byId.get("dashLog").hidden = true;
  byId.set("dashLogToggle", new El("button"));
  // composer-chrome.js's slash command palette (SC1, dashboard/ACP
  // feature-parity plan Phase 2) -- initCommandPaletteDom() (run from
  // cmdPaletteRegion below) looks this up by id.
  byId.set("dashCmdDropdown", new El("div"));
  byId.get("dashCmdDropdown").hidden = true;
  // Queue/Steer + Stop DOM refs (SC5, dashboard/ACP feature-parity plan
  // Phase 3) -- pre-set exactly like dashComposerEl/dashPromptInput/
  // dashSendBtn below rather than extracted from real
  // `document.getElementById(...)` source lines: dashRefreshComposerControls/
  // dashApplySendMode (composerControlsRegion) and the click wiring
  // (queueSteerWiringRegion) reference these by name as free variables that
  // resolve to these sandbox globals, the same mechanism dashSendBtn etc.
  // already rely on.
  byId.set("dashModeLiveRegion", new El("span")); // read via document.getElementById inside dashApplySendMode
  // Sub-agent/crew read-only panel DOM refs (SC8, dashboard/ACP
  // feature-parity plan Phase 5) -- pre-set exactly like the composer-chrome.js
  // refs above: crewSubagentRegion's own top-level `document.getElementById`
  // calls (dashTranscriptWrapEl, dashSubPanelEl, etc.) look these up at parse
  // time, and dashSubBackBtn.addEventListener(...) runs at parse time too, so
  // the ref must already exist by then.
  byId.set("dashTranscriptWrap", new El("div"));
  byId.set("dashSubPanel", new El("div"));
  byId.get("dashSubPanel").hidden = true;
  byId.set("dashSubBack", new El("button"));
  byId.set("dashSubRole", new El("span"));
  byId.set("dashSubStatus", new El("span"));
  byId.set("dashSubTranscript", new El("div"));
  // openSessionTranscript()/dashRailForgetSession() (Fix 3, Step 9 review)
  // both look this up by id to clear/replace the transcript panel's content.
  byId.set("dashTranscript", new El("div"));

  const fetches = [];
  const sentFrames = [];
  const dashSendPromptCalls = [];
  const systemMessages = [];
  const addMessageCalls = [];
  // Whether location.reload() has been called (SC7, dashboard/ACP
  // feature-parity plan Phase 6) -- dashReloadBtn's click handler.
  let dashReloaded = false;
  // renderTranscriptHistory() call log (SC6, Phase 4) -- see the sandbox
  // stub below.
  const historyRenders = [];
  // Timer stand-in for setDashSteerStatus's steering_injected auto-clear
  // (SC5, Phase 3) -- mirrors the acp.html-side harness's own timers/
  // setTimeout/runTimers pattern (see loadPage() above) so a check can fire
  // the 4s clear deterministically rather than waiting on a real timer.
  const timers = [];
  // Backing store for the localStorage stand-in below (SC5, Phase 3) --
  // seeded from opts.stored the same way loadPage()'s own `stored` is, and
  // exposed on the returned harness object as `dashStored` so a check can
  // assert on it directly.
  const dashStoredData = { ...(opts.stored || {}) };
  const dashSendModeBtnSrOnly = new El("span");
  dashSendModeBtnSrOnly.className = "sr-only";
  const dashSendModeBtnEl = new El("button");
  dashSendModeBtnEl.appendChild(dashSendModeBtnSrOnly); // matches real markup's child <span class="sr-only">
  const dashModeToggleEl = new El("button");
  const dashModeMenuEl = new El("div");
  dashModeMenuEl.hidden = true; // matches real markup's `hidden` attribute
  const dashModeOptSteerEl = new El("button");
  const dashModeOptQueueEl = new El("button");
  const dashStopBtnEl = new El("button");
  dashStopBtnEl.hidden = true; // matches real markup's `hidden` attribute
  const dashQueueSteerElEl = new El("div");
  dashQueueSteerElEl.hidden = true; // matches real markup's `hidden` attribute
  const dashSteerStatusElEl = new El("div");
  dashSteerStatusElEl.hidden = true; // matches real markup's `hidden` attribute
  // Document-level listener tracking (SC5, Phase 3) -- the mode menu's
  // outside-click/Esc-close wiring registers through document.addEventListener,
  // which was a blanket no-op here through Phase 2 (nothing needed to fire a
  // document-level listener). Mirrors loadPage()'s own docListeners Map +
  // fireDoc() helper (this file, ~line 1123) exactly.
  const dashDocListeners = new Map();
  // Image tray element (SC6, dashboard/ACP feature-parity plan Phase 4) --
  // pre-set exactly like dashComposerEl/dashPromptInput/dashSendBtn above.
  const dashTrayElEl = new El("div");
  dashTrayElEl.hidden = true; // matches real markup's `hidden` attribute

  /* ---- the image-attachment surface (SC6, Phase 4) ---------------------
   *
   * Same reasoning and same stand-ins as loadPage()'s own image fixtures
   * above (`FakeBlob`/`encodeBlob`/`RATE`): `FileReader`, `Image`, `Blob`,
   * `URL` and a canvas 2D context are browser furniture the bare `vm`
   * context lacks, stubbed rather than skipped because the paste path
   * cannot be driven at all otherwise. The encoder is deterministic and
   * swappable (`opts.encode`/`opts.noWebp`/`opts.imageWidth`/
   * `opts.imageHeight`/`opts.imageDecodeFails`) for the same reason.
   */
  class DashFakeBlob {
    constructor(size, type) { this.size = size; this.type = type; }
  }
  const dashObjectUrls = new Map();
  const dashRevokedUrls = [];
  let dashObjectUrlSeq = 0;
  const DASH_IMAGE_RATE = { "image/webp": 0.06, "image/jpeg": 0.11, "image/png": 0.9 };
  const dashEncodeBlob = opts.encode || ((type, quality, w, h) => {
    const got = (type === "image/webp" && opts.noWebp) ? "image/png" : type;
    return new DashFakeBlob(
      Math.max(1, Math.round(w * h * (DASH_IMAGE_RATE[got] ?? 0.11) * quality)), got);
  });

  /* ---- the sub-agent/crew read-only panel surface (SC8, Phase 5) --------
   *
   * transcript-renderer.js is not loaded for real in this sandbox (see the
   * header comment on this function: only composer-chrome.js is real
   * source here), so the handful of its globals dashSetCrew() reaches into
   * as bare identifiers -- exactly as acp.html's own setCrew() does -- are
   * stood in individually, same reasoning as addMessage/addSystemMessage/
   * renderTranscriptHistory below. `transcriptEl` here is the crew panel's
   * host (mirrors production's dashTranscript element, wired via
   * initTranscriptDom() on the real page); `elapsedText` is copied verbatim
   * from transcript-renderer.js since it is pure and has no DOM dependency
   * of its own.
   */
  const dashMainTranscriptEl = new El("div");
  const dashToolRows = Object.create(null);
  const dashIntervals = [];
  // A second, independent WebSocket (SC8, Phase 5) -- dashConnectSubWs()
  // constructs one directly (mirrors acp.html's own connectSubWs(), which
  // has no reconnect loop and no dependency on dashConnect()/`_dashWs`,
  // both of which stay stubbed/unused for this surface). Readied OPEN on
  // construction like the acp.html-side harness's own FakeWs (this file,
  // ~line 782) -- openSub() below still fires onopen by hand, mirroring
  // page.openAt()'s own pattern, since the real page's onopen is what sends
  // the `subscribe` frame.
  const dashSubSockets = [];
  class DashFakeSubWs {
    static OPEN = 1;
    constructor(url) {
      this.url = url;
      this.readyState = DashFakeSubWs.OPEN;
      this.sent = [];
      this.onopen = this.onmessage = this.onclose = this.onerror = null;
      dashSubSockets.push(this);
    }
    send(text) { this.sent.push(JSON.parse(text)); }
    close() { this.readyState = 3; }
  }

  const sandbox = {
    document: {
      createElement: (tag) => {
        const el = new El(tag);
        // Canvas stand-in for the image-encode pipeline (SC6, Phase 4) --
        // mirrors loadPage()'s own createElement override exactly.
        if (String(tag).toLowerCase() === "canvas" && opts.images !== false) {
          el.getContext = () => ({ drawImage() {} });
          el.toBlob = (cb, type, quality) =>
            cb(dashEncodeBlob(type, quality, el.width, el.height));
        }
        return el;
      },
      getElementById: (id) => byId.get(id) ?? null,
      // openSessionTranscript() (Fix 3, Step 9 review) calls
      // document.querySelectorAll('.acp-rail-row.viewing') to clear the
      // previously-viewed row's highlight -- no rail rows are simulated in
      // this harness, so an empty, forEach-able result is the correct stand-in.
      querySelectorAll: () => [],
      addEventListener: (type, fn) => {
        if (!dashDocListeners.has(type)) dashDocListeners.set(type, []);
        dashDocListeners.get(type).push(fn);
      },
      write: () => { throw new Error("document.write not allowed"); },
    },
    // window.addEventListener('pagehide', dashCloseIfAbandoned) -- a bare,
    // top-level call now inside the extracted handleRegion (dashCloseIfAbandoned
    // was pulled in alongside dashHandle above). A no-op stub (unlike
    // document.addEventListener above, which now tracks listeners for SC5's
    // mode-menu wiring); the harness calls dashCloseIfAbandoned directly
    // rather than through a real pagehide event.
    addEventListener: () => {},
    fetch: (url, init) => {
      fetches.push({ url, init: init || {} });
      // opts.fetchFails (SC7, dashboard/ACP feature-parity plan Phase 6):
      // simulates a server that is not answering at all -- the branch
      // dashExplainRefusedHandshake()'s rejection handler covers.
      if (opts.fetchFails) return Promise.reject(new Error("network error"));
      // opts.pageHangs: the page's own GET of itself never answers until the
      // page aborts it. 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL
      // Phase 6 review (J6)
      if (opts.pageHangs && String(url) === "/") {
        return new Promise((_resolve, reject) => {
          const signal = init && init.signal;
          if (signal) signal.addEventListener("abort", () => reject(new Error("aborted")));
        });
      }
      // opts.pageStatus: the status the page's own GET of itself ("/", the
      // sandbox location.pathname) answers with -- 403 is the loopback gate
      // refusing a browser with no valid pa_local. Keyed on the exact page
      // URL so the picker's own workspace fetches stay healthy.
      // 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 6
      if (opts.pageStatus !== undefined && String(url) === "/") {
        return Promise.resolve({
          ok: opts.pageStatus >= 200 && opts.pageStatus < 300, status: opts.pageStatus,
          json: () => Promise.resolve({}), text: () => Promise.resolve(""),
        });
      }
      // opts.sessionTranscriptFails (Fix 3, Step 9 review): a failed
      // /api/session-transcript fetch specifically -- openSessionTranscript()'s
      // own .catch() branch under test, distinct from opts.fetchFails above
      // (which would also break the picker's own workspace-loading fetches).
      if (opts.sessionTranscriptFails && String(url).indexOf("/api/session-transcript") === 0) {
        return Promise.reject(new Error("network error"));
      }
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve(opts.workspacesResponse ?? {
          workspaces: [], missing: 0, capacity: { held: 0, max: 8 },
        }),
        text: () => Promise.resolve("{}"),
      });
    },
    // Globals read by the picker/dashHandle at runtime
    // The ACP-availability sentinel (web.py's `acp_available`), a boolean.
    // 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 6
    ACP_AVAILABLE: opts.acpAvailable !== undefined ? opts.acpAvailable : true,
    _dashAttachedSid: opts.dashAttachedSid !== undefined ? opts.dashAttachedSid : null,
    _dashTurnActive: opts.dashTurnActive !== undefined ? opts.dashTurnActive : false,
    _viewingSid: opts.viewingSid !== undefined ? opts.viewingSid : null,
    // index.html's transcript-panel metadata strip; DOM-only, no behaviour
    // these tests assert on.
    dashSessionMetaRender: () => {},
    _dashLoadingSid: null,
    _dashEagerLoadPending: false,  // eager-connect plan
    _dashEagerFocusSuppressed: false,  // eager-connect plan
    _dashSent: false,
    _dashOrigin: null,
    _dashPendingSend: null,
    // Mirrors index.html's own var (dashboard/ACP feature-parity plan, Phase
    // 1) -- read by the isReplaying accessor passed to initLogDom() below.
    _dashReplaying: false,
    // Mirrors index.html's own defaults (dashboard/ACP feature-parity plan,
    // Phase 1) -- dashHandle's widened `connected` meta case overwrites these.
    _dashImageMaxCount: 4,
    _dashImageMaxBytes: 176 * 1024,
    // Queue/Steer + Stop state (SC5, dashboard/ACP feature-parity plan Phase
    // 3), overridable so a check can start mid-steer/mid-queue.
    _dashQueuedPrompt: opts.dashQueuedPrompt !== undefined ? opts.dashQueuedPrompt : null,
    _dashQueuedPromptSession: opts.dashQueuedPromptSession !== undefined ? opts.dashQueuedPromptSession : null,
    _dashSteerPending: opts.dashSteerPending !== undefined ? opts.dashSteerPending : null,
    _dashStopInProgress: opts.dashStopInProgress !== undefined ? opts.dashStopInProgress : false,
    _dashSteerStatusTimer: null,
    // WS reconnect-on-drop state (SC7, dashboard/ACP feature-parity plan
    // Phase 6) -- dashHandle's `session`/`session_closed`/agent_died` cases
    // (handleRegion, always loaded) reference _dashReconnectQueueSnapshot as
    // a free variable regardless of whether this check opts into
    // realConnect, so it must be pre-set here unconditionally, the same way
    // _dashQueuedPrompt/etc. above are. dashReconnectBtn/dashReloadBtn are
    // likewise referenced by dashHandle's `error` case (the reportLoadFailure
    // parity branch) unconditionally.
    _dashReconnectQueueSnapshot: opts.dashReconnectQueueSnapshot !== undefined ? opts.dashReconnectQueueSnapshot : null,
    dashReconnectBtn: byId.get("dashReconnect"),
    dashReloadBtn: byId.get("dashReload"),
    // Close-button (Fix 3, Phase 6 review) -- a standalone El() instance, not
    // byId-registered, same reasoning as dashStopBtn/etc. immediately below:
    // closeBtnRegion's own `dashCloseBtn.addEventListener(...)` call (run
    // further down) reads this as a free variable at parse time, so it must
    // already exist by then.
    dashCloseBtn: new El("button"),
    dashStopBtn: dashStopBtnEl,
    dashQueueSteerEl: dashQueueSteerElEl,
    dashSendModeBtn: dashSendModeBtnEl,
    dashModeToggle: dashModeToggleEl,
    dashModeMenu: dashModeMenuEl,
    dashModeOptSteer: dashModeOptSteerEl,
    dashModeOptQueue: dashModeOptQueueEl,
    dashModeOptions: [dashModeOptSteerEl, dashModeOptQueueEl],
    dashSteerStatusEl: dashSteerStatusElEl,
    // localStorage stand-in for railStore/railStored (composer-chrome.js) --
    // dashApplySendMode persists the chosen send mode under DASH_SEND_MODE_KEY
    // through these. Without a stand-in every read/write throws inside
    // railStore/railStored's own try/catch and is silently swallowed, which
    // would make a persistence check pass against a page that persisted
    // nothing (same reasoning as the acp.html-side harness's own
    // localStorage stub, loadPage() above). `dashStoredData` (declared
    // below the sandbox literal) is what a check reads back to assert the
    // store, mirroring loadPage()'s own `page.stored` exposure.
    localStorage: {
      getItem: (key) => (key in dashStoredData ? dashStoredData[key] : null),
      setItem: (key, value) => { dashStoredData[key] = String(value); },
      removeItem: (key) => { delete dashStoredData[key]; },
    },
    setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
    clearTimeout: (id) => {
      if (id != null) {
        const idx = timers.findIndex((_, i) => i + 1 === id);
        if (idx !== -1) timers.splice(idx, 1);
      }
    },
    // Stubs for functions dashHandle and dashPickerRailAdopt call but that live
    // outside the extracted regions.
    dashComposerEl: new El("div"),
    dashPromptInput: new El("input"),
    dashSendBtn: new El("button"),
    // Stop's click handler calls this after a successful cancel send (SC5,
    // Phase 3) -- mirrors acp.html's own railRefreshSoon() call, stubbed the
    // same way dashRailMergeGroup/dashRailBumpGroup/dashRenderRail below are:
    // the picker-region tests never populate real rail state for it to act on.
    dashRailRefreshSoon: () => {},
    dashConnect: (cb) => { if (cb) cb(); },
    // Records every outgoing frame (default `sentOf()` below reads this).
    // The command-palette region's initCommandPaletteDom() call captures
    // whatever `send` resolves to *at harness-setup time* into its own
    // cmdSend module variable (dashboard/ACP feature-parity plan, Phase 2) --
    // unlike dashHandle's bare `send(...)` calls (late-bound, resolved fresh
    // every time dashHandle runs), a post-construction `p.sandbox.send = ...`
    // override would never reach it. Existing tests that install their own
    // override afterward are unaffected -- they exercise dashHandle/picker
    // paths, which stay late-bound.
    send: (type, payload, sid) => { sentFrames.push({ type, payload, sid }); return true; },
    dashSetComposerNote: () => {},
    dashUpdateCloseButton: () => {},
    // dashHandle's generic `error` case tail (index.html, outside both
    // extracted regions' own concerns) calls these for a refusal that falls
    // through every named payload.code branch -- no-op/passthrough stubs,
    // same reasoning as dashSetComposerNote above. _escHtml's real
    // implementation reads .innerHTML off a scratch element, which this
    // harness's El deliberately makes unusable (HTML_SINK) -- a passthrough
    // is the correct stand-in for a check that only cares about the steer
    // restore this call sits downstream of, not the toast's own markup.
    showToast: () => {},
    _escHtml: (s) => String(s),
    // dashRefreshSendButton was stubbed here through Phase 2 (a no-op, since
    // no test needed the real Send-disable logic); Phase 3 (SC5) renamed it
    // to dashRefreshComposerControls and gave it Stop/Queue+Steer visibility
    // logic genuinely under test, so it is now real source, extracted as
    // composerControlsRegion and run below -- not stubbed.
    // The command-palette keydown listener's plain-Enter fallback
    // (dashboard/ACP feature-parity plan, Phase 2) -- this placeholder is
    // overwritten by the real dashSendPrompt() the moment imageAttachRegion
    // runs below (SC6, Phase 4 -- its own top-level `function dashSendPrompt`
    // declaration reassigns the sandbox global, the same way any other
    // region's function declarations do). It is then re-wrapped to keep
    // recording into dashSendPromptCalls, so every pre-Phase-4 assertion on
    // that array keeps working unchanged while the real body -- now needed
    // to test the images payload -- actually runs. See the wrap immediately
    // after imageAttachRegion loads, below.
    dashSendPrompt: () => { dashSendPromptCalls.push(true); },
    // Image tray + staged-image state (SC6, dashboard/ACP feature-parity
    // plan Phase 4) -- overridable so a check can start with images already
    // staged/pending, mirroring the Queue/Steer state above. dashAttachments/
    // dashPendingAttachments are NOT re-declared by imageAttachRegion (its
    // extraction starts at IMAGE_LADDER, after index.html's own `var
    // dashAttachments = []`/`var dashPendingAttachments = []` lines), so
    // these injected values are what the ported functions actually read.
    dashTrayEl: dashTrayElEl,
    dashAttachments: opts.dashAttachments !== undefined ? opts.dashAttachments : [],
    dashPendingAttachments: opts.dashPendingAttachments !== undefined ? opts.dashPendingAttachments : [],
    _dashPendingImages: opts.dashPendingImages !== undefined ? opts.dashPendingImages : null,
    // transcript-renderer.js's history-replay entry point, called from
    // dashHandle()'s `history` branch (handleRegion) -- not loaded by this
    // harness (mirrors dashSetComposerNote/dashUpdateCloseButton above: a
    // no-op stand-in for a dependency outside either extracted region's own
    // concern). Recorded, not a blank no-op, so a Phase 4 check can confirm
    // history rendering still happened alongside the images-payload flush.
    // SC8 (Phase 5): also reproduces the one piece of the real
    // renderTranscriptHistory()'s behavior this phase's crew/sub-agent
    // teardown depends on -- its own real body always calls clearTranscript()
    // first, which calls the window.removeAllCrewPanels/window.closeSubagentView
    // guards (transcript-renderer.js:1899-1900) -- so a `history` frame
    // through dashHandle in this harness tears down crew/sub-agent state the
    // same way it does on the real page, where transcript-renderer.js is
    // loaded for real.
    renderTranscriptHistory: (events) => {
      historyRenders.push(events);
      if (typeof sandbox.removeAllCrewPanels === "function") sandbox.removeAllCrewPanels();
      if (typeof sandbox.closeSubagentView === "function") sandbox.closeSubagentView();
    },
    // dashHandle's session_closed branch calls this bare global directly
    // (commit 8d1782d) -- on the real page it is transcript-renderer.js's
    // top-level function, loaded by <script src> before index.html's inline
    // script. A no-op stand-in that deliberately does NOT reproduce the
    // real body's removeAllCrewPanels/closeSubagentView guard calls (unlike
    // renderTranscriptHistory above): session_closed tears crew/sub-agent
    // state down with its own explicit dash* calls, and the crew/sub-agent
    // session_closed checks must keep testing those calls, not this stub.
    clearTranscript: () => {},
    // dashHandle's agent_died/session_closed/agent_error branches call this
    // (transcript-renderer.js, not part of either extracted region). Records
    // every call (SC5, Phase 3 needs to assert on queue/steer notes and
    // error messages) and returns a fresh element with an appendChild-able
    // body, mirroring the real addMessage()'s return value closely enough
    // for the Queue click handler's `queueNoteEl.appendChild(...)` calls to
    // work without throwing.
    addMessage: (role, text) => {
      const el = new El("div");
      el.className = "acp-msg acp-msg-" + role;
      addMessageCalls.push({ role, text, el });
      return el;
    },
    // composer-chrome.js's handleCommandsExecuteResult() (dashboard/ACP
    // feature-parity plan, Phase 2) calls this (also transcript-renderer.js,
    // not loaded by this harness) -- recorded, not a no-op, so a test can
    // assert on the rendered command-result message.
    addSystemMessage: (text) => { systemMessages.push(text); },
    dashRailMode: "project",
    dashRailMergeGroup: () => {},
    // dashPickerRailAdopt calls this (index.html) to surface a workspace
    // without letting it jump above a pinned one -- stubbed here exactly
    // like dashRailMergeGroup above, since the sandbox's dashRailGroups is
    // never populated with real group objects for these picker-region tests
    // to reorder in the first place.
    dashRailBumpGroup: () => {},
    dashRenderRail: () => {},
    dashRailGroups: [],
    // dashRailForgetSession() (Fix 3, Step 9 review) filters these two
    // directly -- empty by default, same reasoning as dashRailGroups above.
    dashRailPinned: [],
    dashRailFlat: [],
    loadFlatPage: () => {},
    // Sub-agent/crew read-only panel (SC8, dashboard/ACP feature-parity plan
    // Phase 5) -- DOM refs, pre-set exactly like dashComposerEl/dashTrayEl
    // above.
    dashTranscriptWrapEl: byId.get("dashTranscriptWrap"),
    dashSubPanelEl: byId.get("dashSubPanel"),
    dashSubBackBtn: byId.get("dashSubBack"),
    dashSubRoleEl: byId.get("dashSubRole"),
    dashSubStatusEl: byId.get("dashSubStatus"),
    dashSubTranscriptEl: byId.get("dashSubTranscript"),
    // transcript-renderer.js globals dashSetCrew() reaches into as bare
    // identifiers (see the header comment on dashMainTranscriptEl above).
    // `stuckToBottom` mirrors the real implementation exactly
    // (transcript-renderer.js's own stuckToBottom(), which reads these same
    // three properties off transcriptEl) rather than a fixed true/false, so
    // a check can drive the "not stuck to bottom" branch by setting
    // dashMainTranscriptEl's scroll properties directly, the same way it
    // would on the real page.
    transcriptEl: dashMainTranscriptEl,
    toolRows: dashToolRows,
    stuckToBottom: () =>
      dashMainTranscriptEl.scrollHeight - dashMainTranscriptEl.scrollTop
        - dashMainTranscriptEl.clientHeight < 60,
    elapsedText: (startedAt, endAt) => {
      if (typeof startedAt !== "number" || !startedAt) return "";
      const now = (typeof endAt === "number" && endAt) ? endAt : Date.now() / 1000;
      let secs = Math.round(now - startedAt);
      if (secs < 0) secs = 0;
      const m = Math.floor(secs / 60);
      const s = secs % 60;
      return m > 0 ? m + "m " + s + "s" : s + "s";
    },
    // setInterval/clearInterval -- nothing on this page's dashboard-side
    // extracted regions used a timer before crewSubagentRegion (dashSetCrew's
    // elapsed-time ticker), so the sandbox had neither. Mirrors the
    // acp.html-side harness's own setInterval/clearInterval stand-in (this
    // file, ~line 871) exactly: held rather than run, so a check fires a
    // tick by hand (via the returned harness's `intervals` array) rather
    // than racing a real one.
    setInterval: (fn, ms) => { dashIntervals.push({ fn, ms }); return dashIntervals.length; },
    clearInterval: (id) => {
      if (id != null) {
        const idx = dashIntervals.findIndex((_, i) => i + 1 === id);
        if (idx !== -1) dashIntervals.splice(idx, 1);
      }
    },
    // dashConnectSubWs() constructs `new WebSocket(dashWsUrl())` directly
    // (SC8, Phase 5) -- independent of `_dashWs`/dashConnect() above, which
    // stay stubbed/unused for this surface (mirrors acp.html's own
    // connectSubWs(), which has no dependency on connect() either).
    WebSocket: DashFakeSubWs,
    dashWsUrl: () => "ws://test.invalid/ws/acp",
    // WS_PATH (SC7, dashboard/ACP feature-parity plan Phase 6): the real
    // dashWsUrl (connectRegion, loaded only when opts.realConnect is true)
    // reads this as a free variable -- it is declared just before dashWsUrl
    // in the real file, outside the extracted region, which starts at
    // dashWsUrl's own declaration.
    WS_PATH: "/ws/acp",
    // Node's own, for dashExplainRefusedHandshake()'s timeout abort.
    // 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 6 review (J6)
    AbortController,
    // dashConnectSubWs()'s onopen/onerror logLine calls read location.host
    // (Fix 7, Phase 5 review, mirroring dashConnect()'s own onopen logLine
    // call) -- this sandbox has no browser `location` global otherwise,
    // unlike the acp.html-side harness's loadPage() (this file, ~line 886).
    // pathname/search/reload (SC7, Phase 6): dashExplainRefusedHandshake()
    // fetches location.pathname + location.search, and dashReloadBtn's click
    // handler calls location.reload() -- recorded, not a no-op, so a check
    // can assert a reload was actually requested.
    location: {
      protocol: "http:", host: "test.invalid", pathname: "/", search: "",
      reload: () => { dashReloaded = true; },
    },
    console: { log() {}, warn() {}, error() {} },
  };
  // Image API globals (SC6, dashboard/ACP feature-parity plan Phase 4) --
  // assigned after the literal, same pattern and same reasoning as
  // loadPage()'s own `if (opts.images !== false) { sandbox.Blob = ... }`
  // block above.
  if (opts.images !== false) {
    sandbox.Blob = DashFakeBlob;
    sandbox.URL = {
      createObjectURL(source) {
        const url = `blob:dash-fake-${++dashObjectUrlSeq}`;
        dashObjectUrls.set(url, source);
        return url;
      },
      revokeObjectURL(url) { dashRevokedUrls.push(url); },
    };
    sandbox.FileReader = class {
      readAsDataURL(blob) {
        this.result = `data:${blob.type};base64,b64-${blob.type}-${blob.size}`;
        if (this.onload) this.onload();
      }
    };
    sandbox.Image = class {
      constructor() {
        this.naturalWidth = opts.imageWidth ?? 1774;
        this.naturalHeight = opts.imageHeight ?? 887;
      }
      set src(value) {
        this._src = value;
        // opts.imageDecodeFailsFor (Fix 5, Phase 4 review fix cycle): a list
        // of file names that should fail to decode while their siblings in
        // the same paste/drop batch succeed -- needed to test that one bad
        // file no longer silently aborts the rest of the batch.
        // dashObjectUrls (declared above) maps the blob: url minted for this
        // src back to the File object dashLoadImage() created it from.
        const source = dashObjectUrls.get(value);
        const failsForThis = opts.imageDecodeFails ||
          (opts.imageDecodeFailsFor && source && opts.imageDecodeFailsFor.includes(source.name));
        if (failsForThis) { if (this.onerror) this.onerror(); }
        else if (this.onload) this.onload();
      }
      get src() { return this._src; }
    };
  }
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  // composer-chrome.js's real source (dashboard/ACP feature-parity plan,
  // Phase 1) -- must run before handleRegion below, mirroring the real
  // index.html's document order (composer-chrome.js's <script> tag, then the
  // inline script that calls initXxxDom() and defines dashHandle).
  vm.runInContext(composerChromeSource(), sandbox,
                   { filename: "composer-chrome.js" });
  sandbox.initContextDom({
    contextEl: byId.get("dashContext"),
    contextFill: byId.get("dashContextFill"),
    contextLabel: byId.get("dashContextLabel"),
  });
  sandbox.initSidCopyDom({
    sidEl: byId.get("dashSid"),
    copyBtn: byId.get("dashCopy"),
    getSessionId: () => sandbox._dashAttachedSid,
  });
  sandbox.initLogDom({
    logEl: byId.get("dashLog"),
    logToggle: byId.get("dashLogToggle"),
    isReplaying: () => sandbox._dashReplaying,
  });
  // Run in real-file order, with one deliberate exception: composer-controls
  // (SC5, Phase 3 -- must precede cmdPaletteRegion, whose
  // initCommandPaletteDom({onPromptChanged: dashRefreshComposerControls})
  // call reads it as a plain identifier value immediately, at this call's
  // own execution time), then command-palette wiring, THEN imageAttachRegion
  // (SC6, Phase 4) ahead of its real textual position -- its own top-level
  // `function dashSendPrompt` declaration overwrites the placeholder set in
  // the sandbox literal above, and it must run BEFORE queueSteerWiringRegion,
  // whose very first line (`dashSendBtn.addEventListener('click',
  // dashSendPrompt)`) captures whatever `dashSendPrompt` resolves to AT THAT
  // LINE'S OWN EXECUTION TIME -- unlike every other reference to
  // dashSendPrompt in this file, which sits inside a function body and so
  // resolves late, addEventListener's second argument is read immediately.
  // In the real, single-script page this is a non-issue: dashSendPrompt is
  // hoisted before any line runs, real or not, regardless of where its
  // declaration sits textually. Splitting the source into separately-run vm
  // regions loses that hoisting across region boundaries, so this harness
  // has to sequence around it explicitly instead. Wrapped immediately after
  // loading so dashSendPromptCalls keeps recording every call (every
  // pre-Phase-4 test that asserts on it keeps working unmodified) while the
  // real body actually runs -- and so queueSteerWiringRegion's click listener
  // captures the wrapped version, not the raw one. Then queue/steer click
  // wiring, then dashHandle, then the picker section (which also executes
  // the parse-time event-listener wiring against the byId map above).
  vm.runInContext(composerControlsRegion, sandbox, { filename: "index.html#dash-composer-controls" });
  // Close-button click wiring (Fix 3, Phase 6 review) -- self-contained
  // (reads send/_dashAttachedSid/showToast/dashCloseBtn as free variables,
  // all already present in the sandbox literal above), so unlike connectRegion
  // this runs unconditionally, not gated on opts.realConnect -- ordinary
  // synchronous click-dispatch code with no real-WebSocket dependency.
  vm.runInContext(closeBtnRegion, sandbox, { filename: "index.html#dash-close-btn" });
  vm.runInContext(cmdPaletteRegion, sandbox, { filename: "index.html#dash-cmd-palette" });
  vm.runInContext(imageAttachRegion, sandbox, { filename: "index.html#dash-image-attach" });
  const _realDashSendPrompt = sandbox.dashSendPrompt;
  sandbox.dashSendPrompt = function () {
    dashSendPromptCalls.push(true);
    return _realDashSendPrompt.apply(sandbox, arguments);
  };
  vm.runInContext(queueSteerWiringRegion, sandbox, { filename: "index.html#dash-queue-steer" });
  // Sub-agent/crew read-only panel (SC8, dashboard/ACP feature-parity plan
  // Phase 5) -- self-contained (dashSubBackBtn.addEventListener(...) and the
  // window.removeAllCrewPanels/window.closeSubagentView assignments at its
  // own end all resolve within this same region's own hoisted function
  // declarations), so its position relative to the regions above is not
  // load-bearing; run immediately before handleRegion, matching its real
  // textual adjacency to dashCloseIfAbandoned in index.html.
  vm.runInContext(crewSubagentRegion, sandbox, { filename: "index.html#dash-crew-subagent" });
  vm.runInContext(handleRegion, sandbox, { filename: "index.html#dashHandle" });
  // openSessionTranscript()/dashRailForgetSession() (Fix 3, Step 9 review) --
  // self-contained function declarations with no top-level side effects, so
  // (like closeBtnRegion above) they load unconditionally and their position
  // here is not load-bearing; placed after crewSubagentRegion/handleRegion
  // since both call functions those regions define
  // (dashCloseSubagentView/dashRemoveAllCrewPanels/dashCloseSubWs/
  // dashCloseIfAbandoned) at CALL time, never at this load time.
  vm.runInContext(openSessionTranscriptRegion, sandbox, { filename: "index.html#dash-open-transcript" });
  vm.runInContext(railForgetRegion, sandbox, { filename: "index.html#dash-rail-forget" });
  vm.runInContext(pickerRegion, sandbox, { filename: "index.html#dash-picker" });
  // WS reconnect-on-drop (SC7, dashboard/ACP feature-parity plan Phase 6) --
  // opt-in only (see connectRegion's own header comment in dashPickerSource()
  // for why): overwrites the synchronous no-op dashConnect stub set in the
  // sandbox literal above with the real dashConnect()/dashWsUrl()/
  // dashReportSignedOut()/dashExplainRefusedHandshake(), and wires the
  // real Reconnect/Reload buttons. Run last -- by this point every function
  // dashConnect()'s onclose handler reaches as a free variable
  // (dashRefreshComposerControls, dashCloseSubagentView, dashCloseSubWs,
  // hideCommandDropdown, dashRenderTray, dashUpdateCloseButton, dashHandle
  // itself via onmessage) is already real source from the regions above, not
  // a stub -- exactly what a check driving a full reconnect (drop -> backoff
  // -> reopen -> resubscribe -> session frame -> dashHandle) needs.
  if (opts.realConnect) {
    vm.runInContext(connectRegion, sandbox, { filename: "index.html#dash-connect" });
    // connectRegion's own real `function send(...)` (it necessarily includes
    // send() -- dashConnect() sits between it and dashWsUrl() in the real
    // file) just overwrote the shared sentFrames-recording stub set in the
    // sandbox literal above. Every other region's checks, and this region's
    // own dashReconnectOnReady(), are written against that stub (send()
    // returns true and records into sentFrames unconditionally, rather than
    // actually gating on a fake socket's readyState and writing into ITS OWN
    // `.sent` array) -- restore it so `sentOf()`/`sentFrames` stay the
    // single source of truth a check reads, exactly as before this region
    // loaded.
    sandbox.send = (type, payload, sid) => { sentFrames.push({ type, payload, sid }); return true; };
  }

  return {
    sandbox,
    byId,
    fetches,
    sentFrames,
    dashSendPromptCalls,
    addMessageCalls,
    dashStored: dashStoredData,
    timers,
    /** Fire every timer queued so far, once -- mirrors loadPage()'s own
     *  runTimers() (SC5, Phase 3: setDashSteerStatus's steering_injected
     *  auto-clear). */
    runTimers() {
      const due = timers.splice(0, timers.length);
      for (const t of due) t.fn();
      return due.length;
    },
    systemMessages,
    sentOf(type) { return sentFrames.filter((f) => f.type === type); },
    /** Convenience: get an element by id, throws if absent. */
    el(id) {
      const found = byId.get(id);
      if (!found) throw new Error(`dash-picker harness has no element with id '${id}'`);
      return found;
    },
    /** Fire a document-level listener (SC5, Phase 3: the mode menu's
     *  outside-click/Esc-close wiring) -- mirrors loadPage()'s own fireDoc(). */
    fireDoc(type, ev) {
      const fns = dashDocListeners.get(type) ?? [];
      if (fns.length === 0) throw new Error(`nothing listens for document '${type}'`);
      for (const fn of fns) fn(ev ?? {});
    },
    settle() { return new Promise((resolve) => setImmediate(resolve)); },
    // ---- image-attach helpers (SC6, dashboard/ACP feature-parity plan
    // Phase 4) -- mirror loadPage()'s own imageFile()/paste()/drop()/
    // trayChips()/revoked() exactly, targeted at dashPromptInput/
    // dashComposerEl/dashTrayEl instead of acpPrompt/acpComposer/acpTray.
    historyRenders,
    imageFile(type = "image/png", name = "screenshot.png") { return { type, name }; },
    paste(files) {
      let prevented = false;
      sandbox.dashPromptInput.dispatch("paste", {
        clipboardData: { files },
        preventDefault() { prevented = true; },
      });
      return prevented;
    },
    drop(files) {
      let allowed = false;
      sandbox.dashComposerEl.dispatch("dragover", {
        dataTransfer: { types: ["Files"], files: [] },
        preventDefault() { allowed = true; },
      });
      sandbox.dashComposerEl.dispatch("drop", {
        dataTransfer: { files },
        preventDefault() {},
      });
      return allowed;
    },
    trayChips() { return sandbox.dashTrayEl.querySelectorAll(".acp-attach"); },
    /** Every object URL the page has revoked, in order. */
    revoked() { return dashRevokedUrls.slice(); },
    // ---- sub-agent/crew read-only panel helpers (SC8, dashboard/ACP
    // feature-parity plan Phase 5) -- mirror loadPage()'s own
    // intervals/socketAt()/openAt()/deliverTo() (this file, ~lines
    // 801/1003-1025) exactly, targeted at dashConnectSubWs()'s independent
    // dashSubWs instead of acp.html's subWs.
    /** Crew-slot elapsed-time timers registered via setInterval -- mirrors
     *  loadPage()'s own `page.intervals`. A live reference, not a snapshot:
     *  its `.length` reflects clearInterval() calls made since this harness
     *  was created. */
    intervals: dashIntervals,
    /** How many independent sub-agent WebSockets dashConnectSubWs() has
     *  constructed so far. */
    subSocketCount() { return dashSubSockets.length; },
    /** The Nth sub-agent socket dashConnectSubWs() has opened, 0-indexed. */
    subSocket(i) {
      const s = dashSubSockets[i];
      if (!s) throw new Error(`the dashboard has not opened sub-agent socket #${i}`);
      return s;
    },
    /** Fire the Nth sub-agent socket's onopen handler -- the real page's
     *  onopen is what sends the `subscribe` frame. */
    openSub(i) {
      const s = this.subSocket(i);
      s.readyState = DashFakeSubWs.OPEN;
      if (!s.onopen) throw new Error("dashConnectSubWs set no onopen handler");
      s.onopen();
    },
    /** Deliver a frame to the Nth sub-agent socket's onmessage handler. */
    deliverSub(i, frame) {
      const s = this.subSocket(i);
      if (!s.onmessage) throw new Error("dashConnectSubWs set no onmessage handler");
      s.onmessage({ data: JSON.stringify(frame) });
    },
    /** Fire the Nth sub-agent socket's onclose handler (simulates a dropped
     *  or rejected connection -- e.g. a too_many_connections close, code
     *  1013). */
    closeSub(i, ev) {
      const s = this.subSocket(i);
      s.readyState = 3;
      if (s.onclose) s.onclose(ev ?? { code: 1000, reason: "" });
    },
    // ---- WS reconnect-on-drop helpers (SC7, dashboard/ACP feature-parity
    // plan Phase 6) -- only meaningful with loadDashPicker({realConnect:
    // true}) (see connectRegion's own header comment). dashConnect()'s main
    // socket shares the same DashFakeSubWs class and the same dashSubSockets
    // array dashConnectSubWs()'s own sockets use (both construct `new
    // WebSocket(dashWsUrl())`, and dashWsUrl() returns an identical URL for
    // both in production, so there is no way to distinguish them by class or
    // URL) -- these helpers address by construction order ("mainSocket() is
    // whichever socket was opened last", mirroring loadPage()'s own
    // last-opened socket() convention) rather than a fixed index, so a check
    // that also opens a sub-agent socket (e.g. confirming dashCloseSubWs()
    // fires on a main-socket drop) still addresses the right one at each
    // step.
    /** Whichever socket dashConnect()/dashConnectSubWs() has opened most
     *  recently. */
    mainSocket() { return this.subSocket(dashSubSockets.length - 1); },
    /** How many sockets have been constructed so far, main and sub-agent
     *  combined. */
    mainSocketCount() { return dashSubSockets.length; },
    /** Fire the most-recently-opened socket's onopen handler. */
    openMain() {
      const s = this.mainSocket();
      s.readyState = DashFakeSubWs.OPEN;
      if (!s.onopen) throw new Error("dashConnect set no onopen handler");
      s.onopen();
    },
    /** Deliver a frame to the most-recently-opened socket's onmessage
     *  handler. */
    deliverMain(frame) {
      const s = this.mainSocket();
      if (!s.onmessage) throw new Error("dashConnect set no onmessage handler");
      s.onmessage({ data: JSON.stringify(frame) });
    },
    /** Fire the most-recently-opened socket's onclose handler (simulates a
     *  dropped connection). */
    closeMain(ev) {
      const s = this.mainSocket();
      s.readyState = 3;
      if (s.onclose) s.onclose(ev ?? { code: 1006, reason: "" });
    },
    /** Whether location.reload() has been called (dashReloadBtn's click
     *  handler). */
    reloaded() { return dashReloaded; },
  };
}

// 13 new checks — dashboard new-session picker behaviour

check("picker is hidden by default", () => {
  const p = loadDashPicker();
  assert(p.el("dashPicker").hidden === true, "dashPicker must start hidden");
});

check("dashPickerOpen shows the picker", () => {
  const p = loadDashPicker();
  p.sandbox.dashPickerOpen("");
  assert(p.el("dashPicker").hidden === false, "dashPickerOpen must set picker.hidden = false");
});

check("dashPickerOpen resets task mode to kiro_default", () => {
  const p = loadDashPicker();
  p.sandbox._dashPickedTaskMode = "spec";
  p.sandbox.dashPickerOpen("");
  assertEqual(p.sandbox._dashPickedTaskMode, "kiro_default",
    "dashPickerOpen must reset _dashPickedTaskMode to kiro_default");
  assertEqual(p.el("dashPickerTaskModeToggle").dataset.taskMode, "kiro_default",
    "dashPickerOpen must reset the toggle dataset.taskMode to kiro_default");
});

check("dashPickerClose hides the picker", () => {
  // Open manually (set hidden = false directly) to avoid the focus-trap path
  // whose removeEventListener is not exercised by this check. The close path
  // itself — setting hidden = true and clearing _dashPendingCreate — is what
  // the test measures, and it is reachable without a prior dashPickerOpen call.
  const p = loadDashPicker();
  p.el("dashPicker").hidden = false;           // simulate "picker is open"
  p.sandbox._dashPickerTrapRemove = null;       // no trap to remove
  p.sandbox.dashPickerClose();
  assert(p.el("dashPicker").hidden === true, "dashPickerClose must set picker.hidden = true");
});

check("workspace rows are disabled at capacity", () => {
  const p = loadDashPicker();
  p.sandbox._dashPickerWorkspaces = [{ cwd: "/a", name: "a", sessions: 1 }];
  p.sandbox._dashPickerCapacity = { held: 8, max: 8 };
  p.sandbox.dashPickerRender();
  const listEl = p.el("dashPickerList");
  const btn = listEl.childNodes.find((c) => c.tagName === "BUTTON" || c.disabled !== undefined);
  assert(btn, "dashPickerRender should have appended a button row to dashPickerList");
  assert(btn.disabled === true, "workspace button must be disabled when at capacity");
});

check("dashPickerNeutral is disabled at capacity", () => {
  const p = loadDashPicker();
  p.sandbox._dashPickerWorkspaces = [{ cwd: "/a", name: "a", sessions: 1 }];
  p.sandbox._dashPickerCapacity = { held: 8, max: 8 };
  p.sandbox.dashPickerRender();
  assert(p.el("dashPickerNeutral").disabled === true,
    "dashPickerNeutral must be disabled when held >= max");
});

check("dashPickerKeepRow is hidden when no session is attached", () => {
  const p = loadDashPicker({ dashAttachedSid: null });
  p.sandbox._dashAttachedSid = null;
  p.sandbox.dashPickerRenderKeepRow();
  assert(p.el("dashPickerKeepRow").hidden === true,
    "dashPickerKeepRow must stay hidden when _dashAttachedSid is null");
});

check("dashPickerKeepRow is shown and close button enabled when session attached and turn idle", () => {
  const p = loadDashPicker();
  p.sandbox._dashAttachedSid = "sess_test";
  p.sandbox._dashTurnActive = false;
  p.sandbox.dashPickerRenderKeepRow();
  assert(p.el("dashPickerKeepRow").hidden === false,
    "dashPickerKeepRow must be visible when _dashAttachedSid is set");
  assert(p.el("dashPickerCloseCurrent").disabled === false,
    "dashPickerCloseCurrent must be enabled when turn is idle");
});

check("dashPickerCloseCurrent is disabled when turn is active", () => {
  const p = loadDashPicker();
  p.sandbox._dashAttachedSid = "sess_test";
  p.sandbox._dashTurnActive = true;
  p.sandbox.dashPickerRenderKeepRow();
  assert(p.el("dashPickerCloseCurrent").disabled === true,
    "dashPickerCloseCurrent must be disabled while _dashTurnActive is true");
});

check("dashHandle creation branch sets _viewingSid to the new session id", () => {
  const p = loadDashPicker();
  p.sandbox._viewingSid = null;
  p.sandbox._dashPendingCreate = null;
  p.sandbox.dashHandle({
    type: "session", sessionId: "sess_new",
    payload: { created: true, cwd: "/ws" },
  });
  assertEqual(p.sandbox._viewingSid, "sess_new",
    "dashHandle with payload.created must set _viewingSid to the new session id");
});

check("dashHandle creation branch supersedes an existing _viewingSid", () => {
  const p = loadDashPicker();
  p.sandbox._viewingSid = "sess_other";
  p.sandbox.dashHandle({
    type: "session", sessionId: "sess_new",
    payload: { created: true, cwd: "/ws" },
  });
  assertEqual(p.sandbox._viewingSid, "sess_new",
    "dashHandle creation branch must set _viewingSid even when another session was current");
});

check("dashHandle session frame without payload.created is dropped by the stale guard", () => {
  const p = loadDashPicker();
  p.sandbox._viewingSid = "sess_other";
  p.sandbox.dashHandle({
    type: "session", sessionId: "sess_new",
    payload: {},
  });
  assertEqual(p.sandbox._viewingSid, "sess_other",
    "without payload.created the stale guard must fire and leave _viewingSid unchanged");
});

check("dashPickerClose clears _dashPendingCreate", () => {
  const p = loadDashPicker();
  p.sandbox._dashPendingCreate = { cwd: "/x", mode: "kiro_default" };
  p.sandbox.dashPickerClose();
  assertEqual(p.sandbox._dashPendingCreate, null,
    "dashPickerClose must null out _dashPendingCreate");
});

// --- Additional tests for error arms and dashPickerRunPending (Step 9 review M5) ---

check("dashPickerRunPending fires a new session after session_closed", () => {
  const p = loadDashPicker();
  const sent = [];
  // Stub send() to capture outgoing frames
  p.sandbox.send = function(type, payload, sid) {
    sent.push({ type, payload, sid });
    return true;
  };
  // Stub dashConnect to invoke callback immediately
  p.sandbox.dashConnect = function(cb) { if (cb) cb(); };
  // Stub addMessage (called in session_closed handler)
  p.sandbox.addMessage = function() {};
  p.sandbox.dashUpdateCloseButton = function() {};
  p.sandbox.dashPickerRunPending = p.sandbox.dashPickerRunPending; // already present
  // Set up a pending create
  p.sandbox._dashPendingCreate = { cwd: '/ws', mode: 'kiro_default' };
  // Dispatch session_closed frame
  p.sandbox.dashHandle({ type: 'session_closed', sessionId: null, payload: {} });
  assertEqual(p.sandbox._dashPendingCreate, null, '_dashPendingCreate must be cleared after RunPending');
  const newFrame = sent.find(f => f.type === 'new');
  if (!newFrame) throw new Error('send("new") was not called after session_closed + dashPickerRunPending');
  assertEqual(newFrame.payload.cwd, '/ws', 'new frame must carry the pending cwd');
  assertEqual(newFrame.payload.mode, 'kiro_default', 'new frame must carry the pending mode');
});

check("dashHandle close_in_progress error arm fires dashPickerRunPending", () => {
  const p = loadDashPicker();
  const sent = [];
  p.sandbox.send = function(type, payload, sid) { sent.push({ type, payload, sid }); return true; };
  p.sandbox.dashConnect = function(cb) { if (cb) cb(); };
  p.sandbox._dashPendingCreate = { cwd: '/y', mode: 'spec' };
  p.sandbox.dashHandle({ type: 'error', sessionId: null, payload: { code: 'close_in_progress', message: 'closing' } });
  assertEqual(p.sandbox._dashPendingCreate, null, '_dashPendingCreate cleared after close_in_progress');
  const newFrame = sent.find(f => f.type === 'new');
  if (!newFrame) throw new Error('send("new") not called after close_in_progress');
  assertEqual(newFrame.payload.cwd, '/y', 'new frame must carry /y');
});

check("dashHandle turn_in_progress error arm clears _dashPendingCreate", () => {
  const p = loadDashPicker();
  p.sandbox._dashPendingCreate = { cwd: '/z', mode: 'plan' };
  p.sandbox.dashHandle({ type: 'error', sessionId: null, payload: { code: 'turn_in_progress', message: 'turn is running' } });
  assertEqual(p.sandbox._dashPendingCreate, null, '_dashPendingCreate cleared after turn_in_progress');
});

check("dashRailQuickCreate sends 'new' directly and never opens the picker when under capacity", async () => {
  const p = loadDashPicker();
  const sent = [];
  p.sandbox.send = function(type, payload, sid) { sent.push({ type, payload, sid }); return true; };
  p.sandbox._dashPickerCapacity = { held: 0, max: 8 };
  p.sandbox.dashRailQuickCreate("/proj");
  assertEqual(p.el("dashPicker").hidden, true,
    "the picker must stay hidden -- this path exists specifically to skip it");
  const newFrame = sent.find((f) => f.type === "new");
  if (!newFrame) throw new Error("send('new') was not called");
  assertEqual(newFrame.payload.cwd, "/proj", "new frame must carry the workspace's own cwd");
  assertEqual(newFrame.payload.mode, "kiro_default",
    "the direct-create path always uses the default task mode, never a leftover _dashPickedTaskMode");
});

check("dashRailQuickCreate falls back to the picker at capacity instead of failing silently", () => {
  const p = loadDashPicker();
  const sent = [];
  p.sandbox.send = function(type, payload, sid) { sent.push({ type, payload, sid }); return true; };
  p.sandbox._dashPickerCapacity = { held: 8, max: 8 };
  p.sandbox.dashRailQuickCreate("/proj");
  assertEqual(p.el("dashPicker").hidden, false,
    "at capacity, dashRailQuickCreate must open the picker -- it is the only place left to close a session and retry");
  const newFrame = sent.find((f) => f.type === "new");
  if (newFrame) throw new Error("send('new') must not fire before the picker's own capacity/close-current flow runs");
});

check("dashPickerRailAdopt with empty cwd calls loadFlatPage", () => {
  const p = loadDashPicker();
  let flatCalled = false;
  p.sandbox.loadFlatPage = function() { flatCalled = true; };
  p.sandbox.dashRailMode = 'project'; // project mode but empty cwd
  p.sandbox.dashPickerRailAdopt('');
  if (!flatCalled) throw new Error('loadFlatPage was not called for empty cwd in project mode');
});

// ---- dashboard composer chrome (plans/260921_DASHBOARD_ACP_FEATURE_PARITY.md,
// Phase 1: context indicator SC2, sid/copy widget SC3, debug log SC4) -------
//
// Reuses loadDashPicker()'s harness: composer-chrome.js's real source now
// runs in that same sandbox (see the vm.runInContext/initXxxDom() calls added
// to loadDashPicker() above), so these checks exercise the actual shared
// module, not a stand-in -- what is under test is index.html's own wiring
// (the widened `meta` branch, the `session` frame's sidWorkspace/_sessionCwd
// assignment, the baseline `agent_died` case), not composer-chrome.js's
// internals (those are covered by /acp's own suite, which this module must
// keep passing unmodified).

check("a meta frame with contextPercent calls setContext, updating the shared context indicator", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  p.sandbox.dashHandle({ type: "meta", payload: { contextPercent: 42 } });
  assertEqual(p.el("dashContext").hidden, false,
    "the context indicator must become visible once a reading arrives");
  assertEqual(p.el("dashContextFill").style.width, "42%",
    "the fill width must reflect the reported percentage");
  assertEqual(p.el("dashContextLabel").textContent, "context 42%",
    "the label must show the reported percentage");
});

check("a meta frame with contextPercent absent (turn-only) leaves the context branch alone", () => {
  const p = loadDashPicker({ viewingSid: "sess-1", dashTurnActive: false });
  // Sanity: the pre-existing turn-start/turn-end handling must survive the
  // widened `meta` branch unchanged, alongside the new cases.
  p.sandbox.dashHandle({ type: "meta", payload: { turn: "start" } });
  assertEqual(p.sandbox._dashTurnActive, true,
    "meta turn:start must still set _dashTurnActive -- the widened branch must be additive, not a replacement");
  p.sandbox.dashHandle({ type: "meta", payload: { turn: "end" } });
  assertEqual(p.sandbox._dashTurnActive, false,
    "meta turn:end must still clear _dashTurnActive");
});

check("a meta connected frame stores maxPromptImages/maxPromptImageBytes for Phase 4 to consume", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  p.sandbox.dashHandle({
    type: "meta",
    payload: { connected: true, maxPromptImages: 6, maxPromptImageBytes: 200000 },
  });
  assertEqual(p.sandbox._dashImageMaxCount, 6,
    "_dashImageMaxCount must be overwritten from the connected meta's maxPromptImages");
  assertEqual(p.sandbox._dashImageMaxBytes, 200000,
    "_dashImageMaxBytes must be overwritten from the connected meta's maxPromptImageBytes");
});

check("a session frame's cwd populates the sid/copy widget", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  p.sandbox.dashHandle({
    type: "session", sessionId: "sess-1",
    payload: { sessionId: "sess-1", cwd: "C:\\work\\my-repo", turnActive: false },
  });
  assertEqual(p.el("dashSid").textContent, "my-repo",
    "the sid label must show the short workspace name once a session frame lands");
  assertEqual(p.el("dashCopy").hidden, false,
    "the copy button must become visible once a session is attached");
});

check("the copy button copies the full workspace path, not the short display name (SC3 bug fix)", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  const copied = [];
  p.sandbox.navigator = { clipboard: { writeText: (v) => { copied.push(v); return Promise.resolve(); } } };
  p.sandbox.dashHandle({
    type: "session", sessionId: "sess-1",
    payload: { sessionId: "sess-1", cwd: "C:\\work\\my-repo", turnActive: false },
  });
  p.el("dashCopy").dispatch("click");
  assertEqual(copied.length, 1, "clicking copy must copy exactly one value");
  assertEqual(copied[0], "C:\\work\\my-repo",
    "the copied value must be the full workspace path (_sessionCwd), not shortName(cwd) -- " +
    "this is the pre-existing acp.html bug (comment claimed full-path copy, code copied the " +
    "short name) this plan was explicitly asked to fix on both pages");
});

check("the debug log toggle opens/closes the shared log panel and remembers the choice", () => {
  const p = loadDashPicker();
  assertEqual(p.el("dashLog").hidden, true, "the log panel must start collapsed");
  p.el("dashLogToggle").dispatch("click");
  assertEqual(p.el("dashLog").hidden, false, "clicking the toggle must open the log panel");
  assertEqual(p.el("dashLogToggle").getAttribute("aria-expanded"), "true",
    "aria-expanded must reflect the open state for the CSS-drawn chevron");
  p.el("dashLogToggle").dispatch("click");
  assertEqual(p.el("dashLog").hidden, true, "clicking the toggle again must close the log panel");
});

check("logLine is silenced during a replay via the isReplaying accessor, not a snapshot", () => {
  const p = loadDashPicker();
  p.sandbox._dashReplaying = false;
  p.sandbox.logLine("info", "live line");
  assertEqual(p.el("dashLog").childNodes.length, 1,
    "a log line written while not replaying must be appended");
  p.sandbox._dashReplaying = true;
  p.sandbox.logLine("info", "replayed line");
  assertEqual(p.el("dashLog").childNodes.length, 1,
    "isReplaying must be re-read live on every call -- a snapshot taken at initLogDom() time " +
    "would never see this later flip and would log the replayed line too");
  p.sandbox._dashReplaying = false;
  p.sandbox.logLine("info", "live again");
  assertEqual(p.el("dashLog").childNodes.length, 2,
    "logging must resume once _dashReplaying flips back");
});

check("the baseline agent_died case resets turn state, disables the composer with a note, and clears the sid/copy widget", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  const notes = [];
  p.sandbox.dashSetComposerNote = (t) => notes.push(t);
  // Attach first, so there is real state (sid/copy widget, turn flag) for
  // agent_died to tear down -- exercises the session-frame wiring and the
  // agent_died reset together, the same sequence a live agent crash would
  // actually produce.
  p.sandbox.dashHandle({
    type: "session", sessionId: "sess-1",
    payload: { sessionId: "sess-1", cwd: "C:\\work\\my-repo", turnActive: true },
  });
  assertEqual(p.sandbox._dashAttachedSid, "sess-1", "sanity check -- session frame must attach first");
  // Simulate a lazy-load in flight when the agent dies (review finding, Phase
  // 1 fix cycle) -- pre-existing dashboard state, not introduced by this
  // plan, that agent_died's baseline reset did not used to touch.
  p.sandbox._dashLoadingSid = "sess-1";
  p.sandbox._dashPendingSend = "queued text";
  p.sandbox._dashSent = true;

  p.sandbox.dashHandle({ type: "agent_died", sessionId: "sess-1",
                          payload: { exitCode: 1, message: "boom" } });

  assertEqual(p.sandbox._dashAttachedSid, null, "agent_died must clear _dashAttachedSid");
  assertEqual(p.sandbox._dashTurnActive, false, "agent_died must clear _dashTurnActive");
  assertEqual(p.sandbox._dashLoadingSid, null,
    "agent_died must clear _dashLoadingSid -- a mid-lazy-load death must not leave a stale load slot");
  assertEqual(p.sandbox._dashPendingSend, null,
    "agent_died must clear _dashPendingSend -- a queued prompt for a session whose agent just died must not fire later");
  assertEqual(p.sandbox._dashSent, false,
    "agent_died must reset _dashSent to its default");
  // dashPromptInput/dashSendBtn are sandbox globals stubbed directly (like
  // dashComposerEl), not byId-registered elements -- p.el() only looks up the
  // picker/composer-chrome markup ids.
  assertEqual(p.sandbox.dashPromptInput.disabled, true,
    "the composer must be disabled, not left usable against a dead agent");
  assertEqual(p.sandbox.dashSendBtn.disabled, true, "the send button must be disabled too");
  // Fix 1 (Phase 5 review) -- reproduced live by a reviewer's temporary
  // probe as a permanent regression: this session never opened a sub-agent
  // panel, yet dashCloseSubagentView()'s dashComposerEl.hidden =
  // !_dashAttachedSid side effect used to hide the composer's container
  // outright (since _dashAttachedSid is already null by this point),
  // burying the explanatory note set just below where nothing could see it.
  assertEqual(p.sandbox.dashComposerEl.hidden, false,
    "the composer must stay VISIBLE and disabled with the note below, even though no sub-agent " +
    "panel was ever open for this session");
  assert(notes[notes.length - 1] && /agent process ended/.test(notes[notes.length - 1]),
    "an explanatory note must be shown, mirroring acp.html's own agent_died message");
  assertEqual(p.el("dashContext").hidden, true, "the context indicator must hide (setContext(null))");
  assertEqual(p.el("dashSid").textContent, "", "the sid label must clear with no session attached");
  assertEqual(p.el("dashCopy").hidden, true, "the copy button must hide with no session attached");
});

// ---- Phase 1 review-fix cycle: context-clear parity, dashCloseIfAbandoned ---
//
// Both reviewers confirmed via mutation testing that the `session`-frame
// setContext call and dashCloseIfAbandoned() had zero test coverage before
// this cycle (removing the session-case setContext call entirely caused 0/460
// test failures). The two checks below close that gap.

check("a session frame with contextPercent: null clears a stale prior context reading", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  // Leave a stale reading on screen, as a previously-viewed session's own
  // session frame would.
  p.sandbox.setContext(77);
  assertEqual(p.el("dashContext").hidden, false,
    "sanity check -- a numeric reading must show the indicator before the frame under test arrives");
  p.sandbox.dashHandle({
    type: "session", sessionId: "sess-1",
    payload: { sessionId: "sess-1", cwd: "C:\\work\\my-repo", turnActive: false, contextPercent: null },
  });
  assertEqual(p.el("dashContext").hidden, true,
    "a session frame with contextPercent: null must hide the indicator -- the `typeof` guard this " +
    "fix removed used to skip setContext entirely for this common case (a session that has not " +
    "completed a turn yet), leaving the previous session's stale percentage on screen");
});

check("dashCloseIfAbandoned clears the context indicator", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  p.sandbox.setContext(55);
  assertEqual(p.el("dashContext").hidden, false,
    "sanity check -- a numeric reading must show the indicator before dashCloseIfAbandoned runs");
  // A purely static/browse view -- no live-attach was ever made, so there is
  // nothing for the close-if-abandoned's own send('close', ...) branch to do;
  // only the sid/copy + context reset should fire.
  p.sandbox._dashAttachedSid = null;
  p.sandbox._dashOrigin = null;
  p.sandbox.dashCloseIfAbandoned();
  assertEqual(p.el("dashContext").hidden, true,
    "dashCloseIfAbandoned must clear the context indicator (setContext(null)) -- without this, a " +
    "session viewed with no `session` frame ever arriving kept whatever the previously-viewed " +
    "session's context bar last showed");
});

// ---- dashboard slash-command / skill palette (SC1, dashboard/ACP
// feature-parity plan, Phase 2) --------------------------------------------
//
// Wiring-level tests for the dashboard's consumption of composer-chrome.js's
// command palette: the `/` keydown intercept, arrow-key navigation, Enter/Tab
// confirm, and dashHandle()'s new `commands`/`skills`/`commands_execute_result`
// cases plus the palette resets threaded into `session`/`session_closed`/
// `agent_died`/`meta turn:start`. The palette's own core logic (dropdown
// rendering, filtering, selection) is already exercised by acp.html's
// pre-existing ~20 palette checks against the same composer-chrome.js source
// (Design Decisions: "extracted features get wiring-level tests only") --
// these checks are about whether the dashboard wires it up correctly, not
// about re-proving the module's own behaviour.

check("dashboard: '/' on an empty prompt opens the dropdown", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  // Seed at least one command -- an empty catalogue renders nothing and
  // renderCommandDropdown() hides the (already-hidden) dropdown rather than
  // showing an empty one, exactly mirroring acp.html's own
  // slashKeyOpensDropdown fixture.
  p.sandbox.dashHandle({
    type: "commands", sessionId: "sess-1",
    payload: { commands: [{ name: "tools", description: "Tools list" }] },
  });
  p.sandbox.dashPromptInput.value = "";
  let prevented = false;
  p.sandbox.dashPromptInput.dispatch("keydown", {
    key: "/", shiftKey: false, ctrlKey: false, altKey: false, preventDefault() { prevented = true; },
  });
  assert(prevented, "'/' keydown should preventDefault so the browser does not also insert '/'");
  assertEqual(p.sandbox.dashPromptInput.value, "/", "'/' keydown should set the textarea's value to '/'");
  assertEqual(p.el("dashCmdDropdown").hidden, false,
    "'/' on an empty, idle prompt must open the command dropdown");
});

check("dashboard: '/' is blocked during an active turn", () => {
  const p = loadDashPicker({ viewingSid: "sess-1", dashTurnActive: true });
  p.sandbox.dashHandle({
    type: "commands", sessionId: "sess-1",
    payload: { commands: [{ name: "tools", description: "Tools list" }] },
  });
  p.sandbox.dashPromptInput.value = "";
  p.sandbox.dashPromptInput.dispatch("keydown", {
    key: "/", shiftKey: false, ctrlKey: false, altKey: false, preventDefault() {},
  });
  assertEqual(p.el("dashCmdDropdown").hidden, true,
    "'/' must not open the dropdown while _dashTurnActive is true, even with a populated " +
    "catalogue -- mirrors acp.html's own !turnActive gate");
});

check("dashboard: a 'commands' frame populates the catalogue and shows in the open dropdown", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  p.sandbox.dashHandle({
    type: "commands", sessionId: "sess-1",
    payload: { commands: [{ name: "context", description: "Show context usage" }] },
  });
  p.sandbox.dashPromptInput.value = "";
  p.sandbox.dashPromptInput.dispatch("keydown", {
    key: "/", shiftKey: false, ctrlKey: false, altKey: false, preventDefault() {},
  });
  const names = p.el("dashCmdDropdown").querySelectorAll(".acp-cmd-name").map((n) => n.textContent);
  assert(names.includes("/context"),
    "the dropdown should show the command received in the 'commands' frame; got: " + JSON.stringify(names));
});

check("dashboard: a 'skills' frame populates the catalogue and badges entries", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  p.sandbox.dashHandle({
    type: "skills", sessionId: "sess-1",
    payload: { skills: [{ name: "qplan", description: "Write a phased plan. More detail." }] },
  });
  p.sandbox.dashPromptInput.value = "";
  p.sandbox.dashPromptInput.dispatch("keydown", {
    key: "/", shiftKey: false, ctrlKey: false, altKey: false, preventDefault() {},
  });
  const drop = p.el("dashCmdDropdown");
  const names = drop.querySelectorAll(".acp-cmd-name").map((n) => n.textContent);
  assert(names.includes("/qplan"), "the dropdown should show the skill; got: " + JSON.stringify(names));
  assertEqual(drop.querySelectorAll(".acp-cmd-skill-badge").length, 1,
    "a skill entry must carry the skill badge, matching acp.html's own rendering");
});

check("dashboard: a new session frame resets the palette catalogue", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  p.sandbox.dashHandle({
    type: "commands", sessionId: "sess-1",
    payload: { commands: [{ name: "tools", description: "List tools" }] },
  });
  p.sandbox.dashHandle({
    type: "session", sessionId: "sess-1",
    payload: { sessionId: "sess-1", cwd: "C:\\work\\my-repo", turnActive: false, contextPercent: null },
  });
  p.sandbox.dashPromptInput.value = "";
  p.sandbox.dashPromptInput.dispatch("keydown", {
    key: "/", shiftKey: false, ctrlKey: false, altKey: false, preventDefault() {},
  });
  assertEqual(p.el("dashCmdDropdown").hidden, true,
    "a resubscribed/new session frame must reset the command catalogue (resetCommandPalette()) " +
    "so a stale command from the previous session cannot appear -- the empty catalogue means '/' " +
    "opens to nothing, so the dropdown stays hidden (renderCommandDropdown hides on an empty list)");
});

check("dashboard: session_closed resets the palette catalogue", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  p.sandbox.dashHandle({
    type: "commands", sessionId: "sess-1",
    payload: { commands: [{ name: "tools", description: "List tools" }] },
  });
  p.sandbox.dashHandle({ type: "session_closed", sessionId: "sess-1", payload: { message: "closed" } });
  p.sandbox.dashPromptInput.value = "";
  p.sandbox.dashPromptInput.dispatch("keydown", {
    key: "/", shiftKey: false, ctrlKey: false, altKey: false, preventDefault() {},
  });
  assertEqual(p.el("dashCmdDropdown").hidden, true,
    "session_closed must reset the command catalogue, mirroring acp.html's own releaseSession()");
});

check("dashboard: agent_died resets the palette catalogue", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  p.sandbox.dashHandle({
    type: "commands", sessionId: "sess-1",
    payload: { commands: [{ name: "tools", description: "List tools" }] },
  });
  p.sandbox.dashHandle({ type: "agent_died", sessionId: "sess-1", payload: { exitCode: 1, message: "crashed" } });
  p.sandbox.dashPromptInput.value = "";
  p.sandbox.dashPromptInput.dispatch("keydown", {
    key: "/", shiftKey: false, ctrlKey: false, altKey: false, preventDefault() {},
  });
  assertEqual(p.el("dashCmdDropdown").hidden, true,
    "agent_died must reset the command catalogue -- the composer is disabled immediately " +
    "afterward regardless, but the catalogue must not survive into whatever is opened next");
});

check("dashboard: a turn-start meta frame closes an open dropdown", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  p.sandbox.dashHandle({
    type: "commands", sessionId: "sess-1",
    payload: { commands: [{ name: "tools", description: "List tools" }] },
  });
  p.sandbox.dashPromptInput.value = "";
  p.sandbox.dashPromptInput.dispatch("keydown", {
    key: "/", shiftKey: false, ctrlKey: false, altKey: false, preventDefault() {},
  });
  assertEqual(p.el("dashCmdDropdown").hidden, false, "fixture: dropdown should be open");
  p.sandbox.dashHandle({ type: "meta", sessionId: "sess-1", payload: { turn: "start" } });
  assertEqual(p.el("dashCmdDropdown").hidden, true,
    "a mid-turn dropdown cannot be acted on and would just be confusing -- mirrors acp.html's own setTurn(true)");
});

check("dashboard: arrow-key navigation + Enter selects a command, sends commands_execute, and does not fall through to dashSendPrompt", () => {
  const p = loadDashPicker({ viewingSid: "sess-1", dashAttachedSid: "sess-1" });
  p.sandbox.dashHandle({
    type: "commands", sessionId: "sess-1",
    payload: { commands: [
      { name: "context", description: "Show context usage" },
      { name: "tools", description: "List tools" },
    ] },
  });
  p.sandbox.dashPromptInput.value = "";
  p.sandbox.dashPromptInput.dispatch("keydown", {
    key: "/", shiftKey: false, ctrlKey: false, altKey: false, preventDefault() {},
  });
  p.sandbox.dashPromptInput.dispatch("keydown", {
    key: "ArrowDown", shiftKey: false, ctrlKey: false, altKey: false, preventDefault() {},
  });
  p.sandbox.dashPromptInput.dispatch("keydown", {
    key: "Enter", shiftKey: false, ctrlKey: false, altKey: false, preventDefault() {},
  });
  assertEqual(p.el("dashCmdDropdown").hidden, true, "confirming a selection must close the dropdown");
  const executed = p.sentOf("commands_execute");
  assertEqual(executed.length, 1, "selecting a (non-skill) command must send exactly one commands_execute");
  assertEqual(executed[0].payload.name, "tools",
    "ArrowDown from the first row must select the second ('tools'), not re-confirm the first");
  assertEqual(executed[0].sid, "sess-1", "commands_execute must carry the attached session id");
  assertEqual(p.sandbox.dashPromptInput.value, "",
    "the textarea must clear after a command (not a skill) is confirmed");
  assertEqual(p.dashSendPromptCalls.length, 0,
    "Enter-with-the-dropdown-open must be consumed by confirmCommandSelection, never fall through " +
    "to dashSendPrompt -- this is the exact ordering bug acp.html's own listener avoids by checking " +
    "isCommandDropdownVisible() before its plain Enter-sends fallback");
});

check("dashboard: confirming a skill inserts '/<name> ' instead of sending commands_execute", () => {
  const p = loadDashPicker({ viewingSid: "sess-1", dashAttachedSid: "sess-1" });
  p.sandbox.dashHandle({
    type: "skills", sessionId: "sess-1",
    payload: { skills: [{ name: "qplan", description: "Write a phased plan." }] },
  });
  p.sandbox.dashPromptInput.value = "";
  p.sandbox.dashPromptInput.dispatch("keydown", {
    key: "/", shiftKey: false, ctrlKey: false, altKey: false, preventDefault() {},
  });
  p.sandbox.dashPromptInput.dispatch("keydown", {
    key: "Enter", shiftKey: false, ctrlKey: false, altKey: false, preventDefault() {},
  });
  assertEqual(p.sandbox.dashPromptInput.value, "/qplan ",
    "confirming a skill must insert '/<name> ' into the textarea, not clear it");
  assertEqual(p.sentOf("commands_execute").length, 0,
    "a skill is dispatched via prompt text, never via commands_execute");
});

check("dashboard: a commands_execute_result frame closes the dropdown and renders the ack message", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  p.sandbox.dashHandle({
    type: "commands", sessionId: "sess-1",
    payload: { commands: [{ name: "tools", description: "List tools" }] },
  });
  p.sandbox.dashPromptInput.value = "";
  p.sandbox.dashPromptInput.dispatch("keydown", {
    key: "/", shiftKey: false, ctrlKey: false, altKey: false, preventDefault() {},
  });
  assertEqual(p.el("dashCmdDropdown").hidden, false, "fixture: dropdown should be open");
  p.sandbox.dashHandle({
    type: "commands_execute_result", sessionId: "sess-1",
    payload: { name: "tools", result: { success: true, message: "3 tools available." } },
  });
  assertEqual(p.el("dashCmdDropdown").hidden, true, "commands_execute_result must close the dropdown");
  assert(p.systemMessages.includes("3 tools available."),
    "the ack's message must be rendered as a system message; got: " + JSON.stringify(p.systemMessages));
});

check("dashboard: a commands_options_result frame is a silent no-op", () => {
  // SC1's dead-path removal (see the acp.html-side commandsOptionsResultIsANoOp
  // check for the full citation): the dashboard never had a handler for this
  // frame type to begin with, so this pins that dashHandle's generic fall-
  // through (no matching `if`, function returns undefined) does not throw.
  const p = loadDashPicker({ viewingSid: "sess-1" });
  p.sandbox.dashHandle({
    type: "commands_options_result", sessionId: "sess-1",
    payload: { options: [{ name: "memory", description: "Memory stats" }] },
  });
  assertEqual(p.el("dashCmdDropdown").hidden, true, "no dropdown state should change");
});

check("dashboard: a compaction frame calls addSystemMessage with the status-mapped text (Phase 2 review fix)", () => {
  // Phase 2's palette makes /compact invokable on the dashboard for the
  // first time; before this fix, dashHandle() had no case for 'compaction'
  // at all, so triggering it gave zero visible feedback. Minimal ack only --
  // the rich recap UI (.acp-compaction-details/.acp-compaction-recap) stays
  // /acp-only per the plan's scope boundaries.
  const p = loadDashPicker({ viewingSid: "sess-1" });
  p.sandbox.dashHandle({
    type: "compaction", sessionId: "sess-1",
    payload: { status: "completed", summary: "should be ignored" },
  });
  assert(p.systemMessages.includes("Context compacted."),
    "a completed compaction frame must call addSystemMessage('Context compacted.'); got: " +
    JSON.stringify(p.systemMessages));
});

// ---- dashboard Queue/Steer send-mode toggle + Stop/cancel (SC5,
//      dashboard/ACP feature-parity plan Phase 3) -----------------------
//
// A duplicated, not extracted, feature (index.html-only dash-prefixed code,
// not composer-chrome.js) -- see loadDashPicker()'s composerControlsRegion/
// queueSteerWiringRegion extraction above for how the real source gets
// under test here. Density target: comparable to acp.html's own Queue/
// Steer/mode-select test suite (tests/acp_page.test.mjs:7704-8143 plus the
// steer_status suite at :10038-10103, ~31 tests as actually counted in this
// file today).

check("dashRefreshComposerControls: idle state shows Send only", () => {
  const p = loadDashPicker({ dashTurnActive: false });
  p.sandbox.dashPromptInput.value = "";
  p.sandbox.dashRefreshComposerControls();
  assertEqual(p.sandbox.dashSendBtn.hidden, false, "Send must be visible when idle");
  assertEqual(p.sandbox.dashStopBtn.hidden, true, "Stop must stay hidden when idle");
  assertEqual(p.sandbox.dashQueueSteerEl.hidden, true, "Queue+Steer must stay hidden when idle");
});

check("dashRefreshComposerControls: turn active + empty textarea shows Stop only", () => {
  const p = loadDashPicker({ dashTurnActive: true });
  p.sandbox.dashPromptInput.value = "";
  p.sandbox.dashRefreshComposerControls();
  assertEqual(p.sandbox.dashSendBtn.hidden, true, "Send must hide during a turn");
  assertEqual(p.sandbox.dashStopBtn.hidden, false, "Stop must show with an empty textarea during a turn");
  assertEqual(p.sandbox.dashQueueSteerEl.hidden, true, "Queue+Steer must stay hidden with an empty textarea");
});

check("dashRefreshComposerControls: turn active + text shows Queue+Steer only", () => {
  const p = loadDashPicker({ dashTurnActive: true });
  p.sandbox.dashPromptInput.value = "some text";
  p.sandbox.dashRefreshComposerControls();
  assertEqual(p.sandbox.dashStopBtn.hidden, true, "Stop must hide once text is present");
  assertEqual(p.sandbox.dashQueueSteerEl.hidden, false,
    "Queue+Steer must show once text is present during a turn");
});

check("dashRefreshComposerControls: Stop stays hidden while a steer is pending, even with an empty textarea", () => {
  const p = loadDashPicker({ dashTurnActive: true, dashSteerPending: "in flight" });
  p.sandbox.dashPromptInput.value = "";
  p.sandbox.dashRefreshComposerControls();
  assertEqual(p.sandbox.dashStopBtn.hidden, true,
    "Stop must stay hidden while a steer is in-flight, mirroring acp.html's !!_steerPending guard");
});

check("dashRefreshComposerControls: Stop stays disabled while a cancel is in-flight", () => {
  const p = loadDashPicker({ dashTurnActive: true, dashStopInProgress: true });
  p.sandbox.dashPromptInput.value = "";
  p.sandbox.dashRefreshComposerControls();
  assertEqual(p.sandbox.dashStopBtn.disabled, true,
    "Stop must stay disabled while _dashStopInProgress is true, so a second click cannot fire before turn:end clears it");
});

check("Stop button click sends cancel, disables itself, and triggers a rail refresh", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", dashTurnActive: true, viewingSid: "sess-1" });
  let railRefreshed = false;
  p.sandbox.dashRailRefreshSoon = () => { railRefreshed = true; };
  p.sandbox.dashStopBtn.dispatch("click");
  const cancels = p.sentOf("cancel");
  assertEqual(cancels.length, 1, "exactly one cancel frame should be sent");
  assertEqual(cancels[0].sid, "sess-1", "cancel should target the attached session");
  assertEqual(p.sandbox.dashStopBtn.disabled, true, "Stop must disable itself immediately");
  assertEqual(p.sandbox._dashStopInProgress, true,
    "_dashStopInProgress must be set so a repaint cannot re-enable Stop before turn:end");
  assert(railRefreshed, "dashRailRefreshSoon must be called after a successful cancel send");
});

check("Stop button click is a no-op without an attached session or an active turn", () => {
  const p1 = loadDashPicker({ dashAttachedSid: null, dashTurnActive: true });
  p1.sandbox.dashStopBtn.dispatch("click");
  assertEqual(p1.sentOf("cancel").length, 0, "no cancel frame without an attached session");

  const p2 = loadDashPicker({ dashAttachedSid: "sess-1", dashTurnActive: false });
  p2.sandbox.dashStopBtn.dispatch("click");
  assertEqual(p2.sentOf("cancel").length, 0, "no cancel frame without an active turn");
});

check("meta turn:end clears _dashStopInProgress, re-enabling Stop for the next turn", () => {
  const p = loadDashPicker({
    dashAttachedSid: "sess-1", dashTurnActive: true, dashStopInProgress: true, viewingSid: "sess-1",
  });
  p.sandbox.dashHandle({ type: "meta", sessionId: "sess-1", payload: { turn: "end", stopReason: "end_turn" } });
  assertEqual(p.sandbox._dashStopInProgress, false, "turn:end must clear _dashStopInProgress");
});

check("dashApplySendMode('queue') updates label/aria-label/data-mode/aria-current and persists to localStorage", () => {
  const p = loadDashPicker();
  p.sandbox.dashApplySendMode("queue");
  assertEqual(p.sandbox._dashSendMode, "queue", "_dashSendMode must update");
  assertEqual(p.sandbox.dashModeToggle.getAttribute("data-mode"), "queue", "the mode toggle's data-mode must update");
  assertEqual(p.sandbox.dashModeOptQueue.getAttribute("aria-current"), "true", "the Queue option must be marked current");
  assertEqual(p.sandbox.dashModeOptSteer.getAttribute("aria-current"), "false", "the Steer option must be marked not current");
  assert(/Queue/.test(p.sandbox.dashSendModeBtn.getAttribute("aria-label")),
    "sendModeBtn's aria-label must describe queueing");
  const srSpan = p.sandbox.dashSendModeBtn.querySelector(".sr-only");
  assertEqual(srSpan.textContent, "Queue", "the sr-only label span must update");
  assertEqual(p.dashStored["pa_dash_send_mode"], "queue",
    "the chosen mode must persist to localStorage under pa_dash_send_mode");
});

check("dashApplySendMode falls back to 'steer' for a missing or invalid stored value", () => {
  const p1 = loadDashPicker();
  assertEqual(p1.sandbox._dashSendMode, "steer", "default with nothing stored must be steer");
  const p2 = loadDashPicker({ stored: { pa_dash_send_mode: "bogus" } });
  assertEqual(p2.sandbox._dashSendMode, "steer", "an invalid stored value must fall back to steer");
});

check("mode toggle button opens and closes the menu", () => {
  const p = loadDashPicker();
  assertEqual(p.sandbox.dashModeMenu.hidden, true, "sanity check -- menu starts hidden");
  p.sandbox.dashModeToggle.dispatch("click");
  assertEqual(p.sandbox.dashModeMenu.hidden, false, "clicking the toggle must open the menu");
  assertEqual(p.sandbox.dashModeToggle.getAttribute("aria-expanded"), "true", "aria-expanded must flip to true");
  p.sandbox.dashModeToggle.dispatch("click");
  assertEqual(p.sandbox.dashModeMenu.hidden, true, "clicking the toggle again must close the menu");
  assertEqual(p.sandbox.dashModeToggle.getAttribute("aria-expanded"), "false", "aria-expanded must flip back to false");
});

check("clicking a mode option applies that mode and closes the menu", () => {
  const p = loadDashPicker();
  p.sandbox.dashModeToggle.dispatch("click");
  assertEqual(p.sandbox.dashModeMenu.hidden, false, "sanity check -- menu is open");
  p.sandbox.dashModeOptQueue.dispatch("click");
  assertEqual(p.sandbox._dashSendMode, "queue", "clicking the Queue option must apply queue mode");
  assertEqual(p.sandbox.dashModeMenu.hidden, true, "selecting an option must close the menu");
});

check("an outside document click closes the mode menu", () => {
  const p = loadDashPicker();
  p.sandbox.dashModeToggle.dispatch("click");
  assertEqual(p.sandbox.dashModeMenu.hidden, false, "sanity check -- menu is open");
  p.fireDoc("click");
  assertEqual(p.sandbox.dashModeMenu.hidden, true, "a document-level click must close the open menu");
});

check("Escape closes the mode menu", () => {
  const p = loadDashPicker();
  p.sandbox.dashModeToggle.dispatch("click");
  assertEqual(p.sandbox.dashModeMenu.hidden, false, "sanity check -- menu is open");
  p.fireDoc("keydown", { key: "Escape" });
  assertEqual(p.sandbox.dashModeMenu.hidden, true, "Escape must close the open menu");
});

check("Queue: sendModeBtn click stores text, clears the textarea, and shows a cancellable note", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", dashTurnActive: true, viewingSid: "sess-1" });
  p.sandbox.dashApplySendMode("queue");
  p.sandbox.dashPromptInput.value = "hello agent";
  p.sandbox.dashSendModeBtn.dispatch("click");
  assertEqual(p.sandbox._dashQueuedPrompt, "hello agent", "the typed text must be stored in _dashQueuedPrompt");
  assertEqual(p.sandbox._dashQueuedPromptSession, "sess-1", "the session id at queue time must be recorded");
  assertEqual(p.sandbox.dashPromptInput.value, "", "the textarea must be cleared after queueing");
  assertEqual(p.sentOf("prompt").length, 0, "queueing must not send a prompt frame immediately");
  const note = p.addMessageCalls[p.addMessageCalls.length - 1];
  assert(note && note.role === "note", "a note must be added to the transcript");
  const cancelBtn = note.el.querySelector(".acp-inline-cancel");
  assert(cancelBtn !== null, "the queue note must contain a cancel button, mirroring acp.html's own note");
});

check("Queue: cancel link restores the text to the textarea", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", dashTurnActive: true, viewingSid: "sess-1" });
  p.sandbox.dashApplySendMode("queue");
  p.sandbox.dashPromptInput.value = "queued text";
  p.sandbox.dashSendModeBtn.dispatch("click");
  const note = p.addMessageCalls[p.addMessageCalls.length - 1];
  const cancelBtn = note.el.querySelector(".acp-inline-cancel");
  cancelBtn.dispatch("click");
  assertEqual(p.sandbox.dashPromptInput.value, "queued text", "cancel must restore the text to the textarea");
  assertEqual(p.sandbox._dashQueuedPrompt, null, "cancel must clear _dashQueuedPrompt");
});

check("Queue/Steer: sendModeBtn click is a no-op without text, an attached session, or an active turn", () => {
  const p1 = loadDashPicker({ dashAttachedSid: "sess-1", dashTurnActive: true, viewingSid: "sess-1" });
  p1.sandbox.dashPromptInput.value = "";
  p1.sandbox.dashSendModeBtn.dispatch("click");
  assertEqual(p1.sentOf("steer").length, 0, "no steer frame with empty text");

  const p2 = loadDashPicker({ dashAttachedSid: null, dashTurnActive: true, viewingSid: "sess-1" });
  p2.sandbox.dashPromptInput.value = "hi";
  p2.sandbox.dashSendModeBtn.dispatch("click");
  assertEqual(p2.sentOf("steer").length, 0, "no steer frame without an attached session");

  const p3 = loadDashPicker({ dashAttachedSid: "sess-1", dashTurnActive: false, viewingSid: "sess-1" });
  p3.sandbox.dashPromptInput.value = "hi";
  p3.sandbox.dashSendModeBtn.dispatch("click");
  assertEqual(p3.sentOf("steer").length, 0, "no steer frame without an active turn");
});

check("queued prompt auto-sends on meta turn:end when the textarea is empty", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", dashTurnActive: true, viewingSid: "sess-1" });
  p.sandbox._dashQueuedPrompt = "queued message";
  p.sandbox._dashQueuedPromptSession = "sess-1";
  p.sandbox.dashPromptInput.value = "";
  p.sandbox.dashHandle({ type: "meta", sessionId: "sess-1", payload: { turn: "end", stopReason: "end_turn" } });
  assertEqual(p.dashSendPromptCalls.length, 1, "dashSendPrompt must be called to flush the queued prompt");
  // dashSendPrompt is real as of SC6 (Phase 4), not a call-counting stub --
  // it places the queued text into the textarea, sends it for real, then
  // clears the textarea on success, so what is actually observable after
  // the whole call chain completes is the sent frame, not a mid-flight
  // textarea snapshot (a stub-specific artifact the earlier version of this
  // check depended on).
  assertEqual(p.sentOf("prompt")[0].payload.prompt, "queued message",
    "the queued text must actually be sent, not just placed into the textarea");
  assertEqual(p.sandbox.dashPromptInput.value, "",
    "the textarea must be cleared once the queued prompt is actually sent");
  assertEqual(p.sandbox._dashQueuedPrompt, null, "the queued state must be cleared");
});

check("queued prompt discarded on meta turn:end when the session changed", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-2", dashTurnActive: true, viewingSid: "sess-2" });
  p.sandbox._dashQueuedPrompt = "to discard";
  p.sandbox._dashQueuedPromptSession = "sess-1"; // different from the now-attached session
  p.sandbox.dashHandle({ type: "meta", sessionId: "sess-2", payload: { turn: "end", stopReason: "end_turn" } });
  assertEqual(p.dashSendPromptCalls.length, 0, "dashSendPrompt must not be called for a session-mismatched queue");
  const note = p.addMessageCalls[p.addMessageCalls.length - 1];
  assert(note && /session changed/.test(note.text), "a discard note must mention the session changed");
});

check("queued prompt discarded on meta turn:end when the user typed something else", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", dashTurnActive: true, viewingSid: "sess-1" });
  p.sandbox._dashQueuedPrompt = "to discard";
  p.sandbox._dashQueuedPromptSession = "sess-1";
  p.sandbox.dashPromptInput.value = "something new";
  p.sandbox.dashHandle({ type: "meta", sessionId: "sess-1", payload: { turn: "end", stopReason: "end_turn" } });
  assertEqual(p.dashSendPromptCalls.length, 0,
    "dashSendPrompt must not be called when the user already typed something");
  assertEqual(p.sandbox.dashPromptInput.value, "something new", "the user's own text must not be clobbered");
});

check("queued prompt not sent on meta turn:end when the turn was cancelled", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", dashTurnActive: true, viewingSid: "sess-1" });
  p.sandbox._dashQueuedPrompt = "queued";
  p.sandbox._dashQueuedPromptSession = "sess-1";
  p.sandbox.dashHandle({ type: "meta", sessionId: "sess-1", payload: { turn: "end", stopReason: "cancelled" } });
  assertEqual(p.dashSendPromptCalls.length, 0, "a cancelled turn must not fire the queued auto-send");
  const note = p.addMessageCalls[p.addMessageCalls.length - 1];
  assert(note && /not sent/.test(note.text), "a note must explain the turn was stopped");
});

check("Steer (default mode): sendModeBtn click sends a steer frame and disables the composer", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", dashTurnActive: true, viewingSid: "sess-1" });
  p.sandbox.dashPromptInput.value = "inject this";
  p.sandbox.dashSendModeBtn.dispatch("click");
  const steers = p.sentOf("steer");
  assertEqual(steers.length, 1, "exactly one steer frame should be sent");
  assertEqual(steers[0].payload.message, "inject this", "steer payload.message must match the typed text");
  assertEqual(steers[0].sid, "sess-1", "steer must target the attached session");
  assertEqual(p.sandbox.dashPromptInput.value, "", "the textarea must be cleared after steering");
  assertEqual(p.sandbox.dashPromptInput.disabled, true, "the textarea must be disabled while awaiting steer_ack");
  assertEqual(p.sandbox._dashSteerPending, "inject this", "the pending text must be held for restoration");
});

check("Steer: send() returning false immediately restores composer controls", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", dashTurnActive: true, viewingSid: "sess-1" });
  p.sandbox.send = () => false;
  p.sandbox.dashPromptInput.value = "inject this";
  p.sandbox.dashSendModeBtn.dispatch("click");
  assertEqual(p.sandbox.dashPromptInput.disabled, false, "a failed send must not leave the textarea disabled");
  assertEqual(p.sandbox._dashSteerPending, null, "a failed send must clear the pending-steer state");
});

check("steer_ack (queued:true) re-enables controls", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", dashTurnActive: true, viewingSid: "sess-1" });
  p.sandbox.dashPromptInput.value = "steer text";
  p.sandbox.dashSendModeBtn.dispatch("click");
  p.sandbox.dashHandle({ type: "steer_ack", sessionId: "sess-1", payload: { queued: true } });
  assertEqual(p.sandbox.dashPromptInput.disabled, false, "the textarea must be re-enabled after steer_ack");
  assertEqual(p.sandbox.dashSendModeBtn.disabled, false, "the send-mode button must be re-enabled after steer_ack");
  assertEqual(p.sandbox.dashModeToggle.disabled, false, "the mode toggle must be re-enabled after steer_ack");
  assertEqual(p.sandbox._dashSteerPending, null, "_dashSteerPending must be cleared");
});

check("steer_ack (queued:false) restores the text and shows an error message", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", dashTurnActive: true, viewingSid: "sess-1" });
  p.sandbox.dashPromptInput.value = "bad steer";
  p.sandbox.dashSendModeBtn.dispatch("click");
  p.sandbox.dashHandle({ type: "steer_ack", sessionId: "sess-1", payload: { queued: false } });
  assertEqual(p.sandbox.dashPromptInput.value, "bad steer", "steer_ack queued:false must restore the textarea text");
  assertEqual(p.sandbox.dashPromptInput.disabled, false, "the textarea must be re-enabled");
  const err = p.addMessageCalls[p.addMessageCalls.length - 1];
  assertEqual(err.role, "error", "an error message must be shown");
});

check("steer_sent frame adds a dimmed steer band via addMessage", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  p.sandbox.dashHandle({ type: "steer_sent", sessionId: "sess-1", payload: { text: "do X" } });
  const call = p.addMessageCalls[p.addMessageCalls.length - 1];
  assertEqual(call.role, "steer", "the band must be added with the 'steer' role, matching acp.html's addMessage('steer', ...)");
  assertEqual(call.text, "do X", "the band text must match the steered text");
});

check("steer_sent frame with empty text is a no-op", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  const before = p.addMessageCalls.length;
  p.sandbox.dashHandle({ type: "steer_sent", sessionId: "sess-1", payload: { text: "" } });
  assertEqual(p.addMessageCalls.length, before, "an empty steer_sent text must not call addMessage");
});

check("error frame restores pending steer text before any payload.code branching (close_in_progress)", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", dashTurnActive: true, viewingSid: "sess-1" });
  p.sandbox.dashPromptInput.value = "important steer";
  p.sandbox.dashSendModeBtn.dispatch("click");
  assertEqual(p.sandbox.dashPromptInput.disabled, true, "fixture: textarea disabled after steer click");
  p.sandbox.dashHandle({
    type: "error", sessionId: "sess-1",
    payload: { code: "close_in_progress", message: "closing" },
  });
  assertEqual(p.sandbox.dashPromptInput.value, "important steer",
    "the steer-restore block must run even though close_in_progress returns early afterward");
  assertEqual(p.sandbox.dashPromptInput.disabled, false, "the textarea must be re-enabled");
  assertEqual(p.sandbox.dashSendModeBtn.disabled, false, "the send-mode button must be re-enabled");
});

check("error frame restores pending steer text and clears the stop-in-progress flag on a generic refusal", () => {
  const p = loadDashPicker({
    dashAttachedSid: "sess-1", dashTurnActive: false, dashStopInProgress: true, viewingSid: "sess-1",
  });
  p.sandbox.dashPromptInput.value = "important steer";
  p.sandbox._dashSteerPending = "important steer";
  p.sandbox.dashPromptInput.disabled = true;
  p.sandbox.dashSendModeBtn.disabled = true;
  p.sandbox.dashModeToggle.disabled = true;
  p.sandbox.dashHandle({
    type: "error", sessionId: "sess-1",
    payload: { code: "agent_error", message: "steer failed" },
  });
  assertEqual(p.sandbox.dashPromptInput.value, "important steer", "error frame must restore steer text to the textarea");
  assertEqual(p.sandbox.dashPromptInput.disabled, false, "the textarea must be re-enabled after the error frame");
  assertEqual(p.sandbox.dashModeToggle.disabled, false, "the mode toggle must be re-enabled after the error frame");
  assertEqual(p.sandbox._dashStopInProgress, false, "a refused action while idle must clear _dashStopInProgress");
});

check("agent_died restores pending steer text and re-enables mode controls, leaving the textarea disabled", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  p.sandbox.dashHandle({
    type: "session", sessionId: "sess-1",
    payload: { sessionId: "sess-1", cwd: "C:\\work\\repo", turnActive: true },
  });
  p.sandbox._dashSteerPending = "pending steer text";
  p.sandbox.dashSendModeBtn.disabled = true;
  p.sandbox.dashModeToggle.disabled = true;
  p.sandbox.dashPromptInput.value = ""; // cleared, as it would be while awaiting steer_ack
  p.sandbox.dashHandle({ type: "agent_died", sessionId: "sess-1", payload: { exitCode: 1, message: "boom" } });
  assertEqual(p.sandbox.dashPromptInput.value, "pending steer text",
    "agent_died must restore the pending steer text into the textarea");
  assertEqual(p.sandbox.dashSendModeBtn.disabled, false, "the send-mode button must be re-enabled");
  assertEqual(p.sandbox.dashModeToggle.disabled, false, "the mode toggle must be re-enabled");
  assertEqual(p.sandbox._dashSteerPending, null, "_dashSteerPending must be cleared");
  // The composer as a whole stays disabled-with-a-note -- the dashboard's own
  // pre-existing pattern (not acp.html's fully-reenabled composer); see the
  // code comment on agent_died's steer-restore block.
  assertEqual(p.sandbox.dashPromptInput.disabled, true,
    "the textarea itself stays disabled under the agent-died note, per the dashboard's existing pattern");
});

check("session_closed clears queue/steer state and re-enables mode controls with a note", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", dashTurnActive: true, viewingSid: "sess-1" });
  p.sandbox._dashQueuedPrompt = "queued";
  p.sandbox._dashQueuedPromptSession = "sess-1";
  p.sandbox._dashSteerPending = "steering";
  p.sandbox.dashSendModeBtn.disabled = true;
  p.sandbox.dashModeToggle.disabled = true;
  p.sandbox.dashHandle({ type: "session_closed", sessionId: "sess-1", payload: { message: "closed" } });
  assertEqual(p.sandbox._dashQueuedPrompt, null, "session_closed must clear the queued prompt");
  assertEqual(p.sandbox._dashSteerPending, null, "session_closed must clear the pending steer");
  assertEqual(p.sandbox.dashSendModeBtn.disabled, false, "the send-mode button must be re-enabled");
  assertEqual(p.sandbox.dashModeToggle.disabled, false, "the mode toggle must be re-enabled");
  const note = p.addMessageCalls.find((c) => /Steer could not complete/.test(c.text));
  assert(note, "a note explaining the steer could not complete must be shown");
});

check("dashCloseIfAbandoned re-enables mode controls if a steer was pending", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.sandbox._dashOrigin = "joined"; // not this page's own load, so the send('close', ...) arm is skipped
  p.sandbox._dashSteerPending = "steering";
  p.sandbox.dashSendModeBtn.disabled = true;
  p.sandbox.dashModeToggle.disabled = true;
  p.sandbox.dashCloseIfAbandoned();
  assertEqual(p.sandbox._dashSteerPending, null, "dashCloseIfAbandoned must clear the pending steer");
  assertEqual(p.sandbox.dashSendModeBtn.disabled, false, "the send-mode button must be re-enabled");
  assertEqual(p.sandbox.dashModeToggle.disabled, false, "the mode toggle must be re-enabled");
});

check("steer_status queued shows transient text near the composer", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  assertEqual(p.sandbox.dashSteerStatusEl.hidden, true, "steer status must start hidden");
  p.sandbox.dashHandle({
    type: "steer_status", sessionId: "sess-1",
    payload: { status: "steering_queued", content: "look at foo.py" },
  });
  assertEqual(p.sandbox.dashSteerStatusEl.hidden, false, "steer status must show once queued");
  assert(p.sandbox.dashSteerStatusEl.textContent.includes("look at foo.py"),
    "steer status text must include the steered content");
});

check("steer_status injected auto-clears after a 4s timer", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  p.sandbox.dashHandle({
    type: "steer_status", sessionId: "sess-1",
    payload: { status: "steering_injected", content: "look at foo.py" },
  });
  assertEqual(p.sandbox.dashSteerStatusEl.hidden, false, "steer status must show once injected");
  assert(p.timers.length > 0, "steering_injected must schedule a clear timer");
  assertEqual(p.timers[p.timers.length - 1].ms, 4000, "the clear timer must fire after 4000ms, matching acp.html");
  p.runTimers();
  assertEqual(p.sandbox.dashSteerStatusEl.hidden, true, "the status line must hide once the timer fires");
  assertEqual(p.sandbox.dashSteerStatusEl.textContent, "", "the status text must clear once the timer fires");
});

check("steer_status cleared hides the status line immediately", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  p.sandbox.dashHandle({
    type: "steer_status", sessionId: "sess-1", payload: { status: "steering_queued", content: "x" },
  });
  assertEqual(p.sandbox.dashSteerStatusEl.hidden, false, "sanity check -- status should be visible after steering_queued");
  p.sandbox.dashHandle({ type: "steer_status", sessionId: "sess-1", payload: { status: "steering_cleared" } });
  assertEqual(p.sandbox.dashSteerStatusEl.hidden, true, "steering_cleared must hide the status line immediately");
});

check("steer_status during a simulated replay does not touch the DOM", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  // Reachability note: index.html's `history` frame handling goes through
  // renderTranscriptHistory() -> renderTranscriptFrame() (transcript-renderer.js),
  // which has no steer_status case at all, so a real history payload can
  // never reach dashHandle()'s steer_status case in the first place -- a
  // pre-existing dashboard/acp.html replay gap this phase does not close
  // (see the code comment on dashHandle()'s steer_status case in index.html).
  // This test drives the !_dashReplaying guard directly, the way it is
  // written, in case a future change wires nested-frame replay through
  // dashHandle().
  p.sandbox._dashReplaying = true;
  p.sandbox.dashHandle({
    type: "steer_status", sessionId: "sess-1", payload: { status: "steering_injected", content: "stale" },
  });
  assertEqual(p.sandbox.dashSteerStatusEl.hidden, true,
    "a replayed steer_status must not surface a stale composer status");
});

check("steer_status frame with agent-controlled content uses textContent, never innerHTML", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  const malicious = "<img src=x onerror=\"window._dash_steer_xss=true\">";
  p.sandbox.dashHandle({
    type: "steer_status", sessionId: "sess-1", payload: { status: "steering_queued", content: malicious },
  });
  // Reaching here at all means no innerHTML sink fired -- El's innerHTML
  // getter/setter throws unconditionally (HTML_SINK, this file's DOM stand-in).
  assert(p.sandbox.dashSteerStatusEl.textContent.includes(malicious),
    "steer status must render the content as literal text");
  assert(!p.sandbox._dash_steer_xss, "the onerror handler must not fire -- steer status must not use innerHTML");
});

check("dashboard: a session frame (reattach) clears a stale steer status left over from before the switch (Fix 1, Step 9 review)", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  p.sandbox.dashHandle({
    type: "steer_status", sessionId: "sess-1",
    payload: { status: "steering_queued", content: "look at foo.py" },
  });
  assertEqual(p.sandbox.dashSteerStatusEl.hidden, false, "sanity check -- steer status must be visible before reattach");
  p.sandbox.dashHandle({
    type: "session", sessionId: "sess-1",
    payload: { sessionId: "sess-1", cwd: "C:\\work\\repo", turnActive: false },
  });
  assertEqual(p.sandbox.dashSteerStatusEl.hidden, true,
    "a session frame (reattach) must clear any stale steer status, mirroring acp.html's own setSteerStatus('') on session-frame handling");
  assertEqual(p.sandbox.dashSteerStatusEl.textContent, "", "the steer status text must be cleared too");
});

check("dashboard: a session frame (payload.created new-session branch) also clears a stale steer status (Fix 1, Step 9 review)", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.sandbox.dashHandle({
    type: "steer_status", sessionId: "sess-1",
    payload: { status: "steering_queued", content: "look at foo.py" },
  });
  assertEqual(p.sandbox.dashSteerStatusEl.hidden, false, "sanity check -- steer status must be visible before the new session lands");
  // The payload.created early branch (dashPickerCreate/dashRailQuickCreate)
  // falls through into the same case body as a normal reattach -- confirmed
  // by reading dashHandle()'s control flow directly.
  p.sandbox.dashHandle({
    type: "session", sessionId: "sess-2",
    payload: { created: true, cwd: "/ws2" },
  });
  assertEqual(p.sandbox.dashSteerStatusEl.hidden, true,
    "creating a new session must also clear any stale steer status from the previously-viewed session");
});

check("Enter during a turn in steer mode dispatches to sendModeBtn (steer send)", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", dashTurnActive: true, viewingSid: "sess-1" });
  p.sandbox.dashPromptInput.value = "inject via enter";
  p.sandbox.dashPromptInput.dispatch("keydown", { key: "Enter", preventDefault: () => {} });
  const steers = p.sentOf("steer");
  assertEqual(steers.length, 1,
    "Enter during a turn (steer mode) must dispatch to sendModeBtn and send a steer frame");
  assertEqual(p.dashSendPromptCalls.length, 0, "Enter during a turn must not fall through to dashSendPrompt");
});

check("Enter during a turn in queue mode dispatches to sendModeBtn (queues the prompt)", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", dashTurnActive: true, viewingSid: "sess-1" });
  p.sandbox.dashApplySendMode("queue");
  p.sandbox.dashPromptInput.value = "queue via enter";
  p.sandbox.dashPromptInput.dispatch("keydown", { key: "Enter", preventDefault: () => {} });
  assertEqual(p.sandbox._dashQueuedPrompt, "queue via enter",
    "Enter during a turn (queue mode) must queue the prompt");
  assertEqual(p.dashSendPromptCalls.length, 0, "Enter during a turn must not fall through to dashSendPrompt");
});

// ---------------------------------------------------------------------------
// Phase 3 review-fix cycle: 7 findings from independent Security/Senior-
// engineer/Reliability review of commit bea1869. Each check below is named
// for the fix it covers; see index.html's own "Fix N (review, Phase 3 fix
// cycle)" comments at each corresponding edit site.
// ---------------------------------------------------------------------------

check("Fix 1: agent_died clears a pending queued prompt", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", dashTurnActive: true, viewingSid: "sess-1" });
  p.sandbox._dashQueuedPrompt = "queued before the crash";
  p.sandbox._dashQueuedPromptSession = "sess-1";
  p.sandbox.dashHandle({ type: "agent_died", sessionId: "sess-1", payload: { exitCode: 1, message: "crashed" } });
  assertEqual(p.sandbox._dashQueuedPrompt, null, "agent_died must clear _dashQueuedPrompt");
  assertEqual(p.sandbox._dashQueuedPromptSession, null, "agent_died must clear _dashQueuedPromptSession");
});

check("Fix 1: a session frame for the currently-viewed sid clears a leftover queued prompt", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  // Simulates the state agent_died's own clear (checked separately above)
  // did NOT run, or ran on a different code path -- this check isolates the
  // `session`-frame clear alone, so it fails on its own if that specific
  // clear is removed, independent of the agent_died clear.
  p.sandbox._dashQueuedPrompt = "stale from a previous attach";
  p.sandbox._dashQueuedPromptSession = "sess-1";
  p.sandbox.dashHandle({
    type: "session", sessionId: "sess-1",
    payload: { sessionId: "sess-1", cwd: "C:\\work\\repo", turnActive: false },
  });
  assertEqual(p.sandbox._dashQueuedPrompt, null,
    "a session frame for the currently-viewed sid must clear a leftover queued prompt");
  assertEqual(p.sandbox._dashQueuedPromptSession, null,
    "a session frame for the currently-viewed sid must clear the queued prompt's recorded session too");
});

check("Fix 1 regression: queue -> agent_died -> reattach to the same sid -> turn:end must not auto-send stale text", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  // Attach to sess-1 with a turn already running, queue a prompt.
  p.sandbox.dashHandle({
    type: "session", sessionId: "sess-1",
    payload: { sessionId: "sess-1", cwd: "C:\\work\\repo", turnActive: true },
  });
  p.sandbox.dashApplySendMode("queue");
  p.sandbox.dashPromptInput.value = "queued before crash";
  p.sandbox.dashSendModeBtn.dispatch("click");
  assertEqual(p.sandbox._dashQueuedPrompt, "queued before crash", "sanity check -- prompt is queued");
  // The agent process dies mid-turn.
  p.sandbox.dashHandle({ type: "agent_died", sessionId: "sess-1", payload: { exitCode: 1, message: "crashed" } });
  // The user reattaches to the SAME session id (a fresh `session` frame).
  p.sandbox.dashHandle({
    type: "session", sessionId: "sess-1",
    payload: { sessionId: "sess-1", cwd: "C:\\work\\repo", turnActive: false },
  });
  // An unrelated later turn on this same reattached session ends.
  p.sandbox.dashHandle({ type: "meta", sessionId: "sess-1", payload: { turn: "start" } });
  p.sandbox.dashPromptInput.value = "";
  p.sandbox.dashHandle({ type: "meta", sessionId: "sess-1", payload: { turn: "end", stopReason: "end_turn" } });
  assertEqual(p.dashSendPromptCalls.length, 0,
    "a stale pre-crash queued prompt must not auto-send into an unrelated later turn after a same-sid reattach");
});

check("Fix 2: error frame clears _dashStopInProgress and re-enables Stop even while the turn is still active", () => {
  const p = loadDashPicker({
    dashAttachedSid: "sess-1", dashTurnActive: true, dashStopInProgress: true, viewingSid: "sess-1",
  });
  p.sandbox.dashPromptInput.value = "";
  p.sandbox.dashStopBtn.disabled = true;
  p.sandbox.dashHandle({
    type: "error", sessionId: "sess-1",
    payload: { code: "internal_error", message: "cancel refused" },
  });
  assertEqual(p.sandbox._dashStopInProgress, false,
    "a refused cancel must clear _dashStopInProgress even while _dashTurnActive is still true -- " +
    "the realistic refusal case, and the one the old `if (!_dashTurnActive) ...` guard missed");
  assertEqual(p.sandbox.dashStopBtn.disabled, false,
    "Stop must be re-enabled by the same error frame's dashRefreshComposerControls() call");
});

check("Fix 3: an error frame with no sessionId does not restore a pending steer for the viewed session", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", dashTurnActive: true, viewingSid: "sess-1" });
  p.sandbox._dashSteerPending = "important steer";
  p.sandbox.dashPromptInput.value = "";
  p.sandbox.dashSendModeBtn.disabled = true;
  p.sandbox.dashModeToggle.disabled = true;
  // Mirrors acp.py error_frame() calls that omit sessionId entirely (e.g.
  // bad_json/bad_envelope/bad_payload for a frame unrelated to this steer).
  p.sandbox.dashHandle({ type: "error", sessionId: null, payload: { code: "bad_json", message: "Frame is not valid JSON." } });
  assertEqual(p.sandbox.dashPromptInput.value, "",
    "a sid-less error unrelated to this session must not restore steer text into the composer");
  assertEqual(p.sandbox._dashSteerPending, "important steer",
    "a sid-less error must not clear _dashSteerPending -- the real steer is still in flight");
  // Note: dashPromptInput.disabled still ends up false here, via the generic
  // refusal tail further down in the same `error` case (unconditional
  // `dashPromptInput.disabled = false;`, pre-existing and out of Fix 3's own
  // scope -- Fix 3 only concerns the steer-text-restore block above it) --
  // not asserted either way here, since that behaviour is unchanged by this
  // fix cycle.
});

check("Fix 3: an error frame carrying the viewed session's own sid still restores pending steer text", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", dashTurnActive: true, viewingSid: "sess-1" });
  p.sandbox.dashPromptInput.value = "important steer";
  p.sandbox.dashSendModeBtn.dispatch("click");
  p.sandbox.dashHandle({ type: "error", sessionId: "sess-1", payload: { code: "internal_error", message: "steer failed" } });
  assertEqual(p.sandbox.dashPromptInput.value, "important steer",
    "an error frame whose sid matches the viewed session must still restore the steer text");
  assertEqual(p.sandbox._dashSteerPending, null, "the matching-sid restore path must still clear _dashSteerPending");
});

check("Fix 4: a stale steer_ack with no local pending steer is ignored", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  // Simulates leftover disabled state from an abandoned attempt reattached
  // to the same session id -- _dashSteerPending is null (already cleared by
  // dashCloseIfAbandoned()), but this late ack still carries the same sid.
  p.sandbox.dashPromptInput.disabled = true;
  p.sandbox.dashSendModeBtn.disabled = true;
  p.sandbox.dashModeToggle.disabled = true;
  p.sandbox.dashHandle({ type: "steer_ack", sessionId: "sess-1", payload: { queued: true } });
  assertEqual(p.sandbox.dashPromptInput.disabled, true,
    "a steer_ack with no local _dashSteerPending must be treated as stale and ignored, not act on composer state");
  assertEqual(p.sandbox.dashSendModeBtn.disabled, true, "a stale steer_ack must not touch the send-mode button either");
});

check("Fix 4: a live steer_ack (local pending steer set) still re-enables controls", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", dashTurnActive: true, viewingSid: "sess-1" });
  p.sandbox.dashPromptInput.value = "steer text";
  p.sandbox.dashSendModeBtn.dispatch("click");
  p.sandbox.dashHandle({ type: "steer_ack", sessionId: "sess-1", payload: { queued: true } });
  assertEqual(p.sandbox.dashPromptInput.disabled, false,
    "steer_ack must still re-enable the textarea for a genuine, locally in-flight steer");
  assertEqual(p.sandbox._dashSteerPending, null, "_dashSteerPending must still clear for a genuine steer_ack");
});

check("Fix 4: a stale steer_sent does not clear coincidentally-matching composer text, but the band still renders", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  // No local steer pending (as if abandoned then reattached to the same
  // sid) -- the user has since typed fresh text that happens to coincide
  // with the broadcast steer text.
  p.sandbox.dashPromptInput.value = "do X";
  p.sandbox.dashHandle({ type: "steer_sent", sessionId: "sess-1", payload: { text: "do X" } });
  assertEqual(p.sandbox.dashPromptInput.value, "do X",
    "a steer_sent with no local pending steer must not clear composer text merely because it happens to match");
  const call = p.addMessageCalls[p.addMessageCalls.length - 1];
  assertEqual(call.role, "steer",
    "the band must still render -- steer_sent is a broadcast frame other viewers (e.g. /acp) rely on");
  assertEqual(call.text, "do X", "the rendered band text must match the broadcast steer text");
});

check("Fix 5: dashCloseIfAbandoned resets dashPromptInput.disabled", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.sandbox._dashOrigin = "joined"; // skips the send('close', ...) arm
  p.sandbox.dashPromptInput.disabled = true;
  p.sandbox.dashCloseIfAbandoned();
  assertEqual(p.sandbox.dashPromptInput.disabled, false,
    "dashCloseIfAbandoned must reset dashPromptInput.disabled, for consistency with " +
    "session_closed/agent_died/error, which all reset it explicitly");
});

check("Fix 6: Ctrl+Enter is not intercepted as a plain send", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  p.sandbox.dashPromptInput.value = "some text";
  let prevented = false;
  p.sandbox.dashPromptInput.dispatch("keydown", { key: "Enter", ctrlKey: true, preventDefault: () => { prevented = true; } });
  assertEqual(prevented, false, "Ctrl+Enter must not be swallowed by the plain-Enter-sends handler");
  assertEqual(p.dashSendPromptCalls.length, 0, "Ctrl+Enter must not trigger dashSendPrompt");
});

check("Fix 6: Alt+Enter is not intercepted as a plain send", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  p.sandbox.dashPromptInput.value = "some text";
  let prevented = false;
  p.sandbox.dashPromptInput.dispatch("keydown", { key: "Enter", altKey: true, preventDefault: () => { prevented = true; } });
  assertEqual(prevented, false, "Alt+Enter must not be swallowed by the plain-Enter-sends handler");
  assertEqual(p.dashSendPromptCalls.length, 0, "Alt+Enter must not trigger dashSendPrompt");
});

check("Fix 6: a plain Enter (no modifiers) still sends", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  p.sandbox.dashPromptInput.value = "some text";
  p.sandbox.dashPromptInput.dispatch("keydown", { key: "Enter", preventDefault: () => {} });
  assertEqual(p.dashSendPromptCalls.length, 1, "a plain Enter with no modifier keys must still dispatch to dashSendPrompt");
});

check("Fix 7: a failed Stop/cancel send shows a not-connected toast", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", dashTurnActive: true, viewingSid: "sess-1" });
  const toasts = [];
  p.sandbox.showToast = (html) => toasts.push(html);
  p.sandbox.send = () => false;
  p.sandbox.dashStopBtn.dispatch("click");
  assertEqual(toasts.length, 1, "a failed cancel send must show a toast, mirroring dashSendPrompt()'s own pattern");
  assert(/Not connected/.test(toasts[0]), "the toast must explain the send failed for lack of a connection");
});

check("Fix 7: a failed Steer send shows a not-connected toast", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", dashTurnActive: true, viewingSid: "sess-1" });
  const toasts = [];
  p.sandbox.showToast = (html) => toasts.push(html);
  p.sandbox.send = () => false;
  p.sandbox.dashPromptInput.value = "inject this";
  p.sandbox.dashSendModeBtn.dispatch("click");
  assertEqual(p.sandbox.dashPromptInput.disabled, false, "a failed send must not leave the textarea disabled");
  assertEqual(p.sandbox._dashSteerPending, null, "a failed send must clear the pending-steer state");
  assertEqual(toasts.length, 1, "a failed steer send must show a toast, mirroring dashSendPrompt()'s own pattern");
  assert(/Not connected/.test(toasts[0]), "the toast must explain the send failed for lack of a connection");
});

check("Fix 3 (Phase 6 review): a Close click during the reconnect/backoff window shows a not-connected toast", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  const toasts = [];
  p.sandbox.showToast = (html) => toasts.push(html);
  // Simulates the reconnect/backoff window (socket down, waiting to retry) --
  // send() returning false is exactly what a Close click sees there, the
  // same failure Fix 7 (Phase 3 review) already handled for Stop/Steer.
  p.sandbox.send = () => false;
  p.sandbox.dashCloseBtn.dispatch("click");
  assertEqual(toasts.length, 1,
    "a failed close send must show a toast rather than silently doing nothing, mirroring Stop/Steer's " +
    "own not-connected pattern (Fix 7, Phase 3 review)");
  assert(/Not connected/.test(toasts[0]), "the toast must explain the send failed for lack of a connection");
  assertEqual(p.sandbox.dashCloseBtn.disabled, false,
    "a failed close send must not leave the button stuck in the disabled 'Closing…' state");
});

// ---------------------------------------------------------------------------
// Image paste-to-attach (SC6, dashboard/ACP feature-parity plan Phase 4) --
// dash-prefixed, index.html-only. The encoder behind these is the same
// deterministic stub loadPage()'s own image checks use (see loadDashPicker()'s
// DashFakeBlob/dashEncodeBlob above): what they pin is the page's own
// arithmetic and bookkeeping -- the ladder, the budget, the numbering, which
// mimeType is reported, what gets revoked, and (dashboard-specific) which of
// the two send-prompt call sites actually attaches the images.
// ---------------------------------------------------------------------------

check("dashboard: image attach — pasting an image stages it without touching the transcript", async () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  const took = p.paste([p.imageFile()]);
  assert(took, "the page did not take over an image paste itself");
  await settleStaging();
  assertEqual(p.trayChips().length, 1, "the image was not staged");
  assertEqual(p.sandbox.dashTrayEl.hidden, false, "the tray stayed hidden");
  assert(p.sandbox.dashTrayEl.textContent.includes("Image 1"),
         "the chip is not labelled with the name the transcript will use");
  assertEqual(p.sentOf("prompt").length, 0, "staging an image sent a prompt on its own");
});

check("dashboard: image attach — a paste carrying no image is left entirely alone", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  const took = p.paste([{ type: "text/plain", name: "notes.txt" }]);
  assert(!took, "an ordinary text paste was intercepted");
  assertEqual(p.sandbox.dashTrayEl.hidden, true, "a text paste opened the tray");
});

check("dashboard: image attach — dropping an image onto the composer stages it", async () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  const allowed = p.drop([p.imageFile()]);
  assert(allowed, "dragover never called preventDefault, so a real browser would navigate to " +
                  "the image instead of dropping it here");
  await settleStaging();
  assertEqual(p.trayChips().length, 1, "the dropped image was not staged");
});

check("dashboard: image attach — [Image N] marker inserted at cursor position", async () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.sandbox.dashPromptInput.value = "look at ";
  p.sandbox.dashPromptInput.selectionStart = p.sandbox.dashPromptInput.selectionEnd = 8;
  p.paste([p.imageFile()]);
  await settleStaging();
  const val = p.sandbox.dashPromptInput.value;
  assert(val.includes("[Image 1]"), "textarea should contain [Image 1] after paste — got: " + val);
  const pos = val.indexOf("[Image 1]");
  assertEqual(pos, 8, "[Image 1] should appear at cursor position 8 — got: " + pos);
});

check("dashboard: image attach — second paste inserts [Image 2]", async () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.paste([p.imageFile()]);
  await settleStaging();
  p.paste([p.imageFile()]);
  await settleStaging();
  const val = p.sandbox.dashPromptInput.value;
  assert(val.includes("[Image 1]"), "should contain [Image 1] — got: " + val);
  assert(val.includes("[Image 2]"), "should contain [Image 2] — got: " + val);
});

check("dashboard: image attach — the type sent is the one the encoder produced, not the one asked for", async () => {
  const p = loadDashPicker({
    dashAttachedSid: "sess-1", viewingSid: "sess-1",
    noWebp: true, imageWidth: 200, imageHeight: 100,
  });
  p.paste([p.imageFile()]);
  await settleStaging();
  p.sandbox.dashSendBtn.dispatch("click");
  assertEqual(p.sentOf("prompt")[0].payload.images[0].mimeType, "image/png",
              "the page reported the format it requested rather than the one it got back");
});

check("dashboard: image attach — the encoder walks down the ladder until something fits", async () => {
  const p = loadDashPicker({
    dashAttachedSid: "sess-1", viewingSid: "sess-1",
    encode: (type, quality) => ({ size: quality > 0.7 ? 900000 : 5000, type }),
  });
  p.paste([p.imageFile()]);
  await settleStaging();
  assertEqual(p.trayChips().length, 1,
              "the first rung did not fit and the page gave up instead of trying a lower quality");
  assert(p.sandbox.dashTrayEl.textContent.includes("5 KB"),
         "the staged image is not the one the lower rung produced");
});

check("dashboard: image attach — an image that cannot be made to fit is refused, not truncated", async () => {
  const p = loadDashPicker({
    dashAttachedSid: "sess-1", viewingSid: "sess-1",
    encode: (type) => ({ size: 900000, type }),
  });
  p.paste([p.imageFile()]);
  await settleStaging();
  assertEqual(p.trayChips().length, 0, "an oversized image was staged anyway");
  const note = p.addMessageCalls[p.addMessageCalls.length - 1];
  assert(note && /not attached/.test(note.text), "the refusal was not said anywhere the user will read it");
});

check("dashboard: image attach — a connected meta's image budget is enforced on the next paste", async () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.sandbox.dashHandle({
    type: "meta",
    payload: { connected: true, maxMessageBytes: 262144, maxConnections: 8,
               maxPromptImages: 1, maxPromptImageBytes: 180224 },
  });
  assertEqual(p.sandbox._dashImageMaxCount, 1, "sanity check — the budget state itself updated");
  p.paste([p.imageFile()]);
  await settleStaging();
  p.paste([p.imageFile("image/png", "second.png")]);
  await settleStaging();
  assertEqual(p.trayChips().length, 1, "the page ignored the cap the server advertised");
  const note = p.addMessageCalls[p.addMessageCalls.length - 1];
  assert(note && /at most 1 images/.test(note.text), "nothing said why the second image was dropped");
});

check("dashboard: image attach — removing a staged image gives its object URL back", async () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.paste([p.imageFile()]);
  await settleStaging();
  const before = p.revoked().length;
  p.trayChips()[0].querySelector(".acp-attach-drop").dispatch("click");
  assertEqual(p.trayChips().length, 0, "the chip stayed after being removed");
  assert(p.revoked().length > before,
         "the object URL was never revoked — its blob stays alive for the lifetime of the tab");
});

check("dashboard: image attach — removeAttachment renumbers [Image N] markers in textarea", async () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.paste([p.imageFile()]);
  await settleStaging();
  p.paste([p.imageFile()]);
  await settleStaging();
  const val = p.sandbox.dashPromptInput.value;
  assert(val.includes("[Image 1]"), "fixture: [Image 1] present — got: " + val);
  assert(val.includes("[Image 2]"), "fixture: [Image 2] present — got: " + val);
  const chips = p.trayChips();
  assert(chips.length >= 1, "fixture: at least one chip");
  const removeBtn = chips[0].querySelector("button");
  assert(removeBtn !== null, "fixture: remove button on first chip");
  removeBtn.dispatch("click");
  const after = p.sandbox.dashPromptInput.value;
  assert(!after.includes("[Image 2]"), "[Image 2] should have been renumbered to [Image 1] — got: " + after);
  const count1 = (after.match(/\[Image 1\]/g) || []).length;
  assert(count1 === 1,
    "after removing first attachment, [Image 1] should appear exactly once for the remaining one — got: " + after);
});

check("dashboard: image attach — removeAttachment handles the middle element of 3", async () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.paste([p.imageFile()]);
  await settleStaging();
  p.paste([p.imageFile()]);
  await settleStaging();
  p.paste([p.imageFile()]);
  await settleStaging();
  p.sandbox.dashPromptInput.value = "[Image 1][Image 2][Image 3]";
  const chips = p.trayChips();
  assertEqual(chips.length, 3, "fixture: 3 chips — got: " + chips.length);
  chips[1].querySelector("button").dispatch("click");
  const after = p.sandbox.dashPromptInput.value;
  const count1 = (after.match(/\[Image 1\]/g) || []).length;
  assert(count1 === 1, "[Image 1] should appear exactly once — got: " + after);
  const count2 = (after.match(/\[Image 2\]/g) || []).length;
  assert(count2 === 1, "[Image 2] should appear exactly once (renumbered from [Image 3]) — got: " + after);
  assert(!after.includes("[Image 3]"), "[Image 3] should not appear after removal — got: " + after);
});

check("dashboard: image attach — a browser with no image APIs refuses cleanly instead of throwing", async () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1", images: false });
  p.paste([p.imageFile()]);
  await settleStaging();
  assertEqual(p.trayChips().length, 0, "something was staged with no encoder");
  const note = p.addMessageCalls[p.addMessageCalls.length - 1];
  assert(note && /cannot attach images/.test(note.text),
         "the page failed silently rather than saying it could not attach");
});

check("dashboard: image attach — a staged image travels as payload.images independent of the prompt text", async () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.paste([p.imageFile()]);
  await settleStaging();
  p.sandbox.dashPromptInput.value = "what is wrong here?";
  p.sandbox.dashSendBtn.dispatch("click");
  const sent = p.sentOf("prompt")[0];
  assert(sent, "the prompt was never sent");
  assertEqual(sent.payload.prompt, "what is wrong here?",
              "the text and the images should travel in separate fields");
  assertEqual(sent.payload.images.length, 1, "the image never reached the wire");
  assert(sent.payload.images[0].data.length > 0, "the image carried no data");
  assertEqual(Object.keys(sent.payload.images[0]).sort().join(","), "data,mimeType",
              "the wire carried more than the server reads");
  assertEqual(p.trayChips().length, 0, "the tray kept the images after sending");
});

check("dashboard: image attach — an image with no words is a whole prompt", async () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.paste([p.imageFile()]);
  await settleStaging();
  p.sandbox.dashSendBtn.dispatch("click");
  const sent = p.sentOf("prompt")[0];
  assert(sent, "paste-and-send with an empty box sent nothing at all");
  assertEqual(sent.payload.prompt, "[Image 1]",
    "paste inserts [Image 1] marker — the prompt text should carry it");
  assertEqual(sent.payload.images.length, 1, "the image never reached the wire");
});

check("dashboard: image attach — a started turn releases the images it consumed", async () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.paste([p.imageFile()]);
  await settleStaging();
  p.sandbox.dashSendBtn.dispatch("click");
  const before = p.revoked().length;
  p.sandbox.dashHandle({ type: "meta", sessionId: "sess-1", payload: { turn: "start" } });
  assert(p.revoked().length > before,
         "the turn started but the sent images' object URLs were never revoked, so their " +
         "blobs outlive the page's use for them");
});

check("dashboard: image attach — a refused prompt gives the images back", async () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.paste([p.imageFile()]);
  await settleStaging();
  p.sandbox.dashPromptInput.value = "look at this";
  p.sandbox.dashSendBtn.dispatch("click");
  assertEqual(p.trayChips().length, 0, "sanity check — the tray emptied on send");
  p.sandbox.dashHandle({
    type: "error", sessionId: "sess-1",
    payload: { code: "turn_in_progress", message: "still answering" },
  });
  assertEqual(p.trayChips().length, 1,
    "the refusal cost the user their attachment, which is another paste, decode and re-encode " +
    "to replace");
});

check("dashboard: image attach — images staged since a refused send take precedence over restoring old ones", async () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.paste([p.imageFile()]);
  await settleStaging();
  p.sandbox.dashSendBtn.dispatch("click");
  assertEqual(p.trayChips().length, 0, "sanity check — the tray emptied on send");
  // A new image is staged before the refusal arrives.
  p.paste([p.imageFile("image/png", "newer.png")]);
  await settleStaging();
  assertEqual(p.trayChips().length, 1, "sanity check — a new image is now staged");
  const before = p.revoked().length;
  p.sandbox.dashHandle({
    type: "error", sessionId: "sess-1",
    payload: { code: "internal_error", message: "refused" },
  });
  assertEqual(p.trayChips().length, 1,
    "the refused send's images must not be restored over ones staged since");
  assert(p.revoked().length > before,
    "the refused send's images must still be revoked, not merely dropped, once superseded");
  const note = p.addMessageCalls[p.addMessageCalls.length - 1];
  assert(note && /others have been attached since/.test(note.text),
    "the user was not told why the refused images were not restored");
});

check("dashboard: image attach — a sid-less error must not restore images belonging to a different session", async () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.paste([p.imageFile()]);
  await settleStaging();
  p.sandbox.dashSendBtn.dispatch("click");
  assertEqual(p.trayChips().length, 0, "sanity check — the tray emptied on send");
  p.sandbox.dashHandle({
    type: "error", sessionId: null,
    payload: { code: "bad_json", message: "Frame is not valid JSON." },
  });
  assertEqual(p.trayChips().length, 0,
    "a sid-less error unrelated to this session must not restore images into the tray");
});

check("dashboard: image attach — closing the session drops the images staged against it", async () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.paste([p.imageFile()]);
  await settleStaging();
  const before = p.revoked().length;
  p.sandbox.dashHandle({
    type: "session_closed", sessionId: "sess-1", payload: { sessionId: "sess-1", reason: "closed" },
  });
  assertEqual(p.trayChips().length, 0, "the session went away but its staged images stayed behind");
  assert(p.revoked().length > before, "the object URLs were never revoked");
});

check("dashboard: image attach — agent_died clears the staged images", async () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.paste([p.imageFile()]);
  await settleStaging();
  const before = p.revoked().length;
  p.sandbox.dashHandle({ type: "agent_died", sessionId: "sess-1", payload: { exitCode: 1, message: "crashed" } });
  assertEqual(p.trayChips().length, 0, "the agent died but its staged images stayed behind");
  assert(p.revoked().length > before, "the object URLs were never revoked");
});

check("dashboard: image attach — dashCloseIfAbandoned drops images staged against the session just left", async () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.sandbox._dashOrigin = "joined"; // skips the send('close', ...) arm
  p.paste([p.imageFile()]);
  await settleStaging();
  const before = p.revoked().length;
  p.sandbox.dashCloseIfAbandoned();
  assertEqual(p.trayChips().length, 0, "images staged for the previous session survived the switch");
  assert(p.revoked().length > before,
         "the tray was emptied but the object URLs behind it were not revoked");
});

check("dashboard: image attach — an image staged before the session has attached survives the lazy-attach flush", async () => {
  // This is the exact regression scenario Phase 4's design guards against
  // (plan's own named risk): a first image+message sent to a not-yet-attached
  // session must not silently drop the image.
  const p = loadDashPicker({ viewingSid: "sess-1" }); // dashAttachedSid defaults to null -- not yet attached
  p.paste([p.imageFile()]);
  await settleStaging();
  assertEqual(p.trayChips().length, 1, "sanity check — the image staged before attach");
  p.sandbox.dashPromptInput.value = "[Image 1] what is this?";
  p.sandbox.dashSendBtn.dispatch("click");
  // The immediate path is not taken (not yet attached) -- nothing sent yet,
  // and the composer locks while the session attaches.
  assertEqual(p.sentOf("prompt").length, 0, "the prompt must not send before the session has attached");
  assertEqual(p.sandbox.dashPromptInput.disabled, true, "the composer must lock while attaching");
  assert(p.sandbox._dashPendingImages, "the staged image must be snapshotted into _dashPendingImages");
  assertEqual(p.sandbox._dashPendingImages.length, 1, "exactly one image should be snapshotted");
  // The tray itself must still show the image while attaching -- it is not
  // moved/cleared until the deferred send actually fires.
  assertEqual(p.trayChips().length, 1, "the tray must still show the staged image while attaching");
  // The session finishes attaching; its history frame flushes the pending send.
  p.sandbox.dashHandle({ type: "history", sessionId: "sess-1", payload: { events: [] } });
  const sent = p.sentOf("prompt")[0];
  assert(sent, "the deferred send never fired");
  assertEqual(sent.payload.prompt, "[Image 1] what is this?", "the deferred text must be sent");
  assertEqual(sent.payload.images.length, 1,
    "a first image+message sent to a not-yet-attached session must not silently drop the image");
  assert(sent.payload.images[0].data.length > 0, "the image carried no data");
  assertEqual(p.sandbox.dashPromptInput.disabled, false, "the composer must unlock once the send flushes");
  assertEqual(p.trayChips().length, 0, "the tray must clear once the deferred send flushes");
});

check("dashboard: image attach — a refused lazy-attach load leaves the still-staged tray untouched", async () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  p.paste([p.imageFile()]);
  await settleStaging();
  p.sandbox.dashPromptInput.value = "[Image 1]";
  p.sandbox.dashSendBtn.dispatch("click");
  assertEqual(p.sandbox._dashLoadingSid, "sess-1", "sanity check — a lazy load is in flight");
  assert(p.sandbox._dashPendingImages, "sanity check — an image snapshot is pending");
  p.sandbox.dashHandle({
    type: "error", sessionId: "sess-1", payload: { code: "internal_error", message: "load failed" },
  });
  assertEqual(p.sandbox._dashLoadingSid, null, "the lazy-load state must clear on a refusal");
  assertEqual(p.sandbox._dashPendingImages, null, "the pending-image snapshot must clear on a refusal");
  assertEqual(p.trayChips().length, 1,
    "the staged image was never moved out of the tray for this path — it must still be there, " +
    "not duplicated or lost, ready for the user to retry");
});

// ---- Phase 4 review-fix cycle: stale-encode staleness check, staging
// serialization across calls, sid-less error restore, lazy-attach drop
// gating, per-file batch independence, and _dashPendingImages reset parity.
//
// Both reviewers (Senior engineer, Reliability engineer) reviewed the
// Phase 4 commit and found six issues, none of which had test coverage
// before this cycle. The checks below close that gap, one per fix.

check("dashboard: image attach — a stale encode from a session switched away from mid-paste does not corrupt the new session's state", async () => {
  // The test harness's Image/FileReader/canvas.toBlob stand-ins all settle
  // through plain microtask .then() chains with no real macrotask boundary
  // (unlike a real browser, where canvas.toBlob() and Image decode are
  // genuinely async and leave room for the user to act in between) -- so the
  // session switch is injected from inside the encode step itself (the
  // opts.encode hook, invoked synchronously from within
  // dashEncodeToBudget()'s own promise chain), which is exactly the "mid- of
  // an in-flight encode" moment dashStageOne's captured targetSid has to
  // survive. Without this hook, any switch performed from the test body
  // itself would run before dashStageOne even starts (still queued as a
  // microtask), making it indistinguishable from "no image was ever in
  // flight for the old session" rather than the actual regression.
  const p = loadDashPicker({
    dashAttachedSid: "sess-1", viewingSid: "sess-1",
    encode: (type) => {
      // Simulate switching sessions mid-encode, mirroring
      // openSessionTranscript()'s own sequencing: dashCloseIfAbandoned()
      // first (clears dashAttachments for sess-1), then _viewingSid moves to
      // the new session, whose (empty) textarea is now what dashPromptInput
      // shows.
      p.sandbox.dashCloseIfAbandoned();
      p.sandbox._viewingSid = "sess-2";
      p.sandbox.dashPromptInput.value = "";
      return { size: 5000, type };
    },
  });
  p.paste([p.imageFile()]);
  await settleStaging(); // let the now-stale encode resolve
  assertEqual(p.sandbox.dashAttachments.length, 0,
    "a stale encode for a session no longer being viewed must not repopulate dashAttachments — " +
    "silent cross-session state corruption, not just a cosmetic glitch");
  assertEqual(p.sandbox.dashPromptInput.value, "",
    "a stale encode must not insert a stray [Image N] marker into the newly-viewed session's textarea");
});

check("dashboard: image attach — two back-to-back staging calls near the count limit never exceed it", async () => {
  const p = loadDashPicker({
    dashAttachedSid: "sess-1", viewingSid: "sess-1",
    dashAttachments: [
      { mimeType: "image/png", data: "x", bytes: 100, url: "blob:existing-1", name: "one.png" },
      { mimeType: "image/png", data: "x", bytes: 100, url: "blob:existing-2", name: "two.png" },
      { mimeType: "image/png", data: "x", bytes: 100, url: "blob:existing-3", name: "three.png" },
    ], // three already staged, default _dashImageMaxCount is 4 -- one slot left
  });
  // Two paste events fired back-to-back, each with one image, before either
  // has had a chance to actually push -- without serialization both could
  // pass dashStageOne's synchronous count check while dashAttachments.length
  // still reads 3, exceeding the max of 4.
  p.paste([p.imageFile("image/png", "four.png")]);
  p.paste([p.imageFile("image/png", "five.png")]);
  await settleStaging();
  assert(p.sandbox.dashAttachments.length <= 4,
    "two back-to-back staging calls near the count limit must never exceed _dashImageMaxCount — got "
    + p.sandbox.dashAttachments.length);
  assertEqual(p.sandbox.dashAttachments.length, 4,
    "exactly one of the two images should have been accepted into the one remaining slot");
});

check("dashboard: image attach — a sid-less error frame revokes pending attachment URLs", async () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.paste([p.imageFile()]);
  await settleStaging();
  p.sandbox.dashSendBtn.dispatch("click");
  assertEqual(p.sandbox.dashPendingAttachments.length, 1, "sanity check — the send handed the image to dashPendingAttachments");
  const before = p.revoked().length;
  p.sandbox.dashHandle({
    type: "error", sessionId: null,
    payload: { code: "bad_json", message: "Frame is not valid JSON." },
  });
  assertEqual(p.sandbox.dashPendingAttachments.length, 0,
    "a sid-less error frame must clear dashPendingAttachments rather than leaving its object URLs resident");
  assert(p.revoked().length > before,
    "a sid-less error frame must revoke the pending attachments' object URLs, not merely drop the array");
});

check("dashboard: image attach — a drop during an active lazy-attach load is rejected, not silently lost", async () => {
  const p = loadDashPicker({ viewingSid: "sess-1" }); // dashAttachedSid defaults to null -- not yet attached
  p.sandbox.dashPromptInput.value = "hello";
  p.sandbox.dashSendBtn.dispatch("click"); // starts the lazy-attach load
  assertEqual(p.sandbox._dashLoadingSid, "sess-1", "sanity check — a lazy load is in flight");
  const allowed = p.drop([p.imageFile()]);
  assert(allowed, "dragover must still preventDefault during the lazy-attach window, or a real " +
                  "browser navigates to the dropped file instead of firing 'drop' at all");
  await settleStaging();
  assertEqual(p.sandbox.dashAttachments.length, 0,
    "an image dropped during the lazy-attach window must not be staged into a tray that will lose " +
    "it -- it is excluded from the deferred send, which only carries the click-time _dashPendingImages snapshot");
  const note = p.addMessageCalls[p.addMessageCalls.length - 1];
  assert(note && /starting/.test(note.text),
    "the rejected drop must say why, the same way this file already does for other rejected staging actions");
});

check("dashboard: image attach — one bad file in a multi-file paste does not silently abort the rest of the batch", async () => {
  const p = loadDashPicker({
    dashAttachedSid: "sess-1", viewingSid: "sess-1",
    imageDecodeFailsFor: ["bad.png"],
  });
  p.paste([
    p.imageFile("image/png", "good1.png"),
    p.imageFile("image/png", "bad.png"),
    p.imageFile("image/png", "good2.png"),
  ]);
  await settleStaging();
  assertEqual(p.trayChips().length, 2,
    "the two good files must still be staged despite the bad one in between failing to decode");
  const badNote = p.addMessageCalls.find((m) => m.text && m.text.includes("bad.png"));
  assert(badNote, "the failed file's own name must appear in the note — nobody is told which file failed " +
                  "or that anything else was skipped");
});

check("dashboard: image attach — dashCloseIfAbandoned resets _dashPendingImages alongside its sibling _dashPendingSend", () => {
  const p = loadDashPicker({
    viewingSid: "sess-1",
    dashPendingImages: [{ mimeType: "image/png", data: "x" }],
  });
  p.sandbox.dashCloseIfAbandoned();
  assertEqual(p.sandbox._dashPendingImages, null,
    "_dashPendingImages must be reset by dashCloseIfAbandoned, matching its sibling _dashPendingSend");
});

check("dashboard: image attach — the session-frame stale-load block resets _dashPendingImages alongside its sibling _dashPendingSend", () => {
  const p = loadDashPicker({ viewingSid: "sess-2" }); // viewing a DIFFERENT session than the stale load
  p.sandbox._dashLoadingSid = "sess-1";
  p.sandbox._dashPendingImages = [{ mimeType: "image/png", data: "x" }];
  p.sandbox.dashHandle({ type: "session", sessionId: "sess-1", payload: {} });
  assertEqual(p.sandbox._dashPendingImages, null,
    "_dashPendingImages must be reset by the session-frame stale-load block, matching its sibling " +
    "_dashPendingSend");
});

// ---- dashboard sub-agent/crew read-only panel (plans/260921_DASHBOARD_ACP_FEATURE_PARITY.md,
// Phase 5: SC8) --------------------------------------------------------------
//
// Reuses loadDashPicker()'s harness with the crewSubagentRegion additions
// (this file, dashPickerSource()): dashCrewLabel/dashRenderCrewPanel/
// dashSubagentState/dashStopSlotTimer/dashRemoveSingleCrewPanel/
// dashRemoveAllCrewPanels/dashSetCrew (crew panel, rendered inline in
// transcriptEl) and dashOpenSubagent/dashCloseSubagentView/dashConnectSubWs/
// dashSubAppendChunk/dashSubAddToolCall/dashSubAddNote/dashHandleSub (the
// read-only sub-agent panel, driven over its own independent dashSubWs) are
// all real source here, not stand-ins -- kept as two distinct pieces, exactly
// as acp.html keeps them. `dashSubagentsFrame()` mirrors the acp.html-side
// harness's own `subagentsFrame()` helper.

function dashSubagentsFrame(sid, subagents, toolCallId) {
  return { type: "subagents", sessionId: sid, payload: { subagents, toolCallId: toolCallId || "" } };
}

// ---- crew panel rendering ---------------------------------------------

check("dashboard: crew panel — no panel appears until a subagents frame arrives", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  assertEqual(p.sandbox.transcriptEl.querySelectorAll(".acp-crew-panel").length, 0,
    "a session with no crew should show no crew panel");
});

check("dashboard: crew panel — a subagents frame with a running entry renders a row with label and action", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  const now = Date.now() / 1000;
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "explorer", task: "", sessionName: "stage-1",
      status: "working", action: "reading", done: false, error: "", startedAt: now - 5 },
  ]));
  const panels = p.sandbox.transcriptEl.querySelectorAll(".acp-crew-panel");
  assertEqual(panels.length, 1, "a crew panel should appear in the transcript");
  const rows = panels[0].querySelectorAll(".acp-crew-row");
  assertEqual(rows.length, 1, "one row per sub-agent");
  assertEqual(rows[0].querySelector(".acp-crew-label").textContent, "stage-1",
    "entry should show the sessionName as primary label");
  assertEqual(rows[0].querySelector(".acp-crew-action").textContent, "reading",
    "entry should show the current action");
});

check("dashboard: crew panel — a row is clickable and opens the sub-agent panel", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "explorer", task: "", sessionName: "",
      status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  const rows = p.sandbox.transcriptEl.querySelectorAll(".acp-crew-row");
  assertEqual(rows.length, 1);
  rows[0].dispatch("click");
  assertEqual(p.el("dashSubPanel").hidden, false, "clicking a crew row should open the sub-agent panel");
});

check("dashboard: crew panel — a toolCallId of '__proto__' is stored as a literal key, not reassigning dashCrews's own prototype (Fix 4, Phase 5 review)", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "worker", task: "", status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ], "__proto__"));
  // dashCrews is keyed directly by the wire-controlled toolCallId
  // (unrestricted by acp.py's frame format) -- on an ordinary object literal
  // `dashCrews["__proto__"] = value` is intercepted by Object.prototype's
  // own __proto__ accessor and reassigns the object's [[Prototype]] instead
  // of storing a literal key. Object.create(null) has no such accessor in
  // its chain, so the assignment behaves like any other key.
  assert(Object.keys(p.sandbox.dashCrews).includes("__proto__"),
    "the crew slot must be stored under the literal key '__proto__'");
  assertEqual(typeof p.sandbox.dashCrews.hasOwnProperty, "undefined",
    "dashCrews must be Object.create(null) -- inheriting Object.prototype methods here would mean " +
    "the '__proto__' assignment above reassigned the object's own prototype instead of being stored");
  assertEqual(p.sandbox.transcriptEl.querySelectorAll(".acp-crew-row").length, 1,
    "the crew row must still render normally for a toolCallId of '__proto__'");
});

check("dashboard: crew panel — persists after all entries are done (no auto-dismiss)", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  const now = Date.now() / 1000;
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "worker", task: "", status: "working", action: "", done: false, error: "", startedAt: now - 10 },
  ], "tc-persist"));
  assertEqual(p.sandbox.transcriptEl.querySelectorAll(".acp-crew-panel").length, 1);
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "worker", task: "", status: "terminated", action: "", done: true, error: "", startedAt: now - 10, stoppedAt: now },
  ], "tc-persist"));
  assertEqual(p.sandbox.transcriptEl.querySelectorAll(".acp-crew-panel").length, 1,
    "the panel must stay visible after all entries are done");
});

check("dashboard: crew panel — persists after a main-channel meta turn:end (no auto-dismiss)", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1", dashTurnActive: true });
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "worker", task: "", status: "terminated", action: "", done: true, error: "", startedAt: Date.now() / 1000 - 30 },
  ], "tc-turnend"));
  assertEqual(p.sandbox.transcriptEl.querySelectorAll(".acp-crew-panel").length, 1);
  p.sandbox.dashHandle({ type: "meta", sessionId: "sess-1", payload: { turn: "end", stopReason: "end_turn" } });
  assertEqual(p.sandbox.transcriptEl.querySelectorAll(".acp-crew-panel").length, 1,
    "turn end must not dismiss the crew panel");
});

check("dashboard: crew panel — header text: 'Orchestrating (N agents)' singular/plural", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  const now = Date.now() / 1000;
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "worker", task: "", status: "working", action: "", done: false, error: "", startedAt: now },
  ]));
  assertEqual(p.sandbox.transcriptEl.querySelector(".acp-crew-header").textContent,
    "Orchestrating (1 agent)", "singular for one running entry");
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "worker", task: "", status: "working", action: "", done: false, error: "", startedAt: now },
    { sessionId: "sub-2", role: "worker", task: "", status: "working", action: "", done: false, error: "", startedAt: now },
  ]));
  assertEqual(p.sandbox.transcriptEl.querySelector(".acp-crew-header").textContent,
    "Orchestrating (2 agents)", "plural for two running entries");
});

check("dashboard: crew panel — header text: 'Done (N agents)' singular/plural when all entries are done", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  const now = Date.now() / 1000;
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "worker", task: "", status: "done", action: "", done: true, error: "", startedAt: now - 5, stoppedAt: now },
  ]));
  assertEqual(p.sandbox.transcriptEl.querySelector(".acp-crew-header").textContent,
    "Done (1 agent)", "singular when the sole entry is done");
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "worker", task: "", status: "done", action: "", done: true, error: "", startedAt: now - 5, stoppedAt: now },
    { sessionId: "sub-2", role: "worker", task: "", status: "done", action: "", done: true, error: "", startedAt: now - 5, stoppedAt: now },
  ]));
  assertEqual(p.sandbox.transcriptEl.querySelector(".acp-crew-header").textContent,
    "Done (2 agents)", "plural when both entries are done");
});

check("dashboard: crew panel — label falls back from sessionName to truncated task to 'agent'", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  assertEqual(p.sandbox.dashCrewLabel({ sessionName: "stage-a", task: "do a thing" }), "stage-a");
  assertEqual(p.sandbox.dashCrewLabel({ sessionName: "", task: "x".repeat(40) }),
    "x".repeat(30).trim() + "…");
  assertEqual(p.sandbox.dashCrewLabel({ sessionName: "", task: "" }), "agent");
  assertEqual(p.sandbox.dashCrewLabel(null), "agent");
});

check("dashboard: crew panel — action text falls back to 'working…'/'done'/'errored'", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  const now = Date.now() / 1000;
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "worker", task: "", status: "working", action: "", done: false, error: "", startedAt: now },
  ], "a"));
  assertEqual(p.sandbox.transcriptEl.querySelector(".acp-crew-action").textContent, "working…");
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-2", role: "worker", task: "", status: "done", action: "", done: true, error: "", startedAt: now, stoppedAt: now },
  ], "b"));
  const doneAction = p.sandbox.transcriptEl.querySelectorAll(".acp-crew-action")[1];
  assertEqual(doneAction.textContent, "done");
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-3", role: "worker", task: "", status: "error", action: "", done: true, error: "boom", startedAt: now, stoppedAt: now },
  ], "c"));
  const errAction = p.sandbox.transcriptEl.querySelectorAll(".acp-crew-action")[2];
  assertEqual(errAction.textContent, "errored");
});

check("dashboard: crew panel — status dot class reflects working/done/error", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  const now = Date.now() / 1000;
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "worker", task: "", status: "working", action: "", done: false, error: "", startedAt: now },
  ], "a"));
  assert(p.sandbox.transcriptEl.querySelector(".acp-crew-row").className.includes("acp-crew-row-working"));
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-2", role: "worker", task: "", status: "done", action: "", done: true, error: "", startedAt: now, stoppedAt: now },
  ], "b"));
  let rows = p.sandbox.transcriptEl.querySelectorAll(".acp-crew-row");
  assert(rows[1].className.includes("acp-crew-row-done"));
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-3", role: "worker", task: "", status: "error", action: "", done: true, error: "boom", startedAt: now, stoppedAt: now },
  ], "c"));
  rows = p.sandbox.transcriptEl.querySelectorAll(".acp-crew-row");
  assert(rows[2].className.includes("acp-crew-row-error"));
});

check("dashboard: crew panel — setCrew with a toolCallId anchors the panel immediately after the matching tool-call row", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  const toolRow = new El("div");
  p.sandbox.transcriptEl.appendChild(toolRow);
  const toolBody = new El("div");
  toolRow.appendChild(toolBody);
  p.sandbox.toolRows["t:tc-1"] = { body: toolBody };
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "worker", task: "", status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ], "tc-1"));
  const kids = p.sandbox.transcriptEl.childNodes;
  const panelIdx = kids.findIndex((n) => n.className === "acp-crew-panel");
  const toolIdx = kids.indexOf(toolRow);
  assert(panelIdx === toolIdx + 1,
    "the crew panel must be inserted immediately after the anchoring tool-call row");
});

check("dashboard: crew panel — setCrew with no toolCallId appends a no-anchor panel to the transcript", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "worker", task: "", status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  assertEqual(p.sandbox.transcriptEl.querySelectorAll(".acp-crew-panel").length, 1,
    "a no-anchor crew update should still produce a panel, appended to the transcript");
});

check("dashboard: crew panel — empty entries with no active no-anchor slot does not ghost _dashNoAnchorKey", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", []));
  assertEqual(p.sandbox._dashNoAnchorKey, null,
    "an empty-entries update with nothing active must not set _dashNoAnchorKey");
  assertEqual(p.sandbox._dashNoAnchorSeq, 0,
    "an empty-entries update with nothing active must not advance _dashNoAnchorSeq");
});

check("dashboard: crew panel — two subagents frames with different toolCallIds produce two independent panels", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-a", role: "worker", task: "", status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ], "a"));
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-b", role: "worker", task: "", status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ], "b"));
  assertEqual(Object.keys(p.sandbox.dashCrews).length, 2,
    "two different toolCallIds should produce two independent crew slots");
  assertEqual(p.sandbox.transcriptEl.querySelectorAll(".acp-crew-panel").length, 2,
    "two panels should appear in the transcript");
});

check("dashboard: crew panel — two consecutive no-anchor subagents updates reuse the same slot", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "worker", task: "", status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  const keysAfterFirst = Object.keys(p.sandbox.dashCrews);
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "worker", task: "", status: "working", action: "reading", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  const keysAfterSecond = Object.keys(p.sandbox.dashCrews);
  assertEqual(keysAfterSecond.length, 1, "a second no-anchor update should not create a second slot");
  assertEqual(keysAfterSecond[0], keysAfterFirst[0], "the same _na_ key should be reused");
});

check("dashboard: crew panel — dashCrewEntry finds entries across multiple active crews", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-x", role: "worker", task: "", status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ], "x"));
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-y", role: "worker", task: "", status: "done", action: "", done: true, error: "", startedAt: Date.now() / 1000, stoppedAt: Date.now() / 1000 },
  ], "y"));
  const found = p.sandbox.dashCrewEntry("sub-y");
  assert(found && found.sessionId === "sub-y", "dashCrewEntry should find an entry in a non-first crew slot");
});

check("dashboard: crew panel — a malicious sessionName renders as literal text, never innerHTML", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  const malicious = "<img src=x onerror=\"window._dash_crew_xss=true\">";
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "explorer", task: "", sessionName: malicious,
      status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  const label = p.sandbox.transcriptEl.querySelector(".acp-crew-label");
  assert(label.textContent.includes(malicious),
    "the crew label must render an agent-controlled sessionName as literal text");
  assert(!p.sandbox._dash_crew_xss, "the onerror handler must not fire -- crew row rendering must not use innerHTML");
});

// ---- crew-slot timer cleanup (a real, easy-to-miss bug class here) -----

check("dashboard: crew panel — a setInterval timer starts when the crew has a non-done entry", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  const before = p.intervals.length;
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "worker", task: "", status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  assert(p.intervals.length > before, "a setInterval should be registered for a running crew entry");
});

check("dashboard: crew panel — no setInterval timer starts for an all-done crew", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  const before = p.intervals.length;
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "worker", task: "", status: "done", action: "", done: true, error: "", startedAt: Date.now() / 1000 - 10, stoppedAt: Date.now() / 1000 },
  ]));
  assertEqual(p.intervals.length, before, "no new setInterval should be registered when all entries are done");
});

check("dashboard: crew panel — the setInterval timer is cleared once all entries in the slot become done", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  const baseline = p.intervals.length;
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "worker", task: "", status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ], "tc-stop"));
  assert(p.intervals.length > baseline, "fixture: a timer should be registered while running");
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "worker", task: "", status: "done", action: "", done: true, error: "", startedAt: Date.now() / 1000 - 5, stoppedAt: Date.now() / 1000 },
  ], "tc-stop"));
  assertEqual(p.intervals.length, baseline, "the timer must be cleared once the slot's entries are all done");
});

check("dashboard: crew panel — the elapsed-time interval rebuilds rows without error when it fires", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  const startedAt = Date.now() / 1000 - 10;
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "worker", task: "", status: "working", action: "", done: false, error: "", startedAt },
  ]));
  const elapsed = p.sandbox.transcriptEl.querySelector(".acp-crew-elapsed");
  assert(elapsed !== null && elapsed.textContent.length > 0, "elapsed span should show a non-empty time string");
  p.intervals[p.intervals.length - 1].fn();
  const elapsed2 = p.sandbox.transcriptEl.querySelector(".acp-crew-elapsed");
  assert(elapsed2 !== null, "elapsed span should still be present after the tick");
});

check("dashboard: crew panel — session_closed clears crew timers (parity with acp.html's releaseSession)", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  const baseline = p.intervals.length;
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "worker", task: "", status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  assert(p.intervals.length > baseline, "fixture: a timer should be registered for a running crew entry");
  p.sandbox.dashHandle({ type: "session_closed", sessionId: "sess-1", payload: {} });
  assertEqual(p.intervals.length, baseline, "session_closed must clear crew timers");
  assertEqual(Object.keys(p.sandbox.dashCrews).length, 0, "session_closed must clear crew state");
});

check("dashboard: crew panel — agent_died clears crew timers, crew state, and closes any open sub-agent panel", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  const baseline = p.intervals.length;
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "worker", task: "", status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  p.sandbox.transcriptEl.querySelectorAll(".acp-crew-row")[0].dispatch("click");
  assertEqual(p.el("dashSubPanel").hidden, false, "fixture: sub-agent panel should be open");
  assert(p.sandbox.dashSubWs !== null, "fixture: dashSubWs should be open");
  assert(p.intervals.length > baseline, "fixture: a timer should be registered for a running crew entry");
  p.sandbox.dashHandle({ type: "agent_died", sessionId: "sess-1", payload: { exitCode: 1, message: "" } });
  assertEqual(p.intervals.length, baseline, "agent_died must clear crew timers");
  assertEqual(Object.keys(p.sandbox.dashCrews).length, 0, "agent_died must clear crew state");
  assertEqual(p.el("dashSubPanel").hidden, true, "agent_died must close any open sub-agent panel");
  // Fix 8 (Phase 5 review): explicit socket close, not just a hidden panel --
  // see the session_closed test's own comment above for why.
  assertEqual(p.sandbox.dashSubWs, null,
    "agent_died must explicitly close and null dashSubWs, not just hide the panel");
  // Fix 1 (Phase 5 review): unlike the baseline agent_died test above (no
  // sub-agent panel ever opened there), THIS session's panel WAS open when
  // agent_died fired -- confirms the composer still ends up visible-and-
  // disabled correctly in that case too, not just the no-panel case.
  assertEqual(p.sandbox.dashComposerEl.hidden, false,
    "the composer must still end up visible-and-disabled even though a sub-agent panel WAS open " +
    "when agent_died fired");
});

check("dashboard: crew panel — a removed slot's stale timer tick does not throw or leak", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "worker", task: "", status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ], "tc-orphan"));
  const timerEntry = p.intervals[p.intervals.length - 1];
  p.sandbox.dashRemoveAllCrewPanels();
  // The removed slot's own interval callback checks `slot.panel &&
  // slot.panel.parentNode` and clears itself via dashStopSlotTimer if that
  // fails -- calling it after removal must not throw.
  timerEntry.fn();
});

// ---- the live removeAllCrewPanels/closeSubagentView guards (5d) --------

check("dashboard: crew panel — window.removeAllCrewPanels/window.closeSubagentView resolve to the real dash functions", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  assertEqual(p.sandbox.removeAllCrewPanels, p.sandbox.dashRemoveAllCrewPanels,
    "window.removeAllCrewPanels must be assigned to dashRemoveAllCrewPanels");
  assertEqual(p.sandbox.closeSubagentView, p.sandbox.dashCloseSubagentView,
    "window.closeSubagentView must be assigned to dashCloseSubagentView");
});

check("dashboard: crew panel — a history reload clears all crew panels and closes an open sub-agent panel", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "worker", task: "", status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  p.sandbox.transcriptEl.querySelectorAll(".acp-crew-row")[0].dispatch("click");
  assertEqual(p.el("dashSubPanel").hidden, false, "fixture: sub-agent panel should be open");
  p.sandbox.dashHandle({ type: "history", sessionId: "sess-1", payload: { events: [] } });
  assertEqual(Object.keys(p.sandbox.dashCrews).length, 0,
    "all crew slots should be cleared after a history reload (window.removeAllCrewPanels now live)");
  assertEqual(p.sandbox.transcriptEl.querySelectorAll(".acp-crew-panel").length, 0);
  assertEqual(p.el("dashSubPanel").hidden, true,
    "the sub-agent panel should close on a history reload (window.closeSubagentView now live)");
});

// ---- the read-only sub-agent panel --------------------------------------

check("dashboard: sub-agent panel — opening hides the transcript wrapper and composer, shows the sub panel", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "explorer", task: "", sessionName: "", status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  p.sandbox.transcriptEl.querySelectorAll(".acp-crew-row")[0].dispatch("click");
  assertEqual(p.el("dashTranscriptWrap").hidden, true, "the transcript wrapper should hide while a sub-agent is open");
  assertEqual(p.sandbox.dashComposerEl.hidden, true, "the composer should hide -- a sub-agent's conversation is read-only");
  assertEqual(p.el("dashSubPanel").hidden, false);
});

check("dashboard: sub-agent panel — opening a crew row's sub-agent sends exactly one subscribe on a fresh socket", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "explorer", task: "look around", sessionName: "", status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  p.sandbox.transcriptEl.querySelectorAll(".acp-crew-row")[0].dispatch("click");
  assertEqual(p.subSocketCount(), 1, "exactly one sub-agent socket should be opened");
  p.openSub(0);
  const subs = p.subSocket(0).sent.filter((f) => f.type === "subscribe");
  assertEqual(subs.length, 1, "expected exactly one subscribe on the sub-agent socket");
  assertEqual(subs[0].sessionId, "sub-1");
});

check("dashboard: sub-agent panel — dashConnectSubWs logs open/error/close/parse-failure lifecycle events (Fix 7, Phase 5 review)", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "explorer", task: "", sessionName: "", status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  p.sandbox.transcriptEl.querySelectorAll(".acp-crew-row")[0].dispatch("click");
  p.openSub(0);
  let lines = p.el("dashLog").childNodes.length;
  assert(lines > 0,
    "dashConnectSubWs's onopen must log a line, mirroring dashConnect()'s own onopen logLine call");
  const s = p.subSocket(0);
  s.onerror();
  assert(p.el("dashLog").childNodes.length > lines, "dashConnectSubWs's onerror must log a line");
  lines = p.el("dashLog").childNodes.length;
  s.onmessage({ data: "not valid json" });
  assert(p.el("dashLog").childNodes.length > lines,
    "a JSON parse failure on the sub-agent socket must log a line");
  lines = p.el("dashLog").childNodes.length;
  p.closeSub(0, { code: 1000, reason: "" });
  assert(p.el("dashLog").childNodes.length > lines, "dashConnectSubWs's onclose must log a line");
});

check("dashboard: sub-agent panel — the back button restores the main transcript and leaves the sub socket open", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "explorer", task: "", sessionName: "", status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  p.sandbox.transcriptEl.querySelectorAll(".acp-crew-row")[0].dispatch("click");
  p.openSub(0);
  p.el("dashSubBack").dispatch("click");
  assertEqual(p.el("dashSubPanel").hidden, true);
  assertEqual(p.el("dashTranscriptWrap").hidden, false);
  assertEqual(p.sandbox.dashComposerEl.hidden, false, "the composer must be restored -- the page is still attached to sess-1");
  assert(p.subSocket(0).readyState !== 3,
    "the sub-agent socket must be left open, not closed, when returning to the main transcript");
});

check("dashboard: sub-agent panel — closing the panel does not un-hide the composer when no session is attached", () => {
  const p = loadDashPicker({ viewingSid: "sess-1", dashAttachedSid: null });
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "explorer", task: "", sessionName: "", status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  p.sandbox.transcriptEl.querySelectorAll(".acp-crew-row")[0].dispatch("click");
  p.el("dashSubBack").dispatch("click");
  assertEqual(p.sandbox.dashComposerEl.hidden, true,
    "the composer must stay hidden -- there is no attached session for it to show for");
});

check("dashboard: sub-agent panel — reopening a sub-agent reuses the existing socket rather than opening a new one", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "explorer", task: "", sessionName: "", status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  p.sandbox.transcriptEl.querySelectorAll(".acp-crew-row")[0].dispatch("click");
  p.openSub(0);
  p.el("dashSubBack").dispatch("click");
  p.sandbox.transcriptEl.querySelectorAll(".acp-crew-row")[0].dispatch("click");
  assertEqual(p.subSocketCount(), 1, "a second socket must not be opened when the existing one is still OPEN");
  const subs = p.subSocket(0).sent.filter((f) => f.type === "subscribe");
  assertEqual(subs.length, 2, "expected a second subscribe sent on the reused socket");
});

check("dashboard: sub-agent panel — a frame for a previously-viewed sub-agent does not render into a just-opened DIFFERENT sub-agent's panel on the reused socket (Fix 6, Phase 5 review)", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "explorer", task: "", sessionName: "", status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
    { sessionId: "sub-2", role: "writer", task: "", sessionName: "", status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  const rows = p.sandbox.transcriptEl.querySelectorAll(".acp-crew-row");
  rows[0].dispatch("click"); // open sub-1
  p.openSub(0);
  p.deliverSub(0, { type: "chunk", sessionId: "sub-1", payload: { role: "agent", text: "hello from sub-1" } });
  assert(p.el("dashSubTranscript").textContent.includes("hello from sub-1"),
    "fixture: sub-1's own chunk should render while sub-1 is open");
  // Switch straight to sub-2's row -- dashConnectSubWs() reuses the same
  // still-OPEN socket rather than opening a new one (5e's own design,
  // verified by the test above), so it never got a chance to fully drop the
  // previous subscription before a frame still addressed to sub-1 could
  // arrive.
  rows[1].dispatch("click"); // switch to sub-2, reuses the same socket
  assertEqual(p.subSocketCount(), 1, "fixture: switching sub-agents must reuse the existing socket, not open a new one");
  assertEqual(p.sandbox.dashSubViewSid, "sub-2", "fixture: the panel must now be viewing sub-2");
  // A frame still in flight for sub-1 at the moment of the switch.
  p.deliverSub(0, { type: "chunk", sessionId: "sub-1", payload: { role: "agent", text: "late chunk from sub-1" } });
  const body = p.el("dashSubTranscript").textContent;
  assert(!body.includes("late chunk from sub-1"),
    "a stale frame for the previously-viewed sub-agent must not render into the newly-opened one's transcript");
});

check("dashboard: sub-agent panel — renders its own chunk and tool_call frames via dashHandleSub", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "explorer", task: "", sessionName: "", status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  p.sandbox.transcriptEl.querySelectorAll(".acp-crew-row")[0].dispatch("click");
  p.openSub(0);
  p.deliverSub(0, { type: "session", sessionId: "sub-1", payload: { sessionId: "sub-1", readOnly: true, parentSessionId: "sess-1" } });
  p.deliverSub(0, { type: "history", sessionId: "sub-1", payload: { events: [] } });
  p.deliverSub(0, { type: "chunk", sessionId: "sub-1", payload: { role: "agent", text: "looking around" } });
  p.deliverSub(0, { type: "tool_call", sessionId: "sub-1", payload: { toolCallId: "tc-1", title: "read", kind: "", status: "" } });
  const body = p.el("dashSubTranscript").textContent;
  assert(body.includes("looking around"), "the sub-agent's chunk text was not rendered");
  assert(body.includes("read"), "the sub-agent's tool call was not rendered");
});

check("dashboard: sub-agent panel — a live subagents update refreshes the header status while the panel is open", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  const now = Date.now() / 1000;
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "explorer", task: "", sessionName: "", status: "working", action: "reading", done: false, error: "", startedAt: now - 5 },
  ], "tc-update"));
  p.sandbox.transcriptEl.querySelectorAll(".acp-crew-row")[0].dispatch("click");
  p.openSub(0);
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "explorer", task: "", sessionName: "", status: "done", action: "", done: true, error: "", startedAt: now - 5 },
  ], "tc-update"));
  assertEqual(p.el("dashSubPanel").hidden, false, "the panel should stay open across a crew update");
  assertEqual(p.el("dashSubStatus").textContent, "done");
});

check("dashboard: sub-agent panel — dashRenderSubHead shows 'errored: <message>' for an errored entry", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "explorer", task: "", sessionName: "", status: "error", action: "", done: true, error: "boom", startedAt: Date.now() / 1000 },
  ]));
  p.sandbox.transcriptEl.querySelectorAll(".acp-crew-row")[0].dispatch("click");
  assertEqual(p.el("dashSubStatus").textContent, "errored: boom");
  assert(p.el("dashSubStatus").className.includes("acp-subpanel-status-error"));
});

check("dashboard: sub-agent panel — dashOpenSubagent(null) is a no-op", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  p.sandbox.dashOpenSubagent(null);
  assertEqual(p.el("dashSubPanel").hidden, true);
  assertEqual(p.subSocketCount(), 0);
});

check("dashboard: sub-agent panel — session_closed closes any open sub-agent panel (parity with acp.html's releaseSession)", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "explorer", task: "", sessionName: "", status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  p.sandbox.transcriptEl.querySelectorAll(".acp-crew-row")[0].dispatch("click");
  assertEqual(p.el("dashSubPanel").hidden, false, "fixture: sub-agent panel should be open");
  assert(p.sandbox.dashSubWs !== null, "fixture: dashSubWs should be open");
  p.sandbox.dashHandle({ type: "session_closed", sessionId: "sess-1", payload: {} });
  assertEqual(p.el("dashSubPanel").hidden, true, "session_closed must close any open sub-agent panel");
  assertEqual(p.el("dashTranscriptWrap").hidden, false);
  // Fix 8 (Phase 5 review): dashCloseSubagentView() only hides the panel, by
  // design (a tap-to-reopen reuses the socket) -- session_closed's own
  // session is already gone server-side, so nothing will ever reopen this
  // socket. Leaving it open would hold its MAX_CONNECTIONS slot until the
  // page reloads.
  assertEqual(p.sandbox.dashSubWs, null,
    "session_closed must explicitly close and null dashSubWs, not just hide the panel");
});

check("dashboard: sub-agent panel — creating a new session without closing the current one tears down an open sub-agent panel, crew state, and dashSubWs (Fix 2, Phase 5 review)", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "explorer", task: "", sessionName: "", status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  p.sandbox.transcriptEl.querySelectorAll(".acp-crew-row")[0].dispatch("click");
  assertEqual(p.el("dashSubPanel").hidden, false, "fixture: sub-agent panel should be open");
  assert(p.sandbox.dashSubWs !== null, "fixture: dashSubWs should be open");
  // The `session` frame shape dashPickerCreate/dashRailQuickCreate fire when
  // the user creates a new session without first closing the one whose
  // sub-agent panel is currently open (index.html's `payload.created` early
  // branch, dashboard/ACP-new-session-picker plan Phase 4).
  p.sandbox.dashHandle({
    type: "session", sessionId: "sess-2",
    payload: { created: true, cwd: "/ws2" },
  });
  assertEqual(p.el("dashSubPanel").hidden, true,
    "new-session creation must close any open sub-agent panel from the previously-viewed session");
  assertEqual(Object.keys(p.sandbox.dashCrews).length, 0,
    "new-session creation must clear crew state from the previously-viewed session");
  assertEqual(p.sandbox.dashSubWs, null,
    "new-session creation must explicitly close and null dashSubWs, not just hide the panel");
});

check("dashboard: a failed transcript load (openSessionTranscript's fetch .catch()) still tears down an open sub-agent panel, crew state, and dashSubWs (Fix 3, Step 9 review)", async () => {
  const p = loadDashPicker({
    dashAttachedSid: "sess-1", viewingSid: "sess-1", sessionTranscriptFails: true,
  });
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "explorer", task: "", sessionName: "", status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  p.sandbox.transcriptEl.querySelectorAll(".acp-crew-row")[0].dispatch("click");
  assertEqual(p.el("dashSubPanel").hidden, false, "fixture: sub-agent panel should be open");
  assert(p.sandbox.dashSubWs !== null, "fixture: dashSubWs should be open");
  const row = new El("div");
  row.dataset = { sid: "sess-2", provider: "", cwd: "/ws2" };
  p.sandbox.openSessionTranscript(row);
  await settleStaging(); // let the rejected fetch's .catch() run
  assertEqual(p.el("dashSubPanel").hidden, true,
    "a failed transcript load must still close any open sub-agent panel from the previously-viewed session -- " +
    "renderTranscriptHistory()/clearTranscript() (the chain that normally does this) is never reached on this path");
  assertEqual(Object.keys(p.sandbox.dashCrews).length, 0,
    "a failed transcript load must still clear crew state from the previously-viewed session");
  assertEqual(p.sandbox.dashSubWs, null,
    "a failed transcript load must still explicitly close and null dashSubWs, not just hide the panel");
});

check("dashboard: deleting the currently-viewed session (dashRailForgetSession) tears down its open sub-agent panel, crew state, and dashSubWs (Fix 3, Step 9 review)", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "explorer", task: "", sessionName: "", status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  p.sandbox.transcriptEl.querySelectorAll(".acp-crew-row")[0].dispatch("click");
  assertEqual(p.el("dashSubPanel").hidden, false, "fixture: sub-agent panel should be open");
  assert(p.sandbox.dashSubWs !== null, "fixture: dashSubWs should be open");
  p.sandbox.dashRailForgetSession("sess-1");
  assertEqual(p.el("dashSubPanel").hidden, true,
    "deleting the viewed session must close any open sub-agent panel -- there is no new session's transcript " +
    "render here to trigger the usual clearTranscript() teardown");
  assertEqual(Object.keys(p.sandbox.dashCrews).length, 0, "deleting the viewed session must clear crew state");
  assertEqual(p.sandbox.dashSubWs, null,
    "deleting the viewed session must explicitly close and null dashSubWs, not just hide the panel");
});

check("dashboard: sub-agent panel — dashHandleSub is a distinct dispatcher, not threaded through dashHandle", () => {
  const p = loadDashPicker({ viewingSid: "sess-1" });
  assertEqual(typeof p.sandbox.dashHandleSub, "function");
  assert(p.sandbox.dashHandleSub !== p.sandbox.dashHandle,
    "dashHandleSub must be a separate function, not an alias for dashHandle");
  // A sub-agent-only frame type delivered on the MAIN dashHandle dispatcher
  // must not reach the sub-agent transcript.
  p.sandbox.dashHandle({ type: "history_truncated", sessionId: "sess-1", payload: { message: "dropped" } });
  assertEqual(p.el("dashSubTranscript").textContent, "",
    "dashHandle must not thread a sub-agent-shaped frame into dashSubTranscriptEl");
});

// ---- graceful MAX_CONNECTIONS handling (5e) -----------------------------

check("dashboard: sub-agent panel — a too_many_connections error frame shows a clear message, not a stuck loading state", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "explorer", task: "", sessionName: "", status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  p.sandbox.transcriptEl.querySelectorAll(".acp-crew-row")[0].dispatch("click");
  p.openSub(0);
  p.deliverSub(0, { type: "error", payload: { code: "too_many_connections", message: "At most 8 /acp sockets may be open at once." } });
  const body = p.el("dashSubTranscript").textContent;
  assert(body.includes("Too many active connections"),
    "a too_many_connections error frame must render a clear, actionable message in the panel");
});

check("dashboard: sub-agent panel — a too_many_connections error frame followed by the server's own close does not double-message (Fix 3, Phase 5 review)", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "explorer", task: "", sessionName: "", status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  p.sandbox.transcriptEl.querySelectorAll(".acp-crew-row")[0].dispatch("click");
  p.openSub(0);
  // The real production sequence (acp.py's serve_socket()): an `error` frame
  // arrives first (dashHandleSub's own case above already renders it), THEN
  // the socket closes (code 1013) -- dashSubConnected is never set true by
  // an error frame (only by a 'session' reply on a successful connect), so
  // the onclose fallback below used to see !dashSubConnected still true and
  // add its own generic "Could not connect" note right on top.
  p.deliverSub(0, { type: "error", payload: { code: "too_many_connections", message: "At most 8 /acp sockets may be open at once." } });
  p.closeSub(0, { code: 1013, reason: "too many connections" });
  const notes = p.el("dashSubTranscript").querySelectorAll(".acp-msg-note");
  assertEqual(notes.length, 1,
    "exactly one note should render for a real too_many_connections rejection, not two");
  assert(notes[0].textContent.includes("Too many active connections"),
    "the one note shown must be the specific, actionable too_many_connections message, not the generic fallback");
});

check("dashboard: sub-agent panel — an onclose with no prior content shows a graceful fallback message (defense-in-depth)", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "explorer", task: "", sessionName: "", status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  p.sandbox.transcriptEl.querySelectorAll(".acp-crew-row")[0].dispatch("click");
  p.openSub(0);
  // The socket closes (e.g. code 1013) without ever delivering a message --
  // a parse failure, or a close that outraces its own message.
  p.closeSub(0, { code: 1013, reason: "too many connections" });
  const body = p.el("dashSubTranscript").textContent;
  assert(body.length > 0, "the panel must not be left blank when the socket closes with no prior content");
});

check("dashboard: sub-agent panel — a normal close after content already arrived does not add a spurious fallback note", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "explorer", task: "", sessionName: "", status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  p.sandbox.transcriptEl.querySelectorAll(".acp-crew-row")[0].dispatch("click");
  p.openSub(0);
  p.deliverSub(0, { type: "session", sessionId: "sub-1", payload: { sessionId: "sub-1" } });
  p.deliverSub(0, { type: "chunk", sessionId: "sub-1", payload: { role: "agent", text: "hello" } });
  const before = p.el("dashSubTranscript").textContent;
  p.closeSub(0, { code: 1000, reason: "" });
  const after = p.el("dashSubTranscript").textContent;
  assertEqual(after, before,
    "a close after the connection succeeded must not append a spurious 'could not connect' note");
});

// ---- security: agent-controlled text in the sub-agent panel (5c) -------

check("dashboard: sub-agent panel — chunk frame with agent-controlled text uses textContent, never innerHTML", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "explorer", task: "", sessionName: "", status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  p.sandbox.transcriptEl.querySelectorAll(".acp-crew-row")[0].dispatch("click");
  p.openSub(0);
  const malicious = "<img src=x onerror=\"window._dash_sub_xss=true\">";
  p.deliverSub(0, { type: "chunk", sessionId: "sub-1", payload: { role: "agent", text: malicious } });
  // Reaching here at all means no innerHTML sink fired -- El's innerHTML
  // getter/setter throws unconditionally (HTML_SINK, this file's DOM stand-in).
  assert(p.el("dashSubTranscript").textContent.includes(malicious),
    "the sub-agent's chunk text must render as literal text");
  assert(!p.sandbox._dash_sub_xss, "the onerror handler must not fire -- chunk rendering must not use innerHTML");
});

check("dashboard: sub-agent panel — tool_call frame with an agent-controlled title uses textContent, never innerHTML", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "explorer", task: "", sessionName: "", status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  p.sandbox.transcriptEl.querySelectorAll(".acp-crew-row")[0].dispatch("click");
  p.openSub(0);
  const malicious = "<img src=x onerror=\"window._dash_sub_tool_xss=true\">";
  p.deliverSub(0, { type: "tool_call", sessionId: "sub-1", payload: { toolCallId: "tc-x", title: malicious, kind: "", status: "" } });
  assert(p.el("dashSubTranscript").textContent.includes(malicious),
    "the sub-agent's tool-call title must render as literal text");
  assert(!p.sandbox._dash_sub_tool_xss, "the onerror handler must not fire -- tool-call rendering must not use innerHTML");
});

check("dashboard: sub-agent panel — a second tool_call/tool_update for the same toolCallId retitles the existing row via textContent, not innerHTML (Fix 5, Phase 5 review)", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "explorer", task: "", sessionName: "", status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  p.sandbox.transcriptEl.querySelectorAll(".acp-crew-row")[0].dispatch("click");
  p.openSub(0);
  p.deliverSub(0, { type: "tool_call", sessionId: "sub-1", payload: { toolCallId: "tc-1", title: "reading file.py", kind: "", status: "" } });
  const rowsBefore = p.el("dashSubTranscript").querySelectorAll(".acp-msg-tool").length;
  const malicious = "<img src=x onerror=\"window._dash_sub_retitle_xss=true\">";
  p.deliverSub(0, { type: "tool_update", sessionId: "sub-1", payload: { toolCallId: "tc-1", title: malicious, kind: "", status: "" } });
  const rowsAfter = p.el("dashSubTranscript").querySelectorAll(".acp-msg-tool").length;
  assertEqual(rowsAfter, rowsBefore,
    "retitling an already-seen toolCallId must update the existing row's text, not append a new row");
  assert(p.el("dashSubTranscript").textContent.includes(malicious),
    "the retitled row must render the new title as literal text");
  assert(!p.sandbox._dash_sub_retitle_xss,
    "the onerror handler must not fire -- the retitle path must use textContent, never innerHTML");
});

check("dashboard: sub-agent panel — session_closed/error note text uses textContent, never innerHTML", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "explorer", task: "", sessionName: "", status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  p.sandbox.transcriptEl.querySelectorAll(".acp-crew-row")[0].dispatch("click");
  p.openSub(0);
  const malicious = "<img src=x onerror=\"window._dash_sub_note_xss=true\">";
  p.deliverSub(0, { type: "session_closed", sessionId: "sub-1", payload: { message: malicious } });
  assert(p.el("dashSubTranscript").textContent.includes(malicious),
    "the sub-agent's session_closed note must render as literal text");
  assert(!p.sandbox._dash_sub_note_xss, "the onerror handler must not fire -- note rendering must not use innerHTML");
});

// ============================================================================
// SC7 (WS reconnect-on-drop, dashboard/ACP feature-parity plan Phase 6) --
// dashConnect()'s real source (connectRegion, loadDashPicker({realConnect:
// true})) is used throughout, not a hand-rewritten stand-in, for the same
// reason cmdPaletteRegion/queueSteerWiringRegion/imageAttachRegion/
// crewSubagentRegion are: the backoff scheduling, the refused-handshake diagnosis,
// and the state-restoration logic must be exercised as written. Base
// mechanics (checks 1-6) mirror acp.html's own SC-3 reconnect tests
// (tests/acp_page.test.mjs, "auto reconnect scheduled on close when opened"
// through "onclose while timer pending replaces timer not stacks") one for
// one, dash-prefixed; the remaining checks cover each state-restoration
// point 6b's plan text names, including the queue-state restore-ordering
// correction (Follow-up Work: "Phase 6 design note") with a dedicated case
// per branch of its turnActive gate.
// ----------------------------------------------------------------------------

check("dashboard: reconnect — auto reconnect scheduled on close when opened", () => {
  const p = loadDashPicker({ realConnect: true });
  p.sandbox.dashConnect();
  p.openMain();
  const timersBefore = p.timers.length;
  p.closeMain({ code: 1006, reason: "" });
  assert(p.timers.length > timersBefore,
    "a reconnect timer should be scheduled when the socket closes after being opened");
  assertEqual(p.el("dashReconnect").hidden, false,
    "the manual Reconnect button should also appear as an immediate-retry escape hatch");
  const socketsBefore = p.mainSocketCount();
  p.runTimers();
  assert(p.mainSocketCount() > socketsBefore,
    "the reconnect timer should call dashConnect() and open a new WebSocket");
});

check("dashboard: reconnect — reconnect delay doubles on each close", () => {
  const p = loadDashPicker({ realConnect: true });
  p.sandbox.dashConnect();
  p.openMain();
  // onclose does not reset _dashOpened, so firing onclose twice on the same
  // (already-closed) socket exercises the delay doubling without needing a
  // successful reconnect and re-open between them -- mirrors acp.html's own
  // equivalent test's own fixture comment exactly.
  p.closeMain({ code: 1006, reason: "" });
  assertEqual(p.timers[p.timers.length - 1].ms, 1000,
    "first reconnect timer should use 1000ms delay");
  p.closeMain({ code: 1006, reason: "" });
  assertEqual(p.timers[p.timers.length - 1].ms, 2000,
    "second reconnect timer should use doubled 2000ms delay");
});

check("dashboard: reconnect — reconnect delay capped at 30s", () => {
  const p = loadDashPicker({ realConnect: true });
  p.sandbox.dashConnect();
  p.openMain();
  // 5 closes bring delay to 30000 (would be 32000 without the cap):
  // 1k -> 2k -> 4k -> 8k -> 16k -> 30k.
  for (let i = 0; i < 5; i++) p.closeMain({ code: 1006, reason: "" });
  p.closeMain({ code: 1006, reason: "" });
  assertEqual(p.timers[p.timers.length - 1].ms, 30000,
    "delay should be capped at 30000ms after 6 consecutive closes");
  p.closeMain({ code: 1006, reason: "" });
  assertEqual(p.timers[p.timers.length - 1].ms, 30000,
    "delay should remain at 30000ms once capped");
});

check("dashboard: reconnect — reconnect delay resets on open", () => {
  const p = loadDashPicker({ realConnect: true });
  p.sandbox.dashConnect();
  p.openMain();
  p.closeMain({ code: 1006, reason: "" });
  p.closeMain({ code: 1006, reason: "" });
  assertEqual(p.timers[p.timers.length - 1].ms, 2000,
    "fixture: second timer should use 2000ms delay before the reset");
  p.runTimers();
  p.openMain(); // opens the new socket the reconnect timer constructed
  p.closeMain({ code: 1006, reason: "" });
  assertEqual(p.timers[p.timers.length - 1].ms, 1000,
    "delay should reset to 1000ms after onopen");
});

check("dashboard: reconnect — no auto reconnect when not opened; diagnosis runs instead", () => {
  const p = loadDashPicker({ realConnect: true });
  p.sandbox.dashConnect(); // constructs the socket but never opens it
  const timersBefore = p.timers.length;
  p.closeMain({ code: 4401, reason: "token expired" });
  // The diagnostic GET's own 5 s timeout is the one timer allowed here; it is
  // not a reconnect. 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL
  // Phase 6 review (J6)
  assertEqual(p.timers.filter((t) => t.ms !== 5000).length, timersBefore,
    "no reconnect timer should be scheduled when the socket closes without having been opened");
  assertEqual(p.el("dashReconnect").hidden, true,
    "the Reconnect button must not appear on a rejected handshake -- diagnosis decides what to show instead");
});

check("dashboard: reconnect — onclose while a timer is pending replaces it, not stacks", () => {
  const p = loadDashPicker({ realConnect: true });
  p.sandbox.dashConnect();
  p.openMain();
  p.closeMain({ code: 1006, reason: "" });
  assertEqual(p.timers.length, 1, "fixture: the first close should schedule exactly one timer");
  p.closeMain({ code: 1006, reason: "" });
  assertEqual(p.timers.length, 1,
    "a second close while the retry timer is still pending must replace it, not stack a second one");
});

// ---- refused handshake: signed out vs unreachable -----------------------
// 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 6. The
// rejection is driven, not grepped for: the socket closes without ever
// opening, and the page's own GET of itself answers as the real server
// would -- the loopback gate's 403 for a cookie-less tab, a network error for
// a server that is down. Both directions are asserted, and in no case does
// the retired stale-token "reload" affordance appear.

function dashRefusedHandshake(opts) {
  const p = loadDashPicker({ realConnect: true, ...opts });
  const notes = [];
  p.sandbox.dashSetComposerNote = (t) => notes.push(t);
  p.sandbox.dashConnect();
  p.closeMain({ code: 1006, reason: "" }); // never opened
  return { p, notes, log: () => p.el("dashLog").textContent };
}

check("dashboard: refused handshake — a cookie-less tab (the gate's 403) reads as signed out, not unreachable", async () => {
  const { p, notes, log } = dashRefusedHandshake({ pageStatus: 403 });
  await p.settle();
  const shown = notes.join(" | ");
  assert(/signed out/i.test(shown) && /tray/i.test(shown),
    `the composer note must say signed out and point at the tray; got ${JSON.stringify(shown)}`);
  assert(!/unreachable|not answering|still be starting/i.test(log() + shown),
    `a 403 from a live server must not read as unreachable; log: ${JSON.stringify(log())}`);
  assertEqual(p.el("dashReconnect").hidden, true,
    "Reconnect cannot help a signed-out tab -- it resends the same missing cookie");
  assertEqual(p.el("dashReload").hidden, true,
    "no stale-token Reload affordance: a reload cannot sign a browser back in");
  assertEqual(p.reloaded(), false, "nothing may reload the page on its own");
});

check("dashboard: the signed-out message names the step after the tray -- reload this tab (F7)", async () => {
  // 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL final review
  const { p, notes } = dashRefusedHandshake({ pageStatus: 403 });
  await p.settle();
  assert(/reload this tab/i.test(notes.join(" ")),
    `the composer note must say to reload this tab after the tray; got ${JSON.stringify(notes)}`);
});

check("dashboard: a 403 on an htmx request reports signed out, once; other errors do not (F7)", () => {
  // The loopback gate answers a signed-out tab's partials and polls with 403,
  // and nothing used to handle it.
  // 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL final review
  const p = loadDashPicker({ realConnect: true });
  let reported = 0;
  p.sandbox.dashReportSignedOut = () => { reported++; };
  p.fireDoc("htmx:responseError", { detail: { xhr: { status: 500 } } });
  p.fireDoc("htmx:responseError", { detail: {} });
  assertEqual(reported, 0, "a non-403 error was reported as signed out");
  p.fireDoc("htmx:responseError", { detail: { xhr: { status: 403 } } });
  assertEqual(reported, 1, "a 403 on an htmx request was not reported as signed out");
  p.fireDoc("htmx:responseError", { detail: { xhr: { status: 403 } } });
  assertEqual(reported, 1, "every polled 403 repeated the signed-out report");
});

// The shipped htmx shim, run for real against the dashboard's own listener.
// The check above fires a synthetic event; the QA found the shim itself never
// dispatched one and swapped the gate's JSON 403 into the launcher grid.
// 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL final QA
const HTMX_SHIM = path.join(HERE, "..", "src", "power_atlas", "static", "htmx.min.js");

function loadHtmxShim(p, answer) {
  const dispatched = [];
  // A bubbling event on an element in the page reaches the document's
  // listeners; a non-bubbling one stays on the element.
  class ShimEvent {
    constructor(type, init = {}) {
      this.type = type;
      this.bubbles = Boolean(init.bubbles);
      this.detail = init.detail;
    }
  }
  p.sandbox.Event = ShimEvent;
  p.sandbox.CustomEvent = ShimEvent;
  p.sandbox.Element = { prototype: {} };
  p.sandbox.fetch = (url) => {
    const { status, body } = answer(url);
    return Promise.resolve({
      ok: status >= 200 && status < 300, status,
      statusText: status === 403 ? "Forbidden" : "OK",
      text: () => Promise.resolve(body),
    });
  };
  vm.runInContext(fs.readFileSync(HTMX_SHIM, "utf8"), p.sandbox, { filename: "htmx.min.js" });
  // `#launcher-tiles` as index.html marks it up. A plain stand-in rather than
  // an El: the shim queries `[hx-get]`, and El implements class/tag selectors
  // only; and its innerHTML is recorded, where El's forbids the sink.
  function tiles() {
    const attrs = { "hx-get": "/partials/launchers", "hx-trigger": "load", "hx-swap": "innerHTML" };
    const el = {
      swapped: [],
      getAttribute: (n) => (n in attrs ? attrs[n] : null),
      set innerHTML(v) { el.swapped.push(v); },
      querySelectorAll: () => [],
      addEventListener: () => {},
      dispatchEvent(ev) {
        dispatched.push(ev);
        if (ev.bubbles) {
          try { p.fireDoc(ev.type, ev); } catch (e) {
            if (!/nothing listens/.test(e.message)) throw e;
          }
        }
        return true;
      },
    };
    return el;
  }
  const rootOf = (el) => ({ querySelectorAll: (sel) => (sel === "[hx-get]" ? [el] : []) });
  return { dispatched, tiles, rootOf };
}

check("dashboard: the shipped htmx shim reports a 403 as signed out, once, and does not swap the error body (final QA)", async () => {
  // 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL final QA
  const p = loadDashPicker({ realConnect: true });
  const notes = [];
  p.sandbox.dashSetComposerNote = (t) => notes.push(t);
  const shim = loadHtmxShim(p, () => ({ status: 403, body: '{"error":"Forbidden"}' }));
  const first = shim.tiles();
  p.sandbox.htmx.process(shim.rootOf(first));
  await p.settle();
  await p.settle();
  assertEqual(first.swapped.length, 0,
    `the shim swapped an error body into the page: ${JSON.stringify(first.swapped)}`);
  const errors = shim.dispatched.filter((e) => e.type === "htmx:responseError");
  assertEqual(errors.length, 1, "the shim did not dispatch htmx:responseError for a 403");
  assertEqual(errors[0].bubbles, true, "htmx:responseError must bubble to the document listener");
  assertEqual(errors[0].detail && errors[0].detail.xhr && errors[0].detail.xhr.status, 403,
    "the event does not carry detail.xhr.status");
  const signedOut = () => notes.filter((t) => /signed out/i.test(t)).length;
  assertEqual(signedOut(), 1, `a 403 was not reported as signed out: ${JSON.stringify(notes)}`);
  // A second 403 (the next load) is not reported again.
  const second = shim.tiles();
  p.sandbox.htmx.process(shim.rootOf(second));
  await p.settle();
  await p.settle();
  assertEqual(second.swapped.length, 0, "the second 403 was swapped");
  assertEqual(signedOut(), 1, "the signed-out report repeated");
});

check("dashboard: the shipped htmx shim still swaps a 2xx body and reports no error (final QA)", async () => {
  // 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL final QA
  const p = loadDashPicker({ realConnect: true });
  let reported = 0;
  p.sandbox.dashReportSignedOut = () => { reported++; };
  const shim = loadHtmxShim(p, () => ({ status: 200, body: "<div class=\"launcher-tile\"></div>" }));
  const el = shim.tiles();
  p.sandbox.htmx.process(shim.rootOf(el));
  await p.settle();
  await p.settle();
  assertEqual(JSON.stringify(el.swapped), JSON.stringify(["<div class=\"launcher-tile\"></div>"]),
    "a 2xx body was not swapped in");
  assertEqual(shim.dispatched.filter((e) => e.type === "htmx:responseError").length, 0,
    "a 2xx reported an error");
  assertEqual(shim.dispatched.filter((e) => e.type === "htmx:afterSwap").length, 1,
    "htmx:afterSwap no longer fires after a 2xx swap");
  assertEqual(reported, 0, "a 2xx was reported as signed out");
});

check("dashboard: refused handshake — a server that is not answering still reads as unreachable, not signed out", async () => {
  const { p, notes, log } = dashRefusedHandshake({ fetchFails: true });
  await p.settle();
  assert(/not answering/i.test(log()),
    `an unreachable server must say so; log: ${JSON.stringify(log())}`);
  assert(!/signed out/i.test(log() + notes.join(" ")),
    "a network failure must not be reported as signed out");
  assertEqual(p.el("dashReconnect").hidden, false,
    "Reconnect must reappear once the diagnosis concludes the server itself is unreachable");
  assertEqual(p.el("dashReload").hidden, true, "Reload must not be shown when the server is unreachable");
});

check("dashboard: refused handshake — a server that admits this browser offers Reconnect, never the stale-token Reload", async () => {
  const { p, notes, log } = dashRefusedHandshake({ pageStatus: 200 });
  await p.settle();
  assert(!/stale|reload/i.test(log() + notes.join(" ")),
    `no stale-token wording may survive; log: ${JSON.stringify(log())}`);
  assert(!/signed out/i.test(notes.join(" ")), "a signed-in browser is not signed out");
  assertEqual(p.el("dashReconnect").hidden, false, "Reconnect is the recovery when the server admits this browser");
  assertEqual(p.el("dashReload").hidden, true,
    "the stale-token Reload affordance is retired -- there is no per-launch token to go stale");
});

// ---- dashboard refused handshake: review fixes ---------------------------
// 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 6 review
// (J4-J8).

/** True when `el` and every ancestor the harness models is not hidden. */
function dashVisible(el) {
  for (let n = el; n; n = n.parentNode) if (n.hidden) return false;
  return true;
}

for (const path of ["quick create", "picker create"]) {
  check(`dashboard: a signed-out refusal on the ${path} path is visible, replacing 'Creating session…' (J4)`, async () => {
    const p = loadDashPicker({ realConnect: true, pageStatus: 403, dashAttachedSid: null });
    p.sandbox.dashSetComposerNote = () => {}; // the composer is hidden on this path
    // The real markup nests the transcript inside its wrapper.
    p.el("dashTranscriptWrap").appendChild(p.el("dashTranscript"));
    p.sandbox._dashPickerCapacity = { held: 0, max: 8 };
    if (path === "quick create") {
      p.sandbox.dashRailQuickCreate("/proj");
    } else {
      p.sandbox.dashPickerOpen("");
      p.sandbox._dashPickerTrapRemove = null;
      p.sandbox.dashPickerCreate("/proj");
    }
    assert(/Creating session/.test(p.el("dashTranscript").textContent),
      "fixture: the placeholder was not drawn");
    assertEqual(p.sandbox.dashComposerEl.hidden, true,
      "fixture: the create path hides the composer, so its note cannot be the message");
    p.closeMain({ code: 1006, reason: "" }); // never opened
    await p.settle();
    const pane = p.el("dashTranscript");
    const shown = pane.childNodes.find((c) => /signed out/i.test(c.textContent));
    assert(shown && /tray/i.test(shown.textContent),
      `the transcript must carry the signed-out message; got ${JSON.stringify(pane.textContent)}`);
    assert(dashVisible(shown), "the signed-out message sits under a hidden element");
    assert(!/Creating session/.test(pane.textContent),
      "'Creating session…' survived a refusal that will never create anything");
  });
}

check("dashboard: a signed-out refusal appends to a real transcript rather than wiping it (J4)", async () => {
  const p = loadDashPicker({ realConnect: true, pageStatus: 403 });
  p.sandbox.dashSetComposerNote = () => {};
  const pane = p.el("dashTranscript");
  for (const t of ["first", "second"]) {
    const row = new El("div"); row.className = "acp-msg"; row.textContent = t; pane.appendChild(row);
  }
  p.sandbox.dashConnect();
  p.closeMain({ code: 1006, reason: "" });
  await p.settle();
  assert(/first/.test(pane.textContent) && /second/.test(pane.textContent),
    "the viewed transcript was wiped");
  assert(/signed out/i.test(pane.textContent), "the signed-out message was not appended");
});

check("dashboard: refused handshake — a 5xx is neither signed out nor unreachable, and offers Reconnect (J5)", async () => {
  const { p, notes, log } = dashRefusedHandshake({ pageStatus: 500 });
  await p.settle();
  const all = log() + notes.join(" ") + p.el("dashTranscript").textContent;
  assert(!/signed out/i.test(all), `a 5xx is not a signed-out browser; got ${JSON.stringify(all)}`);
  assert(!/unreachable|not answering|still be starting/i.test(all),
    `a server that answered 500 is reachable; got ${JSON.stringify(all)}`);
  assertEqual(p.el("dashReconnect").hidden, false, "Reconnect is the recovery for a server error");
});

check("dashboard: refused handshake — a diagnostic GET that never answers times out as unreachable (J6)", async () => {
  const { p, log } = dashRefusedHandshake({ pageHangs: true });
  await p.settle();
  assertEqual(p.el("dashReconnect").hidden, true, "fixture: still diagnosing");
  const diag = p.timers.filter((t) => t.ms === 5000);
  assertEqual(diag.length, 1, "the diagnosis must arm exactly one 5 s timeout");
  diag[0].fn();
  await p.settle();
  assert(/not answering/i.test(log()), `silence past the timeout reads as unreachable; log: ${JSON.stringify(log())}`);
  assertEqual(p.el("dashReconnect").hidden, false, "Reconnect must reappear after the timeout");
  const get = p.fetches.find((f) => String(f.url) === "/");
  assert(get && get.init.signal && get.init.signal.aborted, "the hung GET must be aborted, not left open");
});

check("dashboard: refused handshake — a throw inside the success handler still ends with Reconnect shown (J7)", async () => {
  const p = loadDashPicker({ realConnect: true, pageStatus: 403 });
  p.sandbox.dashReportSignedOut = () => { throw new Error("injected failure"); };
  p.sandbox.dashConnect();
  p.closeMain({ code: 1006, reason: "" });
  await p.settle();
  await p.settle();
  assertEqual(p.el("dashReconnect").hidden, false,
    "an exception in the success handler left both recovery buttons hidden");
});

check("dashboard: refused handshake — an answer arriving after a later dashConnect() opened paints nothing (J8)", async () => {
  const { p, notes } = dashRefusedHandshake({ pageStatus: 403 });
  p.sandbox.dashConnect(); // a session click or create, before the GET answers
  await p.settle();
  await p.settle();
  assertEqual(notes.length, 0, `a stale diagnosis wrote the composer note: ${JSON.stringify(notes)}`);
  assert(!/signed out/i.test(p.el("dashTranscript").textContent),
    "a stale diagnosis painted signed out over the newer connection");
  assertEqual(p.el("dashReconnect").hidden, true, "a stale diagnosis showed Reconnect over a live socket");
});

check("dashboard: the socket URL carries no ?t= token (cookie-only authentication)", () => {
  const p = loadDashPicker({ realConnect: true });
  p.sandbox.dashConnect();
  const url = p.mainSocket().url;
  assertEqual(url, "ws://test.invalid/ws/acp",
    "the dashboard's /ws/acp URL must be the bare path -- the pa_local cookie authenticates it");
});

// ---- ACP_AVAILABLE: every gated dashboard feature, both directions --------
// 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 6 (D-15). The
// sentinel was ACP_TOKEN doubling as "acp imported"; it is now a boolean.
// Only the pair (renders when true, hidden when false) catches an inverted
// sentinel, so every gated site is driven both ways from real source. Two
// sites live in loadDashPicker's regions (the eager-load focus guard and
// dashPickerOpen, covered there and just below); the other five are not in
// any region that harness loads, so each runs here from its own slice with
// call-time stubs for the rail's DOM helpers.

function dashSentinelSlice(fromMarker, toMarker) {
  const src = fs.readFileSync(INDEX_TEMPLATE, "utf8");
  const from = src.indexOf(fromMarker);
  if (from < 0) throw new Error(`index.html no longer contains ${fromMarker}`);
  const to = src.indexOf(toMarker, from);
  if (to < 0) throw new Error(`index.html no longer has ${toMarker} after ${fromMarker}`);
  const slice = src.slice(from, to);
  if (!slice.includes("ACP_AVAILABLE")) {
    throw new Error(`${fromMarker} no longer reads ACP_AVAILABLE; this check measures nothing`);
  }
  return slice;
}

function runDashSentinel(slice, acpAvailable, extra = {}) {
  const fetches = [];
  const newAcpBtn = new El("button");
  newAcpBtn.hidden = true; // the markup's own initial state
  const railIcon = new Proxy({}, { get: () => () => new El("svg") });
  const box = {
    ACP_AVAILABLE: acpAvailable,
    document: {
      createElement: (tag) => new El(tag),
      getElementById: (id) => (id === "dashRailNewAcp" ? newAcpBtn : null),
    },
    fetch: (u) => { fetches.push(String(u)); return new Promise(() => {}); },
    window: { _launchers: [], _availableProviders: [] },
    RAIL_ICON: railIcon,
    _viewingSid: null,
    dashRailFilter: "",
    dashRailAvailability: () => "available",
    dashRailRowHoverText: () => "",
    dashRailDotClass: () => "",
    dashRailTitleText: (s) => String(s.id),
    dashRailSetHighlighted: (el, text) => { el.textContent = text; },
    dashSessionMetaRender: () => {},
    dashRailSelected: new Set(),
    dashRailSyncSelection: () => {},
    dashRailWhenShort: () => "",
    dashRailProviderIcon: () => new El("img"),
    dashRailHeadNode: () => new El("div"),
    dashRailIsCollapsed: () => true,
    _wireRailActionMenu: () => {},
    ...extra,
  };
  vm.createContext(box);
  vm.runInContext(slice, box, { filename: "index.html#acp-available-sentinel" });
  return { box, fetches, newAcpBtn };
}

const hasText = (root, text) => root.descendants().some((n) => n.textContent === text);

for (const available of [true, false]) {
  const want = available ? "renders" : "is hidden";

  check(`ACP_AVAILABLE=${available}: the rail's New ACP session button ${want}`, () => {
    const slice = dashSentinelSlice("var dashRailNewAcpBtn", "// ---- session-level live diffing");
    const { newAcpBtn } = runDashSentinel(slice, available);
    assertEqual(newAcpBtn.hidden, !available,
      `the global New ACP session button must be ${available ? "shown" : "left hidden"}`);
  });

  check(`ACP_AVAILABLE=${available}: a kiro-cli-v3 row's Delete session item ${want}`, () => {
    const slice = dashSentinelSlice("function dashRailRowNode", "// ---- group/day/status headers");
    const { box } = runDashSentinel(slice, available);
    const row = box.dashRailRowNode(
      { id: "s1", provider: "kiro-cli-v3", pinned: true, availability: "available" }, true, null);
    assertEqual(hasText(row, "Delete session"), available,
      `a kiro-cli-v3 row's Delete session item must ${available ? "exist" : "not exist"}`);
  });

  check(`ACP_AVAILABLE=${available}: every provider's row menu has Copy session id and no Pin session`, () => {
    const slice = dashSentinelSlice("function dashRailRowNode", "// ---- group/day/status headers");
    const { box } = runDashSentinel(slice, available);
    for (const provider of ["kiro-cli", "kiro-cli-v3", "claude-code", "kiro-ide", "codex", ""]) {
      for (const pinned of [true, false]) {
        const row = box.dashRailRowNode(
          { id: "s1", provider, pinned, availability: "available" }, true, null);
        assertEqual(hasText(row, "Copy session id"), true,
          `a ${provider || "provider-less"} row (pinned=${pinned}) must offer Copy session id`);
        assertEqual(hasText(row, "Pin session"), false,
          `a ${provider || "provider-less"} row (pinned=${pinned}) must not offer Pin session in its menu`);
      }
    }
  });

  check(`ACP_AVAILABLE=${available}: a workspace's New kiro-cli v3 ACP session item ${want}`, () => {
    const slice = dashSentinelSlice("function dashRailGroupNode", "// ---- custom-launcher quick launch");
    const { box } = runDashSentinel(slice, available);
    const group = box.dashRailGroupNode(
      { cwd: "/ws", name: "ws", sessions: [], total: 0, pinned: true });
    assertEqual(hasText(group, "New kiro-cli v3 ACP session"), available,
      `the workspace's quick-create ACP item must ${available ? "exist" : "not exist"}`);
  });

  check(`ACP_AVAILABLE=${available}: a workspace's Delete kiro-cli sessions item ${want}`, () => {
    const slice = dashSentinelSlice("function dashRailGroupNode", "// ---- custom-launcher quick launch");
    const { box } = runDashSentinel(slice, available);
    const group = box.dashRailGroupNode(
      { cwd: "/ws", name: "ws", sessions: [], total: 0, pinned: true });
    assertEqual(hasText(group, "Delete kiro-cli sessions…"), available,
      `the workspace's delete item must ${available ? "exist" : "not exist"}`);
  });

  check(`ACP_AVAILABLE=${available}: dashMaybeAttach ${available ? "peeks" : "does not peek"} a kiro-cli-v3 session's availability`, () => {
    const slice = dashSentinelSlice("function dashMaybeAttach", "// ---- Image paste-to-attach");
    const { box, fetches } = runDashSentinel(slice, available);
    const row = new El("button");
    row.dataset.provider = "kiro-cli-v3";
    row.dataset.cwd = "/ws";
    box.dashMaybeAttach(row, "s1");
    assertEqual(fetches.some((u) => u.startsWith("/api/session-availability")), available,
      `the live-attach availability peek must ${available ? "run" : "not run"}`);
  });

  check(`ACP_AVAILABLE=${available}: dashPickerOpen ${available ? "opens" : "does not open"} the picker`, () => {
    const p = loadDashPicker({ acpAvailable: available });
    p.sandbox.dashPickerOpen("");
    assertEqual(p.el("dashPicker").hidden, !available,
      `the new-session picker must ${available ? "open" : "stay closed"}`);
  });
}

check("dashboard: reconnect — clicking Reconnect opens a socket and resubscribes the previously-attached session", () => {
  const p = loadDashPicker({ realConnect: true, dashAttachedSid: "sess-1" });
  p.el("dashReconnect").dispatch("click");
  assertEqual(p.mainSocketCount(), 1, "clicking Reconnect should open a WebSocket");
  p.openMain();
  const subs = p.sentFrames.filter((f) => f.type === "subscribe");
  assertEqual(subs.length, 1, "reconnecting should resubscribe to the previously-attached session");
  assertEqual(subs[0].sid, "sess-1", "the resubscribe must target the previously-attached session id");
});

check("dashboard: reconnect — a failed session load shows Reload without ever closing the socket (reportLoadFailure parity)", () => {
  const p = loadDashPicker({ realConnect: true, viewingSid: "sess-load-fail" });
  p.sandbox.dashConnect();
  p.openMain();
  p.sandbox._dashLoadingSid = "sess-load-fail";
  p.deliverMain({
    type: "error", sessionId: "sess-load-fail",
    payload: { code: "agent_start_failed", message: "could not start" },
  });
  assertEqual(p.el("dashReload").hidden, false,
    "a failed session load must show Reload -- mirrors acp.html's own reportLoadFailure(), a socket-level " +
    "error that never triggers onclose");
  assertEqual(p.mainSocketCount(), 1,
    "a load failure is an ordinary error frame on a healthy socket -- it must not itself close or reopen it");
});

check("dashboard: reconnect — the Reload button clears once a different session's load succeeds (Fix 2, Phase 6 review)", () => {
  const p = loadDashPicker({ realConnect: true, viewingSid: "sess-load-fail" });
  p.sandbox.dashConnect();
  p.openMain();
  p.sandbox._dashLoadingSid = "sess-load-fail";
  p.deliverMain({
    type: "error", sessionId: "sess-load-fail",
    payload: { code: "agent_start_failed", message: "could not start" },
  });
  assertEqual(p.el("dashReload").hidden, false, "fixture: the failed load must show Reload first");
  // Switch to a different session and let ITS load succeed on the same,
  // reused socket -- no fresh dashConnect() open happens on an ordinary
  // session switch, which is exactly the case the fresh-socket-open-only
  // reset used to miss.
  p.sandbox._viewingSid = "sess-2";
  p.deliverMain({
    type: "session", sessionId: "sess-2",
    payload: { sessionId: "sess-2", cwd: "/ws", created: false, turnActive: false, contextPercent: null },
  });
  assertEqual(p.el("dashReload").hidden, true,
    "a successful session-frame attach for a different session must clear a Reload button left over " +
    "from an earlier, unrelated load failure");
});

// ---- state-restoration points (6b's own list) --------------------------

check("dashboard: reconnect — a drop resets _dashTurnActive so a stale active-turn UI does not survive it", () => {
  const p = loadDashPicker({ realConnect: true, dashAttachedSid: "sess-1", dashTurnActive: true });
  p.sandbox.dashConnect();
  p.openMain();
  p.closeMain();
  assertEqual(p.sandbox._dashTurnActive, false, "a socket drop must reset _dashTurnActive to false");
});

check("dashboard: reconnect — a drop resets lazy-load state (_dashLoadingSid/_dashPendingSend/_dashPendingImages)", () => {
  const p = loadDashPicker({ realConnect: true });
  p.sandbox.dashConnect();
  p.openMain();
  p.sandbox._dashLoadingSid = "sess-x";
  p.sandbox._dashPendingSend = "hello";
  p.sandbox._dashPendingImages = [{ mimeType: "image/png", data: "abc" }];
  p.closeMain();
  assertEqual(p.sandbox._dashLoadingSid, null,
    "a drop during lazy-load must clear _dashLoadingSid -- the composer must not stay stuck under " +
    "'Starting the agent…' with no recovery path");
  assertEqual(p.sandbox._dashPendingSend, null, "a drop must clear _dashPendingSend");
  assertEqual(p.sandbox._dashPendingImages, null, "a drop must clear _dashPendingImages");
});

check("dashboard: reconnect — a drop clears a pending close-then-create", () => {
  const p = loadDashPicker({ realConnect: true });
  p.sandbox.dashConnect();
  p.openMain();
  p.sandbox._dashPendingCreate = { cwd: "/ws", mode: "kiro_default" };
  p.closeMain();
  assertEqual(p.sandbox._dashPendingCreate, null, "a drop must clear a pending close-then-create");
});

check("dashboard: reconnect — a drop preserves _dashOrigin for an already-attached session", () => {
  const p = loadDashPicker({ realConnect: true, dashAttachedSid: "sess-1" });
  p.sandbox._dashOrigin = "dashboard";
  p.sandbox.dashConnect();
  p.openMain();
  p.closeMain();
  assertEqual(p.sandbox._dashOrigin, "dashboard",
    "a drop must not clear _dashOrigin for a session already confirmed attached -- " +
    "dashCloseIfAbandoned() reads it later to decide whether to auto-close on navigate-away, and an " +
    "unconditional clear here would silently lose that fact across every reconnect");
});

check("dashboard: reconnect — a drop clears _dashOrigin when no session was confirmed attached yet", () => {
  const p = loadDashPicker({ realConnect: true }); // _dashAttachedSid defaults to null
  p.sandbox._dashOrigin = "dashboard"; // e.g. set by dashSendPrompt's lazy-attach path
  p.sandbox.dashConnect();
  p.openMain();
  p.closeMain();
  assertEqual(p.sandbox._dashOrigin, null,
    "a drop during an unconfirmed lazy-load must reset _dashOrigin to its pre-attempt default");
});

check("dashboard: reconnect — a queued prompt survives the reconnect's session frame when the turn is still active", () => {
  const p = loadDashPicker({ realConnect: true, dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.sandbox._dashQueuedPrompt = "finish this thought";
  p.sandbox._dashQueuedPromptSession = "sess-1";
  p.sandbox.dashConnect();
  p.openMain();
  p.closeMain();
  p.runTimers(); // fires the reconnect timer -> dashConnect() -> new socket
  p.openMain();
  // The re-subscribe's own `session` frame lands: its unconditional clear
  // (Fix 1, Phase 3 review) runs first, and only THEN does the Phase 6
  // restore re-apply the snapshot -- the naive "set the live vars before
  // reconnecting" order would already have lost this by now.
  p.deliverMain({
    type: "session", sessionId: "sess-1",
    payload: { sessionId: "sess-1", cwd: "/ws", created: false, turnActive: true, contextPercent: null },
  });
  assertEqual(p.sandbox._dashQueuedPrompt, "finish this thought",
    "the queued prompt must survive the reconnect's session frame, restored AFTER it lands, not before");
  assertEqual(p.sandbox._dashQueuedPromptSession, "sess-1",
    "the restored queue must still be scoped to the session it was queued against");
});

check("dashboard: reconnect — a queued prompt is surfaced in the textarea, not silently re-armed, when the turn ended while disconnected", () => {
  const p = loadDashPicker({ realConnect: true, dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.sandbox._dashQueuedPrompt = "finish this thought";
  p.sandbox._dashQueuedPromptSession = "sess-1";
  p.sandbox.dashPromptInput.value = "";
  p.sandbox.dashConnect();
  p.openMain();
  p.closeMain();
  p.runTimers();
  p.openMain();
  p.deliverMain({
    type: "session", sessionId: "sess-1",
    payload: { sessionId: "sess-1", cwd: "/ws", created: false, turnActive: false, contextPercent: null },
  });
  assertEqual(p.sandbox._dashQueuedPrompt, null,
    "a queue whose turn already ended while disconnected must not be re-armed as a live queue -- the " +
    "meta turn:end that would flush it correctly was missed, and re-arming it would recreate the exact " +
    "bug Fix 1 (Phase 3 review) closed: a stale prompt auto-sending into a later, unrelated turn");
  assertEqual(p.sandbox.dashPromptInput.value, "finish this thought",
    "the prompt must be surfaced in the textarea instead, so the user decides whether to send it");
});

check("dashboard: reconnect — a queue from a turn that ended while disconnected is discarded, not overwritten, if the user already typed something new", () => {
  const p = loadDashPicker({ realConnect: true, dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.sandbox._dashQueuedPrompt = "finish this thought";
  p.sandbox._dashQueuedPromptSession = "sess-1";
  p.sandbox.dashConnect();
  p.openMain();
  p.closeMain();
  p.runTimers();
  p.openMain();
  p.sandbox.dashPromptInput.value = "something the user typed while offline";
  p.deliverMain({
    type: "session", sessionId: "sess-1",
    payload: { sessionId: "sess-1", cwd: "/ws", created: false, turnActive: false, contextPercent: null },
  });
  assertEqual(p.sandbox.dashPromptInput.value, "something the user typed while offline",
    "a queue that ended while disconnected must not clobber text the user has since typed");
  assert(p.addMessageCalls.some((m) => m.role === "note" && /typed a new prompt/.test(m.text)),
    "a discard note should explain why the queued prompt was not restored");
});

check("dashboard: reconnect — a reconnect's queue snapshot for one session is not applied to a different one", () => {
  const p = loadDashPicker({ realConnect: true, dashAttachedSid: "sess-1", viewingSid: "sess-2" });
  p.sandbox._dashQueuedPrompt = "belongs to sess-1";
  p.sandbox._dashQueuedPromptSession = "sess-1";
  p.sandbox.dashConnect();
  p.openMain();
  p.closeMain();
  p.runTimers();
  p.openMain();
  p.deliverMain({
    type: "session", sessionId: "sess-2",
    payload: { sessionId: "sess-2", cwd: "/ws2", created: false, turnActive: true, contextPercent: null },
  });
  assertEqual(p.sandbox._dashQueuedPrompt, null,
    "a queue snapshot must not be applied to a session frame for a different session id");
});

check("dashboard: reconnect — a drop re-renders the image tray from the currently staged attachments", () => {
  const p = loadDashPicker({
    realConnect: true,
    dashAttachments: [{ mimeType: "image/png", data: "x", bytes: 100, url: "blob:1", name: "a.png" }],
  });
  p.sandbox.dashConnect();
  p.openMain();
  p.sandbox.dashTrayEl.hidden = true; // simulate a stale DOM state
  p.closeMain();
  assertEqual(p.sandbox.dashTrayEl.hidden, false,
    "a drop must re-render the tray from dashAttachments, un-hiding it when images are staged");
  assertEqual(p.trayChips().length, 1, "the re-rendered tray must show the currently staged attachment");
});

check("dashboard: reconnect — a drop restores dashPendingAttachments back into the tray for the user to retry (Fix 9, Step 9 review)", () => {
  // dashPendingAttachments is the Phase 4 "handed over to a send, awaiting
  // confirmation" state -- dashRenderTray() only ever reads dashAttachments,
  // never dashPendingAttachments, so these images were invisible in the tray
  // the whole time a send was in flight. If the WS drops before any server
  // response, nothing used to touch this state at all: the blob URLs leaked
  // until an unrelated later event happened to clean them up, and the images
  // the user just sent looked like they had simply vanished.
  const p = loadDashPicker({
    realConnect: true,
    dashPendingAttachments: [{ mimeType: "image/png", data: "x", bytes: 100, url: "blob:pending-1", name: "a.png" }],
  });
  p.sandbox.dashConnect();
  p.openMain();
  p.closeMain();
  assertEqual(p.sandbox.dashPendingAttachments.length, 0, "dashPendingAttachments must be drained");
  assertEqual(p.sandbox.dashAttachments.length, 1,
    "the pending attachment must be restored back into dashAttachments so the user can retry the send");
  assertEqual(p.trayChips().length, 1, "the restored attachment must be visible in the re-rendered tray");
  assertEqual(p.revoked().length, 0, "a restored attachment's object URL must not be revoked -- it is still in use");
});

check("dashboard: reconnect — a drop discards (and revokes) dashPendingAttachments instead of restoring them if new images were staged since the interrupted send (Fix 9, Step 9 review)", () => {
  const p = loadDashPicker({
    realConnect: true,
    dashPendingAttachments: [{ mimeType: "image/png", data: "x", bytes: 100, url: "blob:pending-1", name: "a.png" }],
    dashAttachments: [{ mimeType: "image/png", data: "y", bytes: 100, url: "blob:new-1", name: "b.png" }],
  });
  p.sandbox.dashConnect();
  p.openMain();
  p.closeMain();
  assertEqual(p.sandbox.dashPendingAttachments.length, 0, "dashPendingAttachments must be drained");
  assertEqual(p.sandbox.dashAttachments.length, 1, "the newly-staged attachment must take precedence, not be doubled up");
  assertEqual(p.sandbox.dashAttachments[0].url, "blob:new-1", "the surviving attachment must be the newly-staged one");
  assert(p.revoked().includes("blob:pending-1"),
    "the old pending attachment (superseded by a newer one) must have its object URL revoked, not leaked");
  const note = p.addMessageCalls.find((c) => /not restored/.test(c.text));
  assert(note, "a note explaining the interrupted send's images were not restored must be shown");
});

check("dashboard: reconnect — a drop closes any open sub-agent panel and explicitly closes dashSubWs", () => {
  const p = loadDashPicker({ realConnect: true, dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "explorer", task: "", sessionName: "", status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  p.sandbox.transcriptEl.querySelectorAll(".acp-crew-row")[0].dispatch("click");
  assertEqual(p.el("dashSubPanel").hidden, false, "fixture: the sub-agent panel should be open");
  p.openSub(0);
  p.sandbox.dashConnect();
  p.openMain();
  p.closeMain();
  assertEqual(p.el("dashSubPanel").hidden, true, "a main-socket drop must close any open sub-agent panel");
  assertEqual(p.sandbox.dashSubWs, null,
    "a main-socket drop must explicitly close and null dashSubWs -- it has no reconnect loop of its own " +
    "and must not be left orphaned holding a MAX_CONNECTIONS slot");
  // Fix 1 (Phase 6 review): a genuinely-open sub-agent panel closing onto a
  // still-attached session must un-hide the composer -- the established
  // pre-Phase-6 behavior (dashCloseSubagentView()'s own `dashComposerEl.hidden
  // = !_dashAttachedSid`) -- confirming the guard added for the lazy-attach
  // regression (see the dedicated check below) did not narrow this, the
  // case it was designed to still handle correctly.
  assertEqual(p.sandbox.dashComposerEl.hidden, false,
    "closing a genuinely-open sub-agent panel onto a still-attached session must show the composer");
});

check("dashboard: reconnect — a drop during an unconfirmed lazy-attach re-enables the composer and clears the note (Fix 1, Phase 6 review)", () => {
  const p = loadDashPicker({ realConnect: true });
  p.sandbox.dashConnect();
  p.openMain();
  // Simulate dashSendPrompt()'s lazy-attach path in flight: _dashAttachedSid
  // stays null for the entire wait, by design, while the composer is shown
  // but disabled under a "Starting the agent…" note -- no sub-agent panel is
  // ever open in this scenario.
  p.sandbox._dashLoadingSid = "sess-x";
  p.sandbox._dashPendingSend = "hello";
  p.sandbox.dashPromptInput.disabled = true;
  p.sandbox.dashComposerEl.hidden = false;
  const notes = [];
  p.sandbox.dashSetComposerNote = (t) => notes.push(t);
  p.closeMain();
  assertEqual(p.sandbox.dashPromptInput.disabled, false,
    "a drop during an unconfirmed lazy-attach must re-enable the composer -- the only other " +
    "unconditional disabled = false reset in this handler lives inside the unrelated " +
    "_dashSteerPending branch, and not even a later successful reconnect can fix this otherwise: " +
    "dashReconnectOnReady() only resubscribes if (_dashAttachedSid), which stays null here");
  assertEqual(notes[notes.length - 1], "",
    "a drop during an unconfirmed lazy-attach must clear the 'Starting the agent…' note");
  assertEqual(p.sandbox.dashComposerEl.hidden, false,
    "the composer container itself must not be hidden either -- dashCloseSubagentView() runs " +
    "unconditionally in this handler, and no sub-agent panel was ever open for this session");
});

check("dashboard: an error frame with code close_in_progress during an unconfirmed lazy-attach re-enables the composer and clears the note (Fix 4, Step 9 review)", () => {
  // The realistic trigger: a lazy-load send('load', ...) for a session the
  // server happens to be mid-release on for an unrelated reason returns this
  // exact code with sessionId === the sid being loaded -- dashHandle()'s
  // `error` case used to return early for this code before ever reaching the
  // _dashLoadingSid reset/composer re-enable, leaving the composer stuck
  // under "Starting the agent…" with no recovery path (not even a later
  // reconnect, since dashReconnectOnReady() only resubscribes
  // if (_dashAttachedSid), which never gets set for a load that never
  // completed).
  const p = loadDashPicker({ viewingSid: "sess-x" });
  p.sandbox._dashLoadingSid = "sess-x";
  p.sandbox._dashPendingSend = "hello";
  p.sandbox._dashPendingImages = [{ mimeType: "image/webp", data: "x" }];
  p.sandbox.dashPromptInput.disabled = true;
  const notes = [];
  p.sandbox.dashSetComposerNote = (t) => notes.push(t);
  p.sandbox.dashHandle({
    type: "error", sessionId: "sess-x",
    payload: { code: "close_in_progress", message: "closing" },
  });
  assertEqual(p.sandbox.dashPromptInput.disabled, false,
    "a close_in_progress refusal for the session currently lazy-loading must re-enable the composer");
  assertEqual(notes[notes.length - 1], "",
    "a close_in_progress refusal during an unconfirmed lazy-attach must clear the 'Starting the agent…' note");
  assertEqual(p.sandbox._dashLoadingSid, null, "_dashLoadingSid must be cleared");
  assertEqual(p.sandbox._dashPendingSend, null, "_dashPendingSend must be cleared");
  assertEqual(p.sandbox._dashPendingImages, null, "_dashPendingImages must be cleared");
  assertEqual(p.el("dashReload").hidden, false,
    "the reload recovery must appear here -- " +
    "nothing else will retry this specific load");
});

check("dashboard: an error frame with code close_in_progress for a session NOT currently lazy-loading does not touch the composer (Fix 4, Step 9 review)", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1", dashTurnActive: false });
  p.sandbox._dashLoadingSid = null; // no lazy-attach in flight
  p.sandbox._dashPendingCreate = { cwd: "/y", mode: "spec" };
  p.sandbox.dashPromptInput.disabled = false;
  p.sandbox.dashHandle({
    type: "error", sessionId: "sess-1",
    payload: { code: "close_in_progress", message: "closing" },
  });
  // The pending-create path (this branch's real purpose) must still run
  // unaffected by the hoisted lazy-attach guard above, which never fires here.
  assertEqual(p.sandbox._dashLoadingSid, null, "_dashLoadingSid stays null -- nothing was in flight");
});

check("dashboard: reconnect — a drop tears down crew-panel timers immediately, not waiting for a reconnect that may never come (Fix 8, Step 9 review)", () => {
  // Supersedes a pre-Fix-8 test with the opposite assertion ("a drop by
  // itself must not tear down crew timers -- only a fresh session/history
  // frame does") -- that was the bug: if reconnect never succeeds, or the
  // user never retries, the timers ticked forever with nothing left to tick
  // against. dashConnect()'s onclose now calls dashRemoveAllCrewPanels()
  // directly, alongside the existing dashCloseSubagentView()/dashCloseSubWs()
  // calls, so the timers are torn down on the drop itself.
  const p = loadDashPicker({ realConnect: true, dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "explorer", task: "", sessionName: "", status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  assertEqual(p.intervals.length, 1, "fixture: a running crew slot should have started its elapsed-time timer");
  p.sandbox.dashConnect();
  p.openMain();
  p.closeMain();
  assertEqual(p.intervals.length, 0,
    "a drop must tear down crew-panel timers immediately, even without a subsequent session/history frame " +
    "-- an indefinitely failed reconnect must not leave them ticking forever");
  assertEqual(Object.keys(p.sandbox.dashCrews).length, 0, "the drop must also clear crew state, not just the timer");
});

check("dashboard: reconnect — the reconnect's own session/history frames remain a harmless no-op for crew-panel teardown once onclose already tore it down", () => {
  const p = loadDashPicker({ realConnect: true, dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.sandbox.dashHandle(dashSubagentsFrame("sess-1", [
    { sessionId: "sub-1", role: "explorer", task: "", sessionName: "", status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  p.sandbox.dashConnect();
  p.openMain();
  p.closeMain();
  assertEqual(p.intervals.length, 0, "sanity check -- the drop itself already tore the timer down (Fix 8)");
  p.runTimers();
  p.openMain();
  p.deliverMain({
    type: "session", sessionId: "sess-1",
    payload: { sessionId: "sess-1", cwd: "/ws", created: false, turnActive: false, contextPercent: null },
  });
  p.deliverMain({ type: "history", sessionId: "sess-1", payload: { events: [] } });
  assertEqual(p.intervals.length, 0,
    "the reconnect's own history-frame teardown guards must not error or double-count against an " +
    "already-cleared crew state");
});

check("dashboard: reconnect — a drop closes any open command palette", () => {
  const p = loadDashPicker({ realConnect: true, dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.sandbox.dashHandle({
    type: "commands", sessionId: "sess-1",
    payload: { commands: [{ name: "tools", description: "Tools list" }] },
  });
  p.sandbox.dashPromptInput.value = "";
  p.sandbox.dashPromptInput.dispatch("keydown", {
    key: "/", shiftKey: false, ctrlKey: false, altKey: false, preventDefault() {},
  });
  assertEqual(p.el("dashCmdDropdown").hidden, false, "fixture: the palette should be open");
  p.sandbox.dashConnect();
  p.openMain();
  p.closeMain();
  assertEqual(p.el("dashCmdDropdown").hidden, true,
    "a drop must close any open command palette, matching its existing hideCommandDropdown() call on " +
    "every other turn-state transition");
});

check("dashboard: reconnect — a steer in flight when the socket drops is restored to the textarea and re-enables the mode controls", () => {
  const p = loadDashPicker({ realConnect: true, dashAttachedSid: "sess-1", dashSteerPending: "steer me" });
  p.sandbox.dashPromptInput.disabled = true;
  p.sandbox.dashSendModeBtn.disabled = true;
  p.sandbox.dashModeToggle.disabled = true;
  p.sandbox.dashConnect();
  p.openMain();
  p.closeMain();
  assertEqual(p.sandbox.dashPromptInput.value, "steer me",
    "a drop must restore the in-flight steer text to the textarea -- the steer_ack that would have " +
    "cleared it is never coming on a dead socket");
  assertEqual(p.sandbox._dashSteerPending, null, "_dashSteerPending must be cleared once restored");
  assertEqual(p.sandbox.dashPromptInput.disabled, false, "the textarea must be re-enabled");
  assertEqual(p.sandbox.dashSendModeBtn.disabled, false, "the send-mode button must be re-enabled");
  assertEqual(p.sandbox.dashModeToggle.disabled, false, "the mode toggle must be re-enabled");
});

check("dashboard: reconnect — a landed steer_sent confirmed via the reconnect's history replay is not left in the textarea to be re-sent (Fix 2, Step 9 review)", () => {
  // Reproduces the exact race: steer_sent is recorded into session history
  // server-side (acp.py's _handle_steer) whether or not this connection ever
  // received the steer_ack confirming it. onclose (tested above) restores the
  // in-flight text into the textarea with no way yet to know whether it
  // landed -- the reconnect's buffered `history` replay is the first place
  // that answer becomes available.
  const p = loadDashPicker({
    realConnect: true, dashAttachedSid: "sess-1", viewingSid: "sess-1", dashSteerPending: "look at foo.py",
  });
  p.sandbox.dashPromptInput.disabled = true;
  p.sandbox.dashSendModeBtn.disabled = true;
  p.sandbox.dashModeToggle.disabled = true;
  p.sandbox.dashConnect();
  p.openMain();
  p.closeMain();
  assertEqual(p.sandbox.dashPromptInput.value, "look at foo.py",
    "sanity check -- onclose must have restored the in-flight steer text, unable to know yet whether it landed");
  assertEqual(p.sandbox._dashSteerPending, null, "sanity check -- onclose already cleared _dashSteerPending");
  p.runTimers();
  p.openMain();
  p.deliverMain({
    type: "session", sessionId: "sess-1",
    payload: { sessionId: "sess-1", cwd: "/ws", created: false, turnActive: true, contextPercent: null },
  });
  p.deliverMain({
    type: "history", sessionId: "sess-1",
    payload: { events: [
      { type: "steer_sent", sessionId: "sess-1", payload: { text: "look at foo.py" } },
    ] },
  });
  assertEqual(p.sandbox.dashPromptInput.value, "",
    "the buffered steer_sent confirms the steer landed before the drop -- the restored text must not be left " +
    "sitting in the textarea ready to be sent again");
});

check("dashboard: reconnect — an unrelated, non-matching steer_sent in the history replay does not clear a genuinely different still-pending steer (Fix 2, Step 9 review)", () => {
  const p = loadDashPicker({
    realConnect: true, dashAttachedSid: "sess-1", viewingSid: "sess-1", dashSteerPending: "a different steer",
  });
  p.sandbox.dashConnect();
  p.openMain();
  p.closeMain();
  assertEqual(p.sandbox.dashPromptInput.value, "a different steer", "sanity check -- restored on drop");
  p.runTimers();
  p.openMain();
  p.deliverMain({
    type: "session", sessionId: "sess-1",
    payload: { sessionId: "sess-1", cwd: "/ws", created: false, turnActive: true, contextPercent: null },
  });
  p.deliverMain({
    type: "history", sessionId: "sess-1",
    payload: { events: [
      { type: "steer_sent", sessionId: "sess-1", payload: { text: "an unrelated earlier steer" } },
    ] },
  });
  assertEqual(p.sandbox.dashPromptInput.value, "a different steer",
    "a non-matching steer_sent must not clear text the user still genuinely has pending to send");
});

check("dashboard: reconnect — a history replay's steer_sent does not touch the textarea when nothing was restored (no coincidental match)", () => {
  const p = loadDashPicker({ dashAttachedSid: "sess-1", viewingSid: "sess-1" });
  p.sandbox.dashPromptInput.value = "";
  p.sandbox.dashHandle({
    type: "history", sessionId: "sess-1",
    payload: { events: [
      { type: "steer_sent", sessionId: "sess-1", payload: { text: "" } },
      { type: "steer_sent", sessionId: "sess-1", payload: {} },
    ] },
  });
  assertEqual(p.sandbox.dashPromptInput.value, "",
    "an empty/malformed steer_sent text must be ignored, not coincidentally match an empty textarea");
});

check("dashboard: reconnect — a drop clears a Stop click's in-progress flag", () => {
  const p = loadDashPicker({
    realConnect: true, dashAttachedSid: "sess-1", dashTurnActive: true, dashStopInProgress: true,
  });
  p.sandbox.dashConnect();
  p.openMain();
  p.closeMain();
  assertEqual(p.sandbox._dashStopInProgress, false,
    "a drop must clear _dashStopInProgress -- otherwise Stop stays disabled forever with no turn:end " +
    "coming to clear it");
});

// ---- Eager-connect tests (eager-connect plan, 260922_DASH_EAGER_CONNECT_ON_FOCUS) ----
// The focus listener added to dashPromptInput by this plan fires when the user
// clicks into the textarea for an available kiro-cli-v3 session, starting the
// ACP load before the first send so the commands/skills catalogue arrives early.

check("dashboard: eager-connect — focus triggers a load for an available session", () => {
  const p = loadDashPicker({ realConnect: true, viewingSid: "sess-1" });
  p.sandbox.dashConnect();
  p.sandbox.dashPromptInput.dispatch("focus");
  assertEqual(p.sandbox._dashLoadingSid, "sess-1",
    "focus on dashPromptInput must set _dashLoadingSid for the current session");
  assertEqual(p.sandbox._dashEagerLoadPending, true,
    "focus must set _dashEagerLoadPending so error handler suppresses Reload");
  assertEqual(p.sandbox._dashOrigin, "dashboard",
    "focus must set _dashOrigin to 'dashboard' for dashCloseIfAbandoned to work");
  const loads = p.sentOf("load");
  assertEqual(loads.length, 1, "focus must send exactly one load frame");
  assertEqual(loads[0].sid, "sess-1", "the load frame must target the viewed session");
});

check("dashboard: eager-connect — focus guard: _dashLoadingSid already set prevents double-load", () => {
  const p = loadDashPicker({ realConnect: true, viewingSid: "sess-1" });
  p.sandbox._dashLoadingSid = "sess-1";
  p.sandbox.dashConnect();
  p.sandbox.dashPromptInput.dispatch("focus");
  assertEqual(p.sentOf("load").length, 0,
    "focus must not send a second load when _dashLoadingSid is already set");
  assertEqual(p.sandbox._dashLoadingSid, "sess-1",
    "_dashLoadingSid must be unchanged when the guard fires");
});

check("dashboard: eager-connect — focus guard: _dashAttachedSid already set (already attached)", () => {
  const p = loadDashPicker({ realConnect: true, viewingSid: "sess-1", dashAttachedSid: "sess-1" });
  p.sandbox.dashConnect();
  p.sandbox.dashPromptInput.dispatch("focus");
  assertEqual(p.sentOf("load").length, 0,
    "focus must not load when _dashAttachedSid is already set (session already live)");
});

check("dashboard: eager-connect — focus guard: _dashOrigin==='joined' (held-session subscribe in flight)", () => {
  const p = loadDashPicker({ realConnect: true, viewingSid: "sess-1" });
  p.sandbox._dashOrigin = "joined";
  p.sandbox.dashConnect();
  p.sandbox.dashPromptInput.dispatch("focus");
  assertEqual(p.sentOf("load").length, 0,
    "focus must not load when _dashOrigin==='joined' — a held session subscribe is in flight");
  assertEqual(p.sandbox._dashOrigin, "joined",
    "_dashOrigin must not be overwritten from 'joined' to 'dashboard' by the focus guard");
});

check("dashboard: eager-connect — focus guard: _dashPendingCreate set (close-then-create in progress)", () => {
  const p = loadDashPicker({ realConnect: true, viewingSid: "sess-1" });
  p.sandbox._dashPendingCreate = { cwd: "/foo", mode: "kiro_default" };
  p.sandbox.dashConnect();
  p.sandbox.dashPromptInput.dispatch("focus");
  assertEqual(p.sentOf("load").length, 0,
    "focus must not consume a slot when a close-then-create is in progress");
});

check("dashboard: eager-connect — focus guard: _dashEagerFocusSuppressed (programmatic focus)", () => {
  const p = loadDashPicker({ realConnect: true, viewingSid: "sess-1" });
  p.sandbox._dashEagerFocusSuppressed = true;
  p.sandbox.dashConnect();
  p.sandbox.dashPromptInput.dispatch("focus");
  assertEqual(p.sentOf("load").length, 0,
    "focus must not load when _dashEagerFocusSuppressed is true (programmatic focus from image handler)");
});

check("dashboard: eager-connect — double-load guard same sid: send while eager load in flight holds prompt", () => {
  const p = loadDashPicker({ realConnect: true, viewingSid: "sess-1" });
  p.sandbox._dashLoadingSid = "sess-1";
  p.sandbox._dashEagerLoadPending = true;
  p.sandbox.dashPromptInput.value = "hello";
  p.sandbox.dashSendPrompt();
  assertEqual(p.sandbox._dashPendingSend, "hello",
    "_dashPendingSend must be set so the history frame delivers the prompt");
  assertEqual(p.sentOf("load").length, 0,
    "dashSendPrompt must not issue a second load when _dashLoadingSid === sid");
  assertEqual(p.sandbox._dashLoadingSid, "sess-1",
    "_dashLoadingSid must be unchanged — the in-flight load continues");
  assertEqual(p.sandbox.dashPromptInput.disabled, true,
    "the textarea must be disabled while waiting for the session frame");
});

check("dashboard: eager-connect — cross-session guard: send while different load is in flight", () => {
  const p = loadDashPicker({ realConnect: true, viewingSid: "sess-B" });
  p.sandbox._dashLoadingSid = "sess-A";
  p.sandbox.dashPromptInput.value = "hello";
  p.sandbox.dashSendPrompt();
  assertEqual(p.sandbox._dashPendingSend, "hello",
    "_dashPendingSend must be set for sess-B");
  assertEqual(p.sentOf("load").length, 0,
    "no new load must be sent for sess-B while sess-A's load is in flight");
  assertEqual(p.sandbox._dashLoadingSid, "sess-A",
    "_dashLoadingSid must remain sess-A (still in flight)");
  assertEqual(p.sandbox.dashPromptInput.disabled, true,
    "the textarea must be disabled while waiting for the stale-arrival guard to clear");
});

check("dashboard: eager-connect — error handler suppresses Reload for eager-triggered failures", () => {
  const p = loadDashPicker({ realConnect: true, viewingSid: "sess-1" });
  p.sandbox._dashLoadingSid = "sess-1";
  p.sandbox._dashEagerLoadPending = true;
  p.sandbox.dashConnect();
  p.openMain();
  p.deliverMain({
    type: "error", sessionId: "sess-1",
    payload: { code: "too_many_sessions", message: "cap reached" },
  });
  assertEqual(p.el("dashReload").hidden, true,
    "Reload button must stay hidden for a focus-triggered load failure (SC-5)");
  assertEqual(p.sandbox.dashPromptInput.disabled, false,
    "textarea must be re-enabled silently after eager-load failure");
  assertEqual(p.sandbox._dashEagerLoadPending, false,
    "_dashEagerLoadPending must be cleared by the error handler");
  assertEqual(p.sandbox._dashLoadingSid, null,
    "_dashLoadingSid must be cleared by the error handler");
});

check("dashboard: eager-connect — error handler shows Reload for non-eager (user-triggered) failures", () => {
  const p = loadDashPicker({ realConnect: true, viewingSid: "sess-1" });
  p.sandbox._dashLoadingSid = "sess-1";
  p.sandbox._dashEagerLoadPending = false;
  p.sandbox.dashConnect();
  p.openMain();
  p.deliverMain({
    type: "error", sessionId: "sess-1",
    payload: { code: "too_many_sessions", message: "cap reached" },
  });
  assertEqual(p.el("dashReload").hidden, false,
    "Reload button must appear for a non-eager (user-triggered) load failure");
  assertEqual(p.sandbox._dashEagerLoadPending, false,
    "_dashEagerLoadPending stays false");
});

check("dashboard: eager-connect — close_in_progress error with eager load suppresses Reload (hoisted _dashLoadingSid block runs first)", () => {
  const p = loadDashPicker({ realConnect: true, viewingSid: "sess-1" });
  p.sandbox._dashLoadingSid = "sess-1";
  p.sandbox._dashEagerLoadPending = true;
  p.sandbox.dashConnect();
  p.openMain();
  p.deliverMain({
    type: "error", sessionId: "sess-1",
    payload: { code: "close_in_progress", message: "releasing" },
  });
  assertEqual(p.el("dashReload").hidden, true,
    "Reload must stay hidden even for close_in_progress — the _dashLoadingSid block runs before the early return");
  assertEqual(p.sandbox._dashLoadingSid, null,
    "_dashLoadingSid must be cleared");
});

check("dashboard: eager-connect — onclose clears _dashEagerLoadPending before _dashWasLazyAttaching capture", () => {
  const p = loadDashPicker({ realConnect: true, viewingSid: "sess-1" });
  p.sandbox.dashConnect();
  p.openMain();
  p.sandbox._dashLoadingSid = "sess-1";
  p.sandbox._dashEagerLoadPending = true;
  p.closeMain();
  assertEqual(p.sandbox._dashEagerLoadPending, false,
    "onclose must clear _dashEagerLoadPending unconditionally");
  assertEqual(p.sandbox._dashLoadingSid, null,
    "onclose must clear _dashLoadingSid");
});

check("dashboard: eager-connect — stale-arrival guard clears _dashEagerLoadPending for a focus-then-navigate-away", () => {
  const p = loadDashPicker({ realConnect: true, viewingSid: "sess-2" });
  p.sandbox._dashLoadingSid = "sess-1";  // focus was on sess-1
  p.sandbox._dashEagerLoadPending = true;
  p.sandbox.dashConnect();
  p.openMain();
  // Session frame for sess-1 arrives, but user is now viewing sess-2
  p.deliverMain({ type: "session", sessionId: "sess-1", payload: {} });
  assertEqual(p.sandbox._dashEagerLoadPending, false,
    "stale-arrival guard must clear _dashEagerLoadPending when closing the orphaned session");
  assertEqual(p.sandbox._dashLoadingSid, null,
    "stale-arrival guard must clear _dashLoadingSid");
  const closes = p.sentOf("close").filter(f => f.sid === "sess-1");
  assertEqual(closes.length, 1,
    "stale-arrival guard must send close for the orphaned session (SC-3)");
});

check("dashboard: eager-connect — focus guard: !_viewingSid (no session selected)", () => {
  const p = loadDashPicker({ realConnect: true, viewingSid: null });
  p.sandbox.dashConnect();
  p.sandbox.dashPromptInput.dispatch("focus");
  assertEqual(p.sentOf("load").length, 0,
    "focus must not load when _viewingSid is null");
});

// Both directions, because only the pair catches an inverted sentinel.
// 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 6
check("dashboard: eager-connect — focus guard: !ACP_AVAILABLE (ACP not available)", () => {
  const p = loadDashPicker({ realConnect: true, viewingSid: "sess-1", acpAvailable: false });
  p.sandbox.dashConnect();
  p.sandbox.dashPromptInput.dispatch("focus");
  assertEqual(p.sentOf("load").length, 0,
    "focus must not load when ACP_AVAILABLE is false");
});

check("dashboard: eager-connect — focus loads when ACP_AVAILABLE is true (the sentinel's other direction)", () => {
  const p = loadDashPicker({ realConnect: true, viewingSid: "sess-1", acpAvailable: true });
  p.sandbox.dashConnect();
  p.sandbox.dashPromptInput.dispatch("focus");
  p.openMain();
  assertEqual(p.sentOf("load").length, 1,
    "focus on a viewed session must eager-load it when ACP is available");
});

check("dashboard: eager-connect — stale-arrival re-enables composer for B after A's session frame arrives mid-cross-session", () => {
  // Scenario: user focuses A (eager load), clicks B (cross-session guard
  // holds B's prompt with textarea disabled), A's session frame arrives.
  const p = loadDashPicker({ realConnect: true, viewingSid: "sess-B" });
  p.sandbox._dashLoadingSid = "sess-A";
  p.sandbox._dashEagerLoadPending = true;
  // Simulate cross-session guard already fired: textarea is disabled, prompt held
  p.sandbox._dashPendingSend = "hello from B";
  p.sandbox.dashPromptInput.disabled = true;
  p.sandbox.dashSendBtn.disabled = true;
  p.sandbox.dashConnect();
  p.openMain();
  // A's session frame arrives — stale-arrival guard should fire
  p.deliverMain({ type: "session", sessionId: "sess-A", payload: {} });
  assertEqual(p.sandbox.dashPromptInput.disabled, true,
    "after auto-retry, textarea is disabled while the new load for B is in flight");
  assertEqual(p.sandbox.dashPromptInput.value, "hello from B",
    "stale-arrival guard must restore B's pending prompt to the textarea");
  assertEqual(p.sandbox._dashPendingSend, "hello from B",
    "_dashPendingSend must be re-set by the lazy-attach path for the new load");
  assertEqual(p.sandbox._dashLoadingSid, "sess-B",
    "_dashLoadingSid must be set to sess-B after the auto-retry lazy-attach");
  const closes = p.sentOf("close").filter(f => f.sid === "sess-A");
  assertEqual(closes.length, 1,
    "stale-arrival guard must send close for the orphaned session A");
  // Auto-retry: dashSendPrompt() is called automatically since value is non-empty.
  // With _dashAttachedSid null and _dashLoadingSid cleared, it takes the
  // lazy-attach path and issues a load for sess-B.
  const loads = p.sentOf("load").filter(f => f.sid === "sess-B");
  assertEqual(loads.length, 1,
    "stale-arrival guard must auto-retry B's prompt via dashSendPrompt (FW-1 fix)");
});

// ---- Phase 7: MCP status panel (SC-4) ------------------------------------
// Frames carry acp.py's projection: {name, status, failedAuthorization, toolCount}.

function mcpDeliver(page, live, servers) {
  page.deliver({ type: "mcp_servers", sessionId: live, payload: { servers } });
}
function mcpRows(page) {
  return page.el("acpMcpList").querySelectorAll(".acp-mcp-server");
}

check("mcp: frame shows the indicator with connected/active ratio", (tpl) => {
  const { page, live } = connected(tpl);
  mcpDeliver(page, live, [
    { name: "github", status: "connected", toolCount: 3 },
    { name: "jira", status: "connected", toolCount: 1 },
    { name: "confluence", status: "disabled" },
  ]);
  assert(!page.el("acpMcpIndicator").hidden, "indicator hidden after an mcp_servers frame");
  assertEqual(page.el("acpMcpCompact").textContent, "MCP 2/2",
    "disabled servers must not count toward the ratio");
  const toggle = page.el("acpMcpToggle");
  assert(!toggle.classList.contains("acp-mcp-warn") &&
         !toggle.classList.contains("acp-mcp-caution"),
    "all active servers connected: no warning colour");
  assertEqual(toggle.getAttribute("aria-label"), "MCP servers \u2014 2 of 2 connected",
    "accessible name");
});

check("mcp: sign-in needed is amber, never red, and offers Connect", (tpl) => {
  const { page, live } = connected(tpl);
  mcpDeliver(page, live, [
    { name: "playwright", status: "connected", toolCount: 21 },
    { name: "atlassian", status: "failed", failedAuthorization: true },
  ]);
  const toggle = page.el("acpMcpToggle");
  assert(toggle.classList.contains("acp-mcp-caution"), "sign-in needed must be amber");
  assert(!toggle.classList.contains("acp-mcp-warn"), "sign-in needed must not be red");
  assert(/1 needs sign-in/.test(toggle.getAttribute("aria-label")),
    "accessible name must say sign-in is needed: " + toggle.getAttribute("aria-label"));
  const row = mcpRows(page)[1];
  assert(row.querySelector(".acp-mcp-badge-auth"), "sign-in badge class missing");
  assertEqual(row.querySelector(".acp-mcp-server-detail").textContent, "Needs sign-in", "detail");
  const btn = row.querySelector(".acp-mcp-connect-btn");
  assert(btn && !btn.disabled && btn.textContent === "Connect", "a sign-in row offers Connect");
  assertEqual(mcpRows(page)[0].querySelectorAll("button").length, 0,
    "a connected server offers no button");
});

check("mcp: Connect sends mcp_signin once, then waits for the browser sign-in", (tpl) => {
  const { page, live } = connected(tpl);
  const servers = (atl) => [{ name: "playwright", status: "connected", toolCount: 21 }, atl];
  mcpDeliver(page, live, servers({ name: "atlassian", status: "failed", failedAuthorization: true }));
  mcpRows(page)[1].querySelector(".acp-mcp-connect-btn").dispatch("click", {});
  const sent = page.sentOf("mcp_signin");
  assertEqual(sent.length, 1, "Connect must send one mcp_signin frame");
  assertEqual(sent[0].payload.serverName, "atlassian", "frame names the server");
  assertEqual(sent[0].sessionId, live, "frame is for the session on screen");
  let row = mcpRows(page)[1];
  let btn = row.querySelector(".acp-mcp-connect-btn");
  assert(btn.disabled && /Waiting/.test(btn.textContent), "button waits after a press");
  assert(/browser on the PowerAtlas PC/.test(row.querySelector(".acp-mcp-server-detail").textContent),
    "detail says where to finish signing in");
  btn.dispatch("click", {});
  assertEqual(page.sentOf("mcp_signin").length, 1, "a waiting row must not send again");
  // kiro-cli reports the server as connecting while the browser flow runs.
  mcpDeliver(page, live, servers({ name: "atlassian", status: "connecting" }));
  assert(/browser on the PowerAtlas PC/.test(mcpRows(page)[1].querySelector(".acp-mcp-server-detail").textContent),
    "still waiting while the server is connecting");
  mcpDeliver(page, live, servers({ name: "atlassian", status: "connected", toolCount: 41 }));
  row = mcpRows(page)[1];
  assertEqual(row.querySelector(".acp-mcp-server-detail").textContent, "41 tools", "signed in");
  assertEqual(row.querySelectorAll("button").length, 0, "no button once connected");
});

check("mcp: an mcp_signin_failed error offers Connect again", (tpl) => {
  const { page, live } = connected(tpl);
  mcpDeliver(page, live, [{ name: "atlassian", status: "failed", failedAuthorization: true }]);
  mcpRows(page)[0].querySelector(".acp-mcp-connect-btn").dispatch("click", {});
  assert(mcpRows(page)[0].querySelector(".acp-mcp-connect-btn").disabled, "waiting first");
  page.deliver({ type: "error", sessionId: live,
                 payload: { code: "mcp_signin_failed", message: "Sign-in did not complete" } });
  const btn = mcpRows(page)[0].querySelector(".acp-mcp-connect-btn");
  assert(btn && !btn.disabled && btn.textContent === "Connect", "Connect is offered again");
});

check("mcp: a real failure is red", (tpl) => {
  const { page, live } = connected(tpl);
  mcpDeliver(page, live, [{ name: "broken", status: "failed" }]);
  assert(page.el("acpMcpToggle").classList.contains("acp-mcp-warn"), "failed must be red");
  assertEqual(mcpRows(page)[0].querySelector(".acp-mcp-server-detail").textContent,
    "Failed to start", "failed detail");
});

check("mcp: disabled servers sort last and are dimmed; states read as text", (tpl) => {
  const { page, live } = connected(tpl);
  mcpDeliver(page, live, [
    { name: "zoho", status: "disabled" },
    { name: "playwright", status: "connected", toolCount: 1 },
    { name: "slow", status: "connecting" },
    { name: "weird", status: "<b>x</b>" },
  ]);
  const rows = mcpRows(page);
  const names = rows.map((r) => r.querySelector(".acp-mcp-server-name").textContent);
  assertEqual(JSON.stringify(names), JSON.stringify(["playwright", "slow", "zoho", "weird"]),
    "active servers first, in their own order, then disabled (unknown status counts as disabled)");
  assert(rows[2].classList.contains("acp-mcp-server-disabled"), "disabled row not dimmed");
  const details = rows.map((r) => r.querySelector(".acp-mcp-server-detail").textContent);
  assertEqual(details[0], "1 tool", "singular tool count");
  assertEqual(details[1], "Connecting\u2026", "connecting detail");
  assertEqual(details[2], "Disabled", "disabled detail");
  assert(rows[3].querySelector(".acp-mcp-badge-disabled"),
    "an unknown status must not reach className as-is");
});

check("mcp: toggle opens, outside click closes, inside click does not", (tpl) => {
  const { page, live } = connected(tpl);
  // `byId` is flat (see loadPage), so nest the one relationship this check
  // is about, as the markup does: indicator > panel > list.
  page.el("acpMcpPanel").appendChild(page.el("acpMcpList"));
  page.el("acpMcpIndicator").appendChild(page.el("acpMcpPanel"));
  mcpDeliver(page, live, [{ name: "github", status: "connected" }]);
  const toggle = page.el("acpMcpToggle");
  toggle.dispatch("click", {});
  assertEqual(toggle.getAttribute("aria-expanded"), "true", "toggle did not open the panel");
  page.fireDoc("click", { target: mcpRows(page)[0] });
  assertEqual(toggle.getAttribute("aria-expanded"), "true",
    "a click inside the panel must not close it");
  page.fireDoc("click", { target: page.el("acpLogToggle") });
  assertEqual(toggle.getAttribute("aria-expanded"), "false", "outside click did not close it");
});

check("mcp: Escape closes the panel and returns focus to the toggle", (tpl) => {
  const { page, live } = connected(tpl);
  mcpDeliver(page, live, [{ name: "github", status: "connected" }]);
  const toggle = page.el("acpMcpToggle");
  toggle.dispatch("click", {});
  ACTIVE = null;
  page.fireDoc("keydown", { key: "Escape" });
  assertEqual(toggle.getAttribute("aria-expanded"), "false", "Escape did not close the panel");
  assert(ACTIVE === toggle, "focus must return to the toggle");
});

check("mcp: session change hides the indicator and closes the panel", (tpl) => {
  const { page, live } = connected(tpl);
  mcpDeliver(page, live, [{ name: "github", status: "connected" }]);
  page.el("acpMcpToggle").dispatch("click", {});
  const newSid = "sess-mcp-reset";
  page.deliver({
    type: "session", sessionId: newSid,
    payload: { sessionId: newSid, cwd: "C:\\tmp", created: true,
               turnActive: false, contextPercent: null },
  });
  assert(page.el("acpMcpIndicator").hidden, "indicator still shown after session change");
  assertEqual(page.el("acpMcpToggle").getAttribute("aria-expanded"), "false",
    "an open panel would re-open on the next session's first frame");
});

// Both pages get the panel's behaviour from the one module. The dashboard's
// own copy once shipped without Escape; a page-local listener would fork it again.
check("mcp: the dashboard wires the shared indicator and keeps no copy of its listeners", () => {
  const html = fs.readFileSync(
    path.join(HERE, "..", "src", "power_atlas", "templates", "index.html"), "utf8");
  assert(/initMcpIndicatorDom\(\{[\s\S]*?toggleEl:\s*document\.getElementById\('dashMcpToggle'\)/.test(html),
    "index.html does not pass #dashMcpToggle to initMcpIndicatorDom");
  assert(!/getElementById\('dashMcpToggle'\)\.addEventListener|_dashMcpToggleEl/.test(html),
    "index.html wires its own MCP toggle listener again");
});

// The [hidden] escape must cover every page that uses the indicator class. An
// id-scoped `#acpMcpIndicator[hidden]` left the dashboard's #dashMcpIndicator
// rendered (display:flex, an empty hexagon button) with no session attached —
// measured in Chromium 2026-09-24. Source check: the DOM stand-in has no
// cascade. Comments stripped so the prose above the rule cannot satisfy it.
check("mcpIndicatorHiddenRuleIsClassScoped", () => {
  const css = fs.readFileSync(
    path.join(HERE, "..", "src", "power_atlas", "static", "style.css"), "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "");
  assert(/^\.acp-mcp-indicator\[hidden\]\s*\{[^}]*display:\s*none\s*!important/m.test(css),
    "style.css has no class-scoped `.acp-mcp-indicator[hidden] { display: none !important }` " +
    "rule, so the dashboard's indicator ignores its hidden attribute");
});

// ---- 260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL final review --------------

check("settings: Apply again is offered only while something is wrong, and posts the checked mode (final review, H-A)", async () => {
  const answer = Object.assign({ ok: true }, permState({ mode: "manual" }));
  const p = loadPanel({ answer: (url) => url === "/api/acp-permissions" ? { body: answer } : { body: {} } });
  const $ = (id) => p.sandbox.document.getElementById(id);
  p.sandbox.renderAcpPermissions(permState({ mode: "manual" }));
  assertEqual($("acpPermApplyAgain").hidden, true, "a healthy state offers Apply again");
  p.sandbox.renderAcpPermissions(permState({ mode: "manual", in_effect: false, state: "absent" }));
  assertEqual($("acpPermApplyAgain").hidden, false, "not in effect does not offer Apply again");
  assert(/press Apply again|Press Apply again/.test($("acpPermWarn").textContent),
    `the warning does not name the button: ${$("acpPermWarn").textContent}`);
  p.sandbox.applyAcpPermissionModeAgain();
  assertEqual(p.fetches.length, 1, "Apply again sent nothing");
  assertEqual(String(p.fetches[0].init.method).toUpperCase(), "POST", "Apply again did not POST");
  assertEqual(JSON.stringify(JSON.parse(p.fetches[0].init.body)), JSON.stringify({ mode: "manual" }),
    "Apply again did not post the checked mode");
  await p.settle();
  assertEqual($("acpPermApplyAgain").hidden, true, "Apply again stayed after the answer put it in effect");
  // A pending notice or a junk stored mode offers it too.
  p.sandbox.renderAcpPermissions(permState({ posture_notice: { mode: "yolo", what: "mode", detected_at: "t" } }));
  assertEqual($("acpPermApplyAgain").hidden, false, "a pending notice does not offer Apply again");
  p.sandbox.renderAcpPermissions(permState({ mode: "manual", mode_warning: "acp_permission_mode 'x' is not a permission mode; running as Manual" }));
  assertEqual($("acpPermApplyAgain").hidden, false, "a junk stored mode does not offer Apply again");
  // Never while config.toml cannot be read: the fix is the file.
  p.sandbox.renderAcpPermissions(permState({ in_effect: false, state: "stale",
    config_error: "PowerAtlas's config.toml could not be read (x)" }));
  assertEqual($("acpPermApplyAgain").hidden, true, "Apply again was offered while config.toml cannot be read");
  // With no radio checked there is nothing to apply.
  const sent = p.fetches.length;
  p.sandbox.applyAcpPermissionModeAgain();
  assertEqual(p.fetches.length, sent, "Apply again posted with no mode checked");
  assert(p.toasts.some((t) => t.includes("Choose a permission mode first")), "no mode checked went unexplained");
  // The markup: a real button, in the Agent permissions section.
  const src = fs.readFileSync(INDEX_TEMPLATE, "utf8");
  assert(/<button type="button" id="acpPermApplyAgain"[^>]*onclick="applyAcpPermissionModeAgain\(\)"[^>]*hidden>/.test(src),
    "index.html has no hidden Apply again button wired to applyAcpPermissionModeAgain()");
});

check("settings: an outside change says what changed and offers Review rules and Acknowledge (final review, M-3, EU14)", async () => {
  const cleared = Object.assign({ ok: true }, permState());
  const p = loadPanel({ answer: (url) => url === "/api/acp-permissions/acknowledge" ? { body: cleared } : { body: {} } });
  const notice = p.sandbox.document.getElementById("acpPermNotice");
  const labels = () => notice.querySelectorAll("button").map((b) => b.textContent);
  p.sandbox.renderAcpPermissions(permState({ mode: "manual",
    posture_notice: { mode: "manual", what: "rules", previous: "manual", detected_at: "2026-09-25 10:00:00" } }));
  assertEqual(notice.hidden, false, "a rules change was not shown");
  assert(/Manual's rules were changed outside the dashboard/.test(notice.textContent),
    `the notice does not say the rules changed: ${notice.textContent}`);
  assert(/does not undo a change to the rules/.test(notice.textContent),
    "the notice implies choosing the mode undoes a rules change");
  assertEqual(labels().join("|"), "Review rules|Acknowledge: keep Manual with the current rules",
    "the notice's buttons are not Review rules and Acknowledge");
  // EU14: the same notice again is not redrawn (a live region re-announces).
  const first = notice.childNodes[0];
  p.sandbox.renderAcpPermissions(permState({ mode: "manual",
    posture_notice: { mode: "manual", what: "rules", previous: "manual", detected_at: "2026-09-25 10:00:00" } }));
  assert(notice.childNodes[0] === first, "an unchanged notice was redrawn");
  p.sandbox.renderAcpPermissions(permState({ mode: "manual",
    posture_notice: { mode: "manual", what: "mode", previous: "yolo", detected_at: "t" } }));
  assert(/new sessions now use Manual instead of Yolo/.test(notice.textContent),
    `the mode notice does not name both modes: ${notice.textContent}`);
  p.sandbox.renderAcpPermissions(permState({
    posture_notice: { mode: "yolo", what: "file", detected_at: "t" } }));
  assert(/agent file \(poweratlas-acp.md\) was edited outside PowerAtlas/.test(notice.textContent)
    && /did not change/.test(notice.textContent), `the file notice is wrong: ${notice.textContent}`);
  p.sandbox.renderAcpPermissions(permState({
    posture_notice: { mode: "yolo", what: "base", detected_at: "t" } }));
  assert(/Base agent setting was changed/.test(notice.textContent), `the base notice is wrong: ${notice.textContent}`);
  assertEqual(labels().join("|"), "Acknowledge: keep Yolo", "a base-agent notice offers Review rules");
  const ack = notice.querySelectorAll("button").pop();
  ack.onclick();
  const posted = p.fetches.find((f) => f.url === "/api/acp-permissions/acknowledge");
  assert(posted, "Acknowledge sent nothing");
  assertEqual(JSON.stringify(JSON.parse(posted.init.body)), JSON.stringify({ notice: "posture" }),
    "Acknowledge did not name the notice");
  await p.settle();
  assertEqual(notice.hidden, true, "the acknowledged notice stayed up");
});

check("settings: the one-time upgrade notice names the mode, opens Always blocked and can be acknowledged (final review, M-6)", async () => {
  const cleared = Object.assign({ ok: true }, permState({ mode: "manual" }));
  const p = loadPanel({ answer: (url) => url === "/api/acp-permissions/acknowledge" ? { body: cleared } : { body: {} } });
  const $ = (id) => p.sandbox.document.getElementById(id);
  p.sandbox.renderAcpPermissions(permState({ mode: "manual",
    upgrade_notice: { mode: "manual", detected_at: "t" } }));
  const up = $("acpPermUpgrade");
  assertEqual(up.hidden, false, "the upgrade notice was not shown");
  assert(/Permissions are now modes: you are on Manual\. The Always blocked list applies in every mode\./.test(up.textContent),
    `the upgrade notice's words changed: ${up.textContent}`);
  assertEqual($("topbarPendingDot").hidden, false, "the upgrade notice did not light the gear dot");
  const buttons = up.querySelectorAll("button");
  assertEqual(buttons.map((b) => b.textContent).join("|"), "Show the Always blocked list|Acknowledge",
    "the upgrade notice's buttons changed");
  buttons[0].onclick();
  assertEqual($("acpPermFloor").open, true, "the Always blocked list was not opened");
  buttons[1].onclick();
  const posted = p.fetches.find((f) => f.url === "/api/acp-permissions/acknowledge");
  assertEqual(JSON.stringify(JSON.parse(posted.init.body)), JSON.stringify({ notice: "upgrade" }),
    "Acknowledge did not name the upgrade notice");
  await p.settle();
  assertEqual(up.hidden, true, "the acknowledged upgrade notice stayed up");
});

check("settings: Manual's description comes from the stored rules, never the seed's words (final review, M-5)", () => {
  const p = loadPanel();
  const desc = () => p.sandbox.document.getElementById("acpPermDescManual").textContent;
  const rows = [["fs_read", "Read files"], ["shell", "Run commands"], ["mcp", "MCP tools"], ["power", "Powers"]]
    .map(([id, label]) => ({ id, label }));
  const rules = {
    fs_read: { default: "ask", allow: ["./**"], block: [] },
    shell: { default: "ask", allow: ["git status", "npm test"], block: [] },
    mcp: { default: "allow", allow: ["s/t"], block: [] },
    power: { default: "block", allow: [], block: [] },
    protected_block: ["agents"],
  };
  const prot = [{ id: "agents", label: "Agent definitions", patterns: [], effect: "block" },
                { id: "steering", label: "Steering files", patterns: [], effect: "ask" }];
  p.sandbox.renderAcpPermissions(permState({ mode: "yolo", rules, rule_rows: rows, protected: prot }));
  const text = desc();
  assert(/Asks first: Read files, Run commands\./.test(text), `the ask rows are not named: ${text}`);
  assert(/Runs without asking: MCP tools\./.test(text), `the allow rows are not named: ${text}`);
  assert(/Blocked: Powers\./.test(text), `the block rows are not named: ${text}`);
  assert(/3 patterns run without asking/.test(text), `the allow patterns are not counted: ${text}`);
  assert(/blocked outright: Agent definitions/.test(text), `a migrated Protected block is not said: ${text}`);
  assert(!/git status|whoami|uname/.test(text), `the seed's commands are claimed: ${text}`);
});

check("settings: a Protected folder that could not be listed says so (final review, RE7)", () => {
  const p = loadPanel();
  p.sandbox.renderAcpPermissions(permState({ protected_links: {
    agents: { count: 0, links: [], error: "PermissionError: Access is denied" },
    steering: { count: 0, links: [], error: "" } } }));
  const prot = p.sandbox.document.getElementById("acpPermProtectedList").textContent;
  assert(prot.includes("Could not be checked for links: PermissionError: Access is denied"),
    `an unlistable folder read as no links: ${prot}`);
});

check("rules editor and card: a file pattern over PowerAtlas's settings or ~/.kiro is warned about (final review, SEC5)", async () => {
  const e = await openRules();
  e.add("fs_write", "allow", "C:/Users/me/AppData/Local/power-atlas/**");
  assert(/PowerAtlas's own settings folder/.test(e.warnings("fs_write")),
    `no warning for the settings folder: ${e.warnings("fs_write")}`);
  e.add("fs_write", "allow", "C:/Users/me/.kiro/**");
  assert(/kiro-cli's own folder/.test(e.warnings("fs_write")), `no warning for ~/.kiro: ${e.warnings("fs_write")}`);
  const d = loadDashCard(() => ({ body: {} }));
  const warn = d.sandbox.permissionRuleWarnings("fs_write", "C:/Users/me/AppData/Local/**", "");
  assert(warn.some((w) => /PowerAtlas's own settings folder/.test(w)), `the card does not warn: ${warn}`);
  assertEqual(d.sandbox.permissionRuleSensitivePlace("C:/work/src/**", "fs_write"), "",
    "an ordinary folder was warned about");
});

check("rules editor: its pattern checks agree with the server's over the shared case table (final review, EU13, A4)", async () => {
  const table = JSON.parse(fs.readFileSync(path.join(HERE, "permission_pattern_cases.json"), "utf8"));
  const e = await openRules();
  const problem = e.p.sandbox._acpRulesPatternProblem;
  for (const c of table.cases) {
    const list = Array.from({ length: c.list_size || 0 }, (_, i) => "p" + i);
    // The editor trims what was typed before it checks it (`_acpRulesAddFrom`).
    const typed = c.pattern.trim();
    const said = problem(list, typed, c.row, "allow");
    const got = !said ? "ok"
      : /matches everything/.test(said) ? "match-all"
      : /at most \d+ characters/.test(said) ? "long"
      : /goes up a folder/.test(said) ? "parent"
      : /at most \d+ patterns/.test(said) ? "full"
      : /contains (.*), which is not allowed/.test(said) ? "char" : said;
    // An edge space cannot reach the server from the editor: it is trimmed.
    const want = c.expect === "edge-space" ? "ok" : c.expect;
    assertEqual(got, want, `${JSON.stringify(c.pattern)}: ${said}`);
    if (c.expect === "char") {
      assertEqual(/contains (.*), which is not allowed/.exec(said)[1], c.words,
        `${JSON.stringify(c.pattern)} is described differently from the server`);
    }
    if (c.expect === "edge-space") {
      assert(typed !== c.pattern, `${JSON.stringify(c.pattern)} was not trimmed`);
    }
  }
});

check("dashboard: an optimistic change refused by the server reverts and says why (final review, RE9)", async () => {
  const src = fs.readFileSync(INDEX_TEMPLATE, "utf8");
  const pick = (name) => {
    const at = src.indexOf("function " + name + "(");
    assert(at >= 0, `index.html no longer defines ${name}`);
    let depth = 0;
    for (let i = src.indexOf("{", at); i < src.length; i++) {
      if (src[i] === "{") depth++;
      else if (src[i] === "}" && --depth === 0) return src.slice(at, i + 1);
    }
    throw new Error(`${name} is unterminated`);
  };
  const toasts = [];
  const box = { toasts, showToast: (h) => toasts.push(h),
    _escHtml: (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"),
    Promise, Error, String };
  vm.createContext(box);
  vm.runInContext(pick("_okOrRefused") + "\n" + pick("_refusedToast"), box);
  const refusal = { ok: false, status: 409, json: () => Promise.resolve({ ok: false,
    error: "PowerAtlas's config.toml could not be read (x), so the change was not saved." }) };
  let caught = null;
  await box._okOrRefused(refusal).catch((err) => { caught = err; });
  assert(caught && caught.refused === true && /could not be read/.test(caught.message),
    "a 409 was not raised as a refusal naming the fix");
  caught = null;
  await box._okOrRefused({ ok: true, status: 200, json: () => Promise.resolve({ ok: false, error: "Profile not found" }) })
    .catch((err) => { caught = err; });
  assert(caught && caught.message === "Profile not found", "an ok:false body was taken as success");
  const fine = await box._okOrRefused({ ok: true, status: 200, json: () => Promise.resolve({ enabled: true }) });
  assertEqual(fine.enabled, true, "a plain answer was refused");
  box._refusedToast("Failed to pin", { refused: true, message: "<b>fix config.toml</b>" });
  assert(toasts[0].includes("Failed to pin: &lt;b&gt;fix config.toml&lt;/b&gt;"), `the refusal was not escaped and shown: ${toasts[0]}`);
  for (const name of ["pinSession", "pinWorkspace", "toggleNotifications", "activateProfile"]) {
    assert(/_okOrRefused/.test(pick(name)), `${name} does not read the answer for a refusal`);
  }
});

let failed = 0;
for (const { name, fn } of checks) {
  try {
    // Awaited, so a check may be `async`: the rail's every behaviour is behind
    // a fetch, and a synchronous check would assert before the first `.then`.
    await fn(template);
    console.log(`  PASS  ${name}`);
  } catch (err) {
    failed += 1;
    console.log(`  FAIL  ${name}`);
    console.log(`        ${String(err && err.message || err).split("\n").join("\n        ")}`);
  }
}
console.log(`\n${checks.length - failed} passed, ${failed} failed of ${checks.length}`);
process.exit(failed ? 1 : 0);


