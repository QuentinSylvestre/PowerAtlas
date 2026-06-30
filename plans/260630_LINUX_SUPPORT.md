# Linux Support

> **Date**: 2026-06-30
> **Status**: In Progress  <!-- Status lifecycle: Exploring → Draft → In Progress → Complete -->
> **Scope**: Make PowerAtlas fully functional on Linux — terminal detection, command building, path normalization, UI adaptation
> **Estimated effort**: 1-2 days

---

## Intent

### Problem statement & desired outcomes

PowerAtlas cannot launch kiro-cli sessions on Linux because terminal detection and command building are Windows-only. Path normalization corrupts Linux paths (unconditional forward→backslash conversion), and the UI hardcodes Windows terminal names. The desired outcome is a fully functional PowerAtlas on Linux desktops — auto-detecting installed terminals, launching sessions correctly, and showing platform-appropriate UI.

### Success criteria

1. `detect_terminal()` finds and returns a Linux terminal emulator (from the supported set) when run on Linux
2. `_build_command()` produces correct command lists for each supported Linux terminal
3. `_normalize_path()` preserves forward slashes on Linux
4. Settings page and topbar terminal selector show only terminals available on the current platform
5. Tray icon renders correctly on Linux (PNG format for AppIndicator)
6. Custom template `{cwd}/{cmd}` mechanism handles paths with spaces on both platforms
7. Existing Windows behavior is unchanged
8. Tests cover Linux terminal detection and command building

### Scope boundaries & non-goals

**In scope**: Terminal detection, command building, path normalization, UI adaptation, tray icon format, template space-handling fix.

**Non-goals**: Wayland-specific features, WSL interop, new config fields, cross-platform config file sharing, new UI pages, changes to session discovery logic beyond path normalization.

---

## 1) Current State

### Terminal detection and launching (`launcher.py`)

- `detect_terminal()` (line 23-31): probes only `("wt", "pwsh", "cmd")` via `shutil.which()`. Returns `None` on Linux.
- `_build_command()` (line 95-118): dispatches on `Path(terminal).stem.lower()` — handles `wt`, `pwsh`, and falls back to `cmd /k`. No Linux terminal stems.
- `_build_custom_command()` (line 156-168): same Windows-only dispatch.
- `{cwd}/{cmd}` template mechanism (line 99-101): uses `full.split()` which breaks paths with spaces.
- `launch_session()` (line 63): subprocess kwargs already cross-platform (`creationflags` on win32, `start_new_session` otherwise).

### Path normalization (`data.py:455-459`)

```python
def _normalize_path(p: str) -> str:
    normalized = p.replace("/", "\\").rstrip("\\")
    if sys.platform == "win32":
        normalized = normalized.casefold()
    return normalized
```

The forward→backslash conversion on line 457 is unconditional — corrupts `/home/user/project` into `\home\user\project` on Linux. Used at 26 call sites across `data.py` and `web.py` for cache keying and deduplication. Display and launch paths use the original raw path (no corruption there).

### Settings UI

- `settings.html` (line 12-28): hardcoded dropdown with "Auto-detect (wt › pwsh › cmd)", "Windows Terminal", "PowerShell", "Command Prompt", "Custom".
- `index.html` (line 6-11): topbar quick selector with same hardcoded Windows options.
- "Start with Windows" label at `settings.html:37`.

### Tray icon (`tray.py:22-29`)

Uses `poweratlas-tray.ico` (14KB). On Linux, pystray with AppIndicator backend works best with PNG. The fallback (Pillow-generated blue square) already works.

### Platform branching

18/19 `sys.platform` checks across the codebase are correct. Only `data.py:457` is broken.

## 2) Goal

Make PowerAtlas detect, build commands for, and launch kiro-cli in common Linux terminal emulators, fix the path normalization bug, and render platform-appropriate UI — all without changing existing Windows behavior.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Linux terminal probe list | `kitty`, `alacritty`, `gnome-terminal`, `konsole`, `xterm` (in order) | Broader set (+wezterm, tilix, foot, xfce4-terminal) | Covers 90%+ of developer Linux desktops; exotic terminals use custom template |
| Command building approach | Dispatch table mapping stem → `(title_flag, cwd_flag, exec_separator)` | Per-terminal builder functions | Regular flag patterns; table is extensible; shell wrapper handles edge cases (xterm cwd) |
| Custom template space fix | Placeholder-aware builder (insert cwd/cmd as discrete list elements) | `shlex.split()`, leave as-is | Reliable on both platforms; no user-facing quoting rules |
| Path normalization | Platform-native only (gate slash conversion behind `win32`) | Universal forward-slash normalization | Single-machine app; no cross-platform config sharing; no migration needed |
| Settings UI terminal list | Backend-computed dynamic list via `shutil.which()` detection | Pass `sys.platform` string to template | Shows only available terminals; auto-detect label adapts |
| Tray icon format | Ship `poweratlas-tray.png`, select by platform in `_create_icon()` | Convert .ico at runtime via Pillow | Simpler, avoids runtime conversion overhead |

