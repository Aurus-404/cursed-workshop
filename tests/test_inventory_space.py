import unittest
from types import SimpleNamespace

from workshop.inventory_space import footprint, make_room
from workshop.syntax import parse


class Resources:
    def __init__(self, files):
        self.files = files
        self.entries = {k: SimpleNamespace(path=k) for k in files}

    def read(self, path):
        return self.files[path]

    def resolve_reference(self, identifier, from_path, suffixes):
        folder = from_path.rsplit('/', 1)[0]
        for suffix in suffixes:
            if folder + '/' + identifier + suffix in self.files:
                return folder + '/' + identifier + suffix
        raise FileNotFoundError(identifier)


FILES = {
    'properties/tank.ext': b'{extender "inventory" {box {Size 9 12} {item "can" 1}}}',
    'set/stuff/mgun/hmgun.pattern': b'{from "pattern bullet" {inventory {size 7 2}}}',
    'set/stuff/mgun/browning_m2': b'{from "pattern hmgun" {inventory {size 7 3}}}',
    'set/stuff/mgun/mg_light': b'{from "pattern hmgun" {entity "mg"}}',
}


class InventorySpaceTests(unittest.TestCase):
    def test_footprint_follows_patterns(self):
        resources = Resources(FILES)
        self.assertEqual(footprint(resources, 'browning_m2'), [7, 3])
        self.assertEqual(footprint(resources, 'mg_light'), [7, 2])
        self.assertIsNone(footprint(resources, 'ammo hmgun_usa'))

    def test_added_mg_and_ammo_get_rows_on_top_of_the_inherited_box(self):
        # Nr9T: a Sherman's own load fills the tank box; the rider M2 was dropped.
        root = parse('{game_entity (include "/properties/tank.ext") '
                     '{extender "inventory" {box {item "shell" 90}}}}').find('game_entity')
        grown = make_room(Resources(FILES), root, ['{item "browning_m2" filled {user "mgun4"}}',
                                                   '{item "ammo hmgun_usa" 300}'])
        self.assertEqual(grown, [9, 16])
        box = root.find('extender').find('box')
        self.assertEqual(box.find('size').args, ['9', '16'])
        self.assertEqual(len(box.find_all('size')), 1)

    def test_unbounded_box_stays_unbounded(self):
        root = parse('{game_entity {extender "inventory" {box {item "shell" 90}}}}').find('game_entity')
        self.assertIsNone(make_room(Resources(FILES), root, ['{item "browning_m2" filled}']))
        self.assertIsNone(root.find('extender').find('box').find('size'))


if __name__ == '__main__':
    unittest.main()
