"""Launch kiro-cli sessions in detected or configured terminals."""

import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from power_atlas.config import LaunchProfile


@dataclass
class LaunchResult:
    success: bool
    session_id: str | None
    workspace: str
    error: str = ""
    warning: str = ""
    used_fallback: bool = False


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


def detect_terminal(terminal_command: str = "") -> str | None:
    """Detect terminal. Priority: config > platform-specific probe order."""
    if terminal_command:
        return terminal_command
    if sys.platform == "win32":
        candidates = ("wt", "pwsh", "cmd")
    else:
        candidates = _LINUX_PROBE_ORDER
    for name in candidates:
        path = shutil.which(name)
        if path:
            return path
    return None




_PROVIDER_DISPLAY = {
    "kiro-cli": "Kiro CLI",
    "claude-code": "Claude Code",
    "kiro-ide": "Kiro IDE",
}

_PROVIDER_BINARY = {
    "kiro-cli": "kiro-cli",
    "claude-code": "claude",
    "kiro-ide": "kiro",
}

_PROVIDER_TERMINAL = {
    "kiro-cli": True,
    "claude-code": True,
    "kiro-ide": False,
}

_MCP_SAFE_WT_PROVIDERS = {"kiro-cli", "claude-code"}

_MCP_SAFE_WT_HELPER = r"""
param(
    [Parameter(Mandatory=$true)][string]$Wt,
    [Parameter(Mandatory=$true)][string]$Title,
    [Parameter(Mandatory=$true)][string]$Cwd,
    [Parameter(Mandatory=$true)][string]$Command,
    [Parameter(Mandatory=$true)][string]$WtProfile,
    [Parameter(Mandatory=$true)][string]$ShellProcessName,
    [Parameter(Mandatory=$true)][int]$AttachTimeoutMs
)

# Exit codes:
#   0 = success (command typed into the new tab)
#   1 = failed BEFORE the WT tab was opened (no orphan tab exists)
#   2 = failed AFTER the WT tab was opened (orphan tab exists, do NOT open another)

# Do NOT use $ErrorActionPreference = 'Stop' — it makes Write-Error
# and Write-Host terminating, which prevents explicit exit codes from running.
# Instead, use try/catch around individual operations.

$tabOpened = $false

try {

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

namespace NativeConsole
{
    public static class Kernel32
    {
        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool AttachConsole(uint dwProcessId);

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool FreeConsole();

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern IntPtr GetStdHandle(int nStdHandle);

        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        public static extern IntPtr CreateFileW(
            string lpFileName,
            uint dwDesiredAccess,
            uint dwShareMode,
            IntPtr lpSecurityAttributes,
            uint dwCreationDisposition,
            uint dwFlagsAndAttributes,
            IntPtr hTemplateFile
        );

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool CloseHandle(IntPtr hObject);

        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        public static extern bool WriteConsoleInput(
            IntPtr hConsoleInput,
            INPUT_RECORD[] lpBuffer,
            uint nLength,
            out uint lpNumberOfEventsWritten
        );
    }

    [StructLayout(LayoutKind.Explicit, CharSet = CharSet.Unicode)]
    public struct INPUT_RECORD
    {
        [FieldOffset(0)]
        public ushort EventType;

        [FieldOffset(4)]
        public KEY_EVENT_RECORD KeyEvent;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct KEY_EVENT_RECORD
    {
        [MarshalAs(UnmanagedType.Bool)]
        public bool bKeyDown;
        public ushort wRepeatCount;
        public ushort wVirtualKeyCode;
        public ushort wVirtualScanCode;
        public char UnicodeChar;
        public uint dwControlKeyState;
    }
}
"@

function New-KeyRecord {
    param(
        [Parameter(Mandatory=$true)][char]$Char,
        [Parameter(Mandatory=$true)][bool]$KeyDown
    )

    $record = New-Object NativeConsole.INPUT_RECORD
    $record.EventType = 0x0001
    $record.KeyEvent = New-Object NativeConsole.KEY_EVENT_RECORD
    $record.KeyEvent.bKeyDown = $KeyDown
    $record.KeyEvent.wRepeatCount = 1
    $record.KeyEvent.wVirtualScanCode = 0
    $record.KeyEvent.UnicodeChar = $Char
    $record.KeyEvent.dwControlKeyState = 0
    if ([int][char]$Char -eq 13) {
        $record.KeyEvent.wVirtualKeyCode = 0x0D
    } else {
        $record.KeyEvent.wVirtualKeyCode = 0
    }
    return $record
}

# --- PRE-TAB PHASE (exit 1 on failure) ---

$beforePwsh = @{}
Get-CimInstance Win32_Process | Where-Object { $_.Name -eq $ShellProcessName } | ForEach-Object {
    $beforePwsh[[int]$_.ProcessId] = $true
}

$wtArgs = @('new-tab')
if ($Title) {
    $wtArgs += @('--title', $Title)
}
$wtArgs += @('-p', $WtProfile, '-d', $Cwd)
& $Wt @wtArgs | Out-Null
if ($LASTEXITCODE -ne 0) {
    [Console]::Error.WriteLine("Windows Terminal exited with code $LASTEXITCODE.")
    exit 1
}

# --- POST-TAB PHASE (exit 2 on failure — orphan tab exists) ---
$tabOpened = $true

$target = $null
$deadline = (Get-Date).AddMilliseconds($AttachTimeoutMs)
do {
    Start-Sleep -Milliseconds 100
    $candidates = @()
    $pwshProcesses = Get-CimInstance Win32_Process | Where-Object { $_.Name -eq $ShellProcessName }
    foreach ($process in $pwshProcesses) {
        $processId = [int]$process.ProcessId
        if ($beforePwsh.ContainsKey($processId)) {
            continue
        }
        if (-not $process.ParentProcessId) {
            continue
        }
        $parent = Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f [int]$process.ParentProcessId) -ErrorAction SilentlyContinue
        if ($parent -and $parent.Name -eq 'WindowsTerminal.exe') {
            $candidates += $process
        }
    }
    if ($candidates.Count -gt 0) {
        $target = $candidates | Sort-Object CreationDate -Descending | Select-Object -First 1
        break
    }
} while ((Get-Date) -lt $deadline)

if (-not $target) {
    [Console]::Error.WriteLine("Could not find a new Windows Terminal $ShellProcessName process within ${AttachTimeoutMs}ms.")
    exit 2
}

$attached = $false
try {
    [NativeConsole.Kernel32]::FreeConsole() | Out-Null
    if (-not [NativeConsole.Kernel32]::AttachConsole([uint32]$target.ProcessId)) {
        $errorCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        [Console]::Error.WriteLine("AttachConsole failed with Win32 error $errorCode.")
        exit 2
    }
    $attached = $true

    # Brief pause to let the console input buffer initialize after attach
    Start-Sleep -Milliseconds 200

    # Use CreateFileW("CONIN$") instead of GetStdHandle(-10).
    # GetStdHandle returns our process's stdin pipe which is invalid after
    # AttachConsole to a ConPTY-hosted process (Windows Terminal uses ConPTY).
    # CreateFileW("CONIN$") opens a direct handle to the attached console's
    # input buffer, which works regardless of ConPTY.
    $GENERIC_READ_WRITE = [uint32]0xC0000000  # GENERIC_READ | GENERIC_WRITE
    $FILE_SHARE_READ = [uint32]0x1
    $OPEN_EXISTING = [uint32]3
    $inputHandle = [NativeConsole.Kernel32]::CreateFileW(
        "CONIN$",
        $GENERIC_READ_WRITE,
        $FILE_SHARE_READ,
        [IntPtr]::Zero,
        $OPEN_EXISTING,
        [uint32]0,
        [IntPtr]::Zero
    )
    if ($inputHandle -eq [IntPtr]::Zero -or $inputHandle.ToInt64() -eq -1) {
        $errorCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        [Console]::Error.WriteLine("CreateFileW(CONIN$) failed with Win32 error $errorCode.")
        exit 2
    }

    $text = $Command + [string][char]13
    $records = New-Object 'NativeConsole.INPUT_RECORD[]' ($text.Length * 2)
    $i = 0
    foreach ($char in $text.ToCharArray()) {
        $records[$i] = New-KeyRecord -Char $char -KeyDown $true
        $i += 1
        $records[$i] = New-KeyRecord -Char $char -KeyDown $false
        $i += 1
    }

    [uint32]$written = 0
    if (-not [NativeConsole.Kernel32]::WriteConsoleInput($inputHandle, $records, [uint32]$records.Length, [ref]$written)) {
        $errorCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        [Console]::Error.WriteLine("WriteConsoleInputW failed with Win32 error $errorCode.")
        exit 2
    }
    if ($written -ne $records.Length) {
        [Console]::Error.WriteLine("WriteConsoleInputW wrote $written of $($records.Length) input records.")
        exit 2
    }
} finally {
    if ($inputHandle -and $inputHandle -ne [IntPtr]::Zero -and $inputHandle.ToInt64() -ne -1) {
        [NativeConsole.Kernel32]::CloseHandle($inputHandle) | Out-Null
    }
    if ($attached) {
        [NativeConsole.Kernel32]::FreeConsole() | Out-Null
    }
}

exit 0

} catch {
    # Unhandled exception — use $tabOpened to determine the right exit code
    [Console]::Error.WriteLine($_.Exception.Message)
    if ($tabOpened) { exit 2 } else { exit 1 }
}
"""

