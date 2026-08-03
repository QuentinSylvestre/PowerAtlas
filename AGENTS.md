# AGENTS.md

## Doc & Test Guidelines

- Update existing documentation files when implementing user-visible changes.
- Do not create new documentation files unless the user requests them.
- Update README.md only when changes affect installation, basic usage, or user-visible CLI/WebUI surface. **Exempt: a surface introduced by a plan whose Intent declares it a throwaway prototype**, for as long as it stays one — the README describes the product, and documenting a surface built to be deleted misleads the reader it exists for. The exemption ends the moment the surface is kept; promoting it to product is what makes the README row required work.
- Update existing tests when implementation changes. Do not introduce new test files unless the user requests them or a regression bug fix requires one.
- Page behaviour in `src/power_atlas/templates/` is covered by `tests/acp_page.test.mjs`, run with `node tests/acp_page.test.mjs`. It renders the Jinja template and drives the rendered script over a DOM stand-in; it is **not** part of the pytest suite and is not run by CI. Run it when changing a template's inline script — the Python suite cannot see those defects.
- A duplicate module-level or class-level definition in a test module is caught by `_check_test_names.py`, run as a **pre-commit hook** against the staged content. Python rebinds a repeated `def` silently, so a second fixture of the same name is not an error — it is simply the only one that exists, and every test written against the first now receives the second's value. The one time this happened it cost a full-suite run: **79 failures and 35 errors**, all of them in unrelated tests hundreds of lines from the duplicate, with nothing in the output naming it. A `conftest.py` would not help and the repo has none — the same rebinding rules apply there.
  - `.git/hooks/` is not version controlled, so **a fresh clone has no hook.** Reinstall with `cp _pre_commit_hook.sh .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit`.
  - Run it by hand any time with `.venv-PowerAtlas/Scripts/python _check_test_names.py` (~270 ms over the whole tree).
- When the user requests something that contradicts these guidelines, apply the request AND propose a durable update to this section so future sessions follow the new policy.
