# ACP Permission Profile and Loopback Credential

> **Date**: 2026-09-21
> **Status**: In Progress — Phases 0-4 complete, reviewed, Green (deny floor removed from scope
> entirely, user decision 2026-09-22, see § 9); Phase 5 next  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
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
  behaviour, unchanged, the default) and **on** = explicit rules reproducing kiro-cli's default
  posture. **No PowerAtlas-authored deny floor.** D-13's floor is removed from this plan's scope —
  user decision 2026-09-22, made after Phase 0 measured live that a bespoke pattern-based floor
  cannot be built with a defensible bypass-resistance guarantee at this layer (§ 9 Gate statement),
  and after concluding the future unattended-automation follow-on (D-1's out-of-scope rule engine)
  will need its own protection model regardless, since it runs with nobody watching — a different
  problem than this plan's interactive-session scope. **off** is therefore a genuine no-op over
  today's behaviour; **on** is where this plan's actual posture improvement lives.
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
running shell or writing files, configurable between allow-all and kiro-cli's default posture (no
PowerAtlas-authored deny floor — D-13 superseded 2026-09-22, see § 9); and put a one-time login code
plus HttpOnly cookie in front of every loopback route, retiring the page-injected `_ACP_TOKEN` it
replaces.

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
| D-13 Rule set | ~~Two states plus an always-on **deny floor**~~ **SUPERSEDED 2026-09-22: two states, no floor** | (orig.) borrowed from Kiro Crew's "never-bypassed" bundled deny list | Phase 0 measured live that the borrowed precedent's "never-bypassed" characterization is false (3 of 4 tested rephrasing forms bypassed a floor entry with zero prompt — § 9 Gate statement). User decision 2026-09-22: drop the floor entirely rather than patch it — a bespoke pattern-based floor can't be built with a defensible guarantee at this layer, and the future unattended-automation follow-on (D-1's out-of-scope rule engine) needs its own protection model regardless, since "an ask rule with nobody watching is a bounded wait, not a policy" (D-1) applies there too |
| D-14 Execution order | Starts only after the parity plan completes; **Phase 0 step 0 verifies it** | Backend-first concurrent; fully parallel | User decision 2026-09-21. The precondition is checkable, so it is checked rather than assumed |
| D-15 `ACP_TOKEN` sentinel | Introduce `ACP_AVAILABLE` **before** removing the credential | Delete and repair the fallout | Six dashboard branches use it as the ACP-availability flag, and the failure is silent |
| D-16 Login-code exchange | Mirror `remote_auth_exchange`'s hardening, but **not** its peer-keyed backoff | Reuse the peer-keyed backoff verbatim | On loopback every caller is `127.0.0.1`, so a peer-keyed lockout lets one bad process throttle the legitimate user for up to 5 minutes |
| D-17 Cookie isolation | Separate cookies; **peer class selects the credential**; one canonical loopback host | One unified cookie | Cookies are host-only. A canonical host is required because `127.0.0.1`, `localhost` and `::1` all pass the Host check but do not share a cookie jar |
| D-18 Frontmatter injection | **Textual insert/replace between the `---` fences. No YAML parsing, no new dependency** | PyYAML; `ruamel.yaml` for round-trip fidelity | No YAML library is declared *or installed*. Byte-identity is the exit criterion, and only not-parsing achieves it by construction. An existing `permissions:` key is **replaced**, not merged |
| D-19 Derived-agent write | **Atomic**: tmp → `fsync` → `os.replace`, reusing `save_config`'s pattern | In-place truncate, mirroring `_write_remote_secret` | Opposite failure directions: a torn secret fails closed (length check), a torn `permissions:` block fails **open** (P5). The secret's pattern must not be copied here |
| D-20 `DERIVED_AGENT_NAME` | Defined in `config.py` | In `agent_profile.py`; duplicated literal in `acp.py` | `acp.py` already imports from `config` (and `launcher`, its only other intra-package module); this adds a name to an existing import line, no new module, and no sync-bug risk. *(Corrected 2026-09-22: the original wording "`config` and nothing else" was false — `launcher` was always the second module.)* |
| D-21 Login code | `secrets.token_urlsafe(32)`; single-use; **120 s TTL on `time.monotonic()`**; bounded outstanding-code store | Leave to the implementer; wall-clock TTL | Entropy is load-bearing because a same-user attacker reaches the exchange directly. Monotonic avoids NTP/sleep clock corrections; the codes are process-local so wall time buys nothing |
| D-22 Local-secret write failure | Keep the generated secret **in memory** for the process lifetime; log ERROR; report in settings | Fail startup; proceed with no secret | Without this, every door mints a cookie that verifies nowhere and the settings panel explaining it is itself behind the gate. In-memory degrades to today's per-launch `_ACP_TOKEN` lifetime |
| D-23 Comment attribution | Every comment this plan adds to a shared file carries the plan slug | Bare `Phase N` / `SC-n` markers | The shared files already carry bare markers from four other plans in the same number space |

## 4) External Dependencies & Costs

### Required external changes

| Category | Change needed | Owner | Status |
|---|---|---|---|
| Secrets / Env vars | New on-disk local secret at `CONFIG_DIR/local-secret`, created at first start. No external provisioning | Implementer | Pending |
| Rollout / cutover | None. Defaults **off** (allow-all), and **off** is now a genuine no-op over today's behaviour (no floor, D-13 superseded) — installing this release changes no permission behaviour until the user opts in | — | N/A |
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
- [x] Step 0's precondition verified and recorded, or the phase stopped and the user asked
- [x] Every anchor re-located; the anchor → current file:line table recorded in § 9
- [x] `ACP_TOKEN` occurrence count recorded and compared against 14
- [x] The stale-token function names actually present after the parity port are recorded
- [x] `tools/acp_permission_probe.py` committed, and a run of it reproduces P1's mode-catalogue output — verified; committed as `ea79482` once the orchestrator reviewed it (Gate decision no longer blocks this — it's independently useful infrastructure)
- [x] Default rule set recorded, each rule tagged documentation-sourced or measured, with its source
- [x] Deny-floor list drafted, every entry justified in one line
- [x] Floor bypass attempted in all four forms; the matching semantics (literal vs canonicalized) recorded
- [ ] Each of the 7 unexercised capabilities recorded as fires / does-not-fire with its consent payload — **6 of 7 measured live; `power` is untestable on this machine (no installed powers) — see § 9. Environment limitation, not deferred to a later phase: `power` was never part of any Success Criterion or the Gate's own findings, and the Gate was resolved with this gap already accounted for. No further action needed unless a future machine has powers installed to re-probe with.**
- [x] "Always allow" persistence answered yes/no, with the diffed paths named
- [x] `~/.kiro` baseline recorded by filename and hash
- [x] A statement in § 9 confirming the design still holds, or naming what must change — **it names what must change; the Gate fired (see § 9)**

