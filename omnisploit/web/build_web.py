#!/usr/bin/env python3
"""
Build the browser version of omnisploit.

Zips the omnisploit package (unmodified -- the actual game, same as the terminal
build imports), base64-encodes it, and inlines it alongside web_curses.py and
web_bridge.py into template.html -> ../index.html (the project root, not this
directory -- so the project root can be copied straight into a static host or a
GitHub Pages repo and just work: index.html and coi-serviceworker.js are the only
two files that need to be public). coi-serviceworker.js ships unmodified at the
project root alongside it (service workers must be fetched from a real URL, so it
can't be inlined). Re-run after changing any of web_curses.py, web_bridge.py,
template.html, or the omnisploit package itself:

    python3 web/build_web.py
"""

import base64
import io
import pathlib
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB = pathlib.Path(__file__).resolve().parent


def zip_package() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted((ROOT / "omnisploit").rglob("*")):
            if "__pycache__" in path.parts:
                continue
            if path.suffix not in (".py", ".toml"):
                continue
            zf.write(path, path.relative_to(ROOT).as_posix())
    return buf.getvalue()


def main() -> None:
    curses_shim = (WEB / "web_curses.py").read_text()
    bridge = (WEB / "web_bridge.py").read_text()
    pkg_b64 = base64.b64encode(zip_package()).decode()

    for text, name in ((curses_shim, "web_curses.py"), (bridge, "web_bridge.py")):
        if "</script" in text:
            raise SystemExit(f"{name} contains '</script', which would break inlining")

    html = (WEB / "template.html").read_text()
    html = (
        html.replace("__PY_CURSES__", curses_shim)
        .replace("__PY_BRIDGE__", bridge)
        .replace("__PKG_B64__", pkg_b64)
    )
    out = ROOT / "index.html"
    out.write_text(html)
    print(
        f"{out} written ({len(html):,} bytes; web_curses.py {len(curses_shim):,}, "
        f"web_bridge.py {len(bridge):,}, omnisploit package {len(pkg_b64):,} b64 bytes)"
    )
    print(f"Serve {ROOT} over http(s) and open index.html -- see web/README.md.")


if __name__ == "__main__":
    main()
