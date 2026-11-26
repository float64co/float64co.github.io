import unittest

from omnisploit.engine.world import World
from omnisploit.shell.shell import Shell


def run(sh, line):
    out = sh.run_line(line)
    o = b"".join(d for d, e in out.chunks if not e)
    e = b"".join(d for d, e in out.chunks if e)
    return o, e


class Base64Tests(unittest.TestCase):
    def setUp(self):
        self.sh = Shell(World())

    def test_encode_and_decode_round_trip(self):
        o, _ = run(self.sh, "echo -n hello | base64")
        self.assertEqual(o, b"aGVsbG8=\n")
        o, _ = run(self.sh, "echo -n hello | base64 | base64 -d")
        self.assertEqual(o, b"hello")

    def test_wraps_long_lines_at_76_columns(self):
        run(self.sh, "python3 -c \"open('/tmp/big','wb').write(b'A'*100)\"")
        o, _ = run(self.sh, "base64 /tmp/big")
        lines = o.decode().splitlines()
        self.assertEqual(len(lines[0]), 76)
        self.assertTrue(all(len(l) <= 76 for l in lines))

    def test_binary_output_is_not_mangled(self):
        run(self.sh, "python3 -c \"open('/tmp/bin','wb').write(bytes(range(256)))\"")
        run(self.sh, "base64 /tmp/bin > /tmp/enc")
        o, _ = run(self.sh, "base64 -d /tmp/enc")
        self.assertEqual(o, bytes(range(256)))

    def test_invalid_input_is_an_error(self):
        self.assertIn("invalid input", run(self.sh, "echo 'not base64 !!!' | base64 -d")[1].decode())


class HashsumTests(unittest.TestCase):
    def setUp(self):
        self.sh = Shell(World())
        run(self.sh, "printf 'hello\\n' > /tmp/a.txt")

    def test_md5(self):
        o, _ = run(self.sh, "md5sum /tmp/a.txt")
        self.assertEqual(o.decode(), "b1946ac92492d2347c6235b4d2611184  /tmp/a.txt\n")

    def test_sha1(self):
        o, _ = run(self.sh, "sha1sum /tmp/a.txt")
        self.assertEqual(o.decode().split()[0], "f572d396fae9206628714fb2ce00f72e94f2258f")

    def test_sha256(self):
        o, _ = run(self.sh, "sha256sum /tmp/a.txt")
        self.assertEqual(o.decode().split()[0], "5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03")

    def test_stdin_is_labeled_dash(self):
        o, _ = run(self.sh, "echo hi | md5sum")
        self.assertTrue(o.decode().endswith("  -\n"))

    def test_missing_file(self):
        self.assertIn("No such file", run(self.sh, "md5sum /nope")[1].decode())

    def test_multiple_files(self):
        run(self.sh, "printf 'bye\\n' > /tmp/b.txt")
        o, _ = run(self.sh, "md5sum /tmp/a.txt /tmp/b.txt")
        lines = o.decode().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].endswith("/tmp/a.txt"))
        self.assertTrue(lines[1].endswith("/tmp/b.txt"))


class DiffTests(unittest.TestCase):
    def setUp(self):
        self.sh = Shell(World())
        run(self.sh, "printf 'a\\nb\\nc\\n' > /tmp/f1")
        run(self.sh, "printf 'a\\nB\\nc\\nd\\n' > /tmp/f2")

    def test_classic_hunks(self):
        o, e = run(self.sh, "diff /tmp/f1 /tmp/f2")
        self.assertEqual(o.decode(), "2c2\n< b\n---\n> B\n3a4\n> d\n")
        self.assertEqual(self.sh.status, 1)

    def test_unified(self):
        o, _ = run(self.sh, "diff -u /tmp/f1 /tmp/f2")
        s = o.decode()
        self.assertIn("--- /tmp/f1", s)
        self.assertIn("+++ /tmp/f2", s)
        self.assertIn("-b", s)
        self.assertIn("+B", s)
        self.assertIn("+d", s)

    def test_identical_files(self):
        o, _ = run(self.sh, "diff /tmp/f1 /tmp/f1")
        self.assertEqual(o, b"")
        self.assertEqual(self.sh.status, 0)

    def test_pure_deletion_and_insertion(self):
        run(self.sh, "printf 'a\\nb\\nc\\n' > /tmp/x")
        run(self.sh, "printf 'a\\nc\\n' > /tmp/y")
        o, _ = run(self.sh, "diff /tmp/x /tmp/y")
        self.assertEqual(o.decode(), "2d1\n< b\n")  # matches real diff's own output for this pair
        o, _ = run(self.sh, "diff /tmp/y /tmp/x")
        self.assertEqual(o.decode(), "1a2\n> b\n")

    def test_missing_file_error(self):
        self.assertIn("No such file", run(self.sh, "diff /tmp/f1 /nope")[1].decode())

    def test_usage_error(self):
        self.assertIn("usage", run(self.sh, "diff /tmp/f1")[1].decode())


