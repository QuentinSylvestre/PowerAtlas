"""Generate the derived kiro-cli agent that carries PowerAtlas's ACP permission mode.

260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL Phase 1, on the generator first
written by 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 1.

PowerAtlas never modifies the user's own kiro-cli agent. It reads a **base
agent** (`~/.kiro/agents/<name>.md`, configurable, default `kiro_default`) and
writes a **derived agent** (`~/.kiro/agents/poweratlas-acp.md`) that is the base
file byte-for-byte with one `permissions:` block inserted into, or replaced
inside, its YAML frontmatter. Interactive terminal kiro-cli sessions keep using
the base agent untouched, which is what keeps their permission posture
independent of anything PowerAtlas does.

**The block is compiled, and the derived agent is always written.** The
permission mode (`acp_permission_mode`: `yolo` or `manual`) and, in Manual, the
rules (`acp_permission_rules`) live in `config.toml`; `compile_block` turns them
into kiro-cli rules (260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL D-1, D-13). Every
mode starts with the Always blocked floor (`FLOOR_RULES`), so there is no Off
state and no delete path (D-8, D-18): a missing file is a failure the settings
panel reports and the session gate refuses on (D-34), never a posture. The
cost, accepted by the user (D-37, R-4): `poweratlas-acp` is always selectable
from kiro-cli's own terminal agent picker.

Five properties this module is built around:

* **Injection is textual, never parsed**
  (260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL D-18). No YAML library
  is declared or installed in this project, and byte-identity outside the
  injected block is an exit criterion — only *not* parsing achieves that by
  construction, since a parse/re-emit round trip normalises quoting, key order
  and flow style.

* **A base agent this module cannot splice safely is refused, not guessed at.**
  The splice assumes a frontmatter whose root mapping sits at column 0, which is
  the shape the shipped `kiro_default.md` has. An indented root mapping, or a
  frontmatter region that turns out to hold document body, raises rather than
  producing a file with two `permissions:` keys or a block spliced into the
  prose — both of which are the silent fail-open below. A base agent that does
  not exist at all is different: generation uses `MINIMAL_BASE` and reports it
  (260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL D-28), so a clean kiro-cli
  install still gets the floor.

* **The write is verified before it is published**
  (260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL D-19): tmp -> `fsync`
  -> read the **tmp** back and re-excise it -> only then `os.replace`. The
  ordering is the point, not the check. Verifying after `os.replace` detects a
  bad splice with the last-good file already destroyed. Staging outside the
  agents directory and publishing only a file that has already been read back
  means a failure leaves the previous file untouched because `os.replace`
  never ran.

  The pattern is `config.save_config`'s, which the secret files share too
  (`config._write_secret_file`). It matters more here than there. A torn
  `permissions:` block fails **open**: kiro-cli loads an agent whose
  frontmatter does not parse without an error or a warning, and silently falls
  back to the wider scopes. That was reproduced live, by accident, across seven
  consecutive probe runs in 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL
  Phase 0.

* **Generation failure never widens the posture**
  (260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL D-10/SC-8). A failed
  regeneration leaves whatever is already on disk exactly where it is. It never
  writes a base-agent copy over a previously generated derived agent.

* **A file PowerAtlas did not write is never overwritten**
  (260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL D-29). The name is not
  reserved; a file at the target whose block lacks `_PROVENANCE_MARKER` is the
  user's, and generation refuses and reports it.

`acp.py` must not import this module: it states a narrow isolation boundary in
its own header and imports three names from two intra-package modules. The name it needs,
`DERIVED_AGENT_NAME`, lives in `config.py` for that reason
(260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL D-20). What it needs from
here — whether the derived agent is in effect — reaches it through
`web._derived_agent_in_effect`, installed as `acp.mode_gate_hook`.
"""

import copy
import hashlib
import json
import logging
import os
import re
import stat
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from .config import (DERIVED_AGENT_NAME, load_config, parse_permission_mode,
                     save_config)

log = logging.getLogger(__name__)


class AgentProfileError(Exception):
    """A generation failure this module predicted and named.

    Distinct from an unexpected bug on purpose. `web.py`'s call sites catch
    `Exception` broadly so that neither startup nor a settings write can be
    aborted by either kind, but only this type carries a message worth showing
    the user in the settings panel.

    `detail`, when set, is `validate_rules`'s structured reason for the rule
    editor: `{row, list, pattern, message}`, with `message` in the editor's own
    words (Phase 2 review, U2).

    `kind`, when set, is which fix the settings panel should name (final
    review, M-8): `"base"` (the base agent), `"rules"` (a rule that does not
    compile), `"foreign"` (a file PowerAtlas did not write) or `"write"`.
    """

    def __init__(self, message: str = "", detail: dict | None = None,
                 kind: str = ""):
        super().__init__(message)
        self.detail = detail
        self.kind = kind


# `~/.kiro/agents` as a module-level constant rather than a function reading
# `Path.home()` per call: the test suite's autouse `isolated_config` fixture
# redirects it into `tmp_path`, and a per-call `Path.home()` would need every
# test that reaches `lifespan` to patch `Path.home` itself — which would also
# redirect `data_kiro_v3`'s session scan and half the launcher.
KIRO_AGENTS_DIR = Path.home() / ".kiro" / "agents"

# Charset regex plus the Windows reserved device names (D-8). A bare charset
# regex admits `CON`, `PRN`, `AUX`, `NUL`, `COM0`-`COM9` and `LPT0`-`LPT9`,
# every one of which misbehaves when opened as `<name>.md` on Windows — the
# open succeeds against the device, not a file. The `0` forms are in Microsoft's
# own reserved list ("Naming Files, Paths, and Namespaces") even though they
# happened to open as ordinary files on the machine this was written on; the set
# follows the documentation rather than one machine's behaviour.
_AGENT_NAME_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL",
                 *(f"COM{i}" for i in range(0, 10)),
                 *(f"LPT{i}" for i in range(0, 10))}

# A top-level `permissions` key inside the frontmatter. Anchored at column 0, so
# a nested `permissions:` under some other mapping is left alone. `\s*` before
# the colon because `permissions :` is legal YAML, and the quoted spellings
# because `"permissions":` is the same key. Every missed spelling leaves the
# base's own block in place beside the injected one — two top-level keys of the
# same name, which is the fail-open shape this module exists to avoid.
#
# Column 0 is also why `_check_frontmatter_shape` refuses an indented root
# mapping outright: a base whose every key including `permissions:` is indented
# by two spaces is valid YAML that this regex cannot see, and the injected block
# would land at column 0 *beside* the base's surviving block.
_PERMISSIONS_KEY_RE = re.compile(
    r"""^(?:permissions|"permissions"|'permissions')\s*:""")

# Characters that continue a `permissions:` block on a line of their own.
# Space and tab are the ordinary indented case. `-` is a **block sequence at
# column 0**, which is how YAML lets `permissions:` take a list without
# indenting it — valid, common, and the case that makes this set more than
# "indented": stopping at it replaces the key line only and leaves the base's
# rule items stranded at column 0 after the injected mapping, which is a root
# document that mixes a mapping with a sequence and does not parse at all.
# `#` is a comment, which a YAML lexer discards but which sits between the key
# and its indented value often enough to matter.
_BLOCK_CONTINUATION_CHARS = " \t-#"

# The provenance stamp every block this module writes carries. Its purpose is
# recognising PowerAtlas's own file (260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL
# D-29): generation overwrites only an absent file or one whose block carries
# it, and `derived_block_state` reads a marked block that does not match the
# current settings as `"stale"` (safe to regenerate, D-30) rather than
# `"unknown"` (someone else's file). The name `poweratlas-acp` is not reserved,
# so a block without the marker is left alone and reported.
#
# A marker rather than byte-equality against every block this module has ever
# written: byte-equality would fail to recognise its own output from one
# release ago, which is exactly the file an upgrade needs to regenerate.
_PROVENANCE_MARKER = "Written by PowerAtlas"

# The header line that records which settings a block was compiled from
# (`settings_fingerprint`). Read back by the D-30 self-heal to tell a posture
# change made outside the dashboard (the fingerprint moved) from a hand edit of
# the file itself (it did not), which is what D-35's notice needs.
_FINGERPRINT_LABEL = "Settings fingerprint: "
_FINGERPRINT_RE = re.compile(r"#\s*Settings fingerprint: ([0-9a-f]{16})\b")
_MODE_LINE_RE = re.compile(r"#\s*Permission mode: ([A-Za-z]+)")

# 260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL final review (SEC3, H-B). The
# base agent a block was built from, by name and by a digest of its text with
# its own `permissions:` blocks removed — which is exactly the derived file
# outside the injected block. The name lets the D-35 notice say that the base
# agent setting changed; the digest lets `_inspect` tell a derived file edited
# outside its block (the digest still matches the base, the rest of the file
# does not) from a base agent that was updated since (the file is still
# consistent with its own digest). A block written before these lines existed
# has neither, and is simply regenerated without a notice.
_BASE_NAME_LABEL = "Base agent: "
_BASE_NAME_RE = re.compile(r"#\s*Base agent: ([A-Za-z0-9_-]{1,64})\s*$", re.MULTILINE)
_BASE_DIGEST_LABEL = "Base agent digest: "
_BASE_DIGEST_RE = re.compile(r"#\s*Base agent digest: ([0-9a-f]{16})\b")

# Serialises generation and the settings write in front of it.
# 260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL D-16: `apply_settings` holds it
# across `load_config`, the mutation, `save_config` and generation, so
# config.toml is never ahead of the derived agent for a session-creation check
# to see. The **only** other acquirer is `web._derived_agent_in_effect`, with a
# bounded `acquire(timeout=...)`, so a stalled generation refuses a session
# rather than hanging it. The routes' `apply_settings` calls are bounded too
# (`lock_timeout`); only the startup pass waits unbounded. `compile_block`,
# `derived_block_state`, `_apply_locked` and `heal_stale_locked` never acquire
# it: a plain `threading.Lock` is not reentrant, and the two acquirers call
# them while holding it. The gate may hand its hold to a worker thread that
# runs the heal and releases it (a plain `Lock` may be released by any thread),
# so the gate's own wait stays inside its budget (D-30).
_generation_lock = threading.Lock()


@dataclass(frozen=True)
class GenerationStatus:
    """The last generation attempt, as the settings panel needs to report it.

    `attempted` separates "no generation has run in this process" from
    "generation ran and succeeded", which the panel must not conflate: the first
    happens for a few milliseconds at startup, the second is the steady state.

    `mode` is the permission mode the attempt compiled. `note` is a success the
    panel still reports: generation from `MINIMAL_BASE` because the base agent
    was not found (260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL D-28).
    """

    attempted: bool = False
    ok: bool = False
    error: str = ""
    mode: str = ""
    base_agent: str = ""
    note: str = ""
    # The attempt was refused because config.toml did not parse, so `error` is
    # only that; `clear_unreadable_status` drops it once the file reads cleanly
    # and the agent file matches (Phase 1 re-review, finding 4).
    unreadable_config: bool = False
    # `AgentProfileError.kind` of the failure, for the panel's next step (M-8).
    error_kind: str = ""


_status = GenerationStatus()

