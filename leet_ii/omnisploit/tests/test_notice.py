import unittest

from omnisploit.engine.cpu import Cpu
from omnisploit.engine.machine import load_all_levels, Process
from omnisploit.engine.missions import NOTICE_OFFSET
from omnisploit.engine.world import World
from omnisploit.shell.shell import Shell


def run(sh, line):
    out = sh.run_line(line)
    o = b"".join(d for d, e in out.chunks if not e)
    e = b"".join(d for d, e in out.chunks if e)
    return o, e


class PrintfEngineTests(unittest.TestCase):
    """Direct tests of the general printf implementation in engine/cpu.py."""

    def setUp(self):
        lv = load_all_levels()["motd-daemon"]  # any level works; printf's args don't depend on it
        self.cpu = Cpu(lv, b"")
        self.cpu.regs.update(rsi=0x1111111122222222, rdx=0x33, rcx=0x44, r8=0x55, r9=0x66)
        self.call_rsp = self.cpu.base + 0x100

    def fmt(self, s: bytes) -> bytes:
        return self.cpu._printf_format(s, self.call_rsp)

    def test_plain_text_is_unchanged(self):
        self.assertEqual(self.fmt(b"hello world\n"), b"hello world\n")

    def test_bare_x_is_the_low_32_bits(self):
        self.assertEqual(self.fmt(b"%x"), b"22222222")

    def test_long_modifier_is_the_full_64_bits(self):
        self.assertEqual(self.fmt(b"%lx"), b"1111111122222222")

    def test_zero_padded_width(self):
        self.assertEqual(self.fmt(b"%08x"), b"22222222")
        self.cpu.regs["rdx"] = 0x33
        self.assertEqual(self.fmt(b"%08x.%08x"), b"22222222.00000033")

    def test_positional_arguments(self):
        self.assertEqual(self.fmt(b"%2$x %1$x"), b"33 22222222")
        self.assertEqual(self.fmt(b"%4$x"), b"55")

    def test_stack_arguments_beyond_the_five_registers(self):
        self.cpu.write(self.call_rsp + 8, (0x7777).to_bytes(8, "little"))
        self.assertEqual(self.fmt(b"%6$x"), b"7777")
        self.cpu.write(self.call_rsp + 16, (0x8888).to_bytes(8, "little"))
        self.assertEqual(self.fmt(b"%7$x"), b"8888")

    def test_pointer_format(self):
        self.assertEqual(self.fmt(b"%p"), b"0x1111111122222222")

    def test_signed_decimal(self):
        self.cpu.regs["rsi"] = 0xFFFFFFFFFFFFFFFF  # -1 at any width
        self.assertEqual(self.fmt(b"%d"), b"-1")
        self.assertEqual(self.fmt(b"%ld"), b"-1")

    def test_unsigned_and_octal(self):
        self.cpu.regs["rsi"] = 8
        self.assertEqual(self.fmt(b"%u"), b"8")
        self.assertEqual(self.fmt(b"%o"), b"10")

    def test_char(self):
        self.cpu.regs["rsi"] = ord("Z")
        self.assertEqual(self.fmt(b"%c"), b"Z")

    def test_literal_percent(self):
        self.assertEqual(self.fmt(b"100%%"), b"100%")

    def test_percent_s_dereferences_and_faults_on_bad_pointer(self):
        self.cpu.regs["rsi"] = 0xDEADBEEF  # unmapped
        self.assertIsNone(self.fmt(b"%s"))

    def test_percent_s_reads_a_real_string(self):
        addr = self.cpu.base - 1  # just inside the mapped stack-ish window; write a C string there
        self.cpu.write(self.cpu.base, b"hi\0")
        self.cpu.regs["rsi"] = self.cpu.base
        self.assertEqual(self.fmt(b"got: %s!"), b"got: hi!")

    def test_running_off_the_end_of_the_stack_window_faults(self):
        self.assertIsNone(self.fmt(b"%10000$x"))  # far past the mapped stack window


