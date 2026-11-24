"""End-to-end tests: the real App driven through a fake curses screen."""

import curses
import json
import os
import tempfile
import unittest
from unittest import mock

from omnisploit.engine import save as savegame
from omnisploit.profile import Profile
from omnisploit.ui import app as app_mod
from omnisploit.ui.app import App
from omnisploit.ui.keys import normalize_key


class FakeScreen:
    """Just enough of a curses window: records what was drawn."""

    def __init__(self, h=24, w=80):
        self.h, self.w = h, w
        self.cursor = (0, 0)
        self.pending: list = []
        self.erase()

    def getmaxyx(self):
        return self.h, self.w

    def erase(self):
        self.grid = [[(" ", 0)] * self.w for _ in range(self.h)]

    def addnstr(self, y, x, text, n, attr=0):
        if not (0 <= y < self.h):
            raise curses.error("y out of range")
        for i, ch in enumerate(text[:n]):
            if 0 <= x + i < self.w:
                self.grid[y][x + i] = (ch, attr)

    def move(self, y, x):
        if not (0 <= y < self.h and 0 <= x < self.w):
            raise curses.error("cursor out of range")
        self.cursor = (y, x)

    def refresh(self):
        pass

    def keypad(self, flag):
        pass

    def get_wch(self):
        if not self.pending:
            raise KeyboardInterrupt  # ends App.loop's wait in tests that call loop()
        return self.pending.pop(0)

    def rows(self):
        return ["".join(c for c, _ in row).rstrip() for row in self.grid]

    def text(self):
        return "\n".join(self.rows())

    def attrs_of(self, needle):
        """Attributes of the cells where `needle` appears on a single row."""
        for y, row in enumerate(self.rows()):
            x = row.find(needle)
            if x >= 0:
                return [self.grid[y][x + i][1] for i in range(len(needle))]
        raise AssertionError(f"{needle!r} not on screen:\n{self.text()}")


PATCHES = dict(
    curs_set=lambda n: None,
    has_colors=lambda: False,
    start_color=lambda: None,
    use_default_colors=lambda: None,
    init_pair=lambda *a: None,
)


class AppTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict(os.environ, {"OMNISPLOIT_CONFIG_DIR": self.tmp.name})
        env.start()
        self.addCleanup(env.stop)
        for name, fn in PATCHES.items():
            p = mock.patch.object(curses, name, fn)
            p.start()
            self.addCleanup(p.stop)
        self.scr = FakeScreen()
        self.app = self.make_app()

    def make_app(self, scr=None, **kw):
        return App(scr or self.scr, Profile("neo", "zion"), **kw)

    # --- driving helpers
    def press(self, text, app=None):
        """Type text; '\\n' is Enter, and <Name> tokens are special keys."""
        app = app or self.app
        i = 0
        while i < len(text):
            if text[i] == "<" and ">" in text[i:] and text[i : text.index(">", i) + 1] in (
                "<Esc>", "<CR>", "<BS>", "<Tab>", "<Up>", "<Down>", "<Left>", "<Right>", "<PgUp>", "<PgDn>",
            ):
                j = text.index(">", i) + 1
                app.handle_key(text[i:j])
                i = j
            else:
                app.handle_key("<CR>" if text[i] == "\n" else text[i])
                i += 1

    def screen(self, app=None):
        (app or self.app).draw()
        return self.scr.text()


class TerminalFlowTests(AppTestCase):
    def test_banner_prompt_and_command_output(self):
        s = self.screen()
        self.assertIn("omnisploit", s)
        self.assertIn("~/todo", s)
        self.assertIn("neo@zion:~$", s)
        self.press("echo hello world\n")
        s = self.screen()
        self.assertIn("neo@zion:~$ echo hello world", s)
        shown = [l for l in s.splitlines() if l.strip()]
        self.assertEqual(shown[-2], "hello world")  # the output sits right above the new prompt
        self.assertTrue(shown[-1].startswith("neo@zion:~$"))

    def test_cursor_follows_input(self):
        self.press("abc")
        self.screen()
        y, x = self.scr.cursor
        self.assertEqual(self.scr.rows()[y][:x], "neo@zion:~$ abc"[:x])
        self.assertEqual(x, len("neo@zion:~$ abc"))

    def test_errors_and_continuation_prompt(self):
        self.press("nosuchcmd\n")
        self.assertIn("nosuchcmd: command not found", self.screen())
        self.press("for i in 1 2; do\n")
        s = self.screen()
        self.assertTrue(s.rstrip().splitlines()[-1].startswith(">"))
        self.press("echo n$i\ndone\n")
        s = self.screen()
        self.assertIn("n1", s)
        self.assertIn("n2", s)

    def test_ctrl_c_aborts_continuation(self):
        self.press("for i in 1; do\n")
        self.assertEqual(self.app.shell.prompt(), "> ")
        self.press("\x03")
        self.assertEqual(self.app.shell.prompt(), "neo@zion:~$ ")
        self.assertIn("^C", self.screen())

    def test_tab_completion_and_history(self):
        self.press("cd mot<Tab>\n")
        self.assertEqual(self.app.shell.cwd, "/home/neo/motd-daemon")
        self.press("<Up>")
        self.assertEqual(self.app.term.buf, "cd motd-daemon/")  # history holds the completed line
        self.assertIs(self.app.shell.history, self.app.term.history)

    def test_clear_and_scrollback(self):
        for i in range(60):
            self.press(f"echo line{i}\n")
        self.assertIn("line59", self.screen())
        self.assertNotIn("line0\n", self.screen())
        self.press("<PgUp>")
        self.assertNotIn("line59", self.screen())  # scrolled back; the newest lines are off screen
        self.press("x")  # typing snaps back to the bottom
        self.assertIn("line59", self.screen())
        self.press("\x0c")
        self.assertNotIn("line59", self.screen())

    def test_loop_runs_until_exit(self):
        self.scr.pending = list("echo bye") + ["\n", *"exit", "\n"]
        with mock.patch.object(app_mod, "normalize_key", lambda k: "<CR>" if k == "\n" else k):
            self.app.loop()
        self.assertFalse(self.app.running)


