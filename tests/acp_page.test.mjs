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
  // `{% extends %}` / `{% block %}` carry no content this page's script reads;
  // the block body is the whole file.
  let out = src.replace(/\{%[^%]*%\}/g, "");
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
  addEventListener(type, fn) {
    (this._listeners[type] = this._listeners[type] || []).push(fn);
  }
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
    out.push({ cwd: `C:\\work\\ws-${w}`, name: `ws-${w}`, sessions: rows });
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

function serveListing(store, params) {
  const groupSize = Number(params.group_size || 10);
  const groupPage = Number(params.group_page || 1);
  const sessionSize = Number(params.session_size || 3);
  const sessionPage = Number(params.session_page || 1);
  const single = Boolean(params.cwd);
  const matched = single ? store.filter((w) => w.cwd === params.cwd) : store;
  const start = single ? 0 : (groupPage - 1) * groupSize;
  const page = single ? matched : matched.slice(start, start + groupSize);
  return {
    groups: page.map((w) => {
      const from = (sessionPage - 1) * sessionSize;
      return {
        cwd: w.cwd,
        name: w.name,
        total: w.sessions.length,
        session_page: sessionPage,
        has_more: from + sessionSize < w.sessions.length,
        sessions: w.sessions.slice(from, from + sessionSize),
      };
    }),
    group_page: groupPage,
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
  });

  const scripts = [...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)];
  if (scripts.length !== 1) {
    throw new Error(`expected exactly one <script>, found ${scripts.length}`);
  }
  const scriptAttrs = scripts[0][1];
  const scriptBody = scripts[0][2];
  const markup = html.replace(/<script[\s\S]*?<\/script>/g, "");

  const byId = new Map();
  for (const m of markup.matchAll(/\bid="([^"]+)"/g)) byId.set(m[1], new El("div"));

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

check("exactly one script tag, carrying the CSP nonce", (tpl) => {
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
  const store = fakeStore({ workspaces: 2, sessions: 3 });
  store[0].sessions[0].availability = "available";
  store[0].sessions[1].availability = "held";
  store[0].sessions[2].availability = "locked";
  // Off the wire and into a class name and a data attribute — both attribute
  // sinks, and this page's rule is that nothing payload-derived reaches one.
  store[1].sessions[0].availability = 'locked" onload=x';
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
  assertEqual(rows[3].dataset.availability, "available",
              "an unrecognised state was passed through rather than narrowed");
  assert(!/onload|"/.test(String(rows[3].className)),
         `an agent-reachable string reached a class name: ${rows[3].className}`);
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
