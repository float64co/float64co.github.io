import unittest

from omnisploit.engine.world import World


class FlagSeedTests(unittest.TestCase):
    def test_no_seed_keeps_the_original_flags(self):
        w = World()
        self.assertEqual(w.levels["motd-daemon"].flag, "FLAG{ret2win_is_just_the_beginning}")
        self.assertEqual(w.levels["vault"].flag, "FLAG{the_stack_remembers_everything}")

    def test_same_seed_gives_the_same_flags(self):
        w1 = World(flag_seed=12345)
        w2 = World(flag_seed=12345)
        for lid in w1.levels:
            self.assertEqual(w1.levels[lid].flag, w2.levels[lid].flag, lid)

    def test_different_seeds_give_different_flags(self):
        w1 = World(flag_seed=1)
        w2 = World(flag_seed=2)
        differs = [lid for lid in w1.levels if w1.levels[lid].flag != w2.levels[lid].flag]
        self.assertEqual(set(differs), set(w1.levels))  # every level's flag changed

    def test_different_levels_get_different_flags_from_the_same_seed(self):
        w = World(flag_seed=42)
        flags = [lv.flag for lv in w.levels.values()]
        self.assertEqual(len(flags), len(set(flags)))

    def test_randomized_flags_still_look_like_flags(self):
        w = World(flag_seed=7)
        for lv in w.levels.values():
            self.assertRegex(lv.flag, r"^FLAG\{[0-9a-z]+\}$")

    def test_flag_path_and_mission_solving_still_work_with_a_randomized_flag(self):
        from omnisploit.engine.machine import p64
        from omnisploit.shell.shell import Shell

        w = World(flag_seed=999)
        sh = Shell(w)
        sh.run_line("cd motd-daemon")
        sh.vfs.install("/tmp/p", b"A" * 72 + p64(0x401196) + b"\n")
        out = sh.run_line("./motd < /tmp/p")
        data = b"".join(d for d, e in out.chunks if not e)
        self.assertIn(w.levels["motd-daemon"].flag.encode(), data)
        self.assertIn("solved:motd-daemon", w.events)
        w.missions.evaluate()
        self.assertIn("motd", w.missions.completed)

    def test_root_owned_flag_file_contains_the_randomized_text(self):
        w = World(flag_seed=555)
        node = w.vfs.get(w.levels["vault"].flag_path)
        self.assertEqual(node.data, (w.levels["vault"].flag + "\n").encode())
        self.assertEqual(node.owner, "root")
