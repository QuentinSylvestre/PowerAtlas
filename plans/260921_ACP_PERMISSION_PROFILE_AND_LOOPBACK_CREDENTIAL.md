# ACP Permission Profile and Loopback Credential

> **Date**: 2026-09-21
> **Status**: Draft  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Last Updated**: <set by /qclose at archival>
> **Scope**: Give ACP sessions a configurable permission posture through a PowerAtlas-generated
> kiro-cli agent profile, and require a credential on every loopback HTTP/WebSocket route.
> **Estimated effort**: ~2-3 weeks (revised up after review; see Review Log finding SE-4)
> **Execution constraint**: Starts only after `plans/260921_DASHBOARD_ACP_FEATURE_PARITY.md`
> completes. User decision, 2026-09-21 — see D-14. **Phase 0 step 0 verifies this rather than
> assuming it.**

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
it blank" and "allow all" are the same state. **This fact has a second consequence the design must
respect: any failure that leaves the derived agent's `permissions:` block absent or malformed fails
*open*, not closed.**

**The unauthenticated half.** The loopback HTTP API has no authentication. `same_origin_guard` checks
`Host` against a loopback allowlist on every request and `Origin`/`Referer` on POSTs; that is
DNS-rebinding and CSRF defence for browsers, and a local script sets both headers in one line.
`RemoteAccessGuard` acts only on non-loopback peers. The `_ACP_TOKEN` that guards `/ws/acp` is
injected into pages any local process can fetch, so the chain is: `GET /` then read `ACP_TOKEN` from
the HTML, then open `/ws/acp?t=...`, then create a session, prompt it, and answer its own permission
requests.

**Desired outcome.** Interactive ACP sessions can be configured to ask before acting, without changing
the posture of interactive terminal kiro-cli sessions; and the local API refuses callers that are not
PowerAtlas's own UI. Neither half alone closes the exposure: a gated agent behind an open API still
lets a rogue process answer its own prompts, and a credentialed API still runs everything it is asked.

### Success criteria

- **SC-1 — Permission posture is a user setting.** Two states: **off** = allow-all (today's
  behaviour, the default) and **on** = explicit rules reproducing kiro-cli's default posture. Both
  states additionally carry an always-on **deny floor** (never-okay destructive shell patterns and
  sensitive-path writes), expressed with kiro-cli's `deny` effect, which beats both `ask` and `allow`
  in the scope merge. The floor's bypass resistance is **tested, not assumed** (Phase 0, Phase 7).
- **SC-2 — PowerAtlas owns and generates the derived agent.** It writes
  `~/.kiro/agents/poweratlas-acp.md`, derived at write time from a configurable **base agent**
  (default `kiro_default`) by injecting a `permissions:` key into that file's frontmatter, **textually
  and without parsing**, so every other byte survives by construction. The write is **atomic**.
  Regenerated at startup and on settings change.
- **SC-3 — The picker's Default entry resolves to the derived agent** when the setting is on, and to
  the base agent when off. `_VALID_TASK_MODES` accepts the derived name. The seven vendor task modes
  are untouched and keep their manufacturer-encoded posture.
- **SC-4 — A permission prompt is answerable on its merits.** The `permission_request` frame carries
  the full (unclamped) title plus an allowlisted projection of `params._meta.kiro.consent` —
  `capability`, `resource`, `matchedRule`, `scope`, `source` — so a write prompt names the file rather
  than reading `Write File`. `MAX_PERMISSION_TITLE_CHARS` reverts to governing only the notification
  body it was sized for. Rendering is escaped and tested against injection.
- **SC-5 — Every loopback route requires a credential**, default-deny, with an exempt set of the
  code-exchange route and `/static`. This covers `GET /` (where the token is scraped), `/acp`,
  `/partials/*` and `/api/*` alike — including `GET /api/remote-access`, which today returns the
  **permanent** remote device secret to any unauthenticated loopback caller.
- **SC-6 — Browsers are credentialed by a one-time login code.** Each door that opens the UI mints a
  fresh code into the URL, exchanged for an HttpOnly cookie. The tray gains **Copy login link** for
  bookmarks, a second browser, and any deliberate re-entry. Minting is **in-process only and never a
  network route**. A browser arriving with no cookie is told to open PowerAtlas from the tray.
- **SC-7 — `_ACP_TOKEN` is retired**, along with `_acp_token_ok`, both template injections, and the
  stale-token/reload branch wherever it then lives. The **ACP-availability sentinel role** that
  `ACP_TOKEN` doubles as in `index.html` is taken over by an explicit boolean *before* the credential
  is removed.
- **SC-8 — Failure is visible and never widens the posture.** If the base agent is missing,
  unparseable, or the write is refused, the session still starts, the **settings panel** reports
  on-but-not-in-effect, and a previously-generated valid derived agent is **kept rather than replaced**
  by a fallback to the base. No toast, no per-session badge.

### Scope boundaries & non-goals

**In scope**: ACP sessions PowerAtlas creates; the loopback HTTP and WebSocket surface; the dashboard's
own transcript composer, which also consumes `ACP_TOKEN`.

**Out of scope, deliberately**:

- **The unattended rule engine.** Now its own roadmap item, which inherits the Automation-gating role.
  This plan does **not** unblock the Automation & Workflows section.
- **The lean dispatch agent.** Belongs to the unattended follow-on.
- **The terminal launcher.** `launcher.py` spawns `chat --agent-engine v3 --trust-tools *`. The two
  spawn paths have genuinely different postures and the terminal one stays wide open. This is the
  constraint that makes the design work: the base agent is untouched.
- **The `/partials/launchers` credential leak.** Its own `[SECURITY]` roadmap item. SC-5 gates the
  route, which removes anonymous reachability, but does **not** fix the leak.
- **The remote/NetBird surface and the TLS decision.** Unchanged.

---

## 1) Current State

Static claims carry `file:line` and are self-verifying by reading. **Runtime claims name the probe and
the date**, because they carry an expiry a static citation does not.

**Anchoring convention.** `plans/260921_DASHBOARD_ACP_FEATURE_PARITY.md` executes first and rewrites
`templates/index.html` across all six of its phases, `templates/acp.html` in its phases 1-2, and
extracts `static/composer-chrome.js`. **Every line number in this plan is expected to be stale at
execution time** — demonstrated: the `ACP_TOKEN` injection recorded at `index.html:701` during
exploration sat at `index.html:781` hours later. Phases anchor on unique greppable strings, and Phase 0
re-locates them all before any edit.

**Half A — permission posture.**

- `~/.kiro/settings/permissions.yaml` is `rules: [{capability: all, effect: allow}]`. Untouched here.
- `_VALID_TASK_MODES` (`acp.py:691`) is the 8-value frozenset kiro-cli's own `session/new` response
  enumerates. `_handle_new` rejects anything outside it with `bad_payload`.
- `_build_kas_session_params(mode_id="kiro_default")` (`acp.py:697`) is the sole place `modeId` is set.
  `new_session` passes the mode (`acp.py:4647`); `load_session` calls it **with no argument**
  (`acp.py:4751`).
- `_on_permission_request` (`acp.py:4479`) rebuilds the frame as `{"title": title}` with the title
  clamped by `MAX_PERMISSION_TITLE_CHARS` (`acp.py:589`, = 200), discarding `params._meta`. The clamped
  variable feeds **both** the frame and the toast.
- **`acp.py`'s isolation boundary is real and narrow**: it imports exactly two intra-package names,
  `from .config import CONFIG_DIR` and `from .launcher import _SESSION_ID_RE` (`acp.py:82-83`).
- **Runtime — P1, 2026-09-21, kiro-cli 2.22.x.** A file in `~/.kiro/agents/` registers in the mode
  catalogue as `source=global origin=user`; an inline `permissions:` block loads cleanly.
- **Runtime — P2, 2026-09-21.** The agent binds at `session/new` and ignores `modeId` on
  `session/load`: a fresh process sending `kiro_default` still reported `modeId_in_effect:
  pa-probe-user` and still raised the prompt. `load_session`'s missing argument is **inert**.
- **Runtime — P3, 2026-09-21.** `always-reject` persists nothing to disk; the agent called it "a
  session-level permission rule". *Not measured*: always-**allow**.
- **Runtime — P4, 2026-09-21.** `toolCall` carries only `{status, title, toolCallId}`. Shell puts the
  literal command in `title`; write puts `"Write File"` and no path. The observed `_meta.kiro` payload,
  **recorded here with its literal values because Phase 7 asserts on them**:
  ```json
  {"kiro": {"toolId": "fs_write", "consentRound": 1,
            "consent": {"capability": "fs_write", "resource": "p4-meta-target.txt",
                        "askType": "explicit", "scope": "agent", "source": "agent-profile",
                        "matchedRule": {"capability": "fs_write", "effect": "ask"},
                        "workspaceRoot": "<cwd>"}}}
  ```