# A posture change the dashboard did not make: set by the D-30 self-heal or the
# startup pass when the regeneration replaced a block compiled from a different
# mode, rule set or base agent, or a file edited outside its block; cleared when
# the dashboard next chooses the mode or changes the rules (`apply_settings`),
# or when the user acknowledges it (`acknowledge_notice`). `None` when there is
# nothing to report. `{mode, what, previous, reason, detected_at}`, where
# `what` is `"mode"`, `"rules"`, `"base"` or `"file"` (final review, M-3).
# 260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL D-35
_posture_notice: dict | None = None
# The one-time notice that the on/off switch became modes (final review, M-6):
# `{mode, detected_at}`, raised by the startup pass that migrated
# `acp_permissions_enabled`, cleared only by `acknowledge_notice` or a mode
# chosen in the dashboard.
_upgrade_notice: dict | None = None
# `_posture_key` of the settings `apply_settings` last saved (Phase 2 review,
# S1), so a later save — or a heal, final review M-1 — can tell the
# dashboard's own change, not yet generated, from one made outside it.
_saved_fingerprint = ""


def last_generation() -> GenerationStatus:
    """The outcome of the most recent generation attempt in this process."""
    return _status


def posture_notice() -> dict | None:
    """D-35's external posture-change notice, or `None`."""
    return dict(_posture_notice) if _posture_notice else None


def upgrade_notice() -> dict | None:
    """M-6's one-time "permissions are now modes" notice, or `None`."""
    return dict(_upgrade_notice) if _upgrade_notice else None


# ---- The notices, kept across a restart (final review, M-2) ------------------
#
# The two notices and `_saved_fingerprint` live in memory and in a small JSON
# file beside config.toml, so a restart neither loses a notice the user has not
# seen nor turns the dashboard's own unapplied change into one (M-1). Nothing
# in it is secret: a mode, a few words and two short digests. Written
# atomically when a notice or the saved fingerprint changes, removed when there
# is nothing left in it, and read by the startup pass.

_NOTICE_FILE = "permission-notice.json"
_NOTICE_WHATS = frozenset(("mode", "rules", "base", "file"))


def _notice_path() -> Path:
    # At call time, from the module: tests and probes redirect `CONFIG_DIR`.
    from . import config as config_mod
    return config_mod.CONFIG_DIR / _NOTICE_FILE


def _persist_notices() -> None:
    """Write the notices and saved fingerprint, or remove the file. Never raises."""
    path = _notice_path()
    payload = {key: value for key, value in (
        ("posture", _posture_notice), ("upgrade", _upgrade_notice),
        ("saved_fingerprint", _saved_fingerprint)) if value}
    try:
        if not payload:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        log.warning("could not store the permission notices in %s: %s", path, exc)


def _clean_notice(raw: object, keys: tuple[str, ...]) -> dict | None:
    """A stored notice with only its known text fields, or `None`."""
    if not isinstance(raw, dict) or not isinstance(raw.get("mode"), str):
        return None
    out = {key: raw[key][:200] for key in keys if isinstance(raw.get(key), str)}
    if "what" in keys and out.get("what") not in _NOTICE_WHATS:
        out["what"] = "rules"
    return out


def _load_persisted_notices() -> None:
    """Restore what `_persist_notices` stored, without replacing what this
    process already holds. A missing file is the normal case; an unreadable
    one is logged and ignored."""
    global _posture_notice, _upgrade_notice, _saved_fingerprint
    path = _notice_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    except (OSError, ValueError) as exc:
        log.warning("could not read the permission notices in %s: %s", path, exc)
        return
    if not isinstance(raw, dict):
        return
    if _posture_notice is None:
        _posture_notice = _clean_notice(
            raw.get("posture"),
            ("mode", "what", "previous", "reason", "detected_at"))
    if _upgrade_notice is None:
        _upgrade_notice = _clean_notice(raw.get("upgrade"), ("mode", "detected_at"))
    saved = raw.get("saved_fingerprint")
    if not _saved_fingerprint and isinstance(saved, str):
        _saved_fingerprint = saved[:120]


def acknowledge_notice(kind: str) -> bool:
    """Clear the `"posture"` or `"upgrade"` notice; whether `kind` was known.

    The dashboard's Acknowledge button (final review, M-3, M-6). Takes the
    generation lock with a bounded wait, because the heal and the startup pass
    set the same notices while holding it; a wait that runs out clears
    nothing and returns False.
    """
    global _posture_notice, _upgrade_notice
    if kind not in ("posture", "upgrade"):
        return False
    if not _generation_lock.acquire(timeout=5.0):
        return False
    try:
        if kind == "posture":
            _posture_notice = None
        else:
            _upgrade_notice = None
        _persist_notices()
        return True
    finally:
        _generation_lock.release()


# ---- The rule model -----------------------------------------------------------
#
# 260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL D-11. One row per kind of action.
# The measured kiro-cli semantics the compiler is built on, carried over from
# the shipped overlay this replaced (260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL
# plan section 9) and from this plan's Phase 0:
#
#   * A capability no rule names does NOT fall back to ask. It inherits the
#     wider scopes, which on a machine whose `~/.kiro/settings/permissions.yaml`
#     is `all: allow` means it runs silently. Manual therefore names every row,
#     and `normalise_rules` fills a missing row from the seed rather than
#     omitting it (D-26).
#
#   * Within one capability, a blanket `ask` rule silently defeats a narrower
#     `allow` rule for the same resource — most-restrictive-wins, regardless of
#     rule order, with no error and no warning. `exclude` is the only mechanism
#     that expresses "allow X, ask about everything else", so every blanket
#     `ask` or `deny` the compiler emits beside an allow list carries an
#     `exclude` equal to that list (D-13; `deny` + `exclude` measured in P-0.7).
#
#   * Deny beats ask and allow, across rules and across rule sources (P-0.7
#     items 5, 9 and 10; F-5), so the floor needs no `exclude` anywhere.
#
#   * `exclude` is a resource glob, not a capability filter: `all: ask` with an
#     `exclude` would drop the excluded resource out of the ask for every
#     capability at once. Manual never names a meta-capability (`all`,
#     `builtin`, `filesystem`). Yolo's single `all: allow` carries no `exclude`
#     and the floor's denies beat it (P-A1).
#
#   * Not named: `context`, `diagnostics` and `sandbox_network`. The kiro-cli
#     documentation lists them in the rule schema, but none was ever observed
#     on the ACP surface, and an unrecognised capability name risks rejecting
#     the whole block, which fails open (Follow-up 8).
#
#   * Shell patterns match the whole command literally and case-sensitively,
#     and `\` is a literal character (P-0.3, F-3, F-4). A `*` also matches an
#     output redirection (`git status > x.txt` ran silently under
#     `git status*`, P-0.8), while `&&`, `;`, `|`, `||`, `&`, a newline and
#     `$(...)` are split and checked per sub-command. File patterns are
#     case-insensitive and match a symlink's resolved target (P-0.11, F-1).
#
#   * `fs_write` rules govern the file-writing tools only: a shell redirection
#     writes any path unchecked (P-0.8, F-6).

PERMISSION_ROWS = ("fs_read", "fs_write", "shell", "web_fetch", "web_search",
                   "mcp", "subagent", "skill", "power")

ROW_LABELS = {
    "fs_read": "Read files",
    "fs_write": "Write files",
    "shell": "Run commands",
    "web_fetch": "Web fetch",
    "web_search": "Web search",
    "mcp": "MCP tools",
    "subagent": "Sub-agents",
    "skill": "Skills",
    "power": "Powers",
}

ROW_DEFAULTS = ("allow", "ask", "block")

# D-14. Past these the rule set is not something a person edits, and a huge
# block is one more thing kiro-cli could reject — which fails open.
MAX_PATTERN_CHARS = 200
MAX_PATTERNS_PER_LIST = 100

# ---- The Always blocked floor ------------------------------------------------
#
# 260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL D-6, D-36, D-38. Emitted first in
# every mode as kiro `deny` rules, which refuse silently with kiro-cli's own
# denial text. Paths use the `**/` form (a `~/` prefix also works, P-0.2).
#
# Each 8.3 short-name variant replaces one long segment with its prefix glob
# (`.ssh` -> `ssh~*`), because agent-profile rules do not map short names to
# long ones for `fs_read` (P-0.11) or `fs_write` (F-5). NTFS hashed short names,
# used after four prefix collisions, are not covered (R-3). Trailing-dot and
# trailing-space spellings name a different, missing folder, not an alias
# (F-2, F-5), so they need no variant.
_FLOOR_FS_READ = (
    "**/.ssh/**", "**/.aws/**", "**/.azure/**", "**/.config/gcloud/**",
    "**/.kiro/secrets.json", "**/Kiro-Cli/data.sqlite3*",
    "**/power-atlas/local-secret*", "**/power-atlas/remote-secret*",
    # 8.3 short-name variants (D-38a).
    "**/ssh~*/**", "**/aws~*/**", "**/azure~*/**", "**/config~*/gcloud/**",
    "**/kiro~*/secrets.json", "**/.kiro/secret~*", "**/kiro~*/secret~*",
    "**/Kiro-Cli/data~*", "**/power-~*/local-*", "**/power-~*/remote*",
    "**/power-atlas/local-~*", "**/power-atlas/remote~*",
)

# Best-effort: a command can be spelled many ways (D-4, R-2). Shell patterns are
# literal (so `/` and `\` spellings are both listed, D-38b) and case-sensitive
# (so every entry also ships upper-case, D-38f; mixed case stays uncovered,
# R-19). `*poweratlas-acp*` stands in for the fs_write floor on shell
# redirections, which no fs_write rule sees (D-38g). Over-matches harmless
# commands such as `echo notes.sshx` (P-0.3) and `ssh -i ~/.ssh/key` (R-12).
_FLOOR_SHELL_LOWER = (
    "*.ssh*", "*.aws*", "*.azure*", "*.config/gcloud*", "*.config\\gcloud*",
    "*secrets.json*", "*data.sqlite3*", "*local-secret*", "*remote-secret*",
    "*poweratlas-acp*",
    # Short-name stems (D-38a).
    "*ssh~*", "*aws~*", "*azure~*", "*config~*gcloud*", "*secret~*",
    "*local-~*", "*remote~*", "*data~*", "*powera~*",
)
_FLOOR_SHELL = _FLOOR_SHELL_LOWER + tuple(p.upper() for p in _FLOOR_SHELL_LOWER)

# File-writing tools only (D-38g). `.kiro/settings` and `.kiro/workspace-roots`
# repeat kiro-cli's own built-in denies so the floor lists them in full, and
# the short-name variant closes the `KIRO~1\settings` spelling the built-in
# misses (F-5).
_FLOOR_FS_WRITE = (
    "**/.kiro/agents/poweratlas-acp.md", "**/.kiro/settings/**",
    "**/.kiro/workspace-roots/**",
    # 8.3 short-name variants (F-5).
    "**/kiro~*/agents/poweratlas-acp.md", "**/.kiro/agents/powera~*",
    "**/kiro~*/agents/powera~*", "**/kiro~*/settings/**",
    "**/.kiro/worksp~*/**", "**/kiro~*/workspace-roots/**",
    "**/kiro~*/worksp~*/**",
)

FLOOR_RULES = (
    {"capability": "fs_read", "match": _FLOOR_FS_READ, "effect": "deny"},
    {"capability": "shell", "match": _FLOOR_SHELL, "effect": "deny"},
    {"capability": "fs_write", "match": _FLOOR_FS_WRITE, "effect": "deny"},
)

