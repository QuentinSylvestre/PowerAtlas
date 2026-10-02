#!/usr/bin/env python3
"""Scenarios for _check_public_ids.py, the pre-commit and commit-msg session-id check.

Each case builds a throwaway git repository and runs the checker against a
SYNTHETIC session store, either in process (the checker's own functions) or
through the real hook scripts and `git commit`.

Self-protecting: HOME, USERPROFILE and CODEX_HOME point into a temporary folder
before anything runs, and the script stops if the home folder does not resolve
there, so the real ~/.codex, ~/.claude and ~/.kiro are never read. The git
configuration is an empty file with the system file switched off. Every id is made
up with uuid4 at run time, and no output line ever holds one: a failed case reports
rc and flags, and the checker's own text is shown only with uuid-shaped strings
replaced.

Usage:
    .venv-PowerAtlas/Scripts/python _check_public_ids_scenarios.py [substring ...]

With substrings, only cases whose name contains one of them run. Set CHECKER to
the path of another copy of the checker to test that copy (a mutant). Exit 0 when
every case passes, 1 otherwise. It is not part of the pytest suite and CI does not
run it: run it after changing _check_public_ids.py or the two hook scripts.
"""
from __future__ import annotations

import atexit
import contextlib
import importlib.util
import io
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

REPO = Path(__file__).resolve().parent
CHECKER = Path(os.environ.get("CHECKER") or REPO / "_check_public_ids.py")
TMP = Path(tempfile.mkdtemp(prefix="pubids-")).resolve()


def _rmtree(path: Path) -> None:
    def unlock(func, p, _exc):  # git object files are read-only on Windows
        os.chmod(p, stat.S_IWRITE)
        func(p)
    shutil.rmtree(path, onerror=unlock)


atexit.register(lambda: _rmtree(TMP))

# ---- the sandbox: nothing below runs before the home folder is proven synthetic ----
HOME = TMP / "home"
HOME.mkdir()
(TMP / "gitconfig").write_text("", encoding="utf-8")
os.environ.update(
    HOME=str(HOME), USERPROFILE=str(HOME), CODEX_HOME=str(HOME / ".codex"),
    GIT_CONFIG_GLOBAL=str(TMP / "gitconfig"), GIT_CONFIG_NOSYSTEM="1",
    GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
    GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
for _name in ("GIT_CONFIG_SYSTEM", "GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE",
              "GIT_EXTERNAL_DIFF", "GIT_DIFF_OPTS", "GIT_EDITOR", "EDITOR", "VISUAL"):
    os.environ.pop(_name, None)
# The hook scripts call `python`: make that this interpreter.
os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]
if TMP not in Path.home().resolve().parents and TMP != Path.home().resolve():
    sys.exit("refusing to run: Path.home() is not inside the temporary folder")
(TMP / "repos").mkdir()

spec = importlib.util.spec_from_file_location("_checker_under_test", CHECKER)
CHK = importlib.util.module_from_spec(spec)
spec.loader.exec_module(CHK)

# ---- made-up ids ----
A, B, C, K, L = (str(uuid.uuid4()) for _ in range(5))  # codex, archived, lock, kiro, claude
FAKE = str(uuid.uuid4())  # in no store
M = str(uuid.uuid4())  # only in the alternate CODEX_HOME
U = str(uuid.uuid4())  # a store folder named in upper case
ALL_IDS = [A, B, C, K, L, FAKE, M, U]
SCISSORS = "# ------------------------ >8 ------------------------"
SCISSORS_NEXT = "# Do not modify or remove the line above."
UUID_SHAPE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def build_home(home: Path, extra: dict[str, str] | None = None) -> None:
    (home / ".codex/sessions/2026/10/02").mkdir(parents=True)
    (home / ".codex/sessions/2026/10/02" / f"rollout-2026-10-02T00-00-00-{A}.jsonl").write_text("{}")
    (home / ".codex/archived_sessions").mkdir(parents=True)
    (home / ".codex/archived_sessions" / f"rollout-x-{B}.jsonl").write_text("{}")
    (home / ".codex/thread-writer-locks").mkdir(parents=True)
    (home / ".codex/thread-writer-locks" / f"{C}.lock").write_text("")
    (home / ".kiro/sessions/h").mkdir(parents=True)
    (home / ".kiro/sessions/h" / f"sess_{K}").mkdir()
    (home / ".claude/projects/slug").mkdir(parents=True)
    (home / ".claude/projects/slug" / f"{L}.jsonl").write_text("{}")
    (home / ".claude/projects/slug" / f"{U.upper()}.jsonl").write_text("{}")


build_home(HOME)


# ---- helpers ----
def clean(text: str) -> str:
    """Output safe to show: uuid-shaped strings replaced, cut short."""
    return UUID_SHAPE.sub("<id>", text).replace("\n", " | ")[:200]


def leaks(out: str) -> list[str]:
    low = out.lower()
    return [i for i in ALL_IDS if i.lower() in low]


@contextlib.contextmanager
def chdir(path: Path) -> Iterator[None]:
    old = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