class EditorFlowTests(AppTestCase):
    def test_edit_save_and_return_to_shell(self):
        self.press("vim note.txt\n")
        self.assertIsNotNone(self.app.editor)
        self.assertIn('"note.txt" [New]', self.screen())
        self.press("ione<CR>two<Esc>")
        s = self.screen()
        self.assertNotIn("-- INSERT --", s)
        self.assertIn("one", s)
        self.assertIn("two", s)
        self.press(":wq\n")
        self.assertIsNone(self.app.editor)
        self.press("cat note.txt\n")
        s = self.screen()
        self.assertIn("one", s)
        self.assertIn("two", s)
        self.assertEqual(self.app.world.vfs.read("/home/neo/note.txt"), b"one\ntwo\n")

    def test_modes_status_ruler_and_line_numbers(self):
        self.press("vim n.txt\niab\n")
        self.assertIn("-- INSERT --", self.screen())
        self.press("<Esc>:set nu\n")
        s = self.screen()
        self.assertIn("1 ab", s)
        self.assertRegex(s.splitlines()[-1], r"2,1\s+All")
        self.press("vl")
        self.assertIn("-- VISUAL --", self.screen())
        self.press("<Esc>V")
        self.assertIn("-- VISUAL LINE --", self.screen())

    def test_visual_selection_is_reverse_video(self):
        self.press("vim n.txt\nihello world<Esc>0vll")
        self.screen()
        attrs = self.scr.attrs_of("hello world")
        self.assertEqual([bool(a & curses.A_REVERSE) for a in attrs], [True] * 3 + [False] * 8)

    def test_hlsearch_marks_matches(self):
        self.press("vim n.txt\nia cat and a cat<Esc>:set hls\n/cat\n")
        self.screen()
        attrs = self.scr.attrs_of("a cat and a cat")
        marked = [i for i, a in enumerate(attrs) if a == curses.A_STANDOUT]
        self.assertEqual(marked, [2, 3, 4, 12, 13, 14])  # both "cat"s, nothing else
        self.press(":noh\n")
        self.screen()
        self.assertFalse(any(a == curses.A_STANDOUT for a in self.scr.attrs_of("a cat and a cat")))

    def test_python_file_gets_syntax_attributes_for_def_names(self):
        self.press("vim t.py\nidef foo(x):<Esc>")
        self.screen()
        attrs = self.scr.attrs_of("foo")
        self.assertTrue(all(a & curses.A_BOLD for a in attrs))  # 'def' names are bold even without colors
        self.assertFalse(self.scr.attrs_of("(x):")[0] & curses.A_BOLD)

    def test_long_lines_scroll_horizontally_and_cursor_stays_visible(self):
        self.press("vim n.txt\ni" + "x" * 200 + "<Esc>")
        self.screen()
        y, x = self.scr.cursor
        self.assertLess(x, self.scr.w)
        self.assertEqual(self.scr.rows()[y][x], "x")

    def test_buffers_via_command_line(self):
        self.press("echo second > b.txt\nvim a.txt\nia<Esc>:w\n:e b.txt\n")
        self.assertIn('"b.txt" 1L', self.screen())
        self.press(":ls\n")
        s = self.screen()
        self.assertIn('"a.txt"', s)
        self.assertIn('"b.txt"', s)
        self.press(":qa\n")
        self.assertIsNone(self.app.editor)

    def test_dot_repeat_and_substitute_events_reach_the_world(self):
        self.press("vim n.txt\nialpha beta<Esc>0xx.:s/beta/B/\n:wq\n")
        for ev in ("vim:repeat", "vim:substitute", "vim:write"):
            self.assertIn(ev, self.app.world.events)


