import unittest

from omnisploit.engine.world import World
from omnisploit.shell.shell import Shell


def run(sh, line):
    out = sh.run_line(line)
    o = b"".join(d for d, e in out.chunks if not e).decode(errors="replace")
    e = b"".join(d for d, e in out.chunks if e).decode(errors="replace")
    return o, e


class Fixture(unittest.TestCase):
    def setUp(self):
        self.world = World()
        self.sh = Shell(self.world)
        run(self.sh, "mkdir ~/workspace")

    def completed(self):
        self.world.missions.evaluate()
        return list(self.world.missions.completed)


class LicenseTests(Fixture):
    def test_the_rule(self):
        # first char K, exactly six characters, character codes summing to 0x270
        for serial, ok in [("Kmmmoo", True), ("Knnnnm", True), ("Knnnnl", False), ("Kmmmmm", False), ("Xmmmoo", False), ("Kmmmooo", False), ("K", False)]:
            o, _ = run(self.sh, f"echo {serial} | ~/license/license")
            self.assertEqual("FLAG{sum_of_all_parts}" in o, ok, serial)

    def test_gdb_watches_the_running_total(self):
        run(self.sh, "cd license")
        run(self.sh, "gdb -q ./license")
        run(self.sh, "break *0x4011af")  # the jmp at the bottom of the loop
        run(self.sh, "run")
        run(self.sh, "Kmmmoo")
        run(self.sh, "continue")
        self.assertEqual(run(self.sh, "x/dwx $rbp-8")[0].split("\t")[1], "0x00000002\n")  # two characters counted
        self.assertEqual(run(self.sh, "x/dwx $rbp-4")[0].split("\t")[1], "0x000000b8\n")  # 75 + 109

    def test_mission_runs_the_serial_through_the_program(self):
        run(self.sh, "echo Kmmmmm > ~/workspace/serial.txt")  # wrong total
        run(self.sh, "echo Kmmmoo | ~/license/license")
        self.assertEqual(self.completed(), [])
        run(self.sh, "echo Kmmmoo > ~/workspace/serial.txt")
        self.assertEqual(self.completed(), ["license"])

    def test_serial_check_does_not_set_events(self):
        run(self.sh, "echo Kmmmoo > ~/workspace/serial.txt")
        self.completed()
        self.assertNotIn("solved:license-check", self.world.events)


class DiagdTests(Fixture):
    def test_menu_and_hidden_option(self):
        self.assertEqual(run(self.sh, "echo 1 | ~/diagd/diagd")[0], "choice: All systems nominal.\n")
        self.assertEqual(run(self.sh, "echo 2 | ~/diagd/diagd")[0], "choice: diagd 2.4.1\n")
        self.assertEqual(run(self.sh, "echo 99 | ~/diagd/diagd")[0], "choice: Unknown option.\n")
        self.assertEqual(run(self.sh, "echo abc | ~/diagd/diagd")[0], "choice: Unknown option.\n")
        self.assertIn("FLAG{not_every_door_has_a_sign}", run(self.sh, "echo 1337 | ~/diagd/diagd")[0])
        self.assertIn("FLAG{", run(self.sh, "echo '   1337' | ~/diagd/diagd")[0])  # scanf skips leading whitespace

    def test_nothing_gives_it_away_statically(self):
        o, _ = run(self.sh, "strings ~/diagd/diagd")
        self.assertNotIn("1337", o)
        self.assertNotIn("debug", o)

    def test_r2_finds_the_function_and_the_number(self):
        run(self.sh, "r2 -A ~/diagd/diagd")
        self.assertIn("sym.debug_dump", run(self.sh, "afl")[0])
        self.assertIn("main 0x401223 [CALL:--x] call sym.debug_dump", run(self.sh, "axt sym.debug_dump")[0])
        self.assertIn("cmp eax, 0x539", run(self.sh, "pdf @ main")[0])
        self.assertEqual(run(self.sh, "?v 0x539")[0], "0x539\n")

    def test_mission_needs_all_three_checks(self):
        run(self.sh, "echo sym.debug_dump > ~/workspace/hidden.txt")
        run(self.sh, "echo 1337 > ~/workspace/choice.txt")
        self.assertEqual(self.completed(), [])
        run(self.sh, "echo 1337 | ~/diagd/diagd")
        self.assertEqual(self.completed(), ["diagd"])

    def test_wrong_answers_do_not_count(self):
        run(self.sh, "echo main > ~/workspace/hidden.txt")
        run(self.sh, "echo 1 > ~/workspace/choice.txt")
        run(self.sh, "echo 1337 | ~/diagd/diagd")
        self.assertEqual(self.completed(), [])


class EngineTests(Fixture):
    def test_scanf_d_leaves_non_numbers_alone(self):
        self.assertEqual(run(self.sh, "echo 12x | ~/diagd/diagd")[0], "choice: Unknown option.\n")

    def test_hex_immediates_in_gdb(self):
        run(self.sh, "cd diagd")
        run(self.sh, "gdb -q ./diagd")
        d, _ = run(self.sh, "disassemble main")
        self.assertIn("mov    eax,0x0", d)
        self.assertIn("cmp    eax,0x539", d)
        self.assertIn("mov    eax,DWORD PTR [rbp-0x4]", d)
