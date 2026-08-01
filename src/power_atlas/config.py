"""Thread-safe config persistence via TOML."""

import ipaddress
import logging
import os
import re
import secrets
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


_DEFAULT_TERMINAL_COMMAND = "wt new-tab --title {title} -p {wt_profile} -d {cwd} -- pwsh -NoExit -Command {pscmd}"

# Legacy top-level keys migrated into structured fields on load. They must be
# excluded from unknown-key preservation (else they'd defeat the save-time drop
# below and linger in config.toml forever) and always dropped on save.
_LEGACY_KEYS = frozenset({"trust_all_tools", "terminal_command"})


@dataclass
class LaunchProfile:
    id: str = "default"
    name: str = "Default"
    terminal_command: str = ""
    wt_profile: str = "PowerShell"


@dataclass
class Config:
    port: int = 0  # 0 = random (OS-assigned), >0 = static port
    peek_hotkey: str = "ctrl+shift+z"
    default_directory: str = ""  # Global fallback for provider launches without workspace selection
    active_launch_profile: str = "default"
    launch_profiles: list[LaunchProfile] = field(default_factory=lambda: [LaunchProfile()])
    pinned_folders: list[str] = field(default_factory=list)  # paths only
    pinned_sessions: list[str] = field(default_factory=list)
    custom_launchers: list[dict] = field(default_factory=list)
    provider_settings: dict[str, dict] = field(default_factory=dict)
    workspace_settings: dict[str, dict] = field(default_factory=dict)
    tag_settings: dict[str, dict] = field(default_factory=dict)
    notifications: dict = field(default_factory=lambda: {"enabled": False})
    # ACP session-lifecycle tunables. Read **once at startup** and pushed into
    # `acp` as module-level names (`acp.apply_config`), never read per call:
    # `at_capacity()` runs on the event loop and `load_config()` is an uncached
    # whole-file TOML parse, so a read there reproduces the stall `_handle_new`
    # already threads out to avoid. The consequence a settings UI has to state
    # is that changing any of the three needs a restart to take effect.
    #
    # 8 is the measured default, not a guess: one agent carrying eight sessions
    # was measured 2026-08-01 (`plans/260731_ACP_REMOTE_CLIENT_PRODUCTIZATION.md`).
    # Bounds live on the write path, not here — `load_config` is documented as
    # never raising, and ~16 routes call it on the loop.
    acp_max_sessions: int = 8
    acp_idle_ttl_seconds: int = 1800
    acp_prompt_silence_seconds: int = 900
    # The single non-loopback address PowerAtlas additionally listens on, and
    # the single non-loopback name `web._ALLOWED_HOSTS` admits. Empty means
    # loopback-only, which is the default and the state a version bump must
    # never silently leave (D21). Validated by `validate_remote_bind_address`
    # on the write path and sanitised to "" here on load — `load_config` is
    # documented as never raising and ~16 routes call it on the event loop.
    remote_bind_address: str = ""


# The device secret backing the remote cookie. Its own file rather than a key
# in config.toml (D8): `save_config` rewrites the whole file from an `asdict`
# of every field, so a credential there is in the blast radius of every
# mutating route and of the 18 known config-leaking tests.
#
# On Windows the protection is `%LOCALAPPDATA%`'s inherited ACLs, **not** file
# mode: `os.chmod` on win32 toggles only the read-only attribute and never
# touches an ACL. The 0o600 below is therefore real on POSIX and decorative on
# Windows, and saying so is the honest form.
REMOTE_SECRET_PATH = CONFIG_DIR / "remote-secret"

# `len(secrets.token_urlsafe(32))` is 43. A shorter file is a truncated write,
# a hand-edit, or an empty file, and every one of those must fail closed: an
# empty secret compared with `compare_digest` matches an empty signature and
# removes authentication while the remote socket stays bound.
REMOTE_SECRET_MIN_LEN = 43


