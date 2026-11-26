import unittest

from omnisploit.engine.machine import p64
from omnisploit.engine.world import World
from omnisploit.shell.gdb import GdbSession
from omnisploit.shell.shell import Shell


def run(sh, line):
    out = sh.run_line(line)
    o = b"".join(d for d, e in out.chunks if not e).decode(errors="replace")
    e = b"".join(d for d, e in out.chunks if e).decode(errors="replace")
    return o, e


class GdbTests(unittest.TestCase):
    def setUp(self):
        self.sh = Shell(World())
        run(self.sh, "cd motd-daemon")
        self.sh.vfs.install("/tmp/crash", b"A" * 72 + b"CCCCCC\0\0\n")
        self.sh.vfs.install("/tmp/win", b"A" * 72 + p64(0x401196) + b"\n")
        run(self.sh, "gdb -q ./motd")

    def test_session_and_exit(self):
        self.assertIsInstance(self.sh.session, GdbSession)
        self.assertEqual(self.sh.prompt(), "(gdb) ")
        run(self.sh, "quit")
        self.assertIsNone(self.sh.session)

    def test_full_banner_without_q(self):
        run(self.sh, "q")
        o, _ = run(self.sh, "gdb ./motd")
        self.assertIn("GNU gdb", o)
        self.assertIn("Reading symbols from ./motd...", o)

    def test_missing_and_non_elf(self):
        run(self.sh, "q")
        self.assertIn("No such file", run(self.sh, "gdb -q nope")[1])
        run(self.sh, "q")
        self.assertIn("not in executable format", run(self.sh, "gdb -q README.txt")[1])
        self.assertIn("No symbol table", run(self.sh, "disassemble win")[1])

    def test_needs_terminal(self):
        run(self.sh, "q")
        self.assertIn("no terminal", run(self.sh, "echo x | gdb")[1])

    def test_run_reads_a_line_then_exits(self):
        o, _ = run(self.sh, "run")
        self.assertIn("Starting program: /home/player/motd-daemon/motd", o)
        self.assertEqual(self.sh.prompt(), "name? ")
        o, _ = run(self.sh, "bob")
        self.assertIn("Hello, bob", o)
        self.assertIn("exited normally", o)
        self.assertEqual(self.sh.prompt(), "(gdb) ")
        self.assertIn("no registers", run(self.sh, "info registers")[1])

    def test_crash_is_inspectable(self):
        run(self.sh, "run")
        o, _ = run(self.sh, "A" * 100)
        self.assertIn("Program received signal SIGSEGV, Segmentation fault.", o)
        self.assertIn("0x00000000004011ed in vuln ()", o)
        self.assertIn("crash:motd-daemon", self.sh.world.events)
        o, _ = run(self.sh, "info registers rip rbp")
        self.assertIn("0x4011ed <vuln+68>", o)
        self.assertIn("0x4141414141414141", o)
        o, _ = run(self.sh, "x/2gx $rsp-16")
        self.assertIn("0x4141414141414141", o)

    def test_cyclic_style_return_address(self):
        o, _ = run(self.sh, "run < /tmp/crash")
        self.assertIn("0x0000434343434343 in ?? ()", o)
        self.assertEqual(run(self.sh, "p/x $rip")[0], "$1 = 0x434343434343\n")
        self.assertEqual(run(self.sh, "x/s $rsp-8")[0].split("\t")[1], '"CCCCCC"\n')

    def test_win_path_solves(self):
        o, _ = run(self.sh, "run < /tmp/win")
        self.assertIn("FLAG{ret2win_is_just_the_beginning}", o)
        self.assertIn("solved:motd-daemon", self.sh.world.events)

    def test_disassembly_is_intel_gdb_style(self):
        o, _ = run(self.sh, "disassemble vuln")
        self.assertIn("Dump of assembler code for function vuln:", o)
        self.assertIn("<+4>:\tsub    rsp,0x40", o)
        self.assertIn("lea    rdi,[rip+0xe4c]        # 0x402004", o)
        self.assertIn("call   0x401040 <gets@plt>", o)
        self.assertIn("lea    rax,[rbp-0x40]", o)
        self.assertTrue(o.endswith("End of assembler dump.\n"))

    def test_static_inspection_without_running(self):
        self.assertIn("0x0000000000401196  win", run(self.sh, "info functions")[0])
        self.assertEqual(run(self.sh, "x/s 0x402004")[0], '0x402004:\t"name? "\n')
        self.assertEqual(run(self.sh, "p win")[0], "$1 = {<text variable, no debug info>} 0x401196 <win>\n")
        self.assertEqual(run(self.sh, "p win+4")[0], "$2 = (<text variable, no debug info> *) 0x40119a <win+4>\n")
        self.assertEqual(run(self.sh, "info symbol 0x4011ed")[0], "vuln + 68 in section .text\n")
        self.assertIn("No registers", run(self.sh, "p $rip")[1])

    def test_errors(self):
        self.assertIn('Undefined command: "frob"', run(self.sh, "frob")[1])
        self.assertIn('No symbol "nosuch"', run(self.sh, "p nosuch")[1])
        self.assertIn("Cannot access memory at address 0x1234", run(self.sh, "x/x 0x1234")[1])
        self.assertIn("not being run", run(self.sh, "continue")[1])
        self.assertIn('Function "nosuch" not defined.', run(self.sh, "break nosuch")[1])
        self.assertIn("No registers", run(self.sh, "watch $rsp")[1])  # watch is simulated, but needs a live process
        self.assertIn("No breakpoint number 9", run(self.sh, "delete 9")[1])

    def test_continue_kills_a_crashed_process(self):
        run(self.sh, "run < /tmp/crash")
        o, _ = run(self.sh, "continue")
        self.assertIn("Program terminated with signal SIGSEGV", o)
        self.assertIn("not being run", run(self.sh, "kill")[1])


