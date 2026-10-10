"""Quota status for the dashboard: the Claude Code and Codex windows, and the sessions a limit stopped.

Everything here is read from files the providers already write; nothing is fetched and no credential
is read.

* Claude Code appends a rejected-request line to the session transcript when a limit is hit
  (`apiErrorStatus` 429 with a `quotaLimits` object that carries the reset time as epoch seconds).
  That line is the only thing that makes a session "interrupted".
* Claude Code shows live percentages only to the statusline command. The owner's statusline script
  may keep the latest payload it received in `claude-statusline.json` (see `STATUSLINE_SNIPPET`);
  without it the Claude meters know only what a hit line says.
* Codex records `rate_limits` on every `token_count` event of a rollout, so its meters need no setup.
* A Codex turn that a limit refused ends with a `task_complete` event whose `error.codex_error_info` is
  `usage_limit_exceeded`. A thread is "interrupted" while that is its last turn record. The event names
  no reset time, so it is read from the last real `rate_limits` before it, or from the message's
  "try again at 9:38 PM".
* kiro-cli records a `UsageLimitReachedError` display error. It carries no reset time.

A reading is never shown as current when it is not: a window whose reset time has passed reads
"reset", and a snapshot older than `STALE_AFTER_SECONDS` reads "stale".
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import math
import re
import threading
import time
from pathlib import Path

from . import config, data_claude, data_codex, overview

log = logging.getLogger(__name__)

WINDOW_SECONDS = {"five_hour": 5 * 3600, "seven_day": 7 * 86400}
# A transcript untouched for longer than this cannot end in a hit that still matters: the longest
# window is a week, and a hit stays listed for `KEEP_AFTER_RESET_SECONDS` after its reset.
HIT_LOOKBACK_SECONDS = 9 * 86400
KEEP_AFTER_RESET_SECONDS = 48 * 3600
# kiro-cli's limit message names no reset time ("return tomorrow"), so a stop is listed this long.
KIRO_KEEP_SECONDS = 48 * 3600
STALE_AFTER_SECONDS = 30 * 60
# The last conversation line of a transcript is within this many bytes of its end, except when one
# huge tool result sits last; then a larger read is tried once.
_TAIL_BYTES = 512 * 1024
_TAIL_BYTES_LARGE = 8 * 1024 * 1024
_CODEX_ROLLOUTS_TRIED = 4

# A cheap test before a line is parsed. Tolerant of whitespace around the colon: the providers write
# compact JSON today, and a prefilter that depended on that would stop finding hits without any error
# the day they did not.
_CONVERSATION_TYPE = re.compile(rb'"type"\s*:\s*"(?:user|assistant)"')
_SESSION_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# A statusline payload is a few KB. Anything larger is not one, and is not read.
MAX_SNAPSHOT_BYTES = 1024 * 1024
_USER_TYPE = re.compile(rb'"type"\s*:\s*"user"')

SNAPSHOT_NAME = "claude-statusline.json"
# Shown to the owner to paste into their statusline command (and kept, word for word, in the agent
# playbook's `providers/claude/statusline-command.sh`). `$input` is the JSON the statusline receives on
# stdin. The whole payload is written, because the match is a shell pattern and the write a redirect: no
# extra process runs on a statusline refresh (jq would add one, and so would `mv`: measured at roughly
# 60-150 ms on Windows, on top of a script that takes over a second). A payload with no `rate_limits`
# object (no subscription, or before the first reply) is skipped, so the last reading stays. A reader
# that catches the file mid-write cannot parse it and keeps the reading it had (`_read_snapshot`). The
# reading time is the file's own.
STATUSLINE_SNIPPET = (
    '# PowerAtlas: keep the latest quota readings where the dashboard can see them.\n'
    'pa_dir="${LOCALAPPDATA:+$LOCALAPPDATA/power-atlas}"\n'
    'if [ -n "$pa_dir" ] && [ -d "$pa_dir" ]; then\n'
    '  case "$input" in\n'
    '    *\'"rate_limits":{\'*|*\'"rate_limits": {\'*)\n'
    '      printf \'%s\' "$input" > "$pa_dir/claude-statusline.json"\n'
    '      ;;\n'
    '  esac\n'
    'fi\n'
)

_lock = threading.Lock()
# path -> (mtime_ns, size, hit or None): a transcript is re-read only when it changed.
_hit_memo: dict[str, tuple[int, int, dict | None]] = {}
# The same for kiro-cli message logs (the stop time, or None) and for Codex rollouts (the last real
# `rate_limits` and its time, or None). The Codex memo holds only the few newest rollouts.
_kiro_memo: dict[str, tuple[int, int, float | None]] = {}
_codex_memo: dict[str, tuple[int, int, tuple | None]] = {}
# Codex rollouts: the state of the last turn (`_codex_end`), for every rollout the stop scan visits.
_codex_end_memo: dict[str, tuple[int, int, dict | None]] = {}
# The last snapshot that parsed, for the moment a writer's `mv` is mid-replace.
_last_snapshot: list = [None]


# ---- small readers -------------------------------------------------------------------------

def _roots() -> tuple[Path, Path]:
    """`(kiro-cli v3 sessions, Claude Code projects)`. One seam, so tests redirect both."""
    v3_root, claude_root, _ide = overview._usage_roots()
    return v3_root, claude_root


def _read_tail(path: Path, size: int, length: int) -> bytes:
    start = max(0, size - length)
    return overview._read_tail(path, start, min(length, size))


def _epoch(value) -> float | None:
    """Epoch seconds from seconds, milliseconds or an ISO-8601 string; None when it is none of them."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        if not math.isfinite(v):
            return None
        return v / 1000.0 if v > 1e11 else v
    if isinstance(value, str) and value.strip():
        text = value.strip()
        try:
            v = float(text)
            if not math.isfinite(v):
                return None
            return v / 1000.0 if v > 1e11 else v
        except ValueError:
            pass
        try:
            return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        except (ValueError, OverflowError, OSError):
            return None
    return None


