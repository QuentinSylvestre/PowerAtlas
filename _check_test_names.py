#!/usr/bin/env python3
"""Fail when a test module defines the same name twice.

Python rebinds rather than errors, so a second `def acp_store(...)` 8,500 lines
below the first is not a syntax error and not a warning -- it is silently the
only one that exists. For a pytest fixture that means every test written
against the *first* definition now receives the second one's value.

Measured cost of the incident this exists to prevent: one full-suite run
reporting **79 failures and 35 errors**, with every symptom in unrelated ACP
lifecycle tests hundreds of lines away from the duplicate. Nothing in the
output named the shadowed fixture, because from Python's point of view nothing
went wrong.

A `conftest.py` would not help and the repo has none: the same rebinding rules
apply there, so moving fixtures into one would relocate the hazard rather than
close it. The check has to be lexical, which is why it is a hook and not a
test -- by the time pytest is running, the shadowing has already happened and
the suite it would report on is the corrupted one.

Two shapes are reported, both the same underlying rebind:

  module-level   two `def`/`async def`/`class` statements sharing a name
  class-level    two methods in one class body sharing a name -- pytest
                 collects the survivor and the other test silently never runs

Deliberately *not* reported: `@typing.overload` stubs and
`@property`/`@x.setter` pairs, which are legitimate same-name definitions.

The two shapes need different rules, which a first cut got wrong. A setter
declares itself -- `@v.setter def v` carries the marker on the redefinition, so
looking at the redefinition is enough. An overload set does not: it is N
decorated stubs followed by the real implementation, and the implementation is
**undecorated**. Judging it by its own decorators reports the one definition
that actually runs. So a redefinition is intentional when it carries a
rebinding decorator, or when the definition it shadows was an `@overload` stub.

Usage:
    python _check_test_names.py [path ...]     # defaults to tests/

Exit 0 clean, 1 on a collision, 2 on a file that could not be parsed.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

# Decorator tails that make a same-name redefinition intentional. Matched on
# the attribute tail so `foo.setter`, `builtins.property` and a bare `overload`
# all resolve, without resolving imports.
_REBINDING_DECORATORS = frozenset({"setter", "getter", "deleter", "overload"})

_DEF_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _decorator_tail(node: ast.expr) -> str:
    """The last attribute of a decorator expression, or '' if it has none."""
    while isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _tails(node: ast.AST) -> set[str]:
    return {_decorator_tail(d) for d in getattr(node, "decorator_list", [])}


def _collisions(body: list[ast.stmt], scope: str) -> list[str]:
    """Names defined more than once directly in `body`."""
    # name -> (line of the first definition, whether the most recent definition
    # was an @overload stub). The second half is what lets the undecorated
    # implementation closing an overload set pass.
    seen: dict[str, tuple[int, bool]] = {}
    out: list[str] = []
    for node in body:
        if not isinstance(node, _DEF_NODES):
            continue
        name = node.name
        tails = _tails(node)
        is_overload = "overload" in tails
        if name not in seen:
            seen[name] = (node.lineno, is_overload)
            continue
        first_line, prev_was_overload = seen[name]
        # Keep the *first* line, so a triplicate reports both later copies
        # against the original rather than chaining off the middle one.
        seen[name] = (first_line, is_overload)
        if tails & _REBINDING_DECORATORS or prev_was_overload:
            continue
        out.append(
            f"{scope}: {name!r} redefined at line {node.lineno}, "
            f"first defined at line {first_line}"
        )
    return out


def check_file(path: Path) -> list[str]:
    """Every duplicate definition in one file, module level and class level."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    found = _collisions(tree.body, "module level")
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            found += _collisions(node.body, f"class {node.name}")
    return found


def main(argv: list[str]) -> int:
    targets = [Path(a) for a in argv[1:]] or [Path("tests")]
    files: list[Path] = []
    for target in targets:
        if target.is_dir():
            files.extend(sorted(target.rglob("*.py")))
        elif target.suffix == ".py":
            files.append(target)
    # Nothing to check is not success: a hook that silently passes when its
    # arguments match no files is a hook that has stopped running.
    if not files:
        print(f"_check_test_names: no Python files under {targets}", file=sys.stderr)
        return 2

    failures = 0
    for path in files:
        try:
            found = check_file(path)
        except SyntaxError as exc:
            print(f"{path}: could not parse: {exc}", file=sys.stderr)
            return 2
        for line in found:
            print(f"{path}: {line}", file=sys.stderr)
            failures += 1

    if failures:
        print(
            f"\n{failures} duplicate definition(s). Python rebinds silently, so "
            f"the earlier one does not exist at run time -- a shadowed fixture "
            f"hands its value to every test written against the other.",
            file=sys.stderr,
        )
        return 1
    print(f"_check_test_names: {len(files)} file(s) clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
