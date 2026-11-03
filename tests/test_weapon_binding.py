import unittest

from workshop.geometry import parse_model
from workshop.integrity import dangling
from workshop.syntax import block
from workshop.weapon_binding import reference_bones


class WeaponBindingTests(unittest.TestCase):
    def test_family_validity_is_per_weapon_even_when_prefix_is_shared(self):
        model=parse_model('{skeleton {bone "root" {bone "Jet00"}{bone "Jet01"}}}')
        root=block('{game_entity {Weaponry {place "a" {Barrels 2}{shooter "jet"}} {place "b" {Barrels 3}{shooter "jet"}}}}')
        self.assertEqual(dangling(root,model),{('shooter','jet')})
        places=root.find('Weaponry').find_all('place')
        self.assertEqual(reference_bones(places[0],model,'shooter','jet'),['Jet00','Jet01'])
        self.assertEqual(reference_bones(places[1],model,'shooter','jet'),[])
        root.find('Weaponry').remove(places[1])
        self.assertEqual(dangling(root,model),set())

    def test_literal_socket_remains_valid_and_unknown_socket_is_rejected(self):
        model=parse_model('{skeleton {bone "Root" {bone "Muzzle"}}}')
        place=block('{place "weapon" {shooter "muzzle"}}')
        self.assertEqual(reference_bones(place,model,'shooter','muzzle'),['Muzzle'])
        self.assertEqual(reference_bones(place,model,'foresight','missing'),[])
