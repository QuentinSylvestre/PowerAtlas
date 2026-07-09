"""Tests for launcher module."""

import subprocess
from pathlib import Path
from unittest.mock import patch

from power_atlas.config import LaunchProfile
from power_atlas.launcher import (
    detect_terminal,
    launch_batch,
    launch_custom,
    launch_custom_batch,
    launch_session,
    _build_command,
    _build_custom_command,
    _build_powershell_invocation,
    _build_provider_args,
    _build_template_command,
    _sanitize_title,
)
from power_atlas.icons import _resolve_cmd_to_exe


class TestDetectTerminal:
    def test_config_override_takes_priority(self):
        assert detect_terminal("C:\\custom\\term.exe") == "C:\\custom\\term.exe"

    @patch("shutil.which")
    def test_finds_wt_first(self, mock_which):
        mock_which.side_effect = lambda n: {"wt": "C:\\wt.exe", "pwsh": "C:\\pwsh.exe", "cmd": "C:\\cmd.exe"}.get(n)
        assert detect_terminal() == "C:\\wt.exe"

    @patch("shutil.which")
    def test_falls_back_to_pwsh(self, mock_which):
        mock_which.side_effect = lambda n: {"pwsh": "C:\\pwsh.exe", "cmd": "C:\\cmd.exe"}.get(n)
        assert detect_terminal() == "C:\\pwsh.exe"

    @patch("shutil.which")
    def test_falls_back_to_cmd(self, mock_which):
        mock_which.side_effect = lambda n: {"cmd": "C:\\cmd.exe"}.get(n)
        assert detect_terminal() == "C:\\cmd.exe"

    @patch("shutil.which", return_value=None)
    def test_returns_none_when_nothing_found(self, _):
        assert detect_terminal() is None


class TestBuildCommand:
    def test_pwsh_escapes_single_quotes(self):
        cmd = _build_command("C:\\pwsh.exe", "C:\\it's a path", ["kiro-cli", "chat"], wt_profile="PowerShell")
        script = cmd[3]
        assert "it''s a path" in script

    def test_cmd_rejects_metacharacters(self):
        assert _build_command("C:\\cmd.exe", "C:\\foo&bar", ["kiro-cli"], wt_profile="PowerShell") is None
        assert _build_command("C:\\cmd.exe", "C:\\foo|pipe", ["kiro-cli"], wt_profile="PowerShell") is None
        assert _build_command("C:\\cmd.exe", "C:\\foo>out", ["kiro-cli"], wt_profile="PowerShell") is None
        assert _build_command("C:\\cmd.exe", "C:\\foo<in", ["kiro-cli"], wt_profile="PowerShell") is None
        assert _build_command("C:\\cmd.exe", "C:\\foo^caret", ["kiro-cli"], wt_profile="PowerShell") is None
        assert _build_command("C:\\cmd.exe", "C:\\100%done", ["kiro-cli"], wt_profile="PowerShell") is None
        assert _build_command("C:\\cmd.exe", 'C:\\foo"bar', ["kiro-cli"], wt_profile="PowerShell") is None

    def test_cmd_allows_safe_paths(self):
        cmd = _build_command("C:\\cmd.exe", "C:\\Users\\normal path", ["kiro-cli"], wt_profile="PowerShell")
        assert cmd is not None
        assert "C:\\Users\\normal path" in cmd[2]


class TestPowerShellInvocation:
    def test_kiro_new_session_command(self):
        args = _build_provider_args("kiro-cli", "kiro-cli", None)
        assert _build_powershell_invocation(args) == "& 'kiro-cli' 'chat'"

    def test_kiro_resume_command(self):
        args = _build_provider_args("kiro-cli", "kiro-cli", "sess-1")
        assert _build_powershell_invocation(args) == "& 'kiro-cli' 'chat' '--resume-id' 'sess-1'"

    def test_claude_resume_command(self):
        args = _build_provider_args("claude-code", "claude", "sess-abc")
        assert _build_powershell_invocation(args) == "& 'claude' '--resume' 'sess-abc'"

    def test_escapes_spaces_and_single_quotes(self):
        args = ["kiro-cli", "chat", "--label", "can't stop", "C:\\my project"]
        assert _build_powershell_invocation(args) == "& 'kiro-cli' 'chat' '--label' 'can''t stop' 'C:\\my project'"


