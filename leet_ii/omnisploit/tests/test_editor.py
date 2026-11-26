import unittest

from omnisploit.ui.editor import Editor


def keys(ed: Editor, seq: str) -> None:
    """Feed a compact key string; <Name> tokens are special keys."""
    i = 0
    while i < len(seq):
        if seq[i] == "<" and ">" in seq[i:] and seq[i : seq.index(">", i) + 1] in (
            "<Esc>", "<CR>", "<BS>", "<Up>", "<Down>", "<Left>", "<Right>", "<Tab>",
        ):
            j = seq.index(">", i) + 1
            ed.key(seq[i:j])
            i = j
        else:
            ed.key(seq[i])
            i += 1


class EditorTests(unittest.TestCase):
    def make(self, text="", name="f.py"):
        self.saved = {}
        return Editor("/f.py", name, text, lambda p, d: self.saved.__setitem__(p, d))

    def test_insert_and_save(self):
        ed = self.make()
        keys(ed, "ihello<CR>world<Esc>:wq<CR>")
        self.assertTrue(ed.done)
        self.assertEqual(self.saved["/f.py"], b"hello\nworld\n")

    def test_quit_guard(self):
        ed = self.make()
        keys(ed, "ix<Esc>:q<CR>")
        self.assertFalse(ed.done)
        self.assertIn("E37", ed.msg)
        keys(ed, ":q!<CR>")
        self.assertTrue(ed.done)

    def test_dd_yy_p_undo(self):
        ed = self.make("a\nb\nc\n")
        keys(ed, "ddp")
        self.assertEqual(ed.lines, ["b", "a", "c"])
        keys(ed, "u")
        self.assertEqual(ed.lines, ["b", "c"])
        keys(ed, "u")
        self.assertEqual(ed.lines, ["a", "b", "c"])
        keys(ed, "yyGp")
        self.assertEqual(ed.lines, ["a", "b", "c", "a"])

    def test_motions_and_counts(self):
        ed = self.make("one two three\nfour\n")
        keys(ed, "w")
        self.assertEqual((ed.cy, ed.cx), (0, 4))
        keys(ed, "$")
        self.assertEqual(ed.cx, 12)
        keys(ed, "0dw")
        self.assertEqual(ed.lines[0], "two three")
        keys(ed, "2x")
        self.assertEqual(ed.lines[0], "o three")
        keys(ed, "G")
        self.assertEqual(ed.cy, 1)

    def test_open_line_and_autoindent(self):
        ed = self.make("def f():\n    pass\n")
        keys(ed, "jofoo<Esc>")
        self.assertEqual(ed.lines, ["def f():", "    pass", "    foo"])

    def test_substitute_and_search(self):
        ed = self.make("aaa\nbab\n")
        keys(ed, ":%s/a/X/g<CR>")
        self.assertEqual(ed.lines, ["XXX", "bXb"])
        keys(ed, "gg/b<CR>")
        self.assertEqual((ed.cy, ed.cx), (1, 0))

    def test_cw_and_r(self):
        ed = self.make("foo bar\n")
        keys(ed, "cwxyz<Esc>")
        self.assertEqual(ed.lines[0], "xyz bar")
        keys(ed, "0rQ")
        self.assertEqual(ed.lines[0], "Qyz bar")

    def test_write_failure(self):
        def bad(p, d):
            from omnisploit.engine.vfs import PermissionDenied
            raise PermissionDenied("x")
        ed = Editor("/f", "f", "", bad)
        keys(ed, "ia<Esc>:w<CR>")
        self.assertIn("E212", ed.msg)
        self.assertTrue(ed.dirty)


