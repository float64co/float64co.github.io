import unittest

from omnisploit.engine.world import World
from omnisploit.shell.pyinterp import PythonSession
from omnisploit.shell.shell import Shell


def run(sh, line):
    out = sh.run_line(line)
    o = b"".join(d for d, e in out.chunks if not e).decode(errors="replace")
    e = b"".join(d for d, e in out.chunks if e).decode(errors="replace")
    return o, e


class PythonTests(unittest.TestCase):
    def setUp(self):
        self.sh = Shell(World())

    def py(self, code):
        return run(self.sh, "python3 -c '%s'" % code)

    def script(self, src):
        self.sh.vfs.install("/tmp/s.py", src)
        return run(self.sh, "python3 /tmp/s.py")

    def test_dir(self):
        self.assertEqual(self.py("x = 1; y = 2\nprint(dir())")[0], "['x', 'y']\n")
        self.assertEqual(self.py("def f():\n    a = 1\n    return dir()\nprint(f())")[0], "['a']\n")
        self.assertIn("append", self.py("print(dir([]))")[0])
        self.assertNotIn("__", self.py("print(dir(\"s\"))")[0])
        self.assertIn("from_bytes", self.py("print(dir(int))")[0])
        self.assertIn("pack", self.py("import struct\nprint(dir(struct))")[0])

    def test_basics(self):
        self.assertEqual(self.py("print(1+2, [x*x for x in range(4)], b\"AB\".hex(), f\"{255:#x}\")")[0], "3 [0, 1, 4, 9] 4142 0xff\n")
        self.assertEqual(self.py("a, *b = [1,2,3]; print(a, b, \"%05.1f\" % 3.14159)")[0], "1 [2, 3] 003.1\n")
        self.assertEqual(self.py("print(sorted({\"b\":1,\"a\":2}.items(), key=lambda kv: kv[1]))")[0], "[('b', 1), ('a', 2)]\n")

    def test_functions_and_errors(self):
        src = "def f(n, k=2, *a, **kw):\\n    return n*k + len(a) + len(kw)\\nprint(f(3), f(3, 1, 9, 9, z=1))\\nx = 1/0\\n"
        self.sh.run_line("printf '%s' > /tmp/a.py" % src)
        o, e = run(self.sh, "python3 /tmp/a.py")
        self.assertEqual(o, "6 6\n")
        self.assertIn('File "/tmp/a.py", line 4', e)
        self.assertIn("ZeroDivisionError", e)
        self.assertEqual(run(self.sh, "echo $?")[0], "1\n")

    def test_try_except_and_exit_codes(self):
        self.assertEqual(self.script('try:\n    int("x")\nexcept ValueError as e:\n    print("bad")\nfinally:\n    print("done")\n')[0], "bad\ndone\n")
        run(self.sh, "python3 -c 'import sys; sys.exit(3)'")
        self.assertEqual(self.sh.status, 3)

    def test_files_are_the_simulated_vfs(self):
        run(self.sh, "python3 -c 'open(\"/tmp/n.txt\", \"w\").write(\"hi\\n\")'")
        self.assertEqual(run(self.sh, "cat /tmp/n.txt")[0], "hi\n")
        self.assertIn("FileNotFoundError", self.py("open(\"/nope\")")[1])
        self.assertIn("PermissionError", self.py("open(\"/opt/motd/flag.txt\")")[1])

    def test_stdin_and_struct(self):
        self.assertEqual(run(self.sh, "echo 41 | python3 -c 'import sys; print(int(sys.stdin.read())+1)'")[0], "42\n")
        self.assertEqual(self.py("import struct; print(struct.pack(\"<I\", 0x401196).hex())")[0], "96114000\n")

    def test_sandbox(self):
        self.assertIn("not allowed", self.py("print((1).__class__)")[1])
        self.assertIn("No module named 'os'", self.py("import os")[1])
        self.assertIn("not defined", self.py("eval(\"1\")")[1])
        self.assertIn("not defined", self.py("__import__(\"os\")")[1])
        self.assertIn("no attribute 'format'", self.py("print(\"{0}\".format(1))")[1])
        self.assertIn("too large", self.py("x = \"a\" * 10**9")[1])
        self.assertIn("too large", self.py("print(\"%999999999d\" % 1)")[1])
        self.assertIn("too large", self.py("bytes(10**9)")[1])
        self.assertIn("Execution limit", self.py("while True: pass")[1])
        self.assertIn("recursion", self.script("def f(): return f()\nf()")[1])
        self.assertIn("does not support ClassDef", self.py("class A: pass")[1])

    def test_unsupported_is_not_swallowed_by_except(self):
        self.assertIn("does not support", self.script('try:\n    class A: pass\nexcept:\n    print("hidden")\n')[1])

    def test_syntax_error(self):
        self.assertIn("SyntaxError", self.py("print(1")[1])

    def test_repl(self):
        run(self.sh, "python3")
        self.assertIsInstance(self.sh.session, PythonSession)
        self.assertEqual(self.sh.prompt(), ">>> ")
        run(self.sh, "x = 5")
        self.assertEqual(run(self.sh, "x * 2")[0], "10\n")
        run(self.sh, "def f(a):")
        self.assertEqual(self.sh.prompt(), "... ")
        run(self.sh, "    return a + 1")
        run(self.sh, "")
        self.assertEqual(run(self.sh, "f(x)")[0], "6\n")
        run(self.sh, "exit()")
        self.assertIsNone(self.sh.session)


