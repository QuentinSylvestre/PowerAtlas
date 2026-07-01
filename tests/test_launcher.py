"""Tests for launcher module."""

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from power_atlas.launcher import detect_terminal, launch_session, launch_batch, _build_command, _sanitize_title, launch_custom, launch_custom_batch, _build_custom_command, _build_template_command, available_terminals
import power_atlas.launcher as launcher_mod


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
        cmd = _build_command("C:\\pwsh.exe", "C:\\it's a path", ["kiro-cli", "chat"])
        script = cmd[3]
        assert "it''s a path" in script

    def test_cmd_rejects_metacharacters(self):
        assert _build_command("C:\\cmd.exe", "C:\\foo&bar", ["kiro-cli"]) is None
        assert _build_command("C:\\cmd.exe", "C:\\foo|pipe", ["kiro-cli"]) is None
        assert _build_command("C:\\cmd.exe", "C:\\foo>out", ["kiro-cli"]) is None
        assert _build_command("C:\\cmd.exe", "C:\\foo<in", ["kiro-cli"]) is None
        assert _build_command("C:\\cmd.exe", "C:\\foo^caret", ["kiro-cli"]) is None
        assert _build_command("C:\\cmd.exe", "C:\\100%done", ["kiro-cli"]) is None
        assert _build_command("C:\\cmd.exe", 'C:\\foo"bar', ["kiro-cli"]) is None

    def test_cmd_allows_safe_paths(self):
        cmd = _build_command("C:\\cmd.exe", "C:\\Users\\normal path", ["kiro-cli"])
        assert cmd is not None
        assert "C:\\Users\\normal path" in cmd[2]


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
        result = launch_session(cwd, session_id="sess-1", provider="kiro-cli", terminal_override="C:\\wt.exe")
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
        result = launch_session(cwd, session_id="sess-abc", provider="claude-code", terminal_override="C:\\wt.exe")
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
        result = launch_session(cwd, session_id=None, provider="claude-code", terminal_override="C:\\wt.exe")
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
        result = launch_session(cwd, session_id="s1", provider="kiro-cli", default_args="--verbose --model opus", terminal_override="C:\\wt.exe")
        assert result.success is True
        cmd = mock_popen.call_args[0][0]
        cmd_str = " ".join(cmd)
        assert "--verbose" in cmd_str
        assert "--model" in cmd_str
        assert "opus" in cmd_str

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
            result = launch_session("C:\\nonexistent\\path\\xyz", terminal_override="wt.exe")
            assert result.success is False
            assert "not found" in result.error.lower()

    @patch("shutil.which")
    def test_no_terminal(self, mock_which, tmp_path):
        # Binary found but no terminal
        mock_which.side_effect = lambda n: {"kiro-cli": "C:\\kiro-cli.exe"}.get(n)
        result = launch_session(str(tmp_path))
        assert result.success is False
        assert "no terminal" in result.error.lower()

    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_custom_template(self, mock_which, mock_popen, tmp_path):
        mock_which.side_effect = lambda n: {"kiro-cli": "C:\\kiro-cli.exe"}.get(n)
        cwd = str(tmp_path)
        template = "myterm --dir {cwd} --exec {cmd}"
        result = launch_session(cwd, session_id=None, terminal_override=template)
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
        result = launch_session(str(bad_dir), terminal_override="C:\\cmd.exe")
        assert result.success is False
        assert "metacharacters" in result.error.lower()
        mock_popen.assert_not_called()


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
        results = launch_batch(sessions, terminal_override="C:\\wt.exe")
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
        results = launch_batch(sessions, terminal_override="C:\\wt.exe")
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
        results = launch_batch(sessions, terminal_override="C:\\wt.exe")
        assert len(results) == 2
        assert results[0].success is True
        assert results[1].success is True
        # Verify different commands were built
        call1 = " ".join(mock_popen.call_args_list[0][0][0])
        call2 = " ".join(mock_popen.call_args_list[1][0][0])
        assert "kiro-cli" in call1
        assert "claude" in call2



