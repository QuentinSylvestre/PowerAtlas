"""Repository change status for the dashboard: the branch in the rail, the status in the Overview.

One background sweep over every known workspace, one cached result. A request never runs git:
it reads the cache and, when the cache is older than `REFRESH_SECONDS`, starts a sweep and
carries on with what it has. A sweep costs about 0.25 s per repository, so it is paid once a
minute at most and only while the dashboard is being looked at.

Read-only. Nothing is fetched, so "ahead" is measured against the last fetch the user made
and "behind" is not reported. Network and missing folders are skipped before any filesystem
call, the same rule `overview.scan_plans` uses.
"""
from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Callable, Iterable

from . import data, overview

log = logging.getLogger(__name__)

# A cached sweep this old is replaced by a new one (the Overview polls every 60 s).
REFRESH_SECONDS = 60.0
# A folder's repository root rarely moves, so it is looked up once per this long. A folder
# that is not a repository is looked up again on the same schedule, so a new `git init`
# shows up without a restart.
_ROOT_TTL_SECONDS = 300.0
# A sweep that has run this long stops and keeps what it has.
_SWEEP_DEADLINE_SECONDS = 45.0

_lock = threading.Lock()
_state: dict = {"at": 0.0, "running": False, "ready": False, "unavailable": False, "repos": [], "by_cwd": {}}
# normalised folder -> (checked_at, repository root or None)
_roots: dict[str, tuple[float, str | None]] = {}


def parse_status(text: str) -> dict | None:
    """The fields of `git status --porcelain=v2 --branch`, or None without a branch header.

    `changed` counts tracked and staged entries (ordinary, renamed, unmerged); `untracked`
    counts `?` entries. `ahead` is 0 when the branch has no upstream, because git then omits
    the `branch.ab` line.
    """
    head = None
    oid = ""
    upstream = ""
    ahead = 0
    changed = 0
    untracked = 0
    for line in text.splitlines():
        if line.startswith("# branch.head "):
            head = line[len("# branch.head "):].strip()
        elif line.startswith("# branch.oid "):
            oid = line[len("# branch.oid "):].strip()
        elif line.startswith("# branch.upstream "):
            upstream = line[len("# branch.upstream "):].strip()
        elif line.startswith("# branch.ab "):
            parts = line.split()
            try:
                ahead = int(parts[2].lstrip("+"))
            except (IndexError, ValueError):
                ahead = 0
        elif line.startswith(("1 ", "2 ", "u ")):
            changed += 1
        elif line.startswith("? "):
            untracked += 1
    if head is None:
        return None
    detached = head == "(detached)"
    return {
        "branch": ("detached " + oid[:7]) if detached else head,
        "detached": detached,
        "unborn": oid == "(initial)",
        "upstream": bool(upstream),
        "ahead": ahead,
        "changed": changed,
        "untracked": untracked,
    }


def _root(cwd: str, now: float) -> str | None:
    """The repository root containing `cwd`, or None."""
    key = data._normalize_path(cwd)
    hit = _roots.get(key)
    if hit and now - hit[0] < _ROOT_TTL_SECONDS:
        return hit[1]
    out = overview._git_output(cwd, "rev-parse", "--show-toplevel")
    root = os.path.normpath(out.strip()) if out and out.strip() else None
    _roots[key] = (now, root)
    return root


def _probe(root: str) -> dict | None:
    """Status of one repository, or None when git fails or answers nothing usable.

    `core.fsmonitor` is switched off for the call: a repository can configure a program there, and
    `git status` would run it. A sweep over every workspace must not run code a cloned repository chose."""
    out = overview._git_output(root, "-c", "core.fsmonitor=false", "status", "--porcelain=v2", "--branch")
    info = parse_status(out) if out is not None else None
    if info is None:
        return None
    # A branch with no upstream has no "ahead" to count. It is flagged only when it holds
    # commits no remote branch has (and a remote exists to push them to), so a scratch repo
    # that was never meant to leave the machine stays quiet.
    info["no_upstream"] = False
    if not info["upstream"] and not info["detached"] and not info["unborn"]:
        remotes = overview._git_output(root, "remote")
        if remotes and remotes.strip():
            count = overview._git_output(root, "rev-list", "--count", "HEAD", "--not", "--remotes")
            try:
                info["no_upstream"] = bool(count and int(count.strip()) > 0)
            except ValueError:
                pass
    return info


