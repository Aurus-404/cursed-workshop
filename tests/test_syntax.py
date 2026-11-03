import unittest
from workshop.syntax import parse, block, bone_name, SyntaxError, replace_atoms, entity_root


class SyntaxTests(unittest.TestCase):
    def test_repeated_parsing_never_shares_mutable_tokens_or_ignores_parse_mode(self):
        text='; header\n{a "original" {b 3}}'
        first=parse(text);first.find('a').atoms[1].set('changed')
        first.find('a').find('b').atoms[1].set('4')
        self.assertEqual(parse(text).render(),text)
        self.assertEqual(parse(text.encode()).render(),text)
        parse('{a {b {c}}}',max_depth=5)
        with self.assertRaises(SyntaxError):parse('{a {b {c}}}',max_depth=1)
        parse('{a})',lenient=True)
        with self.assertRaises(SyntaxError):parse('{a})')

    def test_cached_trees_stay_pristine_after_the_repeat_that_stores_them(self):
        text='{cached_once "x" {inner 1}}'
        for _ in range(4):
            document=parse(text)
            self.assertEqual(document.render(),text)
            document.children[0].atoms[1].set('mutated');document.children[0].remove(document.children[0].find('inner'))

    def test_clones_share_no_mutable_token_with_their_source(self):
        import copy
        document=parse('; c\n{a "q" (m x) {b 1 [k]}}')
        document.children[0].marker='kept'
        for clone in (document.clone(),document.children[0].clone()):
            source=document if hasattr(clone,'opener')==False else document.children[0]
            self.assertEqual(clone.render(),source.render())
            self.assertEqual(clone,copy.deepcopy(source))
            ids={id(n) for n in source.walk()}|{id(a) for n in source.walk() for a in n.atoms}
            self.assertFalse(ids&({id(n) for n in clone.walk()}|{id(a) for n in clone.walk() for a in n.atoms}))
        clone=document.children[0].clone()
        self.assertEqual(clone.marker,'kept')
        clone.find('b').atoms[1].set('2');clone.items.append(block('{new}'))
        self.assertEqual(document.render(),'; c\n{a "q" (m x) {b 1 [k]}}')

    def test_token_rules_match_the_game_text_layout(self):
        text='﻿{a/b c//x\r\nd;y\re /*z*/f "q\\\\\\"\\n"}'
        document=parse(text)
        self.assertEqual(document.render(),text)
        self.assertEqual([a.value for a in document.children[0].atoms],['a/b','c','d','e','f','q\\"\\n'])
        with self.assertRaisesRegex(SyntaxError,'Unterminated quoted string at line 1, column 2'):parse('{"open')
        with self.assertRaisesRegex(SyntaxError,'Unterminated block comment'):parse('{/* x')
        with self.assertRaisesRegex(SyntaxError,"Missing closing '\\)'"):parse('{a (b}',lenient=True)
        with self.assertRaisesRegex(SyntaxError,"Unexpected '\\]'"):parse('{a ]}')

    def test_lossless_nested_comments_escapes_and_macros(self):
        text = '; {ignored}\n{Bone revolute "turret" /* } */ {File "a\\\\b\\\"c.ply"} ("crew" place(gunner))}\n'
        document = parse(text)
        self.assertEqual(document.render(), text)
        bone = document.children[0]
        self.assertEqual(bone_name(bone), "turret")
        self.assertEqual(bone.find("file").args, ['a\\b"c.ply'])
        self.assertEqual(len(bone.children), 2)

    def test_exact_mutation_preserves_comments_and_unknown_blocks(self):
        document = parse('{a "gun" "gunner" ; gun\n{unknown "gun"}}')
        replace_atoms(document, {"gun": "new_gun"})
        self.assertEqual(document.render(), '{a "new_gun" "gunner" ; gun\n{unknown "new_gun"}}')

    def test_malformed_rejected_with_location(self):
        for text in ('{a)', '{a "unterminated}', '{a /*never}', '{a'):
            with self.subTest(text=text), self.assertRaisesRegex(SyntaxError, 'line'):
                parse(text)

    def test_bare_entity_block_named_game_entity(self):
        document = parse('{\n\t(include "lafette.inc")\n}')
        root = entity_root(document)
        self.assertEqual(root.head, "game_entity")
        self.assertIs(entity_root(document), root)
        self.assertIsNone(entity_root(parse('{a}{b}')))

    def test_unknown_windows_escape_preserved(self):
        self.assertEqual(block('{file "dir\\path.ply"}').args, ['dir\\path.ply'])

    def test_lenient_reading_keeps_stray_closers_the_game_accepts(self):
        # Field report 20/10: a mod's global file had a stray ")" at line 66.
        text = '{entities\n\t(include "a.inc")\n)\n\t{x 1}}\n)\n(include "b.inc")\n'
        with self.assertRaises(SyntaxError):
            parse(text)
        document = parse(text, lenient=True)
        self.assertEqual(document.render(), text)
        includes = [node.args[0] for node in document.walk() if node.head == 'include']
        self.assertEqual(includes, ['a.inc', 'b.inc'])
        self.assertEqual(document.find('entities').find('x').args, ['1'])


class OldMacLineBreakTests(unittest.TestCase):
    def test_comments_end_at_a_lone_carriage_return(self):
        # SturmDrachen (MEC): files with \r-only line breaks read as one line,
        # and the first ";" comment swallowed every closing bracket after it.
        text = '{game_entity ; a comment\r{mass 9000}\r(include "x.ext") ; another\r{Weaponry {place "gun"}}\r}\r'
        document = parse(text)
        self.assertEqual([c.head for c in document.children[0].children], ["mass", "include", "Weaponry"])
        self.assertEqual(document.render(), text)
        with self.assertRaisesRegex(SyntaxError, "line 3"):
            parse("{a\r{b\r")


if __name__ == '__main__':
    unittest.main()
