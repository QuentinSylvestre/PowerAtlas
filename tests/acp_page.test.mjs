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
  setAttribute(name, value) { this._attrs[String(name)] = String(value); }
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
    for (const fn of fns) fn.call(this, ev || {});
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
  };
}

// ---------------------------------------------------------------- the page --

function loadPage(templatePath, opts = {}) {
  const src = fs.readFileSync(templatePath, "utf8");
  const html = render(src, {
    acp_token: opts.token ?? "TEST-TOKEN",
    sid: opts.sid ?? "",
    acp_error: opts.acpError ?? "",
    csp_nonce: opts.nonce ?? "NONCE-1",
    // `web.py` derives this from `scope["client"]` (D26). Defaults to the
    // loopback reading, which is what a developer running the page sees.
    local: opts.local ?? true,
  });

  // `acp.html`'s own content block only — `{% extends %}` is stripped by
  // `render()`, so `base.html`'s `<script src="/static/htmx.min.js">` is not in
  // this string and is not being counted. The served `/acp` therefore has three
  // script elements, not two; the policy still holds because base.html applies
  // the same nonce conditionally, and `test_web.py` counts the served page.
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
                 confirms, store, reloaded: false };

  // A fetch with a body. The old stub answered `{ok: true}` and nothing else,
  // which is enough for the stale-token diagnosis (the only caller before the
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
      getElementById: (id) => byId.get(id) ?? null,
      addEventListener: (type, fn) => {
        if (!docListeners.has(type)) docListeners.set(type, []);
        docListeners.get(type).push(fn);
      },
      get visibilityState() { return visibility; },
      write: () => HTML_SINK("document.write"),
    },
    setInterval: (fn, ms) => { intervals.push({ fn, ms }); return intervals.length; },
    clearInterval: () => {},
    setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
    clearTimeout: () => {},
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
  vm.runInContext(scriptBody, sandbox, { filename: `${templatePath}#inline-script` });

  Object.assign(page, {
    el(id) {
      const found = byId.get(id);
      if (!found) throw new Error(`this page has no element with id '${id}'`);
      return found;
    },
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
  // Named for what it measures. The *served* /acp has two script elements: this
  // template extends base.html, which carries `<script src="/static/htmx.min.js">`
  // with the same nonce applied conditionally. `loadPage` renders this template
  // in isolation, so what the count below pins is that acp.html contributes one
  // inline script and no external one — not that the page has a single tag.
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
    assertEqual(String(dot.className), "session-status status-working",
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
  assert(/:focus-within/.test(hover),
         "the menu is revealed by pointing with no keyboard route to it, so "
         + "Delete becomes unreachable without a mouse");
  assert(/aria-expanded="true"/.test(hover),
         "nothing keeps the menu visible while its own popup is open, and the "
         + "pointer leaves the button the moment the popup is used");

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

check("the dashboard link is rendered for the viewer who can follow it", (tpl) => {
  // `/` is not on `_REMOTE_ALLOWED_PATHS` and never will be (SC-4), so from a
  // phone the old `<a href="/">` was a control whose only outcome was a 403
  // with no way back.
  const local = loadPage(tpl, { local: true });
  const localNav = local.markup.match(/<a\b[^>]*class="[^"]*topbar-nav[^"]*"[^>]*>/);
  assert(localNav, "the dashboard link is gone for a loopback viewer too");
  assert(/href="\/"/.test(localNav[0]),
         `the loopback link no longer reaches the dashboard: ${localNav[0]}`);
  assert(/aria-label=/.test(localNav[0]),
         "the link is unlabelled for a screen reader");

  const remote = loadPage(tpl, { local: false });
  assertEqual(remote.markup.match(/topbar-nav/g), null,
              "a remote viewer is still handed a link to a loopback-only page");
  assert(!/href="\/"/.test(remote.markup),
         "something else on the remote page still points at the dashboard");

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
  assert(dotClass().includes("status-working"), "the fixture did not render working");

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
  assert(dot && String(dot.className).includes("status-working"),
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
    local: false, store: fakeStore({ workspaces: 1, sessions: 2 }) });
  assertEqual(remote.railRows().length, 2,
              "the remote rail lost its rows, so this check is measuring nothing");
  assertEqual(remote.railMenuButtons().length, 0,
              "the remote viewer is offered a row menu; the delete route is " +
              "loopback-only, so every press of it would 403");
  // And the loopback viewer is, or the check above passes on a page with no
  // menu anywhere.
  const local = await railed(tpl, { store: fakeStore({ workspaces: 1, sessions: 2 }) });
  assertEqual(local.railMenuButtons().length, 2,
              "the loopback viewer has no row menu");
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
        if (type === "DOMContentLoaded") domReady.push(fn);
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
  vm.runInContext(panelSource(), sandbox, { filename: "index.html#remote-panel" });

  return {
    sandbox, body, restartBody, modal, hosts,
    toasts, fetches, confirms, clipboard, timers, domReady,
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

// ----------------------------------------- the session tooltip's placement --
//
// `index.html` places the session hover tooltip from JS, and nothing checked
// where it landed. The vertical axis had flipped between above and below since
// it was written; the horizontal axis had no constraint at all. `left` was set
// to the hovered row's own left edge, and the box — up to the stylesheet's cap
// wide — simply ran off the right of the window whenever the window was
// narrower than that cap plus the row's offset. A screenshot of an ~833px
// window is what found it, and neither pytest nor a substring check could have:
// every literal involved was still in the file, correctly spelled.
//
// Only the tooltip's own region runs, on the same terms as the panel above.

const TOOLTIP_NAMES = [
  "TOOLTIP_MARGIN", "TOOLTIP_GAP", "_resetTooltipSlot", "loadTail", "hideTail",
];

function tooltipSource() {
  const src = fs.readFileSync(INDEX_TEMPLATE, "utf8");
  const from = src.indexOf("var TOOLTIP_MARGIN=");
  if (from < 0) throw new Error("index.html no longer defines TOOLTIP_MARGIN");
  const to = src.indexOf("</script>", from);
  if (to < 0) throw new Error("the tooltip's <script> element is unterminated");
  const region = src.slice(from, to);
  for (const name of TOOLTIP_NAMES) {
    if (!region.includes(name)) {
      throw new Error(
        `the extracted region does not contain ${name}; the tooltip code has ` +
        "moved and this harness is measuring less of it than it claims to");
    }
  }
  return region;
}

// The stylesheet's own cap, read rather than repeated here. Keeping a box that
// wide inside a window narrower than it is the entire job of the code below, so
// a harness holding its own copy of the number could go on passing after the
// sheet changed and the arithmetic stopped matching what a browser lays out.
function cssTooltipMaxWidth() {
  const css = fs.readFileSync(STYLESHEET, "utf8");
  const rule = css.match(/\.session-tail-tooltip\s*\{([^}]*)\}/);
  if (!rule) throw new Error("style.css no longer has a .session-tail-tooltip rule");
  const cap = rule[1].match(/max-width:\s*(\d+)px/);
  if (!cap) throw new Error(".session-tail-tooltip no longer caps its max-width");
  return Number(cap[1]);
}

const CSS_TOOLTIP_MAX_WIDTH = cssTooltipMaxWidth();

// The floor the placement owes every window edge. Asserted as a floor rather
// than read out of the source: what was asked for is a margin of at least this,
// and a check that parsed `TOOLTIP_MARGIN` back out would agree with the page
// about any value it happened to hold, including zero.
const GUTTER = 4;

function px(v) {
  const n = /^(-?\d+(?:\.\d+)?)px$/.exec(String(v ?? ""));
  return n ? Number(n[1]) : null;
}

// The slot is filled by assigning the server's rendered partial to `innerHTML`
// — the opposite of the acp rail's createElement rule, so `El`, which arms that
// sink to throw, cannot stand in here and this section brings its own box. It
// models the three things the placement reads back out of layout and nothing
// else: offsetWidth, scrollHeight and getBoundingClientRect.
class Box {
  constructor(className) {
    this.className = className;
    this.style = {};
    this.dataset = {};
    this.childNodes = [];
    this.parentNode = null;
    this._natural = 0;   // width the content wants, before any cap applies
    this._content = 0;   // height the content wants, before any cap applies
  }
  append(child) { child.parentNode = this; this.childNodes.push(child); return child; }
  querySelector(sel) {
    const want = String(sel).replace(/^\./, "");
    for (const child of this.childNodes) {
      if (String(child.className).split(/\s+/).includes(want)) return child;
      const deep = child.querySelector(sel);
      if (deep) return deep;
    }
    return null;
  }
  closest(sel) {
    const want = String(sel).replace(/^\./, "");
    for (let node = this; node; node = node.parentNode) {
      if (String(node.className).split(/\s+/).includes(want)) return node;
    }
    return null;
  }
  // A browser reports 0 for both metrics on a `display:none` element. That is
  // not a detail — it is the trap the placement has to step around, because the
  // slot starts hidden and the old code measured it there and got zero. Model
  // it and a placement that measures too early fails the width checks below.
  get _hidden() {
    for (let node = this; node; node = node.parentNode) {
      if (node.style.display === "none") return true;
    }
    return false;
  }
  // Shrink-to-fit under a max-width: the natural width, capped by the
  // stylesheet and then by whatever inline max-width the placement set.
  // Deliberately not a layout engine — it does not grow the height back when
  // the width shrinks, so no check here may assert on a reflowed height.
  get offsetWidth() {
    if (this._hidden) return 0;
    const caps = [this._natural, CSS_TOOLTIP_MAX_WIDTH];
    const inline = px(this.style.maxWidth);
    if (inline !== null) caps.push(inline);
    return Math.min(...caps);
  }
  get scrollHeight() { return this._hidden ? 0 : this._content; }
  set innerHTML(html) {
    this.childNodes = [];
    if (!String(html).includes("session-tail-tooltip")) return;
    const tip = new Box("session-tail-tooltip");
    tip._natural = this._partialWidth;
    tip._content = this._partialHeight;
    this.append(tip);
  }
}

// Node's own scheduler, kept out of the sandbox so a check can let the page's
// fetch chain settle after firing the page's own deferred timer.
const settle = () => new Promise((resolve) => setImmediate(resolve));

async function hoverRow({ viewport, rowRect, naturalWidth, contentHeight }) {
  const row = new Box("session-row");
  row.dataset.sid = "3f9c";
  row.dataset.provider = "claude-code";
  row.dataset.cwd = "C:\\ws";
  const contentEl = row.append(new Box("session-content"));
  const slot = contentEl.append(new Box("session-tooltip-slot"));
  // `.session-tooltip-slot { display: none }` in the stylesheet: the slot the
  // page hands to the placement is a hidden one, every time.
  slot.style.display = "none";
  slot._partialWidth = naturalWidth;
  slot._partialHeight = contentHeight;
  contentEl.getBoundingClientRect = () => ({
    left: rowRect.left,
    right: rowRect.left + rowRect.width,
    top: rowRect.top,
    bottom: rowRect.top + rowRect.height,
    width: rowRect.width,
    height: rowRect.height,
  });

  const timers = [];
  const sandbox = {
    document: { documentElement: { clientWidth: viewport.width } },
    innerWidth: viewport.width,
    innerHeight: viewport.height,
    setTimeout: (fn) => { timers.push(fn); return timers.length; },
    clearTimeout: () => {},
    encodeURIComponent,
    fetch: () => Promise.resolve({
      ok: true,
      status: 200,
      text: () => Promise.resolve('<div class="session-tail-tooltip">tail</div>'),
    }),
    console: { log() {}, warn() {}, error() {} },
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(tooltipSource(), sandbox, { filename: "index.html#session-tooltip" });

  sandbox.loadTail(contentEl);
  // loadTail holds the fetch behind a hover delay; fire it, then let the
  // promise chain that places the box run to completion.
  for (const fire of timers.splice(0)) fire();
  await settleStaging();
  return { sandbox, contentEl, slot, tooltip: slot.querySelector(".session-tail-tooltip") };
}

check("a tooltip too wide for the space to its right slides left, not off-screen", async () => {
  // The reported window, near enough: the row starts 90px in and the stylesheet
  // would give the tooltip 800, which lands its right edge past 833.
  const view = 833;
  const { slot, tooltip } = await hoverRow({
    viewport: { width: view, height: 557 },
    rowRect: { left: 90, width: 700, top: 78, height: 82 },
    naturalWidth: 900,
    contentHeight: 300,
  });
  const left = px(slot.style.left);
  assert(left !== null, "the tooltip was never placed");
  assert(left >= GUTTER,
         `the tooltip's left edge sits ${left}px from the window, under the ` +
         `${GUTTER}px floor`);
  const gap = view - (left + tooltip.offsetWidth);
  assert(gap >= GUTTER,
         `the tooltip's right edge sits ${gap}px from the window edge, under ` +
         `the ${GUTTER}px floor — this is the reported overflow`);
  // Shifted, not shrunk. A placement that met the floor by narrowing the box
  // would pass the assertion above while throwing away width the window had.
  assertEqual(tooltip.offsetWidth, CSS_TOOLTIP_MAX_WIDTH,
              "the tooltip gave up width the window could have given it");
});

check("a window narrower than the tooltip's own cap keeps both side gutters", async () => {
  const view = 400;
  const { slot, tooltip } = await hoverRow({
    viewport: { width: view, height: 700 },
    rowRect: { left: 90, width: 260, top: 60, height: 40 },
    naturalWidth: 900,
    contentHeight: 300,
  });
  const left = px(slot.style.left);
  assertEqual(left, GUTTER,
              "the tooltip did not fall back to the left gutter on a window " +
              "too narrow to hold it at the row's own offset");
  assertEqual(view - (left + tooltip.offsetWidth), GUTTER,
              "the right gutter is not the one the left edge got");
});

check("a tooltip with room to spare stays aligned to the row it belongs to", async () => {
  const { slot, tooltip } = await hoverRow({
    viewport: { width: 1600, height: 900 },
    rowRect: { left: 90, width: 1200, top: 120, height: 60 },
    naturalWidth: 900,
    contentHeight: 300,
  });
  assertEqual(px(slot.style.left), 90,
              "the tooltip drifted off its row's left edge on a window with " +
              "room for it there");
  assertEqual(tooltip.style.maxWidth, "",
              "an inline max-width was set on a window wide enough for the " +
              "stylesheet's cap, which would beat that cap and let the " +
              "tooltip sprawl wider than the sheet allows");
});

check("a tooltip opening below its row keeps the gutter above the window's floor", async () => {
  const view = 600;
  const { slot, tooltip } = await hoverRow({
    viewport: { width: 1200, height: view },
    rowRect: { left: 40, width: 900, top: 80, height: 60 },
    naturalWidth: 700,
    contentHeight: 2000,   // taller than any space on offer
  });
  assertEqual(slot.style.transform, "none",
              "the tooltip flipped above a row with far more room below it");
  const bottom = px(slot.style.top) +
                 Math.min(tooltip.scrollHeight, px(tooltip.style.maxHeight));
  assert(view - bottom >= GUTTER,
         `the tooltip's bottom edge sits ${view - bottom}px from the window ` +
         `edge, under the ${GUTTER}px floor`);
});

check("a tooltip opening above its row keeps the gutter below the window's top", async () => {
  const { slot, tooltip } = await hoverRow({
    viewport: { width: 1200, height: 600 },
    rowRect: { left: 40, width: 900, top: 480, height: 60 },
    naturalWidth: 700,
    contentHeight: 2000,
  });
  assertEqual(slot.style.transform, "translateY(-100%)",
              "the tooltip opened downward into the smaller of the two spaces");
  // translateY(-100%) puts the box's own height above the `top` it was given.
  const top = px(slot.style.top) -
              Math.min(tooltip.scrollHeight, px(tooltip.style.maxHeight));
  assert(top >= GUTTER,
         `the tooltip's top edge sits ${top}px from the window edge, under ` +
         `the ${GUTTER}px floor`);
});

check("hiding a capped tooltip gives back the cap the narrow window imposed", async () => {
  const { sandbox, contentEl, slot, tooltip } = await hoverRow({
    viewport: { width: 400, height: 700 },
    rowRect: { left: 90, width: 260, top: 60, height: 40 },
    naturalWidth: 900,
    contentHeight: 300,
  });
  assertEqual(slot.style.visibility, "",
              "the tooltip was placed and then left invisible — it is hidden " +
              "only for the moment it is measured");
  assert(px(tooltip.style.maxWidth) !== null,
         "the narrow window put no cap on the tooltip at all");
  sandbox.hideTail(contentEl);
  assertEqual(slot.style.display, "none", "hiding left the tooltip on screen");
  assertEqual(slot.style.left, "", "hiding left the placement's own offset behind");
  assertEqual(tooltip.style.maxWidth, "",
              "hiding kept the narrow window's cap, so the next hover after " +
              "the window is widened opens a tooltip still squeezed to it");
});

// ---------------------------------------------- the agent bar + debug log --

function subagentsFrame(live, subagents) {
  return { type: "subagents", sessionId: live, payload: { subagents } };
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
      { sessionId: "sub-1", role: "explorer", task: "", status: "working",
        action: "reading", done: false, error: "", startedAt: now - 5 },
    ]));
    const panels = page.all("acpTranscript", ".acp-crew-panel");
    assertEqual(panels.length, 1, "a crew panel should appear in the transcript");
    const entries = panels[0].querySelectorAll(".acp-crew-entry");
    assertEqual(entries.length, 1, "one entry per sub-agent");
    const nameText = entries[0].querySelector(".acp-crew-name").textContent;
    assertEqual(nameText, "explorer", "entry should show the agent role");
    const actionText = entries[0].querySelector(".acp-crew-action").textContent;
    assertEqual(actionText, "reading", "entry should show the current action");
  });

check("crew panel entries are clickable and open the sub-agent panel", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-1", role: "explorer", task: "", status: "working",
      action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  const entries = page.all("acpTranscript", ".acp-crew-entry");
  assertEqual(entries.length, 1);
  entries[0].dispatch("click");
  assertEqual(page.el("acpSubPanel").hidden, false,
              "clicking a crew card should open the sub-agent panel");
});

