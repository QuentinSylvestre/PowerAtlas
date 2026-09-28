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
# sub-agent transcripts, which only add Claude tokens and tool calls, and the
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
# - Tool calls, failures, Claude tokens and the kiro-cli context peak are
#   counted on the local day of their own record, so a long session modified
#   inside the window contributes only its in-window days.
# - Claude tokens are counted once per `message.id`: Claude Code writes one
#   record per content block and repeats the message's `usage` on each
#   (measured 2026-09-25: 975 of 1,848 assistant records in one file were such
#   repeats, none with a different `usage`).
# - Claude Code sub-agent transcripts,
#   `<project>/<session-uuid>/subagents/agent-<hex>.jsonl`, add their tokens
#   and tool calls to the totals; they are not sessions of their own and add
#   no agent time, model or workspace row (their time is inside the parent
#   turn that waited for them). Measured 2026-09-25: 574 in-window files,
#   holding about 3.09 B cache-read tokens and 22,345 tool calls that the
#   session files alone missed (see docs/KNOWLEDGE.md).
# - Kiro IDE contributes sessions per day only, from `dateCreated` in its
#   `sessions.json` files (D25); it records no durations and no tools.

USAGE_WINDOW_DAYS = 14
USAGE_MAX_LINE_BYTES = 8 * 1024 * 1024
CLAUDE_TURN_CAP_SECONDS = 30 * 60
USAGE_REUSE_SECONDS = 30.0
CONTEXT_PRESSURE_PERCENT = 80.0
_USAGE_TOP_WORKSPACES = 8
_USAGE_TOP_TOOLS = 8
_USAGE_TOP_FAILING = 5
_USAGE_FAILING_MIN_CALLS = 3
_USAGE_TOP_CONTEXT = 5
_USAGE_TOP_MODELS = 8
# A kiro-cli `session.json` or a Kiro IDE `sessions.json` larger than this is
# not read; real ones are a few KiB.
_USAGE_SIDE_FILE_MAX = 8 * 1024 * 1024
_V3 = "kiro-cli-v3"
_CLAUDE = "claude-code"
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


class _UsageStopped(Exception):
    """A request's refresh stopped at shutdown; nothing is cached."""


def _usage_roots() -> tuple[Path, Path, Path]:
    """The three stores' roots: kiro-cli v3 sessions, Claude Code projects and
    the Kiro IDE workspace sessions. Read from their modules at call time, so a
    test that points a constant at a fixture is honoured; one seam, so the test
    suite can point every usage read at an empty folder without touching the
    constants other readers use."""
    from . import data_claude, data_kiro_ide, status_classifier
    return (Path(status_classifier._V3_SESSIONS_ROOT), Path(data_claude.CLAUDE_PROJECTS_DIR),
            Path(data_kiro_ide.SESSIONS_DIR))


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
    if not isinstance(ts, str) or not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _local_day(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).date().isoformat()


def _new_day() -> dict:
    return {"active": False, "agent_seconds": 0.0, "tools": {}, "context_peak": None,
            "tokens": {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0}}


def _tool_slot(day: dict, name: str) -> dict:
    return day["tools"].setdefault(name, {"calls": 0, "failed": 0})


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
    calls: dict[str, tuple[str, str]] = {}   # toolCallId -> (tool name, day)
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
                day_key = _local_day(epoch)
                day = days.setdefault(day_key, _new_day())
                day["active"] = True
                if ptype == "usage_summary":
                    ms = payload.get("elapsedTime")
                    if isinstance(ms, (int, float)) and not isinstance(ms, bool) and ms > 0:
                        day["agent_seconds"] += ms / 1000.0
                elif ptype == "tool_call":
                    name = payload.get("toolName")
                    if isinstance(name, str) and name:
                        _tool_slot(day, name)["calls"] += 1
                        tcid = payload.get("toolCallId")
                        if isinstance(tcid, str) and tcid:
                            calls[tcid] = (name, day_key)
                elif ptype == "tool_result":
                    tcid = payload.get("toolCallId")
                    hit = calls.pop(tcid, None) if isinstance(tcid, str) else None
                    if hit is not None and payload.get("success") is False:
                        _tool_slot(days[hit[1]], hit[0])["failed"] += 1
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
    calls: dict[str, tuple[str, str]] = {}   # tool_use id -> (tool name, day)
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
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "tool_result":
                                tid = block.get("tool_use_id")
                                hit = calls.pop(tid, None) if isinstance(tid, str) else None
                                if hit is not None and block.get("is_error") is True:
                                    _tool_slot(days[hit[1]], hit[0])["failed"] += 1
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
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            name = block.get("name")
                            if isinstance(name, str) and name:
                                _tool_slot(day, name)["calls"] += 1
                                if isinstance(block.get("id"), str):
                                    calls[block["id"]] = (name, day_key)
            except Exception:
                continue
    close_turn()
    model = max(sorted(models), key=lambda m: models[m]) if models else None
    # A sub-agent transcript is filed under its parent session's folder.
    session_id = path.parent.parent.name if subagent else path.stem
    return {"provider": _CLAUDE, "session_id": session_id, "cwd": cwd, "model": model,
            "subagent": subagent, "days": days}


