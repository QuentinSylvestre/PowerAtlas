# ACP Permission Profile and Loopback Credential

> **Date**: 2026-09-21
> **Status**: Exploring  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Scope**: Give ACP sessions a configurable permission posture through a PowerAtlas-generated
> kiro-cli agent profile, and require a credential on every loopback HTTP/WebSocket route.

---

## Intent

### Problem statement & desired outcomes

Two measured facts compose into arbitrary code execution as the user, for any process that can open a
socket to the loopback port.

**The ungated half.** An ACP session PowerAtlas creates runs under the `kiro_default` agent, and this
machine's `~/.kiro/settings/permissions.yaml` carries `rules: [{capability: all, effect: allow}]`. A
probe measured shell, read, write and an out-of-cwd absolute-path write across five sessions with
`session/request_permission` raised zero times. Re-confirmed 2026-09-21: an agent carrying **no**
`permissions:` key at all raises zero prompts and the shell command runs, because a blank section
inherits the user-scope allow-all rather than falling back to kiro-cli's documented defaults. "Leave
it blank" and "allow all" are the same state.

**The unauthenticated half.** The loopback HTTP API has no authentication. `same_origin_guard`
(`web.py:771`) checks `Host` against a loopback allowlist on every request and `Origin`/`Referer` on
POSTs; that is DNS-rebinding and CSRF defence for browsers, and a local script sets both headers in
one line. `RemoteAccessGuard` (`web.py:1204`) acts only on non-loopback peers. The `_ACP_TOKEN` that
guards `/ws/acp` (`web.py:1269`) is injected into pages any local process can fetch, so the chain is:
`GET /` then read `ACP_TOKEN` from the HTML, then open `/ws/acp?t=...`, then create a session, prompt
it, and answer its own permission requests.

**Desired outcome.** Interactive ACP sessions can be configured to ask before acting, without changing
the posture of interactive terminal kiro-cli sessions; and the local API refuses callers that are not
PowerAtlas's own UI. Neither half alone closes the exposure: a gated agent behind an open API still
lets a rogue process answer its own prompts, and a credentialed API still runs everything it is asked.

### Success criteria

- **SC-1 — Permission posture is a user setting.** Two states: **off** = allow-all (today's
  behaviour, the default) and **on** = explicit rules reproducing kiro-cli's documented default
  posture. Both states additionally carry an always-on **deny floor** (never-okay destructive shell
  patterns and sensitive-path writes), expressed with kiro-cli's `deny` effect, which beats both `ask`
  and `allow` in the scope merge.
- **SC-2 — PowerAtlas owns and generates the derived agent.** It writes
  `~/.kiro/agents/poweratlas-acp.md`, derived at write time from a configurable **base agent**
  (default `kiro_default`) by injecting a `permissions:` key into that file's frontmatter. The base is
  named, never duplicated, so the derived agent inherits whatever `resources:`, MCP servers and model
  pin the base carries. Regenerated at startup and on settings change.
- **SC-3 — The picker's Default entry resolves to the derived agent** when the setting is on, and to
  the base agent when off. `_VALID_TASK_MODES` (`acp.py:691`) accepts the derived name. The seven
  vendor task modes are untouched and keep their manufacturer-encoded posture.
- **SC-4 — A permission prompt is answerable on its merits.** The `permission_request` frame carries
  the full (unclamped) title plus `params._meta.kiro.consent` — `capability`, `resource`,
  `matchedRule`, `scope`, `source` — so a write prompt names the file rather than reading `Write File`.
  `MAX_PERMISSION_TITLE_CHARS` reverts to governing only the notification body it was sized for.
- **SC-5 — Every loopback route requires a credential**, default-deny, with an exempt set of the
  code-exchange route and `/static`. This covers `GET /` (where the token is scraped), `/acp`,
  `/partials/*` and `/api/*` alike.
