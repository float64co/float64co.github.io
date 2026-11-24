import os
import tempfile
import unittest

from omnisploit import profile
from omnisploit.engine.world import World
from omnisploit.shell.shell import Shell


def run(sh, line):
    out = sh.run_line(line)
    return (
        b"".join(d for d, e in out.chunks if not e).decode(),
        b"".join(d for d, e in out.chunks if e).decode(),
    )


class ExtraProgramTests(unittest.TestCase):
    def setUp(self):
        self.sh = Shell(World())
        run(self.sh, "printf 'b 2\\na 10\\nb 2\\nc 1\\n' > /tmp/t")

    def test_sort_uniq(self):
        self.assertEqual(run(self.sh, "sort /tmp/t")[0], "a 10\nb 2\nb 2\nc 1\n")
        self.assertEqual(run(self.sh, "sort -u /tmp/t")[0], "a 10\nb 2\nc 1\n")
        self.assertEqual(run(self.sh, "sort /tmp/t | uniq -c | head -1")[0], "      1 a 10\n")
        self.assertEqual(run(self.sh, "cut -d' ' -f2 /tmp/t | sort -n | tail -1")[0], "10\n")
        self.assertEqual(run(self.sh, "sort -rn -k2 /tmp/t")[1].count("sort"), 0)

    def test_tee_tr_rev_seq(self):
        self.assertEqual(run(self.sh, "echo Hello | tee /tmp/o | tr a-z A-Z")[0], "HELLO\n")
        self.assertEqual(run(self.sh, "cat /tmp/o")[0], "Hello\n")
        self.assertEqual(run(self.sh, "echo abc | rev")[0], "cba\n")
        self.assertEqual(run(self.sh, "seq 3")[0], "1\n2\n3\n")
        self.assertEqual(run(self.sh, "echo hello | tr -d l")[0], "heo\n")

    def test_printf(self):
        self.assertEqual(run(self.sh, "printf '%s-%d\\n' a 1 b 2")[0], "a-1\nb-2\n")
        self.assertEqual(run(self.sh, "printf '\\x41\\x42'")[0], "AB")

    def test_find_tree(self):
        o, _ = run(self.sh, "find . -name README.txt")
        self.assertEqual(sorted(o.split()), ["./diagd/README.txt", "./dispatch/README.txt", "./license/README.txt", "./motd-daemon/README.txt", "./notice/README.txt", "./sentry/README.txt", "./vault/README.txt"])
        o, e = run(self.sh, "find /opt -type f")
        self.assertIn("Permission denied", e + o) if not o else None
        o, _ = run(self.sh, "tree motd-daemon")
        self.assertIn("├── README.txt", o)
        self.assertTrue(o.rstrip().endswith("0 directories, 2 files"))

    def test_sed(self):
        self.assertEqual(run(self.sh, "echo hello world | sed 's/o/0/g'")[0], "hell0 w0rld\n")
        self.assertEqual(run(self.sh, "sed -n 2p /tmp/t")[0], "a 10\n")
        self.assertEqual(run(self.sh, "sed '1d;$d' /tmp/t")[0], "a 10\nb 2\n")
        self.assertEqual(run(self.sh, "sed -n '/^a/,/^c/p' /tmp/t")[0], "a 10\nb 2\nc 1\n")
        run(self.sh, "sed -i 's/c/Z/' /tmp/t")
        self.assertIn("Z 1", run(self.sh, "cat /tmp/t")[0])

    def test_awk(self):
        self.assertEqual(run(self.sh, "awk '{print $2}' /tmp/t | head -2")[0], "2\n10\n")
        self.assertEqual(run(self.sh, "awk '$2 > 5 {print $1, \"big\"}' /tmp/t")[0], "a big\n")
        self.assertEqual(run(self.sh, "echo a:b:c | awk -F: '{print $3 $1}'")[0], "ca\n")
        self.assertEqual(run(self.sh, "awk 'END{print NR}' /tmp/t")[0], "4\n")
        self.assertEqual(run(self.sh, "awk '/^a/' /tmp/t")[0], "a 10\n")

    def test_ps_nc_misc(self):
        self.assertIn("PID", run(self.sh, "ps")[0])
        self.assertIn("sshd", run(self.sh, "ps aux")[0])
        self.assertIn("Connection refused", run(self.sh, "nc 10.10.0.20 80")[1])
        self.assertEqual(run(self.sh, "basename /a/b/c.txt .txt")[0], "c\n")
        self.assertEqual(run(self.sh, "dirname /a/b/c.txt")[0], "/a/b\n")


