"""Translate curses key codes into the string names used by the UI widgets."""

from __future__ import annotations

import curses

KEYMAP = {
    "\n": "<CR>", "\r": "<CR>", "\x1b": "<Esc>", "\x7f": "<BS>", "\b": "<BS>", "\t": "<Tab>",
}
CURSES_KEYS = {
    "KEY_BACKSPACE": "<BS>", "KEY_UP": "<Up>", "KEY_DOWN": "<Down>", "KEY_LEFT": "<Left>",
    "KEY_RIGHT": "<Right>", "KEY_DC": "<Del>", "KEY_HOME": "<Home>", "KEY_END": "<End>",
    "KEY_PPAGE": "<PgUp>", "KEY_NPAGE": "<PgDn>", "KEY_ENTER": "<CR>", "KEY_RESIZE": "<Resize>",
    "KEY_BTAB": "<S-Tab>",
}
_NAME_FOR_CODE = {getattr(curses, n): v for n, v in CURSES_KEYS.items() if hasattr(curses, n)}


def normalize_key(k) -> str:
    if isinstance(k, int):
        return _NAME_FOR_CODE.get(k, "<?>")
    return KEYMAP.get(k, k)
