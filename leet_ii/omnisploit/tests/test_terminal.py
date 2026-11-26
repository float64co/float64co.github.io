import unittest

from omnisploit.engine.world import World
from omnisploit.shell.shell import Shell
from omnisploit.ui.terminal import Terminal, sanitize, wrap


def typed(term, text, prompt="$ "):
    out = None
    for ch in text:
        out = term.key("<CR>" if ch == "\n" else ch, prompt)
    return out


class TerminalTests(unittest.TestCase):
    def setUp(self):
        self.sh = Shell(World())
        self.term = Terminal(self.sh.complete)
        self.term.history = self.sh.history

    def test_submit_echo_and_history_shared_with_shell(self):
        self.assertEqual(typed(self.term, "ls\n"), "ls")
        self.assertEqual(self.term.lines[-1].text, "$ ls")
        self.assertEqual(self.term.lines[-1].prompt_len, 2)
        typed(self.term, "pwd\n")
        self.assertEqual(self.sh.history, ["ls", "pwd"])
        typed(self.term, "pwd\n")  # consecutive duplicates are not repeated
        self.assertEqual(self.sh.history, ["ls", "pwd"])

    def test_history_navigation_and_draft(self):
        typed(self.term, "one\n")
        typed(self.term, "two\n")
        typed(self.term, "dra")
        self.term.key("<Up>", "$ ")
        self.assertEqual(self.term.buf, "two")
        self.term.key("<Up>", "$ ")
        self.assertEqual(self.term.buf, "one")
        self.term.key("<Down>", "$ ")
        self.term.key("<Down>", "$ ")
        self.assertEqual(self.term.buf, "dra")

    def test_line_editing_keys(self):
        typed(self.term, "hello world")
        self.term.key("\x17", "$ ")  # Ctrl-W
        self.assertEqual(self.term.buf, "hello ")
        self.term.key("\x01", "$ ")
        typed(self.term, ">> ")
        self.assertEqual(self.term.buf, ">> hello ")
        self.term.key("\x0b", "$ ")  # kill to end (cursor after '>> ')
        self.assertEqual(self.term.buf, ">> ")
        self.term.key("\x15", "$ ")
        self.assertEqual(self.term.buf, "")
        typed(self.term, "ab")
        self.term.key("<Left>", "$ ")
        self.term.key("<BS>", "$ ")
        self.assertEqual(self.term.buf, "b")

    def test_ctrl_c_and_ctrl_d(self):
        typed(self.term, "oops")
        self.assertIsNone(self.term.key("\x03", "$ "))
        self.assertEqual(self.term.buf, "")
        self.assertEqual(self.term.lines[-1].text, "$ oops^C")
        self.assertEqual(self.term.key("\x04", "$ "), "exit")

    def test_ctrl_d_submits_a_custom_eof_line(self):
        self.assertEqual(self.term.key("\x04", ">>> ", eof="exit()"), "exit()")
        self.assertEqual(self.term.lines[-1].text, ">>> exit()")
        self.assertNotIn("exit()", self.term.history)  # Ctrl-D never touches history, same as plain "exit"

    def test_ctrl_d_with_pending_text_still_deletes_forward(self):
        typed(self.term, "ab")
        self.term.pos = 0
        self.assertIsNone(self.term.key("\x04", "$ ", eof="exit()"))
        self.assertEqual(self.term.buf, "b")

    def test_tab_completion(self):
        typed(self.term, "cd motd")
        self.term.key("<Tab>", "$ ")
        self.assertEqual(self.term.buf, "cd motd-daemon/")
        self.term.buf, self.term.pos = "", 0
        typed(self.term, "ech")
        self.term.key("<Tab>", "$ ")
        self.assertEqual(self.term.buf, "echo ")

    def test_tab_lists_ambiguous_candidates(self):
        typed(self.term, "hist")
        self.term.key("<Tab>", "$ ")
        self.assertEqual(self.term.buf, "history ")
        self.term.buf, self.term.pos = "", 0
        typed(self.term, "ba")
        self.term.key("<Tab>", "$ ")
        self.assertEqual(self.term.buf, "bas")  # common prefix of base*/bash
        self.term.key("<Tab>", "$ ")
        self.assertIn("basename", self.term.lines[-1].text)

    def test_write_joins_partial_lines_and_sanitizes(self):
        self.term.write("name? ")
        self.term.write("Hello\nsecond")
        self.term.write(" half\n")
        self.assertEqual([l.text for l in self.term.lines], ["name? Hello", "second half"])
        self.assertEqual(sanitize("a\x00b\x1b[0m\tz\r\n"), "a.b.[0m    z\n")

    def test_layout_wraps_scrolls_and_places_cursor(self):
        for i in range(30):
            self.term.write(f"line {i}\n")
        rows, crow, ccol = self.term.layout("$ ", 20, 5)
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[-1][2].rstrip(), "$")
        self.assertEqual((crow, ccol), (4, 2))
        self.term.key("<PgUp>", "$ ")
        rows, crow, _ = self.term.layout("$ ", 20, 5)
        self.assertIsNone(crow)  # cursor hidden while scrolled back
        self.term.scroll = 10_000
        self.term.layout("$ ", 20, 5)
        self.assertLessEqual(self.term.scroll, 30)  # clamped to the scrollback size
        self.assertEqual(wrap("abcdef", 4), [(0, "abcd"), (4, "ef")])
        self.assertEqual(wrap("", 4), [(0, "")])

    def test_cursor_on_wrapped_input_and_at_exact_width(self):
        typed(self.term, "x" * 18)
        rows, crow, ccol = self.term.layout("$ ", 20, 5)
        self.assertEqual((crow, ccol), (len(rows) - 1, 0))  # wraps to the next row

    def test_scrollback_is_bounded(self):
        for i in range(4000):
            self.term.write(f"{i}\n")
        self.assertLessEqual(len(self.term.lines), 3000)

    def test_shell_prompt_drives_continuation_echo(self):
        out = self.sh.run_line("for i in 1 2; do")
        self.assertEqual(self.sh.prompt(), "> ")
        typed(self.term, "echo $i\n", self.sh.prompt())
        self.assertEqual(self.term.lines[-1].text, "> echo $i")


if __name__ == "__main__":
    unittest.main()
