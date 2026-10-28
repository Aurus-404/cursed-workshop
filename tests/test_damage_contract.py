import tempfile
import unittest
from pathlib import Path

from workshop.compiler import compile_assembly
from workshop.campaign import _migrate_state, CampaignError
from workshop.syntax import block
from tests.test_compiler import FakeResources
from tests.test_gun_assembly import Resources


class DamageContractTests(unittest.TestCase):
    def test_macro_component_alias_survives_turret_gun_and_complete_edit(self):
        cases=[(FakeResources(),'hull','donor',{}),
               (Resources(),'receiver','donor',{'gun_swap':{'schema':1,'recipient_place':'main_slot','donor_place':'main_slot'}}),
               (FakeResources(),'hull','hull',{'commander_only':True})]
        for resources,recipient,donor,transform in cases:
            with self.subTest(operation=transform):
                resources.files['properties/vehicle.ext']+=b'''
                  (define "internals" {volume %0 {component "%1"}})
                  (define "fuel" ("internals" args %0 fuel))
                  (define "unused" {volume "ghost_volume" {component "ghost"}})'''
                path=f'entity/{recipient}/{recipient}.def'
                resources.files[path]=resources.files[path].replace(b'{mass ',
                    b'(include "/properties/vehicle.ext") ("fuel" args "left_tank") ("fuel" args "right_tank") {mass ')
                if isinstance(resources,FakeResources) and transform.get('commander_only'):
                    resources.files[path]=resources.files[path].replace(b'{mass ',
                        b'{placer {place "driver" {group "crew"}}{place "gunner" {group "crew"}}} {mass ')
                if isinstance(resources,FakeResources):
                    for tag in ('x','xx'):
                        for source,raw in list(resources.files.items()):
                            if source.startswith('entity/hull/'):
                                target=source.replace('entity/hull/','entity/hull_'+tag+'/').replace('/hull.','/hull_'+tag+'.')
                                resources.files[target]=raw.replace(b'hull.mdl',('hull_'+tag+'.mdl').encode()).replace(
                                    b'hull.ext',('hull_'+tag+'.ext').encode())
                with tempfile.TemporaryDirectory() as folder:
                    result=compile_assembly(resources,recipient,donor,transform,Path(folder),'mw_damage')
                profile=result['metadata']['state_profile']
                self.assertIn('fuel',profile['allowed_damage_ids'])
                self.assertNotIn('ghost',profile['allowed_damage_ids'])
                self.assertNotIn('ghost_volume',profile['allowed_damage_ids'])
                original=block('{Entity "fixture" {Component "fuel" {State destroyed}{Health 0}{HealthBroken 0}}}')
                migrated=_migrate_state(original,block('{Entity "donor"}'),profile)
                self.assertEqual(migrated.find('Component').render(),original.find('Component').render())
                original.find('Component').atoms[1].set('not_emitted')
                with self.assertRaisesRegex(CampaignError,'Persisted component'):
                    _migrate_state(original,block('{Entity "donor"}'),profile)