check("crew panel stays visible while running but is removed after all done + next main event",
  (tpl) => {
    const { page, live } = connected(tpl);
    const now = Date.now() / 1000;
    page.deliver(subagentsFrame(live, [
      { sessionId: "sub-1", role: "explorer", task: "", status: "working",
        action: "", done: false, error: "", startedAt: now - 10 },
    ]));
    assertEqual(page.all("acpTranscript", ".acp-crew-panel").length, 1,
                "panel should be present while running");
    // All done
    page.deliver(subagentsFrame(live, [
      { sessionId: "sub-1", role: "explorer", task: "", status: "terminated",
        action: "", done: true, error: "", startedAt: now - 10 },
    ]));
    // Panel still present — waiting for the next main-session event
    assertEqual(page.all("acpTranscript", ".acp-crew-panel").length, 1,
                "panel should stay until next main event after all done");
    // Next main event: a chunk from the agent
    page.deliver({ type: "chunk", sessionId: live,
                   payload: { role: "agent", text: "finished" } });
    assertEqual(page.all("acpTranscript", ".acp-crew-panel").length, 0,
                "panel should be removed after all done + next main event");
  });

check("crew panel is removed when turn ends after all done", (tpl) => {
  const { page, live } = connected(tpl);
  const now = Date.now() / 1000;
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-1", role: "builder", task: "", status: "terminated",
      action: "", done: true, error: "", startedAt: now - 30 },
  ]));
  assertEqual(page.all("acpTranscript", ".acp-crew-panel").length, 1);
  // turn end is another main-session event that should dismiss the panel
  page.deliver({ type: "meta", sessionId: live,
                 payload: { turn: "end", stopReason: "end_turn" } });
  assertEqual(page.all("acpTranscript", ".acp-crew-panel").length, 0,
              "panel should be removed on turn end when all done");
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
      status: "working", action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  page.all("acpTranscript", ".acp-crew-entry")[0].dispatch("click");
  assertEqual(page.el("acpTranscript").hidden, true,
              "the main transcript should hide while a sub-agent is open");
  assertEqual(page.el("acpComposer").hidden, true,
              "the composer should hide — a sub-agent's conversation is read-only");
  assertEqual(page.el("acpSubPanel").hidden, false);
  page.openAt(1);
  const subs = page.socketAt(1).sent.filter((f) => f.type === "subscribe");
  assertEqual(subs.length, 1, "expected exactly one subscribe on the sub-agent socket");
  assertEqual(subs[0].sessionId, "sub-1");
});