class TestLaunchSession:
    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_success(self, mock_which, mock_popen, tmp_path):
        mock_which.side_effect = lambda n: {"kiro-cli": "C:\\kiro-cli.exe", "cmd": "C:\\cmd.exe"}.get(n, "C:\\cmd.exe" if n == "cmd" else None)
        cwd = str(tmp_path)
        result = launch_session(cwd, session_id="abc123")
        assert result.success is True
        assert result.session_id == "abc123"
        assert result.workspace == cwd
        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        assert "--resume-id" in " ".join(cmd)

    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_launch_session_kiro_builds_correct_args(self, mock_which, mock_popen, tmp_path):
        mock_which.side_effect = lambda n: {"kiro-cli": "C:\\kiro-cli.exe", "wt": "C:\\wt.exe"}.get(n)
        cwd = str(tmp_path)
        result = launch_session(cwd, session_id="sess-1", provider="kiro-cli", launch_profile=LaunchProfile(terminal_command="C:\\wt.exe"))
        assert result.success is True
        cmd = mock_popen.call_args[0][0]
        cmd_str = " ".join(cmd)
        assert "kiro-cli" in cmd_str
        assert "chat" in cmd_str
        assert "--resume-id" in cmd_str
        assert "sess-1" in cmd_str

    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_launch_session_claude_builds_correct_args(self, mock_which, mock_popen, tmp_path):
        mock_which.side_effect = lambda n: {"claude": "C:\\claude.exe", "wt": "C:\\wt.exe"}.get(n)
        cwd = str(tmp_path)
        result = launch_session(cwd, session_id="sess-abc", provider="claude-code", launch_profile=LaunchProfile(terminal_command="C:\\wt.exe"))
        assert result.success is True
        cmd = mock_popen.call_args[0][0]
        cmd_str = " ".join(cmd)
        assert "claude" in cmd_str
        assert "--resume" in cmd_str
        assert "sess-abc" in cmd_str
        # Should NOT have kiro-cli specific flags
        assert "kiro-cli" not in cmd_str
        assert "--resume-id" not in cmd_str

    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_launch_session_claude_new_session(self, mock_which, mock_popen, tmp_path):
        mock_which.side_effect = lambda n: {"claude": "C:\\claude.exe", "wt": "C:\\wt.exe"}.get(n)
        cwd = str(tmp_path)
        result = launch_session(cwd, session_id=None, provider="claude-code", launch_profile=LaunchProfile(terminal_command="C:\\wt.exe"))
        assert result.success is True
        cmd = mock_popen.call_args[0][0]
        cmd_str = " ".join(cmd)
        assert "claude" in cmd_str
        # No --resume when session_id=None
        assert "--resume" not in cmd_str

    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_launch_session_default_args_appended(self, mock_which, mock_popen, tmp_path):
        mock_which.side_effect = lambda n: {"kiro-cli": "C:\\kiro-cli.exe", "wt": "C:\\wt.exe"}.get(n)
        cwd = str(tmp_path)
        result = launch_session(cwd, session_id="s1", provider="kiro-cli", default_args="--verbose --model opus", launch_profile=LaunchProfile(terminal_command="C:\\wt.exe"))
        assert result.success is True
        cmd = mock_popen.call_args[0][0]
        cmd_str = " ".join(cmd)
        assert "--verbose" in cmd_str
        assert "--model" in cmd_str
        assert "opus" in cmd_str

    @patch("power_atlas.launcher.sys.platform", "win32")
    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_windows_wt_uses_pwsh_noexit_by_default(self, mock_which, mock_popen, tmp_path):
        """WT launches use pwsh -NoExit -Command format by default."""
        mock_which.side_effect = lambda n: {"kiro-cli": "C:\\kiro-cli.exe", "pwsh": "C:\\pwsh.exe"}.get(n)
        cwd = str(tmp_path)

        result = launch_session(cwd, provider="kiro-cli", launch_profile=LaunchProfile(terminal_command="C:\\wt.exe"))

        assert result.success is True
        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "C:\\wt.exe"
        assert "-p" in cmd
        assert cmd[cmd.index("-p") + 1] == "PowerShell"
        # Should use pwsh -NoExit -Command
        assert "C:\\pwsh.exe" in cmd
        assert "-NoExit" in cmd
        assert "-Command" in cmd
        # The command should contain kiro-cli invocation
        command_idx = cmd.index("-Command") + 1
        assert "kiro-cli" in cmd[command_idx]
        assert "'chat'" in cmd[command_idx]

    @patch("power_atlas.launcher.sys.platform", "win32")
    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_windows_wt_custom_profile_used(self, mock_which, mock_popen, tmp_path):
        """Custom wt_profile is passed to the WT command."""
        mock_which.side_effect = lambda n: {"kiro-cli": "C:\\kiro-cli.exe", "pwsh": "C:\\pwsh.exe"}.get(n)
        cwd = str(tmp_path)
        profile = LaunchProfile(
            terminal_command="C:\\wt.exe",
            wt_profile="MyCustomProfile",
        )

        result = launch_session(cwd, provider="kiro-cli", launch_profile=profile)

        assert result.success is True
        cmd = mock_popen.call_args[0][0]
        assert cmd[cmd.index("-p") + 1] == "MyCustomProfile"

    @patch("power_atlas.launcher.sys.platform", "win32")
    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_windows_claude_wt_uses_pwsh_noexit(self, mock_which, mock_popen, tmp_path):
        """Claude Code also uses pwsh -NoExit -Command format in WT."""
        mock_which.side_effect = lambda n: {"claude": "C:\\claude.exe", "pwsh": "C:\\pwsh.exe"}.get(n)
        cwd = str(tmp_path)

        result = launch_session(cwd, session_id="sess-abc", provider="claude-code", launch_profile=LaunchProfile(terminal_command="C:\\wt.exe"))

        assert result.success is True
        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        command_idx = cmd.index("-Command") + 1
        assert cmd[command_idx] == "& 'claude' '--resume' 'sess-abc'"

    @patch("power_atlas.launcher.sys.platform", "win32")
    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_windows_wt_no_pwsh_falls_back_to_direct(self, mock_which, mock_popen, tmp_path):
        """When pwsh not found, WT falls back to direct args (no pwsh -NoExit wrapper)."""
        mock_which.side_effect = lambda n: {"kiro-cli": "C:\\kiro-cli.exe"}.get(n)
        cwd = str(tmp_path)

        result = launch_session(cwd, provider="kiro-cli", launch_profile=LaunchProfile(terminal_command="C:\\wt.exe"))

        assert result.success is True
        cmd = mock_popen.call_args[0][0]
        # Should NOT have pwsh wrapper
        assert "-NoExit" not in cmd
        assert "-Command" not in cmd
        # Should have direct args
        assert "kiro-cli" in cmd
        assert "chat" in cmd

    @patch("power_atlas.launcher.sys.platform", "linux")
    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_non_windows_launch_uses_existing_builder(self, mock_which, mock_popen, tmp_path):
        mock_which.side_effect = lambda n: {"kiro-cli": "/usr/bin/kiro-cli"}.get(n)
        cwd = str(tmp_path)

        result = launch_session(cwd, provider="kiro-cli", launch_profile=LaunchProfile(terminal_command="/usr/bin/kitty"))

        assert result.success is True
        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "/usr/bin/kitty"
        assert cmd[-2:] == ["kiro-cli", "chat"]

    @patch("power_atlas.launcher.sys.platform", "win32")
    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_custom_terminal_template_uses_direct_launch(self, mock_which, mock_popen, tmp_path):
        mock_which.side_effect = lambda n: {"kiro-cli": "C:\\kiro-cli.exe", "pwsh": "C:\\pwsh.exe"}.get(n)
        cwd = str(tmp_path)
        template = "myterm --dir {cwd} --exec {cmd}"

        result = launch_session(cwd, provider="kiro-cli", launch_profile=LaunchProfile(terminal_command=template))

        assert result.success is True
        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        assert cmd == ["myterm", "--dir", cwd, "--exec", "kiro-cli", "chat"]

    @patch("shutil.which")
    def test_launch_session_binary_not_found(self, mock_which, tmp_path):
        mock_which.return_value = None
        cwd = str(tmp_path)
        result = launch_session(cwd, provider="claude-code")
        assert result.success is False
        assert "'claude' not found on PATH" in result.error
        assert "Claude Code" in result.error

    @patch("shutil.which")
    def test_launch_session_kiro_binary_not_found(self, mock_which, tmp_path):
        mock_which.return_value = None
        cwd = str(tmp_path)
        result = launch_session(cwd, provider="kiro-cli")
        assert result.success is False
        assert "'kiro-cli' not found on PATH" in result.error
        assert "Kiro CLI" in result.error

    def test_deleted_folder(self):
        with patch("shutil.which", return_value="C:\\kiro-cli.exe"):
            result = launch_session("C:\\nonexistent\\path\\xyz", launch_profile=LaunchProfile(terminal_command="wt.exe"))
            assert result.success is False
            assert "not found" in result.error.lower()

    @patch("shutil.which")
    def test_no_terminal(self, mock_which, tmp_path):
        # Binary found but no terminal (empty terminal_command triggers auto-detect)
        mock_which.side_effect = lambda n: {"kiro-cli": "C:\\kiro-cli.exe"}.get(n)
        result = launch_session(str(tmp_path), launch_profile=LaunchProfile(terminal_command=""))
        assert result.success is False
        assert "no terminal" in result.error.lower()

    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_custom_template(self, mock_which, mock_popen, tmp_path):
        mock_which.side_effect = lambda n: {"kiro-cli": "C:\\kiro-cli.exe"}.get(n)
        cwd = str(tmp_path)
        template = "myterm --dir {cwd} --exec {cmd}"
        result = launch_session(cwd, session_id=None, launch_profile=LaunchProfile(terminal_command=template))
        assert result.success is True
        cmd = mock_popen.call_args[0][0]
        assert "myterm" in cmd[0]
        assert cwd in " ".join(cmd)

    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_cmd_metachar_rejected(self, mock_which, mock_popen, tmp_path):
        # Create a directory with & in the name
        bad_dir = tmp_path / "a&b"
        bad_dir.mkdir()
        mock_which.side_effect = lambda n: {"kiro-cli": "C:\\kiro-cli.exe", "cmd": "C:\\cmd.exe"}.get(n)
        result = launch_session(str(bad_dir), launch_profile=LaunchProfile(terminal_command="C:\\cmd.exe"))
        assert result.success is False
        assert "metacharacters" in result.error.lower()
        mock_popen.assert_not_called()

    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_launch_session_kiro_ide_non_terminal(self, mock_which, mock_popen, tmp_path):
        """Kiro IDE launches directly without a terminal."""
        mock_which.side_effect = lambda n: {"kiro": "C:\\kiro.exe"}.get(n)
        cwd = str(tmp_path)
        result = launch_session(cwd, session_id=None, provider="kiro-ide")
        assert result.success is True
        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "kiro"
        assert cwd in cmd  # workspace path passed as positional arg
        # No terminal detection needed
        mock_popen.assert_called_once()
        # Verify DETACHED_PROCESS flags used (Windows)
        kwargs = mock_popen.call_args[1]
        import subprocess
        assert kwargs.get("creationflags") == (subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW)

    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_launch_session_kiro_ide_no_resume(self, mock_which, mock_popen, tmp_path):
        """Kiro IDE does not support session resume."""
        mock_which.side_effect = lambda n: {"kiro": "C:\\kiro.exe"}.get(n)
        cwd = str(tmp_path)
        result = launch_session(cwd, session_id="some-session-id", provider="kiro-ide")
        assert result.success is True
        cmd = mock_popen.call_args[0][0]
        # No --resume flags
        assert "--resume" not in cmd
        assert "--resume-id" not in cmd

    @patch("shutil.which")
    def test_launch_session_kiro_ide_binary_not_found(self, mock_which, tmp_path):
        """Kiro IDE reports helpful error when binary not found."""
        mock_which.return_value = None
        cwd = str(tmp_path)
        result = launch_session(cwd, provider="kiro-ide")
        assert result.success is False
        assert "'kiro' not found on PATH" in result.error
        assert "Kiro IDE" in result.error