class VisualRepeatBufferTests(unittest.TestCase):
    def make(self, text="", name="f.py", loader=None):
        self.saved = {}
        self.events = []
        return Editor("/f.py", name, text, lambda p, d: self.saved.__setitem__(p, d),
                      loader=loader, on_event=self.events.append)

    # ---- visual mode
    def test_visual_charwise_delete_and_yank_paste(self):
        ed = self.make("hello world\n")
        keys(ed, "vlld")
        self.assertEqual(ed.lines, ["lo world"])
        keys(ed, "0vey")  # no 'e' motion: should leave line intact
        self.assertEqual(ed.lines, ["lo world"])
        keys(ed, "0vly$p")
        self.assertEqual(ed.lines, ["lo worldlo"])
        self.assertIn("vim:visual", self.events)
        self.assertIn("vim:visual-op", self.events)

    def test_visual_line_mode(self):
        ed = self.make("a\nb\nc\nd\n")
        keys(ed, "Vjd")
        self.assertEqual(ed.lines, ["c", "d"])
        keys(ed, "Vjy")
        keys(ed, "Gp")
        self.assertEqual(ed.lines, ["c", "d", "c", "d"])
        keys(ed, "ggVj>")
        self.assertEqual(ed.lines[:2], ["    c", "    d"])
        keys(ed, "u")
        self.assertEqual(ed.lines[:2], ["c", "d"])

    def test_visual_multiline_charwise_and_change(self):
        ed = self.make("abc\ndef\nghi\n")
        keys(ed, "lvjd")  # from (0,1) to (1,1) inclusive
        self.assertEqual(ed.lines, ["af", "ghi"])
        keys(ed, "vcXY<Esc>")
        self.assertEqual(ed.lines[0], "aXY")  # the cursor was on 'f', so only 'f' was replaced

    def test_visual_escape_and_toggle_and_swap(self):
        ed = self.make("abcdef\n")
        keys(ed, "v<Esc>")
        self.assertEqual(ed.mode, "NORMAL")
        keys(ed, "vv")
        self.assertEqual(ed.mode, "NORMAL")
        keys(ed, "llvlo")
        self.assertEqual(ed.cx, 2)
        self.assertEqual(ed.selected_span(0), (2, 4))
        keys(ed, "<Esc>")
        self.assertIsNone(ed.selected_span(0))

    # ---- dot repeat
    def test_dot_repeats_x_dd_and_inserts(self):
        ed = self.make("abcdef\nl1\nl2\nl3\n")
        keys(ed, "x..")
        self.assertEqual(ed.lines[0], "def")
        keys(ed, "jdd.")
        self.assertEqual(ed.lines, ["def", "l3"])
        ed2 = self.make("a\nb\n")
        keys(ed2, "Ax<Esc>j.")
        self.assertEqual(ed2.lines, ["ax", "bx"])
        self.assertIn("vim:repeat", self.events)

    def test_dot_repeats_cw_and_ignores_movement_and_undo(self):
        ed = self.make("foo bar baz\n")
        keys(ed, "cwXX<Esc>w.")
        self.assertEqual(ed.lines[0], "XX XX baz")
        keys(ed, "w")  # a motion must not replace the last change
        keys(ed, ".")
        self.assertEqual(ed.lines[0], "XX XX XX")
        keys(ed, "u")  # undo is not repeatable
        keys(ed, ".")
        self.assertEqual(ed.lines[0], "XX XX XX")

    def test_dot_with_nothing_to_repeat(self):
        ed = self.make("a\n")
        keys(ed, ".")
        self.assertEqual(ed.lines, ["a"])

    # ---- buffers
    def loader(self, files):
        def load(name):
            path = "/" + name
            return (path, files[name], True) if name in files else (path, "", False)
        return load

    def test_edit_ls_switch_and_close(self):
        ed = self.make("one\n", loader=self.loader({"g.txt": "two\n"}))
        keys(ed, ":e g.txt<CR>")
        self.assertEqual((ed.name, ed.lines), ("g.txt", ["two"]))
        keys(ed, ":ls<CR>")
        self.assertIn('1 #  "f.py"', ed.msg)
        self.assertIn('2 %  "g.txt"', ed.msg)
        keys(ed, ":bp<CR>")
        self.assertEqual(ed.name, "f.py")
        keys(ed, ":b 2<CR>")
        self.assertEqual(ed.name, "g.txt")
        keys(ed, "<Esc>\x1e")  # Ctrl-^ toggles the alternate buffer
        self.assertEqual(ed.name, "f.py")
        keys(ed, ":bn<CR>:q<CR>")  # closes g.txt only
        self.assertFalse(ed.done)
        self.assertEqual((ed.name, ed.lines), ("f.py", ["one"]))
        keys(ed, ":q<CR>")
        self.assertTrue(ed.done)
        self.assertIn("vim:buffers", self.events)

    def test_buffers_keep_independent_state(self):
        ed = self.make("a\nb\nc\n", loader=self.loader({"g.txt": "x\n"}))
        keys(ed, "Gix<Esc>:e g.txt<CR>:bp<CR>")
        self.assertEqual(ed.lines, ["a", "b", "xc"])
        self.assertEqual(ed.cy, 2)
        self.assertTrue(ed.dirty)
        keys(ed, "u")
        self.assertEqual(ed.lines, ["a", "b", "c"])

    def test_dirty_buffers_block_quit_all_and_wa_writes_them(self):
        ed = self.make("a\n", loader=self.loader({"g.txt": "x\n"}))
        keys(ed, "iZ<Esc>:e g.txt<CR>ig<Esc>:qa<CR>")
        self.assertFalse(ed.done)
        self.assertIn("E37", ed.msg)
        keys(ed, ":wa<CR>")
        self.assertEqual(self.saved, {"/f.py": b"Za\n", "/g.txt": b"gx\n"})
        keys(ed, ":qa<CR>")
        self.assertTrue(ed.done)

    def test_reopening_same_file_switches_to_it(self):
        ed = self.make("a\n", loader=self.loader({"f.py": "a\n"}))
        ed.path = "/f.py"
        ed.bufs[0].path = "/f.py"
        keys(ed, ":e f.py<CR>")
        self.assertEqual(len(ed.bufs), 1)

    def test_bd_and_missing_buffer_errors(self):
        ed = self.make("a\n", loader=self.loader({"g.txt": "x\n"}))
        keys(ed, ":e g.txt<CR>ix<Esc>:bd<CR>")
        self.assertIn("E89", ed.msg)
        keys(ed, ":bd!<CR>")
        self.assertEqual(ed.name, "f.py")
        keys(ed, ":b 9<CR>")
        self.assertIn("E86", ed.msg)

    def test_events_for_substitute_and_write(self):
        ed = self.make("aaa\n")
        keys(ed, ":%s/a/b/g<CR>:w<CR>")
        self.assertIn("vim:substitute", self.events)
        self.assertIn("vim:write", self.events)


if __name__ == "__main__":
    unittest.main()