check("the sub-agent panel renders its own chunk and tool_call frames", (tpl) => {
  const { page, live } = connected(tpl);
  page.deliver(subagentsFrame(live, [
    { sessionId: "sub-1", role: "explorer", task: "", status: "working",
      action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  page.all("acpTranscript", ".acp-crew-entry")[0].dispatch("click");
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
    { sessionId: "sub-1", role: "explorer", task: "", status: "working",
      action: "", done: false, error: "", startedAt: Date.now() / 1000 },
  ]));
  page.all("acpTranscript", ".acp-crew-entry")[0].dispatch("click");
  page.openAt(1);
  page.click("acpSubBack");
  assertEqual(page.el("acpSubPanel").hidden, true);
  assertEqual(page.el("acpTranscript").hidden, false);
});

check("reopening a sub-agent reuses the existing socket rather than a third one",
  (tpl) => {
    const { page, live } = connected(tpl);
    page.deliver(subagentsFrame(live, [
      { sessionId: "sub-1", role: "explorer", task: "", status: "working",
        action: "", done: false, error: "", startedAt: Date.now() / 1000 },
    ]));
    page.all("acpTranscript", ".acp-crew-entry")[0].dispatch("click");
    page.openAt(1);
    page.click("acpSubBack");
    page.all("acpTranscript", ".acp-crew-entry")[0].dispatch("click");
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
    { sessionId: "sub-1", role: "explorer", task: "", status: "working",
      action: "reading", done: false, error: "", startedAt: now - 5 },
  ]));
  page.all("acpTranscript", ".acp-crew-entry")[0].dispatch("click");
  page.openAt(1);
  // On the MAIN socket (index 0) — `page.deliver()` alone would now target the
  // sub-agent socket, since it always addresses whichever opened last.
  page.deliverTo(0, subagentsFrame(live, [
    { sessionId: "sub-1", role: "explorer", task: "", status: "done",
      action: "", done: true, error: "", startedAt: now - 5 },
  ]));
  assertEqual(page.el("acpSubPanel").hidden, false,
              "the panel should stay open across a crew update");
  assertEqual(page.el("acpSubStatus").textContent, "done");
});