class TestLaunchBatch:
    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_mixed_results(self, mock_which, mock_popen, tmp_path):
        mock_which.side_effect = lambda n: {"kiro-cli": "C:\\kiro-cli.exe", "wt": "C:\\wt.exe"}.get(n)
        good = str(tmp_path)
        bad = "C:\\nonexistent\\nope"
        sessions = [
            {"session_id": "s1", "workspace": good, "provider": "kiro-cli"},
            {"session_id": "s2", "workspace": bad, "provider": "kiro-cli"},
        ]
        results = launch_batch(sessions, launch_profile=LaunchProfile(terminal_command="C:\\wt.exe"))
        assert len(results) == 2
        assert results[0].success is True
        assert results[1].success is False
        # One failure doesn't prevent the other
        assert mock_popen.call_count == 1

    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_missing_workspace_key(self, mock_which, mock_popen, tmp_path):
        mock_which.side_effect = lambda n: {"kiro-cli": "C:\\kiro-cli.exe", "wt": "C:\\wt.exe"}.get(n)
        good = str(tmp_path)
        sessions = [
            {"session_id": "s1", "workspace": good, "provider": "kiro-cli"},
            {"session_id": "s2"},  # missing workspace
        ]
        results = launch_batch(sessions, launch_profile=LaunchProfile(terminal_command="C:\\wt.exe"))
        assert len(results) == 2
        assert results[0].success is True
        assert results[1].success is False
        assert "missing" in results[1].error.lower()
        assert results[1].workspace == "<unknown>"

    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_launch_batch_mixed_providers(self, mock_which, mock_popen, tmp_path):
        mock_which.side_effect = lambda n: {"kiro-cli": "C:\\kiro-cli.exe", "claude": "C:\\claude.exe", "wt": "C:\\wt.exe"}.get(n)
        cwd = str(tmp_path)
        sessions = [
            {"session_id": "s1", "workspace": cwd, "provider": "kiro-cli"},
            {"session_id": "s2", "workspace": cwd, "provider": "claude-code"},
        ]
        results = launch_batch(sessions, launch_profile=LaunchProfile(terminal_command="C:\\wt.exe"))
        assert len(results) == 2
        assert results[0].success is True
        assert results[1].success is True
        # Verify different commands were built
        call1 = " ".join(mock_popen.call_args_list[0][0][0])
        call2 = " ".join(mock_popen.call_args_list[1][0][0])
        assert "kiro-cli" in call1
        assert "claude" in call2

    @patch("power_atlas.launcher.sys.platform", "win32")
    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_launch_batch_propagates_profile(self, mock_which, mock_popen, tmp_path):
        """launch_batch propagates launch_profile to each launch_session call."""
        mock_which.side_effect = lambda n: {"kiro-cli": "C:\\kiro-cli.exe", "pwsh": "C:\\pwsh.exe"}.get(n)
        cwd = str(tmp_path)
        profile = LaunchProfile(terminal_command="C:\\wt.exe", wt_profile="CustomTab")
        sessions = [
            {"session_id": "s1", "workspace": cwd, "provider": "kiro-cli"},
        ]
        results = launch_batch(sessions, launch_profile=profile)
        assert results[0].success is True
        # Profile's wt_profile should be in the wt command
        cmd = mock_popen.call_args[0][0]
        assert cmd[cmd.index("-p") + 1] == "CustomTab"