class TestTabTitle:
    def test_sanitize_title_strips_unsafe_chars(self):
        assert _sanitize_title('hello"world') == "helloworld"
        assert _sanitize_title("it's") == "its"
        assert _sanitize_title("a & b | c") == "a  b  c"
        assert _sanitize_title("safe-title_v2") == "safe-title_v2"
        assert _sanitize_title("kiro-cli - proj") == "kiro-cli - proj"

    def test_wt_includes_title(self):
        cmd = _build_command("C:\\wt.exe", "C:\\proj", ["kiro-cli", "chat"], title="kiro-cli - proj")
        assert "--title" in cmd
        idx = cmd.index("--title")
        assert cmd[idx + 1] == "kiro-cli - proj"

    def test_wt_omits_title_when_empty(self):
        cmd = _build_command("C:\\wt.exe", "C:\\proj", ["kiro-cli", "chat"], title="")
        assert "--title" not in cmd

    def test_pwsh_includes_title(self):
        cmd = _build_command("C:\\pwsh.exe", "C:\\proj", ["kiro-cli", "chat"], title="kiro-cli - proj")
        script = cmd[3]
        assert "$Host.UI.RawUI.WindowTitle = 'kiro-cli - proj'" in script

    def test_cmd_includes_title(self):
        cmd = _build_command("C:\\cmd.exe", "C:\\proj", ["kiro-cli", "chat"], title="kiro-cli - proj")
        assert cmd[2].startswith("title kiro-cli - proj&& ")

    def test_custom_template_ignores_title(self):
        cmd = _build_command("myterm --dir {cwd} --exec {cmd}", "C:\\proj", ["kiro-cli"], title="kiro-cli - proj")
        assert "kiro-cli - proj" not in " ".join(cmd)


class TestBuildCustomCommand:
    def test_wt_format(self):
        cmd = _build_custom_command("C:\\wt.exe", "C:\\proj", "npm start", "npm - proj")
        assert cmd == ["C:\\wt.exe", "--title", "npm - proj", "-p", "PowerShell", "-d", "C:\\proj", "--", "cmd", "/c", "npm start"]

    def test_pwsh_format(self):
        cmd = _build_custom_command("C:\\pwsh.exe", "C:\\proj", "npm start", "npm - proj")
        assert "Set-Location" in cmd[3]
        assert "npm start" in cmd[3]
        assert "WindowTitle" in cmd[3]

    def test_cmd_format(self):
        cmd = _build_custom_command("C:\\cmd.exe", "C:\\proj", "npm start", "npm - proj")
        assert cmd[0] == "C:\\cmd.exe"
        assert "npm start" in cmd[2]

    def test_cmd_rejects_unsafe_cwd(self):
        assert _build_custom_command("C:\\cmd.exe", "C:\\a&b", "npm start", "t") is None


