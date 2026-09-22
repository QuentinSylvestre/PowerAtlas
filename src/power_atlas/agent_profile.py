"""Generate the derived kiro-cli agent that carries PowerAtlas's ACP posture.

260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 1.

PowerAtlas never modifies the user's own kiro-cli agent. It reads a **base
agent** (`~/.kiro/agents/<name>.md`, configurable, default `kiro_default`) and
writes a **derived agent** (`~/.kiro/agents/poweratlas-acp.md`) that is the base
file byte-for-byte with one `permissions:` block inserted into, or replaced
inside, its YAML frontmatter. Interactive terminal kiro-cli sessions keep using
the base agent untouched, which is what keeps their permission posture
independent of anything PowerAtlas does.

Three properties this module is built around, each measured rather than assumed
(plan section 9):

* **Injection is textual, never parsed** (D-18). No YAML library is declared or
  installed in this project, and byte-identity outside the injected block is an
  exit criterion — only *not* parsing achieves that by construction, since a
  parse/re-emit round trip normalises quoting, key order and flow style.

* **The write is atomic** (D-19): tmp -> `fsync` -> `os.replace`, reusing
  `config.save_config`'s pattern rather than `config._write_remote_secret`'s
  in-place `O_CREAT|O_TRUNC`. The two fail in opposite directions. A torn secret
  fails closed, because `load_remote_secret` rejects anything short. A torn
  `permissions:` block fails **open**: kiro-cli loads an agent whose frontmatter
  does not parse without an error or a warning, and silently falls back to the
  wider scopes. That was reproduced live, by accident, across seven consecutive
  probe runs in Phase 0.

* **Generation failure never widens the posture** (D-10/SC-8). A failed
  regeneration leaves whatever is already on disk exactly where it is. It never
  writes a base-agent copy over a previously generated derived agent, because an
  unconditional fallback like that turns a transient write error into a silently
  ungated session.

`acp.py` must not import this module: it states a narrow isolation boundary in
its own header and imports exactly two intra-package names. The name it needs,
`DERIVED_AGENT_NAME`, lives in `config.py` for that reason (D-20).
"""

import logging
import os
import re
import threading
from dataclasses import dataclass, replace
from importlib import resources
from pathlib import Path

from .config import DERIVED_AGENT_NAME

log = logging.getLogger(__name__)


class AgentProfileError(Exception):
    """A generation failure this module predicted and named.

    Distinct from an unexpected bug on purpose. `web.py`'s call sites catch
    `Exception` broadly so that neither startup nor a settings write can be
    aborted by either kind, but only this type carries a message worth showing
    the user in the settings panel.
    """


# `~/.kiro/agents` as a module-level constant rather than a function reading
# `Path.home()` per call: the test suite's autouse `isolated_config` fixture
# redirects it into `tmp_path`, and a per-call `Path.home()` would need every
# test that reaches `lifespan` to patch `Path.home` itself — which would also
# redirect `data_kiro_v3`'s session scan and half the launcher.
KIRO_AGENTS_DIR = Path.home() / ".kiro" / "agents"

# Charset regex plus the Windows reserved device names (D-8). A bare charset
# regex admits `CON`, `PRN`, `AUX`, `NUL`, `COM1`-`COM9` and `LPT1`-`LPT9`,
# every one of which misbehaves when opened as `<name>.md` on Windows — the
# open succeeds against the device, not a file.
_AGENT_NAME_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL",
                 *(f"COM{i}" for i in range(1, 10)),
                 *(f"LPT{i}" for i in range(1, 10))}

# A top-level `permissions` key inside the frontmatter. Anchored at column 0, so
# a nested `permissions:` under some other mapping is left alone. `\s*` before
# the colon because `permissions :` is legal YAML, and the quoted spellings
# because `"permissions":` is the same key. Every missed spelling leaves the
# base's own block in place beside the injected one — two top-level keys of the
# same name, which is the fail-open shape this module exists to avoid.
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

# The overlay is package data, read through `importlib.resources` so it survives
# a wheel build. Read once and cached: it is a committed constant, and a
# per-generation read would put a package-data lookup on the settings write path
# for no benefit.
_OVERLAY_PACKAGE = "power_atlas"
_OVERLAY_RESOURCE = "agents/permissions.yaml"
_overlay_cache: str | None = None

