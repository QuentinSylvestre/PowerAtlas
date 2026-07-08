"""Thread-safe config persistence via TOML."""

import logging
import os
import re
import shutil
import sys
import threading
import tomllib
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

import tomli_w

log = logging.getLogger(__name__)


def _config_dir() -> Path:
    """Platform-appropriate config directory."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config"))
    return base / "power-atlas"


CONFIG_DIR = _config_dir()
CONFIG_PATH = CONFIG_DIR / "config.toml"

_lock = threading.Lock()

_PROFILE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SHELL_PROCESS_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}\.exe$")
_SHELL_DENY_LIST = frozenset({"cmd.exe", "conhost.exe", "explorer.exe", "svchost.exe"})
_HELPER_ALLOW_LIST = frozenset({"pwsh", "pwsh.exe"})


@dataclass
class LaunchProfile:
    id: str = "default"
    name: str = "Default"
    terminal_command: str = ""
    wt_profile: str = "PowerShell"
    shell_process_name: str = "pwsh.exe"
    helper_runner: str = "pwsh"
    attach_timeout_ms: int = 4500
    helper_timeout_ms: int = 8000
    mcp_safe_enabled: bool = True


@dataclass
class Config:
    port: int = 0  # 0 = random (OS-assigned), >0 = static port
    peek_hotkey: str = "ctrl+shift+z"
    active_launch_profile: str = "default"
    launch_profiles: list[LaunchProfile] = field(default_factory=lambda: [LaunchProfile()])
    pinned_folders: list[str] = field(default_factory=list)  # paths only
    pinned_sessions: list[str] = field(default_factory=list)
    workspace_icons: dict[str, str] = field(default_factory=dict)
    custom_launchers: list[dict] = field(default_factory=list)
    provider_settings: dict[str, dict] = field(default_factory=dict)


def get_active_launch_profile(config: Config) -> LaunchProfile:
    """Return a copy of the active launch profile, falling back deterministically."""
    for profile in config.launch_profiles:
        if profile.id == config.active_launch_profile:
            return replace(profile)
    # Fallback: first in list, or fresh default
    if config.launch_profiles:
        return replace(config.launch_profiles[0])
    return LaunchProfile()


def _strip_control_chars(s: str) -> str:
    """Remove characters < 0x20 except space (0x20)."""
    return "".join(ch for ch in s if ord(ch) >= 0x20)


def _normalize_launch_profile(raw: dict, index: int, seen_ids: dict[str, str], import_counter: list[int]) -> LaunchProfile:
    """Normalize a raw TOML dict into a validated LaunchProfile."""
    # --- id ---
    raw_id = str(raw.get("id", "")) if raw.get("id") is not None else ""
    if not _PROFILE_ID_RE.match(raw_id):
        import_counter[0] += 1
        raw_id = f"imported-{import_counter[0]}"

    # Handle duplicate IDs: keep first, regenerate later
    if raw_id in seen_ids:
        import_counter[0] += 1
        raw_id = f"imported-{import_counter[0]}"

    seen_ids[raw_id] = raw_id

    # --- name ---
    name = str(raw.get("name", "Default")) if raw.get("name") is not None else "Default"
    name = _strip_control_chars(name).strip()[:80] or "Default"

    # --- terminal_command ---
    tc = str(raw.get("terminal_command", "")) if raw.get("terminal_command") is not None else ""
    tc = _strip_control_chars(tc)[:512]

    # --- wt_profile ---
    wt = str(raw.get("wt_profile", "")) if raw.get("wt_profile") is not None else ""
    wt = _strip_control_chars(wt).strip()[:128]
    if not wt:
        wt = "PowerShell"

    # --- shell_process_name ---
    spn = str(raw.get("shell_process_name", "")) if raw.get("shell_process_name") is not None else ""
    if not _SHELL_PROCESS_RE.match(spn) or spn.lower() in _SHELL_DENY_LIST:
        spn = "pwsh.exe"

    # --- helper_runner ---
    hr = str(raw.get("helper_runner", "")) if raw.get("helper_runner") is not None else ""
    if hr not in _HELPER_ALLOW_LIST:
        hr = "pwsh"

    # --- attach_timeout_ms ---
    atm = raw.get("attach_timeout_ms", 4500)
    if not isinstance(atm, int) or isinstance(atm, bool):
        atm = 4500
    atm = max(500, min(30000, atm))

    # --- helper_timeout_ms ---
    htm = raw.get("helper_timeout_ms", 8000)
    if not isinstance(htm, int) or isinstance(htm, bool):
        htm = 8000
    htm = max(1000, min(60000, htm))
    # Ensure helper >= attach + 1000
    if htm < atm + 1000:
        htm = atm + 1000

    # --- mcp_safe_enabled ---
    mse = raw.get("mcp_safe_enabled", True)
    if not isinstance(mse, bool):
        mse = True

    return LaunchProfile(
        id=raw_id,
        name=name,
        terminal_command=tc,
        wt_profile=wt,
        shell_process_name=spn,
        helper_runner=hr,
        attach_timeout_ms=atm,
        helper_timeout_ms=htm,
        mcp_safe_enabled=mse,
    )


def load_config() -> Config:
    """Load config from TOML. Missing keys get defaults, unknown keys ignored, wrong types get defaults."""
    with _lock:
        if not CONFIG_PATH.exists():
            return Config()
        try:
            with open(CONFIG_PATH, "rb") as f:
                data = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
            try:
                shutil.copy2(CONFIG_PATH, CONFIG_PATH.with_name(CONFIG_PATH.name + ".bak"))
                log.warning("Corrupt config backed up to %s; using defaults", CONFIG_PATH.with_name(CONFIG_PATH.name + ".bak"))
            except Exception:
                log.warning("Corrupt config; using defaults (backup failed)")
            return Config()
        defaults = Config()
        fields = {f.name for f in Config.__dataclass_fields__.values()}
        kwargs = {}
        for k, v in data.items():
            if k not in fields:
                continue
            expected = type(getattr(defaults, k))
            # Python: isinstance(True, int) is True; reject booleans for non-bool fields
            if isinstance(v, bool) and expected is not bool:
                continue
            if isinstance(v, expected):
                kwargs[k] = v
            # else: skip — default will fill in via dataclass
        # Preserve unknown keys so future config additions aren't lost on re-save
        extra = {k: v for k, v in data.items() if k not in fields and k != "trust_all_tools"}
        config = Config(**kwargs)
        # Store as instance attr (not a dataclass field) — object identity constraint:
        # the same Config instance returned by load must be passed to save for extras to persist.
        config._extra = extra

        # --- Launch profile normalization ---
        has_launch_profiles_in_toml = "launch_profiles" in data and isinstance(data.get("launch_profiles"), list)

        if has_launch_profiles_in_toml:
            raw_profiles = data["launch_profiles"]
            seen_ids: dict[str, str] = {}
            import_counter = [0]
            normalized = []
            for i, raw in enumerate(raw_profiles):
                if isinstance(raw, dict):
                    normalized.append(_normalize_launch_profile(raw, i, seen_ids, import_counter))
            config.launch_profiles = normalized if normalized else [LaunchProfile()]

            # Remap active_launch_profile if it pointed to a regenerated duplicate
            active_id = config.active_launch_profile
            profile_ids = {p.id for p in config.launch_profiles}
            if active_id not in profile_ids:
                # Check if any profile was originally the active_id before dedup
                # Fall back to first profile
                config.active_launch_profile = config.launch_profiles[0].id
        else:
            # Legacy migration: terminal_command -> default profile
            legacy_tc = data.get("terminal_command", "")
            if isinstance(legacy_tc, str) and legacy_tc:
                config.launch_profiles = [LaunchProfile(terminal_command=legacy_tc)]
            else:
                config.launch_profiles = [LaunchProfile()]

        # Migration: pinned_folders list[dict] -> list[str] (provider-agnostic)
        if config.pinned_folders and isinstance(config.pinned_folders[0], dict):
            seen = set()
            paths = []
            for entry in config.pinned_folders:
                folder = entry.get("folder", "") if isinstance(entry, dict) else (entry if isinstance(entry, str) else "")
                if folder and folder not in seen:
                    seen.add(folder)
                    paths.append(folder)
            config.pinned_folders = paths
        # Already list[str] — no migration needed
        # Migration: trust_all_tools=true → provider_settings["kiro-cli"].default_args = "-a"
        if data.get("trust_all_tools") is True and "kiro-cli" not in config.provider_settings:
            config.provider_settings["kiro-cli"] = {
                "default_args": "-a",
                "color": "",
                "enabled": True,
            }
        # Sanitize nested types: drop entries that aren't the expected type
        config.pinned_folders = [x for x in config.pinned_folders if isinstance(x, str)]
        config.pinned_sessions = [x for x in config.pinned_sessions if isinstance(x, str)]
        config.workspace_icons = {k: v for k, v in config.workspace_icons.items() if isinstance(k, str) and isinstance(v, str)}
        config.custom_launchers = [x for x in config.custom_launchers if isinstance(x, dict)]
        config.provider_settings = {k: v for k, v in config.provider_settings.items() if isinstance(v, dict)}
        return config


def save_config(config: Config) -> None:
    """Atomic write: .tmp → fsync → os.replace. Lock-protected."""
    with _lock:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_PATH.with_suffix(".tmp")
        try:
            data = asdict(config)
            data.pop("trust_all_tools", None)  # never write legacy key
            data.pop("terminal_command", None)  # defensive: field removed from Config but guard legacy
            # Restore unknown keys preserved at load time (object-identity constraint:
            # caller must pass the same Config instance returned by load_config).
            data.update(getattr(config, "_extra", {}) or {})
            with open(tmp, "wb") as f:
                tomli_w.dump(data, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, CONFIG_PATH)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