def load_remote_secret() -> str:
    """Return the device secret, or ``""`` when it is unusable.

    Unusable covers absent, unreadable, empty, whitespace-only and shorter than
    ``REMOTE_SECRET_MIN_LEN``. Every one of them collapses to the same empty
    string precisely so that no caller has to enumerate them, and so that the
    only possible verdict on a damaged secret is "no remote access".

    Never raises: it is called from the bind path at startup and from the
    request path's startup setter, and neither has anywhere to put an
    ``OSError``.
    """
    try:
        raw = REMOTE_SECRET_PATH.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    value = raw.strip()
    if len(value) < REMOTE_SECRET_MIN_LEN:
        return ""
    return value


def ensure_remote_secret() -> str:
    """Create the device secret if absent; return it, or ``""`` on failure.

    Called on first enable — the write path that sets ``remote_bind_address``
    to a usable value — so that enabling remote access and being able to
    authenticate to it are one step. An existing usable secret is returned
    untouched: regenerating here would silently revoke every device that
    already holds a cookie, on a route the user thinks only flips an address.
    """
    existing = load_remote_secret()
    if existing:
        return existing
    value = secrets.token_urlsafe(32)
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        fd = os.open(REMOTE_SECRET_PATH,
                     os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, value.encode("ascii"))
        finally:
            os.close(fd)
    except OSError:
        log.error("Could not write %s; remote access stays disabled",
                  REMOTE_SECRET_PATH)
        return ""
    return value


def validate_remote_bind_address(raw: object, port: int) -> str:
    """Return ``""`` when ``raw`` is a usable remote bind address, else why not.

    Rejection is on **parsed properties**, never string equality. A comparison
    against ``"0.0.0.0"``/``"::"`` is bypassed by ``::0``, ``0000::`` and
    ``::ffff:0.0.0.0``, and would let ``remote_bind_address = "127.0.0.1"``
    produce two listeners on the same address and port inside one process.

    ``ipaddress.ip_address`` also enforces "an IP, never an FQDN" (D2)
    mechanically: a single-label name is hijackable over LLMNR/NBT-NS/mDNS, so
    widening the Host allowlist to one would re-open the rebinding hole the
    allowlist exists to close.

    The bracketed, zone-id and non-canonical forms are rejected separately,
    because ``web._host_allowed`` strips brackets and lowercases before its
    membership test — an address stored as ``[FD00::1]`` or ``fe80::1%eth0``
    would bind a socket and then match no Host header ever sent, which is a
    listener nobody can reach and no error anywhere.

    An empty value is valid: it means loopback-only, which is the default.
    """
    if not isinstance(raw, str):
        return "remote_bind_address must be a string"
    value = raw.strip()
    if not value:
        return ""
    if len(value) > 45:  # longest textual IPv6 form
        return "remote_bind_address is too long to be an IP address"
    if "%" in value:
        return "remote_bind_address must not carry an IPv6 zone id"
    if "[" in value or "]" in value:
        return "remote_bind_address must be unbracketed"
    try:
        parsed = ipaddress.ip_address(value)
    except ValueError:
        return ("remote_bind_address must be a literal IP address, "
                "not a hostname")
    # An IPv4-mapped IPv6 address carries its properties on the embedded v4
    # address, so `::ffff:0.0.0.0` is NOT `is_unspecified` and `::ffff:127.0.0.1`
    # is NOT `is_loopback`. Unwrap before asking.
    checked = getattr(parsed, "ipv4_mapped", None) or parsed
    if checked.is_unspecified:
        return "remote_bind_address must not be a wildcard address"
    if checked.is_loopback:
        return "remote_bind_address must not be a loopback address"
    if checked.is_multicast:
        return "remote_bind_address must not be a multicast address"
    if str(parsed) != value:
        return (f"remote_bind_address must be in canonical form "
                f"(did you mean {parsed}?)")
    if port == 0:
        # SC-3b. With `port = 0` the OS assigns a number **per bind call**, so
        # the loopback and remote sockets would land on different ports and the
        # laptop and phone URLs would permanently disagree — and a phone cannot
        # bookmark an ephemeral port in the first place.
        return "port must be a fixed non-zero value when remote_bind_address is set"
    return ""


