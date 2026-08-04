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
  // this string and is not being counted. The served `/acp` therefore has two
  // script elements, not one; the policy still holds because base.html applies
  // the same nonce conditionally, and what is measured here is that this
  // template contributes exactly one inline script and no external one.
  const scripts = [...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)];
  if (scripts.length !== 1) {
    throw new Error(
      `expected exactly one inline <script> in acp.html's content block, ` +
      `found ${scripts.length}`);
  }
  const scriptAttrs = scripts[0][1];
  const scriptBody = scripts[0][2];
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

  const sandbox = {
    document: {
      createElement: (tag) => new El(tag),
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
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
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
    open() {
      const s = page.socket();
      s.readyState = FakeWs.OPEN;
      if (!s.onopen) throw new Error("the page set no onopen handler");
      s.onopen();
    },
    deliver(frame) {
      page.socket().onmessage({ data: JSON.stringify(frame) });
    },
    click(id) { page.el(id).dispatch("click"); },
    type(text) { page.el("acpPrompt").value = text; },
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
function connected(templatePath, { sid = "", turnActive = false } = {}) {
  const page = loadPage(templatePath, { sid });
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

// Every element name the page is allowed to build from a token tree. Anything
// else in the bubble is a tag name that came off the wire.
const MD_TAGS = new Set([
  "DIV", "SPAN", "P", "H1", "H2", "H3", "H4", "H5", "H6",
  "UL", "OL", "LI", "PRE", "CODE", "STRONG", "EM", "BR", "A",
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

check("the rail asks for ten workspaces and three sessions each", async (tpl) => {
  const page = await railed(tpl);
  const calls = page.listingCalls();
  assertEqual(calls.length, 1, "the rail made the wrong number of listing requests");
  const { params, init } = calls[0];
  assertEqual(params.group_size, "10",
              "D16 shows ten workspaces; the rail asked for a different page");
  assertEqual(params.session_size, "3",
              "D16 shows three sessions a workspace; the rail asked for a different page");
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
  assertEqual(page.railRows().length, 30,
              "ten groups of three is thirty rows; the rail drew a different shape");
  const first = page.railGroups()[0];
  const head = first.querySelector(".acp-rail-group-head").textContent;
  assert(head.includes("ws-0"), `the group is not named after its workspace: ${head}`);
  assert(head.includes("3 of 5"),
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
  assertEqual(page.railRows().length, 3,
              "matching the workspace should keep all of its loaded rows");

  box.value = "no-such-thing";
  box.dispatch("input");
  assertEqual(page.railRows().length, 0, "the filter matched something it should not");
  assert(page.one("acpRailGroups", ".acp-rail-empty"),
         "an empty result left the rail silently blank, which reads as a broken page");

  box.value = "";
  box.dispatch("input");
  assertEqual(page.railRows().length, 30, "clearing the filter did not restore the rows");
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
  const page = await railed(tpl);
  const group = page.railGroups()[0];
  const more = group.querySelector(".acp-rail-group-more");
  assert(more, "a workspace with 5 sessions showing 3 offered no way to see the rest");
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
  assertEqual(rows.length, 5, "the workspace's own show-more did not extend it");
  assertEqual(page.railGroups()[1].querySelectorAll(".acp-rail-row").length, 3,
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
  rows[4].dispatch("click");
  const subs = page.sentOf("subscribe");
  assertEqual(subs.length, 1, "the row did not subscribe to its session");
  assertEqual(subs[0].sessionId, "sess-w1-s1", "the rail opened the wrong session");
  assert(page.el("acpSid").textContent.includes("sess-w1-s1"),
         "the header does not name the session the rail just opened");
  assertEqual(page.urls[page.urls.length - 1], "/acp?sid=sess-w1-s1",
              "the id never reached the URL, so a reload strands the session");
  // A second click on the row already open must not re-subscribe: the server
  // answers every subscribe with a `session` frame that clears the transcript.
  rows[4].dispatch("click");
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
  assertEqual(page.railRows()[0].title, want,
              "the rail drew the store's UTC digits instead of the reader's local time");
  assert(when[0] && when[0].length < want.length,
         `the row still spends the full ${want.length} characters on a timestamp: ${when[0]}`);

  // Not "renders something harmless" — `new Date(null)` is the epoch, so the
  // failure this guards is a confident `1969-12-31` that reads as a real date.
  assertEqual(when[1], "",
              "an absent updated_at drew a timestamp; new Date(null) is 1969-12-31");
  assertEqual(when[2], "not a timestamp",
              "a record the rail cannot read must be shown as it came, not as Invalid Date");
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
});

check("a day shows three rows and offers exactly the rest", async (tpl) => {
  const store = dayStore();
  for (let i = 0; i < 4; i++) {
    store[0].sessions.push({
      id: `extra-${i}`, title: `extra ${i}`, updated_at: isoAtLocal(0, 12),
      availability: "available" });
  }
  const page = await railed(tpl, {
    store, stored: { pa_acp_group: "date" } });
  const first = page.railGroups()[0];
  assertEqual(first.querySelectorAll(".acp-rail-row").length, 3,
              "the day drew more than the three rows a group shows");
  const more = first.querySelector(".acp-rail-group-more");
  // Six sessions fall on today, three are drawn: the promise is exact because
  // these rows are already loaded, unlike the grouped mode's button which
  // promises what the next request will bring.
  assertEqual(more.textContent, "Show 3 more",
              `the button misstates what it will reveal: ${more.textContent}`);
  more.dispatch("click");
  assertEqual(page.railGroups()[0].querySelectorAll(".acp-rail-row").length, 6,
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
  assertEqual(options.length, 2, "the settings popup did not offer both modes");
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

check("the back link is a link on loopback and inert from a remote peer", (tpl) => {
  // `/` is not on `_REMOTE_ALLOWED_PATHS` and never will be (SC-4), so from a
  // phone the old `<a href="/">` was a control whose only outcome was a 403
  // with no way back.
  const local = loadPage(tpl, { local: true });
  const localBack = local.markup.match(/<a\b[^>]*class="acp-back"[^>]*>/);
  assert(localBack, "the dashboard link is gone for a loopback viewer too");
  assert(/href="\/"/.test(localBack[0]),
         `the loopback link no longer reaches the dashboard: ${localBack[0]}`);

  const remote = loadPage(tpl, { local: false });
  assertEqual(remote.markup.match(/<a\b[^>]*class="acp-back"/g), null,
              "a remote viewer is still handed a link to a loopback-only page");
  assert(/class="acp-back acp-back-local-only"/.test(remote.markup),
         "the remote page dropped the product name entirely rather than " +
         "rendering it as text");
  assert(!/href="\/"/.test(remote.markup),
         "something else on the remote page still points at the dashboard");
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
  const store = fakeStore({ workspaces: 12, sessions: 5 });
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
          { cwd: "C:\\work\\ws-0", name: "ws-0", total: 5, session_page: 1,
            has_more: true, sessions: store[0].sessions.slice(0, 3) },
          { cwd: "C:\\work\\ws-11", name: "ws-11", total: 5, session_page: 1,
            has_more: true, sessions: store[11].sessions.slice(0, 3) },
        ],
        group_page: 2, group_total: 12, has_more: false,
      } };
    },
  });

  // Page into ws-0 first, so the merge has state that must survive it.
  page.railGroups()[0].querySelector(".acp-rail-group-more").dispatch("click");
  await page.settle();
  assertEqual(page.railGroups()[0].querySelectorAll(".acp-rail-row").length, 5,
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
  assertEqual(page.railRows().length, 35,
              "the repeat's rows were appended beside the ones already drawn");
  assertEqual(page.railGroups()[0].querySelectorAll(".acp-rail-row").length, 5,
              "the merge rewound ws-0 to the three rows the repeat carried, " +
              "losing the page the user had already asked for");
  assert(!page.railGroups()[0].querySelector(".acp-rail-group-more"),
         "the merge took the repeat's has_more and re-offered rows already drawn");
  assert(names.includes("ws-11"),
         "the workspace that was genuinely new on the second page never arrived");
});

check("a second show-more with nothing settled in between is dropped, not raced",
      async (tpl) => {
  const page = await railed(tpl);
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
             .textContent.includes("3 of 5"),
         "clearing the filter did not restore the loaded-of-total count");
});

check("a re-render puts keyboard focus back where the user left it", async (tpl) => {
  const page = await railed(tpl);

  // A workspace's own show-more: three sessions become five, which is all of
  // them, so the button the user pressed does not exist after the rebuild.
  const more = page.railGroups()[0].querySelector(".acp-rail-group-more");
  more.focus();
  more.dispatch("click");
  await page.settle();
  let now = page.focused();
  assert(now, "the rebuild dropped focus to the document body, throwing a keyboard " +
              "or screen-reader user out of the rail mid-task — the same population " +
              "the locked row's `disabled` exists for");
  assertEqual(now.dataset.sid, "sess-w0-s4",
              "focus did not land on the rows the press revealed");

  // Row selection, which re-renders to move the `current` class.
  const sid = page.railRows()[7].dataset.sid;
  page.railRows()[7].focus();
  page.railRows()[7].dispatch("click");
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
  const page = await railed(tpl, { store: fakeStore({ workspaces: 1, sessions: 5 }) });
  assertEqual(page.railRows().length, 3, "the fixture did not page as expected");
  for (let i = 0; i < 3; i++) {
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
  // the overflow rather than removing it. The back link is the designated one.
  const back = body(".acp-back");
  assert(/min-width:\s*0/.test(back) && /text-overflow:\s*ellipsis/.test(back),
         "nothing in the topbar is allowed to give way, so pinning the pill only " +
         "moves the 44 px overflow onto whichever item is last in the line box");
  assert(/flex-shrink:\s*(?!0\b)[1-9]/.test(back),
         "the back link shrinks only in proportion to its width, so the cluster " +
         "holding the pill — the widest item — gives up the most, which is the " +
         "opposite of the intended order");
  assert(/min-width:\s*\d+px/.test(body(".acp-context-track")),
         "the context meter's track has no floor, so it collapses to nothing " +
         "before the back link has finished giving way");
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
  assert(!hidden.includes(".acp-back"),
         "`.acp-back` is hidden outright, and *both* arms of the template carry " +
         "that class — so a loopback viewer who merely narrowed a desktop window " +
         "below 768 px loses the only link back to the dashboard. The width is " +
         "not the viewer; `local` is, and the template already computes it");
  // The positive control. Without it this check also passes against a sheet
  // that hides nothing at all — a different regression, in which the remote
  // arm's plain-text span keeps the space the 390 px topbar reclaimed from it.
  assert(hidden.includes(".acp-back-local-only"),
         "nothing hides the remote arm at narrow widths, so the tightest row on " +
         "the page keeps a control that resolves to nothing for that viewer");
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