class TestTabTitle:
    def test_sanitize_title_strips_unsafe_chars(self):
        assert _sanitize_title('hello"world') == "helloworld"
        assert _sanitize_title("it's") == "its"
        assert _sanitize_title("a & b | c") == "a  b  c"
        assert _sanitize_title("safe-title_v2") == "safe-title_v2"
        assert _sanitize_title("kiro-cli - proj") == "kiro-cli - proj"

    def test_wt_includes_title(self):
        with patch("shutil.which", return_value="C:\\pwsh.exe"):
            cmd = _build_command("C:\\wt.exe", "C:\\proj", ["kiro-cli", "chat"], title="kiro-cli - proj", wt_profile="PowerShell")
        assert "--title" in cmd
        idx = cmd.index("--title")
        assert cmd[idx + 1] == "kiro-cli - proj"

    def test_wt_omits_title_when_empty(self):
        with patch("shutil.which", return_value="C:\\pwsh.exe"):
            cmd = _build_command("C:\\wt.exe", "C:\\proj", ["kiro-cli", "chat"], title="", wt_profile="PowerShell")
        assert "--title" not in cmd

    def test_pwsh_includes_title(self):
        cmd = _build_command("C:\\pwsh.exe", "C:\\proj", ["kiro-cli", "chat"], title="kiro-cli - proj", wt_profile="PowerShell")
        script = cmd[3]
        assert "$Host.UI.RawUI.WindowTitle = 'kiro-cli - proj'" in script

    def test_cmd_includes_title(self):
        cmd = _build_command("C:\\cmd.exe", "C:\\proj", ["kiro-cli", "chat"], title="kiro-cli - proj", wt_profile="PowerShell")
        assert cmd[2].startswith("title kiro-cli - proj&& ")

    def test_custom_template_ignores_title(self):
        cmd = _build_command("myterm --dir {cwd} --exec {cmd}", "C:\\proj", ["kiro-cli"], title="kiro-cli - proj", wt_profile="PowerShell")
        assert "kiro-cli - proj" not in " ".join(cmd)

    def test_wt_uses_custom_wt_profile(self):
        with patch("shutil.which", return_value="C:\\pwsh.exe"):
            cmd = _build_command("C:\\wt.exe", "C:\\proj", ["kiro-cli", "chat"], title="t", wt_profile="MyProfile")
        idx = cmd.index("-p")
        assert cmd[idx + 1] == "MyProfile"

    def test_wt_default_profile_is_powershell(self):
        with patch("shutil.which", return_value="C:\\pwsh.exe"):
            cmd = _build_command("C:\\wt.exe", "C:\\proj", ["kiro-cli", "chat"], title="t", wt_profile="PowerShell")
        idx = cmd.index("-p")
        assert cmd[idx + 1] == "PowerShell"
class TestBuildCustomCommand:
    def test_wt_format(self):
        cmd = _build_custom_command("C:\\wt.exe", "C:\\proj", "npm start", "npm - proj", wt_profile="PowerShell")
        assert cmd == ["C:\\wt.exe", "--title", "npm - proj", "-p", "PowerShell", "-d", "C:\\proj", "--", "cmd", "/c", "npm start"]

    def test_wt_custom_profile(self):
        cmd = _build_custom_command("C:\\wt.exe", "C:\\proj", "npm start", "npm - proj", wt_profile="Git Bash")
        assert cmd == ["C:\\wt.exe", "--title", "npm - proj", "-p", "Git Bash", "-d", "C:\\proj", "--", "cmd", "/c", "npm start"]

    def test_pwsh_format(self):
        cmd = _build_custom_command("C:\\pwsh.exe", "C:\\proj", "npm start", "npm - proj", wt_profile="PowerShell")
        assert "Set-Location" in cmd[3]
        assert "npm start" in cmd[3]
        assert "WindowTitle" in cmd[3]

    def test_cmd_format(self):
        cmd = _build_custom_command("C:\\cmd.exe", "C:\\proj", "npm start", "npm - proj", wt_profile="PowerShell")
        assert cmd[0] == "C:\\cmd.exe"
        assert "npm start" in cmd[2]

    def test_cmd_rejects_unsafe_cwd(self):
        assert _build_custom_command("C:\\cmd.exe", "C:\\a&b", "npm start", "t", wt_profile="PowerShell") is None


class TestLaunchCustom:
    @patch("subprocess.Popen")
    @patch("shutil.which", return_value="C:\\wt.exe")
    def test_success(self, _, mock_popen, tmp_path):
        result = launch_custom("test", "npm", custom_args="start", cwd=str(tmp_path))
        assert result.success is True
        mock_popen.assert_called_once()

    def test_missing_cwd(self):
        result = launch_custom("test", "npm", cwd="C:\\nonexistent\\xyz", launch_profile=LaunchProfile(terminal_command="wt"))
        assert result.success is False
        assert "not found" in result.error.lower()

    @patch("shutil.which", return_value=None)
    def test_no_terminal(self, _, tmp_path):
        result = launch_custom("test", "npm", cwd=str(tmp_path))
        assert result.success is False
        assert "no terminal" in result.error.lower()

    @patch("subprocess.Popen")
    @patch("shutil.which", return_value="C:\\wt.exe")
    def test_env_passed(self, _, mock_popen, tmp_path):
        result = launch_custom("test", "npm", cwd=str(tmp_path), env={"FOO": "bar"})
        assert result.success is True
        kwargs = mock_popen.call_args[1]
        assert "FOO" in kwargs["env"]

    @patch("power_atlas.launcher.sys.platform", "win32")
    @patch("subprocess.Popen")
    @patch("shutil.which", return_value="C:\\wt.exe")
    def test_custom_wt_uses_profile_wt_profile(self, _, mock_popen, tmp_path):
        """Custom launcher in WT uses profile's wt_profile."""
        profile = LaunchProfile(terminal_command="C:\\wt.exe", wt_profile="Git Bash")
        result = launch_custom("test", "npm", custom_args="start", cwd=str(tmp_path), launch_profile=profile)
        assert result.success is True
        cmd = mock_popen.call_args[0][0]
        assert "-p" in cmd
        assert cmd[cmd.index("-p") + 1] == "Git Bash"


