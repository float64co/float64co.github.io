import unittest

from omnisploit.ui.pager import Pager


def keys(pg: Pager, seq: str) -> None:
    """Feed a compact key string; <Name> tokens are special keys."""
    i = 0
    while i < len(seq):
        if seq[i] == "<" and ">" in seq[i:] and seq[i : seq.index(">", i) + 1] in (
            "<Esc>", "<CR>", "<BS>", "<Up>", "<Down>", "<PgUp>", "<PgDn>", "<Home>", "<End>",
        ):
            j = seq.index(">", i) + 1
            pg.key(seq[i:j])
            i = j
        else:
            pg.key(seq[i])
            i += 1


def make(n=60, height=20, name="f.txt") -> Pager:
    text = "\n".join(f"line{i}" for i in range(1, n + 1)) + "\n"
    pg = Pager(text, name)
    pg.height = height
    return pg


class PagerTests(unittest.TestCase):
    def test_trailing_newline_does_not_add_a_blank_line(self):
        self.assertEqual(Pager("a\nb\n", "f").lines, ["a", "b"])
        self.assertEqual(Pager("a\nb", "f").lines, ["a", "b"])
        self.assertEqual(Pager("", "f").lines, [])

    def test_quit_keys(self):
        for k in ("q", "<Esc>", "\x03"):
            pg = make()
            keys(pg, k)
            self.assertTrue(pg.done, k)

    def test_page_down_and_up_overlap_by_one_line(self):
        pg = make(n=100, height=20)
        keys(pg, " ")
        self.assertEqual(pg.top, 19)
        keys(pg, "b")
        self.assertEqual(pg.top, 0)
        keys(pg, "f")
        self.assertEqual(pg.top, 19)
        keys(pg, "<PgUp>")
        self.assertEqual(pg.top, 0)
        keys(pg, "<PgDn>")
        self.assertEqual(pg.top, 19)

    def test_half_page_down_and_up(self):
        pg = make(n=100, height=20)
        keys(pg, "d")
        self.assertEqual(pg.top, 10)
        keys(pg, "u")
        self.assertEqual(pg.top, 0)

    def test_line_scrolling(self):
        pg = make(n=100, height=20)
        keys(pg, "jjj")
        self.assertEqual(pg.top, 3)
        keys(pg, "k")
        self.assertEqual(pg.top, 2)
        keys(pg, "<Down>")
        self.assertEqual(pg.top, 3)
        keys(pg, "<Up>")
        self.assertEqual(pg.top, 2)
        keys(pg, "<CR>")
        self.assertEqual(pg.top, 3)

    def test_cannot_scroll_past_the_ends(self):
        pg = make(n=100, height=20)
        keys(pg, "k")
        self.assertEqual(pg.top, 0)
        keys(pg, "G")
        self.assertEqual(pg.top, 80)
        self.assertTrue(pg.at_end)
        keys(pg, "j")
        self.assertEqual(pg.top, 80)

    def test_g_and_shift_g(self):
        pg = make(n=100, height=20)
        keys(pg, "G")
        self.assertEqual(pg.top, 80)
        keys(pg, "g")
        self.assertEqual(pg.top, 0)

    def test_short_file_has_no_room_to_scroll(self):
        pg = make(n=5, height=20)
        self.assertEqual(pg.max_top, 0)
        keys(pg, "G")
        self.assertEqual(pg.top, 0)
        self.assertTrue(pg.at_end)

    def test_search_forward_and_repeat(self):
        pg = Pager("\n".join(["a", "b", "MARK", "c", "d", "MARK", "e", "f"]) + "\n", "f")
        pg.height = 3  # smaller than the content, so there is real room to scroll between matches
        keys(pg, "/MARK<CR>")
        self.assertEqual(pg.top, 2)
        self.assertEqual(pg.mode, "normal")
        keys(pg, "n")
        self.assertEqual(pg.top, 5)
        keys(pg, "n")  # no more matches ahead
        self.assertEqual(pg.top, 5)
        self.assertEqual(pg.msg, "Pattern not found")
        keys(pg, "N")
        self.assertEqual(pg.top, 2)
        keys(pg, "N")  # no more matches behind
        self.assertEqual(pg.top, 2)
        self.assertEqual(pg.msg, "Pattern not found")

    def test_search_pattern_not_found_sets_a_message(self):
        pg = make(n=20, height=10)
        keys(pg, "/nosuchthing<CR>")
        self.assertEqual(pg.msg, "Pattern not found")
        self.assertEqual(pg.status(), "Pattern not found")

    def test_search_input_editing(self):
        pg = make(n=20, height=10)
        keys(pg, "/line9")
        self.assertEqual(pg.cmdline, "/line9")
        keys(pg, "<BS>")
        self.assertEqual(pg.cmdline, "/line")
        keys(pg, "<Esc>")
        self.assertEqual(pg.mode, "normal")
        self.assertEqual(pg.cmdline, "")

    def test_backspace_past_the_slash_cancels_search(self):
        pg = make(n=20, height=10)
        keys(pg, "/")
        keys(pg, "<BS>")
        self.assertEqual(pg.mode, "normal")

    def test_regex_in_search_is_supported_and_bad_regex_falls_back_to_literal(self):
        pg = make(n=20, height=10)
        keys(pg, "/line1[0-9]<CR>")
        self.assertEqual(pg.top, 9)  # line10
        pg2 = make(n=20, height=10)
        keys(pg2, "/line[<CR>")  # unbalanced bracket: invalid regex, falls back to a literal match
        self.assertEqual(pg2.msg, "Pattern not found")  # no literal "line[" in the text

    def test_match_spans_highlight_hits(self):
        pg = make(n=5, height=10)
        keys(pg, "/line3<CR>")
        self.assertEqual(pg.match_spans("line3"), [(0, 5)])
        self.assertEqual(pg.match_spans("line1"), [])

    def test_status_line_shows_percentage_and_end(self):
        pg = make(n=100, height=20)
        self.assertIn("lines 1-20/100", pg.status())
        self.assertIn("20%", pg.status())
        keys(pg, "G")
        self.assertIn("(END)", pg.status())

    def test_status_line_on_an_empty_file(self):
        pg = Pager("", "empty.txt")
        self.assertEqual(pg.status(), "empty.txt: (END)")

    def test_search_mode_status_shows_the_cmdline(self):
        pg = make(n=20, height=10)
        keys(pg, "/abc")
        self.assertEqual(pg.status(), "/abc")

    def test_unrecognized_keys_are_ignored(self):
        pg = make(n=20, height=10)
        keys(pg, "zZ!@#")
        self.assertEqual(pg.top, 0)
        self.assertFalse(pg.done)