def _build_provider_args(provider: str, binary: str, session_id: str | None) -> list[str]:
    """Build CLI args for a given provider."""
    if provider == "claude-code":
        args = [binary]
        if session_id:
            args += ["--resume", session_id]
    elif provider == "kiro-ide":
        args = [binary]  # No session resume support
    else:  # kiro-cli
        args = [binary, "chat"]
        if session_id:
            args += ["--resume-id", session_id]
    return args


def _quote_powershell_arg(arg: str) -> str:
    """Render one argv element as a PowerShell single-quoted literal."""
    return "'" + arg.replace("'", "''") + "'"


def _build_powershell_invocation(args: list[str]) -> str:
    """Render argv as a typed PowerShell command line."""
    return "& " + " ".join(_quote_powershell_arg(arg) for arg in args)


def _is_windows_terminal(terminal: str) -> bool:
    """Return true for Windows Terminal executables, excluding user templates."""
    if "{cwd}" in terminal or "{cmd}" in terminal:
        return False
    return Path(terminal).stem.lower() == "wt"


def _write_mcp_safe_wt_helper() -> Path:
    """Write the generated helper script to a temporary .ps1 file."""
    with tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False, encoding="utf-8") as handle:
        handle.write(_MCP_SAFE_WT_HELPER)
        return Path(handle.name)