check("a new session frame clears the crew panel and closes any open sub-agent panel",
  (tpl) => {
    const { page, live } = connected(tpl);
    page.deliver(subagentsFrame(live, [
      { sessionId: "sub-1", role: "explorer", task: "", status: "working",
        action: "", done: false, error: "", startedAt: Date.now() / 1000 },
    ]));
    page.all("acpTranscript", ".acp-crew-entry")[0].dispatch("click");
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
    assertEqual(page.el("acpTranscript").hidden, false);
    assertEqual(page.el("acpComposer").hidden, false);
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
               status: "started", command: "ls -la" },
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
               status: "started", command: "git status" },
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
               status: "started", command: "echo hi" },
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
               status: "started" },
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
  const call = { toolCallId: "tc-col-5", title: "shell", kind: "execute", status: "started" };
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
                 status: "started", command: "npm test" };
  page.deliver({ type: "tool_call", sessionId: live, payload: call });
  const transcript = page.el("acpTranscript");
  const statusEl = transcript.querySelector(".acp-tool-status");
  assertEqual(statusEl.textContent, "started", "fixture: initial status");
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
               status: "started", command: "make build" },
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
               status: "started", command: "grep -r foo ." },
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
                 status: "started" };
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

check("P3: group header format: N tool calls (name xCount) · status xCount", async (tpl) => {
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
  // Expected: "3 tool calls (shell ×2, read_file ×1) · completed ×3"
  assert(text.includes("3 tool calls"),
    "header should start with count — got: " + text);
  assert(text.includes("shell"),
    "header should contain 'shell' — got: " + text);
  assert(text.includes("\xd72"),
    "header should use \xd7 (multiplication sign) for counts — got: " + text);
  assert(text.includes("read_file"),
    "header should contain 'read_file' — got: " + text);
  assert(text.includes("\xb7"),
    "header should contain \xb7 (middle dot) separator — got: " + text);
  assert(text.includes("completed"),
    "header should include narrowed status — got: " + text);
});

