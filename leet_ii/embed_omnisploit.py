#!/usr/bin/env python3
"""
Copy the omnisploit package (code and levels) into leet_ii.py.

    python3 embed_omnisploit.py           refresh the embedded copy
    python3 embed_omnisploit.py --check   exit non-zero if it is out of date

omnisploit/ remains the copy to edit and test. leet_ii.py only ever runs the
embedded one, so re-run this after changing anything there.
"""

import base64
import hashlib
import json
import sys
import textwrap
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "omnisploit"
TARGET = ROOT / "leet_ii.py"
BEGIN = "# --- BEGIN EMBEDDED OMNISPLOIT (written by embed_omnisploit.py: do not edit) ---\n"
END = "# --- END EMBEDDED OMNISPLOIT ---\n"


def payload():
    files = {path.relative_to(SOURCE).as_posix(): path.read_text(encoding="utf-8")
             for path in sorted((SOURCE / "omnisploit").rglob("*"))
             if path.suffix in (".py", ".toml")}
    if not files:
        raise SystemExit(f"no omnisploit sources under {SOURCE}")
    return json.dumps(files, sort_keys=True).encode()


def split_target():
    text = TARGET.read_text(encoding="utf-8")
    if text.count(BEGIN) != 1 or text.count(END) != 1:
        raise SystemExit(f"{TARGET.name} must contain the BEGIN and END marker lines exactly once")
    head, rest = text.split(BEGIN)
    block, tail = rest.split(END)
    return head, block, tail


def is_current():
    digest = hashlib.sha256(payload()).hexdigest()[:16]
    return f'OMNI_DIGEST = "{digest}"\n' in split_target()[1]


def main():
    if "--check" in sys.argv[1:]:
        if not is_current():
            raise SystemExit("embedded omnisploit is out of date: run embed_omnisploit.py")
        print("embedded omnisploit is current")
        return
    data = payload()
    head, _, tail = split_target()
    blob = base64.b85encode(zlib.compress(data, 9)).decode()  # no quotes or backslashes
    block = (f'OMNI_DIGEST = "{hashlib.sha256(data).hexdigest()[:16]}"\n'
             f'OMNI_BLOB = """\n' + "\n".join(textwrap.wrap(blob, 100, break_on_hyphens=False)) + '\n"""\n')
    TARGET.write_text(head + BEGIN + block + END + tail, encoding="utf-8")
    print(f"embedded {len(json.loads(data))} files: {len(data):,} bytes -> {len(blob):,}")


if __name__ == "__main__":
    main()