- **SC-6 — Browsers are credentialed by a one-time code.** Each door that opens the UI mints a fresh
  code into the URL, exchanged for an HttpOnly cookie. The tray gains **Copy login link** for
  bookmarks, a second browser, and any deliberate re-entry. A browser arriving with no cookie is told
  to open PowerAtlas from the tray; it can never self-serve, because a rogue local process is
  indistinguishable from a typed URL at the HTTP layer.
- **SC-7 — `_ACP_TOKEN` is deleted**, along with `_acp_token_ok`, both template injections, and
  `acp.html`'s stale-token/reload branch (`acp.html:5766-5799`), which exists only because the token
  rotates per launch.
- **SC-8 — Generation failure is visible.** If the base agent is missing, unparseable, or the write to
  `~/.kiro/agents/` is refused, the session still starts under the base agent, and the **settings
  panel** reports on-but-not-in-effect. No toast, no per-session badge.

### Scope boundaries & non-goals

**In scope**: ACP sessions PowerAtlas creates; the loopback HTTP and WebSocket surface; the dashboard's
own transcript composer, which also injects `ACP_TOKEN` (`index.html:701`) and therefore moves to the
cookie with everything else.

**Out of scope, deliberately**:

- **The unattended rule engine.** Deciding `session/request_permission` by rule when nobody is
  watching — auto-approve reads, deny shell and write unless a pattern allows, bounded wait then
  cancel and mark needs-attention. This item does **not** unblock the Automation & Workflows section;
  an `ask` rule with nobody watching is a 30-minute silence timeout, not a policy. The roadmap's
  "gates the entire Automation & Workflows section" line must be corrected to point at that follow-on.
- **The lean dispatch agent.** The roadmap folds it into this item; that folding is wrong under
  interactive-only scope, because an interactive session wants full governance loaded. It belongs to
  the unattended follow-on, where a narrowed `resources:` list is the point.
- **The terminal launcher.** `launcher.py:112` spawns `chat --agent-engine v3 --trust-tools *`. The
  two spawn paths have genuinely different postures and the terminal one stays wide open. This is the
  constraint that makes the whole design work: the base agent is untouched, so interactive kiro-cli
  keeps its permissive posture.
- **The `/partials/launchers` credential leak** (filed separately, see Open items).
- **The remote/NetBird surface and the TLS decision.** Unchanged.

---

## Exploration Discovery

<!-- Transient: /qplan folds these into the planning sections and removes this section. -->

Step 1.5 dispatched the **code-tracing trio** (directed / subsystem / mutation-finder) on Sonnet 5 —
in-scope files were predominantly `.py` and Jinja templates, with the agent-playbook deploy surface as
a named minority covered inside the directed brief. Model override recorded per
`shared/AGENTS.md` § Continuous Improvement: default subagent model overridden to `sonnet` at the
user's standing preference from the preceding session.

### Existing patterns & constraints

**Measured this session (kiro-cli 2.22.0, KAS 0.66.0).** Five probes, all with snapshot-and-restore on
`~/.kiro`; restoration verified by hash each time.

- **P1 — a user-scoped agent registers as a mode.** A file dropped in `~/.kiro/agents/` appears in
  `configOptions[id=mode].options` as `source=global origin=user`, alongside the seven `bundled`
  vendor modes. The `permissions:` block loaded cleanly, independently retiring the topic-memory note
  that an inline block broke agent loading during the v3 migration. `_meta.kiro` on the permission
  frame also reports `modeId_in_effect`, which is the honest verification signal `modeId` alone cannot
  give — an unrecognised value coerces to `vibe` silently.
- **P2 — the agent binds at `session/new` and never re-binds.** A fresh process loading an existing
  session while sending `modeId: kiro_default` got `modeId_in_effect: pa-probe-user`, and the
  permission still fired with `scope: agent`, `source: agent-profile`. So `acp.py:4751`'s unconditional
  `_build_kas_session_params()` on the load path is **inert** — a hygiene defect, not a security hole,
  and Half A needs no per-session mode store surviving restart. *Measurement limit*: not tested across
  a kiro-cli upgrade.
