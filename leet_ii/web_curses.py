"""
Minimal in-memory curses shim for running L.E.E.T. II inside Pyodide.

Grown from the L.E.E.T. web build's shim to cover what the sequel adds: the
ground war (right click) and the omnisploit terminal (get_wch, raw mode,
a visible text cursor, editing keys). The bridge registers this module as
sys.modules['curses'] before any game module is imported.

Typed characters are queued as str and special keys as int, so getch() (the
games) and get_wch() (omnisploit) can each return what they expect.
Consecutive pure mouse-motion reports are coalesced so the pointer can't
outrun the tick rate; presses and releases are never dropped.
"""


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
COLORS = 256  # only the intro uses any beyond the first eight

# ---- attribute bits (style in the low byte; color pair packed above it) ----
A_NORMAL = 0
A_BOLD = 1 << 0
A_DIM = 1 << 1
A_REVERSE = 1 << 2
A_UNDERLINE = 1 << 3
A_STANDOUT = A_REVERSE
_PAIR_SHIFT = 8

# ---- key constants: arbitrary but distinct. leet.py only ever compares
# them against values this same module hands back from getch() ----
KEY_UP = 1001
KEY_DOWN = 1002
KEY_LEFT = 1003
KEY_RIGHT = 1004
KEY_ENTER = 1005
KEY_MOUSE = 1006
KEY_RESIZE = 1007
KEY_BACKSPACE = 1008
KEY_DC = 1009
KEY_HOME = 1010
KEY_END = 1011
KEY_PPAGE = 1012
KEY_NPAGE = 1013
KEY_BTAB = 1014

# ---- mouse button bits ----
BUTTON1_PRESSED = 1 << 0
BUTTON1_RELEASED = 1 << 1
BUTTON1_CLICKED = 1 << 2
BUTTON3_PRESSED = 1 << 3
BUTTON3_RELEASED = 1 << 4
BUTTON3_CLICKED = 1 << 5
ALL_MOUSE_EVENTS = (BUTTON1_PRESSED | BUTTON1_RELEASED | BUTTON1_CLICKED |
                     BUTTON3_PRESSED | BUTTON3_RELEASED | BUTTON3_CLICKED)
REPORT_MOUSE_POSITION = 1 << 6

_PAIRS = {0: (-1, -1)}
_current_screen = None


def start_color():
    pass


def use_default_colors():
    pass


def init_pair(n, fg, bg):
    _PAIRS[n] = (fg, bg)


def color_pair(n):
    return n << _PAIR_SHIFT


def curs_set(n):
    if _current_screen is not None:
        _current_screen.cursor_visible = bool(n)


def has_colors():
    return True


# Keys already arrive uninterpreted from the browser.
def raw():
    pass


def noraw():
    pass


def cbreak():
    pass


def mousemask(mask):
    return mask, mask


def mouseinterval(n):
    pass


def getmouse():
    """Real curses.getmouse() is a module-level call, not a stdscr method -
    it returns the event queued behind the KEY_MOUSE that getch() just
    returned. Mirror that here instead of hanging it off Screen."""
    if _current_screen is None:
        raise error("getmouse() called before any screen existed")
    return _current_screen.getmouse()


def wrapper(fn):
    """Real curses.wrapper() sets up/tears down a terminal. There's
    nothing to set up here; this exists only so `if __name__ ==
    "__main__": curses.wrapper(main)` doesn't blow up if this module
    ever gets exec'd directly instead of imported as 'leet'."""
    return fn(Screen())


