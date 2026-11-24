# omnisploit, in a browser

Runs the real `omnisploit` package (same engine, shell, and levels as the terminal
build -- nothing in `omnisploit/` is forked or special-cased for the web) inside
Pyodide, following the pattern from `../../cyberpunk/Terminal/`: a hand-rolled
curses shim registered into `sys.modules['curses']` before the game imports it.

## The one real difference from Terminal's build

Terminal is a real-time game: its own loop calls `getch()` on a timer and treats
"no key yet" as a normal, non-blocking answer. omnisploit is turn-based --
`ui/app.py`'s `App.loop()` and `ui/setup.py`'s `run_setup()` call `scr.get_wch()`
and genuinely **block** until a key arrives, with no `nodelay`/`timeout` anywhere.
A browser tab can't block its main thread waiting on a keystroke the way a real
terminal blocks on a syscall, so this build runs the game inside a **Web Worker**
and makes `get_wch()` really block there, via `Atomics.wait()` against a
`SharedArrayBuffer` that the main thread writes a key into on every keydown. That
needs the page to be [cross-origin isolated][coi], which needs two response
headers on the document itself that static hosts don't let you set --
`coi-serviceworker.js` intercepts the page's own request and adds them, so no
server configuration is required, but it does mean:

- **The page must be served over http(s)**, not opened as a `file://` URL
  (service workers require a real origin). `python3 -m http.server` from the
  project root (where `index.html` lives) works fine for local testing.
- **The first load reloads itself once**, right after the service worker installs,
  so the *second* load is the one that's actually cross-origin isolated.

[coi]: https://web.dev/articles/coop-coep

## Build

```sh
python3 web/build_web.py
```

Zips the `omnisploit/` package, base64-encodes it, and inlines it plus
`web_curses.py` and `web_bridge.py` into `template.html` → **`../index.html`**
(the project root, one level up from this directory -- not `web/index.html`).
Re-run this after changing any of those, or the omnisploit package itself.

`index.html` lives at the project root, alongside `coi-serviceworker.js`
(which ships unmodified there -- service workers must be fetched from a real
URL, so it can't be inlined), deliberately: the project root is the one
directory that needs to be public, so it can be copied straight into a static
host or a GitHub Pages repo (e.g. as `<user>.github.io/omnisploit/`) and just
work, without dragging `web/`'s build sources, `web/.certs/` (a **private** local
dev TLS key -- never publish it), or the Python package's own tooling along for
the ride. Nothing outside the project root's `index.html` + `coi-serviceworker.js`
needs to be reachable over the web at all.

## Serve

```sh
python3 -m http.server 8000      # from the project root
```

then open `http://localhost:8000/index.html`. `index.html` and
`coi-serviceworker.js` both need to be reachable from the same directory --
everything else (the omnisploit source, the curses shim, pyodide itself) is
either inlined or fetched from a CDN at boot.

For local HTTPS testing instead, `python3 web/serve_https.py` serves the
project root using the self-signed cert in `web/.certs/`.

## Known limitations

- **No persistence across a reload.** Save data lives only in the worker's
  in-memory filesystem for that tab's session; reloading the page starts a fresh
  game, same as `omnisploit --new-game`. (Mounting IDBFS was considered, but a
  background sync can't actually run while the worker is parked in
  `Atomics.wait()` between keystrokes -- the thread is genuinely blocked, not
  just idle -- so it wouldn't reliably persist anything, and shipping something
  that *sometimes* saves is worse than clearly not saving at all.)
- **No mouse support** -- omnisploit's UI never uses one, so the shim doesn't
  implement `getmouse`/`mousemask` either.
- Only single-codepoint keyboard input is handled (`e.key` on `keydown`); IME
  composition and multi-codepoint input methods aren't supported.
- First load fetches Pyodide (~12 MB) from `cdn.jsdelivr.net`; cached by the
  browser after that. To run fully offline, download a Pyodide release and
  change `PYODIDE_BASE` in `template.html`'s worker script, as noted in
  Terminal's own build for the same reason.

## Files

| File | Role |
|---|---|
| `web_curses.py` | The curses shim: implements exactly the surface omnisploit's UI calls (`addnstr`, `erase`, `get_wch`, `getmaxyx`, `keypad`, `move`, `refresh`, color/attr constants), with `get_wch()`/`refresh()` calling out to JS (`js.waitForEvent`/`js.postRenderJSON`) instead of a real terminal. |
| `web_bridge.py` | Points `OMNISPLOIT_CONFIG_DIR` at the in-memory save directory, then calls the real `omnisploit.ui.app.run()`. |
| `template.html` | The page: DOM/CSS for the terminal grid, the cross-origin-isolation bootstrap, the main-thread input/render glue, and the worker script (as a `<script type="text/plain">` block, since workers can't read the page's own tags). |
| `coi-serviceworker.js` | Adds `Cross-Origin-Opener-Policy`/`Cross-Origin-Embedder-Policy` to the document response so `SharedArrayBuffer` is available, without needing server config. Ships unmodified next to `index.html`. |
| `build_web.py` | Inlines `web_curses.py`, `web_bridge.py`, and a zip of `omnisploit/` into `template.html` → `index.html`. |