- **P3 — "Always deny" persists nowhere.** After selecting `always-reject`, the user `permissions.yaml`,
  both agent files and the whole `workspace-roots` set were byte-identical. The agent named the
  mechanism: "denied by a session-level permission rule". So nothing but PowerAtlas writes the derived
  agent file. *Measurement limit*: always-**allow** was not tested.
- **P4 — the useful half of the permission payload is discarded.** `toolCall` carries only
  `{status, title, toolCallId}`. Shell puts the literal command in `title`; **write puts `"Write File"`
  and no path**. The path lives in `params._meta.kiro.consent.resource`, alongside `capability`,
  `matchedRule`, `scope` and `source`. `acp.py:4552` rebuilds the frame as `{"title": title}` and drops
  `_meta` entirely.
- **P5 — a blank permissions section equals allow-all.** An agent with no `permissions:` key raised 0
  prompts and the shell command ran, with `modeId_in_effect` confirming the agent had loaded. This is
  why the rules must be written explicitly rather than omitted.

**Codebase constraints.**

- `acp.py` imports exactly two intra-package names (`from .config import CONFIG_DIR`,
  `from .launcher import _SESSION_ID_RE`) and takes every other coupling through a `web.py`-injected
  hook. Hook contract (`acp.py:870-885`, `909-912`): a plain synchronous callable, `None` is a no-op,
  a raise is caught and logged, and the call is **not** offloaded to a thread — a blocking hook body
  stalls the event loop. Any new coupling follows this shape.
- `_build_kas_session_params(mode_id="kiro_default")` (`acp.py:697`) is the sole place `modeId` is set,
  for both `session/new` (`acp.py:4647`, passes the mode) and `session/load` (`acp.py:4751`, passes
  nothing). The default argument is doing policy work; the call site that omits it looks like the tidy
  one.
- `_new_session_record` stores `cwd` and two clocks only — **no mode**. Nothing reads a prior mode back.
- Mode validation lives in `_handle_new` (`acp.py:5637-5645`), a **WebSocket** frame handler. The
  `isinstance` guard precedes the `in` test so an unhashable payload cannot raise. There is no HTTP
  route that creates an ACP session; `/api/new-session` spawns a terminal-attached process through
  `launcher.py` and carries no mode.
- Middleware order: `RemoteAccessGuard` is **outermost** (registered last at `web.py:1248`),
  `same_origin_guard` inner. `RemoteAccessGuard` is a raw ASGI class specifically so it sees
  `websocket` scopes, which `BaseHTTPMiddleware` never does. A loopback gate must mirror this shape.
- `_cookie_ok` (`web.py:930-964`) hand-parses the raw `Cookie` header from the ASGI scope rather than
  using `http.cookies`, precisely so it can run on websocket handshakes without raising. A cookie
  therefore covers `/ws/acp` with no new machinery.
- The one `set_cookie` in the repo (`web.py:3245-3248`): `httponly=True, samesite="strict", path="/"`,
  90-day `max_age`, **no `secure`** — justified as "no TLS by design (D5)". Value shape is
  `device_id.stamp.hmac` with no server-side store.
- `/api/save-setting` rejects booleans for every key before the type check (`web.py:3805-3807`), by
  design; booleans get dedicated routes (`/api/autostart`, `/api/notifications`). `_SETTING_TYPES`
  (`web.py:3708`) is a str/int/list allowlist whose own comment warns that adding a key without a
  bound turns a fail-closed refusal into an unbounded write — for a string that means a charset guard.
- `POST /api/notifications` does a full-dict **replace** (`config.notifications = {"enabled": ...}`),
  so any second key under the same table would be silently dropped. This is why the base-agent name
  goes in `_SETTING_TYPES` rather than into a shared table.
