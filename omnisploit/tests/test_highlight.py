import unittest

from omnisploit.ui.highlight import highlight, kind_for


def styled(line, spans):
    return [(line[s:e], st) for s, e, st in spans]


class HighlightTests(unittest.TestCase):
    def test_kind_detection(self):
        self.assertEqual(kind_for("a.py"), "python")
        self.assertEqual(kind_for("run.sh"), "sh")
        self.assertEqual(kind_for("noext", "#!/bin/bash"), "sh")
        self.assertEqual(kind_for("tool", "#!/usr/bin/env python3"), "python")
        self.assertIsNone(kind_for("notes.txt", "hello"))

    def test_python_basics(self):
        line = "def f(x): return len(x) + 0x1F  # done"
        got = styled(line, highlight([line], "python")[0])
        self.assertIn(("def", "kw"), got)
        self.assertIn(("f", "def"), got)
        self.assertIn(("return", "kw"), got)
        self.assertIn(("len", "bi"), got)
        self.assertIn(("0x1F", "num"), got)
        self.assertIn(("# done", "com"), got)

    def test_python_strings_and_hash_inside_string(self):
        line = 'x = "a # not a comment" + b\'\\x41\' + f"{y}"'
        got = styled(line, highlight([line], "python")[0])
        self.assertIn(('"a # not a comment"', "str"), got)
        self.assertIn(("b'\\x41'", "str"), got)
        self.assertIn(('f"{y}"', "str"), got)
        self.assertFalse(any(s == "com" for _, s in got))

    def test_python_triple_quotes_span_lines(self):
        lines = ['s = """abc', "still # inside", 'end""" + x', "y = 1"]
        h = highlight(lines, "python")
        self.assertEqual(styled(lines[0], h[0])[-1], ('"""abc', "str"))
        self.assertEqual(h[1], [(0, len(lines[1]), "str")])
        self.assertEqual(styled(lines[2], h[2])[0], ('end"""', "str"))
        self.assertEqual(h[3], [(4, 5, "num")])

    def test_python_unterminated_string_and_escapes(self):
        line = "x = 'abc"
        self.assertEqual(styled(line, highlight([line], "python")[0])[-1], ("'abc", "str"))
        line = "x = 'it\\'s'"
        self.assertEqual(styled(line, highlight([line], "python")[0])[-1], ("'it\\'s'", "str"))

    def test_decorator_and_identifiers_containing_keywords(self):
        lines = ["@property", "    notify = 1", "class Foo: pass"]
        h = highlight(lines, "python")
        self.assertEqual(styled(lines[0], h[0]), [("@property", "def")])
        self.assertEqual(h[1][-1][2], "num")
        self.assertFalse(any(st == "kw" for _, st in styled(lines[1], h[1])))  # 'notify' is not 'not'
        self.assertIn(("Foo", "def"), styled(lines[2], h[2]))

    def test_shell(self):
        lines = ["#!/bin/bash", 'for i in $(seq 3); do echo "n=$i" # c', "done; echo $HOME ${X}"]
        h = highlight(lines, "sh")
        self.assertEqual(h[0], [(0, 11, "com")])
        got = styled(lines[1], h[1])
        self.assertIn(("for", "kw"), got)
        self.assertIn(("in", "kw"), got)
        self.assertIn(("do", "kw"), got)
        self.assertIn(('"n=$i"', "str"), got)
        self.assertIn(("# c", "com"), got)
        got = styled(lines[2], h[2])
        self.assertIn(("done", "kw"), got)
        self.assertIn(("$HOME", "var"), got)
        self.assertIn(("${X}", "var"), got)

    def test_unknown_kind_and_robustness(self):
        self.assertEqual(highlight(["a", "b"], None), [[], []])
        for junk in ["", "'", '"""', "\\", "$", "# ", "\t\t", "é = 1", "x = (", "0x", "1e"]:
            highlight([junk, junk], "python")
            highlight([junk, junk], "sh")


if __name__ == "__main__":
    unittest.main()
