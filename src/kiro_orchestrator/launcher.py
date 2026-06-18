"""Launch kiro-cli sessions in detected or configured terminals."""

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

    kiro_cmd = "kiro-cli chat"
    if session_id:
        kiro_cmd += f" --resume-id {session_id}"
    if trust_all:
        kiro_cmd += " -a"

    cmd = _build_command(terminal, cwd, kiro_cmd)

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
    return [
        launch_session(
            cwd=s["workspace"],
            session_id=s.get("session_id"),
            trust_all=trust_all,
            terminal_override=terminal_override,
        )
        for s in sessions
    ]


def _build_command(terminal: str, cwd: str, kiro_cmd: str) -> list[str]:
    """Build terminal-specific command list."""
    t = Path(terminal).stem.lower()

    if "{cwd}" in terminal or "{cmd}" in terminal:
        # Custom template
        full = terminal.replace("{cwd}", cwd).replace("{cmd}", kiro_cmd)
        return full.split()

    if t == "wt":
        return [terminal, "-d", cwd, "--", *kiro_cmd.split()]
    if t == "pwsh":
        return [terminal, "-NoExit", "-Command", f"cd '{cwd}'; {kiro_cmd}"]
    # cmd fallback
    return [terminal, "/k", f'cd /d "{cwd}" && {kiro_cmd}']
