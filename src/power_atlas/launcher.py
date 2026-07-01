"""Launch kiro-cli sessions in detected or configured terminals."""

import os
import re
import shlex
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

# Terminal dispatch table: stem -> (title_flag, cwd_flag, exec_separator)
_LINUX_TERMINALS: dict[str, tuple[str | None, str | None, str | None]] = {
    "kitty":          ("--title",  "--directory",          "--"),
    "alacritty":      ("--title",  "--working-directory",  "-e"),
    "gnome-terminal": ("--title=", "--working-directory=", "--"),
    "konsole":        (None,       "--workdir",            "-e"),
    "xterm":          ("-title",   None,                   "-e"),
}

_LINUX_PROBE_ORDER = ("kitty", "alacritty", "gnome-terminal", "konsole", "xterm")


def detect_terminal(config_override: str = "") -> str | None:
    """Detect terminal. Priority: config > platform-specific probe order."""
    if config_override:
        return config_override
    if sys.platform == "win32":
        candidates = ("wt", "pwsh", "cmd")
    else:
        candidates = _LINUX_PROBE_ORDER
    for name in candidates:
        path = shutil.which(name)
        if path:
            return path
    return None


_terminal_cache: list[tuple[str, str]] | None = None


def available_terminals() -> list[tuple[str, str]]:
    """Return (value, label) pairs of detected terminals for the current platform.

    Cached for process lifetime (terminals don't change at runtime).
    Always includes ("" , "Auto-detect (...)") first and ("custom", "Custom") last.
    """
    global _terminal_cache
    if _terminal_cache is not None:
        return list(_terminal_cache)

    if sys.platform == "win32":
        candidates = [("wt", "Windows Terminal"), ("pwsh", "PowerShell"), ("cmd", "Command Prompt")]
    else:
        candidates = [
            ("kitty", "kitty"),
            ("alacritty", "Alacritty"),
            ("gnome-terminal", "GNOME Terminal"),
            ("konsole", "Konsole"),
            ("xterm", "xterm"),
        ]

    found = [(val, label) for val, label in candidates if shutil.which(val)]

    # Build auto-detect label from found terminals
    if found:
        auto_label = f"Auto-detect ({' \u203a '.join(label for _, label in found)})"
    else:
        auto_label = "Auto-detect (none found)"

    result = [("", auto_label)]
    result.extend(found)
    result.append(("custom", "Custom"))
    _terminal_cache = result
    return list(result)


_PROVIDER_DISPLAY = {
    "kiro-cli": "Kiro CLI",
    "claude-code": "Claude Code",
}

_PROVIDER_BINARY = {
    "kiro-cli": "kiro-cli",
    "claude-code": "claude",
}


def launch_session(
    cwd: str,
    session_id: str | None = None,
    provider: str = "kiro-cli",
    default_args: str = "",
    terminal_override: str = "",
) -> LaunchResult:
    """Launch a provider session in a terminal. Returns result, never raises."""
    binary = _PROVIDER_BINARY.get(provider, provider)
    display = _PROVIDER_DISPLAY.get(provider, provider)

    if not shutil.which(binary):
        return LaunchResult(
            False, session_id, cwd,
            error=f"'{binary}' not found on PATH. Install {display} or check your PATH.",
        )

    terminal = detect_terminal(terminal_override)
    if not terminal:
        if sys.platform == "win32":
            msg = "No terminal found. Configure one in Settings."
        else:
            msg = "No terminal found. Install kitty, alacritty, gnome-terminal, konsole, or xterm \u2014 or configure a custom terminal in Settings."
        return LaunchResult(False, session_id, cwd, error=msg)

    if not Path(cwd).exists():
        return LaunchResult(False, session_id, cwd, error=f"Folder not found: {cwd}")

    if session_id and not _SESSION_ID_RE.match(session_id):
        return LaunchResult(False, session_id, cwd, error="Invalid session ID format")

    # Build args based on provider
    if provider == "claude-code":
        cli_args = ["claude"]
        if session_id:
            cli_args += ["--resume", session_id]
    else:
        cli_args = ["kiro-cli", "chat"]
        if session_id:
            cli_args += ["--resume-id", session_id]

    if default_args:
        cli_args += shlex.split(default_args)

    title = f"{display} - {Path(cwd).name}"
    cmd = _build_command(terminal, cwd, cli_args, title=title)
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
    default_args: str = "",
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
            provider=s.get("provider", "kiro-cli"),
            default_args=default_args,
            terminal_override=terminal_override,
        ))
    return results


_CMD_METACHAR_RE = re.compile(r'[&|<>^%"]')
_TITLE_UNSAFE_RE = re.compile(r'["\'&|]')


def _sanitize_title(title: str) -> str:
    """Strip chars unsafe for shell title injection."""
    return _TITLE_UNSAFE_RE.sub("", title)


def _build_template_command(template: str, cwd: str, kiro_args: list[str]) -> list[str]:
    """Build command from user template with {cwd}/{cmd} placeholders.

    Handles paths with spaces by splitting the template around placeholders
    and inserting values as discrete elements.
    """
    parts = re.split(r"(\{cwd\}|\{cmd\})", template)
    result: list[str] = []
    for part in parts:
        if part == "{cwd}":
            result.append(cwd)
        elif part == "{cmd}":
            result.extend(kiro_args)
        else:
            result.extend(p for p in part.split() if p)
    return result


