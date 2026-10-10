"""Tests for launcher module."""

import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from power_atlas.config import LaunchProfile
from power_atlas.launcher import (
    detect_terminal,
    launch_batch,
    launch_custom,
    launch_custom_batch,
    launch_session,
    launch_terminal,
    _build_command,
    _build_custom_command,
    _build_powershell_invocation,
    _build_provider_args,
    _build_template_command,
    _build_terminal_only_command,
    _quote_powershell_arg,
    _sanitize_cmd_title,
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
    def test_claude_resume_command(self):
        args = _build_provider_args("claude-code", "claude", "sess-abc")
        assert _build_powershell_invocation(args) == "& 'claude' '--resume' 'sess-abc'"

    def test_escapes_spaces_and_single_quotes(self):
        args = ["kiro-cli", "chat", "--label", "can't stop", "C:\\my project"]
        assert _build_powershell_invocation(args) == "& 'kiro-cli' 'chat' '--label' 'can''t stop' 'C:\\my project'"


    def test_kiro_v3_new_session_args(self):
        args = _build_provider_args("kiro-cli-v3", "kiro-cli", None)
        assert args == ["kiro-cli", "chat", "--agent-engine", "v3", "--trust-tools", "*"]

    def test_kiro_v3_resume_args(self):
        args = _build_provider_args("kiro-cli-v3", "kiro-cli", "sess_abc")
        assert args == ["kiro-cli", "chat", "--agent-engine", "v3", "--trust-tools", "*", "--resume-id", "sess_abc"]

    def test_kiro_v3_is_terminal_provider(self):
        from power_atlas.launcher import _PROVIDER_TERMINAL
        assert _PROVIDER_TERMINAL.get("kiro-cli-v3") is True

    def test_kiro_v3_default_args_from_settings_not_hardcoded(self):
        """--trust-tools * is baked into _build_provider_args (not in default_args / config)."""
        base = _build_provider_args("kiro-cli-v3", "kiro-cli", None)
        assert "--trust-tools" in base
        assert "*" in base
        assert base == ["kiro-cli", "chat", "--agent-engine", "v3", "--trust-tools", "*"]

    def test_kiro_v3_provider_binary_display_shows_trust_tools(self):
        """_PROVIDER_BINARY_DISPLAY for kiro-cli-v3 shows the full built-in command including --trust-tools *."""
        from power_atlas.web import _PROVIDER_BINARY_DISPLAY
        display = _PROVIDER_BINARY_DISPLAY.get("kiro-cli-v3", "")
        assert "--trust-tools" in display
        assert "--agent-engine v3" in display


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

    @patch("power_atlas.launcher.subprocess.Popen")
    @patch("power_atlas.launcher.shutil.which")
    def test_launch_session_kiro_builds_correct_args(self, mock_which, mock_popen, monkeypatch, tmp_path):
        monkeypatch.setenv("CLAUDECODE", "1")
        monkeypatch.setenv("CLAUDE_PID", "999")
        mock_which.side_effect = lambda n: {"kiro-cli": "C:\\kiro-cli.exe", "wt": "C:\\wt.exe"}.get(n)
        cwd = str(tmp_path)
        result = launch_session(cwd, session_id="sess_1abc", provider="kiro-cli-v3", launch_profile=LaunchProfile(terminal_command="C:\\wt.exe"))
        assert result.success is True
        cmd = mock_popen.call_args[0][0]
        cmd_str = " ".join(cmd)
        assert "kiro-cli" in cmd_str
        assert "chat" in cmd_str
        assert "--resume-id" in cmd_str
        assert "sess_1abc" in cmd_str
        assert "--agent-engine" in cmd_str
        assert "--trust-tools" in cmd_str
        kwargs = mock_popen.call_args.kwargs
        assert kwargs["env"]["POWER_ATLAS_SESSION"] == "1"
        assert "CLAUDECODE" not in kwargs["env"]
        assert "CLAUDE_PID" not in kwargs["env"]

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
        result = launch_session(cwd, session_id="sess_s1", provider="kiro-cli-v3", default_args="--verbose --model opus", launch_profile=LaunchProfile(terminal_command="C:\\wt.exe"))
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

        result = launch_session(cwd, provider="kiro-cli-v3", launch_profile=LaunchProfile(terminal_command="C:\\wt.exe"))

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

        result = launch_session(cwd, provider="kiro-cli-v3", launch_profile=profile)

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

        result = launch_session(cwd, provider="kiro-cli-v3", launch_profile=LaunchProfile(terminal_command="C:\\wt.exe"))

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

        result = launch_session(cwd, provider="kiro-cli-v3", launch_profile=LaunchProfile(terminal_command="/usr/bin/kitty"))

        assert result.success is True
        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "/usr/bin/kitty"
        assert "kiro-cli" in cmd
        assert "chat" in cmd

    @patch("power_atlas.launcher.sys.platform", "win32")
    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_custom_terminal_template_uses_direct_launch(self, mock_which, mock_popen, tmp_path):
        mock_which.side_effect = lambda n: {"kiro-cli": "C:\\kiro-cli.exe", "pwsh": "C:\\pwsh.exe"}.get(n)
        cwd = str(tmp_path)
        template = "myterm --dir {cwd} --exec {cmd}"

        result = launch_session(cwd, provider="kiro-cli-v3", launch_profile=LaunchProfile(terminal_command=template))

        assert result.success is True
        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        assert cmd[:4] == ["myterm", "--dir", cwd, "--exec"]
        assert "kiro-cli" in cmd
        assert "chat" in cmd

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
        result = launch_session(cwd, provider="kiro-cli-v3")
        assert result.success is False
        assert "'kiro-cli' not found on PATH" in result.error
        assert "Install kiro-cli" in result.error

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
        # Review finding R3 (261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 2, round 2): the failure text is terminal-neutral now, because a None
        # from _build_command also covers the PowerShell refusal of a control character; it matches launch_terminal's wording.
        assert result.error == "Path contains characters unsafe for this terminal"
        mock_popen.assert_not_called()

    @patch("power_atlas.launcher.subprocess.Popen")
    @patch("power_atlas.launcher.shutil.which")
    def test_launch_session_kiro_ide_non_terminal(self, mock_which, mock_popen, monkeypatch, tmp_path):
        """Kiro IDE launches directly without a terminal."""
        monkeypatch.setenv("CLAUDECODE", "1")
        monkeypatch.setenv("CLAUDE_PID", "999")
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
        kwargs = mock_popen.call_args.kwargs
        import subprocess
        assert kwargs.get("creationflags") == (subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW)
        assert kwargs["env"]["POWER_ATLAS_SESSION"] == "1"
        assert "CLAUDECODE" not in kwargs["env"]
        assert "CLAUDE_PID" not in kwargs["env"]

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

    @patch("power_atlas.launcher.subprocess.Popen")
    @patch("power_atlas.launcher.shutil.which")
    def test_launch_session_scrubs_claude_markers(self, mock_which, mock_popen, monkeypatch, tmp_path):
        """CLAUDECODE, CLAUDE_CODE_*, CLAUDE_PID, NO_COLOR absent from launched session env; POWER_ATLAS_SESSION present."""
        monkeypatch.setenv("CLAUDECODE", "1")
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "abc")
        monkeypatch.setenv("CLAUDE_PID", "999")
        monkeypatch.setenv("NO_COLOR", "1")
        mock_which.side_effect = lambda n: {"kiro-cli": "C:\\kiro-cli.exe", "wt": str(tmp_path / "wt.exe")}.get(n)
        cwd = str(tmp_path)
        result = launch_session(cwd, session_id=None, provider="kiro-cli-v3", launch_profile=LaunchProfile(terminal_command=str(tmp_path / "wt.exe")))
        assert result.success is True
        env = mock_popen.call_args.kwargs["env"]
        assert "CLAUDECODE" not in env
        assert "CLAUDE_CODE_SESSION_ID" not in env
        assert "CLAUDE_PID" not in env
        assert "NO_COLOR" not in env
        assert env["POWER_ATLAS_SESSION"] == "1"