class TestLaunchCustom:
    @patch("subprocess.Popen")
    @patch("shutil.which", return_value="C:\\wt.exe")
    def test_success(self, _, mock_popen, tmp_path):
        result = launch_custom("test", "npm", custom_args="start", cwd=str(tmp_path))
        assert result.success is True
        mock_popen.assert_called_once()

    def test_missing_cwd(self):
        result = launch_custom("test", "npm", cwd="C:\\nonexistent\\xyz", terminal_override="wt")
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
        cmd = _build_command("/usr/bin/kitty", "/home/user/proj", ["kiro-cli", "chat"], title="test")
        assert cmd == ["/usr/bin/kitty", "--title", "test", "--directory", "/home/user/proj", "--", "kiro-cli", "chat"]

    def test_alacritty(self):
        cmd = _build_command("/usr/bin/alacritty", "/home/user/proj", ["kiro-cli", "chat"], title="test")
        assert cmd == ["/usr/bin/alacritty", "--title", "test", "--working-directory", "/home/user/proj", "-e", "kiro-cli", "chat"]

    def test_gnome_terminal(self):
        cmd = _build_command("/usr/bin/gnome-terminal", "/home/user/proj", ["kiro-cli", "chat"], title="test")
        assert cmd == ["/usr/bin/gnome-terminal", "--title=test", "--working-directory=/home/user/proj", "--", "kiro-cli", "chat"]

    def test_xterm_uses_shell_wrapper(self):
        cmd = _build_command("/usr/bin/xterm", "/home/user/proj", ["kiro-cli", "chat"], title="test")
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
        cmd = _build_command("/usr/bin/konsole", "/home/user/proj", ["kiro-cli", "chat"], title="test")
        assert cmd == ["/usr/bin/konsole", "--workdir", "/home/user/proj", "-e", "kiro-cli", "chat"]

    def test_xterm_quotes_special_chars_in_cwd(self):
        cmd = _build_command("/usr/bin/xterm", "/home/user/my$project", ["kiro-cli"], title="")
        shell_cmd = cmd[cmd.index("-c") + 1]
        # shlex.quote wraps in single quotes for shell safety
        assert "'/home/user/my$project'" in shell_cmd

    def test_unknown_stem_returns_none_on_linux(self):
        with patch("sys.platform", "linux"):
            # Unknown terminal on Linux should not fall through to cmd fallback
            result = _build_command("/usr/bin/unknownterm", "/home/user/proj", ["kiro-cli"])
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
        cmd = _build_custom_command("/usr/bin/kitty", "/home/user/proj", "npm start", "npm - proj")
        assert cmd[0] == "/usr/bin/kitty"
        assert "--directory" in cmd
        assert "/home/user/proj" in cmd
        assert "sh" in cmd
        assert "-c" in cmd
        shell_cmd = cmd[cmd.index("-c") + 1]
        assert "npm start" in shell_cmd

    def test_xterm_uses_shlex_quote(self):
        cmd = _build_custom_command("/usr/bin/xterm", "/home/user/my$proj", "npm start", "t")
        shell_cmd = cmd[cmd.index("-c") + 1]
        # shlex.quote wraps the path in single quotes
        assert "'/home/user/my$proj'" in shell_cmd

    def test_unknown_stem_returns_none_on_linux(self):
        with patch("sys.platform", "linux"):
            assert _build_custom_command("/usr/bin/unknown", "/proj", "cmd", "t") is None

    def test_konsole_no_title(self):
        cmd = _build_custom_command("/usr/bin/konsole", "/home/user/proj", "npm start", "title")
        assert cmd[0] == "/usr/bin/konsole"
        # konsole has no title flag, so title should not appear
        assert "--title" not in cmd
        assert "title" not in cmd[1:]  # first element is the terminal path


class TestAvailableTerminals:
    def setup_method(self):
        """Reset cache before each test."""
        launcher_mod._terminal_cache = None

    @patch("power_atlas.launcher.sys.platform", "win32")
    @patch("shutil.which")
    def test_windows_with_terminals_found(self, mock_which):
        mock_which.side_effect = lambda n: {"wt": "C:\\wt.exe", "cmd": "C:\\cmd.exe"}.get(n)
        result = available_terminals()
        assert result[0][0] == ""  # auto-detect entry
        assert "Windows Terminal" in result[0][1]
        assert "Command Prompt" in result[0][1]
        assert ("wt", "Windows Terminal") in result
        assert ("cmd", "Command Prompt") in result
        assert result[-1] == ("custom", "Custom")

    @patch("power_atlas.launcher.sys.platform", "linux")
    @patch("shutil.which")
    def test_linux_with_terminals_found(self, mock_which):
        mock_which.side_effect = lambda n: {"kitty": "/usr/bin/kitty", "alacritty": "/usr/bin/alacritty"}.get(n)
        result = available_terminals()
        assert result[0][0] == ""
        assert "kitty" in result[0][1]
        assert "Alacritty" in result[0][1]
        assert ("kitty", "kitty") in result
        assert ("alacritty", "Alacritty") in result
        assert result[-1] == ("custom", "Custom")

    @patch("power_atlas.launcher.sys.platform", "linux")
    @patch("shutil.which", return_value=None)
    def test_no_terminals_found(self, _):
        result = available_terminals()
        assert result[0] == ("", "Auto-detect (none found)")
        assert len(result) == 2  # only auto-detect + custom
        assert result[-1] == ("custom", "Custom")

    @patch("power_atlas.launcher.sys.platform", "win32")
    @patch("shutil.which")
    def test_cache_returns_copy(self, mock_which):
        mock_which.side_effect = lambda n: {"wt": "C:\\wt.exe"}.get(n)
        result1 = available_terminals()
        result1.append(("extra", "Extra"))  # mutate the returned copy
        result2 = available_terminals()
        assert ("extra", "Extra") not in result2  # cache unaffected


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
            terminal_override="C:\\wt.exe",
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
            terminal_override="C:\\wt.exe",
        )
        assert len(results) == 1
        assert results[0].success is True

    def test_launch_custom_batch_empty_workspaces(self):
        results = launch_custom_batch(
            name="test", command="echo",
            workspaces=[],
        )
        assert results == []
