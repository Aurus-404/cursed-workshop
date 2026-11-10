"""Editing a vehicle that carries inherited fittings and a second gun."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from workshop.compiler import _guns_first
from workshop.draft import compile_draft
from workshop.extra_armor import resolve_fitting
from workshop.syntax import parse


class StackedEditTests(unittest.TestCase):
    def test_the_check_says_what_the_shop_would_ask(self):
        from workshop.app import AppState
        with tempfile.TemporaryDirectory() as temp:
            state = AppState(Path(temp))
            state.settings = {**state.settings, 'game_root': temp}
            state.index = object()
            metadata = {'recipient': 'hull', 'donor': 'donor', 'transform': {}}
            prices = {'hull': (1000, 1), 'donor': (500, 1)}
            with mock.patch('workshop.registration.shop_prices', return_value=prices) as read:
                self.assertEqual(state.suggested_shop_cost(metadata), 1125, '75% of its parts, as the shop')
                state.suggested_shop_cost(metadata)
            read.assert_called_once()
            with mock.patch.object(state, 'shop_price_table', side_effect=OSError('no roster')):
                self.assertIsNone(state.suggested_shop_cost(metadata), 'no price never stops a build')
            with mock.patch.object(state, 'shop_price_table', return_value={}):
                self.assertIsNone(state.suggested_shop_cost(metadata), 'parts with no price: nothing to suggest')
            state.stop_catalog_warmup()
            state.pool.shutdown(wait=True, cancel_futures=True)

    def test_guns_lead_the_weapon_bar(self):
        root = parse('{game_entity {Weaponry ("keep") {place "gun" {weapon "g"}} {place "hull" {weapon "m"}} '
                     '{place "coax" {weapon "m"}{type "coax"}} {place "gun2" {weapon "g"}{basic}}}}').find('game_entity')
        kinds = {'g': 'stuff/x/gun/a.ext', 'm': 'stuff/x/mgun/b.ext'}
        resources = mock.Mock(entries={})
        with mock.patch('workshop.registration._stuff_definition', lambda _, name: kinds[name]):
            _guns_first(resources, root)
        weaponry = root.find('Weaponry')
        self.assertEqual([p.args[0] for p in weaponry.find_all('place')], ['gun', 'gun2', 'hull', 'coax'])
        self.assertEqual(weaponry.children[0].head, 'keep', 'what is not a place stays where it was')

    def test_a_vehicle_led_by_a_machine_gun_keeps_its_order(self):
        root = parse('{game_entity {Weaponry {place "mg" {weapon "m"}} {place "gun" {weapon "g"}{basic}}}}').find('game_entity')
        kinds = {'g': 'stuff/x/gun/a.ext', 'm': 'stuff/x/mgun/b.ext'}
        with mock.patch('workshop.registration._stuff_definition', lambda _, name: kinds[name]):
            _guns_first(mock.Mock(entries={}), root)
        self.assertEqual([p.args[0] for p in root.find('Weaponry').find_all('place')], ['mg', 'gun'])

    def test_a_reopened_design_is_built_on_its_stripped_source(self):
        # The fittings of its parents are editable now: on the parent as built they showed twice.
        transform = {'source_recipe': {'template': 'parent'}, 'assembly_draft': {'schema': 1, 'guns': [], 'turrets': []}}
        with mock.patch('workshop.editable_recipe.compile_reopened', return_value='rebuilt') as rebuilt:
            self.assertEqual(compile_draft('res', 'hull', 'donor', transform, Path('out'), 'id'), 'rebuilt')
        rebuilt.assert_called_once_with('res', 'hull', 'donor', transform, Path('out'), 'id')

    def test_a_fitting_in_a_folder_spelled_differently_does_not_read_the_whole_game(self):
        resources = mock.Mock(spec=['entries', '__dict__'])
        resources.entries = {'entity/-vehicle/tank/us_thunder/US_Thunder.def': 1, 'entity/-vehicle/tank/us_thunder/local.ext': 1}
        item = {'id': 'entity/-vehicle/tank/US_Thunder/us_thunder.mdl#plate'}
        with mock.patch('workshop.extra_armor._definition_items', return_value=[item]) as read, \
                mock.patch('workshop.extra_armor.catalog', side_effect=AssertionError('scanned the whole game')):
            self.assertIs(resolve_fitting(resources, item['id']), item)
        path, extensions = read.call_args.args[1:]
        self.assertEqual(path, 'entity/-vehicle/tank/us_thunder/US_Thunder.def')
        self.assertEqual(extensions['entity/-vehicle/tank/us_thunder'], ['entity/-vehicle/tank/us_thunder/local.ext'])


if __name__ == '__main__':
    unittest.main()