# The `off` state's block. A Python constant rather than a second package-data
# file: it is four lines with nothing to review, and it reproduces the shape of
# the machine-wide `~/.kiro/settings/permissions.yaml` this feature leaves
# untouched. `off` exists so the file on disk always agrees with the setting —
# without regenerating on the off transition, a previously generated `on` file
# would stay selectable after the user turned the setting off.
_ALLOW_ALL_BLOCK = """\
permissions:
  # Written by PowerAtlas. This whole file is rewritten at startup and on every
  # settings change, so an edit here is lost.
  #
  # 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 1
  #
  # The PowerAtlas ACP permission posture is OFF, which is allow-all and is
  # exactly the behaviour PowerAtlas had before the setting existed. The file is
  # still written in this state so that what is on disk always matches the
  # setting; nothing selects it while the setting is off.
  rules:
    - capability: all
      effect: allow
"""

# Serialises generation. Two settings writes dispatched through
# `asyncio.to_thread` would otherwise race on the same `.tmp` path, and the
# loser could `os.replace` a half-written file into place.
_generation_lock = threading.Lock()


@dataclass(frozen=True)
class GenerationStatus:
    """The last generation attempt, as the settings panel needs to report it.

    `attempted` separates "no generation has run in this process" from
    "generation ran and succeeded", which the panel must not conflate: the first
    happens for a few milliseconds at startup, the second is the steady state.
    """

    attempted: bool = False
    ok: bool = False
    error: str = ""
    enabled: bool = False
    base_agent: str = ""


_status = GenerationStatus()


def last_generation() -> GenerationStatus:
    """The outcome of the most recent generation attempt in this process."""
    return _status


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


def overlay_text() -> str:
    """The `on` state's `permissions:` block, from package data.

    Line endings are normalised to `\\n` because the block is spliced into a
    file whose every other byte is copied through unchanged, and a `\\r\\n`
    overlay checked out on Windows would otherwise make the derived agent
    mixed-ending.
    """
    global _overlay_cache
    if _overlay_cache is None:
        try:
            raw = (resources.files(_OVERLAY_PACKAGE)
                   .joinpath(_OVERLAY_RESOURCE)
                   .read_text(encoding="utf-8"))
        except (OSError, ValueError, ModuleNotFoundError) as exc:
            raise AgentProfileError(
                f"permission overlay unreadable: {type(exc).__name__}: {exc}"
            ) from exc
        _overlay_cache = raw.replace("\r\n", "\n").replace("\r", "\n")
    return _overlay_cache


def assemble_rules(enabled: bool) -> str:
    """The `permissions:` block for the given toggle state.

    `(allow-all | transcribed defaults)`, with no PowerAtlas-authored deny floor
    — D-13 was superseded by user decision on 2026-09-22 after Phase 0 measured
    that a bespoke pattern floor is defeated by an ordinary rephrasing. Correct
    assembly of the `on` branch is therefore the only backstop this feature
    ships, which is why the `exclude` pairing in the overlay is load-bearing
    rather than cosmetic.
    """
    return overlay_text() if enabled else _ALLOW_ALL_BLOCK


def _frontmatter_bounds(lines: list[str]) -> int:
    """Index of the closing `---` fence, or raise.

    kiro-cli's own rule: the opening fence is the first line of the file and the
    closing fence is the first `---` line after it. Nothing cleverer, because
    anything cleverer disagrees with the loader this file is written for.
    """
    if not lines:
        raise AgentProfileError("base agent is empty")
    # A UTF-8 BOM is tolerated on the first line and nowhere else. It is not
    # stripped from the output: every byte outside the injected block survives.
    if lines[0].lstrip("﻿").rstrip("\r") != "---":
        raise AgentProfileError(
            "base agent has no YAML frontmatter (first line is not '---')")
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r") == "---":
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
    lines = text.split("\n")
    close = _frontmatter_bounds(lines)
    regions = _permissions_regions(lines, close)
    if not regions:
        return text, ""
    kept: list[str] = []
    cursor = 0
    for start, end in regions:
        kept.extend(lines[cursor:start])
        cursor = end
    kept.extend(lines[cursor:])
    first = "\n".join(lines[regions[0][0]:regions[0][1]]) + "\n"
    return "\n".join(kept), first


def inject_permissions(base_text: str, block: str) -> str:
    """`base_text` with `block` as its only top-level `permissions:` key.

    Replaces an existing block in place, else inserts immediately before the
    closing fence. Purely textual: nothing outside the block is inspected, let
    alone rewritten.
    """
    lines = base_text.split("\n")
    close = _frontmatter_bounds(lines)
    block_lines = block.rstrip("\n").split("\n")
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


