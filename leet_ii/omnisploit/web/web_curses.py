"""
Minimal in-memory curses shim for running omnisploit inside Pyodide.

Pyodide's CPython build ships no ncurses, and there's no real terminal
under a browser tab anyway. Unlike a real-time game, omnisploit's UI loop
(see ui/app.py's App.loop() and ui/setup.py's run_setup()) calls
scr.get_wch() and genuinely blocks until a key arrives -- there's no
nodelay/timeout anywhere. Rather than rewrite that control flow, this shim
makes get_wch() *actually* block: the worker script that loads this module
registers a JS function, waitForEvent(), that does a real synchronous
Atomics.wait() against a SharedArrayBuffer the main thread writes into on
every keydown. That's only legal off the main thread, which is exactly
where Pyodide runs here (see worker-template.js). refresh() similarly
calls back into JS (postRenderJSON) to push the freshly drawn screen to
the main thread for painting -- no polling either direction.

omnisploit never imports this module by name; the worker script registers
an instance of it into sys.modules['curses'] before omnisploit is
imported, so `import curses` resolves here instead of to a real (absent)
curses.

The KEY_* integer values below are not arbitrary: worker-template.js
hardcodes the identical numbers when encoding a special keypress, since
it has no visibility into this module's namespace. Keep them in sync by
hand if either side changes.
"""

import json

import js  # Pyodide's bridge to the worker's JS globals; waitForEvent/postRenderJSON/getInitialSize live there


class error(Exception):
    """Stand-in for curses.error."""


# ---- color constants (mirror real curses ordering; not load-bearing) ----
COLOR_BLACK = 0
COLOR_RED = 1
COLOR_GREEN = 2
COLOR_YELLOW = 3
COLOR_BLUE = 4
COLOR_MAGENTA = 5
COLOR_CYAN = 6
COLOR_WHITE = 7

# ---- attribute bits (style in the low byte; color pair packed above it) ----
A_NORMAL = 0
A_BOLD = 1 << 0
A_DIM = 1 << 1
A_REVERSE = 1 << 2
A_STANDOUT = 1 << 3
A_UNDERLINE = 1 << 4
_PAIR_SHIFT = 8

# ---- special key codes: must match worker-template.js's KEYMAP exactly ----
KEY_BACKSPACE = 1001
KEY_UP = 1002
KEY_DOWN = 1003
KEY_LEFT = 1004
KEY_RIGHT = 1005
KEY_DC = 1006
KEY_HOME = 1007
KEY_END = 1008
KEY_PPAGE = 1009
KEY_NPAGE = 1010
KEY_ENTER = 1011
KEY_BTAB = 1012
KEY_RESIZE = 1013

_PAIRS = {0: (-1, -1)}
_current_screen = None


def start_color():
    pass


def use_default_colors():
    pass


def has_colors():
    return True


def init_pair(n, fg, bg):
    _PAIRS[n] = (fg, bg)


def color_pair(n):
    return n << _PAIR_SHIFT


def curs_set(n):
    if _current_screen is not None:
        _current_screen.cursor_visible = bool(n)


def raw():
    pass


def wrapper(fn):
    """Real curses.wrapper() sets up/tears down a terminal. There's nothing
    to set up here beyond making a Screen; this call blocks for the life of
    the game, same as it would under a real terminal."""
    return fn(Screen())


class Screen:
    """Stands in for stdscr: a (char, attr) grid plus a blocking key source."""

    def __init__(self, cols=None, rows=None):
        if cols is None or rows is None:
            size = js.getInitialSize()
            cols, rows = int(size[0]), int(size[1])
        self.cols = cols
        self.rows = rows
        self._buf = self._blank(cols, rows)
        self.cursor_visible = False
        self.cy = self.cx = 0
        global _current_screen
        _current_screen = self

    @staticmethod
    def _blank(cols, rows):
        return [[(" ", 0) for _ in range(cols)] for _ in range(rows)]

    # -------- curses-alike surface used by omnisploit --------

    def getmaxyx(self):
        return self.rows, self.cols

    def erase(self):
        self._buf = self._blank(self.cols, self.rows)

    def addnstr(self, y, x, text, n, attr=0):
        if not (0 <= y < self.rows):
            return
        row = self._buf[y]
        for i, ch in enumerate(text[:n]):
            xi = x + i
            if xi >= self.cols:
                break
            if xi >= 0:
                row[xi] = (ch, attr)

    def move(self, y, x):
        self.cy, self.cx = y, x

    def keypad(self, flag):
        pass

    def refresh(self):
        js.postRenderJSON(json.dumps(self._render()))

    def resize(self, cols, rows):
        cols, rows = max(1, int(cols)), max(1, int(rows))
        if (cols, rows) != (self.cols, self.rows):
            self.cols, self.rows = cols, rows
            self._buf = self._blank(cols, rows)

    def get_wch(self):
        """Block (via a real Atomics.wait in JS) until a key or resize event arrives."""
        while True:
            evt = js.waitForEvent()
            kind, value = int(evt[0]), int(evt[1])
            if kind == 0:  # plain character: value is its Unicode codepoint
                return chr(value)
            if kind == 1:  # special key: value is already one of the KEY_* ints above
                return value
            if kind == 2:  # resize: value packs cols*100000 + rows
                self.resize(value // 100000, value % 100000)
                return KEY_RESIZE
            raise error(f"unknown event kind {kind}")

    # -------- rendering for the browser: run-length-encoded rows --------

    def _render(self):
        rows_out = []
        for row in self._buf:
            runs = []
            cur_text, cur_style = "", None
            for ch, attr in row:
                style = _cell_style(attr)
                if style != cur_style:
                    if cur_text:
                        runs.append([cur_text, *cur_style])
                    cur_text, cur_style = ch, style
                else:
                    cur_text += ch
            if cur_text:
                runs.append([cur_text, *cur_style])
            rows_out.append(runs)
        cursor = [self.cy, self.cx] if self.cursor_visible else None
        return {"cols": self.cols, "rows": self.rows, "data": rows_out, "cursor": cursor}


# ---- theme: 8 ANSI-ish colors resolved to hex for the browser renderer ----
_HEX = {
    COLOR_BLACK: "#0b0f0b", COLOR_RED: "#ff5a5a", COLOR_GREEN: "#9dff9d",
    COLOR_YELLOW: "#ffd24d", COLOR_BLUE: "#6a8caf", COLOR_MAGENTA: "#ff7ad1",
    COLOR_CYAN: "#7fe6ff", COLOR_WHITE: "#eafcea",
}
_DEFAULT_FG = "#eafcea"  # terminal's default foreground (COLOR_WHITE) -- plain text, not the green of pair 1
_DEFAULT_BG = "#060a06"


def _cell_style(attr):
    pair = attr >> _PAIR_SHIFT
    style = attr & 0xFF
    fg, bg = _PAIRS.get(pair, (-1, -1))
    fg_hex = _HEX.get(fg, _DEFAULT_FG) if fg != -1 else _DEFAULT_FG
    bg_hex = _HEX.get(bg) if bg != -1 else None
    if style & A_REVERSE or style & A_STANDOUT:
        fg_hex, bg_hex = (bg_hex or _DEFAULT_BG), fg_hex
    return (fg_hex, bg_hex, bool(style & A_BOLD), bool(style & A_DIM), bool(style & A_UNDERLINE))
