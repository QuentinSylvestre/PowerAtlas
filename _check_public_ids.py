#!/usr/bin/env python3
"""Fail when a staged change, or a commit message, holds a session id that exists in a local session store.

This repository is public. A real Codex, Claude Code or kiro-cli session id in a
plan, doc, test or commit stays in the history for good, and the Codex work
shipped one in a committed plan that only a review caught. A synthetic id
(`00000001-1111-...`) is fine. A real one is not.

"Real" means: the id names a file or folder under one of the local session
stores: `~/.codex/sessions`, `~/.codex/archived_sessions` and
`~/.codex/thread-writer-locks` (all under `$CODEX_HOME` when it is set),
`~/.claude/projects` and `~/.kiro/sessions`. An id is read from a name with
lookarounds rather than a word boundary, so a kiro-cli `sess_<uuid>` folder
yields the bare uuid and a `sess_<uuid>` written in a document is matched.

What it scans, by default (pre-commit, on the staged change):

- the **added** lines of the staged diff, so ids already in history do not block
  an unrelated commit;
- the **paths** of added and renamed files (an id in a file name). A deleted file
  is not a violation;
- staged files that git treats as binary but that start with a UTF-16 or UTF-32
  byte-order mark (UTF-16: FF FE or FE FF; UTF-32: FF FE 00 00 or 00 00 FE FF):
  they are decoded and their lines scanned (for a modified file, only the lines
  absent from the committed version). Only the first 4 bytes of each binary blob
  are read to test the mark; the whole blob is read only when a mark matched.

`--message <file>` scans a commit message file instead (the `commit-msg` hook,
`_commit_msg_hook.sh`). Everything before git's scissors line is scanned,
comment lines included, because the hook cannot know the cleanup mode.

Limits, stated so nobody trusts it for more than it does:

- an id whose session was already deleted from the store, and an id copied from
  another machine;
- an id that exists only in the Codex state database (`state_*.sqlite`), which is
  not a store this check walks;
- other binary files (images, archives); UTF-16 and UTF-32 files without a
  byte-order mark; and text files that contain a NUL byte or that .gitattributes
  marks binary or `-diff` (git treats them as binary, and without a UTF byte-order
  mark they are not decoded);
- an id directly followed by a hex letter (`<id>end`, `<id>bak`): the pattern must
  not match inside a longer hex run, so such an id is missed;
- `git commit --no-verify`, which skips both hooks; tag and branch names and
  annotated-tag messages, which no hook here sees; and `git commit-tree`;
- home paths and user names, which are not ids;
- a `**Source**` line of `memory/MEMORY.md` is skipped whole, not only its id.
  That is the one sanctioned place: the memory format records `session-id +
  line-span` anchors there (`shared/skills/qdream/memory-rules.md`, Memory File
  Format).

The report names the file and line and never prints an id: a path that holds an
id is printed with the id replaced by `<id>`, because a hook's output ends up in
transcripts. The stores are walked only when id-shaped text was found, so the
common commit stays fast.

Exit 0 clean, 1 when an id was found, 2 when git or the message file could not be read.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

_UUID_RE = re.compile(
    r"(?<![0-9a-f])[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?![0-9a-f])",
    re.IGNORECASE,
)
_MEMORY_FILE = "memory/MEMORY.md"
_ANCHOR_MARK = "**Source**"
_SCISSORS = "# ------------------------ >8 ------------------------"
_UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")
_UTF32_BOMS = (b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")

Candidate = tuple[str, int, str]  # (path, line number or 0 for the path itself, text)


class _GitError(Exception):
    pass


def _redact(text: str) -> str:
    return _UUID_RE.sub("<id>", text)


def _store_roots() -> list[Path]:
    home = Path.home()
    codex = Path(os.environ.get("CODEX_HOME") or home / ".codex")
    return [
        codex / "sessions",
        codex / "archived_sessions",
        codex / "thread-writer-locks",
        home / ".claude" / "projects",
        home / ".kiro" / "sessions",
    ]


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


def added_lines(diff: str) -> list[Candidate]:
    """(path, new line number, text) for each added line of a `-U0` diff."""
    out: list[Candidate] = []
    path, lineno, in_header = "", 0, False
    # Split on LF only. str.splitlines() also splits on CR, FF, U+2028, U+0085 and
    # others, so an added line `progress<CR>id <id>` would lose the leading "+" on
    # its second fragment and escape. Git ends every diff line with LF; a trailing
    # CR of a CRLF file stays in the text, which is harmless.
    for line in diff.split("\n"):
        if line.startswith("diff --git "):
            in_header = True
        elif in_header and line.startswith("+++ "):
            path = line[4:].removeprefix("b/").rstrip("\t")
        elif line.startswith("@@"):
            in_header = False
            match = re.search(r"\+(\d+)", line)
            lineno = int(match.group(1)) if match else 0
        elif not in_header and line.startswith("+"):
            out.append((path, lineno, line[1:]))
            lineno += 1
    return out


def find_violations(candidates: list[Candidate], known: set[str]) -> list[tuple[str, int]]:
    hits: list[tuple[str, int]] = []
    for path, lineno, text in candidates:
        if path == _MEMORY_FILE and _ANCHOR_MARK in text:
            continue
        if any(m.lower() in known for m in _UUID_RE.findall(text)):
            hits.append((path, lineno))
    return hits


def _git(args: list[str]) -> bytes:
    run = subprocess.run(["git", "-c", "core.quotepath=off", *args], capture_output=True)
    if run.returncode != 0:
        first = run.stderr.decode("utf-8", errors="replace").strip().splitlines()[:1]
        # Redact first, then truncate: cutting at 200 characters first could leave
        # a prefix of an id that the pattern no longer recognises.
        raise _GitError(_redact(first[0])[:200] if first else f"exit {run.returncode}")
    return run.stdout


def _bom_codec(head: bytes) -> str | None:
    """The codec for a UTF-16 or UTF-32 byte-order mark at the start of `head`."""
    # UTF-32 first: the UTF-32LE mark (FF FE 00 00) starts with the UTF-16LE mark.
    if head.startswith(_UTF32_BOMS):
        return "utf-32"
    if head.startswith(_UTF16_BOMS):
        return "utf-16"
    return None


def _decode_wide(blob: bytes) -> list[str] | None:
    """Lines of a blob that starts with a UTF-16/UTF-32 mark, else None."""
    codec = _bom_codec(blob[:4])
    if codec is None:
        return None
    # splitlines() is safe here, unlike in added_lines: every fragment is scanned
    # on its own, and an id contains no line separator, so a split cannot hide one.
    return blob.decode(codec, errors="replace").splitlines()


def _blob_lines(spec: str) -> list[str] | None:
    """Lines of the blob `spec` (e.g. `:path`) when it starts with a wide mark.

    Reads through `git cat-file blob` (no textconv, no filters) and takes only 4
    bytes unless a mark matched. None means no mark. Raises _GitError when the
    blob cannot be read.
    """
    try:
        proc = subprocess.Popen(["git", "-c", "core.quotepath=off", "cat-file", "blob", spec],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise _GitError(type(exc).__name__) from None
    assert proc.stdout is not None
    try:
        head = proc.stdout.read(4)
        codec = _bom_codec(head)
        blob = head + proc.stdout.read() if codec else b""
    except OSError as exc:
        proc.kill()
        proc.wait()
        raise _GitError(type(exc).__name__) from None
    finally:
        proc.stdout.close()  # without a mark, git sees a closed pipe and stops
    proc.wait()
    # A closed pipe makes git exit non-zero after it wrote something; a real
    # failure (missing blob) wrote nothing.
    if proc.returncode != 0 and not head:
        raise _GitError(f"exit {proc.returncode}")
    if codec is None:
        return None
    if proc.returncode != 0:
        raise _GitError(f"exit {proc.returncode}")
    return _decode_wide(blob)


def _name_status(raw: bytes) -> list[tuple[str, str, str]]:
    """(status letter, old path, new path) from `--name-status -z`."""
    tokens = raw.decode("utf-8", errors="replace").split("\0")
    out: list[tuple[str, str, str]] = []
    i = 0
    while i < len(tokens) and tokens[i]:
        status = tokens[i][0]
        if status in "RC" and i + 2 < len(tokens):
            out.append((status, tokens[i + 1], tokens[i + 2]))
            i += 3
        else:
            out.append((status, tokens[i + 1], tokens[i + 1]))
            i += 2
    return out


def _binary_paths(raw: bytes) -> set[str]:
    """New paths git reports as binary, from `--numstat -z` (`-\\t-\\t<path>`)."""
    tokens = raw.decode("utf-8", errors="replace").split("\0")
    out: set[str] = set()
    i = 0
    while i < len(tokens) and tokens[i]:
        head = tokens[i]
        parts = head.split("\t", 2)
        if len(parts) == 3 and parts[2] == "":  # rename: the two paths follow
            new = tokens[i + 2] if i + 2 < len(tokens) else ""
            i += 3
        else:
            new = parts[2] if len(parts) == 3 else ""
            i += 1
        if parts[0] == "-" and parts[1] == "-" and new:
            out.add(new)
    return out


def staged_candidates() -> list[Candidate]:
    """Everything the staged change adds that could hold an id."""
    flags = ["--cached", "--diff-filter=ACMR", "-M", "--no-color"]
    diff = _git(["diff", "-U0", *flags]).decode("utf-8", errors="replace")
    changes = _name_status(_git(["diff", "--name-status", "-z", *flags]))
    candidates = [a for a in added_lines(diff) if _UUID_RE.search(a[2])]
    # The path of an added or renamed file; a modified file keeps its name.
    candidates += [(new, 0, new) for status, _old, new in changes
                   if status in "AR" and _UUID_RE.search(new)]
    binary = _binary_paths(_git(["diff", "--numstat", "-z", *flags]))
    for status, old, new in changes:
        if new not in binary:
            continue
        try:
            lines = _blob_lines(f":{new}")
        except _GitError:
            # Names are decoded with errors="replace", so a non-UTF-8 name may not
            # round-trip to the real path; either way the file is not scanned.
            print(f"_check_public_ids: could not read {_redact(new)} from the index; "
                  f"it was not scanned", file=sys.stderr)
            continue
        if lines is None:
            continue
        before: set[str] = set()
        if status in "MR":
            try:
                before = set(_blob_lines(f"HEAD:{old}") or [])
            except _GitError:
                pass  # no HEAD yet, or no old blob: every line counts as added
        candidates += [(new, n, text) for n, text in enumerate(lines, 1)
                       if text not in before and _UUID_RE.search(text)]
    return candidates


def _report(hits: list[tuple[str, int]], what: str) -> None:
    for path, lineno in hits:
        where = f"{_redact(path)}:{lineno}" if lineno else f"{_redact(path)}: file name"
        print(f"{where}: {what} an id that exists in a local session store "
              f"(this repository is public)", file=sys.stderr)


def check_message(path: str) -> int:
    try:
        blob = Path(path).read_bytes()
    except OSError as exc:
        print(f"_check_public_ids: cannot read the commit message file ({type(exc).__name__})",
              file=sys.stderr)
        return 2
    lines = _decode_wide(blob)
    if lines is None:
        # Split on LF only, as git does: splitlines() would also split on FF,
        # U+2028 and U+0085, so a message could fake a scissors line git does not
        # see and stop the scan before its real text.
        lines = blob.decode("utf-8", errors="replace").split("\n")
    kept: list[Candidate] = []
    for n, text in enumerate(lines, 1):
        if text.startswith(_SCISSORS):
            break
        if _UUID_RE.search(text):
            kept.append(("commit message", n, text))
    if not kept:
        return 0
    hits = find_violations(kept, store_ids(_store_roots()))
    _report(hits, "has")
    return 1 if hits else 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["--message"]:
        if len(argv) != 2:
            print("usage: _check_public_ids.py [--message <file>]", file=sys.stderr)
            return 2
        return check_message(argv[1])
    try:
        candidates = staged_candidates()
    except _GitError as exc:
        print(f"_check_public_ids: git failed: {exc}", file=sys.stderr)
        return 2
    if not candidates:
        return 0  # the common case: no id-shaped text, so the stores are never walked
    hits = find_violations(candidates, store_ids(_store_roots()))
    _report(hits, "adds")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
