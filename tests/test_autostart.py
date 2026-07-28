"""Tests for autostart and the interpreter resolution it depends on."""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from power_atlas import autostart, interpreter


def _make_venv(root: Path, name: str) -> Path:
    """Create a venv-shaped directory tree with a stub interpreter."""
    venv = root / name
    if sys.platform == "win32":
        exe_dir, exes = venv / "Scripts", ("python.exe", "pythonw.exe")
    else:
        exe_dir, exes = venv / "bin", ("python",)
    exe_dir.mkdir(parents=True)
    for exe in exes:
        (exe_dir / exe).write_text("")
    return venv


@pytest.fixture
def tmp_shortcut(tmp_path, monkeypatch):
    """Patch the shortcut/desktop path to use tmp_path."""
    shortcut_file = tmp_path / "PowerAtlas.lnk"
    if sys.platform == "win32":
        monkeypatch.setattr(autostart, "_windows_shortcut_path", lambda: shortcut_file)
    else:
        desktop_file = tmp_path / "power-atlas.desktop"
        monkeypatch.setattr(autostart, "_linux_desktop_path", lambda: desktop_file)
        shortcut_file = desktop_file
    return shortcut_file


def test_is_enabled_false_when_missing(tmp_shortcut):
    assert autostart.is_enabled() is False


def test_disable_when_missing(tmp_shortcut):
    autostart.disable()  # should not raise
    assert autostart.is_enabled() is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only COM shortcut test")
def test_enable_creates_shortcut_windows(tmp_shortcut):
    mock_shortcut = MagicMock()
    mock_shell = MagicMock()
    mock_shell.CreateShortCut.return_value = mock_shortcut

    with patch("win32com.client.Dispatch", return_value=mock_shell) as mock_dispatch:
        autostart.enable()

    mock_dispatch.assert_called_once_with("WScript.Shell")
    mock_shell.CreateShortCut.assert_called_once_with(str(tmp_shortcut))
    mock_shortcut.save.assert_called_once()
    expected_icon = str(Path(autostart.__file__).parent / "static" / "poweratlas.ico")
    assert mock_shortcut.IconLocation == f"{expected_icon},0"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only COM shortcut test")
def test_enable_targets_project_venv_not_running_interpreter(tmp_shortcut, tmp_path, monkeypatch):
    """The shortcut records the checkout's venv, whatever interpreter toggled it."""
    venv_pythonw = tmp_path / "venv" / "Scripts" / "pythonw.exe"
    monkeypatch.setattr(
        autostart, "venv_python",
        lambda *, windowed=False: venv_pythonw if windowed else venv_pythonw.with_name("python.exe"),
    )
    mock_shortcut = MagicMock()
    mock_shell = MagicMock()
    mock_shell.CreateShortCut.return_value = mock_shortcut

    with patch("win32com.client.Dispatch", return_value=mock_shell):
        autostart.enable()

    assert mock_shortcut.TargetPath == str(venv_pythonw)
    assert mock_shortcut.Arguments == "-m power_atlas"


@pytest.mark.skipif(sys.platform == "win32", reason="Linux-only desktop file test")
def test_enable_creates_desktop_file_linux(tmp_shortcut):
    autostart.enable()
    assert tmp_shortcut.exists()
    content = tmp_shortcut.read_text()
    assert "[Desktop Entry]" in content
    assert "power_atlas" in content


@pytest.mark.skipif(sys.platform == "win32", reason="Linux-only desktop file test")
def test_desktop_file_execs_project_venv_linux(tmp_shortcut, tmp_path, monkeypatch):
    venv_python = tmp_path / "venv" / "bin" / "python"
    monkeypatch.setattr(autostart, "venv_python", lambda *, windowed=False: venv_python)

    autostart.enable()

    assert f"Exec={venv_python} -m power_atlas" in tmp_shortcut.read_text()


def test_startup_interpreter_falls_back_when_no_venv(monkeypatch):
    """A wheel install outside a checkout keeps the interpreter it was given."""
    monkeypatch.setattr(autostart, "venv_python", lambda *, windowed=False: None)

    resolved = autostart._startup_interpreter(windowed=True)

    if sys.platform == "win32":
        assert resolved == Path(sys.executable).parent / "pythonw.exe"
    else:
        assert resolved == Path(sys.executable)


def test_disable_removes_shortcut(tmp_shortcut):
    tmp_shortcut.write_text("")  # simulate existing shortcut
    assert autostart.is_enabled() is True

    autostart.disable()
    assert autostart.is_enabled() is False


