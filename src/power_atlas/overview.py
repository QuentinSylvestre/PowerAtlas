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
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, NamedTuple

log = logging.getLogger(__name__)

# --- Active plans ------------------------------------------------------------

# A plan file larger than this is skipped unread (D21). The largest plan in the
# user's workspaces is well under 100 KiB; a megabyte file in `plans/` is not a
# plan this list can summarise.
PLAN_MAX_BYTES = 1024 * 1024
# An In Progress plan whose file has not changed for longer than this is badged
# `stale` (D7).
PLAN_STALE_SECONDS = 7 * 24 * 3600
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


def _read_plan_file(path: Path) -> str | None:
    """The file's text, or None when it is larger than `PLAN_MAX_BYTES`.

    Invalid UTF-8 is replaced rather than raised (D21). The read itself is
    bounded, not only the `stat` before it: an agent appending to the file
    between the two cannot make this read more than the cap.

    Module-level and called by name so a test can count the reads.
    """
    with path.open("rb") as fh:
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
    against the server's own working directory."""
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


def scan_plans(workspaces, deadline_s: float = 2.0, now: float | None = None) -> list[dict]:
    """Active plans across `workspaces`, a list of `(cwd, name)` pairs.

    Blocking; runs in a worker thread. The caller has already applied the
    rail's filters (hidden tag, disabled providers) and de-duplicated the cwds.

    Robust reads (D21): cwds that are UNC or not absolute are skipped, and so
    is a cwd whose `plans` folder resolves (a junction or symlink) to a UNC
    target. Nothing more is read once `deadline_s` has passed; the deadline is
    checked before each cwd and before each file, and the first file of the
    first cwd is always read, as in `web._acp_exists_flags`. A single stalled
    call cannot be interrupted, so the bound is one stalled call, not zero. A
    mapped or `subst` drive letter that points at a dead share passes every
    check here; no network workspace exists today, so that residual risk is
    recorded rather than handled. Only top-level `plans/*.md` is read, which
    leaves `plans/done/` out; anything that is not a regular file (a symlink
    included) is skipped; files over `PLAN_MAX_BYTES` are skipped, and the
    read itself is capped too; one unreadable file skips that file only.

    Parsed files are memoised per path on `(mtime_ns, size)` (D22), so a
    repeat scan of unchanged files costs one `stat` each. Paths not seen by a
    scan that ran to completion are evicted.

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
                    text = _read_plan_file(path)
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
            out.append({
                "cwd": cwd,
                "workspace": name,
                "file": path.name,
                "title": _plan_title(path.name),
                "state": parsed["state"],
                "detail": parsed["detail"],
                "mtime": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
                "stale": (parsed["state"] == "In Progress"
                          and now - mtime > PLAN_STALE_SECONDS),
                "ready_to_close": parsed["state"] == "Complete",
                "progress": parsed["progress"],
                "tracker": parsed["tracker"],
                "_mtime": mtime,
            })
    if complete:
        with _plan_memo_lock:
            for key in [k for k in _plan_memo if k not in seen]:
                del _plan_memo[key]
    out.sort(key=lambda p: p["_mtime"], reverse=True)
    out.sort(key=lambda p: 0 if p["state"] == "In Progress" else 1)
    for plan in out:
        del plan["_mtime"]
    return out


# --- Live now ------------------------------------------------------------------
#
# SC-4/SC-5: one tile per live session, with the rail's own liveness rule and
# filters (D8), and the last few events of its transcript (D13). The route in
# `web.py` polls this every ~2 s while the Overview is showing, from a worker
# thread. 260924_DASHBOARD_OVERVIEW_LIVE_TAILS_PLANS_USAGE

# Session ids taken from process command lines are checked against this before
# any path is built from them (D21). The same pattern `/api/session-transcript`
# validates with: kiro-cli-v3 ids carry a `sess_` prefix, Claude ids are bare.
SESSION_ID_RE = re.compile(
    r"(?:sess_)?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

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


def _read_tail(path: Path, start: int, length: int) -> bytes:
    """`length` bytes of `path` from `start`. Module-level and called by name
    so a test can count the reads."""
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


def _line_events(line: bytes, provider: str) -> list[dict]:
    """One transcript line as tile events; `[]` for a line that is not one.

    Any failure skips this line only, never the tile: a line is data from a
    file an agent writes, and a shape the readers do not expect (a `null`
    `message`, JSON nested deeper than the recursion limit) must not cost the
    other lines. Not logged: the poll runs every 2 s.
    260924_DASHBOARD_OVERVIEW_LIVE_TAILS_PLANS_USAGE
    """
    try:
        obj = json.loads(line)
        if not isinstance(obj, dict):
            return []
        if provider == "claude-code":
            return _claude_events(obj)
        return _v3_events(obj)
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
        lines = (_read_tail(path, start, end - start) + carry).split(b"\n")
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
    only, never content. A line that cannot be read as an event is skipped and
    the result is still memoised (`_line_events`).
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
    try:
        events = _parse_tail(path, st.st_size, provider, n)
    except (OSError, ValueError):
        log.warning("Overview: could not read transcript tail %s", key)
        return []
    with _tail_memo_lock:
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


def _epoch_of(iso) -> float:
    if not iso:
        return 0.0
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, OverflowError, OSError):
        return 0.0


def live_sessions(held: dict[str, str], snapshot, filter_: str, deps: LiveDeps,
                  originals: dict[str, str]) -> list[dict]:
    """The Live now tiles. Blocking; runs in a worker thread.

    Live is the rail's rule (D8): a session this PowerAtlas holds, or one the
    rail's `_session_is_live` marks live, less hidden workspaces and providers
    the rail does not show. Held sessions with no store record (a new session,
    or one in the agent's own folder) are included, titled "New session".

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
    from . import data, data_kiro_v3

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

    def is_live(session, provider: str) -> bool:
        try:
            return bool(deps.session_is_live(snapshot, session, provider))
        except Exception:
            return False

    # (provider, sid) -> (session, cwd, held, live)
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
        cands[(_HELD_PROVIDER, sid)] = (session, cwd, True, is_live(session, _HELD_PROVIDER))

    if not only_held:
        # (b) Session ids on a process command line or in a sidecar. Checked
        # against SESSION_ID_RE before anything is looked up by them.
        for provider, sid, norm in snapshot.live_sids():
            if (provider, sid) in cands or not SESSION_ID_RE.fullmatch(sid or ""):
                continue
            original = originals.get(norm)
            if not original or not shown(provider, original):
                continue
            session = next((s for s in sessions_in(provider, original)
                            if s.session_id == sid), None)
            if session is not None and is_live(session, provider):
                cands[(provider, sid)] = (session, original, False, True)
        # (c) Sessions in a cwd a provider process runs in, written recently.
        for provider, norm in snapshot.live_cwd_pairs():
            original = originals.get(norm)
            if not original or not shown(provider, original):
                continue
            for session in sessions_in(provider, original):
                key = (provider, session.session_id)
                if key not in cands and is_live(session, provider):
                    cands[key] = (session, original, False, True)

    now = time.time()
    rows = []
    for (provider, sid), (session, cwd, is_held, live) in cands.items():
        path = None
        st = None
        activity = 0.0
        try:
            path = _transcript_path(sid, provider, cwd)
            if path is not None:
                st = path.stat()
                activity = st.st_mtime
        except (OSError, ValueError):
            path = st = None
        if not activity:
            activity = _epoch_of(session.updated_at)
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