class BreakpointFixture(unittest.TestCase):
    def setUp(self):
        self.sh = Shell(World())
        run(self.sh, "cd motd-daemon")
        self.sh.vfs.install("/tmp/crash", b"A" * 72 + b"CCCCCC\0\0\n")
        run(self.sh, "gdb -q ./motd")

    def stack_line(self):
        return run(self.sh, "x/gx $rsp")[0]


class BreakpointTests(BreakpointFixture):
    def test_function_breakpoint_skips_the_prologue(self):
        self.assertEqual(run(self.sh, "break vuln")[0], "Breakpoint 1 at 0x4011ad\n")
        self.assertEqual(run(self.sh, "break *vuln")[0], "Breakpoint 2 at 0x4011a9\n")
        self.assertEqual(run(self.sh, "tbreak main")[0], "Temporary breakpoint 3 at 0x4011f2\n")

    def test_hit_stops_before_the_instruction_and_bt_unwinds(self):
        run(self.sh, "break vuln")
        o, _ = run(self.sh, "run")
        self.assertIn("Breakpoint 1, 0x00000000004011ad in vuln ()", o)
        o, _ = run(self.sh, "bt")
        self.assertEqual(o, "#0  0x00000000004011ad in vuln ()\n#1  0x00000000004011fc in main ()\n")
        self.assertIn("0x4011ad <vuln+4>", run(self.sh, "info registers rip")[0])
        self.assertIn("sub    rsp,0x40", run(self.sh, "x/i $pc")[0])

    def test_program_waits_in_gets_after_continue(self):
        run(self.sh, "break vuln")
        run(self.sh, "run")
        o, _ = run(self.sh, "continue")
        self.assertEqual(o, "Continuing.\n")
        self.assertEqual(self.sh.prompt(), "name? ")
        o, _ = run(self.sh, "bob")
        self.assertIn("Hello, bob", o)
        self.assertIn("exited normally", o)

    def test_tbreak_is_deleted_and_hits_are_counted(self):
        run(self.sh, "tbreak vuln")
        run(self.sh, "break *0x4011ce")
        run(self.sh, "run")
        self.assertNotIn("Temporary", run(self.sh, "info breakpoints")[0])
        run(self.sh, "continue")  # on to the second breakpoint, just before gets
        o, _ = run(self.sh, "info breakpoints")
        self.assertIn("breakpoint already hit 1 time", o)
        self.assertEqual(o.count("keep y"), 1)

    def test_disable_delete(self):
        run(self.sh, "break vuln")
        run(self.sh, "break main")
        run(self.sh, "disable 2")
        self.assertIn("keep n", run(self.sh, "info breakpoints")[0])
        o, _ = run(self.sh, "run")
        self.assertIn("Breakpoint 1,", o)
        run(self.sh, "delete")
        self.assertEqual(run(self.sh, "info breakpoints")[0], "No breakpoints or watchpoints.\n")

    def test_stepi_nexti_and_next(self):
        run(self.sh, "start")
        self.assertIn("mov    eax,0", run(self.sh, "x/i $pc")[0])
        self.assertEqual(run(self.sh, "si")[0], "0x00000000004011f7 in main ()\n")
        self.assertEqual(run(self.sh, "si")[0], "0x00000000004011a9 in vuln ()\n")  # into the call
        run(self.sh, "ni"); run(self.sh, "ni")
        o, _ = run(self.sh, "next")
        self.assertIn("Single stepping until exit from function vuln", o)
        self.assertEqual(self.sh.prompt(), "name? ")  # the program wants its line
        o, _ = run(self.sh, "zed")
        self.assertIn("Hello, zed\n0x00000000004011fc in main ()", o)

    def test_nexti_steps_over_a_call(self):
        run(self.sh, "break *0x4011f7")  # the call to vuln in main
        run(self.sh, "run < /tmp/crash")
        o, _ = run(self.sh, "nexti")
        self.assertIn("Program received signal SIGSEGV", o)

    def test_finish_returns_to_the_caller(self):
        run(self.sh, "break vuln")
        run(self.sh, "run")
        o, _ = run(self.sh, "finish")
        self.assertIn("Run till exit from #0  0x00000000004011ad in vuln ()", o)
        run(self.sh, "zed")
        self.assertTrue(self.sh.prompt() == "(gdb) ")

    def test_inspect_the_smashed_stack_at_the_ret(self):
        run(self.sh, "break *0x4011ed")  # the ret in vuln
        o, _ = run(self.sh, "run < /tmp/crash")
        self.assertIn("Breakpoint 1, 0x00000000004011ed in vuln ()", o)
        self.assertEqual(run(self.sh, "x/gx $rsp")[0], "0x7fffffffdfd8:\t0x0000434343434343\n")
        self.assertIn("#1  0x0000434343434343 in ?? ()", run(self.sh, "bt")[0])
        self.assertEqual(run(self.sh, "stepi")[0], "0x0000434343434343 in ?? ()\n")  # the ret itself worked
        o, _ = run(self.sh, "stepi")
        self.assertIn("Program received signal SIGSEGV", o)
        self.assertIn("0x0000434343434343 in ?? ()", o)

    def test_non_canonical_return_faults_at_the_ret(self):
        self.sh.vfs.install("/tmp/nc", b"A" * 72 + b"C" * 8 + b"\n")
        run(self.sh, "break *0x4011ed")
        run(self.sh, "run < /tmp/nc")
        o, _ = run(self.sh, "stepi")
        self.assertIn("Program received signal SIGSEGV", o)
        self.assertIn("0x00000000004011ed in vuln ()", o)  # #GP is raised at the ret itself
        self.assertEqual(run(self.sh, "x/gx $rsp")[0], "0x7fffffffdfd8:\t0x4343434343434343\n")

    def test_breakpoint_at_a_libc_stub(self):
        run(self.sh, "break gets")
        o, _ = run(self.sh, "run")
        self.assertIn("Breakpoint 1, 0x0000000000401040 in gets@plt ()", o)
        self.assertEqual(run(self.sh, "bt")[0].splitlines()[1], "#1  0x00000000004011d3 in vuln ()")
        self.assertEqual(run(self.sh, "x/gx $rsp")[0], "0x7fffffffdf88:\t0x00000000004011d3\n")

    def test_rop_into_the_middle_of_a_function(self):
        # landing at win+4 skips `push rbp; mov rbp,rsp` and still runs system()
        self.sh.vfs.install("/tmp/mid", b"A" * 72 + p64(0x40119A) + b"\n")
        o, _ = run(self.sh, "run < /tmp/mid")
        self.assertIn("FLAG{", o)
        self.assertIn("solved:motd-daemon", self.sh.world.events)