- **Runtime — P5, 2026-09-21.** An agent with **no** `permissions:` key raised 0 prompts and the
  command ran, with `modeId_in_effect` confirming the agent had loaded.
- **Runtime — 2026-09-21.** `kiro-cli` has no `permissions` subcommand (`kiro-cli permissions --help`
  → "unrecognized subcommand"), so the merged rule set is not inspectable from the CLI.
- **The probe harness that produced P1-P5 is NOT in the repo.** It was disposable, in a session
  scratchpad. Phase 0 must reconstruct and commit it; Phase 0 and Phase 7 both depend on it.

**Half B — the loopback surface.**

- Middleware order: `same_origin_guard` registered first (`web.py:771-772`), `RemoteAccessGuard` second
  (`web.py:1248`) and therefore **outermost**; a raw ASGI class specifically so it sees `websocket`
  scopes. `RemoteAccessGuard`'s cookie check (`web.py:1231`) fires only inside the `_is_remote_peer`
  branch, so it is a pure passthrough for loopback today.
- `_refuse` (`web.py:1167-1183`) is already **scope-typed**: `websocket` → close 1008, otherwise a
  JSON 403. It does not distinguish a browser navigation from an API call — the new gate needs that.
- `_cookie_ok` (`web.py:930-964`) hand-parses the raw `Cookie` header from the ASGI scope so it can run
  on websocket handshakes without raising.
- `set_cookie` (`web.py:3245`): `httponly=True, samesite="strict", path="/"`, 90-day max-age, **no
  `secure`**. `make_device_cookie`/`_device_cookie_sig` (`web.py:891-905`): `device_id.stamp.hmac`,
  keyed by the file secret, **no server-side store**.
- `remote_auth_exchange` (`web.py:3155`) carries per-peer exponential backoff (2s→300s), a streamed
  body-size ceiling, a field-count ceiling, and `compare_digest` over UTF-8 bytes.
- `_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})` (`web.py:574`) — **all three pass
  the Host check, and a host-only cookie set for one spelling is never sent for another.**
- `_ACP_TOKEN` (`web.py:1269`), `_acp_token_ok` (**a real function, defined `web.py:1272`, called
  `web.py:1550`** — `acp.py:5076` merely mentions it in a docstring).
- **`ACP_TOKEN` in `index.html` is overloaded**: 14 occurrences — the var declaration, the socket-URL
  query use, **6 sentinel branches** (`if (!ACP_TOKEN) return;` etc.) and 6 comments. The in-file
  comment says `ACP_TOKEN is null (acp failed to import)`. Removing the credential without first
  replacing the sentinel silently disables every ACP feature on the dashboard.
- **`permission_request` is handled in five source files**: `acp.py`, `web.py`,
  `static/transcript-renderer.js` (lines 1773/1781/1921), `templates/acp.html`, `templates/index.html`.
- The stale-token branch is `reportStaleToken` / `diagnoseRejectedHandshake` (`acp.html`, 2 occurrences
  each). **The parity plan's own unfinished Phase 6 instructs porting both into `index.html`'s
  `dashConnect()`** (`260921_DASHBOARD_ACP_FEATURE_PARITY.md:314`, `:327`), so by the time this plan
  runs they are expected to exist in **two** files, possibly renamed.
- Three doors, all from the single `server_url` in `__main__.py`: `tray.py:57`, `peek.py:190`
  (double-tap), and `peek.py:79-87` (pywebview, **its own cookie jar**). `peek.py:213-215`'s `_show()`
  navigates with `location.href` and mints nothing.
- `_SETTING_TYPES` (`web.py:3708`) is str/int/list; `/api/save-setting` rejects booleans first
  (`web.py:3804-3806`). `POST /api/notifications` (`web.py:3300`) full-dict **replaces**.
- `save_config` (`config.py:481-500`) is the repo's atomic-write pattern: tmp → `fsync` → `os.replace`,
  with `tmp.unlink(missing_ok=True)` on failure. `_write_remote_secret` (`config.py:167-187`) is a
  plain `os.open(O_CREAT|O_TRUNC)` with **no** `O_NOFOLLOW`.
- `pyproject.toml:37-38` package-data omits any `agents/**` path. **No YAML library is declared or
  installed** — `import yaml` raises `ModuleNotFoundError` in the venv. **No clipboard library either.**
- `tests/test_web.py` is ~21.9k lines / ~1,069 tests, and already exercises
  `async with web_mod.lifespan(None)` (≈`tests/test_web.py:10664`), which is the seam Phase 1's
  generation-on-startup test can reuse.
- **PowerAtlas writes to `~/.kiro/` nowhere today** — only reads.
- **Comment attribution**: the shared files carry bare `SC-1`..`SC-9` and `Phase 1`..`Phase 6` markers
  from at least four prior/concurrent plans, in the same number space this plan will use.

## 2) Goal

Ship a PowerAtlas-generated kiro-cli agent profile that makes interactive ACP sessions ask before
running shell or writing files, configurable between allow-all and kiro-cli's default posture with an
always-on deny floor; and put a one-time login code plus HttpOnly cookie in front of every loopback
route, retiring the page-injected `_ACP_TOKEN` it replaces.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| D-1 Scope | Interactive ACP sessions only | Include the unattended rule engine | User instruction 2026-09-21. An `ask` rule with nobody watching is a bounded wait, not a policy |
| D-2 Route coverage | Default-deny on **every** route; exempt only the exchange route and `/static` | `/api/*` + `/ws/acp`; adding `/partials/*` | The credential is scraped from `GET /`, in neither narrower set. Also covers `GET /api/remote-access`, which leaks the permanent remote secret to unauthenticated loopback today |
| D-3 Re-entry | Per-door one-time code + tray "Copy login link"; no cookie yields an "open from the tray" page | Allow any loopback caller to mint | A rogue local process is indistinguishable from a typed URL at the HTTP layer |
| D-4 `_ACP_TOKEN` | Retire it; cookie replaces it | Keep both; sign the cookie per launch | The token adds no barrier a cookie-holder does not already pass |
| D-5 Permission frame | Forward an allowlisted `consent` projection + unclamped title; clamp the toast only | Consent with the 200-clamp retained; leave as-is | A write prompt otherwise reads `"Write File"` with no path. The clamp was sized for a toast body |
| D-6 Agent-file ownership | **PowerAtlas repo owns it**; PowerAtlas installs it | agent-playbook inventory row | agent-playbook is a personal config repo; a product feature must not depend on a personal deploy pipeline |
| D-7 Derived-agent contents | Generated from a **configurable base agent** (default `kiro_default`) | Standalone agent; standalone with hardcoded paths | A standalone agent cannot know the user's `resources:` paths, so ACP sessions would silently lose governance |
| D-8 Base-agent setting | Agent **name**, charset-regex validated **plus Windows reserved-device-name rejection** | Absolute path; name + path override | A bare charset regex admits `CON`, `PRN`, `AUX`, `NUL`, `COM1`-`COM9`, `LPT1`-`LPT9`, which misbehave when opened as `<name>.md` |
| D-9 Regeneration | At startup and on settings change, **via `asyncio.to_thread`** | Per session creation; only on toggle-on | Two defined trigger points, no per-session I/O. The thread dispatch matches the codebase's established idiom for filesystem I/O on the event loop |
| D-10 Generation failure | Fall back to the base agent **only when no previously-validated derived agent exists**; settings panel reports on-but-not-in-effect | Fail closed; toast; per-session badge; always fall back | User decision 2026-09-21 (continuity), narrowed to settings-panel-only. The last-good qualifier is a review finding: an unconditional fallback *widens* posture on a transient regen failure |
| D-11 Config shape | Dedicated bool route + string in `_SETTING_TYPES` | One `[permissions]` table | `/api/save-setting` rejects booleans by design; `POST /api/notifications` full-dict-replaces |
| D-12 Rollout | New sessions only; documented in settings copy | Surface ungated live sessions; close and recreate | Measured property of kiro-cli (P2), not a PowerAtlas choice |
| D-13 Rule set | Two states plus an always-on **deny floor** | A PowerAtlas-authored ask-list; no floor | User decision 2026-09-21, borrowed from Kiro Crew's never-bypassed bundled deny list; kiro-cli's `deny` effect expresses the same layering |
| D-14 Execution order | Starts only after the parity plan completes; **Phase 0 step 0 verifies it** | Backend-first concurrent; fully parallel | User decision 2026-09-21. The precondition is checkable, so it is checked rather than assumed |
| D-15 `ACP_TOKEN` sentinel | Introduce `ACP_AVAILABLE` **before** removing the credential | Delete and repair the fallout | Six dashboard branches use it as the ACP-availability flag, and the failure is silent |
| D-16 Login-code exchange | Mirror `remote_auth_exchange`'s hardening, but **not** its peer-keyed backoff | Reuse the peer-keyed backoff verbatim | On loopback every caller is `127.0.0.1`, so a peer-keyed lockout lets one bad process throttle the legitimate user for up to 5 minutes |
| D-17 Cookie isolation | Separate cookies; **peer class selects the credential**; one canonical loopback host | One unified cookie | Cookies are host-only. A canonical host is required because `127.0.0.1`, `localhost` and `::1` all pass the Host check but do not share a cookie jar |
| D-18 Frontmatter injection | **Textual insert/replace between the `---` fences. No YAML parsing, no new dependency** | PyYAML; `ruamel.yaml` for round-trip fidelity | No YAML library is declared *or installed*. Byte-identity is the exit criterion, and only not-parsing achieves it by construction. An existing `permissions:` key is **replaced**, not merged |
| D-19 Derived-agent write | **Atomic**: tmp → `fsync` → `os.replace`, reusing `save_config`'s pattern | In-place truncate, mirroring `_write_remote_secret` | Opposite failure directions: a torn secret fails closed (length check), a torn `permissions:` block fails **open** (P5). The secret's pattern must not be copied here |
| D-20 `DERIVED_AGENT_NAME` | Defined in `config.py` | In `agent_profile.py`; duplicated literal in `acp.py` | `acp.py` already imports `config` and nothing else intra-package; this adds no import and no sync-bug risk |
| D-21 Login code | `secrets.token_urlsafe(32)`; single-use; **120 s TTL on `time.monotonic()`**; bounded outstanding-code store | Leave to the implementer; wall-clock TTL | Entropy is load-bearing because a same-user attacker reaches the exchange directly. Monotonic avoids NTP/sleep clock corrections; the codes are process-local so wall time buys nothing |
| D-22 Local-secret write failure | Keep the generated secret **in memory** for the process lifetime; log ERROR; report in settings | Fail startup; proceed with no secret | Without this, every door mints a cookie that verifies nowhere and the settings panel explaining it is itself behind the gate. In-memory degrades to today's per-launch `_ACP_TOKEN` lifetime |
| D-23 Comment attribution | Every comment this plan adds to a shared file carries the plan slug | Bare `Phase N` / `SC-n` markers | The shared files already carry bare markers from four other plans in the same number space |

