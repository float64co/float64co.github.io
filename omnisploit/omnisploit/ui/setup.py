"""First-run screen: choose the player name and the machine's hostname."""

from __future__ import annotations

import curses

from ..profile import Profile, validate_hostname, validate_name
from .keys import normalize_key

TITLE = "o m n i s p l o i t"
INTRO = [
    "Welcome. Before you log in, pick who you are and what your machine is called.",
    "Both show up in your shell prompt and in system files. Nothing leaves your computer.",
    "Press Enter to accept the [default]. Ctrl-C quits.",
]


def run_setup(scr, defaults: Profile) -> Profile | None:
    """Prompt for the profile. Returns None if the user aborts."""
    steps = [
        ("Player name", defaults.name, validate_name),
        ("Hostname", defaults.hostname, validate_hostname),
    ]
    answers: list[str] = []
    for label, default, validate in steps:
        buf, error = "", ""
        while True:
            _draw(scr, label, default, buf, error, answers)
            try:
                k = normalize_key(scr.get_wch())
            except KeyboardInterrupt:
                return None
            except curses.error:
                continue
            if k in ("\x03", "<Esc>"):
                return None
            if k == "<CR>":
                value = buf.strip() or default
                err = validate(value)
                if err:
                    error = err
                    continue
                answers.append(value)
                break
            if k == "<BS>":
                buf, error = buf[:-1], ""
            elif k == "\x15":
                buf, error = "", ""
            elif len(k) == 1 and ord(k) >= 32 and len(buf) < 64:
                buf, error = buf + k, ""
    return Profile(answers[0], answers[1])


def _draw(scr, label: str, default: str, buf: str, error: str, answers: list[str]) -> None:
    scr.erase()
    h, w = scr.getmaxyx()

    def put(y: int, x: int, text: str, attr: int = 0) -> None:
        if 0 <= y < h and 0 <= x < w:
            try:
                scr.addnstr(y, x, text, max(w - x - 1, 0), attr)
            except curses.error:
                pass

    put(1, 2, TITLE, curses.A_BOLD)
    for i, line in enumerate(INTRO):
        put(3 + i, 2, line)
    y = 3 + len(INTRO) + 1
    for name, value in zip(("Player name", "Hostname"), answers):
        put(y, 2, f"{name}: {value}", curses.A_DIM)
        y += 1
    prompt = f"{label} [{default}]: "
    put(y, 2, prompt, curses.A_BOLD)
    put(y, 2 + len(prompt), buf)
    if error:
        put(y + 2, 2, error, curses.color_pair(2) if curses.has_colors() else curses.A_BOLD)
    try:
        curses.curs_set(1)
        scr.move(y, min(2 + len(prompt) + len(buf), w - 1))
    except curses.error:
        pass
    scr.refresh()