def sweep(cwds: Iterable[str], now: float | None = None,
          deadline_s: float = _SWEEP_DEADLINE_SECONDS, previous: dict | None = None) -> dict:
    """Probe every repository the folders in `cwds` belong to, once each.

    Returns `{"repos": [...], "by_cwd": {normalised folder: branch label}}`. Several folders
    inside one repository share one probe. A repository that could not be probed this time (git
    failed or timed out, or the deadline came first) keeps its entry from `previous`, the last
    sweep's result, so one slow repository does not make every dirty one look clean.
    """
    now = time.time() if now is None else now
    previous = previous or {"repos": [], "by_cwd": {}}
    stop = time.monotonic() + deadline_s
    roots: dict[str, tuple[str, list[str]]] = {}
    cut_short = False
    for cwd in dict.fromkeys(cwds):
        if overview._skip_cwd(cwd):
            continue
        if time.monotonic() > stop:
            log.warning("git status sweep stopped at its deadline while locating repositories")
            cut_short = True
            break
        root = _root(cwd, now)
        if root:
            roots.setdefault(os.path.normcase(root), (root, []))[1].append(cwd)
    repos: list[dict] = []
    by_cwd: dict[str, str] = {}
    stopped = False
    for root, folders in roots.values():
        info = None
        if not stopped and time.monotonic() > stop:
            stopped = True
            log.warning("git status sweep stopped at its deadline; the rest keep their last result")
        if not stopped:
            info = _probe(root)
        if info is None:
            # Not probed this time: carry the last known entry (and the branch of each folder in it).
            old = next((r for r in previous["repos"]
                        if os.path.normcase(r["path"]) == os.path.normcase(root)), None)
            if old is None:
                continue
            repos.append(dict(old))
            for cwd in folders:
                key = data._normalize_path(cwd)
                if key in previous["by_cwd"]:
                    by_cwd[key] = previous["by_cwd"][key]
            continue
        info["path"] = root
        info["name"] = Path(root).name or root
        repos.append(info)
        for cwd in folders:
            by_cwd[data._normalize_path(cwd)] = info["branch"]
    if cut_short:
        # Folders never reached keep what the last sweep knew about them.
        have = {os.path.normcase(r["path"]) for r in repos}
        repos.extend(dict(r) for r in previous["repos"] if os.path.normcase(r["path"]) not in have)
        for key, branch in previous["by_cwd"].items():
            by_cwd.setdefault(key, branch)
    return {"repos": repos, "by_cwd": by_cwd}


def _run(workspaces: Callable[[], list]) -> None:
    result = None
    unavailable = shutil.which("git") is None
    try:
        if not unavailable:
            with _lock:
                previous = {"repos": [dict(r) for r in _state["repos"]], "by_cwd": dict(_state["by_cwd"])}
            result = sweep((w[0] for w in workspaces()), previous=previous)
    except Exception:
        log.exception("git status sweep failed")
    with _lock:
        _state["unavailable"] = unavailable
        if result is not None:
            _state["repos"] = result["repos"]
            _state["by_cwd"] = result["by_cwd"]
            _state["ready"] = True
        # `at` moves on a failure too, so a broken sweep is retried at the normal pace.
        _state["at"] = time.time()
        _state["running"] = False


def request(workspaces: Callable[[], list]) -> bool:
    """Start a background sweep when the cache is older than `REFRESH_SECONDS`.

    `workspaces` returns `[(cwd, name), ...]` and runs on the sweep thread. Single-flight.
    Returns True when a sweep was started.
    """
    with _lock:
        if _state["running"] or time.time() - _state["at"] < REFRESH_SECONDS:
            return False
        _state["running"] = True
    threading.Thread(target=_run, args=(workspaces,), name="gitstate-sweep", daemon=True).start()
    return True


def attention(repos: list[dict]) -> list[dict]:
    """The repositories worth a line in the Overview, most urgent first.

    Tracked changes, commits ahead of the upstream, or local-only commits on a branch with
    no upstream. A repository whose only difference is untracked files is not listed; its
    untracked count shows as a quiet chip once it is listed for another reason.
    """
    shown = [r for r in repos if r["changed"] or r["ahead"] or r["no_upstream"]]
    shown.sort(key=lambda r: (-r["changed"], -r["ahead"], r["name"].lower()))
    return shown


def overview_payload(workspaces: Callable[[], list]) -> dict:
    """The Overview's `git` block: `{"state": "warming" | "ready", "repos": [...]}`.

    Starts a sweep when the cache is stale. The repository list is a copy, so a caller may
    reshape it without touching the cache.
    """
    request(workspaces)
    with _lock:
        ready = _state["ready"]
        unavailable = _state["unavailable"]
        repos = [dict(r) for r in attention(_state["repos"])]
    # "unavailable": git is not installed or not on PATH, which must not read as "everything is clean".
    return {"state": "unavailable" if unavailable else ("ready" if ready else "warming"), "repos": repos}


def branch_for(cwd: str) -> str:
    """The branch label for a workspace folder, or "" when it is unknown or not a repository."""
    key = data._normalize_path(cwd)
    with _lock:
        return _state["by_cwd"].get(key, "")


def reset() -> None:
    """Forget everything (tests)."""
    with _lock:
        _state.update(at=0.0, running=False, ready=False, unavailable=False, repos=[], by_cwd={})
        _roots.clear()
