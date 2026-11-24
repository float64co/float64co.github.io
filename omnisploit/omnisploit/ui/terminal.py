"""Scrollback + readline-style input line. Drawing is done with a curses window."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

NORMAL, PROMPT, ERROR = 0, 1, 2
MAX_LINES = 3000


@dataclass
class Line:
    text: str
    prompt_len: int = 0
    style: int = NORMAL


def sanitize(text: str) -> str:
    out = []
    for ch in text.replace("\r\n", "\n").replace("\r", ""):
        if ch == "\t":
            out.append("    ")
        elif ch == "\n" or (ch >= " " and ch != "\x7f" and not ("\x80" <= ch <= "\x9f")):
            out.append(ch)
        else:
            out.append(".")
    return "".join(out)


def wrap(text: str, width: int) -> list[tuple[int, str]]:
    if not text:
        return [(0, "")]
    return [(i, text[i : i + width]) for i in range(0, len(text), width)]


class Terminal:
    def __init__(self, complete: Callable[[str], tuple[int, list[str]]]) -> None:
        self.lines: list[Line] = []
        self.open = False  # last line has no trailing newline yet
        self.buf = ""
        self.pos = 0
        self.history: list[str] = []
        self.hist_idx = 0
        self.scroll = 0
        self.complete = complete
        self._draft = ""

    # ---------------------------------------------------------------- output
    def write(self, text: str, style: int = NORMAL) -> None:
        text = sanitize(text)
        if not text:
            return
        ends = text.endswith("\n")
        parts = text[:-1].split("\n") if ends else text.split("\n")
        for i, part in enumerate(parts):
            if i == 0 and self.open and self.lines:
                self.lines[-1].text += part
            else:
                self.lines.append(Line(part, 0, style))
        self.open = not ends
        del self.lines[:-MAX_LINES]
        self.scroll = 0

    def clear(self) -> None:
        self.lines.clear()
        self.open = False
        self.scroll = 0

    def echo(self, prompt: str, command: str) -> None:
        self.open = False
        self.lines.append(Line(sanitize(prompt + command), len(prompt), NORMAL))
        self.scroll = 0

    # ----------------------------------------------------------------- input
    def key(self, k: str, prompt: str, secret: bool = False, eof: str = "exit") -> str | None:
        """Feed a key; returns a submitted line, or None.

        ``secret`` (a password prompt): the typed text is never shown, never added to the history,
        and the history and completion keys do nothing.

        ``eof``: the line Ctrl-D submits when the buffer is empty (plain bash: "exit"; a session
        can ask for something else, e.g. the Python prompt wants "exit()" so it is actually called).
        """
        if k == "<CR>":
            line = self.buf
            if not secret and line.strip() and (not self.history or self.history[-1] != line):
                self.history.append(line)
            self.hist_idx = len(self.history)
            self.buf, self.pos = "", 0
            self.echo(prompt, "" if secret else line)
            return line
        if secret and k in ("<Up>", "<Down>", "<Tab>"):
            return None
        if k == "\x03":  # Ctrl-C
            self.echo(prompt, ("" if secret else self.buf) + "^C")
            self.buf, self.pos = "", 0
            self.hist_idx = len(self.history)
        elif k == "\x04":  # Ctrl-D
            if not self.buf:
                self.echo(prompt, eof)
                return eof
            self.buf = self.buf[: self.pos] + self.buf[self.pos + 1 :]
        elif k == "\x0c":
            self.clear()
        elif k == "<BS>":
            if self.pos:
                self.buf = self.buf[: self.pos - 1] + self.buf[self.pos :]
                self.pos -= 1
        elif k == "<Del>":
            self.buf = self.buf[: self.pos] + self.buf[self.pos + 1 :]
        elif k == "<Left>":
            self.pos = max(0, self.pos - 1)
        elif k == "<Right>":
            self.pos = min(len(self.buf), self.pos + 1)
        elif k in ("<Home>", "\x01"):
            self.pos = 0
        elif k in ("<End>", "\x05"):
            self.pos = len(self.buf)
        elif k == "\x0b":
            self.buf = self.buf[: self.pos]
        elif k == "\x15":
            self.buf, self.pos = self.buf[self.pos :], 0
        elif k == "\x17":
            head = self.buf[: self.pos].rstrip()
            cut = head.rfind(" ") + 1
            self.buf = self.buf[:cut] + self.buf[self.pos :]
            self.pos = cut
        elif k == "<Up>":
            if self.hist_idx == len(self.history):
                self._draft = self.buf
            if self.hist_idx > 0:
                self.hist_idx -= 1
                self.buf = self.history[self.hist_idx]
                self.pos = len(self.buf)
        elif k == "<Down>":
            if self.hist_idx < len(self.history):
                self.hist_idx += 1
                self.buf = self._draft if self.hist_idx == len(self.history) else self.history[self.hist_idx]
                self.pos = len(self.buf)
        elif k == "<PgUp>":
            self.scroll += 10
        elif k == "<PgDn>":
            self.scroll = max(0, self.scroll - 10)
        elif k == "<Tab>":
            self._tab(prompt)
        elif len(k) == 1 and ord(k) >= 32:
            self.buf = self.buf[: self.pos] + k + self.buf[self.pos :]
            self.pos += 1
            self.scroll = 0
        return None

    def _tab(self, prompt: str) -> None:
        start, cands = self.complete(self.buf[: self.pos])
        if not cands:
            return
        word = self.buf[start : self.pos]
        common = cands[0]
        for c in cands[1:]:
            while not c.startswith(common):
                common = common[:-1]
        if len(cands) == 1:
            suffix = "" if cands[0].endswith("/") else " "
            new = cands[0] + suffix
        elif len(common) > len(word):
            new = common
        else:
            self.echo(prompt, self.buf)
            self.write("  ".join(cands) + "\n")
            return
        self.buf = self.buf[:start] + new + self.buf[self.pos :]
        self.pos = start + len(new)

    # -------------------------------------------------------------- rendering
    def layout(self, prompt: str, width: int, height: int, secret: bool = False):
        """Return (visible_rows, cursor_row, cursor_col); rows are (Line, start, chunk)."""
        width = max(width, 2)
        rows: list[tuple[Line, int, str]] = []
        for line in self.lines:
            rows.extend((line, s, c) for s, c in wrap(line.text, width))
        shown = "" if secret else self.buf  # a password is never drawn
        inp = Line(prompt + shown + " ", len(prompt), NORMAL)
        input_rows = [(inp, s, c) for s, c in wrap(inp.text, width)]
        rows.extend(input_rows)
        self.scroll = max(0, min(self.scroll, max(len(rows) - height, 0)))
        end = len(rows) - self.scroll
        visible = rows[max(0, end - height) : end]
        cur = len(prompt) + (0 if secret else self.pos)
        crow = ccol = None
        if self.scroll == 0:
            for i, (ln, s, c) in enumerate(visible):
                if ln is inp and s <= cur < s + width:
                    crow, ccol = i, cur - s
        return visible, crow, ccol
