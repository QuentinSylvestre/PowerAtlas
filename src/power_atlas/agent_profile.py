"""Generate the derived kiro-cli agent that carries PowerAtlas's ACP posture.

260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 1.

PowerAtlas never modifies the user's own kiro-cli agent. It reads a **base
agent** (`~/.kiro/agents/<name>.md`, configurable, default `kiro_default`) and
writes a **derived agent** (`~/.kiro/agents/poweratlas-acp.md`) that is the base
file byte-for-byte with one `permissions:` block inserted into, or replaced
inside, its YAML frontmatter. Interactive terminal kiro-cli sessions keep using
the base agent untouched, which is what keeps their permission posture
independent of anything PowerAtlas does.

**The derived agent exists only while the setting is on.** Turning the setting
off *deletes* the file rather than writing an allow-all one (Step 5 review,
2026-09-22; the earlier behaviour is described in plan section 9 Phase 1). The
reason is P1: any file under `~/.kiro/agents/` registers in kiro-cli's own mode
catalogue, so an allow-all `poweratlas-acp` is selectable from a terminal
session's agent picker. For a user whose machine baseline is narrower than
allow-all, that widens their posture — in the one state where PowerAtlas is
supposed to be doing nothing at all. Absence is the only representation of
`off` that cannot widen anything.

Four properties this module is built around, each measured rather than assumed
(plan section 9):

* **Injection is textual, never parsed** (D-18). No YAML library is declared or
  installed in this project, and byte-identity outside the injected block is an
  exit criterion — only *not* parsing achieves that by construction, since a
  parse/re-emit round trip normalises quoting, key order and flow style.

* **A base agent this module cannot splice safely is refused, not guessed at.**
  The splice assumes a frontmatter whose root mapping sits at column 0, which is
  the shape the shipped `kiro_default.md` has. An indented root mapping, or a
  frontmatter region that turns out to hold document body, raises rather than
  producing a file with two `permissions:` keys or a block spliced into the
  prose — both of which are the silent fail-open below.

* **The write is verified before it is published** (D-19): tmp -> `fsync` ->
  read the **tmp** back and re-excise it -> only then `os.replace`. The ordering
  is the point, not the check. Verifying after `os.replace` detects a bad splice
  with the last-good file already destroyed, which is the opposite of what
  D-10/SC-8 promises. Staging outside the agents directory and publishing only a
  file that has already been read back means a failure leaves the previous file
  untouched because `os.replace` never ran.

  The pattern is `config.save_config`'s, which the secret files now share too
  (`config._write_secret_file`: tmp, `fsync`, `os.replace`; the earlier
  in-place `O_CREAT|O_TRUNC` secret write was reversed in
  260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 4 review). It
  matters more here than there. A torn `permissions:` block fails **open**:
  kiro-cli loads an agent whose frontmatter does not parse without an error or
  a warning, and silently falls back to the wider scopes. That was reproduced
  live, by accident, across seven consecutive probe runs in Phase 0. This file
  adds one step the secrets do not need: the tmp is read back and re-excised
  before `os.replace`.

* **Generation failure never widens the posture** (D-10/SC-8). A failed
  regeneration leaves whatever is already on disk exactly where it is. It never
  writes a base-agent copy over a previously generated derived agent, because an
  unconditional fallback like that turns a transient write error into a silently
  ungated session. A failed *delete* fails the same way round: the file stays,
  and the failure is reported, rather than the module pretending it is gone.

`acp.py` must not import this module: it states a narrow isolation boundary in
its own header and imports three names from two intra-package modules. The name it needs,
`DERIVED_AGENT_NAME`, lives in `config.py` for that reason (D-20).
"""

import logging
import os
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from importlib import resources
from pathlib import Path

from .config import DERIVED_AGENT_NAME, load_config

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

# The overlay is package data, read through `importlib.resources` so it survives
# a wheel build. Read once and cached: it is a committed constant, and a
# per-generation read would put a package-data lookup on the settings write path
# for no benefit.
_OVERLAY_PACKAGE = "power_atlas"
_OVERLAY_RESOURCE = "agents/permissions.yaml"
_overlay_cache: str | None = None

