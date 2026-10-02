#!/usr/bin/env python3
"""Fail when a staged change adds a session id that exists in a local session store.

This repository is public. A real Codex, Claude Code or kiro-cli session id in a
plan, doc, test or commit stays in the history for good, and the Codex work
shipped one in a committed plan that only a review caught. A synthetic id
(`00000001-1111-...`) is fine. A real one is not.

"Real" means: the id names a file or folder under one of the local session
stores (`~/.codex/sessions`, or `$CODEX_HOME/sessions`; `~/.claude/projects`;
`~/.kiro/sessions`). The check reads only the **added** lines of the staged
diff, so ids already in history do not block an unrelated commit.

Two things it cannot see, stated so nobody trusts it for more than it does: an id
whose session was deleted from the store before the commit, and an id copied from
another machine. It is a net for the common slip, not a guarantee.

One sanctioned place: a `**Source**` line of `memory/MEMORY.md`, where the memory
format records `session-id + line-span` anchors (`shared/skills/qdream/
memory-rules.md`, Memory File Format).

The report names the file and line and the pattern that matched. It never prints
the id, because a hook's output ends up in transcripts.

Exit 0 clean, 1 when an id was found, 2 when git could not be read.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

_UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE)
_MEMORY_FILE = "memory/MEMORY.md"
_ANCHOR_MARK = "**Source**"


def _store_roots() -> list[Path]:
    home = Path.home()
    codex = Path(os.environ.get("CODEX_HOME") or home / ".codex")
    return [codex / "sessions", home / ".claude" / "projects", home / ".kiro" / "sessions"]


def store_ids(roots: list[Path]) -> set[str]:
    """Every id found in a file or folder name under the roots (lower-case)."""
    found: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for _dir, dirs, files in os.walk(root):
            for name in dirs + files:
                found.update(m.lower() for m in _UUID_RE.findall(name))
    return found


def added_lines(diff: str) -> list[tuple[str, int, str]]:
    """(path, new line number, text) for each added line of a `-U0` diff."""
    out: list[tuple[str, int, str]] = []
    path, lineno = "", 0
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[4:].removeprefix("b/")
        elif line.startswith("@@"):
            match = re.search(r"\+(\d+)", line)
            lineno = int(match.group(1)) if match else 0
        elif line.startswith("+") and not line.startswith("+++"):
            out.append((path, lineno, line[1:]))
            lineno += 1
    return out


def find_violations(added: list[tuple[str, int, str]], known: set[str]) -> list[tuple[str, int]]:
    hits: list[tuple[str, int]] = []
    for path, lineno, text in added:
        if path == _MEMORY_FILE and _ANCHOR_MARK in text:
            continue
        if any(m.lower() in known for m in _UUID_RE.findall(text)):
            hits.append((path, lineno))
    return hits


def main() -> int:
    run = subprocess.run(
        ["git", "diff", "--cached", "-U0", "--diff-filter=ACMR", "--no-color"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if run.returncode != 0:
        print(f"_check_public_ids: git diff failed: {run.stderr.strip()[:200]}", file=sys.stderr)
        return 2
    added = [a for a in added_lines(run.stdout) if _UUID_RE.search(a[2])]
    if not added:
        return 0  # the common case: no id-shaped text, so the stores are never walked
    hits = find_violations(added, store_ids(_store_roots()))
    for path, lineno in hits:
        print(f"{path}:{lineno}: adds an id that exists in a local session store "
              f"(this repository is public)", file=sys.stderr)
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
