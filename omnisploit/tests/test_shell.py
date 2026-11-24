import unittest

from omnisploit.engine.world import World
from omnisploit.shell.shell import Shell


def run(sh: Shell, line: str) -> tuple[str, str]:
    out = sh.run_line(line)
    o = b"".join(d for d, e in out.chunks if not e).decode()
    e = b"".join(d for d, e in out.chunks if e).decode()
    return o, e


class ShellTests(unittest.TestCase):
    def setUp(self):
        self.sh = Shell(World())

    def test_basics(self):
        self.assertEqual(run(self.sh, "pwd")[0], "/home/player\n")
        self.assertEqual(run(self.sh, "echo hi  there")[0], "hi there\n")
        self.assertEqual(run(self.sh, "echo 'a  b' \"c $USER\"")[0], "a  b c player\n")

    def test_cd_and_ls(self):
        run(self.sh, "cd motd-daemon")
        self.assertEqual(self.sh.prompt(), "player@lab:~/motd-daemon$ ")
        o, _ = run(self.sh, "ls")
        self.assertIn("README.txt", o)
        self.assertIn("motd", o)
        self.assertIn("-rwxr-xr-x", run(self.sh, "ls -l")[0])

    def test_redirect_pipe_and_substitution(self):
        run(self.sh, "echo hello > /tmp/a.txt")
        run(self.sh, "echo world >> /tmp/a.txt")
        self.assertEqual(run(self.sh, "cat /tmp/a.txt")[0], "hello\nworld\n")
        self.assertEqual(run(self.sh, "cat /tmp/a.txt | grep wor")[0], "world\n")
        self.assertEqual(run(self.sh, "echo got $(echo inner)")[0], "got inner\n")
        self.assertEqual(run(self.sh, "wc -l < /tmp/a.txt")[0], "2\n")

    def test_and_or(self):
        self.assertEqual(run(self.sh, "false && echo no ; echo yes")[0], "yes\n")
        self.assertEqual(run(self.sh, "false || echo fallback")[0], "fallback\n")

    def test_permissions(self):
        _, err = run(self.sh, "cat /opt/motd/flag.txt")
        self.assertIn("Permission denied", err)
        _, err = run(self.sh, "echo x > /etc/passwd")
        self.assertIn("Permission denied", err)

    def test_errors(self):
        self.assertIn("command not found", run(self.sh, "nosuchcmd")[1])
        self.assertIn("No such file", run(self.sh, "cat nope")[1])
        self.assertIn("syntax error", run(self.sh, "done")[1])
        self.assertIn("syntax error", run(self.sh, "fi")[1])
        # a trailing pipe waits for more input, like bash
        self.assertEqual(run(self.sh, "echo hi |"), ("", ""))
        self.assertEqual(self.sh.prompt(), "> ")
        self.assertEqual(run(self.sh, "tr a-z A-Z")[0], "HI\n")
        self.assertEqual(self.sh.prompt(), "player@lab:~$ ")

    def test_glob_and_xxd(self):
        run(self.sh, "cd motd-daemon")
        self.assertEqual(run(self.sh, "echo R*.txt")[0], "README.txt\n")
        o, _ = run(self.sh, "xxd -l 16 motd")
        self.assertTrue(o.startswith("00000000: 7f45 4c46"))

    def test_vim_action(self):
        out = self.sh.run_line("vim notes.txt")
        self.assertEqual(out.actions, [("edit", "/home/player/notes.txt", "notes.txt")])

    def test_completion(self):
        start, cands = self.sh.complete("cd mot")
        self.assertEqual((start, cands), (3, ["motd-daemon/"]))
        _, cands = self.sh.complete("ech")
        self.assertEqual(cands, ["echo"])

    def test_bash_c(self):
        self.assertEqual(run(self.sh, "bash -c 'echo a; echo b'")[0], "a\nb\n")


if __name__ == "__main__":
    unittest.main()
