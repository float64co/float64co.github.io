import unittest

from omnisploit.engine.world import World
from omnisploit.shell.script import parse_script, unparse
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


class FunctionTests(unittest.TestCase):
    def setUp(self):
        self.sh = Shell(World())

    def test_define_call_args_and_return_status(self):
        feed(self.sh, "greet() { echo hello $1; }", "function add { echo $(($1 + $2)); return 3; }")
        self.assertEqual(run(self.sh, "greet bob")[0], "hello bob\n")
        self.assertEqual(run(self.sh, "add 2 5; echo status=$?")[0], "7\nstatus=3\n")
        self.assertEqual(run(self.sh, "echo [$1]")[0], "[]\n")  # positional restored

    def test_multiline_definition_and_continuation_prompt(self):
        self.assertEqual(run(self.sh, "f() {"), ("", ""))
        self.assertEqual(self.sh.prompt(), "> ")
        run(self.sh, "echo in-f")
        run(self.sh, "}")
        self.assertEqual(run(self.sh, "f")[0], "in-f\n")

    def test_function_output_pipes_and_substitutes(self):
        feed(self.sh, "lines() { echo a; echo b; }")
        self.assertEqual(run(self.sh, "lines | tr a-z A-Z")[0], "A\nB\n")
        self.assertEqual(run(self.sh, "x=$(lines); echo $x")[0], "a b\n")

    def test_local_and_globals(self):
        feed(self.sh, "v=outer", "setit() { local v=inner; w=leaked; echo $v; }")
        self.assertEqual(run(self.sh, "setit; echo $v $w")[0], "inner\nouter leaked\n")
        self.assertIn("only be used in a function", run(self.sh, "local x=1")[1])
        self.assertIn("only `return' from a function", run(self.sh, "return")[1])

    def test_recursion_and_depth_limit(self):
        feed(self.sh, "fact() { if [ $1 -le 1 ]; then echo 1; else local p=$(fact $(($1 - 1))); echo $(($1 * p)); fi; }")
        self.assertEqual(run(self.sh, "fact 5")[0], "120\n")
        feed(self.sh, "boom() { boom; }")
        self.assertIn("nesting level", run(self.sh, "boom")[1])

    def test_functions_override_builtins_and_unset(self):
        feed(self.sh, "echo() { printf 'custom\\n'; }")
        self.assertEqual(run(self.sh, "echo hi")[0], "custom\n")
        run(self.sh, "unset -f echo")
        self.assertEqual(run(self.sh, "echo hi")[0], "hi\n")

    def test_type_and_declare(self):
        feed(self.sh, "hello() { echo hi; }")
        self.assertIn("hello is a function", run(self.sh, "type hello")[0])
        self.assertIn("declare -f hello", run(self.sh, "declare -F")[0])
        o = run(self.sh, "declare -f hello")[0]
        self.assertIn("echo hi", o)
        self.assertIn("{", o)

    def test_function_inside_script_with_args(self):
        feed(self.sh, "printf '#!/bin/bash\\nshout() { echo \"$1!\"; }\\nshout $1\\nshout two\\n' > /tmp/s.sh", "chmod +x /tmp/s.sh")
        self.assertEqual(run(self.sh, "/tmp/s.sh one")[0], "one!\ntwo!\n")


class CaseGroupTests(unittest.TestCase):
    def setUp(self):
        self.sh = Shell(World())

    def test_case_patterns(self):
        script = 'for x in apple b.txt zed; do case $x in a*|c*) echo "$x:fruit" ;; *.txt) echo "$x:text" ;; *) echo "$x:other" ;; esac; done'
        self.assertEqual(run(self.sh, script)[0], "apple:fruit\nb.txt:text\nzed:other\n")

    def test_case_multiline_and_no_match(self):
        feed(self.sh, "case q in", "a) echo a ;;", "b) echo b", ";;", "esac", "echo done")
        self.assertEqual(self.sh.status, 0)
        self.assertEqual(run(self.sh, "case x in x) echo yes;; esac")[0], "yes\n")
        self.assertEqual(run(self.sh, "case 'a b' in 'a b') echo quoted;; esac")[0], "quoted\n")

    def test_group_and_pipeline_of_groups(self):
        self.assertEqual(run(self.sh, "{ echo a; echo b; } | tr a-z A-Z")[0], "A\nB\n")
        self.assertEqual(run(self.sh, "{ echo x; } && echo y")[0], "x\ny\n")