check("P3: clicking group toggle reveals rows; individual rows start collapsed", async (tpl) => {
  const { page, live } = connected(tpl);
  await deliverTurn(page, live, [
    { toolCallId: "g4a", title: "shell", kind: "execute", status: "completed", command: "ls" },
    { toolCallId: "g4b", title: "shell", kind: "execute", status: "completed", command: "pwd" },
  ]);
  const transcript = page.el("acpTranscript");
  const toggle = transcript.querySelector(".acp-tool-group-toggle");
  assert(toggle !== null, "group toggle should exist");
  const body = transcript.querySelector(".acp-tool-group-body");
  toggle.dispatch("click");
  assertEqual(toggle.getAttribute("aria-expanded"), "true",
    "after click group should be expanded");
  assert(body.hidden === false, "group body should be visible after click");
  // Individual rows inside should start collapsed (Phase 2 toggle hidden)
  const innerRows = body.querySelectorAll(".acp-msg-tool");
  assert(innerRows.length >= 2, "group body should contain the individual rows");
  for (const row of innerRows) {
    const cmdWrap = row.querySelector(".acp-tool-cmd")
      ? row.querySelector(".acp-tool-cmd").parentNode : null;
    if (cmdWrap) {
      assert(cmdWrap.hidden === true,
        "individual rows inside group should start collapsed (command body hidden)");
    }
  }
});