# The provenance stamp every block this module writes carries, and the whole
# basis on which the `off` transition is allowed to delete a file. The derived
# agent is documented as a build product that is never hand-edited, but the
# *name* is not reserved: if a block at that path does not say PowerAtlas wrote
# it, it is left alone and reported rather than removed.
#
# A marker rather than byte-equality against a table of every block this module
# has ever written: byte-equality would fail to recognise its own output from
# one overlay revision ago, which is exactly the file an upgrade needs to clean
# up. `TestOverlayIsReachableAtRuntime` asserts the shipped overlay carries the
# marker, so a rewrite that drops the line fails loudly in the suite instead of
# quietly making every derived agent undeletable.
_PROVENANCE_MARKER = "Written by PowerAtlas"

# Serialises generation. Two settings writes dispatched through
# `asyncio.to_thread` would otherwise race on the same staging path, and the
# loser could `os.replace` a half-written file into place. It also covers the
# `load_config()` read in `sync_from_config`, so two rapid writes cannot each
# capture a snapshot and then publish in scheduling order rather than in the
# order the settings were saved.
_generation_lock = threading.Lock()


@dataclass(frozen=True)
class GenerationStatus:
    """The last generation attempt, as the settings panel needs to report it.

    `attempted` separates "no generation has run in this process" from
    "generation ran and succeeded", which the panel must not conflate: the first
    happens for a few milliseconds at startup, the second is the steady state.

    A successful `off` pass — the derived agent deleted, or already absent — is
    also `ok=True`. The healthy off state is therefore `enabled=False`,
    `ok=True`, block state `"absent"`.
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


def overlay_text() -> str:
    """The `on` state's `permissions:` block, from package data.

    Normalised in two ways, both because the block is spliced into a file whose
    every other byte is copied through unchanged:

    * Line endings become `\\n`. The splice re-applies the *base's* dominant
      ending (`_dominant_line_ending`), so a `\\r\\n` overlay checked out on
      Windows would otherwise reach an LF base as `\\r\\n` and make the derived
      agent mixed-ending, and a CRLF base would end up with `\\r\\r\\n`.

    * Exactly one trailing newline. `inject_permissions` drops trailing blank
      lines from the block and `excise_permissions` hands them back to the
      surrounding text, so an incidental blank line at the end of the
      package-data file would make the written block and the assembled block
      compare unequal — and the verification would then fail every time,
      permanently disabling the feature over a whitespace edit.
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
        lf = raw.replace("\r\n", "\n").replace("\r", "\n")
        _overlay_cache = lf.rstrip("\n") + "\n"
    return _overlay_cache


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


def build_derived_agent(base_text: str) -> str:
    """The derived agent's full text for a base agent.

    One argument, not two: there is no `off` variant to build. `off` deletes the
    file (see the module docstring), so the only block this module ever splices
    is the overlay.
    """
    return inject_permissions(base_text, overlay_text())


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


def derived_block_state() -> str:
    """Classify the derived agent currently on disk.

    The settings panel needs this rather than the bare toggle: the toggle
    records what the user asked for, and this records what a session started
    right now would actually get. One of:

    * `"absent"` — no derived agent at all. This is the **expected, healthy**
      state while the setting is off, not an error, because `off` deletes the
      file. It is also the state after a failed first generation.
    * `"on"` — the current overlay, byte-for-byte modulo the line endings and
      trailing blank lines the splice re-derives from the base. The only state
      `in_effect` accepts.
    * `"stale"` — a block carrying PowerAtlas's provenance marker that is not
      the current overlay: this module's own output from a different overlay
      revision, or from the version that wrote an allow-all block in the `off`
      state. Never `in_effect`, and safe to delete, which is the only reason it
      is a state of its own rather than folded into `"unknown"`.
    * `"unknown"` — anything else: a file whose `permissions:` block PowerAtlas
      did not write, a file with no `permissions:` key, a file that cannot be
      read, or one whose frontmatter `_check_frontmatter_shape` refuses. Never
      deleted — the name is not reserved, and a hand-authored agent that took it
      is the user's file, not a build product.
    """
    path = derived_agent_path()
    if not path.exists():
        return "absent"
    try:
        _, block = excise_permissions(_read_text(path))
    except AgentProfileError:
        return "unknown"
    if not block:
        return "unknown"
    try:
        on_block = overlay_text()
    except AgentProfileError:
        return "unknown"
    if _norm_block(block) == _norm_block(on_block):
        return "on"
    if _PROVENANCE_MARKER in block:
        return "stale"
    return "unknown"