@contextlib.contextmanager
def patched(obj: object, **attrs: object) -> Iterator[None]:
    old = {k: getattr(obj, k) for k in attrs}
    for k, v in attrs.items():
        setattr(obj, k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            setattr(obj, k, v)


@contextlib.contextmanager
def env(**values: str) -> Iterator[None]:
    old = {k: os.environ.get(k) for k in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@contextlib.contextmanager
def count_popen() -> Iterator[list[list[str]]]:
    calls: list[list[str]] = []
    real = subprocess.Popen

    class Counting(real):  # type: ignore[valid-type, misc]
        def __init__(self, args, *a, **k):
            calls.append(list(args))
            super().__init__(args, *a, **k)

    subprocess.Popen = Counting
    try:
        yield calls
    finally:
        subprocess.Popen = real


def empty_home(name: str) -> dict[str, str]:
    home = TMP / name
    home.mkdir()
    return {"HOME": str(home), "USERPROFILE": str(home), "CODEX_HOME": str(home / ".codex")}


class Repo:
    """A throwaway repository with one commit; `hooks` is "", "pre", "msg" or "both"."""

    def __init__(self, hooks: str = "") -> None:
        self.path = Path(tempfile.mkdtemp(prefix="r", dir=TMP / "repos"))
        self.git("init", "-q", "-b", "main")
        self.write("base.txt", "base\n")
        self.stage("base.txt")
        self.git("commit", "-q", "-m", "base", "--no-verify")
        if hooks:
            shutil.copy(CHECKER, self.path / "_check_public_ids.py")
            if hooks in ("pre", "both"):
                shutil.copy(REPO / "_pre_commit_hook.sh", self.path / ".git/hooks/pre-commit")
            if hooks in ("msg", "both"):
                shutil.copy(REPO / "_commit_msg_hook.sh", self.path / ".git/hooks/commit-msg")

    def git(self, *args: str, input: bytes | None = None, check: bool = True) -> subprocess.CompletedProcess:
        run = subprocess.run(["git", *args], cwd=self.path, capture_output=True, input=input)
        if check and run.returncode != 0:
            raise RuntimeError(f"git {args[0]} failed (rc {run.returncode})")
        return run

    def write(self, rel: str, text: str | bytes, enc: str = "utf-8") -> None:
        p = self.path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text if isinstance(text, bytes) else text.encode(enc))

    def stage(self, *rels: str) -> None:
        self.git("add", "--", *rels)

    def commit_old(self) -> None:
        self.git("commit", "-q", "-m", "o", "--no-verify")

    def put_entry(self, mode: str, rel: str, content: bytes) -> None:
        """Stage `content` as `rel` with a given mode, without touching the work tree."""
        oid = self.git("hash-object", "-w", "--stdin", input=content).stdout.strip().decode()
        self.git("update-index", "--add", "--cacheinfo", f"{mode},{oid},{rel}")

    def raw(self) -> str:
        return self.git("diff", "--cached", "--raw", "--no-abbrev", "-M").stdout.decode()


def run_main(argv: list[str], cwd: Path) -> tuple[int, str]:
    buf = io.StringIO()
    with chdir(cwd), contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        rc = CHK.main(argv)
    return rc, buf.getvalue()


_msg_counter = 0


def run_message(data: bytes, cwd: Path = TMP) -> tuple[int, str]:
    global _msg_counter
    _msg_counter += 1
    f = TMP / f"msg{_msg_counter}.txt"
    f.write_bytes(data)
    return run_main(["--message", str(f)], cwd)


def script_path(name: str, body: str) -> str:
    p = TMP / name
    p.write_text(body, encoding="utf-8", newline="\n")
    return p.as_posix()


EDITOR = script_path("editor.sh", '#!/bin/sh\ncp "$1" "$TMPL_OUT"\n'
                     '{ printf \'subject\\n\'; cat "$1"; } > "$1.new" && mv "$1.new" "$1"\n')


def real_commit(r: Repo, *args: str, editor: bool = False) -> tuple[int, str, str]:
    """`git commit` through the installed hooks: (rc, hook-visible output, editor template)."""
    e = dict(os.environ)
    tmpl = TMP / "tmpl.txt"
    tmpl.unlink(missing_ok=True)
    if editor:
        e["GIT_EDITOR"] = f'sh "{EDITOR}"'
        e["TMPL_OUT"] = tmpl.as_posix()
    run = subprocess.run(["git", "commit", "-q", *args], cwd=r.path, capture_output=True, env=e)
    out = (run.stdout + run.stderr).decode("utf-8", "replace")
    if run.returncode == 0:
        # git's own summary may name a deleted file; only the hooks' lines count
        out = "\n".join(ln for ln in out.splitlines() if "_check_public_ids" in ln)
    shown = tmpl.read_text(encoding="utf-8", errors="replace") if tmpl.exists() else ""
    return run.returncode, out, shown


# ---- case registry ----
CASES: list[tuple[str, Callable[[], None]]] = []


def case(name: str):
    def register(fn: Callable[[], None]) -> Callable[[], None]:
        CASES.append((name, fn))
        return fn
    return register


def check(cond: bool, what: str) -> None:
    if not cond:
        raise AssertionError(what)


WANT = {"block": 1, "pass": 0}


def staged(name: str, setup: Callable[[Repo], None], want: str, quiet: bool = True) -> None:
    def run() -> None:
        r = Repo()
        setup(r)
        rc, out = run_main([], r.path)
        check(rc == WANT[want], f"rc={rc}, wanted {want}: {clean(out)}")
        check(not leaks(out), "an id appeared in the output")
        if want == "pass" and quiet:
            check(out == "", f"a passing run printed: {clean(out)}")
    CASES.append((name, run))


def message(name: str, data: str | bytes, want: str) -> None:
    def run() -> None:
        rc, out = run_message(data if isinstance(data, bytes) else data.encode("utf-8"))
        check(rc == WANT[want], f"rc={rc}, wanted {want}: {clean(out)}")
        check(not leaks(out), "an id appeared in the output")
    CASES.append((name, run))


# ---- staged-change cases (in process) ----
def w(rel: str, text: str | bytes, enc: str = "utf-8", also: tuple[str, ...] = ()):
    """A setup that writes one file and stages it."""
    def setup(r: Repo) -> None:
        r.write(rel, text, enc)
        r.stage(rel, *also)
    return setup


def many(r: Repo) -> None:
    r.write("a.md", f"{A} {L} {K} sess_{K} {A.upper()}\n")
    r.write(f"docs/{B}.md", f"{C}\n")
    r.write("u16.txt", f"{A} {L}\n", "utf-16")
    r.write("u32.txt", "﻿" + f"{C} {B}\n", "utf-32-le")
    r.stage("a.md", f"docs/{B}.md", "u16.txt", "u32.txt")


def removed_only(r: Repo) -> None:
    r.write("r.md", f"id {A}\n")
    r.stage("r.md")
    r.commit_old()
    r.write("r.md", "clean\n")
    r.stage("r.md")


def rename_id_name(r: Repo) -> None:
    r.write("old.md", "line one\nline two\nline three\n")
    r.stage("old.md")
    r.commit_old()
    r.git("mv", "old.md", f"new-{A}.md")


def delete_id_name(r: Repo) -> None:
    r.write(f"del-{A}.md", "x\n")
    r.stage(f"del-{A}.md")
    r.commit_old()
    r.git("rm", "-q", "--", f"del-{A}.md")


def rename_away_id_name(r: Repo) -> None:
    r.write(f"gone-{A}.md", "line one\nline two\nline three\n")
    r.stage(f"gone-{A}.md")
    r.commit_old()
    r.git("mv", f"gone-{A}.md", "kept.md")


def utf16_mod_history(r: Repo) -> None:
    r.write("u.txt", f"id {A}\nold\n", "utf-16")
    r.stage("u.txt")
    r.commit_old()
    r.write("u.txt", f"id {A}\nnew\n", "utf-16")
    r.stage("u.txt")


def utf32_mod_history(r: Repo) -> None:
    r.write("u.txt", "﻿" + f"id {A}\nold\n", "utf-32-le")
    r.stage("u.txt")
    r.commit_old()
    r.write("u.txt", "﻿" + f"id {A}\nnew\n", "utf-32-le")
    r.stage("u.txt")


def utf16_rename_mod(r: Repo) -> None:
    r.write("u.txt", "keep\n" * 5, "utf-16")
    r.stage("u.txt")
    r.commit_old()
    r.git("mv", "u.txt", "v.txt")
    r.write("v.txt", "keep\n" * 5 + f"id {A}\n", "utf-16")
    r.stage("v.txt")


def utf16_rename_id_in_history(r: Repo) -> None:
    body = "".join(f"line {n}\n" for n in range(30))
    r.write("u.txt", body + f"id {A}\n", "utf-16")
    r.stage("u.txt")
    r.commit_old()
    r.git("mv", "u.txt", "v.txt")
    r.write("v.txt", body + f"id {A}\nextra\n", "utf-16")
    r.stage("v.txt")
    check(re.search(r" R\d+\t", r.raw()) is not None, "precondition: git did not see a rename")


def utf16_deleted(r: Repo) -> None:
    r.write("u.txt", f"id {A}\n", "utf-16")
    r.stage("u.txt")
    r.commit_old()
    r.git("rm", "-q", "--", "u.txt")


def utf16_ffff_nul(text: str) -> bytes:
    """FF FE 00 00 then UTF-16LE text: git calls it binary, and the mark also reads as UTF-32LE."""
    return b"\xff\xfe\x00\x00" + text.encode("utf-16-le")


def bom00_mod_history(r: Repo) -> None:
    r.write("u.txt", utf16_ffff_nul(f"id {A}\nold\n"))
    r.stage("u.txt")
    r.commit_old()
    r.write("u.txt", utf16_ffff_nul(f"id {A}\nnew\n"))
    r.stage("u.txt")


STAGED = [
    ("added line", w("a.md", f"id {A}\n"), "block"),
    ("synthetic id passes", w("a.md", f"id {FAKE}\n"), "pass"),
    ("**Source** line in memory/MEMORY.md passes", w("memory/MEMORY.md", f"- **Source**: {A} 1-2\n"), "pass"),
    ("id only in a removed line passes", removed_only, "pass"),
    ("sess_<uuid>", w("a.md", f"sess_{K}\n"), "block"),
    ("archived-session id", w("a.md", f"{B}\n"), "block"),
    ("lock-directory id", w("a.md", f"{C}\n"), "block"),
    ("id in an added file name", w(f"docs/{A}.md", "x\n"), "block"),
    ("id in a UTF-16LE file with a mark", w("u.txt", f"id {A}\n", "utf-16"), "block"),
    ("rename to an id-named file", rename_id_name, "block"),
    ("deleting an id-named file passes", delete_id_name, "pass"),
    ("renaming away an id-named file passes", rename_away_id_name, "pass"),
    ("UTF-16 file with a made-up id passes", w("u.txt", f"id {FAKE}\n", "utf-16"), "pass"),
    ("UTF-16BE file with a mark", w("u.txt", "﻿" + f"id {A}\n", "utf-16-be"), "block"),
    ("modified UTF-16 file, id only in history, passes", utf16_mod_history, "pass"),
    ("added line starting with '++ '", w("a.md", f"++ {A}\n"), "block"),
    ("Claude Code projects id", w("a.md", f"{L}\n"), "block"),
    ("upper-case id in the text", w("a.md", f"{A.upper()}\n"), "block"),
    ("file name with a space", w(f"my file {A}.md", "x\n"), "block"),
    ("lone CR before the id", w("a.md", f"progress\rid {A}\n"), "block"),
    ("form feed before the id", w("a.md", f"x\x0cid {A}\n"), "block"),
    ("U+2028 before the id", w("a.md", f"x id {A}\n"), "block"),
    ("U+0085 before the id", w("a.md", f"x\u0085id {A}\n"), "block"),
    ("UTF-32LE file with a mark", w("u.txt", "﻿" + f"id {A}\n", "utf-32-le"), "block"),
    ("UTF-32BE file with a mark", w("u.txt", "﻿" + f"id {A}\n", "utf-32-be"), "block"),
    ("modified UTF-32 file, id only in history, passes", utf32_mod_history, "pass"),
    ("pinned limit: UTF-16 without a mark passes", w("u.txt", f"id {A}\n", "utf-16-le"), "pass"),
    ("id followed by -x and _y", w("a.md", f"{A}-x {A}_y\n"), "block"),
    ("id as a folder name", w(f"{A}/f.md", "x\n"), "block"),
    ("file name starting with dashes, id in content", w("--output=x.md", f"{A}\n"), "block"),
    ("UTF-16 file in an odd path", w("a b/é c'd.txt", f"id {A}\n", "utf-16"), "block"),
    ("UTF-16 rename plus edit", utf16_rename_mod, "block"),
    ("CRLF text", w("a.md", f"x\r\nid {A}\r\n"), "block"),
    ("pinned limit: NUL byte in UTF-8 text passes", w("a.md", f"x\0\nid {A}\n"), "pass"),
    ("BOM-only 2-byte file", w("e.txt", b"\xff\xfe"), "pass"),
    ("big random binary without a mark passes", w("big.bin", b"\x89" + os.urandom(3_000_000)), "pass"),
    ("several ids in content, names and UTF-16/32 files", many, "block"),
    ("id directly followed by a hex digit", w("a.md", f"{A}2\n"), "block"),
    ("id directly after hex letters", w("a.md", f"dead{A}\n"), "block"),
    ("id directly followed by letters", w("a.md", f"{A}end\n"), "block"),
    ("id with hex letters in a path-like text", w("a.md", f"docs/{A}bak.txt\n"), "block"),
    ("FF FE 00 00 then UTF-16LE text", w("u.txt", utf16_ffff_nul(f"id {A}\n")), "block"),
    ("FF FE 00 00 UTF-16LE file, id only in history, passes", bom00_mod_history, "pass"),
    ("FF FE 00 00 then UTF-32LE text", w("u.txt", b"\xff\xfe\x00\x00" + f"id {A}\n".encode("utf-32-le")), "block"),
    ("UTF-16 file deleted, id only in history, passes", utf16_deleted, "pass"),
    ("UTF-16 rename, id only in history, passes", utf16_rename_id_in_history, "pass"),
    ("**Source** line outside memory/MEMORY.md", w("docs/a.md", f"- **Source**: {A} 1-2\n"), "block"),
    ("plain id line in memory/MEMORY.md", w("memory/MEMORY.md", f"note {A}\n"), "block"),
    ("id line next to a **Source** line in memory/MEMORY.md",
     w("memory/MEMORY.md", f"- **Source**: {A} 1-2\nnote {A}\n"), "block"),
    ("memory/MEMORY.md in a subfolder gets no exemption", w("sub/memory/MEMORY.md", f"- **Source**: {A}\n"), "block"),
]
for _name, _setup, _want in STAGED:
    staged(_name, _setup, _want)


# ---- commit-message cases (in process, on a message file) ----
MESSAGES = [
    ("message with an id", f"fix {A}\n", "block"),
    ("clean message", "clean\n", "pass"),
    ("CR before the id", f"subject\n\nx\rid {A}\n", "block"),
    ("fake scissors through U+2028 hides nothing", f"subject\n\nx {SCISSORS}\n{SCISSORS_NEXT}\nid {A}\n", "block"),
    ("id above git's scissors block", f"subject\n\nbody {A}\n\n{SCISSORS}\n{SCISSORS_NEXT}\ndiff text\n", "block"),
    ("id only below git's scissors block passes", f"subject\n\n{SCISSORS}\n{SCISSORS_NEXT}\n# Everything below it will be ignored.\n-id {A}\n", "pass"),
    ("typed scissors line alone does not cut", f"subject\n\n{SCISSORS}\nid {A}\n", "block"),
    ("scissors then a blank line then git's text does not cut", f"subject\n\n{SCISSORS}\n\n{SCISSORS_NEXT}\nid {A}\n", "block"),
    ("scissors with trailing text is not git's line", f"subject\n\n{SCISSORS} x\n{SCISSORS_NEXT}\nid {A}\n", "block"),
    ("template line #<TAB>deleted: passes", f"subject\n\n# Changes to be committed:\n#\tdeleted:    docs/{A}.md\n", "pass"),
    ("template line #<TAB>renamed: passes", f"subject\n\n#\trenamed:    docs/{A}.md -> kept.md\n", "pass"),
    ("pinned limit: a hand-typed #<TAB>deleted: <id> passes", f"subject\n\n#\tdeleted: {A}\n", "pass"),
    ("#<TAB>modified: line is scanned", f"subject\n\n#\tmodified:   docs/{A}.md\n", "block"),
    ("'# deleted:' with a space is scanned", f"subject\n\n# deleted: {A}\n", "block"),
    ("pinned limit: an untracked id-named file in the template blocks", f"subject\n\n# Untracked files:\n#\tdocs/{A}.md\n", "block"),
    ("other # lines are scanned", f"subject\n\n# note {A}\n", "block"),
    ("UTF-16 message is scanned whole", ("﻿" + f"subject\n{SCISSORS}\n{SCISSORS_NEXT}\nid {A}\n").encode("utf-16-le"), "block"),
    ("clean UTF-16 message", ("﻿" + "subject\n").encode("utf-16-le"), "pass"),
    ("message with a synthetic id", f"fix {FAKE}\n", "pass"),
]
for _name, _data, _want in MESSAGES:
    message(_name, _data, _want)


# ---- the checker's own failure modes ----
@case("no store folder: one warning, exit 0 (staged change)")
def _() -> None:
    r = Repo()
    w("a.md", f"id {FAKE}\n")(r)
    with env(**empty_home("nostore1")):
        rc, out = run_main([], r.path)
    check(rc == 0, f"rc={rc}")
    check(len(out.strip().splitlines()) == 1 and "could not be checked" in out, f"output: {clean(out)}")
    check(not leaks(out), "an id appeared in the output")


@case("no store folder: one warning, exit 0 (message)")
def _() -> None:
    with env(**empty_home("nostore2")):
        rc, out = run_message(f"fix {FAKE}\n".encode())
    check(rc == 0 and len(out.strip().splitlines()) == 1 and "could not be checked" in out, f"rc={rc}: {clean(out)}")


@case("a root that raises PermissionError: warning, no traceback")
def _() -> None:
    def deny(self, *a, **k):
        raise PermissionError("denied")
    r = Repo()
    w("a.md", f"id {A}\n")(r)
    with patched(Path, is_dir=deny):
        rc, out = run_main([], r.path)
    check(rc == 0, f"rc={rc}: {clean(out)}")
    check("Traceback" not in out and "could not be checked" in out, f"output: {clean(out)}")


@case("a walk error: warning, ids found elsewhere still block")
def _() -> None:
    real_walk = os.walk

    def walk(top, topdown=True, onerror=None, followlinks=False):
        if onerror is not None and str(top).endswith("projects"):
            onerror(OSError("denied"))
            return iter(())
        return real_walk(top, topdown, onerror, followlinks)

    r = Repo()
    w("a.md", f"id {A}\n")(r)
    with patched(os, walk=walk):
        rc, out = run_main([], r.path)
    check(rc == 1, f"rc={rc}: {clean(out)}")
    check(out.count("could not be checked") == 1, f"output: {clean(out)}")


@case("the synthetic stores are read in full, with no walk error")
def _() -> None:
    found, complete = CHK.store_ids(CHK._store_roots())
    check(complete and {A, B, C, K, L, U} <= found, "the synthetic stores were not read in full")


@case("CODEX_HOME is honoured when it differs from HOME/.codex")
def _() -> None:
    alt = TMP / "codexalt"
    (alt / "sessions").mkdir(parents=True)
    (alt / "sessions" / f"rollout-{M}.jsonl").write_text("{}")
    r = Repo()
    w("a.md", f"id {M}\n")(r)
    rc, _out = run_main([], r.path)
    check(rc == 0, "precondition: the id is in the default home")
    with env(CODEX_HOME=str(alt)):
        rc, out = run_main([], r.path)
    check(rc == 1 and not leaks(out), f"rc={rc}")


@case("store folder names in upper case match a lower-case id")
def _() -> None:
    r = Repo()
    w("a.md", f"id {U.lower()}\n")(r)
    rc, out = run_main([], r.path)
    check(rc == 1 and not leaks(out), f"rc={rc}")


@case("git missing from PATH: exit 2 and a message")
def _() -> None:
    r = Repo()
    nogit = TMP / "nogit"
    nogit.mkdir()
    e = dict(os.environ, PATH=str(nogit))
    run = subprocess.run([sys.executable, str(CHECKER)], cwd=r.path, capture_output=True, env=e)
    out = (run.stdout + run.stderr).decode("utf-8", "replace")
    check(run.returncode == 2, f"rc={run.returncode}: {clean(out)}")
    check("git failed" in out and "Traceback" not in out, f"output: {clean(out)}")


@case("--message with a missing file: exit 2")
def _() -> None:
    rc, out = run_main(["--message", str(TMP / "does-not-exist.txt")], TMP)
    check(rc == 2 and "Traceback" not in out, f"rc={rc}: {clean(out)}")


@case("--message with no file: exit 2")
def _() -> None:
    rc, _out = run_main(["--message"], TMP)
    check(rc == 2, f"rc={rc}")


@case("run outside a repository: exit 2")
def _() -> None:
    outside = TMP / "norepo"
    outside.mkdir()
    rc, out = run_main([], outside)
    check(rc == 2 and "git failed" in out, f"rc={rc}: {clean(out)}")


@case("a diff-call timeout: exit 2")
def _() -> None:
    r = Repo()
    w("a.md", f"id {A}\n")(r)
    with patched(CHK, _DIFF_TIMEOUT=0.0001):
        rc, out = run_main([], r.path)
    check(rc == 2 and "TimeoutExpired" in out, f"rc={rc}: {clean(out)}")


@case("an unexpected exception: exit 2 and only the class name")
def _() -> None:
    def boom():
        raise KeyError(A)
    r = Repo()
    with patched(CHK, staged_candidates=boom):
        rc, out = run_main([], r.path)
    check(rc == 2 and "KeyError" in out and not leaks(out), f"rc={rc}: {clean(out)}")


@case("a git error line is redacted before it is cut at 200 characters")
def _() -> None:
    r = Repo()
    marker = "zzmarker"
    with chdir(r.path):
        probe = r.git("show", marker, check=False).stderr.decode("utf-8", "replace").splitlines()[0]
        prefix = probe.index(marker)
        pad = "x" * (192 - prefix)  # the id then starts at character 192 of the line
        try:
            CHK._git(["show", pad + A])
            check(False, "git did not fail")
        except CHK._GitError as exc:
            text = str(exc)
    check(len(text) >= 195, f"precondition: the line is too short to straddle ({len(text)})")
    check(A[:6] not in text and A[:6].upper() not in text, "a prefix of the id leaked")


@case("_redact replaces ids next to hex characters")
def _() -> None:
    check(CHK._redact(f"docs/{A}bak.txt") == "docs/<id>bak.txt", "id before letters")
    check(CHK._redact(f"dead{A}") == "dead<id>", "id after hex letters")
    check(CHK._redact(A.upper()) == "<id>", "upper case")


@case("an unreadable blob: one redacted warning, exit code unchanged")
def _() -> None:
    r = Repo()
    w(f"docs/{FAKE}.txt", f"id {A}\n", "utf-16")(r)
    with patched(CHK, _read_wide_blobs=lambda oids: ({}, dict.fromkeys(oids, "unreadable"))):
        rc, out = run_main([], r.path)
    check(rc == 0 and len(out.strip().splitlines()) == 1, f"rc={rc}: {clean(out)}")
    check("<id>" in out and "not scanned" in out and not leaks(out), f"output: {clean(out)}")


@case("a blob over the cap is skipped with a warning")
def _() -> None:
    r = Repo()
    w("u.txt", "".join(f"filler line {n} {FAKE}\n" for n in range(40)) + f"id {A}\n", "utf-16")(r)
    with patched(CHK, _WIDE_CAP=100):
        rc, out = run_main([], r.path)
    check(rc == 0 and "too large" in out and "Traceback" not in out and not leaks(out), f"rc={rc}: {clean(out)}")


@case("a blob read that exceeds the time limit is skipped with a warning")
def _() -> None:
    r = Repo()
    w("u.txt", f"id {A}\n", "utf-16")(r)
    with patched(CHK, _BLOB_TIMEOUT=0):
        rc, out = run_main([], r.path)
    check(rc == 0 and "not scanned" in out and "Traceback" not in out, f"rc={rc}: {clean(out)}")


# ---- git configuration must not change what is checked ----
def noop_script(name: str) -> str:
    return script_path(name, "#!/bin/sh\nexit 0\n")


@case("diff.external printing nothing does not hide an id")
def _() -> None:
    r = Repo()
    w("a.md", f"id {A}\n")(r)
    r.git("config", "diff.external", f"sh {noop_script('noop.sh')}")
    plain = r.git("diff", "--cached", "-U0").stdout.decode()
    check(A not in plain, "precondition: the external diff did not hide the id")
    rc, out = run_main([], r.path)
    check(rc == 1 and not leaks(out), f"rc={rc}")


@case("GIT_EXTERNAL_DIFF in the environment does not hide an id")
def _() -> None:
    r = Repo()
    w("a.md", f"id {A}\n")(r)
    with env(GIT_EXTERNAL_DIFF=f"sh {noop_script('noop2.sh')}"):
        check(A not in r.git("diff", "--cached", "-U0").stdout.decode(), "precondition: the id was not hidden")
        rc, out = run_main([], r.path)
    check(rc == 1 and not leaks(out), f"rc={rc}")


@case("a textconv driver does not hide an id")
def _() -> None:
    r = Repo()
    r.write(".gitattributes", "*.md diff=hide\n")
    w("a.md", f"id {A}\n")(r)
    converter = script_path("tc.sh", "#!/bin/sh\necho converted\n")
    r.git("config", "diff.hide.textconv", f"sh {converter}")
    check(A not in r.git("diff", "--cached", "-U0").stdout.decode(), "precondition: textconv did not hide the id")
    rc, out = run_main([], r.path)
    check(rc == 1 and not leaks(out), f"rc={rc}")


@case("diff.mnemonicPrefix: a **Source** line in memory/MEMORY.md still passes")
def _() -> None:
    r = Repo()
    w("memory/MEMORY.md", f"- **Source**: {A} 1-2\n")(r)
    r.git("config", "diff.mnemonicPrefix", "true")
    check("+++ i/memory/MEMORY.md" in r.git("diff", "--cached", "-U0").stdout.decode(), "precondition: no mnemonic prefix")
    rc, out = run_main([], r.path)
    check(rc == 0 and out == "", f"rc={rc}: {clean(out)}")


@case("diff.noprefix: a **Source** line in memory/MEMORY.md still passes")
def _() -> None:
    r = Repo()
    w("memory/MEMORY.md", f"- **Source**: {A} 1-2\n")(r)
    r.git("config", "diff.noprefix", "true")
    check("+++ memory/MEMORY.md" in r.git("diff", "--cached", "-U0").stdout.decode(), "precondition: prefix still there")
    rc, out = run_main([], r.path)
    check(rc == 0 and out == "", f"rc={rc}: {clean(out)}")


@case("type change from a symlink to a regular file holding an id")
def _() -> None:
    r = Repo()
    r.put_entry("120000", "l", b"target")
    r.commit_old()
    r.put_entry("100644", "l", f"id {A}\n".encode())
    check(re.search(r" T\tl", r.raw()) is not None, "precondition: not a type change")
    rc, out = run_main([], r.path)
    check(rc == 1 and not leaks(out), f"rc={rc}: {clean(out)}")


@case("type change from a regular file to a symlink whose target holds an id")
def _() -> None:
    r = Repo()
    r.put_entry("100644", "l", b"plain\n")
    r.commit_old()
    r.put_entry("120000", "l", f"../{A}".encode())
    check(re.search(r" T\tl", r.raw()) is not None, "precondition: not a type change")
    rc, out = run_main([], r.path)
    check(rc == 1 and not leaks(out), f"rc={rc}: {clean(out)}")


# ---- binary-blob reading ----
@case("binary files: the process count does not grow with their number")
def _() -> None:
    counts = {}
    for n in (2, 60):
        r = Repo()
        names = []
        for i in range(n):
            r.write(f"bin/f{i}.bin", b"\x89BIN\0" + os.urandom(64))
            names.append(f"bin/f{i}.bin")
        r.write("u.txt", f"id {A}\n", "utf-16")
        r.stage(*names, "u.txt")
        started = time.monotonic()
        with count_popen() as calls:
            rc, out = run_main([], r.path)
        counts[n] = len(calls)
        check(rc == 1 and not leaks(out), f"rc={rc} with {n} binaries")
        check(time.monotonic() - started < 60, f"too slow with {n} binaries")
        batch = [c for c in calls if "cat-file" in c]
        check(batch == [["git", "cat-file", "--batch"]], "cat-file was not one batch process without paths")
    check(counts[2] == counts[60] and counts[60] <= 6, f"spawn counts {counts}")


class _Recorder(io.BytesIO):
    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.sizes: list[int] = []

    def read(self, n: int | None = -1) -> bytes:
        self.sizes.append(-1 if n is None else n)
        return super().read(n)


class _FakeProc:
    def __init__(self, data: bytes) -> None:
        self.stdin = io.BytesIO()
        self.stdout = _Recorder(data)


def _frame(oid: str, body: bytes) -> bytes:
    return f"{oid} blob {len(body)}\n".encode() + body + b"\n"


@case("the batch reader keeps 4 bytes of an unmarked blob and discards the rest in chunks")
def _() -> None:
    oid = "a" * 40
    proc = _FakeProc(_frame(oid, b"\x89" + os.urandom(1_000_000)))
    check(CHK._read_one(proc, oid) is None, "an unmarked blob must give None")
    check(max(proc.stdout.sizes) <= 65536 and proc.stdout.sizes[0] == 4, f"read sizes {proc.stdout.sizes[:3]}")
    check(proc.stdin.getvalue() == oid.encode() + b"\n", "only the object id may be written")


@case("the batch reader: a mark gives the decoded lines, bad streams raise")
def _() -> None:
    oid = "b" * 40
    wide = ("hi\nthere\n").encode("utf-16")
    check(CHK._read_one(_FakeProc(_frame(oid, wide)), oid) == ["hi", "there"], "a UTF-16 blob must decode")
    check(CHK._read_one(_FakeProc(f"{oid} missing\n".encode()), oid) == "unreadable", "missing must be unreadable")
    for bad, exc in ((b"", ValueError), (b"garbage\n", ValueError), (_frame(oid, b"abcdef")[:-4], EOFError),
                     (f"{'c' * 40} blob 3\nabc\n".encode(), ValueError)):
        try:
            CHK._read_one(_FakeProc(bad), oid)
        except exc:
            continue
        check(False, f"{bad[:12]!r} must raise {exc.__name__}")
    with patched(CHK, _WIDE_CAP=4):
        check(CHK._read_one(_FakeProc(_frame(oid, wide)), oid) == "too large", "over the cap must say too large")


@case("one batch process stays in step across big, missing and wide blobs")
def _() -> None:
    r = Repo()
    with chdir(r.path):
        def blob(data: bytes) -> str:
            return r.git("hash-object", "-w", "--stdin", input=data).stdout.strip().decode()
        big, wide = blob(b"\x89" + os.urandom(200_000)), blob(f"id {A}\n".encode("utf-16"))
        missing, text = "0" * 40, blob(b"plain\n")
        lines, skipped = CHK._read_wide_blobs([big, missing, text, wide])
    check(skipped == {missing: "unreadable"}, f"skipped {skipped}")
    check(list(lines) == [wide] and lines[wide] == [f"id {A}"], "the wide blob after the others was not decoded")


# ---- the real hooks, through git commit ----
def _stub(r: Repo, code: int | None) -> None:
    target = r.path / "_check_public_ids.py"
    if code is None:
        target.unlink()
    else:
        target.write_text(f"import sys\nsys.exit({code})\n", encoding="utf-8")


def hook_text_case(name: str, hooks: str, code: int | None, want_rc: int, present: str, absent: str) -> None:
    def run() -> None:
        r = Repo(hooks)
        _stub(r, code)
        w("a.md", "clean\n")(r)
        rc, out, _t = real_commit(r, "-m", "work")
        check((rc != 0) == bool(want_rc), f"rc={rc}")
        check(present in out, f"missing text: {present!r}: {clean(out)}")
        check(absent not in out, f"unexpected text: {absent!r}")
    CASES.append((name, run))


hook_text_case("pre-commit: checker exit 1 says blocked", "pre", 1, 1, "pre-commit: blocked. Replace the id", "could not run")
hook_text_case("pre-commit: checker exit 2 says it could not run", "pre", 2, 1, "could not run (exit 2)", "Replace the id")
hook_text_case("pre-commit: checker exit 3 says it could not run", "pre", 3, 1, "could not run (exit 3)", "Replace the id")
hook_text_case("pre-commit: no checker file says it could not run", "pre", None, 1, "could not run (exit 2)", "Replace the id")
hook_text_case("pre-commit: checker exit 0 commits", "pre", 0, 0, "", "could not run")
hook_text_case("commit-msg: checker exit 1 says blocked", "msg", 1, 1, "commit-msg: blocked. Replace the id", "could not run")
hook_text_case("commit-msg: checker exit 2 says it could not run", "msg", 2, 1, "could not run (exit 2)", "Replace the id")
hook_text_case("commit-msg: checker exit 0 commits", "msg", 0, 0, "", "could not run")


def commit_blocked(r: Repo, *args: str, editor: bool = False) -> tuple[str, str]:
    rc, out, shown = real_commit(r, *args, editor=editor)
    check(rc != 0, "the commit was not blocked")
    check(not leaks(out), "an id appeared in the output")
    return out, shown


def commit_passes(r: Repo, *args: str, editor: bool = False) -> str:
    rc, out, shown = real_commit(r, *args, editor=editor)
    check(rc == 0, f"the commit was blocked: {clean(out)}")
    check(not leaks(out), "an id appeared in the output")
    return shown


@case("git commit -m: typed scissors line, id below, blocks")
def _() -> None:
    r = Repo("both")
    w("a.md", "clean\n")(r)
    out, _t = commit_blocked(r, "-m", f"subject\n\n{SCISSORS}\nid {A}\n")
    check("commit-msg: blocked" in out, f"not blocked by commit-msg: {clean(out)}")


def f_scissors_case(cleanup: str) -> None:
    def run() -> None:
        r = Repo("both")
        w("a.md", "clean\n")(r)
        (TMP / "m.txt").write_text(f"subject\n\n{SCISSORS}\nid {A}\n", encoding="utf-8", newline="\n")
        out, _t = commit_blocked(r, f"--cleanup={cleanup}", "-F", str(TMP / "m.txt"))
        check("commit-msg: blocked" in out, f"not blocked by commit-msg: {clean(out)}")
    CASES.append((f"git commit -F --cleanup={cleanup}: typed scissors line, id below, blocks", run))


for _cleanup in ("whitespace", "strip", "verbatim", "scissors"):
    f_scissors_case(_cleanup)


@case("git commit -m: a # line with an id blocks")
def _() -> None:
    r = Repo("both")
    w("a.md", "clean\n")(r)
    commit_blocked(r, "-m", f"subject\n\n# note {A}\n")


@case("git commit -v: id only below git's own scissors line passes")
def _() -> None:
    r = Repo("both")
    removed_only(r)
    shown = commit_passes(r, "-v", editor=True)
    check(SCISSORS in shown and SCISSORS_NEXT in shown, "precondition: no scissors block in the template")
    check(A in shown.split(SCISSORS)[1] and A not in shown.split(SCISSORS)[0], "precondition: the id is not only below")


@case("editor mode: deleting an id-named file passes")
def _() -> None:
    r = Repo("both")
    delete_id_name(r)
    shown = commit_passes(r, editor=True)
    check(re.search(r"#\tdeleted:.*" + re.escape(A), shown) is not None, "precondition: the template does not list the file")


@case("editor mode: renaming away an id-named file passes")
def _() -> None:
    r = Repo("both")
    rename_away_id_name(r)
    shown = commit_passes(r, editor=True)
    check(re.search(r"#\trenamed:.*" + re.escape(A), shown) is not None, "precondition: the template does not list the file")


@case("git commit -- <path>: a partial commit with an id blocks")
def _() -> None:
    r = Repo("both")
    r.write("p.md", "clean\n")
    r.stage("p.md")
    r.commit_old()
    r.write("p.md", f"id {A}\n")
    commit_blocked(r, "-m", "work", "--", "p.md")


@case("pinned limit: a merge commit blocks over an id already in the merged history")
def _() -> None:
    r = Repo("both")
    r.git("checkout", "-q", "-b", "side")
    w("s.md", f"id {A}\n")(r)
    r.commit_old()
    r.git("checkout", "-q", "main")
    w("m.md", "main\n")(r)
    r.commit_old()
    r.git("merge", "--no-commit", "--no-ff", "side")
    commit_blocked(r, "-m", "merge")


@case("real hooks: an id in a staged file blocks and the output holds no id")
def _() -> None:
    r = Repo("both")
    many(r)
    out, _t = commit_blocked(r, "-m", "work")
    check("pre-commit: blocked" in out, f"not blocked by pre-commit: {clean(out)}")


@case("real hooks: a clean commit passes")
def _() -> None:
    r = Repo("both")
    w("a.md", f"id {FAKE}\n")(r)
    commit_passes(r, "-m", "clean")


def main(argv: list[str]) -> int:
    chosen = [(n, f) for n, f in CASES if not argv or any(a in n for a in argv)]
    failed = 0
    for name, fn in chosen:
        try:
            fn()
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {name}: {clean(str(exc))}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {name}: {type(exc).__name__}: {clean(str(exc))}")
        else:
            print(f"ok   {name}")
    print(f"{len(chosen) - failed} of {len(chosen)} cases passed" if not failed
          else f"{failed} of {len(chosen)} cases FAILED")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