## 4) External Dependencies & Costs

### Required external changes

None. All changes are code-only within the existing package. No new dependencies (PyGObject already declared for Linux). No CI/CD, IAM, or infra changes.

### Cost impact

None.

## 5) Implementation Phases

### Phase 1: Core launcher — terminal detection, command building, path fix [QA]

**Goal**: Make `detect_terminal()` and `_build_command()` work on Linux; fix `_normalize_path()`; fix `{cwd}/{cmd}` space handling.

**File scope**: `src/power_atlas/launcher.py`, `src/power_atlas/data.py`, `tests/test_launcher.py`, `tests/test_data.py`

**Changes**:

1. **`launcher.py` — Linux terminal dispatch table and detection**:

```python
# Terminal dispatch table: stem -> (title_flag, cwd_flag, exec_separator)
# exec_separator is the flag before the command, or None for "--"
_LINUX_TERMINALS: dict[str, tuple[str | None, str | None, str | None]] = {
    "kitty":          ("--title",  "--directory",          "--"),
    "alacritty":      ("--title",  "--working-directory",  "-e"),
    "gnome-terminal": ("--title=", "--working-directory=", "--"),
    "konsole":        (None,       "--workdir",            "-e"),
    "xterm":          ("-title",   None,                   "-e"),
}

_LINUX_PROBE_ORDER = ("kitty", "alacritty", "gnome-terminal", "konsole", "xterm")
```

2. **`launcher.py:detect_terminal()` — add Linux probing**:

```python
def detect_terminal(config_override: str = "") -> str | None:
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
```

3. **`launcher.py:_build_command()` — add Linux dispatch**:

```python
def _build_command(terminal: str, cwd: str, kiro_args: list[str], title: str = "") -> list[str] | None:
    t = Path(terminal).stem.lower()

    # Custom template with placeholders (both platforms)
    if "{cwd}" in terminal or "{cmd}" in terminal:
        return _build_template_command(terminal, cwd, kiro_args)

    # Windows terminals
    if t == "wt":
        # ... existing wt code ...
    if t == "pwsh":
        # ... existing pwsh code ...
    
    # Linux terminals via dispatch table
    if t in _LINUX_TERMINALS:
        return _build_linux_command(terminal, cwd, kiro_args, title, t)

    # cmd fallback (Windows only)
    if _CMD_METACHAR_RE.search(cwd):
        return None
    kiro_cmd = " ".join(kiro_args)
    prefix = f"title {_sanitize_title(title)}&& " if title else ""
    return [terminal, "/k", f'{prefix}cd /d "{cwd}" && {kiro_cmd}']
```

4. **`launcher.py` — new `_build_linux_command()` helper**:

```python
import shlex

def _build_linux_command(terminal: str, cwd: str, kiro_args: list[str], title: str, stem: str) -> list[str]:
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

    # For terminals without cwd_flag (xterm), wrap in shell with proper escaping
    if not cwd_flag:
        shell_cmd = f'cd {shlex.quote(cwd)} && exec {" ".join(shlex.quote(a) for a in kiro_args)}'
        cmd += ["sh", "-c", shell_cmd]
    else:
        cmd += kiro_args

    return cmd
```

5. **`launcher.py:_build_custom_command()` — add Linux dispatch**:

```python
def _build_custom_command(terminal: str, cwd: str, cmd_str: str, title: str) -> list[str] | None:
    t = Path(terminal).stem.lower()
    # ... existing wt/pwsh/cmd cases ...

    # Linux terminals
    if t in _LINUX_TERMINALS:
        title_flag, cwd_flag, exec_sep = _LINUX_TERMINALS[t]
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
        if not cwd_flag:
            cmd += ["sh", "-c", f'cd {shlex.quote(cwd)} && exec {cmd_str}']
        else:
            cmd += ["sh", "-c", cmd_str]
        return cmd

    # cmd fallback (Windows-only; on Linux, return None to surface clear error)
    if sys.platform != "win32":
        return None
    if _CMD_METACHAR_RE.search(cwd):
        return None
    safe_title = _sanitize_title(title)
    return [terminal, "/k", f'title {safe_title}&& cd /d "{cwd}" && {cmd_str}']
```

6. **`launcher.py` — fix `{cwd}/{cmd}` template space handling**:

```python
def _build_template_command(template: str, cwd: str, kiro_args: list[str]) -> list[str]:
    """Build command from user template with {cwd}/{cmd} placeholders.
    
    Handles paths with spaces by splitting the template around placeholders
    and inserting values as discrete elements.
    """
    import re
    parts = re.split(r"(\{cwd\}|\{cmd\})", template)
    result: list[str] = []
    for part in parts:
        if part == "{cwd}":
            result.append(cwd)
        elif part == "{cmd}":
            result.extend(kiro_args)  # keep as separate elements for subprocess
        else:
            result.extend(p for p in part.split() if p)
    return result
```

7. **`data.py:_normalize_path()` — gate slash conversion**:

```python
def _normalize_path(p: str) -> str:
    if sys.platform == "win32":
        normalized = p.replace("/", "\\").rstrip("\\")
        return normalized.casefold()
    else:
        return p.rstrip("/")
```

8. **`tests/test_launcher.py` — add Linux tests**:

```python
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
        assert "sh" in cmd
        assert "-c" in cmd

    def test_konsole(self):
        cmd = _build_command("/usr/bin/konsole", "/home/user/proj", ["kiro-cli", "chat"], title="test")
        assert cmd == ["/usr/bin/konsole", "--workdir", "/home/user/proj", "-e", "kiro-cli", "chat"]


class TestTemplateSpaceHandling:
    def test_cwd_with_spaces(self):
        cmd = _build_template_command("myterm --dir {cwd} -e {cmd}", "/home/user/my project", ["kiro-cli", "chat"])
        assert "/home/user/my project" in cmd
        assert cmd == ["myterm", "--dir", "/home/user/my project", "-e", "kiro-cli", "chat"]

    def test_cmd_args_kept_separate(self):
        cmd = _build_template_command("term -e {cmd}", "/proj", ["kiro-cli", "chat", "--resume-id", "abc"])
        assert cmd == ["term", "-e", "kiro-cli", "chat", "--resume-id", "abc"]


class TestBuildCustomCommandLinux:
    def test_kitty(self):
        cmd = _build_custom_command("/usr/bin/kitty", "/home/user/proj", "npm start", "npm - proj")
        assert cmd[0] == "/usr/bin/kitty"
        assert "--directory" in cmd
        assert "sh" in cmd and "-c" in cmd

    def test_xterm_uses_shlex_quote(self):
        cmd = _build_custom_command("/usr/bin/xterm", "/home/user/my$proj", "npm start", "t")
        shell_arg = cmd[cmd.index("-c") + 1]
        assert "my$proj" not in shell_arg  # should be quoted
        assert "'/home/user/my$proj'" in shell_arg or "my\\$proj" in shell_arg

    def test_unknown_stem_returns_none_on_linux(self):
        with patch("sys.platform", "linux"):
            assert _build_custom_command("/usr/bin/unknown", "/proj", "cmd", "t") is None


class TestNormalizePathLinux:
    @patch("sys.platform", "linux")
    def test_preserves_forward_slashes(self):
        from power_atlas.data import _normalize_path
        assert _normalize_path("/home/user/project") == "/home/user/project"

    @patch("sys.platform", "linux")
    def test_strips_trailing_slash(self):
        from power_atlas.data import _normalize_path
        assert _normalize_path("/home/user/project/") == "/home/user/project"

    @patch("sys.platform", "linux")
    def test_preserves_case(self):
        from power_atlas.data import _normalize_path
        assert _normalize_path("/home/User/MyProject") == "/home/User/MyProject"
```