class Screen:
    """Stands in for stdscr: a (char, attr) grid plus input queues."""

    def __init__(self, cols=120, rows=40):
        self.cols = cols
        self.rows = rows
        self._buf = self._blank(cols, rows)
        self._input = []  # [('key', code), ...] / [('mouse', (id,x,y,z,bstate)), ...]
        self._pending_mouse = None
        self.cursor_visible = False
        self._cursor = (0, 0)
        self.tick_ms = 80
        global _current_screen
        _current_screen = self

    @staticmethod
    def _blank(cols, rows):
        return [[(' ', 0) for _ in range(cols)] for _ in range(rows)]

    # -------- curses-alike surface used by leet.py --------

    def getmaxyx(self):
        return self.rows, self.cols

    def erase(self):
        self._buf = self._blank(self.cols, self.rows)

    def addch(self, y, x, ch, attr=0):
        if 0 <= y < self.rows and 0 <= x < self.cols:
            self._buf[y][x] = (ch, attr)

    def addstr(self, y, x, text, attr=0):
        if not (0 <= y < self.rows):
            return
        row = self._buf[y]
        for i, ch in enumerate(text):
            xi = x + i
            if xi >= self.cols:
                break
            if xi >= 0:
                row[xi] = (ch, attr)

    def addnstr(self, y, x, text, n, attr=0):
        self.addstr(y, x, text[:max(n, 0)], attr)

    def move(self, y, x):
        self._cursor = (y, x)

    def refresh(self):
        pass

    def keypad(self, flag):
        pass

    def nodelay(self, flag):
        pass

    def timeout(self, ms):
        self.tick_ms = ms  # the page paces its tick loop by this

    def get_wch(self):
        if not self._input:
            raise error("no input")
        kind, val = self._input.pop(0)
        if kind == "mouse":
            self._pending_mouse = val
            return KEY_MOUSE
        return val

    def pending(self):
        return len(self._input)

    def getch(self):
        if not self._input:
            return -1
        kind, val = self._input.pop(0)
        if kind == "mouse":
            self._pending_mouse = val
            return KEY_MOUSE
        return ord(val) if isinstance(val, str) else val

    def getmouse(self):
        if self._pending_mouse is None:
            raise error("getmouse() called without a pending mouse event")
        m = self._pending_mouse
        self._pending_mouse = None
        return m

    # -------- driven by the JS bridge, not by leet.py --------

    def resize(self, cols, rows):
        cols, rows = max(1, int(cols)), max(1, int(rows))
        if (cols, rows) != (self.cols, self.rows):
            self.cols, self.rows = cols, rows
            self._buf = self._blank(cols, rows)

    def push_key(self, code):
        self._input.append(("key", code))

    def push_mouse(self, x, y, bstate):
        event = ("mouse", (0, x, y, 0, bstate))
        if bstate == REPORT_MOUSE_POSITION and self._input and self._input[-1][0] == "mouse" \
                and self._input[-1][1][4] == REPORT_MOUSE_POSITION:
            # Coalesce: this is a pure-motion report and the queue's tail is
            # still an unconsumed pure-motion report - replace it instead of
            # growing the queue. A press or release always still lands as
            # its own entry (the bstate check above only matches motion).
            self._input[-1] = event
        else:
            self._input.append(event)

    # -------- rendering for the browser: run-length-encoded rows --------

    def render_rows(self):
        rows_out = []
        for y, row in enumerate(self._buf):
            cy, cx = self._cursor
            if self.cursor_visible and y == cy and 0 <= cx < self.cols:
                row = list(row)
                row[cx] = (row[cx][0], row[cx][1] ^ A_REVERSE)
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
        return {"cols": self.cols, "rows": self.rows, "data": rows_out}


# ---- theme: 8 ANSI-ish colors resolved to hex for the browser renderer ----
_HEX = {
    COLOR_BLACK: "#0b0f0b", COLOR_RED: "#ff5a5a", COLOR_GREEN: "#9dff9d",
    COLOR_YELLOW: "#ffd24d", COLOR_BLUE: "#6a8caf", COLOR_MAGENTA: "#ff7ad1",
    COLOR_CYAN: "#7fe6ff", COLOR_WHITE: "#eafcea",
}
# Colours past the first eight are only used by the intro: the xterm 256 cube,
# with the company blue and white pinned.
_LEVELS = (0, 95, 135, 175, 215, 255)
for _n in range(216):
    _HEX[16 + _n] = "#%02x%02x%02x" % (_LEVELS[_n // 36], _LEVELS[_n // 6 % 6], _LEVELS[_n % 6])
_HEX[21], _HEX[231] = "#0b2fd6", "#ffffff"
_DEFAULT_FG = "#9dff9d"
_DEFAULT_BG = "#060a06"


def _cell_style(attr):
    pair = attr >> _PAIR_SHIFT
    style = attr & 0xFF
    fg, bg = _PAIRS.get(pair, (-1, -1))
    fg_hex = _HEX.get(fg, _DEFAULT_FG) if fg != -1 else _DEFAULT_FG
    bg_hex = _HEX.get(bg) if bg != -1 else None
    if style & A_REVERSE:
        fg_hex, bg_hex = (bg_hex or _DEFAULT_BG), fg_hex
    return (fg_hex, bg_hex, bool(style & A_BOLD), bool(style & A_DIM), bool(style & A_UNDERLINE))
