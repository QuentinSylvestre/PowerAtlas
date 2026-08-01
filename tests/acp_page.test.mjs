// Behavioural coverage for src/power_atlas/templates/acp.html.
//
//   node tests/acp_page.test.mjs                    # the committed template
//   node tests/acp_page.test.mjs <path-to-acp.html> # any other copy of it
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
// after Jinja has substituted it. `render()` handles the subset of Jinja this
// template uses and refuses anything left unrendered, so a new construct fails
// loudly instead of reaching the page as literal `{{ ... }}`.

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
  // `{% extends %}` / `{% block %}` carry no content this page's script reads;
  // the block body is the whole file.
  out = out.replace(/\{%[^%]*%\}/g, "");
  const lookup = (name) => {
    if (!(name in ctx)) throw new Error(`template reads an unknown variable: ${name}`);
    return ctx[name];
  };
  out = out.replace(/\{\{\s*(\w+)\s*\|\s*tojson\s*\}\}/g,
                    (_m, name) => JSON.stringify(lookup(name)));
  out = out.replace(/\{\{\s*(\w+)\s*\}\}/g, (_m, name) => String(lookup(name)));
  const leftover = out.match(/\{\{[^}]*\}\}|\{%[^%]*%\}/);
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
        updated_at: `2026-07-${String(10 + (s % 20)).padStart(2, "0")}T09:${String(s).padStart(2, "0")}:00`,
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

function clampSize(raw, fallback, ceiling) {
  const n = Number(raw === undefined || raw === "" ? fallback : raw);
  return Math.max(1, Math.min(Number.isFinite(n) ? n : fallback, ceiling));
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
        sessions: w.sessions.slice(from, from + sessionSize),
      };
    }),
    group_page: single ? 1 : groupPage,
    group_total: single ? matched.length : store.length,
    has_more: single ? false : start + groupSize < store.length,
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

  const byId = new Map();
  for (const m of markup.matchAll(/\bid="([^"]+)"/g)) byId.set(m[1], new El("div"));
  ACTIVE = null;

  const sockets = [];
  const urls = [];
  const fetches = [];
  const store = opts.store ?? fakeStore();
  const page = { html, markup, scriptAttrs, scriptBody, sockets, urls, fetches,
                 store, reloaded: false };

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
    const body = override && "body" in override
      ? override.body
      : (url.startsWith("/api/acp/sessions") ? serveListing(store, params) : {});
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

  const sandbox = {
    document: {
      createElement: (tag) => new El(tag),
      getElementById: (id) => byId.get(id) ?? null,
      write: () => HTML_SINK("document.write"),
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
    listingCalls() {
      return page.fetches.filter((f) => f.url.startsWith("/api/acp/sessions"));
    },
    // Null is this harness's <body>: nothing in the rail holds focus.
    focused() { return ACTIVE; },
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

check("the availability indicator renders all three states", async (tpl) => {
  const store = fakeStore({ workspaces: 3, sessions: 3 });
  store[0].sessions[0].availability = "available";
  store[0].sessions[1].availability = "held";
  store[0].sessions[2].availability = "locked";
  // Off the wire and into a class name and a data attribute — both attribute
  // sinks, and this page's rule is that nothing payload-derived reaches one.
  //
  // The first of these is an *own-property* miss and passes any lookup. The
  // other three are the reason the map has to be prototype-less: on an object
  // literal every `Object.prototype` key is a hit, so `map[value] || 'available'`
  // answers with the inherited value and never reaches the default. Measured on
  // the literal: "constructor" puts `acp-rail-row-function Object() { [native
  // code] }` into className and dataset.availability and makes the indicator's
  // aria-label the literal string "undefined"; "__proto__" gives
  // `acp-rail-row-[object Object]`; "toString" the same shape.
  store[1].sessions[0].availability = 'locked" onload=x';
  store[1].sessions[1].availability = "constructor";
  store[1].sessions[2].availability = "__proto__";
  store[2].sessions[0].availability = "toString";
  const page = await railed(tpl, { store });

  const rows = page.railRows();
  assertEqual(rows.map((r) => r.dataset.availability).slice(0, 3).join(","),
              "available,held,locked",
              "the three states did not survive the trip to the row");
  for (const [i, want] of [[0, "available"], [1, "held"], [2, "locked"]]) {
    assert(String(rows[i].className).includes(`acp-rail-row-${want}`),
           `row ${i} carries no ${want} class: ${rows[i].className}`);
    const dot = rows[i].querySelector(".acp-avail");
    assert(dot, `row ${i} has no availability indicator at all`);
    assert(String(dot.className).includes(`acp-avail-${want}`),
           `the indicator is not distinguishable for ${want}: ${dot.className}`);
    assert(dot.getAttribute("aria-label"),
           `the ${want} indicator has no accessible name, and it carries no text`);
  }
  for (const [i, sent] of [[3, 'locked" onload=x'], [4, "constructor"],
                           [5, "__proto__"], [6, "toString"]]) {
    assertEqual(rows[i].dataset.availability, "available",
                `the state ${JSON.stringify(sent)} was passed through rather ` +
                "than narrowed to one of the three literals");
    assert(/^acp-rail-row acp-rail-row-(available|held|locked)$/.test(
             String(rows[i].className)),
           `${JSON.stringify(sent)} reached a class name: ${rows[i].className}`);
    assertEqual(rows[i].querySelector(".acp-avail").getAttribute("aria-label"),
                "available",
                `${JSON.stringify(sent)} left the indicator without a real ` +
                "accessible name, and it carries no text of its own");
  }
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

// -------------------------------------------------------------------- main --

const template = process.argv[2]
  ? path.resolve(process.argv[2])
  : DEFAULT_TEMPLATE;

console.log(`acp.html behavioural harness — ${template}\n`);
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
