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
// 5 of the 15 checks below fail against `e8cb4df`, the commit those fixes
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
    this._text = "";
    this._listeners = Object.create(null);
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
  querySelector(sel) {
    if (!sel.startsWith(".")) {
      throw new Error(`the harness implements class selectors only, got ${sel}`);
    }
    const want = sel.slice(1);
    const walk = (node) => {
      for (const child of node.childNodes) {
        if (String(child.className).split(/\s+/).includes(want)) return child;
        const hit = walk(child);
        if (hit) return hit;
      }
      return null;
    };
    return walk(this);
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
  const page = { html, markup, scriptAttrs, scriptBody, sockets, urls, reloaded: false };

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
    fetch: () => Promise.resolve({ ok: true }),
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
  });
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

check("Send with no session sends nothing and keeps the text", (tpl) => {
  const page = loadPage(tpl);
  page.open();
  page.type("typed before there was anywhere to send it");
  page.click("acpSend");
  assertEqual(page.sentOf("prompt").length, 0, "a prompt was sent with no session");
  assertEqual(page.el("acpPrompt").value, "typed before there was anywhere to send it",
              "the text was cleared even though nothing was sent");
});

// -------------------------------------------------------------------- main --

const template = process.argv[2]
  ? path.resolve(process.argv[2])
  : DEFAULT_TEMPLATE;

console.log(`acp.html behavioural harness — ${template}\n`);
let failed = 0;
for (const { name, fn } of checks) {
  try {
    fn(template);
    console.log(`  PASS  ${name}`);
  } catch (err) {
    failed += 1;
    console.log(`  FAIL  ${name}`);
    console.log(`        ${String(err && err.message || err).split("\n").join("\n        ")}`);
  }
}
console.log(`\n${checks.length - failed} passed, ${failed} failed of ${checks.length}`);
process.exit(failed ? 1 : 0);