class PathAndScriptTests(unittest.TestCase):
    def setUp(self):
        self.sh = Shell(World())

    def test_path_lookup(self):
        self.assertEqual(run(self.sh, "which ls")[0], "/usr/bin/ls\n")
        run(self.sh, "export PATH=/nonexistent")
        self.assertIn("command not found", run(self.sh, "ls")[1])
        self.assertEqual(run(self.sh, "echo still works")[0], "still works\n")  # bash builtin
        run(self.sh, "export PATH=/usr/bin:/bin")
        self.assertEqual(run(self.sh, "echo back")[0], "back\n")

    def test_builtins_ignore_path(self):
        run(self.sh, "export PATH=")
        run(self.sh, "cd /tmp")
        self.assertEqual(self.sh.cwd, "/tmp")

    def test_shebang_script(self):
        run(self.sh, "printf '#!/bin/bash\\necho from script\\necho two | tr a-z A-Z\\n' > /tmp/s.sh")
        self.assertIn("Permission denied", run(self.sh, "/tmp/s.sh")[1])
        run(self.sh, "chmod +x /tmp/s.sh")
        self.assertEqual(run(self.sh, "/tmp/s.sh")[0], "from script\nTWO\n")
        self.assertEqual(run(self.sh, "/tmp/s.sh | tr a-z A-Z")[0], "FROM SCRIPT\nTWO\n")
        self.assertEqual(run(self.sh, "bash -c 'echo hi' | tr a-z A-Z")[0], "HI\n")

    def test_script_on_path(self):
        run(self.sh, "mkdir ~/bin")
        run(self.sh, "echo 'echo mytool' > ~/bin/mytool")
        run(self.sh, "printf '#!/bin/sh\\necho mytool\\n' > ~/bin/mytool; chmod +x ~/bin/mytool")
        run(self.sh, "export PATH=$PATH:~/bin")
        self.assertEqual(run(self.sh, "mytool")[0], "mytool\n")
        self.assertEqual(run(self.sh, "which mytool")[0], "/home/player/bin/mytool\n")


class CustomIdentityTests(unittest.TestCase):
    def test_custom_user_and_host(self):
        sh = Shell(World("neo", "nebuchadnezzar"))
        self.assertEqual(sh.prompt(), "neo@nebuchadnezzar:~$ ")
        self.assertEqual(run(sh, "whoami")[0], "neo\n")
        self.assertEqual(run(sh, "id")[0], "uid=1000(neo) gid=1000(neo) groups=1000(neo)\n")
        self.assertEqual(run(sh, "hostname")[0], "nebuchadnezzar\n")
        self.assertEqual(run(sh, "pwd")[0], "/home/neo\n")
        self.assertIn("neo:x:1000", run(sh, "cat /etc/passwd")[0])
        self.assertEqual(run(sh, "cat /etc/hostname")[0], "nebuchadnezzar\n")
        o, _ = run(sh, "ls -l ~/motd-daemon")
        self.assertIn("neo", o)
        self.assertIn("README.txt", o)
        self.assertIn("Permission denied", run(sh, "cat /opt/motd/flag.txt")[1])
        run(sh, "echo hi > ~/f && cat ~/f")  # can write in own home

    def test_profile_validation_and_storage(self):
        self.assertIsNone(profile.validate_name("neo"))
        for bad in ("Neo", "1abc", "", "a b", "x" * 40, "root"):
            self.assertIsNotNone(profile.validate_name(bad), bad)
        self.assertIsNone(profile.validate_hostname("lab-01"))
        for bad in ("-a", "a-", "a_b", "", "a" * 64, "has space"):
            self.assertIsNotNone(profile.validate_hostname(bad), bad)
        with tempfile.TemporaryDirectory() as d:
            os.environ["OMNISPLOIT_CONFIG_DIR"] = d
            try:
                self.assertIsNone(profile.load())
                profile.save(profile.Profile("neo", "zion"))
                self.assertEqual(profile.load(), profile.Profile("neo", "zion"))
                (profile.config_dir() / "profile.json").write_text('{"name": "ROOT!", "hostname": "x"}')
                self.assertIsNone(profile.load())
                (profile.config_dir() / "profile.json").write_text("not json")
                self.assertIsNone(profile.load())
            finally:
                del os.environ["OMNISPLOIT_CONFIG_DIR"]


if __name__ == "__main__":
    unittest.main()


class ChecksecTests(unittest.TestCase):
    def setUp(self):
        self.sh = Shell(World())
        run(self.sh, "cd motd-daemon")

    def test_pwntools_style(self):
        o, _ = run(self.sh, "checksec ./motd")
        self.assertIn("[*] '/home/player/motd-daemon/motd'", o)
        self.assertIn("RELRO:    Partial RELRO", o)
        self.assertIn("Stack:    No canary found", o)
        self.assertIn("NX:       NX enabled", o)
        self.assertIn("PIE:      No PIE (0x400000)", o)

    def test_script_style_and_errors(self):
        o, _ = run(self.sh, "checksec --file=motd")
        self.assertIn("Partial RELRO", o.splitlines()[1])
        self.assertTrue(o.splitlines()[1].endswith("motd"))
        self.assertIn("not an ELF", run(self.sh, "checksec README.txt")[1])
        self.assertIn("cannot open", run(self.sh, "checksec nope")[1])


class GrepOnlyMatchingTests(unittest.TestCase):
    def setUp(self):
        self.sh = Shell(World())

    def test_o_e_h(self):
        run(self.sh, "printf 'id=12 name=ann\\nid=7 name=bo\\n' > /tmp/g")
        self.assertEqual(run(self.sh, "grep -o 'id=[0-9]*' /tmp/g")[0], "id=12\nid=7\n")
        self.assertEqual(run(self.sh, "grep -oE '[a-z]+$' /tmp/g")[0], "ann\nbo\n")
        self.assertEqual(run(self.sh, "grep -e 'name=bo' /tmp/g")[0], "id=7 name=bo\n")
        self.assertEqual(run(self.sh, "grep -on 'id' /tmp/g /tmp/g")[0].splitlines()[0], "/tmp/g:1:id")
        self.assertEqual(run(self.sh, "grep -h id /tmp/g /tmp/g")[0].count("\n"), 4)