check("P3: turn with 1 tool call: no group; row stays at transcript root", async (tpl) => {
  const { page, live } = connected(tpl);
  await deliverTurn(page, live, [
    { toolCallId: "g5a", title: "shell", kind: "execute", status: "completed", command: "ls" },
  ]);
  const transcript = page.el("acpTranscript");
  const groups = transcript.querySelectorAll(".acp-tool-group");
  assertEqual(groups.length, 0, "single call should not produce a group");
  const rootToolRows = transcript.childNodes.filter(
    (n) => n.className && String(n.className).includes("acp-msg-tool"));
  assertEqual(rootToolRows.length, 1, "single call should remain at transcript root");
});

check("P3: tool_call + prose + tool_call+tool_call: first stays individual; last two form group", async (tpl) => {
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
  assertEqual(groups.length, 1, "only the adjacent pair should form a group");
  const rootToolRows = transcript.childNodes.filter(
    (n) => n.className && String(n.className).includes("acp-msg-tool"));
  assertEqual(rootToolRows.length, 1,
    "the first call (non-adjacent) should remain at root");
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
               status: "started", command: "ls" } });
  page.deliver({ type: "tool_call", sessionId: live,
    payload: { toolCallId: "g8b", title: "shell", kind: "execute",
               status: "started", command: "pwd" } });
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
  // `in_progress` is not in TOOL_STATUS_LABEL, so the status tally should be
  // empty and the · separator should not appear in the toggle's text.
  const { page, live } = connected(tpl);
  await deliverTurn(page, live, [
    { toolCallId: "m3a", title: "shell", kind: "execute", status: "in_progress", command: "x" },
    { toolCallId: "m3b", title: "shell", kind: "execute", status: "in_progress", command: "y" },
  ]);
  const transcript = page.el("acpTranscript");
  const groups = transcript.querySelectorAll(".acp-tool-group");
  assertEqual(groups.length, 1, "two in_progress tool calls should still form a group");
  const toggle = groups[0].querySelector(".acp-tool-group-toggle");
  assert(toggle, "group has no toggle button");
  assert(!toggle.textContent.includes("in_progress"),
    "raw wire status 'in_progress' reached the toggle textContent — must be narrowed out");
  assert(!toggle.textContent.includes('\xb7'),
    "· separator appears even though the status tally is empty (all statuses unknown)");
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
               status: "started", command: "ls" } });
  // Switch session (triggers clearTranscript)
  const newSid = "sess-post-clear";
  page.deliver({ type: "session", sessionId: newSid,
    payload: { sessionId: newSid, cwd: "C:\\other", created: true,
               turnActive: false, contextPercent: null } });
  await page.settle();
  const transcript = page.el("acpTranscript");
  assertEqual(transcript.querySelectorAll(".acp-tool-group").length, 0,
    "no groups should exist after clearTranscript");
  // New single-call turn should not form a group (old toolGroup cleared)
  await deliverTurn(page, newSid, [
    { toolCallId: "g10b", title: "shell", kind: "execute", status: "completed", command: "new" },
  ]);
  assertEqual(transcript.querySelectorAll(".acp-tool-group").length, 0,
    "single call after clear should not form a group (toolGroup was properly reset)");
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
  page.click("acpQueue");
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
  page.click("acpQueue");
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
  page.click("acpQueue");
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
  page.click("acpQueue");
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
  page.click("acpSteer");
  const steers = page.socket().sent.filter((f) => f.type === "steer");
  assertEqual(steers.length, 1, "exactly one steer frame should be sent");
  assertEqual(steers[0].payload && steers[0].payload.message, "inject this",
    "steer payload.message should match typed text");
  assertEqual(page.el("acpPrompt").value, "",
    "textarea should be cleared after steer");
  assert(page.el("acpPrompt").disabled === true,
    "textarea should be disabled while awaiting steer_ack");
});

