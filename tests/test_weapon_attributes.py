import unittest

from workshop.syntax import block, entity_root, parse


class Entry:
    def __init__(self, path):
        self.path = path


class Stuff:
    """Weapon files the way the game writes them: patterns, macros, inheritance."""
    def __init__(self):
        self.files = {
            'set/stuff/.presets': '''
                (define "reload time" {rechargeTime %seconds})
                (define "fire rate" {recoveryTime (/ 60 %rpm)})
                (define "range_200" {range 200 200})
                (define "velocity m/s" {parameters "ap" {speed (* 0.75 %ap)}} {parameters "he" {speed (* 0.75 %he)}})
                (define "penetration_medium" {parameters "%shell"
                    {projectileDamageTable {10 %a} {100 %b} {%range %c} {(+ %range 15) 0}} {range %range %range}})''',
            'set/stuff/gun/gun.pattern': '{weapon_bullet {rechargeTime 5} {range 15 25} {healthDamage (* 1 2500)} {FiringTimeout 0.35 0.15}}',
            'set/stuff/gun/basegun': '''{from "pattern gun" ("reload time" seconds(7)) ("range_200")
                ("velocity m/s" ap(750) he(570)) ("penetration_medium" shell(ap) range(200) a(100) b(80) c(60))
                {filling "shell_75"}
                {parameters "ap" {spreading {radiusTable {0 0} {100 1} {(- 200 0.01) 2} {(+ 200 50) 10}}}}}''',
            'set/stuff/gun/mygun': '{from "basegun"}',
            'set/stuff/mgun/mg.pattern': '{weapon_bullet {burst {short 6 1} {long 7 2} {rangeKoef 0.5}} {FiringTimeout 1.75 0.5} {spreading {SpreadRadius 5}} {range 120 120}}',
            'set/stuff/mgun/mg': '{from "pattern mg" {filling "mgun_x" 100} ("fire rate" rpm(600)) {rechargeTime 8}}',
        }
        self.entries = {key: Entry(key) for key in self.files}

    def read(self, path):
        return self.files[path].encode()

    def resolve_reference(self, name, from_path, suffixes):
        raise FileNotFoundError(name)


VEHICLE = '''{game_entity
    {Weaponry
        {place "gun" {weapon "mygun" filling "shell_75 ap" 1} {gunner "gunner"}}
        {place "mgun" {type "coaxial"} {weapon "mg" filled}}
        {place "vision" {weapon "commander_vision"}}}
    {extender "inventory" {box {item "shell_75 ap" 30} {item "shell_75 he" 20} {item "ammo mgun_x belt" 1000}}}}'''