class TestLaunchSessionPrompt:
    """`launch_session(prompt=...)`: `claude --resume <id> "<prompt>"` in a terminal, safely quoted."""

    _WT = LaunchProfile(terminal_command="C:\\wt.exe")

    @staticmethod
    def _which(name):
        return {"claude": "C:\\claude.exe", "wt": "C:\\wt.exe", "pwsh": "C:\\pwsh.exe"}.get(name)

    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_the_prompt_is_the_last_argument_and_quoted(self, mock_which, mock_popen, tmp_path):
        mock_which.side_effect = self._which
        result = launch_session(str(tmp_path), session_id="sess-abc", provider="claude-code",
                                launch_profile=self._WT, prompt="resume")
        assert result.success is True
        script = mock_popen.call_args[0][0][-1]
        assert script == "& 'claude' '--resume' 'sess-abc' 'resume'"

    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_default_args_come_before_the_prompt(self, mock_which, mock_popen, tmp_path):
        mock_which.side_effect = self._which
        launch_session(str(tmp_path), session_id="sess-abc", provider="claude-code", default_args="--model haiku",
                       launch_profile=self._WT, prompt="go on")
        assert mock_popen.call_args[0][0][-1] == "& 'claude' '--resume' 'sess-abc' '--model' 'haiku' 'go on'"

    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_every_quote_character_in_a_prompt_is_doubled(self, mock_which, mock_popen, tmp_path):
        mock_which.side_effect = self._which
        launch_session(str(tmp_path), session_id="sess-abc", provider="claude-code",
                       launch_profile=self._WT, prompt="it's \u2018odd\u2019 \"x\"")
        script = mock_popen.call_args[0][0][-1]
        assert script.endswith("'it''s \u2018\u2018odd\u2019\u2019 \"x\"'")

    @pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is not installed")
    def test_the_prompt_reaches_the_program_unchanged_through_a_real_powershell(self):
        """The quoting is exercised end to end: a prompt full of shell syntax arrives as one argument."""
        import json
        import sys
        prompts = ["resume", "it's here", "say \"hi\"", "$(whoami) `n ; & calc | more", "a  b   c",
                   "\u2018smart\u2019 \u201cquotes\u201d", "100% {done} [ok] <x> ^caret"]
        for prompt in prompts:
            line = _build_powershell_invocation(
                [sys.executable, "-c", "import sys, json; print(json.dumps(sys.argv[1:]))", prompt])
            done = subprocess.run([shutil.which("pwsh"), "-NoProfile", "-Command", line],
                                  capture_output=True, text=True, encoding="utf-8", timeout=60)
            assert json.loads(done.stdout.strip().splitlines()[-1]) == [prompt], prompt

    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_a_prompt_that_could_change_the_command_is_refused(self, mock_which, mock_popen, tmp_path):
        mock_which.side_effect = self._which
        for bad in ("-p hello", "--dangerously-skip-permissions", "two\nlines", "tab\there", "x" * 4001):
            result = launch_session(str(tmp_path), session_id="sess-abc", provider="claude-code",
                                    launch_profile=self._WT, prompt=bad)
            assert result.success is False and result.error == "Invalid prompt", repr(bad[:30])
        mock_popen.assert_not_called()

    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_a_provider_that_takes_no_terminal_prompt_refuses_one(self, mock_which, mock_popen, tmp_path):
        mock_which.side_effect = lambda n: {"kiro": "C:\\kiro.exe"}.get(n)
        result = launch_session(str(tmp_path), provider="kiro-ide", prompt="resume")
        assert result.success is False and "cannot be started with a prompt" in result.error
        mock_popen.assert_not_called()

    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_a_codex_resume_takes_the_prompt_after_the_id_and_default_arguments(self, mock_which, mock_popen, tmp_path):
        mock_which.side_effect = lambda n: {"codex": "C:\\codex.exe", "wt": "C:\\wt.exe", "pwsh": "C:\\pwsh.exe"}.get(n)
        sid = "0199c8f2-7a3b-7c41-9e5d-3b8a1f6e2d40"
        result = launch_session(str(tmp_path), session_id=sid, provider="codex", default_args="--model gpt-x",
                                launch_profile=self._WT, prompt="carry on")
        assert result.success is True
        assert mock_popen.call_args[0][0][-1] == f"& 'codex' 'resume' '{sid}' '--model' 'gpt-x' 'carry on'"

    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_windows_terminal_gets_every_semicolon_escaped(self, mock_which, mock_popen, tmp_path):
        """An unescaped `;` splits a Windows Terminal command line, so a prompt cannot carry one raw."""
        mock_which.side_effect = self._which
        result = launch_session(str(tmp_path), session_id="sess-abc", provider="claude-code",
                                launch_profile=self._WT, prompt="fix a; then b")
        assert result.success is True
        script = mock_popen.call_args[0][0][-1]
        assert script.endswith("'fix a\\; then b'")
        assert ";" not in script.replace("\\;", "")

    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_a_wt_template_escapes_semicolons_too(self, mock_which, mock_popen, tmp_path):
        mock_which.side_effect = self._which
        template = "C:\\wt.exe -d {cwd} -- pwsh -NoExit -Command {pscmd}"
        result = launch_session(str(tmp_path), session_id="sess-abc", provider="claude-code",
                                launch_profile=LaunchProfile(terminal_command=template), prompt="a; b")
        assert result.success is True
        cmd = mock_popen.call_args[0][0]
        assert cmd[-1].endswith("'a\\; b'")

    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_a_template_for_another_program_is_not_semicolon_escaped(self, mock_which, mock_popen, tmp_path):
        mock_which.side_effect = self._which
        launch_session(str(tmp_path), session_id="sess-abc", provider="claude-code",
                       launch_profile=LaunchProfile(terminal_command="myterm --exec {cmd}"), prompt="a; b")
        assert mock_popen.call_args[0][0][-1] == "a; b"

    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_a_prompt_holding_the_escape_sequence_is_refused_for_windows_terminal(self, mock_which, mock_popen, tmp_path):
        """`\\;` would arrive as a plain `;` after the escaping, so the prompt would not be the one typed."""
        mock_which.side_effect = self._which
        result = launch_session(str(tmp_path), session_id="sess-abc", provider="claude-code",
                                launch_profile=self._WT, prompt="path C:\\dir\\; end")
        assert result.success is False and "semicolon" in result.error
        mock_popen.assert_not_called()

    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_a_cmd_terminal_refuses_a_prompt(self, mock_which, mock_popen, tmp_path):
        """cmd.exe re-parses the joined command line: the prompt's words become options and `&` starts a command."""
        mock_which.side_effect = lambda n: {"claude": "C:\\claude.exe", "cmd": "C:\\Windows\\cmd.exe"}.get(n)
        for terminal in ("C:\\Windows\\cmd.exe", "C:\\Windows\\cmd.exe /c start {cwd} {cmd}"):
            result = launch_session(str(tmp_path), session_id="sess-abc", provider="claude-code",
                                    launch_profile=LaunchProfile(terminal_command=terminal), prompt="resume")
            assert result.success is False and "cmd.exe" in result.error, terminal
        mock_popen.assert_not_called()


