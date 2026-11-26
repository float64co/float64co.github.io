#!/usr/bin/env python3
"""
Build the browser version of L.E.E.T. II: index.html, one self-contained
page (Pyodide itself is fetched from a CDN).

    python3 build_web.py

Sources are read straight from the project, so re-run after changing the game
or the web files. After changing omnisploit, run embed_omnisploit.py first.
"""

import json
from pathlib import Path

import embed_omnisploit

ROOT = Path(__file__).resolve().parent
GAME_MODULES = ("leet_ii.py",)  # omnisploit is embedded in it


def collect():
    """Map of path-inside-the-page's-filesystem -> source text."""
    files = {name: (ROOT / name).read_text() for name in GAME_MODULES}
    files["web_curses.py"] = (ROOT / "web_curses.py").read_text()
    return files


def embed(text):
    # "</script" inside an inline script would end it early.
    return text.replace("</", "<\\/")


def main():
    if embed_omnisploit.SOURCE.is_dir() and not embed_omnisploit.is_current():
        raise SystemExit("embedded omnisploit is out of date: run embed_omnisploit.py first")
    files = collect()
    bridge = (ROOT / "web_bridge.py").read_text()
    for name, source in list(files.items()) + [("web_bridge.py", bridge)]:
        if name.endswith(".py"):
            compile(source, name, "exec")
    html = (ROOT / "template.html").read_text()
    # Bridge first: the JSON blob must not be scanned for placeholders.
    html = html.replace("__PY_BRIDGE__", json.dumps(bridge).replace("</", "<\\/"))
    html = html.replace("__PY_FILES__", embed(json.dumps(files)))
    (ROOT / "index.html").write_text(html)
    print(f"index.html written ({len(html):,} bytes, {len(files)} files)")


if __name__ == "__main__":
    main()