# kiro-cli's own `kiro-scope` rules, which apply in every mode and which no
# agent rule can lift (P-0.10, F-5, D-38c). Listed so the settings panel and
# the Yolo description can say so.
KIRO_BUILTIN_ASKS = (
    ".git", ".vscode", "*.code-workspace", ".kiro/agents", ".kiro/hooks",
)
KIRO_BUILTIN_DENIES = (
    ".kiroignore", ".kiro/settings", "~/.kiro/workspace-roots",
    "~/.kiro/sandbox-state", "~/.kiro/web-session",
    "~/.kiro/powers/installed/*/mcp.json", "~/.kiro/cloud-cache",
)

# ---- Protected ---------------------------------------------------------------
#
# Manual only (D-5): writes to agent, steering, skill and hook configuration
# always prompt, even under a Write files row whose default is Allow, and each
# can be switched to Block outright (`protected_block`). kiro-cli's built-in
# asks already cover `agents` and `hooks` in every mode (P-0.10); Protected
# still owns the Block outright switch. Each item ships its `kiro~*` short-name
# variant (F-5). Governs the file-writing tools only (D-38g) and does not see a
# write through a symlink whose target lies outside (D-38h, F-1);
# `find_protected_links` names those links for the settings panel (D-39).
PROTECTED = {
    "agents": ("Agent definitions", ("**/.kiro/agents/**", "**/kiro~*/agents/**")),
    "steering": ("Steering files", ("**/.kiro/steering/**", "**/kiro~*/steering/**")),
    # "Skill files", not "Skills": the rule editor also has a Skills row (the
    # skill capability), and the two are different things (Phase 2 review, U11).
    "skills": ("Skill files", ("**/.kiro/skills/**", "**/kiro~*/skills/**")),
    "hooks": ("Hooks", ("**/.kiro/hooks/**", "**/kiro~*/hooks/**")),
}

# ---- The Manual seed ------------------------------------------------------------
#
# The shipped overlay this plan replaced, as rows (D-22, D-24). The shell allow
# patterns are exact literals: a `*` suffix also allows an output redirection
# (P-0.8), and an exact pattern matches the whole command, so a redirection or
# an extra argument falls to the ask (F-3). The shell block patterns guard the
# git prefixes a user may add later: `--output`, `--no-index` and `--ext-diff`
# turn a read-only git command into a write, a read outside the repository or
# a program run.
#
# Flagged rather than changed (R-8, carried forward): `git status`, `git log`,
# `git diff` and `git branch` can each execute repository-controlled code,
# because git honours `.gitattributes` `textconv` filters and repo-local
# `diff.external` from the checkout it runs in.
SEED_RULES = {
    "fs_read": {"default": "ask", "allow": ["./**"], "block": []},
    "fs_write": {"default": "ask", "allow": [], "block": []},
    "shell": {
        "default": "ask",
        "allow": ["git status", "git log", "git diff", "git branch",
                  "pwd", "whoami", "uname"],
        "block": ["git *--output*", "git *--no-index*", "git *--ext-diff*"],
    },
    "web_fetch": {"default": "ask", "allow": [], "block": []},
    "web_search": {"default": "ask", "allow": [], "block": []},
    "mcp": {"default": "ask", "allow": [], "block": []},
    "subagent": {"default": "ask", "allow": [], "block": []},
    "skill": {"default": "ask", "allow": [], "block": []},
    "power": {"default": "ask", "allow": [], "block": []},
    "protected_block": [],
}

# The base agent used when the configured one does not exist (D-28). It must
# pass `_check_frontmatter_shape` and carry no bare `: ` in a plain scalar,
# which is the malformation that made kiro-cli fall open in the prior plan's
# Phase 0.
MINIMAL_BASE = (
    "---\n"
    "description: PowerAtlas ACP sessions on a minimal base agent\n"
    "tools:\n"
    '  - "*"\n'
    "---\n"
    "\n"
    "PowerAtlas wrote this agent from a built-in minimal base because the\n"
    "configured base agent was not found.\n"
)


# Characters that match no text of their own: wildcards, separators, and the
# `.`, `:` and space that sit between them in `*.*`, `?:/**` or `* *`. Glob
# syntax too — braces, brackets, `!` and `,` — since `{**}`, `*{,}*` and
# `**/{*}` each match everything under a glob matcher that reads them
# (final review, SEC6). Counting them as non-literal only ever refuses more.
_MATCH_ALL_CHARS = frozenset("*?/\\.: {}[]!,")
# `.`, `..`, `./…` and `../…` name a folder (`./**` is the session folder the
# seed allows), so a pattern that starts with one is not match-all. A `..`
# segment is still refused in a file row's allow list (`fs_allow_error`).
_RELATIVE_ANCHOR_RE = re.compile(r"\.{1,2}(?:[/\\]|$)")

# Phase 3 review (M1): a `..` path segment in a Read files or Write files
# allow pattern. `../**` is every sibling of the session folder, and
# `C:/Users/q/../../**` a whole drive spelled so no broadness check sees it.
# Shell patterns keep `..` (`cd ..` is a command, not a path).
_PARENT_SEGMENT_RE = re.compile(r"(?:^|[/\\])\.\.(?:[/\\]|$)")
_FILE_ROWS = frozenset(("fs_read", "fs_write"))
FS_PARENT_SEGMENT_ERROR = (
    "goes up a folder with “..”; write the folder's full path instead")

# How the rule editor names a character a pattern cannot hold (Phase 2 review,
# U2): in words, since the character is usually invisible where it was typed.
_CHAR_WORDS = {
    "\t": "an invisible tab character",
    "\n": "a line break",
    # The same words as `\n` (final review, EU13): the rule editor's copy in
    # index.html has always said "a line break" for both, and a person
    # cannot tell the two apart where they typed them.
    "\r": "a line break",
    "\x7f": "an invisible delete character",
    " ": "a non-breaking space",
    "​": "an invisible zero-width space",
    " ": "a line separator",
    " ": "a paragraph separator",
    "﻿": "an invisible byte-order mark",
}


def _char_words(ch: str) -> str:
    code = ord(ch)
    if ch in _CHAR_WORDS:
        return _CHAR_WORDS[ch]
    if 0xD800 <= code <= 0xDFFF:
        return "a broken character (half of an emoji or symbol)"
    if code > 0xFFFF:
        return "an emoji or another character PowerAtlas cannot store in a rule"
    if code < 0x20 or 0x7F <= code <= 0x9F:
        return "an invisible control character"
    return "an invisible or unsupported character"


def _matches_everything(pattern: str) -> bool:
    """Whether `pattern` holds no literal character that could narrow it.

    `*`, `**`, `**/*`, `?*`, `* *`, `*.*`, `?:/**` and `**/?*` each match every
    command, host or path (or every path on every drive), which is the row's
    default in disguise (D-14; Phase 1 review, finding 9; Phase 2 review, S4).
    """
    text = pattern.strip()
    return set(text) <= _MATCH_ALL_CHARS and not _RELATIVE_ANCHOR_RE.match(text)


def _pattern_fault(pattern: object, new: bool = False) -> tuple[str, str]:
    """`(technical, words)` reasons `pattern` cannot be used, or `("", "")`.

    The technical reason (`pattern_error`) names a character by code point, for
    logs and the generation error; the words are the rule editor's (U2).
    `new` adds the checks only a pattern being saved now gets (SE4).
    """
    if not isinstance(pattern, str):
        return "is not text", "is not text"
    if not pattern.strip():
        return "is blank", "is blank"
    if len(pattern) > MAX_PATTERN_CHARS:
        why = f"is longer than {MAX_PATTERN_CHARS} characters"
        return why, why
    if _matches_everything(pattern):
        return ("matches everything; set the row's default instead",
                "matches everything; choose the row's default instead")
    if new and pattern != pattern.strip(" "):
        # Final review, SE4: shell patterns match the whole command
        # literally, so "git status " never matches `git status`, and a
        # trailing space in a file pattern names a different path. Only the
        # ASCII space: every other edge whitespace is refused by name below.
        # Only for a pattern being saved (the editor, the API, the prompt
        # card): a stored block pattern with an edge space is harmless, and
        # refusing it on load would stop the rules compiling (D-34 refuses
        # every Default session then).
        why = "starts or ends with a space"
        return why, why + "; remove it"
    for ch in pattern:
        # `isprintable` is False for Cc (C0, DEL, C1), Cs (surrogates), Zl/Zp
        # (U+2028/U+2029), Cf (U+FEFF) and every separator but the ASCII space.
        if ord(ch) > 0xFFFF or not (ch == " " or ch.isprintable()):
            return (f"contains the character U+{ord(ch):04X}, which is not allowed",
                    f"contains {_char_words(ch)}, which is not allowed")
    return "", ""


def quote_pattern(pattern: object, limit: int = 60) -> str:
    """`pattern` in curly quotes for a message, invisible characters escaped.

    Final review, EU13: a refusal quoted the pattern with Python's `repr`,
    which shows `'git status'` with straight quotes and escapes non-ASCII
    text. Here only what cannot be seen is escaped (`\\t`, `\\n`, `\\r`, else
    `\\uXXXX`), so a pattern reads as it was typed.
    """
    text = str(pattern)[:limit]
    out = []
    for ch in text:
        if ch == " " or (ord(ch) <= 0xFFFF and ch.isprintable()):
            out.append(ch)
        else:
            out.append({"\t": "\\t", "\n": "\\n", "\r": "\\r"}.get(
                ch, f"\\u{ord(ch):04X}" if ord(ch) <= 0xFFFF
                else f"\\U{ord(ch):08X}"))
    return "“" + "".join(out) + "”"


def pattern_error(pattern: object, *, new: bool = False) -> str:
    """Why `pattern` cannot be used, or `""` when it can (D-14).

    1-200 characters, not blank, not a pattern that matches everything
    (`_matches_everything`), and printable BMP characters only: no C0 or C1
    controls, DEL, surrogates, U+2028/U+2029 or U+FEFF. Emission uses
    `json.dumps(s, ensure_ascii=False)`, and a surrogate escape or a raw
    control character can make kiro-cli reject the frontmatter, which fails
    open silently. `new=True` for a pattern being saved now: it also refuses
    a leading or trailing space (final review, SE4).
    """
    return _pattern_fault(pattern, new)[0]


def fs_allow_error(row: str, pattern: object) -> str:
    """Why `pattern` cannot be an allow pattern of `row` beyond `pattern_error`.

    Phase 3 review (M1): a Read files or Write files allow pattern with a
    `..` segment. Checked on every way in: the prompt card's route,
    `validate_rules` and loading (`normalise_rules` drops it, D-26).
    """
    if row in _FILE_ROWS and isinstance(pattern, str) \
            and _PARENT_SEGMENT_RE.search(pattern):
        return FS_PARENT_SEGMENT_ERROR
    return ""


def _seed_row(row: str) -> dict:
    return copy.deepcopy(SEED_RULES[row])


def normalise_rules(raw: object) -> dict:
    """`acp_permission_rules` as the complete, well-typed rule set. Never raises.

    260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL D-26, applied by `load_config`
    and again by `compile_block`:

    * a missing or non-table row is the seed row, so every capability is always
      named (an unnamed one inherits the wider scopes);
    * an unreadable default becomes `ask`, keeping the row's lists;
    * an invalid **allow** pattern is dropped, and the list is capped at
      `MAX_PATTERNS_PER_LIST` — both narrow what runs silently;
    * the **block** list is kept exactly as stored, invalid entries and length
      included, so `compile_block` refuses it by name rather than dropping a
      protection silently;
    * unknown rows and unknown Protected names are dropped.

    Every change that is not a plain fill-in is named by
    `normalise_rules_report` and logged once per distinct problem per process.
    """
    return normalise_rules_report(raw)[0]