- Non-code artifacts ship via `pyproject.toml:37-38`, `power_atlas = ["static/**", "templates/**"]`.
  There is no PyInstaller spec. A permissions overlay shipped by PowerAtlas adds one entry here.
- **PowerAtlas writes to `~/.kiro/` nowhere today** — it only reads (session listing helpers at
  `acp.py:2266`, `2338`, `2455`). Generating the derived agent is a genuinely new capability.
- `GET /api/remote-access` (`web.py:3857`) already implements loopback-only + no-store + copyable
  secret, and nothing fetches it yet. It is the closest in-repo precedent for the tray's Copy login
  link, distinct from the `/remote-auth` cookie-exchange precedent.
- Agent-file deploy facts (agent-playbook), relevant even though it is no longer the owner:
  `inventory.tsv:57` is **`template-file`**, not `copy-file`, and
  `providers/kiro/agents/kiro_default.md:23` carries a `{{PLAYBOOK_ROOT}}` token that only
  `template-file` substitutes. A bad resource path fails open and silent.

### Risks & mitigations

- **Sub-agent conflict, resolved by measurement.** The mutation-finder report concluded `modeId`
  selects only among kiro-cli's built-in task modes and is "not a pointer to a PowerAtlas-authored
  agent definition", which would have invalidated the whole design. The directed report and the
  roadmap held the opposite. **P1 settled it in the roadmap's favour** (`origin=user`,
  `modeId_in_effect`, `source: agent-profile`). Recorded rather than silently dropped.
- **A stale derived agent.** Regeneration at startup and on settings change (Q10) leaves a window
  where a base-agent redeploy is not reflected. The permissions block is PowerAtlas's own and still
  applies, so the *gate* never goes stale; only inherited governance can drift. Accepted, documented.
- **Silent coercion to `vibe`.** An unrecognised `modeId` fails open with no error, so a
  misconfigured derived agent and a working one look identical from PowerAtlas's side. Mitigation:
  assert on `modeId_in_effect` from `config_option_update`, and on `consent.scope`/`consent.source`
  from the permission frame, rather than inferring success from the absence of a prompt.
- **A permission prompt that cannot be answered on its merits.** Mitigated by SC-4. Note the 200-char
  clamp currently governs both the toast and the transcript frame; a shell command longer than 200
  characters is approved from its first 200 characters today.
- **Breaking the remote surface.** A second cookie demanded of remote peers would break NetBird
  access. Mitigated structurally: cookies are host-only, so a loopback cookie is never sent to the
  NetBird host and vice versa. The composition rule is **peer class selects the credential** — the new
  gate acts on loopback peers exactly as `RemoteAccessGuard` acts on remote ones. Never both.
- **Locking the user out of their own dashboard.** A cookie-gated UI breaks bookmarks, typed URLs and
  the peek embedded webview (`peek.py:79-87`), which is a pywebview runtime with its own cookie jar,
  distinct from the system browser used by `tray.py:57` and `peek.py:190`. Mitigated by minting per
  door plus Copy login link (Q3). All three doors take their URL from the single `server_url` built at
  `__main__.py:836`.
- **Path traversal via the base-agent setting.** A user-supplied name becomes a filesystem path.
  Mitigated by a strict charset regex following `_valid_session_id` / `_DEVICE_ID_RE`, so traversal is
  unrepresentable rather than filtered.
- **Deleting `_ACP_TOKEN` also changes the dashboard.** `index.html:701` injects it for the
  transcript composer. In scope, stated so it is not discovered mid-implementation.

### Resolved decisions