class WeaponAttributeTests(unittest.TestCase):
    def setUp(self):
        self.stuff = Stuff()
        self.root = entity_root(parse(VEHICLE))

    def test_each_weapon_place_lists_the_values_it_really_has(self):
        from workshop.weapon_attributes import describe
        gun, mg = describe(self.stuff, self.root)  # the vision device is no weapon
        self.assertEqual((gun['place'], gun['weapon']), ('gun', 'mygun'))
        self.assertEqual(gun['fields'], {'reload_time': 7, 'range': 200, 'burst_pause': 0.35, 'dispersion': 1.0})
        ap, he = gun['shells']
        self.assertEqual((ap['key'], ap['velocity'], ap['infantry_damage']), ('ap', 750, 2500))
        self.assertEqual(ap['penetration'], [[10, 100], [100, 80], [200, 60], [215, 0]])
        self.assertEqual((he['key'], he['velocity']), ('he', 570))
        self.assertNotIn('penetration', he)
        self.assertEqual([a['item'] for a in gun['ammo']], ['shell_75 ap', 'shell_75 he'])
        self.assertEqual(mg['type'], 'coaxial')
        self.assertEqual(mg['fields'], {'reload_time': 8, 'fire_rate': 100.0 * 6, 'magazine': 100, 'range': 120,
                                        'burst_pause': 1.75, 'burst_short': 6, 'burst_long': 7, 'dispersion': 1.0})
        self.assertEqual(mg['ammo'], [{'item': 'ammo mgun_x belt', 'count': 1000, 'shared': []}])

    def test_an_edited_place_gets_its_own_copy_of_the_weapon(self):
        from workshop.weapon_attributes import apply, describe
        spec = {'places': {'gun': {'reload_time': 3, 'range': 300, 'dispersion': 0.5,
                                   'shells': {'ap': {'velocity': 800, 'penetration': [120, 90, 70, 0]}}},
                           'mgun': {'magazine': 150, 'burst_long': 12, 'burst_pause': 0.5, 'dispersion': 0.5}},
                'ammo': {'shell_75 ap': 99}}
        files, renamed, totals = apply(self.stuff, self.root, spec, 'mw_abc')
        self.assertEqual(renamed, ['gun', 'mgun'])
        self.assertEqual(totals, {'shell_75 ap': 99})
        gun = files['resource/set/stuff/gun/mw_abc_gun']
        self.assertTrue(gun.startswith('{from "mygun"'))
        for text in ('{rechargeTime 3}', '{range 300 300}', '{speed (* 0.75 800)}',
                     '{projectileDamageTable {10 120} {100 90} {300 70} {315 0}}',
                     '{radiusTable {0 0} {100 0.5} {299.99 1} {350 5}}'):
            self.assertIn(text, gun)
        mg = files['resource/set/stuff/mgun/mw_abc_mgun']
        for text in ('{filling "mgun_x" 150}', '{long 12 2}', '{FiringTimeout 0.5 0.1429}', '{SpreadRadius 2.5}'):
            self.assertIn(text, mg)
        self.assertNotIn('rechargeTime', mg)
        weapons = [p.find('weapon').args[0] for w in self.root.find_all('Weaponry') for p in w.find_all('place')]
        self.assertEqual(weapons, ['mw_abc_gun', 'mw_abc_mgun', 'commander_vision'])
        self.assertIn('{item "shell_75 ap" 99}', self.root.render())
        # The copy is read by the same rules: the edited values are what it has now.
        self.stuff.files['set/stuff/gun/mw_abc_gun'] = gun
        self.stuff.entries['set/stuff/gun/mw_abc_gun'] = Entry('set/stuff/gun/mw_abc_gun')
        edited = describe(self.stuff, self.root)[0]
        self.assertEqual((edited['fields']['reload_time'], edited['fields']['range']), (3, 300))
        self.assertEqual(edited['shells'][0]['velocity'], 800)

    def test_a_weapon_copy_shows_the_original_name(self):
        from workshop.weapon_attributes import apply
        self.stuff.files['localizations/default/interface/text/desc/w.pot'] = 'msgctxt "desc/weapon/mygun"\nmsgid "5cm PaK 38"\nmsgstr ""\n'
        self.stuff.files['localizations/es/interface/text/desc/w.po'] = 'msgctxt "desc/weapon/mygun"\nmsgid "5cm PaK 38"\nmsgstr "PaK 38 de 5 cm"\n'
        for key in self.stuff.files:
            entry = self.stuff.entries[key] = Entry(key)
            entry.priority = 0
        self.stuff.read_text = lambda path: self.stuff.files[path]
        files, _, _ = apply(self.stuff, self.root, {'places': {'gun': {'reload_time': 3}}}, 'mw_abc')
        pot = files['localizations/default/interface/text/desc/modular_workshop_mw_abc_weapons.pot']
        po = files['localizations/es/interface/text/desc/modular_workshop_mw_abc_weapons.po']
        self.assertIn('msgctxt "desc/weapon/mw_abc_gun"\nmsgid "5cm PaK 38"', pot)
        self.assertIn('msgstr "PaK 38 de 5 cm"', po)

    def test_values_out_of_bounds_or_unknown_fields_are_refused(self):
        from workshop.weapon_attributes import WeaponAttributeError, validate
        for bad in ({'places': {'gun': {'range': 0}}}, {'places': {'gun': {'reload': 3}}},
                    {'places': {'gun': {'shells': {'ap': {'penetration': []}}}}}, {'ammo': {'x': -1}},
                    {'places': {'bad place!': {'range': 10}}}, {'other': 1}):
            with self.subTest(bad=bad), self.assertRaises(WeaponAttributeError):
                validate(bad)
        validate({'places': {'gun': {'range': 250, 'shells': {'': {'velocity': 900}}}}, 'ammo': {'shell_75 ap': 0}})

    def test_the_save_drops_the_native_weapon_state_and_takes_the_new_ammo(self):
        from workshop.campaign import _set_ammo_totals
        from workshop.weapon_state import reconcile_weaponry
        entity = block('{Entity "mw_abc" 0x10 {Weaponry {State "gun" {Weapon "mygun" filling "shell_75" "ap" "ammo"}} {State "mgun" {Bullets 7}}}}')
        reconcile_weaponry(entity, {'weapon_copy_places': ['gun']})
        self.assertEqual([s.args[0] for s in entity.find('Weaponry').children], ['mgun'])
        inventory = block('{Inventory 0x10 {box {item "shell_75" "ap" "ammo" 117 {cell 0 3}} {item "shell_75" "ap" "ammo" 4 {cell 1 3}} '
                          '{item "mgun_x" "belt" "ammo" 900 {cell 0 6}} {item "can" {cell 0 0}}}}')
        _set_ammo_totals(inventory, {'shell_75 ap': 40, 'ammo mgun_x belt': 0, 'shell_75 he': 12})
        items = [i.render() for i in inventory.find('box').find_all('item')]
        self.assertEqual(items, ['{item "shell_75" "ap" "ammo" 40 {cell 0 3}}', '{item "can" {cell 0 0}}',
                                 '{item "shell_75" "he" "ammo" 12}'])


if __name__ == '__main__':
    unittest.main()
