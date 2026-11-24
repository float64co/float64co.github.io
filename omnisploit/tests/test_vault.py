import unittest

from omnisploit.engine.world import World
from omnisploit.shell.shell import Shell


def run(sh, line):
    out = sh.run_line(line)
    o = b"".join(d for d, e in out.chunks if not e).decode(errors="replace")
    e = b"".join(d for d, e in out.chunks if e).decode(errors="replace")
    return o, e


class VaultTests(unittest.TestCase):
    def setUp(self):
        self.sh = Shell(World())
        run(self.sh, "cd vault")

    def test_interactive_run(self):
        run(self.sh, "./vault")
        self.assertEqual(self.sh.prompt(), "pass: ")
        o, _ = run(self.sh, "nope")
        self.assertEqual(o, "Wrong.\n")

    def test_whitespace_splits_the_token(self):
        o, _ = run(self.sh, "echo 'N1ghtOwl extra' | ./vault")
        self.assertIn("FLAG{", o)

    def test_long_input_is_cut_off_by_the_scanf_width(self):
        o, e = run(self.sh, "python3 -c 'print(\"A\" * 500)' | ./vault")
        self.assertEqual((o, e), ("pass: Wrong.\n", ""))  # %31s cannot overflow

    def test_gdb_can_read_both_strcmp_arguments(self):
        run(self.sh, "gdb -q ./vault")
        run(self.sh, "break strcmp")
        run(self.sh, "run")
        o, _ = run(self.sh, "hunter2")
        self.assertIn("Breakpoint 1, 0x0000000000401050 in strcmp@plt ()", o)
        self.assertIn('"hunter2"', run(self.sh, "x/s $rdi")[0])
        self.assertIn('"N1ghtOwl"', run(self.sh, "x/s $rsi")[0])
        self.assertIn("#1  0x00000000004011b9 in check_password ()", run(self.sh, "bt")[0])
        run(self.sh, "finish")
        self.assertEqual(run(self.sh, "p $eax")[0], "$1 = 1\n")  # strcmp says "greater"

    def test_stepping_the_comparison_branch(self):
        run(self.sh, "gdb -q ./vault")
        run(self.sh, "break *0x4011fa")  # the jne after the check
        run(self.sh, "run")
        run(self.sh, "N1ghtOwl")
        self.assertEqual(run(self.sh, "p $eax")[0], "$1 = 0\n")
        o, _ = run(self.sh, "stepi")
        self.assertEqual(o, "0x00000000004011fc in main ()\n")  # not taken: the success path

    def test_disassembly_agrees_between_tools(self):
        run(self.sh, "r2 ./vault")
        run(self.sh, "aa")
        pdf, _ = run(self.sh, "pdf @ sym.check_password")
        self.assertIn("c645f04e       mov byte [rbp-0x10], 0x4e", pdf)
        self.assertIn("call sym.imp.strcmp        ; int strcmp(const char *s1, const char *s2)", pdf)
        run(self.sh, "q")
        run(self.sh, "gdb -q ./vault")
        d, _ = run(self.sh, "disassemble check_password")
        self.assertIn("mov    BYTE PTR [rbp-0x10],0x4e", d)
        self.assertIn("jne    0x401216 <main+91>", run(self.sh, "disassemble main")[0])