def _window(used, resets_at, captured_at: float | None, window_s: int, now: float, source: str) -> dict:
    """One meter: `{state, used_percent, resets_at, elapsed, source, captured_at}`.

    `state` is "live" or "stale" for a reading whose window is still open, "reset" once the reset
    time has passed (the old percentage no longer says anything), and "limited" is set by the caller
    for a hit. `elapsed` is how far through the window `now` is (0 to 1), for the pace marker.
    """
    pct = float(used) if isinstance(used, (int, float)) and not isinstance(used, bool) else None
    if pct is not None:
        # A non-finite reading is no reading: it would otherwise clamp to a full bar.
        pct = max(0.0, min(100.0, pct)) if math.isfinite(pct) else None
    reset = _epoch(resets_at)
    state = "live"
    if reset is not None and now >= reset:
        state, pct = "reset", None
    elif captured_at is not None and now - captured_at > STALE_AFTER_SECONDS:
        state = "stale"
    elapsed = None
    if reset is not None and now < reset:
        elapsed = max(0.0, min(1.0, 1.0 - (reset - now) / window_s))
    return {"state": state, "used_percent": pct, "resets_at": reset, "elapsed": elapsed,
            "source": source, "captured_at": captured_at}


# ---- Claude Code: hit lines ---------------------------------------------------------------

def _last_conversation(data_bytes: bytes) -> dict | None:
    """The last conversation line in `data_bytes`, parsed; None when there is none.

    Conversation means a user or assistant line that is not a sub-agent line and not one of Claude
    Code's own bookkeeping messages (a `/usage` or `/model` echo): a hit followed by one of those is
    still a hit.
    """
    for raw in reversed(data_bytes.split(b"\n")):
        if not _CONVERSATION_TYPE.search(raw):
            continue
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(obj, dict) or obj.get("type") not in ("user", "assistant"):
            continue
        if obj.get("isSidechain"):
            continue
        if obj["type"] == "user" and data_claude._is_meta_or_command_message(obj):
            continue
        return obj
    return None


