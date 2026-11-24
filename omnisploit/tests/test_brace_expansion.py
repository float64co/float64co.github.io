import unittest

from omnisploit.engine.world import World
from omnisploit.shell.parser import expand_braces_in_line
from omnisploit.shell.shell import Shell


def run(sh, text):
    out = sh.run_line(text)
    return (
        b"".join(d for d, e in out.chunks if not e).decode(),
        b"".join(d for d, e in out.chunks if e).decode(),
    )


class ExpandBracesInLineTests(unittest.TestCase):
    def test_numeric_ascending_range(self):
        self.assertEqual(expand_braces_in_line("echo {1..5}"), "echo 1 2 3 4 5")

    def test_numeric_descending_range(self):
        self.assertEqual(expand_braces_in_line("echo {5..1}"), "echo 5 4 3 2 1")

    def test_numeric_range_with_step(self):
        self.assertEqual(expand_braces_in_line("echo {1..10..2}"), "echo 1 3 5 7 9")

    def test_zero_padded_range(self):
        self.assertEqual(expand_braces_in_line("echo {01..5}"), "echo 01 02 03 04 05")

    def test_negative_numbers(self):
        self.assertEqual(expand_braces_in_line("echo {-2..2}"), "echo -2 -1 0 1 2")

    def test_letter_range(self):
        self.assertEqual(expand_braces_in_line("echo {a..e}"), "echo a b c d e")

    def test_descending_letter_range(self):
        self.assertEqual(expand_braces_in_line("echo {e..a}"), "echo e d c b a")

    def test_comma_list(self):
        self.assertEqual(expand_braces_in_line("echo {red,green,blue}"), "echo red green blue")

    def test_prefix_and_suffix_are_kept(self):
        self.assertEqual(expand_braces_in_line("echo file{1,2,3}.txt"), "echo file1.txt file2.txt file3.txt")

    def test_two_brace_groups_cross_product(self):
        self.assertEqual(expand_braces_in_line("echo {a,b}{1,2}"), "echo a1 a2 b1 b2")

    def test_single_quoted_braces_stay_literal(self):
        self.assertEqual(expand_braces_in_line("echo '{1..3}'"), "echo '{1..3}'")

    def test_double_quoted_braces_stay_literal(self):
        self.assertEqual(expand_braces_in_line('echo "{1..3}"'), 'echo "{1..3}"')

    def test_parameter_expansion_is_not_treated_as_brace_expansion(self):
        self.assertEqual(expand_braces_in_line("echo ${foo}"), "echo ${foo}")

    def test_single_item_no_comma_no_range_is_left_literal(self):
        self.assertEqual(expand_braces_in_line("echo {foo}"), "echo {foo}")

    def test_no_braces_is_a_no_op(self):
        self.assertEqual(expand_braces_in_line("echo plain text"), "echo plain text")

    def test_unmatched_brace_is_left_alone(self):
        self.assertEqual(expand_braces_in_line("echo {oops"), "echo {oops")


class BraceExpansionInTheShellTests(unittest.TestCase):
    def setUp(self):
        self.w = World()
        self.sh = Shell(self.w)

    def test_echo_range(self):
        o, _ = run(self.sh, "echo {1..5}")
        self.assertEqual(o, "1 2 3 4 5\n")

    def test_for_loop_over_a_range(self):
        o, _ = run(self.sh, "for i in {1..5}; do echo $i; done")
        self.assertEqual(o, "1\n2\n3\n4\n5\n")

    def test_printf_over_a_range_with_no_loop_at_all(self):
        o, _ = run(self.sh, "printf '%s\\n' {1..5}")
        self.assertEqual(o, "1\n2\n3\n4\n5\n")

    def test_comma_list_as_filenames(self):
        o, _ = run(self.sh, "echo /tmp/file{a,b,c}.txt")
        self.assertEqual(o, "/tmp/filea.txt /tmp/fileb.txt /tmp/filec.txt\n")

    def test_quoted_brace_is_not_expanded_in_a_real_command(self):
        o, _ = run(self.sh, 'echo "{1..3}"')
        self.assertEqual(o, "{1..3}\n")

    def test_dollar_brace_variable_still_works(self):
        run(self.sh, "foo=hello")
        o, _ = run(self.sh, "echo ${foo}")
        self.assertEqual(o, "hello\n")


class AutomationMissionBraceExpansionTests(unittest.TestCase):
    def setUp(self):
        self.w = World()
        self.sh = Shell(self.w)
        run(self.sh, "mkdir ~/workspace")

    def write_script(self, body: str):
        run(self.sh, f"printf '{body}' > ~/workspace/count.sh")
        run(self.sh, "chmod +x ~/workspace/count.sh")

    def completed(self):
        self.w.missions.evaluate()
        return list(self.w.missions.completed)

    def test_classic_for_loop_still_completes_it(self):
        self.write_script(r"#!/bin/bash\nfor i in 1 2 3 4 5; do echo \$i; done\n")
        self.assertEqual(self.completed(), ["automation"])

    def test_for_loop_over_brace_expansion_completes_it(self):
        self.write_script(r"#!/bin/bash\nfor i in {1..5}; do echo \$i; done\n")
        self.assertEqual(self.completed(), ["automation"])

    def test_brace_expansion_with_no_loop_keyword_also_completes_it(self):
        self.write_script(r"#!/bin/bash\nprintf \x27%%s\\n\x27 {1..5}\n")
        self.assertEqual(self.completed(), ["automation"])

    def test_plain_hardcoded_echo_with_no_loop_or_braces_does_not_complete_it(self):
        self.write_script(r"#!/bin/bash\necho 1\necho 2\necho 3\necho 4\necho 5\n")
        self.assertEqual(self.completed(), [])