class WrapTests(unittest.TestCase):
    def wrapped(self, text, width=10, height=3):
        pg = Pager(text, "f")
        pg.height, pg.width = height, width
        return pg

    def shown(self, pg):
        return [row for _, _, row in pg.visible()]

    def test_long_lines_wrap_and_nothing_is_lost(self):
        pg = self.wrapped("abcdefghijKLMNOPQRSTuvw\n\nxy\n", height=10)
        self.assertEqual(self.shown(pg), ["abcdefghij", "KLMNOPQRST", "uvw", "", "xy"])

    def test_a_line_of_exactly_the_width_does_not_add_a_row(self):
        self.assertEqual(self.shown(self.wrapped("a" * 10 + "\nb\n")), ["a" * 10, "b"])

    def test_one_line_taller_than_the_screen_scrolls_row_by_row(self):
        pg = self.wrapped("".join(str(i) * 10 for i in range(6)))
        keys(pg, "j")
        self.assertEqual(self.shown(pg), ["1" * 10, "2" * 10, "3" * 10])
        keys(pg, "G")
        self.assertEqual(self.shown(pg), ["3" * 10, "4" * 10, "5" * 10])
        self.assertTrue(pg.at_end)
        self.assertIn("lines 1-1/1", pg.status())

    def test_search_goes_to_the_row_holding_the_match(self):
        pg = self.wrapped("." * 30 + "HIT" + "." * 17 + "HIT\n" + "z\n" * 9)
        keys(pg, "/HIT<CR>")
        self.assertEqual(pg.top, 3)
        keys(pg, "n")
        self.assertEqual(pg.top, 5)
        keys(pg, "N")
        self.assertEqual(pg.top, 3)

    def test_resize_keeps_the_same_text_at_the_top(self):
        pg = self.wrapped("".join(chr(97 + i) * 10 for i in range(8)) + "\n" + "z\n" * 9)
        keys(pg, "jjjj")
        self.assertTrue(self.shown(pg)[0].startswith("e"))
        pg.width = 20
        self.assertIn("e", self.shown(pg)[0])
        pg.width = 5
        self.assertTrue(self.shown(pg)[0].startswith("e"))

    def test_tabs_are_expanded_before_wrapping(self):
        self.assertEqual(self.shown(self.wrapped("a\tbcd\n")), ["a       bc", "d"])

    def test_empty_file_wraps_to_nothing(self):
        pg = self.wrapped("")
        self.assertEqual(self.shown(pg), [])
        self.assertEqual(pg.status(), "f: (END)")