class PwnModuleTests(unittest.TestCase):
    def setUp(self):
        self.sh = Shell(World())
        run(self.sh, "cd motd-daemon")

    def script(self, src):
        self.sh.vfs.install("/tmp/x.py", src)
        return run(self.sh, "python3 /tmp/x.py")

    def test_pack_unpack_and_context(self):
        o, e = self.script("from pwn import *\ncontext.arch = 'amd64'\nprint(p64(0x401196), p32(1, endian='big'), hex(u64(b'\\x96\\x11@\\0\\0\\0\\0\\0')), context.bits)\n")
        self.assertEqual(e, "")
        self.assertEqual(o, "b'\\x96\\x11@\\x00\\x00\\x00\\x00\\x00' b'\\x00\\x00\\x00\\x01' 0x401196 64\n")
        self.assertIn("unpack requires", self.script("from pwn import *\nu64(b'AAAA')\n")[1])
        self.assertIn("unsupported arch", self.script("from pwn import *\ncontext.arch = 'z80'\n")[1])
        self.assertIn("cannot set", self.script("from pwn import *\ncontext.word_size = 1\n")[1])

    def test_cyclic_helpers_and_flat(self):
        o, _ = self.script("from pwn import *\nprint(cyclic(8), cyclic_find(0x62616161), cyclic_find(b'baaa'), cyclic_find(b'1111'))\n")
        self.assertEqual(o, "b'aaaabaaa' 1 4 -1\n")
        o, _ = self.script("from pwn import *\ncontext.arch='amd64'\nprint(enhex(flat(b'A', [1, 2])), hexdump(b'hi').splitlines()[0])\n")
        self.assertTrue(o.startswith("41" + "01" + "00" * 7 + "02"))
        self.assertIn("68 69", o)

    def test_build_a_payload_and_use_it(self):
        src = ("from pwn import *\ncontext.arch = 'amd64'\n"
               "payload = b'A' * 72 + p64(0x401196)\n"
               "open('/tmp/pl', 'wb').write(payload + b'\\n')\nlog.info('wrote %d bytes', len(payload))\n")
        o, e = self.script(src)
        self.assertEqual((o, e), ("[*] wrote 80 bytes\n", ""))
        o, _ = run(self.sh, "./motd < /tmp/pl")
        self.assertIn("FLAG{ret2win_is_just_the_beginning}", o)

    def test_offset_from_a_crash_then_payload(self):
        run(self.sh, "python3 -c 'from pwn import *; open(\"/tmp/pat\", \"wb\").write(cyclic(100) + b\"\\n\")'")
        o, _ = run(self.sh, "./motd < /tmp/pat")
        self.assertIn("Hello, aaaabaaac", o)

    def test_elf_reads_level_metadata(self):
        o, e = self.script(
            "from pwn import *\n"
            "e = ELF('./motd')\n"
            "print(hex(e.address), hex(e.entry), e.canary, e.nx, e.pie, e.relro)\n"
            "print(e.symbols['win'], e.sym['vuln'], e.plt['gets'])\n"
            "print(e.functions['vuln'])\n"
        )
        self.assertEqual(e, "")
        lines = o.splitlines()
        self.assertEqual(lines[0], "0x400000 0x401196 False True False partial")
        self.assertEqual(lines[1], "4198806 4198825 4198464")
        self.assertEqual(lines[2], "{'address': 4198825, 'size': 69}")

    def test_elf_read_and_search(self):
        o, e = self.script(
            "from pwn import *\n"
            "e = ELF('./motd')\n"
            "print(e.search(b'cat /opt'))\n"
            "print(e.read(e.symbols['win'], 4).hex())\n"
        )
        self.assertEqual(e, "")
        self.assertEqual(o.splitlines(), ["[4202518]", "554889e5"])
        self.assertIn("ValueError", self.script("from pwn import *\nELF('./motd').read(0, 8)\n")[1])

    def test_elf_errors(self):
        self.assertIn("No such file", self.script("from pwn import *\nELF('/nope')\n")[1])
        self.assertIn("is not an ELF file", self.script("from pwn import *\nELF('/etc/passwd')\n")[1])
        self.assertIn("ELF() needs a path", self.script("from pwn import *\nELF(1)\n")[1])
        self.assertIn("ELF() supports only a path", self.script("from pwn import *\nELF('./motd', 1)\n")[1])

    def test_elf_on_a_non_level_binary_is_empty_but_not_an_error(self):
        o, e = self.script("from pwn import *\ne = ELF('/bin/ls')\nprint(e.symbols, e.address, e.functions)\n")
        self.assertEqual((o, e), ("{} 0 {}\n", ""))

    def test_rop_ret2win_solves_motd(self):
        src = (
            "from pwn import *\n"
            "context.arch = 'amd64'\n"
            "e = ELF('./motd')\n"
            "rop = ROP(e)\n"
            "rop.call('win')\n"
            "payload = b'A' * 72 + rop.chain()\n"
            "p = process('./motd')\n"
            "p.sendlineafter(b'? ', payload)\n"
            "out = p.recvall()\n"
            "print(b'FLAG{' in out, p.poll())\n"
        )
        o, e = self.script(src)
        self.assertEqual(o, "True -11\n")
        self.assertIn("solved:motd-daemon", self.sh.world.events)

    def test_rop_find_gadget_and_raw(self):
        o, e = self.script(
            "from pwn import *\n"
            "e = ELF('./motd')\n"
            "rop = ROP(e)\n"
            "print(rop.find_gadget(['pop rbp', 'ret']))\n"
            "print(rop.find_gadget(['nonexistent']))\n"
            "rop.raw(0x4141414141414141)\n"
            "print(rop.chain())\n"
            "print(rop.dump())\n"
        )
        self.assertEqual(e, "")
        lines = o.splitlines()
        self.assertEqual(lines[0], "4198823")
        self.assertEqual(lines[1], "None")
        self.assertEqual(lines[2], "b'AAAAAAAA'")
        self.assertEqual(lines[3], "0x0000: 0x4141414141414141")

    def test_rop_multi_arg_call_is_refused_not_faked(self):
        e = self.script("from pwn import *\ne = ELF('./motd')\nrop = ROP(e)\nrop.call('win', [1])\n")[1]
        self.assertIn("NotImplementedError", e)
        self.assertIn("no gadgets", e)

    def test_rop_needs_an_elf(self):
        self.assertIn("ROP() expects an ELF object", self.script("from pwn import *\nROP('nope')\n")[1])

    def test_rop_unknown_symbol(self):
        self.assertIn("no symbol named", self.script("from pwn import *\nROP(ELF('./motd')).call('nosuch')\n")[1])

    def test_elf_and_rop_attribute_sandbox(self):
        self.assertIn("not allowed", self.script("from pwn import *\nELF('./motd')._level\n")[1])
        self.assertIn("no attribute", self.script("from pwn import *\nELF('./motd').gadgets\n")[1])


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.sh = Shell(World())
        run(self.sh, "cd motd-daemon")

    def script(self, src):
        self.sh.vfs.install("/tmp/x.py", src)
        return run(self.sh, "python3 /tmp/x.py")

    def test_talk_to_the_program(self):
        o, e = self.script("from pwn import *\nlog_level = context.log_level = 'error'\np = process('./motd')\n"
                           "print(p.recvuntil(b'? '))\np.sendline(b'bob')\nprint(p.recvline(), p.recvall(), p.poll())\n")
        self.assertEqual((o, e), ("b'name? '\nb'Hello, bob\\n' b'' 0\n", ""))

    def test_exploit_script_gets_the_flag(self):
        src = ("from pwn import *\ncontext.arch = 'amd64'\np = process('./motd')\n"
               "p.sendlineafter(b'? ', b'A' * 72 + p64(0x401196))\nout = p.recvall()\nprint(b'FLAG{' in out, p.poll())\n")
        o, e = self.script(src)
        self.assertEqual(o, "True -11\n")
        self.assertIn("[+] Starting local process './motd': pid", e)
        self.assertIn("stopped with exit code -11", e)
        self.assertIn("solved:motd-daemon", self.sh.world.events)

    def test_waits_for_a_full_line_and_reports_state(self):
        o, _ = self.script("from pwn import *\ncontext.log_level = 'error'\np = process('./motd')\n"
                           "p.send(b'bo')\nprint(p.poll(), p.recv())\np.send(b'b\\n')\nprint(p.recvall(), p.poll())\n")
        self.assertEqual(o, "None b'name? '\nb'Hello, bob\\n' 0\n")

    def test_errors_and_limits(self):
        self.assertIn("No such file", self.script("from pwn import *\nprocess('./nope')\n")[1])
        self.assertIn("Exec format error", self.script("from pwn import *\nprocess('/etc/passwd')\n")[1])
        self.assertIn("does not support interactive()", self.script("from pwn import *\nprocess('./motd').interactive()\n")[1])
        self.assertIn("no attribute", self.script("from pwn import *\nprocess('./motd').cpu\n")[1])
        self.assertIn("too many open processes", self.script("from pwn import *\nfor i in range(9):\n    process('./motd')\n")[1])
        self.sh.vfs.get("motd", self.sh.cwd).mode = 0o644
        self.assertIn("Permission denied", self.script("from pwn import *\nprocess('./motd')\n")[1])

    def test_recv_after_exit_raises_eof(self):
        self.assertIn("EOFError", self.script("from pwn import *\ncontext.log_level='error'\np = process('./motd')\n"
                                              "p.sendline(b'x')\np.recvall()\np.recv()\n")[1])