- Q1: Does this deliverable unblock the Automation section, or is the unattended rule engine a separate item? — A: Split, and correct the roadmap claim — Decision: ship interactive prompts + the loopback credential; the unattended rule engine becomes its own roadmap item and inherits the "gates all six Automation items" role. The cloud-session framing of this item as an unattended policy is the **rejected reading**.
- Q2: Which routes does the loopback credential gate? — A: Everything, bar an explicit exempt set — Decision: default-deny across all routes; exempt set is the code-exchange route plus `/static`. Only this closes the `GET /` scrape-then-connect chain.
- Q3: How does a browser with no cookie re-enter? — A: Strict, plus a tray Copy login link — Decision: each door mints a one-time code; no cookie yields an "open from the tray" page; the tray mints on demand. A loopback caller may never self-serve.
- Q4: Does the cookie replace `_ACP_TOKEN` or sit alongside it? — A: Replace and delete — Decision: cookie signed from the on-disk secret survives restart; `_ACP_TOKEN`, `_acp_token_ok`, both injections and the stale-token branch are deleted outright rather than shimmed.
- Q5: *(retired — answered by probe P2 before it was asked: the agent binding survives a restart, so no per-session mode store is needed.)*
- Q6: What should the `permission_request` frame carry? — A: Full title plus the consent block; clamp only the toast — Decision: forward `_meta.kiro.consent`; `MAX_PERMISSION_TITLE_CHARS` reverts to notification-body duty.
- Q7: How does the derived agent file come to exist? — A: **PowerAtlas owns and holds it, not agent-playbook** — Decision: the artifact lives in the PowerAtlas repo and PowerAtlas installs it. Rejects all three options offered (new inventory kind / hand-mirrored file / hand-mirrored with guard). Rationale: agent-playbook is a personal config repo; a product feature must not depend on a personal deploy pipeline, and a PowerAtlas invariant must live where PowerAtlas can enforce it. Eliminates the `setup.ps1`/`setup.sh` twin work, `verify-deployment` teaching and the `test-parity.sh` tally bump entirely.
- Q8: What are the derived agent's contents, given ACP sessions currently inherit governance from `kiro_default`? — A: **A user-configurable reference agent, defaulting to `kiro_default`** — Decision: PowerAtlas ships only a permissions overlay and generates the derived agent from the configured base at write time. Rejects both a standalone agent (would silently drop the user's governance) and hardcoding `kiro_default`.
- Q9: What does the base-agent setting hold? — A: An agent name, regex-validated — Decision: a name resolved against `~/.kiro/agents/<name>.md`, guarded by a strict charset regex.
- Q10: When is the derived agent regenerated? — A: At startup and on settings change — Decision: two defined trigger points, no per-session I/O; the staleness window is accepted and documented.
- Q11: What happens when generation fails? — A: Fall back to the base agent and warn — Decision: the session still starts; continuity of service is preferred over refusing. *Superseded in part by: Q12* (the warning surface is the settings panel, not a toast).
- Q12: How visible is the ungated fallback state? — A: **Settings panel warning only — no toast, no per-session badge** — Decision: the settings toggle reports on-but-not-in-effect, following the existing `restart_pending` drift-badge pattern. Narrower than any option offered.
- Q13: How is the feature's state stored? — A: Dedicated bool route plus a string in `_SETTING_TYPES` — Decision: each value uses the mechanism already built for its type; avoids the notifications full-dict-replace trap.
- Q14: Toggling on does not affect existing sessions. How is that handled? — A: Accept, new sessions only, documented — Decision: a property of kiro-cli measured in P2, stated in the settings copy rather than worked around. No per-session mode tracking is added for display.
- Q15: How is this verified? — A: Unit + template tests + one live QA pass — Decision: pytest for the gate, route coverage, setting validation and the frontmatter injection (a pure function); `node tests/acp_page.test.mjs` for prompt rendering; one live pass that makes a real prompt fire, because a configured-but-inert gate is indistinguishable from a working one in a unit test.
- Q16: Terminology — A: `code` / `base agent` / `derived agent` — Decision: **login code** for the one-time URL credential (`nonce` is already the CSP nonce in `_acp_csp`), **base agent** for the configured source, **derived agent** for the generated `poweratlas-acp.md`. Rejected synonyms: `nonce`, `token`.
- Q17: Given a blank section means allow-all, where do the rules come from, and is there a deny floor? — A: **Two settings plus an always-on deny floor** — Decision: off = allow-all + floor; on = transcribed kiro-cli defaults + floor. Borrowed from Kiro Crew, whose bundled deny-pattern list sits below the approval mode and is never bypassed even under Autopilot; kiro-cli's `deny` effect expresses the same layering. This gives the default (off) state a floor it has nowhere today, and only ACP sessions are affected.

