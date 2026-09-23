#!/usr/bin/env python3
import os
import sys
import unittest

import hancho

# pyright: reportAttributeAccessIssue=false

####################################################################################################

def setUpModule():
    os.chdir(os.path.dirname(__file__))

def split(template):
    return hancho.Expander._split_text(template)

####################################################################################################

# FIXME test a nested dict using different delimiters than the parent dict

class TestSplit(unittest.TestCase):
    def setUp(self):
        sys.stdout.flush()

    def tearDown(self):
        sys.stdout.flush()

    def test_hash_matches_str(self):
        self.assertEqual(hash("a"), hash("a"))
        self.assertEqual(hash("{a}"), hash("{a}"))

    def test_basic(self):
        # Sanity check - Single braces should produce a block
        self.assertEqual(split("a {b} c"), ["a ", "{b}", " c"])

        # Degenerate cases should produce single blocks
        self.assertEqual(split(""), [])
        self.assertEqual(split("{"), ["{"])
        self.assertEqual(split("}"), ["}"])
        self.assertEqual(split("a"), ["a"])

        # Multiple single-braced blocks should not produce empty text between them if they touch
        self.assertEqual(split("{a}{b}{c}"), ["{a}", "{b}", "{c}"])

        # But if there's whitespace between them, it should be preserved
        self.assertEqual(
            split(" {a} {b} {c} "), [" ", "{a}", " ", "{b}", " ", "{c}", " "]
        )

        # Whitespace inside a block should not split the block
        self.assertEqual(split("{ a }{ b }{ c }"), ["{ a }", "{ b }", "{ c }"])

        # Unmatched braces
        self.assertEqual(split("{"), ["{"])
        self.assertEqual(split("}"), ["}"])

        self.assertEqual(split("{}"), ["{}"])
        self.assertEqual(split("}{"), ["}{"])
        self.assertEqual(split("{a"), ["{a"])
        self.assertEqual(split("a}"), ["a}"])

        self.assertEqual(split("a{b"), ["a{b"])
        self.assertEqual(split("a}b"), ["a}b"])
        self.assertEqual(split("}}{"), ["}}{"])
        self.assertEqual(split("}{{"), ["}{{"])
        self.assertEqual(split("{{}"), ["{", "{}"])
        self.assertEqual(split("{}}"), ["{}", "}"])

        # Nesting
        self.assertEqual(split("a{{b}}c"), ["a{", "{b}", "}c"])
        self.assertEqual(split("{a{b}c}"), ["{a", "{b}", "c}"])
        self.assertEqual(split("x{a{b}{c}d}y"), ["x{a", "{b}", "{c}", "d}y"])
        self.assertEqual(split("{{{{a}}}}"), ["{{{", "{a}", "}}}"])

        # Adjacent blocks with different brace counts
        self.assertEqual(
            split("{a}{{b}}{c}"), ["{a}", "{", "{b}", "}", "{c}"]
        )
        self.assertEqual(
            split("{{a}}{b}{{c}}"),
            ["{", "{a}", "}", "{b}", "{", "{c}", "}"],
        )
        self.assertEqual(split("{{a}}"), ["{", "{a}", "}"])
        self.assertEqual(split("{{a}{b}}"), ["{", "{a}", "{b}", "}"])
        self.assertEqual(split("{{{a}}}"), ["{{", "{a}", "}}"])

        # Escaped braces should be ignored.
#        self.assertEqual(split(r"a\{b\}c"), [r"a\{b\}c"])
#        self.assertEqual(split(r"a{\}}b"), ["a", r"{\}}", "b"])
#        self.assertEqual(split(r"a{\{}b"), ["a", r"{\{}", "b"])

#        self.assertEqual(split("\\"), ["\\"])
#        self.assertEqual(split(r"{\n}"), [r"{\n}"])
#        self.assertEqual(split(r"a\{b}"), [r"a\{b}"])
#        self.assertEqual(split(r"a{b\}"), [r"a{b\}"])

        # Escaped backslashes should _not_ cause a following brace to be ignored.
        self.assertEqual(split(r"a\\{b}"), [r"a\\", r"{b}"])
        self.assertEqual(split(r"a{b\\}"), [r"a", r"{b\\}"])

#        self.assertEqual(split(r"a \{a\} a"), [r"a \{a\} a"])
#        self.assertEqual(split(r"a \\{a\\} a"), [r"a \\", r"{a\\}", r" a"])
#        self.assertEqual(split(r"a \\\{a\\\} a"), [r"a \\\{a\\\} a"])


####################################################################################################

if __name__ == "__main__":
    unittest.main(verbosity=999)