# Problems `normalise_rules_report` has already logged in this process, so a
# config read on every request does not repeat them (the D-25 pattern).
_rule_problems_logged: set[str] = set()


def normalise_rules_report(raw: object) -> tuple[dict, list[str]]:
    """`normalise_rules(raw)` plus what it changed, in plain words.

    Phase 1 review, finding 7: an unreadable default, a dropped allow pattern
    and an unknown Protected name were silent. Each is now one line in the
    returned list — `load_config` records them on the config for the settings
    panel (`_rules_warning`) — and is logged at WARNING once per process.
    A missing row filled from the seed is not a problem: it is how a config
    without rules reads.
    """
    out, problems, _by_row = _normalise_rules_full(raw)
    return out, problems


def rule_problems_by_row(raw: object) -> dict[str, list[str]]:
    """`normalise_rules_report`'s problems, keyed by the row they are in.

    Phase 2 review, S2: the rule editor must not open a row whose stored block
    list it cannot show — saving from it would replace that list with `[]` and
    drop the protection silently. Rows with no problem are absent.
    """
    return _normalise_rules_full(raw)[2]


def _normalise_rules_full(raw: object) -> tuple[dict, list[str], dict[str, list[str]]]:
    src = raw if isinstance(raw, dict) else {}
    out: dict = {}
    problems: list[str] = []
    by_row: dict[str, list[str]] = {}

    def note(row: str, text: str) -> None:
        problems.append(text)
        by_row.setdefault(row, []).append(text)

    for row in PERMISSION_ROWS:
        raw_row = src.get(row)
        if not isinstance(raw_row, dict):
            if raw_row is not None:
                note(row, f"{row}: the row is not a table, so the default "
                          "rules are used for it")
            out[row] = _seed_row(row)
            continue
        raw_default = raw_row.get("default")
        default = raw_default.strip().lower() if isinstance(raw_default, str) else ""
        if default not in ROW_DEFAULTS:
            note(row, f"{row}: default {str(raw_default)[:40]!r} is not "
                      "allow, ask or block, so it asks")
            default = "ask"
        allow: list = []
        raw_allow = raw_row.get("allow")
        if isinstance(raw_allow, list):
            for pattern in raw_allow:
                if len(allow) >= MAX_PATTERNS_PER_LIST:
                    note(row, f"{row}: only the first "
                              f"{MAX_PATTERNS_PER_LIST} allow patterns "
                              "are used")
                    break
                reason = pattern_error(pattern) or fs_allow_error(row, pattern)
                if reason:
                    note(row, f"{row}: allow pattern {quote_pattern(pattern)} "
                              f"{reason}, so it was ignored")
                elif pattern not in allow:
                    allow.append(pattern)
        elif raw_allow is not None:
            note(row, f"{row}: the allow list is not a list, so it was "
                      "ignored")
        raw_block = raw_row.get("block")
        if raw_block is None:
            block = []
        elif isinstance(raw_block, list):
            block = list(raw_block)
        else:
            # Kept as stored, so `compile_block` refuses it by name; named
            # here too, so the rule editor refuses to open on it (S2).
            block = raw_block
            note(row, f"{row}: the block list is not a list "
                      f"({str(raw_block)[:60]!r}), so the rules are not "
                      "applied until it is fixed in config.toml")
        out[row] = {"default": default, "allow": allow, "block": block}
    raw_protected = src.get("protected_block")
    if isinstance(raw_protected, str):
        raw_protected = [raw_protected]
    if raw_protected is not None and not isinstance(raw_protected, list):
        problems.append("protected_block is not a list, so no Protected item "
                        "is blocked outright")
        raw_protected = []
    for name in raw_protected or []:
        # `isinstance` first: a TOML table in the list is unhashable.
        if not (isinstance(name, str) and name in PROTECTED):
            problems.append(f"protected_block: {str(name)[:40]!r} is not a "
                            "Protected item, so it was ignored")
    out["protected_block"] = [key for key in PROTECTED if key in (raw_protected or [])]
    for problem in problems:
        if problem not in _rule_problems_logged:
            _rule_problems_logged.add(problem)
            log.warning("acp_permission_rules: %s", problem)
    return out, problems, by_row


_ROW_KEYS = frozenset(("default", "allow", "block"))

# The rule editor's names for a row's two lists (Phase 2 review, U2).
LIST_LABELS = {"allow": "Allow without asking", "block": "Always block"}


def _refuse(error: str, row: str | None = None, which: str | None = None,
            pattern: object = None, words: str = "") -> AgentProfileError:
    """A `validate_rules` refusal: `error` for the API, `detail` for the editor.

    `detail.message` names the row and list the way the editor labels them and
    describes a bad character in words (U2); `error` keeps the row key and the
    code point, which is what logs and API callers have always read.
    """
    where = ROW_LABELS.get(row, "") if row else ""
    if where and which:
        where += ", " + LIST_LABELS[which]
    message = (where + ": " if where else "") + (words or error)
    detail = {"row": row, "list": which,
              "pattern": pattern if isinstance(pattern, str) else None,
              "message": message}
    return AgentProfileError(error, detail)


def validate_rules(raw: object) -> dict:
    """A rule set sent by the rule editor, checked strictly. Raises on the first fault.

    260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL Phase 2 (D-11, D-14, D-26).
    `normalise_rules` repairs a stored rule set so that loading never fails;
    this is the other direction — a full replacement the user is saving right
    now — so nothing is repaired, dropped or filled in: every row needs its
    `default`, `allow` and `block`, and the set needs `protected_block`
    (Phase 2 review, S3). An unknown or missing row or key, a default other
    than allow/ask/block, an invalid pattern and a list over
    `MAX_PATTERNS_PER_LIST` each raise `AgentProfileError` naming the row (by
    its label and key) and the pattern, with a `detail` for the editor (U2).
    Returns the rule set in the shape `normalise_rules` produces, with
    duplicate patterns removed.
    """
    if not isinstance(raw, dict):
        raise _refuse("rules must be a table of rows")
    known = set(PERMISSION_ROWS) | {"protected_block"}
    for key in raw:
        if key not in known:
            raise _refuse(f"{str(key)[:40]!r} is not a kind of action")
    out: dict = {}
    for row in PERMISSION_ROWS:
        name = f"{ROW_LABELS[row]} ({row})"
        spec = raw.get(row)
        if not isinstance(spec, dict):
            raise _refuse(f"{name}: the row is missing or not a table", row,
                          words="this row is missing")
        for key in spec:
            if key not in _ROW_KEYS:
                raise _refuse(
                    f"{name}: {str(key)[:40]!r} is not default, allow or block",
                    row, words=f"{str(key)[:40]!r} is not a setting of this row")
        if "default" not in spec:
            raise _refuse(f"{name}: the default is missing", row,
                          words="choose Allow, Ask or Block for when no "
                                "pattern matches")
        default = spec["default"]
        if default not in ROW_DEFAULTS:
            raise _refuse(
                f"{name}: default {str(default)[:40]!r} is not allow, ask or block",
                row, words="choose Allow, Ask or Block for when no pattern "
                           "matches")
        lists: dict = {}
        for which in ("allow", "block"):
            if which not in spec:
                raise _refuse(f"{name}: the {which} list is missing", row,
                              which, words="the list is missing")
            patterns = spec[which]
            if not isinstance(patterns, list):
                raise _refuse(f"{name}: the {which} list is not a list", row,
                              which, words="the list is not a list")
            if len(patterns) > MAX_PATTERNS_PER_LIST:
                raise _refuse(
                    f"{name}: the {which} list has {len(patterns)} patterns; at "
                    f"most {MAX_PATTERNS_PER_LIST} are allowed", row, which,
                    words=f"this list has {len(patterns)} patterns; at most "
                          f"{MAX_PATTERNS_PER_LIST} are allowed")
            kept: list = []
            for pattern in patterns:
                reason, words = _pattern_fault(pattern, new=True)
                if not reason and which == "allow":
                    reason = words = fs_allow_error(row, pattern)
                if reason:
                    shown = quote_pattern(pattern)
                    raise _refuse(
                        f"{name}: {which} pattern {shown} {reason}", row,
                        which, pattern,
                        words=f"{shown} {words}")
                if pattern not in kept:
                    kept.append(pattern)
            lists[which] = kept
        out[row] = {"default": default, **lists}
    if "protected_block" not in raw:
        raise _refuse("protected_block is missing")
    blocked = raw["protected_block"]
    if not isinstance(blocked, list):
        raise _refuse("protected_block is not a list")
    for item in blocked:
        if not (isinstance(item, str) and item in PROTECTED):
            raise _refuse(
                f"protected_block: {str(item)[:40]!r} is not a Protected item")
    out["protected_block"] = [key for key in PROTECTED if key in blocked]
    return out


def _check_block_lists(rules: dict) -> None:
    """Refuse a block list that cannot be compiled as stored (D-26, D-14)."""
    for row in PERMISSION_ROWS:
        block = rules[row]["block"]
        name = f"{ROW_LABELS[row]} ({row})"
        if not isinstance(block, list):
            raise AgentProfileError(
                f"{name}: the block list is not a list, so the rules were not "
                "applied; fix acp_permission_rules in config.toml")
        if len(block) > MAX_PATTERNS_PER_LIST:
            raise AgentProfileError(
                f"{name}: the block list has {len(block)} patterns; at most "
                f"{MAX_PATTERNS_PER_LIST} are allowed")
        for pattern in block:
            reason = pattern_error(pattern)
            if reason:
                raise AgentProfileError(
                    f"{name}: block pattern {quote_pattern(pattern)} {reason}, so "
                    "the rules were not applied")


def settings_fingerprint(mode: str, rules: dict) -> str:
    """A short digest of what a block is compiled from (D-35).

    Yolo compiles no rules, so its fingerprint is the mode alone: editing
    Manual's rows while in Yolo is not a posture change.
    """
    payload: dict = {"mode": mode}
    if mode != "yolo":
        payload["rules"] = rules
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=repr)
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:16]


def _compiled_settings(config) -> tuple[str, dict]:
    """The mode and normalised rules `config` compiles to."""
    mode, _warning = parse_permission_mode(
        getattr(config, "acp_permission_mode", None))
    rules = normalise_rules(getattr(config, "acp_permission_rules", None))
    return mode, rules


def compile_rules(mode: str, rules: dict) -> list[dict]:
    """The kiro-cli rule list for a mode and a normalised rule set.

    The floor first in both modes. Yolo adds one `all: allow`. Manual adds the
    Protected items, then one group per row (D-13):

    * a block list -> `deny` with `match`, first, so it beats everything below;
    * default Allow -> `allow` (the allow list is ignored);
    * default Ask -> `allow match <allow>` + `ask exclude <allow>`;
    * default Block -> `allow match <allow>` + `deny exclude <allow>`;
    * an empty allow list -> the bare effect.
    """
    out = [dict(rule) for rule in FLOOR_RULES]
    if mode == "yolo":
        out.append({"capability": "all", "effect": "allow"})
        return out
    blocked = set(rules["protected_block"])
    for key, (_label, patterns) in PROTECTED.items():
        out.append({"capability": "fs_write", "match": patterns,
                    "effect": "deny" if key in blocked else "ask"})
    for row in PERMISSION_ROWS:
        spec = rules[row]
        allow, block, default = spec["allow"], spec["block"], spec["default"]
        if block:
            out.append({"capability": row, "match": block, "effect": "deny"})
        if default == "allow":
            out.append({"capability": row, "effect": "allow"})
            continue
        effect = "ask" if default == "ask" else "deny"
        if allow:
            out.append({"capability": row, "match": allow, "effect": "allow"})
            out.append({"capability": row, "exclude": allow, "effect": effect})
        else:
            out.append({"capability": row, "effect": effect})
    return out