class PagerFlowTests(AppTestCase):
    def make_file(self, n=60):
        text = "\n".join(f"line{i}" for i in range(1, n + 1)) + "\n"
        self.app.world.vfs.install("/home/neo/f.txt", text)

    def test_open_page_down_and_quit(self):
        self.make_file()
        self.press("less f.txt\n")
        self.assertIsNotNone(self.app.pager)
        s = self.screen()
        self.assertIn("line1", s)
        self.assertNotIn("line30", s)
        self.assertRegex(s.splitlines()[-1], r"f\.txt\s+lines 1-23/60\s+38%")
        self.press(" ")
        s = self.screen()
        self.assertNotIn("line1\n", s)
        self.assertIn("line23", s)
        self.press("q")
        self.assertIsNone(self.app.pager)
        self.assertIn("neo@zion:~$", self.screen())

    def test_g_and_shift_g_jump_to_ends(self):
        self.make_file()
        self.press("less f.txt\n")
        self.press("G")
        s = self.screen()
        self.assertIn("line60", s)
        self.assertIn("(END)", s)
        self.press("g")
        s = self.screen()
        self.assertIn("line1", s)
        self.assertNotIn("(END)", s)

    def test_half_page_and_single_line_scrolling(self):
        self.make_file()
        self.press("less f.txt\n")
        self.press("d")
        self.assertEqual(self.app.pager.top, 11)  # half of a 23-line window
        self.press("u")
        self.assertEqual(self.app.pager.top, 0)
        self.press("d")
        before = self.app.pager.top
        self.press("j")
        self.assertEqual(self.app.pager.top, before + 1)
        self.press("k")
        self.assertEqual(self.app.pager.top, before)

    def test_search_forward_and_repeat(self):
        self.make_file()
        self.press("less f.txt\n")
        self.press("/line45\n")
        s = self.screen()
        self.assertIn("line45", s)
        attrs = self.scr.attrs_of("line45")
        self.assertTrue(all(a == curses.A_STANDOUT for a in attrs))
        self.press("/line50\n")
        self.assertIn("line50", self.screen())
        self.press("N")
        self.assertIn("line45", self.screen())

    def test_pattern_not_found(self):
        self.make_file()
        self.press("less f.txt\n")
        self.press("/nosuchpattern\n")
        self.assertIn("Pattern not found", self.screen())

    def test_esc_and_ctrl_c_quit(self):
        self.make_file()
        self.press("less f.txt\n")
        self.press("<Esc>")
        self.assertIsNone(self.app.pager)
        self.press("less f.txt\n")
        self.app.handle_key("\x03")
        self.assertIsNone(self.app.pager)

    def test_missing_filename_error_and_directory_error(self):
        self.press("less\n")
        self.assertIsNone(self.app.pager)
        self.assertIn("Missing filename", self.screen())
        self.press("mkdir d\nless d\n")
        self.assertIsNone(self.app.pager)

    def test_piped_content_is_paged(self):
        self.press("echo hello world | less\n")
        self.assertIsNotNone(self.app.pager)
        self.assertIn("hello world", self.screen())
        self.assertIn("standard input", self.screen())
        self.press("q")

    def test_more_is_an_alias(self):
        self.make_file()
        self.press("more f.txt\n")
        self.assertIsNotNone(self.app.pager)
        self.press("q")

    def test_missions_and_autosave_wait_until_the_pager_closes(self):
        self.press("mkdir workspace\necho hello > workspace/hello.txt\n")
        self.press("ls workspace | less\n")
        self.assertNotIn("target complete", self.screen())
        self.press("q")
        s = self.screen()
        self.assertIn("target complete: workbench", s)


