# AGENTS.md

## Doc & Test Guidelines

- **Never restart PowerAtlas autonomously.** Restarting kills every kiro-cli ACP process the supervisor is managing, terminating all active sessions. Always defer to the user — present the restart as needed and let them do it when ready.

- **ACP UI iteration does not require a restart.** Most JS for `/acp` is inline in `src/power_atlas/templates/acp.html`; the composer chrome — context indicator, workspace/session-id tap-to-copy widget, debug/transport log panel — lives in the shared static module `src/power_atlas/static/composer-chrome.js` instead (`plans/260921_DASHBOARD_ACP_FEATURE_PARITY.md` Phase 1), loaded by both `acp.html` and `index.html` via `<script src>` so the dashboard consumes the same code rather than a duplicate. **Its CSS is not inline** — `acp.html` opens with `{% extends "base.html" %}`, and `base.html` links `/static/style.css`, where most `.acp-*` rules live (this line used to claim the opposite, and sent at least one session looking for `.acp-tool-status` in the template; corrected 2026-08-14). **Exception**: a few features keep their CSS inline in `acp.html`'s own `<style>` block instead — confirmed for the compaction recap (`.acp-compaction-details`/`.acp-compaction-recap`), deliberately out of scope for dashboard parity (see `plans/260921_DASHBOARD_ACP_FEATURE_PARITY.md`'s Scope boundaries); check both locations before assuming a `.acp-*` rule lives in `style.css`. Note: the task-mode picker (`.acp-taskmode-*`) was moved from the inline block to `style.css` by `plans/260919_DASHBOARD_ACP_NEW_SESSION_PICKER.md` Phase 1, and the send-mode picker (`.acp-mode-*`, plus `.acp-queue-steer`/`.sr-only`) was moved the same way by `plans/260921_DASHBOARD_ACP_FEATURE_PARITY.md` Phase 3 — both are now shared between `acp.html` and `index.html`. `acp.html` is a Jinja2 template served with `Cache-Control: no-store` and `auto_reload=True`, and `style.css`/`composer-chrome.js` are served from the plain `/static` mount, so in all cases the server reads the current file from disk. After editing any of them, a **hard reload** (`Ctrl+Shift+R`) in the browser picks up the changes immediately — no PowerAtlas restart needed. A normal F5 may serve a stale copy from the browser's memory cache, and for `style.css`/`composer-chrome.js` that cache is the only thing between you and the new rules; hard reload bypasses it. Do not instruct the user to restart PowerAtlas for ACP UI-only changes. **Python changes (`acp.py`, `web.py`, `data.py`, etc.) do require a restart** — the running process loaded the compiled bytecode at startup and editing the source file on disk has no effect until the process restarts.

- Update existing documentation files when implementing user-visible changes.
- Do not create new documentation files unless the user requests them.
- Update README.md only when changes affect installation, basic usage, or user-visible CLI/WebUI surface. **Exempt: a surface introduced by a plan whose Intent declares it a throwaway prototype**, for as long as it stays one — the README describes the product, and documenting a surface built to be deleted misleads the reader it exists for. The exemption ends the moment the surface is kept; promoting it to product is what makes the README row required work.
- Update existing tests when implementation changes. Do not introduce new test files unless the user requests them or a regression bug fix requires one.
- Page behaviour in `src/power_atlas/templates/` is covered by `tests/acp_page.test.mjs`, run with `node tests/acp_page.test.mjs`. It renders the Jinja template and drives the rendered script over a DOM stand-in; it is **not** part of the pytest suite and is not run by CI. Run it when changing a template's inline script — the Python suite cannot see those defects.
- A duplicate module-level or class-level definition in a test module is caught by `_check_test_names.py`, run as a **pre-commit hook** against the staged content. Python rebinds a repeated `def` silently, so a second fixture of the same name is not an error — it is simply the only one that exists, and every test written against the first now receives the second's value. The one time this happened it cost a full-suite run: **79 failures and 35 errors**, all of them in unrelated tests hundreds of lines from the duplicate, with nothing in the output naming it. A `conftest.py` would not help and the repo has none — the same rebinding rules apply there.
  - `.git/hooks/` is not version controlled, so **a fresh clone has no hook.** Reinstall with `cp _pre_commit_hook.sh .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit`.
  - Run it by hand any time with `.venv-PowerAtlas/Scripts/python _check_test_names.py` (~270 ms over the whole tree).
- `src/power_atlas/static/prism.js` is **generated** — the syntax highlighter behind /acp's code blocks, built by `_build_prism.mjs` from the `prismjs` npm tarball. Never hand-edit it: change the language list in `_build_prism.mjs` and rebuild (`npm pack prismjs@<version> && tar -xzf prismjs-<version>.tgz && node _build_prism.mjs ./package`). The languages are concatenated in dependency order and a grammar added out of order throws at load. `tests/acp_page.test.mjs` runs the committed bundle for real, so a bad rebuild fails there rather than in a browser.
- `pytest-timeout` is a dev dependency (`.venv-PowerAtlas/Scripts/python -m pip install -e ".[dev]"` picks it up). Use `pytest tests/test_web.py --timeout=300` when running the full suite after a change to session-close/concurrency code — a test whose mocking strategy assumes a code path that no longer exists can hang on an `asyncio.Event` that nothing will ever set, rather than failing fast; the flag turns that into a loud stack dump at 300s instead of an indefinitely stuck run.
- When the user requests something that contradicts these guidelines, apply the request AND propose a durable update to this section so future sessions follow the new policy.

## Terminology

Project-specific terms. Each entry names the rejected synonyms too, so a future session does not
re-litigate a settled word. General programming vocabulary does not belong here.

- **login code** — the one-time credential PowerAtlas mints into the URL when it opens its own web UI
  (tray, peek double-tap, peek webview), exchanged exactly once for the loopback session cookie.
  **Not "nonce"**: `_acp_csp` in `web.py` already uses that word for the per-response CSP nonce, and
  the two appear within a few lines of each other. **Not "token"**: `_ACP_TOKEN` meant a different,
  per-launch mechanism that the login code replaces. Settled 2026-09-21,
  `plans/260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL.md`.
- **base agent** — the kiro-cli agent definition PowerAtlas *reads* in order to build the derived
  agent. User-configurable by name, defaults to `kiro_default`. It is never modified; interactive
  terminal kiro-cli sessions keep using it untouched, which is what keeps their permission posture
  independent of anything PowerAtlas does.
- **derived agent** — `~/.kiro/agents/poweratlas-acp.md`, which PowerAtlas *generates* from the base
  agent plus a permissions overlay. Never hand-edited and never committed: it is a build product,
  regenerated at startup and on settings change. Editing it directly is always the wrong move — change
  the base agent or the overlay instead.