def _linux_base_cmd(terminal: str, cwd: str, title: str, stem: str) -> tuple[list[str], str | None]:
    """Build Linux terminal prefix: terminal + title + cwd + exec_sep.

    Returns (cmd_prefix, cwd_flag) so callers know whether the terminal
    handles cwd natively or needs a shell wrapper.
    """
    title_flag, cwd_flag, exec_sep = _LINUX_TERMINALS[stem]
    cmd: list[str] = [terminal]

    if title and title_flag:
        if title_flag.endswith("="):
            cmd.append(f"{title_flag}{_sanitize_title(title)}")
        else:
            cmd += [title_flag, _sanitize_title(title)]

    if cwd_flag:
        if cwd_flag.endswith("="):
            cmd.append(f"{cwd_flag}{cwd}")
        else:
            cmd += [cwd_flag, cwd]

    if exec_sep:
        cmd.append(exec_sep)

    return cmd, cwd_flag


def _build_linux_command(terminal: str, cwd: str, kiro_args: list[str], title: str, stem: str) -> list[str]:
    """Build command for a Linux terminal using the dispatch table."""
    cmd, cwd_flag = _linux_base_cmd(terminal, cwd, title, stem)

    # For terminals without cwd_flag (xterm), wrap in shell with proper escaping
    if not cwd_flag:
        shell_cmd = f'cd {shlex.quote(cwd)} && exec {" ".join(shlex.quote(a) for a in kiro_args)}'
        cmd += ["sh", "-c", shell_cmd]
    else:
        cmd += kiro_args

    return cmd


def _build_command(terminal: str, cwd: str, kiro_args: list[str], title: str = "") -> list[str] | None:
    """Build terminal-specific command list. Returns None if cwd is unsafe for cmd."""
    t = Path(terminal).stem.lower()

    if "{cwd}" in terminal or "{cmd}" in terminal:
        return _build_template_command(terminal, cwd, kiro_args)

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

    # Linux terminals via dispatch table
    if t in _LINUX_TERMINALS:
        return _build_linux_command(terminal, cwd, kiro_args, title, t)

    # cmd fallback (Windows only)
    if sys.platform != "win32":
        return None
    if _CMD_METACHAR_RE.search(cwd):
        return None
    kiro_cmd = " ".join(kiro_args)
    prefix = f"title {_sanitize_title(title)}&& " if title else ""
    return [terminal, "/k", f'{prefix}cd /d "{cwd}" && {kiro_cmd}']



def launch_custom(name: str, command: str, custom_args: str = "", cwd: str = "", env: dict[str, str] | None = None, terminal_override: str = "", use_terminal: bool = True) -> LaunchResult:
    """Launch a custom command, optionally in a terminal."""
    work_dir = cwd or "."
    if not Path(work_dir).exists():
        return LaunchResult(False, None, work_dir, error=f"Folder not found: {work_dir}")
    full_cmd_str = f"{command} {custom_args}".strip() if custom_args else command
    proc_env = {**os.environ, **env} if env else None
    kwargs: dict = {"creationflags": subprocess.CREATE_NEW_CONSOLE} if sys.platform == "win32" else {"start_new_session": True}
    if proc_env:
        kwargs["env"] = proc_env

    if not use_terminal:
        # Launch directly as a detached process (no terminal window)
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
        try:
            subprocess.Popen(full_cmd_str, cwd=work_dir, shell=True, **kwargs)
            return LaunchResult(True, None, work_dir)
        except OSError as e:
            return LaunchResult(False, None, work_dir, error=str(e))

    terminal = detect_terminal(terminal_override)
    if not terminal:
        if sys.platform == "win32":
            msg = "No terminal found. Configure one in Settings."
        else:
            msg = "No terminal found. Install kitty, alacritty, gnome-terminal, konsole, or xterm — or configure a custom terminal in Settings."
        return LaunchResult(False, None, work_dir, error=msg)
    title = _sanitize_title(f"{Path(command).stem} - {Path(work_dir).name}")
    cmd = _build_custom_command(terminal, work_dir, full_cmd_str, title)
    if cmd is None:
        return LaunchResult(False, None, work_dir, error="Path contains unsafe characters for this terminal")
    try:
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

    # Linux terminals
    if t in _LINUX_TERMINALS:
        cmd, cwd_flag = _linux_base_cmd(terminal, cwd, title, t)
        if not cwd_flag:
            cmd += ["sh", "-c", f'cd {shlex.quote(cwd)} && exec {cmd_str}']
        else:
            # cmd_str is user-authored config, intentionally unquoted (quoting would break shell features)
            cmd += ["sh", "-c", cmd_str]
        return cmd

    # cmd fallback (Windows only)
    if sys.platform != "win32":
        return None
    if _CMD_METACHAR_RE.search(cwd):
        return None
    safe_title = _sanitize_title(title)
    return [terminal, "/k", f'title {safe_title}&& cd /d "{cwd}" && {cmd_str}']
