#!/usr/bin/env python3
"""Fail when a staged change, or a commit message, holds a session id that exists in a local session store.

This repository is public. A real Codex, Claude Code or kiro-cli session id in a
plan, doc, test or commit stays in the history for good, and the Codex work
shipped one in a committed plan that only a review caught. A synthetic id
(`00000001-1111-...`) is fine. A real one is not.

Usage:
    python _check_public_ids.py                    # pre-commit: the staged change
    python _check_public_ids.py --message <file>   # commit-msg: a commit message file

"Real" means: the id names a file or folder under one of the local session
stores: `~/.codex/sessions`, `~/.codex/archived_sessions` and
`~/.codex/thread-writer-locks` (all under `$CODEX_HOME` when it is set),
`~/.claude/projects` and `~/.kiro/sessions`. An id is the hyphenated 8-4-4-4-12
hex form in any case, matched against that exact set with no word-boundary rule,
so `sess_<uuid>` and an id glued to other letters or hex still match.

What it scans, by default (pre-commit, on the staged change):

- the **added** lines of the staged diff, so ids already in history do not block
  an unrelated commit;
- the **paths** of added and renamed files (an id in a file name). A deleted file
  is not a violation;
- staged files that git treats as binary but that start with a UTF-16 or UTF-32
  byte-order mark (UTF-16: FF FE or FE FF; UTF-32: FF FE 00 00 or 00 00 FE FF; FF FE
  00 00 is also tried as UTF-16LE text that starts with a NUL): they are decoded and
  their lines scanned (for a modified file, only the lines absent from the committed
  version). One `git cat-file --batch` process reads every such blob by object id;
  only the first 4 bytes are kept to test the mark, and the whole blob is kept
  only when a mark matched. A blob over 64 MiB is skipped with a warning.

`--message <file>` scans a commit message file instead (the `commit-msg` hook,
`_commit_msg_hook.sh`), comment lines included, because the hook cannot know the
cleanup mode. Two things are not scanned. Everything from git's own scissors line
on (`# ---... >8 ---...` immediately followed by `# Do not modify or remove the
line above.`) is ignored: git cuts the message there, and under `commit -v` the
diff below it is already scanned as the staged change. A scissors line typed in a
message without that second line is not a cut (with `-m` or `-F` git keeps the
text below it), so the scan goes on. And a status-template line `#<TAB>deleted:`
or `#<TAB>renamed:` is skipped, because an editor-mode template lists the paths
of a deleted or renamed-away file, which pre-commit allows. A hand-typed
`#<TAB>deleted: <id>` line therefore passes. The same template also lists untracked
and unstaged files as `#<TAB><path>`: one named with a store id blocks an editor-mode
commit until it is renamed or removed (a false positive).

Limits, stated so nobody trusts it for more than it does:

- an id whose session was already deleted from the store, and an id copied from
  another machine; an id written without hyphens, or any shape other than the
  hyphenated uuid;
- stores that are not walked: Claude Code `todos`, `file-history`, `session-env`,
  `shell-snapshots` and `debug`; Codex `shell_snapshots`, `log` and
  `thread_history_*.sqlite`, and an id that exists only in the Codex state database
  (`state_*.sqlite`); the Kiro IDE `workspace-sessions` folder; PowerAtlas's own
  config folder. The store walk is not atomic with the commit;
- other binary files (images, archives); UTF-16 and UTF-32 without a byte-order
  mark; and text files that contain a NUL byte or that .gitattributes marks binary
  or `-diff` (git treats them as binary, and without a UTF mark they are not
  decoded);
- a merge: `git merge --no-commit` (or a conflicted merge) stages the merged
  branch's content, so every line that history added counts as added and an id
  already in it blocks the merge commit;
- `git commit --no-verify`, which skips both hooks; flows that skip them anyway
  (a clean cherry-pick, a rebase that does not reword, a revert, a clean merge);
  tag and branch names and annotated-tag messages, which no hook here sees; and
  `git commit-tree`;
- a linked worktree runs the checker revision it has checked out: an older one,
  or none (the hook then fails closed with a message);
- home paths, user names and workspace folder names, which are not ids;
- a `**Source**` line of `memory/MEMORY.md` is skipped whole, not only its id.
  That is the one sanctioned place: the memory format records `session-id +
  line-span` anchors there (`shared/skills/qdream/memory-rules.md`, Memory File
  Format);
- a blob that cannot be read (or is too large) is skipped with a warning and does
  not change the exit code; and when no store folder could be read, or a walk hit
  an error, one warning says ids could not be checked against the stores, and the
  exit code stays 0.

The report names the file and line and never prints an id: a path that holds an
id is printed with the id replaced by `<id>`, because a hook's output ends up in
transcripts. The stores are walked only when id-shaped text was found, so the
common commit stays fast.

Exit 0 clean, 1 when an id was found, 2 when git or the message file could not be
read or the check itself failed (git missing or timed out, an unexpected error;
only the error class is printed).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
from pathlib import Path

_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
_MEMORY_FILE = "memory/MEMORY.md"
_ANCHOR_MARK = "**Source**"
# git's own scissors text, as written by `commit -v` (wt-status.c): the cut line,
# then an explanation whose first line follows it immediately.
_SCISSORS = "# ------------------------ >8 ------------------------"
_SCISSORS_NEXT = "# Do not modify or remove the line above."
_TEMPLATE_PATH_RE = re.compile(r"#\t(deleted|renamed):")
_UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")
_UTF32_BOMS = (b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")
_DIFF_TIMEOUT = 60  # seconds, per git diff call
_BLOB_TIMEOUT = 120  # seconds, for reading every binary blob
_WIDE_CAP = 64 * 1024 * 1024  # bytes: a larger wide blob is not decoded
# Options that keep a user's git configuration from changing what the diff shows:
# an external diff or textconv prints other text, and diff.mnemonicPrefix or
# diff.noprefix changes the `+++ b/path` header the Source exemption reads.
_DIFF_FLAGS = ["--cached", "--diff-filter=ACMRT", "-M", "--no-color", "--no-ext-diff",
               "--no-textconv", "--src-prefix=a/", "--dst-prefix=b/"]

Candidate = tuple[str, int, str]  # (path, line number or 0 for the path itself, text)


class _GitError(Exception):
    pass


def _redact(text: str) -> str:
    return _UUID_RE.sub("<id>", text)


def _git_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k != "GIT_EXTERNAL_DIFF"}


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


def store_ids(roots: list[Path]) -> tuple[set[str], bool]:
    """(every id found in a file or folder name under the roots, lower-case; complete).

    `complete` is False when no root could be read or a walk hit an error: the
    ids may then be missing some that exist.
    """
    found: set[str] = set()
    readable, failed = 0, False

    def walk_error(_exc: OSError) -> None:
        nonlocal failed
        failed = True

    for root in roots:
        try:
            if not root.is_dir():
                continue
        except OSError:
            failed = True
            continue
        readable += 1
        for _dir, dirs, files in os.walk(root, onerror=walk_error):
            for name in dirs + files:
                found.update(m.lower() for m in _UUID_RE.findall(name))
    return found, readable > 0 and not failed


def _known_ids() -> set[str]:
    found, complete = store_ids(_store_roots())
    if not complete:
        print("_check_public_ids: warning: ids could not be checked against the local "
              "session stores (none found, or one was unreadable)", file=sys.stderr)
    return found


def _lines(text: str, wide: bool = False) -> list[str]:
    """Split text into lines the way each source needs.

    Git text (a diff, a commit message) is split on LF only. str.splitlines() also
    splits on CR, FF, U+2028 and U+0085, so an added line `progress<CR>id <id>`
    would lose its leading "+" on the second fragment and escape, and a message
    could fake a scissors line git does not see. Text decoded from UTF-16/32 is
    split with splitlines(): each fragment is scanned on its own and an id holds
    no separator, so a split cannot hide one.
    """
    return text.splitlines() if wide else text.split("\n")


def added_lines(diff: str) -> list[Candidate]:
    """(path, new line number, text) for each added line of a `-U0` diff."""
    out: list[Candidate] = []
    path, lineno, in_header = "", 0, False
    for line in _lines(diff):
        if line.startswith("diff --git "):
            in_header = True
        elif in_header and line.startswith("+++ "):
            # git puts a TAB after a path that holds a space
            path = line[4:].removeprefix("b/").rstrip("\t")
        elif line.startswith("@@"):
            in_header = False
            match = re.search(r"\+(\d+)", line)
            lineno = int(match.group(1)) if match else 0
        elif line.startswith("+"):
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
    try:
        run = subprocess.run(["git", "-c", "core.quotepath=off", *args], capture_output=True,
                             env=_git_env(), timeout=_DIFF_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _GitError(type(exc).__name__) from None
    if run.returncode != 0:
        first = run.stderr.decode("utf-8", errors="replace").strip().splitlines()[:1]
        # Redact first, then truncate: cutting at 200 characters first could leave
        # a prefix of an id that the pattern no longer recognises.
        raise _GitError(_redact(first[0])[:200] if first else f"exit {run.returncode}")
    return run.stdout


def _bom_codecs(head: bytes) -> tuple[str, ...]:
    """The codecs to try for a UTF-16 or UTF-32 byte-order mark at the start of `head`."""
    # FF FE 00 00 is the UTF-32LE mark, but it is also the UTF-16LE mark followed by
    # a NUL character, and git reports both as binary: try both decodings.
    if head.startswith(_UTF32_BOMS[0]):
        return ("utf-32", "utf-16")
    if head.startswith(_UTF32_BOMS[1]):
        return ("utf-32",)
    if head.startswith(_UTF16_BOMS):
        return ("utf-16",)
    return ()


def _decode_wide(blob: bytes) -> list[str] | None:
    """Lines of a blob that starts with a UTF-16/UTF-32 mark, else None."""
    codecs = _bom_codecs(blob[:4])
    if not codecs:
        return None
    out: list[str] = []
    for codec in codecs:
        out += _lines(blob.decode(codec, errors="replace"), wide=True)
    return out


def _read_one(proc: subprocess.Popen[bytes], oid: str) -> list[str] | str | None:
    """Ask `cat-file --batch` for one blob: decoded lines, None (no mark) or a reason.

    One id is written and its whole answer read before the next, so neither pipe
    can fill and stall the other. Raises EOFError or ValueError when the answer
    cannot be followed: the stream is then out of sync and every later blob is lost.
    """
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write(oid.encode("ascii") + b"\n")
    proc.stdin.flush()
    header = proc.stdout.readline().split()
    if len(header) == 2 and header[1] == b"missing":
        return "unreadable"
    if len(header) != 3 or header[0] != oid.encode("ascii") or header[1] != b"blob" \
            or not header[2].isdigit():
        raise ValueError("unparseable header")
    size = int(header[2])
    head = proc.stdout.read(min(size, 4))
    if len(head) < min(size, 4):
        raise EOFError
    codecs = _bom_codecs(head)
    result: list[str] | str | None = None
    if codecs and size <= _WIDE_CAP:
        body = head + proc.stdout.read(size - len(head))
        if len(body) < size:
            raise EOFError
        result = _decode_wide(body)
    else:
        left = size - len(head)
        while left > 0:  # keep the stream in step without holding the blob
            chunk = proc.stdout.read(min(left, 65536))
            if not chunk:
                raise EOFError
            left -= len(chunk)
        if codecs:
            result = "too large"
    if proc.stdout.read(1) != b"\n":
        raise EOFError
    return result


def _read_wide_blobs(oids: list[str]) -> tuple[dict[str, list[str]], dict[str, str]]:
    """(oid -> decoded lines for each blob with a wide mark, oid -> reason it was skipped).

    A blob with no mark is in neither. Reads by object id through one process, so
    no path is ever passed to git and the process count does not grow with the
    number of files.
    """
    lines: dict[str, list[str]] = {}
    skipped: dict[str, str] = {}
    if not oids:
        return lines, skipped
    try:
        proc = subprocess.Popen(["git", "cat-file", "--batch"], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                env=_git_env())
    except OSError:
        return lines, dict.fromkeys(oids, "unreadable")
    watchdog = threading.Timer(_BLOB_TIMEOUT, proc.kill)
    watchdog.start()
    try:
        for i, oid in enumerate(oids):
            try:
                outcome = _read_one(proc, oid)
            except (OSError, EOFError, ValueError):
                skipped.update(dict.fromkeys(oids[i:], "unreadable"))
                break
            if isinstance(outcome, str):
                skipped[oid] = outcome
            elif outcome is not None:
                lines[oid] = outcome
    finally:
        watchdog.cancel()
        try:
            proc.stdin.close()  # type: ignore[union-attr]
        except OSError:
            pass
        if proc.stdout is not None:
            proc.stdout.close()
        if proc.poll() is None:
            proc.kill()
        proc.wait()
    return lines, skipped


Change = tuple[str, str, str, str, str]  # (status letter, old oid, new oid, old path, new path)


def _raw_changes(raw: bytes) -> list[Change]:
    """Changes from `diff --raw -z --no-abbrev`: `:<modes> <old oid> <new oid> <status>` NUL paths."""
    tokens = raw.decode("utf-8", errors="replace").split("\0")
    out: list[Change] = []
    i = 0
    while i < len(tokens) and tokens[i].startswith(":"):
        meta = tokens[i].split()
        if len(meta) < 5:
            break
        status = meta[4][0]
        if status in "RC" and i + 2 < len(tokens):
            out.append((status, meta[2], meta[3], tokens[i + 1], tokens[i + 2]))
            i += 3
        elif i + 1 < len(tokens):
            out.append((status, meta[2], meta[3], tokens[i + 1], tokens[i + 1]))
            i += 2
        else:
            break
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
    diff = _git(["diff", "-U0", *_DIFF_FLAGS]).decode("utf-8", errors="replace")
    changes = _raw_changes(_git(["diff", "--raw", "-z", "--no-abbrev", *_DIFF_FLAGS]))
    candidates = [a for a in added_lines(diff) if _UUID_RE.search(a[2])]
    # The path of an added or renamed file; a modified file keeps its name.
    candidates += [(new, 0, new) for status, _a, _b, _old, new in changes
                   if status in "AR" and _UUID_RE.search(new)]
    binary = _binary_paths(_git(["diff", "--numstat", "-z", *_DIFF_FLAGS]))
    todo = [c for c in changes if c[4] in binary]
    oids = [c[2] for c in todo] + [c[1] for c in todo if c[0] in "MRT"]
    lines, skipped = _read_wide_blobs(list(dict.fromkeys(oids)))
    for status, old_oid, new_oid, _old, new in todo:
        if new_oid in skipped:
            what = "could not be read from the index" if skipped[new_oid] == "unreadable" \
                else "is too large to decode"
            print(f"_check_public_ids: {_redact(new)} {what}; it was not scanned", file=sys.stderr)
            continue
        if new_oid not in lines:
            continue
        # A modified file, or one renamed or retyped: only lines absent from the old blob count.
        before = set(lines.get(old_oid, ())) if status in "MRT" else set()
        candidates += [(new, n, text) for n, text in enumerate(lines[new_oid], 1)
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
    wide = _decode_wide(blob)
    # A UTF-16/32 message is not text git can cut or template, so it is scanned whole.
    lines = wide if wide is not None else _lines(blob.decode("utf-8", errors="replace"))
    kept: list[Candidate] = []
    for n, text in enumerate(lines, 1):
        if wide is None:
            if text == _SCISSORS and lines[n:n + 1] == [_SCISSORS_NEXT]:
                break
            if _TEMPLATE_PATH_RE.match(text):
                continue
        if _UUID_RE.search(text):
            kept.append(("commit message", n, text))
    if not kept:
        return 0
    hits = find_violations(kept, _known_ids())
    _report(hits, "has")
    return 1 if hits else 0


def _run(argv: list[str]) -> int:
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
    hits = find_violations(candidates, _known_ids())
    _report(hits, "adds")
    return 1 if hits else 0


def main(argv: list[str]) -> int:
    try:
        return _run(argv)
    except Exception as exc:  # noqa: BLE001 - a hook must end in a clear exit code
        # Only the class: a message or traceback could carry an id or a path.
        print(f"_check_public_ids: the check failed ({type(exc).__name__})", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