def _launch_mcp_safe_wt(
    terminal: str, cwd: str, typed_command: str, title: str,
    wt_profile: str, shell_process_name: str, helper_runner: str,
    attach_timeout_ms: int, helper_timeout_ms: int,
) -> tuple[bool, bool, str]:
    """Launch a WT profile tab, attach to its console, and type the command.

    Returns (success, tab_opened, error_message):
      - (True, True, '')    = command typed successfully
      - (False, False, msg) = failed before opening tab (safe to fallback)
      - (False, True, msg)  = tab opened but typing failed (orphan exists)
    """
    runner = shutil.which(helper_runner)
    if not runner:
        return (False, False, f"{helper_runner} not found on PATH")

    script_path = _write_mcp_safe_wt_helper()
    try:
        cmd = [
            runner,
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
            "-Wt",
            terminal,
            "-Title",
            _sanitize_title(title),
            "-Cwd",
            cwd,
            "-Command",
            typed_command,
            "-WtProfile",
            wt_profile,
            "-ShellProcessName",
            shell_process_name,
            "-AttachTimeoutMs",
            str(attach_timeout_ms),
        ]
        kwargs: dict = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        completed = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=helper_timeout_ms / 1000,
            **kwargs,
        )
        if completed.returncode == 0:
            return (True, True, "")
        # Exit 2 = tab opened but typing failed (orphan exists)
        # Exit 1 = failed before tab opened (no orphan)
        tab_opened = completed.returncode == 2
        error_msg = completed.stderr.strip() or "helper exited with non-zero status"
        return (False, tab_opened, error_msg)
    except (OSError, subprocess.SubprocessError) as e:
        # If the subprocess itself failed to launch, no tab was opened
        return (False, False, str(e))
    finally:
        try:
            script_path.unlink(missing_ok=True)
        except OSError:
            pass