def _as_hit(obj: dict, session_id: str) -> dict | None:
    """The hit described by an assistant line, or None when the line is not a quota hit."""
    if obj.get("type") != "assistant" or obj.get("apiErrorStatus") != 429:
        return None
    limits = obj.get("quotaLimits")
    if not isinstance(limits, dict):
        return None
    reset = _epoch(limits.get("resetsAt"))
    if reset is None:
        return None
    window = limits.get("rateLimitType")
    return {
        "session_id": session_id,
        "cwd": obj.get("cwd") if isinstance(obj.get("cwd"), str) else "",
        "window": window if window in WINDOW_SECONDS else "other",
        "resets_at": reset,
        "hit_at": _epoch(obj.get("timestamp")) or 0.0,
    }


def _transcript_hit(path: Path, st) -> dict | None:
    """The quota hit that ends the transcript at `path`, or None. Memoised on `(mtime_ns, size)`."""
    key = str(path)
    with _lock:
        memo = _hit_memo.get(key)
    if memo and memo[0] == st.st_mtime_ns and memo[1] == st.st_size:
        return memo[2]
    hit = None
    try:
        for length in (_TAIL_BYTES, _TAIL_BYTES_LARGE):
            obj = _last_conversation(_read_tail(path, st.st_size, length))
            if obj is not None or st.st_size <= length:
                hit = _as_hit(obj, path.stem) if obj is not None else None
                break
        if hit is not None:
            # The folder the session was STARTED in, which is where `claude --resume` looks for it: the
            # hit line's own `cwd` is wherever the session had `cd`'d to by then.
            hit["cwd"] = _first_cwd(path) or hit["cwd"]
    except OSError:
        return None
    with _lock:
        _hit_memo[key] = (st.st_mtime_ns, st.st_size, hit)
    return hit


_CWD_FIELD = re.compile(rb'"cwd"\s*:\s*"((?:[^"\\]|\\.)*)"')


def _first_cwd(path: Path) -> str:
    """The first `cwd` recorded in a transcript, or "" when its first 64 KB holds none."""
    try:
        head = overview._read_tail(path, 0, 65536)
    except OSError:
        return ""
    match = _CWD_FIELD.search(head)
    if not match:
        return ""
    try:
        value = json.loads('"' + match.group(1).decode("utf-8", "replace") + '"')
    except ValueError:
        return ""
    return value if isinstance(value, str) else ""


def _scan_claude(now: float) -> list[dict]:
    """Interrupted Claude Code sessions, most recent hit first."""
    _v3, claude_root = _roots()
    found: list[dict] = []
    seen: set[str] = set()
    for path in claude_root.glob("*/*.jsonl"):
        if not data_claude._is_session_file(path.name):
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        if now - st.st_mtime > HIT_LOOKBACK_SECONDS:
            continue
        seen.add(str(path))
        hit = _transcript_hit(path, st)
        if hit is None or now > hit["resets_at"] + KEEP_AFTER_RESET_SECONDS:
            continue
        try:
            title = data_claude._parse_session_cached(path, st)[0]
        except OSError:
            title = ""
        cwd = hit["cwd"]
        found.append({
            "id": hit["session_id"], "provider": "claude-code", "title": title or "untitled session",
            "cwd": cwd, "name": Path(cwd).name or cwd, "window": hit["window"],
            "resets_at": hit["resets_at"], "hit_at": hit["hit_at"],
            "state": "limited" if now < hit["resets_at"] else "ready",
        })
    with _lock:
        for stale in [k for k in _hit_memo if k not in seen]:
            del _hit_memo[stale]
    found.sort(key=lambda r: r["hit_at"], reverse=True)
    return found