class HelpAndExitTests(unittest.TestCase):
    def setUp(self):
        self.sh = Shell(World())

    def py(self, code):
        return run(self.sh, "python3 -c '%s'" % code)

    def test_bare_exit_quit_help_show_a_hint_instead_of_exiting(self):
        self.assertEqual(self.py("print(exit)"), ("Use exit() or Ctrl-D (i.e. EOF) to exit\n", ""))
        self.assertEqual(self.py("print(quit)"), ("Use exit() or Ctrl-D (i.e. EOF) to exit\n", ""))
        self.assertEqual(self.py("print(help)"), ("Type help() for interactive help, or help(object) for help about object.\n", ""))

    def test_exit_call_still_exits(self):
        run(self.sh, "python3 -c 'import sys; sys.exit(3)'")
        self.assertEqual(self.sh.status, 3)
        run(self.sh, "python3 -c 'exit()'")
        self.assertEqual(self.sh.status, 0)
        run(self.sh, "python3 -c 'exit(5)'")
        self.assertEqual(self.sh.status, 5)

    def test_help_no_args_consumes_stdin_topics_non_interactively(self):
        o, e = run(self.sh, "echo 'for\nquit' | python3 -c 'help()'")
        self.assertEqual(e, "")
        self.assertIn("Welcome to Python 3.10's help utility!", o)
        self.assertIn("Keyword: for", o)
        self.assertIn("Runs the body once per item", o)

    def test_help_no_args_with_no_stdin_just_shows_the_banner(self):
        o, e = self.py("help()")
        self.assertEqual(e, "")
        self.assertIn("Welcome to Python 3.10's help utility!", o)

    def test_help_with_a_topic_string(self):
        self.assertIn("Runs the body once per item", self.py('help("for")')[0])
        self.assertIn("Not supported in this simulation", self.py('help("class")')[0])
        self.assertIn("no documentation found", self.py('help("nosuchtopic")')[0])
        self.assertEqual(self.py('help("modules")')[0].strip(), "Modules available: math, pwn, struct, sys")

    def test_help_with_a_real_object(self):
        self.assertIn("Return a new list", self.py("help(sorted)")[0])
        o, e = self.py("import math\nhelp(math)")
        self.assertIn("sqrt, floor, ceil", o)

    def test_help_on_a_users_own_function(self):
        src = "def add(a, b):\n    'Add two numbers.'\n    return a + b\nhelp(add)\n"
        self.sh.vfs.install("/tmp/h.py", src)
        o, e = run(self.sh, "python3 /tmp/h.py")
        self.assertEqual(e, "")
        self.assertIn("Help on function add", o)
        self.assertIn("Add two numbers.", o)

    def test_help_does_not_break_lambdas_or_lambda_repr(self):
        self.assertEqual(self.py("f = lambda x: x + 1\nprint(f(4))")[0], "5\n")

    def test_live_repl_help_sub_prompt(self):
        from omnisploit.shell.pyinterp import PythonSession

        run(self.sh, "python3")
        self.assertIsInstance(self.sh.session, PythonSession)
        o, _ = run(self.sh, "help()")
        self.assertIn("Welcome to Python 3.10's help utility!", o)
        self.assertEqual(self.sh.prompt(), "help> ")
        o, _ = run(self.sh, "for")
        self.assertIn("Keyword: for", o)
        self.assertEqual(self.sh.prompt(), "help> ")  # stays in help mode for more topics
        run(self.sh, "")  # blank line leaves help
        self.assertEqual(self.sh.prompt(), ">>> ")
        self.assertEqual(run(self.sh, "1 + 1")[0], "2\n")  # back to ordinary evaluation
        run(self.sh, "exit()")
        self.assertIsNone(self.sh.session)

    def test_ctrl_d_calls_exit_in_python_but_just_exits_bash(self):
        from omnisploit.ui.terminal import Terminal

        term = Terminal(lambda s: (0, []))

        def ctrl_d(sh):
            line = term.key("\x04", sh.prompt(), sh.secret_input(), sh.eof_line())
            if line is not None:
                run(sh, line)
            return line

        self.assertEqual(ctrl_d(self.sh), "exit")  # plain bash: Ctrl-D still means "exit"
        self.assertEqual(self.sh.status, 0)

        sh2 = Shell(World())
        run(sh2, "python3")
        self.assertEqual(sh2.eof_line(), "exit()")
        self.assertEqual(ctrl_d(sh2), "exit()")
        self.assertIsNone(sh2.session)  # the interpreter actually ran exit(), not the bare name


