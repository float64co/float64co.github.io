"""A read-only full-screen pager (``less``/``more``): paging, line scrolling and search.

Pure logic, no curses -- the App's ``draw_pager`` renders ``visible()`` and feeds key presses to
:meth:`Pager.key`. Long lines wrap at ``width`` columns, and scrolling counts screen rows, so a
line taller than the screen can still be read in full. Modeled after a useful subset of real ``less``: no numeric
prefix counts, no backward search (``?``), no multiple files, no following (``F``).
"""

from __future__ import annotations

import re
from bisect import bisect_right

PAGE_KEYS = frozenset({" ", "f", "<PgDn>"})
HALF_DOWN_KEYS = frozenset({"d"})
BACK_KEYS = frozenset({"b", "<PgUp>"})
HALF_UP_KEYS = frozenset({"u"})
DOWN_KEYS = frozenset({"j", "<Down>", "<CR>"})
UP_KEYS = frozenset({"k", "<Up>"})
TOP_KEYS = frozenset({"g", "<Home>"})
BOTTOM_KEYS = frozenset({"G", "<End>"})
QUIT_KEYS = frozenset({"q", "<Esc>", "\x03"})
CTRL = {"\x06": "f", "\x02": "b", "\x04": "d", "\x15": "u"}  # Ctrl-F/B/D/U, the readline-style spellings


class Pager:
    def __init__(self, text: str, name: str) -> None:
        self.name = name
        self.lines = text.split("\n")
        if self.lines and self.lines[-1] == "":
            self.lines.pop()  # a trailing newline shouldn't show as one more blank line
        self.lines = [line.expandtabs() for line in self.lines]  # so a column is a character
        self._width: int | None = None  # None: not wrapped
        self._wrap()
        self.top = 0  # the first screen row shown
        self.height = 24  # the App overwrites this every frame from the real screen size
        self.done = False
        self.mode = "normal"  # normal | search
        self.cmdline = ""
        self.msg = ""
        self.last_search: str | None = None

    # ----------------------------------------------------------------- wrap
    @property
    def width(self) -> int | None:
        return self._width

    @width.setter
    def width(self, width: int | None) -> None:
        width = None if width is None else max(width, 1)
        if width == self._width:
            return
        at = self.rows[self.top] if self.rows else (0, 0)
        self._width = width
        self._wrap()
        # after a resize, stay on the text that was at the top
        self.top = max(0, min(self._row_of(*at), self.max_top))

    def _wrap(self) -> None:
        """``rows`` holds (line index, first column) for every screen row."""
        self.rows: list[tuple[int, int]] = []
        for i, line in enumerate(self.lines):
            step = self._width or max(len(line), 1)
            self.rows.extend((i, col) for col in range(0, max(len(line), 1), step))

    def _row_of(self, line: int, col: int = 0) -> int:
        """The screen row showing this column of this line."""
        return max(0, bisect_right(self.rows, (line, col)) - 1)

    # ----------------------------------------------------------------- view
    def visible(self) -> list[tuple[int, int, str]]:
        """(line index, first column, text) for each row on screen."""
        return [(i, col, self.lines[i][col : None if self._width is None else col + self._width])
                for i, col in self.rows[self.top : self.top + self.height]]

    @property
    def max_top(self) -> int:
        return max(0, len(self.rows) - self.height)

    @property
    def at_end(self) -> bool:
        return self.top >= self.max_top

    def _goto(self, top: int) -> None:
        self.top = max(0, min(top, self.max_top))
        self.msg = ""

    def _compile(self, pattern: str) -> re.Pattern:
        try:
            return re.compile(pattern)
        except re.error:
            return re.compile(re.escape(pattern))

    def match_spans(self, line: str) -> list[tuple[int, int]]:
        if not self.last_search:
            return []
        return [(m.start(), m.end()) for m in self._compile(self.last_search).finditer(line) if m.end() > m.start()]

    def _search(self, pattern: str, forward: bool = True) -> None:
        self.last_search = pattern
        rx = self._compile(pattern)
        # every match's screen row; one line can hold several, on different rows
        found = sorted({self._row_of(i, m.start()) for i, line in enumerate(self.lines)
                        for m in rx.finditer(line)})
        ahead = [row for row in found if row > self.top] if forward \
            else [row for row in reversed(found) if row < self.top]
        if ahead:
            self._goto(ahead[0])
            return
        self.msg = "Pattern not found"

    # ----------------------------------------------------------------- keys
    def key(self, k: str) -> None:
        if self.mode == "search":
            self._key_search(k)
            return
        k = CTRL.get(k, k)
        if k in QUIT_KEYS:
            self.done = True
        elif k in PAGE_KEYS:
            self._goto(self.top + max(self.height - 1, 1))
        elif k in BACK_KEYS:
            self._goto(self.top - max(self.height - 1, 1))
        elif k in HALF_DOWN_KEYS:
            self._goto(self.top + max(self.height // 2, 1))
        elif k in HALF_UP_KEYS:
            self._goto(self.top - max(self.height // 2, 1))
        elif k in DOWN_KEYS:
            self._goto(self.top + 1)
        elif k in UP_KEYS:
            self._goto(self.top - 1)
        elif k in TOP_KEYS:
            self._goto(0)
        elif k in BOTTOM_KEYS:
            self._goto(self.max_top)
        elif k == "/":
            self.mode, self.cmdline, self.msg = "search", "/", ""
        elif k == "n" and self.last_search:
            self._search(self.last_search, forward=True)
        elif k == "N" and self.last_search:
            self._search(self.last_search, forward=False)

    def _key_search(self, k: str) -> None:
        if k == "<CR>":
            pattern = self.cmdline[1:]
            self.mode = "normal"
            if pattern:
                self._search(pattern, forward=True)
        elif k in ("<Esc>", "\x03"):
            self.mode, self.cmdline, self.msg = "normal", "", ""
        elif k == "<BS>":
            if len(self.cmdline) > 1:
                self.cmdline = self.cmdline[:-1]
            else:
                self.mode, self.cmdline = "normal", ""
        elif len(k) == 1 and ord(k) >= 32:
            self.cmdline += k

    # ---------------------------------------------------------------- status
    def status(self) -> str:
        if self.mode == "search":
            return self.cmdline
        if self.msg:
            return self.msg
        if not self.lines:
            return f"{self.name}: (END)"
        end = min(self.top + self.height, len(self.rows))
        first, last = self.rows[self.top][0] + 1, self.rows[end - 1][0] + 1
        where = "(END)" if self.at_end else f"{100 * end // len(self.rows)}%"
        return f"{self.name}  lines {first}-{last}/{len(self.lines)}  {where}"