def interrupted(now: float | None = None) -> list[dict]:
    """Every interrupted session, Claude Code, Codex and kiro-cli, most recent first."""
    now = time.time() if now is None else now
    rows = _scan_claude(now) + _scan_codex(now) + _scan_kiro(now)
    rows.sort(key=lambda r: r["hit_at"], reverse=True)
    return rows


def find_hit(session_id: str, now: float | None = None) -> dict | None:
    """The interrupted-session row for one Claude Code or Codex session id, or None when it is not listed.
    Both id shapes are UUIDs that cannot meet in practice, so the id alone names the provider."""
    now = time.time() if now is None else now
    for scan in (_scan_claude, _scan_codex):
        for row in scan(now):
            if row["id"] == session_id:
                return row
    return None


def claude_session_path(session_id: str) -> Path | None:
    """The transcript of a Claude Code session, or None. The id must be a whole UUID: nothing else
    (a path segment, a wildcard) reaches the glob."""
    if not isinstance(session_id, str) or not _SESSION_UUID.fullmatch(session_id):
        return None
    _v3, claude_root = _roots()
    for path in claude_root.glob("*/" + session_id + ".jsonl"):
        return path
    return None


def transcript_progress(path: Path) -> dict | None:
    """Where a transcript's conversation stands: `{kind, at, hit}`, or None when it cannot be read.

    `kind` is "hit" (the last conversation line is a quota hit; `hit` describes it), "reply" (an
    assistant line that is not a hit) or "prompt" (a user line: a request nothing has answered yet).
    `at` is that line's timestamp. Not memoised: the caller asks about one transcript, a few seconds
    after it changed.
    """
    try:
        size = path.stat().st_size
        for length in (_TAIL_BYTES, _TAIL_BYTES_LARGE):
            obj = _last_conversation(_read_tail(path, size, length))
            if obj is not None or size <= length:
                break
    except OSError:
        return None
    if obj is None:
        return None
    hit = _as_hit(obj, path.stem)
    kind = "hit" if hit else ("reply" if obj.get("type") == "assistant" else "prompt")
    return {"kind": kind, "at": _epoch(obj.get("timestamp")) or 0.0, "hit": hit}


# ---- kiro-cli: usage-limit display errors -------------------------------------------------

def _kiro_stop(path: Path, st) -> float | None:
    """When the session's last turn ended in a usage-limit error, or None. Memoised on `(mtime_ns, size)`."""
    key = str(path)
    with _lock:
        memo = _kiro_memo.get(key)
    if memo and memo[0] == st.st_mtime_ns and memo[1] == st.st_size:
        return memo[2]
    try:
        raw = _read_tail(path, st.st_size, _TAIL_BYTES)
    except OSError:
        return None
    stop_at = None
    for line in reversed(raw.split(b"\n")):
        if b'"displayError"' not in line and not _USER_TYPE.search(line):
            continue
        try:
            entry = json.loads(line)
        except (ValueError, RecursionError):
            continue
        payload = entry.get("payload") if isinstance(entry, dict) else None
        if not isinstance(payload, dict):
            continue
        if payload.get("type") == "user":
            break
        value = payload.get("value")
        if (payload.get("type") == "session_metadata" and payload.get("key") == "displayError"
                and isinstance(value, dict) and value.get("errorType") == "UsageLimitReachedError"):
            stop_at = _epoch(entry.get("timestamp"))
            break
    with _lock:
        _kiro_memo[key] = (st.st_mtime_ns, st.st_size, stop_at)
    return stop_at