class ShebangTests(unittest.TestCase):
    def setUp(self):
        self.sh = Shell(World("player", "lab"))

    def script(self, shebang, path="t.py", body='import sys\nprint("ran", sys.argv)'):
        self.sh.vfs.install(self.sh.abspath(path), (shebang + "\n" + body + "\n").encode(),
                            owner="player", mode=0o755)

    def test_python_shebangs_run_with_dot_slash(self):
        for shebang in ("#!/usr/bin/python", "#!/usr/bin/python3", "#!/usr/bin/env python3",
                        "#!/usr/bin/env python", "#! /usr/bin/python3", "#!/usr/bin/python3 -u"):
            self.script(shebang)
            self.assertEqual(run(self.sh, "./t.py a b")[0].strip(), "ran ['./t.py', 'a', 'b']", shebang)

    def test_script_found_on_path(self):
        run(self.sh, "mkdir -p ~/bin")
        self.script("#!/usr/bin/env python3", "bin/tool")
        run(self.sh, "PATH=$PATH:$HOME/bin")
        self.assertEqual(run(self.sh, "tool x")[0].strip(), "ran ['/home/player/bin/tool', 'x']")

    def test_stdin_reaches_the_script(self):
        self.script("#!/usr/bin/python3", body="print(input().upper())")
        self.assertEqual(run(self.sh, "echo hello | ./t.py")[0].strip(), "HELLO")

    def test_missing_or_unknown_interpreters_still_fail(self):
        for shebang in ("#!/opt/nowhere/python3", "#!/usr/bin/ruby", "#!/usr/bin/env ruby"):
            self.script(shebang)
            self.assertIn("bad interpreter", run(self.sh, "./t.py")[1], shebang)

    def test_a_script_without_the_execute_bit_is_refused(self):
        self.script("#!/usr/bin/python3")
        run(self.sh, "chmod -x t.py")
        self.assertIn("Permission denied", run(self.sh, "./t.py")[1])