check("steer_ack re-enables controls and shows note", (tpl) => {
  const { page, live } = connected(tpl, { turnActive: true });
  page.type("steer text");
  page.el("acpPrompt").dispatch("input");
  page.click("acpSteer");
  page.deliver({ type: "steer_ack", sessionId: live, payload: { queued: true } });
  assert(page.el("acpPrompt").disabled === false,
    "textarea should be re-enabled after steer_ack");
  assert(page.el("acpSteer").disabled === false,
    "steer button should be re-enabled after steer_ack");
  assert(page.el("acpTranscript").textContent.includes("Steer sent"),
    "transcript should contain 'Steer sent' note");
});

check("steer_ack queued:false shows error note", (tpl) => {
  const { page, live } = connected(tpl, { turnActive: true });
  const originalText = "bad steer";
  page.type(originalText);
  page.el("acpPrompt").dispatch("input");
  page.click("acpSteer");
  page.deliver({ type: "steer_ack", sessionId: live, payload: { queued: false } });
  assert(page.el("acpTranscript").textContent.includes("not accepted"),
    "transcript should contain rejection note when queued:false");
  assertEqual(page.el("acpPrompt").value, originalText,
    "steer_ack queued:false should restore the textarea text");
});

check("error frame during steer restores textarea text", (tpl) => {
  const { page, live } = connected(tpl, { turnActive: true });
  page.type("important steer");
  page.el("acpPrompt").dispatch("input");
  page.click("acpSteer");
  assert(page.el("acpPrompt").disabled === true, "fixture: textarea disabled after steer click");
  page.deliver({
    type: "error", sessionId: live,
    payload: { code: "agent_error", message: "steer failed" },
  });
  assertEqual(page.el("acpPrompt").value, "important steer",
    "error frame should restore steer text to textarea");
  assert(page.el("acpPrompt").disabled === false,
    "textarea should be re-enabled after error frame");
});