def _flow(patterns) -> str:
    """A single-line YAML flow sequence of JSON-quoted strings (D-14).

    A JSON string is a valid YAML double-quoted scalar. `ensure_ascii=False`
    keeps non-ASCII as UTF-8 rather than `\\u` escapes (F-7 measured that
    kiro-cli sends and matches plain UTF-8); `pattern_error` has already kept
    out every character whose escape could trip kiro-cli's parser.
    """
    return "[" + ", ".join(json.dumps(p, ensure_ascii=False) for p in patterns) + "]"


def compile_block(config, base_digest: str = "") -> str:
    """The derived agent's `permissions:` block for `config`. Pure.

    260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL Phase 1. Reads
    `acp_permission_mode` and `acp_permission_rules` (normalised again here,
    D-26) and nothing on disk. Raises `AgentProfileError` naming the row and
    pattern when a Manual block list cannot be compiled.

    Deterministic, byte for byte: no timestamp or per-run text, because
    `derived_block_state` compares the file against this output to decide
    whether the settings are in effect (D-15). LF line endings and exactly one
    trailing newline, because the splice re-derives both from the base agent.

    `base_digest` is `_digest` of the base agent's text outside its own
    `permissions:` blocks, which `_generate` reads; it is recorded in the
    header with the base agent's name (final review, H-B, SEC3), and both are
    left out when not known.
    """
    mode, rules = _compiled_settings(config)
    if mode != "yolo":
        _check_block_lists(rules)
    base = _base_name_or_empty(config)
    lines = [
        "permissions:",
        f"  # {_PROVENANCE_MARKER} from its ACP permission mode and rules.",
        "  # The whole file is rewritten at startup and on every settings change,",
        "  # so an edit here is lost. Change the mode or the rules in PowerAtlas's",
        "  # settings; compile_block in src/power_atlas/agent_profile.py writes it.",
        f"  # Permission mode: {mode}",
        f"  # {_FINGERPRINT_LABEL}{settings_fingerprint(mode, rules)}",
    ]
    if base:
        lines.append(f"  # {_BASE_NAME_LABEL}{base}")
    if base_digest:
        lines.append(f"  # {_BASE_DIGEST_LABEL}{base_digest}")
    lines.append("  rules:")
    for rule in compile_rules(mode, rules):
        lines.append(f"    - capability: {rule['capability']}")
        if "match" in rule:
            lines.append(f"      match: {_flow(rule['match'])}")
        if "exclude" in rule:
            lines.append(f"      exclude: {_flow(rule['exclude'])}")
        lines.append(f"      effect: {rule['effect']}")
    return "\n".join(lines) + "\n"


def _base_name_or_empty(config) -> str:
    """The configured base agent's name when it is valid, else `""`."""
    name = getattr(config, "acp_permission_base_agent", None)
    try:
        return validate_base_agent_name(name)
    except AgentProfileError:
        return ""


def _digest(text: str) -> str:
    """A short digest of `text`, for the header's base-agent digest."""
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:16]


def validate_base_agent_name(name: object) -> str:
    """Return `name` when it is a usable base-agent name, else raise.

    Rejection happens **before** any path is built, which is the whole point:
    `../x`, an absolute path and a name with a separator in it never reach
    `Path` at all, so there is no traversal to reason about downstream.
    """
    if not isinstance(name, str):
        raise AgentProfileError(f"invalid base agent name: {name!r}")
    if not _AGENT_NAME_RE.fullmatch(name) or name.upper() in _WIN_RESERVED:
        raise AgentProfileError(f"invalid base agent name: {name!r}")
    return name


def base_agent_path(name: str) -> Path:
    """`~/.kiro/agents/<name>.md` for a validated name."""
    return KIRO_AGENTS_DIR / f"{validate_base_agent_name(name)}.md"


def derived_agent_path() -> Path:
    """`~/.kiro/agents/poweratlas-acp.md`."""
    return KIRO_AGENTS_DIR / f"{DERIVED_AGENT_NAME}.md"


def _stage_path() -> Path:
    """The staging path, deliberately **outside** the agents directory.

    One directory up — `~/.kiro/poweratlas-acp.md.tmp` — which is the same
    volume, so `os.replace` is still atomic, but not inside the directory
    kiro-cli scans. Any file under `~/.kiro/agents/` registers in the mode
    catalogue (P1) and whether that scan is restricted to `.md` was never
    measured, so a `.tmp` left behind by a hard kill between `open` and
    `os.replace` is a question this module does not need to have. It is also
    removed at the start of every pass, including an `off` pass, so a stray does
    not outlive the next thing PowerAtlas does.
    """
    return KIRO_AGENTS_DIR.parent / f"{DERIVED_AGENT_NAME}.md.tmp"


def _norm_block(block: str) -> str:
    """A `permissions:` block reduced to what a comparison should care about.

    Line endings and trailing blank lines are both re-derived by the splice from
    the base agent, so neither is evidence that the block itself differs. Two
    comparisons depend on this: the pre-publish verification, and
    `derived_block_state`'s classification of the file on disk.
    """
    return block.replace("\r\n", "\n").rstrip("\n")


def _dominant_line_ending(lines: list[str]) -> str:
    """`"\\r\\n"` when most of the base's terminated lines end `\\r`, else `"\\n"`.

    The injected block adopts it. YAML accepts either break, so a mixed-ending
    derived agent is byte-hygiene rather than a fail-open — but the file is
    supposed to be the base plus one block, and a base a user keeps in CRLF
    should not come back half-converted.
    """
    terminated = lines[:-1] if len(lines) > 1 else lines
    if not terminated:
        return "\n"
    crlf = sum(1 for line in terminated if line.endswith("\r"))
    return "\r\n" if crlf * 2 > len(terminated) else "\n"


def _check_frontmatter_shape(lines: list[str], close: int) -> None:
    """Refuse a frontmatter region this module's column-0 splice cannot handle.

    Two structural guards, both cheap and neither a parser (D-18):

    * **The root must be a mapping whose keys sit at column 0**, which is
      checked on the region's first content line. An indented root mapping is
      valid YAML that `_PERMISSIONS_KEY_RE` cannot see, so the base's own
      indented `permissions:` block would survive beside the injected one — two
      top-level keys of the same name, merged by whatever kiro-cli does with
      that shape, with the base's rules still live. A root *sequence* is the
      same hazard from the other side: splicing a column-0 mapping key into it
      produces a root document that mixes a mapping with a sequence, which does
      not parse at all. Refusing both is a deliberate scope narrowing to the
      shape the shipped `kiro_default.md` actually has, not a workaround:
      guessing at a second shape is how the fail-open this module exists to
      prevent gets manufactured.

    * **Every column-0 line in the region must look like a mapping key.** This
      is the guard that catches a *mis-identified* closing fence: if the scan
      runs past the real fence and finds a later `---` in the markdown body,
      the "frontmatter" then holds prose, and the block is spliced into the
      document body instead. That failure is silent — generation reports
      success, the derived agent's frontmatter has no `permissions:` key at all,
      and the read-back verification is blind to it because it makes the same
      wrong split on both sides of the comparison, so a wrong split is
      self-consistent. A body line rarely looks like `key: value`; a
      frontmatter line always does.

    Continuation characters are exempt from the second guard for the reasons
    `_BLOCK_CONTINUATION_CHARS` gives: an indented value, a column-0 block
    sequence item, and a comment are all legal and none of them carries a key.
    """
    first = None
    for i in range(1, close):
        stripped = lines[i].rstrip()
        if stripped == "" or stripped.lstrip().startswith("#"):
            continue
        first = stripped
        break
    if first is not None and (first[0] in " \t-" or ":" not in first):
        raise AgentProfileError(
            "base agent frontmatter does not start with a column-0 mapping "
            f"key ({first[:40]!r}); PowerAtlas only splices a frontmatter "
            "whose root mapping starts at column 0")
    for i in range(1, close):
        stripped = lines[i].rstrip()
        if stripped == "" or stripped[0] in _BLOCK_CONTINUATION_CHARS:
            continue
        if ":" not in stripped:
            raise AgentProfileError(
                "base agent frontmatter holds a line that is not a mapping "
                f"key ({stripped[:40]!r}); the closing '---' may have been "
                "mis-identified and the frontmatter may not be frontmatter")


def _frontmatter_bounds(lines: list[str]) -> int:
    """Index of the closing `---` fence, or raise.

    The rule applied here — the opening fence is the first line of the file and
    the closing fence is the first `---` line after it, with trailing whitespace
    ignored — is an **assumption** about kiro-cli's loader, not a measurement.
    Phase 0 established that an inline `permissions:` block loads; it never
    established the precise fence-recognition rule, and Phase 7's live probe is
    what settles it. Nothing cleverer is attempted in the meantime, because
    anything cleverer disagrees with the loader this file is written for.

    Trailing whitespace is ignored (`rstrip()`, never `strip()`) because a base
    agent whose closing fence is `'--- '` would otherwise send the scan past it
    to the next `---` in the markdown body. Leading whitespace is *not* ignored:
    an indented `---` is not a document boundary in YAML, and treating one
    inside a block scalar as a fence would be a worse bug than the one being
    fixed. `_check_frontmatter_shape` is the structural backstop for whatever
    the real rule turns out to be.
    """
    if not lines:
        raise AgentProfileError("base agent is empty")
    # A UTF-8 BOM is tolerated on the first line and nowhere else. It is not
    # stripped from the output: every byte outside the injected block survives.
    if lines[0].lstrip("\ufeff").rstrip() != "---":
        raise AgentProfileError(
            "base agent has no YAML frontmatter (first line is not '---')")
    for i in range(1, len(lines)):
        if lines[i].rstrip() == "---":
            _check_frontmatter_shape(lines, i)
            return i
    raise AgentProfileError(
        "base agent frontmatter is unterminated (no closing '---')")


def _permissions_regions(lines: list[str], close: int) -> list[tuple[int, int]]:
    """Half-open `[start, end)` line spans of every top-level `permissions:`.

    A block runs from its key line through every following blank line and every
    line beginning with `_BLOCK_CONTINUATION_CHARS`, stopping at the first line
    that starts a new top-level key. That covers `permissions: {}` inline, an
    indented mapping, a column-0 block sequence, interleaved comments, and blank
    lines inside any of them.

    Trailing blank lines and trailing column-0 comments are handed back to the
    surrounding text rather than swallowed: a blank line that separated the
    block from the next key, and a comment that introduces the next key, both
    survive the round trip byte-for-byte. Handing them back is also the
    conservative direction — it preserves more of the base.

    Every match is returned, not just the first. A base agent carrying two
    top-level `permissions:` keys is malformed YAML that kiro-cli loads anyway,
    and leaving one of them behind beside the injected block is precisely the
    silent fail-open this module exists to prevent.
    """
    regions: list[tuple[int, int]] = []
    i = 1
    while i < close:
        if not _PERMISSIONS_KEY_RE.match(lines[i].rstrip("\r")):
            i += 1
            continue
        # Ordering invariant: the continuation scan starts at `i + 1`, i.e. only
        # *after* a `permissions:` key line has matched. That is what keeps
        # `-` and `#` from over-reaching. A column-0 block sequence belonging to
        # an earlier key (`tools:` followed by `- "*"`) is never examined by
        # this loop as a continuation, because the outer `while` only advances
        # one line at a time until the key regex matches, and the inner scan
        # never looks backwards. Treating `-` as a continuation unconditionally
        # would absorb the previous key's own items into the block.
        j = i + 1
        while j < close:
            stripped = lines[j].rstrip("\r")
            if stripped == "" or stripped[0] in _BLOCK_CONTINUATION_CHARS:
                j += 1
                continue
            break
        end = j
        while end - 1 > i:
            tail = lines[end - 1].rstrip("\r")
            if tail == "" or tail.startswith("#"):
                end -= 1
                continue
            break
        regions.append((i, end))
        i = j
    return regions


