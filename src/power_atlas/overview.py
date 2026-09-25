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

import logging
import os
import re
import stat as stat_mod
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

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

_STATUS_RE = re.compile(r"^> \*\*Status\*\*:\s*(.+)$", re.MULTILINE)
_COMMENT_RE = re.compile(r"\s*<!--.*?-->\s*$")
# The state ends at the first " — " or " - "; everything after is the detail.
_STATE_SPLIT_RE = re.compile(r" (?:—|-) ")
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


def _read_plan_file(path: Path) -> str:
    """The file's text. Invalid UTF-8 is replaced rather than raised (D21).

    Module-level and called by name so a test can count the reads.
    """
    return path.read_text(encoding="utf-8", errors="replace")


def _trim(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _tracker_status(raw: str) -> str:
    """Normalise a tracker Status cell to done | in_progress | pending | other."""
    s = raw.strip().strip("*_~`").strip().lower()
    if s.startswith(("done", "complete")):
        return "done"
    if s.startswith(("in progress", "in-progress")):
        return "in_progress"
    if s.startswith("pending") or s == "":
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
    status = _COMMENT_RE.sub("", m.group(1)).strip()
    parts = _STATE_SPLIT_RE.split(status, maxsplit=1)
    state = parts[0].strip()
    if state not in _PLAN_STATES:
        return None
    detail = parts[1].strip() if len(parts) > 1 else ""
    tracker = _tracker_rows(text.splitlines())
    if tracker:
        current = next((r for r in tracker if r["status"] == "in_progress"), None)
        progress: dict | None = {
            "done": sum(1 for r in tracker if r["status"] == "done"),
            "total": len(tracker),
            "current": ({"id": current["id"], "name": current["name"]}
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


def scan_plans(workspaces, deadline_s: float = 2.0, now: float | None = None) -> list[dict]:
    """Active plans across `workspaces`, a list of `(cwd, name)` pairs.

    Blocking; runs in a worker thread. The caller has already applied the
    rail's filters (hidden tag, disabled providers) and de-duplicated the cwds.

    Robust reads (D21): cwds that are UNC or not absolute are skipped; no
    further cwd is scanned once `deadline_s` has passed (the first is always
    scanned, as in `web._acp_exists_flags`; a single stalled call cannot be
    interrupted, so the bound is one stalled cwd, not zero); only top-level
    `plans/*.md` is read, which leaves `plans/done/` out; files over
    `PLAN_MAX_BYTES` are skipped; one unreadable file skips that file only.

    Parsed files are memoised per path on `(mtime_ns, size)` (D22), so a
    repeat scan of unchanged files costs one `stat` each. Paths not seen by a
    scan that ran to completion are evicted.
    """
    now = time.time() if now is None else now
    deadline = time.monotonic() + deadline_s
    out: list[dict] = []
    seen: set[str] = set()
    complete = True
    for index, (cwd, name) in enumerate(workspaces):
        if index and time.monotonic() >= deadline:
            complete = False
            break
        if _skip_cwd(cwd):
            continue
        plans_dir = Path(cwd) / "plans"
        try:
            if not plans_dir.is_dir():
                continue
            files = sorted(plans_dir.glob("*.md"))
        except OSError:
            continue
        for path in files:
            if path.name.upper() in _PLAN_SKIP_NAMES:
                continue
            key = str(path)
            seen.add(key)
            try:
                st = path.stat()
                if not stat_mod.S_ISREG(st.st_mode) or st.st_size > PLAN_MAX_BYTES:
                    continue
                with _plan_memo_lock:
                    hit = _plan_memo.get(key)
                if hit is not None and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
                    parsed = hit[2]
                else:
                    parsed = parse_plan(_read_plan_file(path))
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