def _parse_usage_file(path: Path, provider: str) -> dict:
    """Parse one transcript into its summary. Module-level and called by name
    so a test can count the parses."""
    if provider == _V3:
        return _parse_v3_usage(path)
    return _parse_claude_usage(path, subagent=provider == _CLAUDE_SUB)


def _summarize(path: Path, provider: str, st) -> tuple[dict, bool]:
    """The file's summary and whether it was parsed now (False: a memo hit)."""
    key = str(path)
    with _usage_memo_lock:
        hit = _usage_memo.get(key)
    if hit is not None and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
        return hit[2], False
    summary = _parse_usage_file(path, provider)
    with _usage_memo_lock:
        _usage_memo[key] = (st.st_mtime_ns, st.st_size, summary)
    return summary, True


def summarize_file(path, provider: str) -> dict:
    """One transcript's usage summary, memoised per path on `(mtime_ns, size)`.

    `{"provider", "session_id", "cwd", "model", "subagent", "days"}`, where
    `days` maps a local `YYYY-MM-DD` to `{"active", "agent_seconds", "tools":
    {name: {"calls", "failed"}}, "tokens": {"input", "output", "cache_read",
    "cache_creation"}, "context_peak"}`. `model` is the v3 `modelId` or the
    most frequent Claude `message.model`; `context_peak` the v3 maximum
    `usagePercentage` (0-100) recorded that day, else None. `subagent` is True
    for a Claude Code sub-agent transcript (`provider` `_CLAUDE_SUB`), whose
    `session_id` is its parent session's. Lines over `USAGE_MAX_LINE_BYTES` are skipped, and one
    unreadable line skips that line only. The v3 `session.json` fields (cwd,
    model) are memoised under `messages.jsonl`'s key; they do not change during
    a session.
    """
    path = Path(path)
    return _summarize(path, provider, path.stat())[0]


def _window(now: float) -> tuple[list[str], float]:
    """The window's local days, oldest first, and the epoch of its first
    midnight."""
    today = datetime.fromtimestamp(now).date()
    first = today.fromordinal(today.toordinal() - (USAGE_WINDOW_DAYS - 1))
    days = [first.fromordinal(first.toordinal() + i).isoformat()
            for i in range(USAGE_WINDOW_DAYS)]
    start = datetime(first.year, first.month, first.day).timestamp()
    return days, start