def excise_permissions(text: str) -> tuple[str, str]:
    """Split `text` into (everything but its `permissions:` blocks, first block).

    The inverse of `inject_permissions`, and the exact operation the
    byte-identity check needs: two files agree outside the injected block if and
    only if their excised forms are equal. Returns `""` for the block when the
    frontmatter declares none.
    """
    kept, first, _count = _excise_full(text)
    return kept, first


def _excise_full(text: str) -> tuple[str, str, int]:
    """`excise_permissions` plus how many top-level `permissions:` blocks
    there were. The derived agent must have exactly one (final review, H-B):
    a second one beside PowerAtlas's is read by kiro-cli too."""
    lines = text.split("\n")
    close = _frontmatter_bounds(lines)
    regions = _permissions_regions(lines, close)
    if not regions:
        return text, "", 0
    kept: list[str] = []
    cursor = 0
    for start, end in regions:
        kept.extend(lines[cursor:start])
        cursor = end
    kept.extend(lines[cursor:])
    first = "\n".join(lines[regions[0][0]:regions[0][1]]) + "\n"
    return "\n".join(kept), first, len(regions)


def inject_permissions(base_text: str, block: str) -> str:
    """`base_text` with `block` as its only top-level `permissions:` key.

    Replaces an existing block in place, else inserts immediately before the
    closing fence. Purely textual: nothing outside the block is inspected, let
    alone rewritten. The block's line endings are re-derived from the base, so a
    CRLF base agent does not come back with an LF island in it.
    """
    lines = base_text.split("\n")
    close = _frontmatter_bounds(lines)
    block_lines = block.replace("\r\n", "\n").rstrip("\n").split("\n")
    if _dominant_line_ending(lines) == "\r\n":
        block_lines = [f"{line}\r" for line in block_lines]
    regions = _permissions_regions(lines, close)
    if not regions:
        return "\n".join(lines[:close] + block_lines + lines[close:])
    out: list[str] = []
    cursor = 0
    for index, (start, end) in enumerate(regions):
        out.extend(lines[cursor:start])
        if index == 0:
            out.extend(block_lines)
        cursor = end
    out.extend(lines[cursor:])
    return "\n".join(out)


def _read_text(path: Path) -> str:
    """Read `path` as UTF-8 with `\\n` preserved exactly as stored.

    Binary read and an explicit decode, never text mode: Python's universal
    newlines would turn a `\\r\\n` base agent into `\\n` on the way in, and text
    mode on Windows turns it back into `\\r\\n` on the way out — for a file whose
    byte-identity is the contract, a round trip through text mode is a rewrite.
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise AgentProfileError(f"cannot read {path}: {exc}") from exc
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AgentProfileError(f"{path} is not valid UTF-8: {exc}") from exc


def _clear_stage() -> None:
    """Remove the staging file if one survived a hard kill.

    Only ever this module's own single staging path — never a glob over a
    directory, which could catch a file that is not PowerAtlas's.
    """
    stage = _stage_path()
    try:
        stage.unlink(missing_ok=True)
    except OSError:
        log.warning("could not remove the stale staging file %s", stage)


def _publish(path: Path, text: str, verify: Callable[[str], None]) -> None:
    """Stage -> `fsync` -> **verify the staged file** -> `os.replace`.

    `verify` receives the staged file read back off disk, before publication. If
    it raises, the staging file is removed and `path` is never touched — because
    `os.replace` has not run yet, which is the only ordering under which
    "a failed regeneration keeps the last-good file" is true. Verifying after
    publication detects a bad splice with the previous file already gone.
    """
    stage = _stage_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        stage.parent.mkdir(parents=True, exist_ok=True)
        with open(stage, "wb") as fh:
            fh.write(text.encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
        verify(_read_text(stage))
        os.replace(stage, path)
    except BaseException:
        # `missing_ok` because the failure may have been the `mkdir` or the
        # `open` itself, and an unlink raising here would mask the real cause.
        try:
            stage.unlink(missing_ok=True)
        except OSError:
            log.warning("could not remove %s after a failed write", stage)
        raise


def _block_on_disk() -> str | None:
    """The derived agent's first `permissions:` block; `""` when it has none.

    `None` when there is no file, or it cannot be read or split — the cases
    `derived_block_state` reads as `"absent"`, `"unreadable"` or `"unknown"`.
    """
    path = derived_agent_path()
    if not path.exists():
        return None
    try:
        return excise_permissions(_read_text(path))[1]
    except AgentProfileError:
        return None


def _header_value(regex: re.Pattern, block: str) -> str:
    found = regex.search(block or "")
    return found.group(1) if found else ""


def _base_text(config) -> tuple[str, str]:
    """The base agent's text and D-28's note (`""` unless it was not found).

    Raises `AgentProfileError` for an invalid name, a name that is the
    derived agent itself, or a base agent that exists but cannot be read.
    """
    name = validate_base_agent_name(getattr(config, "acp_permission_base_agent", None))
    source = base_agent_path(name)
    if source == derived_agent_path():
        # Only reachable if the base-agent setting names the derived agent
        # itself, which would make generation read its own output and compound
        # the block on every restart.
        raise AgentProfileError(
            f"base agent {name!r} is the derived agent; pick a different base")
    if source.exists():
        return _read_text(source), ""
    # D-28: a clean kiro-cli install has no `kiro_default.md` unless
    # agent-playbook deployed it. Generating from a minimal agent keeps the
    # floor there; refusing would leave Default sessions refused (D-34).
    return MINIMAL_BASE, f"base agent {name!r} not found — using a minimal agent"


def _inspect(config) -> dict:
    """The derived agent on disk against `config`, in full. Never raises.

    260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL D-15, final review H-B and
    RE4. `state` is `block_state_detail`'s; `error` is the compile error for
    `"unknown"` and the read error for `"unreadable"`. `fingerprint`, `mode`
    and `base` are what the block on disk records (empty when it does not),
    for D-35's notice. `tampered` is True for a PowerAtlas file that was
    edited outside `_generate`: its content does not match what its own
    header says it was built from (a second `permissions:` block, a key
    added beside the block, or a hand edit of the block with the settings
    unchanged). A base agent updated since the file was written is not a
    tamper: the file still matches the digest it records.

    One read of the derived agent and one of the base agent.
    """
    info = {"state": "unknown", "error": "", "fingerprint": "", "mode": "",
            "base": "", "tampered": False}
    path = derived_agent_path()
    if not path.exists():
        info["state"] = "absent"
        return info
    try:
        raw = path.read_bytes()
    except OSError as exc:
        # RE4: a sharing violation or a permission problem, not a file that
        # is someone else's. Generation still will not overwrite it
        # (`_target_is_ours`), but the fix is to try again.
        info.update(state="unreadable", error=f"cannot read {path}: {exc}")
        return info
    try:
        kept, first, count = _excise_full(raw.decode("utf-8"))
    except (UnicodeDecodeError, AgentProfileError):
        return info
    if not first:
        return info
    info["fingerprint"] = _header_value(_FINGERPRINT_RE, first)
    info["mode"] = _header_value(_MODE_LINE_RE, first).lower()
    info["base"] = _header_value(_BASE_NAME_RE, first)
    try:
        base_kept = excise_permissions(_base_text(config)[0])[0]
    except AgentProfileError:
        # Cannot be compared; a PowerAtlas file then reads as stale, and the
        # regeneration that follows reports why the base agent is unusable.
        base_kept = None
    try:
        expected = compile_block(
            config, base_digest=_digest(base_kept) if base_kept is not None else "")
    except AgentProfileError as exc:
        info["error"] = str(exc)
        return info
    except Exception as exc:  # noqa: BLE001 - never raises, by contract
        info["error"] = f"{type(exc).__name__}: {exc}"
        return info
    # H-B: the whole file, not the block alone. Exactly one `permissions:`
    # block, the compiled one, and every other byte the base agent's — the
    # same comparison `_generate`'s pre-publish verification makes.
    if (base_kept is not None and count == 1 and kept == base_kept
            and _norm_block(first) == _norm_block(expected)):
        info["state"] = "on"
        return info
    if _PROVENANCE_MARKER not in first:
        return info
    info["state"] = "stale"
    recorded = _header_value(_BASE_DIGEST_RE, first)
    if recorded:
        consistent = count == 1 and _digest(kept) == recorded
        unchanged_inputs = (
            base_kept is not None and recorded == _digest(base_kept)
            and info["fingerprint"] == settings_fingerprint(*_compiled_settings(config))
            and info["base"] == _base_name_or_empty(config))
        info["tampered"] = not consistent or unchanged_inputs
    return info


def block_state_detail(config) -> tuple[str, str]:
    """`(state, compile_error)` for the derived agent against `config`.

    Never raises (260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL D-15). States:

    * `"absent"` — no derived agent. Since the file is always written, this is
      a failure: the first generation has not run or did not succeed. The
      session gate regenerates it (final review, H-A).
    * `"on"` — the whole file is exactly what generation would write now: one
      `permissions:` block, the one `compile_block(config)` produces (modulo
      the line endings and trailing blank lines the splice re-derives from the
      base), and every other byte the base agent's (final review, H-B). The
      only state `in_effect` accepts.
    * `"stale"` — a file carrying PowerAtlas's marker that is not that: the
      settings changed outside `apply_settings`, the base agent changed, the
      file was edited (inside its block or beside it), or an older release
      wrote it. Safe to regenerate (D-30).
    * `"unreadable"` — the file exists but could not be read just now (RE4).
    * `"unknown"` — anything else: a file PowerAtlas did not write, a file with
      no `permissions:` key, one that cannot be decoded or split, or a rule set
      that does not compile, in which case `compile_error` says why.
    """
    info = _inspect(config)
    return info["state"], info["error"] if info["state"] == "unknown" else ""


def derived_block_state(config) -> str:
    """Classify the derived agent on disk against `config`; see
    `block_state_detail`. Never raises, never takes `_generation_lock`."""
    return block_state_detail(config)[0]


def _target_is_ours() -> bool:
    """Whether generation may write the derived agent's path (D-29).

    True for an absent file and for one whose block carries the provenance
    marker. Anything else — no block, a block without the marker, a file that
    cannot be read or split — is someone else's, and is never overwritten.
    """
    if not derived_agent_path().exists():
        return True
    block = _block_on_disk()
    return bool(block) and _PROVENANCE_MARKER in block


# The fix every "save it again" message names (final review, H-A): the
# settings panel's own control, which re-applies the chosen mode even when it
# is unchanged. A radio that is already checked sends nothing when clicked.
APPLY_AGAIN_STEP = "press Apply again under Settings > Agent permissions"


def _generate(status: GenerationStatus, config) -> GenerationStatus:
    """Write the derived agent for `config`, or raise leaving the previous file alone."""
    target = derived_agent_path()
    try:
        name = validate_base_agent_name(
            getattr(config, "acp_permission_base_agent", None))
        if base_agent_path(name) == target:
            _base_text(config)  # raises, naming the problem
    except AgentProfileError as exc:
        exc.kind = exc.kind or "base"
        raise
    status = replace(status, base_agent=name)
    if not _target_is_ours():
        raise AgentProfileError(
            f"{target} was not written by PowerAtlas, so it was left in place; "
            f"remove or rename it, then {APPLY_AGAIN_STEP}", kind="foreign")
    # A rule set that does not compile is reported before the base agent is
    # read, so its error is the one named when both are wrong.
    mode, rules = _compiled_settings(config)
    try:
        if mode != "yolo":
            _check_block_lists(rules)
    except AgentProfileError as exc:
        exc.kind = exc.kind or "rules"
        raise
    try:
        base_text, note = _base_text(config)
        base_kept = excise_permissions(base_text)[0]
        block = compile_block(config, base_digest=_digest(base_kept))
        derived_text = inject_permissions(base_text, block)
    except AgentProfileError as exc:
        # Unreadable, not UTF-8, or a frontmatter the splice refuses.
        exc.kind = exc.kind or "base"
        raise

    def verify(written: str) -> None:
        """A structural confirmation of this module's own splice.

        The frontmatter still has its fences, the block is the compiled one,
        and every other byte still matches the base. It is deliberately **not**
        a claim that kiro-cli bound the rules: only a live session can show
        that, and `kiro-cli agent validate --path` was measured in
        260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 0 to report
        every `.md`-format agent as invalid, including the machine's own
        working one (R-17).
        """
        kept, block_back = excise_permissions(written)
        if _norm_block(block_back) != _norm_block(block) or kept != base_kept:
            raise AgentProfileError(
                f"the staged {target.name} does not match what was spliced; "
                "the permission block may not be in effect", kind="write")

    try:
        _publish(target, derived_text, verify)
    except OSError as exc:
        raise AgentProfileError(f"cannot write {target}: {exc}",
                                kind="write") from exc
    if note and note not in _notes_logged:
        # Once per process (Phase 1 review, finding 8): every regeneration
        # repeats the note, and the settings panel shows it for as long as it
        # holds.
        _notes_logged.add(note)
        log.warning("derived agent: %s", note)
    return replace(status, ok=True, error="", note=note)


# D-28 notes `_generate` has already logged in this process.
_notes_logged: set[str] = set()


def _apply_locked(config) -> GenerationStatus:
    """Write the derived agent for `config`. The lock must be held; this never
    takes it (D-16). Records the outcome for the panel, and re-raises."""
    global _status
    mode, _rules = _compiled_settings(config)
    base = getattr(config, "acp_permission_base_agent", "")
    status = GenerationStatus(attempted=True, ok=False, mode=mode,
                              base_agent=base if isinstance(base, str) else "")
    try:
        _clear_stage()
        status = _generate(status, config)
    except AgentProfileError as exc:
        _status = replace(status, ok=False, error=str(exc), error_kind=exc.kind)
        # A failure this module predicted and named (a file that is not
        # PowerAtlas's, a rule that does not compile): the panel reports it,
        # and a traceback adds nothing (final review, RE6).
        log.warning("derived agent update failed: %s", exc)
        raise
    except Exception as exc:  # noqa: BLE001 - see below
        # An unexpected bug is still a failure the panel has to report.
        # Recording it here rather than only at the call site is what stops a
        # stale `ok=True` from an earlier successful run outliving a crash in a
        # later one.
        _status = replace(status, ok=False,
                          error=f"{type(exc).__name__}: {exc}")
        log.exception("derived agent update raised unexpectedly")
        raise
    _status = status
    return _status


def config_load_error_message(config) -> str:
    """The plain-words refusal for a config.toml that could not be used, or `""`.

    Phase 1 review, finding 1. `load_config` answers an unreadable file with
    the defaults, and the default mode is Yolo, the least restrictive one: a
    generation or a save from those defaults would widen the posture the user
    set. Paths are read from `config` at call time, because tests and
    probes redirect `CONFIG_PATH`. The fix depends on whether the file did not
    parse or could not be opened (final review, RE5).
    """
    error = config._load_error
    if not error:
        return ""
    from . import config as config_mod
    return (f"PowerAtlas's config.toml could not be read ({error}), so no "
            "setting was changed and the permission settings were not applied. "
            + config_mod.unreadable_fix(config, "reload this page or restart "
                                                "PowerAtlas"))


def clear_unreadable_status(config) -> None:
    """Drop a status that only says config.toml could not be read.

    Phase 1 re-review, finding 4. Called once config.toml has loaded cleanly
    and the file on disk is exactly what it compiles to: by the gate with the
    lock held, and by the settings panel's read (final review, RE10), which
    does not take it — the swap below is one assignment, and the worst a race
    with a generation can do is let this status replace a newer one that says
    the same thing. The status then reports the settings in effect. A D-28
    note is not known here; the next regeneration restores it.
    """
    global _status
    if not _status.unreadable_config or config._load_error:
        return
    base = getattr(config, "acp_permission_base_agent", "")
    _status = GenerationStatus(
        attempted=True, ok=True, error="",
        # The mode as loaded, not as stored (final review, SE9): a stored
        # "AUTO" is Manual.
        mode=parse_permission_mode(getattr(config, "acp_permission_mode", None))[0],
        base_agent=base if isinstance(base, str) else "")


def _posture_key(fingerprint: str, base: str) -> str:
    """What `_saved_fingerprint` holds: the settings and the base agent's name."""
    return f"{fingerprint}|{base}"


