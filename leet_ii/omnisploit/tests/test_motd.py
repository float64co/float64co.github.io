import unittest

from omnisploit.engine.machine import p64
from omnisploit.engine.world import World
from omnisploit.shell.shell import LevelSession, Shell


def run(sh: Shell, line: str) -> tuple[bytes, bytes]:
    out = sh.run_line(line)
    o = b"".join(d for d, e in out.chunks if not e)
    e = b"".join(d for d, e in out.chunks if e)
    return o, e


class MotdHandlerTests(unittest.TestCase):
    def setUp(self):
        self.sh = Shell(World())
        run(self.sh, "cd motd-daemon")

    def test_interactive_session(self):
        self.assertEqual(run(self.sh, "./motd"), (b"", b""))
        self.assertIsInstance(self.sh.session, LevelSession)
        self.assertEqual(self.sh.prompt(), "name? ")
        self.assertEqual(run(self.sh, "bob"), (b"Hello, bob\n", b""))
        self.assertIsNone(self.sh.session)
        self.assertEqual(self.sh.status, 0)

    def test_piped_input_includes_prompt(self):
        self.assertEqual(run(self.sh, "echo bob | ./motd"), (b"name? Hello, bob\n", b""))

    def test_overflow_crashes(self):
        run(self.sh, "./motd")
        _, err = run(self.sh, "A" * 100)
        self.assertIn(b"Segmentation fault", err)
        self.assertEqual(self.sh.status, 139)
        self.assertIn("crash:motd-daemon", self.sh.world.events)
        self.assertNotIn("solved:motd-daemon", self.sh.world.events)

    def test_ret2win_prints_flag(self):
        payload = b"A" * 72 + p64(0x401196) + b"\n"
        self.sh.vfs.write("/tmp/payload", payload, "/")
        out, _ = run(self.sh, "./motd < /tmp/payload")
        self.assertIn(b"FLAG{ret2win_is_just_the_beginning}", out)
        self.assertIn("solved:motd-daemon", self.sh.world.events)

    def test_not_executable(self):
        self.sh.vfs.get("motd", self.sh.cwd).mode = 0o644
        _, err = run(self.sh, "./motd")
        self.assertIn(b"Permission denied", err)


if __name__ == "__main__":
    unittest.main()
