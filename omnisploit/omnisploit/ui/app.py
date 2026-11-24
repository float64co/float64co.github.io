"""curses front-end: a terminal pane and a full-screen embedded editor."""

from __future__ import annotations

import curses
import os
import secrets

from ..engine import save as savegame
from ..engine.vfs import NotFound, VFSError
from ..engine.world import World
from ..profile import Profile, load, save as save_profile
from ..shell.shell import Shell
from .editor import Editor
from .highlight import highlight, kind_for
from .keys import normalize_key
from .pager import Pager
from .setup import run_setup
from .terminal import ERROR, PROMPT, Terminal

BANNER = """\
omnisploit -- a simulated machine. Nothing here touches your real system.
Your targets are listed in ~/todo (take them in any order). Type `help` for commands.

"""
MAX_HIGHLIGHT_LINES = 2000


class App:
    def __init__(self, scr, profile: Profile, new_game: bool = False) -> None:
        self.scr = scr
        self.save_path = savegame.save_path(profile.name, profile.hostname)
        if new_game:
            self.save_path.unlink(missing_ok=True)  # explicit --new-game: discard this identity's save
        # a seed that picks this player's flag text, stable across reloads (saved and restored
        # below) but different for anyone else -- a walkthrough's exact flag text won't work for them
        seed = savegame.peek_flag_seed(self.save_path)
        if seed is None:
            seed = secrets.randbits(64)
        self.world = World(profile.name, profile.hostname, flag_seed=seed)
        self.shell = Shell(self.world)
        self.term = Terminal(self.shell.complete)
        self.term.history = self.shell.history  # one history, shared with the `history` builtin
        self.editor: Editor | None = None
        self.pager: Pager | None = None
        self.running = True
        self._init_colors()

        self.pristine = savegame.snapshot(self.world)
        restored = savegame.load_game(self.world, self.shell, self.save_path)
        self.term.hist_idx = len(self.shell.history)
        self.term.write(BANNER)
        if restored:
            self.term.write(f"Welcome back, {profile.name}. Your progress was restored.\n\n", PROMPT)

    def _init_colors(self) -> None:
        self.attrs = {0: curses.A_NORMAL, 1: curses.A_BOLD, 2: curses.A_NORMAL}
        self.dim = curses.A_DIM
        self.tilde = curses.A_BOLD
        self.syntax = {k: curses.A_NORMAL for k in ("kw", "str", "com", "num", "def", "bi", "var")}
        self.syntax["def"] = curses.A_BOLD
        self.match = curses.A_STANDOUT  # hlsearch matches
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            for pair, color in enumerate(
                (curses.COLOR_GREEN, curses.COLOR_RED, curses.COLOR_BLUE, curses.COLOR_YELLOW,
                 curses.COLOR_MAGENTA, curses.COLOR_CYAN), 1
            ):
                curses.init_pair(pair, color, -1)
            curses.init_pair(7, curses.COLOR_BLACK, curses.COLOR_YELLOW)
            cp = curses.color_pair
            self.match = cp(7)
            self.attrs[1] = cp(1) | curses.A_BOLD
            self.attrs[2] = cp(2)
            self.tilde = cp(3) | curses.A_BOLD
            self.dim = cp(4)
            self.syntax.update(
                kw=cp(4) | curses.A_BOLD, str=cp(1), com=cp(6) | curses.A_DIM, num=cp(5),
                bi=cp(6), var=cp(5), **{"def": cp(3) | curses.A_BOLD},
            )

    # ---------------------------------------------------------------- loop
    def loop(self) -> None:
        try:
            while self.running:
                self.draw()
                try:
                    raw = self.scr.get_wch()
                except KeyboardInterrupt:
                    raw = "\x03"
                except curses.error:
                    continue
                self.handle_key(normalize_key(raw))
        finally:
            self.autosave()

    def handle_key(self, k: str) -> None:
        """Dispatch one normalized key to the editor or the terminal."""
        if k == "<Resize>":
            return
        if self.editor:
            self.editor.key(k)
            if self.editor.done:
                self.editor = None
                self.after_command()
        elif self.pager:
            self.pager.key(k)
            if self.pager.done:
                self.pager = None
                self.after_command()
        else:
            self.terminal_key(k)

    def terminal_key(self, k: str) -> None:
        h, w = self.scr.getmaxyx()
        self.shell.width = w
        if k == "\x03":
            self.shell.abort_input()
        line = self.term.key(k, self.shell.prompt(), self.shell.secret_input(), self.shell.eof_line())
        if line is None:
            return
        out = self.shell.run_line(line)
        for data, is_err in out.chunks:
            self.term.write(data.decode("utf-8", errors="replace"), ERROR if is_err else 0)
        for action in out.actions:
            self.do_action(action)
        if not self.editor and not self.pager:
            self.after_command()

    def after_command(self) -> None:
        """Check targets, announce completions, and autosave."""
        if self.world.missions:
            for m in self.world.missions.evaluate():
                self.term.write(f"\n[todo] target complete: {m.id} -- {m.title}\n", PROMPT)
            if len(self.world.missions.completed) == len(self.world.missions.missions):
                if "all-done-announced" not in self.world.events:
                    self.world.events.add("all-done-announced")
                    self.term.write("[todo] every target is complete. Nicely done.\n", PROMPT)
        self.autosave()

    def autosave(self) -> None:
        savegame.save_game(self.world, self.shell, self.pristine, self.save_path)

    def do_action(self, action: tuple) -> None:
        if action[0] == "clear":
            self.term.clear()
        elif action[0] == "quit":
            self.running = False
        elif action[0] == "edit":
            self.open_editor(action[1], action[2])
        elif action[0] == "page":
            self.pager = Pager(action[1], action[2])
            self.pager.height = max(self.scr.getmaxyx()[0] - 1, 1)  # correct before the first keypress, not just the first draw

    def open_editor(self, path: str | None, name: str) -> None:
        vfs = self.world.vfs
        text, exists = "", False
        if path:
            try:
                text, exists = vfs.read(path).decode("utf-8", "replace"), True
            except VFSError:
                pass

        def load_for_editor(arg: str) -> tuple[str | None, str, bool]:
            p = self.shell.abspath(arg)
            try:
                node = vfs.get(p)
            except NotFound:
                return p, "", False
            if node.is_dir:
                raise VFSError("is a directory")
            return p, vfs.read(p).decode("utf-8", "replace"), True

        self.editor = Editor(
            path, name, text, lambda p, data: vfs.write(p, data), exists,
            loader=load_for_editor, on_event=self.world.events.add,
        )

    # ------------------------------------------------------------- drawing
    def draw(self) -> None:
        self.scr.erase()
        if self.editor:
            self.draw_editor(self.editor)
        elif self.pager:
            self.draw_pager(self.pager)
        else:
            self.draw_terminal()
        self.scr.refresh()

    def _put(self, y: int, x: int, text: str, attr: int = 0) -> None:
        h, w = self.scr.getmaxyx()
        if 0 <= y < h and x < w:
            try:
                self.scr.addnstr(y, x, text, max(w - x - (1 if y == h - 1 else 0), 0), attr)
            except curses.error:
                pass

    def draw_terminal(self) -> None:
        h, w = self.scr.getmaxyx()
        prompt = self.shell.prompt()
        rows, crow, ccol = self.term.layout(prompt, w, h, self.shell.secret_input())
        for y, (line, start, chunk) in enumerate(rows):
            split = max(0, min(line.prompt_len - start, len(chunk)))
            if split:
                self._put(y, 0, chunk[:split], self.attrs[PROMPT])
            self._put(y, split, chunk[split:], self.attrs[line.style])
        if crow is None:
            curses.curs_set(0)
        else:
            curses.curs_set(1)
            self.scr.move(crow, min(ccol, w - 1))

    def _draw_styled(self, row: int, x0: int, text: str, attrs: list[int]) -> None:
        """Draw text with a per-character attribute list, in runs."""
        i = 0
        while i < len(text):
            j = i
            while j < len(text) and attrs[j] == attrs[i]:
                j += 1
            self._put(row, x0 + i, text[i:j], attrs[i])
            i = j

    def draw_editor(self, ed: Editor) -> None:
        h, w = self.scr.getmaxyx()
        text_h = max(h - 1, 1)
        ed.scroll_into_view(text_h, w)
        gutter = len(str(len(ed.lines))) + 1 if ed.number else 0
        kind = kind_for(ed.name, ed.lines[0] if ed.lines else "")
        spans = highlight(ed.lines, kind) if kind and len(ed.lines) <= MAX_HIGHLIGHT_LINES else None
        avail = max(w - gutter, 1)
        for row in range(text_h):
            idx = ed.top + row
            if idx >= len(ed.lines):
                self._put(row, 0, "~", self.tilde)
                continue
            if gutter:
                self._put(row, 0, f"{idx + 1:>{gutter - 1}} ", self.dim)
            line = ed.lines[idx]
            attrs = [curses.A_NORMAL] * (len(line) + 1)
            if spans is not None:
                for s, e, style in spans[idx]:
                    for c in range(s, min(e, len(line))):
                        attrs[c] = self.syntax.get(style, curses.A_NORMAL)
            for ms, me in ed.search_spans(idx):
                for c in range(ms, min(me, len(line))):
                    attrs[c] = self.match
            sel = ed.selected_span(idx)
            if sel:
                for c in range(sel[0], min(sel[1], len(attrs))):
                    attrs[c] |= curses.A_REVERSE
            view = (line + " ")[ed.left : ed.left + avail]
            self._draw_styled(row, gutter, view, attrs[ed.left : ed.left + avail])
        modes = {"INSERT": "-- INSERT --", "VISUAL": "-- VISUAL --", "VLINE": "-- VISUAL LINE --"}
        if ed.mode == "COMMAND":
            status = ed.cmdline
        elif ed.mode in modes:
            status = modes[ed.mode] + (("   " + ed.msg) if ed.msg else "")
        else:
            status = ed.msg or (f"{ed.count}" if ed.count else "") + ed.pending
        self._put(h - 1, 0, status, curses.A_BOLD if ed.mode in modes else 0)
        ruler = f"{ed.cy + 1},{ed.cx + 1}"
        pct = "All" if len(ed.lines) <= text_h else "Top" if ed.top == 0 else (
            "Bot" if ed.top + text_h >= len(ed.lines) else f"{100 * ed.top // len(ed.lines)}%"
        )
        if ed.mode != "COMMAND" and w > 30:
            self._put(h - 1, w - 22, f"{ruler:<10}{pct}", 0)
        curses.curs_set(1)
        if ed.mode == "COMMAND":
            self.scr.move(h - 1, min(len(ed.cmdline), w - 1))
        else:
            self.scr.move(min(ed.cy - ed.top, text_h - 1), min(gutter + ed.cx - ed.left, w - 1))

    def draw_pager(self, pg: Pager) -> None:
        h, w = self.scr.getmaxyx()
        text_h = max(h - 1, 1)
        pg.height = text_h
        for row in range(text_h):
            idx = pg.top + row
            if idx >= len(pg.lines):
                continue  # less leaves trailing rows blank -- no '~' filler
            line = pg.lines[idx]
            attrs = [curses.A_NORMAL] * (len(line) + 1)
            for s, e in pg.match_spans(line):
                for c in range(s, min(e, len(attrs))):
                    attrs[c] = self.match
            view = line[:w]
            self._draw_styled(row, 0, view, attrs[:w])
        self._put(h - 1, 0, pg.status(), curses.A_BOLD)
        curses.curs_set(1 if pg.mode == "search" else 0)
        if pg.mode == "search":
            self.scr.move(h - 1, min(len(pg.cmdline), w - 1))


def _main(scr, force_setup: bool = False, new_game: bool = False) -> None:
    curses.raw()
    scr.keypad(True)
    profile = None if force_setup else load()
    if profile is None:
        profile = run_setup(scr, load() or Profile())
        if profile is None:
            return
        save_profile(profile)
    App(scr, profile, new_game).loop()


def run(force_setup: bool = False, new_game: bool = False) -> None:
    os.environ.setdefault("ESCDELAY", "25")
    curses.wrapper(lambda scr: _main(scr, force_setup, new_game))