def _config_posture(config) -> tuple[str, str, str]:
    """`(fingerprint, mode, base)` for `config`."""
    mode, rules = _compiled_settings(config)
    return settings_fingerprint(mode, rules), mode, _base_name_or_empty(config)


def _disk_posture() -> dict:
    """`{fingerprint, mode, base}` as the block on disk records them."""
    block = _block_on_disk() or ""
    return {"fingerprint": _header_value(_FINGERPRINT_RE, block),
            "mode": _header_value(_MODE_LINE_RE, block).lower(),
            "base": _header_value(_BASE_NAME_RE, block)}


def _settings_moved(disk: dict, fingerprint: str, base: str) -> bool:
    """Whether the file on disk was compiled from other settings than these.

    A file with no fingerprint (none yet, or one written before fingerprints
    existed) gives False: there is no earlier posture to compare with. The
    base agent's name is compared only when the file records one (SEC3), so a
    file from before that line existed does not read as a change.
    """
    if not disk["fingerprint"]:
        return False
    return (disk["fingerprint"] != fingerprint
            or bool(disk["base"]) and disk["base"] != base)


def _changed_what(disk: dict, mode: str, base: str) -> str:
    """What moved between the file on disk and these settings (M-3)."""
    if disk["mode"] and disk["mode"] != mode:
        return "mode"
    if disk["base"] and disk["base"] != base:
        return "base"
    return "rules"