def _should_use_mcp_safe_wt(provider: str, terminal: str, mcp_safe_enabled: bool) -> bool:
    """Use the MCP-safe path only for supported Windows CLI providers in WT."""
    return (
        sys.platform == "win32"
        and mcp_safe_enabled
        and provider in _MCP_SAFE_WT_PROVIDERS
        and _is_windows_terminal(terminal)
    )


def launch_session(
    cwd: str,
    session_id: str | None = None,
    provider: str = "kiro-cli",
    default_args: str = "",
    launch_profile: LaunchProfile | None = None,
) -> LaunchResult:
    """Launch a provider session in a terminal (or directly for non-terminal providers). Returns result, never raises."""
    profile = launch_profile or LaunchProfile()
    binary = _PROVIDER_BINARY.get(provider, provider)
    display = _PROVIDER_DISPLAY.get(provider, provider)

    if not shutil.which(binary):
        return LaunchResult(
            False, session_id, cwd,
            error=f"'{binary}' not found on PATH. Install {display} or check your PATH.",
        )

    if cwd and cwd != "." and not Path(cwd).exists():
        return LaunchResult(False, session_id, cwd, error=f"Folder not found: {cwd}")

    if session_id and (len(session_id) > 128 or not _SESSION_ID_RE.match(session_id)):
        return LaunchResult(False, session_id, cwd, error="Invalid session ID format")

    # Parse default_args once (before both terminal and non-terminal branches)
    try:
        extra_args = shlex.split(default_args, posix=(sys.platform != "win32")) if default_args else []
    except ValueError as e:
        return LaunchResult(False, session_id, cwd, error=f"Invalid launch arguments: {e}")

    # Non-terminal providers: launch directly without a terminal
    if not _PROVIDER_TERMINAL.get(provider, True):
        cli_args = _build_provider_args(provider, binary, session_id)
        if default_args:
            cli_args += extra_args
        # Append workspace path if a real workspace was specified
        if cwd and cwd != "." and Path(cwd).exists():
            cli_args.append(cwd)
        try:
            kwargs: dict = {}
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
                # .cmd/.bat files need shell=True on Windows to execute properly
                resolved = shutil.which(binary) or binary
                if resolved.lower().endswith((".cmd", ".bat")):
                    kwargs["shell"] = True
            else:
                kwargs["start_new_session"] = True
            subprocess.Popen(cli_args, **kwargs)
            return LaunchResult(True, session_id, cwd)
        except OSError as e:
            return LaunchResult(False, session_id, cwd, error=str(e))

    # Terminal-based providers
    terminal = detect_terminal(profile.terminal_command)
    if not terminal:
        if sys.platform == "win32":
            msg = "No terminal found. Configure one in Settings."
        else:
            msg = "No terminal found. Install kitty, alacritty, gnome-terminal, konsole, or xterm \u2014 or configure a custom terminal in Settings."
        return LaunchResult(False, session_id, cwd, error=msg)

    cli_args = _build_provider_args(provider, binary, session_id)
    if default_args:
        cli_args += extra_args

    title = f"{display} - {Path(cwd).name}"
    if _should_use_mcp_safe_wt(provider, terminal, profile.mcp_safe_enabled):
        typed_command = _build_powershell_invocation(cli_args)
        helper_ok, tab_opened, helper_error = _launch_mcp_safe_wt(
            terminal, cwd, typed_command, title,
            wt_profile=profile.wt_profile,
            shell_process_name=profile.shell_process_name,
            helper_runner=profile.helper_runner,
            attach_timeout_ms=profile.attach_timeout_ms,
            helper_timeout_ms=profile.helper_timeout_ms,
        )
        if helper_ok:
            return LaunchResult(True, session_id, cwd)
        if tab_opened:
            # Tab exists but command wasn't typed — do NOT open a second tab.
            # The orphan tab has a shell prompt; user can type the command manually.
            return LaunchResult(
                False, session_id, cwd,
                error=f"MCP-safe: tab opened but command typing failed ({helper_error}). "
                      f"A PowerShell tab is open in the target folder — type the command manually or close it and retry.",
            )
        # Tab was never opened — safe to fall back to direct WT launch
        cmd = _build_command(terminal, cwd, cli_args, title=title, wt_profile=profile.wt_profile)
        if cmd is None:
            return LaunchResult(
                False, session_id, cwd,
                error=f"MCP-safe helper failed: {helper_error}. Direct fallback also failed: path contains shell metacharacters unsafe for cmd.exe",
            )
        try:
            fb_kwargs: dict = {"creationflags": subprocess.CREATE_NEW_CONSOLE} if sys.platform == "win32" else {"start_new_session": True}
            subprocess.Popen(cmd, **fb_kwargs)
            return LaunchResult(
                True, session_id, cwd,
                warning=f"MCP-safe helper failed ({helper_error}); launched via direct Windows Terminal tab.",
                used_fallback=True,
            )
        except OSError as e:
            direct_error = str(e)
            return LaunchResult(
                False, session_id, cwd,
                error=f"MCP-safe helper failed: {helper_error}. Direct fallback also failed: {direct_error}.",
            )

    cmd = _build_command(terminal, cwd, cli_args, title=title, wt_profile=profile.wt_profile)
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
    launch_profile: LaunchProfile | None = None,
    provider_settings: dict[str, dict] | None = None,
) -> list[LaunchResult]:
    """Launch multiple sessions. Never aborts on single failure.

    If provider_settings is provided, per-provider default_args are looked up
    for each session (overriding the flat default_args parameter).
    """
    results = []
    for s in sessions:
        workspace = s.get("workspace") or "<unknown>"
        if workspace == "<unknown>":
            results.append(LaunchResult(False, s.get("session_id"), workspace, error="Missing 'workspace' key"))
            continue
        provider = s.get("provider") or "kiro-cli"
        if provider_settings:
            args = provider_settings.get(provider, {}).get("default_args", "")
        else:
            args = default_args
        results.append(launch_session(
            cwd=workspace,
            session_id=s.get("session_id"),
            provider=provider,
            default_args=args,
            launch_profile=launch_profile,
        ))
    return results


