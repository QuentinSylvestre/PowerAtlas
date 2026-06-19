"""Launch kiro-cli sessions in detected or configured terminals."""

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class LaunchResult:
    success: bool
    session_id: str | None
    workspace: str
    error: str = ""


_SESSION_ID_RE = re.compile(r"^[\w\-]+$")


def detect_terminal(config_override: str = "") -> str | None:
    """Detect terminal. Priority: config > wt > pwsh > cmd."""
    if config_override:
        return config_override
    for name in ("wt", "pwsh", "cmd"):
        path = shutil.which(name)
        if path:
            return path
    return None


def launch_session(
    cwd: str,
    session_id: str | None = None,
    trust_all: bool = False,
    terminal_override: str = "",
) -> LaunchResult:
    """Launch a kiro-cli session in a terminal. Returns result, never raises."""
    terminal = detect_terminal(terminal_override)
    if not terminal:
        return LaunchResult(False, session_id, cwd, error="No terminal found. Configure one in Settings.")

    if not Path(cwd).exists():
        return LaunchResult(False, session_id, cwd, error=f"Folder not found: {cwd}")

    if session_id and not _SESSION_ID_RE.match(session_id):
        return LaunchResult(False, session_id, cwd, error="Invalid session ID format")

    kiro_args = ["kiro-cli", "chat"]
    if session_id:
        kiro_args += ["--resume-id", session_id]
    if trust_all:
        kiro_args.append("-a")

    title = f"kiro-cli - {Path(cwd).name}"
    cmd = _build_command(terminal, cwd, kiro_args, title=title)
    if cmd is None:
        return LaunchResult(False, session_id, cwd, error="Path contains shell metacharacters unsafe for cmd.exe")

    try:
        kwargs: dict = {"creationflags": subprocess.CREATE_NEW_CONSOLE} if sys.platform == "win32" else {"start_new_session": True}
        subprocess.Popen(cmd, **kwargs)
        return LaunchResult(True, session_id, cwd)
    except OSError as e:
        return LaunchResult(False, session_id, cwd, error=str(e))


def launch_batch(
    sessions: list[dict],
    trust_all: bool = False,
    terminal_override: str = "",
) -> list[LaunchResult]:
    """Launch multiple sessions. Never aborts on single failure."""
    results = []
    for s in sessions:
        workspace = s.get("workspace") or "<unknown>"
        if workspace == "<unknown>":
            results.append(LaunchResult(False, s.get("session_id"), workspace, error="Missing 'workspace' key"))
            continue
        results.append(launch_session(
            cwd=workspace,
            session_id=s.get("session_id"),
            trust_all=trust_all,
            terminal_override=terminal_override,
        ))
    return results


_CMD_METACHAR_RE = re.compile(r'[&|<>^%"]')
_TITLE_UNSAFE_RE = re.compile(r'["\'&|]')


def _sanitize_title(title: str) -> str:
    """Strip chars unsafe for shell title injection."""
    return _TITLE_UNSAFE_RE.sub("", title)


def _build_command(terminal: str, cwd: str, kiro_args: list[str], title: str = "") -> list[str] | None:
    """Build terminal-specific command list. Returns None if cwd is unsafe for cmd."""
    t = Path(terminal).stem.lower()

    if "{cwd}" in terminal or "{cmd}" in terminal:
        kiro_cmd = " ".join(kiro_args)
        full = terminal.replace("{cwd}", cwd).replace("{cmd}", kiro_cmd)
        return full.split()

    if t == "wt":
        cmd = [terminal]
        if title:
            cmd += ["--title", _sanitize_title(title)]
        cmd += ["-p", "PowerShell", "-d", cwd, "--", *kiro_args]
        return cmd
    if t == "pwsh":
        escaped_cwd = cwd.replace("'", "''")
        script = ""
        if title:
            safe = _sanitize_title(title).replace("'", "''")
            script = f"$Host.UI.RawUI.WindowTitle = '{safe}'; "
        script += f"Set-Location -LiteralPath '{escaped_cwd}'; & {' '.join(kiro_args)}"
        return [terminal, "-NoExit", "-Command", script]
    # cmd fallback — reject paths with shell metacharacters
    if _CMD_METACHAR_RE.search(cwd):
        return None
    kiro_cmd = " ".join(kiro_args)
    prefix = f"title {_sanitize_title(title)}&& " if title else ""
    return [terminal, "/k", f'{prefix}cd /d "{cwd}" && {kiro_cmd}']



def launch_custom(name: str, command: str, custom_args: str = "", cwd: str = "", env: dict[str, str] | None = None, terminal_override: str = "") -> LaunchResult:
    """Launch a custom command in a terminal."""
    terminal = detect_terminal(terminal_override)
    if not terminal:
        return LaunchResult(False, None, cwd or ".", error="No terminal found.")
    work_dir = cwd or "."
    if not Path(work_dir).exists():
        return LaunchResult(False, None, work_dir, error=f"Folder not found: {work_dir}")
    full_cmd_str = f"{command} {custom_args}".strip() if custom_args else command
    title = _sanitize_title(f"{Path(command).stem} - {Path(work_dir).name}")
    cmd = _build_custom_command(terminal, work_dir, full_cmd_str, title)
    if cmd is None:
        return LaunchResult(False, None, work_dir, error="Path contains unsafe characters for this terminal")
    proc_env = {**os.environ, **env} if env else None
    try:
        kwargs: dict = {"creationflags": subprocess.CREATE_NEW_CONSOLE} if sys.platform == "win32" else {"start_new_session": True}
        if proc_env:
            kwargs["env"] = proc_env
        subprocess.Popen(cmd, **kwargs)
        return LaunchResult(True, None, work_dir)
    except OSError as e:
        return LaunchResult(False, None, work_dir, error=str(e))


def _build_custom_command(terminal: str, cwd: str, cmd_str: str, title: str) -> list[str] | None:
    """Build terminal-specific command for custom launcher. Returns None if unsafe."""
    t = Path(terminal).stem.lower()
    if t == "wt":
        return [terminal, "--title", title, "-p", "PowerShell", "-d", cwd, "--", "cmd", "/c", cmd_str]
    if t == "pwsh":
        escaped_cwd = cwd.replace("'", "''")
        escaped_title = title.replace("'", "''")
        script = f"$Host.UI.RawUI.WindowTitle = '{escaped_title}'; Set-Location -LiteralPath '{escaped_cwd}'; & cmd /c '{cmd_str}'"
        return [terminal, "-NoExit", "-Command", script]
    if _CMD_METACHAR_RE.search(cwd):
        return None
    safe_title = _sanitize_title(title)
    return [terminal, "/k", f'title {safe_title}&& cd /d "{cwd}" && {cmd_str}']