def _record_notice(mode: str, what: str, why: str, previous: str = "") -> None:
    global _posture_notice
    _posture_notice = {"mode": mode, "what": what, "previous": previous,
                       "reason": why,
                       "detected_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    _persist_notices()
    if what == "file":
        log.warning("the derived agent file was changed outside PowerAtlas (%s); "
                    "it was rewritten from the settings for mode %s", why, mode)
    else:
        log.warning("ACP permission settings changed outside the dashboard "
                    "(%s: the %s changed); the derived agent was regenerated "
                    "for mode %s", why, what, mode)


def _notice_after_regeneration(disk: dict, config, why: str,
                               tampered: bool) -> None:
    """D-35: raise the notice for a regeneration that replaced `disk`.

    `disk` is what the replaced block recorded. A change of settings names
    what moved; a file edited outside `_generate` with the settings unchanged
    names the file (final review, H-B). Nothing is raised when the settings
    compiled now are the ones the dashboard itself last saved (final review,
    M-1): that is the dashboard's own change, whose generation failed then
    and succeeded now.
    """
    fingerprint, mode, base = _config_posture(config)
    if _settings_moved(disk, fingerprint, base):
        if _posture_key(fingerprint, base) == _saved_fingerprint:
            return
        _record_notice(mode, _changed_what(disk, mode, base), why,
                       previous=disk["mode"])
    elif tampered:
        _record_notice(mode, "file", why)


NOTICE_CHOICES = ("choose", "adopt", "preserve")


def apply_settings(mutate: Callable[[object], None] | None = None, *,
                   lock_timeout: float | None = None,
                   notice: str = "adopt") -> dict:
    """Save a permission-settings change and regenerate, as one locked step.

    260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL D-16, D-32. Holds
    `_generation_lock` across `load_config`, `mutate(config)`, `save_config` and
    generation, so the session gate can never see a config.toml that is ahead
    of the derived agent. `mutate=None` saves nothing and only regenerates from
    the current settings (startup).

    `lock_timeout` bounds the wait for the lock (the routes pass one, Phase 1
    review finding 5); `None` waits as long as it takes (startup, which is
    itself bounded by `lifespan`). A wait that runs out saves nothing.

    A config.toml that did not parse is never generated from or saved over
    (finding 1): its in-memory reading is the defaults, Yolo included.

    D-35's notice, by `notice` (final review, A6):

    * `"choose"` — the mode route, which is also how the dashboard
      acknowledges a change: clears the notice (and M-6's upgrade notice).
    * `"adopt"` (the default) — a rules-only save or a base-agent rename:
      clears the notice only when it moved what the mode compiles to, so a
      rename keeps it (finding 3b).
    * `"preserve"` — the prompt card's "always allow" (Phase 3 review, M3):
      never clears it. One click on a card, where the notice is not shown,
      must not erase the only record of a change made outside the dashboard.

    A mutation that does not choose the posture raises the notice when the
    settings it started from had changed outside the dashboard and not yet
    been healed: it would otherwise adopt that change silently (Phase 2
    review, S1). "Outside" means different from the file on disk and from
    what the dashboard last saved, so a save after a failed generation of the
    dashboard's own change raises nothing. The startup pass (`mutate=None`)
    restores the notices kept across a restart (M-2), raises the notice when
    the file on disk had been compiled from other settings or edited outside
    PowerAtlas (finding 3a, H-B), and raises M-6's upgrade notice when
    config.toml still held the retired on/off switch, saving the migrated
    mode so it is raised once.

    Returns `{saved, generation_ok, generation_error}` (plus `error` when
    nothing was saved) and never raises: `saved` False means nothing changed
    (the route answers `ok: false`); `saved` True with `generation_ok` False
    means the setting is stored but not yet in effect (the route answers
    `ok: true` and a warning). The settings state the route returns is
    computed after this releases the lock.
    """
    global _posture_notice, _upgrade_notice, _status, _saved_fingerprint
    if notice not in NOTICE_CHOICES:
        raise ValueError(f"notice must be one of {NOTICE_CHOICES}")
    if lock_timeout is None:
        _generation_lock.acquire()
    elif not _generation_lock.acquire(timeout=lock_timeout):
        message = ("The permission settings are being applied; try again in a "
                   "moment")
        return {"saved": False, "generation_ok": False,
                "generation_error": "", "error": message + "."}
    try:
        if mutate is None:
            _load_persisted_notices()
        config = load_config()
        load_error = config_load_error_message(config)
        if load_error:
            base = getattr(config, "acp_permission_base_agent", "")
            _status = GenerationStatus(
                attempted=True, ok=False, error=load_error, mode="",
                base_agent=base if isinstance(base, str) else "",
                unreadable_config=True)
            log.error("derived agent not updated: %s", load_error)
            return {"saved": False, "generation_ok": False,
                    "generation_error": load_error, "error": load_error + "."}
        saved = False
        disk = _disk_posture()
        tampered = False
        if mutate is not None:
            before, before_mode, before_base = _config_posture(config)
            # Phase 2 review, S1: the settings this save starts from are not
            # what the file on disk was compiled from, and not what the
            # dashboard itself last saved, so they changed outside it (a hand
            # edit of config.toml, not yet healed). A save that does not
            # choose the posture adopts that change, so it must say so.
            outside = (_settings_moved(disk, before, before_base)
                       and _posture_key(before, before_base) != _saved_fingerprint)
            try:
                mutate(config)
                save_config(config)
            except AgentProfileError as exc:
                # A refusal the mutation raised on purpose (the prompt card's
                # route re-checks inside the lock): expected, so no traceback
                # (final review, RE6).
                log.warning("permission settings were not saved: %s", exc)
                return {"saved": False, "generation_ok": False,
                        "generation_error": "",
                        "error": f"The setting was not saved: {exc}"}
            except Exception as exc:  # noqa: BLE001 - reported, never raised
                log.exception("permission settings were not saved")
                return {"saved": False, "generation_ok": False,
                        "generation_error": "",
                        "error": f"The setting was not saved: {exc}"}
            saved = True
            after, after_mode, after_base = _config_posture(config)
            _saved_fingerprint = _posture_key(after, after_base)
            # A posture chosen here is the dashboard's own; the notice was
            # about one that was not.
            if notice == "choose":
                _posture_notice = None
                _upgrade_notice = None
            elif notice == "adopt" and before != after:
                _posture_notice = None
            if outside and notice != "choose":
                _record_notice(after_mode,
                               _changed_what(disk, before_mode, before_base),
                               "found by a settings save that did not choose "
                               "the mode", previous=disk["mode"])
            _persist_notices()
        else:
            tampered = _inspect(config)["tampered"]
            if config._migrated_from_switch:
                _raise_upgrade_notice(config)
        try:
            _apply_locked(config)
        except Exception:  # noqa: BLE001 - logged and recorded by _apply_locked
            return {"saved": saved, "generation_ok": False,
                    "generation_error": _status.error}
        if mutate is None:
            _notice_after_regeneration(disk, config, "found at startup", tampered)
        return {"saved": saved, "generation_ok": True, "generation_error": ""}
    finally:
        _generation_lock.release()


def _raise_upgrade_notice(config) -> None:
    """M-6: say once that the on/off switch became a mode, and store the mode.

    Called by the startup pass, with the lock held, when `load_config`
    migrated `acp_permissions_enabled`. The save drops the retired key
    (`config._LEGACY_KEYS`), which is what makes the notice one-time; a save
    that fails leaves the key, and the next start raises the same notice
    again rather than a second one.
    """
    global _upgrade_notice
    mode = parse_permission_mode(config.acp_permission_mode)[0]
    if _upgrade_notice is None:
        _upgrade_notice = {"mode": mode,
                           "detected_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        _persist_notices()
        log.warning("the ACP permission on/off switch is now a permission "
                    "mode: this installation is on %s", mode)
    try:
        save_config(config)
    except Exception as exc:  # noqa: BLE001 - the notice stands either way
        log.warning("the migrated permission mode was not saved: %s", exc)


def sync_from_config() -> dict:
    """Regenerate from the current settings; the startup entry point.

    `apply_settings` with no mutation, so the settings are read inside the
    lock and it stays one of D-16's two acquirers.
    """
    return apply_settings(None)


def heal_stale_locked(config) -> bool:
    """Regenerate a `"stale"` or `"absent"` derived agent once (D-30, H-A).
    The lock must be held.

    Called by the session gate, which holds `_generation_lock` and has already
    checked that `config` loaded cleanly. Heals a hand edit of the file, a
    config.toml edited while PowerAtlas runs, a settings route that saved
    over a just-applied change (R-13), and a file that was never written or
    was deleted (final review, H-A), without a restart.

    Raises D-35's notice when the block it replaced was compiled from other
    settings, naming what moved, or when the file had been edited outside
    PowerAtlas with the settings unchanged (final review, H-B) — unless the
    settings are the ones the dashboard last saved (M-1). Returns whether the
    regeneration succeeded.

    Refuses a config whose config.toml did not parse (Phase 1 re-review,
    finding 5): it is the defaults, and healing from them would write the
    least restrictive mode. The gate already checks; this is so a future
    caller cannot skip that check.
    """
    if config._load_error:
        log.warning("derived agent not healed: config.toml could not be read")
        return False
    info = _inspect(config)
    disk = {key: info[key] for key in ("fingerprint", "mode", "base")}
    try:
        _apply_locked(config)
    except Exception:  # noqa: BLE001 - recorded by _apply_locked
        return False
    _notice_after_regeneration(disk, config, "found by the session check",
                               info["tampered"])
    return True


# ---- What the settings panel shows ---------------------------------------------


def floor_display() -> list[dict]:
    """The Always blocked floor as the settings panel lists it (SC-3).

    Credential-store wording carries no guarantee (D-38a), the shell tier is
    labelled best-effort (D-4), and kiro-cli's own built-ins are listed so the
    view is complete (D-38c).
    """
    return [
        {"id": "fs_read",
         "label": "Reading credential stores",
         "detail": ("SSH, AWS, Azure and gcloud folders, kiro-cli's token files, "
                    "and PowerAtlas's sign-in secrets"),
         "patterns": list(_FLOOR_FS_READ),
         "note": ("Covers the file-reading and search tools, including common 8.3 "
                  "short-name spellings. A link to one of these folders, or an "
                  "NTFS hashed short name, is not covered.")},
        {"id": "shell",
         "label": "Commands that mention those stores",
         "detail": "",
         "patterns": list(_FLOOR_SHELL_LOWER),
         "note": ("Catches common accidents, not a guarantee: a command can be "
                  "spelled many ways. Matching is case-sensitive; lower- and "
                  "upper-case spellings are both blocked. Also blocks harmless "
                  "commands that mention these names, such as "
                  "`echo notes.sshx` or `ssh -i ~/.ssh/key`.")},
        {"id": "fs_write",
         "label": "Writing PowerAtlas's own agent and kiro-cli's settings",
         "detail": "",
         "patterns": list(_FLOOR_FS_WRITE),
         "note": ("File-writing tools only; a shell redirection such as "
                  "`> file` is not checked.")},
        {"id": "kiro",
         "label": "kiro-cli's own rules, in every mode",
         "detail": "",
         "patterns": [],
         "note": ("kiro-cli always asks before writes to "
                  + ", ".join(KIRO_BUILTIN_ASKS)
                  + ", and always blocks writes to "
                  + ", ".join(KIRO_BUILTIN_DENIES) + ".")},
    ]


def protected_display(rules: object) -> list[dict]:
    """The Protected items with their current effect (Manual only)."""
    blocked = set(normalise_rules(rules)["protected_block"])
    return [{"id": key, "label": label, "patterns": list(patterns),
             "effect": "block" if key in blocked else "ask"}
            for key, (label, patterns) in PROTECTED.items()]


# How many links per folder the panel lists by name; the count is always exact.
_LINKS_LISTED_PER_FOLDER = 50


def _describe_link(entry: Path) -> dict:
    """One link's name and resolved target, or why it has none."""
    try:
        target = str(entry.resolve(strict=True))
        error = ""
    except (OSError, RuntimeError) as exc:
        target = ""
        try:
            pointed = os.readlink(entry)
        except OSError:
            pointed = ""
        error = ("unresolvable (" + type(exc).__name__
                 + (f"; points at {pointed}" if pointed else "") + ")")
    return {"name": entry.name, "path": str(entry), "target": target,
            "error": error}


def find_protected_links(root: Path | None = None) -> dict:
    """Symlinks and junctions one level deep under `~/.kiro/{agents,steering,skills,hooks}`.

    260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL D-39. kiro-cli matches a file
    rule against a link's resolved target (F-1), so a write through a link
    whose target lies outside a Protected folder escapes the ask. This names
    those links for the settings panel; it does not enforce anything.

    Called **only** when the settings state is served (`GET
    /api/acp-permissions`) — never by `compile_block`, `derived_block_state` or
    the session gate, so a slow filesystem walk (these folders are often links
    into a synced folder) can never delay or refuse a session.

    A Protected folder that is itself a link is listed first, with
    `folder: True`, and counted: every write under it lands at its target
    (Phase 1 review, finding 10).

    Returns `{folder: {"count": n, "links": [{name, path, target, error}],
    "error": ""}}`. Never raises. A folder that exists but cannot be listed
    has its reason in `error` (final review, RE7), logged once per process,
    and the panel says it could not be checked; a missing folder is simply
    empty. Only the links the panel lists are resolved — a resolve can stall
    on an offline share — and the rest are counted (RE8). The answer is
    reused for `_LINKS_CACHE_SECONDS`, because the dashboard reads the state
    on every load and every return to the tab.
    """
    base = root if root is not None else KIRO_AGENTS_DIR.parent
    key = str(base)
    now = time.monotonic()
    cached = _links_cache.get(key)
    if cached is not None and now - cached[0] < _LINKS_CACHE_SECONDS:
        return copy.deepcopy(cached[1])
    found: dict = {}
    for name in PROTECTED:
        found[name] = _links_in(base / name)
    _links_cache[key] = (now, found)
    return copy.deepcopy(found)


# How long `find_protected_links` reuses its answer (RE8), and the answers.
_LINKS_CACHE_SECONDS = 30.0
_links_cache: dict[str, tuple[float, dict]] = {}
# Folder errors already logged in this process (RE7).
_link_errors_logged: set[str] = set()


def _links_in(folder: Path) -> dict:
    """`find_protected_links`'s entry for one Protected folder."""
    links: list[dict] = []
    count = 0
    error = ""
    try:
        if _is_link(folder):
            links.append({**_describe_link(folder), "folder": True})
            count += 1
    except Exception:  # noqa: BLE001 - never raises, by contract
        pass
    try:
        entries = sorted(folder.iterdir(), key=lambda p: p.name.lower())
    except FileNotFoundError:
        entries = []
    except Exception as exc:  # noqa: BLE001 - never raises, by contract
        entries = []
        error = f"{type(exc).__name__}: {exc}"
        if str(folder) not in _link_errors_logged:
            _link_errors_logged.add(str(folder))
            log.warning("could not check %s for links: %s", folder, error)
    for entry in entries:
        try:
            if not _is_link(entry):
                continue
            if len(links) < _LINKS_LISTED_PER_FOLDER:
                links.append(_describe_link(entry))
        except Exception:  # noqa: BLE001 - never raises, by contract
            continue
        count += 1
    return {"count": count, "links": links, "error": error}


def _is_junction(path: Path) -> bool:
    """`os.path.isjunction`, which is Python 3.12+; the project allows 3.11.

    Phase 1 review, finding 4. The fallback reads the reparse tag `lstat`
    reports on Windows; elsewhere there are no junctions.
    """
    isjunction = getattr(os.path, "isjunction", None)
    if isjunction is not None:
        return bool(isjunction(path))
    try:
        tag = getattr(os.lstat(path), "st_reparse_tag", None)
    except OSError:
        return False
    return tag is not None and tag == getattr(
        stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003)


def _is_link(path: Path) -> bool:
    return path.is_symlink() or _is_junction(path)