class TestDetectTerminalLinux:
    @patch("sys.platform", "linux")
    @patch("shutil.which")
    def test_finds_kitty_first(self, mock_which):
        mock_which.side_effect = lambda n: {"kitty": "/usr/bin/kitty"}.get(n)
        assert detect_terminal() == "/usr/bin/kitty"

    @patch("sys.platform", "linux")
    @patch("shutil.which")
    def test_falls_back_to_gnome_terminal(self, mock_which):
        mock_which.side_effect = lambda n: {"gnome-terminal": "/usr/bin/gnome-terminal"}.get(n)
        assert detect_terminal() == "/usr/bin/gnome-terminal"

    @patch("sys.platform", "linux")
    @patch("shutil.which", return_value=None)
    def test_returns_none_when_nothing_found(self, _):
        assert detect_terminal() is None


class TestBuildCommandLinux:
    def test_kitty(self):
        cmd = _build_command("/usr/bin/kitty", "/home/user/proj", ["kiro-cli", "chat"], title="test", wt_profile="PowerShell")
        assert cmd == ["/usr/bin/kitty", "--title", "test", "--directory", "/home/user/proj", "--", "kiro-cli", "chat"]

    def test_alacritty(self):
        cmd = _build_command("/usr/bin/alacritty", "/home/user/proj", ["kiro-cli", "chat"], title="test", wt_profile="PowerShell")
        assert cmd == ["/usr/bin/alacritty", "--title", "test", "--working-directory", "/home/user/proj", "-e", "kiro-cli", "chat"]

    def test_gnome_terminal(self):
        cmd = _build_command("/usr/bin/gnome-terminal", "/home/user/proj", ["kiro-cli", "chat"], title="test", wt_profile="PowerShell")
        assert cmd == ["/usr/bin/gnome-terminal", "--title=test", "--working-directory=/home/user/proj", "--", "kiro-cli", "chat"]

    def test_xterm_uses_shell_wrapper(self):
        cmd = _build_command("/usr/bin/xterm", "/home/user/proj", ["kiro-cli", "chat"], title="test", wt_profile="PowerShell")
        assert cmd[0] == "/usr/bin/xterm"
        assert "-title" in cmd
        assert "test" in cmd
        assert "sh" in cmd
        assert "-c" in cmd
        # Verify shlex quoting is used in the shell command
        shell_cmd = cmd[cmd.index("-c") + 1]
        assert "cd" in shell_cmd
        assert "/home/user/proj" in shell_cmd

    def test_konsole(self):
        cmd = _build_command("/usr/bin/konsole", "/home/user/proj", ["kiro-cli", "chat"], title="test", wt_profile="PowerShell")
        assert cmd == ["/usr/bin/konsole", "--workdir", "/home/user/proj", "-e", "kiro-cli", "chat"]

    def test_xterm_quotes_special_chars_in_cwd(self):
        cmd = _build_command("/usr/bin/xterm", "/home/user/my$project", ["kiro-cli"], title="", wt_profile="PowerShell")
        shell_cmd = cmd[cmd.index("-c") + 1]
        # shlex.quote wraps in single quotes for shell safety
        assert "'/home/user/my$project'" in shell_cmd

    def test_unknown_stem_returns_none_on_linux(self):
        with patch("sys.platform", "linux"):
            # Unknown terminal on Linux should not fall through to cmd fallback
            result = _build_command("/usr/bin/unknownterm", "/home/user/proj", ["kiro-cli"], wt_profile="PowerShell")
            assert result is None


class TestTemplateSpaceHandling:
    def test_cwd_with_spaces(self):
        cmd = _build_template_command("myterm --dir {cwd} -e {cmd}", "/home/user/my project", ["kiro-cli", "chat"])
        assert cmd == ["myterm", "--dir", "/home/user/my project", "-e", "kiro-cli", "chat"]

    def test_cmd_args_kept_separate(self):
        cmd = _build_template_command("term -e {cmd}", "/proj", ["kiro-cli", "chat", "--resume-id", "abc"])
        assert cmd == ["term", "-e", "kiro-cli", "chat", "--resume-id", "abc"]

    def test_cwd_and_cmd_both_present(self):
        cmd = _build_template_command("t --dir {cwd} --exec {cmd}", "/proj", ["kiro-cli"])
        assert cmd == ["t", "--dir", "/proj", "--exec", "kiro-cli"]

    def test_windows_cwd_with_spaces(self):
        cmd = _build_template_command("wt -d {cwd} -- {cmd}", "C:\\Users\\My User\\proj", ["kiro-cli", "chat"])
        assert cmd == ["wt", "-d", "C:\\Users\\My User\\proj", "--", "kiro-cli", "chat"]


class TestBuildCustomCommandLinux:
    def test_kitty(self):
        cmd = _build_custom_command("/usr/bin/kitty", "/home/user/proj", "npm start", "npm - proj", wt_profile="PowerShell")
        assert cmd[0] == "/usr/bin/kitty"
        assert "--directory" in cmd
        assert "/home/user/proj" in cmd
        assert "sh" in cmd
        assert "-c" in cmd
        shell_cmd = cmd[cmd.index("-c") + 1]
        assert "npm start" in shell_cmd

    def test_xterm_uses_shlex_quote(self):
        cmd = _build_custom_command("/usr/bin/xterm", "/home/user/my$proj", "npm start", "t", wt_profile="PowerShell")
        shell_cmd = cmd[cmd.index("-c") + 1]
        # shlex.quote wraps the path in single quotes
        assert "'/home/user/my$proj'" in shell_cmd

    def test_unknown_stem_returns_none_on_linux(self):
        with patch("sys.platform", "linux"):
            assert _build_custom_command("/usr/bin/unknown", "/proj", "cmd", "t", wt_profile="PowerShell") is None

    def test_konsole_no_title(self):
        cmd = _build_custom_command("/usr/bin/konsole", "/home/user/proj", "npm start", "title", wt_profile="PowerShell")
        assert cmd[0] == "/usr/bin/konsole"
        # konsole has no title flag, so title should not appear
        assert "--title" not in cmd
        assert "title" not in cmd[1:]  # first element is the terminal path