_CMD_METACHAR_RE = re.compile(r'[&|<>^%"]')
_TITLE_UNSAFE_RE = re.compile(r'[\"\'&|;$`]')


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


def _build_command(terminal: str, cwd: str, kiro_args: list[str], title: str = "", *, wt_profile: str) -> list[str] | None:
    """Build terminal-specific command list. Returns None if cwd is unsafe for cmd."""
    t = Path(terminal).stem.lower()

    if "{cwd}" in terminal or "{cmd}" in terminal:
        return _build_template_command(terminal, cwd, kiro_args)

    if t == "wt":
        cmd = [terminal]
        if title:
            cmd += ["--title", _sanitize_title(title)]
        cmd += ["-p", wt_profile, "-d", cwd, "--", *kiro_args]
        return cmd
    if t == "pwsh":
        escaped_cwd = cwd.replace("'", "''")
        script = ""
        if title:
            safe = _sanitize_title(title).replace("'", "''")
            script = f"$Host.UI.RawUI.WindowTitle = '{safe}'; "
        invocation = " ".join("'" + a.replace("'", "''") + "'" for a in kiro_args)
        script += f"Set-Location -LiteralPath '{escaped_cwd}'; & {invocation}"
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
    # _CMD_METACHAR_RE is cmd.exe-specific; reject args containing its metacharacters
    if _CMD_METACHAR_RE.search(kiro_cmd):
        return None
    prefix = f"title {_sanitize_title(title)}&& " if title else ""
    return [terminal, "/k", f'{prefix}cd /d "{cwd}" && {kiro_cmd}']



