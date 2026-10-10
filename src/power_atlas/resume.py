"""Resume the Claude Code and Codex sessions a quota limit stopped: quick resume, scheduled resume, and
what follows.

Two ways to resume, chosen by whether the session's terminal is still open:

* **Closed**: PowerAtlas opens a Windows Terminal tab running `claude --resume <id> "<prompt>"` (or
  `codex resume <id> "<prompt>"`) in the session's folder (`launcher.launch_session`). The session starts
  by answering the prompt.
* **Open** (Claude Code only): a hit leaves the TUI at its prompt, so the prompt is typed into that console by a helper
  process (`consoleinject`). Nothing is typed unless all of these hold: the process is the one
  PowerAtlas's presence scan validated for this exact session, it is an interactive terminal session
  (not a `claude -p` run that shares its parent's console), the TUI is not waiting on a dialog, the
  transcript still ends at the hit, and the reset time has passed. The prompt may not start with `!` or
  `/` there, because the TUI would run it as a shell or slash command. Whatever sits unsent in the TUI's
  prompt box is submitted with it: nothing can read that box from outside, so the Overview says so. A Codex
thread open in a terminal is never typed into (its writer lock is held): the owner is told to continue it there.

A resume is reserved for its session before anything slow happens, so a click that races a due schedule
(or two tabs) cannot start two. It is then watched in the transcript. A reply means it worked. A new hit
with a *later* reset time means the quota had not really reset; the resume is armed once more for that
time (two attempts in all). A hit that does not move the reset time, a second failure, or no reply
within the wait ends the watch with a toast. A launch that fails to start is reported and never retried.

Schedules are kept in `quota-state.json` beside `config.toml` (not in it: that file is rewritten whole on
every setting change). A schedule fires only if PowerAtlas is running at its time. One that came due
while PowerAtlas was down, or more than `LATE_SECONDS` ago for any reason (the PC slept), is dropped
and never fired late: the session then reads "Quota reset, ready to resume" and the owner decides.
Schedules due at one moment all fire in that moment, one after the other with nothing queued behind a
timer; each is taken off the list just before it fires, so cancelling one that has not started still works.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from . import config, data_codex, launcher, notifications, presence, quota

log = logging.getLogger(__name__)

STATE_NAME = "quota-state.json"
DEFAULT_PROMPT = "resume"
MAX_PROMPT_CHARS = 2000
FIRE_DELAY_SECONDS = 60          # after the reset time
MAX_FIRE_AHEAD_SECONDS = 7 * 86400   # a schedule may sit this long after the reset, no more
LATE_SECONDS = 600               # a schedule overdue by more than this is dropped, not fired
NO_ACTIVITY_SECONDS = 180        # nothing in the transcript since the resume started
PROMPT_REPLY_SECONDS = 600       # the prompt landed but nothing has answered it
MAX_ATTEMPTS = 2
TICK_SECONDS = 5.0
DISMISS_KEEP_SECONDS = 14 * 86400
_HELPER_TIMEOUT_SECONDS = 20
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f\x85  ]")
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_GONE = "That session is no longer stopped by a quota limit"

_lock = threading.RLock()
_state: dict = {"loaded": False, "schedules": [], "dismissed": []}
# Resumes being watched, and the ones that ended badly (kept so the row can say so). Memory only: a
# restart forgets them, and the row then reads as it did before, interrupted.
_runs: list[dict] = []
# [thread, stop event]. The event belongs to its thread, so a thread that outlived a stop is never
# revived by a later start.
_scheduler: list = [None, None]


# ---- the state file ------------------------------------------------------------------------

def _state_path() -> Path:
    return Path(config.CONFIG_DIR) / STATE_NAME


def _num(value) -> float | None:
    """A finite number, or None. NaN and infinity are numbers to `json` and to `max`, and neither can
    be sent back to a browser, so they never get in."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _load() -> None:
    """Read the state file once. Caller holds `_lock`. An unreadable or odd file reads as empty."""
    if _state["loaded"]:
        return
    _state["loaded"] = True
    schedules, dismissed = [], []
    try:
        raw = json.loads(_state_path().read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raw = {}
    except (OSError, ValueError, RecursionError):
        log.warning("%s could not be read; starting with no schedules", STATE_NAME, exc_info=True)
        raw = {}
    if isinstance(raw, dict):
        for s in raw.get("schedules") if isinstance(raw.get("schedules"), list) else []:
            if (isinstance(s, dict) and isinstance(s.get("id"), str) and isinstance(s.get("prompt"), str)
                    and isinstance(s.get("session_id"), str) and _UUID_RE.fullmatch(s["session_id"])
                    and isinstance(s.get("cwd"), str) and _num(s.get("fire_at")) is not None
                    and _num(s.get("resets_at")) is not None and s.get("attempt") in (1, 2)):
                schedules.append({k: s[k] for k in ("id", "session_id", "cwd", "prompt", "fire_at",
                                                    "resets_at", "attempt")}
                                 | {"title": s["title"] if isinstance(s.get("title"), str) else ""})
        for d in raw.get("dismissed") if isinstance(raw.get("dismissed"), list) else []:
            if isinstance(d, dict) and isinstance(d.get("session_id"), str) and _num(d.get("key_at")) is not None \
                    and _num(d.get("at")) is not None:
                dismissed.append({"session_id": d["session_id"], "key_at": float(d["key_at"]), "at": float(d["at"])})
    _state["schedules"], _state["dismissed"] = schedules, dismissed


def _save() -> bool:
    """Write the state file atomically (temporary file, then rename). Caller holds `_lock`. False when it
    could not be written; the in-memory state stays, so nothing is lost until the process ends."""
    path = _state_path()
    tmp = path.with_name(path.name + ".tmp")
    try:
        body = json.dumps({"version": 1, "schedules": _state["schedules"], "dismissed": _state["dismissed"]},
                          indent=1, allow_nan=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, path)
        return True
    except (OSError, ValueError):
        log.exception("Could not save %s", path)
        return False


# ---- inputs --------------------------------------------------------------------------------

def clean_prompt(text) -> tuple[str, str]:
    """`(prompt, error)`. Missing or blank means the default. Control characters become spaces, because
    a newline would end a statement in the launched script and a key press in the typed one."""
    if text is None:
        return DEFAULT_PROMPT, ""
    if not isinstance(text, str):
        return "", "The prompt must be text"
    cleaned = " ".join(_CONTROL_RE.sub(" ", text).split())
    if not cleaned:
        return DEFAULT_PROMPT, ""
    if len(cleaned) > MAX_PROMPT_CHARS:
        return "", f"The prompt is too long (at most {MAX_PROMPT_CHARS} characters)"
    if cleaned.startswith("-"):
        return "", "The prompt cannot start with a dash"
    return cleaned, ""


def _valid_session_id(session_id) -> bool:
    return isinstance(session_id, str) and bool(_UUID_RE.fullmatch(session_id))


def _key_at(row: dict) -> float:
    """What tells one stop of a session from the next: the reset time, or the stop time when there is none."""
    return float(row["resets_at"] if row.get("resets_at") is not None else row["hit_at"])


# ---- the two ways to type or start -------------------------------------------------------------

def _type_into(pid: int, text: str) -> tuple[bool, str]:
    """Type `text` into `pid`'s console through the helper process. `(ok, error)`."""
    if sys.platform != "win32":
        return False, "Typing into a terminal is only available on Windows"
    exe, env = sys.executable, dict(os.environ)
    base = getattr(sys, "_base_executable", exe)
    if base and os.path.normcase(base) != os.path.normcase(exe):
        env["__PYVENV_LAUNCHER__"] = exe
        exe = base
    src = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (src, env.get("PYTHONPATH")) if p)
    try:
        done = subprocess.run(
            [exe, "-P", "-c", "from power_atlas.consoleinject import main; raise SystemExit(main())"],
            input=json.dumps({"pid": pid, "text": text}), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=_HELPER_TIMEOUT_SECONDS, env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        answer = json.loads((done.stdout or "").strip().splitlines()[-1])
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return False, "Typing into the terminal failed"
    if isinstance(answer, dict) and answer.get("ok") is True:
        return True, ""
    return False, str(answer.get("error")) if isinstance(answer, dict) and answer.get("error") else "Typing failed"


def _relaunch(row: dict, prompt: str) -> tuple[bool, str]:
    from .config import get_active_launch_profile, load_config
    cfg = load_config()
    provider = row["provider"]
    result = launcher.launch_session(
        cwd=row["cwd"], session_id=row["id"], provider=provider,
        default_args=cfg.provider_settings.get(provider, {}).get("default_args", ""),
        launch_profile=get_active_launch_profile(cfg), session_title=row["title"], prompt=prompt)
    return bool(result.success), result.error


# ---- the actions -----------------------------------------------------------------------------

def _refuse(error: str, gone: bool = False) -> dict:
    out = {"ok": False, "error": error}
    if gone:
        out["gone"] = True
    return out


def resume_now(session_id, prompt=None, attempt: int = 1, now: float | None = None,
               expect_resets_at: float | None = None) -> dict:
    """Resume one session now. `{"ok": True, "mode": "terminal" | "typed"}` or `{"ok": False, "error": ...}`
    (`"gone": True` when the session is no longer stopped, which is not a failure worth a toast).

    `expect_resets_at` is the reset time a schedule was made for: a session that has since been stopped
    again by a different limit is not resumed on the old schedule's say-so."""
    now = time.time() if now is None else now
    if not _valid_session_id(session_id):
        return _refuse("That is not a session id")
    cleaned, error = clean_prompt(prompt)
    if error:
        return _refuse(error)
    row = quota.find_hit(session_id, now)
    if row is None or (expect_resets_at is not None
                       and (row["resets_at"] is None or abs(row["resets_at"] - expect_resets_at) > 1.0)):
        return _refuse(_GONE, gone=True)
    if row["resets_at"] is None:
        return _refuse("The reset time of this stop is not known")
    if not row["cwd"]:
        return _refuse("The session's folder is not recorded in its transcript")
    provider = row["provider"]
    pid = None
    mode = "terminal"
    if provider == "codex":
        # A thread open in a terminal holds its writer lock; Codex itself refuses to resume it, and
        # nothing here types into a Codex terminal.
        if data_codex.session_writer_locked(session_id):
            return _refuse("This thread is open in a terminal PowerAtlas cannot type into. Continue it there.")
    else:
        snapshot = presence.get_snapshot(force=True)
        pid = snapshot.pid_for("claude-code", session_id)
        if pid is None and snapshot.is_live("claude-code", row["cwd"], session_id):
            return _refuse("This session is open in a terminal PowerAtlas cannot type into. Continue it there.")
    if pid is not None:
        # The gates for typing into a live terminal.
        mode = "typed"
        if snapshot.session_entrypoint("claude-code", session_id) != "cli":
            return _refuse("This session is not running in an interactive terminal, so nothing was typed")
        if snapshot.reported_status("claude-code", session_id) == "waiting":
            return _refuse("This session is waiting on a question in its terminal. Answer it there first.")
        if now < row["resets_at"]:
            return _refuse("This session is open in a terminal, and the quota has not reset yet")
        if cleaned[0] in "!/":
            return _refuse("In an open terminal the prompt cannot start with ! or /")
        path = quota.claude_session_path(session_id)
        progress = quota.transcript_progress(path) if path else None
        if not progress or progress["kind"] != "hit":
            return _refuse("The session has moved on since it was stopped")
    run = {"id": uuid.uuid4().hex, "session_id": session_id, "provider": provider, "cwd": row["cwd"],
           "title": row["title"], "prompt": cleaned, "launched_at": now, "resets_at": row["resets_at"],
           "attempt": attempt, "mode": mode, "state": "watching", "detail": ""}
    # Reserve the session before the slow part (the helper takes up to 20 s): a second resume of it,
    # from a due schedule or a second tab, is refused here and not launched alongside this one.
    with _lock:
        _load()
        if any(r["session_id"] == session_id and r["state"] == "watching" for r in _runs):
            return _refuse("That session is already being resumed")
        _runs[:] = [r for r in _runs if r["session_id"] != session_id] + [run]
        before = len(_state["schedules"])
        _state["schedules"] = [s for s in _state["schedules"] if s["session_id"] != session_id]
        if len(_state["schedules"]) != before:
            _save()
    try:
        ok, error = _type_into(pid, cleaned) if mode == "typed" else _relaunch(row, cleaned)
    except Exception:
        log.exception("resuming %s failed", session_id)
        ok, error = False, "The session could not be resumed"
    if not ok:
        with _lock:
            run["state"], run["detail"] = "error", error or "The session could not be resumed"
        return _refuse(run["detail"])
    return {"ok": True, "mode": mode}


def schedule(session_id, prompt=None, fire_at=None, now: float | None = None) -> dict:
    """Arm a resume for `fire_at` (default: a minute after the reset). One schedule per session.
    `{"ok": True, "fire_at": ..., "persisted": bool}`; `persisted` is False when it could not be saved,
    in which case it holds until PowerAtlas restarts and no longer."""
    now = time.time() if now is None else now
    if not _valid_session_id(session_id):
        return _refuse("That is not a session id")
    cleaned, error = clean_prompt(prompt)
    if error:
        return _refuse(error)
    row = quota.find_hit(session_id, now)
    if row is None:
        return _refuse(_GONE, gone=True)
    if row["resets_at"] is None:
        return _refuse("The reset time of this stop is not known, so it cannot be scheduled")
    if now >= row["resets_at"]:
        return _refuse("The quota has already reset. Use Resume now.")
    if fire_at is None:
        when = row["resets_at"] + FIRE_DELAY_SECONDS
    else:
        when = _num(fire_at)
        if when is None:
            return _refuse("The time must be a number")
        if when > row["resets_at"] + MAX_FIRE_AHEAD_SECONDS:
            return _refuse("That time is too far after the reset")
    # Never before the reset: a resume earlier than that only meets the limit again.
    when = max(when, row["resets_at"], now + 5.0)
    record = {"id": uuid.uuid4().hex, "session_id": session_id, "cwd": row["cwd"], "title": row["title"],
              "prompt": cleaned, "fire_at": when, "resets_at": row["resets_at"], "attempt": 1}
    with _lock:
        _load()
        _state["schedules"] = [s for s in _state["schedules"] if s["session_id"] != session_id] + [record]
        _runs[:] = [r for r in _runs if r["session_id"] != session_id]
        persisted = _save()
    return {"ok": True, "fire_at": when, "persisted": persisted}


def cancel(session_id) -> dict:
    with _lock:
        _load()
        before = len(_state["schedules"])
        _state["schedules"] = [s for s in _state["schedules"] if s["session_id"] != session_id]
        if len(_state["schedules"]) != before:
            _save()
            return {"ok": True}
    return {"ok": False, "error": "Nothing was scheduled for that session"}


def dismiss(session_id, now: float | None = None) -> dict:
    """Hide the session's current stop from the list. A later, different stop shows again."""
    now = time.time() if now is None else now
    if not isinstance(session_id, str) or not session_id:
        return {"ok": False, "error": "Missing session id"}
    row = next((r for r in quota.interrupted(now) if r["id"] == session_id), None)
    if row is None:
        return {"ok": False, "error": "That session is not in the list"}
    with _lock:
        _load()
        _state["dismissed"] = [d for d in _state["dismissed"] if now - d["at"] < DISMISS_KEEP_SECONDS]
        _state["dismissed"].append({"session_id": session_id, "key_at": _key_at(row), "at": now})
        _state["schedules"] = [s for s in _state["schedules"] if s["session_id"] != session_id]
        _runs[:] = [r for r in _runs if r["session_id"] != session_id]
        _save()
    return {"ok": True}


def decorate(rows: list[dict], now: float | None = None) -> list[dict]:
    """The interrupted rows without the dismissed ones, each with its resume state.

    `resume` is absent when nothing is happening, else `{state, ...}`: "scheduled" (`fire_at`,
    `attempt`, `prompt`), "watching", or one of the ended states "no_activity", "gave_up", "error"
    (each with a `detail`). `can_resume` says whether the row has resume buttons at all.
    """
    now = time.time() if now is None else now
    with _lock:
        _load()
        hidden = {(d["session_id"], d["key_at"]) for d in _state["dismissed"]}
        scheduled = {s["session_id"]: dict(s) for s in _state["schedules"]}
        runs = {r["session_id"]: dict(r) for r in _runs}
    out: list[dict] = []
    for row in rows:
        if (row["id"], _key_at(row)) in hidden:
            continue
        row = dict(row)
        row["can_resume"] = row["provider"] in ("claude-code", "codex") and row["resets_at"] is not None
        if row["id"] in scheduled:
            s = scheduled[row["id"]]
            row["resume"] = {"state": "scheduled", "fire_at": s["fire_at"], "attempt": s["attempt"],
                             "prompt": s["prompt"]}
        elif row["id"] in runs:
            r = runs[row["id"]]
            row["resume"] = {"state": r["state"], "attempt": r["attempt"], "detail": r["detail"],
                             "mode": r["mode"]}
        out.append(row)
    return out


# ---- the scheduler -------------------------------------------------------------------------

def _notify(label: str, message: str) -> None:
    try:
        from .config import load_config
        if load_config().notifications.get("enabled", False):
            notifications.notify_quota(label, message)
    except Exception:
        log.exception("quota notification failed")


def _label(run: dict) -> str:
    return Path(run["cwd"]).name or run["title"] or "Session"


def _watch(run: dict, now: float) -> str | None:
    """Look at one watched resume. Returns "done", "retry" or "end" when the watch is over, else None.

    The transcript (a Codex rollout, for a Codex thread) is read outside the lock; what is decided from it
    is decided inside, after checking the run is still the one being watched (a dismissal or a new
    schedule may have replaced it). For a Codex thread a turn that started counts as the reply: its
    first answer can be a long way off, and the `reply` kind means only that the prompt got through."""
    if run.get("provider") == "codex":
        progress = quota.codex_progress(run["session_id"])
    else:
        path = quota.claude_session_path(run["session_id"])
        progress = quota.transcript_progress(path) if path else None
    fresh = progress is not None and progress["at"] >= run["launched_at"] - 2.0
    kind = progress["kind"] if fresh else None
    waited = now - run["launched_at"]
    notify = None
    with _lock:
        # `run` is the caller's copy; the record that decides what the row says is the live one.
        live = next((r for r in _runs if r["id"] == run["id"]), None)
        if live is None:
            return "done"   # dismissed or replaced while we were reading the transcript: do nothing
        if kind == "reply":
            return "done"
        if kind == "hit":
            hit = progress["hit"]
            if run["attempt"] < MAX_ATTEMPTS and hit["resets_at"] is not None and hit["resets_at"] > run["resets_at"] + 1.0:
                _load()
                _state["schedules"] = [s for s in _state["schedules"] if s["session_id"] != run["session_id"]] + [{
                    "id": uuid.uuid4().hex, "session_id": run["session_id"], "cwd": run["cwd"],
                    "title": run["title"], "prompt": run["prompt"], "fire_at": hit["resets_at"] + FIRE_DELAY_SECONDS,
                    "resets_at": hit["resets_at"], "attempt": run["attempt"] + 1}]
                _save()
                notify, verdict = "Still limited. Resuming again after the new reset time.", "retry"
            else:
                live["state"], live["detail"] = "gave_up", "The quota limit stopped the resume again."
                notify, verdict = "The quota limit stopped the resume again. Giving up.", "end"
        elif kind == "prompt" and waited > PROMPT_REPLY_SECONDS:
            live["state"], live["detail"] = "no_activity", "No reply yet since the prompt was sent."
            notify, verdict = live["detail"], "end"
        elif kind is None and waited > NO_ACTIVITY_SECONDS:
            live["state"], live["detail"] = "no_activity", "No activity since the resume was started."
            notify, verdict = live["detail"], "end"
        else:
            return None
    _notify(_label(run), notify)
    return verdict


def tick(now: float | None = None) -> None:
    """One scheduler step: fire what is due, then watch what was launched."""
    now = time.time() if now is None else now
    while True:
        # One schedule at a time: it leaves the list just before it fires, so a cancel or a dismissal
        # reaches every schedule that has not started. An exception in one never costs the others theirs.
        with _lock:
            _load()
            due = next((s for s in _state["schedules"] if s["fire_at"] <= now), None)
            if due is None:
                break
            _state["schedules"] = [s for s in _state["schedules"] if s is not due]
            _save()
        if now - due["fire_at"] > LATE_SECONDS:
            log.info("A scheduled resume was %d s overdue and was dropped, not fired late", now - due["fire_at"])
            continue
        try:
            result = resume_now(due["session_id"], due["prompt"], attempt=due["attempt"], now=now,
                                expect_resets_at=due["resets_at"])
        except Exception:
            log.exception("a scheduled resume raised")
            result = _refuse("The scheduled resume failed unexpectedly")
        if result["ok"] or result.get("gone"):
            continue
        with _lock:
            if not any(r["session_id"] == due["session_id"] for r in _runs):
                _runs.append({"id": uuid.uuid4().hex, "session_id": due["session_id"], "cwd": due["cwd"],
                              "title": due["title"], "prompt": due["prompt"], "launched_at": now,
                              "resets_at": due["resets_at"], "attempt": due["attempt"], "mode": "",
                              "state": "error", "detail": result["error"]})
        _notify(Path(due["cwd"]).name or due["title"] or "Claude Code",
                "The scheduled resume did not start: " + result["error"])
    with _lock:
        watching = [dict(r) for r in _runs if r["state"] == "watching"]
    for run in watching:
        try:
            verdict = _watch(run, now)
        except Exception:
            log.exception("watching a resume failed")
            verdict = None
            if now - run["launched_at"] > NO_ACTIVITY_SECONDS:
                # A watch that keeps failing must not leave the row "Resuming…" for ever.
                with _lock:
                    for live in _runs:
                        if live["id"] == run["id"] and live["state"] == "watching":
                            live["state"], live["detail"] = "error", "The resume could not be followed."
        if verdict in ("done", "retry"):
            with _lock:
                _runs[:] = [r for r in _runs if r["id"] != run["id"]]


def restore(now: float | None = None) -> int:
    """At start: drop schedules whose time came while PowerAtlas was down. Returns how many."""
    now = time.time() if now is None else now
    with _lock:
        _load()
        keep = [s for s in _state["schedules"] if s["fire_at"] > now]
        dropped = len(_state["schedules"]) - len(keep)
        if dropped:
            _state["schedules"] = keep
            _save()
    return dropped


def _loop(stop: threading.Event) -> None:
    while not stop.wait(TICK_SECONDS):
        try:
            tick()
        except Exception:
            log.exception("resume scheduler tick failed")


def start_scheduler() -> None:
    """Start the scheduler thread (once). Called from the app's lifespan."""
    with _lock:
        thread = _scheduler[0]
        if thread is not None and thread.is_alive():
            return
        dropped = restore()
        if dropped:
            log.info("Dropped %d scheduled resume(s) whose time passed while PowerAtlas was not running", dropped)
        stop = threading.Event()
        thread = threading.Thread(target=_loop, args=(stop,), name="resume-scheduler", daemon=True)
        _scheduler[:] = [thread, stop]
        thread.start()


def stop_scheduler() -> None:
    thread, stop = _scheduler
    if stop is not None:
        stop.set()
    if thread is not None:
        thread.join(timeout=TICK_SECONDS + 2)
        if thread.is_alive():
            log.warning("The resume scheduler is still finishing a step; it will stop when that ends")
            return
    _scheduler[:] = [None, None]


def reset() -> None:
    """Forget everything in memory (tests)."""
    with _lock:
        _state.update(loaded=False, schedules=[], dismissed=[])
        _runs.clear()