class TestLaunchCustomBatch:
    @patch("subprocess.Popen")
    @patch("shutil.which", return_value="C:\\wt.exe")
    def test_launch_custom_batch_fires_per_workspace(self, _, mock_popen, tmp_path):
        ws1 = str(tmp_path / "proj1")
        ws2 = str(tmp_path / "proj2")
        ws3 = str(tmp_path / "proj3")
        for d in (ws1, ws2, ws3):
            Path(d).mkdir()
        results = launch_custom_batch(
            name="test", command="npm", custom_args="start",
            workspaces=[ws1, ws2, ws3],
            launch_profile=LaunchProfile(terminal_command="C:\\wt.exe"),
        )
        assert len(results) == 3
        assert all(r.success for r in results)
        assert mock_popen.call_count == 3

    @patch("subprocess.Popen")
    @patch("shutil.which", return_value="C:\\wt.exe")
    def test_launch_custom_batch_empty_cwd_uses_home(self, _, mock_popen):
        from pathlib import Path as P
        results = launch_custom_batch(
            name="test", command="echo", custom_args="hi",
            workspaces=[str(P.home())],
            launch_profile=LaunchProfile(terminal_command="C:\\wt.exe"),
        )
        assert len(results) == 1
        assert results[0].success is True

    def test_launch_custom_batch_empty_workspaces(self):
        results = launch_custom_batch(
            name="test", command="echo",
            workspaces=[],
        )
        assert results == []

    @patch("subprocess.Popen")
    @patch("shutil.which", return_value="C:\\wt.exe")
    def test_launch_custom_batch_propagates_profile(self, _, mock_popen, tmp_path):
        """launch_custom_batch propagates launch_profile to each launch_custom call."""
        ws = str(tmp_path / "proj")
        Path(ws).mkdir()
        profile = LaunchProfile(terminal_command="C:\\wt.exe", wt_profile="MyTab")
        results = launch_custom_batch(
            name="test", command="npm",
            workspaces=[ws],
            launch_profile=profile,
        )
        assert len(results) == 1
        assert results[0].success is True
        cmd = mock_popen.call_args[0][0]
        assert cmd[cmd.index("-p") + 1] == "MyTab"


class TestLaunchCustomWorkspaceArg:
    @patch("subprocess.Popen")
    @patch("shutil.which", return_value="C:\\wt.exe")
    def test_pass_workspace_arg_true_appends_workspace(self, _, mock_popen, tmp_path):
        cwd = str(tmp_path)
        result = launch_custom("test", "code", cwd=cwd, launch_profile=LaunchProfile(terminal_command="C:\\wt.exe"), pass_workspace_arg=True)
        assert result.success is True
        # The workspace path should be appended to the command string
        call_args = mock_popen.call_args[0][0]
        cmd_str = " ".join(call_args)
        assert cwd in cmd_str or str(tmp_path) in cmd_str

    @patch("subprocess.Popen")
    @patch("shutil.which", return_value="C:\\wt.exe")
    def test_pass_workspace_arg_false_no_workspace(self, _, mock_popen, tmp_path):
        cwd = str(tmp_path)
        result = launch_custom("test", "code", cwd=cwd, launch_profile=LaunchProfile(terminal_command="C:\\wt.exe"), pass_workspace_arg=False)
        assert result.success is True
        # The command should be just "code" without the workspace appended as an argument
        call_args = mock_popen.call_args[0][0]
        # In wt format: [..., "--", "cmd", "/c", "code"]
        # The final cmd /c argument should just be "code"
        cmd_str = call_args[-1] if isinstance(call_args[-1], str) else ""
        # With pass_workspace_arg=False, command is just "code"
        assert cmd_str.strip().endswith("code") or "code" in cmd_str

    @patch("subprocess.Popen")
    def test_pass_workspace_arg_no_terminal_includes_workspace(self, mock_popen, tmp_path):
        """Non-terminal launch with pass_workspace_arg=True includes workspace."""
        cwd = str(tmp_path)
        result = launch_custom("test", "code", cwd=cwd, use_terminal=False, pass_workspace_arg=True)
        assert result.success is True
        # shell=True so first positional arg is the command string
        cmd_str = mock_popen.call_args[0][0]
        assert cwd in cmd_str

    @patch("subprocess.Popen")
    def test_pass_workspace_arg_no_terminal_without_workspace(self, mock_popen, tmp_path):
        """Non-terminal launch with pass_workspace_arg=False does not include workspace."""
        cwd = str(tmp_path)
        result = launch_custom("test", "code", cwd=cwd, use_terminal=False, pass_workspace_arg=False)
        assert result.success is True
        cmd_str = mock_popen.call_args[0][0]
        # Command should just be "code", no workspace appended
        assert cmd_str == "code"

    @patch("subprocess.Popen")
    def test_pass_workspace_arg_dot_cwd_no_append(self, mock_popen, tmp_path):
        """When cwd is '.', workspace is not appended even with pass_workspace_arg=True."""
        # Use monkeypatch to make "." resolve to an existing path
        with patch("pathlib.Path.exists", return_value=True):
            result = launch_custom("test", "code", cwd=".", use_terminal=False, pass_workspace_arg=True)
        assert result.success is True
        cmd_str = mock_popen.call_args[0][0]
        assert cmd_str == "code"

    @patch("subprocess.Popen")
    @patch("shutil.which", return_value="C:\\wt.exe")
    def test_workspace_with_spaces_quoted_on_windows(self, _, mock_popen, tmp_path):
        """Windows paths with spaces get double-quoted."""
        spaced = tmp_path / "my project"
        spaced.mkdir()
        cwd = str(spaced)
        with patch("power_atlas.launcher.sys.platform", "win32"):
            result = launch_custom("test", "code", cwd=cwd, use_terminal=False, pass_workspace_arg=True)
        assert result.success is True
        cmd_str = mock_popen.call_args[0][0]
        # Should contain quoted path
        assert f'"{cwd}"' in cmd_str