class PipelineCompoundTests(unittest.TestCase):
    def setUp(self):
        self.sh = Shell(World())
        feed(self.sh, "printf 'alice 30\\nbob 25\\ncarol 41\\n' > /tmp/people")

    def test_while_read_from_pipe(self):
        o, _ = run(self.sh, "cat /tmp/people | while read name age; do echo $name is $age; done")
        self.assertEqual(o, "alice is 30\nbob is 25\ncarol is 41\n")

    def test_while_read_from_redirect_and_output_redirect(self):
        self.assertEqual(run(self.sh, "while read n a; do echo $n; done < /tmp/people")[0], "alice\nbob\ncarol\n")
        run(self.sh, "while read n a; do echo $a; done < /tmp/people > /tmp/ages")
        self.assertEqual(run(self.sh, "cat /tmp/ages")[0], "30\n25\n41\n")

    def test_read_last_var_takes_rest_and_eof_status(self):
        feed(self.sh, "echo 'a b c d' | { read x rest; echo [$x] [$rest]; }")
        self.assertEqual(run(self.sh, "echo 'a b c d' | { read x rest; echo [$x] [$rest]; }")[0], "[a] [b c d]\n")
        self.assertEqual(run(self.sh, "echo one | { read a; read b; echo $a/$b rc=$?; }")[0], "one/ rc=1\n")

    def test_commands_inside_see_same_stdin(self):
        o, _ = run(self.sh, "cat /tmp/people | { head -1; }")
        self.assertEqual(o, "alice 30\n")
        o, _ = run(self.sh, "for i in 3 1 2; do echo $i; done | sort | head -2")
        self.assertEqual(o, "1\n2\n")

    def test_compound_redirect_errors(self):
        self.assertIn("No such file", run(self.sh, "while read x; do echo $x; done < /nope")[1])
        o, e = run(self.sh, "for i in 1; do echo out; echo err >&2; done 2>/dev/null")
        self.assertEqual(o, "out\n")

    def test_ls_output_is_one_per_line_inside_substitution_and_pipes(self):
        o, _ = run(self.sh, "echo $(ls /etc | head -2)")
        self.assertEqual(o.count("\n"), 1)
        o, _ = run(self.sh, "{ ls /etc; } | cat")
        self.assertGreater(o.count("\n"), 1)  # not column-formatted


class Ps1Tests(unittest.TestCase):
    def test_default_and_custom_prompts(self):
        sh = Shell(World("neo", "zion.example"))
        self.assertEqual(sh.prompt(), "neo@zion:~$ ")
        run(sh, "export PS1='[\\u on \\H in \\W]\\$ '")
        self.assertEqual(sh.prompt(), "[neo on zion.example in ~]$ ")
        run(sh, "cd /etc")
        self.assertEqual(sh.prompt(), "[neo on zion.example in etc]$ ")
        run(sh, "PS1='\\w> '")
        self.assertEqual(sh.prompt(), "/etc> ")
        run(sh, "PS1='\\[\\e[0m\\]\\n\\u\\\\x '")  # unsupported escapes are dropped, \\\\ kept
        self.assertEqual(sh.prompt(), "neo\\x ")

    def test_prompt_goes_back_to_default_after_unset(self):
        sh = Shell(World())
        run(sh, "PS1='>> '")
        run(sh, "unset PS1")
        self.assertEqual(sh.prompt(), "player@lab:~$ ")


class UnparseRoundTripTests(unittest.TestCase):
    CASES = [
        "f() { echo hi; }", "function g { echo $1; return 3; }",
        "case $x in a|b) echo ab ;; *.txt) echo t ;; *) echo other ;; esac",
        "for i in 1 2; do if [ $i = 1 ]; then echo one; elif [ $i = 2 ]; then echo two; else echo n; fi; done",
        "while read l; do echo $l; done < /etc/passwd > /tmp/o", "{ echo a; echo b; } | cat",
        "a | while read x; do echo $x; done | sort", "false || { echo recovered; } && echo end",
        "until [ $i -ge 3 ]; do i=$((i+1)); done",
    ]

    def test_unparse_is_a_fixed_point(self):
        for src in self.CASES:
            once = unparse(parse_script(src))
            self.assertEqual(unparse(parse_script(once)), once, src)

    def test_unparsed_text_behaves_the_same(self):
        a, b = Shell(World()), Shell(World())
        src = "for i in 1 2 3; do case $i in 2) continue;; esac; echo n$i; done"
        b_src = unparse(parse_script(src))
        self.assertEqual(run(a, src), run(b, b_src))



class AssignmentSplittingTests(unittest.TestCase):
    def test_assignment_values_are_not_word_split(self):
        sh = Shell(World())
        self.assertEqual(run(sh, "x=$(printf 'a\\nb\\n'); echo \"[$x]\"")[0], "[a\nb]\n")
        self.assertEqual(run(sh, "y=$(echo a b c); echo $y")[0], "a b c\n")
        self.assertEqual(run(sh, "echo $(printf 'a\\nb\\n')")[0], "a b\n")  # but arguments still split
        self.assertEqual(run(sh, 'for w in $(echo p q); do echo "<$w>"; done')[0], "<p>\n<q>\n")


if __name__ == "__main__":
    unittest.main()