def build_derived_agent(base_text: str, enabled: bool) -> str:
    """The derived agent's full text for a base agent and a toggle state."""
    return inject_permissions(base_text, assemble_rules(enabled))


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


def _atomic_write(path: Path, text: str) -> None:
    """tmp -> `fsync` -> `os.replace`, with the tmp removed on any failure."""
    tmp = path.with_suffix(".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "wb") as fh:
            fh.write(text.encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        # `missing_ok` because the failure may have been the `mkdir` or the
        # `open` itself, and an unlink raising here would mask the real cause.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            log.warning("could not remove %s after a failed write", tmp)
        raise


def derived_block_state() -> str:
    """Classify the `permissions:` block currently on disk.

    One of `"absent"` (no derived agent, or one with no `permissions:` key),
    `"on"`, `"off"`, or `"unknown"` (a block PowerAtlas did not write, or a file
    it cannot read). The settings panel needs this rather than the bare toggle:
    the toggle records what the user asked for, and this records what a session
    started right now would actually get. A regeneration that fails on the
    `off` -> `on` transition leaves the previous **allow-all** file in place, and
    "the setting says on" would otherwise read as "the posture is on".
    """
    path = derived_agent_path()
    if not path.exists():
        return "absent"
    try:
        _, block = excise_permissions(_read_text(path))
    except AgentProfileError:
        return "unknown"
    if not block:
        return "absent"
    try:
        on_block = overlay_text()
    except AgentProfileError:
        return "unknown"
    if block == on_block:
        return "on"
    if block == _ALLOW_ALL_BLOCK:
        return "off"
    return "unknown"


def regenerate(*, enabled: bool, base_agent: str) -> GenerationStatus:
    """Rewrite the derived agent, or raise `AgentProfileError` leaving it alone.

    The failure path is the designed one (D-10/SC-8): nothing on disk is
    touched, so a previously generated derived agent is **kept** rather than
    replaced by a base-agent copy, and the recorded status is what the settings
    panel reads to say on-but-not-in-effect.

    After `os.replace` the written file is read back and re-excised, and both
    halves are compared against what was spliced. That is a structural
    confirmation of this module's own splice — the frontmatter still has its
    fences, the block is byte-identical to the overlay, and every other byte
    still matches the base. It is deliberately **not** a claim that kiro-cli
    bound the rules: only a live session can show that, and
    `kiro-cli agent validate --path` was measured in Phase 0 to report every
    `.md`-format agent as invalid, including the machine's own working one. Live
    bind confirmation is Phase 7's.
    """
    global _status
    with _generation_lock:
        status = GenerationStatus(attempted=True, ok=False,
                                  enabled=bool(enabled), base_agent="")
        try:
            name = validate_base_agent_name(base_agent)
            status = replace(status, base_agent=name)
            block = assemble_rules(enabled)
            base_text = _read_text(base_agent_path(name))
            derived_text = inject_permissions(base_text, block)
            target = derived_agent_path()
            if target == base_agent_path(name):
                # Only reachable if the base-agent setting names the derived
                # agent itself, which would make generation read its own output
                # and compound the block on every restart.
                raise AgentProfileError(
                    f"base agent {name!r} is the derived agent; "
                    "pick a different base")
            try:
                _atomic_write(target, derived_text)
            except OSError as exc:
                raise AgentProfileError(
                    f"cannot write {target}: {exc}") from exc
            written = _read_text(target)
            kept, block_back = excise_permissions(written)
            if block_back != block or kept != excise_permissions(base_text)[0]:
                raise AgentProfileError(
                    f"{target} does not match what was written; "
                    "the permission block may not be in effect")
        except AgentProfileError as exc:
            _status = replace(status, ok=False, error=str(exc))
            log.error("derived agent generation failed: %s", exc)
            raise
        except Exception as exc:  # noqa: BLE001 - see below
            # An unexpected bug is still a generation failure the panel has to
            # report. Recording it here rather than only at the call site is
            # what stops a stale `ok=True` from an earlier successful run
            # outliving a crash in a later one.
            _status = replace(status, ok=False,
                              error=f"{type(exc).__name__}: {exc}")
            log.exception("derived agent generation raised unexpectedly")
            raise
        _status = replace(status, ok=True, error="")
        return _status
