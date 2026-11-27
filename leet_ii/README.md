# L.E.E.T. II

The sequel to L.E.E.T.: trade and fight among the stars, land and take the
planets, and hack pirates with exploits you work out in your ship's terminal.

**Play it on the web: https://float64co.github.io/leet_ii/**

## Play in a terminal

    python3 leet_ii.py

Needs Python 3.11+ and a terminal of at least 92x30. `leet_ii.py` is the whole
game and runs on its own.

## omnisploit

`leet_ii.py` carries a compressed copy of omnisploit, unpacked on first use of
the ship's Terminal into `~/.leet2_hack/engine-<version>/`. The `omnisploit/`
folder is the copy to edit and test; after changing it, run:

    python3 embed_omnisploit.py

## Play in a browser

Open `index.html`, or serve this folder (`python3 -m http.server 8000`).
It is a single page holding the whole game; Pyodide is fetched
from a CDN on load.

Progress is stored in the browser's localStorage under `leet2_state`, separate
from terminal saves. In the ship's Terminal, Ctrl-letter keys and paste are
passed through, except Ctrl-V (paste) and any the browser reserves.

## Rebuild the browser version

    python3 build_web.py

Re-run after changing the game or the web files (after `embed_omnisploit.py` if
omnisploit changed). Don't edit
`index.html` by hand.

- `web_curses.py` - in-browser stand-in for `curses`
- `web_bridge.py` - drives the game one tick at a time; save restore/export
- `template.html` - rendering, input, and localStorage persistence
