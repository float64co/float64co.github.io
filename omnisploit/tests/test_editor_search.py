import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from keyhelper import keys  # noqa: E402

from omnisploit.ui.editor import Editor  # noqa: E402


def make(text):
    return Editor("/f", "f", text, lambda p, d: None)


class SearchTests(unittest.TestCase):
    def test_forward_backward_and_repeat(self):
        ed = make("foo bar\nbar foo\nbar\n")
        keys(ed, "/bar<CR>")
        self.assertEqual((ed.cy, ed.cx), (0, 4))
        keys(ed, "n")
        self.assertEqual((ed.cy, ed.cx), (1, 0))
        keys(ed, "N")  # reverse direction
        self.assertEqual((ed.cy, ed.cx), (0, 4))
        keys(ed, "?foo<CR>")  # backward from (0,4) finds the foo at (0,0)
        self.assertEqual((ed.cy, ed.cx), (0, 0))
        keys(ed, "n")  # same direction (backward) wraps to the last foo
        self.assertEqual((ed.cy, ed.cx), (1, 4))
        keys(ed, "N")
        self.assertEqual((ed.cy, ed.cx), (0, 0))

    def test_search_wraps_and_wrapscan_can_be_disabled(self):
        ed = make("a\nb\na\n")
        keys(ed, "G/a<CR>")
        self.assertEqual(ed.cy, 0)  # wrapped
        ed = make("a\nb\na\n")
        keys(ed, ":set nows<CR>G/a<CR>")
        self.assertEqual(ed.cy, 2)
        self.assertIn("E486", ed.msg)

    def test_not_found_message(self):
        ed = make("abc\n")
        keys(ed, "/zzz<CR>")
        self.assertIn("E486: Pattern not found: zzz", ed.msg)

    def test_ignorecase_and_smartcase(self):
        ed = make("one\nONE\nOne\n")
        keys(ed, "/ONE<CR>")
        self.assertEqual(ed.cy, 1)
        ed = make("one\nONE\nOne\n")
        keys(ed, ":set ic<CR>/ONE<CR>")
        self.assertEqual(ed.cy, 1)  # from line 0, first match after cursor is line 1 either way
        keys(ed, "ggn")
        self.assertEqual(ed.cy, 1)
        keys(ed, "n")
        self.assertEqual(ed.cy, 2)  # 'One' matches only with ignorecase
        ed = make("one\nONE\nOne\n")
        keys(ed, ":set ic scs<CR>/one<CR>")
        self.assertEqual(ed.cy, 1)  # lowercase pattern: case-insensitive
        keys(ed, "gg/One<CR>")
        self.assertEqual(ed.cy, 2)  # uppercase in pattern: case-sensitive

    def test_star_and_hash_search_whole_words(self):
        ed = make("cat concat cat\ncat\n")
        keys(ed, "*")
        self.assertEqual((ed.cy, ed.cx), (0, 11))  # skips 'concat'
        keys(ed, "*")
        self.assertEqual((ed.cy, ed.cx), (1, 0))
        keys(ed, "#")
        self.assertEqual((ed.cy, ed.cx), (0, 11))
        keys(ed, "n")  # direction stays backward after #
        self.assertEqual((ed.cy, ed.cx), (0, 0))

    def test_star_with_nothing_under_cursor(self):
        ed = make("   \n")
        keys(ed, "*")
        self.assertIn("E348", ed.msg)

    def test_hlsearch_spans_and_noh(self):
        ed = make("abab\nxx\n")
        keys(ed, "/ab<CR>")
        self.assertEqual(ed.search_spans(0), [])  # off by default
        keys(ed, ":set hls<CR>")
        self.assertEqual(ed.search_spans(0), [(0, 2), (2, 4)])
        self.assertEqual(ed.search_spans(1), [])
        keys(ed, ":noh<CR>")
        self.assertEqual(ed.search_spans(0), [])

    def test_set_toggle_query_and_unknown(self):
        ed = make("x\n")
        keys(ed, ":set ic?<CR>")
        self.assertIn("noignorecase", ed.msg)
        keys(ed, ":set invic<CR>:set ic?<CR>")
        self.assertIn("ignorecase", ed.msg)
        self.assertNotIn("noignorecase", ed.msg)
        keys(ed, ":set noic<CR>:set ic?<CR>")
        self.assertIn("noignorecase", ed.msg)
        keys(ed, ":set bogus<CR>")
        self.assertIn("E518: Unknown option: bogus", ed.msg)
        keys(ed, ":set nu<CR>")
        self.assertTrue(ed.number)
        keys(ed, ":set nonu<CR>")
        self.assertFalse(ed.number)

    def test_substitute_respects_ignorecase(self):
        ed = make("Foo foo\n")
        keys(ed, ":s/foo/X/<CR>")
        self.assertEqual(ed.lines[0], "Foo X")
        ed = make("Foo foo\n")
        keys(ed, ":set ic<CR>:s/foo/X/g<CR>")
        self.assertEqual(ed.lines[0], "X X")


if __name__ == "__main__":
    unittest.main()
