# AGENTS.md

## Doc & Test Guidelines

- Update existing documentation files when implementing user-visible changes.
- Do not create new documentation files unless the user requests them.
- Update README.md only when changes affect installation, basic usage, or user-visible CLI/WebUI surface. **Exempt: a surface introduced by a plan whose Intent declares it a throwaway prototype**, for as long as it stays one — the README describes the product, and documenting a surface built to be deleted misleads the reader it exists for. The exemption ends the moment the surface is kept; promoting it to product is what makes the README row required work.
- Update existing tests when implementation changes. Do not introduce new test files unless the user requests them or a regression bug fix requires one.
- Page behaviour in `src/power_atlas/templates/` is covered by `tests/acp_page.test.mjs`, run with `node tests/acp_page.test.mjs`. It renders the Jinja template and drives the rendered script over a DOM stand-in; it is **not** part of the pytest suite and is not run by CI. Run it when changing a template's inline script — the Python suite cannot see those defects.
- When the user requests something that contradicts these guidelines, apply the request AND propose a durable update to this section so future sessions follow the new policy.
