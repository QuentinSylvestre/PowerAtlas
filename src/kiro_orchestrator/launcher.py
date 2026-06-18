"""Launch kiro-cli sessions in detected or configured terminals."""

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

    cmd = _build_command(terminal, cwd, kiro_args)

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


def _build_command(terminal: str, cwd: str, kiro_args: list[str]) -> list[str]:
    """Build terminal-specific command list. Uses list args to avoid shell injection."""
    t = Path(terminal).stem.lower()

    if "{cwd}" in terminal or "{cmd}" in terminal:
        # Custom template — replace placeholders, split on spaces
        kiro_cmd = " ".join(kiro_args)
        full = terminal.replace("{cwd}", cwd).replace("{cmd}", kiro_cmd)
        return full.split()

    if t == "wt":
        return [terminal, "-d", cwd, "--", *kiro_args]
    if t == "pwsh":
        # Use -File/-Command with separate args to avoid shell parsing
        script = f"Set-Location -LiteralPath '{cwd}'; & {' '.join(kiro_args)}"
        return [terminal, "-NoExit", "-Command", script]
    # cmd fallback — /k requires a single string but we quote properly
    kiro_cmd = " ".join(kiro_args)
    return [terminal, "/k", f'cd /d "{cwd}" && {kiro_cmd}']
