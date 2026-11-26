import unittest

from omnisploit.engine.missions import BLOB_SECRET, CULPRIT_IP
from omnisploit.engine.world import World
from omnisploit.shell.shell import Shell


def run(sh, text):
    out = sh.run_line(text)
    return (
        b"".join(d for d, e in out.chunks if not e).decode(errors="replace"),
        b"".join(d for d, e in out.chunks if e).decode(errors="replace"),
    )


class TargetFixture(unittest.TestCase):
    def setUp(self):
        self.world = World()
        self.sh = Shell(self.world)
        run(self.sh, "mkdir ~/workspace")

    def completed(self):
        self.world.missions.evaluate()
        return list(self.world.missions.completed)


class LogsTargetTests(TargetFixture):
    def test_puzzle_has_exactly_one_answer(self):
        o, _ = run(self.sh, "grep 'GET /admin/export HTTP/1.1\" 200' /var/log/webapp/access.log")
        self.assertEqual(len(o.strip().splitlines()), 1)
        self.assertTrue(o.startswith(CULPRIT_IP))
        # refused attempts exist (so a naive grep for the path is not enough) ...
        o, _ = run(self.sh, "grep -c '/admin/export' /var/log/webapp/access.log")
        self.assertGreater(int(o), 3)
        # ... and the culprit also has ordinary traffic and a refused attempt
        o, _ = run(self.sh, f"grep -c '^{CULPRIT_IP} ' /var/log/webapp/access.log")
        self.assertGreaterEqual(int(o), 3)
        # the rotated log has no successful export at all
        o, _ = run(self.sh, "grep -c 'admin/export HTTP/1.1\" 200' /var/log/webapp/access.log.1")
        self.assertEqual(o.strip(), "0")

    def test_solve_with_shell_tools(self):
        run(self.sh, "grep '/admin/export' /var/log/webapp/access.log | awk '$9 == 200 {print $1}' > ~/workspace/culprit.txt")
        self.assertIn("logs", self.completed())

    def test_solve_with_grep_and_cut(self):
        run(self.sh, "grep 'GET /admin/export HTTP/1.1\" 200' /var/log/webapp/access.log | cut -d' ' -f1 > ~/workspace/culprit.txt")
        self.assertEqual(self.completed(), ["logs"])

    def test_refused_client_is_not_accepted(self):
        o, _ = run(self.sh, "grep '/admin/export HTTP/1.1\" 403' /var/log/webapp/access.log | head -1 | cut -d' ' -f1")
        run(self.sh, f"echo {o.strip()} > ~/workspace/culprit.txt")
        self.assertEqual(self.completed(), [])

    def test_log_is_deterministic(self):
        a = World().vfs.get("/var/log/webapp/access.log").data
        b = World().vfs.get("/var/log/webapp/access.log").data
        self.assertEqual(a, b)
        self.assertGreater(len(a.splitlines()), 400)


class CarveTargetTests(TargetFixture):
    def test_file_and_strings_workflow(self):
        o, _ = run(self.sh, "file /srv/drop/artifact.bin")
        self.assertIn("data", o)
        o, _ = run(self.sh, "strings /srv/drop/artifact.bin")
        self.assertIn("secret=" + BLOB_SECRET, o)
        self.assertIn("build=2026.11.3", o)  # decoys are present too
        self.assertEqual(sum(1 for l in o.splitlines() if l.startswith("secret=")), 1)

    def test_solve(self):
        run(self.sh, "strings /srv/drop/artifact.bin | grep '^secret=' | cut -d= -f2 > ~/workspace/secret.txt")
        self.assertEqual(self.completed(), ["carve"])

    def test_xxd_shows_the_same_bytes(self):
        o, _ = run(self.sh, "xxd /srv/drop/artifact.bin | grep -c ORCHID")
        self.assertNotEqual(o.strip(), "0")  # visible in the ASCII column

    def test_placeholder_format_string_is_not_the_answer(self):
        run(self.sh, "echo 'ORCHID-NNNN' > ~/workspace/secret.txt")
        self.assertEqual(self.completed(), [])


class MotdMissionTests(TargetFixture):
    def test_events_drive_the_mission(self):
        self.assertEqual(self.completed(), [])
        self.world.events.add("crash:motd-daemon")
        self.assertEqual(self.completed(), [])
        todo, _ = run(self.sh, "cat ~/todo")
        self.assertIn("[x] make ./motd crash", todo)
        self.assertIn("[ ] get the program to print the flag", todo)
        self.world.events.add("solved:motd-daemon")
        self.assertEqual(self.completed(), ["motd"])

    def test_mission_listed_and_readme_is_current(self):
        todo, _ = run(self.sh, "cat ~/todo")
        self.assertIn("motd -- Break the motd service", todo)
        readme, _ = run(self.sh, "cat ~/motd-daemon/README.txt")
        self.assertNotIn("isn't wired up", readme)

    def test_running_the_program_normally_is_harmless(self):
        o, e = run(self.sh, "echo bob | ~/motd-daemon/motd")
        self.assertIn("Hello, bob", o)
        self.assertEqual(self.completed(), [])


