import unittest

from omnisploit.engine.world import World
from omnisploit.shell.r2 import R2Session
from omnisploit.shell.shell import Shell


def run(sh, line):
    out = sh.run_line(line)
    o = b"".join(d for d, e in out.chunks if not e).decode(errors="replace")
    e = b"".join(d for d, e in out.chunks if e).decode(errors="replace")
    return o, e


class R2Tests(unittest.TestCase):
    def setUp(self):
        self.sh = Shell(World())
        run(self.sh, "cd motd-daemon")
        run(self.sh, "r2 ./motd")

    def test_session_prompt_follows_the_seek(self):
        self.assertIsInstance(self.sh.session, R2Session)
        self.assertEqual(self.sh.prompt(), "[0x00401060]> ")
        run(self.sh, "s sym.win")
        self.assertEqual(self.sh.prompt(), "[0x00401196]> ")
        run(self.sh, "s+4")
        self.assertEqual(run(self.sh, "s")[0], "0x40119a\n")
        run(self.sh, "q")
        self.assertIsNone(self.sh.session)

    def test_functions_are_unknown_until_analysis(self):
        self.assertNotIn("sym.vuln", run(self.sh, "afl")[0])
        self.assertIn("Cannot find function", run(self.sh, "pdf @ sym.vuln")[1])
        self.assertIn("(aa)", run(self.sh, "aaa")[0])
        self.assertIn("sym.vuln", run(self.sh, "afl")[0])

    def test_pdf_matches_the_binary(self):
        run(self.sh, "aa")
        o, _ = run(self.sh, "pdf @ sym.vuln")
        self.assertIn("┌ 69: sym.vuln ();", o)
        self.assertIn("; var char *s @ rbp-0x40", o)
        self.assertIn("; CALL XREF from main @ 0x4011f7", o)
        self.assertIn("0x004011ce      e86dfeffff     call sym.imp.gets", o)
        self.assertIn("; char *gets(char *s)", o)
        self.assertIn("└           0x004011ed      c3             ret", o)

    def test_bytes_agree_with_xxd(self):
        o, _ = run(self.sh, "px 16 @ sym.win")
        self.assertIn("0x00401196  5548 89e5 488d 3d75 0e00 00e8 aafe ffff  UH..H.=u........", o)
        self.assertEqual(run(self.sh, "p8 4 @ sym.win")[0], "554889e5\n")
        run(self.sh, "q")
        x, _ = run(self.sh, "xxd -s 0x1196 -l 16 motd")
        self.assertIn("5548 89e5 488d 3d75 0e00 00e8 aafe ffff", x)

    def test_info_and_strings(self):
        o, _ = run(self.sh, "iI")
        self.assertIn("canary   false", o)
        self.assertIn("nx       true", o)
        self.assertIn("relro    partial", o)
        self.assertIn("0x00402004 6   7    .rodata ascii name? ", run(self.sh, "iz")[0])
        self.assertIn("GLOBAL FUNC       gets", run(self.sh, "ii")[0])
        self.assertEqual(run(self.sh, "iI~canary")[0], "canary   false\n")

    def test_xrefs(self):
        run(self.sh, "aa")
        self.assertEqual(run(self.sh, "axt sym.imp.gets")[0], "sym.vuln 0x4011ce [CALL:--x] call sym.imp.gets\n")
        self.assertIn("[DATA:r--] lea rdi, str.name_", run(self.sh, "axt str.name_")[0])

    def test_calculator_and_chaining(self):
        self.assertEqual(run(self.sh, "?v sym.win+4")[0], "0x40119a\n")
        self.assertEqual(run(self.sh, "?v 0x48-8")[0], "0x40\n")
        self.assertEqual(run(self.sh, "s 0x401000; s")[0], "0x401000\n")

    def test_errors_and_limits(self):
        self.assertIn("Invalid command 'frob'", run(self.sh, "frob")[1])
        self.assertIn("Invalid address", run(self.sh, "s nosuch")[1])
        self.assertIn("read-only", run(self.sh, "w")[1])

    def test_launch_variants(self):
        run(self.sh, "q")
        o, _ = run(self.sh, "r2 -A ./motd")
        self.assertIn("(aaft)", o)
        self.assertIn("main ();", run(self.sh, "pdf @ main")[0])
        run(self.sh, "q")
        self.assertIn("-d", run(self.sh, "r2 -d ./motd")[1])
        self.assertIn("Cannot open", run(self.sh, "r2 nope")[1])
        self.assertIn("no terminal", run(self.sh, "echo x | r2 motd")[1])

    def test_raw_files_open_as_hexdumps(self):
        run(self.sh, "q")
        run(self.sh, "r2 /srv/drop/artifact.bin")
        self.assertIn("BLB", run(self.sh, "px 16")[0])
        self.assertIn("ORCHID-NNNN", run(self.sh, "iz")[0])
        self.assertIn("needs an analyzable ELF", run(self.sh, "ii")[1])