class TarTests(unittest.TestCase):
    def setUp(self):
        self.sh = Shell(World())
        run(self.sh, "mkdir -p /tmp/d")
        run(self.sh, "echo x > /tmp/d/x.txt")
        run(self.sh, "echo y > /tmp/d/y.txt")

    def test_create_list_and_extract_gzip(self):
        run(self.sh, "tar -czf /tmp/out.tar.gz /tmp/d")
        o, _ = run(self.sh, "tar -tzf /tmp/out.tar.gz")
        self.assertEqual(sorted(o.decode().split()), ["tmp/d/x.txt", "tmp/d/y.txt"])
        run(self.sh, "mkdir /tmp/extracted")
        o, e = run(self.sh, "tar -xzf /tmp/out.tar.gz -C /tmp/extracted")
        self.assertEqual(e, b"")
        self.assertEqual(run(self.sh, "cat /tmp/extracted/tmp/d/x.txt")[0], b"x\n")
        self.assertEqual(run(self.sh, "cat /tmp/extracted/tmp/d/y.txt")[0], b"y\n")

    def test_plain_uncompressed(self):
        run(self.sh, "tar -cf /tmp/out.tar /tmp/d/x.txt")
        run(self.sh, "mkdir /tmp/e2")
        run(self.sh, "tar -xf /tmp/out.tar -C /tmp/e2")
        self.assertEqual(run(self.sh, "cat /tmp/e2/tmp/d/x.txt")[0], b"x\n")

    def test_gz_suffix_implies_gzip_without_z_flag(self):
        run(self.sh, "tar -cf /tmp/implicit.tar.gz /tmp/d/x.txt")
        o, e = run(self.sh, "tar -tf /tmp/implicit.tar.gz")
        self.assertEqual(e, b"")
        self.assertIn("tmp/d/x.txt", o.decode())

    def test_extract_without_c_uses_cwd(self):
        run(self.sh, "tar -czf /tmp/out.tar.gz /tmp/d/x.txt")
        run(self.sh, "mkdir /tmp/here && cd /tmp/here")
        run(self.sh, "tar -xzf /tmp/out.tar.gz")
        self.assertEqual(run(self.sh, "cat /tmp/here/tmp/d/x.txt")[0], b"x\n")

    def test_empty_archive_refused(self):
        self.assertIn("empty archive", run(self.sh, "tar -czf /tmp/empty.tar.gz")[1].decode())

    def test_missing_archive_on_read(self):
        self.assertIn("No such file", run(self.sh, "tar -tzf /tmp/nope.tar.gz")[1].decode())

    def test_corrupt_archive(self):
        run(self.sh, "echo not-a-tarball > /tmp/bad.tar.gz")
        self.assertIn("not a valid tar archive", run(self.sh, "tar -tzf /tmp/bad.tar.gz")[1].decode())

    def test_round_trip_is_byte_for_byte(self):
        run(self.sh, "printf '\\x00\\x01binary\\xff' > /tmp/d/bin.dat")
        run(self.sh, "tar -czf /tmp/out.tar.gz /tmp/d/bin.dat")
        run(self.sh, "mkdir /tmp/e3")
        run(self.sh, "tar -xzf /tmp/out.tar.gz -C /tmp/e3")
        self.assertEqual(run(self.sh, "cat /tmp/e3/tmp/d/bin.dat")[0], b"\x00\x01binary\xff")