class TestCodexLaunch:
    """261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 2:
    Codex launches as `codex` / `codex resume <uuid>` with no baked flags."""

    _SID = "0199c8f2-7a3b-7c41-9e5d-3b8a1f6e2d40"

    def test_the_three_launcher_tables_know_codex(self):
        from power_atlas.launcher import _PROVIDER_BINARY, _PROVIDER_DISPLAY, _PROVIDER_TERMINAL
        assert _PROVIDER_DISPLAY["codex"] == "Codex"
        assert _PROVIDER_BINARY["codex"] == "codex"
        assert _PROVIDER_TERMINAL["codex"] is True

    def test_new_session_is_the_bare_binary(self):
        assert _build_provider_args("codex", "codex", None) == ["codex"]
        # An empty id is falsy and means "new session", as for Claude Code.
        assert _build_provider_args("codex", "codex", "") == ["codex"]

    def test_resume_takes_the_subcommand_then_the_id_and_nothing_else(self):
        # The binary argument is used as given (a resolved path, not the id "codex").
        args = _build_provider_args("codex", "codex-bin", self._SID)
        assert args == ["codex-bin", "resume", self._SID]

    def test_an_upper_case_uuid_is_accepted(self):
        sid = self._SID.upper()
        assert _build_provider_args("codex", "codex", sid) == ["codex", "resume", sid]

    @pytest.mark.parametrize("bad", [
        "--flag",                       # passes launcher._SESSION_ID_RE, must not reach `codex resume`
        "-h",
        "--last",
        "abc123",
        "sess_abc",
        "0199c8f2-7a3b-7c41-9e5d-3b8a1f6e2d4",     # one hex digit short
        "0199c8f2-7a3b-7c41-9e5d-3b8a1f6e2d40a",   # one hex digit long
        "0199c8f27a3b7c419e5d3b8a1f6e2d40",         # no hyphens
        "0199c8f2-7a3b-7c41-9e5d-3b8a1f6e2dg0",     # non-hex digit
        "0199c8f2-7a3b-7c41-9e5d-3b8a1f6e2d40\n",   # "$" would accept a trailing newline
        "\n0199c8f2-7a3b-7c41-9e5d-3b8a1f6e2d40",
        " 0199c8f2-7a3b-7c41-9e5d-3b8a1f6e2d40",
        "0199c8f2-7a3b-7c41-9e5d-3b8a1f6e2d40 --last",
        # ASCII only: each of these is 36 characters of the right shape, so a
        # pattern using \d, [0-9a-f\-]{36} or \w would accept it.
        chr(0xFF10) + "199c8f2-7a3b-7c41-9e5d-3b8a1f6e2d40",       # fullwidth zero in a digit position
        "0199c8f2-7a3b-7c41-9e5d-3b8a1f6e2d4" + chr(0xFF10),       # fullwidth zero in the last position
        "-" * 36,                                                    # right length, right alphabet, no structure
        "0199c8f2-7a3b-7c41-9e5d-3b8a1f6e2d4" + chr(0x212A),       # Kelvin sign (case-folds to k)
        "0199c8f2-7a3b-7c41-9e5d-3b8a1f6e2d4" + chr(0),            # NUL
    ], ids=[
        "flag", "h", "last", "short-word", "sess-id", "one-short", "one-long", "no-hyphens",
        "non-hex", "trailing-newline", "leading-newline", "leading-space", "trailing-flag",
        "fullwidth-digit-first", "fullwidth-digit-last", "36-hyphens", "kelvin-sign", "nul",
    ])
    def test_a_non_uuid_id_is_refused(self, bad):
        with pytest.raises(ValueError, match="Invalid Codex session id"):
            _build_provider_args("codex", "codex", bad)

    @patch("power_atlas.launcher._build_command", return_value=["term"])
    @patch("power_atlas.launcher.subprocess.Popen")
    @patch("power_atlas.launcher.shutil.which")
    def test_launch_session_runs_codex_resume_with_default_args_after_the_id(
            self, mock_which, mock_popen, mock_build, tmp_path):
        mock_which.side_effect = lambda n: {"codex": "C:\\codex.exe", "wt": "C:\\wt.exe"}.get(n)
        result = launch_session(str(tmp_path), session_id=self._SID, provider="codex",
                                default_args="--model gpt-x --flag2",
                                launch_profile=LaunchProfile(terminal_command="C:\\wt.exe"))
        assert result.success is True
        assert result.session_id == self._SID
        cli_args = mock_build.call_args[0][2]
        assert cli_args == ["codex", "resume", self._SID, "--model", "gpt-x", "--flag2"]
        assert "Codex" in mock_build.call_args.kwargs["title"]
        mock_popen.assert_called_once()
        assert mock_popen.call_args[0][0] == ["term"]

    @patch("power_atlas.launcher._build_command", return_value=["term"])
    @patch("power_atlas.launcher.subprocess.Popen")
    @patch("power_atlas.launcher.shutil.which")
    def test_launch_session_new_codex_session_has_no_flags(
            self, mock_which, mock_popen, mock_build, tmp_path):
        mock_which.side_effect = lambda n: {"codex": "C:\\codex.exe", "wt": "C:\\wt.exe"}.get(n)
        result = launch_session(str(tmp_path), session_id=None, provider="codex",
                                launch_profile=LaunchProfile(terminal_command="C:\\wt.exe"))
        assert result.success is True
        assert mock_build.call_args[0][2] == ["codex"]

    @patch("power_atlas.launcher.subprocess.Popen")
    @patch("power_atlas.launcher.shutil.which")
    def test_launch_session_command_line_carries_resume_and_id(self, mock_which, mock_popen, tmp_path):
        mock_which.side_effect = lambda n: {"codex": "C:\\codex.exe", "wt": "C:\\wt.exe"}.get(n)
        result = launch_session(str(tmp_path), session_id=self._SID, provider="codex",
                                launch_profile=LaunchProfile(terminal_command="C:\\wt.exe"))
        assert result.success is True
        cmd_str = " ".join(mock_popen.call_args[0][0])
        assert cmd_str.endswith("-- codex resume " + self._SID)
        assert "kiro" not in cmd_str and "--resume" not in cmd_str

    @patch("power_atlas.launcher.subprocess.Popen")
    @patch("power_atlas.launcher.shutil.which")
    def test_launch_session_default_terminal_quotes_the_codex_invocation_for_pwsh(self, mock_which, mock_popen, tmp_path):
        """With pwsh on PATH the wt terminal runs one quoted PowerShell command line
        (each argv element in single quotes), not the bare args of the fallback."""
        mock_which.side_effect = lambda n: {"codex": "C:\\codex.exe", "wt": "C:\\wt.exe", "pwsh": "C:\\pwsh.exe"}.get(n)
        result = launch_session(str(tmp_path), session_id=self._SID, provider="codex",
                                default_args="--model gpt-x",
                                launch_profile=LaunchProfile(terminal_command="C:\\wt.exe"))
        assert result.success is True
        cmd = mock_popen.call_args[0][0]
        sep = cmd.index("--")
        assert cmd[sep:] == ["--", "C:\\pwsh.exe", "-NoExit", "-Command",
                             "& 'codex' 'resume' '" + self._SID + "' '--model' 'gpt-x'"]

    @pytest.mark.parametrize("bad", ["abc123", "sess_0199c8f2-7a3b-7c41-9e5d-3b8a1f6e2d40"])
    @patch("power_atlas.launcher.subprocess.Popen")
    @patch("power_atlas.launcher.shutil.which")
    def test_launch_session_refuses_a_non_uuid_codex_id_without_launching(
            self, mock_which, mock_popen, bad, tmp_path):
        """Both ids pass the generic launcher id check (it takes any alphanumeric-led
        id); the Codex branch is what stops them. The ids the generic check used to let
        through ("--flag", a trailing newline) are now stopped by it first, and are
        covered in TestSessionIdShape (261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 2)."""
        mock_which.side_effect = lambda n: {"codex": "C:\\codex.exe", "wt": "C:\\wt.exe"}.get(n)
        result = launch_session(str(tmp_path), session_id=bad, provider="codex",
                                launch_profile=LaunchProfile(terminal_command="C:\\wt.exe"))
        assert result.success is False
        assert "Invalid Codex session id" in result.error
        mock_popen.assert_not_called()

    @patch("power_atlas.launcher.shutil.which", return_value=None)
    def test_launch_session_reports_a_missing_codex_binary(self, _which, tmp_path):
        result = launch_session(str(tmp_path), provider="codex")
        assert result.success is False
        assert "'codex' not found on PATH" in result.error
        assert "Codex" in result.error


