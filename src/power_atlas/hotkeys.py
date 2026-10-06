"""Shortcut names, parsing and validation, shared by the settings write path
and the PowerAtlas window's shortcut listener.

Pure: no pywebview or pynput import, so `web.py` can validate a shortcut on
save without loading either.
261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 3
"""

DEFAULT_PEEK_HOTKEY = "ctrl+shift+z"

MODIFIERS = frozenset({"ctrl", "shift", "alt"})


def _build_vk_names() -> dict[int, str]:
    names: dict[int, str] = {}
    for vk in range(0x41, 0x5B):  # A-Z
        names[vk] = chr(vk).lower()
    for vk in range(0x30, 0x3A):  # 0-9
        names[vk] = chr(vk)
    for vk in range(0x70, 0x88):  # F1-F24
        names[vk] = f"f{vk - 0x6F}"
    names.update({
        0x1B: "esc", 0x20: "space", 0x09: "tab", 0x0D: "enter",
        0x08: "backspace", 0x2E: "delete", 0x24: "home", 0x23: "end",
        0x21: "page_up", 0x22: "page_down",
        0xBF: "/", 0xBE: ".", 0xBC: ",", 0xBA: ";",
        0xBB: "=", 0xBD: "-", 0xDB: "[", 0xDD: "]", 0xDC: "\\",
        0xC0: "`", 0xDE: "'",
        # The keys pynput reports under these names on every platform, which
        # the non-suppressing path accepted before Phase 3. Names checked
        # against `pynput.keyboard.Key` 1.8.2, VK codes against its values.
        0x26: "up", 0x28: "down", 0x25: "left", 0x27: "right",
        0x2D: "insert", 0x13: "pause", 0x2C: "print_screen",
        0x91: "scroll_lock", 0x90: "num_lock", 0x5D: "menu",
    })
    return names


# The single source for Windows virtual-key names: the keyboard filter maps a
# VK code through it, and a shortcut may only name a key it lists.
VK_NAMES: dict[int, str] = _build_vk_names()

# Esc dismisses a peek, so it is never a shortcut key.
KEY_NAMES = frozenset(VK_NAMES.values()) - {"esc"}


def parse_hotkey(hotkey: str) -> frozenset[str]:
    """'ctrl+shift+z' -> {'ctrl', 'shift', 'z'}: split on '+', trimmed, lowercased."""
    return frozenset(part.strip().lower() for part in hotkey.split("+")
                     if part.strip())


def hotkey_error(hotkey: str) -> str | None:
    """None when `hotkey` is a valid shortcut, else the problem in a few words.

    Valid: at least one modifier, at least one other key, and every token a
    known modifier or key name.
    """
    if not isinstance(hotkey, str):
        return "is not text"
    keys = parse_hotkey(hotkey)
    if not keys:
        return "is empty"
    for key in sorted(keys):
        if key == "esc":
            return "cannot use esc, which dismisses the peek"
        if key not in MODIFIERS and key not in KEY_NAMES:
            return f"has an unknown key '{key}'"
    if not keys & MODIFIERS:
        return "needs a modifier (ctrl, shift or alt)"
    if not keys - MODIFIERS:
        return "needs a key besides the modifiers"
    return None


def hotkeys_conflict(a: str, b: str) -> bool:
    """True when the two shortcuts are equal or one contains the other.

    Pressing the larger one would also satisfy the smaller one, so the two
    cannot both be in use. An empty shortcut conflicts with nothing.
    """
    ka, kb = parse_hotkey(a), parse_hotkey(b)
    if not ka or not kb:
        return False
    return ka <= kb or kb <= ka


def effective_peek_hotkey(hotkey: str) -> str:
    """The peek shortcut the window actually runs: the stored one, or the
    default when the stored one is invalid (and `create_peek` warns)."""
    return hotkey if hotkey_error(hotkey) is None else DEFAULT_PEEK_HOTKEY


def match_chord(chords: dict, key: str, held) -> str | None:
    """Which chord a key-down of `key` fires, or None (D-18).

    A chord matches when `key` is one of its non-modifier keys and all its
    keys are down (`held` plus `key`). Among matches the one with the most
    keys wins; a tie goes to "peek". `chords` maps an id to a key set, or to
    None for a chord that is off.
    """
    if key in MODIFIERS:
        return None
    down = set(held) | {key}
    best, best_len = None, -1
    for cid, keys in chords.items():
        if not keys or key not in keys or not keys <= down:
            continue
        n = len(keys)
        if n > best_len or (n == best_len and cid == "peek"):
            best, best_len = cid, n
    return best
