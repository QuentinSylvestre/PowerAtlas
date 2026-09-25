"""The permission rule rows: their keys, plain labels and which a card offers.

260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL. One source for the row names the
permission compiler (``agent_profile``), the ACP bridge (``acp``) and the web
routes (``web``) all use, so the rule editor, a prompt card and an error
message call a row by one name.

A leaf module: it imports nothing from ``power_atlas``. ``acp`` imports it, and
``acp``'s isolation boundary (see that module's docstring) holds only while
this module reaches nothing else in the package; a test pins it.
"""

from typing import Final

# The rows in compile order (D-11). The rule editor lists them in this order.
PERMISSION_ROWS: Final[tuple[str, ...]] = (
    "fs_read", "fs_write", "shell", "web_fetch", "web_search",
    "mcp", "subagent", "skill", "power")

ROW_LABELS: Final[dict[str, str]] = {
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

# D-31: the rows a prompt card's "Allow, and always in new sessions…" button
# can add to, in `PERMISSION_ROWS` order. Left out: `web_fetch` (P-0.5:
# `consent.resource` is the host, so a URL pattern never matches; the button is
# hidden), and `web_search` and `power`, whose prompt resource was never
# measured to match an allow pattern (P-0.9 covered `mcp`, `subagent` and
# `skill` only). Not named `RULE_ROWS`: the rules state payload's `rule_rows`
# already means all nine rows.
CARD_ROWS: Final[tuple[str, ...]] = (
    "fs_read", "fs_write", "shell", "mcp", "subagent", "skill")