def launch_custom_batch(
    name: str,
    command: str,
    custom_args: str = "",
    workspaces: list[str] | None = None,
    env: dict[str, str] | None = None,
    launch_profile: LaunchProfile | None = None,
    use_terminal: bool = True,
    pass_workspace_arg: bool = False,
) -> list[LaunchResult]:
    """Launch a custom command once per workspace. Returns list of results."""
    results = []
    for ws in (workspaces or []):
        cwd = ws or str(Path.home())
        results.append(launch_custom(
            name=name, command=command, custom_args=custom_args,
            cwd=cwd, env=env, launch_profile=launch_profile,
            use_terminal=use_terminal, pass_workspace_arg=pass_workspace_arg,
        ))
    return results


def launch_custom(name: str, command: str, custom_args: str = "", cwd: str = "", env: dict[str, str] | None = None, launch_profile: LaunchProfile | None = None, use_terminal: bool = True, pass_workspace_arg: bool = False) -> LaunchResult:
    """Launch a custom command, optionally in a terminal. Never uses MCP-safe helper."""
    profile = launch_profile or LaunchProfile()
    work_dir = cwd or "."
    if not Path(work_dir).exists():
        return LaunchResult(False, None, work_dir, error=f"Folder not found: {work_dir}")
    full_cmd_str = f"{command} {custom_args}".strip() if custom_args else command

    # Append workspace path as argument when requested and a real workspace is set
    if pass_workspace_arg and work_dir != ".":
        if sys.platform == "win32":
            # Quote paths with spaces; no inner-quote escaping needed since NTFS
            # forbids " in filenames so work_dir can never contain one.
            quoted_ws = f'"{work_dir}"' if " " in work_dir else work_dir
        else:
            quoted_ws = shlex.quote(work_dir)
        full_cmd_str = f"{full_cmd_str} {quoted_ws}"

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

    terminal = detect_terminal(profile.terminal_command)
    if not terminal:
        if sys.platform == "win32":
            msg = "No terminal found. Configure one in Settings."
        else:
            msg = "No terminal found. Install kitty, alacritty, gnome-terminal, konsole, or xterm \u2014 or configure a custom terminal in Settings."
        return LaunchResult(False, None, work_dir, error=msg)
    title = _sanitize_title(f"{Path(command).stem} - {Path(work_dir).name}")
    cmd = _build_custom_command(terminal, work_dir, full_cmd_str, title, wt_profile=profile.wt_profile)
    if cmd is None:
        return LaunchResult(False, None, work_dir, error="Path contains unsafe characters for this terminal")
    try:
        subprocess.Popen(cmd, **kwargs)
        return LaunchResult(True, None, work_dir)
    except OSError as e:
        return LaunchResult(False, None, work_dir, error=str(e))


def _build_custom_command(terminal: str, cwd: str, cmd_str: str, title: str, *, wt_profile: str) -> list[str] | None:
    """Build terminal-specific command for custom launcher. Returns None if unsafe."""
    t = Path(terminal).stem.lower()
    if t == "wt":
        return [terminal, "--title", title, "-p", wt_profile, "-d", cwd, "--", "cmd", "/c", cmd_str]
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