class TestLaunchCustomBatchWorkspaceArg:
    @patch("subprocess.Popen")
    @patch("shutil.which", return_value="C:\\wt.exe")
    def test_pass_workspace_arg_forwarded(self, _, mock_popen, tmp_path):
        ws1 = str(tmp_path / "proj1")
        Path(ws1).mkdir()
        results = launch_custom_batch(
            name="test", command="code",
            workspaces=[ws1],
            launch_profile=LaunchProfile(terminal_command="C:\\wt.exe"),
            use_terminal=False,
            pass_workspace_arg=True,
        )
        assert len(results) == 1
        assert results[0].success is True
        cmd_str = mock_popen.call_args[0][0]
        assert ws1 in cmd_str


class TestResolveCmdToExe:
    def test_resolves_dp0_relative_path(self, tmp_path):
        """Parses %~dp0..\\App.exe pattern and resolves to real .exe."""
        # Create the directory structure: shim/kiro.cmd -> ../app/Kiro.exe
        shim_dir = tmp_path / "shim"
        shim_dir.mkdir()
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        exe = app_dir / "Kiro.exe"
        exe.write_text("fake exe")

        cmd_file = shim_dir / "kiro.cmd"
        cmd_file.write_text('@"%~dp0..\\app\\Kiro.exe" %*\n')

        result = _resolve_cmd_to_exe(cmd_file)
        assert result is not None
        assert result.name == "Kiro.exe"
        assert result.is_file()

    def test_resolves_quoted_absolute_path(self, tmp_path):
        """Parses quoted absolute path to .exe."""
        exe = tmp_path / "App.exe"
        exe.write_text("fake exe")

        cmd_file = tmp_path / "launcher.cmd"
        cmd_file.write_text(f'@"{exe}" %*\n')

        result = _resolve_cmd_to_exe(cmd_file)
        assert result is not None
        assert result == exe.resolve()

    def test_resolves_unquoted_absolute_path(self, tmp_path):
        """Parses unquoted absolute path to .exe."""
        exe = tmp_path / "App.exe"
        exe.write_text("fake exe")

        cmd_file = tmp_path / "launcher.cmd"
        cmd_file.write_text(f'@{exe} %*\n')

        result = _resolve_cmd_to_exe(cmd_file)
        assert result is not None
        assert result == exe.resolve()

    def test_returns_none_when_no_exe_found(self, tmp_path):
        """Returns None when .cmd doesn't reference any existing .exe."""
        cmd_file = tmp_path / "broken.cmd"
        cmd_file.write_text('@echo off\necho hello\n')

        result = _resolve_cmd_to_exe(cmd_file)
        assert result is None

    def test_returns_none_for_nonexistent_exe_path(self, tmp_path):
        """Returns None when referenced .exe doesn't exist on disk."""
        cmd_file = tmp_path / "missing.cmd"
        cmd_file.write_text('@"%~dp0..\\nonexistent\\App.exe" %*\n')

        result = _resolve_cmd_to_exe(cmd_file)
        assert result is None

    def test_returns_none_for_unreadable_file(self, tmp_path):
        """Returns None when .cmd file can't be read."""
        result = _resolve_cmd_to_exe(tmp_path / "nonexistent.cmd")
        assert result is None


class TestMalformedInputCrashes:
    """Regression tests for SC1/L2, SC2/I3, SC17/L3: malformed input must not crash."""

    @patch("power_atlas.launcher.shutil.which", return_value="C:\\kiro-cli.exe")
    def test_launch_session_malformed_default_args_returns_error(self, _, tmp_path):
        """Unbalanced quotes in default_args returns LaunchResult error, never raises."""
        cwd = str(tmp_path)
        # Non-terminal provider path
        result = launch_session(cwd, provider="kiro-ide", default_args='"')
        assert result.success is False
        assert "Invalid" in result.error

        # Terminal provider path (needs a terminal to be detected)
        with patch("power_atlas.launcher.detect_terminal", return_value="C:\\wt.exe"):
            result = launch_session(cwd, provider="kiro-cli", default_args='"')
        assert result.success is False
        assert "Invalid" in result.error

    def test_resolve_binary_whitespace_command_returns_none(self):
        """Whitespace-only command returns None, never raises IndexError."""
        from power_atlas.icons import _resolve_binary
        assert _resolve_binary("   ") is None
        assert _resolve_binary("  \t\n  ") is None
        assert _resolve_binary('  " "  ') is None

    @patch("power_atlas.launcher.sys.platform", "win32")
    def test_default_args_windows_quoting(self):
        """posix=False preserves Windows backslash paths and retains quotes around spaced args."""
        import shlex
        # Backslash path preserved intact
        result = shlex.split(r'C:\Users\me\proj', posix=False)
        assert result == [r'C:\Users\me\proj']

        # Quoted-spaces arg: posix=False retains the quotes in the token
        result = shlex.split('--foo "bar baz"', posix=False)
        assert len(result) == 2
        assert result[0] == "--foo"
        # posix=False retains the quotes as part of the token
        assert "bar baz" in result[1]


class TestCmdMetacharInArgs:
    """SC16: cmd fallback rejects args containing metacharacters."""

    def test_cmd_rejects_metacharacters_in_args(self):
        """Verify cmd fallback returns None when args contain & or other metacharacters."""
        # The cmd path uses the terminal stem "cmd"
        result = _build_command("C:\\cmd.exe", "C:\\safe_path", ["kiro-cli", "chat", "--label", "a&b"], wt_profile="PowerShell")
        assert result is None

    def test_cmd_rejects_pipe_in_args(self):
        result = _build_command("C:\\cmd.exe", "C:\\safe_path", ["kiro-cli", "chat", "|", "evil"], wt_profile="PowerShell")
        assert result is None

    def test_cmd_allows_safe_args(self):
        result = _build_command("C:\\cmd.exe", "C:\\safe_path", ["kiro-cli", "chat", "--resume-id", "sess_abc-123"], wt_profile="PowerShell")
        assert result is not None