### Open items

- **Deterministic — the deny floor's contents and the transcribed default rule set.** kiro-cli exposes
  no `permissions` subcommand, so the effective default is not inspectable from the CLI. Resolvable by
  reading kiro-cli's published documentation, and verifiable during the SC-1 live QA pass. Record each
  rule as documentation-sourced until the live pass confirms it fires.
- **Deterministic — capabilities never exercised.** P4's probe covered `shell` and `fs_write` only.
  `mcp`, `web_fetch`, `web_search`, `subagent`, `skill`, `power` and deletion were not measured, and
  `match`/`exclude` glob scoping was read from docs rather than measured. MCP is not hypothetical here:
  `~/.kiro/workspace-roots/2d86113386de3488/permissions.yaml` carries live MCP allow-rules (dated
  2026-08-17, a v2 trust migration).
- **Deterministic — does "Always allow" persist?** P3 measured always-**deny** (session-scoped, writes
  nothing). Symmetry suggests always-allow behaves the same, but it is untested, and a consent that
  outlived its session would quietly widen later ones.
- **Execution-contingent — the peek embedded webview's cookie jar.** `peek.py:79-87` loads the server
  URL in a pywebview runtime. Whether it persists cookies across window creation, and whether a code
  in the URL survives its navigation, is a property of that runtime. Decide when the door is wired.
- **Filed separately, not part of this work.** `/partials/launchers` (`web.py:4280-4281`) renders
  `config.custom_launchers` unstripped, and `templates/partials/launcher_tile.html:14` emits
  `launcher.env | tojson` when `env` is non-empty — so an unauthenticated local GET returns the
  `AUTH_TOKEN_PRODUCTION` / `AUTH_TOKEN_STAGING` values that `_launchers_without_env` exists to
  protect. Its docstring asserts "the tile partial never renders `env`", which is false. The one test
  on that route uses `env: {}`, which is falsy, so the branch is never exercised. SC-5 gates the route;
  the stripping fix is its own item.

### Assumptions (unconfirmed)

Exploration ran at **high** depth, so this list is near-empty by construction; both consequence-significant
assumptions were surfaced at the checkpoint and resolved into decisions rather than carried.

- The login code is exchanged at a route mirroring `/remote-auth`'s shape (rate-limited, size-capped,
  `compare_digest`). *Falsification survived*: the precedent exists and is already exercised by tests.
- The login cookie is HMAC-signed from the on-disk secret with no server-side store, mirroring
  `make_device_cookie`. *Falsification survived*: the existing shape carries its own expiry and needs
  no store to invalidate.
- Cookie attributes mirror `pa_device` — `httponly`, `samesite=strict`, `path=/`, no `secure` — on the
  same no-TLS reasoning. *Falsification survived*: loopback has no TLS either, and the code arrives by
  top-level navigation, which `samesite=strict` permits.
- Subresources inherit the cookie, so `/api/launcher-icon/*` and similar need no exemption.

### Recommended approach

Build the two halves in sequence; they share `web.py` and both templates but touch different logic.