**Exit criteria**:
- [x] `detect_terminal()` returns a Linux terminal path when one is available (mocked test)
- [x] `_build_command()` produces correct command lists for all 5 supported Linux terminals
- [x] `_build_custom_command()` handles Linux terminals (tested)
- [x] Shell-interpolated paths use `shlex.quote()` — no injection via `$`, backticks, or quotes in cwd
- [x] `_normalize_path()` preserves forward slashes on Linux, existing Windows behavior unchanged (tested)
- [x] `{cwd}/{cmd}` template handles paths with spaces; `{cmd}` expands to separate args
- [x] Unknown terminal stems return `None` on Linux (not `cmd /k` fallback)
- [x] All existing Windows tests pass unchanged
- [x] New Linux tests pass (detection, command build, custom command, normalize path, template)

#### Implementation (2026-06-30, code: 9d78acc)

Added Linux terminal support to the launcher module. Implemented a dispatch table (`_LINUX_TERMINALS`) mapping terminal stems to their flag patterns (title, cwd, exec separator), with `_LINUX_PROBE_ORDER` for detection priority. `detect_terminal()` is now platform-aware, probing Linux terminals when not on Windows. Added `_build_linux_command()` helper that constructs commands using the dispatch table, with `shlex.quote()` for shell-interpolated paths (xterm's `sh -c` wrapper). Extracted `_build_template_command()` to properly handle `{cwd}/{cmd}` placeholders as discrete list elements (fixing space handling). Extended `_build_custom_command()` with Linux terminal dispatch. Fixed `_normalize_path()` to gate backslash conversion behind `sys.platform == "win32"`. Added 30 new tests covering Linux detection, all 5 terminal command builds, template space handling, custom command Linux paths, and Linux path normalization.

### Phase 2: UI adaptation — dynamic terminal list, platform labels [QA]

**Goal**: Make settings page and topbar terminal selector show platform-appropriate options; fix hardcoded labels.

**File scope**: `src/power_atlas/web.py`, `src/power_atlas/templates/settings.html`, `src/power_atlas/templates/index.html`, `src/power_atlas/launcher.py` (read-only import of probe list), `README.md`

**Changes**:

1. **`launcher.py` — export terminal lists for UI consumption (cached)**:

```python
_terminal_cache: list[tuple[str, str]] | None = None

def available_terminals() -> list[tuple[str, str]]:
    """Return (value, label) pairs of detected terminals for the current platform.
    
    Cached for process lifetime (terminals don't change at runtime).
    Always includes ("", "Auto-detect (...)") first and ("custom", "Custom") last.
    """
    global _terminal_cache
    if _terminal_cache is not None:
        return _terminal_cache
    
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
        auto_label = f"Auto-detect ({' › '.join(label for _, label in found)})"
    else:
        auto_label = "Auto-detect (none found)"
    
    result = [("", auto_label)]
    result.extend(found)
    result.append(("custom", "Custom"))
    _terminal_cache = result
    return result
```

2. **`web.py` — pass terminal list and platform info to ALL template renders**:

In `index()`, `settings_page()`, AND `save_settings()` (POST handler that re-renders settings.html):

```python
from .launcher import available_terminals

# Helper to build common template context
def _terminal_context() -> dict:
    options = available_terminals()
    return {
        "terminal_options": options,
        "terminal_values": {v for v, _ in options},
        "autostart_label": "Start at login" if sys.platform != "win32" else "Start with Windows",
        "no_terminals_found": len(options) == 2,  # only Auto-detect + Custom
    }
```

3. **`templates/settings.html` — dynamic terminal dropdown with guidance**:

```html
<select name="terminal_command" id="terminal_command" class="settings-select" onchange="document.getElementById('custom-terminal').style.display=this.value==='custom'?'flex':'none'">
  {% for value, label in terminal_options %}
    <option value="{{ value }}" {% if config.terminal_command == value or (value == "custom" and config.terminal_command not in terminal_values) %}selected{% endif %}>{{ label }}</option>
  {% endfor %}
</select>
{% if no_terminals_found %}
<p class="settings-hint">No terminal detected. Install one of: kitty, alacritty, gnome-terminal, konsole, or xterm.</p>
{% endif %}
```

Replace "Start with Windows" label with `{{ autostart_label }}`.

Add placeholder help text for custom template input:
```html
<input type="text" name="custom_terminal_value" ... placeholder="e.g. alacritty --working-directory {cwd} -e {cmd}">
<p class="settings-hint">Placeholders: {cwd} = workspace path, {cmd} = kiro-cli command</p>
```

4. **`templates/index.html` — dynamic topbar selector**:

```html
<select onchange="fetch('/api/save-setting',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:'terminal_command',value:this.value})})">
  {% for value, label in terminal_options %}
    <option value="{{ value }}" {% if config.terminal_command == value %}selected{% endif %}>{{ label }}</option>
  {% endfor %}
</select>
```

5. **`launcher.py:launch_session()` — improve error message for Linux users**:

```python
if not terminal:
    if sys.platform == "win32":
        msg = "No terminal found. Configure one in Settings."
    else:
        msg = "No terminal found. Install kitty, alacritty, gnome-terminal, konsole, or xterm — or configure a custom terminal in Settings."
    return LaunchResult(False, session_id, cwd, error=msg)
```

**Exit criteria**:
- [ ] Settings page shows only terminals available on the current platform
- [ ] Topbar quick selector matches settings page options
- [ ] "Auto-detect" label shows platform-appropriate terminal names
- [ ] Autostart label reads "Start at login" on Linux, "Start with Windows" on Windows
- [ ] "Custom" option still works with template input field
- [ ] Selecting a terminal via topbar persists correctly to config
- [ ] README.md updated: add Linux to supported platforms, note PyGObject requirement, update auto-detect description

### Phase 3: Tray icon PNG for Linux [QA]

**Goal**: Ship a PNG tray icon and select the correct format by platform.

**File scope**: `src/power_atlas/tray.py`, `src/power_atlas/static/poweratlas-tray.png`

**Changes**:

1. **Generate `poweratlas-tray.png`** from the existing `.ico` file (extract largest frame, save as PNG). 128x128 or 256x256.

2. **`tray.py:_create_icon()` — platform-adaptive icon loading**:

```python
def _create_icon() -> Image.Image:
    static_dir = Path(__file__).parent / "static"
    if sys.platform == "win32":
        icon_path = static_dir / "poweratlas-tray.ico"
    else:
        icon_path = static_dir / "poweratlas-tray.png"
    try:
        with Image.open(icon_path) as img:
            img.load()
            return img.copy()
    except OSError:
        log.warning("Tray icon not found at %s, using fallback", icon_path)
        img = Image.new("RGBA", (16, 16), (60, 120, 220, 255))
        ImageDraw.Draw(img).text((3, 1), "P", fill="white")
        return img
```

**Exit criteria**:
- [ ] `poweratlas-tray.png` exists in `static/` and is a valid PNG (128x128+)
- [ ] `_create_icon()` loads `.ico` on Windows and `.png` on Linux
- [ ] Fallback still works when icon file is missing

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| Linux terminal flag conventions may differ across versions | Low — commands fail gracefully with subprocess error | Test against documented flags; `LaunchResult.error` propagates to UI toast |
| `_build_template_command()` regex split changes behavior for existing Windows custom templates | Medium — could break user configs | Only activates when `{cwd}` or `{cmd}` present in template; existing non-template paths unchanged |
| gnome-terminal `--` separator changed to require `--` before command in recent versions | Low | Use `--` which works across versions |
| Shell injection via `cwd` in xterm's `sh -c` wrapper | Low (mitigated) | All shell-interpolated values use `shlex.quote()` |
| `_normalize_path()` change affects in-flight cache keys | None — cache is ephemeral, cleared on restart | No migration needed |
| Stale terminal config (user uninstalls selected terminal) | Low — launch fails with clear error | `LaunchResult.error` surfaces the failure; user can reconfigure in Settings |
| `xdg-open` missing on minimal Linux installs | Low — tray "Open" and "Logs" silently fail | Existing `except Exception` in tray.py catches it; log warning emitted |

## 7) Verification

**Automated** (run on both platforms):
```bash
pytest tests/test_launcher.py -v
pytest tests/ -v  # full suite, ensure no regressions
```

**Manual (Linux)**:
- Launch PowerAtlas, verify tray icon appears
- Open dashboard, verify terminal selector shows detected Linux terminals
- Launch a kiro-cli session from a workspace card
- Verify custom template with `{cwd}` and path-with-spaces works
- Check Settings page → "Start at login" label correct

**Manual (Windows)**:
- Full smoke test — existing behavior unchanged
- Terminal selector still shows wt/pwsh/cmd
- Custom template still works

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `README.md` | Add Linux to supported platforms; note `PyGObject` requirement | 2 |

## 9) Implementation Divergences from Plan

<Reserved — filled during implementation>

## Review Log

### 2026-06-30 -- Implementation Review (after Phase 1, personas: Security auditor, Reliability engineer, Maintainability reviewer, Senior engineer)

Implementation health: Green.
4 findings (0 High, 3 Medium, 4 Low). High-effort, 4 personas.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | Medium | `_build_custom_command` Linux path passes `cmd_str` raw to `sh -c` — intentional but undocumented | Fixed — added trust boundary comment (ff7a10e) |
| 2 | Medium | `_normalize_path("/")` returns empty string on Linux (root path edge case) | Fixed — added `or "/"` guard (ff7a10e) |
| 3 | Medium | Copy-paste duplication between `_build_linux_command` and `_build_custom_command` Linux block | Escalated — design choice, see below |
| 4 | Low | Test mocking uses global `sys.platform` instead of module-scoped patch | Accepted — works correctly today, fragility is bounded |
| 5 | Low | `use_terminal=False` branch is from separate uncommitted feature, not Phase 1 scope | Accepted — unrelated working-tree change, not part of this commit |
| 6 | Low | No test for empty-title branch on terminals with title_flag (kitty, alacritty) | Accepted — guard logic is simple, covered incidentally via konsole |
| 7 | Low | `_build_template_command` doesn't warn when `{cmd}` placeholder is absent | Accepted — user misconfiguration, UX issue for future improvement |

Duplication finding (#3): The Security auditor, Reliability, and Maintainability reviewers all noted that `_build_linux_command()` and the Linux block in `_build_custom_command()` share ~20 lines of near-identical flag-building logic. A shared `_build_linux_base()` helper could eliminate this. However, the two functions differ in their final command portion (list of args vs shell string) and the duplication is bounded (won't grow). Deferring to user decision.

4 personas (Architect, Senior engineer, End-user advocate, Reliability engineer). 19 findings total (3 High, 8 Medium, 8 Low). 11 auto-resolved.

| # | Severity | Finding (one line) | Status (one line) |
|---|---|---|---|
| 1 | High | Shell injection in xterm `sh -c` wrapper via unescaped `cwd` | Resolved — use `shlex.quote()` for all shell-interpolated values |
| 2 | High | Phase 1-2 marked parallel but Phase 2 depends on Phase 1's `available_terminals()` export | Resolved — removed `[P:N]` annotations |
| 3 | High | No user guidance when zero Linux terminals detected | Resolved — added hint text in settings + improved error message |
| 4 | Medium | `_build_template_command` joins kiro_args as single string, breaks subprocess semantics | Resolved — `{cmd}` now expands to separate list elements |
| 5 | Medium | `save_settings` handler re-renders settings.html without terminal_options context | Resolved — added `_terminal_context()` helper used by all template renders |
| 6 | Medium | `available_terminals()` probes disk per page load (5 which calls) | Resolved — cached for process lifetime |
| 7 | Medium | No `_normalize_path` Linux test | Resolved — added `TestNormalizePathLinux` class |
| 8 | Medium | No `_build_custom_command` Linux test coverage | Resolved — added `TestBuildCustomCommandLinux` class |
| 9 | Medium | Stale terminal config not revalidated | Noted — acceptable risk; launch fails with clear error message |
| 10 | Medium | `xdg-open` missing on minimal installs has no fallback | Noted — existing exception handling logs warning; low priority |
| 11 | Medium | Custom template placeholder syntax not documented in UI | Resolved — added placeholder help text below custom input |
| 12 | Low | konsole has no `--title` flag | Noted — documented as known limitation |
| 13 | Low | Plan doesn't specify PNG generation method | Noted — implementer uses Pillow one-liner or ImageMagick |
| 14 | Low | gnome-terminal `--title=` may be ambiguous if title contains `=` | Noted — exotic edge case |
| 15 | Low | `_build_custom_command` cmd fallback fires on Linux for unknown terminals | Resolved — returns `None` on Linux for unknown stems |
| 16 | Low | `use_pywebview` config field untested on Linux | Noted — vestigial field, not in scope |
| 17 | Low | Auto-detect label mixed case may confuse users | Noted — labels match canonical terminal names |
| 18 | Low | No smoke test for autostart.py Linux path | Noted — existing test covers desktop file creation |
| 19 | Low | Terminal version incompatibility fails silently | Noted — OSError caught, error propagated to UI toast |