def _generate(status: GenerationStatus, base_agent: str) -> GenerationStatus:
    """Write the derived agent, or raise leaving the previous file alone."""
    name = validate_base_agent_name(base_agent)
    status = replace(status, base_agent=name)
    source = base_agent_path(name)
    target = derived_agent_path()
    if target == source:
        # Only reachable if the base-agent setting names the derived agent
        # itself, which would make generation read its own output and compound
        # the block on every restart.
        raise AgentProfileError(
            f"base agent {name!r} is the derived agent; pick a different base")
    block = overlay_text()
    base_text = _read_text(source)
    derived_text = inject_permissions(base_text, block)
    base_kept = excise_permissions(base_text)[0]

    def verify(written: str) -> None:
        """A structural confirmation of this module's own splice.

        The frontmatter still has its fences, the block is the overlay, and
        every other byte still matches the base. It is deliberately **not** a
        claim that kiro-cli bound the rules: only a live session can show that,
        and `kiro-cli agent validate --path` was measured in Phase 0 to report
        every `.md`-format agent as invalid, including the machine's own working
        one. Live bind confirmation is Phase 7's.
        """
        kept, block_back = excise_permissions(written)
        if _norm_block(block_back) != _norm_block(block) or kept != base_kept:
            raise AgentProfileError(
                f"the staged {target.name} does not match what was spliced; "
                "the permission block may not be in effect")

    try:
        _publish(target, derived_text, verify)
    except OSError as exc:
        raise AgentProfileError(f"cannot write {target}: {exc}") from exc
    return replace(status, ok=True, error="")


def _remove(status: GenerationStatus) -> GenerationStatus:
    """Delete the derived agent, or refuse to touch a file this module did not write."""
    target = derived_agent_path()
    state = derived_block_state()
    if state == "absent":
        return replace(status, ok=True, error="")
    if state not in ("on", "stale"):
        raise AgentProfileError(
            f"{target} does not carry PowerAtlas's marker, so it was left in "
            "place; remove it by hand if it should not be selectable")
    try:
        target.unlink()
    except OSError as exc:
        # The fail-safe direction is a surviving file that is reported, not a
        # module that claims the posture is gone when it is still selectable.
        raise AgentProfileError(f"cannot remove {target}: {exc}") from exc
    return replace(status, ok=True, error="")


def _apply_locked(*, enabled: bool, base_agent: object) -> GenerationStatus:
    """Bring the derived agent in line with `enabled`. The lock must be held."""
    global _status
    status = GenerationStatus(attempted=True, ok=False,
                              enabled=bool(enabled), base_agent="")
    try:
        _clear_stage()
        if enabled:
            status = _generate(status, base_agent)
        else:
            # The name plays no part in a delete, so an invalid one left in
            # `config.toml` by a hand edit must not stop the file coming off
            # disk. It is still recorded, because the panel shows it.
            if isinstance(base_agent, str):
                status = replace(status, base_agent=base_agent)
            status = _remove(status)
    except AgentProfileError as exc:
        _status = replace(status, ok=False, error=str(exc))
        log.error("derived agent update failed: %s", exc)
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


def sync_from_config() -> GenerationStatus:
    """Bring the derived agent in line with the **current** settings.

    The entry point `web.py` uses. `load_config()` runs *inside* the lock, which
    is the difference that matters: read outside it, two settings writes
    dispatched through `asyncio.to_thread` can each capture a snapshot and then
    publish in scheduling order rather than in the order they were saved, so the
    file can end up disagreeing with `config.toml`.
    """
    with _generation_lock:
        config = load_config()
        return _apply_locked(
            enabled=bool(config.acp_permissions_enabled),
            base_agent=config.acp_permission_base_agent)
