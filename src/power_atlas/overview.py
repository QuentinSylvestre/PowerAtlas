"""Data for the dashboard's Overview panel.

260924_DASHBOARD_OVERVIEW_LIVE_TAILS_PLANS_USAGE. The Overview is what the
dashboard's right panel shows while no session is open. This module holds its
server-side readers; the routes in `web.py` call them from worker threads.

It never imports `web` (D10): `web` imports this module, so the reverse import
would be circular, and the dependency direction stays downward. Anything a
reader needs from `web` (the rail's filters, its liveness helpers) is decided by
the route and passed in.

Active plans (SC-6): `scan_plans` lists the `plans/*.md` files of the given
workspaces whose Status is `In Progress`, or `Complete` but not yet moved to
`plans/done/`, with progress read from the file's `## Progress Tracker`.
"""

from __future__ import annotations

import json
import logging
import os
import re
import stat as stat_mod
import subprocess
import threading
import time
from collections import OrderedDict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, NamedTuple

log = logging.getLogger(__name__)

# --- Active plans ------------------------------------------------------------

# A plan file larger than this is skipped unread (D21). The largest plan in the
# user's workspaces is well under 100 KiB; a megabyte file in `plans/` is not a
# plan this list can summarise.
PLAN_MAX_BYTES = 1024 * 1024
# An In Progress plan with no activity for longer than this is badged `stale`
# (D7). Activity is `_plan_activity`, not the file's mtime.
PLAN_STALE_SECONDS = 7 * 24 * 3600
# One bound per git call in `_plan_activity`; a plan costs at most two.
_GIT_TIMEOUT_SECONDS = 3.0
# A plan slug is the file stem. Anything else is not interpolated into a git
# pattern.
_SLUG_RE = re.compile(r"[A-Za-z0-9_.-]+")
# Variables that would point a `git -C <cwd>` call at some other repository.
_GIT_REDIRECT_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR")
# Files in `plans/` that are not plans. Compared case-insensitively.
_PLAN_SKIP_NAMES = frozenset({"ROADMAP.MD", "CLOSED_INVESTIGATIONS.MD"})
_PLAN_STATES = ("In Progress", "Complete")
_DETAIL_MAX = 140
_NOTES_MAX = 120

# The Status value is cut to this many characters before any further parsing.
# A real one is well under 200; the cap bounds the work on a pathological line.
_STATUS_MAX = 500

_STATUS_RE = re.compile(r"^> \*\*Status\*\*:\s*(.+)$", re.MULTILINE)
# The state ends at the first " — ", " – " or " - "; everything after is the
# detail.
_STATE_SPLIT_RE = re.compile(r" (?:—|–|-) ")
_TRACKER_HEAD_RE = re.compile(r"^## Progress Tracker\s*$")
_H2_RE = re.compile(r"^## ")
_PHASE_RE = re.compile(r"^### Phase (\d+)", re.MULTILINE)
_DIGITS_RE = re.compile(r"[0-9]+")
_DATE_PREFIX_RE = re.compile(r"^\d{6}_")

# Path-keyed memo of parsed plans (D22): path -> (mtime_ns, size, parsed).
# Keyed by path alone, so a file edited many times keeps one entry. The lock
# guards get/put only; two threads parsing the same file at once is tolerated.
_plan_memo: dict[str, tuple[int, int, dict | None]] = {}
_plan_memo_lock = threading.Lock()


def _read_plan_file(path: Path, st=None) -> str | None:
    """The file's text, or None when it is larger than `PLAN_MAX_BYTES`, or
    when it is no longer the file `st` describes.

    Invalid UTF-8 is replaced rather than raised (D21). The read itself is
    bounded, not only the `stat` before it: an agent appending to the file
    between the two cannot make this read more than the cap. `st`, the
    caller's `lstat`, is compared with an `fstat` of the open handle: a file
    swapped for another (a symlink or a junction included) between the two
    calls, or one that changed size, is not read; the next scan reads it.

    Module-level and called by name so a test can count the reads.
    """
    with path.open("rb") as fh:
        if st is not None:
            fst = os.fstat(fh.fileno())
            if (not stat_mod.S_ISREG(fst.st_mode) or fst.st_size != st.st_size
                    or (fst.st_ino and st.st_ino and fst.st_ino != st.st_ino)
                    or (fst.st_dev and st.st_dev and fst.st_dev != st.st_dev)):
                return None
        raw = fh.read(PLAN_MAX_BYTES + 1)
    if len(raw) > PLAN_MAX_BYTES:
        return None
    return raw.decode("utf-8", errors="replace")


def _strip_status_comment(status: str) -> str:
    """The Status value without a trailing `<!-- ... -->`.

    Done with string methods, not a regex: a `\\s*<!--.*?-->\\s*$` pattern
    backtracks quadratically over a long run of spaces, and the Status line is
    free text from a file on disk. 260924_DASHBOARD_OVERVIEW_LIVE_TAILS_PLANS_USAGE
    """
    s = status.rstrip()
    if s.endswith("-->"):
        start = s.rfind("<!--")
        if start >= 0:
            s = s[:start]
    return s.strip()