def _usage_files(since: float, subagents: bool = True) -> list[tuple[Path, str, object]]:
    """`(path, provider, stat)` for each transcript modified at or after
    `since`: kiro-cli v3 `<root>/<hash>/sess_*/messages.jsonl`, Claude Code
    `<root>/<project>/<uuid>.jsonl`, and, unless `subagents` is False, Claude
    Code sub-agent transcripts `<root>/<project>/<uuid>/subagents/*.jsonl`
    (tagged `_CLAUDE_SUB`)."""
    from . import data_claude

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
    for path, provider, st in _usage_files(since, subagents):
        if stop_event is not None and stop_event.is_set():
            complete = False
            break
        keep.add(str(path))
        try:
            summary, parsed = _summarize(path, provider, st)
        except Exception:
            log.warning("Overview: could not summarise transcript %s", path)
            continue
        reparsed += parsed
        summaries.append(summary)
    if complete and subagents:
        with _usage_memo_lock:
            for key in [k for k in _usage_memo if k not in keep]:
                del _usage_memo[key]
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
    days, oldest first, sessions and agent seconds per provider), `tools`
    (`top` by calls over the window with `fail_rate`, `failing` this week with
    at least 3 calls), `context_pressure` (kiro-cli v3 only: `sessions_total`
    is every in-window kiro-cli session, `sessions_over_80` those whose
    in-window peak `usagePercentage` reached 80), `models` (sessions per
    model), `claude_tokens` (with `cache_hit_ratio` = cache reads / (input +
    cache reads + cache writes), 0 when that is 0), `reparsed` (files parsed
    for this call rather than taken from the memo), `aggregate_age_s` and
    `partial`. Claude Code sub-agent transcripts count in `tools` and
    `claude_tokens` only; with `partial` True they are left out (neither listed
    nor parsed, nothing is evicted) and the result says `partial: True`.
    """
    from . import data

    now = time.time() if now is None else now

    def shown(provider: str) -> bool:
        try:
            return provider_shown is None or bool(provider_shown(provider))
        except Exception:
            return False

    def is_hidden(cwd: str) -> bool:
        try:
            return hidden is not None and bool(hidden(cwd))
        except Exception:
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
    daily = {d: {"sessions": {}, "agent_s": {}} for d in day_list}
    workspaces: dict[str, dict] = {}
    tools: dict[str, list[int]] = {}
    tools_week: dict[str, list[int]] = {}
    models: dict[str, int] = {}
    tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0}
    pressure: list[dict] = []
    v3_sessions = 0
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
            for name, t in day["tools"].items():
                for bucket in ((tools, tools_week) if day_key in this_week else (tools,)):
                    slot = bucket.setdefault(name, [0, 0])
                    slot[0] += t["calls"]
                    slot[1] += t["failed"]
            if provider == _CLAUDE:
                for key in tokens:
                    tokens[key] += day["tokens"][key]
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
        if provider == _V3:
            v3_sessions += 1
            if peak is not None:
                pressure.append({"session_id": s["session_id"], "cwd": cwd,
                                 "name": (Path(cwd).name or cwd) if cwd else "",
                                 "peak": float(peak)})
    for day_key, count in _ide_daily(day_set, since, shown, is_hidden).items():
        daily[day_key]["sessions"][_IDE] = count

    ws_rows = [r for r in workspaces.values() if r["this_week_s"] or r["last_week_s"]]
    ws_rows.sort(key=lambda r: (-r["this_week_s"], -r["last_week_s"], r["name"].lower()))
    for r in ws_rows:
        r["this_week_s"] = round(r["this_week_s"], 1)
        r["last_week_s"] = round(r["last_week_s"], 1)
    top_tools = sorted(tools.items(), key=lambda kv: (-kv[1][0], kv[0]))[:_USAGE_TOP_TOOLS]
    failing = [(n, c, f) for n, (c, f) in tools_week.items()
               if c >= _USAGE_FAILING_MIN_CALLS and f > 0]
    failing.sort(key=lambda t: (-t[2], -t[2] / t[1], t[0]))
    # Compared and sorted on the raw peak; rounded for display only.
    over_80 = sum(1 for p in pressure if p["peak"] >= CONTEXT_PRESSURE_PERCENT)
    pressure.sort(key=lambda p: (-p["peak"], p["session_id"]))
    top_pressure = [dict(p, peak=round(p["peak"], 1)) for p in pressure[:_USAGE_TOP_CONTEXT]]
    denom = tokens["input"] + tokens["cache_read"] + tokens["cache_creation"]
    return {
        "window_days": USAGE_WINDOW_DAYS,
        "by_workspace": ws_rows[:_USAGE_TOP_WORKSPACES],
        "daily": [{"date": d,
                   "sessions": daily[d]["sessions"],
                   "agent_s": {p: round(v, 1) for p, v in daily[d]["agent_s"].items()}}
                  for d in day_list],
        "tools": {
            "top": [{"name": n, "calls": c, "fail_rate": round(f / c, 4) if c else 0.0}
                    for n, (c, f) in top_tools],
            "failing": [{"name": n, "failed": f, "calls": c}
                        for n, c, f in failing[:_USAGE_TOP_FAILING]],
        },
        "context_pressure": {
            "sessions_over_80": over_80,
            "sessions_total": v3_sessions,
            "top": top_pressure,
        },
        "models": [{"model": m, "sessions": n} for m, n in
                   sorted(models.items(), key=lambda kv: (-kv[1], kv[0]))[:_USAGE_TOP_MODELS]],
        "claude_tokens": dict(tokens, cache_hit_ratio=(round(tokens["cache_read"] / denom, 4)
                                                       if denom else 0.0)),
        "reparsed": reparsed,
        "aggregate_age_s": 0.0,
        "partial": partial,
    }


def _usage_worker_main() -> None:
    """The warm pass's child process (`_UsageWorker`). Never called in the server.

    Reads one request line from stdin, `{"stages": [[[path, provider], ...],
    ...]}`, parses every file of each stage in order and writes one JSON line
    per file, `[path, mtime_ns, size, summary]` (`[path, null, null, null]`
    when the file could not be read), then `["stage", i]` at the end of stage
    `i`. It exits as soon as stdin reaches end of file: the parent closes it
    when it is done or stopping, and the pipe breaks when the parent dies, so
    the child never outlives the server. 260924_DASHBOARD_OVERVIEW_LIVE_TAILS_PLANS_USAGE
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
    correctness. The child is started with `sys._base_executable` inside a
    venv, as `multiprocessing` does, so killing it kills the interpreter and
    not a redirector; it never imports `power_atlas.__main__` or `web`.
    260924_DASHBOARD_OVERVIEW_LIVE_TAILS_PLANS_USAGE
    """

    POLL_SECONDS = 0.1

    def __init__(self, stages: list[list[tuple[str, str]]]):
        import queue
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
        self._proc = subprocess.Popen(
            [exe, "-c", "from power_atlas.overview import _usage_worker_main; _usage_worker_main()"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        _usage_worker_proc[0] = self._proc
        self._lines: "queue.Queue[bytes | None]" = queue.Queue()
        self._queue_empty = queue.Empty
        self._reader = threading.Thread(target=self._read, daemon=True,
                                        name="overview-usage-worker-reader")
        self._reader.start()
        try:
            self._proc.stdin.write(json.dumps({"stages": stages}).encode("utf-8") + b"\n")
            self._proc.stdin.flush()
        except OSError:
            # The child is already gone: `wait_stage` reads its end of file.
            pass

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
        stage `index`. False when `stop_event` is set first or the child ended
        without reaching it."""
        while True:
            if stop_event is not None and stop_event.is_set():
                return False
            try:
                line = self._lines.get(timeout=self.POLL_SECONDS)
            except self._queue_empty:
                continue
            if line is None:
                log.warning("Overview: the usage worker ended early (exit code %s); "
                            "parsing in the server", self._proc.poll())
                return False
            try:
                record = json.loads(line)
                if record[0] == "stage":
                    if record[1] == index:
                        return True
                    continue
                path, mtime_ns, size, summary = record
            except (ValueError, TypeError, IndexError):
                continue
            if isinstance(summary, dict) and isinstance(mtime_ns, int) and isinstance(size, int):
                with _usage_memo_lock:
                    _usage_memo[path] = (mtime_ns, size, summary)

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
        for path, provider, st in _usage_files(since):
            key = str(path)
            with _usage_memo_lock:
                hit = _usage_memo.get(key)
            if hit is None or hit[0] != st.st_mtime_ns or hit[1] != st.st_size:
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
    sets `error`.
    """
    _set_usage_state("warming")
    state = "error"
    worker = None
    try:
        worker = _start_usage_worker(time.time())
        if worker is not None:
            worker.wait_stage(0, stop_event)
        _summaries, _reparsed, complete = _refresh(time.time(), stop_event, subagents=False)
        if complete:
            _usage_stage1[0] = True
            if worker is not None:
                worker.wait_stage(1, stop_event)
            _summaries, _reparsed, complete = _refresh(time.time(), stop_event)
        state = "ready" if complete else "cold"
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