class MotdDissectMissionTests(TargetFixture):
    EXPLOIT = (
        "from pwn import *\n"
        "context.arch = 'amd64'\n"
        "p = process('./motd')\n"
        "payload = b'A' * 72 + p64(0x401196)\n"
        "p.sendlineafter(b'? ', payload)\n"
        "out = p.recvall()\n"
        "print(b'FLAG{' in out)\n"
    )

    def write_facts(self, win="0x401196", canary="no", offset="72", retaddr="0x7fffffffdfd8"):
        run(self.sh, f"echo {win} > ~/workspace/win-addr.txt")
        run(self.sh, f"echo {canary} > ~/workspace/canary.txt")
        run(self.sh, f"echo {offset} > ~/workspace/offset.txt")
        run(self.sh, f"echo {retaddr} > ~/workspace/retaddr.txt")

    def test_all_five_findings_are_required(self):
        self.write_facts()
        self.assertEqual(self.completed(), [])  # no exploit.py yet
        self.sh.vfs.install((self.sh.world.home + "/workspace/exploit.py"), self.EXPLOIT)
        self.assertEqual(self.completed(), ["motd-dissect"])

    def test_accepts_plain_decimal_too(self):
        self.write_facts(win="4198806", offset="72")
        self.sh.vfs.install((self.sh.world.home + "/workspace/exploit.py"), self.EXPLOIT)
        self.assertEqual(self.completed(), ["motd-dissect"])

    def test_wrong_win_address_fails(self):
        self.write_facts(win="0x401197")
        self.sh.vfs.install((self.sh.world.home + "/workspace/exploit.py"), self.EXPLOIT)
        self.assertEqual(self.completed(), [])

    def test_wrong_offset_fails(self):
        self.write_facts(offset="64")
        self.sh.vfs.install((self.sh.world.home + "/workspace/exploit.py"), self.EXPLOIT)
        self.assertEqual(self.completed(), [])

    def test_canary_answer_is_case_insensitive(self):
        self.write_facts(canary="No")
        self.sh.vfs.install((self.sh.world.home + "/workspace/exploit.py"), self.EXPLOIT)
        self.assertEqual(self.completed(), ["motd-dissect"])

    def test_a_script_that_does_not_actually_solve_motd_fails(self):
        self.write_facts()
        self.sh.vfs.install((self.sh.world.home + "/workspace/exploit.py"), "print('hello')\n")
        self.assertEqual(self.completed(), [])

    def test_a_broken_script_fails_without_crashing_evaluation(self):
        self.write_facts()
        self.sh.vfs.install((self.sh.world.home + "/workspace/exploit.py"), "this is not python\n")
        self.assertEqual(self.completed(), [])

    def test_missing_exploit_file_is_handled(self):
        self.write_facts()
        self.assertEqual(self.completed(), [])

    def test_is_independent_of_the_plain_motd_mission(self):
        self.write_facts()
        self.sh.vfs.install((self.sh.world.home + "/workspace/exploit.py"), self.EXPLOIT)
        self.assertEqual(sorted(self.completed()), ["motd-dissect"])  # motd itself was never attempted directly


class ToolbeltMissionTests(TargetFixture):
    def test_each_tool_use_ticks_its_own_check(self):
        run(self.sh, "cd motd-daemon")
        run(self.sh, "checksec ./motd")
        self.assertEqual(self.completed(), [])
        run(self.sh, "r2 ./motd")
        run(self.sh, "aa")
        run(self.sh, "pdf @ sym.vuln")
        run(self.sh, "q")
        run(self.sh, "gdb -q ./motd")
        run(self.sh, "break vuln")
        run(self.sh, "run")
        self.assertNotIn("toolbelt", self.completed())
        run(self.sh, "info registers rip")
        self.assertIn("toolbelt", self.completed())
        run(self.sh, "q")
        todo, _ = run(self.sh, "cat ~/todo")
        self.assertIn("[x] disassemble a function in r2 (pdf)", todo)

    def test_inspecting_needs_a_live_process(self):
        run(self.sh, "cd motd-daemon")
        run(self.sh, "gdb -q ./motd")
        run(self.sh, "x/s 0x402004")  # static memory only: no process yet
        self.assertNotIn("gdb:inspected", self.world.events)


class VaultMissionTests(TargetFixture):
    def test_passphrase_is_not_in_strings_but_is_in_the_code(self):
        run(self.sh, "cd vault")
        o, _ = run(self.sh, "strings vault")
        self.assertNotIn("N1ghtOwl", o)
        self.assertNotIn("N1gh", o)

    def test_both_checks_needed(self):
        run(self.sh, "echo N1ghtOwl > ~/workspace/passphrase.txt")
        self.assertEqual(self.completed(), [])
        o, _ = run(self.sh, "echo N1ghtOwl | ~/vault/vault")
        self.assertIn("FLAG{the_stack_remembers_everything}", o)
        self.assertEqual(self.completed(), ["vault"])

    def test_wrong_passphrase_is_harmless(self):
        o, _ = run(self.sh, "echo guess | ~/vault/vault")
        self.assertEqual(o, "pass: Wrong.\n")
        self.assertNotIn("solved:vault", self.world.events)


class AllTargetsTests(TargetFixture):
    def test_every_target_is_independent_and_listed(self):
        ids = [m.id for m in self.world.missions.missions]
        self.assertEqual(ids, ["workbench", "scribe", "automation", "dig", "logs", "carve", "toolbelt", "motd", "motd-dissect", "vault", "license", "diagd", "notice", "sentry", "dispatch", "recon", "webenum", "revshell", "breach", "privesc"])
        self.assertEqual(len(set(ids)), len(ids))
        for m in self.world.missions.missions:
            self.assertTrue(m.checks and m.hint, m.id)


if __name__ == "__main__":
    unittest.main()
