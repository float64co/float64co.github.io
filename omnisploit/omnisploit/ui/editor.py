"""A small modal editor with vim-style keys. Pure logic: no curses imports.

Keys are strings: single characters, or names like ``<Esc>``, ``<CR>``, ``<BS>``,
``<Up>``. Rendering lives in ``ui/app.py``.

Supported: normal / insert / visual (v, V) / command-line modes, counts, an
unnamed register, undo/redo, ``.`` repeat, search, ``:s`` substitution, and
multiple buffers (:e :ls :bn :bp :b N :bd :wa :qa).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from ..engine.vfs import VFSError

SaveFn = Callable[[str, bytes], None]
LoadFn = Callable[[str], "tuple[str | None, str, bool]"]  # name -> (abs_path, text, exists)
EventFn = Callable[[str], None]

Snapshot = tuple[list[str], int, int]
Register = tuple[str, list[str]]  # ("line"|"char", payload)

MOTION_KEYS = {
    "h", "<Left>", "<BS>", "l", "<Right>", " ", "j", "<Down>", "<CR>", "k", "<Up>",
    "0", "<Home>", "^", "$", "<End>", "w", "b", "G", "<PgDn>", "\x04", "<PgUp>", "\x15",
}


def _cls(c: str) -> int:
    if c in " \t":
        return 0
    return 1 if (c.isalnum() or c == "_") else 2


@dataclass
class Buffer:
    path: str | None
    name: str
    lines: list[str]
    cy: int = 0
    cx: int = 0
    top: int = 0
    left: int = 0
    undo: list[Snapshot] = field(default_factory=list)
    redo: list[Snapshot] = field(default_factory=list)
    dirty: bool = False


def _split_text(text: str) -> list[str]:
    lines = text.replace("\t", "    ").split("\n")
    if len(lines) > 1 and lines[-1] == "":
        lines.pop()
    return lines or [""]


class Editor:
    def __init__(
        self,
        path: str | None,
        name: str,
        text: str,
        save: SaveFn,
        exists: bool = True,
        loader: LoadFn | None = None,
        on_event: EventFn | None = None,
    ) -> None:
        self._save = save
        self._loader = loader
        self._on_event = on_event
        self.events: set[str] = set()
        self.bufs: list[Buffer] = [Buffer(path, name, _split_text(text))]
        self.bi = 0
        self.alt: int | None = None
        # state of the *current* buffer lives in these attributes; _stash()/_restore() swap them
        self.path, self.name = path, name
        self.lines = self.bufs[0].lines
        self.cy = self.cx = self.top = self.left = 0
        self.undo: list[Snapshot] = []
        self.redo: list[Snapshot] = []
        self.dirty = False
        # modes and editing state
        self.mode = "NORMAL"  # NORMAL | INSERT | COMMAND | VISUAL | VLINE
        self.cmdline = ""
        self.count = 0
        self.pending = ""
        self.reg: Register | None = None
        self.vstart = (0, 0)
        self.done = False
        self.number = False
        self.search = ""
        self.search_back = False
        self.opts = {"ignorecase": False, "smartcase": False, "hlsearch": False, "wrapscan": True}
        self.height = 20
        self.last_change: list[str] | None = None
        self._rec: list[str] = []
        self._changed = False
        self._replaying = False
        if not name:
            self.msg = ""
        elif exists:
            self.msg = f'"{name}" {len(self.lines)}L, {len(text.encode())}B'
        else:
            self.msg = f'"{name}" [New]'

    # ------------------------------------------------------------- helpers
    @property
    def line(self) -> str:
        return self.lines[self.cy]

    def _ev(self, name: str) -> None:
        self.events.add(name)
        if self._on_event:
            self._on_event(name)

    def _snap(self) -> None:
        self.undo.append((list(self.lines), self.cy, self.cx))
        del self.undo[:-200]
        self.redo.clear()
        self.dirty = True
        self._changed = True

    def _clamp(self) -> None:
        self.cy = max(0, min(self.cy, len(self.lines) - 1))
        limit = len(self.line) if self.mode == "INSERT" else max(len(self.line) - 1, 0)
        self.cx = max(0, min(self.cx, limit))

    def _first_nonblank(self) -> int:
        return len(self.line) - len(self.line.lstrip())

    # ------------------------------------------------------------- buffers
    def _stash(self) -> None:
        b = self.bufs[self.bi]
        b.path, b.name, b.lines = self.path, self.name, self.lines
        b.cy, b.cx, b.top, b.left = self.cy, self.cx, self.top, self.left
        b.undo, b.redo, b.dirty = self.undo, self.redo, self.dirty

    def _restore(self, i: int) -> None:
        self.bi = i
        b = self.bufs[i]
        self.path, self.name, self.lines = b.path, b.name, b.lines
        self.cy, self.cx, self.top, self.left = b.cy, b.cx, b.top, b.left
        self.undo, self.redo, self.dirty = b.undo, b.redo, b.dirty
        self.mode, self.pending, self.count = "NORMAL", "", 0

    def _switch(self, i: int) -> None:
        if i == self.bi:
            return
        self._stash()
        self.alt = self.bi
        self._restore(i)
        self._clamp()
        self.msg = f'"{self.name or "[No Name]"}" {len(self.lines)}L'

    def _buffer_label(self, i: int) -> str:
        b = self.bufs[i]
        name = (self.name if i == self.bi else b.name) or "[No Name]"
        dirty = self.dirty if i == self.bi else b.dirty
        flag = "%" if i == self.bi else ("#" if i == self.alt else " ")
        return f'{i + 1} {flag}{"+" if dirty else " "} "{name}"'

    def _edit(self, name: str) -> None:
        if not name:
            self.msg = "E32: No file name"
            return
        if self._loader is None:
            self.msg = "E212: Can't open file"
            return
        try:
            path, text, exists = self._loader(name)
        except VFSError:
            self.msg = f"E484: Can't open file {name}"
            return
        self._stash()
        for i, b in enumerate(self.bufs):
            if path and b.path == path:
                self._switch(i)
                return
        self.bufs.append(Buffer(path, name, _split_text(text)))
        self.alt = self.bi
        self._restore(len(self.bufs) - 1)
        self._ev("vim:buffers")
        self.msg = f'"{name}" {len(self.lines)}L, {len(text.encode())}B' if exists else f'"{name}" [New]'

    def _close_buffer(self) -> None:
        """Drop the current buffer; finish the editor when it was the last one."""
        self._stash()
        if len(self.bufs) == 1:
            self.done = True
            return
        gone = self.bi
        self.bufs.pop(gone)
        target = self.alt if self.alt is not None and self.alt != gone else max(gone - 1, 0)
        if target > gone:
            target -= 1
        self.alt = None
        self._restore(min(target, len(self.bufs) - 1))
        self._clamp()

    def _dirty_buffers(self) -> list[int]:
        self._stash()
        return [i for i, b in enumerate(self.bufs) if b.dirty]

    def _write_all(self) -> bool:
        self._stash()
        for b in self.bufs:
            if b.dirty and b.path:
                try:
                    self._save(b.path, ("\n".join(b.lines) + "\n").encode())
                except VFSError:
                    self.msg = f"E212: Can't open file for writing ({b.name})"
                    return False
                b.dirty = False
        self._restore(self.bi)
        self._ev("vim:write")
        return True

    # ---------------------------------------------------------------- input
    def key(self, k: str) -> None:
        idle = self.mode == "NORMAL" and not self.pending and not self.count and not self._rec
        if not self._replaying:
            if idle:
                self._changed = False
            if self.mode in ("NORMAL", "INSERT"):
                self._rec.append(k)
        if self.mode == "INSERT":
            self._insert(k)
        elif self.mode == "COMMAND":
            self._command(k)
        elif self.mode in ("VISUAL", "VLINE"):
            self._visual(k)
        else:
            self._normal(k)
        self._clamp()
        if not self._replaying and self.mode == "NORMAL" and not self.pending and not self.count and self._rec:
            if self._changed and self._rec[0] not in (":", "/", ".", "u", "\x12", "v", "V"):
                self.last_change = list(self._rec)
            self._rec = []
        elif not self._replaying and self.mode in ("VISUAL", "VLINE"):
            self._rec = []  # visual-mode edits are not repeatable with '.'

    # --------------------------------------------------------- insert mode
    def _insert(self, k: str) -> None:
        if k == "<Esc>" or k == "\x03":
            self.mode = "NORMAL"
            self.cx = max(self.cx - 1, 0)
        elif k == "<CR>":
            indent = re.match(r"\s*", self.line).group()
            rest = self.line[self.cx :]
            self.lines[self.cy] = self.line[: self.cx]
            self.lines.insert(self.cy + 1, indent + rest)
            self.cy += 1
            self.cx = len(indent)
        elif k == "<BS>":
            if self.cx > 0:
                self.lines[self.cy] = self.line[: self.cx - 1] + self.line[self.cx :]
                self.cx -= 1
            elif self.cy > 0:
                prev = self.lines[self.cy - 1]
                self.cx = len(prev)
                self.lines[self.cy - 1] = prev + self.line
                del self.lines[self.cy]
                self.cy -= 1
        elif k == "<Del>":
            if self.cx < len(self.line):
                self.lines[self.cy] = self.line[: self.cx] + self.line[self.cx + 1 :]
            elif self.cy < len(self.lines) - 1:
                self.lines[self.cy] += self.lines.pop(self.cy + 1)
        elif k == "<Tab>":
            self.lines[self.cy] = self.line[: self.cx] + "    " + self.line[self.cx :]
            self.cx += 4
        elif k in ("<Left>", "<Right>", "<Up>", "<Down>", "<Home>", "<End>"):
            self._arrow(k)
        elif len(k) == 1 and ord(k) >= 32:
            self.lines[self.cy] = self.line[: self.cx] + k + self.line[self.cx :]
            self.cx += 1

    def _arrow(self, k: str) -> None:
        if k == "<Left>":
            self.cx -= 1
        elif k == "<Right>":
            self.cx += 1
        elif k == "<Up>":
            self.cy -= 1
        elif k == "<Down>":
            self.cy += 1
        elif k == "<Home>":
            self.cx = 0
        elif k == "<End>":
            self.cx = len(self.line)

    def _enter_insert(self, snap: bool = True) -> None:
        if snap:
            self._snap()
        self.mode = "INSERT"

    # -------------------------------------------------------------- motions
    def _motion(self, k: str, n: int, has_count: bool) -> bool:
        """Apply a cursor motion if k is one. Shared by normal and visual modes."""
        if k not in MOTION_KEYS:
            return False
        if k in ("h", "<Left>", "<BS>"):
            self.cx -= n
        elif k in ("l", "<Right>", " "):
            self.cx += n
        elif k in ("j", "<Down>", "<CR>"):
            self.cy += n
        elif k in ("k", "<Up>"):
            self.cy -= n
        elif k in ("0", "<Home>"):
            self.cx = 0
        elif k == "^":
            self.cx = self._first_nonblank()
        elif k in ("$", "<End>"):
            self.cx = len(self.line)
        elif k == "w":
            for _ in range(n):
                self._word_fwd()
        elif k == "b":
            for _ in range(n):
                self._word_back()
        elif k == "G":
            self.cy = max(0, min((n - 1) if has_count else len(self.lines) - 1, len(self.lines) - 1))
            self.cx = self._first_nonblank()
        elif k in ("<PgDn>", "\x04"):
            self.cy += max(self.height // 2, 1)
        elif k in ("<PgUp>", "\x15"):
            self.cy -= max(self.height // 2, 1)
        return True

    # --------------------------------------------------------- normal mode
    def _normal(self, k: str) -> None:
        if self.pending:
            return self._pending(k)
        if k.isdigit() and (k != "0" or self.count):
            self.count = self.count * 10 + int(k)
            return
        has_count = bool(self.count)
        n = self.count or 1
        self.count = 0
        self.msg = ""

        if self._motion(k, n, has_count):
            return
        if k == "x":
            if self.line:
                self._snap()
                end = min(self.cx + n, len(self.line))
                self.reg = ("char", [self.line[self.cx : end]])
                self.lines[self.cy] = self.line[: self.cx] + self.line[end:]
        elif k == "X":
            if self.cx > 0:
                self._snap()
                start = max(self.cx - n, 0)
                self.reg = ("char", [self.line[start : self.cx]])
                self.lines[self.cy] = self.line[:start] + self.line[self.cx :]
                self.cx = start
        elif k == "D":
            self._snap()
            self.reg = ("char", [self.line[self.cx :]])
            self.lines[self.cy] = self.line[: self.cx]
        elif k == "C":
            self._enter_insert()
            self.reg = ("char", [self.line[self.cx :]])
            self.lines[self.cy] = self.line[: self.cx]
        elif k == "J":
            self._snap()
            for _ in range(n):
                if self.cy < len(self.lines) - 1:
                    nxt = self.lines.pop(self.cy + 1).lstrip()
                    self.cx = len(self.line)
                    self.lines[self.cy] = self.line.rstrip() + (" " if nxt else "") + nxt
        elif k == "i":
            self._enter_insert()
        elif k == "a":
            self._enter_insert()
            self.cx += 1 if self.line else 0
        elif k == "I":
            self._enter_insert()
            self.cx = self._first_nonblank()
        elif k == "A":
            self._enter_insert()
            self.cx = len(self.line)
        elif k in ("o", "O"):
            self._enter_insert()
            indent = re.match(r"\s*", self.line).group()
            if k == "o":
                self.cy += 1
            self.lines.insert(self.cy, indent)
            self.cx = len(indent)
        elif k in ("p", "P"):
            self._paste(k == "p", n)
        elif k == "u":
            self._undo(self.undo, self.redo, "oldest change")
        elif k == "\x12":
            self._undo(self.redo, self.undo, "newest change")
        elif k == ".":
            self._repeat()
        elif k in ("v", "V"):
            self.mode = "VISUAL" if k == "v" else "VLINE"
            self.vstart = (self.cy, self.cx)
            self._ev("vim:visual")
        elif k in (":", "/", "?"):
            self.mode, self.cmdline = "COMMAND", k
        elif k in ("n", "N"):
            self._find(self.search, self.search_back != (k == "N"))
        elif k in ("*", "#"):
            self._search_word(k == "#")
        elif k == "\x1e":  # Ctrl-^
            if self.alt is not None and self.alt < len(self.bufs):
                self._switch(self.alt)
        elif k in ("d", "y", "c", "g", "r", "Z", ">", "<"):
            self.pending = k
            self.count = n if has_count else 0

    def _repeat(self) -> None:
        if not self.last_change:
            return
        self._replaying = True
        try:
            for key in list(self.last_change):
                self.key(key)
        finally:
            self._replaying = False
        if self.mode == "INSERT":  # a replay that ended mid-insert (should not happen); be safe
            self.mode = "NORMAL"
        self._ev("vim:repeat")

    def _pending(self, k: str) -> None:
        p, self.pending = self.pending, ""
        n = self.count or 1
        self.count = 0
        if p == "r":
            if len(k) == 1 and ord(k) >= 32 and self.cx + n <= len(self.line):
                self._snap()
                self.lines[self.cy] = self.line[: self.cx] + k * n + self.line[self.cx + n :]
                self.cx += n - 1
        elif p == "g" and k == "g":
            self.cy = n - 1 if n > 1 else 0
            self.cx = self._first_nonblank()
        elif p == "Z":
            if k == "Z":
                self._ex("wq")
            elif k == "Q":
                self._ex("q!")
        elif p in ("d", "y", "c"):
            if k == p:
                self._line_op(p, n)
            elif k == "w":
                self._word_op(p, n)
        elif p in "<>" and k == p:
            self._snap()
            for i in range(self.cy, min(self.cy + n, len(self.lines))):
                self.lines[i] = self._shift(self.lines[i], p == ">")

    @staticmethod
    def _shift(line: str, right: bool) -> str:
        return "    " + line if right else re.sub(r"^ {1,4}", "", line)

    def _line_op(self, op: str, n: int) -> None:
        end = min(self.cy + n, len(self.lines))
        block = self.lines[self.cy : end]
        self.reg = ("line", list(block))
        if op == "y":
            return
        self._snap()
        if op == "c":
            indent = re.match(r"\s*", self.line).group()
            self.lines[self.cy : end] = [indent]
            self.cx = len(indent)
            self.mode = "INSERT"
            return
        del self.lines[self.cy : end]
        if not self.lines:
            self.lines = [""]
        self.cy = min(self.cy, len(self.lines) - 1)
        self.cx = self._first_nonblank()

    def _word_op(self, op: str, n: int) -> None:
        """dw / yw / cw, confined to the current line (like vim for the last word)."""
        line, sx = self.line, self.cx
        end = sx
        for _ in range(n):
            if end < len(line) and _cls(line[end]) != 0:
                c = _cls(line[end])
                while end < len(line) and _cls(line[end]) == c:
                    end += 1
            if op != "c":  # cw leaves the whitespace after the word alone
                while end < len(line) and _cls(line[end]) == 0:
                    end += 1
        self.reg = ("char", [line[sx:end]])
        if op == "y":
            return
        self._snap()
        self.lines[self.cy] = line[:sx] + line[end:]
        if op == "c":
            self.mode = "INSERT"

    def _paste(self, after: bool, n: int) -> None:
        if not self.reg:
            return
        kind, payload = self.reg
        self._snap()
        if kind == "line":
            at = self.cy + 1 if after else self.cy
            self.lines[at:at] = list(payload) * n
            self.cy = at
            self.cx = self._first_nonblank()
            return
        at = self.cx + 1 if after and self.line else self.cx
        if len(payload) == 1:
            text = payload[0] * n
            self.lines[self.cy] = self.line[:at] + text + self.line[at:]
            self.cx = at + max(len(text) - 1, 0)
            return
        head, tail = self.line[:at], self.line[at:]
        new = [head + payload[0]] + payload[1:-1] + [payload[-1] + tail]
        self.lines[self.cy : self.cy + 1] = new
        self.cy += len(new) - 1
        self.cx = max(len(payload[-1]) - 1, 0)

    def _undo(self, src: list, dst: list, what: str) -> None:
        if not src:
            self.msg = f"Already at {what}"
            return
        dst.append((list(self.lines), self.cy, self.cx))
        self.lines, self.cy, self.cx = src.pop()
        self.dirty = True

    # -------------------------------------------------------- visual mode
    def _selection(self) -> tuple[int, int, int, int]:
        a, b = self.vstart, (self.cy, self.cx)
        if a > b:
            a, b = b, a
        return a[0], a[1], b[0], b[1]

    def selected_span(self, y: int) -> tuple[int, int] | None:
        """Columns [x1, x2) of line y that are highlighted, for the renderer."""
        if self.mode not in ("VISUAL", "VLINE"):
            return None
        ay, ax, by, bx = self._selection()
        if not ay <= y <= by:
            return None
        n = len(self.lines[y])
        if self.mode == "VLINE":
            return 0, max(n, 1)
        start = ax if y == ay else 0
        end = bx + 1 if y == by else n + 1
        return start, max(end, start + 1)

    def _visual(self, k: str) -> None:
        if self.pending == "g":
            self.pending = ""
            if k == "g":
                self.cy, self.cx = 0, 0
            return
        if k in ("<Esc>", "\x03"):
            self.mode = "NORMAL"
            return
        if k.isdigit() and (k != "0" or self.count):
            self.count = self.count * 10 + int(k)
            return
        has_count = bool(self.count)
        n = self.count or 1
        self.count = 0
        if self._motion(k, n, has_count):
            return
        if k in ("v", "V"):
            want = "VISUAL" if k == "v" else "VLINE"
            self.mode = "NORMAL" if self.mode == want else want
        elif k == "o":
            (self.cy, self.cx), self.vstart = self.vstart, (self.cy, self.cx)
        elif k == "g":
            self.pending = "g"
        elif k in ("d", "x", "X", "D", "y", "c", "s", ">", "<", "J"):
            self._visual_op(k)

    def _visual_op(self, k: str) -> None:
        ay, ax, by, bx = self._selection()
        linewise = self.mode == "VLINE" or k in ("X", "D")
        self._ev("vim:visual-op")
        self.mode = "NORMAL"
        if k in (">", "<"):
            self._snap()
            for i in range(ay, by + 1):
                self.lines[i] = self._shift(self.lines[i], k == ">")
            self.cy, self.cx = ay, self._first_nonblank()
            return
        if k == "J":
            self._snap()
            for _ in range(max(by - ay, 1)):
                if ay < len(self.lines) - 1:
                    nxt = self.lines.pop(ay + 1).lstrip()
                    self.lines[ay] = self.lines[ay].rstrip() + (" " if nxt else "") + nxt
            self.cy, self.cx = ay, 0
            return
        if linewise:
            self.reg = ("line", self.lines[ay : by + 1])
            self.cy, self.cx = ay, 0
            if k == "y":
                return
            self._snap()
            del self.lines[ay : by + 1]
            if not self.lines:
                self.lines = [""]
            self.cy = min(ay, len(self.lines) - 1)
            if k in ("c", "s"):
                self.lines.insert(self.cy, "")
                self.mode = "INSERT"
            self.cx = 0
            return
        # charwise, inclusive of the character under the cursor
        first, last = self.lines[ay], self.lines[by]
        bx = min(bx, len(last) - 1) if last else -1
        if ay == by:
            seg = [first[ax : bx + 1]]
        else:
            seg = [first[ax:]] + self.lines[ay + 1 : by] + [last[: bx + 1]]
        self.reg = ("char", seg)
        self.cy, self.cx = ay, ax
        if k == "y":
            return
        self._snap()
        self.lines[ay : by + 1] = [first[:ax] + last[bx + 1 :]]
        if k in ("c", "s"):
            self.mode = "INSERT"

    # ------------------------------------------------------------- motions
    def _word_fwd(self) -> None:
        line, x, y = self.line, self.cx, self.cy
        if x < len(line):
            c = _cls(line[x])
            while x < len(line) and c and _cls(line[x]) == c:
                x += 1
        while True:
            while x < len(line) and _cls(line[x]) == 0:
                x += 1
            if x < len(line) or y >= len(self.lines) - 1:
                break
            y += 1
            line, x = self.lines[y], 0
            if not line:
                break
        self.cy, self.cx = y, min(x, max(len(line) - 1, 0))

    def _word_back(self) -> None:
        line, x, y = self.line, self.cx, self.cy
        x -= 1
        while True:
            while x >= 0 and _cls(line[x]) == 0:
                x -= 1
            if x >= 0 or y == 0:
                break
            y -= 1
            line = self.lines[y]
            x = len(line) - 1
            if not line:
                break
        if x >= 0:
            c = _cls(line[x])
            while x > 0 and _cls(line[x - 1]) == c:
                x -= 1
        self.cy, self.cx = y, max(x, 0)

    def _regex(self, pattern: str) -> "re.Pattern[str]":
        """Compile a search pattern honouring ignorecase/smartcase; fall back to a literal."""
        flags = 0
        if self.opts["ignorecase"] and not (self.opts["smartcase"] and any(c.isupper() for c in pattern)):
            flags = re.I
        try:
            return re.compile(pattern, flags)
        except re.error:
            return re.compile(re.escape(pattern), flags)

    def _find(self, pattern: str, backward: bool = False) -> None:
        if not pattern:
            self.msg = "E35: No previous regular expression"
            return
        rx = self._regex(pattern)
        total = len(self.lines)
        wrap = self.opts["wrapscan"]
        steps = range(total + 1) if wrap else range(total)
        for step in steps:
            raw = self.cy - step if backward else self.cy + step
            if wrap:
                y = raw % total
            elif 0 <= raw < total:
                y = raw
            else:
                break
            line = self.lines[y]
            if backward:
                limit = self.cx if step == 0 else len(line) + 1
                hits = [m for m in rx.finditer(line) if m.start() < limit]
                if hits:
                    self.cy, self.cx = y, hits[-1].start()
                    return
            else:
                m = rx.search(line, self.cx + 1 if step == 0 else 0)
                if m:
                    self.cy, self.cx = y, m.start()
                    return
        self.msg = f"E486: Pattern not found: {pattern}"

    def _search_word(self, backward: bool) -> None:
        """* and #: search for the whole word under (or after) the cursor."""
        line, x = self.line, self.cx
        m = None
        for cand in re.finditer(r"\w+", line):
            if cand.end() > x:
                m = cand
                break
        if not m:
            self.msg = "E348: No string under cursor"
            return
        self.search = rf"\b{re.escape(m.group())}\b"
        self.search_back = backward
        self.cx = m.start()  # so the search steps off the current occurrence
        self._find(self.search, backward)

    def search_spans(self, y: int) -> list[tuple[int, int]]:
        """Highlighted match ranges on line y when 'hlsearch' is on (for the renderer)."""
        if not (self.opts["hlsearch"] and self.search):
            return []
        return [(m.start(), m.end()) for m in self._regex(self.search).finditer(self.lines[y]) if m.end() > m.start()]

    # -------------------------------------------------------- command line
    def _command(self, k: str) -> None:
        if k == "<Esc>" or k == "\x03":
            self.mode, self.cmdline = "NORMAL", ""
        elif k == "<BS>":
            self.cmdline = self.cmdline[:-1]
            if not self.cmdline:
                self.mode = "NORMAL"
        elif k == "<CR>":
            line, self.cmdline, self.mode = self.cmdline, "", "NORMAL"
            if line[:1] in ("/", "?"):
                self.search = line[1:] or self.search
                self.search_back = line[0] == "?"
                self._find(self.search, self.search_back)
            else:
                self._ex(line[1:].strip())
        elif len(k) == 1 and ord(k) >= 32:
            self.cmdline += k

    def _write(self, path: str | None) -> bool:
        target = path or self.path
        if not target:
            self.msg = "E32: No file name"
            return False
        data = ("\n".join(self.lines) + "\n").encode()
        try:
            self._save(target, data)
        except VFSError:
            self.msg = "E212: Can't open file for writing"
            return False
        if not self.path:
            self.path = target
            self.name = self.name or target
        self.dirty = False
        self.msg = f'"{path or self.name or target}" {len(self.lines)}L, {len(data)}B written'
        self._ev("vim:write")
        return True

    def _ex(self, cmd: str) -> None:
        if not cmd:
            return
        if cmd.isdigit():
            self.cy = int(cmd) - 1
            self.cx = self._first_nonblank()
            return
        m = re.fullmatch(r"(%?)s(.)(.*?)\2(.*?)(?:\2([gi]*))?", cmd)
        if m:
            whole, _, pat, rep, fl = m.groups()
            self._substitute(bool(whole), pat, rep, fl or "")
            return
        head, _, arg = cmd.partition(" ")
        arg = arg.strip()
        force = head.endswith("!")
        base = head.rstrip("!")
        if base in ("w", "write"):
            self._write(arg or None)
        elif base in ("wq", "x", "xit"):
            if self._write(arg or None):
                self._close_buffer()
        elif base in ("q", "quit", "close"):
            if self.dirty and not force:
                self.msg = "E37: No write since last change (add ! to override)"
            else:
                self._close_buffer()
        elif base in ("qa", "qall", "quitall"):
            bad = self._dirty_buffers()
            if bad and not force:
                self.msg = f"E37: No write since last change for buffer {bad[0] + 1} (add ! to override)"
            else:
                self.done = True
        elif base in ("wa", "wall"):
            self._write_all()
        elif base in ("wqa", "xa", "wqall", "xall"):
            if self._write_all():
                self.done = True
        elif base in ("e", "edit"):
            self._edit(arg)
        elif base in ("ls", "buffers", "files"):
            self._stash()
            self.msg = "  ".join(self._buffer_label(i) for i in range(len(self.bufs)))
        elif base in ("bn", "bnext"):
            self._switch((self.bi + 1) % len(self.bufs))
        elif base in ("bp", "bprev", "bprevious", "bN"):
            self._switch((self.bi - 1) % len(self.bufs))
        elif base in ("b", "buffer"):
            if arg == "#" and self.alt is not None and self.alt < len(self.bufs):
                self._switch(self.alt)
            elif arg.isdigit() and 1 <= int(arg) <= len(self.bufs):
                self._switch(int(arg) - 1)
            else:
                self.msg = f"E86: Buffer {arg} does not exist"
        elif base in ("bd", "bdelete"):
            if self.dirty and not force:
                self.msg = "E89: No write since last change (add ! to override)"
            else:
                self._close_buffer()
        elif base in ("set", "se"):
            self._set(arg.split())
        elif base in ("noh", "nohlsearch"):
            self.opts["hlsearch"] = False
        else:
            self.msg = f"E492: Not an editor command: {cmd}"

    OPT_ALIASES = {
        "ic": "ignorecase", "ignorecase": "ignorecase", "scs": "smartcase", "smartcase": "smartcase",
        "hls": "hlsearch", "hlsearch": "hlsearch", "ws": "wrapscan", "wrapscan": "wrapscan",
        "nu": "number", "number": "number",
    }

    def _set(self, args: list[str]) -> None:
        for opt in args:
            query = opt.endswith("?")
            name = opt.rstrip("?")
            on = True
            if name.startswith("no") and name[2:] in self.OPT_ALIASES:
                name, on = name[2:], False
            elif name.startswith("inv") and name[3:] in self.OPT_ALIASES:
                name, on = name[3:], None  # toggle
            canonical = self.OPT_ALIASES.get(name)
            if canonical is None:
                self.msg = f"E518: Unknown option: {name}"
                return
            current = self.number if canonical == "number" else self.opts[canonical]
            if query:
                self.msg = f"  {'' if current else 'no'}{canonical}"
                continue
            value = (not current) if on is None else on
            if canonical == "number":
                self.number = value
            else:
                self.opts[canonical] = value

    def _substitute(self, whole: bool, pat: str, rep: str, flags: str) -> None:
        try:
            rx = self._regex(pat) if "i" not in flags else re.compile(pat, re.I)
        except re.error:
            self.msg = f"E486: Pattern not found: {pat}"
            return
        rows = range(len(self.lines)) if whole else [self.cy]
        changed = 0
        self._snap()
        for y in rows:
            new, n = rx.subn(rep, self.lines[y], count=0 if "g" in flags else 1)
            if n:
                self.lines[y] = new
                changed += n
        if not changed:
            self.undo.pop()
            self.msg = f"E486: Pattern not found: {pat}"
        else:
            self.msg = f"{changed} substitution{'s' if changed != 1 else ''}"
            self._ev("vim:substitute")

    # ------------------------------------------------------------- viewport
    def scroll_into_view(self, height: int, width: int) -> None:
        self.height = height
        if self.cy < self.top:
            self.top = self.cy
        elif self.cy >= self.top + height:
            self.top = self.cy - height + 1
        gutter = len(str(len(self.lines))) + 1 if self.number else 0
        avail = max(width - gutter - 1, 1)
        if self.cx < self.left:
            self.left = self.cx
        elif self.cx >= self.left + avail:
            self.left = self.cx - avail + 1