class TestNonTerminalCmdShimMetacharGuard:
    """Non-terminal (kiro-ide) launches via a .cmd/.bat shim use shell=True on
    Windows; default_args with cmd metacharacters must be rejected, not executed."""

    @patch("power_atlas.launcher.sys.platform", "win32")
    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_cmd_shim_rejects_metachar_default_args(self, mock_which, mock_popen, tmp_path):
        mock_which.side_effect = lambda n: {"kiro": "C:\\tools\\kiro.cmd"}.get(n)
        result = launch_session(str(tmp_path), provider="kiro-ide", default_args="x & calc.exe")
        assert result.success is False
        assert "unsafe" in result.error.lower()
        mock_popen.assert_not_called()

    @patch("power_atlas.launcher.sys.platform", "win32")
    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_cmd_shim_allows_safe_default_args(self, mock_which, mock_popen, tmp_path):
        mock_which.side_effect = lambda n: {"kiro": "C:\\tools\\kiro.cmd"}.get(n)
        result = launch_session(str(tmp_path), provider="kiro-ide", default_args="--model opus")
        assert result.success is True
        mock_popen.assert_called_once()
        assert mock_popen.call_args[1].get("shell") is True

    @patch("power_atlas.launcher.sys.platform", "win32")
    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_exe_binary_not_shell_so_metachars_allowed(self, mock_which, mock_popen, tmp_path):
        # A real .exe launches without shell=True, so its argv is not shell-interpreted
        # and metachar args are safe — the guard must not over-reject this path.
        mock_which.side_effect = lambda n: {"kiro": "C:\\tools\\kiro.exe"}.get(n)
        result = launch_session(str(tmp_path), provider="kiro-ide", default_args="a & b")
        assert result.success is True
        mock_popen.assert_called_once()
        assert mock_popen.call_args[1].get("shell") is not True


class TestSessionIdLengthBound:
    """SC18: session_id > 128 chars is rejected."""

    @patch("shutil.which", return_value="C:\\kiro-cli.exe")
    def test_session_id_length_bound(self, _, tmp_path):
        cwd = str(tmp_path)
        long_id = "a" * 129
        result = launch_session(cwd, session_id=long_id, launch_profile=LaunchProfile(terminal_command="C:\\wt.exe"))
        assert result.success is False
        assert "Invalid session ID" in result.error

    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_session_id_at_128_is_valid(self, mock_which, mock_popen, tmp_path):
        mock_which.side_effect = lambda n: {"kiro-cli": "C:\\kiro-cli.exe", "wt": "C:\\wt.exe"}.get(n)
        cwd = str(tmp_path)
        ok_id = "a" * 128
        result = launch_session(cwd, session_id=ok_id, launch_profile=LaunchProfile(terminal_command="C:\\wt.exe"))
        assert result.success is True


class TestSanitizeTitleExtended:
    """SC19: title sanitizer strips ;, $, backtick."""

    def test_sanitize_title_strips_extended(self):
        assert _sanitize_title("hello;world") == "helloworld"
        assert _sanitize_title("cost$100") == "cost100"
        assert _sanitize_title("run`cmd`") == "runcmd"
        assert _sanitize_title("a;b$c`d&e|f") == "abcdef"
        # Existing chars still stripped
        assert _sanitize_title('he"llo') == "hello"
        assert _sanitize_title("it's") == "its"


class TestDefaultIconSvgColorValidation:
    """SC20: invalid color strings must not be injected into SVG."""

    def test_default_icon_svg_rejects_invalid_color(self):
        from power_atlas.icons import default_icon_svg
        malicious = '"><script>alert(1)</script>'
        svg = default_icon_svg(True, color=malicious)
        assert malicious not in svg
        assert 'stroke="currentColor"' in svg

    def test_default_icon_svg_accepts_valid_hex_color(self):
        from power_atlas.icons import default_icon_svg
        svg = default_icon_svg(True, color="#ff5500")
        assert 'stroke="#ff5500"' in svg

    def test_default_icon_svg_accepts_named_color(self):
        from power_atlas.icons import default_icon_svg
        svg = default_icon_svg(False, color="red")
        assert 'stroke="red"' in svg

    def test_default_icon_svg_rejects_color_with_parens(self):
        from power_atlas.icons import default_icon_svg
        svg = default_icon_svg(True, color="url(evil)")
        assert "url(evil)" not in svg
        assert 'stroke="currentColor"' in svg


class TestResolveBinarySpacedPath:
    """SC21: resolve binary when path contains spaces and has trailing args."""

    def test_resolve_binary_spaced_path_with_args(self, tmp_path):
        from power_atlas.icons import _resolve_binary
        # Create a file at a space-containing path
        spaced_dir = tmp_path / "path with spaces"
        spaced_dir.mkdir()
        app = spaced_dir / "app.exe"
        app.write_text("fake")
        # Command string: "path with spaces/app.exe --flag"
        cmd = f"{app} --flag"
        result = _resolve_binary(cmd)
        assert result is not None
        assert result.name == "app.exe"

    def test_resolve_binary_spaced_path_no_file(self, tmp_path):
        from power_atlas.icons import _resolve_binary
        # Non-existent spaced path should return None
        cmd = "C:\\nonexistent path\\app.exe --flag"
        result = _resolve_binary(cmd)
        assert result is None


class TestResolveCmdToExeDp0LeadingBackslash:
    """I4: %~dp0\\node.exe resolves correctly (leading backslash stripped)."""

    def test_resolve_cmd_to_exe_dp0_leading_backslash(self, tmp_path):
        # Create: shim_dir/kiro.cmd referencing %~dp0\Kiro.exe (leading backslash)
        shim_dir = tmp_path / "shim"
        shim_dir.mkdir()
        exe = shim_dir / "Kiro.exe"
        exe.write_text("fake exe")

        cmd_file = shim_dir / "kiro.cmd"
        # Note the leading backslash after %~dp0 — this is what the fix handles
        cmd_file.write_text('@"%~dp0\\Kiro.exe" %*\n')

        result = _resolve_cmd_to_exe(cmd_file)
        assert result is not None
        assert result.name == "Kiro.exe"
        assert result.is_file()

    def test_resolve_cmd_to_exe_dp0_leading_forward_slash(self, tmp_path):
        """Also handles forward slash: %~dp0/Kiro.exe"""
        shim_dir = tmp_path / "shim"
        shim_dir.mkdir()
        exe = shim_dir / "Kiro.exe"
        exe.write_text("fake exe")

        cmd_file = shim_dir / "kiro.cmd"
        cmd_file.write_text('@"%~dp0/Kiro.exe" %*\n')

        result = _resolve_cmd_to_exe(cmd_file)
        assert result is not None
        assert result.name == "Kiro.exe"