## 4) External Dependencies & Costs

### Required external changes

| Category | Change needed | Owner | Status |
|---|---|---|---|
| Secrets / Env vars | New on-disk local secret at `CONFIG_DIR/local-secret`, created at first start. No external provisioning | Implementer | Pending |
| Rollout / cutover | None. Defaults **off** (allow-all), so installing this release changes no permission behaviour until the user opts in — except that deny-floor patterns become blocked | — | N/A |
| Data migration / backfill | None. The derived agent is a regenerable build product, not state | — | N/A |

CI/CD, IAM, cloud, DNS and third-party rows do not apply — a local desktop application with no cloud
surface.

**Non-code runtime artifact**: the permissions overlay ships as package data (Phase 1 exit criterion on
`pyproject.toml`).

**New runtime dependency — clipboard.** "Copy login link" needs a clipboard write from the tray
thread. **No clipboard library is in `pyproject.toml`.** Phase 5 must use `pywin32`'s
`win32clipboard` (already a declared Windows dependency) rather than adding a package; on non-Windows,
fall back to displaying the link for manual copy rather than adding a dependency.

**Explicitly NOT added**: a YAML library. D-18 makes one unnecessary.

### Cost impact

None. No hosting, compute, API-call, storage or licensing change.

## 5) Implementation Phases

> **Comment convention for every phase (D-23)**: any comment this plan adds to a file shared with
> another plan carries the slug — e.g. `# 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 2`.
> Bare `Phase N` or `SC-n` is prohibited: four other plans already use those labels in these files.

### Phase 0: Pre-flight — preconditions, anchors, harness, and the rule set

**Goal**: Establish the empirical facts the design depends on, rebuild the probe harness the plan
relies on, and re-locate every anchor. Nothing in Phases 1-7 is valid until this phase confirms its
premises.

**File scope**: `tools/acp_permission_probe.py` (new, committed harness) and this plan file (§ 9).

**Step 0 — verify the execution precondition (D-14).** Confirm **both**: the parity plan's Status
reads `Complete`, or it has moved to `plans/done/`; **and** `git status --short` is clean across its
file scope (`templates/index.html`, `templates/acp.html`, `static/composer-chrome.js`,
`static/style.css`, `tests/acp_page.test.mjs`, `tests/test_web.py`, `AGENTS.md`). **If either fails,
STOP and ask the user.** As of 2026-09-21 this precondition was NOT satisfied — the parity tracker read
"Not started" on all six phases while its Phase 1 artifacts were already in the working tree.

1. **Re-locate anchors**, recording file and current line for each, and noting whether the code moved
   into `static/composer-chrome.js` or `static/transcript-renderer.js`: `reportStaleToken`,
   `diagnoseRejectedHandshake`, `ACP_TOKEN`, `acp-taskmode-option`, `notifyToggle`, `autostartToggle`,
   `permission_request`. Record the `ACP_TOKEN` occurrence count in `index.html` (14 on 2026-09-21) —
   a changed count means the sentinel surface changed and D-15's sizing must be revisited.
2. **Check whether the parity plan renamed the stale-token functions** when porting them into
   `dashConnect()` (its Phase 6 instructs the port at `260921_DASHBOARD_ACP_FEATURE_PARITY.md:314`).
   Record the names actually present. Phase 6's deletion criteria are behavioural precisely because a
   rename would let a name-based grep pass over a surviving branch.
3. **Rebuild and commit the probe harness** as `tools/acp_permission_probe.py`. Contract: spawn
   `kiro-cli acp --agent-engine v3`; answer `_kiro/auth/getAccessToken` by shelling to
   `kiro-cli chat _ get-kas-token` and replying with the four fields nested under `["data"]`; read the
   mode catalogue from `config_option_update`'s `configOptions[id="mode"]`; record every
   `session/request_permission` **with its full `params`** and then **deny** it so nothing executes.
   It must print `modeId_in_effect` so a silent coercion to `vibe` is visible.
4. **Establish the default rule set.** Read kiro-cli's published permission documentation and
   transcribe the default posture as an explicit rule list, recording the doc reference per rule.
   **If no such documentation is reachable**, the set can be derived empirically instead — but
   **STOP and ask the user first.** The empirical route moves `~/.kiro/settings/permissions.yaml`
   aside, which removes this machine's permission posture for **every concurrent kiro-cli session**,
   not just the probe's; it is the same blast radius as a PowerAtlas restart and gets the same
   discipline. No turn has approved it — the exploration's Q17 was answered with a question, and
   Q17-revised settled the deny floor, not this. On approval: move the file, run the harness with a
   no-rules agent, record which capabilities prompt, restore, and verify by hash against the § 7
   baseline. Record which of the two routes was used — a documentation-sourced set and a measured set
   carry different confidence, and Phase 7 should know which it is confirming.
5. **Draft the deny floor** — a short curated list of never-okay patterns, each justified in one line.
   It is always on, including in allow-all, so a false positive blocks work the user expects to run.
6. **Test the floor's bypass resistance.** For at least one floor pattern, attempt: a case-variant
   path, a quoted/aliased shell form, a `../`-traversal path, and a symlinked target. Record whether
   kiro-cli matches on the literal string or a canonicalized path. **The property under test**: the
   floor must hold against a rephrasing an ordinary agent would produce on its own, not merely against
   the exact string it was written with.
7. **Measure the unexercised capabilities**: `mcp`, `web_fetch`, `web_search`, `subagent`, `skill`,
   `power`, and a deletion — each under an `ask` rule, recording whether it fires and its consent
   payload. P4 covered only `shell` and `fs_write`, and the workspace-scope file on this machine shows
   live MCP consent rules, so MCP is not hypothetical.
8. **Measure whether "always allow" persists.** Select the allow option on a harmless call and diff
   `~/.kiro/settings/permissions.yaml`, `~/.kiro/agents/`, and every
   `~/.kiro/workspace-roots/*/permissions.yaml`. If it persists, the deny floor must account for a user
   widening the posture out from under it.
9. **Record a `~/.kiro` baseline** naming **which** files were in which state with hashes, never only a
   count — some are rewritten by other tooling between runs.

> **Rejected:** omit the `permissions:` key to get kiro-cli's defaults — measured (P5): a blank section
> inherits the user-scope allow-all and raises zero prompts. **Use instead:** an explicit rule list.

**Gate**: if step 4 cannot produce a defensible rule set by either route, or steps 6-8 contradict a
premise, **stop and revise the design before Phase 1**.

**Exit criteria**:
- [ ] Step 0's precondition verified and recorded, or the phase stopped and the user asked
- [ ] Every anchor re-located; the anchor → current file:line table recorded in § 9
- [ ] `ACP_TOKEN` occurrence count recorded and compared against 14
- [ ] The stale-token function names actually present after the parity port are recorded
- [ ] `tools/acp_permission_probe.py` committed, and a run of it reproduces P1's mode-catalogue output
- [ ] Default rule set recorded, each rule tagged documentation-sourced or measured, with its source
- [ ] Deny-floor list drafted, every entry justified in one line
- [ ] Floor bypass attempted in all four forms; the matching semantics (literal vs canonicalized) recorded
- [ ] Each of the 7 unexercised capabilities recorded as fires / does-not-fire with its consent payload
- [ ] "Always allow" persistence answered yes/no, with the diffed paths named
- [ ] `~/.kiro` baseline recorded by filename and hash
- [ ] A statement in § 9 confirming the design still holds, or naming what must change

