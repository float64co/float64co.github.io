"""
L.E.E.T. II web bridge.

By the time this runs, template.html has registered web_curses as
sys.modules['curses'] and written the game sources into APP_DIR. This repeats
leet_ii.main()'s curses setup, then drives App.step() from the page's timer
and mirrors the save files out for localStorage.
"""
import json
import sys
from pathlib import Path

APP_DIR = "/app"
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import curses  # the shim

import leet_ii

MAX_KEYS_PER_TICK = 200

SAVE_FILE = Path(leet_ii.SAVE_PATH)
HACK_ROOT = leet_ii.HACK_ROOT


def _restore(text):
    """Write saved files back into Pyodide's in-memory filesystem."""
    try:
        state = json.loads(text)
        for name, body in state.items():
            if name == "save":
                path = SAVE_FILE
            elif name.startswith("hack/") and ".." not in name:
                path = HACK_ROOT / name[len("hack/"):]
            else:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body)
    except (ValueError, AttributeError, TypeError, OSError) as e:
        print(f"could not restore saved progress: {e}")


_seed = globals().get("SEED_STATE_TEXT")
if _seed:
    _restore(_seed)

_screen = curses.Screen(cols=120, rows=40)
curses.curs_set(0)
_screen.keypad(True)
curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
curses.mouseinterval(0)
curses.start_color()
curses.use_default_colors()
leet_ii.init_space_colors()

_app = leet_ii.App(_screen)
_button_held = False


def _mode():
    return _app.game.mode if _app.phase == "game" else _app.phase


def _drain_backlog():
    """A real terminal handles each key as it arrives, but the page ticks on
    a fixed timer. Handle all but the last queued input here, without
    advancing the simulation, so typing and key repeat don't lag."""
    for _ in range(MAX_KEYS_PER_TICK):
        if _screen.pending() <= 1 or _app.phase != "game":
            return
        game = _app.game
        if game.mode == "hack":
            game.tick_once()
        elif game.mode == "ground":
            if not game.campaign.running:
                return
            key = _screen.getch()
            if key == curses.KEY_MOUSE:
                game.campaign.handle_mouse()
            else:
                game.campaign.handle_key(key)
        elif game.mode == "ground_end" or not game.running:
            return
        else:
            game._handle_key(_screen.getch())


def web_tick():
    _drain_backlog()
    _app.step()
    # omnisploit draws before reading input; redraw so the last key shows now.
    if _mode() == "hack":
        _app.game.hack_terminal.app.draw()
    return json.dumps(dict(screen=_screen.render_rows(), mode=_mode(), delay=_screen.tick_ms))


def web_min_size():
    return json.dumps({"cols": leet_ii.SPACE_MIN_TERM_W, "rows": leet_ii.SPACE_MIN_TERM_H})


def web_resize(cols, rows):
    _screen.resize(cols, rows)


def web_char(ch):
    _screen.push_key(ch)


def web_special(name):
    _screen.push_key(getattr(curses, name))


def web_mouse(kind, x, y):
    global _button_held
    mode = _mode()
    if kind == "down":
        _button_held = True
        bstate = curses.BUTTON1_PRESSED
    elif kind == "up":
        _button_held = False
        bstate = curses.BUTTON1_RELEASED
    elif kind == "right":
        if mode != "ground":
            return
        bstate = curses.BUTTON3_PRESSED
    else:
        # Hover steers the ship, but in the ground war it would drag the
        # command cursor around under the keyboard.
        if mode == "ground" and not _button_held:
            return
        bstate = curses.REPORT_MOUSE_POSITION
    if mode != "hack":
        _screen.push_mouse(x, y, bstate)


def web_read_state():
    """Every save file as one JSON object, for the page to keep."""
    state = {}
    if SAVE_FILE.is_file():
        state["save"] = SAVE_FILE.read_text()
    for path in sorted(HACK_ROOT.glob("*/*.json")):
        state[f"hack/{path.parent.name}/{path.name}"] = path.read_text()
    return json.dumps(state) if state else ""