check("queuedPrompt and _steerPending cleared on releaseSession", (tpl) => {
  const { page, live } = connected(tpl, { turnActive: true });
  // Queue a prompt
  page.type("queue me");
  page.el("acpPrompt").dispatch("input");
  page.click("acpQueue");
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

// Fix 7: steer textarea re-enabled on WS close during pending steer
check("steer textarea re-enabled on ws close during pending steer", (tpl) => {
  const { page, live } = connected(tpl, { turnActive: true });
  page.type("steer text");
  page.el("acpPrompt").dispatch("input");
  page.click("acpSteer");
  assert(page.el("acpPrompt").disabled === true,
    "fixture: textarea should be disabled after clicking Steer");
  // Simulate WS close mid-steer
  page.socket().onclose({ code: 1006, reason: "" });
  assertEqual(page.el("acpPrompt").disabled, false,
    "textarea should be re-enabled when WS closes during a pending steer");
  assertEqual(page.el("acpSteer").disabled, false,
    "steer button should be re-enabled when WS closes during a pending steer");
  // Textarea text should be restored from _steerPending
  assertEqual(page.el("acpPrompt").value, "steer text",
    "textarea text should be restored from _steerPending on WS close");
});

// Fix 7b: steer controls re-enabled on session_closed (releaseSession) while steer pending
check("steer controls re-enabled on session release during pending steer", (tpl) => {
  const { page, live } = connected(tpl, { turnActive: true });
  page.type("steer before close");
  page.el("acpPrompt").dispatch("input");
  page.click("acpSteer");
  assert(page.el("acpPrompt").disabled === true,
    "fixture: textarea disabled after steer click");
  // Release session via session_closed
  page.deliver({
    type: "session_closed", sessionId: live,
    payload: { message: "closed" },
  });
  assertEqual(page.el("acpPrompt").disabled, false,
    "textarea should be re-enabled after session release with steer pending");
});

// Fix 13: session-change guard — queue with sessionId=A, change to B, turn:end with A, no prompt sent
check("queued prompt not sent when sessionId changed before turn end", (tpl) => {
  const { page, live } = connected(tpl, { turnActive: true });
  page.type("queue this");
  page.el("acpPrompt").dispatch("input");
  page.click("acpQueue");
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

// -------------------------------------------------------------------- main --

const template = process.argv[2]
  ? path.resolve(process.argv[2])
  : DEFAULT_TEMPLATE;

console.log(`browser-side behavioural harness — ${template}\n`);
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
