"""
omnisploit web bridge.

Runs entirely inside the worker thread. By the time this module is exec'd,
worker-template.js has already: registered web_curses as sys.modules['curses'],
unpacked the omnisploit package into the Pyodide filesystem and put it on
sys.path, and mounted IDBFS at the save-data directory. This just points
omnisploit's save path at that directory and starts the real game -- the
same omnisploit.ui.app.run() the terminal build calls, untouched.

web_start() blocks for the lifetime of the game (it's turn-based: the whole
call stack sits inside curses.Screen.get_wch()'s Atomics.wait() between
keystrokes). That's fine -- it's the only thing this worker does.
"""

import os

os.environ.setdefault("OMNISPLOIT_CONFIG_DIR", "/omni-config")
os.makedirs(os.environ["OMNISPLOIT_CONFIG_DIR"], exist_ok=True)


def web_start() -> None:
    from omnisploit.ui.app import run

    run()