**Gate fired.** Step 6's bypass-resistance test failed: the deny floor was defeated by 3 of its 4
tested rephrasing forms (case-variant, alias/quoted form, `../`-traversal), confirming kiro-cli's
shell-capability matching is a literal, case-sensitive string match with no canonicalization —
directly contradicting D-13's characterization of the borrowed Kiro Crew deny list as
"never-bypassed" (public trackers document the same literal-matching class of bypass against that
list: kirodotdev/KiroCrew#8387, kirodotdev/Kiro#11498, kirodotdev/KiroCrew#8240). Per this phase's
brief, this is named explicitly as a Gate-firing condition. **Status: blocked. See § 9 for full
findings and the specific design question this raises for Phase 1.** *(Resolved 2026-09-22 — see
§ 9 "Gate resolution": the floor was dropped from scope entirely, not patched.)*

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

The rule assembly is `(allow-all | transcribed defaults)` — **no floor layer** (D-13 superseded
2026-09-22, § 9). The `on` branch's `transcribed defaults` still needs Phase 0's `exclude` finding
(§ 9 Step 4): a same-capability blanket `ask` rule silently defeats a narrower `allow` rule for the
same resource unless the blanket rule carries an `exclude` populated from the narrower rule's own
`match` patterns. That finding is independent of the floor decision and still applies.

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
- [x] `build_derived_agent` round-trips a fixture base agent: every byte outside the injected `permissions:` block is identical, asserted by comparing the full text with that block excised — `excise_permissions` is the shared inverse both the test and the pre-publish self-check use; 23 injection tests (20 functions, 3 of them parametrised) including CRLF, an inline `permissions: {}`, a `permissions :` spaced key, a nested `permissions:` left alone, idempotence, a closing fence carrying trailing whitespace, a frontmatter region holding body prose, an indented or sequence root refused, and the machine's real `kiro_default.md`
- [x] A base agent that **already declares** `permissions:` has it replaced, not duplicated or merged — and a base carrying **two** top-level `permissions:` keys collapses to one, since leaving one behind is the same silent fail-open. The Step 5 review caught three spellings the first implementation replaced only *partially*, each manufacturing the fail-open shape: a **column-0 block sequence** (`permissions:` then `- capability: …` unindented, which left the base's rule items stranded after the injected mapping — a root document mixing a mapping with a sequence, which does not parse), a **comment between the key and its indented value**, and a **quoted key** (`"permissions":`). All three are now regression-tested, as is the inverse — a comment introducing the *next* key, and a sequence belonging to an *earlier* key, are both left where they were
- [x] Invalid base names raise rather than resolving a path — asserted for `../x`, an absolute path, an empty string, an over-length name, and `CON`, plus `..`, a Windows drive path, `a/b`, `a\b`, `con`/`NUL`/`com1`/`LPT9`, an embedded NUL, and non-string types; both `validate_base_agent_name` and `base_agent_path` are asserted, so no path is built either way
- [x] The base agent is never modified — SHA-256 asserted before and after
- [x] The write is atomic: a simulated failure mid-write leaves the previous derived agent intact and no `.tmp` residue — `os.replace` patched to raise `EACCES`; the previous file's bytes and its block state (`on`) both asserted unchanged
- [x] `GET`/`POST /api/acp-permissions` round-trip the boolean; the POST rejects a non-boolean body — 8 rejected bodies (`"true"`, `1`, `0`, `None`, `{}`, `[]`, a list, a wrong key), each asserted to leave the stored value untouched. The route **sets** rather than toggles (unlike `/api/notifications`): a toggle cannot express "make sure this is off"
- [x] `acp_permission_base_agent` rejects a name failing validation via `/api/save-setting` — 11 values, each asserted not to reach `config.toml`
- [x] Generation is invoked from `lifespan` and the settings write path, asserted through the existing `async with web_mod.lifespan(None)` seam
- [x] A generation failure inside `lifespan` does not prevent startup, asserted by raising a non-`AgentProfileError` (`RuntimeError`) from a patched generator
- [x] On regen failure with a valid derived agent already on disk, that file is **kept**, not replaced by a base-agent fallback (D-10) — asserted through `lifespan` with the base agent deleted after a successful generation. No base-agent fallback is written in any path: "fall back to the base agent" is resolved at mode-selection time (Phases 2-3), never by copying the base over the derived file
- [x] `pyproject.toml` `package-data` includes the overlay, and an `importlib.resources` read confirms it is reachable at runtime rather than merely present in the source tree — both halves asserted, because the `importlib.resources` read alone passes under an editable install regardless. Additionally **measured**: `pip wheel . --no-deps` produces a wheel containing `power_atlas/agents/permissions.yaml`
- [x] **(added, Step 5 review, 2026-09-22 — with the deny floor dropped, this is the sole remaining backstop for `on`)** The `on` branch's assembled rule set names every capability this design wants to gate explicitly — all 9 capabilities Phase 0 § 9 Step 7 measured on the ACP surface (`fs_read`, `fs_write`, `shell`, `web_fetch`, `web_search`, `mcp`, `subagent`, `skill`, `power`) are named, and the test fails when any one is removed (mutation-verified). The 3 doc-only schema names (`context`, `diagnostics`, `sandbox_network`) are deliberately **not** named, with the reason written into the overlay itself and carried to Phase 7 — see § 9 Phase 1
- [x] **(added, Step 5 review, 2026-09-22)** No same-capability (or `all`) blanket `ask` rule sits beside a narrower `allow` rule without a populated `exclude` — the checker is asserted against a negative fixture reproducing Phase 0 § 9 Step 4's measured defeat in both its forms (same-capability `{shell, match, allow}` + `{shell, ask}`, and the `all`-scoped variant), and mutation-verified: deleting the shipped `shell` `exclude` line fails the test
- [x] **(added, Step 5 review, 2026-09-22)** A recorded decision on Gate finding #6 — **split decision, both halves shipped or named.** A **pre-publish structural self-verification** ships now: the staged file is read back and re-excised **before** `os.replace`, and generation fails unless the block is byte-identical to the assembled overlay *and* every other byte still matches the base — so the last-good file is kept because publication never happens. *(Ordering corrected in the Step 5 review cycle: the first implementation verified after `os.replace`, which detected a bad splice with the last-good file already destroyed.)* Live **bind** confirmation is explicitly deferred to Phase 7, because it requires a kiro-cli spawn, an auth round trip and a *triggered* prompt — P5 proves `modeId_in_effect` alone is insufficient — which cannot run inside `lifespan` or a settings write, and `kiro-cli agent validate` is unusable for `.md` agents. Full reasoning and what the self-check does *not* cover: § 9 Phase 1
- [x] `pytest tests/test_web.py --timeout=300` passes — 1562 passed, 1 skipped (after the Step 5 review cycle; 1526 before it). The other 8 pytest modules also pass (440 passed, 2 skipped), and `_check_test_names.py` is clean (9 files)

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
- [x] `_VALID_TASK_MODES` accepts `DERIVED_AGENT_NAME` imported from `config`, with no duplicated literal and no new intra-package import in `acp.py` beyond `config` — added to the existing `from .config import CONFIG_DIR` line; the frozenset names the constant. `test_handle_new_accepts_the_derived_agent_as_a_mode` asserts `acp.DERIVED_AGENT_NAME is config.DERIVED_AGENT_NAME`, which a copied literal would fail
- [x] Its docstring no longer claims to be a pure mirror of the vendor enumeration — the constant has no runtime docstring, so this is its comment block: it now opens "Every modeId `_handle_new` accepts. **No longer a pure mirror of kiro-cli's own enumeration**"
- [x] A `permission_request` frame carries all five consent fields — asserted against a fixture copied from the P4 payload recorded in § 1 — `test_permission_request_forwards_the_measured_consent_payload`, exact-equality against the literal § 1 payload (which is what proves `askType`/`consentRound`/`workspaceRoot` are dropped)
- [x] `_project_consent` drops an unexpected key rather than forwarding it — asserted with an extra field — `test_project_consent_drops_an_unexpected_key`, using the `mcp` extras Phase 0 § 9 measured (`mcpTool`, `agentManagesTrust`) plus a `matchedRule.match` list
- [x] A title longer than `MAX_PERMISSION_TITLE_CHARS` reaches the frame **unclamped** and the notification **clamped** — one test asserting both halves — `test_permission_request_title_is_clamped_for_the_toast_only` (the morphed former `test_permission_request_title_is_clamped`)
- [x] The `load_session` mode situation is resolved so a reader cannot conclude the load path re-asserts the posture — dated comment route (the option this phase's own wording sanctions): a P2-citing comment at the `session/load` call site, plus a line in `load_session`'s docstring and in `_build_kas_session_params`'s. Not the plumbing route — see § 9 Phase 2
- [x] `pytest tests/test_web.py --timeout=300` passes — 1568 passed, 1 skipped (1562 + 6 new before this phase). The other 8 pytest modules also pass (440 passed, 2 skipped) and `_check_test_names.py` is clean (9 files)

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
- [x] Toggle and base-agent input render and round-trip against the Phase 1 routes — `acp_page.test.mjs` settings round-trip checks; confirmed live over HTTP in Step 5b
- [x] The settings panel shows on-but-not-in-effect when generation has failed (SC-8's UI half) — badge, warning line and (after the review fix) the settings-gear dot; also warns when a failed base-agent change left the previous profile in effect
- [x] Settings copy states the setting applies to newly created sessions only — reworded in the review fix to also cover sessions reopened later (P2)
- [x] A frame carrying a `consent` block renders capability and resource; one carrying **no** consent block still renders without throwing — absent, `{}`, `null`, string, array and partial blocks all tested
- [x] A consent `resource` and a title each containing `<script>alert(1)</script>` and quote characters render as inert text — the escaping assertion R-7 promises — mutation-verified by the Security reviewer: switching either sink to `innerHTML`/`insertAdjacentHTML` fails 6-15 tests
- [x] A 500-character shell title renders without truncation
- [x] **(added, Phase 2 divergence, 2026-09-22; revised after Phase 2 review)** With the permission setting `off`, the mode picker's Default entry does **not** offer `poweratlas-acp` as a selectable `modeId`. This is now a **UX** criterion, not the security gate: the Phase 2 review fix (`04d9360`) added a server-side `mode_gate_hook` that refuses `poweratlas-acp` with `bad_payload` unless the derived agent is `on`. The picker's job is to not offer an option the server will refuse, and to surface that refusal legibly if a stale tab sends it anyway — **met in a stronger form after the Phase 3 review** (user decision 2026-09-23): no picker option ever carries `poweratlas-acp`, and Default is resolved by the **server**, so the page never chooses the posture; a refused create is legible in the transcript on both pages
- [x] **(added, Phase 2 review, 2026-09-22)** Every `consent` field renders through `textContent` (never `innerHTML`) with CSS overflow/wrap, including a `resource` of tens of kilobytes — the only bound on these fields is the 1 MiB inbound-line cap (`MAX_AGENT_LINE_BYTES`), so the renderer is where their length is contained — 48 KB resource test; the title is now height-bounded too (review fix)
- [x] **(added, Phase 2 review, 2026-09-22)** The in-page browser `Notification` built in `acp.html` from a permission frame's title is clamped client-side (200 chars, matching `MAX_PERMISSION_TITLE_CHARS`) — Phase 2 moved the server-side clamp to the OS toast only, so the frame title arriving in the page is now unbounded
- [x] `node tests/acp_page.test.mjs` passes — 687/687 at `04982bf`, 693/693 after the review fix `4fc6923`

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
- [x] Local secret created on first call, reused on the second, rotates to a different value on demand
- [x] A truncated or short secret file reads as "no usable secret" rather than as a usable short one — mutation-verified by the Security reviewer
- [x] A secret that cannot be persisted is still usable in-process for the process lifetime, and the condition is recorded where the settings route can report it — `GET /api/settings`'s `local_secret: {persisted, error, path}`. *The first implementation could report `persisted: True` over a file a failed rotation had emptied; the review fix (`3a4f3b5`) made secret writes atomic, so the recorded status is now truthful*
- [x] A login code is exchangeable exactly once; a replay is refused — mutation-verified; confirmed live in Step 5b
- [x] A code older than its TTL is refused, with the TTL measured on `time.monotonic()` (asserted by patching the clock, not by sleeping) — mutation to `time.time` killed
- [x] Minting many codes without exchanging them does not grow the store without bound — 64-code store, 512-entry failure table, both mutation-verified
- [x] The cookie verifies with `compare_digest`; tampered signature, tampered timestamp and future-dated stamp are each refused — plus a review-fix test that a remote-key-signed cookie is refused when the local key is empty (D-17)
- [x] Throttling one code's repeated failures does not refuse a different, valid code — the D-16 regression test — peer-keyed mutations killed
- [x] `rotate_local_secret`'s response re-authenticates its caller — only for a caller already holding a valid `pa_local`; confirmed live in Step 5b
- [x] `pytest tests/test_web.py --timeout=300` passes — 1631 at `6ff4b50`; 1646 after `3a4f3b5`; 1657 after the Phase 3 fix `4fc6923`

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
- [ ] **(added, Phase 4 review, 2026-09-23)** The exempt exchange route is exactly `GET /local-auth` (Phase 4's name); doors build their URL with `web.login_path(web.mint_login_code())`, never by hand
- [ ] **(added, Phase 4 review, 2026-09-23)** The pywebview (peek) window survives a local-secret rotation: it loads its URL once at process start, so after `POST /api/local-secret/rotate` it would hold a dead cookie until restart. Either mint a fresh code and navigate to `login_path` whenever peek is shown, or assert and document the limitation
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
- [ ] With the setting **off**, no prompt is raised and no pattern is blocked — confirms **off** is a genuine no-op over today's behaviour (D-13 superseded 2026-09-22, no floor layer exists)
- [ ] **(added, Step 5 review, 2026-09-22)** With the setting **on**: an allow-listed command (e.g. `git status`) runs silently with no prompt, **and** a capability this design intends to gate but that isn't allow-listed (e.g. `web_fetch`, or another capability from Phase 0 § 9 Step 7's list) still prompts — the live pair confirming rule-assembly completeness is what backstops `on` now that no floor exists
- [ ] **(added, Phase 2 review, 2026-09-22)** With the setting **off**, a live `session/new` requesting `poweratlas-acp` is refused with `bad_payload` and no kiro-cli process is spawned — the server-side gate, confirmed against the running build rather than only unit-tested
- [ ] **(added, Phase 2 review, 2026-09-22)** `session/load` of a session id with **no persisted kiro-cli metadata**, sent once with `modeId: "kiro_default"` and once without: record the bound mode in each case. The vendored KAS source (`hydrateSessionForLoad`) makes the request's `modeId` the fallback when persisted metadata is absent, so P2's "ignored on load" holds only for sessions that have metadata. Decide from the result whether PowerAtlas should send the derived agent's name, the base agent's, or nothing on load
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
| R-4 Deny floor blocks legitimate work | Medium | **No longer applicable** — no floor ships (D-13 superseded 2026-09-22), so it cannot block anything |
| R-5 Deny floor bypassed by rephrasing | High | **Confirmed true** (Phase 0 step 6: 3 of 4 forms bypassed live), which is *why* D-13 was superseded rather than patched. **Accepted, not mitigated**: `off` (allow-all) now ships with no PowerAtlas-authored control at all — this is a knowing return to today's shipped behaviour, not a "problem solved." A reader should not infer zero risk from "no floor"; it means the same exposure allow-all already has today |
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
| R-19 A same-user process reads `local-secret` and forges `pa_local`; or another local listener on a different `127.0.0.1` port receives the cookie (cookies are host-scoped and port-agnostic) and replays it for its 90-day life | High | **Accepted by the user, 2026-09-23** (Phase 4 review): the credential's real boundary is other OS users, web content, and processes that cannot read the user's profile — **not** code already running as the user, which this layer cannot stop (DPAPI does not help against a same-user caller; Follow-up #5's file handoff has the same limit). Cookie lifetime kept at 90 days by the same decision. Cross-port POST forgery is still blocked: `_origin_or_referer_ok` compares the exact origin including the port |

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
| 0 | Pre-flight — preconditions, anchors, harness, rule set | **Complete — Gate resolved** | Deny floor failed bypass resistance (3/4 forms); user decision 2026-09-22 removed the floor from scope entirely (D-13 superseded) rather than patching it, see § 9 |
| 1 | Derived-agent generation and settings | **Complete** | 15/15 exit criteria; 3 commits (`6490554` feat, `ae36282` self-fix, `c2f324b` Step 5 review fix — 3 High findings, generate-only-when-`on` policy adopted); Green health, see § 9 and Review Log |
| 2 | Mode wiring and frame enrichment | **Complete** | 7/7 exit criteria; 4 commits (`bf832a7`, `dba7684`, review fixes `04d9360`, `e0f25d0`); server-side derived-mode gate added; QA PASS 8/8; Green |
| 3 | Settings and permission-prompt UI `[P:4]` | **Complete** | 10/10 exit criteria; 2 commits (`04982bf` feat, `4fc6923` review fix — server resolves Default, user decision 2026-09-23); QA PASS 13/13 over HTTP (no real browser); Green |
| 4 | Local secret, login code, exchange `[P:3]` | **Complete** | 10/10 exit criteria; 2 commits (`6ff4b50` feat, `3a4f3b5` review fix — atomic secret writes, guarded startup); R-19 accepted by the user 2026-09-23; QA PASS 11/11; Green |
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
| Default posture | Off = allow-all as today, **unchanged** (no floor, D-13 superseded) | Installing this release changes nothing opted into |
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

### Phase 0 (2026-09-22) — pre-flight findings, and a Gate

**Status: blocked.** *(Resolved 2026-09-22 — see "Gate resolution" below: the floor was dropped from
scope entirely, not patched.)* Steps 0-5 and 7-9 completed cleanly; step 6 (deny-floor bypass resistance)
failed against its own property-under-test. Per this phase's brief, "step 6 shows the floor is
trivially bypassed" is a named Gate-firing condition. Full evidence below; the design question is
left for the orchestrator/user, not resolved here.

#### Step 0 — precondition (D-14)

**Verified — measured (live check, 2026-09-22).** `plans/done/260922-0859_DASHBOARD_ACP_FEATURE_PARITY.md`
line 4 reads `Status: Complete — Phases 1-6, Step 9 holistic review + fixes, and Step 9b exhaustive
live QA all done.` `git status --short` against its full file scope (`templates/index.html`,
`templates/acp.html`, `static/composer-chrome.js`, `static/style.css`, `tests/acp_page.test.mjs`,
`tests/test_web.py`, `AGENTS.md`) returned empty — clean. Both conditions D-14 requires are
satisfied; this corroborates the orchestrator's own pre-dispatch check.

#### Anchor table — read from source (file:line), 2026-09-22

| Anchor | Plan's citation | Current location | Status |
|---|---|---|---|
| `reportStaleToken` / `diagnoseRejectedHandshake` | not cited by line | `acp.html:5290`, `acp.html:5306` (unchanged, original names) | unmoved |
| Ported stale-token functions in `index.html` | n/a (plan predicted a possible rename) | `dashReportStaleToken` (`index.html:944`), `dashDiagnoseRejectedHandshake` (`index.html:957`), called at `index.html:964` and `index.html:1169` | **renamed** during the parity port — see below |
| `ACP_TOKEN` in `index.html` | 14 occurrences (2026-09-21) | **15 occurrences** (2026-09-22): decl `:893`, socket-URL use `:897`, 6 sentinel branches (`:1589, :3681, :4740, :5074, :5186, :5737`), 7 comments (`:211, :215, :939, :1586, :1587, :5071, :5181`) | +1, all in the +1 comment at `:939` (inside the `dashReportStaleToken` mirror comment the parity port added); sentinel-branch count unchanged at 6 — D-15's sizing still holds |
| `acp-taskmode-option` | `acp.html` | `acp.html:383-388,564`; `index.html:3614,3626,3644,3699`; `style.css:2175,2187,2188,2192` | present in both templates + shared CSS, as expected post-parity |
| `notifyToggle` / `autostartToggle` | `index.html` | `index.html:55` (autostart), `index.html:60` (notify), `index.html:4083` (notify handler) | unchanged |
| `permission_request` (backend) | `acp.py`, `web.py` | `acp.py:186,188,192,4474,4479,4504,4549,4559,5928,5972,5977`; `web.py:444` | unchanged in shape |
| `permission_request` (frontend) | `acp.html`, `index.html`, `transcript-renderer.js:1773/1781/1921` | `acp.html:4996,5027`; `index.html:889,3251`; `transcript-renderer.js:1781,1921` | `transcript-renderer.js` now has **2** hits, not 3 — `:1773` no longer matches; Phase 3's file scope still correctly includes this file |
| `_on_permission_request` | `acp.py:4479` | `acp.py:4479` (def line) — **exact match, not drift**; the title-clamp assignment the plan's sketch targets is at `acp.py:4548`, one line before the `_emit` call at `:4549` | both cited for Phase 2 |
| `MAX_PERMISSION_TITLE_CHARS` | `acp.py:589` | `acp.py:589` | exact match |
| `_VALID_TASK_MODES` | `acp.py:691` | `acp.py:691` | exact match |
| `_build_kas_session_params` | `acp.py:697` | `acp.py:697` | exact match |
| `new_session` mode pass-through | `acp.py:4647` | `acp.py:4647` | exact match |
| `load_session` mode omission | `acp.py:4751` | `acp.py:4751` (`**_build_kas_session_params()`, no argument) | exact match, confirmed still inert per P2 |
| `_LOOPBACK_HOSTS` | `web.py:574` | `web.py:574` | exact match |
| `_ACP_TOKEN` | `web.py:1269` | `web.py:1269` | exact match |
| `_acp_token_ok` (def) | `web.py:1272` | `web.py:1272`, called `web.py:1550` | exact match |
| `_acp_token_ok` (docstring mention) | `acp.py:5076` | `acp.py:5076` | exact match |
| `same_origin_guard` | `web.py:771-772` | `web.py:772` (def) | exact match |
| `RemoteAccessGuard` | `web.py:1204`, registered `:1248` | `web.py:1204` (class), `web.py:1248` (`app.add_middleware`) | exact match |
| `_refuse` | `web.py:1167-1183` | `web.py:1167` | exact match |
| `_cookie_ok` | `web.py:930-964` | `web.py:930` | exact match |
| `set_cookie` call | `web.py:3245` | `web.py:3245` (`response.set_cookie(`) | exact match |
| `make_device_cookie` / `_device_cookie_sig` | `web.py:891-905` | `web.py:891` (`_device_cookie_sig`), `web.py:898` (`make_device_cookie`) | exact match |
| `remote_auth_exchange` | `web.py:3155` | `web.py:3155` | exact match |
| `_SETTING_TYPES` | `web.py:3708` | `web.py:3708` | exact match |
| `save_config` | `config.py:481-500` | `config.py:481` | exact match |
| `_write_remote_secret` | `config.py:167-187` | `config.py:167` | exact match |
| `pyproject.toml` package-data | `:37-38` | `:37-38` (`[tool.setuptools.package-data]`, `power_atlas = ["static/**", "templates/**"]`) | exact match; confirms no `agents/**` entry yet, per plan |
| `tests/test_web.py` lifespan seam | `≈:10664` | `:10664`, `:10680` | exact match |

Every anchor either matched exactly or was precisely re-located with its current line. No anchor was
unfindable.

#### Step 2 — stale-token function names post-port

The parity plan's Phase 6 **did** port `reportStaleToken`/`diagnoseRejectedHandshake` into
`index.html`'s `dashConnect()`, and **did** rename them: `dashReportStaleToken` (`index.html:944`)
and `dashDiagnoseRejectedHandshake` (`index.html:957`), wired at `index.html:964` and `:1169`.
`acp.html`'s originals are untouched at their original names. Neither pair moved into
`composer-chrome.js`. This confirms R-17's premise exactly as predicted: Phase 6's own deletion
check must be behavioral (drive a rejected handshake, assert no stale-token affordance renders),
never a name-based grep for the old names alone — a grep for `reportStaleToken`/
`diagnoseRejectedHandshake` would miss the `dash`-prefixed pair entirely.

#### Step 3 — probe harness

Written to `tools/acp_permission_probe.py` (526 lines; `tools/` did not previously exist in this
repo). *(Committed 2026-09-22 as `ea79482` after orchestrator review — the commit was initially
withheld pending the Gate decision below, since this phase's brief scoped the commit to a successful
phase and a `blocked` return should not silently land a file the orchestrator hasn't seen; once
reviewed, it was committed as independently useful infrastructure regardless of the Gate's outcome.
Two further fixes landed in the Step 5 review after Gate resolution: `_pick_option`'s reject-path
no longer falls back to `options[0]`, and kiro-cli's stderr is now captured under `--verbose`.)*

Design: spawns `kiro-cli acp --agent-engine v3` directly over stdio NDJSON JSON-RPC 2.0 (never
through PowerAtlas), drives `initialize` → `session/new` (binding the `_meta.kiro.modeId` to
`--agent`) → an optional single `session/prompt` turn → a drain period, answers every inbound
`_kiro/auth/getAccessToken` by shelling to `kiro-cli chat _ get-kas-token`, and records every
`session/request_permission` in full before answering it per `--answer` (default `reject`, so
nothing asked about actually runs). Token hygiene: the OIDC token is held in-process only; the one
outbound frame shape that would carry it is redacted before being appended to any frame log.

**Verified — measured (live probe, 2026-09-22, kiro-cli 2.22.1).** `--agent kiro_default --cwd
<scratch dir>`, no prompt, reproduces P1 exactly: the mode catalogue (via a `config_option_update`
notification) lists all 8 `_VALID_TASK_MODES` values, and `kiro_default` registers with
`_meta.kiro.source: "global"`, `resource.source.origin: "user"` — matching P1's finding verbatim.
`mode_in_effect` (`_meta.agentMode` and `modes.currentModeId`, both read from the `session/new`
response) read `"kiro_default"`, confirming no coercion for the baseline case.

Two bugs were found and fixed **before** any measurement that depended on them (both caught by
advisor review, not self-discovered):
1. `_pick_option`'s substring matching for `--answer allow_always` matched on the bare substring
   `"always"`, which is also a substring of `reject_always` (`"Always deny"`) — a request for
   "always allow" could have silently selected "always deny" instead. Fixed to match `kind` exactly
   first (`allow_always`, `reject_once`, `allow_once`), falling back to a verb-prefix match
   (`allow_`/`reject_`) only if no exact match exists, never a bare `"always"` substring.
2. Mode coercion (R-2) was computed but never surfaced loudly — a silent `vibe` fallback would have
   been visible only to an operator who happened to read `mode_in_effect` by eye. Fixed: `new_session`
   now sets `mode_coerced` and prints an unconditional (non-`--verbose`) stderr warning the moment the
   bound mode differs from the one requested, and the summary JSON carries `mode_coerced` explicitly.

`kiro-cli agent validate --path <file>` was tried as an independent YAML-validity check and rejected
as a viable one: it reports `Json supplied ... is invalid: invalid number at line 1 column 2` for
**every** `.md`-frontmatter agent file tested, including the machine's own live, daily-used
`kiro_default.md` — it appears to expect a JSON-format agent config, not the Markdown+YAML-frontmatter
format this installation actually uses for `.md` agents, and its exit code is 0 even when it prints
`Error:`. It is not a usable generation-failure gate for Phase 1 in its current form; see the
YAML-fail-open finding below for what actually needs gating.

#### Step 4 — default rule set (documentation-sourced)

No empirical fallback was needed — the STOP-and-ask gate for moving `~/.kiro/settings/permissions.yaml`
was never approached. Published documentation was reachable and used:

- **Source**: [kiro.dev/docs/permissions/](https://kiro.dev/docs/permissions/) and
  [kiro.dev/docs/cli/v3/permissions/](https://kiro.dev/docs/cli/v3/permissions/) — read
  2026-09-22 (`read from kiro-cli docs`).
- **Default rules with no `permissions.yaml` configured**:
  | Rule | Effect | Doc reference |
  |---|---|---|
  | `fs_read` on `./**` | allow (silent) | kiro.dev/docs/permissions/ |
  | `shell` for git read-only commands (`git status`, `git log`, `git diff`, `git branch`) | allow (silent) | kiro.dev/docs/permissions/ |
  | `shell` for system-info commands (`pwd`, `whoami`, `uname`) | allow (silent) | kiro.dev/docs/permissions/ |
  | `fs_write` to `~/.kiro/settings/`, `.kiro/settings/`, `~/.kiro/workspace-roots/` | **always deny** (Kiro scope, not overridable) | kiro.dev/docs/permissions/ |
  | Writes to `.git/**`, `.kiro/agents/**`, `.kiro/hooks/**`, `.kiroignore` | always ask (Kiro scope) | kiro.dev/docs/permissions/ |
  | Everything else | ask | kiro.dev/docs/permissions/ |
- **Rule schema**: `capability` (`fs_read`, `fs_write`, `shell`, `web_fetch`, `web_search`, `mcp`,
  `subagent`, `skill`, `power`, `context`, `diagnostics`, `sandbox_network`; meta-capabilities `all`,
  `builtin`, `filesystem`), `effect` (`deny`/`ask`/`allow`), `match` (glob patterns), `exclude`
  (glob patterns that must NOT match) — `read from kiro-cli docs`.
- **Precedence**: "deny > ask > allow — a deny rule always wins regardless of scope"; scope order is
  Kiro → administration → user → workspace → agent → session, most-restrictive-wins, no scope
  hierarchy overrides deny — `read from kiro-cli docs`.

**A load-bearing empirical correction to the precedence doc, found while establishing this set —
measured (live probe, 2026-09-22):** the doc's "most restrictive wins" statement is *silent* on
what happens **within one scope** when two rules of different effects both match the same resource
via `match`. This was tested directly and is not documented:

1. Agent-scope `{capability: all, effect: ask}` + `{capability: fs_read, match: ["./**"], effect:
   allow}` (in either order) → an `fs_read` on a workspace file still fires an `ask`, with
   `matchedRule: {capability: "all", effect: "ask"}` — the specific `allow` rule for the same
   resource **never engages**, regardless of which rule is listed first. Caveat: whether the
   `fs_read` rule's `./**` glob actually matched the resource at all was not isolated in this test —
   kiro-cli reported the target in `rawInput.path` as an absolute Windows path (`c:\Users\...`), and
   it is possible `./**` simply never matched that form, independent of the `all` rule's presence.
   Points 2 and 3 below (same-capability `shell` rules, and the `exclude` fix) do not share this
   ambiguity and carry the design implication on their own; Phase 1 should confirm `./**` matches on
   Windows before relying on it.
2. The same defeat happens **within a single capability**: `{capability: shell, match: ["echo *"],
   effect: allow}` + `{capability: shell, effect: ask}` (blanket, same capability) → running `echo
   ...` still asks, matched against the blanket `shell/ask` rule, not the specific `shell/allow`
   rule that literally matches the command run.
3. **The fix is `exclude`, not two same-capability rules.** `{capability: shell, match: ["echo *"],
   effect: allow}` + `{capability: shell, exclude: ["echo *"], effect: ask}` (the blanket rule
   *excludes* what the narrow rule covers) → `echo ...` runs silently, zero prompts. This is the
   only mechanism tested that reproduces "allow X, ask about everything else" within one capability.

**Design-affecting implication for Phase 1** (not fixed here — Phase 0 does not fix designs):
`agent_profile.py`'s rule-assembly algorithm cannot express the "on" branch as "a blanket `all: ask`
plus specific allow exceptions," nor as "a blanket `shell: ask` plus specific shell allow exceptions" —
both are silently defeated by most-restrictive-wins with no error, no warning, and no visible signal
that the intended allow-list is dead code. The assembly must instead emit narrower ask rules with an
explicit `exclude` list populated from every narrower allow/deny rule's own `match` patterns for the
same capability, or omit a same-capability catch-all `ask` entirely for capabilities that need a
mixed allow/ask split. This directly affects whether the "on" branch can reproduce "fs_read on ./**
allow, everything else fs_read ask" and "shell git-read allow, everything else shell ask" as literally
stated in the doc-sourced default set above.

#### Step 5 — deny floor (drafted)

The always-on floor is scoped to `shell` and `fs_write` (D-13's own scope: "never-okay destructive
shell patterns and sensitive-path writes"), each entry justified in one line:

| Pattern (illustrative form) | Capability | Justified because |
|---|---|---|
| Recursive force-delete of the user's home directory or its ancestors (`Remove-Item -Recurse -Force ~`/`$HOME`/`/` and PowerShell/POSIX equivalents) | `shell` | Total data loss with no recovery path; never a legitimate ACP-session action |
| Recursive force-delete of `.kiro`, `.git`, or any workspace-root ancestor | `shell` | Removes the very governance/history the agent's own trust depends on |
| `fs_write` to `~/.kiro/settings/**`, `~/.kiro/agents/**` (outside the one file PowerAtlas itself generates), `~/.kiro/workspace-roots/**` | `fs_write` | Self-modification of the permission system that is supposed to be constraining the agent — already Kiro-scope always-deny per the doc-sourced defaults above, so this floor entry is redundant with a built-in and mainly documents the overlap rather than adding new coverage |
| Piping a network fetch straight into a shell interpreter (`curl \| bash`/`iwr \| iex` and equivalents) | `shell` | Arbitrary remote code execution with no local review step |
| Disabling or uninstalling security tooling referenced by this machine's own governance (Windows Defender, the git pre-commit hook, `.venv` isolation) | `shell` | Removes a control this very design assumes stays in place |

(The specific Kiro Crew deny list itself — "137 built-in patterns" per public sources — was not
directly inspectable: `kiro-cli crew` is not installed on this machine, and installing it purely to
read its bundled deny list would be an irreversible, side-effecting action outside this phase's
authority. The floor above is drafted from first principles plus the public documentation found
during step 6's research, not transcribed from Crew's actual file.)

#### Step 6 — bypass resistance (FAILED — the Gate)

**Control — measured (live probe, 2026-09-22).** A floor entry `{capability: shell, match:
["Remove-Item -Recurse -Force ./canary", "Remove-Item -Recurse -Force ./canary/*"], effect: deny}`
(PowerShell-native syntax — see the shell-backend finding below) against a disposable canary
directory: the exact literal command is silently blocked, zero `session/request_permission` frames,
tool call fails immediately with `Tool call denied by user's permissions. Rule: deny shell matching
"..." Source: agent-profile.` Canary survives. This is the floor working exactly as designed.

**Bypass forms — measured (live probe, 2026-09-22), canary recreated before each:**

| # | Form | Command | Result |
|---|---|---|---|
| 1 | Case-variant | `remove-item -recurse -force ./canary` | **BYPASSED** — canary deleted, zero prompt |
| 2 | Quoted/aliased shell form | `rm -Recurse -Force "./canary"` (PowerShell's `rm` alias for `Remove-Item`, quoted path) | **BYPASSED** — canary deleted, zero prompt |
| 3 | `../`-traversal | `Remove-Item -Recurse -Force ./sub/../canary` | **BYPASSED** — canary deleted, zero prompt |
| 4 | Symlinked target | `Remove-Item -Recurse -Force ./canary-link` (symlink to `./canary`) | Literal string `"canary-link"` does not match `"canary"`, so the same literal-mismatch defeat applies — but in this instance PowerShell's own `Remove-Item -Recurse` on a directory symlink removed only the link, not the real target's contents, so the underlying canary happened to survive. This is an artifact of PowerShell's own symlink-deletion semantics, not evidence the floor generalizes to canonicalized matching — forms 1-3 already establish the matching is literal. **This form, as designed, cannot distinguish "the floor caught it" from "the OS didn't recurse through the link"** — both produce the same "canary survived" observation. A discriminating rerun would use a symlinked *file* (not directory) plus a write-through command, where a literal-match floor and a canonicalizing one would diverge observably; Phase 7 should use that shape if it re-attempts this bypass form live |

**Matching semantics — measured, conclusive: literal, case-sensitive string matching against the
raw command text, with no canonicalization, no alias resolution, and no path normalization.** This
is not a PowerAtlas-specific finding — it corroborates kiro-cli's own documentation ("match entries
are glob patterns tested against the command string, so the rule constrains the spelling of an
action, not the action" — `read from` kirodotdev/Kiro#11498) and Kiro Crew's own public issue
tracker, which documents the identical bypass class against its bundled deny list: `rm -rf ~` is
denied but `rm -rf $HOME` (a different spelling, same destructive target) is not
(kirodotdev/KiroCrew#8387), a PR specifically fixing missed `rm` flag-order variants
(kirodotdev/KiroCrew#8240), and an agent that "reasons its way around" a deny rule by reformulating
the command entirely (kirodotdev/Kiro#11498).

**This directly contradicts D-13's characterization** of the borrowed Kiro Crew list as
"never-bypassed" — the public record shows the opposite, repeatedly, and this phase's own live
measurement reproduces the same defeat class against three of four tested forms on the very first
attempt, using the simplest possible rephrasing (case).

**Shell-backend finding, measured live and worth recording on its own:** kiro-cli's shell tool on
this Windows machine executes via PowerShell. POSIX `rm -rf ./target` was tried first and does
**not** execute destructively at all here — PowerShell's `Remove-Item` rejects `-rf` outright
(`A parameter cannot be found that matches parameter name 'rf'`), confirmed by a control run against
a non-denied decoy directory that survived. Any floor pattern written in POSIX shell idiom (as the
GitHub-issue precedent and D-13's own phrasing, "never-okay destructive shell patterns," both
implicitly assume) is **inert on this machine** — a real gap distinct from the literal-matching
bypass above. The floor must be authored in whatever shell syntax the target machine's kiro-cli
backend actually executes, which is platform-dependent and was not previously called out anywhere
in the plan.

**Per this phase's brief, this is a named Gate-firing condition** ("step 6 shows the floor is
trivially bypassed"). R-5's mitigation text ("Addressed: Phase 0 step 6 tests case, quoting,
traversal and symlink forms before the floor is trusted") frames the test's own result as the thing
that decides whether the floor is trusted — the test says it should not be, as currently conceived.
This phase does not resolve the design question; see the Gate statement at the end of this section.

#### Step 7 — unexercised capabilities

**A methodological finding came first and matters more than any individual capability result.** The
very first step-7 probe (`mcp`) used a description with no bare `: ` in it, and it fired correctly.
Every probe run **after** that one, until this was caught, used the description pattern
`... measurement (step 7): web_fetch. ...` — an unquoted colon-space inside a plain YAML scalar,
invalid frontmatter. That covers seven runs in sequence: `web_fetch` under `ask`, `web_search` under
`ask`, `web_fetch` under `deny` (re-check), `web_search` under `deny` (re-check), `subagent`,
`skill`, and a plain `fs_write` create. **All seven** showed zero permission prompts and the tool
proceeded — nine data points total (1 clean + 7 broken + 1 more broken variant of the `fs_write`
retest with an explicit `match` field, also silently open), a clean split: every broken-YAML run
fell open, every valid-YAML run enforced correctly. That split is what makes the artifact diagnosis
airtight, not just the single `web_fetch` re-run. **It was an artifact**, not a kiro-cli property. `kiro-cli agent validate --path` was tried as a check and (per the step-3
notes above) turned out to be unusable for `.md`-format agents on this version regardless of validity.
The correct diagnosis, found via advisor review before this was reported as a real finding, was
functional: rewriting each description to remove the bare `: ` and rerunning reproduced the expected
`ask` behavior in every case. **This is a live, accidental demonstration of exactly the SC-8/R-10
fail-open threat the plan is built around**: a malformed `permissions:` block (here, caused by the
whole frontmatter failing to parse, not the `permissions:` key itself) did not error, did not warn,
and did not block the session from starting — it silently fell back to this machine's user-scope
allow-all, identical in shape to P5's "blank permissions: key" finding, just reached by a different
malformation. **Concrete Phase 1 implication**: `agent_profile.py`'s generation path needs *some*
positive confirmation that the derived agent's `permissions:` block actually bound — not
`kiro-cli agent validate` in its current form (shown unusable above) — before treating generation
as successful; what mechanism can provide that confirmation is an open question for Phase 1, not
answered here.

**Results, all re-measured live with corrected YAML (2026-09-22), `mode_coerced: false` in every
case:**

| Capability | Fires under `ask`? | consent payload highlights |
|---|---|---|
| `mcp` | **Yes** | `toolId: "mcp_playwright_browser_navigate"`, `capability: "mcp"`, `resource: "playwright/browser_navigate"`, plus an `mcpTool.annotations` block (`readOnlyHint`, `destructiveHint`, `openWorldHint`) and `agentManagesTrust: true` — both outside Phase 2's 5-field consent allowlist (`capability`, `resource`, `matchedRule`, `scope`, `source`); noted for Phase 2, not acted on here |
| `web_fetch` | **Yes** | `toolCall.title: "Fetch URL"`, standard 3-field consent (`capability`, `matchedRule`, `scope`/`source` implied by the shared shape) |
| `web_search` | **Yes** | `toolCall.title: "web_search"` |
| `subagent` | **Yes** | `toolCall.title: "Sub-agent: kiro_default"` |
| `skill` | **Yes** | `toolCall.title: "Load skill: qmemory-eval"`, `toolId` implied `skill`-shaped; denying it correctly stopped the skill load (a downstream shell step in the same turn then ran via this machine's fallthrough allow-all — expected, not a new gap, see below) |
| `power` | **Untestable live** — `~/.kiro/powers/installed.json` shows `"installedPowers": []` on this machine; nothing is installed to invoke. Capability name (`power`) is `read from kiro-cli docs` only, never exercised |
| a deletion (`fs_write`) | **Yes** | `toolId: "delete_file"`, `capability: "fs_write"` (same tag as a plain write), `resource: "<filename>"`, `matchedRule: {capability: fs_write, effect: ask}` — reconciles cleanly with the plan's own recorded P4 payload for a plain write |

**A capability not covered by any agent-scope rule inherits this machine's user-scope `allow-all`,
not kiro-cli's documented ask-by-default** — measured directly (live probe, 2026-09-22): an agent
whose only rule was `{capability: fs_read, match: ["./**"], effect: allow}` ran an unrelated `shell`
command (`echo ...`) with zero prompts. This generalizes P5's "blank `permissions:` key" finding to
"any capability the rule list is silent about," not only a wholly blank block. **Design-affecting
implication for Phase 1**: the "on" branch's rule list cannot rely on omission defaulting to `ask` —
every capability the design wants to gate must be named explicitly (subject to the `exclude`
mechanism above for capabilities needing a mixed allow/ask split).

#### Step 8 — "always allow" persistence

**Answered: not reachable over the ACP protocol surface at all, on kiro-cli 2.22.1 — measured (live
probe, 2026-09-22).** Every `session/request_permission` captured across this entire phase (11
distinct requests, across `fs_read`, `fs_write`×2, `shell`×2, `mcp`, `web_fetch`, `web_search`,
`subagent`, `skill`) offered exactly the same three options: `allow_once` ("Allow"), `reject_once`
("Deny"), `reject_always` ("Always deny"). **`allow_always` was never once offered.** A probe run
with `--answer allow_always` against a `shell: ask` rule confirmed this directly: no option of that
kind existed, so the harness's documented fallback (verb-prefix match) selected `allow_once` instead.
Diffed paths, before vs. after: `~/.kiro/agents/kiro_default.md`,
`~/.kiro/agents/agent_config.json.example`, `~/.kiro/settings/permissions.yaml`, and
`~/.kiro/workspace-roots/*/permissions.yaml` (all 12 directories) — **zero differences** apart from
the probe agent file's own hash, which changed only because this phase rewrote its content between
measurements (expected, not kiro-cli's doing). Combined with the plan's own already-recorded P3
finding (`reject_always` "persists nothing to disk"), the conclusion is: **the ACP surface PowerAtlas
actually drives cannot widen the permission posture at all, in either direction** — a
design-favorable asymmetry worth stating in Phase 1/7's documentation rather than only in this
section.

#### Step 9 — `~/.kiro` baseline, by filename and hash

Recorded before any Phase 0 probe touched the directory (2026-09-22T07:32:02Z) and reconfirmed
identical after every mutating step, with the probe agent removed each time before the next:

| File | SHA-256 |
|---|---|
| `~/.kiro/agents/kiro_default.md` | `4db4781968fa232cc643c46a3cbf5745b4f3b48d4399ce0482518b27e024ee16` |
| `~/.kiro/agents/agent_config.json.example` | `1c2b829d84131c73627b973cf07c1f4bab521e9d1ab8d17de75691745432094e` |
| `~/.kiro/settings/permissions.yaml` | `f8a0d44394b5b2a23ff363b69538911caeda28c97604ad7d6825b93ec40146c8` (matches the plan's own § 7 baseline, unchanged since 2026-09-21) |
| `~/.kiro/workspace-roots/2d86113386de3488/permissions.yaml` | `114202c326e1dbe3ae7f09a06e29763b8b39c1715e6f9e6dec4da7d0fdb2925f` (pre-existing, 3 `mcp` allow rules for zoho/atlassian/playwright tool names — this is the "live MCP consent rules" the plan's own Current State already noted; not written by this phase) |
| `~/.kiro/workspace-roots/*` (other 11 dirs) | no `permissions.yaml` present, before or after |

**Expected churn, not a restoration failure**: `~/.kiro/sessions/` and `~/.kiro/session-index/` gain
new entries on every probe run (kiro-cli's own session-history bookkeeping, unrelated to permission
state) — not diffed further, and never restored, since nothing in the plan's threat model concerns
that directory. No new `~/.kiro/workspace-roots/*` directory was created by any probe in this phase,
despite each one using a distinct-from-production cwd — kiro-cli did not register the scratch probe
cwd as its own workspace root within this phase's measurements. Final state, verified 2026-09-22:
**identical to the baseline above**, byte-for-byte on every hashed file, with zero probe agent files
remaining under `~/.kiro/agents/`.

#### Gate statement

**The design does not fully hold as stated, and this phase does not fix it.** Specifically:

1. **D-13's deny floor, as literally specified, is not the guarantee SC-1 implies.** It blocks the
   exact string it was written against and nothing else — a case change, a built-in shell alias, or a
   no-op path segment each defeat it, matching the public, repeated experience of the very list D-13
   cites as precedent. Whether the floor is re-scoped as explicit best-effort defense-in-depth (with
   SC-1's language softened to match), or whether Phase 1 attempts broader pattern coverage
   (enumerating case/alias variants per entry — itself an arms race the public trackers show never
   fully closes), or something else, is a design decision for the orchestrator/user, not made here.
2. **The floor must be authored per-platform.** POSIX `rm -rf` patterns are inert against this
   machine's PowerShell-backed shell tool. A cross-platform PowerAtlas install needs either
   platform-aware floor generation or floor entries covering both idioms.
3. **The "on" branch's rule-assembly algorithm cannot use a same-capability (or `all`) catch-all
   `ask` beside narrower `allow` rules** — most-restrictive-wins silently defeats the narrower rule
   with no error. The `exclude` field is the mechanism that works instead, and Phase 1's design as
   currently sketched ("deny floor + (allow-all | transcribed defaults)") does not yet account for
   this.
4. **Six of the seven previously-unexercised capabilities are confirmed correctly gated** (`mcp`,
   `web_fetch`, `web_search`, `subagent`, `skill`, and `fs_write`/deletion all fire under `ask` and
   are controllable). `power` remains untested (no installed powers on this machine). This part of
   the design premise holds.
5. **"Always allow" cannot be reached over the ACP surface at all**, so R-15's concern does not
   apply to this delivery path specifically — the design is *more* favorable here than the plan
   assumed, not less.
6. **A malformed derived-agent frontmatter fails open, silently, exactly as R-10/SC-8 predict** —
   demonstrated live, by accident, in this phase. Phase 1 needs a real post-generation confirmation
   step; `kiro-cli agent validate` does not currently serve that role for `.md`-format agents.

None of these are attempted fixes — per this phase's brief, that decision belongs to the
orchestrator/user before Phase 1 proceeds.

#### Gate resolution — user decision, 2026-09-22

Escalated through `/qcouncil` (Full stakes tier — large blast radius: Phase 1's rule-assembly code,
SC-1's guarantee language, README, settings UI copy). Four options were debated by 12 sub-agents
(4 advocates, 4 prosecutors, 4 independent jurors: Senior engineer, Security auditor, Reliability
engineer, Architect). The jury landed unanimously (4-0) on patching the mechanically-closable
bypasses (alias enumeration, conditional case-insensitivity) while rewriting SC-1's language to
disclose what remains open — two jurors independently verified against the actual `acp.py` code
that a fourth, more invasive option (routing enforcement through PowerAtlas's own process) rested on
a misattributed code citation and would have made PowerAtlas's own liveness safety-critical for
enforcement, colliding with this project's no-autonomous-restart governance constraint.

**The user did not take the council's recommendation.** Presented with the plain-language trade-off,
the user's decision was to drop the deny floor from this plan's scope entirely, for two reasons
stated directly: (1) "we're unable to design it perfectly" — consistent with Phase 0's own finding
that even the recommended patch (Option C) left known gaps (flag-order variants, `$HOME`-vs-`~`
expansion, the unresolved symlink form) uncovered; (2) the future unattended-automation follow-on
("auto-mode") will need its own protection model regardless, since it runs with nobody watching —
a categorically different problem than backstopping an interactive, human-supervised session, so
investing in a bespoke floor here doesn't carry forward. The user also confirmed, in the same
exchange, that the floor was never intended to matter in the **on** (ask-based) state — its whole
job was backstopping **off** (allow-all) — and chose to drop it from `off` as well rather than keep
it as insurance against Phase 0's separately-measured rule-assembly bug (Gate finding #3) that could
cause `on`'s own ask-blanket to silently degrade toward allow-all. That bug still needs fixing in
Phase 1 (§ Design Decisions, Phase 1's rule-assembly note above) — it's just no longer backstopped
by a floor if the fix is incomplete.

**Net effect**: D-13 is superseded (see § 3). `off` is now a byte-for-byte no-op over today's
shipped behaviour. `on` is where this plan's entire posture improvement lives. SC-1, the Goal
statement, Phase 1's rule-assembly description, Phase 7's exit criteria, the Risk Assessment table
(R-4, R-5), the External Dependencies rollout row, and the Backwards Compatibility table have all
been updated to match. Gate findings #2 (platform-authoring), #3 (most-restrictive-wins/`exclude`),
#6 (silent fail-open on malformed frontmatter), and **Step 7's capability-silence finding** ("a
capability the rule list is silent about inherits this machine's user-scope allow-all, not
kiro-cli's documented ask-by-default" — every gated capability must be named explicitly, omission
does not default to `ask`) are **not** resolved by dropping the floor — they were never
floor-specific — and remain Phase 1/7 obligations independent of this decision.

**With the floor gone, correct rule assembly in the `on` state is now the *only* backstop this plan
ships** (Step 5 review finding, Security auditor persona, 2026-09-22). Phase 1's and Phase 7's exit
criteria (§ 5) now include explicit checks for this — see the added items in each phase below.

### Phase 1 (2026-09-22) — derived-agent generation

Implementation (2026-09-22, code: `ae36282`)

#### What shipped

`src/power_atlas/agent_profile.py` (new) builds `~/.kiro/agents/poweratlas-acp.md` from a configurable base agent. The base is never modified, which is what keeps interactive terminal kiro-cli sessions' posture independent of anything PowerAtlas does.

Injection is textual (D-18). `_frontmatter_bounds` locates the fences on kiro-cli's own rule — opening fence is line 1, closing fence is the first subsequent `---` — and `_permissions_regions` returns the line span of every top-level `permissions:` block. `inject_permissions` replaces the first and drops the rest; when there is none it inserts before the closing fence. `excise_permissions` is the shared inverse, which is what makes the byte-identity claim checkable rather than asserted: two files agree outside the injected block if and only if their excised forms are equal, and that same function is what the test and the post-write self-check both use.

I/O is binary in both directions with an explicit UTF-8 decode. Text mode on Windows would turn a `\r\n` base agent into `\n` on the way in and back on the way out — for a file whose byte-identity is the contract, a round trip through text mode is a rewrite. Splitting is `text.split("\n")` rather than `splitlines()`, which also splits on `\x85`/`\u2028` and would corrupt an odd base. A UTF-8 BOM is tolerated on line 1 and not stripped.

The write is `save_config`'s pattern — tmp -> `fsync` -> `os.replace`, tmp unlinked on any failure (D-19). Not `_write_remote_secret`'s in-place `O_CREAT|O_TRUNC`: a torn secret fails closed via its length check, a torn `permissions:` block fails open. A `threading.Lock` serialises generation, since two settings writes dispatched through `asyncio.to_thread` would otherwise race on the same `.tmp`.

`config.py` gains `acp_permissions_enabled: bool = False`, `acp_permission_base_agent: str = "kiro_default"`, and `DERIVED_AGENT_NAME = "poweratlas-acp"` (D-20 — in `config.py` so Phase 2 can name it from `acp.py` without a second intra-package import). No load-time sanitisation: `load_config` must never raise, and a hand-edited bad name surfaces as a typed error in the generation status instead.

`web.py` gains `_regenerate_derived_agent()`, called from `lifespan` (before the sweeper) and from the two settings write paths, through `asyncio.to_thread` (D-9), swallowing every exception with `log.exception`. `GET`/`POST /api/acp-permissions` carry the boolean; `acp_permission_base_agent` goes through `/api/save-setting` with validation on the write path, mirroring how `remote_bind_address` produces its named error there (D-11).

#### The rule set, and why it looks the way it does

`(allow-all | transcribed defaults)`, no floor. The `on` branch is the package-data overlay `src/power_atlas/agents/permissions.yaml`; the `off` branch is a four-line `all: allow` Python constant. One file rather than two because the `off` block has nothing to review as YAML, and a Python constant keeps all the *reviewable* rule text in one auditable artifact.

Both properties Phase 0 measured are encoded, and both are mutation-verified in the suite:

- **Every gated capability is named explicitly.** All 9 that Phase 0 Step 7 exercised on the ACP surface: `fs_read`, `fs_write`, `shell`, `web_fetch`, `web_search`, `mcp`, `subagent`, `skill`, `power`. A capability no rule names inherits the wider scopes, not `ask`.
- **No blanket `ask` sits beside a narrower `allow` without a populated `exclude`.** The `fs_read` and `shell` ask rules carry `exclude` lists naming exactly their paired allow rule's `match` patterns. The test's checker is proved to bite by a negative fixture reproducing the measured defeat in both its forms — same-capability (`{shell, match: ["echo *"], allow}` + `{shell, ask}`) and the `all`-scoped variant.

No meta-capability (`all`, `builtin`, `filesystem`) appears in the `on` set, and the test forbids one. `exclude` is a resource glob, not a capability filter, so `all: ask` with an `exclude` would drop the excluded resource out of the ask for *every* capability at once, `fs_write` included.

`fs_write` needs no allow rules and no `exclude`: kiro-cli's non-overridable Kiro-scope rules already always-deny writes to `~/.kiro/settings/` etc. and always-ask writes to `.git/**` etc.

#### Decision on Gate finding #6 — post-generation bind confirmation

**Split: a structural self-verification ships now; live bind confirmation is deferred to Phase 7.**

What ships: `regenerate()` writes to a staging file, fsyncs, **reads the staged file back and re-excises it before publishing**, and fails — leaving the previous file, if any, completely untouched — unless the block is byte-identical to the assembled overlay *and* every other byte still matches the base. *(Corrected 2026-09-22, Step 5 review: the first implementation verified **after** `os.replace`, which meant a bad splice replaced the last-good file instead of being caught — the exact opposite of what this paragraph originally claimed. Fixed in `c2f324b`; two mutation-verified regression tests, one of which spies on `os.replace` and asserts it is never called when verification fails.)* This is not decorative. It is the definition of "previously-validated" that D-10 leaves undefined, and it covers the one fail-open this module can *create*: a bad splice. The overlay itself is static committed package data, not the dynamic content that caused Phase 0's accident, and a test asserts it contains no bare `: ` inside a plain scalar and no tabs — the exact malformation that fell open across 7 consecutive probe runs.

Why the rest is deferred rather than attempted: confirming a *bind* needs a kiro-cli spawn, an auth-token round trip and a triggered prompt, since P5 proves `modeId_in_effect` alone is insufficient. None of that belongs inside `lifespan` or a settings write. Hand-rolling a YAML validator instead is the arms race the user rejected for the floor, and D-18 forbids adding a parser. `kiro-cli agent validate --path` is unusable for `.md` agents. Phase 7 already owns the harness.

`derived_block_state()` is the other half of SC-8 and is what the panel should read rather than the toggle. *(Revised 2026-09-22, Step 5 review, `c2f324b`: now classifies as `on` / `stale` / `absent` / `unknown` — see "Divergences flagged for orchestrator attention" below for why the `off` state was eliminated entirely rather than reclassified. `on` and `stale` are this module's own output, recognized by a provenance marker every written block carries rather than by byte-equality — so an overlay revision or an old-code instance's leftover file is still recognized and cleaned up. `absent` is the healthy `off` state. `unknown` is foreign content, never touched.)* `in_effect` is `enabled and state == "on"`. This matters for a specific case: a user flips off->on, regeneration fails, and D-10 correctly keeps the file already on disk — which could be a `stale` allow-all-equivalent file from before this fix, or `absent`. Reporting only the toggle would then claim a posture that is not there.

#### The Step 5 review fix (`ae36282`)

The first implementation replaced only *part* of the base's block in three shapes, and each partial replace manufactures the silent fail-open this phase exists to prevent. Reproduced live before fixing, regression-tested after.

The worst is a column-0 block sequence, which is how YAML lets `permissions:` take a list without indenting it:

```yaml
permissions:
- capability: all
  effect: deny
```

The region scan stopped at the `- ` item because its first character is not a space or tab, so only the key line was replaced and the base's rule items were left stranded at column 0 after the injected mapping — a root document mixing a mapping with a sequence, which does not parse, which kiro-cli loads anyway with no error and no warning. The post-write self-verify could not catch it: `excise_permissions` made the same wrong split on both sides, so the file read back as a correct `on`. Same class: a comment between the key and its indented value ended the region early, and a quoted key (`"permissions":`) was not recognised at all, leaving a duplicate top-level key.

`-` and `#` now continue a block; the trailing trim hands back trailing blank lines and trailing column-0 comments so a comment introducing the *next* key still belongs to that key; and `-` continues a block only once a `permissions:` key has started, so a sequence belonging to an earlier key is untouched.

#### What Phase 7 needs to measure

Five questions this phase surfaced and could not answer without a live probe. Every one of them fails in the safe direction — toward more prompts, never fewer — so none blocks shipping `on` as opt-in.

1. **Live bind confirmation.** Generate for real, then `tools/acp_permission_probe.py --agent poweratlas-acp` with reject-default: prompt `echo hi` and assert an `ask` with `matchedRule: {shell, ask}` and `source: agent-profile`. That is the confirmation deferred from criterion 14, and it also confirms the shipped overlay's capability names are all accepted.
2. **Whether `resources:` loading is `fs_read`-gated.** The highest-value one. The base agent loads `file://~/.kiro/steering/*.md` and an absolute playbook path, both outside `./**`. If resource loading goes through `fs_read`, an `on` session prompts at session start before the user has typed anything — which would make `on` unusable in practice. A bare `session/new` with no prompt should raise zero prompts.
3. **Whether `./**` matches on Windows.** kiro-cli reported `rawInput.path` as an absolute `c:\Users\…` path in Phase 0, and Step 4's test 1 did not isolate whether `./**` matched it at all. Benign either way: if it does not match, `fs_read` over-asks.
4. **Whether `*` stops at `&&` / `;` / `|`.** One prompt decides it: `git status && echo x` against `match: ["git status*"]`. If it asks, the exact-literal shell patterns can be widened back to kiro's own default shape and the ergonomic cost disappears. Worth doing early, since `allow_always` is never offered over the ACP surface (Phase 0 Step 8), so `git log -5` prompts *every* time under exact literals.
5. **The `context` / `diagnostics` / `sandbox_network` decision.** Include them only if the assembled block still binds with them present; otherwise the omission stands with the reason already recorded in the overlay.

#### Divergences flagged for orchestrator attention

- `POST /api/acp-permissions` **sets** the boolean rather than toggling it (mirrors `/api/notifications`'s route shape, not its verb) — a toggle cannot express "make sure this is off," and the exit criterion "the POST rejects a non-boolean body" presupposes set semantics.
- Shell allow patterns are exact command-string literals, not `*`-widened — kiro-cli's own default posture presumably does prefix-plus-chain-detection a glob cannot safely express, and Phase 0 never measured whether `*` stops at a chaining operator (`git status*` could also match `git status && <anything>`). Conservative default; Phase 7 item 4 above resolves it.
- Three doc-only capability names (`context`, `diagnostics`, `sandbox_network`) are deliberately omitted from the `on` set — never observed live on the ACP surface in Phase 0, and an unrecognised capability name risks rejecting the whole block (the exact fail-open hazard this phase defends against).
- ~~**Generation now runs in both toggle states**~~ **RESOLVED 2026-09-22, `c2f324b`.** Both Step 5 review personas (Security auditor, Senior engineer), working independently from fresh context, reached the same recommendation with reproduced evidence: generate only when `on`; **delete** `~/.kiro/agents/poweratlas-acp.md` when `off`, rather than writing an allow-all block into it. The Security auditor's evidence was decisive — Phase 0 § 9 Step 7 already showed a cross-scope case (an agent with only an `fs_read` rule ran an unrelated `shell` command with zero prompts, inheriting user-scope allow-all rather than kiro-cli's documented ask-by-default), which means a **default-configuration user** (no custom `~/.kiro/settings/permissions.yaml` at all — not only a user with a narrower-than-allow-all baseline) would have their posture widened if they ever manually selected `poweratlas-acp` from kiro-cli's own terminal agent picker (P1: any file under `~/.kiro/agents/` registers in the mode catalogue). User decision, 2026-09-22: adopt generate-only-when-`on`. Absence is now the only representation of `off`, which cannot widen anything by construction — this also restores the Gate resolution's "`off` is a byte-for-byte no-op" claim on the filesystem, not only in ACP-session behaviour. See `derived_block_state()`'s revised four-state model above.
- `GET /api/acp-permissions` reads the derived-agent file on the event loop rather than via `asyncio.to_thread` — matches existing precedent (`/api/remote-access`, every route's `load_config()` call) for settings-panel routes, not a hot path.
- The `isolated_config` test fixture now also redirects `agent_profile.KIRO_AGENTS_DIR` and resets its module state — necessary once `lifespan` regenerates at startup, or every test touching `lifespan` or `/api/save-setting` would read/write the developer's real `~/.kiro/agents/`. Verified after every run: the real agents dir still holds only its original two files, `kiro_default.md` unchanged (hash matches Phase 0's § 9 Step 9 baseline).
- No live kiro-cli probe was run in this phase (authority boundary — Phase 0's brief authorised live probing explicitly, this phase's did not); the five Phase 7 questions above are the result.

#### Step 5b QA verification (2026-09-22) — PASS

`/qdev`'s per-phase QA (this phase is `[QA]`-annotated) ran against a real, isolated `uvicorn`
instance of the actual FastAPI app — module-attribute patching matching `tests/test_web.py`'s own
`isolated_config` fixture pattern (`CONFIG_DIR`, `CONFIG_PATH`, `REMOTE_SECRET_PATH`,
`KIRO_AGENTS_DIR` all redirected to a scratch directory before the app module's first import), on a
scratch port, with a fixture base agent — never touching the live PowerAtlas process or the real
`~/.kiro`. Confirmed live, not only via pytest: `GET`/`POST /api/acp-permissions` round-trip
correctly; enabling generates a derived agent carrying both the `permissions:` key and the
provenance marker; disabling deletes it; a hand-authored foreign file at the derived-agent path
survives a disable (the `unknown`-state guard the Step 5 review fix added, now confirmed end-to-end
rather than only unit-tested); a bad base-agent name is rejected via `/api/save-setting`. One
observation, not a defect: validation failures return HTTP 200 with `{"ok": false, "error": "..."}`
rather than a 4xx status — consistent with this codebase's pre-existing `/api/save-setting`
convention, not something this phase introduced.

### Phase 2 (2026-09-22) — mode wiring and frame enrichment

Implementation (2026-09-22, code: `dba7684`)

**Change 1 — `_VALID_TASK_MODES`.** `DERIVED_AGENT_NAME` joined the existing `from .config import CONFIG_DIR` line, so `acp.py` still imports from exactly two intra-package modules (three names in all, after this phase — the module header comment was corrected to say so, see below). The docstring no longer claims to mirror kiro-cli's enumeration; it separates the eight vendor values from PowerAtlas's own derived agent and records a new exposure this member creates (see "R-2 exposure" below). A test asserts `acp.DERIVED_AGENT_NAME is config.DERIVED_AGENT_NAME` — an identity check a copied literal would fail.

**Change 2 — the permission frame.** A new `_project_consent` helper allowlists `capability`, `resource`, `scope`, `source` through `_as_text`, and rebuilds `matchedRule` as its own nested `{capability, effect}` allowlist rather than forwarding the object whole — a rule can legally carry `match`/`exclude` lists, and forwarding it whole would put unbounded agent-authored arrays in a browser. Accepts any input, returns `{}` for anything not a dict. The `_meta` → `kiro` → `consent` walk in `_on_permission_request` is now `isinstance`-guarded at each level instead of `or {}`-chained — the chain form raises past the point where `_pending_permission[request_id]` is stored, inside a `call_soon_threadsafe` callback where asyncio's default handler swallows the traceback, leaving a stored request nothing can answer. `toolCall.title` in the frame is now unclamped; `MAX_PERMISSION_TITLE_CHARS` moved to gate only the `_notify(...)` argument.

**Change 3 — `load_session`'s mode.** Resolved by comment (the route the phase explicitly sanctioned), not by threading a real mode through: `_handle_load` has no per-session mode to pass, and P2 measured kiro-cli ignores the parameter on this method anyway, so manufacturing one would read as a stronger posture claim, not a more honest one. Three call sites now document this explicitly, dated and citing P2.

**R-2 exposure introduced and documented, not fixed** *(superseded — fixed server-side in the Step 5 review pass, `04d9360`; see "Step 5 review fix pass" below)*. Phase 1's revised design (§ 9 Phase 1's Gate resolution) deletes the derived agent file entirely when the setting is off. `_VALID_TASK_MODES` now accepts `"poweratlas-acp"` as a modeId regardless of the toggle, so a client that sends it while off gets kiro-cli's silent coercion to `vibe` (R-2) rather than an error — `acp.py` cannot detect the toggle state without importing `agent_profile`, which D-20's isolation boundary forbids. SC-3 places the gate upstream (the picker's Default entry resolving to the base agent while off); the comment states plainly that nothing in `acp.py` itself enforces this. **Flagged for Phase 3 to own** (verify the picker actually excludes the derived-agent option while off) **and for Phase 7's live `modeId_in_effect` probe to catch** if it doesn't.

**Two stale claims found outside this phase's file scope, reported not fixed**: `agent_profile.py:62` still says `acp.py` "imports exactly two intra-package names" (now three, same two modules); `config.py:115` says `acp.py` "imports config and nothing else intra-package," which was already false before this phase (`launcher` is the other) — neither file is in Phase 2's scope.

**Tests**: 1568 passed, 1 skipped (up from 1562 + 1; 6 new tests). `tests/acp_page.test.mjs` not run — no template or static file touched this phase. A same-phase follow-up commit (`dba7684`) corrected three comment claims a pre-handback review pass caught: the module-header import count, a citation of "D-10 as revised" that should have cited the actual Gate-resolution decision, and an unverified claim that Phase 3's UI already gates the picker.

#### Step 5 review fix pass (2026-09-22, code: `04d9360`, `e0f25d0`)

**Derived-mode gate.** `_VALID_TASK_MODES` membership is now necessary but not sufficient for `poweratlas-acp`. `acp.py` gains `mode_gate_hook` / `set_mode_gate_hook` on the existing `notify_hook` / `sessions_changed_hook` pattern — `None` is permissive, so `acp.py` stays usable standalone and D-20 holds (no new import). `_handle_new` consults it, through `asyncio.to_thread`, only when the derived mode is requested, and refuses with the existing `bad_payload` shape if it answers falsy. `web.py` wires it in `lifespan` to `agent_profile.derived_block_state() == "on"`, so `absent`, `stale` and `unknown` all refuse — a hand-authored same-named file is never handed to a user as PowerAtlas's profile. **A hook that raises fails closed** (refuses): failing open would forward a modeId kiro-cli silently runs as `vibe`, the exact failure the gate exists to prevent. Consequence: the session-open path now does one read-only file classification, only for the derived mode — D-9's "no filesystem I/O on the session-open path" was about regeneration and still holds for it. `web.py` was outside Phase 2's file scope; the gate can only be wired there, because `acp.py` may not import `agent_profile`.

**`session/load` wire left unchanged, deliberately.** The vendored KAS bundles (kiro-cli 2.22.0, 2.22.1, and the installed 2.23.1) make `_meta.kiro.modeId` optional in the schema, so omitting it is schema-safe — but `hydrateSessionForLoad` selects `persisted ? persisted.metadata.agentMode : modeId ?? "vibe"`. P2's "ignored on load" therefore holds only when the session has persisted metadata; without it, the request's `modeId` is the fallback, and omitting the key would move that fallback from `kiro_default` to `vibe`. The comments now record this reading instead of "ignored". A Phase 7 probe was added to settle which value PowerAtlas should send.

**Permission-frame hardening.** `_project_consent` now omits missing and non-string fields at both levels and drops an empty `matchedRule` (it previously emitted `''`, contradicting its own docstring). `_on_permission_request` refuses a JSON-RPC id that is not a string or integer (bool excluded) before the `_pending_permission` store — an unhashable id used to raise `TypeError` there, inside the same callback, hanging the agent. Comments no longer say asyncio "swallows" that callback's exceptions (its default handler logs them at ERROR; the hang is real because nothing answers the agent). The `MAX_PERMISSION_TITLE_CHARS` comments now say the clamp covers only the server desktop toast; the in-page browser `Notification` in `acp.html` needs a client-side clamp, now a Phase 3 exit criterion. The measured-consent test also asserts `requestId`, `sessionId`, `toolCall` and `options` survive.

**Housekeeping.** Seven dead citations of three archived plans repointed to their `plans/done/` names. The stale import-count comments in `agent_profile.py` and `config.py` corrected (comment-only). Reported, not fixed: `tests/test_web.py:~3848` has one more "the same handler swallows it" line.

**Tests**: full suite 2020 passed, 2 skipped; `tests/test_web.py` 1580 passed.

#### Step 5b QA verification (2026-09-22) — PASS, 8/8

Driven over the real `/ws/acp` socket against an isolated `uvicorn` instance (same module-attribute isolation as Phase 1's QA; never the live process or the real `~/.kiro`). The one stub is `_supervisor.new_session`, which runs *after* the gate, so the "gate passes" probe spawns no real kiro-cli; the gate and `web.py`'s hook wiring are real. Confirmed: the real `lifespan` registers the hook; with the setting off, `new` for `poweratlas-acp` is refused with `bad_payload` before any `pending` frame or spawn; a hand-authored same-named file (state `unknown`) is still refused; after enabling through `POST /api/acp-permissions` the gate passes and reaches spawn; a vendor mode (`kiro_default`) is never gated. *(Superseded 2026-09-23 by the Phase 3 review fix `4fc6923`: `kiro_default` is now the Default entry's wire value and **is** resolved through the gate — see § 9 Phase 3.)* Not exercised here: the permission frame's consent projection against a live agent prompt — that needs a real kiro-cli permission round trip and is covered by the unit suite plus Phase 7's live probes.

### Phase 3 (2026-09-23) — settings and permission-prompt UI

Executed as a parallel group with Phase 4 (disjoint file scopes, confirmed by comparing the two
returned changed-file manifests before committing). Both sub-agents ran commit-suppressed; the
orchestrator committed each phase serially.

Implementation (2026-09-23, code: `04982bf`)

Phase 3 builds the UI half of the ACP permission profile. The dashboard settings menu gets an "Agent permissions" section. It has a Permission profile toggle (`#acpPermToggle`), a Base agent input (`#acpPermBaseAgent`), a scope line stating that the setting applies to newly created sessions only (D-12), and an on-but-not-in-effect report. The report follows the `restart_pending` drift-badge pattern: a "not in effect" badge on the toggle row, plus an amber warning line that carries `generation_error`. Every element is addressed by id, never by `.topbar-toggle`. The toggle posts `{enabled: <value it is moving to>}` to `POST /api/acp-permissions`, because that route sets the value rather than flipping it. Success is read from the body's `ok`, because a refusal arrives as HTTP 200. The base agent is trimmed and saved through `/api/save-setting` under `acp_permission_base_agent`. After a rejection, the value in force is written back into the field, even when the field still has focus after an Enter save. The rows render `in_effect`, never the toggle alone. The toggle has no Jinja initial state: that would need a render-context key in `web.py`, which is Phase 4's file. It is filled from `GET /api/acp-permissions` on load and whenever the tab becomes visible again.

The permission prompt now renders the frame's `consent` block through a new `permissionConsentBlock` in `transcript-renderer.js`. `addPermissionRequest` gains an optional trailing `consent` argument, which both `acp.html` and `index.html` pass. Only string values of the five allowlisted fields are shown, with `matchedRule` shown as "capability -> effect". Values are set through `textContent` only. An absent, empty or malformed block renders the prompt exactly as before. Length is contained in `style.css`: consent values wrap anywhere and scroll inside a 12em maximum height. The now-unclamped title gains `overflow-wrap: anywhere` and is never cut. The in-page browser `Notification` in `acp.html` clamps the title to 200 characters (`NOTIFY_PERMISSION_TITLE_MAX`, which matches `MAX_PERMISSION_TITLE_CHARS`), while the transcript row keeps the full title.

SC-3's picker rule lives in `composer-chrome.js` so both pages share it. No picker option ever carries `poweratlas-acp`. Instead, the Default choice is rewritten at send time to the derived agent only when `enabled && in_effect`. When the profile is off, or on but not in effect, it sends `kiro_default`, the server's own fallback. A user-named base agent is not a valid `modeId`, so "base agent when off" means `kiro_default` on the wire. The state is re-read when a picker opens, on page load and when the tab becomes visible in `/acp`, and after any `bad_payload` error. That last case means a stale tab whose derived-agent create was refused shows the server's message and sends `kiro_default` next time. A Default create waits for the state read if one has never completed or one is in flight, rather than guessing `kiro_default` while the profile might be on. The dashboard's quick create also resolves through the same rule; before, it hardcoded `kiro_default`.

Tests: `tests/acp_page.test.mjs` gains 17 checks. They cover consent rendering, malformed consent, an injection payload in the title and resource, a 500-character title, a 48 KB resource with its CSS containment, the notification clamp, the picker in the off, in-effect, on-but-not-in-effect and race cases on both pages, the stale-tab refusal, and the settings rows. The panel and dashboard-picker harnesses gained the permission elements and an `/api/acp-permissions` route. The existing quick-create check became async because the first Default create now waits one microtask for the state read. The result is 687 passed, 0 failed, and a mutation run confirmed that the new checks fail when the behaviour is removed.

**Divergences declared by the implementer** (judged by the review; the client-side resolver ones
are superseded by the review fix below): no Jinja initial state for the toggle (`web.py` was Phase
4's file); "base agent when off" sent as `kiro_default`, since a user-named base is not a valid
`modeId`; a second copy of the `poweratlas-acp` literal in `composer-chrome.js` *(removed by the
review fix)*; the shared resolver placed in `composer-chrome.js` *(removed by the review fix)*;
`dashRailQuickCreate` made to resolve Default too; a Default create made to wait for the state read
*(removed by the review fix)*.

#### Step 5 review fix pass (2026-09-23, code: `4fc6923`)

Both reviewers independently found the same High: when the page's read of `/api/acp-permissions`
failed, it resolved Default to `kiro_default`, starting an ungated session that looked gated. On the
remote phone page the read *always* fails (the route is not in `_REMOTE_ALLOWED_PATHS`), and Phase 5
would add stale tabs. **User decision, 2026-09-23: the server resolves Default.** The fix pass
implemented that plus 13 auto-fixes:

The Phase 3 review fixes move the Default decision from the page to the server, as the user decided on 2026-09-23 (G1). Every page path now sends the Default entry's own value, `kiro_default`. That covers the /acp picker, the dashboard picker, the dashboard quick create, and both close-then-create paths. The client resolver in `composer-chrome.js` (`DERIVED_AGENT_MODE`, `refreshAcpPermissionState`, `wireTaskMode`, `withWireTaskMode`) is deleted along with every call site, so no page needs `/api/acp-permissions` to create a session. The settings panel still reads that route for display. In `acp.py`, `_handle_new` treats `kiro_default` and an omitted mode as Default. It asks `mode_gate_hook` whether the permission profile is in effect, then binds `DERIVED_AGENT_NAME` if it is and `kiro_default` otherwise, which is D-10's fallback. An explicit `poweratlas-acp` request uses the same predicate and is refused unless in effect. The refusal copy now points to Settings and no longer says "turn it on". A hook that raises refuses the create on both paths with its own "could not check" message, rather than falling back to an ungated session. A `None` hook reads as not in effect: Default binds `kiro_default` and the derived agent is refused. The created `session` frame now carries the bound `mode`. `web._derived_agent_in_effect` is now exactly `_acp_permission_state`'s `in_effect` (setting on and file `on`), so a leftover `on` file left behind while the setting is off is never used. `acp.py` still imports only `.config` and `.launcher` (D-20). One consequence is deliberate: while the profile is in effect, `kiro_default` cannot be chosen explicitly. Another is that every Default create now costs one off-loop config read plus one file classification. On the dashboard, a refused create now replaces the "Creating session…" placeholder with the server's message in the transcript, and brings the composer back when a session is still attached (G2). The settings rows gained several things. They warn when a base-agent change failed but the previous profile is still in effect (G3). They light the settings-gear dot, shared with restart drift and combined by OR, while the profile is on but not working as set (G4). The scope note now covers reopened sessions (G5), and a visible line says what on and off mean (G6). The toggle is a keyboard-operable `role="switch"` with `aria-checked` kept in sync (G7). Each warning names a next step (G9). A failed state read shows as "could not read" (`aria-checked="mixed"`) instead of off (G10). The error toasts have a dismiss button (G12). Permission prompts bound the unclamped title's height with internal scroll (G8). Known capability and source identifiers show as plain words with the raw id kept, looked up with `hasOwnProperty` (G11). A doc comment now names `permissionConsentBlock` correctly (G13), and `agent_profile.py`'s D-19 comment now describes the atomic secret writes (G14). Python tests cover Default binding the derived agent when in effect, `kiro_default` when off and when on but not in effect, refusal when the hook raises, the leftover `on` file, and the bound mode in the frame. The Node harness drops the resolver checks and asserts that every Default path sends `kiro_default` without reading `/api/acp-permissions`. Six Python and seven JS mutations were each confirmed killed. Results: `node tests/acp_page.test.mjs` 693 passed; `tests/test_web.py` 1657 passed; the other modules 440 passed and 2 skipped; `_check_test_names.py` is clean.

**Divergences from the fix pass, recorded for the reader:**

- While the profile is in effect, `kiro_default` cannot be chosen explicitly: the Default value
  always binds the derived agent. This is SC-3 read literally, and the intent of "on".
- **Reverses a Phase 2 statement.** § 9 Phase 2 says "`None` is permissive, so `acp.py` stays
  usable standalone". After `4fc6923` a `None` hook reads as *not in effect*: Default binds
  `kiro_default` and an explicit `poweratlas-acp` is refused. `acp.py` is still usable standalone;
  it just never offers the derived agent without a hook to vouch for it.
- An omitted `mode` is now resolved as Default, rather than passed through as `None` — otherwise a
  client that omits the mode would bypass the profile.
- **Extends Phase 2's cost note.** Every Default create now does one `load_config()` plus one
  derived-file classification, off the event loop. Phase 2 recorded that cost as derived-mode-only.
- A hook that raises refuses with a dedicated "could not check" message, distinct from "not in
  effect", so a broken check is never reported as the profile being off.
- The bound mode travels only on the created `session` frame (`mode`). No page renders it: SC-8
  and the 2026-09-21 decision reject a per-session badge.
- The settings-gear dot is the existing `topbarPendingDot`, shared with restart drift, each source
  keeping its own flag so one cannot clear the other's reason.
- Reported, not fixed (outside scope): `autostartToggle` and `notifyToggle` have the same keyboard
  gap the new toggle's fix closed; after a refused dashboard create, the attached session's earlier
  transcript is not repainted (predates Phase 3).

#### Step 5b QA verification (2026-09-23) — PASS, 13/13 (HTTP), with a gap named

The Chrome extension was not connected, so no real browser was driven. The substitute: an isolated
`uvicorn` of the real app (scratch config and `~/.kiro/agents`, port 18918), asserting the served
`index.html` carries every new settings element and the scope copy, the served
`composer-chrome.js`/`acp.html` load, and the full API round trip the rows depend on — off/absent
initially, on → `in_effect`, a bad base-agent name rejected, off → absent. DOM behaviour itself is
covered by the node harness (693 checks). **Not exercised in a real browser**: layout, long-content
containment, and screen-reader announcement — carried to Step 9b.

One state-dependent observation from this QA, not a failure at the time: renaming the base agent to
a missing one after a good generation keeps the last-good file (D-10), so `in_effect` stays `true`
and the panel showed no warning. Raised as review finding UX-3 and fixed in `4fc6923`.

### Phase 4 (2026-09-23) — local secret, login code, exchange route

Parallel group with Phase 3 (see § 9 Phase 3). Committed by the orchestrator.

Implementation (2026-09-23, code: `6ff4b50`)

Phase 4 adds a local secret, stored in its own file beside the remote secret. `config.py` gains `LOCAL_SECRET_PATH` (`CONFIG_DIR/local-secret`) and three functions: `load_local_secret`, `ensure_local_secret` and `rotate_local_secret`. A `local_secret_status()` accessor reports the secret's state. Loading applies the same fail-closed `REMOTE_SECRET_MIN_LEN` check as the remote secret, so an empty, whitespace-only, truncated or short file reads as "no usable secret". Both secret files are now written by one shared `_write_secret_file`, a fixed-mode create-truncate, which `_write_remote_secret` now calls as well. This approach was chosen over tmp then `os.replace` on purpose. A torn secret fails closed through the length check, while D-19's atomic rule exists for the derived agent file, whose torn state fails open.

Under D-22, a write failure in `ensure_local_secret` does not return an empty secret. The generated value is kept in `_local_secret_memory` for the life of the process, and later calls return that same value. The failure is logged at ERROR and recorded in `_local_secret_persist_error`. `GET /api/settings` exposes the status as a new `local_secret: {persisted, error, path}` field, which never includes the secret. `rotate_local_secret` behaves like the remote rotation instead: a failed write changes nothing. Keeping a rotation only in memory would revoke cookies that the old file brings back at the next restart. `web.lifespan` loads the secret at startup with `set_local_secret(await asyncio.to_thread(ensure_local_secret))`.

In `web.py`, the loopback cookie is `pa_local`. It is separate from `pa_device` and signed with a different key (D-17). It has the same shape as `make_device_cookie`, `subject.stamp.hmac`, with the constant subject `loopback` so one parser shape serves both cookies. `make_local_cookie` mints it. `_local_cookie_ok(scope)` is the check Phase 5's gate will call. It never raises, and it rejects a missing secret, a wrong name or subject, non-ASCII-digit stamps, stamps more than 300 s in the future, stamps older than 90 days, and bad signatures. The signature comparison uses `compare_digest` over UTF-8 bytes. `_set_local_cookie` is the one place that sets the cookie's attributes: `HttpOnly`, `SameSite=Strict`, `Path=/`, no `Domain` and no `Secure`. The cookie is therefore host-only, and neither its name nor its path depends on the Host header, so Phase 5 can introduce a canonical loopback host without changing this code.

Login codes follow D-21. `power_atlas.web.mint_login_code()` is a plain in-process function, never a route, and a test asserts that no route's endpoint is the mint function. It returns `secrets.token_urlsafe(32)` and stores the code with its mint time from `_login_now`, a module seam that is `time.monotonic`. The store holds at most 64 codes, purges codes past the 120 s TTL, evicts the oldest code when full, and is guarded by a `threading.Lock` because Phase 5's tray and peek threads will mint while the event loop exchanges. `login_path(code)` builds the door URL path.

The exchange is `GET /local-auth?code=...`. It checks the query length (512 bytes or less) and the field count before parsing, and checks the code's shape before the code reaches a throttle key or a log line. It then checks the backoff before the comparison. The code is consumed with a `compare_digest` against every outstanding code, so a code works exactly once. Success returns 303 to `/` with the cookie, `Cache-Control: no-store` and `Referrer-Policy: no-referrer`. Refusals are script-free HTML pages that say to open PowerAtlas from the tray: 400 for a malformed code, 429 for a throttled one, 403 for an unknown, expired or used one, and 503 when there is no secret.

The hardening mirrors `remote_auth_exchange` except for the D-16 difference: failures are recorded per code in `_login_failures`. The existing backoff helpers take an optional `store` argument for this, and the table keeps the existing 512-entry bound. A code that one process keeps failing therefore never locks out another valid code from the same 127.0.0.1 peer. Refusal logging is limited to one WARNING per 60 s, because a local process can invent a new code for every request. The route is loopback-only because it is left out of `_REMOTE_ALLOWED_PATHS`. Phase 5 must exempt `/local-auth` from its gate.

`POST /api/local-secret/rotate` writes the new secret before it applies it in-process, and it clears outstanding codes. It sets the caller's replacement cookie in the same response. It does this only for a caller that presented a valid `pa_local` cookie; any other caller gets 403 and nothing is rotated, so the route can never mint for a stranger. No route is gated yet, and `_ACP_TOKEN` is untouched.

`tests/test_web.py`'s autouse `isolated_config` fixture now redirects `LOCAL_SECRET_PATH` and resets the D-22 state and `_LOCAL_SECRET`. The Phase 4 tests are grouped into four classes: `TestLocalSecretFile`, `TestLocalCookie`, `TestLoginCodeExchange` and `TestLocalSecretRotation`.

Other files in the working tree were modified when this phase started and were not touched here: Phase 3's templates, static files and `tests/acp_page.test.mjs`, plus `docs/KNOWLEDGE.md`. `tests/test_config.py` has its own `isolated_config` that does not redirect `LOCAL_SECRET_PATH`. That file is outside this phase's scope, and none of its tests reach the new functions.

**Divergences declared by the implementer:**

- `_write_remote_secret` now delegates to a shared `_write_secret_file`. *(The create-truncate
  choice it carried was **reversed** by the review fix — see below.)*
- The exchange is `GET /local-auth?code=…`, not a POST form: a door opens a URL, so the first
  browser load must be the exchange. Body/field ceilings became a 512-byte query cap plus
  `parse_qsl(max_num_fields=64)`, both before parsing.
- `POST /api/local-secret/rotate` re-issues a cookie only to a caller already holding a valid
  `pa_local` (403 otherwise, nothing rotated) — without this the route would be a self-service mint
  (D-3). Consequence: rotation is unusable until Phase 5's doors hand out cookies.
- A failed rotate write changes nothing; D-22's in-memory rule applies only to `ensure`.
- The startup load is wired in `web.lifespan`, not `__main__.py` (Phase 5's file).
- D-22 is reported through a new `local_secret` field on `GET /api/settings`; the UI does not
  render it yet.
- The backoff helpers gained an optional `store` argument; remote behaviour and tests unchanged.
- Helpers the plan does not name: `login_path(code)`, a once-per-60 s refusal WARNING, and clearing
  outstanding codes on rotation.

#### Step 5 review fix pass (2026-09-23, code: `3a4f3b5`)

The Phase 4 review auto-fixes (F1 to F11) are applied. `config._write_secret_file` now writes atomically, the same way `save_config` does. It writes a `<name>.tmp` beside the target with mode 0o600, loops until every byte is written (a write that makes no progress counts as a failure), fsyncs, and then calls `os.replace`. On any failure it deletes the tmp. This reverses the Phase 4 choice of create-truncate: that approach truncated the working secret before a write that could then fail, so a failed rotation destroyed the file while reporting that nothing had changed. The remote secret shares the writer and otherwise behaves exactly as before. `ensure_local_secret` now tells a missing file apart from one it cannot read. Only a missing file, or one that is readable but holds unusable content, is replaced. A file that exists but cannot be read or decoded is left untouched and logged at WARNING, and the process uses a D-22 in-memory secret through the new `config.hold_local_secret_in_memory`. That function is also the new R-13 fallback when `lifespan`'s now-guarded local-secret setup raises anything unexpected. In both cases `/api/settings` reports the reason. The local secret read is capped at 4096 bytes. `/api/local-secret/rotate` now writes and applies the secret synchronously on the event loop, as the remote rotate route does, so concurrent rotations cannot leave the process and the file holding different secrets. A comment at the login-code clear records the accepted race with a door-minted code. The no-secret refusal on `/local-auth` now shares the rate-limited refusal logger and keeps ERROR severity, and the suppressed-refusal count is flushed during `lifespan` teardown. On the test side, the `isolated_config` fixture in `tests/test_config.py` now redirects `LOCAL_SECRET_PATH`, and two startup tests now use `local_enabled` for teardown. New tests cover failing and short low-level `os.write` calls, unreadable, undecodable and oversized files, startup surviving an unexpected error, loop-bound rotation, refusal of a remote-key cookie when the local key is empty, a per-candidate `compare_digest` in `_consume_login_code`, and rate-bounded and flushed refusal logging. Each new test was confirmed to kill its named mutation. Two items remain outside this phase's file scope: the comment at `agent_profile.py` lines 46-48, which still describes the old in-place secret write, and the plan's own Phase 4 divergence note, which F1 reverses.

*(Both out-of-scope items are now closed: the `agent_profile.py` comment in `4fc6923`, and the
divergence note above, which is marked reversed.)* Further fix-pass divergences: rotation runs
synchronously on the loop rather than under an `asyncio.Lock` (a module-level lock binds to the
first loop that contends it, and the suite runs several); only an *unreadable* existing file is
protected from overwrite — a readable file with unusable content could never have signed a cookie,
so it is still replaced; the remote loader keeps its old read behaviour (no cap, no
missing-vs-unreadable distinction).

#### Step 5b QA verification (2026-09-23) — PASS, 11/11

An isolated `uvicorn` of the real app and real `lifespan` (scratch config dir, port 18917; never the
live process or the real config dir), run against `6ff4b50`. Confirmed live: the lifespan creates the
local secret; `GET /api/settings` reports `local_secret.persisted` without exposing the secret; a
code minted in-process exchanges in one navigation for a 303 to `/` carrying `pa_local` with
`HttpOnly`, `SameSite=Strict`, `Path=/`, plus `no-store` and `no-referrer`; a replay gets a 403 HTML
page, a malformed code 400, a well-formed unknown code 403; rotation without a cookie is refused 403,
and with a valid cookie writes a new secret and returns a replacement cookie; no route is gated yet.
Not re-run after the fix pass: the fix pass changed the write path and startup guard, both covered by
the fix pass's new unit tests, each with a confirmed-killed mutation.

## Follow-up Work (Deferred)

1. **Fail-closed on generation failure.** R-3 accepted rather than fixed: a session whose derived agent
   could not be generated *and* which has no last-good file runs ungated while the toggle reads on,
   reported only in the settings panel. Source: D-10, R-3.
2. **Derived-agent staleness against a changed base.** Regeneration is at startup and on settings
   change, so a base change mid-run is not reflected until restart. Governance drift only, never the
   gate. Source: D-9.
3. **`/partials/launchers` env leak.** Its own `[SECURITY]` roadmap item. SC-5 removes anonymous
   reachability but does not fix the leak. Source: Intent scope boundaries.
4. **The unattended rule engine.** Its own roadmap item, carrying the Automation-gating role. Now also
   inherits the protection-model role this plan's deny floor would have played — a session with
   nobody watching needs its own answer to "never-okay actions," since D-13 was superseded rather
   than fixed (see § 9 Gate resolution, user decision 2026-09-22). Source: D-1.
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

### 2026-09-22 — Implementation Review (after Phase 0, persona: Security auditor, Senior engineer)

Implementation health: Green (all findings resolved — 7 fixed, 1 user-accepted). 8 findings
(0 High, 5 Medium, 3 Low).

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | Medium | `_pick_option`'s reject-path fell back to `options[0]` when no reject-kind option existed, which could silently pick an allow-shaped option | Fixed — reject path now returns the same no-match error-reply as an empty option list, never guesses allow |
| 2 | Medium | `Probe.spawn()` discarded kiro-cli's stderr unconditionally, foreclosing a diagnostic signal for the exact class of failure (silent fail-open) Gate finding #6 found | Fixed — stderr captured to the harness's own stderr under `--verbose`, never into the redacted `--json-out` frame log |
| 3 | Medium | Phase 1/Phase 7 exit criteria had no check for rule-assembly completeness, which is now the sole backstop for `on` with the floor gone; Step 7's capability-silence finding was missing from the Gate resolution's "remaining obligations" list | Fixed — added explicit Phase 1 structural criteria (every capability named, `exclude` populated, finding #6 decision recorded) and a Phase 7 live behavioral pair; folded the capability-silence finding into the Gate resolution's obligations list |
| 4 | Medium | The probe harness never documented that the caller owns `~/.kiro/agents/` file lifecycle, nor named the bare-colon YAML pitfall that broke 7 of Phase 0's own 9 step-7 runs | Fixed — added a docstring paragraph naming both explicitly |
| 5 | Medium | All three Phase 0 commits (`ea79482`, `7114f33`, `341780b`) carry a `Claude-Session:` trailer, which the user's own CLAUDE.md explicitly bans and states overrides the harness default | User: accepted — leave as-is; nothing pushed to `origin` yet, and no `--amend`/`rebase -i` available to fix it safely. All commits from this point on in the session omit the trailer |
| 6 | Low | "Status: blocked" (Phase 0 body text and § 9's opening) had no forward pointer to the Gate resolution, reading as still-blocking to a top-down reader | Fixed — one-line forward references added at both sites |
| 7 | Low | Risk table R-4/R-5 said "Moot" after the floor's removal, which reads as "problem gone" rather than "accepted, not mitigated" | Fixed — reworded to state the accepted residual exposure explicitly |
| 8 | Low | § 9 Step 3's prose still said the harness was "not yet committed... held pending the Gate decision," stale relative to the exit-criteria line two paragraphs above it | Fixed — synced to record the actual commit and the two subsequent Step 5 fixes |

Cycle 2 skipped per user instruction ("1 qreview cycle per phase," `/qdev` invocation 2026-09-22).
Auto-fixes applied directly by the orchestrator (Light/inline-style editing — these were documentation
and small, well-specified code changes, not a fresh sub-agent implementation phase). Finding 5 closed
by user decision 2026-09-22 (leave as-is).

### 2026-09-22 — Implementation Review (after Phase 1, persona: Security auditor, Senior engineer)

Implementation health: Green (all findings fixed). 20 findings merged across two independently
dispatched personas working from fresh context against the real code (3 High — each reproduced by
at least one persona, one confirmed independently by both — 6 Medium, 11 Low). Both personas also
independently reached the same recommendation on the phase's one flagged design divergence.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | The post-write self-verification ran **after** `os.replace`, so a bad splice replaced (not preserved) the last-good file — contradicting D-10, the module's own docstrings, and exit criterion 11 | Fixed — reordered to stage → fsync → read the staged file back → verify → only then publish; 2 mutation-verified regression tests, one spying on `os.replace` to assert it is never called when verification fails |
| 2 | High | A closing frontmatter fence with trailing whitespace (`--- `) was not recognized, so the `permissions:` block was spliced into the markdown body instead of the frontmatter — silent fail-open, reported as success | Fixed, two parts — a structural sanity guard requiring every column-0 frontmatter line to look like a mapping key (load-bearing, catches the shape regardless of kiro-cli's real fence behavior), and the fence comparison switched to full `rstrip()` (never `.strip()`, which would create a worse bug) |
| 3 | High | A base agent with a uniformly-indented frontmatter root mapping (valid YAML) was missed by the column-0-anchored key regex, producing two live `permissions:` blocks with the base's own rules still active | Fixed — fail closed on any frontmatter root that isn't a column-0 mapping (indented root, tab-indented, and root-sequence variants all now raise a typed error rather than guessing); 3 parametrized regression cases |
| 4 | Medium | The derived agent's own `permissions:` block could be rewritten by the agent itself with one `fs_write` approval — Phase 0's Kiro-scope built-ins cover `~/.kiro/settings/` but only ask-gate `.kiro/agents/**`, and it was unestablished whether that covers the `~`-prefixed home path at all | Fixed — added `{capability: fs_write, match: ["**/.kiro/agents/**", "**/.kiro/settings/**"], effect: deny}` to the overlay; explicitly not a reintroduction of the rejected D-13 floor (a path match, not a rephrasable command pattern) |
| 5 | Medium | **Design divergence**: generation ran in both toggle states, writing an allow-all file even when `off` | Fixed by user decision — switched to generate-only-when-`on`, delete-on-`off`, guarded on a provenance marker so a hand-authored same-named file is never touched; see § 9 Phase 1 "Divergences" for full reasoning |
| 6 | Medium | No test asserted default-`off` startup behavior for the on-disk file | Fixed — 3 new `lifespan` tests (default-off writes nothing / off removes a prior `on` file / off leaves a foreign file alone) |
| 7 | Medium | `POST /api/save-setting`'s base-agent-rename branch returned no generation-outcome info, unlike `POST /api/acp-permissions` | Fixed — both routes now return the same generation-status fields |
| 8 | Medium | `_regenerate_derived_agent` called `load_config()` outside the generation lock, so two rapid settings writes could race and leave the on-disk file disagreeing with `config.toml` by scheduling rather than request order | Fixed — `load_config()` now runs inside the locked region via `agent_profile.sync_from_config()` |
| 9 | Medium | The `ae36282` regression tests used the same splice/excise logic to verify their own correctness — the exact blind spot that let findings 1-3 slip through | Fixed — new regression tests carry an independent check each (e.g. an independently-scanned fence index, a spy on `os.replace`) rather than only round-tripping through the code under test |
| 10 | Low | A trailing blank line in `permissions.yaml` made generation fail permanently (asymmetric whitespace comparison) | Fixed — both sides of the comparison normalized consistently |
| 11 | Low | A CRLF base agent produced a mixed-line-ending derived file; the docstring's stated rationale described the opposite direction | Fixed — injected block now adopts the base's dominant line ending; docstring corrected to state both directions; end-to-end CRLF regression test through `regenerate()` |
| 12 | Low | The shipped `git status`/`log`/`diff`/`branch` allow-list permits repository-controlled code execution via `.gitattributes` textconv or repo-local `diff.external` — reproduces kiro-cli's own documented default, not a regression | Fixed — noted in an overlay comment; flagged as a constraint on Phase 7's shell-pattern-widening item |
| 13 | Low | A hard kill between opening the tmp file and `os.replace` could leave a stray `.tmp` file inside `~/.kiro/agents/`, a directory kiro-cli scans for its mode catalogue | Fixed — staging file moved outside `~/.kiro/agents/` (same volume) and cleared at the start of every pass |
| 14 | Low | `_frontmatter_bounds`'s docstring asserted kiro-cli's fence-recognition rule as measured fact; only that an inline `permissions:` block loads was actually measured | Fixed — docstring now marks it as an assumption pending Phase 7 live verification |
| 15 | Low | The `-`/`#` block-continuation ordering invariant (why an earlier key's sequence is never absorbed) lived only in a commit message, not in the code | Fixed — comment added at the scan logic it governs |
| 16 | Low | Two exit-criteria checkboxes cited stale test counts from before the `ae36282` fix commit | Fixed — corrected to the real current counts (23 injection tests, 1562 passed / 1 skipped) |
| 17 | Low | `COM0`/`LPT0` were not in the Windows-reserved-name rejection set (only `COM1`-`9`/`LPT1`-`9`) | Fixed — widened defensively, no live defect found |
| 18 | Low | `patch.object(agent_profile.os, "replace", ...)` in a test mutates the process-global `os` module reference rather than something scoped | Fixed — one-line comment added noting the scope and that it's restored |
| 19 | Low | CRLF-on-Windows: `os.path.replace`/write-mode discrepancies between the module and `save_config`'s established pattern | Not a separate finding — covered by findings 1 and 11 together; no additional action |
| 20 | N/A | A new test (`test_the_real_base_agent_on_this_machine_is_accepted`) deliberately reaches past the `isolated_config` fixture's redirection to read the real `~/.kiro/agents/kiro_default.md` (read-only, skips if absent) — a documented exception to the fixture's "tests never touch real `~/.kiro`" principle | User: accepted — keep it; catching "the shape guard rejects the operator's actual real-world agent" as a suite failure outweighs the purity break, given the test is read-only and degrades gracefully |

Both personas, working independently from fresh context, recommended the same resolution for finding 5
before either saw the other's report — presented to the user as a converged recommendation rather than
a council escalation, given the strength of that convergence; user confirmed. Cycle 2 skipped per user
instruction ("1 qreview cycle per phase"); the fix batch's own test run (1562 passed, 1 skipped, up from
1526) and the three mutation-verified High-finding regression tests serve as this cycle's verification
in place of a second full review pass.

### 2026-09-22 — Implementation Review (after Phase 2, persona: Security auditor, Senior engineer)

Implementation health: Green (all fixed or deliberately routed to a later phase). 12 findings
(0 High, 3 Medium, 9 Low). The Senior engineer also checked and cleared one concern (history replay
of the new `consent` block — `_emit` stores the whole payload), recorded here rather than as a finding. The Security auditor's first dispatch failed on an API weekly limit; it was
re-dispatched after the user confirmed quota was back, and completed.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | Medium | [Security] The server accepted `poweratlas-acp` whatever the setting; the only planned gate was the Phase 3 picker, so a stale tab silently fell back to `vibe` | Fixed — server-side `mode_gate_hook` refuses unless `derived_block_state() == "on"`, fails closed on hook error (`04d9360`); verified live |
| 2 | Medium | Both personas: `session/load` still puts `modeId: "kiro_default"` on the wire; a future kiro-cli honouring it would rebind derived-agent sessions | Fixed as a finding, wire unchanged — KAS source shows omission would move the no-metadata fallback to `vibe`; comments corrected, Phase 7 probe added |
| 3 | Medium | [Senior] The Phase 3 picker criterion was the sole R-2 gate, and the Phase 7 backstop only runs with the setting on | Fixed — Phase 3 criterion reframed as UX; Phase 7 gains an off-state refusal probe |
| 4 | Low | `_project_consent` emitted `''` for missing or non-string fields, contradicting its docstring | Fixed — both levels omit; empty `matchedRule` dropped; partial-rule and non-string tests added |
| 5 | Low | [Security] An unhashable JSON-RPC id raised `TypeError` at the pending-permission store, hanging the agent | Fixed — non-str/int (and bool) ids refused via `_refuse` before the store; parametrized test |
| 6 | Low | [Security] The in-page browser `Notification` in `acp.html` lost its clamp when the frame title became unclamped | Fixed as a routing — comment corrected; client-side clamp added as a Phase 3 exit criterion (template is Phase 3 scope) |
| 7 | Low | Five unbounded agent-authored consent strings reach the browser; nothing named the renderer as their bound | Fixed as a routing — Phase 3 criterion: `textContent` only, CSS overflow/wrap; confirmed no renderer reads `consent` yet |
| 8 | Low | [Senior] Call-site comment said all three guards live there; the third is inside `_project_consent` | Fixed — reworded |
| 9 | Low | [Security] Comments said asyncio "swallows" the callback exception; its default handler logs it at ERROR | Fixed — reworded in five places; one more in `tests/test_web.py:~3848` reported, not fixed |
| 10 | Low | [Senior] The P4 consent test asserted only `consent`, not that the frame's other fields survive | Fixed — also asserts `requestId`, `sessionId`, `toolCall`, `options` |
| 11 | Low | [Senior] Nine citations of three archived plans pointed at paths that no longer exist | Fixed — seven path citations repointed to `plans/done/`; four bare slug mentions left as non-paths |
| 12 | Low | [Senior] Stale import-count claims in `agent_profile.py`, `config.py`, and this plan's D-20 row | Fixed — comment-only corrections; D-20 row corrected by the orchestrator |

Cycle 2 skipped per user instruction ("1 qreview cycle per phase"). The fix pass's own suite run (2020
passed, 2 skipped) and the 8/8 live Step 5b probe serve as this cycle's verification.

### 2026-09-23 — Implementation Review (after Phase 3, persona: Security auditor, End-user advocate)

Implementation health: Green (all fixed; one by user decision). 18 findings merged across two
personas (1 High — raised independently by both — 8 Medium, 9 Low). Both ran in isolated worktrees
and mutation-verified the escaping, notification-clamp and not-in-effect tests.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | Both: a failed or non-OK permission-state read resolved Default to `kiro_default`, starting an ungated session that looked gated — always, from the phone | Fixed — user decision 2026-09-23: the server resolves Default through the gate; the client resolver is deleted (`4fc6923`) |
| 2 | Medium | [Security] After Phase 5 gates `/api/*`, a stale tab would hit finding 1 on every create | Fixed — same fix; creating a session no longer reads `/api/acp-permissions` |
| 3 | Medium | [End-user] A server-refused create on the dashboard left "Creating session…" with the composer hidden; only a 4 s toast explained | Fixed — the refusal replaces the placeholder in the transcript and restores the composer |
| 4 | Medium | [End-user] A failed base-agent change kept the last-good profile silently; also found by Step 5b QA | Fixed — warns "Still using the previous profile…" when `generation_ok` is false |
| 5 | Medium | [End-user] Not-in-effect was visible only inside the closed settings menu | Fixed — lights the settings-gear dot, shared with restart drift by OR |
| 6 | Medium | [End-user] The scope copy omitted sessions reopened later, which keep their creation-time agent (P2) | Fixed — reworded to cover reopened sessions |
| 7 | Medium | [End-user] What on and off mean lived only in a tooltip keyboard and touch users cannot reach | Fixed — visible one-line description, referenced by `aria-describedby` |
| 8 | Medium | [End-user] The new toggle was a `div` with no keyboard operation or switch semantics | Fixed — `role="switch"`, `tabindex`, `aria-checked`, Enter/Space; the two older toggles reported, not changed |
| 9 | Medium | [End-user] An unbounded title above the consent block could push Allow/Deny off screen | Fixed — the title gets a max-height with internal scroll; never truncated |
| 10 | Low | [Security] A second copy of the `poweratlas-acp` literal in `composer-chrome.js` reintroduced D-20's drift risk | Fixed — removed with the client resolver |
| 11 | Low | Both: a Default create waiting on a hung state read never happened and said nothing | Fixed — the wait is gone with server-side resolution |
| 12 | Low | [End-user] The refusal copy said "turn it on" even when the profile was already on but not in effect | Fixed — rewritten to point to Settings |
| 13 | Low | [End-user] Warnings showed raw errors with no next step | Fixed — every warning ends with a next step |
| 14 | Low | [End-user] A failed state read on load showed the toggle as off | Fixed — shown as "could not read" (`aria-checked="mixed"`) |
| 15 | Low | [End-user] Consent labels showed raw identifiers such as `fs_write` | Fixed — plain words with the raw id kept; `hasOwnProperty` lookup |
| 16 | Low | [End-user] The new error toasts lacked the dismiss button the others carry | Fixed — added |
| 17 | Low | [Security] A doc comment named `addPermissionConsent`, not `permissionConsentBlock` | Fixed — renamed |
| 18 | Low | [End-user] The dashboard refusal path was not asserted by any test | Fixed — two dashboard assertions added |

Not a reviewer finding, recorded for completeness: the Phase 4 fixer reported `agent_profile.py`'s
D-19 comment still described in-place secret writes; corrected in `4fc6923`.

Finding 1's resolution changed Phase 2 behaviour (a `None` hook now reads as not in effect, and
`kiro_default` is resolved through the gate) — recorded in § 9 Phase 3. Cycle 2 skipped per user
instruction ("1 qreview cycle per phase"); the fix pass's suite runs (693 page checks, 1657 + 440
pytest) and 13 confirmed-killed mutations serve as this cycle's verification.

### 2026-09-23 — Implementation Review (after Phase 4, persona: Security auditor, Reliability engineer)

Implementation health: Green (all fixed or user-accepted). 16 findings merged across two personas
(3 High, 6 Medium, 7 Low). The security audit ran 15 mutations (13 killed; the two survivors became
findings 7 and 13); the reliability review probed four failure shapes live in a scratch directory.
Both confirmed the exchange, cookie and rotate logic hold against a same-user HTTP caller.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | [Reliability] A rotate write failing after the truncating open emptied `local-secret`, reported no change, and signed every browser out at restart — reproduced | Fixed — secret writes atomic (tmp, full-write loop, fsync, `os.replace`) for local and remote (`3a4f3b5`) |
| 2 | High | [Reliability] The new `lifespan` secret load had no `except Exception`, contrary to R-13 | Fixed — guarded, with an in-memory D-22 fallback reported by `/api/settings`; read capped at 4096 bytes |
| 3 | High | [Security] A same-user process can read `local-secret` and forge `pa_local` indefinitely; no risk row covered it | User: accepted — 2026-09-23, recorded as R-19 with the honest boundary (not code running as the user) |
| 4 | Medium | [Security] `pa_local` on `127.0.0.1` reaches every local port; another listener could replay it for 90 days | User: accepted — 2026-09-23, folded into R-19, cookie lifetime kept at 90 days |
| 5 | Medium | [Reliability] `os.write`'s return value was ignored, so a short write was reported as persisted | Fixed — full-write loop; zero-progress write is a failure |
| 6 | Medium | [Reliability] Two concurrent rotations could leave the process and the file holding different secrets | Fixed — rotation writes and applies synchronously on the loop, like the remote route |
| 7 | Medium | [Security] A fallback to the remote key when the local key is empty survived every Phase 4 test | Fixed — test added; the mutation is now killed |
| 8 | Medium | Both: an existing but unreadable secret file was silently overwritten, revoking every cookie | Fixed — only a missing (or readable-but-unusable) file is replaced; unreadable is kept, logged, in-memory fallback |
| 9 | Medium | [Reliability] The peek webview holds a dead cookie after a rotation until restart | Fixed as a routing — added as a Phase 5 exit criterion (peek is Phase 5's door) |
| 10 | Medium | [Reliability] `tests/test_config.py`'s fixture did not redirect `LOCAL_SECRET_PATH` | Fixed — redirected |
| 11 | Low | [Reliability] The no-secret refusal logged an ERROR per request outside the rate limit | Fixed — routed through the rate-limited logger, ERROR kept |
| 12 | Low | [Reliability] Clearing codes on rotation can kill a code a door minted a moment earlier | Fixed — kept deliberately (the Security review counts it as a protection); race named in a comment |
| 13 | Low | [Security] The constant-time code comparison was claimed but not pinned; a dict-lookup mutation survived | Fixed — spy test requires one `compare_digest` per outstanding code |
| 14 | Low | Both: the suppressed-refusal count from a final burst was never logged | Fixed — flushed during `lifespan` teardown |
| 15 | Low | [Security] Two startup tests left `web._LOCAL_SECRET` set after they ran | Fixed — moved to the `local_enabled` fixture |
| 16 | Low | [Reliability] `agent_profile.py`'s comment still described in-place secret writes after the fix | Fixed — corrected in `4fc6923` (Phase 3's fix pass, whose scope included that file) |

Findings 3 and 4 record the user's answer to the escalation of 2026-09-23 ("Accept, keep 90 days").
The security auditor's "remote refactor unchanged" check was re-confirmed by the fix pass's
consumer list (every caller of the shared writer still returns its existing verdicts). Cycle 2
skipped per user instruction ("1 qreview cycle per phase"); the fix pass's suite runs (1646 + 440
pytest) and 10 confirmed-killed mutations serve as this cycle's verification.

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
- **Override recorded (2026-09-22, `/qdev` invocation)**: user instructed 1 `/qreview` cycle per phase
  (default: 2, cap-gated). No tier was declared in the header blockquote; `/qdev` inferred Major from
  the plan's own structure (Dependency Graph, Risk Assessment, 4-persona plan review) and applied
  `effort: full` at Step 9 accordingly — cost: none yet; recurring absence of an explicit tier would be
  worth a `/qplan` template default — suggested change: none required unless this recurs.
- Phase 1's implementation brief enumerated verification commands (pytest, name check) but not whether
  live kiro-cli probing was in scope, while the phase's own open design questions were empirical ones
  Phase 0 had already answered by probing — cost: 5 questions deferred to Phase 7 that a 5-minute probe
  run would have settled, and one design choice (exact-literal shell patterns over widened globs) made
  on the conservative branch rather than the measured one — suggested change: when a phase inherits open
  empirical questions from a predecessor's findings, the dispatch brief should state explicitly whether
  the predecessor's probe authority carries forward.
- Phase 1's own exit criteria (written before Phase 0's Gate resolution) referenced a deny floor that no
  longer exists; three replacement criteria were appended after the Gate resolution, but the phase
  body's own prose (the "rule assembly is deny floor + ..." line) needed a separate manual correction —
  cost: low here, since the orchestrator's Phase 1 dispatch brief pre-empted it inline ("if you see any
  reference to deny floor logic, that's stale"), but a sub-agent relying on the plan body alone would
  have been misled — suggested change: when a Gate resolution supersedes a design decision referenced by
  a not-yet-executed phase's own prose (not just its exit criteria), `/qdev` should re-read and correct
  the affected phase body at resolution time, not only append to its criteria.
- The Agent tool's `isolation: "worktree"` created every review worktree from `origin/main`, not from
  local `main`, so all four Phase 3/4 reviewers saw pre-Phase-3 code and (correctly) stopped; the
  harness then deleted each worktree when its agent stopped unchanged, so resuming them found no
  directory. Recreating worktrees by hand then failed on Windows' 260-character path limit
  (`_proto/` images, `plans/done/` names under the long scratchpad path) — cost: roughly 20 minutes
  and four wasted dispatches before any review began — suggested change: `/qdev`'s review dispatch
  should say "unpushed commits: build the worktree yourself from local HEAD" and name a sparse
  checkout (`src/ tests/ plans/*.md` plus governance files) as the default on Windows.