class MissionAndSaveFlowTests(AppTestCase):
    def test_target_completion_is_announced_and_saved(self):
        self.press("mkdir workspace\n")
        self.press("echo hello > workspace/hello.txt\n")
        self.assertNotIn("target complete", self.screen())
        self.press("ls | wc -l\n")
        s = self.screen()
        self.assertIn("[todo] target complete: workbench -- Set up your workbench", s)
        data = json.loads(savegame.save_path("neo", "zion").read_text())
        self.assertEqual(data["completed"], ["workbench"])
        self.assertIn("pipe", data["events"])

    def test_editor_session_can_complete_a_target_on_exit(self):
        self.press("mkdir workspace\nvim workspace/token.txt\n")
        self.press("iTKN-7f3a-91c2<Esc>:wq\n")
        self.assertIn("target complete: dig", self.screen())

    def test_relaunch_restores_state(self):
        self.press("mkdir workspace\necho keep > workspace/f.txt\nalias ll='ls -l'\ncd workspace\n")
        self.app.autosave()
        scr2 = FakeScreen()
        app2 = self.make_app(scr2)
        scr2.erase()
        app2.draw()
        self.assertIn("Welcome back, neo", scr2.text())
        self.assertEqual(app2.shell.cwd, "/home/neo/workspace")
        self.press("cat f.txt\nll\n", app2)
        app2.draw()
        self.assertIn("keep", scr2.text())
        self.assertIn("-rw-r--r--", scr2.text())

    def test_new_game_discards_the_save(self):
        self.press("touch marker\n")
        self.app.autosave()
        app2 = self.make_app(FakeScreen(), new_game=True)
        self.assertFalse(app2.world.vfs.exists("/home/neo/marker"))
        self.assertNotIn("Welcome back", "\n".join(l.text for l in app2.term.lines))

    def test_other_identities_are_unaffected(self):
        self.press("touch marker\n")
        self.app.autosave()
        other = App(FakeScreen(), Profile("trinity", "zion"))
        self.assertFalse(other.world.vfs.exists("/home/trinity/marker"))
        self.assertTrue(savegame.save_path("neo", "zion").exists())

    def test_functions_survive_a_restart(self):
        self.press("hi() { echo hello $1; }\n")
        self.app.autosave()
        app2 = self.make_app(FakeScreen())
        self.press("hi world\n", app2)
        self.assertEqual(b"".join(d for d, e in app2.shell.run_line("hi again").chunks), b"hello again\n")

    def test_corrupt_save_is_ignored(self):
        savegame.save_path("neo", "zion").write_text("{broken")
        app2 = self.make_app(FakeScreen())
        self.assertIn("neo@zion", app2.shell.prompt())

    def test_flag_text_is_randomized_but_stable_across_a_reload(self):
        flag = self.app.world.levels["motd-daemon"].flag
        self.assertNotEqual(flag, "FLAG{ret2win_is_just_the_beginning}")  # the fixed, un-seeded default
        self.app.autosave()
        app2 = self.make_app(FakeScreen())
        self.assertEqual(app2.world.levels["motd-daemon"].flag, flag)
        self.assertEqual(app2.world.flag_seed, self.app.world.flag_seed)

    def test_a_different_identity_gets_a_different_flag(self):
        other = App(FakeScreen(), Profile("trinity", "zion"))
        self.assertNotEqual(other.world.levels["motd-daemon"].flag, self.app.world.levels["motd-daemon"].flag)

    def test_new_game_also_gets_a_fresh_seed(self):
        seed = self.app.world.flag_seed
        self.app.autosave()
        app2 = self.make_app(FakeScreen(), new_game=True)
        self.assertNotEqual(app2.world.flag_seed, seed)

    def test_exploiting_with_a_randomized_flag_still_completes_the_mission(self):
        self.press("mkdir workspace\ncd motd-daemon\n")
        flag = self.app.world.levels["motd-daemon"].flag
        self.press(f"python3 -c \"from pwn import *; context.arch='amd64'; open('/tmp/p','wb').write(b'A'*72+p64(0x401196)+b'\\\\n')\"\n")
        self.press("./motd < /tmp/p\n")
        s = self.screen()
        self.assertIn(flag, s)
        self.assertIn("target complete: motd", s)


class RobustnessTests(AppTestCase):
    def test_tiny_screens_never_crash(self):
        for h, w in [(1, 1), (1, 10), (2, 5), (3, 3), (5, 12), (24, 2)]:
            scr = FakeScreen(h, w)
            app = self.make_app(scr)
            self.press("echo hi\nvim x\nitext<Esc>:set nu\nvl", app)
            app.draw()
            self.press("<Esc>:q!\nls -l\n", app)
            app.draw()

    def test_unknown_and_resize_keys_are_ignored(self):
        self.app.handle_key("<Resize>")
        self.app.handle_key("<?>")
        self.app.draw()
        self.assertEqual(self.app.term.buf, "")

    def test_key_normalization(self):
        self.assertEqual(normalize_key("\n"), "<CR>")
        self.assertEqual(normalize_key("\x1b"), "<Esc>")
        self.assertEqual(normalize_key("\x7f"), "<BS>")
        self.assertEqual(normalize_key(curses.KEY_UP), "<Up>")
        self.assertEqual(normalize_key(curses.KEY_RESIZE), "<Resize>")
        self.assertEqual(normalize_key("x"), "x")
        self.assertEqual(normalize_key(99999), "<?>")

    def test_unicode_input_and_binary_output_are_drawn_safely(self):
        self.press("echo héllo ✓\n")
        self.assertIn("héllo ✓", self.screen())
        self.press("xxd -l 4 /home/neo/motd-daemon/motd\n")
        self.assertIn("7f45 4c46", self.screen())


if __name__ == "__main__":
    unittest.main()
