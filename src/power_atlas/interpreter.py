"""Resolve the project virtualenv and re-exec into it.

Four entry points independently choose an interpreter — the ``power-atlas``
console script, ``python -m power_atlas``, the autostart shortcut, and
``__main__._relaunch_detached`` — and each inherits whichever environment
invoked it. Left alone they drift apart: the app ends up on one dependency
stack and the test suite on another, and a green suite stops being evidence
about the running app (see ``plans/ROADMAP.md`` § Platform for the security
defect that split has already hidden).

Resolving the venv from the checkout makes the interpreter a property of the
source tree rather than of how the user happened to start the app. When
PowerAtlas is installed from a wheel outside a checkout there is no venv to
find and every function here degrades to ``None``, leaving the caller on
whatever interpreter it already had.
"""

import os
import subprocess
import sys
from pathlib import Path

# Set on the child so a mistaken venv detection costs one wasted process hop
# rather than an unbounded re-exec chain.
REEXEC_SENTINEL = "POWER_ATLAS_VENV_REEXEC"


def project_root() -> Path | None:
    """The checkout root, or None when not running from a source tree."""
    here = Path(__file__).resolve()
    # <root>/src/power_atlas/interpreter.py
    if here.parent.parent.name != "src":
        return None
    root = here.parents[2]
    return root if (root / "pyproject.toml").is_file() else None


def project_venv_dir() -> Path | None:
    """The checkout's virtualenv directory, or None if absent or ambiguous."""
    root = project_root()
    if root is None:
        return None
    for name in (f".venv-{root.name}", ".venv"):
        if _interpreter_in(root / name) is not None:
            return root / name
    candidates = [d for d in root.glob(".venv*") if _interpreter_in(d) is not None]
    return candidates[0] if len(candidates) == 1 else None


def _interpreter_in(venv: Path, *, windowed: bool = False) -> Path | None:
    if sys.platform == "win32":
        exe = venv / "Scripts" / ("pythonw.exe" if windowed else "python.exe")
    else:
        exe = venv / "bin" / "python"
    return exe if exe.is_file() else None


def venv_python(*, windowed: bool = False) -> Path | None:
    """Path to the project venv's interpreter.

    ``windowed`` selects ``pythonw.exe`` on Windows, which runs without
    allocating a console; it has no effect on other platforms.
    """
    venv = project_venv_dir()
    if venv is None:
        return None
    return _interpreter_in(venv, windowed=windowed)


def running_in_project_venv() -> bool:
    """Whether this interpreter is the project venv's.

    Compares ``sys.prefix`` rather than ``sys.executable``: on Windows the
    venv's ``python.exe`` is a redirector that reports the base installation
    as its image path, so comparing executables reports a false negative.
    """
    venv = project_venv_dir()
    if venv is None:
        return False
    try:
        return Path(sys.prefix).resolve() == venv.resolve()
    except OSError:
        return False


def ensure_project_interpreter() -> None:
    """Re-launch this process on the project venv. Returns if already there."""
    if os.environ.get(REEXEC_SENTINEL):
        return
    target = venv_python()
    if target is None or running_in_project_venv():
        return

    os.environ[REEXEC_SENTINEL] = "1"
    cmd = [str(target), "-m", __package__, *sys.argv[1:]]
    if sys.platform == "win32":
        # os.execv hands the console back to the shell the moment the image is
        # replaced, so a --foreground run would interleave its output with the
        # next prompt. Waiting on a child keeps stdio and the exit code intact.
        raise SystemExit(subprocess.run(cmd).returncode)
    os.execv(str(target), cmd)