class MotdStillOnlySubstitutesS(unittest.TestCase):
    """The pre-existing levels pass a literal format string; player input is only ever a %s value."""

    def test_format_specifiers_in_input_are_not_reinterpreted(self):
        lv = load_all_levels()["motd-daemon"]
        res = Process(lv).feed(b"%x.%x.%p\n")
        self.assertEqual(res.stdout, b"Hello, %x.%x.%p\n")
        self.assertIsNone(res.crash)


class NoticeLevelTests(unittest.TestCase):
    def setUp(self):
        self.sh = Shell(World())
        run(self.sh, "cd notice")

    def test_plain_input_is_echoed_back_unharmed(self):
        # printf(buf) with no format literal adds no trailing newline of its own, unlike printf("%s\n", buf)
        o, _ = run(self.sh, "echo hello | ./notice")
        self.assertEqual(o, b"notice: hello")

    def test_bare_x_probes_are_safe(self):
        o, e = run(self.sh, "printf '%%x.%%x.%%x.%%x\\n' | ./notice")
        self.assertEqual(e, b"")
        self.assertTrue(o.startswith(b"notice: "))
        self.assertNotIn(b"FLAG", o)

    def test_percent_s_blind_probe_can_crash(self):
        o, e = run(self.sh, "printf '%%s\\n' | ./notice")
        self.assertIn(b"Segmentation fault", e)

    def test_the_documented_offset_leaks_the_flag(self):
        o, _ = run(self.sh, f"printf '%{NOTICE_OFFSET}$s\\n' | ./notice")
        self.assertIn(b"FLAG{format_strings_leak_everything}", o)

    def test_offset_as_hex_reveals_a_pointer_into_the_binarys_data(self):
        o, _ = run(self.sh, f"printf '%{NOTICE_OFFSET}$x' | ./notice")
        self.assertRegex(o, rb"notice: 40[0-9a-f]+$")

    def test_checksec_shows_no_canary(self):
        o, _ = run(self.sh, "checksec ./notice")
        self.assertIn(b"No canary found", o)

    def test_strings_does_not_reveal_the_flag_without_running_it(self):
        o, _ = run(self.sh, "strings notice")
        self.assertIn(b"FLAG{format_strings_leak_everything}", o)  # it is a string constant, but reading it is not solving

    def test_r2_shows_the_vulnerable_call_but_not_the_flag_text_in_disassembly(self):
        run(self.sh, "r2 -A ./notice")
        o, _ = run(self.sh, "pdf @ main")
        self.assertIn("call sym.imp.printf", o.decode())
        self.assertIn('; var char *secret @ rbp-0x8', o.decode())
        run(self.sh, "q")

    def test_gdb_can_inspect_the_secret_pointer_on_the_stack(self):
        run(self.sh, "gdb -q ./notice")
        run(self.sh, "break *0x4011ad")  # the vulnerable printf call
        run(self.sh, "run")
        run(self.sh, "x")
        o, _ = run(self.sh, "x/20gx $rsp")
        self.assertIn("40200d", o.decode())  # the flag string's address, sitting on the stack (gdb zero-pads it)
        run(self.sh, "c")


class NoticeMissionTests(unittest.TestCase):
    def setUp(self):
        self.sh = Shell(World())
        run(self.sh, "mkdir ~/workspace")

    def completed(self):
        self.sh.world.missions.evaluate()
        return list(self.sh.world.missions.completed)

    def test_requires_both_the_offset_and_the_flag(self):
        run(self.sh, f"echo {NOTICE_OFFSET} > ~/workspace/notice-offset.txt")
        self.assertEqual(self.completed(), [])
        run(self.sh, f"printf '%{NOTICE_OFFSET}$s\\n' | ~/notice/notice")
        self.assertEqual(self.completed(), ["notice"])

    def test_wrong_offset_does_not_complete_it(self):
        run(self.sh, "echo 7 > ~/workspace/notice-offset.txt")
        run(self.sh, f"printf '%{NOTICE_OFFSET}$s\\n' | ~/notice/notice")
        self.assertEqual(self.completed(), [])