### Phase 1: Derived-agent generation and its settings [QA]

**Goal**: PowerAtlas can generate the derived agent from a configurable base, driven by two new
settings, atomically, with the failure path defined. Verifiable entirely through the API.

**File scope**: `src/power_atlas/agent_profile.py` (new), `src/power_atlas/agents/permissions.yaml`
(new, package data), `src/power_atlas/config.py`, `src/power_atlas/web.py`, `pyproject.toml`,
`tests/test_web.py`.

**Why horizontal**: backend-only, no UI. (a) Phases 3 and 6 are the vertical completion — the settings
UI and the sentinel replacement both consume what this phase builds. (b) The generation engine is
independently verifiable through `POST /api/acp-permissions` plus an on-disk file assertion, and its
correctness properties (byte-identical round-trip, atomicity, traversal rejection) are precisely the
ones a UI cannot exercise; binding them to a template phase would couple a pure-function test suite to
DOM harness availability. *(Reason (b) originally cited the concurrent parity plan; D-14 sequences that
plan strictly before this one, so that justification was stale and has been replaced — Review Log Arch-5.)*

`DERIVED_AGENT_NAME` is defined in **`config.py`** (D-20), which `acp.py` already imports.

**The generation contract** (`agent_profile.py`):

- **Input**: a base-agent name (validated), the overlay (package data), the toggle state.
- **Output**: the derived agent written atomically, or a typed `AgentProfileError`.
- **It must** preserve every byte of the base except the `permissions:` block it inserts or replaces;
  never modify the base; and write via tmp → `fsync` → `os.replace`.
- **It must not** be imported by `acp.py` (isolation boundary, `acp.py:82-83`).

Injection is **textual** (D-18): locate the opening and closing `---` fences; within that region, if a
top-level `permissions:` key exists, replace its whole block; otherwise insert before the closing
fence. Everything outside the inserted block is copied through byte-for-byte. Nothing is parsed.

```python
# agent_profile.py (sketch — the shape, not the deliverable)
_AGENT_NAME_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
_WIN_RESERVED = {"CON","PRN","AUX","NUL",
                 *(f"COM{i}" for i in range(1,10)), *(f"LPT{i}" for i in range(1,10))}

def _validate(name: str) -> str:
    if not _AGENT_NAME_RE.fullmatch(name) or name.upper() in _WIN_RESERVED:
        raise AgentProfileError(f"invalid base agent name: {name!r}")
    return name
```

The rule assembly is `deny floor + (allow-all | transcribed defaults)`, floor first so its precedence
is visible to a human reading the generated file. Both branches always include the floor (D-13).

Settings (D-11): `GET`/`POST /api/acp-permissions` for the boolean, mirroring `/api/notifications`;
`acp_permission_base_agent` in `_SETTING_TYPES` as `str`, validated on the write path with the same
`_validate`.

The `lifespan` call site wraps generation in a **broad `except Exception`** (D-10/SC-8) — a typed error
is what the module promises, but an unexpected bug must not abort startup. It dispatches through
`asyncio.to_thread` (D-9).

> **Rejected:** `yaml.safe_load` + `yaml.dump` — no YAML library is declared *or installed*
> (`ModuleNotFoundError` in the venv), and a parse/re-emit round trip cannot preserve byte-identity.
> **Use instead:** textual insert/replace between the fences.

> **Rejected:** mirroring `_write_remote_secret`'s in-place `O_CREAT|O_TRUNC` — a torn secret fails
> closed via the length check, a torn `permissions:` block fails **open** (P5). **Use instead:**
> `save_config`'s tmp → fsync → `os.replace`.

> **Rejected:** one `[permissions]` config table — `POST /api/notifications`'s full-dict replace would
> silently drop the base-agent key. **Use instead:** boolean route + `_SETTING_TYPES` string.

**Exit criteria**:
- [ ] `build_derived_agent` round-trips a fixture base agent: every byte outside the injected `permissions:` block is identical, asserted by comparing the full text with that block excised
- [ ] A base agent that **already declares** `permissions:` has it replaced, not duplicated or merged
- [ ] Invalid base names raise rather than resolving a path — asserted for `../x`, an absolute path, an empty string, an over-length name, and `CON`
- [ ] The base agent is never modified — hash asserted before and after
- [ ] The write is atomic: a simulated failure mid-write leaves the previous derived agent intact and no `.tmp` residue
- [ ] `GET`/`POST /api/acp-permissions` round-trip the boolean; the POST rejects a non-boolean body
- [ ] `acp_permission_base_agent` rejects a name failing validation via `/api/save-setting`
- [ ] Generation is invoked from `lifespan` and the settings write path, asserted through the existing `async with web_mod.lifespan(None)` seam
- [ ] A generation failure inside `lifespan` does not prevent startup, asserted by raising a non-`AgentProfileError` from a patched generator
- [ ] On regen failure with a valid derived agent already on disk, that file is **kept**, not replaced by a base-agent fallback (D-10)
- [ ] `pyproject.toml` `package-data` includes the overlay, and an `importlib.resources` read confirms it is reachable at runtime rather than merely present in the source tree
- [ ] `pytest tests/test_web.py --timeout=300` passes

**Covers**: SC-1, SC-2, SC-8 (backend half)

### Phase 2: Mode wiring and permission-frame enrichment [QA]

**Goal**: The derived agent is selectable through `modeId`, and a permission prompt carries enough to
be answered on its merits.

**File scope**: `src/power_atlas/acp.py`, `tests/test_web.py`.

*(No `[P:1]` annotation: an earlier draft marked this parallel with Phase 4, but both edit
`tests/test_web.py`, so their file scopes overlap — Review Log Arch-4.)*

1. **`_VALID_TASK_MODES`** gains `DERIVED_AGENT_NAME`, imported from `config` (D-20). Update its
   docstring: the set is no longer a pure mirror of kiro-cli's enumeration.
2. **`_on_permission_request`** forwards an allowlisted consent projection and stops clamping the
   transcript title:
   ```python
   # sketch — the delta, not the deliverable
   consent = ((params.get("_meta") or {}).get("kiro") or {}).get("consent") or {}
   _emit(session_id, envelope("permission_request", {
       "requestId": request_id, "sessionId": session_id,
       "toolCall": {"title": title},            # no longer clamped here
       "consent": _project_consent(consent),    # five allowlisted fields only
       "options": options,
   }, session_id))
   _notify("permission_request", session_id, title[:MAX_PERMISSION_TITLE_CHARS])  # clamp lives here
   ```
   `_project_consent` **allowlists** `capability`, `resource`, `matchedRule`, `scope`, `source` — the
   payload is agent-authored and lands in a browser, and the existing `options` handling already sets
   this precedent by rebuilding each option element-by-element.
3. **`load_session`'s `_build_kas_session_params()`**. Measured inert (P2), so this is hygiene — but a
   trap, because the parameter reads as if it re-asserts the mode. **The property the choice must
   preserve**: a reader must not conclude the load path re-asserts the permission posture when it does
   not. Passing the real mode and adding a dated comment both satisfy it; doing nothing does not.

**Exit criteria**:
- [ ] `_VALID_TASK_MODES` accepts `DERIVED_AGENT_NAME` imported from `config`, with no duplicated literal and no new intra-package import in `acp.py` beyond `config`
- [ ] Its docstring no longer claims to be a pure mirror of the vendor enumeration
- [ ] A `permission_request` frame carries all five consent fields — asserted against a fixture copied from the P4 payload recorded in § 1
- [ ] `_project_consent` drops an unexpected key rather than forwarding it — asserted with an extra field
- [ ] A title longer than `MAX_PERMISSION_TITLE_CHARS` reaches the frame **unclamped** and the notification **clamped** — one test asserting both halves
- [ ] The `load_session` mode situation is resolved so a reader cannot conclude the load path re-asserts the posture
- [ ] `pytest tests/test_web.py --timeout=300` passes

**Covers**: SC-3 (backend half), SC-4 (backend half)

### Phase 3: Settings and permission-prompt UI [QA] [P:4]

**Goal**: The user can turn the posture on, name a base agent, see when it is on-but-not-in-effect, and
read what a permission prompt is asking.