class TestProjectVenvDir:
    def test_prefers_venv_named_after_the_checkout(self, tmp_path, monkeypatch):
        root = tmp_path / "PowerAtlas"
        root.mkdir()
        _make_venv(root, ".venv")
        named = _make_venv(root, ".venv-PowerAtlas")
        monkeypatch.setattr(interpreter, "project_root", lambda: root)

        assert interpreter.project_venv_dir() == named

    def test_accepts_a_plain_dot_venv(self, tmp_path, monkeypatch):
        root = tmp_path / "PowerAtlas"
        root.mkdir()
        plain = _make_venv(root, ".venv")
        monkeypatch.setattr(interpreter, "project_root", lambda: root)

        assert interpreter.project_venv_dir() == plain

    def test_ambiguous_venvs_resolve_to_none(self, tmp_path, monkeypatch):
        """Two off-convention venvs: refuse to guess rather than pick one."""
        root = tmp_path / "PowerAtlas"
        root.mkdir()
        _make_venv(root, ".venv-one")
        _make_venv(root, ".venv-two")
        monkeypatch.setattr(interpreter, "project_root", lambda: root)

        assert interpreter.project_venv_dir() is None

    def test_directory_without_an_interpreter_is_not_a_venv(self, tmp_path, monkeypatch):
        root = tmp_path / "PowerAtlas"
        (root / ".venv-PowerAtlas").mkdir(parents=True)
        monkeypatch.setattr(interpreter, "project_root", lambda: root)

        assert interpreter.project_venv_dir() is None

    def test_no_checkout_means_no_venv(self, monkeypatch):
        monkeypatch.setattr(interpreter, "project_root", lambda: None)

        assert interpreter.project_venv_dir() is None
        assert interpreter.venv_python() is None
        assert interpreter.running_in_project_venv() is False


class TestProjectRoot:
    def test_resolves_the_real_checkout(self):
        """The live source tree is a checkout, so resolution must find it."""
        root = interpreter.project_root()

        assert root is not None
        assert (root / "pyproject.toml").is_file()
        assert (root / "src" / "power_atlas" / "interpreter.py").is_file()


class TestEnsureProjectInterpreter:
    def test_noop_when_already_in_the_project_venv(self, monkeypatch):
        monkeypatch.delenv(interpreter.REEXEC_SENTINEL, raising=False)
        monkeypatch.setattr(interpreter, "running_in_project_venv", lambda: True)
        monkeypatch.setattr(interpreter, "venv_python", lambda **_: Path("python"))
        monkeypatch.setattr(interpreter.subprocess, "run", _fail_on_call)

        interpreter.ensure_project_interpreter()  # must not re-exec

    def test_noop_when_no_venv_exists(self, monkeypatch):
        monkeypatch.delenv(interpreter.REEXEC_SENTINEL, raising=False)
        monkeypatch.setattr(interpreter, "venv_python", lambda **_: None)
        monkeypatch.setattr(interpreter, "running_in_project_venv", lambda: False)
        monkeypatch.setattr(interpreter.subprocess, "run", _fail_on_call)

        interpreter.ensure_project_interpreter()

    def test_sentinel_stops_a_re_exec_chain(self, monkeypatch):
        """A wrong detection must cost one hop, not an unbounded chain."""
        monkeypatch.setenv(interpreter.REEXEC_SENTINEL, "1")
        monkeypatch.setattr(interpreter, "venv_python", lambda **_: Path("python"))
        monkeypatch.setattr(interpreter, "running_in_project_venv", lambda: False)
        monkeypatch.setattr(interpreter.subprocess, "run", _fail_on_call)

        interpreter.ensure_project_interpreter()

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows re-exec path")
    def test_re_execs_with_the_venv_interpreter_and_original_args(self, monkeypatch):
        # setenv (not delenv) so teardown also clears what the call itself writes
        monkeypatch.setenv(interpreter.REEXEC_SENTINEL, "")
        target = Path("C:/checkout/.venv-PowerAtlas/Scripts/python.exe")
        monkeypatch.setattr(interpreter, "venv_python", lambda **_: target)
        monkeypatch.setattr(interpreter, "running_in_project_venv", lambda: False)
        monkeypatch.setattr(sys, "argv", ["power-atlas", "--foreground"])
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return MagicMock(returncode=7)

        monkeypatch.setattr(interpreter.subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc:
            interpreter.ensure_project_interpreter()

        assert exc.value.code == 7
        assert calls == [[str(target), "-m", "power_atlas", "--foreground"]]
        assert os.environ[interpreter.REEXEC_SENTINEL] == "1"


def _fail_on_call(*args, **kwargs):
    raise AssertionError("re-exec attempted when none was warranted")