class EngineParityTests(unittest.TestCase):
    """The stepper must agree with what `./motd` prints for the classic inputs."""

    def test_plain_and_smashed_inputs(self):
        sh = Shell(World())
        run(sh, "cd motd-daemon")
        self.assertEqual(run(sh, "echo bob | ./motd")[0], "name? Hello, bob\n")
        sh.vfs.install("/tmp/p", b"A" * 72 + p64(0x401196) + b"\n")
        o, e = run(sh, "./motd < /tmp/p")
        self.assertIn("FLAG{ret2win_is_just_the_beginning}", o)
        self.assertIn("Segmentation fault", e)  # win() returns into a zeroed slot


class WatchpointTests(unittest.TestCase):
    def setUp(self):
        self.sh = Shell(World())
        run(self.sh, "cd sentry")
        self.sh.vfs.install("/tmp/safe", b"hi\n")
        self.sh.vfs.install("/tmp/overflow", b"A" * 80 + b"\n")
        run(self.sh, "gdb -q ./sentry")

    def test_watch_fires_when_the_canary_is_planted(self):
        run(self.sh, "watch *0x7fffffffdfc8")
        o, _ = run(self.sh, "run < /tmp/safe")
        self.assertIn("Hardware watchpoint 1: *0x7fffffffdfc8", o)
        self.assertIn("Old value = 0x0", o)
        self.assertIn("New value = 0xbaadf000", o)
        self.assertIn("in vuln ()", o)

    def test_watch_fires_again_when_the_canary_is_smashed(self):
        run(self.sh, "watch *0x7fffffffdfc8")
        run(self.sh, "run < /tmp/safe")
        run(self.sh, "c")  # let the first (harmless) run finish
        run(self.sh, "run < /tmp/overflow")  # second hit: the canary being planted again
        o, _ = run(self.sh, "c")
        self.assertIn("Old value = 0xbaadf000", o)
        self.assertIn("New value = 0x4141414141414141", o)

    def test_info_breakpoints_counts_watch_hits(self):
        run(self.sh, "watch *0x7fffffffdfc8")
        run(self.sh, "run < /tmp/safe")
        o, _ = run(self.sh, "info breakpoints")
        self.assertIn("hw watchpoint", o)
        self.assertIn("*0x7fffffffdfc8", o)
        self.assertIn("watchpoint already hit 1 time", o)

    def test_watch_shares_numbering_with_breakpoints(self):
        run(self.sh, "break vuln")
        o, _ = run(self.sh, "watch *0x7fffffffdfc8")
        self.assertIn("Hardware watchpoint 2:", o)

    def test_bare_address_without_a_star_also_works(self):
        o, _ = run(self.sh, "watch 0x7fffffffdfc8")
        self.assertEqual(o, "Hardware watchpoint 1: 0x7fffffffdfc8\n")

    def test_delete_and_disable_apply_to_watchpoints_too(self):
        run(self.sh, "watch *0x7fffffffdfc8")
        run(self.sh, "disable 1")
        o, _ = run(self.sh, "run < /tmp/safe")
        self.assertNotIn("Hardware watchpoint", o)
        self.assertIn("exited normally", o)
        run(self.sh, "enable 1")
        run(self.sh, "delete 1")
        self.assertEqual(run(self.sh, "info breakpoints")[0], "No breakpoints or watchpoints.\n")

    def test_watch_needs_an_argument(self):
        self.assertIn("Argument required", run(self.sh, "watch")[1])

    def test_watch_on_a_register_needs_a_live_process(self):
        self.assertIn("No registers", run(self.sh, "watch $rsp")[1])
