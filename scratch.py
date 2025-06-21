import unittest

def chop(text):
    """
    Extract all innermost single-brace-delimited spans from a block of text and produce a list of
    non-delimited and delimited blocks. Escaped braces don't count as delimiters.
    """
    result = []
    cursor = 0
    lbrace = -1
    rbrace = -1
    escaped = False

    for i, c in enumerate(text):
        if escaped:
            escaped = False
        elif c == '\\':
            escaped = True
        elif c == '{':
            lbrace = i
        elif c == '}' and lbrace >= 0:
            rbrace = i
            if cursor < lbrace:
                result.append(text[cursor:lbrace])
            result.append(text[lbrace:rbrace + 1])
            cursor = rbrace + 1
            lbrace = -1
            rbrace = -1

    if cursor < len(text):
        result.append(text[cursor:])

    return result


class TestContext(unittest.TestCase):
    def test_basic(self):
        # Sanity check - Single braces should produce a block
        self.assertEqual(chop("a {b} c"), ['a ', '{b}', ' c'])

        # Degenerate cases should produce single blocks
        self.assertEqual(chop(""),  []   )
        self.assertEqual(chop("{"), ['{'])
        self.assertEqual(chop("}"), ['}'])
        self.assertEqual(chop("a"), ['a'])

        # Multiple single-braced blocks should not produce empty text between them if they touch
        self.assertEqual(chop("{a}{b}{c}"), ['{a}', '{b}', '{c}']  )

        # But if there's whitespace between them, it should be preserved
        self.assertEqual(chop(" {a} {b} {c} "), [' ', '{a}', ' ', '{b}', ' ', '{c}', ' '])

        # Whitespace inside a block should not split the block
        self.assertEqual(chop("{ a }{ b }{ c }"), ['{ a }', '{ b }', '{ c }'])

        # Unmatched braces
        self.assertEqual(chop("{"),   ['{'])
        self.assertEqual(chop("}"),   ['}'])

        self.assertEqual(chop("{}"),  ['{}'])
        self.assertEqual(chop("}{"),  ['}{'])
        self.assertEqual(chop("{a"),  ['{a'])
        self.assertEqual(chop("a}"),  ['a}'])

        self.assertEqual(chop("a{b"), ['a{b'])
        self.assertEqual(chop("a}b"), ['a}b'])
        self.assertEqual(chop("}}{"), ['}}{'])
        self.assertEqual(chop("}{{"), ['}{{'])
        self.assertEqual(chop("{{}"), ['{', '{}'])
        self.assertEqual(chop("{{}"), ['{', '{}'])
        self.assertEqual(chop("{}}"), ['{}', '}'])

        # Nesting
        self.assertEqual(chop("a{{b}}c"),      ['a{', '{b}', '}c'])
        self.assertEqual(chop("{a{b}c}"),      ['{a', '{b}', 'c}'])
        self.assertEqual(chop("x{a{b}{c}d}y"), ['x{a', '{b}', '{c}', 'd}y'])
        self.assertEqual(chop("{{{{a}}}}"),    ['{{{', '{a}', '}}}'])

        # Adjacent blocks with different brace counts
        self.assertEqual(chop("{a}{{b}}{c}"),   ['{a}', '{', '{b}', '}', '{c}'])
        self.assertEqual(chop("{{a}}{b}{{c}}"), ['{', '{a}', '}', '{b}', '{', '{c}', '}'])
        self.assertEqual(chop("{{a}}"),         ['{', '{a}', '}']       )
        self.assertEqual(chop("{{a}{b}}"),      ['{', '{a}', '{b}', '}'])
        self.assertEqual(chop("{{{a}}}"),       ['{{', '{a}', '}}']     )

        # Escaped braces should be ignored.
        self.assertEqual(chop("a\\{b\\}c"),  ['a\\{b\\}c']      )
        self.assertEqual(chop("a{\\}}b"),    ['a', '{\\}}', 'b'])
        self.assertEqual(chop("a{\\{}b"),    ['a', '{\\{}', 'b'])

        self.assertEqual(chop("\\"),            ['\\'])
        self.assertEqual(chop("{\\n}"),         ['{\\n}'])
        self.assertEqual(chop("a\\{b}"),        ['a\\{b}'])
        self.assertEqual(chop("a{b\\}"),        ['a{b\\}'])

        # Escaped backslashes should _not_ cause a following brace to be ignored.
        self.assertEqual(chop("a\\\\{b}"),      ['a\\\\', '{b}'])
        self.assertEqual(chop("a{b\\\\}"),      ['a', '{b\\\\}'])


if __name__ == "__main__":
    unittest.main(verbosity=0)

#print(chop("a {b} c"))
