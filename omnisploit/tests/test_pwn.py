import unittest

from omnisploit.engine.world import World
from omnisploit.shell.shell import Shell


def run(sh, line):
    out = sh.run_line(line)
    o = b"".join(d for d, e in out.chunks if not e).decode(errors="replace")
    e = b"".join(d for d, e in out.chunks if e).decode(errors="replace")
    return o, e


class PwnTests(unittest.TestCase):
    def setUp(self):
        self.sh = Shell(World())
        run(self.sh, "cd motd-daemon")

    def test_cyclic_pattern_and_lookup(self):
        self.assertEqual(run(self.sh, "cyclic 12")[0], "aaaabaaacaaa\n")
        self.assertEqual(run(self.sh, "cyclic -l 0x62616161")[0], "1\n")  # "aaab" read little-endian
        self.assertEqual(run(self.sh, "cyclic -l baaa")[0], "4\n")
        self.assertIn("not found", run(self.sh, "cyclic -l aa1a")[1])
        self.assertEqual(run(self.sh, "pwn cyclic 4")[0], "aaaa\n")

    def test_crash_workflow(self):
        # feed a pattern to the program, read the crash address in gdb, look up the offset
        run(self.sh, "cyclic 100 > /tmp/pat")
        run(self.sh, "gdb -q ./motd")
        o, _ = run(self.sh, "run < /tmp/pat")
        self.assertIn("Program received signal SIGSEGV", o)
        o, _ = run(self.sh, "x/gx $rsp")
        self.assertIn("0x", o)

    def test_hex_unhex_phd(self):
        self.assertEqual(run(self.sh, "pwn hex ABC")[0], "414243\n")
        self.assertEqual(run(self.sh, "pwn unhex 414243")[0], "ABC")
        o, _ = run(self.sh, "echo hi | pwn phd")
        self.assertIn("68 69 0a", o)
        self.assertIn("│hi.│", o)

    def test_pwn_dispatch(self):
        self.assertIn("RELRO", run(self.sh, "pwn checksec ./motd")[0])
        self.assertIn("invalid choice", run(self.sh, "pwn frob")[1])