def _trim(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _tracker_status(raw: str) -> str:
    """Normalise a tracker Status cell to done | in_progress | pending | other."""
    s = raw.strip().strip("*_~`").strip().lower()
    if s.startswith(("done", "complete")):
        return "done"
    # "Implemented, review pending" and "Review pending" are phases whose code
    # exists and whose review has not run: under way, not waiting to start.
    if s.startswith(("in progress", "in-progress", "implemented", "review pending")):
        return "in_progress"
    if s.startswith(("pending", "not started")) or s == "":
        return "pending"
    return "other"


def _tracker_rows(lines: list[str]) -> list[dict]:
    """The numbered rows of the `## Progress Tracker` table.

    Rows run from that heading to the next `## ` heading. Only rows whose `#`
    cell is all digits are phase rows; header, separator and lettered rows
    (such as `U`) are left out.
    """
    rows: list[dict] = []
    inside = False
    for line in lines:
        if not inside:
            inside = bool(_TRACKER_HEAD_RE.match(line))
            continue
        if _H2_RE.match(line):
            break
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if not cells or not _DIGITS_RE.fullmatch(cells[0]):
            continue
        rows.append({
            "id": cells[0],
            "name": cells[1] if len(cells) > 1 else "",
            "status": _tracker_status(cells[2] if len(cells) > 2 else ""),
            "notes": _trim(cells[3], _NOTES_MAX) if len(cells) > 3 else "",
        })
    return rows


def parse_plan(text: str) -> dict | None:
    """The parts of a plan file the Overview shows, or None when it is not listed.

    Listed plans have a Status line whose state is `In Progress` or `Complete`.
    A file without a Status line, or in any other state (`Draft`,
    `Exploring`, ...), is not listed.
    """
    m = _STATUS_RE.search(text)
    if not m:
        return None
    status = _strip_status_comment(m.group(1)[:_STATUS_MAX])
    parts = _STATE_SPLIT_RE.split(status, maxsplit=1)
    state = parts[0].strip()
    if state not in _PLAN_STATES:
        return None
    detail = parts[1].strip() if len(parts) > 1 else ""
    tracker = _tracker_rows(text.splitlines())
    if tracker:
        # The phase to name under the bar. Trackers seldom mark a row In
        # Progress, so the first row not yet done stands in for it; `state`
        # tells the page which of the two it got, so it can word the label
        # truthfully ("in progress" against "next").
        current = next((r for r in tracker if r["status"] == "in_progress"), None)
        current_state = "in_progress"
        if current is None:
            current = next((r for r in tracker if r["status"] != "done"), None)
            current_state = "next"
        progress: dict | None = {
            "done": sum(1 for r in tracker if r["status"] == "done"),
            "total": len(tracker),
            "current": ({"id": current["id"], "name": current["name"],
                         "state": current_state}
                        if current else None),
        }
    else:
        phases = len(set(_PHASE_RE.findall(text)))
        progress = {"phases": phases} if phases else None
    return {
        "state": state,
        "detail": _trim(detail, _DETAIL_MAX),
        "progress": progress,
        "tracker": tracker,
    }


def _plan_title(file_name: str) -> str:
    stem = file_name[:-3] if file_name.lower().endswith(".md") else file_name
    return _DATE_PREFIX_RE.sub("", stem).replace("_", " ")


def _skip_cwd(cwd: str) -> bool:
    """A string check, before any filesystem call (D21): a UNC path can stall a
    `stat` for tens of seconds on a dead host, and a relative one would resolve
    against the server's own working directory. It runs first: `realpath`
    opens the path on Windows, so a UNC cwd must never reach it."""
    return (not cwd or cwd.startswith("\\\\") or cwd.startswith("//")
            or not os.path.isabs(cwd))


def _resolves_to_unc(real: str) -> bool:
    """True when `real`, a `os.path.realpath` result, names a network path.

    `realpath` can keep the `\\\\?\\` extended-length prefix on Windows: a
    `\\\\?\\C:\\...` path is local, `\\\\?\\UNC\\...` is a share.
    260924_DASHBOARD_OVERVIEW_LIVE_TAILS_PLANS_USAGE (D21)
    """
    if real.startswith("\\\\?\\"):
        return real[4:].upper().startswith("UNC\\")
    return real.startswith("\\\\") or real.startswith("//")


def _git_output(cwd: str, *args: str) -> str | None:
    """stdout of `git -C cwd <args>`, or None when git is missing, `cwd` is not
    a repository, the call times out or exits non-zero.

    Read-only: `GIT_OPTIONAL_LOCKS=0` keeps `status` from refreshing the index.
    No console window, so the tray app does not flash one.
    """
    env = {k: v for k, v in os.environ.items() if k not in _GIT_REDIRECT_ENV}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        res = subprocess.run(
            ["git", "-C", cwd, *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=_GIT_TIMEOUT_SECONDS, stdin=subprocess.DEVNULL, env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return res.stdout if res.returncode == 0 else None


def _plan_activity(cwd: str, path: Path, mtime: float) -> float:
    """When work on the plan last happened, as epoch seconds.

    The file's mtime is not that: a bulk rewrite of plan files (a history
    remap, a checkout) moves it without any work on the plan. So, in a git
    repository, it is the author date of the latest commit whose subject is
    scoped to the plan's slug, `type(<plan-slug>): ...` (the playbook's
    Conventional Commits scope for a planned project), which counts a code
    commit as well as a plan edit. The author date, not the committer date,
    because a rebase resets the latter.

    The file's own mtime stands when the plan has uncommitted changes (it is
    being edited now), and when anything is unavailable: not a repository, no
    such commit, a git failure, a slug that is not a plain file stem.
    """
    slug = path.stem
    if not _SLUG_RE.fullmatch(slug):
        return mtime
    stamp = _git_output(cwd, "log", "-1", "--format=%at", "--extended-regexp",
                        "--grep=^[a-z]+\\(" + slug + "\\)!?:")
    try:
        committed = int(stamp.strip()) if stamp and stamp.strip() else None
    except ValueError:
        committed = None
    if committed is None:
        return mtime
    dirty = _git_output(cwd, "status", "--porcelain", "--", str(path))
    if dirty is None or dirty.strip():
        return mtime
    return float(committed)


def scan_plans(workspaces, deadline_s: float = 2.0, now: float | None = None) -> list[dict]:
    """Active plans across `workspaces`, a list of `(cwd, name)` pairs.

    Blocking; runs in a worker thread. The caller has already applied the
    rail's filters (hidden tag, disabled providers) and de-duplicated the cwds.

    Robust reads (D21): cwds that are UNC or not absolute are skipped, and so
    is a cwd whose `plans` folder resolves (a junction or symlink) to a UNC
    target. Nothing more is read once `deadline_s` has passed; the deadline is
    checked before each cwd and before each file, and the first file of the
    first cwd is always read, as in `web._acp_exists_flags`. A single stalled
    call cannot be interrupted, so the bound is one stalled call, not zero.
    The `realpath` check itself is such a call: it opens the path, so a
    mapped or `subst` drive letter that points at a dead share passes the
    string check and stalls there, once per scan, for the OS timeout. No
    network workspace exists today; that residual risk was accepted by the
    user on 2026-09-28 rather than handled. Only top-level `plans/*.md` is read, which
    leaves `plans/done/` out; anything that is not a regular file (a symlink
    included) is skipped, and so is a file swapped between the `lstat` and
    the open (`_read_plan_file`); files over `PLAN_MAX_BYTES` are skipped, and
    the read itself is capped too; one unreadable file skips that file only.

    Parsed files are memoised per path on `(mtime_ns, size)` (D22), so a
    repeat scan of unchanged files costs one `stat` each. Paths not seen by a
    scan that ran to completion are evicted.

    Each plan's `activity` is `_plan_activity`, not its mtime; the `stale`
    badge and the sort are measured on it. It costs up to two `git` calls per
    listed plan on every scan, so a scan is as slow as git is for the plans it
    lists; the web layer reuses a scan for 30 s.

    `now` is the wall-clock time the `stale` badge is measured against;
    tests pass it to make that comparison deterministic.
    """
    now = time.time() if now is None else now
    deadline = time.monotonic() + deadline_s
    out: list[dict] = []
    seen: set[str] = set()
    complete = True
    for index, (cwd, name) in enumerate(workspaces):
        if not complete:
            break
        if index and time.monotonic() >= deadline:
            complete = False
            break
        if _skip_cwd(cwd):
            continue
        plans_dir = Path(cwd) / "plans"
        try:
            if _resolves_to_unc(os.path.realpath(str(plans_dir))):
                continue
            if not plans_dir.is_dir():
                continue
            files = sorted(plans_dir.glob("*.md"))
        except (OSError, ValueError):
            continue
        for file_index, path in enumerate(files):
            if (index or file_index) and time.monotonic() >= deadline:
                complete = False
                break
            if path.name.upper() in _PLAN_SKIP_NAMES:
                continue
            key = str(path)
            seen.add(key)
            try:
                # lstat: a symlink in `plans/` is not followed (it could point
                # anywhere, a share included); it fails S_ISREG below.
                st = path.lstat()
                if not stat_mod.S_ISREG(st.st_mode) or st.st_size > PLAN_MAX_BYTES:
                    continue
                with _plan_memo_lock:
                    hit = _plan_memo.get(key)
                if hit is not None and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
                    parsed = hit[2]
                else:
                    text = _read_plan_file(path, st)
                    if text is None:
                        continue
                    parsed = parse_plan(text)
                    with _plan_memo_lock:
                        _plan_memo[key] = (st.st_mtime_ns, st.st_size, parsed)
            except (OSError, UnicodeDecodeError, ValueError):
                # The path only: never the content.
                log.warning("Overview: could not read plan file %s", key)
                continue
            if parsed is None:
                continue
            mtime = st.st_mtime_ns / 1e9
            # Not memoised with the parse: a code commit scoped to the plan
            # is activity and does not touch the file. Past the deadline the
            # mtime stands, so git never extends the scan.
            activity = (_plan_activity(cwd, path, mtime)
                        if time.monotonic() < deadline else mtime)
            out.append({
                "cwd": cwd,
                "workspace": name,
                "file": path.name,
                "title": _plan_title(path.name),
                "state": parsed["state"],
                "detail": parsed["detail"],
                "activity": datetime.fromtimestamp(activity, tz=timezone.utc).isoformat(),
                "stale": (parsed["state"] == "In Progress"
                          and now - activity > PLAN_STALE_SECONDS),
                "ready_to_close": parsed["state"] == "Complete",
                "progress": parsed["progress"],
                "tracker": parsed["tracker"],
                "_activity": activity,
            })
    if complete:
        with _plan_memo_lock:
            for key in [k for k in _plan_memo if k not in seen]:
                del _plan_memo[key]
    out.sort(key=lambda p: p["_activity"], reverse=True)
    out.sort(key=lambda p: 0 if p["state"] == "In Progress" else 1)
    for plan in out:
        del plan["_activity"]
    return out


# --- Live now ------------------------------------------------------------------
#
# SC-4/SC-5: one tile per live session, with the rail's own liveness rule and
# filters (D8), and the last few events of its transcript (D13). The route in
# `web.py` polls this every ~2 s while the Overview is showing, from a worker
# thread. 260924_DASHBOARD_OVERVIEW_LIVE_TAILS_PLANS_USAGE

LIVE_MAX_TILES = 8
LIVE_FILTERS = ("all", "poweratlas")
_HELD_PROVIDER = "kiro-cli-v3"
_NEW_SESSION_TITLE = "New session"

# Tail reads (D13). The first window, the growth factor and the ceiling; a line
# longer than `TAIL_MAX_LINE_BYTES` is skipped unparsed (a pasted image or a
# huge tool result, never a tile's worth of text).
TAIL_FIRST_BYTES = 64 * 1024
TAIL_GROWTH = 8
TAIL_MAX_BYTES = 2 * 1024 * 1024
TAIL_MAX_LINE_BYTES = 256 * 1024
TAIL_EVENTS = 5
_EVENT_TEXT_MAX = 240
_TOOL_ARG_MAX = 80

# Path-keyed memo of tail events (D22): path -> (mtime_ns, size, events). An
# idle tile costs one stat. A least-recently-used bound of `_TAIL_MEMO_MAX`
# paths rather than the current poll's tiles: two tabs on different filters, or
# a filter toggled back and forth, would otherwise evict each other's entries
# every poll. 260924_DASHBOARD_OVERVIEW_LIVE_TAILS_PLANS_USAGE
_TAIL_MEMO_MAX = 4 * LIVE_MAX_TILES
_tail_memo: OrderedDict[str, tuple[int, int, list]] = OrderedDict()
_tail_memo_lock = threading.Lock()
# A transcript whose tail could not be read (a share-none hold by antivirus or a backup tool on
# a Codex rollout, say) is remembered per path as `(mtime_ns, size, tried_at, warned_at)`, in the
# same bounded least-recently-used form and under the same lock. The unchanged file is not read
# again for `_TAIL_RETRY_SECONDS` (a hold usually lets go without touching the file, so it is
# retried, but not on every 2 s poll), and the warning is logged once per path per that time
# however often the file changes: 1,000 polls gave 1,000 lines before. Path only, never content.
# 261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 4
_TAIL_RETRY_SECONDS = 60.0
_tail_failures: OrderedDict[str, tuple[int, int, float, float]] = OrderedDict()
# The clock the retry and the warning throttle read, a seam so a test can move time.
_tail_clock = time.monotonic


class LiveDeps(NamedTuple):
    """The rail helpers `live_sessions` needs, passed in by `web.py` (D10).

    `session_is_live(snapshot, session, provider) -> bool` is the rail's live
    check; `acp_availability(sids, held, workspace_hashes) -> {sid: state}` and
    `acp_status_for_held(sessions, snapshot) -> {sid: status}` its held/locked
    and working/waiting verdicts; `row_title(session) -> str` its label.
    `hidden(cwd) -> bool` is the `hidden` workspace tag and
    `provider_shown(provider) -> bool` the available-and-enabled rule.
    """
    session_is_live: Callable
    acp_availability: Callable
    acp_status_for_held: Callable
    row_title: Callable
    hidden: Callable
    provider_shown: Callable


def _read_tail(path: Path, start: int, length: int, shared: bool = False) -> bytes:
    """`length` bytes of `path` from `start`. Module-level and called by name
    so a test can count the reads.

    `shared` is passed for a Codex rollout only: it is opened through
    `data_codex.open_shared`, because a plain `open()` on Windows blocks
    Codex's own rename and delete of the file while the handle lives (D10).
    261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 4
    """
    if shared:
        from . import data_codex
        with data_codex.open_shared(path) as fh:
            fh.seek(start)
            return fh.read(length)
    with path.open("rb") as fh:
        fh.seek(start)
        return fh.read(length)


def _clip(text, limit: int) -> str:
    """One line of at most `limit` characters: whitespace runs collapse to a
    space, and a cut ends in an ellipsis."""
    return _trim(" ".join(str(text).split()), limit)


def _first_string(value) -> str:
    """The first string value of a tool's input, for `› tool argument`."""
    if isinstance(value, dict):
        for v in value.values():
            if isinstance(v, str) and v.strip():
                return v
    return ""


def _tool_event(name, arg) -> dict:
    name = name if isinstance(name, str) and name.strip() else "tool"
    arg = arg if isinstance(arg, str) else ""
    return {"kind": "tool", "name": _clip(name, _TOOL_ARG_MAX), "arg": _clip(arg, _TOOL_ARG_MAX)}


def _text_event(role: str, text) -> dict | None:
    text = _clip(text, _EVENT_TEXT_MAX) if isinstance(text, str) else ""
    return {"kind": "text", "role": role, "text": text} if text else None


def _v3_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(item["text"] for item in content
                        if isinstance(item, dict) and item.get("type") == "text"
                        and isinstance(item.get("text"), str))
    return ""


def _v3_events(obj: dict) -> list[dict]:
    """A kiro-cli v3 `messages.jsonl` record as tile events."""
    payload = obj.get("payload")
    if not isinstance(payload, dict):
        return []
    ptype = payload.get("type")
    if ptype in ("user", "assistant"):
        ev = _text_event(ptype, _v3_text(payload.get("content")))
        return [ev] if ev else []
    if ptype == "tool_call":
        return [_tool_event(payload.get("toolName"), payload.get("title"))]
    if ptype == "tool_result":
        ok = payload.get("success")
        return [{"kind": "result", "ok": ok}] if isinstance(ok, bool) else []
    return []


def _claude_events(obj: dict) -> list[dict]:
    """A Claude Code transcript line as tile events, in block order."""
    from . import data_claude

    otype = obj.get("type")
    if otype not in ("user", "assistant"):
        return []
    if otype == "user" and data_claude._is_meta_or_command_message(obj):
        return []
    msg = obj.get("message")
    if not isinstance(msg, dict):
        return []
    content = msg.get("content")
    if isinstance(content, str):
        text = data_claude._strip_command_xml(content) if otype == "user" else content
        ev = _text_event(otype, text)
        return [ev] if ev else []
    if not isinstance(content, list):
        return []
    out: list[dict] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text = block.get("text")
            if otype == "user" and isinstance(text, str):
                text = data_claude._strip_command_xml(text)
            ev = _text_event(otype, text)
            if ev:
                out.append(ev)
        elif btype == "tool_use" and otype == "assistant":
            out.append(_tool_event(block.get("name"), _first_string(block.get("input"))))
        elif btype == "tool_result":
            out.append({"kind": "result", "ok": not block.get("is_error")})
    return out


def _codex_events(obj: dict) -> list[dict]:
    """A Codex rollout record as tile events.

    `event_msg` `item_completed` `UserMessage` / `AgentMessage` give text
    events (never the user-role `response_item` messages, which carry injected
    context); `response_item` `function_call` and `custom_tool_call` give tool
    events (the name and the first string of the arguments, or the custom
    tool's input); the matching `*_output` gives a result event whose `ok` is
    the tool's own exit code, and none when it records no exit code (an
    outcome is never guessed). Every other record gives `[]`.

    Deliberately narrower than the other two readers of this record format
    (usage's `_parse_codex_usage`, the transcript's `data_codex._transcript_events`):
    this reader is stateless, one record at a time, so it cannot know that an
    output belongs to a call named `exec` and ignores the `exec` outcome markers
    that they read. A tile therefore shows no result for a finished `exec` call.
    `test_the_tile_usage_and_transcript_readers_agree_on_one_record_shape`
    pins the difference. 261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 4
    """
    from . import data_codex

    otype = obj.get("type")
    if otype == "event_msg":
        found = data_codex.item_of(obj)
        if found is None or found[0] not in ("UserMessage", "AgentMessage"):
            return []
        ev = _text_event("user" if found[0] == "UserMessage" else "assistant",
                         data_codex.item_text(found[1]))
        return [ev] if ev else []
    if otype != "response_item":
        return []
    payload = obj.get("payload")
    if not isinstance(payload, dict):
        return []
    ptype = payload.get("type")
    if ptype == "function_call":
        args = payload.get("arguments")
        arg = ""
        if isinstance(args, str):
            try:
                arg = _first_string(json.loads(args))
            except ValueError:
                arg = args
        return [_tool_event(payload.get("name"), arg)]
    if ptype == "custom_tool_call":
        return [_tool_event(payload.get("name"), payload.get("input"))]
    if ptype in ("function_call_output", "custom_tool_call_output"):
        ok = data_codex.exit_success(data_codex.output_text(payload.get("output")))
        return [{"kind": "result", "ok": ok}] if ok is not None else []
    return []


def _line_events(line: bytes, provider: str) -> list[dict]:
    """One transcript line as tile events; `[]` for a line that is not one.

    An explicit dispatch on the provider with no fall-through: a provider this
    function does not know gives `[]`, never another provider's parse (SC-6,
    D18). Any failure skips this line only, never the tile: a line is data
    from a file an agent writes, and a shape the readers do not expect (a
    `null` `message`, JSON nested deeper than the recursion limit) must not
    cost the other lines. Not logged: the poll runs every 2 s.
    260924_DASHBOARD_OVERVIEW_LIVE_TAILS_PLANS_USAGE
    261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 4
    """
    try:
        obj = json.loads(line)
        if not isinstance(obj, dict):
            return []
        if provider == _CLAUDE:
            return _claude_events(obj)
        if provider == _V3:
            return _v3_events(obj)
        if provider == _CODEX:
            return _codex_events(obj)
        return []
    except Exception:
        return []


def _parse_tail(path: Path, size: int, provider: str, n: int) -> list[dict]:
    """The last `n` events of the file, widening the window until it has them.

    D13: `status_classifier._read_tail_lines` stops at one complete line, and
    a large Claude transcript's last 64 KiB can hold no event at all (measured
    2026-09-24), so this loop widens by `TAIL_GROWTH` until it has `n` events,
    has read the whole file, or has reached `TAIL_MAX_BYTES`.

    Each growth step reads and parses only the bytes the previous window did
    not cover. A window that begins mid-line leaves its first line unparsed as
    `carry`; the next step appends it to the bytes before it, so that line is
    parsed whole once its start is inside the window, and never as a fragment.
    260924_DASHBOARD_OVERVIEW_LIVE_TAILS_PLANS_USAGE
    """
    if n <= 0:
        return []
    events: list[dict] = []
    end = size          # everything from `end` on is parsed, bar `carry`
    carry = b""
    window = TAIL_FIRST_BYTES
    while True:
        start = size - min(window, size)
        # The extra argument goes to a Codex rollout only (D10), so another
        # provider's read keeps the three-argument call a test can hook.
        # 261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 4
        chunk = (_read_tail(path, start, end - start, shared=True) if provider == _CODEX
                 else _read_tail(path, start, end - start))
        lines = (chunk + carry).split(b"\n")
        if start > 0:
            # The window began mid-line.
            carry = lines[0]
            lines = lines[1:]
        else:
            carry = b""
        found: list[dict] = []
        for line in reversed(lines):
            if len(events) + len(found) >= n:
                break
            line = line.strip()
            if not line or len(line) > TAIL_MAX_LINE_BYTES:
                continue
            found[:0] = _line_events(line, provider)
        events = found + events
        end = start
        if len(events) >= n or start == 0 or window >= TAIL_MAX_BYTES:
            return events[-n:]
        window = min(window * TAIL_GROWTH, TAIL_MAX_BYTES)


def tail_events(path, provider: str, n: int = TAIL_EVENTS, st=None) -> list[dict]:
    """The last `n` events of a transcript, for a live tile.

    Each event is one of `{"kind": "text", "role": "user"|"assistant",
    "text"}`, `{"kind": "tool", "name", "arg"}` or `{"kind": "result", "ok"}`.
    Memoised per path on `(mtime_ns, size)` (D22), in a least-recently-used
    memo of at most `_TAIL_MEMO_MAX` paths. `st`, when given, is a `stat` of
    `path` the caller already took this poll, reused instead of a second one.
    A missing or unreadable file gives `[]`; a failure is logged with the path
    only, never content, at most once per path per `_TAIL_RETRY_SECONDS`, and
    the unchanged file is not read again within that time (`_tail_failures`).
    A line that cannot be read as an event is skipped and the result is still
    memoised (`_line_events`).
    261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 4
    """
    path = Path(path)
    key = str(path)
    if st is None:
        try:
            st = path.stat()
        except (OSError, ValueError):
            return []
    with _tail_memo_lock:
        hit = _tail_memo.get(key)
        if hit is not None:
            _tail_memo.move_to_end(key)
    if hit is not None and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
        return list(hit[2])
    with _tail_memo_lock:
        failed = _tail_failures.get(key)
    if (failed is not None and failed[0] == st.st_mtime_ns and failed[1] == st.st_size
            and 0 <= _tail_clock() - failed[2] < _TAIL_RETRY_SECONDS):
        return []
    try:
        events = _parse_tail(path, st.st_size, provider, n)
    except (OSError, ValueError):
        now = _tail_clock()
        with _tail_memo_lock:
            previous = _tail_failures.get(key)
            warn = previous is None or not 0 <= now - previous[3] < _TAIL_RETRY_SECONDS
            _tail_failures[key] = (st.st_mtime_ns, st.st_size, now, now if warn else previous[3])
            _tail_failures.move_to_end(key)
            while len(_tail_failures) > _TAIL_MEMO_MAX:
                _tail_failures.popitem(last=False)
        if warn:
            from . import data_codex
            log.warning("Overview: could not read transcript tail %s", data_codex.log_path(key))
        return []
    with _tail_memo_lock:
        _tail_failures.pop(key, None)
        _tail_memo[key] = (st.st_mtime_ns, st.st_size, events)
        _tail_memo.move_to_end(key)
        while len(_tail_memo) > _TAIL_MEMO_MAX:
            _tail_memo.popitem(last=False)
    return list(events)


def _transcript_path(sid: str, provider: str, cwd: str) -> Path | None:
    """Where a session's transcript is. Module-level so tests can point it at
    fixtures."""
    from .status_classifier import _resolve_jsonl_path
    return _resolve_jsonl_path(sid, provider, cwd)


# A live session id whose process cwd could not be read (access denied, a
# process that exited mid-scan) has no workspace in the snapshot. Its
# transcript is found without one and its workspace read from it; the lookup is
# memoised per (provider, sid): (monotonic time, path or None, cwd). A complete
# answer (path and cwd) is kept, least recently used first out; an incomplete
# one is retried after `_CWDLESS_RETRY_SECONDS`, so a poll every 2 s costs no
# directory walk. 260924_DASHBOARD_OVERVIEW_LIVE_TAILS_PLANS_USAGE
_CWDLESS_TITLE = "Live session"
_CWDLESS_MEMO_MAX = 4 * LIVE_MAX_TILES
_CWDLESS_RETRY_SECONDS = 30.0
_CWDLESS_HEAD_BYTES = 64 * 1024
_cwdless_memo: OrderedDict[tuple[str, str], tuple[float, Path | None, str]] = OrderedDict()
_cwdless_lock = threading.Lock()


def _transcript_cwd(path: Path, provider: str) -> str:
    """The workspace a transcript records: kiro-cli v3's `session.json`
    `workspacePaths[0]`, the first `cwd` in a Claude Code transcript's first
    `_CWDLESS_HEAD_BYTES`, or a Codex rollout's `session_meta` `cwd` (its
    first line, read through `data_codex.read_meta`, which caps it at 256 KiB
    and opens it with shared delete access). "" when it names none, and for a
    provider this function does not know (D18: no fall-through).
    261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 4"""
    if provider == _V3:
        meta = _read_small_json(path.parent / "session.json")
        paths = meta.get("workspacePaths") if isinstance(meta, dict) else None
        return paths[0] if isinstance(paths, list) and paths and isinstance(paths[0], str) else ""
    if provider == _CODEX:
        from . import data_codex
        meta = data_codex.read_meta(path)
        cwd = meta.get("cwd") if isinstance(meta, dict) else None
        return cwd if isinstance(cwd, str) else ""
    if provider != _CLAUDE:
        return ""
    with path.open("rb") as fh:
        head = fh.read(_CWDLESS_HEAD_BYTES)
    for line in head.split(b"\n"):
        try:
            obj = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if isinstance(obj, dict) and isinstance(obj.get("cwd"), str) and obj["cwd"]:
            return obj["cwd"]
    return ""


def _find_cwdless(provider: str, sid: str) -> tuple[Path | None, str]:
    """The transcript path and recorded workspace of a live session whose
    process cwd is unknown; `(None, "")` when it cannot be found. `sid` has
    passed `data.SESSION_ID_RE`, so it holds no glob character."""
    key = (provider, sid)
    with _cwdless_lock:
        hit = _cwdless_memo.get(key)
        if hit is not None:
            _cwdless_memo.move_to_end(key)
    if hit is not None and ((hit[1] is not None and hit[2])
                            or time.monotonic() - hit[0] < _CWDLESS_RETRY_SECONDS):
        return hit[1], hit[2]
    path: Path | None = None
    cwd = ""
    try:
        # kiro-cli v3 is found by id alone, and so is a Codex rollout
        # (`_resolve_jsonl_path` asks the Codex store index); a Claude Code
        # transcript lives in a folder named after the cwd, so every project
        # folder is tried.
        # 261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 4
        path = _transcript_path(sid, provider, "")
        if path is None and provider == _CLAUDE:
            from . import data_claude
            path = next(iter(Path(data_claude.CLAUDE_PROJECTS_DIR).glob(f"*/{sid}.jsonl")), None)
        if path is not None:
            cwd = _transcript_cwd(path, provider)
    except (OSError, ValueError):
        log.warning("Overview: could not locate the transcript of live session %s", sid)
    with _cwdless_lock:
        _cwdless_memo[key] = (time.monotonic(), path, cwd)
        _cwdless_memo.move_to_end(key)
        while len(_cwdless_memo) > _CWDLESS_MEMO_MAX:
            _cwdless_memo.popitem(last=False)
    return path, cwd


def _cwdless_candidate(provider: str, sid: str, originals: dict[str, str],
                       shown: Callable, sessions_in: Callable, is_live: Callable):
    """A `live_sessions` candidate for a live session id with no process cwd,
    or None when the rail's filters leave it out.

    The workspace is the one its transcript records. A known workspace goes
    through the same filters as any other tile, and its store record is used
    when the rail has discovered that workspace; otherwise the tile is a
    minimal one titled "Live session", with no workspace when none is
    recorded (only the provider filter then applies)."""
    from . import data

    path, cwd = _find_cwdless(provider, sid)
    original = originals.get(data._normalize_path(cwd)) if cwd else None
    if not shown(provider, original or cwd):
        return None
    if original:
        session = next((s for s in sessions_in(provider, original) if s.session_id == sid), None)
        if session is not None:
            return (session, original, False, True, path) if is_live(session, provider) else None
    cwd = original or cwd
    return (data.Session(sid, _CWDLESS_TITLE, cwd, "", "", "", "", ""), cwd, False, True, path)


def live_sessions(held: dict[str, str], snapshot, filter_: str, deps: LiveDeps,
                  originals: dict[str, str]) -> list[dict]:
    """The Live now tiles. Blocking; runs in a worker thread.

    Live is the rail's rule (D8): a session this PowerAtlas holds, or one the
    rail's `_session_is_live` marks live, less hidden workspaces and providers
    the rail does not show. Held sessions with no store record (a new session,
    or one in the agent's own folder) are included, titled "New session". A
    live session id whose process cwd could not be read is included too, with
    the workspace its transcript records (`_cwdless_candidate`), titled "Live
    session" when it has no store record.

    `held` maps each held sid to its cwd, captured on the loop by the route.
    `snapshot` is a `presence.Snapshot` taken in this thread. `originals` maps
    a normalised cwd to the spelling the store discovered: a snapshot cwd is
    casefolded, and handing it to `data.get_sessions` would record that as the
    workspace's spelling, so every cwd goes through `originals` first and one
    it does not know is not read (a held session there gets the minimal
    record).

    `filter_ == "poweratlas"` keeps held sessions only. At most
    `LIVE_MAX_TILES` tiles, most recent activity first; availability and
    status are worked out for those only.
    """
    from . import data, data_codex, data_kiro_v3

    only_held = filter_ == "poweratlas"
    loaded: dict[tuple[str, str], list] = {}

    def sessions_in(provider: str, cwd: str) -> list:
        key = (provider, cwd)
        if key not in loaded:
            try:
                loaded[key] = list(data.get_sessions(cwd, provider))
            except Exception:
                log.exception("Overview: could not read %s sessions for %s", provider, cwd)
                loaded[key] = []
        return loaded[key]

    def shown(provider: str, cwd: str) -> bool:
        try:
            return bool(deps.provider_shown(provider)) and not deps.hidden(cwd)
        except Exception:
            log.exception("Overview: could not apply the rail's filters to %s", cwd)
            return False

    # A rail helper that raises is logged once per poll, not once per session.
    failed: set[str] = set()

    def is_live(session, provider: str) -> bool:
        try:
            return bool(deps.session_is_live(snapshot, session, provider))
        except Exception:
            if "live" not in failed:
                failed.add("live")
                log.exception("Overview: could not apply the rail's live rule to %s",
                              session.session_id)
            return False

    # (provider, sid) -> (session, cwd, held, live, transcript path or None).
    # The path is set only where it was found without the cwd (`_find_cwdless`).
    cands: dict[tuple[str, str], tuple] = {}

    # (a) Held sessions. The cwd comes from the supervisor's record.
    for sid, held_cwd in held.items():
        held_cwd = held_cwd or ""
        if not shown(_HELD_PROVIDER, held_cwd):
            continue
        original = originals.get(data._normalize_path(held_cwd)) if held_cwd else None
        session = None
        if original:
            session = next((s for s in sessions_in(_HELD_PROVIDER, original)
                            if s.session_id == sid), None)
        cwd = original or held_cwd
        if session is None:
            session = data.Session(sid, _NEW_SESSION_TITLE, cwd, "", "", "", "", "")
        cands[(_HELD_PROVIDER, sid)] = (session, cwd, True, is_live(session, _HELD_PROVIDER), None)

    if not only_held:
        # (b) Session ids on a process command line or in a sidecar. Checked
        # against SESSION_ID_RE before anything is looked up by them.
        for provider, sid, norm in snapshot.live_sids():
            if (provider, sid) in cands or not data.SESSION_ID_RE.fullmatch(sid or ""):
                continue
            if not norm:
                # The process's cwd could not be read (the rail still shows
                # its dot, from the record's own cwd): the workspace comes
                # from the transcript instead.
                cand = _cwdless_candidate(provider, sid, originals, shown, sessions_in, is_live)
                if cand is not None:
                    cands[(provider, sid)] = cand
                continue
            original = originals.get(norm)
            if not original or not shown(provider, original):
                continue
            session = next((s for s in sessions_in(provider, original)
                            if s.session_id == sid), None)
            if session is not None and is_live(session, provider):
                cands[(provider, sid)] = (session, original, False, True, None)
        # (c) Sessions in a cwd a provider process runs in, written recently.
        for provider, norm in snapshot.live_cwd_pairs():
            original = originals.get(norm)
            if not original or not shown(provider, original):
                continue
            for session in sessions_in(provider, original):
                key = (provider, session.session_id)
                if key not in cands and is_live(session, provider):
                    cands[key] = (session, original, False, True, None)

    now = time.time()
    rows = []
    for (provider, sid), (session, cwd, is_held, live, found) in cands.items():
        path = None
        st = None
        activity = 0.0
        try:
            path = found or _transcript_path(sid, provider, cwd)
            if path is not None:
                st = path.stat()
                # Windows freezes the mtime of a rollout Codex holds open, so
                # a Codex tile's activity (and its rank) reads the last record
                # as well (D16). 261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 4
                activity = (data_codex.activity_epoch(path, st) if provider == _CODEX
                            else st.st_mtime)
        except (OSError, ValueError):
            path = st = None
        if not activity:
            activity = _epoch(session.updated_at) or 0.0
        # A held session with no transcript and no record time is one just
        # created: it sorts as active now, so the cap never cuts it. Its shown
        # activity stays unknown. 260924_DASHBOARD_OVERVIEW_LIVE_TAILS_PLANS_USAGE
        order = activity or (now if is_held else 0.0)
        rows.append((activity, provider, sid, session, cwd, is_held, path, live, st, order))
    rows.sort(key=lambda r: r[9], reverse=True)
    rows = rows[:LIVE_MAX_TILES]

    # Workspace-hash dirs for `acp_availability`, which reads one only for a
    # `sess_` id that is not held. A v3 transcript is
    # `<root>/<hash>/<sid>/messages.jsonl`, so the hash comes from the path
    # already resolved; otherwise one lookup per distinct cwd per poll (each
    # walks every v3 session dir). A wrong name is safe: `_lock_holder_v3`
    # validates it and falls back to its full scan.
    # 260924_DASHBOARD_OVERVIEW_LIVE_TAILS_PLANS_USAGE
    hashes: dict[str, str] = {}
    by_cwd: dict[str, str | None] = {}
    for _a, prov, sid, _s, cwd, is_held, path, _l, _st, _o in rows:
        if is_held or not sid.startswith("sess_"):
            continue
        wh = None
        if (prov == _HELD_PROVIDER and path is not None and path.name == "messages.jsonl"
                and path.parent.name == sid):
            wh = path.parent.parent.name or None
        elif cwd:
            if cwd not in by_cwd:
                try:
                    by_cwd[cwd] = data_kiro_v3.hash_dir_for_cwd(cwd)
                except Exception:
                    by_cwd[cwd] = None
            wh = by_cwd[cwd]
        if wh:
            hashes[sid] = wh
    try:
        availability = deps.acp_availability([r[2] for r in rows], frozenset(held), hashes)
    except Exception:
        log.exception("Overview: could not read session availability")
        availability = {}
    try:
        statuses = deps.acp_status_for_held([r[3] for r in rows if r[5]], snapshot)
    except Exception:
        log.exception("Overview: could not settle held session status")
        statuses = {}

    tiles = []
    for activity, provider, sid, session, cwd, is_held, path, live, st, _o in rows:
        events: list = []
        if path is not None:
            try:
                events = tail_events(path, provider, st=st)
            except Exception:
                log.exception("Overview: could not read the tail of %s", path)
        try:
            title = deps.row_title(session) or ""
        except Exception:
            if "title" not in failed:
                failed.add("title")
                log.exception("Overview: could not title the tile of %s", sid)
            title = ""
        tiles.append({
            "id": sid,
            "provider": provider,
            "title": title,
            "cwd": cwd,
            "name": (Path(cwd).name or cwd) if cwd else "",
            "created_at": session.created_at or "",
            "updated_at": session.updated_at or "",
            "availability": availability.get(sid, "held" if is_held else "available"),
            "status": statuses.get(sid, ""),
            "live": live,
            "last_activity": (datetime.fromtimestamp(activity, tz=timezone.utc).isoformat()
                              if activity else ""),
            "events": events,
        })
    return tiles


# --- Usage -------------------------------------------------------------------
#
# SC-7/SC-8: 14 days of usage, computed in memory from the transcript stores.
# A per-file summary is memoised on `(mtime_ns, size)` (D22), a warm pass fills
# the memo after startup (D12), and a request re-parses only files that changed
# since. Nothing is written to disk. 260924_DASHBOARD_OVERVIEW_LIVE_TAILS_PLANS_USAGE
#
# The warm pass runs in two stages (user decision, 2026-09-28). Stage 1 parses
# the main transcripts (kiro-cli v3 and Claude Code sessions); once it is done
# the summary route returns an aggregate marked `partial`, computed from the
# memo without the Claude Code sub-agent transcripts. Stage 2 then parses the
# sub-agent transcripts, which only add Claude tokens, and the
# route returns the complete aggregate. Measured 2026-09-28 out of process:
# the sub-agent files were 548 of the 682 in-window files and about half of a
# cold pass. A pass that starts with the memo filled finds stage 1 all hits.
#
# Definitions the section is built on:
#
# - Days are local calendar days. The window is today and the 13 days before.
# - "This week" is the last 7 days of the window, today included; "last week"
#   is the 7 before. A 14-day window cannot hold two calendar weeks.
# - Agent time, kiro-cli v3: the sum of `usage_summary.payload.elapsedTime`,
#   which is **milliseconds** (verified 2026-09-25 against 399 turns: it equals
#   the `turn_start` -> `usage_summary` timestamp span to within 0.3 %; see
#   docs/KNOWLEDGE.md), on the local day of that record.
# - Agent time, Claude Code (an estimate): each turn runs from a prompt record
#   to the last assistant record before the next prompt, capped at
#   `CLAUDE_TURN_CAP_SECONDS`, on the prompt's local day. A prompt is a `user`
#   record that is neither `isMeta` nor `isCompactSummary` (the recap Claude
#   Code writes after compacting, which continues a turn rather than starting
#   one) and whose content is a non-empty string, or a list with no
#   `tool_result` block.
# - Tokens and the kiro-cli context peak are counted on the local day of their
#   own record, so a long session modified inside the window contributes only
#   its in-window days.
# - Claude tokens are counted once per `message.id`: Claude Code writes one
#   record per content block and repeats the message's `usage` on each
#   (measured 2026-09-25: 975 of 1,848 assistant records in one file were such
#   repeats, none with a different `usage`).
# - Claude Code sub-agent transcripts,
#   `<project>/<session-uuid>/subagents/agent-<hex>.jsonl`, add their tokens
#   to the totals; they are not sessions of their own and add no agent time,
#   model or workspace row (their time is inside the parent turn that waited
#   for them). Measured 2026-09-25: 574 in-window files, holding about 3.09 B
#   cache-read tokens that the session files alone missed (see
#   docs/KNOWLEDGE.md).
# - Kiro IDE contributes sessions per day only, from `dateCreated` in its
#   `sessions.json` files (D25); it records no durations and no tokens.
# - Codex: one rollout is one top-level session. Every sub-agent rollout is
#   excluded, tokens included: it embeds its parent's history
#   with no usable window (measured on a full scan of 509 of them), so a sum
#   would count that history again. A rollout is in the window when its mtime
#   or its last record's timestamp is (Windows freezes the mtime of a file
#   Codex holds open). A day is active when it holds a user or agent message,
#   a `task_started` or a tool call. Agent time is `task_complete.duration_ms`
#   (exact, Codex 0.139 and later, and at most `CODEX_DURATION_CAP_SECONDS`)
#   else the span from `task_started` to
#   `task_complete`, capped at `CODEX_TURN_CAP_SECONDS` (an estimate), and a
#   `turn_aborted` closes its turn the same way; either is counted on the day
#   of the record that ends the turn, and a turn that never ends counts
#   nothing. Tokens are what each `token_count` event reports of its
#   own, its `last_token_usage`, counted once per distinct cumulative
#   `total_token_usage` among the last `_CODEX_RECENT_EVENTS` events (a repeat
#   counts nothing), so a restart, a lost event or a rollout that holds more
#   than one cumulative series never skips anything (`_CodexTokenCounter`). An
#   event without a usable `last_token_usage` counts the growth of its
#   cumulative total over the previous one (a field that went down is a reset
#   and counts its new value), and so does the first event of a file when its
#   cumulative is larger than its last (a rollout whose early events were
#   lost). `cached_input_tokens` are part of
#   `input_tokens`, so `input` is the count of the one minus the count of
#   the other. A changed rollout is re-parsed at most once per
#   `CODEX_REPARSE_SECONDS`: the previous summary stays until then.
#   261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 4

USAGE_WINDOW_DAYS = 14
USAGE_MAX_LINE_BYTES = 8 * 1024 * 1024
CLAUDE_TURN_CAP_SECONDS = 30 * 60
# A Codex turn timed from its timestamps (no `duration_ms`) counts at most this.
CODEX_TURN_CAP_SECONDS = 30 * 60
# A changed Codex rollout is re-parsed at most this often (D19): an active
# session grows on every event, and a parse of a large one costs about 0.34 s.
CODEX_REPARSE_SECONDS = 60.0
# A cumulative token counter above this is not a token count (a hostile value
# would otherwise reach the page as a number a double cannot hold).
_CODEX_TOKEN_MAX = 10 ** 15
# An exact `duration_ms` above this is not a turn (tokens are bounded above; a
# duration needs a bound too): the timestamps, themselves capped, time it instead.
CODEX_DURATION_CAP_SECONDS = 24 * 3600
# Token rule L (D19 as amended after review, 261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 4):
# an event whose cumulative total equals one seen in this many of the events
# before it is a repeat and counts nothing; any other event counts its own
# `last_token_usage`. No distance, ratio or tolerance is tuned: the 93-rollout
# store has no delta that differs from the event's own last except in the one
# rollout that interleaves two cumulative series.
_CODEX_RECENT_EVENTS = 8
# A `turn_context` model and a `session_meta` cwd reach the page through the usage summary, from
# a file an agent writes: each is cut to this many characters when the file is parsed (a model
# the way a tile's tool name is, `_clip`; a cwd by `_trim`, which keeps its whitespace, so a
# workspace the rail hides still matches). 260 is a Windows MAX_PATH.
# 261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 4
_CODEX_NAME_MAX = 80
_CODEX_CWD_MAX = 260
USAGE_REUSE_SECONDS = 30.0
CONTEXT_PRESSURE_PERCENT = 80.0
_USAGE_TOP_WORKSPACES = 8
_USAGE_TOP_CONTEXT = 5
_USAGE_TOP_MODELS = 8
# A kiro-cli `session.json` or a Kiro IDE `sessions.json` larger than this is
# not read; real ones are a few KiB.
_USAGE_SIDE_FILE_MAX = 8 * 1024 * 1024
_V3 = "kiro-cli-v3"
_CLAUDE = "claude-code"
_CODEX = "codex"
# The file-discovery tag of a Claude Code sub-agent transcript. Internal only:
# its summary carries `provider == _CLAUDE` and `subagent: True`.
_CLAUDE_SUB = "claude-code-subagent"
_IDE = "kiro-ide"
# Record types that mark a kiro-cli v3 session as active on their day.
_V3_ACTIVE_TYPES = frozenset({"user", "assistant", "tool_call", "tool_result",
                              "turn_start", "usage_summary"})
_TOKEN_KEYS = (("input", "input_tokens"), ("output", "output_tokens"),
               ("cache_read", "cache_read_input_tokens"),
               ("cache_creation", "cache_creation_input_tokens"))

# Path-keyed memo of file summaries (D22): path -> (mtime_ns, size, summary).
# Each complete pass evicts the paths that left the window.
_usage_memo: dict[str, tuple[int, int, dict]] = {}
_usage_memo_lock = threading.Lock()
# Codex rollout path -> the `_debounce_clock()` time its summary entered the
# memo, for the 60 s re-parse limit (D19). Kept beside the memo, not in it: a
# memo entry stays a `(mtime_ns, size, summary)` triple, which the worker's
# records, the eviction and the tests all read. Guarded by `_usage_memo_lock`
# and evicted with the memo. 261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 4
_codex_parsed_at: dict[str, float] = {}
# The clock the debounce reads, a seam so a test can move time.
_debounce_clock = time.monotonic

# `cold` until the warm pass starts, `warming` while it runs, then `ready`, or
# `error` when it failed as a whole. A one-element list so tests can reset it.
_usage_state: list[str] = ["cold"]
# True once the running warm pass has finished its stage 1 (the main
# transcripts are in the memo): while `warming`, the route then returns a
# partial aggregate instead of None. Cleared whenever the state turns
# `warming`, and when the pass ends `ready` or `error`; a pass stopped in
# stage 2 leaves it set, with the state `cold`.
_usage_stage1: list[bool] = [False]
# The last aggregate: (monotonic time it was computed, payload, filter key).
# Reused for `USAGE_REUSE_SECONDS` while the rail's filters are unchanged
# (D23); the lock makes the computation single-flight.
_usage_cache: list = [0.0, None, None]
_usage_compute_lock = threading.Lock()
# Set by `web.lifespan` on shutdown. Every pass checks it between files: the
# startup warm pass, the background pass `usage_payload` starts from `cold` or
# `error`, and a request's own refresh. 260924_DASHBOARD_OVERVIEW_LIVE_TAILS_PLANS_USAGE
_usage_stop = threading.Event()
# The background pass `usage_payload` started, if any (tests join it), and the
# lock that makes starting one single-flight.
_usage_bg: list = [None]
_usage_bg_lock = threading.Lock()
# Whether the warm pass parses in a child process (`_UsageWorker`). The parse
# is CPU-bound Python, and in the server it shares one GIL with every request
# thread: measured 2026-09-28, stage 1 took 3.4 s alone and 6.9-7.7 s with two
# dashboard clients loading (19 s live). The tests turn it off so that their
# `_parse_usage_file` hooks see every parse.
_USAGE_WORKER = True
# The running worker's process, if any (tests poll it).
_usage_worker_proc: list = [None]
# A child that sends no record for this long is stuck (a rollout that will not open, a file of
# many gigabytes): `_UsageWorker.wait_stage` kills it and the pass ends `error`, so the page's
# 60 s retry applies. Before it, a stuck child held the state at `warming` for every provider and
# `usage_payload` never started another pass. The longest single file measured was 361 MB at
# 2.7 s, so 120 s is 44 times that. A seam, so a test can shorten it.
# 261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 4
CHILD_STALL_SECONDS = 120.0
# The clock that watchdog reads, a seam so a test can move time.
_stall_clock = time.monotonic
# The file summary's format, sent to the worker and echoed back by it: bump it
# whenever a parser's output changes shape, so a child running newer code from
# disk than this server loaded is ignored rather than mixed into the memo.
# 2: a `codex` provider (Codex rollouts parse by `_parse_codex_usage`, and an
# unknown provider no longer parses as Claude Code).
# 3: Codex tokens follow a stream rule, Codex `exec` failures count, and an
# exact Codex duration above 24 h falls back to the timestamps.
# 4: Codex tokens are each event's own `last_token_usage` (rule L,
# `_CodexTokenCounter`), replacing the growth-versus-baseline stream rule of 3.
# 5: a Codex tool name, model and cwd are cut to `_CODEX_NAME_MAX` / `_CODEX_CWD_MAX`.
# 6: a Codex tool name carries the `_CODEX_TOOL_PREFIX` prefix.
# 7: a day holds no per-tool call and failure counts: `tools` is gone.
# 261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 4
# 8: a Codex day carries a `context_peak`, the peak of last input tokens over the model's
# context window (`_codex_context_pct`).
# 261002_CODEX_LIVE_STATUS_CONTEXT_PRESSURE_AND_SUBAGENT_USAGE_FROM_STATE_DB Phase 2
_USAGE_SCHEMA = 8
# The keys `usage_summary` reads from a file summary.
_SUMMARY_KEYS = frozenset({"provider", "session_id", "cwd", "model", "subagent", "days"})


class _UsageStopped(Exception):
    """A request's refresh stopped at shutdown; nothing is cached."""


class _UsageWorkerStalled(Exception):
    """The worker child sent nothing for `CHILD_STALL_SECONDS` and was killed; the warm pass
    ends `error`. 261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 4"""


def _usage_roots() -> tuple[Path, Path, Path]:
    """The three stores' roots: kiro-cli v3 sessions, Claude Code projects and
    the Kiro IDE workspace sessions. Read from their modules at call time, so a
    test that points a constant at a fixture is honoured; one seam, so the test
    suite can point every usage read at an empty folder without touching the
    constants other readers use."""
    from . import data_claude, data_kiro_ide, status_classifier
    return (Path(status_classifier._V3_SESSIONS_ROOT), Path(data_claude.CLAUDE_PROJECTS_DIR),
            Path(data_kiro_ide.SESSIONS_DIR))


def _codex_usage_root() -> Path:
    """The Codex sessions folder (`~/.codex/sessions`, or under `CODEX_HOME`).
    A seam of its own: `_usage_roots` stays a triple, which the existing tests
    patch. Read from `data_codex` at call time, so a test that points the
    constant at a fixture is honoured.
    261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 4"""
    from . import data_codex
    return Path(data_codex.CODEX_SESSIONS_DIR)


def usage_state() -> str:
    return _usage_state[0]


def _set_usage_state(state: str) -> None:
    if state == "warming":
        _usage_stage1[0] = False
    _usage_state[0] = state


def _iter_lines(fh, limit: int):
    """The file's lines, skipping any longer than `limit` bytes without holding
    more than `limit + 1` bytes of one in memory."""
    while True:
        line = fh.readline(limit + 1)
        if not line:
            return
        if len(line) > limit:
            while line and not line.endswith(b"\n"):
                line = fh.readline(1024 * 1024)
            continue
        yield line


def _epoch(ts) -> float | None:
    """The epoch seconds of an ISO 8601 timestamp (`Z` accepted, naive read as
    UTC), or None for anything that is not one. The one parser for the live
    tiles' record times and the usage records' timestamps."""
    if not isinstance(ts, str) or not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def _local_day(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).date().isoformat()


def _new_day() -> dict:
    return {"active": False, "agent_seconds": 0.0, "context_peak": None,
            "tokens": {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0}}


def _read_small_json(path: Path):
    """A small JSON side file (`session.json`, `sessions.json`), or None. A
    byte-order mark is accepted: one real Kiro IDE `sessions.json` has one."""
    try:
        with path.open("rb") as fh:
            raw = fh.read(_USAGE_SIDE_FILE_MAX + 1)
        if len(raw) > _USAGE_SIDE_FILE_MAX:
            return None
        return json.loads(raw.decode("utf-8-sig"))
    except (OSError, ValueError, RecursionError):
        return None


def _parse_v3_usage(path: Path) -> dict:
    days: dict[str, dict] = {}
    with path.open("rb") as fh:
        for line in _iter_lines(fh, USAGE_MAX_LINE_BYTES):
            try:
                obj = json.loads(line)
                if not isinstance(obj, dict):
                    continue
                payload = obj.get("payload")
                if not isinstance(payload, dict):
                    continue
                ptype = payload.get("type")
                if ptype == "session_metadata":
                    # The peak is kept per day, so only in-window records
                    # count; a metadata record does not make its day active.
                    value = payload.get("value")
                    if payload.get("key") == "contextUsage" and isinstance(value, dict):
                        pct = value.get("usagePercentage")
                        epoch = _epoch(obj.get("timestamp"))
                        if (isinstance(pct, (int, float)) and not isinstance(pct, bool)
                                and epoch is not None):
                            day = days.setdefault(_local_day(epoch), _new_day())
                            peak = day["context_peak"]
                            day["context_peak"] = pct if peak is None else max(peak, pct)
                    continue
                if ptype not in _V3_ACTIVE_TYPES:
                    continue
                epoch = _epoch(obj.get("timestamp"))
                if epoch is None:
                    continue
                day = days.setdefault(_local_day(epoch), _new_day())
                day["active"] = True
                if ptype == "usage_summary":
                    ms = payload.get("elapsedTime")
                    if isinstance(ms, (int, float)) and not isinstance(ms, bool) and ms > 0:
                        day["agent_seconds"] += ms / 1000.0
            except Exception:
                # One odd line costs that line only (the tail reader's rule).
                continue
    meta = _read_small_json(path.parent / "session.json")
    cwd = ""
    model = None
    if isinstance(meta, dict):
        paths = meta.get("workspacePaths")
        if isinstance(paths, list) and paths and isinstance(paths[0], str):
            cwd = paths[0]
        if isinstance(meta.get("modelId"), str) and meta["modelId"]:
            model = meta["modelId"]
    return {"provider": _V3, "session_id": path.parent.name, "cwd": cwd, "model": model,
            "subagent": False, "days": days}


def _claude_is_prompt(content) -> bool:
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        blocks = [b for b in content if isinstance(b, dict)]
        return bool(blocks) and not any(b.get("type") == "tool_result" for b in blocks)
    return False


def _parse_claude_usage(path: Path, subagent: bool = False) -> dict:
    days: dict[str, dict] = {}
    models: dict[str, int] = {}
    seen_messages: set[str] = set()
    cwd = ""
    turn: list = [None, None, None]          # prompt epoch, prompt day, last assistant epoch

    def close_turn():
        start, day_key, end = turn
        if start is not None and end is not None and end > start:
            days[day_key]["agent_seconds"] += min(end - start, CLAUDE_TURN_CAP_SECONDS)

    with path.open("rb") as fh:
        for line in _iter_lines(fh, USAGE_MAX_LINE_BYTES):
            # Once `cwd` is known only user and assistant records are read,
            # and a line holding neither word as a JSON string cannot be one:
            # Claude Code writes compact JSON and never escapes a letter. It
            # skips about a third of the bytes (attachments, progress, queue
            # and mode records), and `json.loads` is most of the parse.
            # 260924_DASHBOARD_OVERVIEW_LIVE_TAILS_PLANS_USAGE
            if cwd and b'"user"' not in line and b'"assistant"' not in line:
                continue
            try:
                obj = json.loads(line)
                if not isinstance(obj, dict):
                    continue
                if not cwd and isinstance(obj.get("cwd"), str):
                    cwd = obj["cwd"]
                otype = obj.get("type")
                if otype not in ("user", "assistant"):
                    continue
                msg = obj.get("message")
                if not isinstance(msg, dict):
                    continue
                epoch = _epoch(obj.get("timestamp"))
                if epoch is None:
                    continue
                day_key = _local_day(epoch)
                content = msg.get("content")
                if otype == "user":
                    if (obj.get("isMeta") or obj.get("isCompactSummary")
                            or not _claude_is_prompt(content)):
                        continue
                    close_turn()
                    days.setdefault(day_key, _new_day())["active"] = True
                    turn[:] = [epoch, day_key, None]
                    continue
                day = days.setdefault(day_key, _new_day())
                day["active"] = True
                if turn[0] is not None:
                    turn[2] = epoch
                model = msg.get("model")
                if isinstance(model, str) and model and not model.startswith("<"):
                    models[model] = models.get(model, 0) + 1
                mid = msg.get("id")
                usage = msg.get("usage")
                if isinstance(usage, dict) and not (isinstance(mid, str) and mid in seen_messages):
                    if isinstance(mid, str):
                        seen_messages.add(mid)
                    for key, src in _TOKEN_KEYS:
                        n = usage.get(src)
                        if isinstance(n, int) and not isinstance(n, bool) and n > 0:
                            day["tokens"][key] += n
            except Exception:
                continue
    close_turn()
    model = max(sorted(models), key=lambda m: models[m]) if models else None
    # A sub-agent transcript is filed under its parent session's folder.
    session_id = path.parent.parent.name if subagent else path.stem
    return {"provider": _CLAUDE, "session_id": session_id, "cwd": cwd, "model": model,
            "subagent": subagent, "days": days}


# A line is parsed only when it holds one of these (a substring test is far
# cheaper than `json.loads`, and most bytes of a rollout are tool output and
# injected context). Each ends with its closing quote, so a `*_output` record
# (the largest lines of a rollout, and read for nothing) never matches. A false
# positive costs one parse; a false negative would lose a record, so every
# record kind the parser reads is here.
# 261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 4
_CODEX_KEYS = (b'"token_count"', b'"task_started"', b'"task_complete"', b'"turn_aborted"',
               b'"turn_context"', b'"item_completed"', b'"function_call"', b'"custom_tool_call"')
_CODEX_TOKEN_FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens",
                       "cache_write_input_tokens")


def _codex_count(value) -> int | None:
    """A token counter as a non-negative int, or None for anything else (a
    string, a float, a bool, a negative, an absurd size)."""
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= _CODEX_TOKEN_MAX:
        return value
    return None


def _codex_duration_s(value) -> float | None:
    """`task_complete.duration_ms` in seconds, or None when it is not a finite
    non-negative number of at most `CODEX_DURATION_CAP_SECONDS`."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not (0 <= value <= CODEX_DURATION_CAP_SECONDS * 1000):
        return None
    return value / 1000.0


def _codex_stream_total(usage) -> int | None:
    """`input_tokens + output_tokens` of a token-usage block (the total, because
    `cached_input_tokens` is a subset of the input), or None when either is
    missing or not a token count. The duplicate key of `_CodexTokenCounter`."""
    if not isinstance(usage, dict):
        return None
    inp, out = _codex_count(usage.get("input_tokens")), _codex_count(usage.get("output_tokens"))
    return None if inp is None or out is None else inp + out


def _codex_fields(usage) -> dict[str, int]:
    """The token counters of a usage block that are token counts."""
    if not isinstance(usage, dict):
        return {}
    found = {}
    for field in _CODEX_TOKEN_FIELDS:
        value = _codex_count(usage.get(field))
        if value is not None:      # absent or hostile: counts nothing
            found[field] = value
    return found


class _CodexTokenCounter:
    """What each `token_count` event of one rollout adds, per token field (rule L).

    `add(total, last)` takes the event's cumulative `total_token_usage` and its
    `last_token_usage` and returns the growth to count, keyed by
    `_CODEX_TOKEN_FIELDS`. A Codex rollout can hold more than one cumulative
    series (one of 93 real top-level rollouts alternated between two, one had a
    real restart), so no baseline is trusted: an event counts what it reports of
    its own turn, and that cannot ratchet or skip.

    1. The event has a usable `last_token_usage` (a dict with at least one token
       count among input, cached and output) and a cumulative total
       (`_codex_stream_total`): it counts its own last, unless its cumulative
       total equals one of the previous `_CODEX_RECENT_EVENTS` events' (a repeat,
       also a non-adjacent one: counts nothing).
    2. The first event of a file (no earlier event with a cumulative total) whose
       cumulative total is larger than its last total counts its whole cumulative
       counters instead, so a rollout whose early events were lost keeps its
       history; normally the first event equals its last.
    3. An event without a usable last, or without a cumulative total, counts the
       growth of its cumulative counters over the previous event's, per field (a
       counter that went down is a reset and counts its new value; the first
       such event counts its whole cumulative): D19's original rule, which needs
       no last.

    The window counts every event that has a cumulative total, repeats included,
    so it spans `_CODEX_RECENT_EVENTS` events and not that many distinct totals.
    The baseline of rule 3 follows every event with a usable counter.
    `_USAGE_SCHEMA` 4.
    261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 4
    """

    __slots__ = ("baseline", "recent")

    def __init__(self) -> None:
        self.baseline: dict[str, int] = {}          # the last cumulative value seen per field
        self.recent: deque[int] = deque(maxlen=_CODEX_RECENT_EVENTS)

    def add(self, total, last) -> dict[str, int]:
        values = _codex_fields(total)
        key = _codex_stream_total(total)
        own = _codex_fields(last)
        usable_last = any(f in own for f in _CODEX_TOKEN_FIELDS[:3])
        if key is None or not usable_last:
            grown = {}
            for field, value in values.items():
                delta = value - self.baseline.get(field, 0)
                grown[field] = value if delta < 0 else delta   # a counter that went down was reset
        elif key in self.recent:
            grown = {}
        elif not self.recent and key > own.get("input_tokens", 0) + own.get("output_tokens", 0):
            grown = dict(values)
        else:
            grown = own
        self.baseline.update(values)
        if key is not None:
            self.recent.append(key)
        return grown


def _empty_summary(provider: str, session_id: str = "") -> dict:
    """A summary that contributes nothing: for a transcript no parser owns."""
    return {"provider": provider, "session_id": session_id, "cwd": "", "model": None,
            "subagent": False, "days": {}}


# A model context window outside 1 to this is not believed (a hostile or corrupt record).
_CODEX_WINDOW_MAX = 10 ** 9


def _codex_context_pct(info) -> float | None:
    """How full the context window looked at one Codex `token_count`, in percent: the
    event's last input tokens over the model's window, clipped to 0-100. None unless the
    window is an integer from 1 to `_CODEX_WINDOW_MAX` and the last input a non-negative
    integer (a bool, a string or a negative number gives None). An estimate: the last
    input is the tokens sent in one request, which is what Codex's own indicator tracks,
    not a count of what the model holds. 261002_CODEX_LIVE_STATUS_CONTEXT_PRESSURE_AND_SUBAGENT_USAGE_FROM_STATE_DB
    Phase 2"""
    if not isinstance(info, dict):
        return None
    window = info.get("model_context_window")
    last = info.get("last_token_usage")
    used = last.get("input_tokens") if isinstance(last, dict) else None
    for n in (window, used):
        if not isinstance(n, int) or isinstance(n, bool):
            return None
    if not 1 <= window <= _CODEX_WINDOW_MAX or used < 0:
        return None
    if used >= window:
        return 100.0  # also keeps an absurdly large integer out of the float multiplication
    return 100.0 * used / window


def _parse_codex_usage(path: Path) -> dict:
    """One Codex rollout's usage summary (see the Codex bullet above).

    An empty summary (no days) for a sub-agent rollout, a legacy rollout (no
    `payload` wrapper) and any file whose first line is not a `session_meta`:
    none of them is a session of its own. Every Codex read goes through
    `data_codex.open_shared`. Lines over `USAGE_MAX_LINE_BYTES` are skipped,
    one unreadable line skips that line only, and a number of the wrong type
    counts nothing.
    261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 4
    """
    from . import data_codex

    stem = path.stem
    days: dict[str, dict] = {}
    models: dict[str, int] = {}
    counter = _CodexTokenCounter()
    open_start: float | None = None          # the open turn's `task_started` epoch

    def day_of(epoch: float) -> dict:
        return days.setdefault(_local_day(epoch), _new_day())

    with data_codex.open_shared(path) as fh:
        # The first line is read here, not through `read_meta`, which cannot
        # tell a file that failed to open (an error: nothing is memoised and
        # the next pass retries) from one that is no session (an empty summary).
        first = data_codex.read_first_line(fh)
        head_obj = data_codex.loads(first) if first else None
        meta = head_obj.get("payload") if (isinstance(head_obj, dict)
                                           and head_obj.get("type") == "session_meta") else None
        source = meta.get("source") if isinstance(meta, dict) else None
        if not isinstance(meta, dict) or (isinstance(source, dict) and "subagent" in source):
            return _empty_summary(_CODEX, stem)
        sid = meta.get("id")
        cwd = meta.get("cwd")
        for line in _iter_lines(fh, USAGE_MAX_LINE_BYTES):
            if not any(key in line for key in _CODEX_KEYS):
                continue
            try:
                obj = json.loads(line)
                if not isinstance(obj, dict):
                    continue
                payload = obj.get("payload")
                if not isinstance(payload, dict):
                    continue
                otype, ptype = obj.get("type"), payload.get("type")
                epoch = _epoch(obj.get("timestamp"))
                if epoch is None:
                    continue
                if otype == "turn_context":
                    model = payload.get("model")
                    if isinstance(model, str) and model and not model.startswith("<"):
                        model = _clip(model, _CODEX_NAME_MAX)
                        models[model] = models.get(model, 0) + 1
                elif otype == "response_item":
                    if ptype in ("function_call", "custom_tool_call"):
                        day_of(epoch)["active"] = True
                elif otype == "event_msg":
                    if ptype == "token_count":
                        info = payload.get("info")
                        pct = _codex_context_pct(info)
                        if pct is not None:
                            peak_day = day_of(epoch)
                            peak = peak_day["context_peak"]
                            peak_day["context_peak"] = pct if peak is None else max(peak, pct)
                        total = info.get("total_token_usage") if isinstance(info, dict) else None
                        if not isinstance(total, dict):
                            continue
                        grown = counter.add(total, info.get("last_token_usage"))
                        cached = grown.get("cached_input_tokens", 0)
                        tokens = day_of(epoch)["tokens"]
                        tokens["input"] += max(0, grown.get("input_tokens", 0) - cached)
                        tokens["cache_read"] += cached
                        tokens["output"] += grown.get("output_tokens", 0)
                        tokens["cache_creation"] += grown.get("cache_write_input_tokens", 0)
                    elif ptype == "item_completed":
                        item = payload.get("item")
                        if isinstance(item, dict) and item.get("type") in ("UserMessage", "AgentMessage"):
                            day_of(epoch)["active"] = True
                    elif ptype == "task_started":
                        day_of(epoch)["active"] = True
                        open_start = epoch
                    elif ptype in ("task_complete", "turn_aborted"):
                        secs = (_codex_duration_s(payload.get("duration_ms"))
                                if ptype == "task_complete" else None)
                        if secs is None and open_start is not None and epoch > open_start:
                            secs = min(epoch - open_start, CODEX_TURN_CAP_SECONDS)
                        open_start = None
                        if secs:
                            day_of(epoch)["agent_seconds"] += secs
            except Exception:
                # One odd line costs that line only (the tail reader's rule).
                continue
    model = max(sorted(models), key=lambda m: models[m]) if models else None
    return {"provider": _CODEX, "session_id": sid if isinstance(sid, str) and sid else stem,
            "cwd": _trim(cwd, _CODEX_CWD_MAX) if isinstance(cwd, str) else "", "model": model,
            "subagent": False, "days": days}


def _parse_usage_file(path: Path, provider: str) -> dict:
    """Parse one transcript into its summary. Module-level and called by name
    so a test can count the parses; `_summarize` memoises it per path on
    `(mtime_ns, size)`, and the worker child calls it directly.

    `{"provider", "session_id", "cwd", "model", "subagent", "days"}`
    (`_SUMMARY_KEYS`), where `days` maps a local `YYYY-MM-DD` to `{"active",
    "agent_seconds", "tokens": {"input", "output", "cache_read",
    "cache_creation"}, "context_peak"}`.
    `model` is the v3 `modelId`, the most frequent Claude `message.model` or
    the most frequent Codex `turn_context` model; `context_peak` the v3
    maximum `usagePercentage` (0-100) recorded that day, or for Codex the
    estimate of `_codex_context_pct` (peak of last input tokens over the
    model's window), else None.
    `subagent` is True for a Claude Code sub-agent transcript (`provider`
    `_CLAUDE_SUB`), whose `session_id` is its parent session's. Lines over
    `USAGE_MAX_LINE_BYTES` are skipped, and one unreadable line skips that
    line only. The v3 `session.json` fields (cwd, model) are memoised under
    `messages.jsonl`'s key; they do not change during a session. Changing
    this shape means bumping `_USAGE_SCHEMA`.

    The dispatch is explicit and has no fall-through (SC-6, D18): a provider
    tag this function does not know gets the empty summary, never another
    provider's parser.
    261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 4
    """
    if provider == _V3:
        return _parse_v3_usage(path)
    if provider in (_CLAUDE, _CLAUDE_SUB):
        return _parse_claude_usage(path, subagent=provider == _CLAUDE_SUB)
    if provider == _CODEX:
        return _parse_codex_usage(path)
    return _empty_summary(provider)


def _codex_debounced(key: str, provider: str, hit) -> bool:
    """True when a changed Codex rollout keeps its previous summary for now.

    D19: a rollout whose `(mtime_ns, size)` changed is re-parsed at most once
    per `CODEX_REPARSE_SECONDS`. `hit` is the memo entry of the previous parse,
    which differs from the file's present stat (the caller has checked). Only
    Codex is ever debounced (Claude Code and kiro-cli files re-parse at once),
    a first parse (`hit` None) is never delayed, and a previous entry that is
    not a readable summary, or whose parse time is unknown, parses at once.
    The one predicate behind both places a changed file would be parsed, the
    memo in `_summarize` and the worker's request in `_start_usage_worker`.
    261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 4
    """
    if provider != _CODEX or hit is None:
        return False
    summary = hit[2]
    if not (isinstance(summary, dict) and _SUMMARY_KEYS <= summary.keys()):
        return False
    with _usage_memo_lock:
        parsed_at = _codex_parsed_at.get(key)
    return parsed_at is not None and 0 <= _debounce_clock() - parsed_at < CODEX_REPARSE_SECONDS


def _summarize(path: Path, provider: str, st) -> tuple[dict, bool]:
    """The file's summary and whether it was parsed now (False: a memo hit, or
    a changed Codex rollout still inside its re-parse limit, `_codex_debounced`)."""
    key = str(path)
    with _usage_memo_lock:
        hit = _usage_memo.get(key)
    if hit is not None and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
        return hit[2], False
    if _codex_debounced(key, provider, hit):
        return hit[2], False
    summary = _parse_usage_file(path, provider)
    with _usage_memo_lock:
        _usage_memo[key] = (st.st_mtime_ns, st.st_size, summary)
        if provider == _CODEX:
            _codex_parsed_at[key] = _debounce_clock()
    return summary, True


def _window(now: float) -> tuple[list[str], float]:
    """The window's local days, oldest first, and the epoch of its first
    midnight."""
    today = datetime.fromtimestamp(now).date()
    first = today.fromordinal(today.toordinal() - (USAGE_WINDOW_DAYS - 1))
    days = [first.fromordinal(first.toordinal() + i).isoformat()
            for i in range(USAGE_WINDOW_DAYS)]
    start = datetime(first.year, first.month, first.day).timestamp()
    return days, start


def _usage_files(since: float, subagents: bool = True,
                 stop_event=None) -> list[tuple[Path, str, object]]:
    """`(path, provider, stat)` for each transcript modified at or after
    `since`: kiro-cli v3 `<root>/<hash>/sess_*/messages.jsonl`, Claude Code
    `<root>/<project>/<uuid>.jsonl`, and, unless `subagents` is False, Claude
    Code sub-agent transcripts `<root>/<project>/<uuid>/subagents/*.jsonl`
    (tagged `_CLAUDE_SUB`).

    Codex rollouts follow, in a `try` of their own so a Codex failure cannot
    fail the other providers' pass. They are `data_codex.canonical_rollouts()`,
    the very set the session listing shows (one file per thread id, name and
    `session_meta` agreeing, no symlink, no sub-agent rollout: D7), never a
    glob of `rollout-*.jsonl`, which counts a stray copy, a duplicate-id file
    or a symlink as a session of its own. The adapter's store index sizes its
    own caches. A rollout is in the window when its mtime is, or else when
    the timestamp of its last record is (Windows freezes the mtime of a
    rollout Codex holds open); that tail read happens only for a file whose
    mtime is outside the window, and is cached by `(mtime_ns, size)`. The
    window rule reads a fresh `stat` of the path, never the index's, which
    can be 5 s old. `stop_event`, when set, ends the Codex scan at once with
    what it has found (a store of thousands of rollouts would otherwise
    delay shutdown); the caller treats that pass as incomplete.
    261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 4"""
    from . import data_claude, data_codex

    v3_root, claude_root, _ide_root = _usage_roots()
    found: list[tuple[Path, str, object]] = []
    sources = [(_V3, v3_root.glob("*/sess_*/messages.jsonl")),
               (_CLAUDE, claude_root.glob("*/*.jsonl"))]
    if subagents:
        sources.append((_CLAUDE_SUB, claude_root.glob("*/*/subagents/*.jsonl")))
    for provider, paths in sources:
        for path in paths:
            if provider == _CLAUDE and not data_claude._is_session_file(path.name):
                continue
            if provider == _CLAUDE_SUB and not data_claude._is_session_file(
                    path.parent.parent.name + ".jsonl"):
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            if st.st_mtime >= since:
                found.append((path, provider, st))
    now = time.time()
    try:
        for canonical in data_codex.canonical_rollouts():
            if stop_event is not None and stop_event.is_set():
                break
            path = Path(canonical.path)
            try:
                st = path.stat()
            except OSError:
                continue
            if st.st_mtime >= since:
                found.append((path, _CODEX, st))
                continue
            stamp = data_codex.last_event_epoch(path) or 0.0
            if since <= stamp <= now + data_codex.FUTURE_SKEW:   # a stamp from the future is a clock error (D16)
                found.append((path, _CODEX, st))
    except OSError:
        log.warning("Overview: could not list the Codex rollouts under %s", _codex_usage_root())
    return found


def _refresh(now: float, stop_event=None, subagents: bool = True) -> tuple[list[dict], int, bool]:
    """Summaries of every in-window transcript, how many were parsed now, and
    whether the pass ran to the end. One file that raises is logged (path
    only) and skipped. With `subagents` False the Claude Code sub-agent
    transcripts are neither listed nor parsed (stage 1 of the warm pass, and
    the partial aggregate). Only a complete pass over every kind of file
    evicts memo paths no longer in the window."""
    _days, since = _window(now)
    summaries: list[dict] = []
    reparsed = 0
    keep: set[str] = set()
    complete = True
    files = _usage_files(since, subagents, stop_event)
    if stop_event is not None and stop_event.is_set():
        # The Codex scan may have stopped early: nothing is parsed, nothing evicted.
        return summaries, reparsed, False
    for path, provider, st in files:
        if stop_event is not None and stop_event.is_set():
            complete = False
            break
        keep.add(str(path))
        try:
            summary, parsed = _summarize(path, provider, st)
        except Exception:
            from . import data_codex
            log.warning("Overview: could not summarise transcript %s", data_codex.log_path(str(path)))
            continue
        reparsed += parsed
        summaries.append(summary)
    if complete and subagents:
        with _usage_memo_lock:
            for key in [k for k in _usage_memo if k not in keep]:
                del _usage_memo[key]
            for key in [k for k in _codex_parsed_at if k not in keep]:
                del _codex_parsed_at[key]
    return summaries, reparsed, complete


def _ide_daily(days: set[str], since: float, shown: Callable, hidden: Callable) -> dict[str, int]:
    """Kiro IDE sessions created on each window day (D25): read from its
    `sessions.json` files directly, never through `data.get_sessions`."""
    counts: dict[str, int] = {}
    if not shown(_IDE):
        return counts
    ide_root = _usage_roots()[2]
    for sf in ide_root.glob("*/sessions.json"):
        try:
            if sf.stat().st_mtime < since:
                continue
        except OSError:
            continue
        entries = _read_small_json(sf)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            try:
                day = _local_day(int(entry.get("dateCreated")) / 1000.0)
            except (TypeError, ValueError, OverflowError, OSError):
                continue
            ws = entry.get("workspaceDirectory")
            if day in days and not (isinstance(ws, str) and ws and hidden(ws)):
                counts[day] = counts.get(day, 0) + 1
    return counts


def _pressure_block(rows: list[dict], total: int) -> dict:
    """`{"sessions_over_80", "sessions_total", "top"}` from the sessions that have a peak:
    compared and sorted on the raw peak, rounded for display only."""
    over_80 = sum(1 for p in rows if p["peak"] >= CONTEXT_PRESSURE_PERCENT)
    ranked = sorted(rows, key=lambda p: (-p["peak"], p["session_id"]))
    return {"sessions_over_80": over_80, "sessions_total": total,
            "top": [dict(p, peak=round(p["peak"], 1)) for p in ranked[:_USAGE_TOP_CONTEXT]]}


def usage_summary(now: float | None = None, provider_shown: Callable | None = None,
                  hidden: Callable | None = None, stop_event=None,
                  partial: bool = False) -> dict:
    """The Usage section's aggregate over the last `USAGE_WINDOW_DAYS` days.

    Blocking; runs in a worker thread. `provider_shown(provider)` and
    `hidden(cwd)` are the rail's filters, passed in by `web.py` (D10): a
    provider the rail does not show and a workspace tagged hidden are left out
    of every row and total. They are applied here, not in the memo, so a tag
    change shows at the next aggregate. A filter that raises excludes.
    `stop_event`, optional, is checked between files; a refresh it stops
    raises `_UsageStopped` and evicts nothing.

    Keys: `by_workspace` (top 8 by this week's agent seconds), `daily` (14
    days, oldest first, sessions and agent seconds per provider, and `tokens`:
    the four counters per provider that records them, only on a day it has
    any, summing to `claude_tokens` and `codex_tokens`),
    `context_pressure` (kiro-cli v3 only: `sessions_total`
    is every in-window kiro-cli session, `sessions_over_80` those whose
    in-window peak `usagePercentage` reached 80) and its sibling
    `codex_context_pressure` (the same fields plus `estimate: True`, for Codex
    sessions that are not sub-agents: `sessions_total` is every one active in
    the window, the peak is last input tokens over the model's context window,
    so a session whose rollout holds no usable reading counts in the total only), `models` (sessions per
    model), `claude_tokens` and `codex_tokens` (each with `cache_hit_ratio` =
    cache reads / (input + cache reads + cache writes), 0 when that is 0; a
    Codex `input` excludes its cached tokens and `cache_creation` is the
    growth of `cache_write_input_tokens`, 0 when a rollout has none), `reparsed`
    (files parsed for this call rather than taken from the memo),
    `aggregate_age_s` and `partial`. Codex enters `daily`, `by_workspace`
    and `models` like the other providers; its sub-agent rollouts are
    not read at all. The lifetime token total of its sub-agent threads comes
    from the state database as `codex_subagent_tokens` (None without a usable
    database, else `threads`, `total`, `thread_spawn` and `guardian`, an upper
    bound that includes context inherited at spawn) with the reason in
    `codex_state_db_status`; neither touches `codex_tokens`. Claude Code
    sub-agent transcripts count in `claude_tokens` only; with `partial` True
    they are left out (neither listed nor parsed, nothing is evicted) and the
    result says `partial: True`.
    261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 4
    """
    from . import data, data_codex_state

    now = time.time() if now is None else now
    # A filter that raises is logged once per aggregate, not once per file.
    failed: set[str] = set()

    def shown(provider: str) -> bool:
        try:
            return provider_shown is None or bool(provider_shown(provider))
        except Exception:
            if "shown" not in failed:
                failed.add("shown")
                log.exception("Overview: the provider filter failed for usage (%s)", provider)
            return False

    def is_hidden(cwd: str) -> bool:
        try:
            return hidden is not None and bool(hidden(cwd))
        except Exception as exc:
            if "hidden" not in failed:
                failed.add("hidden")
                # The class only: `cwd` may come from another program's database or a
                # transcript, and a log is read in transcripts (D20).
                log.warning("Overview: the hidden-workspace filter failed for usage (%s)", type(exc).__name__)
            return True

    day_list, since = _window(now)
    day_set = set(day_list)
    this_week = set(day_list[USAGE_WINDOW_DAYS - 7:])
    summaries, reparsed, complete = _refresh(now, stop_event, subagents=not partial)
    if not complete:
        raise _UsageStopped()

    # A sub-agent record with no `cwd` takes its parent session's, so the
    # hidden-workspace filter still applies to it.
    parent_cwd = {s["session_id"]: s["cwd"] for s in summaries
                  if s["provider"] == _CLAUDE and not s["subagent"] and s["cwd"]}
    daily = {d: {"sessions": {}, "agent_s": {}, "tokens": {}} for d in day_list}
    workspaces: dict[str, dict] = {}
    models: dict[str, int] = {}
    # One token accumulator per provider that records tokens: Claude Code
    # (sub-agent transcripts included) and Codex.
    tokens = {p: {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0}
              for p in (_CLAUDE, _CODEX)}
    pressure: list[dict] = []
    v3_sessions = 0
    codex_pressure: list[dict] = []
    codex_sessions = 0
    for s in summaries:
        provider, cwd, sub = s["provider"], s["cwd"], s["subagent"]
        if sub and not cwd:
            cwd = parent_cwd.get(s["session_id"], "")
        if not shown(provider) or (cwd and is_hidden(cwd)):
            continue
        in_window = False
        peak = None
        for day_key, day in s["days"].items():
            if day_key not in day_set:
                continue
            if provider in tokens:
                acc = tokens[provider]
                # The same counts again per day, for the Tokens charts. Added
                # here, before the sub-agent `continue`, so a day's bar and the
                # window total always agree.
                if any(day["tokens"][key] for key in acc):
                    day_acc = daily[day_key]["tokens"].setdefault(provider, dict.fromkeys(acc, 0))
                    for key in acc:
                        day_acc[key] += day["tokens"][key]
                for key in acc:
                    acc[key] += day["tokens"][key]
            if sub:
                # Not a session of its own: its time is inside the parent's
                # turn, and its model and workspace are the parent's.
                continue
            if day["active"]:
                in_window = True
                sessions = daily[day_key]["sessions"]
                sessions[provider] = sessions.get(provider, 0) + 1
            day_peak = day["context_peak"]
            if day_peak is not None:
                peak = day_peak if peak is None else max(peak, day_peak)
            secs = day["agent_seconds"]
            if secs:
                agent = daily[day_key]["agent_s"]
                agent[provider] = agent.get(provider, 0.0) + secs
                if cwd:
                    row = workspaces.setdefault(data._normalize_path(cwd), {
                        "cwd": cwd, "name": Path(cwd).name or cwd,
                        "this_week_s": 0.0, "last_week_s": 0.0})
                    row["this_week_s" if day_key in this_week else "last_week_s"] += secs
        if sub or not in_window:
            continue
        if s["model"]:
            models[s["model"]] = models.get(s["model"], 0) + 1
        if provider == _V3 or provider == _CODEX:
            if provider == _V3:
                v3_sessions += 1
            else:
                codex_sessions += 1
            if peak is not None:
                (pressure if provider == _V3 else codex_pressure).append({
                    "session_id": s["session_id"], "cwd": cwd,
                    "name": (Path(cwd).name or cwd) if cwd else "", "peak": float(peak)})
    for day_key, count in _ide_daily(day_set, since, shown, is_hidden).items():
        daily[day_key]["sessions"][_IDE] = count

    ws_rows = [r for r in workspaces.values() if r["this_week_s"] or r["last_week_s"]]
    ws_rows.sort(key=lambda r: (-r["this_week_s"], -r["last_week_s"], r["name"].lower()))
    for r in ws_rows:
        r["this_week_s"] = round(r["this_week_s"], 1)
        r["last_week_s"] = round(r["last_week_s"], 1)
    # Codex sub-agent threads: a lifetime total per thread from the state database,
    # shown on its own line and never folded into `codex_tokens`.
    # 261002_CODEX_LIVE_STATUS_CONTEXT_PRESSURE_AND_SUBAGENT_USAGE_FROM_STATE_DB Phase 1
    if stop_event is not None and stop_event.is_set():
        raise _UsageStopped()
    subagent_tokens, state_db_status = data_codex_state.subagent_usage(since, shown, is_hidden, stop_event)
    if stop_event is not None and stop_event.is_set():
        raise _UsageStopped()

    def with_ratio(t: dict) -> dict:
        denom = t["input"] + t["cache_read"] + t["cache_creation"]
        return dict(t, cache_hit_ratio=round(t["cache_read"] / denom, 4) if denom else 0.0)

    return {
        "window_days": USAGE_WINDOW_DAYS,
        "by_workspace": ws_rows[:_USAGE_TOP_WORKSPACES],
        "daily": [{"date": d,
                   "sessions": daily[d]["sessions"],
                   "agent_s": {p: round(v, 1) for p, v in daily[d]["agent_s"].items()},
                   "tokens": daily[d]["tokens"]}
                  for d in day_list],
        "context_pressure": _pressure_block(pressure, v3_sessions),
        "codex_context_pressure": dict(_pressure_block(codex_pressure, codex_sessions), estimate=True),
        "models": [{"model": m, "sessions": n} for m, n in
                   sorted(models.items(), key=lambda kv: (-kv[1], kv[0]))[:_USAGE_TOP_MODELS]],
        "claude_tokens": with_ratio(tokens[_CLAUDE]),
        "codex_tokens": with_ratio(tokens[_CODEX]),
        "codex_subagent_tokens": subagent_tokens,
        "codex_state_db_status": state_db_status,
        "reparsed": reparsed,
        "aggregate_age_s": 0.0,
        "partial": partial,
    }


def _usage_worker_main() -> None:
    """The warm pass's child process (`_UsageWorker`). Never called in the server.

    Reads one request line from stdin, `{"schema": n, "stages": [[[path,
    provider], ...], ...]}`, and first writes `["schema", _USAGE_SCHEMA]`, its
    own summary format; when that differs from the request's `schema` it
    stops there. Otherwise it parses every file of each stage in order and
    writes one JSON line per file, `[path, mtime_ns, size, summary]` (`[path,
    null, null, null]` when the file could not be read), then `["stage", i]`
    at the end of stage `i`. It exits as soon as stdin reaches end of file:
    the parent closes it when it is done or stopping, and the pipe breaks when
    the parent dies, so the child never outlives the server.
    260924_DASHBOARD_OVERVIEW_LIVE_TAILS_PLANS_USAGE
    """
    import sys
    stdin, out = sys.stdin.buffer, sys.stdout.buffer
    request = json.loads(stdin.readline() or b"null")

    def watch_parent():
        try:
            stdin.read()
        finally:
            os._exit(0)

    threading.Thread(target=watch_parent, daemon=True).start()
    out.write(json.dumps(["schema", _USAGE_SCHEMA]).encode("utf-8") + b"\n")
    out.flush()
    if request.get("schema") != _USAGE_SCHEMA:
        out.close()
        os._exit(0)
    for index, stage in enumerate(request["stages"]):
        for path, provider in stage:
            try:
                st = os.stat(path)
                record = [path, st.st_mtime_ns, st.st_size, _parse_usage_file(Path(path), provider)]
            except Exception:
                record = [path, None, None, None]
            out.write(json.dumps(record, separators=(",", ":")).encode("utf-8") + b"\n")
        out.write(json.dumps(["stage", index]).encode("utf-8") + b"\n")
        out.flush()
    out.close()
    os._exit(0)


class _UsageWorker:
    """A child process that parses the warm pass's memo misses (`_USAGE_WORKER`).

    It only fills the memo: the pass then runs its usual `_refresh`, which
    finds the files as memo hits, parses in-thread whatever the child did not
    deliver or what changed since, and applies the eviction rules as before.
    So a child that fails to start, dies or is stopped costs time, never
    correctness: once its output ends, every `wait_stage` returns False at
    once and the pass parses in-thread. The child is started with
    `sys._base_executable` inside a venv, as `multiprocessing` does, so
    killing it kills the interpreter and not a redirector; it never imports
    `power_atlas.__main__` or `web`. It runs with `-P`, so the server's own
    working directory is not first on its `sys.path`.

    The child imports `overview` from disk, which can be newer than the code
    this server loaded. Its records are stored only after it has reported
    the same `_USAGE_SCHEMA`, and only when each summary has the keys
    `usage_summary` reads (`_SUMMARY_KEYS`).
    260924_DASHBOARD_OVERVIEW_LIVE_TAILS_PLANS_USAGE
    """

    POLL_SECONDS = 0.1
    # How much of the child's stderr is kept, from the end, for the log.
    STDERR_TAIL_BYTES = 2048

    def __init__(self, stages: list[list[tuple[str, str]]]):
        import subprocess
        import sys

        exe, env = sys.executable, dict(os.environ)
        base = getattr(sys, "_base_executable", exe)
        if sys.platform == "win32" and base and os.path.normcase(base) != os.path.normcase(exe):
            env["__PYVENV_LAUNCHER__"] = exe
            exe = base
        # The package's parent folder, for a run from a source tree that is
        # not installed.
        src = str(Path(__file__).resolve().parent.parent)
        env["PYTHONPATH"] = os.pathsep.join(p for p in (src, env.get("PYTHONPATH")) if p)
        self._init_state()
        self._proc = subprocess.Popen(
            [exe, "-P", "-c", "from power_atlas.overview import _usage_worker_main; _usage_worker_main()"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        _usage_worker_proc[0] = self._proc
        self._reader = threading.Thread(target=self._read, daemon=True,
                                        name="overview-usage-worker-reader")
        self._reader.start()
        # Drained by a thread of its own: a full stderr pipe would block the
        # child.
        self._stderr_reader = threading.Thread(target=self._drain_stderr, daemon=True,
                                               name="overview-usage-worker-stderr")
        self._stderr_reader.start()
        try:
            self._proc.stdin.write(json.dumps({"schema": _USAGE_SCHEMA, "stages": stages})
                                   .encode("utf-8") + b"\n")
            self._proc.stdin.flush()
        except OSError:
            # The child is already gone: `wait_stage` reads its end of file.
            pass

    def _init_state(self) -> None:
        """Everything but the process and its threads; a test builds a worker
        around a fake process with this."""
        import queue

        self._lines: "queue.Queue[bytes | None]" = queue.Queue()
        self._queue_empty = queue.Empty
        # Set once `wait_stage` has read the end of the child's output: the
        # end-of-file marker is queued once, and every later wait must see it.
        self._ended = False
        # Set once the child reported the schema this server expects.
        self._schema_ok = False
        # Summaries stored in the memo, for the pass's log line.
        self.delivered = 0
        self._stderr_tail = b""
        self._stderr_reader = None

    def _drain_stderr(self) -> None:
        try:
            for chunk in iter(lambda: self._proc.stderr.read(4096), b""):
                self._stderr_tail = (self._stderr_tail + chunk)[-self.STDERR_TAIL_BYTES:]
        except (OSError, ValueError):
            pass
        finally:
            try:
                self._proc.stderr.close()
            except OSError:
                pass

    def _exit_details(self) -> tuple[int | None, str]:
        """The child's exit code (None if it has not exited within a second)
        and the end of its stderr."""
        import subprocess

        try:
            code = self._proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            code = None
        if self._stderr_reader is not None:
            self._stderr_reader.join(timeout=1.0)
        return code, self._stderr_tail.decode("utf-8", errors="replace").strip()

    def _read(self) -> None:
        try:
            for line in self._proc.stdout:
                self._lines.put(line)
        except (OSError, ValueError):
            pass
        finally:
            self._lines.put(None)
            # Closed here, by the thread that reads it: a close from another
            # thread would wait on this one's read.
            try:
                self._proc.stdout.close()
            except OSError:
                pass

    def wait_stage(self, index: int, stop_event) -> bool:
        """Store the child's summaries in the memo until it reports the end of
        stage `index`. False when `stop_event` is set first, or the child's
        output ended without reaching it; once it has ended, every later call
        returns False at once. A child that reports another `_USAGE_SCHEMA`
        is treated as ended.

        Raises `_UsageWorkerStalled`, after killing the child, when no record
        (of any kind) has arrived for `CHILD_STALL_SECONDS`; the clock starts
        at each call, so time the pass spent parsing in-thread between two
        stages is not held against the child. The summaries stored before
        that stay in the memo.
        261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 4"""
        last_record = _stall_clock()
        while True:
            if self._ended:
                return False
            if stop_event is not None and stop_event.is_set():
                return False
            try:
                line = self._lines.get(timeout=self.POLL_SECONDS)
            except self._queue_empty:
                if _stall_clock() - last_record >= CHILD_STALL_SECONDS:
                    self._ended = True
                    try:
                        self._proc.kill()
                    except OSError:
                        pass
                    raise _UsageWorkerStalled()
                continue
            last_record = _stall_clock()
            if line is None:
                self._ended = True
                code, stderr = self._exit_details()
                log.warning("Overview: the usage worker ended early (exit code %s); "
                            "parsing in the server%s", code,
                            f"; its stderr ends: {stderr}" if stderr else "")
                return False
            try:
                record = json.loads(line)
                if record[0] == "schema":
                    self._schema_ok = record[1] == _USAGE_SCHEMA
                    if not self._schema_ok:
                        self._ended = True
                        log.warning("Overview: the usage worker reported summary format %r, "
                                    "not %r (the code on disk changed); parsing in the server",
                                    record[1], _USAGE_SCHEMA)
                        return False
                    continue
                if not self._schema_ok:
                    continue
                if record[0] == "stage":
                    if record[1] == index:
                        return True
                    continue
                path, mtime_ns, size, summary = record
            except (ValueError, TypeError, IndexError, KeyError):
                continue
            if (isinstance(path, str) and isinstance(summary, dict)
                    and _SUMMARY_KEYS <= summary.keys()
                    and isinstance(summary["days"], dict)
                    and isinstance(mtime_ns, int) and isinstance(size, int)):
                with _usage_memo_lock:
                    _usage_memo[path] = (mtime_ns, size, summary)
                    if summary["provider"] == _CODEX:
                        _codex_parsed_at[path] = _debounce_clock()
                self.delivered += 1

    def close(self) -> None:
        """Close the child's stdin, which ends it; kill it if it is still
        running, and reap it. Idempotent."""
        proc = self._proc
        try:
            proc.stdin.close()
        except OSError:
            pass
        if proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass
        proc.wait()
        self._reader.join(timeout=1.0)
        if self._stderr_reader is not None:
            self._stderr_reader.join(timeout=1.0)
        if _usage_worker_proc[0] is proc:
            _usage_worker_proc[0] = None


def _start_usage_worker(now: float):
    """A `_UsageWorker` over the in-window files the memo does not hold, main
    transcripts as stage 0 and sub-agent transcripts as stage 1; None when the
    worker is off, nothing needs parsing, or the child could not be started
    (the pass then parses in-thread, as it always did)."""
    if not _USAGE_WORKER:
        return None
    try:
        _days, since = _window(now)
        stages: list[list[tuple[str, str]]] = [[], []]
        for path, provider, st in _usage_files(since, stop_event=_usage_stop):
            key = str(path)
            with _usage_memo_lock:
                hit = _usage_memo.get(key)
            if hit is None or hit[0] != st.st_mtime_ns or hit[1] != st.st_size:
                if _codex_debounced(key, provider, hit):
                    continue   # the child would otherwise re-parse what the memo keeps
                stages[provider == _CLAUDE_SUB].append((key, provider))
        if not stages[0] and not stages[1]:
            return None
        return _UsageWorker(stages)
    except Exception:
        log.warning("Overview: could not start the usage worker; parsing in the server",
                    exc_info=True)
        return None


def usage_stop_event() -> threading.Event:
    """The shutdown event every usage pass checks (`_usage_stop`). `web.lifespan`
    clears it at startup and sets it on shutdown."""
    return _usage_stop


def warm_usage(stop_event) -> None:
    """A warm pass: parse every in-window transcript into the memo, so the
    next Overview request finds it filled (D12).

    Runs in its own worker thread: after startup, started by `web.lifespan`,
    and from `cold` or `error`, started by `usage_payload`. Two stages: the
    main transcripts first, then `_usage_stage1` is set so the route can
    return a partial aggregate, then every file, sub-agent transcripts
    included (the main ones are memo hits by then). The parsing itself is done
    ahead of each `_refresh` by a child process (`_UsageWorker`), which fills
    the memo without holding this process's GIL; the `_refresh` calls then
    find memo hits, and parse in-thread only what the child did not deliver.
    `stop_event`, a `threading.Event`, is checked between files (every 0.1 s
    while the child works); once set the pass kills the child and returns
    after the file in hand, leaves the state `cold` and evicts nothing. One
    file that fails is skipped (`_refresh`); a failure of the pass as a whole
    sets `error`. A child whose output ends early (it crashed, was killed, or
    could not start its interpreter) is not waited for again: the pass parses
    the rest in-thread and still ends `ready`. A child that is alive but sends
    nothing for `CHILD_STALL_SECONDS` is killed and the pass ends `error` (an
    in-thread parse would meet the same stuck file); what the child delivered
    stays in the memo, and the page's next 60 s poll starts a new pass.
    261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 4

    Each pass logs one INFO line: its final state, how long each stage took
    and how many files the child and this thread parsed.
    """
    _set_usage_state("warming")
    state = "error"
    worker = None
    started = time.monotonic()
    stage1_s = stage2_s = None
    in_thread = 0
    try:
        worker = _start_usage_worker(time.time())
        if worker is not None:
            worker.wait_stage(0, stop_event)
        _summaries, reparsed, complete = _refresh(time.time(), stop_event, subagents=False)
        in_thread += reparsed
        if complete:
            stage1_s = time.monotonic() - started
            _usage_stage1[0] = True
            if worker is not None:
                worker.wait_stage(1, stop_event)
            _summaries, reparsed, complete = _refresh(time.time(), stop_event)
            in_thread += reparsed
            if complete:
                stage2_s = time.monotonic() - started - stage1_s
        state = "ready" if complete else "cold"
    except _UsageWorkerStalled:
        log.warning("Overview: the usage worker sent nothing for %s s and was killed; "
                    "the pass ends in error and is retried", CHILD_STALL_SECONDS)
    except Exception:
        log.exception("Overview: the usage warm pass failed")
    finally:
        # The state first: reaping the child must never hold it at `warming`.
        if state != "cold":
            _usage_stage1[0] = False
        _set_usage_state(state)
        if worker is not None:
            try:
                worker.close()
            except Exception:
                log.exception("Overview: could not stop the usage worker")
        log.info("Overview: usage pass ended %s after %.1f s (stage 1 %s, stage 2 %s); "
                 "%d files parsed by the worker, %d in the server",
                 state, time.monotonic() - started,
                 "%.1f s" % stage1_s if stage1_s is not None else "not finished",
                 "%.1f s" % stage2_s if stage2_s is not None else "not finished",
                 getattr(worker, "delivered", 0), in_thread)


def _start_background_pass() -> bool:
    """Start one warm pass in a daemon thread, unless one is already running
    or shutdown has begun. True when a pass is running. The state turns
    `warming` here, before the thread starts, so a request in between cannot
    start a second one. 260924_DASHBOARD_OVERVIEW_LIVE_TAILS_PLANS_USAGE"""
    with _usage_bg_lock:
        if _usage_stop.is_set():
            return False
        if usage_state() == "warming":
            return True
        _set_usage_state("warming")
        thread = threading.Thread(target=warm_usage, args=(_usage_stop,), daemon=True,
                                  name="overview-usage-pass")
        _usage_bg[0] = thread
        thread.start()
        return True


def usage_payload(filters: Callable) -> tuple[dict | None, str]:
    """`(usage, usage_state)` for the summary route. Blocking; runs off the loop.

    - `warming` (a warm pass is running): `(None, "warming")` during its
      stage 1; once stage 1 is done, a partial aggregate (`partial: True`, no
      Claude Code sub-agent transcripts) computed now from the memo and never
      cached, with `"warming"`, so the page keeps re-fetching until the
      complete aggregate is `ready`. Held under the same lock as a complete
      computation.
    - `cold` or `error` (no pass finished, or the last one failed as a whole):
      one warm pass is started in the background and the call returns at once,
      so the route never makes plans wait for a full parse. `cold` gives
      `(None, "warming")`, and the page re-fetches within seconds; `error`
      gives `(None, "error")`, so a pass that keeps failing is reported, and
      is retried at the page's next 60 s poll rather than every few seconds.
    - `ready`: the aggregate, reused for `USAGE_REUSE_SECONDS` (D23) with
      `reparsed` 0 and its age in `aggregate_age_s`, as long as the rail's
      filters are unchanged; otherwise computed now from the memo, parsing
      only files that changed since. The lock makes that single-flight:
      concurrent requests share one computation.

    `filters()` returns the rail's `(providers, hidden)` and is called on
    every `ready` request, so it should be cheap (the route passes a cached
    one). The reuse key is `(providers, hidden.key)`, `hidden.key` being a
    hashable form of the hidden set (the object itself when it has none). A
    computation that fails gives `(None, "error")`; one stopped by shutdown
    caches nothing and gives `(None, "cold")`.
    """
    state = usage_state()
    if state == "warming" and not _usage_stage1[0]:
        return None, "warming"
    if state in ("cold", "error"):
        started = _start_background_pass()
        if state == "error":
            return None, "error"
        return None, "warming" if started else "cold"
    try:
        providers, hidden = filters()
        key = (frozenset(providers), getattr(hidden, "key", hidden))
    except Exception:
        log.exception("Overview: could not read the rail's filters for usage")
        return None, "error"
    if state == "warming":
        with _usage_compute_lock:
            try:
                usage = usage_summary(provider_shown=lambda p: p in providers, hidden=hidden,
                                      stop_event=_usage_stop, partial=True)
            except _UsageStopped:
                return None, "cold"
            except Exception:
                log.exception("Overview: could not compute the partial usage")
                return None, "warming"
        return usage, "warming"
    with _usage_compute_lock:
        at, cached, cached_key = _usage_cache
        age = time.monotonic() - at
        if cached is not None and cached_key == key and age < USAGE_REUSE_SECONDS:
            return dict(cached, reparsed=0, aggregate_age_s=round(age, 1)), "ready"
        try:
            usage = usage_summary(provider_shown=lambda p: p in providers, hidden=hidden,
                                  stop_event=_usage_stop)
        except _UsageStopped:
            return None, "cold"
        except Exception:
            log.exception("Overview: could not compute usage")
            return None, "error"
        _usage_cache[:] = [time.monotonic(), usage, key]
    return usage, "ready"