# The four single-quote characters PowerShell treats as a literal's delimiter
# besides ASCII ': U+2018, U+2019, U+201A, U+201B. Built with chr() so the test
# source holds no invisible or look-alike characters.
_CURLY_SINGLES = [chr(0x2018), chr(0x2019), chr(0x201A), chr(0x201B)]
# Characters that end a statement or line: C0 controls, DEL, NEL, U+2028, U+2029.
_LINE_BREAKERS = [chr(c) for c in (*range(0x20), 0x7F, 0x85, 0x2028, 0x2029)]
_TYPOGRAPHIC_QUOTES = [chr(c) for c in range(0x2018, 0x2020)]


def _find_powershell() -> str | None:
    return shutil.which("pwsh") or shutil.which("powershell")


_PARSE_ONLY_SCRIPT = (
    "$ErrorActionPreference='Stop';"
    "$in=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:PA_TEST_SCRIPTS_B64))|ConvertFrom-Json;"
    "$out=@();"
    "foreach($b in $in){"
    "$s=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($b));"
    "$e=$null;$t=$null;"
    "$ast=[System.Management.Automation.Language.Parser]::ParseInput($s,[ref]$t,[ref]$e);"
    "$st=@($ast.EndBlock.Statements);"
    "$v='';"
    "if($st.Count -gt 0 -and $st[0].PipelineElements -and $st[0].PipelineElements[0].Expression -is [System.Management.Automation.Language.StringConstantExpressionAst]){"
    "$v=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($st[0].PipelineElements[0].Expression.Value))};"
    "$out+=[pscustomobject]@{n=$st.Count;errors=@($e).Count;value=$v}};"
    "ConvertTo-Json -Compress -InputObject @($out)"
)