**Half A — the derived agent.** Ship a permissions overlay as package data
(`pyproject.toml` `agents/**`). Add a generation module: read `~/.kiro/agents/<base>.md`, parse its
frontmatter, inject the `permissions:` key assembled from (deny floor) + (allow-all | transcribed
defaults), write `~/.kiro/agents/poweratlas-acp.md`. Call it from `lifespan` and from the settings-write
path. Add the derived name to `_VALID_TASK_MODES` and resolve the picker's Default entry through the
setting. Forward `_meta.kiro.consent` on the permission frame and return the title clamp to the
notification path. Assert on `modeId_in_effect` and `consent.scope`/`consent.source` rather than on the
absence of a prompt.

**Half B — the login code and cookie.** Add `ensure/load/rotate` helpers for a local secret beside the
existing remote-secret trio in `config.py`. Add a loopback guard mirroring `RemoteAccessGuard`'s raw-ASGI
shape so it sees websocket scopes, acting only on loopback peers, default-deny with the code-exchange
route and `/static` exempt. Mint a one-time code in each of the three doors. Add Copy login link to the
tray, reusing `GET /api/remote-access`'s loopback-only no-store pattern. Delete `_ACP_TOKEN` and its
stale-token branch.

**Sequencing note.** Half B's default-deny touches every route and every test that drives one; landing
it first would make Half A's diffs harder to read. Land Half A first, then Half B.

### QA environment

- **Unit**: `.venv-PowerAtlas/Scripts/python -m pytest tests/test_web.py --timeout=300`. Relevant
  existing classes to extend: `TestSameOriginGuard`, `TestGuardOrdering`, `TestAcpTokenCheck` (deleted
  with the token), `TestRemoteRequestsNeedTheCookie`, `TestDeviceCookieVerification`,
  `TestSaveSettingAllowlist`, `TestSupervisor`.
- **Templates**: `node tests/acp_page.test.mjs` — not part of pytest, not run by CI. Required for the
  permission-prompt rendering change; the Python suite cannot see inline-script defects.
- **Pre-commit**: `.git/hooks/pre-commit` runs `_check_test_names.py` against staged content. Not
  version-controlled; reinstall with `cp _pre_commit_hook.sh .git/hooks/pre-commit`.
- **Live**: a real ACP session at `/acp`. Restarting PowerAtlas kills every supervised kiro-cli process
  and is **always the user's call** per `AGENTS.md` — never restart autonomously.
- **Wire-level probe harness** (disposable, this session's scratchpad, not committed): drives
  `kiro-cli acp --agent-engine v3` directly, handles the `_kiro/auth/getAccessToken` handshake via
  `kiro-cli chat _ get-kas-token` (token nested under `["data"]`), reads the mode catalogue from
  `config_option_update`, and records every `session/request_permission` before denying it. This is
  how P1-P5 were measured and is the fastest way to re-verify a rule set without touching PowerAtlas.
- **State discipline**: every probe that touches `~/.kiro` snapshots hashes first and verifies
  restoration after. `permissions.yaml` baseline on this machine:
  `f8a0d44394b5b2a23ff363b69538911caeda28c97604ad7d6825b93ec40146c8`.

---

## Harness Improvement Opportunities

- All three Step 1.5 sub-agents refused the brief's instruction to write a report file to their scratch
  path ("Subagents should return findings as text, not write report files"), while `shared/AGENTS.md`
  § Multi-Agent Coordination states a sub-agent's deliverable is a file, not a return — cost: three
  briefs carried a dead instruction and the durable-artifact guarantee silently did not hold —
  suggested change: reconcile the governance rule with the harness behaviour, or have qexplore's briefs
  stop requesting a file write.
- The roadmap's own seed note (written 2026-09-21, one session earlier) carried three errors that only
  a sub-agent re-read caught: `copy-file` for a `template-file` row, a line number that had drifted,
  and a reuse plan for machinery that cannot be reused — cost: a wrong cost estimate would have
  survived into planning — suggested change: when a seed note cites a deploy manifest row, cite it by
  content (kind + dest) rather than by line number.