def _scan_kiro(now: float) -> list[dict]:
    """kiro-cli sessions whose last turn ended in a usage-limit error. No reset time is known."""
    v3_root, _claude = _roots()
    found: list[dict] = []
    seen: set[str] = set()
    for path in v3_root.glob("*/sess_*/messages.jsonl"):
        try:
            st = path.stat()
        except OSError:
            continue
        if now - st.st_mtime > KIRO_KEEP_SECONDS:
            continue
        seen.add(str(path))
        stop_at = _kiro_stop(path, st)
        if stop_at is None or now - stop_at > KIRO_KEEP_SECONDS:
            continue
        meta = {}
        try:
            meta = json.loads((path.parent / "session.json").read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            pass
        paths = meta.get("workspacePaths") if isinstance(meta, dict) else None
        cwd = paths[0] if isinstance(paths, list) and paths and isinstance(paths[0], str) else ""
        title = meta.get("title") if isinstance(meta, dict) and isinstance(meta.get("title"), str) else ""
        found.append({
            "id": path.parent.name, "provider": "kiro-cli-v3", "title": title or "untitled session",
            "cwd": cwd, "name": Path(cwd).name or cwd, "window": "daily", "resets_at": None,
            "hit_at": stop_at, "state": "limited",
        })
    with _lock:
        for stale in [k for k in _kiro_memo if k not in seen]:
            del _kiro_memo[stale]
    found.sort(key=lambda r: r["hit_at"], reverse=True)
    return found


# ---- Codex: usage-limit turn ends ---------------------------------------------------------

_CODEX_TURN_RECORDS = (b'"task_started"', b'"task_complete"', b'"turn_aborted"')
_CODEX_LIMIT_CODE = "usage_limit_exceeded"
# The only form seen so far (5-hour windows): "... or try again at 9:38 PM." A local clock time, no date.
_TRY_AGAIN_AT = re.compile(r"try again at (\d{1,2}):(\d{2})\s*([AaPp])[Mm]\b")


def _codex_all() -> list[Path]:
    """Every top-level Codex rollout. One seam, so tests supply their own."""
    return [Path(r.path) for r in data_codex.canonical_rollouts()]


def _message_reset(message, hit_at: float) -> float | None:
    """The reset time a limit message gives as a clock time, as epoch seconds: the next time after the hit
    that the local clock reads it. None when there is no such phrase or the hour is not one."""
    match = _TRY_AGAIN_AT.search(message) if isinstance(message, str) else None
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if not (1 <= hour <= 12 and minute < 60):
        return None
    hour = hour % 12 + (12 if match.group(3).lower() == "p" else 0)
    try:
        base = dt.datetime.fromtimestamp(hit_at)
        at = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if at.timestamp() <= hit_at:
            at += dt.timedelta(days=1)
        return at.timestamp()
    except (OverflowError, OSError, ValueError):
        return None


def _full_window(limits, hit_at: float) -> tuple[str, float] | None:
    """`(window name, reset epoch)` of the window a `rate_limits` reading shows full and not yet reset at
    `hit_at`, the later one when both are; None when it shows none."""
    best = None
    for role in ("primary", "secondary"):
        raw = limits.get(role) if isinstance(limits, dict) else None
        if not isinstance(raw, dict):
            continue
        used, reset = raw.get("used_percent"), _epoch(raw.get("resets_at"))
        if isinstance(used, bool) or not isinstance(used, (int, float)) or not math.isfinite(used):
            continue
        if used < 99.0 or reset is None or reset <= hit_at:
            continue
        minutes = raw.get("window_minutes")
        name = "seven_day" if isinstance(minutes, (int, float)) and minutes >= 24 * 60 else "five_hour"
        if best is None or reset > best[1]:
            best = (name, reset)
    return best


def _codex_end(path: Path, size: int) -> dict | None:
    """Where a rollout's last turn stands: `{kind, at, hit}`, or None when it has no turn record.

    `kind` is "hit" (the last turn record is a `task_complete` a usage limit refused; `hit` is
    `{hit_at, window, resets_at}`, `resets_at` None when no source names one) or "reply" (any other
    turn record: a turn started, ended or was aborted). The record is matched on its `type` and
    `payload.type`, never as text: a rollout routinely quotes these words in tool output.
    """
    for length in (_TAIL_BYTES, _TAIL_BYTES_LARGE):
        lines = overview._read_tail(path, max(0, size - length), min(length, size), shared=True).split(b"\n")
        found = None
        for i in range(len(lines) - 1, -1, -1):
            raw = lines[i]
            if b'"event_msg"' not in raw or not any(word in raw for word in _CODEX_TURN_RECORDS):
                continue
            try:
                obj = json.loads(raw)
            except (ValueError, RecursionError):
                continue
            payload = obj.get("payload") if isinstance(obj, dict) else None
            if (obj.get("type") == "event_msg" and isinstance(payload, dict)
                    and payload.get("type") in ("task_started", "task_complete", "turn_aborted")):
                found = (i, payload, _epoch(obj.get("timestamp")) or 0.0)
                break
        if found is None:
            if size <= length:
                return None
            continue
        index, payload, at = found
        error = payload.get("error") if payload.get("type") == "task_complete" else None
        if not isinstance(error, dict) or error.get("codex_error_info") != _CODEX_LIMIT_CODE:
            return {"kind": "reply", "at": at, "hit": None}
        # The reading that says which window is full: the last real one before the stop. The event
        # right before it usually carries `rate_limits` with both windows null.
        reading = None
        for raw in reversed(lines[:index]):
            if b'"rate_limits"' not in raw:
                continue
            try:
                obj = json.loads(raw)
            except (ValueError, RecursionError):
                continue
            limits = obj.get("payload", {}).get("rate_limits") if isinstance(obj, dict) and isinstance(obj.get("payload"), dict) else None
            if isinstance(limits, dict) and (isinstance(limits.get("primary"), dict) or isinstance(limits.get("secondary"), dict)):
                reading = limits
                break
        full = _full_window(reading, at)
        if full is None:
            reset = _message_reset(error.get("message"), at)
            full = ("other", reset) if reset is not None else ("other", None)
        return {"kind": "hit", "at": at, "hit": {"hit_at": at, "window": full[0], "resets_at": full[1]}}
    return None


def _codex_stop(path: Path, st) -> dict | None:
    """The hit that ends the rollout at `path`, or None. Memoised on `(mtime_ns, size)`."""
    key = str(path)
    with _lock:
        memo = _codex_end_memo.get(key)
    if memo and memo[0] == st.st_mtime_ns and memo[1] == st.st_size:
        return memo[2]
    try:
        end = _codex_end(path, st.st_size)
    except OSError:
        return None
    hit = end["hit"] if end and end["kind"] == "hit" else None
    with _lock:
        _codex_end_memo[key] = (st.st_mtime_ns, st.st_size, hit)
    return hit


def codex_progress(session_id: str) -> dict | None:
    """Where a Codex thread's last turn stands, as `transcript_progress` does for a Claude transcript:
    `{kind, at, hit}` with `kind` "hit" or "reply" (a turn started or ended after the stop), or None when
    the rollout cannot be found or read. Not memoised: the caller asks about one thread, now."""
    path = data_codex.rollout_path(session_id)
    if path is None:
        return None
    try:
        return _codex_end(path, path.stat().st_size)
    except OSError:
        return None


def _scan_codex(now: float) -> list[dict]:
    """Interrupted Codex threads, most recent hit first."""
    found: list[dict] = []
    seen: set[str] = set()
    for path in _codex_all():
        try:
            st = path.stat()
        except OSError:
            continue
        if now - st.st_mtime > HIT_LOOKBACK_SECONDS:
            continue
        seen.add(str(path))
        hit = _codex_stop(path, st)
        if hit is None:
            continue
        reset = hit["resets_at"]
        # Without a reset time a stop is listed as long as kiro's, which has none either.
        if now > (reset + KEEP_AFTER_RESET_SECONDS if reset is not None else hit["hit_at"] + KIRO_KEEP_SECONDS):
            continue
        meta = data_codex.read_meta(path) or {}
        sid = meta.get("id").lower() if isinstance(meta.get("id"), str) else ""
        cwd = meta.get("cwd")
        if not _SESSION_UUID.fullmatch(sid) or not isinstance(cwd, str) or not cwd:
            continue
        found.append({
            "id": sid, "provider": "codex", "title": data_codex.thread_title(sid) or "untitled session",
            "cwd": cwd, "name": Path(cwd).name or cwd, "window": hit["window"],
            "resets_at": reset, "hit_at": hit["hit_at"],
            "state": "ready" if reset is not None and now >= reset else "limited",
        })
    with _lock:
        for stale in [k for k in _codex_end_memo if k not in seen]:
            del _codex_end_memo[stale]
    found.sort(key=lambda r: r["hit_at"], reverse=True)
    return found


# ---- Claude Code: the statusline snapshot -------------------------------------------------

def _snapshot_path() -> Path:
    return Path(config.CONFIG_DIR) / SNAPSHOT_NAME


def _read_snapshot() -> tuple[dict, float] | None:
    """`(payload, read_at)` from the snapshot file, or None when there is none.

    The payload is what the statusline received (a top-level `rate_limits` object). `read_at` is its
    `captured_at` when it carries one, else the file's modification time. A file that does not parse (a
    writer caught mid-replace) answers with the last one that did.
    """
    path = _snapshot_path()
    try:
        st = path.stat()
        if st.st_size > MAX_SNAPSHOT_BYTES:
            log.warning("%s is %d bytes: not a statusline payload, ignored", path.name, st.st_size)
            return None
        mtime = st.st_mtime
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return None
    try:
        snap = json.loads(text)
    except (ValueError, RecursionError):
        with _lock:
            return _last_snapshot[0]
    if not isinstance(snap, dict):
        return None
    result = (snap, _epoch(snap.get("captured_at")) or mtime)
    with _lock:
        _last_snapshot[0] = result
    return result


def _claude_meters(hits: list[dict], now: float) -> dict:
    pair = _read_snapshot()
    snap, captured = pair if pair else (None, None)
    limits = snap.get("rate_limits") if snap and isinstance(snap.get("rate_limits"), dict) else {}
    windows: dict[str, dict | None] = {}
    for name, seconds in WINDOW_SECONDS.items():
        raw = limits.get(name) if isinstance(limits.get(name), dict) else None
        win = None
        if raw is not None:
            win = _window(raw.get("used_percentage"), raw.get("resets_at"), captured, seconds, now,
                          "statusline")
        # An open hit says the window is full, and it is newer than a snapshot taken before it.
        active = [h for h in hits if h["window"] == name and h["resets_at"] > now
                  and (win is None or captured is None or h["hit_at"] >= captured)]
        if active:
            top = max(active, key=lambda h: h["resets_at"])
            win = _window(100.0, top["resets_at"], top["hit_at"], seconds, now, "hit")
            win["state"] = "limited"
        windows[name] = win
    return {"state": "ready" if snap else "no_snapshot", "captured_at": captured, "windows": windows}


# ---- Codex: rollout rate limits -----------------------------------------------------------

def _codex_candidates() -> list[Path]:
    """The newest Codex rollouts, newest first. One seam, so tests supply their own."""
    rollouts = sorted(data_codex.canonical_rollouts(), key=lambda r: r.mtime_ns, reverse=True)
    return [Path(r.path) for r in rollouts[:_CODEX_ROLLOUTS_TRIED]]


def _codex_last_limits(path: Path) -> tuple[dict, float | None] | None:
    """The last real `rate_limits` of a rollout and the time of its event. Memoised on `(mtime_ns, size)`."""
    key = str(path)
    try:
        st = path.stat()
    except OSError:
        return None
    with _lock:
        memo = _codex_memo.get(key)
    if memo and memo[0] == st.st_mtime_ns and memo[1] == st.st_size:
        return memo[2]
    try:
        raw = overview._read_tail(path, max(0, st.st_size - _TAIL_BYTES), min(_TAIL_BYTES, st.st_size), shared=True)
    except OSError:
        return None
    found = None
    for line in reversed(raw.split(b"\n")):
        if b'"rate_limits"' not in line:
            continue
        try:
            entry = json.loads(line)
        except (ValueError, RecursionError):
            continue
        payload = entry.get("payload") if isinstance(entry, dict) else None
        limits = payload.get("rate_limits") if isinstance(payload, dict) else None
        if isinstance(limits, dict) and isinstance(limits.get("primary"), dict):
            found = (limits, _epoch(entry.get("timestamp")))
            break
    with _lock:
        _codex_memo[key] = (st.st_mtime_ns, st.st_size, found)
        # Only the few newest rollouts are ever asked about; the rest are not kept.
        while len(_codex_memo) > 4 * _CODEX_ROLLOUTS_TRIED:
            del _codex_memo[next(iter(_codex_memo))]
    return found


def _codex_meters(now: float) -> dict:
    found = None
    try:
        for path in _codex_candidates():
            found = _codex_last_limits(path)
            if found:
                break
    except OSError:
        log.warning("Quota: could not list the Codex rollouts", exc_info=True)
    if not found:
        return {"state": "none", "captured_at": None, "plan": "", "windows": {"five_hour": None, "seven_day": None}}
    limits, captured = found
    windows: dict[str, dict | None] = {"five_hour": None, "seven_day": None}
    for role in ("primary", "secondary"):
        raw = limits.get(role)
        if not isinstance(raw, dict):
            continue
        minutes = raw.get("window_minutes")
        name = "seven_day" if isinstance(minutes, (int, float)) and minutes >= 24 * 60 else "five_hour"
        windows[name] = _window(raw.get("used_percent"), raw.get("resets_at"), captured,
                                WINDOW_SECONDS[name], now, "rollout")
    plan = limits.get("plan_type") if isinstance(limits.get("plan_type"), str) else ""
    return {"state": "ready", "captured_at": captured, "plan": plan, "windows": windows}


# ---- the payload -----------------------------------------------------------------------------

def payload(now: float | None = None) -> dict:
    """The Overview's `quota` block.

    `claude` and `codex` are `{state, captured_at, windows: {five_hour, seven_day}}` (Claude also
    names `setup` when no snapshot exists yet); `interrupted` lists the sessions a limit stopped,
    most recent first. Each failure is contained: a provider that cannot be read leaves its own
    block empty and the rest intact.
    """
    now = time.time() if now is None else now
    interrupted: list[dict] = []
    hits: list[dict] = []
    try:
        claude_rows = _scan_claude(now)
        interrupted.extend(claude_rows)
        hits = [{"window": r["window"], "resets_at": r["resets_at"], "hit_at": r["hit_at"]}
                for r in claude_rows]
    except Exception:
        log.exception("Quota: could not scan Claude Code transcripts")
    try:
        interrupted.extend(_scan_codex(now))
    except Exception:
        log.exception("Quota: could not scan Codex rollouts")
    try:
        interrupted.extend(_scan_kiro(now))
    except Exception:
        log.exception("Quota: could not scan kiro-cli sessions")
    interrupted.sort(key=lambda r: r["hit_at"], reverse=True)
    try:
        claude = _claude_meters(hits, now)
    except Exception:
        log.exception("Quota: could not build the Claude meters")
        claude = {"state": "no_snapshot", "captured_at": None, "windows": {"five_hour": None, "seven_day": None}}
    if claude["state"] == "no_snapshot":
        claude["setup"] = STATUSLINE_SNIPPET
    try:
        codex = _codex_meters(now)
    except Exception:
        log.exception("Quota: could not build the Codex meters")
        codex = {"state": "none", "captured_at": None, "plan": "", "windows": {"five_hour": None, "seven_day": None}}
    return {"now": now, "claude": claude, "codex": codex, "interrupted": interrupted}


def reset() -> None:
    """Forget every memo (tests)."""
    with _lock:
        _hit_memo.clear()
        _kiro_memo.clear()
        _codex_memo.clear()
        _codex_end_memo.clear()
        _last_snapshot[0] = None
