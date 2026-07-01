"""Thread-safe config persistence via TOML."""

import os
import sys
import threading
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

import tomli_w


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


@dataclass
class Config:
    peek_hotkey: str = "ctrl+shift+z"
    terminal_command: str = ""
    pinned_folders: list[str] = field(default_factory=list)
    pinned_sessions: list[str] = field(default_factory=list)
    workspace_icons: dict[str, str] = field(default_factory=dict)
    custom_launchers: list[dict] = field(default_factory=list)
    provider_settings: dict[str, dict] = field(default_factory=dict)


def load_config() -> Config:
    """Load config from TOML. Missing keys get defaults, unknown keys ignored, wrong types get defaults."""
    with _lock:
        if not CONFIG_PATH.exists():
            return Config()
        try:
            with open(CONFIG_PATH, "rb") as f:
                data = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError):
            return Config()
        defaults = Config()
        fields = {f.name for f in Config.__dataclass_fields__.values()}
        kwargs = {}
        for k, v in data.items():
            if k not in fields:
                continue
            expected = type(getattr(defaults, k))
            if isinstance(v, expected):
                kwargs[k] = v
            # else: skip — default will fill in via dataclass
        config = Config(**kwargs)
        # Migration: trust_all_tools=true → provider_settings["kiro-cli"].default_args = "-a"
        if data.get("trust_all_tools") is True and not config.provider_settings:
            config.provider_settings["kiro-cli"] = {
                "default_args": "-a",
                "color": "",
                "enabled": True,
            }
        return config


def save_config(config: Config) -> None:
    """Atomic write: .tmp → fsync → os.replace. Lock-protected."""
    with _lock:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_PATH.with_suffix(".tmp")
        try:
            data = asdict(config)
            data.pop("trust_all_tools", None)  # never write legacy key
            with open(tmp, "wb") as f:
                tomli_w.dump(data, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, CONFIG_PATH)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