**File scope**: `src/power_atlas/templates/index.html`, `src/power_atlas/templates/acp.html`,
`src/power_atlas/static/transcript-renderer.js`, `src/power_atlas/static/composer-chrome.js` (both
conditional on Phase 0's anchor findings), `src/power_atlas/static/style.css`,
`tests/acp_page.test.mjs`.

`transcript-renderer.js` is in scope because it already handles `permission_request` (lines
1773/1781/1921 as of 2026-09-21) — an earlier draft omitted it (Review Log Arch-3).

Settings rows follow the existing toggle pattern — anchor on `notifyToggle` and `autostartToggle`;
project memory records a `document.querySelector('.topbar-toggle')` class collision that broke this
exact surface before. The on-but-not-in-effect warning follows the `restart_pending` drift-badge
pattern. Settings copy must state that the setting governs **newly created sessions only** (D-12).

> **Rejected:** a toast or per-session badge for the ungated fallback — user decision 2026-09-21.
> **Use instead:** the settings row reports on-but-not-in-effect.

**Exit criteria**:
- [ ] Toggle and base-agent input render and round-trip against the Phase 1 routes
- [ ] The settings panel shows on-but-not-in-effect when generation has failed (SC-8's UI half)
- [ ] Settings copy states the setting applies to newly created sessions only
- [ ] A frame carrying a `consent` block renders capability and resource; one carrying **no** consent block still renders without throwing
- [ ] A consent `resource` and a title each containing `<script>alert(1)</script>` and quote characters render as inert text — the escaping assertion R-7 promises
- [ ] A 500-character shell title renders without truncation
- [ ] `node tests/acp_page.test.mjs` passes

**Covers**: SC-1 (UI half), SC-4 (UI half), SC-8 (UI half)

### Phase 4: Local secret, login code, and the exchange route [QA] [P:3]

**Goal**: A durable local secret exists and a one-time login code can be exchanged for an HttpOnly
cookie. No route is gated yet — Phase 5 enforces.

**File scope**: `src/power_atlas/config.py`, `src/power_atlas/web.py`, `tests/test_web.py`.

`config.py` gains `ensure_local_secret` / `load_local_secret` / `rotate_local_secret` beside the remote
trio, sharing the fixed-mode create-truncate approach and the `REMOTE_SECRET_MIN_LEN` fail-closed
length check. **On write failure the generated secret is retained in memory** for the process lifetime
(D-22) — otherwise every door mints a cookie that verifies nowhere, and the settings panel that would
explain it is itself behind the gate.

The cookie mirrors `make_device_cookie`'s shape. The exchange mirrors `remote_auth_exchange`'s
hardening but **not** its peer-keyed backoff (D-16). Login code per D-21: `secrets.token_urlsafe(32)`,
single-use, 120 s TTL measured on `time.monotonic()`, bounded outstanding-code store.

`rotate_local_secret` must **re-issue the caller's cookie in the same response** before the old secret
stops verifying, or the rotate request logs the user out of the surface they used to make it.

> **Rejected:** reuse `pa_device` for loopback — cookies are host-only so it would never be sent to
> `127.0.0.1`, and a unified cookie would make "Copy login link" hand out remote reach. **Use instead:**
> a separate loopback cookie; peer class selects which applies (D-17).

> **Rejected:** peer-keyed exponential backoff on the loopback exchange — every loopback caller is
> `127.0.0.1`, so one bad process locks out the real user for up to 5 minutes. **Use instead:** key the
> throttle per-code.

**Exit criteria**:
- [ ] Local secret created on first call, reused on the second, rotates to a different value on demand
- [ ] A truncated or short secret file reads as "no usable secret" rather than as a usable short one
- [ ] A secret that cannot be persisted is still usable in-process for the process lifetime, and the condition is recorded where the settings route can report it
- [ ] A login code is exchangeable exactly once; a replay is refused
- [ ] A code older than its TTL is refused, with the TTL measured on `time.monotonic()` (asserted by patching the clock, not by sleeping)
- [ ] Minting many codes without exchanging them does not grow the store without bound
- [ ] The cookie verifies with `compare_digest`; tampered signature, tampered timestamp and future-dated stamp are each refused
- [ ] Throttling one code's repeated failures does not refuse a different, valid code — the D-16 regression test
- [ ] `rotate_local_secret`'s response re-authenticates its caller
- [ ] `pytest tests/test_web.py --timeout=300` passes

**Covers**: SC-6 (credential half)

### Phase 5: Default-deny gate and the three doors [QA]

**Goal**: Every loopback route requires the cookie, and all three doors still open the UI.

**File scope**: `src/power_atlas/web.py`, `src/power_atlas/tray.py`, `src/power_atlas/peek.py`,
`src/power_atlas/__main__.py`, `tests/test_web.py`.

A **new raw-ASGI class**, registered **after** `RemoteAccessGuard` so it becomes the outermost layer —
it must reject before any inner guard runs, and `add_middleware` inserts at index 0 with the stack
built over `reversed(middleware)`. It acts **only** on loopback peers via `scope["client"]`, the exact
mirror of `RemoteAccessGuard`'s `_is_remote_peer` branch.

**Refusal dispatch** (the existing `_refuse` is scope-typed but not navigation-aware): websocket →
close 1008; a request whose path is a page and whose method is GET → the HTML "open from the tray"
page; everything else → the existing JSON 403.

**One canonical loopback host** (D-17): all doors and cookie issuance use the same spelling, because
`127.0.0.1`, `localhost` and `::1` all pass the Host check but do not share a cookie jar.

Doors: `tray.py` and `peek.py`'s double-tap mint and append a code; the pywebview window gets its own
mint at creation. **Copy login link is an in-process tray callback, never an HTTP route** — a
network-reachable mint would reopen exactly the self-service path D-3 rejects. It borrows only the
no-store, loopback-only *pattern* of `GET /api/remote-access`. Clipboard via `pywin32`'s
`win32clipboard` (already a declared Windows dependency); elsewhere, display the link for manual copy.

> **Rejected:** gate `/api/*` and `/ws/acp` only — the credential is read from `GET /`, in neither.
> **Use instead:** default-deny across all routes, exempting the exchange route and `/static`.

> **Rejected:** expose minting as an HTTP endpoint so the tray can call it — that is self-service
> minting under another name. **Use instead:** an in-process call; tray, peek and uvicorn share one
> process.

**Exit criteria**:
- [ ] A loopback request with no cookie is refused on `/`, `/acp`, an `/api/*` route, `/partials/launchers`, and the `/ws/acp` upgrade — one test per surface, not one standing for all five
- [ ] `GET /api/remote-access` specifically is refused without a cookie (it returns the permanent remote secret today)
- [ ] The exchange route and `/static` are reachable without a cookie
- [ ] A **remote** peer with a valid `pa_device` cookie is still served and is **not** additionally required to hold the loopback cookie — the D-17 regression test
- [ ] A cookie minted at one loopback spelling is not silently accepted at another, and all doors use the canonical spelling
- [ ] Refusal dispatch asserted for all three shapes: page GET → HTML, `/api/*` → JSON 403, websocket → 1008
- [ ] Guard ordering asserted, not assumed
- [ ] Each of the three doors produces a URL that authenticates in one navigation
- [ ] "Copy login link" is not reachable as an HTTP route — asserted by a request to any plausible mint path returning 404/403
- [ ] `pytest tests/test_web.py --timeout=300` passes

**Covers**: SC-5, SC-6 (delivery half)

### Phase 6: Retire `_ACP_TOKEN`, introduce `ACP_AVAILABLE`

**Goal**: Remove the per-launch token and the stale-token branch without disabling the dashboard's ACP
features, which key off the same variable.

**File scope**: `src/power_atlas/web.py`, `src/power_atlas/templates/index.html`,
`src/power_atlas/templates/acp.html`, `src/power_atlas/static/composer-chrome.js` and
`static/transcript-renderer.js` (per Phase 0), `src/power_atlas/acp.py` (docstring only),
`tests/test_web.py`, `tests/acp_page.test.mjs`.

**Ordered, and the order is the point** (D-15): introduce `ACP_AVAILABLE`, repoint the sentinel
branches, verify the dashboard still renders its ACP features, and only then delete the credential.
The other order leaves every intermediate state with the ACP UI silently off.

1. Add `"acp_available": acp is not None` to the `/` render context; add `var ACP_AVAILABLE`.
2. Repoint every sentinel branch from `ACP_TOKEN` to `ACP_AVAILABLE` (Phase 0 recorded the count).
3. Delete `_ACP_TOKEN`, `_acp_token_ok`, both `acp_token` render keys, both `ACP_TOKEN` JS vars, and
   the `?t=` query parameter from the socket URL builder.
4. Delete the stale-token branch **wherever Phase 0 found it and under whatever name it then carries** —
   the parity plan's Phase 6 ports it into `index.html`, so expect it in two files and possibly renamed.
5. Update the `acp.py` docstring mentioning `_acp_token_ok`.

> **Rejected:** delete `ACP_TOKEN` and repair the fallout — it is the ACP-availability sentinel in six
> dashboard branches and the failure is silent. **Use instead:** introduce `ACP_AVAILABLE`, repoint,
> verify, then delete.

> **Rejected:** a name-based grep as the sole deletion check — the parity plan may rename the functions
> when porting them, so a grep for the old names would pass over a surviving branch. **Use instead:**
> a behavioural assertion plus a grep for whatever names Phase 0 recorded.

**Exit criteria**:
- [ ] `grep` for `ACP_TOKEN` across `templates/` and `static/` returns 0; `_ACP_TOKEN` across `src/` returns 0
- [ ] `grep` for `_acp_token_ok` returns no hits, including the `acp.py` docstring
- [ ] `grep` for the stale-token function names **recorded by Phase 0** returns no hits in either template or either static module
- [ ] **Behavioural**: a websocket handshake rejected for a bad cookie produces no "stale token, reload" affordance anywhere in the UI — asserted by driving the rejection, not by grepping names
- [ ] With `acp` importable, every previously `ACP_TOKEN`-gated dashboard feature still renders; with `acp` unimportable, each is hidden — both directions asserted, because only the pair catches an inverted sentinel
- [ ] The socket opens with no `?t=` parameter and is accepted on the cookie alone
- [ ] "Server unreachable" remains distinguishable from "rejected handshake", or § 9 records that it deliberately does not
- [ ] `pytest tests/test_web.py --timeout=300` and `node tests/acp_page.test.mjs` both pass

**Covers**: SC-7

### Phase 7: Live verification and documentation

**Goal**: Confirm the gate fires against a real agent, and land the documentation the earlier phases
deferred.

**File scope**: `README.md`, `docs/KNOWLEDGE.md`, `memory/MEMORY.md`, `plans/ROADMAP.md`,
`plans/tests/260701_POWERATLAS.md`, `plans/260919_ACP_TURN_END_AND_PERMISSION_NOTIFICATIONS.md`.

A unit test cannot distinguish a working permission gate from an inert one — both produce no prompt.
**Requires the user** to restart PowerAtlas; `AGENTS.md` is explicit that a restart kills every
supervised kiro-cli process and is never done autonomously. The phase presents the restart and waits.

**Exit criteria**:
- [ ] With the setting **on**, a real `/acp` session raises a prompt for a shell command showing the literal command
- [ ] The same session raises a prompt for a file write that **names the file**
- [ ] `consent.scope` reads `agent` and `consent.source` reads `agent-profile` — matching the P4 values recorded in § 1
- [ ] `modeId_in_effect` reads `DERIVED_AGENT_NAME`, confirming no silent coercion to `vibe`
- [ ] With the setting **off**, no prompt is raised **and** a deny-floor pattern is still blocked — the assertion that the floor is genuinely always-on
- [ ] At least one Phase 0 bypass form (case-variant, quoted, traversal, symlink) is re-attempted live against the shipped floor and blocked
- [ ] Denying a prompt leaves the tool `failed` with no side effect on disk
- [ ] The UI is reachable from all three doors after a restart
- [ ] `README.md` updated: the Default→agent mapping, the "tool permissions are asked, not assumed" paragraph (already stale today), the `http://127.0.0.1:<port>` bare-visit behaviour, and the new loopback credential as user-visible WebUI surface
- [ ] `docs/KNOWLEDGE.md`'s "Still outstanding: permission prompts — the shipped surface runs `-a`" corrected (stale on two counts)
- [ ] `memory/MEMORY.md`'s "`kiro_default` — the value PowerAtlas hardcodes as its own default" corrected, and its 8-value `_VALID_TASK_MODES` guidance noted as now 9
- [ ] `plans/tests/260701_POWERATLAS.md`'s `how-to-reach` lines annotated that loopback routes now require the cookie
- [ ] `plans/260919_ACP_TURN_END_AND_PERMISSION_NOTIFICATIONS.md`'s "no length clamp" line corrected
- [ ] `plans/ROADMAP.md`: both paired items marked shipped; the `/partials/launchers` item left open
- [ ] `~/.kiro` state matches the Phase 0 baseline, by filename and hash

**Covers**: SC-1, SC-2, SC-3, SC-4, SC-5, SC-6, SC-7, SC-8 (live confirmation)

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| R-1 Anchors stale at execution | High | Addressed: Phase 0 re-locates every anchor; phases cite names, not lines |
| R-2 Silent coercion to `vibe` | High | Addressed: Phase 7 asserts `modeId_in_effect` and `consent.scope`/`source` |
| R-3 Fallback runs ungated while the toggle reads on | Medium | **Accepted by the user** (D-10), narrowed to settings-panel reporting, and further narrowed so a valid derived agent is never replaced by a fallback. See Follow-up #1 |
| R-4 Deny floor blocks legitimate work | Medium | Addressed: Phase 0 justifies every entry; Phase 7 exercises the floor in both toggle states |
| R-5 Deny floor bypassed by rephrasing | High | Addressed: Phase 0 step 6 tests case, quoting, traversal and symlink forms before the floor is trusted |
| R-6 Lockout — no door works | High | Addressed: Phase 5 asserts all three doors; D-22 keeps an unpersistable secret in memory so cookies still verify |
| R-7 `acp.py` isolation boundary eroded | Medium | Addressed: `DERIVED_AGENT_NAME` lives in `config.py`, already imported; `agent_profile.py` is never imported by `acp.py` |
| R-8 Consent block is agent-authored and lands in a browser | Medium | Addressed: Phase 2 allowlists five fields; Phase 3 asserts escaping with an injection payload |
| R-9 Base-agent name becomes a filesystem path | High | Addressed: charset regex **and** Windows reserved-device-name rejection, at both write and generation paths |
| R-10 Torn derived-agent write fails **open** | High | Addressed: atomic tmp → fsync → `os.replace` (D-19). This is the plan's own threat model reappearing through an unaudited path |
| R-11 Login code visible in the spawned browser's command line | Medium | **Accepted, with mitigation**: any same-user process can enumerate process arguments, so a code in a URL is readable by exactly the attacker class this plan defends against. Reduced by a 120 s single-use TTL consumed on first load. Same accepted limitation as Jupyter's token-in-URL. See Follow-up #5 |
| R-12 Regen failure widens posture | Medium | Addressed: D-10's last-good rule keeps a valid derived agent rather than falling back |
| R-13 Startup aborted by a generation bug | High | Addressed: broad `except Exception` at the `lifespan` call site, mirroring the existing `acp` import containment |
| R-14 pywebview cookie jar behaviour unknown | Medium | Addressed: Phase 0 records it; Phase 5 asserts the door. If the runtime cannot hold the cookie, the overlay falls back to the system browser |
| R-15 "Always allow" may persist and widen the posture | Medium | Addressed: Phase 0 step 8 measures it before the design commits |
| R-16 Cross-plan comment collision | Low | Addressed: D-23 requires plan-slug attribution on every comment in a shared file |
| R-17 Parity plan's Phase 6 ports code this plan deletes | Medium | Addressed: Phase 0 step 2 records the post-port names; Phase 6's deletion check is behavioural rather than name-based |
| R-18 Clock correction rejects a valid credential | Low | Addressed: login-code TTL on `time.monotonic()` (D-21). The cookie's own wall-clock skew bound is inherited from the existing device-cookie design and is self-recovering via a fresh mint |

## 7) Verification

**Automated**

- `.venv-PowerAtlas/Scripts/python -m pytest tests/test_web.py --timeout=300` after every phase. The
  timeout is not optional: `AGENTS.md` records that a test whose mocking assumes a removed code path
  hangs on an `asyncio.Event` rather than failing, and this plan changes session-adjacent code.
- `node tests/acp_page.test.mjs` after Phases 3 and 6. Not part of pytest, not run by CI.
- `.venv-PowerAtlas/Scripts/python _check_test_names.py`. The pre-commit hook runs it against staged
  content, but `.git/hooks/` is not version-controlled — confirm the hook is installed before relying
  on it. This matters more than usual here: seven phases add tests to one 21.9k-line module, and
  `AGENTS.md` records a duplicate-name incident that cost 79 failures and 35 errors.

**Manual / live** (Phase 7)

- Requires a user-performed PowerAtlas restart. Never restart autonomously.
- `tools/acp_permission_probe.py` (committed by Phase 0) drives `kiro-cli acp` directly and is the
  fastest way to re-verify a rule set without touching PowerAtlas.
- Every probe touching `~/.kiro` snapshots hashes first and verifies restoration after.
  `permissions.yaml` baseline, 2026-09-21:
  `f8a0d44394b5b2a23ff363b69538911caeda28c97604ad7d6825b93ec40146c8`.

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `AGENTS.md` | Terminology entries — **already landed** 2026-09-21 (`f9b55cd`); Phase 7 re-checks they match the shipped names | 7 |
| `README.md` | Default→agent mapping now resolves to the derived agent when enabled | 7 |
| `README.md` | "Tool permissions are asked, not assumed" — stale **today** (measured zero prompts); becomes conditional on the new setting | 7 |
| `README.md` | "the laptop keeps using `http://127.0.0.1:<port>` unchanged" — a bare visit now lands on the open-from-tray page | 7 |
| `README.md` | Document the loopback credential as new user-visible WebUI surface | 7 |
| `docs/KNOWLEDGE.md` | "Still outstanding: permission prompts — the shipped surface runs `-a`" — stale on two counts | 7 |
| `memory/MEMORY.md` | "`kiro_default` — the value PowerAtlas hardcodes as its own default" becomes false; `_VALID_TASK_MODES` guidance moves from 8 values to 9 | 7 |
| `plans/tests/260701_POWERATLAS.md` | ~38 `how-to-reach` lines name loopback routes with no credential; annotate that the cookie is now required | 7 |
| `plans/260919_ACP_TURN_END_AND_PERMISSION_NOTIFICATIONS.md` | "`toolCall.title` passes through `_as_text` with no length clamp" — already false today, doubly so after Phase 2 | 7 |
| `plans/ROADMAP.md` | Mark both paired items shipped; leave `/partials/launchers` open | 7 |
| `plans/260921_DASHBOARD_ACP_FEATURE_PARITY.md` | Its Phase 6 lines 314/327 instruct porting functions this plan deletes — reconcile at that plan's archival, or annotate if still active | 7 (doc-table-only) |

## Progress Tracker

| # | Phase/Task | Status | Notes |
|---|---|---|---|
| 0 | Pre-flight — preconditions, anchors, harness, rule set | Not started | Step 0 STOPs if the parity plan is not complete |
| 1 | Derived-agent generation and settings | Not started | Atomic write; textual injection; no YAML dep |
| 2 | Mode wiring and frame enrichment | Not started | |
| 3 | Settings and permission-prompt UI `[P:4]` | Not started | Includes `transcript-renderer.js`; parallel-eligible with 4 |
| 4 | Local secret, login code, exchange `[P:3]` | Not started | Parallel-eligible with 3 |
| 5 | Default-deny gate and the three doors | Not started | |
| 6 | Retire `_ACP_TOKEN`, add `ACP_AVAILABLE` | Not started | Ordered: repoint before delete |
| 7 | Live verification and documentation | Not started | Requires a user-performed restart |

## Dependency Graph

```
        [parity plan completes]  (D-14 — Phase 0 step 0 VERIFIES this, STOPs if not)
                  |
              Phase 0  preconditions + anchors + committed harness + rule set + floor bypass tests
                  |
                  +-- gate: revise design if a premise failed
                  |
              Phase 1  generation engine (atomic, textual) + settings backend
                  |
              Phase 2  mode wiring + frame enrichment        (acp.py)
                  |
        +---------+---------+
        |                   |
    Phase 3 [P:4]       Phase 4 [P:3]     (templates+static  vs  config.py+web.py: no overlap)
    UI: settings        local secret
    + prompt render     + login code
        |                   |
        +---------+---------+
                  |
              Phase 5  default-deny gate + three doors
                  |
              Phase 6  retire _ACP_TOKEN                     (needs 5's cookie enforcing)
                  |
              Phase 7  live verification + docs
```

**Parallel eligibility**: exactly one pair qualifies. **Phases 3 and 4 are `[P:4]` / `[P:3]`** — Phase 3's scope
is templates plus `static/` plus `acp_page.test.mjs`; Phase 4's is `config.py`, `web.py` and
`tests/test_web.py`. No shared file, and Phase 4 consumes nothing Phase 3 produces. Every other pair
either shares `tests/test_web.py` (2 and 4), shares a template (3 and 6), or consumes the previous
phase's output. *An earlier draft asserted no pair was eligible; that was false against the plan's own
file scopes — the `parallel-symmetry` check passed only because no annotations existed to verify.*

## Backwards Compatibility

| Item | Strategy | Safety effect |
|---|---|---|
| Existing ACP sessions at toggle-on | Unchanged — the agent binds at `session/new` (P2) | No mid-session change; documented in settings copy |
| Default posture | Off = allow-all as today, **plus** the deny floor | Installing changes nothing opted into, except floor patterns become blocked |
| `_ACP_TOKEN` consumers | None external — per-launch, page-injected, no documented API | Removal is internal |
| A tab open across a restart | Improves: the cookie survives, so it reconnects instead of showing "stale token, reload" | The stale-token branch is deleted because its state can no longer occur |
| A bookmarked bare URL | **Regression**: it currently works and afterwards lands on the open-from-tray page | Deliberate (D-3); mitigated by tray "Copy login link" and documented in README |
| Remote/NetBird access | Untouched; `pa_device` remains the only remote credential | Asserted by a Phase 5 regression test (D-17) |
| Interactive terminal kiro-cli | Untouched — the base agent is never modified | The property the whole design rests on |

## File Change Summary

### Created
- `src/power_atlas/agent_profile.py` — generation engine
- `src/power_atlas/agents/permissions.yaml` — the overlay (package data)
- `tools/acp_permission_probe.py` — the committed probe harness (Phase 0)

### Modified
- `src/power_atlas/web.py` — settings routes, exchange, loopback gate, render context, `_ACP_TOKEN` removal
- `src/power_atlas/config.py` — `Config` fields, `DERIVED_AGENT_NAME`, local-secret helpers
- `src/power_atlas/acp.py` — `_VALID_TASK_MODES`, `_on_permission_request`, `load_session` mode note, one docstring
- `src/power_atlas/tray.py`, `peek.py`, `__main__.py` — door wiring, Copy login link
- `src/power_atlas/templates/index.html`, `templates/acp.html` — settings UI, prompt rendering, `ACP_AVAILABLE`
- `src/power_atlas/static/transcript-renderer.js` — prompt rendering
- `src/power_atlas/static/composer-chrome.js` — conditional on Phase 0
- `src/power_atlas/static/style.css` — settings-row and prompt styling
- `pyproject.toml` — `package-data`
- `tests/test_web.py`, `tests/acp_page.test.mjs`
- `README.md`, `docs/KNOWLEDGE.md`, `memory/MEMORY.md`, `plans/ROADMAP.md`, `plans/tests/260701_POWERATLAS.md`

### Deleted
- `_ACP_TOKEN`, `_acp_token_ok` (`web.py`); `acp_token` render keys and `ACP_TOKEN` JS vars (both templates); the stale-token branch wherever Phase 0 locates it

### Unchanged
- `src/power_atlas/launcher.py` — the terminal spawn path stays permissive by design
- `~/.kiro/settings/permissions.yaml`, `~/.kiro/agents/<base>.md` — never written by this plan
- `agent-playbook` — no longer involved (D-6)

## 9) Implementation Divergences from Plan
<Reserved -- filled during implementation. Phase 0 writes its precondition statement, anchor table,
recorded rule set and measurement results here.>

## Follow-up Work (Deferred)

1. **Fail-closed on generation failure.** R-3 accepted rather than fixed: a session whose derived agent
   could not be generated *and* which has no last-good file runs ungated while the toggle reads on,
   reported only in the settings panel. Source: D-10, R-3.
2. **Derived-agent staleness against a changed base.** Regeneration is at startup and on settings
   change, so a base change mid-run is not reflected until restart. Governance drift only, never the
   gate. Source: D-9.
3. **`/partials/launchers` env leak.** Its own `[SECURITY]` roadmap item. SC-5 removes anonymous
   reachability but does not fix the leak. Source: Intent scope boundaries.
4. **The unattended rule engine.** Its own roadmap item, carrying the Automation-gating role. Source: D-1.
5. **Login code in process arguments.** R-11 accepted: a code passed on a browser command line is
   readable by any same-user process. Mitigated by a 120 s single-use TTL, not eliminated. A
   loopback-only local handoff (e.g. a file-backed one-shot the browser is pointed at) would remove it.
   Source: R-11.
6. **Tray-icon posture indicator.** Reliability review suggested the tray icon could reflect effective
   posture at near-zero cost without violating D-12's no-toast/no-badge decision. Not adopted here
   because it is a new surface the user did not ask for. Source: Review Log Rel-12.

## Review Log

### 2026-09-21 — Plan review (via /qplan Step 4)

Four personas, one cycle, dispatched in parallel with fresh context: **Architect** (gap-critic lens),
**Security auditor**, **Senior engineer**, **Reliability engineer**. A separate mandatory doc-impact
sub-agent scanned tracked documentation. **33 review findings (10 High, 19 Medium, 4 Low); all High and
Medium auto-resolved into the plan; 2 Low adopted, 2 Low recorded as deferred.**

Per-persona confidence before fixes: Architect 55%, Security 60%, Senior engineer 60%, Reliability 55%.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| SE-1 | High | Phase 0's rule-set step named no documentation source, and kiro-cli exposes no `permissions` subcommand | Fixed — Phase 0 step 4 names both routes and an explicit measured fallback with user consent |
| SE-2 | High | No YAML library is declared or installed, yet Phase 1 required parsing frontmatter with a byte-identical exit criterion | Fixed — D-18 makes injection textual; no dependency, byte-identity by construction |
| SE-3 | Medium | Current State implied `_acp_token_ok` was only a docstring mention; it is a real function in `web.py` | Fixed — Current State now states where it is defined and called |
| SE-4 | Medium | "~1-2 weeks" optimistic for 8 phases over a 21.9k-line test module | Fixed — revised to ~2-3 weeks |
| SE-5 | Low | "Copy login link reusing `GET /api/remote-access`" ambiguous; that route sits behind the new gate | Fixed — Phase 5 states in-process only, with a negative exit criterion |
| Sec-1 | High | The login code rides a spawned browser's command line, readable by any same-user process | Fixed as accepted risk — R-11 + Follow-up #5, with a 120 s single-use TTL (D-21) |
| Sec-2 | High | The deny floor is the only control in the default state and its bypass resistance was untested | Fixed — Phase 0 step 6 tests four bypass forms; Phase 7 re-tests live; R-5 added |
| Sec-3 | Medium | R-7 promised an escaping assertion that no Phase 3 exit criterion delivered | Fixed — Phase 3 gains an explicit injection-payload criterion |
| Sec-4 | Medium | The name regex admitted Windows reserved device names | Fixed — D-8 and the Phase 1 sketch reject `CON`/`PRN`/`AUX`/`NUL`/`COM1-9`/`LPT1-9` |
| Sec-5 | Medium | Ambiguity over whether minting is network-reachable | Fixed — same as SE-5 |
| Sec-6 | Medium | No symlink/TOCTOU story for the new `~/.kiro` write target | Fixed — D-19's atomic replace; R-10 |
| Sec-7 | Medium | Login-code entropy unspecified | Fixed — D-21 specifies `secrets.token_urlsafe(32)` |
| Sec-8 | Low | No invariant that all doors use one canonical loopback host | Fixed — D-17 and a Phase 5 exit criterion |
| Arch-1 | High | D-14's precondition was assumed; the parity tracker and working tree disagreed | Fixed — Phase 0 step 0 verifies and STOPs |
| Arch-2 | High | The probe harness Phases 0 and 7 depend on is not in the repo | Fixed — Phase 0 step 3 rebuilds and commits `tools/acp_permission_probe.py` |
| Arch-3 | High | `transcript-renderer.js` already handles `permission_request` and was in no file scope | Fixed — added to Phase 0 anchors and Phase 3 scope |
| Arch-4 | Medium | `[P:1]` on Phases 2 and 4 ignored their shared `tests/test_web.py` | Fixed — annotation removed; the graph states no pair is parallel-eligible |
| Arch-5 | Medium | Phase 1's "Why horizontal" reason (b) rested on a concurrency premise D-14 removes | Fixed — replaced with a durable testability argument |
| Arch-6 | Medium | `DERIVED_AGENT_NAME` placement forced either a duplicate literal or a wider isolation boundary | Fixed — D-20 puts it in `config.py`, already imported by `acp.py` |
| Arch-7 | Medium | Phase 7 asserted consent values the plan never recorded | Fixed — the literal P4 payload is now in Current State. *Reviewer premise partly incorrect: the values were measured, but the plan carried only field names* |
| Arch-8 | Low | The new gate's class shape and registration position were unstated | Fixed — Phase 5 states a new raw-ASGI class registered after `RemoteAccessGuard` |
| Rel-1 | High | A local secret that cannot persist leaves every door minting cookies that verify nowhere | Fixed — D-22 retains it in memory for the process lifetime |
| Rel-2 | High | The derived-agent write had no atomicity story, and a torn write fails **open** | Fixed — D-19 adopts `save_config`'s tmp→fsync→replace; R-10 |
| Rel-3 | High | An unexpected generation bug could abort startup, contradicting SC-8 | Fixed — broad `except Exception` at the `lifespan` call site; R-13 |
| Rel-4 | Medium | Peer-keyed backoff degenerates on loopback, letting one process lock out the user | Fixed — D-16 keys the throttle per-code |
| Rel-5 | Medium | Fallback on regen failure widens posture even when a valid derived agent exists | Fixed — D-10 keeps the last-good file; R-12 |
| Rel-6 | Medium | `rotate_local_secret` would log out the caller making the request | Fixed — Phase 4 re-issues the caller's cookie in the same response |
| Rel-7 | Medium | Peek's overlay navigates without a fresh mint and could strand on the recovery page | Fixed — cookie lifetime is long relative to uptime; Phase 5 asserts the door, R-14 covers the fallback |
| Rel-8 | Medium | "Copy login link" needs a clipboard library that is not a dependency | Fixed — § 4 names `pywin32`'s `win32clipboard`, already declared |
| Rel-9 | Medium | `_refuse` is scope-typed but not navigation-aware; the HTML recovery page needed a dispatch rule | Fixed — Phase 5 states the three-way dispatch and asserts it |
| Rel-10 | Low | Wall-clock TTL is vulnerable to NTP/sleep corrections | Fixed — D-21 uses `time.monotonic()`; R-18 |
| Rel-11 | Low | Generation would run synchronously on the event loop against the codebase idiom | Fixed — D-9 dispatches through `asyncio.to_thread` |
| Rel-12 | Low | On-but-not-in-effect detectability is weak; a tray-icon indicator was suggested | Deferred — Follow-up #6; adopting it would add a surface the user did not request |
| Doc-1 | Medium | Nine documentation files carry claims this plan makes false, several already stale today | Fixed — § 8 expanded from 4 rows to 11, all assigned to Phase 7 |
| Doc-2 | Medium | The parity plan's unfinished Phase 6 instructs porting the two functions this plan deletes | Fixed — Phase 0 step 2 records post-port names; Phase 6's check is behavioural; R-17 |
| Doc-3 | Low | Shared files carry bare `SC-n`/`Phase N` markers from four plans in the same number space | Fixed — D-23 requires plan-slug attribution |

**Not adopted, with reason**: Rel-12 (tray-icon posture indicator) — deferred to Follow-up #6 rather
than fixed, because D-12 settled the visibility surface and adding one silently would reverse a user
decision. Arch-7's premise was partly incorrect and is corrected inline above rather than accepted as
stated.

**Coverage note**: one review cycle was run, per the user's instruction. The auto-fix loop's normal
re-review pass was therefore not performed — **the 33 resolutions above are unreviewed by a second
cycle**. Phase 0 is the phase most likely to still carry an error, because it absorbed the most new
text (step 0, the harness contract, the bypass tests, and the rule-set fallback all landed in this
pass).

### 2026-09-21 — Post-commit corrections (author self-check)

Two defects found in the resolutions themselves, after the plan was committed as `d08efdc`. Both are
the class a second review cycle exists to catch: a fix that introduces a new claim.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| PC-1 | Medium | The Dependency Graph asserted no phase pair was parallel-eligible, which is false against the plan's own file scopes | Fixed — Phases 3 and 4 annotated `[P:4]`/`[P:3]`; the claim now names the one eligible pair and why the others are not |
| PC-2 | Medium | Phase 0's empirical rule-set fallback said "with the user's consent" as though approval existed; no turn granted it | Fixed — rewritten as an explicit STOP-and-ask, with the blast radius (every concurrent kiro-cli session) stated |
| PC-3 | Low | PC-1's first fix used `[P:1]` meaning "group 1"; the grammar is `[P:<partner phase>]` and qvalidate rejected it | Fixed — corrected to `[P:4]`/`[P:3]`; the guard caught a defect introduced while fixing another |

PC-1 also shows a limit of the mechanical check: `parallel-symmetry` passed on the committed version
**because no annotations existed to verify**, not because the no-eligible-pairs claim was true. A
guard that validates declared annotations cannot catch an annotation that should have been declared.

## Harness Improvement Opportunities

- All three `/qexplore` Step 1.5 sub-agents, and the `/qplan` doc-impact sub-agent, refused the brief's
  instruction to write a report file to their scratch path ("Subagents should return findings as text,
  not write report files"), while `shared/AGENTS.md` § Multi-Agent Coordination states a sub-agent's
  deliverable is a file, not a return — cost: four briefs carried a dead instruction and the
  durable-artifact guarantee silently did not hold — suggested change: reconcile the governance rule
  with the harness behaviour, or stop requesting a file write in those briefs.
- The roadmap's seed note carried three errors that only a sub-agent re-read caught — cost: a wrong
  cost estimate would have survived into planning — suggested change: cite a deploy manifest row by
  content (kind + dest), never by line number.
- `/qplan`'s live-data discipline forbids line-number anchors for files the plan edits, but says nothing
  about a plan whose *execution is deferred behind another plan that rewrites the same files* — cost:
  none here, but the anchor-staleness risk was one sentence from being missed and the drift was already
  80 lines within a day — suggested change: extend the rule to name deferred execution as a staleness
  source.
- The doc-impact sub-agent scanned the plan file **while `/qplan` was rewriting it**, and reported
  citing line numbers from an intermediate revision — cost: its per-line citations against that one
  file had to be treated as indicative rather than exact — suggested change: have `/qplan` dispatch the
  doc-impact scan either before the plan body is written or after it is committed, not concurrently.