def _parse_only(scripts: list[str]) -> list[dict]:
    """Parse each script with PowerShell's own parser, in one subprocess.

    ParseInput builds a syntax tree and executes nothing. Returns, per script,
    the number of top-level statements, the parse-error count and (when the
    first statement is a bare string literal) that literal's decoded value.
    """
    import base64
    import json
    import os

    exe = _find_powershell()
    if exe is None:
        pytest.skip("no PowerShell on PATH")
    payload = base64.b64encode(json.dumps(
        [base64.b64encode(s.encode("utf-8")).decode("ascii") for s in scripts]).encode("utf-8")).decode("ascii")
    done = subprocess.run(
        [exe, "-NoProfile", "-NonInteractive", "-Command", _PARSE_ONLY_SCRIPT],
        env={**os.environ, "PA_TEST_SCRIPTS_B64": payload},
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert done.returncode == 0, done.stderr
    rows = json.loads(done.stdout)
    for row in rows:
        row["value"] = base64.b64decode(row["value"]).decode("utf-8") if row["value"] else None
    return rows


class TestTerminalQuoting:
    """261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 2:
    titles and folders reach a PowerShell or cmd script string; they must stay data."""

    _TERM = "C:\\pwsh.exe"
    _CMD = "C:\\cmd.exe"

    # -- _sanitize_title ----------------------------------------------------

    @pytest.mark.parametrize("ch", _LINE_BREAKERS, ids=lambda c: f"U+{ord(c):04X}")
    def test_sanitize_title_removes_every_line_breaking_character(self, ch):
        assert _sanitize_title("a" + ch + "b") == "ab"

    @pytest.mark.parametrize("ch", _TYPOGRAPHIC_QUOTES, ids=lambda c: f"U+{ord(c):04X}")
    def test_sanitize_title_removes_every_typographic_quote(self, ch):
        assert _sanitize_title("a" + ch + "b") == "ab"

    def test_sanitize_title_keeps_ordinary_text_unchanged(self):
        for ordinary in ("Codex - proj - fix the bug", "kiro-cli - my project (2)", "a_b-c.d [e]", "Caf" + chr(0xE9) + " - " + chr(0x4E2D) + chr(0x6587)):
            assert _sanitize_title(ordinary) == ordinary

    def test_sanitize_cmd_title_also_removes_redirection_and_escape_characters(self):
        assert _sanitize_cmd_title("a<b>c^d") == "abcd"
        assert _sanitize_cmd_title("kiro-cli - proj") == "kiro-cli - proj"
        # The shared sanitizer is unchanged for the other terminals: < > ^ stay.
        assert _sanitize_title("a<b>c^d") == "a<b>c^d"

    # -- the quoting helper -------------------------------------------------

    @pytest.mark.parametrize("ch", ["'", *_CURLY_SINGLES], ids=lambda c: f"U+{ord(c):04X}")
    def test_quote_doubles_each_single_quote_character_exactly_once(self, ch):
        # Twice, not four times: a value is escaped by this helper only.
        assert _quote_powershell_arg("x" + ch + "y") == "'x" + ch + ch + "y'"

    def test_quote_doubles_an_ascii_quote_next_to_a_curly_one(self):
        rsq, lsq = _CURLY_SINGLES[1], _CURLY_SINGLES[0]
        assert _quote_powershell_arg("a'" + rsq + "b" + lsq + "'") == "'a''" + rsq + rsq + "b" + lsq + lsq + "''" + "'"

    def test_quote_leaves_an_ordinary_value_alone(self):
        assert _quote_powershell_arg("C:\\Program Files\\x") == "'C:\\Program Files\\x'"

    def test_invocation_quotes_a_hostile_binary_and_argument(self):
        rsq = _CURLY_SINGLES[1]
        got = _build_powershell_invocation(["C:\\it" + rsq + "s\\codex.exe", "a'" + rsq])
        assert got == "& 'C:\\it" + rsq + rsq + "s\\codex.exe' 'a''" + rsq + rsq + "'"

    # -- the pwsh scripts: string level -------------------------------------

    @pytest.mark.parametrize("title", [
        "x" + _CURLY_SINGLES[1] + "\ncalc.exe\n" + _CURLY_SINGLES[1] + "x",
        "x" + _CURLY_SINGLES[0] + "\r\ncalc.exe\r\n" + _CURLY_SINGLES[3] + "x",
        "x" + _CURLY_SINGLES[2] + chr(0x2028) + "calc.exe" + chr(0x2029) + _CURLY_SINGLES[1] + "x",
        "x'" + _CURLY_SINGLES[1] + "\ncalc.exe\n'" + _CURLY_SINGLES[0] + "x",
    ], ids=["rsquo-lf", "lsquo-crlf-low9", "low9-ls-ps", "ascii-curly-mix"])
    @pytest.mark.parametrize("builder", ["session", "terminal_only", "custom"])
    def test_a_hostile_title_leaves_no_quote_or_line_break_in_the_script(self, builder, title):
        script = self._script(builder, cwd="C:\\proj", title=title)
        # The title reduces to "xcalc.exex"; nothing in it can reach the script as syntax.
        assert script.startswith("$Host.UI.RawUI.WindowTitle = 'xcalc.exex'; Set-Location -LiteralPath 'C:\\proj'")
        for ch in _LINE_BREAKERS + _CURLY_SINGLES:
            assert ch not in script

    @pytest.mark.parametrize("quote", ["'", *_CURLY_SINGLES], ids=lambda c: f"U+{ord(c):04X}")
    @pytest.mark.parametrize("builder", ["session", "terminal_only", "custom"])
    def test_a_folder_quote_is_doubled_in_the_set_location_literal(self, builder, quote):
        script = self._script(builder, cwd="C:\\it" + quote + "s", title="t")
        assert "Set-Location -LiteralPath 'C:\\it" + quote + quote + "s'" in script

    @pytest.mark.parametrize("builder", ["session", "terminal_only", "custom"])
    def test_a_folder_quote_mix_is_doubled_without_double_escaping(self, builder):
        rsq, lsq = _CURLY_SINGLES[1], _CURLY_SINGLES[0]
        script = self._script(builder, cwd="C:\\a'" + rsq + lsq + "b", title="t")
        assert "Set-Location -LiteralPath 'C:\\a''" + rsq + rsq + lsq + lsq + "b'" in script

    @pytest.mark.parametrize("ch", _LINE_BREAKERS, ids=lambda c: f"U+{ord(c):04X}")
    @pytest.mark.parametrize("builder", ["session", "terminal_only", "custom"])
    def test_a_folder_with_a_line_breaking_character_is_refused(self, builder, ch):
        assert self._script(builder, cwd="C:\\pr" + ch + "oj", title="t") is None

    def test_a_custom_command_string_is_quoted_not_spliced(self):
        rsq = _CURLY_SINGLES[1]
        cmd = _build_custom_command(self._TERM, "C:\\proj", "run it's" + rsq, "t", wt_profile="PowerShell")
        assert cmd[3].endswith("; & cmd /c 'run it''s" + rsq + rsq + "'")

    def test_wt_with_pwsh_quotes_a_hostile_argument(self):
        rsq = _CURLY_SINGLES[1]
        with patch("shutil.which", return_value="C:\\pwsh.exe"):
            cmd = _build_command("C:\\wt.exe", "C:\\proj", ["codex", "resume", "a'" + rsq], title="t", wt_profile="PowerShell")
        assert cmd[-1] == "& 'codex' 'resume' 'a''" + rsq + rsq + "'"

    def test_ordinary_title_and_folder_script_is_unchanged(self):
        cmd = _build_command(self._TERM, "C:\\Users\\normal path", ["codex", "resume", "abc"], title="Codex - proj", wt_profile="PowerShell")
        assert cmd == [self._TERM, "-NoExit", "-Command",
                       "$Host.UI.RawUI.WindowTitle = 'Codex - proj'; Set-Location -LiteralPath 'C:\\Users\\normal path'; & 'codex' 'resume' 'abc'"]

    # -- the pwsh scripts: parsed by PowerShell itself (parse only) ----------

    @pytest.mark.parametrize("builder", ["session", "terminal_only", "custom"])
    def test_powershell_parses_a_hostile_script_to_the_same_statements_as_a_benign_one(self, builder):
        rsq, lsq = _CURLY_SINGLES[1], _CURLY_SINGLES[0]
        injected = "x" + rsq + "\ncalc.exe\n" + rsq + "x"
        benign = self._script(builder, cwd="C:\\proj", title="Codex - proj")
        hostile_title = self._script(builder, cwd="C:\\proj", title=injected)
        hostile_cwd = self._script(builder, cwd="C:\\pr" + rsq + " calc.exe " + rsq + "o'" + lsq + "j", title="t")
        base, from_title, from_cwd = _parse_only([benign, hostile_title, hostile_cwd])
        assert base["errors"] == 0
        assert from_title == {**base, "value": from_title["value"]}
        assert from_cwd == {**base, "value": from_cwd["value"]}

    @pytest.mark.parametrize("value", [
        "plain",
        "it's",
        "x" + _CURLY_SINGLES[1] + "y",
        "'" + _CURLY_SINGLES[0],
        _CURLY_SINGLES[2] + _CURLY_SINGLES[3] + "'",
        "a'" + _CURLY_SINGLES[1] + "\ncalc.exe\n" + _CURLY_SINGLES[1] + "b",
    ], ids=["plain", "ascii", "rsquo", "ascii-lsquo", "low9-rev9-ascii", "injection-shaped"])
    def test_powershell_reads_a_quoted_value_back_as_the_original_single_literal(self, value):
        (row,) = _parse_only([_quote_powershell_arg(value)])
        assert row["errors"] == 0
        assert row["n"] == 1
        assert row["value"] == value

    # -- the cmd fallback ----------------------------------------------------

    def test_cmd_fallback_title_drops_redirection_and_escape_characters(self):
        cmd = _build_command(self._CMD, "C:\\proj", ["kiro-cli"], title="a<b>c^d - p", wt_profile="PowerShell")
        assert cmd[2] == 'title abcd - p&& cd /d "C:\\proj" && kiro-cli'

    def test_cmd_fallback_title_drops_a_line_break(self):
        cmd = _build_command(self._CMD, "C:\\proj", ["kiro-cli"], title="a\r\nnotepad\nb", wt_profile="PowerShell")
        assert cmd[2] == 'title anotepadb&& cd /d "C:\\proj" && kiro-cli'

    def test_cmd_fallback_custom_and_terminal_only_titles_drop_them_too(self):
        custom = _build_custom_command(self._CMD, "C:\\proj", "npm start", "a<b>c^d", wt_profile="PowerShell")
        assert custom[2] == 'title abcd&& cd /d "C:\\proj" && npm start'
        only = _build_terminal_only_command(self._CMD, "C:\\proj", title="a<b>c^d")
        assert only[2] == 'title abcd&& cd /d "C:\\proj"'

    # -- R1 (round 2): "%" in a cmd title expands %CMDCMDLINE:~N,1% into "&" --

    _PCT_TITLE = "x%CMDCMDLINE:~15,1%y 100% done"
    _PCT_TITLE_CLEAN = "xCMDCMDLINE:~15,1y 100 done"

    def test_sanitize_cmd_title_removes_percent_but_the_shared_sanitizer_keeps_it(self):
        assert _sanitize_cmd_title(self._PCT_TITLE) == self._PCT_TITLE_CLEAN
        assert _sanitize_title(self._PCT_TITLE) == self._PCT_TITLE

    def test_every_cmd_builder_removes_percent_from_the_title(self):
        session = _build_command(self._CMD, "C:\\proj", ["kiro-cli"], title=self._PCT_TITLE, wt_profile="PowerShell")
        custom = _build_custom_command(self._CMD, "C:\\proj", "npm start", self._PCT_TITLE, wt_profile="PowerShell")
        only = _build_terminal_only_command(self._CMD, "C:\\proj", title=self._PCT_TITLE)
        prefix = f"title {self._PCT_TITLE_CLEAN}&& cd /d "
        assert session[2].startswith(prefix)
        assert custom[2].startswith(prefix)
        assert only[2].startswith(prefix)
        for cmd in (session, custom, only):
            assert "%" not in cmd[2]

    def test_cmd_titles_without_special_characters_are_unchanged(self):
        for title in ("Codex - proj - fix: the bug (2)", "kiro-cli - my project [x]"):
            assert _build_command(self._CMD, "C:\\proj", ["kiro-cli"], title=title, wt_profile="PowerShell")[2].startswith(f"title {title}&& ")
            assert _build_custom_command(self._CMD, "C:\\proj", "npm", title, wt_profile="PowerShell")[2].startswith(f"title {title}&& ")
            assert _build_terminal_only_command(self._CMD, "C:\\proj", title=title)[2].startswith(f"title {title}&& ")

    def test_percent_stays_in_the_title_for_non_cmd_terminals(self):
        title = "100% done"
        pwsh = _build_command(self._TERM, "C:\\proj", ["codex"], title=title, wt_profile="PowerShell")
        assert pwsh[3].startswith("$Host.UI.RawUI.WindowTitle = '100% done'; ")
        wt = _build_command("C:\\wt.exe", "C:\\proj", ["codex"], title=title, wt_profile="PowerShell")
        assert wt[wt.index("--title") + 1] == title

    # -- R4 (round 2): a user template that runs cmd gets a cmd-safe {title} --

    _HOSTILE = "a<b>c^d%e&f"

    @pytest.mark.parametrize("template", [
        "cmd.exe /k title {title} && cd /d {cwd}",
        "C:\\Windows\\System32\\CMD.EXE /c title {title}",
        '"C:\\Windows\\System32\\cmd.exe" /k title {title}',
        "wt.exe --title {title} -d {cwd} -- cmd /k {pscmd}",
    ], ids=["bare-exe", "upper-case-full-path", "quoted-path", "inside-wt-command"])
    def test_a_template_that_runs_cmd_sanitizes_the_title_for_cmd(self, template):
        got = _build_command(template, "C:\\proj", ["codex"], title=self._HOSTILE, wt_profile="PowerShell")
        assert "abcdef" in got
        only = _build_terminal_only_command(template, "C:\\proj", title=self._HOSTILE)
        assert "abcdef" in only
        for ch in "<>^%":
            assert ch not in "".join(got) and ch not in "".join(only)

    @pytest.mark.parametrize("template", [
        "myterm --title {title} -d {cwd}",
        "C:\\tools\\cmd\\myterm.exe --title {title} -d {cwd}",
        "C:\\cmdline\\term.exe --title {title} -d {cwd}",
        "/opt/cmd/term --title {title} -d {cwd}",
        "myterm {cmd} --title {title} -d {cwd}",
    ], ids=["no-cmd", "cmd-folder", "cmdline-folder", "posix-cmd-folder", "cmd-placeholder"])
    def test_a_template_that_does_not_run_cmd_keeps_the_shared_sanitizer(self, template):
        # "cmd" only inside a folder name, or as the {cmd} placeholder, is not cmd.exe.
        got = _build_command(template, "C:\\proj", ["codex"], title=self._HOSTILE, wt_profile="PowerShell")
        assert "a<b>c^d%ef" in got
        only = _build_terminal_only_command(template, "C:\\proj", title=self._HOSTILE)
        assert "a<b>c^d%ef" in only

    def test_a_cmd_template_with_no_title_keeps_an_empty_title_element(self):
        assert "" in _build_command("cmd /k title {title}", "C:\\proj", ["codex"], title="", wt_profile="PowerShell")

    @pytest.mark.parametrize("ch", ["\n", "\r", "\t", chr(0x7F)], ids=["lf", "cr", "tab", "del"])
    def test_cmd_fallback_refuses_a_folder_or_argument_with_a_control_character(self, ch):
        assert _build_command(self._CMD, "C:\\a" + ch + "b", ["kiro-cli"], wt_profile="PowerShell") is None
        assert _build_command(self._CMD, "C:\\a", ["kiro-cli", "x" + ch + "y"], wt_profile="PowerShell") is None
        assert _build_terminal_only_command(self._CMD, "C:\\a" + ch + "b") is None
        assert _build_custom_command(self._CMD, "C:\\a" + ch + "b", "npm", "t", wt_profile="PowerShell") is None

    def _script(self, builder, *, cwd, title):
        if builder == "session":
            cmd = _build_command(self._TERM, cwd, ["codex", "resume", "abc"], title=title, wt_profile="PowerShell")
        elif builder == "terminal_only":
            cmd = _build_terminal_only_command(self._TERM, cwd, title=title)
        else:
            cmd = _build_custom_command(self._TERM, cwd, "npm start", title, wt_profile="PowerShell")
        return None if cmd is None else cmd[3]



class TestLaunchBatch:
    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_mixed_results(self, mock_which, mock_popen, tmp_path):
        mock_which.side_effect = lambda n: {"kiro-cli": "C:\\kiro-cli.exe", "wt": "C:\\wt.exe"}.get(n)
        good = str(tmp_path)
        bad = "C:\\nonexistent\\nope"
        sessions = [
            {"session_id": "sess_s1", "workspace": good, "provider": "kiro-cli-v3"},
            {"session_id": "sess_s2", "workspace": bad, "provider": "kiro-cli-v3"},
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
            {"session_id": "sess_s1", "workspace": good, "provider": "kiro-cli-v3"},
            {"session_id": "sess_s2"},  # missing workspace
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
            {"session_id": "sess_s1", "workspace": cwd, "provider": "kiro-cli-v3"},
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
            {"session_id": "sess_s1", "workspace": cwd, "provider": "kiro-cli-v3"},
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


class TestLaunchCustomWorkspaceArgMetacharacters:
    """Review finding R2 (261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 2, round 2): the workspace
    path is appended to a string that cmd.exe runs, so a metacharacter in it must not start a second command."""

    @pytest.mark.parametrize("name", ["d&calc", "a|b", "a^b"], ids=["amp", "pipe", "caret"])
    @pytest.mark.parametrize("use_terminal", [False, True], ids=["direct", "terminal"])
    @patch("subprocess.Popen")
    @patch("shutil.which", return_value="C:\\wt.exe")
    def test_a_workspace_with_a_cmd_metacharacter_is_quoted(self, _, mock_popen, use_terminal, name):
        cwd = "C:\\work\\" + name
        profile = LaunchProfile(terminal_command="C:\\wt.exe")
        with patch("pathlib.Path.exists", return_value=True), patch("power_atlas.launcher.sys.platform", "win32"):
            result = launch_custom("test", "code", cwd=cwd, launch_profile=profile, use_terminal=use_terminal, pass_workspace_arg=True)
        assert result.success is True
        popen_cmd = mock_popen.call_args[0][0]
        cmd_str = popen_cmd if isinstance(popen_cmd, str) else popen_cmd[-1]
        assert cmd_str == f'code "{cwd}"'

    @pytest.mark.parametrize("name", ["p%PATH%x", "a%b", 'a"b', "a\nb"], ids=["pct-var", "pct", "quote", "newline"])
    @pytest.mark.parametrize("use_terminal", [False, True], ids=["direct", "terminal"])
    @patch("subprocess.Popen")
    @patch("shutil.which", return_value="C:\\wt.exe")
    def test_a_workspace_that_cannot_be_quoted_is_refused(self, _, mock_popen, use_terminal, name):
        cwd = "C:\\work\\" + name
        profile = LaunchProfile(terminal_command="C:\\wt.exe")
        with patch("pathlib.Path.exists", return_value=True), patch("power_atlas.launcher.sys.platform", "win32"):
            result = launch_custom("test", "code", cwd=cwd, launch_profile=profile, use_terminal=use_terminal, pass_workspace_arg=True)
        assert result.success is False
        assert result.error == "Path contains characters unsafe for this terminal"
        mock_popen.assert_not_called()

    @pytest.mark.parametrize("cwd,expected", [
        ("C:\\work\\proj", "code C:\\work\\proj"),
        ("C:\\work\\proj (2)", 'code "C:\\work\\proj (2)"'),
        ("C:\\work\\proj(2)", "code C:\\work\\proj(2)"),
    ], ids=["plain", "spaced", "parens-only"])
    @patch("subprocess.Popen")
    def test_an_ordinary_workspace_command_string_is_unchanged(self, mock_popen, cwd, expected):
        with patch("pathlib.Path.exists", return_value=True), patch("power_atlas.launcher.sys.platform", "win32"):
            result = launch_custom("test", "code", cwd=cwd, use_terminal=False, pass_workspace_arg=True)
        assert result.success is True
        assert mock_popen.call_args[0][0] == expected

    @patch("subprocess.Popen")
    @patch("shutil.which", return_value="C:\\wt.exe")
    def test_a_metacharacter_workspace_is_harmless_when_the_argument_is_not_requested(self, _, mock_popen):
        with patch("pathlib.Path.exists", return_value=True), patch("power_atlas.launcher.sys.platform", "win32"):
            result = launch_custom("test", "code", cwd="C:\\work\\p%x", use_terminal=False, pass_workspace_arg=False)
        assert result.success is True
        assert mock_popen.call_args[0][0] == "code"


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
            result = launch_session(cwd, provider="kiro-cli-v3", default_args='"')
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


class TestSessionIdShape:
    """261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 2: the generic
    launch id rule. An id reaches `claude --resume <id>` / `kiro-cli ... --resume-id <id>`,
    so a value that starts with "-" is read as an option, "$" lets a trailing newline
    through, and Unicode `\\w` lets fullwidth digits through."""

    _UUID = "0199c8f2-7a3b-7c41-9e5d-3b8a1f6e2d40"

    # Every id shape the adapters produce on a real machine (measured through the data
    # layer: claude-code, kiro-ide and codex are lower-case UUIDs; kiro-cli-v3 is
    # `sess_<uuid>` or a bare UUID), plus the upper-case and short forms older tests use.
    _REAL_SHAPES = [
        _UUID,
        _UUID.upper(),
        "sess_" + _UUID,
        "sess_1abc",
        "abc123",
        "A1",
        "a" * 128,
    ]

    _BAD_IDS = [
        "--flag",
        "-h",
        "-",
        "--",
        "-" + _UUID,
        "_" + _UUID,                          # leading underscore: not a real id start
        _UUID + "\n",                         # "$" matches before a final newline
        "\n" + _UUID,
        " " + _UUID,
        _UUID + " ",
        _UUID + " --flag",
        _UUID + "\r",
        _UUID + "\t",
        chr(0xFF11) + chr(0xFF12) + chr(0xFF13),            # fullwidth digits, leading
        "abc" + chr(0xFF11),                                # fullwidth digit, not leading
        "abc" + chr(0x00E9),                                # accented letter
        "abc" + chr(0x212A),                                # Kelvin sign
        "abc" + chr(0),                                     # NUL
        "ab..cd",
        "a/b",
        "a\\b",
        "a;b",
        "a b",
        "a|b",
        "a&b",
        "a$(x)",
        "a" * 200,
        "a" * 129,
    ]

    @staticmethod
    def _which(name):
        return "C:\\" + name + ".exe"

    @pytest.mark.parametrize("provider", ["claude-code", "kiro-cli-v3", "kiro-ide", "codex"])
    @pytest.mark.parametrize("bad", _BAD_IDS, ids=repr)
    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_a_malformed_id_is_refused_before_any_process_is_started(
            self, mock_which, mock_popen, bad, provider, tmp_path):
        mock_which.side_effect = self._which
        result = launch_session(str(tmp_path), session_id=bad, provider=provider,
                                launch_profile=LaunchProfile(terminal_command="C:\\wt.exe"))
        assert result.success is False
        assert "Invalid session ID format" in result.error
        mock_popen.assert_not_called()

    @pytest.mark.parametrize("sid", _REAL_SHAPES)
    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_every_real_id_shape_still_launches_for_claude_and_kiro_cli(
            self, mock_which, mock_popen, sid, tmp_path):
        mock_which.side_effect = self._which
        for provider, flag in (("claude-code", "--resume"), ("kiro-cli-v3", "--resume-id")):
            mock_popen.reset_mock()
            result = launch_session(str(tmp_path), session_id=sid, provider=provider,
                                    launch_profile=LaunchProfile(terminal_command="C:\\wt.exe"))
            assert result.success is True, (provider, sid)
            cmd = " ".join(str(a) for a in mock_popen.call_args[0][0])
            # The pwsh script quotes each argument: `'--resume' '<id>'`.
            assert "'" + flag + "' '" + sid + "'" in cmd

    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_no_id_is_a_new_session_and_is_not_validated(self, mock_which, mock_popen, tmp_path):
        mock_which.side_effect = self._which
        for empty in ("", None):
            mock_popen.reset_mock()
            result = launch_session(str(tmp_path), session_id=empty, provider="claude-code",
                                    launch_profile=LaunchProfile(terminal_command="C:\\wt.exe"))
            assert result.success is True
            assert "--resume" not in " ".join(str(a) for a in mock_popen.call_args[0][0])

    def test_the_rule_accepts_what_the_dashboard_routes_accept(self):
        # data.SESSION_ID_RE is the shape the dashboard and Overview routes accept
        # (`sess_`-prefixed or bare UUIDs). Anything they accept must be launchable.
        from power_atlas import data
        from power_atlas.launcher import _SESSION_ID_RE
        for sid in (self._UUID, "sess_" + self._UUID):
            assert data.SESSION_ID_RE.fullmatch(sid)
            assert _SESSION_ID_RE.fullmatch(sid)


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


class TestIconPathTraversalGuard:
    """SC23: icon_path rejects launcher_ids that escape ICONS_DIR."""

    def test_valid_id_returns_path(self):
        from power_atlas.icons import icon_path
        p = icon_path("abc-123")
        assert p.name == "abc-123.png"

    def test_dotdot_raises(self):
        import pytest
        from power_atlas.icons import icon_path
        with pytest.raises(ValueError, match="Invalid launcher_id"):
            icon_path("../../etc/passwd")

    def test_absolute_path_raises(self):
        import pytest
        from power_atlas.icons import icon_path
        with pytest.raises(ValueError, match="Invalid launcher_id"):
            icon_path("/etc/passwd")

    def test_remove_icon_ignores_traversal(self):
        from power_atlas.icons import remove_icon
        # Should not raise even with a crafted id
        remove_icon("../../etc/shadow")


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


# --- Phase 5: launch_terminal and _build_terminal_only_command ---


class TestLaunchTerminal:
    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_success_valid_cwd(self, mock_which, mock_popen, tmp_path):
        """launch_terminal with valid cwd returns LaunchResult(success=True)."""
        mock_which.side_effect = lambda n: {"wt": "C:\\wt.exe"}.get(n)
        cwd = str(tmp_path)
        result = launch_terminal(cwd, launch_profile=LaunchProfile(terminal_command="C:\\wt.exe"))
        assert result.success is True
        assert result.workspace == cwd
        mock_popen.assert_called_once()

    def test_nonexistent_cwd_returns_error(self):
        """launch_terminal with non-existent cwd returns LaunchResult(success=False)."""
        result = launch_terminal("C:\\nonexistent\\bogus\\path", launch_profile=LaunchProfile(terminal_command="C:\\wt.exe"))
        assert result.success is False
        assert "not found" in result.error.lower() or "Folder not found" in result.error


class TestBuildTerminalOnlyCommand:
    def test_wt_terminal_args(self):
        """WT terminal builds correct args with -p, -d, and optional --title."""
        cmd = _build_terminal_only_command("C:\\wt.exe", "C:\\my\\project", title="Terminal - project", wt_profile="PowerShell")
        assert cmd[0] == "C:\\wt.exe"
        assert "--title" in cmd
        assert cmd[cmd.index("--title") + 1] == "Terminal - project"
        assert "-p" in cmd
        assert cmd[cmd.index("-p") + 1] == "PowerShell"
        assert "-d" in cmd
        assert cmd[cmd.index("-d") + 1] == "C:\\my\\project"

    def test_pwsh_terminal_set_location(self):
        """pwsh terminal builds -NoExit -Command with Set-Location."""
        cmd = _build_terminal_only_command("C:\\pwsh.exe", "C:\\my\\project", title="Terminal - project")
        assert cmd[0] == "C:\\pwsh.exe"
        assert cmd[1] == "-NoExit"
        assert cmd[2] == "-Command"
        script = cmd[3]
        assert "Set-Location" in script
        assert "C:\\my\\project" in script
        assert "WindowTitle" in script
        assert "Terminal - project" in script

    @patch("sys.platform", "linux")
    def test_kitty_terminal_directory_flag(self):
        """Linux kitty uses --directory and no exec_sep is appended (terminal-only)."""
        cmd = _build_terminal_only_command("/usr/bin/kitty", "/home/user/proj", title="Terminal - proj")
        assert cmd[0] == "/usr/bin/kitty"
        assert "--title" in cmd
        assert cmd[cmd.index("--title") + 1] == "Terminal - proj"
        assert "--directory" in cmd
        assert cmd[cmd.index("--directory") + 1] == "/home/user/proj"
        # No exec separator (--) or command appended for terminal-only
        assert "--" not in cmd



    @patch("sys.platform", "win32")
    def test_cmd_metachar_returns_none(self):
        """cmd fallback returns None when cwd contains metacharacters."""
        cmd = _build_terminal_only_command("C:\\cmd.exe", "C:\\my&project", title="Terminal")
        assert cmd is None

    @patch("sys.platform", "linux")
    def test_xterm_shell_wrapper(self):
        """xterm uses shell wrapper with SHELL fallback since it has no cwd flag."""
        cmd = _build_terminal_only_command("/usr/bin/xterm", "/home/user/proj", title="Terminal")
        assert cmd[0] == "/usr/bin/xterm"
        assert "-e" in cmd
        assert "sh" in cmd
        # Verify SHELL fallback is present
        shell_cmd = cmd[-1]
        assert "${SHELL:-/bin/sh}" in shell_cmd
        assert "/home/user/proj" in shell_cmd


class TestLaunchTerminalErrors:
    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_popen_oserror(self, mock_which, mock_popen, tmp_path):
        """launch_terminal returns error when Popen raises OSError."""
        mock_which.side_effect = lambda n: {"wt": "C:\\wt.exe"}.get(n)
        mock_popen.side_effect = OSError("Permission denied")
        cwd = str(tmp_path)
        result = launch_terminal(cwd, launch_profile=LaunchProfile(terminal_command="C:\\wt.exe"))
        assert result.success is False
        assert "Permission denied" in result.error

    @patch("shutil.which", return_value=None)
    def test_no_terminal_detected(self, mock_which, tmp_path):
        """launch_terminal returns error when no terminal can be found."""
        cwd = str(tmp_path)
        result = launch_terminal(cwd, launch_profile=LaunchProfile(terminal_command=""))
        assert result.success is False
        assert "No terminal found" in result.error

    def test_empty_cwd(self):
        """launch_terminal with empty cwd returns error."""
        result = launch_terminal("", launch_profile=LaunchProfile(terminal_command="C:\\wt.exe"))
        assert result.success is False
        assert "No directory" in result.error



class TestExtractIconSentinelGuard:
    """SC22: extract_icon returns False silently when win32 modules are unavailable."""

    def test_returns_false_when_win32gui_sentinel_is_none(self, monkeypatch, tmp_path):
        import power_atlas.icons as icons_mod
        # Create a real .exe file so _resolve_binary can find it and pass it to
        # _extract_windows_icon — otherwise the guard is never reached.
        fake_exe = tmp_path / "fake.exe"
        fake_exe.write_bytes(b"MZ")  # minimal PE magic so suffix check passes
        monkeypatch.setattr(icons_mod, "_win32gui", None)
        result = icons_mod.extract_icon("test-id", str(fake_exe), False)
        assert result is False

    def test_returns_false_when_pil_sentinel_is_none(self, monkeypatch, tmp_path):
        import power_atlas.icons as icons_mod
        from unittest.mock import MagicMock
        fake_exe = tmp_path / "fake.exe"
        fake_exe.write_bytes(b"MZ")
        # Keep _win32gui as a mock (non-None) so the first guard passes,
        # then patch _PilImage to None so the second guard fires.
        monkeypatch.setattr(icons_mod, "_win32gui", MagicMock())
        monkeypatch.setattr(icons_mod, "_PilImage", None)
        result = icons_mod.extract_icon("test-id", str(fake_exe), False)
        assert result is False
