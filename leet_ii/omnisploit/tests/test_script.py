import unittest

from omnisploit.engine.world import World
from omnisploit.shell.shell import Shell


def run(sh, text):
    out = sh.run_line(text)
    return (
        b"".join(d for d, e in out.chunks if not e).decode(),
        b"".join(d for d, e in out.chunks if e).decode(),
    )


def feed(sh, *lines):
    o = e = ""
    for l in lines:
        a, b = run(sh, l)
        o, e = o + a, e + b
    return o, e


class ScriptingTests(unittest.TestCase):
    def setUp(self):
        self.sh = Shell(World())

    def test_variables_and_arithmetic(self):
        self.assertEqual(run(self.sh, "x=5; y=$((x * 2 + 1)); echo $x $y")[0], "5 11\n")
        self.assertEqual(run(self.sh, 'echo "$((7 / 2)) $((-7 / 2)) $((7 % 3)) $((2 ** 5))"')[0], "3 -3 1 32\n")
        self.assertIn("division by 0", run(self.sh, "echo $((1/0))")[1])
        self.assertEqual(run(self.sh, "FOO=bar echo hi; echo [$FOO]")[0], "hi\n[]\n")

    def test_if(self):
        self.assertEqual(run(self.sh, "if true; then echo yes; else echo no; fi")[0], "yes\n")
        self.assertEqual(run(self.sh, "if false; then echo a; elif true; then echo b; else echo c; fi")[0], "b\n")
        self.assertEqual(run(self.sh, "if [ -d /tmp ] && [ ! -f /tmp ]; then echo ok; fi")[0], "ok\n")
        self.assertEqual(run(self.sh, "if [ 3 -lt 10 ]; then echo lt; fi")[0], "lt\n")
        self.assertEqual(run(self.sh, "[ a = a ] && echo eq")[0], "eq\n")

    def test_for_and_split(self):
        self.assertEqual(run(self.sh, "for i in 1 2 3; do echo n$i; done")[0], "n1\nn2\nn3\n")
        self.assertEqual(run(self.sh, "for i in $(seq 3); do echo -n $i; done; echo")[0], "123\n")
        self.assertEqual(run(self.sh, "for f in /etc/h*; do echo $f; done")[0], "/etc/hostname\n/etc/hosts\n")
        self.assertEqual(run(self.sh, 'for w in "a b" c; do echo "[$w]"; done')[0], "[a b]\n[c]\n")

    def test_while_until_break_continue(self):
        self.assertEqual(run(self.sh, "i=0; while [ $i -lt 3 ]; do echo $i; i=$((i+1)); done")[0], "0\n1\n2\n")
        self.assertEqual(run(self.sh, "i=0; until [ $i -ge 2 ]; do i=$((i+1)); done; echo $i")[0], "2\n")
        self.assertEqual(run(self.sh, "for i in 1 2 3 4; do if [ $i = 3 ]; then break; fi; echo $i; done")[0], "1\n2\n")
        self.assertEqual(run(self.sh, "for i in 1 2 3; do if [ $i = 2 ]; then continue; fi; echo $i; done")[0], "1\n3\n")
        self.assertIn("only meaningful", run(self.sh, "break")[1])

    def test_infinite_loop_is_stopped(self):
        o, e = run(self.sh, "while true; do :; done")
        self.assertIn("limit", e)
        o, e = run(self.sh, "while true; do echo spam; done")
        self.assertIn("limit", e)

    def test_multiline_continuation(self):
        self.assertEqual(self.sh.prompt(), "player@lab:~$ ")
        self.assertEqual(run(self.sh, "for i in a b; do"), ("", ""))
        self.assertEqual(self.sh.prompt(), "> ")
        self.assertEqual(run(self.sh, "echo $i"), ("", ""))
        self.assertEqual(run(self.sh, "done"), ("a\nb\n", ""))
        self.assertEqual(self.sh.prompt(), "player@lab:~$ ")
        run(self.sh, "echo 'open")
        self.assertEqual(self.sh.prompt(), "> ")
        self.sh.abort_input()
        self.assertEqual(self.sh.prompt(), "player@lab:~$ ")
        self.assertEqual(feed(self.sh, "echo 'a", "b'")[0], "a\nb\n")

    def test_scripts_with_args(self):
        feed(self.sh, "printf '#!/bin/bash\\nfor a in \"$@\"; do echo arg=$a; done\\necho n=$# first=$1\\n' > /tmp/s.sh", "chmod +x /tmp/s.sh")
        self.assertEqual(run(self.sh, "/tmp/s.sh x y")[0], "arg=x y\nn=2 first=x\n")
        feed(self.sh, "printf 'for a\\ndo echo [$a]\\ndone\\n' > /tmp/t.sh")
        self.assertEqual(run(self.sh, "bash /tmp/t.sh p q")[0], "[p]\n[q]\n")
        self.assertEqual(run(self.sh, "echo $1")[0], "\n")  # restored after the script

    def test_source_shares_state(self):
        feed(self.sh, "echo 'V=42' > /tmp/env.sh")
        self.assertEqual(run(self.sh, "source /tmp/env.sh; echo $V")[0], "42\n")
        self.assertEqual(run(self.sh, ". /tmp/env.sh; echo $V")[0], "42\n")

    def test_aliases(self):
        feed(self.sh, "alias ll='ls -l'", "alias say=echo")
        self.assertIn("-rwxr-xr-x", run(self.sh, "cd motd-daemon; ll")[0])
        self.assertEqual(run(self.sh, "say hi | tr a-z A-Z")[0], "HI\n")
        self.assertIn("alias ll='ls -l'", run(self.sh, "alias")[0])
        self.assertEqual(run(self.sh, "type ll")[0], "ll is aliased to `ls -l'\n")
        run(self.sh, "unalias ll")
        self.assertIn("command not found", run(self.sh, "ll")[1])
        feed(self.sh, "cd", "alias ls='ls -1'")  # self-referential aliases must not recurse
        self.assertIn("motd-daemon", run(self.sh, "ls")[0])

    def test_background_and_jobs(self):
        o, e = run(self.sh, "echo bg &")
        self.assertEqual(o, "bg\n")
        self.assertRegex(e, r"\[1\] \d+")
        self.assertIn("Done", run(self.sh, "jobs")[0])
        self.assertEqual(run(self.sh, "jobs")[0], "")

    def test_history_type_which_man(self):
        self.sh.history.extend(["ls", "pwd"])
        self.assertEqual(run(self.sh, "history")[0], "    1  ls\n    2  pwd\n")
        run(self.sh, "history -c")
        self.assertEqual(self.sh.history, [])
        self.assertEqual(run(self.sh, "type cd")[0], "cd is a shell builtin\n")
        self.assertEqual(run(self.sh, "type ls")[0], "ls is /usr/bin/ls\n")
        o, _ = run(self.sh, "man grep")
        self.assertIn("GREP(1)", o)
        self.assertIn("-r, -R", o)
        self.assertIn("No manual entry", run(self.sh, "man nonesuch")[1])
        self.assertIn("grep:", run(self.sh, "help grep")[0])
        self.assertIn("Pipes", run(self.sh, "help")[0])

    def test_every_command_has_a_man_page(self):
        from omnisploit.shell.commands import REGISTRY
        from omnisploit.shell.manpages import PAGES

        self.assertEqual(sorted(set(REGISTRY) - set(PAGES)), [])

    def test_grep_recursive(self):
        feed(self.sh, "mkdir -p /tmp/d/sub", "echo needle > /tmp/d/sub/a", "echo hay > /tmp/d/b")
        self.assertEqual(run(self.sh, "grep -r needle /tmp/d")[0], "/tmp/d/sub/a:needle\n")
        self.assertEqual(run(self.sh, "grep -rl hay /tmp/d")[0], "/tmp/d/b\n")
        o, e = run(self.sh, "grep -r nothing-here /opt")
        self.assertEqual(o, "")
        self.assertIn("Permission denied", e)

    def test_pipeline_and_redirect_events(self):
        run(self.sh, "echo a | cat > /tmp/x")
        self.assertIn("pipe", self.sh.world.events)
        self.assertIn("redirect", self.sh.world.events)



class GrepCountTests(unittest.TestCase):
    def test_grep_c(self):
        sh = Shell(World())
        feed(sh, "printf 'a\\nb\\na\\n' > /tmp/g", "printf 'a\\n' > /tmp/h")
        self.assertEqual(run(sh, "grep -c a /tmp/g")[0], "2\n")
        self.assertEqual(run(sh, "grep -c a /tmp/g /tmp/h")[0], "/tmp/g:2\n/tmp/h:1\n")
        self.assertEqual(run(sh, "grep -c zzz /tmp/g")[0], "0\n")
        self.assertEqual(run(sh, "cat /tmp/g | grep -vc a")[0], "1\n")


if __name__ == "__main__":
    unittest.main()