def get_workspace_settings(config: Config, cwd: str) -> dict:
    """Return workspace settings for a path, normalizing for lookup.

    Uses a pre-built normalized lookup dict (cached on the Config instance
    at load time) for O(1) access instead of linear scan.
    Returns a shallow copy to prevent callers from mutating cached state.
    """
    from .data import _normalize_path

    norm_map = getattr(config, "_ws_norm_map", None)
    if norm_map is None:
        norm_map = {_normalize_path(k): v for k, v in config.workspace_settings.items()}
        config._ws_norm_map = norm_map
    found = norm_map.get(_normalize_path(cwd))
    if found is None:
        return {"tags": [], "color": ""}
    return {"tags": list(found["tags"]), "color": found["color"]}


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

    return LaunchProfile(
        id=raw_id,
        name=name,
        terminal_command=tc,
        wt_profile=wt,
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
        extra = {k: v for k, v in data.items() if k not in fields and k not in _LEGACY_KEYS}
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
        config.custom_launchers = [x for x in config.custom_launchers if isinstance(x, dict)]
        config.provider_settings = {k: v for k, v in config.provider_settings.items() if isinstance(v, dict)}

        # Sanitize workspace_settings: drop non-dict values, validate keys, normalize inner fields
        config.workspace_settings = {
            _strip_control_chars(k)[:1024]: v
            for k, v in config.workspace_settings.items()
            if isinstance(v, dict) and isinstance(k, str) and len(k) <= 1024
        }
        for path, ws in list(config.workspace_settings.items()):
            ws.setdefault("tags", [])
            ws.setdefault("color", "")
            ws["tags"] = [t for t in ws["tags"] if isinstance(t, str)]
            if not isinstance(ws.get("color"), str):
                ws["color"] = ""

        # Sanitize tag_settings: drop non-dict values, validate keys, normalize inner fields
        config.tag_settings = {
            _strip_control_chars(k)[:64]: v
            for k, v in config.tag_settings.items()
            if isinstance(v, dict) and isinstance(k, str) and len(k) <= 64
        }
        for tag_name, ts in list(config.tag_settings.items()):
            ts.setdefault("color", "")
            if not isinstance(ts.get("color"), str):
                ts["color"] = ""

        # Ensure "hidden" tag always exists in tag_settings
        config.tag_settings.setdefault("hidden", {"color": ""})

        # Sanitize default_directory: must be string, strip control chars
        if not isinstance(config.default_directory, str):
            config.default_directory = ""
        config.default_directory = _strip_control_chars(config.default_directory).strip()

        # Sanitize remote_bind_address: fail closed to loopback-only, log, never
        # raise. `load_config` is documented as never raising and is called on
        # the event loop from ~16 routes, so a raising validator would turn one
        # config.toml typo into a 500 on every route plus a startup crash. The
        # named error SC-3b asks for is produced here in the log and on the
        # write path in `web.save_setting`.
        if not isinstance(config.remote_bind_address, str):
            config.remote_bind_address = ""
        config.remote_bind_address = config.remote_bind_address.strip()
        if config.remote_bind_address:
            reason = validate_remote_bind_address(
                config.remote_bind_address, config.port)
            if reason:
                log.error("remote_bind_address %r rejected (%s); "
                          "listening on loopback only",
                          config.remote_bind_address, reason)
                config.remote_bind_address = ""

        # Platform-aware terminal_command default: fill on Windows, leave empty on Linux (auto-detect)
        if sys.platform == "win32":
            for profile in config.launch_profiles:
                if not profile.terminal_command:
                    profile.terminal_command = _DEFAULT_TERMINAL_COMMAND

        # Build normalized workspace-settings lookup map for O(1) access
        from .data import _normalize_path
        config._ws_norm_map = {_normalize_path(k): v for k, v in config.workspace_settings.items()}

        return config


def save_config(config: Config) -> None:
    """Atomic write: .tmp → fsync → os.replace. Lock-protected."""
    with _lock:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_PATH.with_suffix(".tmp")
        try:
            data = asdict(config)
            for legacy in _LEGACY_KEYS:
                data.pop(legacy, None)  # never write migrated legacy keys
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
