import os
import tempfile
import unittest
from pathlib import Path

from omnisploit.engine import save
from omnisploit.engine.missions import ACME_TOKEN
from omnisploit.engine.world import World
from omnisploit.shell.shell import Shell


def run(sh, text):
    out = sh.run_line(text)
    return (
        b"".join(d for d, e in out.chunks if not e).decode(),
        b"".join(d for d, e in out.chunks if e).decode(),
    )


def feed(sh, *lines):
    for l in lines:
        run(sh, l)


class MissionTests(unittest.TestCase):
    def setUp(self):
        self.world = World()
        self.sh = Shell(self.world)
        self.log = self.world.missions

    def done(self):
        self.log.evaluate()
        return list(self.log.completed)

    def test_todo_file_exists_and_lists_all_targets(self):
        o, _ = run(self.sh, "cat ~/todo")
        for m in self.log.missions:
            self.assertIn(f"[ ] {m.id}", o)
        self.assertIn("any order", o)
        self.assertIn(f"0/{len(self.log.missions)}", o)

    def test_any_order_and_incremental_progress(self):
        self.assertEqual(self.done(), [])
        # take 'dig' first
        feed(self.sh, "mkdir ~/workspace")
        self.assertEqual(self.done(), [])
        o, _ = run(self.sh, "find / -name '.token*' 2>/dev/null")
        self.assertIn("/var/lib/acme/.token", o)
        feed(self.sh, f"cat /var/lib/acme/.token > ~/workspace/token.txt")
        self.assertEqual(self.done(), ["dig"])
        # then 'workbench'
        feed(self.sh, "echo hello > ~/workspace/hello.txt")
        self.assertEqual(self.done(), ["dig"])  # still needs a pipe
        feed(self.sh, "ls | wc -l")
        self.assertEqual(self.done(), ["dig", "workbench"])
        todo, _ = run(self.sh, "cat ~/todo")
        self.assertIn("[x] dig", todo)
        self.assertIn("[x] workbench", todo)
        self.assertIn("[ ] scribe", todo)
        self.assertIn(f"2/{len(self.log.missions)}", todo)

    def test_wrong_token_does_not_count(self):
        feed(self.sh, "mkdir ~/workspace", "cat /var/lib/acme/.token.bak > ~/workspace/token.txt")
        self.assertEqual(self.done(), [])
        feed(self.sh, "cat /var/tmp/.token-cache > ~/workspace/token.txt")
        self.assertEqual(self.done(), [])

    def test_automation_runs_the_script_in_a_sandbox(self):
        feed(self.sh, "mkdir ~/workspace")
        feed(self.sh, "printf '#!/bin/bash\\nfor i in $(seq 5); do echo $i; done\\n' > ~/workspace/count.sh")
        self.assertEqual(self.done(), [])  # not executable yet
        feed(self.sh, "chmod +x ~/workspace/count.sh")
        self.assertEqual(self.done(), ["automation"])

    def test_script_without_loop_or_with_wrong_output_fails(self):
        feed(self.sh, "mkdir ~/workspace")
        feed(self.sh, "printf '#!/bin/bash\\necho 1 2 3 4 5\\n' > ~/workspace/count.sh", "chmod +x ~/workspace/count.sh")
        self.assertEqual(self.done(), [])  # right output, but no loop
        feed(self.sh, "printf '#!/bin/bash\\nfor i in 1 2 3; do echo $i; done\\n' > ~/workspace/count.sh")
        self.assertEqual(self.done(), [])  # loop, wrong output

    def test_script_check_has_no_side_effects(self):
        feed(self.sh, "mkdir ~/workspace")
        feed(self.sh, "printf '#!/bin/bash\\nfor i in 1 2 3 4 5; do echo $i; done\\ntouch /tmp/pwned\\nrm -rf ~/workspace\\n' > ~/workspace/count.sh", "chmod +x ~/workspace/count.sh")
        self.done()
        self.assertTrue(self.world.vfs.exists("/home/player/workspace/count.sh"))
        self.assertFalse(self.world.vfs.exists("/tmp/pwned"))

    def test_editor_events_complete_scribe(self):
        feed(self.sh, "mkdir ~/workspace", "printf 'a\\nb\\nc\\nd\\n' > ~/workspace/poem.txt")
        self.world.events.update({"vim:visual-op", "vim:substitute"})
        self.assertEqual(self.done(), [])
        self.world.events.add("vim:repeat")
        self.assertEqual(self.done(), ["scribe"])

    def test_completion_is_latched(self):
        feed(self.sh, "mkdir ~/workspace", f"echo {ACME_TOKEN} > ~/workspace/token.txt")
        self.assertEqual(self.done(), ["dig"])
        feed(self.sh, "rm ~/workspace/token.txt")
        self.assertEqual(self.done(), ["dig"])


class SaveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["OMNISPLOIT_CONFIG_DIR"] = self.tmp.name
        self.world = World("neo", "zion")
        self.sh = Shell(self.world)
        self.pristine = save.snapshot(self.world)
        self.path = save.save_path("neo", "zion")

    def tearDown(self):
        del os.environ["OMNISPLOIT_CONFIG_DIR"]
        self.tmp.cleanup()

    def fresh(self):
        w = World("neo", "zion")
        return w, Shell(w)

    def test_roundtrip_files_dirs_deletions_and_shell_state(self):
        feed(self.sh, "mkdir -p ~/workspace/sub", "echo hello > ~/workspace/hello.txt",
             "chmod 600 ~/workspace/hello.txt", "rm ~/motd-daemon/README.txt",
             "alias ll='ls -l'", "export FOO=bar", "cd ~/workspace/sub", "ls | cat")
        self.sh.history.extend(["one", "two"])
        self.world.missions.evaluate()
        self.assertTrue(save.save_game(self.world, self.sh, self.pristine, self.path))

        w2, sh2 = self.fresh()
        self.assertTrue(save.load_game(w2, sh2, self.path))
        self.assertEqual(sh2.cwd, "/home/neo/workspace/sub")
        self.assertEqual(run(sh2, "cat ~/workspace/hello.txt")[0], "hello\n")
        self.assertEqual(w2.vfs.get("/home/neo/workspace/hello.txt").mode, 0o600)
        self.assertFalse(w2.vfs.exists("/home/neo/motd-daemon/README.txt"))
        self.assertTrue(w2.vfs.exists("/home/neo/motd-daemon/motd"))
        self.assertEqual(sh2.aliases, {"ll": "ls -l"})
        self.assertEqual(sh2.env["FOO"], "bar")
        self.assertEqual(sh2.history, ["one", "two"])
        self.assertIn("pipe", w2.events)

    def test_deleted_directory_stays_deleted(self):
        feed(self.sh, "rm -r ~/motd-daemon")
        save.save_game(self.world, self.sh, self.pristine, self.path)
        w2, sh2 = self.fresh()
        save.load_game(w2, sh2, self.path)
        self.assertFalse(w2.vfs.exists("/home/neo/motd-daemon"))

    def test_mission_progress_and_todo_survive(self):
        feed(self.sh, "mkdir ~/workspace", f"echo {ACME_TOKEN} > ~/workspace/token.txt")
        self.world.missions.evaluate()
        save.save_game(self.world, self.sh, self.pristine, self.path)
        w2, sh2 = self.fresh()
        save.load_game(w2, sh2, self.path)
        self.assertEqual(w2.missions.completed, ["dig"])
        self.assertIn("[x] dig", run(sh2, "cat ~/todo")[0])

    def test_save_does_not_include_the_pristine_world(self):
        save.save_game(self.world, self.sh, self.pristine, self.path)
        self.assertLess(self.path.stat().st_size, 2000)

    def test_unusable_saves_are_rejected(self):
        self.assertFalse(save.load_game(*self.fresh(), self.path))  # missing
        self.path.write_text("not json")
        self.assertFalse(save.load_game(*self.fresh(), self.path))
        self.path.write_text('{"version": 99}')
        self.assertFalse(save.load_game(*self.fresh(), self.path))

    def test_separate_saves_per_identity(self):
        self.assertNotEqual(save.save_path("neo", "zion"), save.save_path("neo", "other"))


class FlagSeedSaveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["OMNISPLOIT_CONFIG_DIR"] = self.tmp.name
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(os.environ.pop, "OMNISPLOIT_CONFIG_DIR")
        self.path = save.save_path("neo", "zion")

    def test_peek_with_no_save_returns_none(self):
        self.assertIsNone(save.peek_flag_seed(self.path))

    def test_seed_round_trips_through_save_and_load(self):
        world = World("neo", "zion", flag_seed=4242)
        sh = Shell(world)
        pristine = save.snapshot(world)
        self.assertTrue(save.save_game(world, sh, pristine, self.path))
        self.assertEqual(save.peek_flag_seed(self.path), 4242)
        world2 = World("neo", "zion", flag_seed=save.peek_flag_seed(self.path))
        self.assertEqual(world2.levels["motd-daemon"].flag, world.levels["motd-daemon"].flag)

    def test_peek_ignores_a_corrupt_save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("not json")
        self.assertIsNone(save.peek_flag_seed(self.path))
        self.path.write_text('{"flag_seed": "not-a-number"}')
        self.assertIsNone(save.peek_flag_seed(self.path))


if __name__ == "__main__":
    unittest.main()
